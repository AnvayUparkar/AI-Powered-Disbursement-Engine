import re
from typing import List, Tuple, Optional, Set
from idp.models.layout import LayoutElement
from idp.services.table_processing.models import PhysicalTable, RawRow, RawCell, LogicalBlock
from idp.services.table_processing.logical_segmenter import LogicalTableSegmenter
from idp.services.table_processing.header_detector import HeaderDetector
from idp.services.table_processing.kv_detector import KeyValueDetector


class OCRTableDetector:
    """
    Recovers structured tables from raw OCR layout elements when
    vision-based table detectors (e.g. Docling) detect no tables.
    """

    @classmethod
    def cluster_elements_into_rows(
        cls,
        elements: List[LayoutElement],
        y_tolerance: float = 0.015
    ) -> List[List[LayoutElement]]:
        """
        Clusters 2D layout elements into horizontal lines/rows based on vertical proximity.
        """
        if not elements:
            return []

        valid_elems = [e for e in elements if e.bbox and len(e.bbox) == 4 and e.text and e.text.strip()]
        if not valid_elems:
            return []

        def get_center_y(e: LayoutElement) -> float:
            return (e.bbox[1] + e.bbox[3]) / 2.0

        sorted_elems = sorted(valid_elems, key=lambda e: (get_center_y(e), e.bbox[0]))

        rows: List[List[LayoutElement]] = []
        current_row: List[LayoutElement] = []
        current_y: Optional[float] = None

        for elem in sorted_elems:
            elem_cy = get_center_y(elem)
            if current_y is None:
                current_row.append(elem)
                current_y = elem_cy
            elif abs(elem_cy - current_y) <= y_tolerance:
                current_row.append(elem)
                current_y = sum(get_center_y(e) for e in current_row) / len(current_row)
            else:
                current_row.sort(key=lambda e: e.bbox[0])
                rows.append(current_row)
                current_row = [elem]
                current_y = elem_cy

        if current_row:
            current_row.sort(key=lambda e: e.bbox[0])
            rows.append(current_row)

        return rows

    @classmethod
    def detect_and_segment_tables(
        cls,
        elements: List[LayoutElement],
        page_number: int = 1
    ) -> Tuple[List[LogicalBlock], Set[str]]:
        """
        Scans layout elements for multi-column or key-value table structures.

        Returns:
            Tuple of (detected_logical_blocks, consumed_element_ids)
        """
        rows = cls.cluster_elements_into_rows(elements)
        if not rows:
            return [], set()

        raw_rows: List[RawRow] = []
        row_element_ids: List[Set[str]] = []

        for r_idx, r_elems in enumerate(rows):
            cells = [
                RawCell(text=e.text.strip(), column_index=c_idx, bbox=e.bbox)
                for c_idx, e in enumerate(r_elems)
            ]
            raw_rows.append(RawRow(cells=cells, row_index=r_idx))
            row_element_ids.append({e.id for e in r_elems if e.id})

        # Identify candidate table row ranges
        candidate_indices: List[int] = []
        for idx, r in enumerate(raw_rows):
            texts = [c.text for c in r.cells]
            is_hdr = HeaderDetector.is_header(r)
            is_kv = len(KeyValueDetector.extract_pairs(r.cells)) > 0
            is_multi = len(texts) >= 2

            if is_hdr or is_kv or is_multi:
                candidate_indices.append(idx)

        if len(candidate_indices) < 2:
            return [], set()

        # Group consecutive candidate row sequences
        candidate_groups: List[List[int]] = []
        curr_group: List[int] = []

        for c_idx in candidate_indices:
            if not curr_group:
                curr_group.append(c_idx)
            elif c_idx == curr_group[-1] + 1 or c_idx == curr_group[-1] + 2:
                curr_group.append(c_idx)
            else:
                if len(curr_group) >= 2:
                    candidate_groups.append(curr_group)
                curr_group = [c_idx]

        if len(curr_group) >= 2:
            candidate_groups.append(curr_group)

        blocks: List[LogicalBlock] = []
        consumed_ids: Set[str] = set()

        for g_idx, group in enumerate(candidate_groups):
            g_rows = [raw_rows[i] for i in group]
            phys = PhysicalTable(
                id=f"ocr-tbl-p{page_number}-{g_idx+1}",
                page_number=page_number,
                rows=g_rows
            )
            g_blocks = LogicalTableSegmenter.segment(phys)
            if g_blocks:
                blocks.extend(g_blocks)
                for row_idx in group:
                    consumed_ids.update(row_element_ids[row_idx])

        return blocks, consumed_ids
