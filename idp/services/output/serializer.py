import os
import re
import json
import xml.etree.ElementTree as ET
from enum import Enum
from types import SimpleNamespace
from typing import List, Dict, Any, Optional
from idp.models.document import ParsedDocument, DocumentSource, PageInformation
from idp.models.layout import LayoutElement, ElementType
from idp.models.ocr import OCRResult, OCRElement
from idp.models.table import TableStructure
from idp.models.processing import ProcessingMetadata, ProcessingMetrics
from idp.services.docling.parser import DoclingParseResult
from idp.services.vlm.client import VLMResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.fusion.region_mask import TableRegionMask
from idp.services.extraction.comb_box_detector import CombBoxDetector
from idp.utils.image_utils import normalize_bbox
from idp.core.exceptions import SerializationError
from idp.core.logging import logger, format_doc_log


class TableShapeDecision(str, Enum):
    """
    Tri-state outcome of comb-box-vs-real-table shape analysis. A binary
    reclassify/keep decision has no room for "I'm not sure" -- and a
    genuine single-row summary table with short cell values (e.g.
    "Sr No | ROI | Fee | GST") can look identical to a comb-box grid on
    a pure candidate-ratio check. AMBIGUOUS exists so that case gets
    surfaced for a human to check instead of silently guessed at.
    """
    RECLASSIFY_AS_COMB_BOX = "RECLASSIFY_AS_COMB_BOX"
    AMBIGUOUS = "AMBIGUOUS"
    KEEP_AS_TABLE = "KEEP_AS_TABLE"


