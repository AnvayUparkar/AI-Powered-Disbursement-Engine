import os
import json
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from idp.models.document import ParsedDocument, DocumentSource, PageInformation
from idp.models.layout import LayoutElement, ElementType
from idp.models.ocr import OCRResult, OCRElement
from idp.models.table import TableStructure
from idp.models.processing import ProcessingMetadata, ProcessingMetrics
from idp.services.docling.parser import DoclingParseResult
from idp.services.vlm.client import VLMResult
from idp.utils.image_utils import normalize_bbox
from idp.core.exceptions import SerializationError
from idp.core.logging import logger, format_doc_log


class DocumentSerializer:
    """Combines preprocessor, Docling, RapidOCR, VLM, and XML results into Canonical Document Representation."""

    def build_unified_document(
        self,
        doc_id: str,
        filename: str,
        mime_type: str,
        file_size_bytes: int,
        page_count: int,
        docling_result: Optional[DoclingParseResult],
        ocr_results: List[OCRResult],
        vlm_corrections: Dict[str, VLMResult],  # key: ocr_element_id
        metrics: ProcessingMetrics,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        docling_used: bool = True,
        vlm_used: bool = False,
        vlm_provider: Optional[str] = None
    ) -> ParsedDocument:
        """
        Merge extracted layout elements, OCR text, VLM corrected elements, and tables into ParsedDocument.
        """
        logger.info(format_doc_log(doc_id, "Building canonical unified document representation"))

        try:
            pages_map: Dict[int, PageInformation] = {}

            # Initialize pages_map
            for pno in range(1, page_count + 1):
                w = 595.0
                h = 842.0
                if docling_result and pno <= len(docling_result.pages_dimensions):
                    w = docling_result.pages_dimensions[pno - 1].get("width", 595.0)
                    h = docling_result.pages_dimensions[pno - 1].get("height", 842.0)

                pages_map[pno] = PageInformation(
                    page_number=pno,
                    width=w,
                    height=h,
                    elements=[],
                    tables=[]
                )

            # Process each page independently
            for pno in range(1, page_count + 1):
                page_info = pages_map[pno]
                w = page_info.width
                h = page_info.height
                consumed_ocr_ids = set()

                # 1. Direct ingestion of Docling structural & OCR elements (with optional legacy OCR spatial alignment)
                if docling_result and docling_result.elements:
                    for elem in docling_result.elements:
                        if elem.page_number == pno:
                            norm_box = normalize_bbox(elem.bbox, w, h)
                            final_text = elem.text
                            src = elem.source or "docling_ocr"
                            ocr_orig = elem.ocr_original
                            conf = elem.confidence

                            # Legacy spatial mapping support when explicit ocr_results are provided
                            if ocr_results:
                                matched_ocr = []
                                for ocr_res in ocr_results:
                                    if ocr_res.page_number == pno:
                                        img_w = ocr_res.image_width if ocr_res.image_width > 0 else w
                                        img_h = ocr_res.image_height if ocr_res.image_height > 0 else h
                                        for ocr in ocr_res.elements:
                                            if ocr.id in consumed_ocr_ids:
                                                continue
                                            norm_ocr_box = normalize_bbox(ocr.bbox, img_w, img_h)
                                            if self._compute_overlap_score(norm_ocr_box, norm_box) >= 0.35 or self._compute_iou(norm_ocr_box, norm_box) >= 0.20:
                                                matched_ocr.append(ocr)
                                if matched_ocr:
                                    matched_ocr.sort(key=lambda o: (round(o.bbox[1], 2), o.bbox[0]))
                                    final_text = " ".join([o.text for o in matched_ocr])
                                    conf = sum([o.confidence for o in matched_ocr]) / len(matched_ocr)
                                    src = "rapidocr" if matched_ocr[0].source == "ocr" else matched_ocr[0].source
                                    for o in matched_ocr:
                                        consumed_ocr_ids.add(o.id)

                            # Apply VLM corrections in-place if element was flagged & re-analyzed
                            if elem.id in vlm_corrections:
                                vlm_res = vlm_corrections[elem.id]
                                final_text = vlm_res.text
                                src = "vlm_corrected"
                                ocr_orig = elem.text
                                conf = vlm_res.confidence

                            layout_elem = LayoutElement(
                                id=elem.id,
                                type=elem.type,
                                text=final_text,
                                bbox=norm_box,
                                confidence=round(conf, 4),
                                page_number=pno,
                                reading_order=elem.reading_order,
                                level=elem.level,
                                source=src,
                                structure_source="docling",
                                ocr_original=ocr_orig
                            )
                            page_info.elements.append(layout_elem)

                # 2. Direct ingestion of Docling TableFormer tables (with optional legacy cell OCR alignment)
                if docling_result and docling_result.tables:
                    for table in docling_result.tables:
                        if table.page_number == pno:
                            norm_table_box = normalize_bbox(table.bbox or [0, 0, w, h], w, h)
                            table.bbox = norm_table_box
                            
                            rows_dict: Dict[int, List[str]] = {}
                            # Normalize cell bounding boxes and apply cell-level VLM corrections or legacy OCR alignment
                            for cell in table.cells:
                                norm_cell_box = normalize_bbox(cell.bbox or norm_table_box, w, h)
                                cell.bbox = norm_cell_box

                                if ocr_results:
                                    matched_ocr = []
                                    for ocr_res in ocr_results:
                                        if ocr_res.page_number == pno:
                                            img_w = ocr_res.image_width if ocr_res.image_width > 0 else w
                                            img_h = ocr_res.image_height if ocr_res.image_height > 0 else h
                                            for ocr in ocr_res.elements:
                                                if ocr.id in consumed_ocr_ids:
                                                    continue
                                                norm_ocr_box = normalize_bbox(ocr.bbox, img_w, img_h)
                                                if self._compute_overlap_score(norm_ocr_box, norm_cell_box) >= 0.40 or self._compute_iou(norm_ocr_box, norm_cell_box) >= 0.15:
                                                    matched_ocr.append(ocr)
                                    if matched_ocr:
                                        matched_ocr.sort(key=lambda o: (round(o.bbox[1], 2), o.bbox[0]))
                                        cell_texts = []
                                        for o in matched_ocr:
                                            t_txt = vlm_corrections[o.id].text if o.id in vlm_corrections else o.text
                                            cell_texts.append(t_txt)
                                            consumed_ocr_ids.add(o.id)
                                        cell.text = " ".join(cell_texts)

                                cell_key = f"cell-{table.id}-{cell.row_index}-{cell.col_index}"
                                if cell_key in vlm_corrections:
                                    cell.text = vlm_corrections[cell_key].text

                                if cell.row_index not in rows_dict:
                                    rows_dict[cell.row_index] = []
                                rows_dict[cell.row_index].append(cell.text)

                            if rows_dict:
                                table.rows_raw = [rows_dict[r] for r in sorted(rows_dict.keys())]
                                if table.rows_raw:
                                    table.headers = table.rows_raw[0]

                            page_info.tables.append(table)

                # 3. Defensive Fallback: Absorb unconsumed external OCR elements if present
                if ocr_results:
                    for ocr_res in ocr_results:
                        if ocr_res.page_number == pno:
                            ocr_img_w = ocr_res.image_width if ocr_res.image_width > 0 else w
                            ocr_img_h = ocr_res.image_height if ocr_res.image_height > 0 else h
                            for ocr_elem in ocr_res.elements:
                                if ocr_elem.id in consumed_ocr_ids:
                                    continue
                                norm_box = normalize_bbox(ocr_elem.bbox, ocr_img_w, ocr_img_h)
                                final_text = ocr_elem.text
                                src = "rapidocr" if ocr_elem.source == "ocr" else ocr_elem.source
                                ocr_orig = ocr_elem.ocr_original
                                conf = ocr_elem.confidence

                                if ocr_elem.id in vlm_corrections:
                                    vlm_res = vlm_corrections[ocr_elem.id]
                                    final_text = vlm_res.text
                                    src = "vlm_corrected"
                                    ocr_orig = ocr_elem.text
                                    conf = vlm_res.confidence

                                if self._is_duplicate(norm_box, page_info.elements, iou_threshold=0.50, text=final_text):
                                    continue

                                page_info.elements.append(
                                    LayoutElement(
                                        id=ocr_elem.id or f"ocr-{pno}-{len(page_info.elements)+1}",
                                        type=ElementType.TEXT,
                                        text=final_text,
                                        bbox=norm_box,
                                        confidence=conf,
                                        page_number=pno,
                                        source=src,
                                        structure_source="none",
                                        ocr_original=ocr_orig
                                    )
                                )

                logger.info(
                    format_doc_log(
                        doc_id,
                        f"Page {pno} direct layout summary: elements={len(page_info.elements)}, "
                        f"tables={len(page_info.tables)}"
                    )
                )

                # Sort elements on page by spatial reading order
                page_info.elements.sort(key=lambda e: (e.reading_order if e.reading_order is not None else 9999, round(e.bbox[1] if e.bbox else 0.0, 2), e.bbox[0] if e.bbox else 0.0))

            # 3. Concatenate clean full text across all pages for downstream Node 3 (GLM-5)
            all_elements: List[LayoutElement] = []
            all_tables: List[TableStructure] = []
            full_text_parts = []

            for pno in sorted(pages_map.keys()):
                p = pages_map[pno]
                all_elements.extend(p.elements)
                all_tables.extend(p.tables)

                full_text_parts.append(f"--- PAGE {pno} ---")
                for elem in p.elements:
                    if elem.text:
                        full_text_parts.append(elem.text)

                for tbl in p.tables:
                    if tbl.rows_raw:
                        # Check if table actually has meaningful non-empty text
                        has_content = any(any(c.strip() for c in r if isinstance(c, str)) for r in tbl.rows_raw)
                        if has_content:
                            full_text_parts.append("[TABLE]")
                            if tbl.headers and any(h.strip() for h in tbl.headers):
                                full_text_parts.append(" | ".join(tbl.headers))
                            for r in tbl.rows_raw:
                                full_text_parts.append(" | ".join(r))
                            full_text_parts.append("[/TABLE]")

            full_text = "\n".join(full_text_parts)

            metrics.total_elements_extracted = len(all_elements)

            proc_meta = ProcessingMetadata(
                document_id=doc_id,
                processing_id=f"proc-{doc_id}",
                file_type=filename.split(".")[-1] if "." in filename else "unknown",
                mime_type=mime_type,
                file_size_bytes=file_size_bytes,
                page_count=page_count,
                docling_used=docling_used,
                ocr_engine="docling_rapidocr",
                ocr_model="PP-OCRv6_MEDIUM",
                vlm_used=vlm_used,
                vlm_provider=vlm_provider,
                metrics=metrics
            )

            source = DocumentSource(
                filename=filename,
                mime_type=mime_type,
                s3_bucket=s3_bucket,
                s3_key=s3_key
            )

            return ParsedDocument(
                document_id=doc_id,
                source=source,
                pages=list(pages_map.values()),
                tables=all_tables,
                elements=all_elements,
                text=full_text,
                processing=proc_meta
            )

        except Exception as e:
            logger.error(format_doc_log(doc_id, f"Document serialization error: {e}"))
            raise SerializationError(f"Failed to build unified document for {doc_id}", details=str(e))

    @staticmethod
    def _compute_iou(box_a: List[float], box_b: List[float]) -> float:
        """
        Compute Intersection over Union (IoU) between two [l, t, r, b] bounding boxes.
        Both boxes should be in the same coordinate space (normalized or absolute).
        """
        if len(box_a) < 4 or len(box_b) < 4:
            return 0.0

        # Intersection rectangle
        inter_l = max(box_a[0], box_b[0])
        inter_t = max(box_a[1], box_b[1])
        inter_r = min(box_a[2], box_b[2])
        inter_b = min(box_a[3], box_b[3])

        inter_w = max(0.0, inter_r - inter_l)
        inter_h = max(0.0, inter_b - inter_t)
        inter_area = inter_w * inter_h

        if inter_area == 0.0:
            return 0.0

        area_a = max(0.0, (box_a[2] - box_a[0]) * (box_a[3] - box_a[1]))
        area_b = max(0.0, (box_b[2] - box_b[0]) * (box_b[3] - box_b[1]))
        union_area = area_a + area_b - inter_area

        if union_area <= 0.0:
            return 0.0

        return inter_area / union_area

    @staticmethod
    def _compute_overlap_score(box_a: List[float], box_b: List[float]) -> float:
        """
        Compute containment ratio of box_a inside box_b (intersection_area / area_a).
        Used to check if an OCR line (box_a) lies inside a structural region or cell (box_b).
        """
        if len(box_a) < 4 or len(box_b) < 4:
            return 0.0

        inter_l = max(box_a[0], box_b[0])
        inter_t = max(box_a[1], box_b[1])
        inter_r = min(box_a[2], box_b[2])
        inter_b = min(box_a[3], box_b[3])

        inter_w = max(0.0, inter_r - inter_l)
        inter_h = max(0.0, inter_b - inter_t)
        inter_area = inter_w * inter_h

        if inter_area == 0.0:
            return 0.0

        area_a = max(0.0, (box_a[2] - box_a[0]) * (box_a[3] - box_a[1]))
        if area_a <= 0.0:
            return 0.0

        return inter_area / area_a

    @staticmethod
    def _is_duplicate(
        ocr_bbox: List[float],
        existing_elements: List[LayoutElement],
        iou_threshold: float = 0.5,
        text: Optional[str] = None
    ) -> bool:
        """
        Check if an OCR element's bounding box spatially overlaps any existing
        element on the same page with IoU >= threshold or matching text content.
        """
        for elem in existing_elements:
            if text and elem.text and text.strip().lower() == elem.text.strip().lower():
                if DocumentSerializer._compute_iou(ocr_bbox, elem.bbox) >= 0.20 or DocumentSerializer._compute_overlap_score(ocr_bbox, elem.bbox) >= 0.20:
                    return True
            if DocumentSerializer._compute_iou(ocr_bbox, elem.bbox) >= iou_threshold:
                return True
        return False

    def parse_xml_fast_path(
        self,
        file_path: str,
        doc_id: str,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None
    ) -> ParsedDocument:
        """
        Deterministic XML parsing fast path (bypasses Docling and OCR completely).
        """
        logger.info(format_doc_log(doc_id, f"Executing XML deterministic fast-path for: {file_path}"))
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()

            elements: List[LayoutElement] = []
            text_lines = []

            def _traverse(node, depth=0):
                tag = node.tag.split("}")[-1]  # remove namespace if present
                val = (node.text or "").strip()
                if val:
                    line = f"{tag}: {val}"
                    text_lines.append(line)
                    elements.append(
                        LayoutElement(
                            id=f"xml-{len(elements)+1}",
                            type=ElementType.KEY_VALUE,
                            text=line,
                            bbox=[0.0, 0.0, 1.0, 1.0],
                            confidence=1.0,
                            page_number=1,
                            source="xml"
                        )
                    )
                for child in node:
                    _traverse(child, depth + 1)

            _traverse(root)

            full_text = "\n".join(text_lines)
            file_size = os.path.getsize(file_path)

            proc_meta = ProcessingMetadata(
                document_id=doc_id,
                processing_id=f"proc-{doc_id}",
                file_type="xml",
                mime_type="application/xml",
                file_size_bytes=file_size,
                page_count=1,
                docling_used=False,
                ocr_engine="none",
                ocr_model="none",
                vlm_used=False,
                metrics=ProcessingMetrics(total_elements_extracted=len(elements))
            )

            page = PageInformation(
                page_number=1,
                width=800.0,
                height=1100.0,
                elements=elements,
                tables=[]
            )

            return ParsedDocument(
                document_id=doc_id,
                source=DocumentSource(
                    filename=os.path.basename(file_path),
                    mime_type="application/xml",
                    s3_bucket=s3_bucket,
                    s3_key=s3_key
                ),
                pages=[page],
                tables=[],
                elements=elements,
                text=full_text,
                processing=proc_meta
            )

        except Exception as e:
            logger.error(format_doc_log(doc_id, f"XML fast-path parsing error: {e}"))
            raise SerializationError(f"Failed to parse XML file {file_path}", details=str(e))
