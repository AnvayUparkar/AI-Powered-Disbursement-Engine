import math
import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from idp.services.docling.pipeline import DoclingPipeline
from idp.services.docling.options import DoclingOptions
from idp.models.layout import LayoutElement, ElementType
from idp.models.table import TableStructure, TableCell
from idp.core.exceptions import DoclingProcessingError
from idp.core.logging import logger, format_doc_log


def _extract_top_left_bbox(bbox_obj: Any, page_height: float = 842.0) -> List[float]:
    """Convert Docling BoundingBox to TOP-LEFT origin [l, t, r, b] coordinate space."""
    if not bbox_obj:
        return [0.0, 0.0, 0.0, 0.0]
    try:
        if hasattr(bbox_obj, "to_top_left_origin"):
            b_tl = bbox_obj.to_top_left_origin(page_height)
            l = float(b_tl.l)
            t = min(float(b_tl.t), float(b_tl.b))
            r = float(b_tl.r)
            b = max(float(b_tl.t), float(b_tl.b))
            return [l, t, r, b]
        elif hasattr(bbox_obj, "l"):
            l = float(bbox_obj.l)
            r = float(bbox_obj.r)
            t1 = float(bbox_obj.t)
            t2 = float(bbox_obj.b)
            return [l, min(t1, t2), r, max(t1, t2)]
    except Exception:
        pass
    return [0.0, 0.0, 0.0, 0.0]


def _finite(value: Any) -> Optional[float]:
    """Return a float only when it is a real number; Docling emits NaN for stages that
    did not run (table_score with no tables, parse_score on image input)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 4)


def _grade_str(grade: Any) -> Optional[str]:
    """Docling reports quality as a QualityGrade enum. Coerce defensively: anything that is
    not plainly a string is dropped rather than allowed to fail DoclingParseResult validation
    (which would silently push the whole document onto the fallback parser)."""
    value = getattr(grade, "value", None)
    if isinstance(value, str):
        return value
    return grade if isinstance(grade, str) else None


def _overlap_ratio(inner: List[float], outer: List[float]) -> float:
    """Intersection area over `inner`'s own area. Both boxes are [l, t, r, b], same origin."""
    if len(inner) < 4 or len(outer) < 4:
        return 0.0
    il = max(inner[0], outer[0])
    it = max(inner[1], outer[1])
    ir = min(inner[2], outer[2])
    ib = min(inner[3], outer[3])
    inter = max(0.0, ir - il) * max(0.0, ib - it)
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    return (inter / area) if area > 0 else 0.0


class DoclingParseResult(BaseModel):
    """Output structure returned by DoclingParser."""
    elements: List[LayoutElement] = Field(default_factory=list)
    tables: List[TableStructure] = Field(default_factory=list)
    page_count: int = 1
    pages_dimensions: List[Dict[str, float]] = Field(default_factory=list)
    # Docling's own per-stage quality scores, surfaced verbatim so each model can be
    # inspected independently. None means the stage did not run for this document.
    layout_score: Optional[float] = None
    ocr_score: Optional[float] = None
    table_score: Optional[float] = None
    parse_score: Optional[float] = None
    quality_grade: Optional[str] = None


