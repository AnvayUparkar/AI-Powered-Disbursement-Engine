import math
from typing import List, Dict, Tuple, Optional
from idp.models.table import TableRegion, TableStructure
from idp.services.docling.parser import DoclingParseResult
from idp.utils.image_utils import normalize_bbox
from idp.core.logging import logger


class TableRegionMask:
    """
    Manages region-based OCR ownership between Docling (Table Authority)
    and RapidOCR (Non-Table Text Authority).
    """

    @staticmethod
    def create_page_table_masks(
        docling_result: Optional[DoclingParseResult],
        page_count: int,
        pages_dimensions: Optional[List[Dict[str, float]]] = None
    ) -> Dict[int, List[TableRegion]]:
        """
        Extracts and normalizes all table bounding boxes from Docling, grouping them by page number.
        """
        page_masks: Dict[int, List[TableRegion]] = {pno: [] for pno in range(1, page_count + 1)}
        if not docling_result or not docling_result.tables:
            return page_masks

        for table in docling_result.tables:
            pno = table.page_number
            if pno not in page_masks:
                page_masks[pno] = []

            w = 595.0
            h = 842.0
            if pages_dimensions and pno <= len(pages_dimensions):
                w = pages_dimensions[pno - 1].get("width", 595.0)
                h = pages_dimensions[pno - 1].get("height", 842.0)

            raw_box = list(table.bbox) if table.bbox and len(table.bbox) == 4 and any(v > 0 for v in table.bbox) else None
            cell_boxes = []
            if table.cells:
                for c in table.cells:
                    if c.bbox and len(c.bbox) == 4 and any(v > 0 for v in c.bbox):
                        cell_boxes.append(normalize_bbox(c.bbox, w, h))

            if cell_boxes:
                min_l = min(cb[0] for cb in cell_boxes)
                min_t = min(cb[1] for cb in cell_boxes)
                max_r = max(cb[2] for cb in cell_boxes)
                max_b = max(cb[3] for cb in cell_boxes)
                if raw_box:
                    norm_raw = normalize_bbox(raw_box, w, h)
                    norm_box = [
                        min(norm_raw[0], min_l),
                        min(norm_raw[1], min_t),
                        max(norm_raw[2], max_r),
                        max(norm_raw[3], max_b)
                    ]
                else:
                    norm_box = [min_l, min_t, max_r, max_b]
            elif raw_box:
                norm_box = normalize_bbox(raw_box, w, h)
            else:
                continue

            region = TableRegion(
                page_number=pno,
                bbox=norm_box,
                table_id=table.id or f"tbl-p{pno}-{len(page_masks[pno])+1}",
                table_data=table
            )
            page_masks[pno].append(region)

        return page_masks

    @staticmethod
    def is_inside_or_overlapping_table(
        rapidocr_bbox: List[float],
        table_regions: List[TableRegion],
        overlap_threshold: float = 0.40
    ) -> Tuple[bool, str]:
        """
        Determines whether a RapidOCR text element falls inside or substantially overlaps any
        Docling table region on the page.

        Evaluation criteria:
        1. Center point containment (handles small OCR boxes inside large tables).
        2. Bounding box containment (RapidOCR bbox fully inside table bbox).
        3. Area overlap ratio (Intersection Area / RapidOCR Area >= 0.40).

        Returns:
            (is_blocked: bool, decision_code: str)
            decision_code is "SKIPPED_INSIDE_DOCLING_TABLE", "SKIPPED_OVERLAPPING_TABLE", or "ADDED_NON_TABLE_TEXT".
        """
        if not rapidocr_bbox or len(rapidocr_bbox) < 4 or not table_regions:
            return False, "ADDED_NON_TABLE_TEXT"

        rx1, ry1, rx2, ry2 = rapidocr_bbox[0], rapidocr_bbox[1], rapidocr_bbox[2], rapidocr_bbox[3]
        ocr_w = max(0.0, rx2 - rx1)
        ocr_h = max(0.0, ry2 - ry1)
        ocr_area = ocr_w * ocr_h
        center_x = (rx1 + rx2) / 2.0
        center_y = (ry1 + ry2) / 2.0

        for region in table_regions:
            tx1, ty1, tx2, ty2 = region.bbox[0], region.bbox[1], region.bbox[2], region.bbox[3]

            # 1. Center Containment Check
            if (tx1 <= center_x <= tx2) and (ty1 <= center_y <= ty2):
                return True, "SKIPPED_INSIDE_DOCLING_TABLE"

            # 2. Complete/Substantial Containment Check (with 2% tolerance)
            eps_x = (tx2 - tx1) * 0.02
            eps_y = (ty2 - ty1) * 0.02
            if (rx1 >= tx1 - eps_x) and (ry1 >= ty1 - eps_y) and (rx2 <= tx2 + eps_x) and (ry2 <= ty2 + eps_y):
                return True, "SKIPPED_INSIDE_DOCLING_TABLE"

            # 3. Area Overlap Ratio Check (Intersection / OCR Area)
            inter_x1 = max(rx1, tx1)
            inter_y1 = max(ry1, ty1)
            inter_x2 = min(rx2, tx2)
            inter_y2 = min(ry2, ty2)

            inter_w = max(0.0, inter_x2 - inter_x1)
            inter_h = max(0.0, inter_y2 - inter_y1)
            inter_area = inter_w * inter_h

            if ocr_area > 0.0:
                overlap_ratio = inter_area / ocr_area
                if overlap_ratio >= overlap_threshold:
                    return True, "SKIPPED_OVERLAPPING_TABLE"

        return False, "ADDED_NON_TABLE_TEXT"
