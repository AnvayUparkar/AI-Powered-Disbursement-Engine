from typing import List, Dict, Tuple, Optional, Set
from idp.models.table import TableStructure, TableCell
from idp.models.ocr import OCRElement
from idp.utils.image_utils import normalize_bbox
from idp.core.logging import logger


class SpatialCellFusion:
    """
    Performs single-pass geometric projection of OCR elements into Docling table cells.
    Bypasses duplicate OCR execution by binding vision-only table structure with OCR text.
    """

    @staticmethod
    def _compute_overlap_ratio(elem_box: List[float], cell_box: List[float]) -> float:
        """
        Calculates intersection area / element area.
        Returns fraction of elem_box inside cell_box.
        """
        if len(elem_box) < 4 or len(cell_box) < 4:
            return 0.0

        inter_l = max(elem_box[0], cell_box[0])
        inter_t = max(elem_box[1], cell_box[1])
        inter_r = min(elem_box[2], cell_box[2])
        inter_b = min(elem_box[3], cell_box[3])

        inter_w = max(0.0, inter_r - inter_l)
        inter_h = max(0.0, inter_b - inter_t)
        inter_area = inter_w * inter_h

        elem_area = max(0.0, (elem_box[2] - elem_box[0]) * (elem_box[3] - elem_box[1]))
        if elem_area <= 0.0:
            return 0.0

        return inter_area / elem_area

    @classmethod
    def fuse_table_cells(
        cls,
        table: TableStructure,
        ocr_elements: List[OCRElement],
        page_width: float,
        page_height: float,
        overlap_threshold: float = 0.40
    ) -> Set[str]:
        """
        Projects RapidOCR tokens into Docling table cells using 2D geometric containment.

        Args:
            table: The Docling TableStructure to populate.
            ocr_elements: List of OCRElement on the same page.
            page_width: Rendered image width for coordinate normalization.
            page_height: Rendered image height for coordinate normalization.
            overlap_threshold: Minimum intersection-over-element ratio to assign token to cell.

        Returns:
            Set of OCR element IDs that were absorbed into table cells.
        """
        consumed_ocr_ids: Set[str] = set()
        if not table.cells or not ocr_elements:
            return consumed_ocr_ids

        # Pre-normalize OCR element bboxes
        normalized_ocr: List[Tuple[OCRElement, List[float], float, float]] = []
        for elem in ocr_elements:
            norm_box = normalize_bbox(elem.bbox, page_width, page_height)
            cx = (norm_box[0] + norm_box[2]) / 2.0
            cy = (norm_box[1] + norm_box[3]) / 2.0
            normalized_ocr.append((elem, norm_box, cx, cy))

        for cell in table.cells:
            cell_box = cell.bbox
            if not cell_box or len(cell_box) < 4:
                continue

            # Ensure cell_box is normalized
            norm_cell_box = normalize_bbox(cell_box, page_width, page_height)
            cell.bbox = norm_cell_box

            # If cell already has valid non-empty text, preserve it
            if cell.text and cell.text.strip():
                continue

            cell_tokens: List[Tuple[float, float, str, str]] = []
            for elem, elem_box, cx, cy in normalized_ocr:
                # Criterion 1: Center-point containment
                center_inside = (
                    norm_cell_box[0] <= cx <= norm_cell_box[2] and
                    norm_cell_box[1] <= cy <= norm_cell_box[3]
                )

                # Criterion 2: Overlap ratio >= threshold
                overlap_inside = False
                if not center_inside:
                    overlap_inside = cls._compute_overlap_ratio(elem_box, norm_cell_box) >= overlap_threshold

                if center_inside or overlap_inside:
                    cell_tokens.append((elem_box[1], elem_box[0], elem.text, elem.id))
                    if elem.id:
                        consumed_ocr_ids.add(elem.id)

            if cell_tokens:
                # Sort tokens in reading order: top-to-bottom, left-to-right
                cell_tokens.sort(key=lambda t: (round(t[0], 3), round(t[1], 3)))
                cell.text = " ".join(t[2] for t in cell_tokens).strip()

        # Reconstruct rows_raw and headers if any cells were populated
        rows_dict: Dict[int, List[str]] = {}
        for cell in table.cells:
            if cell.row_index not in rows_dict:
                rows_dict[cell.row_index] = []
            rows_dict[cell.row_index].append(cell.text or "")

        if rows_dict:
            table.rows_raw = [rows_dict[r] for r in sorted(rows_dict.keys())]
            if table.rows_raw and any(any(c.strip() for c in r) for r in table.rows_raw):
                table.headers = table.rows_raw[0]

        return consumed_ocr_ids