class DoclingParser:
    """Docling layout parser service abstraction for structural extraction."""

    def __init__(self, options: Optional[DoclingOptions] = None):
        self.options = options or DoclingOptions()
        self.pipeline = DoclingPipeline(self.options)

    def parse(self, document_path: str, doc_id: str = "DOC") -> DoclingParseResult:
        logger.info(format_doc_log(doc_id, f"Parsing layout structure with Docling: {document_path}"))
        converter = self.pipeline.get_converter()

        if converter == "MOCK":
            return self._fallback_parse(document_path, doc_id)

        try:
            conv_result = converter.convert(document_path)
            doc = conv_result.document

            # Harvest the real, model-reported confidences before they are discarded.
            # Docling keeps them on the per-page layout prediction: every Cluster carries the
            # layout model's own score, and each TextCell under it carries RapidOCR's
            # recognition score. Previously these were thrown away and replaced by a
            # rule-based estimate that returned a near-constant value for all text.
            cluster_index: Dict[int, List[Dict[str, Any]]] = {}
            for cpage in getattr(conv_result, "pages", []) or []:
                layout_pred = getattr(getattr(cpage, "predictions", None), "layout", None)
                if not layout_pred:
                    continue
                # Docling builds pages as Page(page_no=i + 1), so page_no is already 1-based
                # and lines up with prov.page_no on the document items. Do not offset it.
                pno_c = int(getattr(cpage, "page_no", 1) or 1)
                page_h = float(getattr(getattr(cpage, "size", None), "height", 842.0) or 842.0)
                entries: List[Dict[str, Any]] = []
                for cl in layout_pred.clusters:
                    cell_scores = [
                        float(c.confidence) for c in (cl.cells or [])
                        if getattr(c, "confidence", None) is not None
                    ]
                    entries.append({
                        "bbox": _extract_top_left_bbox(cl.bbox, page_h),
                        "layout_confidence": _finite(cl.confidence),
                        "ocr_confidence": _finite(sum(cell_scores) / len(cell_scores)) if cell_scores else None,
                    })
                cluster_index[pno_c] = entries

            def _scores_for(bbox: List[float], pno_lookup: int) -> tuple[Optional[float], Optional[float]]:
                """Best-overlapping layout cluster's scores for an element bbox."""
                best, best_ratio = None, 0.0
                for entry in cluster_index.get(pno_lookup, []):
                    ratio = _overlap_ratio(bbox, entry["bbox"])
                    if ratio > best_ratio:
                        best, best_ratio = entry, ratio
                if best is None or best_ratio < 0.3:
                    return None, None
                return best["ocr_confidence"], best["layout_confidence"]

            elements: List[LayoutElement] = []
            tables: List[TableStructure] = []
            pages_dimensions: List[Dict[str, float]] = []

            # Extract page info
            if hasattr(doc, "pages"):
                for pno, pdata in doc.pages.items():
                    w = float(getattr(pdata.size, "width", 595.0))
                    h = float(getattr(pdata.size, "height", 842.0))
                    pages_dimensions.append({"width": w, "height": h})
            
            if not pages_dimensions:
                pages_dimensions = [{"width": 595.0, "height": 842.0}]

            # Helper for page height lookup
            def _get_page_h(pno: int) -> float:
                if 1 <= pno <= len(pages_dimensions):
                    return pages_dimensions[pno - 1].get("height", 842.0)
                return 842.0

            # Process layout elements (texts, headings, lists)
            reading_order = 0
            if hasattr(doc, "texts"):
                for item in doc.texts:
                    reading_order += 1
                    elem_type = ElementType.PARAGRAPH
                    label = getattr(item, "label", "text").lower()
                    if "heading" in label or "title" in label:
                        elem_type = ElementType.HEADING
                    elif "caption" in label:
                        elem_type = ElementType.CAPTION

                    bbox_list = [0.0, 0.0, 0.0, 0.0]
                    pno = 1
                    if hasattr(item, "prov") and item.prov:
                        prov_item = item.prov[0]
                        pno = getattr(prov_item, "page_no", 1)
                        if hasattr(prov_item, "bbox") and prov_item.bbox:
                            bbox_list = _extract_top_left_bbox(prov_item.bbox, _get_page_h(pno))

                    txt = getattr(item, "text", "") or ""
                    
                    # Capture ALL text - no filtering at extraction
                    if not txt or not txt.strip():
                        continue
                    
                    # NOTE: TextItem and ProvenanceItem carry no `confidence` field, so
                    # reading it off the document item always yields None. The real scores
                    # live on the layout Cluster and its TextCells; _scores_for reads those.
                    ocr_conf, layout_conf = _scores_for(bbox_list, pno)
                    elements.append(
                        LayoutElement(
                            id=f"docling-{uuid.uuid4().hex[:8]}",
                            type=elem_type,
                            text=txt,  # RAW text - clean later
                            bbox=bbox_list,
                            # `confidence` mirrors the OCR score so existing consumers keep
                            # working; the two model scores stay separately inspectable.
                            confidence=round(ocr_conf, 4) if ocr_conf is not None else 1.0,
                            ocr_confidence=ocr_conf,
                            layout_confidence=layout_conf,
                            page_number=pno,
                            reading_order=reading_order,
                            source="docling_ocr",
                            structure_source="docling"
                        )
                    )

            # Process tables
            if hasattr(doc, "tables"):
                for tidx, table in enumerate(doc.tables):
                    pno = 1
                    bbox_list = [0.0, 0.0, 0.0, 0.0]
                    table_conf = getattr(table, "confidence", None)
                    if hasattr(table, "prov") and table.prov:
                        prov_item = table.prov[0]
                        pno = getattr(prov_item, "page_no", 1)
                        if table_conf is None:
                            table_conf = getattr(prov_item, "confidence", None)
                        if hasattr(prov_item, "bbox") and prov_item.bbox:
                            bbox_list = _extract_top_left_bbox(prov_item.bbox, _get_page_h(pno))

                    cells: List[TableCell] = []
                    headers: List[str] = []
                    rows_raw: List[List[str]] = []

                    # 1. Native Docling TableData cell grid extraction (span-aware)
                    if hasattr(table, "data") and hasattr(table.data, "table_cells") and table.data.table_cells:
                        try:
                            tc_list = table.data.table_cells

                            # Determine grid dimensions from span attributes
                            n_rows = getattr(table.data, "num_rows", 0)
                            n_cols = getattr(table.data, "num_cols", 0)
                            if not n_rows or not n_cols:
                                for tc in tc_list:
                                    end_r = getattr(tc, "end_row_offset_idx", getattr(tc, "start_row_offset_idx", 0))
                                    end_c = getattr(tc, "end_col_offset_idx", getattr(tc, "start_col_offset_idx", 0))
                                    n_rows = max(n_rows, end_r + 1)
                                    n_cols = max(n_cols, end_c + 1)

                            # Build NxM grid; each slot holds the cell text for that (row, col) position.
                            # Merged cells stamp their text into every slot they span so that
                            # rows_raw[r] always has exactly n_cols entries.
                            grid: List[List[str]] = [[""] * n_cols for _ in range(n_rows)]
                            occupied: set = set()

                            for tc in tc_list:
                                r0 = getattr(tc, "start_row_offset_idx", 0)
                                c0 = getattr(tc, "start_col_offset_idx", 0)
                                r1 = getattr(tc, "end_row_offset_idx", r0)
                                c1 = getattr(tc, "end_col_offset_idx", c0)
                                c_txt = str(getattr(tc, "text", "")).strip()
                                
                                # Capture ALL table cell text - no filtering
                                
                                is_hdr = bool(getattr(tc, "column_header", False)) or (r0 == 0)

                                c_bbox = None
                                cell_conf = getattr(tc, "confidence", None)
                                if hasattr(tc, "prov") and tc.prov:
                                    tb = tc.prov[0].bbox
                                    if tb:
                                        c_bbox = _extract_top_left_bbox(tb, _get_page_h(pno))
                                    if cell_conf is None:
                                        cell_conf = getattr(tc.prov[0], "confidence", None)

                                if cell_conf is None and table_conf is not None:
                                    cell_conf = table_conf

                                # No fabricated fallback: when neither the cell nor its table
                                # reports a score, record full confidence rather than inventing
                                # one from the text's shape.
                                c_conf_val = float(cell_conf) if cell_conf is not None else 1.0

                                cells.append(
                                    TableCell(
                                        row_index=r0,
                                        col_index=c0,
                                        text=c_txt,
                                        is_header=is_hdr,
                                        bbox=c_bbox,
                                        confidence=round(c_conf_val, 4)
                                    )
                                )

                                # Write text only at the origin slot.
                                # Mark all spanned slots as occupied so a later
                                # non-spanning cell cannot overwrite them.
                                # Continuation slots stay empty — this prevents
                                # row-spanning header text from bleeding into
                                # sub-header and data rows below.
                                if (r0, c0) not in occupied:
                                    grid[r0][c0] = c_txt
                                for sr in range(r0, min(r1 + 1, n_rows)):
                                    for sc in range(c0, min(c1 + 1, n_cols)):
                                        occupied.add((sr, sc))

                            # Read rows off the grid
                            for r_idx, row_vals in enumerate(grid):
                                rows_raw.append(row_vals)
                                if r_idx == 0:
                                    headers = row_vals

                        except Exception as ex:
                            logger.debug(f"Native table cell extraction fallback: {ex}")

                    # 2. Dataframe fallback if table_cells is unavailable
                    if not rows_raw and hasattr(table, "export_to_dataframe"):
                        try:
                            df = table.export_to_dataframe()
                            headers = [str(c).strip() for c in df.columns]
                            if headers:
                                rows_raw.append(headers)
                                for c_idx, val in enumerate(headers):
                                    cells.append(TableCell(row_index=0, col_index=c_idx, text=val, is_header=True, confidence=round(float(table_conf) if table_conf is not None else 1.0, 4)))

                            for r_idx, row in df.iterrows():
                                row_vals = [str(v).strip() if v is not None and str(v) != "nan" else "" for v in row.values]
                                actual_r_idx = r_idx + 1 if headers else r_idx
                                rows_raw.append(row_vals)
                                for c_idx, val in enumerate(row_vals):
                                    cells.append(
                                        TableCell(
                                            row_index=actual_r_idx,
                                            col_index=c_idx,
                                            text=val,
                                            is_header=False,
                                            confidence=round(float(table_conf) if table_conf is not None else 1.0, 4)
                                        )
                                    )
                        except Exception:
                            pass

                    num_cols = len(headers) if headers else (len(rows_raw[0]) if rows_raw else 0)

                    # Threshold post-filter: Docling's TableStructureOptions has no
                    # native min_rows/min_cols/confidence knobs, so these are
                    # enforced here. A table that fails the gate is dropped entirely
                    # rather than silently ignored (see options.py NOTE).
                    if len(rows_raw) < self.options.table_min_rows or num_cols < self.options.table_min_cols:
                        logger.debug(
                            f"Dropping table {tidx+1} on page {pno}: "
                            f"{len(rows_raw)}x{num_cols} below min "
                            f"{self.options.table_min_rows}x{self.options.table_min_cols}"
                        )
                        continue

                    non_empty_cells = sum(1 for c in cells if c.text.strip())
                    fill_ratio = (non_empty_cells / len(cells)) if cells else 0.0
                    if fill_ratio < self.options.table_confidence_threshold:
                        logger.debug(
                            f"Dropping table {tidx+1} on page {pno}: "
                            f"cell fill ratio {fill_ratio:.2f} below "
                            f"table_confidence_threshold={self.options.table_confidence_threshold}"
                        )
                        continue

                    # Docling's own markdown table renderer (span-aware, handles
                    # merged header cells) -- captured verbatim rather than
                    # re-derived from rows_raw so the UI can show exactly what
                    # Docling itself considers the table's structure to be.
                    table_markdown: Optional[str] = None
                    if hasattr(table, "export_to_markdown"):
                        try:
                            md_result = table.export_to_markdown(doc)
                            if isinstance(md_result, str):
                                table_markdown = md_result
                        except Exception as md_ex:
                            logger.debug(f"export_to_markdown failed for table {tidx+1} on page {pno}: {md_ex}")

                    tbl_ocr_conf, tbl_layout_conf = _scores_for(bbox_list, pno)
                    t_conf_val = float(table_conf) if table_conf is not None else (sum(c.confidence for c in cells)/len(cells) if cells else 1.0)

                    tables.append(
                        TableStructure(
                            id=f"table-{tidx+1}",
                            page_number=pno,
                            num_rows=len(rows_raw),
                            num_cols=num_cols,
                            cells=cells,
                            bbox=bbox_list,
                            headers=headers,
                            rows_raw=rows_raw,
                            markdown=table_markdown,
                            confidence=round(t_conf_val, 4),
                            table_confidence=tbl_layout_conf
                        )
                    )

            logger.info(format_doc_log(doc_id, f"Docling successfully extracted {len(elements)} structural elements and {len(tables)} tables."))
            
            conf_report = getattr(conv_result, "confidence", None)
            grade = getattr(conf_report, "mean_grade", None)
            result = DoclingParseResult(
                elements=elements,
                tables=tables,
                page_count=len(pages_dimensions),
                pages_dimensions=pages_dimensions,
                layout_score=_finite(getattr(conf_report, "layout_score", None)),
                ocr_score=_finite(getattr(conf_report, "ocr_score", None)),
                table_score=_finite(getattr(conf_report, "table_score", None)),
                parse_score=_finite(getattr(conf_report, "parse_score", None)),
                quality_grade=_grade_str(grade),
            )
            logger.info(format_doc_log(
                doc_id,
                f"Docling stage scores -- layout={result.layout_score} ocr={result.ocr_score} "
                f"table={result.table_score} parse={result.parse_score} grade={result.quality_grade}"
            ))
            return result

        except Exception as e:
            logger.error(format_doc_log(doc_id, f"Docling parsing error: {e}"))
            return self._fallback_parse(document_path, doc_id)

    def _fallback_parse(self, document_path: str, doc_id: str) -> DoclingParseResult:
        """Fallback parse method if native Docling is uninstalled or fails."""
        logger.info(format_doc_log(doc_id, "Executing fallback Docling layout parser"))
        elements: List[LayoutElement] = []

        # PyMuPDF block layout fallback
        try:
            import fitz
            doc = fitz.open(document_path)
            reading_order = 0
            dimensions = []
            for pidx, page in enumerate(doc):
                pno = pidx + 1
                rect = page.rect
                dimensions.append({"width": float(rect.width), "height": float(rect.height)})
                blocks = page.get_text("blocks")
                for b in blocks:
                    reading_order += 1
                    # b format: (x0, y0, x1, y1, "text", block_no, block_type)
                    elements.append(
                        LayoutElement(
                            id=f"docling-fb-{reading_order}",
                            type=ElementType.PARAGRAPH,
                            text="",  # Docling layout fallback provides structural regions only
                            bbox=[float(b[0]), float(b[1]), float(b[2]), float(b[3])],
                            confidence=0.9,
                            page_number=pno,
                            reading_order=reading_order,
                            source="docling_ocr",
                            structure_source="docling"
                        )
                    )
            doc.close()
            return DoclingParseResult(
                elements=elements,
                tables=[],
                page_count=len(dimensions),
                pages_dimensions=dimensions
            )
        except Exception:
            return DoclingParseResult(
                elements=[],
                tables=[],
                page_count=1,
                pages_dimensions=[{"width": 595.0, "height": 842.0}]
            )