class DocumentSerializer:
    """Combines preprocessor, Docling, RapidOCR, VLM, and XML results into Canonical Document Representation."""

    # ------------------------------------------------------------------
    # NOTE: Per this repo's own standard ("Zero Hardcoded Business Logic:
    # All comparison thresholds... are configured in pipeline/config.py"),
    # these belong in pipeline/config.py, not here. They're defined as
    # named class constants -- and overridable via __init__ -- as a
    # stopgap until pipeline/config.py's actual contents can be patched
    # directly (see the accompanying pipeline/config.py snippet).
    # ------------------------------------------------------------------

    # candidate_ratio >= this -> confidently a comb-box grid, reclassify.
    DEFAULT_COMB_TABLE_RECLASSIFY_RATIO = 0.8

    # this <= candidate_ratio < RECLASSIFY_RATIO -> ambiguous: keep as a
    # table (safe default -- never silently drop real table content) but
    # log a warning for human/dashboard review.
    DEFAULT_COMB_TABLE_AMBIGUOUS_FLOOR_RATIO = 0.5

    # Fallback confidence for a synthesized comb-box element when neither
    # the source TableCell nor its parent TableStructure carries one.
    DEFAULT_COMB_TABLE_SYNTHETIC_CONFIDENCE = 0.75

    def __init__(
        self,
        enable_comb_box_detection: bool = True,
        enable_comb_box_table_reclassification: bool = True,
        comb_table_reclassify_ratio: float = DEFAULT_COMB_TABLE_RECLASSIFY_RATIO,
        comb_table_ambiguous_floor_ratio: float = DEFAULT_COMB_TABLE_AMBIGUOUS_FLOOR_RATIO,
        comb_table_synthetic_confidence: float = DEFAULT_COMB_TABLE_SYNTHETIC_CONFIDENCE,
    ):
        self.evaluator = OCRConfidenceEvaluator()

        # Explicit constructor flags/params now exist (previously
        # enable_comb_box_detection only worked via getattr() duck-typing
        # with no way to actually pass it in). Both flags default True so
        # existing callers that construct DocumentSerializer() with no
        # args keep today's behavior unchanged.
        self.enable_comb_box_detection = enable_comb_box_detection
        self.enable_comb_box_table_reclassification = enable_comb_box_table_reclassification
        self.comb_table_reclassify_ratio = comb_table_reclassify_ratio
        self.comb_table_ambiguous_floor_ratio = comb_table_ambiguous_floor_ratio
        self.comb_table_synthetic_confidence = comb_table_synthetic_confidence

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
        logger.info(format_doc_log(doc_id, "Building canonical unified document representation with Region-Based Ownership"))

        try:
            pages_map: Dict[int, PageInformation] = {}

            # Create page-level table region masks from Docling table authority
            pages_dims = docling_result.pages_dimensions if docling_result else None
            page_table_masks = TableRegionMask.create_page_table_masks(
                docling_result=docling_result,
                page_count=page_count,
                pages_dimensions=pages_dims
            )

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
                table_regions = page_table_masks.get(pno, [])

                # PRODUCTION FIX: comb-box fields (Aadhaar/PAN/application-no/
                # handwritten-name written one character per printed cell) are
                # visually indistinguishable from a real table to Docling's
                # TableFormer -- a single row of bordered single-character
                # cells IS a table shape. When that happens, every character
                # lands in table.cells / table.rows_raw and NEVER becomes a
                # docling_page_elems entry at all, which means it never reaches
                # the comb-box candidate-bypass logic below and never reaches
                # CombBoxDetector -- both of which only ever see
                # page_info.elements. The result was a comb-box field with
                # zero LayoutElements, zero merged tokens, and zero bounding
                # box, with nothing in page_info.tables usable as a field
                # value either (scattered single-char cells, no encompassing
                # bbox). Tables matching _is_comb_box_shaped_table() are
                # reclassified here: their cells are synthesized into
                # elements and routed into docling_page_elems instead of
                # page_info.tables, so they flow through the exact same path
                # as any other short comb-box character.
                comb_grid_synthetic_elements: List[SimpleNamespace] = []

                # 1. DOCLING TABLE AUTHORITY: Ingest Docling TableFormer tables
                if docling_result and docling_result.tables:
                    for table in docling_result.tables:
                        if table.page_number == pno:
                            shape_decision = (
                                self._classify_table_shape(
                                    table,
                                    reclassify_ratio=self.comb_table_reclassify_ratio,
                                    ambiguous_floor_ratio=self.comb_table_ambiguous_floor_ratio,
                                )
                                if self.enable_comb_box_table_reclassification
                                else TableShapeDecision.KEEP_AS_TABLE
                            )

                            if shape_decision == TableShapeDecision.AMBIGUOUS:
                                # Safe default: never silently drop real table
                                # content on an uncertain signal. Keep it as a
                                # table (falls through below) but flag it loudly
                                # so a human/dashboard can confirm the call.
                                logger.warning(
                                    format_doc_log(
                                        doc_id,
                                        f"page={pno} table={table.id} decision=AMBIGUOUS_TABLE_SHAPE "
                                        f"cells={len(table.cells)} -- candidate ratio fell between the "
                                        f"comb-box and real-table thresholds; kept as a table. "
                                        f"Review manually if this table's rendered content looks wrong."
                                    )
                                )
                                self._report_table_shape_event(
                                    doc_id=doc_id, page_number=pno, table=table,
                                    decision=shape_decision
                                )

                            elif shape_decision == TableShapeDecision.RECLASSIFY_AS_COMB_BOX:
                                synth = self._synthesize_comb_box_elements_from_table(
                                    table=table,
                                    vlm_corrections=vlm_corrections,
                                    default_confidence=self.comb_table_synthetic_confidence,
                                )
                                comb_grid_synthetic_elements.extend(synth)
                                logger.info(
                                    format_doc_log(
                                        doc_id,
                                        f"page={pno} table={table.id} decision=RECLASSIFIED_AS_COMB_BOX_GRID "
                                        f"cells={len(table.cells)} synthesized_elements={len(synth)} "
                                        f"(bypassing table ingestion; routed to CombBoxDetector candidate pool)"
                                    )
                                )
                                self._report_table_shape_event(
                                    doc_id=doc_id, page_number=pno, table=table,
                                    decision=shape_decision, synthesized_count=len(synth)
                                )
                                continue  # skip normal table ingestion for this table

                            norm_table_box = normalize_bbox(table.bbox or [0, 0, w, h], w, h)
                            table.bbox = norm_table_box
                            
                            rows_dict: Dict[int, List[str]] = {}
                            for cell in table.cells:
                                norm_cell_box = normalize_bbox(cell.bbox or norm_table_box, w, h)
                                cell.bbox = norm_cell_box

                                cell_key = f"cell-{table.id}-{cell.row_index}-{cell.col_index}"
                                if cell_key in vlm_corrections:
                                    cell.text = vlm_corrections[cell_key].text

                                cell.text = self.evaluator.clean_bilingual_label_noise(cell.text)

                                if cell.row_index not in rows_dict:
                                    rows_dict[cell.row_index] = []
                                rows_dict[cell.row_index].append(cell.text)

                            # Populate clean rows_raw on table
                            table.rows_raw = [rows_dict[r_idx] for r_idx in sorted(rows_dict.keys())]
                            if table.rows_raw:
                                table.headers = table.rows_raw[0]

                            page_info.tables.append(table)

                # 2. DOCLING LAYOUT TEXT AUTHORITY: Ingest Docling text elements (primary)
                docling_page_elems = [
                    e for e in (docling_result.elements if docling_result else [])
                    if e.page_number == pno
                ]
                if comb_grid_synthetic_elements:
                    docling_page_elems = docling_page_elems + comb_grid_synthetic_elements
                comb_candidates_bypassed = 0
                if docling_page_elems:
                    for elem in docling_page_elems:
                        norm_box = normalize_bbox(elem.bbox, w, h)

                        # PRODUCTION FIX: comb-box fields (Aadhaar/name/application-no
                        # written one character per printed cell) OCR as a run of
                        # short 1-3 char elements. Both the table-region filter below
                        # and OCRConfidenceEvaluator.is_garbled_text() (which treats
                        # any standalone 1-2 char uppercase token as noise) were
                        # silently deleting every one of those characters here, before
                        # CombBoxDetector ever ran — so comb-box fields got zero
                        # elements, zero merged tokens, and zero bounding box, with no
                        # log line to explain why. Short alphanumeric candidates now
                        # bypass both filters and are always retained; CombBoxDetector
                        # (idp/services/output/serializer.py, step below) either merges
                        # them into a real field or — worst case — they surface as an
                        # occasional stray single-character "Text Block", which is a
                        # far safer failure mode than losing handwritten form data.
                        is_comb_candidate = CombBoxDetector.is_candidate_text(elem.text)

                        if not is_comb_candidate:
                            is_blocked, decision = TableRegionMask.is_inside_or_overlapping_table(
                                rapidocr_bbox=norm_box,
                                table_regions=table_regions,
                                text=elem.text
                            )
                            if is_blocked:
                                logger.info(
                                    format_doc_log(
                                        doc_id,
                                        f"docling_region_decision page={pno} elem={elem.id} decision={decision} text='{elem.text[:30]}'"
                                    )
                                )
                                continue

                        final_text = self.evaluator.clean_bilingual_label_noise(elem.text)

                        retained_meta: Dict[str, Any] = {}
                        if not is_comb_candidate:
                            if not final_text:
                                continue
                            if self.evaluator.is_garbled_text(final_text) and elem.source != "vlm_corrected":
                                # PRODUCTION FIX: photographed comb-box forms emit
                                # rows where a real structured VALUE (application
                                # no., GSTIN, account no., email, Aadhaar last-4)
                                # is fused with an unreadable glyph, tripping the
                                # garble gate. Dropping the whole element loses the
                                # bounding box AND the value. If a recoverable value
                                # survives, retain the element with a review flag
                                # instead of discarding it.
                                if not self._has_recoverable_value(final_text):
                                    continue
                                retained_meta = {
                                    "partial_garble_retained": True,
                                    "needs_vlm": True,
                                }
                        elif not final_text:
                            continue
                        else:
                            comb_candidates_bypassed += 1

                        src = elem.source or "docling_ocr"
                        conf = elem.confidence
                        ocr_orig = elem.ocr_original

                        if elem.id in vlm_corrections:
                            vlm_res = vlm_corrections[elem.id]
                            final_text = self.evaluator.clean_bilingual_label_noise(vlm_res.text)
                            src = "vlm_corrected"
                            ocr_orig = elem.text
                            conf = vlm_res.confidence
                            retained_meta = {}
                        elif retained_meta:
                            # Cap confidence on a retained partial-garble row so
                            # downstream routing still treats it as low-trust.
                            conf = min(conf, 0.35)

                        # PRODUCTION FIX: comb-box candidates must not be silently
                        # deduped away. Adjacent comb-box cells frequently repeat
                        # the same character (e.g. "1","1" in a numeric ID, or
                        # "A","A" in a name), and their tightly-packed bboxes can
                        # legitimately trip the text-match + spatial-overlap dedup
                        # rule below. That previously deleted a valid neighbor
                        # mid-sequence, silently dropping the run below
                        # CombBoxDetector.min_sequence_length before merging was
                        # ever attempted. Only non-candidates go through dedup.
                        if not is_comb_candidate and self._is_duplicate(
                            norm_box, page_info.elements, iou_threshold=0.50, text=final_text
                        ):
                            continue

                        page_info.elements.append(
                            LayoutElement(
                                id=elem.id or f"elem-{pno}-{len(page_info.elements)+1}",
                                type=elem.type,
                                text=final_text,
                                bbox=norm_box,
                                confidence=round(conf, 4),
                                page_number=pno,
                                reading_order=elem.reading_order,
                                level=elem.level,
                                source=src,
                                structure_source="docling",
                                ocr_original=ocr_orig,
                                metadata=retained_meta
                            )
                        )

                    if comb_candidates_bypassed:
                        logger.info(
                            format_doc_log(
                                doc_id,
                                f"page={pno} comb_box_candidates_retained={comb_candidates_bypassed} "
                                f"(bypassed table-region + garbled-text filters, pending CombBoxDetector)"
                            )
                        )

                # # 3. RAPIDOCR FALLBACK: Ingest RapidOCR text elements ONLY if Docling produced no elements for this page
                # elif ocr_results:
                #     for ocr_res in ocr_results:
                #         if ocr_res.page_number == pno:
                #             ocr_img_w = ocr_res.image_width if ocr_res.image_width > 0 else w
                #             ocr_img_h = ocr_res.image_height if ocr_res.image_height > 0 else h
                #             for ocr_elem in ocr_res.elements:
                #                 norm_box = normalize_bbox(ocr_elem.bbox, ocr_img_w, ocr_img_h)
                #                 final_text = self.evaluator.clean_bilingual_label_noise(ocr_elem.text)
                #                 src = "RAPIDOCR" if ocr_elem.source in ["ocr", "rapidocr"] else ocr_elem.source
                #                 ocr_orig = ocr_elem.ocr_original
                #                 conf = ocr_elem.confidence
                # 
                #                 if ocr_elem.id in vlm_corrections:
                #                     vlm_res = vlm_corrections[ocr_elem.id]
                #                     final_text = self.evaluator.clean_bilingual_label_noise(vlm_res.text)
                #                     src = "vlm_corrected"
                #                     ocr_orig = ocr_elem.text
                #                     conf = vlm_res.confidence
                # 
                #                 # RapidOCR Non-Table Filtering against Docling Table Regions
                #                 is_blocked, decision = TableRegionMask.is_inside_or_overlapping_table(
                #                     rapidocr_bbox=norm_box,
                #                     table_regions=table_regions
                #                 )
                # 
                #                 if is_blocked:
                #                     logger.info(
                #                         format_doc_log(
                #                             doc_id,
                #                             f"ocr_region_decision page={pno} elem={ocr_elem.id} decision={decision} text='{final_text[:30]}'"
                #                         )
                #                     )
                #                     continue
                # 
                #                 if self.evaluator.is_garbled_text(final_text) and src != "vlm_corrected":
                #                     logger.info(
                #                         format_doc_log(
                #                             doc_id,
                #                             f"ocr_region_decision page={pno} elem={ocr_elem.id} decision=SKIPPED_GARBLED_TEXT text='{final_text[:30]}'"
                #                         )
                #                     )
                #                     continue
                # 
                #                 # Secondary page-scoped deduplication
                #                 if self._is_duplicate(norm_box, page_info.elements, iou_threshold=0.50, text=final_text):
                #                     logger.info(
                #                         format_doc_log(
                #                             doc_id,
                #                             f"ocr_region_decision page={pno} elem={ocr_elem.id} decision=SKIPPED_DUPLICATE text='{final_text[:30]}'"
                #                         )
                #                     )
                #                     continue
                # 
                #                 logger.info(
                #                     format_doc_log(
                #                         doc_id,
                #                         f"ocr_region_decision page={pno} elem={ocr_elem.id} decision={decision} text='{final_text[:30]}'"
                #                     )
                #                 )
                # 
                #                 page_info.elements.append(
                #                     LayoutElement(
                #                         id=ocr_elem.id or f"ocr-{pno}-{len(page_info.elements)+1}",
                #                         type=ElementType.TEXT,
                #                         text=final_text,
                #                         bbox=norm_box,
                #                         confidence=conf,
                #                         page_number=pno,
                #                         source=src,
                #                         structure_source="none",
                #                         ocr_original=ocr_orig
                #                     )
                #                 )

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

            # NEW: Opt-in comb-box detection (feature flag controlled)
            comb_box_enabled = getattr(self, 'enable_comb_box_detection', True)
            if comb_box_enabled:
                comb_detector = CombBoxDetector()
                total_merged = 0
                
                for pno in sorted(pages_map.keys()):
                    p = pages_map[pno]
                    
                    # Detect and merge comb-box sequences
                    merged_tokens = comb_detector.detect_and_merge_comb_boxes(
                        elements=p.elements,
                        page_number=pno,
                        doc_id=doc_id
                    )
                    
                    # Add merged tokens as supplementary LayoutElements
                    for mt in merged_tokens:
                        merged_elem = LayoutElement(
                            id=mt.id,
                            text=mt.text,
                            bbox=mt.bbox,
                            page_number=mt.page_number,
                            confidence=mt.confidence,
                            source="comb_box_merged",
                            type=ElementType.TEXT,
                            reading_order=9999,  # Place after regular elements
                            structure_source="spatial_clustering",
                            metadata={
                                "merge_method": mt.merge_method,
                                "uniformity_score": mt.uniformity_score,
                                "constituent_ids": mt.constituent_element_ids,
                                "num_constituents": len(mt.constituent_element_ids)
                            }
                        )
                        p.elements.append(merged_elem)
                        total_merged += 1

                if total_merged > 0:
                    logger.info(
                        f"[{doc_id}] Comb-box detection: Added {total_merged} merged tokens "
                        f"across {len(pages_map)} pages"
                    )

            for pno in sorted(pages_map.keys()):
                p = pages_map[pno]
                all_elements.extend(p.elements)
                all_tables.extend(p.tables)

                full_text_parts.append(f"--- PAGE {pno} ---")
                for elem in p.elements:
                    if self._is_element_inside_tables(elem.bbox, p.tables):
                        continue
                    
                    if elem.source == "vlm_corrected":
                        clean_txt = elem.text
                    else:
                        from idp.services.ocr.text_sanitizer import clean_ocr_text
                        clean_txt = clean_ocr_text(elem.text, document_type=None)
                        if not clean_txt:
                            clean_txt = self.evaluator.clean_bilingual_label_noise(elem.text)
                            if self.evaluator.is_garbled_text(clean_txt):
                                continue
                    
                    if clean_txt:
                        full_text_parts.append(clean_txt)

                for tbl in p.tables:
                    if tbl.rows_raw:
                        # Check if table actually has meaningful non-empty text
                        has_content = any(any(c.strip() for c in r if isinstance(c, str)) for r in tbl.rows_raw)
                        if has_content:
                            full_text_parts.append("[TABLE]")
                            if tbl.headers and any(h.strip() for h in tbl.headers):
                                if not tbl.rows_raw or tbl.rows_raw[0] != tbl.headers:
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
    def _is_element_inside_tables(elem_bbox: Optional[List[float]], tables: List[TableStructure]) -> bool:
        """
        Determines whether a layout element's bounding box is spatially located inside
        any extracted table bounding box on the page, preventing text duplication.
        """
        if not elem_bbox or not tables or len(elem_bbox) < 4:
            return False
        for tbl in tables:
            if tbl.bbox and len(tbl.bbox) == 4:
                # If element overlaps >= 60% with table bbox, it is inside the table
                if DocumentSerializer._compute_overlap_score(elem_bbox, tbl.bbox) >= 0.60:
                    return True
        return False

    # Precompiled here (not per-call) -- runs once per Docling element.
    _EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
    _LONG_DIGIT_RUN_RE = re.compile(r"\d{5,}")
    _ALNUM_TOKEN_RE = re.compile(r"[A-Za-z0-9]{6,}")

    @staticmethod
    def _has_recoverable_value(text: str) -> bool:
        """
        True if an otherwise-garbled OCR row still carries a structured field
        VALUE worth keeping for downstream VLM correction rather than dropping:
          - a PAN/IFSC/GSTIN identifier,
          - an email address,
          - a run of >= 5 digits (application no., account no., mobile, pincode,
            amount), or
          - a >= 6 char alphanumeric token that mixes letters AND digits (a PAN
            / GSTIN / account no. partially broken by an unreadable glyph, e.g.
            "08AO0?K6924P1Z2").

        Rationale: on photographed comb-box forms a legitimate value is often
        fused with a single unreadable glyph, which flips is_garbled_text() to
        True for the whole line. Discarding the element then loses the bounding
        box and the value together; retaining it (flagged, low-confidence) is
        the safer failure mode. A pure-letter run (e.g. "HRAHRR") is NOT a
        value -- the letters+digits requirement keeps it excluded.
        """
        if not text or not text.strip():
            return False
        if OCRConfidenceEvaluator.IDENTIFIER_PATTERNS.search(text):
            return True
        if DocumentSerializer._EMAIL_RE.search(text):
            return True
        if DocumentSerializer._LONG_DIGIT_RUN_RE.search(text):
            return True
        for token in DocumentSerializer._ALNUM_TOKEN_RE.findall(text):
            if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
                return True
        return False

    @staticmethod
    def _is_duplicate(
        ocr_bbox: List[float],
        existing_elements: List[LayoutElement],
        iou_threshold: float = 0.75,
        text: Optional[str] = None
    ) -> bool:
        """
        Check if an OCR element's bounding box spatially overlaps any existing
        element on the same page with IoU >= threshold and matching text content.
        """
        norm_txt = text.strip().lower() if text and text.strip() else None

        for elem in existing_elements:
            elem_txt = elem.text.strip().lower() if elem.text and elem.text.strip() else None
            iou = DocumentSerializer._compute_iou(ocr_bbox, elem.bbox)
            overlap = DocumentSerializer._compute_overlap_score(ocr_bbox, elem.bbox)

            # Exact or highly similar text match with spatial overlap >= 0.20
            if norm_txt and elem_txt and norm_txt == elem_txt:
                if iou >= 0.20 or overlap >= 0.20:
                    return True

            # Pure spatial overlap: require very high IoU (>= 0.85) if text is different,
            # ensuring adjacent key-value form fields are not falsely dropped as duplicates
            if iou >= 0.85:
                return True

        return False

    @staticmethod
    def _classify_table_shape(
        table: TableStructure,
        reclassify_ratio: float,
        ambiguous_floor_ratio: float,
    ) -> "TableShapeDecision":
        """
        Tri-state classification distinguishing a genuine semantic table
        from a printed comb-box grid (a row of bordered single-character
        cells used for Aadhaar numbers, PAN, application numbers,
        handwritten names, etc.) that Docling's TableFormer classifies as
        a table purely because it looks like one visually.

        Two independent signals must agree to reclassify:
          1. Shape: single row, multiple columns (comb boxes are laid out
             left-to-right in one row; anything with >1 row is treated as
             a real table outright, full stop).
          2. Content: what fraction of cells hold a single short
             alphanumeric token (CombBoxDetector.is_candidate_text()).

        Thresholds are injected (not hardcoded) so they can be sourced
        from pipeline/config.py and tuned without touching this file.

        Returns:
            RECLASSIFY_AS_COMB_BOX  if candidate_ratio >= reclassify_ratio
            AMBIGUOUS                if ambiguous_floor_ratio <= candidate_ratio < reclassify_ratio
            KEEP_AS_TABLE            otherwise (includes any multi-row table)
        """
        if not table.cells or len(table.cells) < 2:
            return TableShapeDecision.KEEP_AS_TABLE

        row_indices = {c.row_index for c in table.cells}
        col_indices = {c.col_index for c in table.cells}

        # Comb-box grids are single-row, multi-column. A multi-row table
        # is never ambiguous -- it's a real table, regardless of content.
        if len(row_indices) > 1 or len(col_indices) < 2:
            return TableShapeDecision.KEEP_AS_TABLE

        candidate_cells = [c for c in table.cells if CombBoxDetector.is_candidate_text(c.text)]
        candidate_ratio = len(candidate_cells) / len(table.cells)

        if candidate_ratio >= reclassify_ratio:
            return TableShapeDecision.RECLASSIFY_AS_COMB_BOX
        if candidate_ratio >= ambiguous_floor_ratio:
            return TableShapeDecision.AMBIGUOUS
        return TableShapeDecision.KEEP_AS_TABLE

    @staticmethod
    def _report_table_shape_event(
        doc_id: str,
        page_number: int,
        table: TableStructure,
        decision: "TableShapeDecision",
        synthesized_count: Optional[int] = None,
    ) -> None:
        """
        Best-effort forward of a table-shape decision to the same
        auditor CombBoxDetector already reports merges to
        (idp/services/diagnostics/comb_box_audit.py), so reclassification
        and ambiguous-table events show up on the same dashboard as
        comb-box merges instead of only existing as log lines.

        Dispatched defensively via getattr(): this file has no visibility
        into comb_box_audit.py's actual method set, and log_reconstruction()
        is shaped for merge events (merged_text/constituent_tokens/
        uniformity_score), not table-shape events -- reusing it here would
        silently corrupt that schema. If the auditor doesn't yet expose a
        log_table_shape_decision() method, this becomes a no-op rather than
        raising, and reclassification decisions still surface via the
        logger.info/logger.warning calls at the call site either way.
        See the accompanying comb_box_audit.py patch to add real support.
        """
        try:
            from idp.services.diagnostics.comb_box_audit import get_global_auditor
            auditor = get_global_auditor()
            report_fn = getattr(auditor, "log_table_shape_decision", None)
            if callable(report_fn):
                report_fn(
                    document_id=doc_id,
                    page_number=page_number,
                    table_id=table.id,
                    decision=decision.value,
                    cell_count=len(table.cells) if table.cells else 0,
                    synthesized_element_count=synthesized_count,
                )
        except Exception as e:
            # Auditing must never break document serialization.
            logger.warning(f"[{doc_id}] table-shape audit reporting failed (non-fatal): {e}")

    @staticmethod
    def _synthesize_comb_box_elements_from_table(
        table: TableStructure,
        vlm_corrections: Dict[str, VLMResult],
        default_confidence: float = DEFAULT_COMB_TABLE_SYNTHETIC_CONFIDENCE,
    ) -> List[SimpleNamespace]:
        """
        Converts the cells of a table flagged RECLASSIFY_AS_COMB_BOX by
        _classify_table_shape() into lightweight objects matching the
        attribute shape of a raw Docling text element (id/text/bbox/
        page_number/type/confidence/source/ocr_original/reading_order/
        level), so they can be appended to docling_page_elems and flow
        through the *same* candidate-bypass and CombBoxDetector pipeline
        as any other short comb-box character.

        bbox is left in raw (un-normalized) coordinates here, matching
        what the step-2 ingestion loop expects -- it calls
        normalize_bbox() on elem.bbox itself, exactly as it does for
        ordinary Docling elements.
        """
        synthetic: List[SimpleNamespace] = []
        for idx, cell in enumerate(sorted(table.cells, key=lambda c: (c.row_index, c.col_index))):
            cell_key = f"cell-{table.id}-{cell.row_index}-{cell.col_index}"
            text = vlm_corrections[cell_key].text if cell_key in vlm_corrections else cell.text
            if not text or not text.strip():
                continue

            synthetic.append(
                SimpleNamespace(
                    id=f"synth-combcell-{table.id}-{cell.row_index}-{cell.col_index}",
                    text=text,
                    bbox=cell.bbox or table.bbox,
                    page_number=table.page_number,
                    type=ElementType.TEXT,
                    confidence=getattr(cell, "confidence", None) or getattr(table, "confidence", None) or default_confidence,
                    source="docling_table_cell_reclassified",
                    ocr_original=cell.text,
                    reading_order=idx,
                    level=None,
                )
            )
        return synthetic

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