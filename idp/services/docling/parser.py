import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from idp.services.docling.pipeline import DoclingPipeline
from idp.services.docling.options import DoclingOptions
from idp.models.layout import LayoutElement, ElementType
from idp.models.table import TableStructure, TableCell
from idp.core.exceptions import DoclingProcessingError
from idp.core.logging import logger, format_doc_log
from idp.services.ocr.confidence import compute_text_confidence


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


class DoclingParseResult(BaseModel):
    """Output structure returned by DoclingParser."""
    elements: List[LayoutElement] = Field(default_factory=list)
    tables: List[TableStructure] = Field(default_factory=list)
    page_count: int = 1
    pages_dimensions: List[Dict[str, float]] = Field(default_factory=list)


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
                    
                    elements.append(
                        LayoutElement(
                            id=f"docling-{uuid.uuid4().hex[:8]}",
                            type=elem_type,
                            text=txt,  # RAW text - clean later
                            bbox=bbox_list,
                            confidence=compute_text_confidence(txt),
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
                    if hasattr(table, "prov") and table.prov:
                        prov_item = table.prov[0]
                        pno = getattr(prov_item, "page_no", 1)
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
                                if hasattr(tc, "prov") and tc.prov:
                                    tb = tc.prov[0].bbox
                                    if tb:
                                        c_bbox = _extract_top_left_bbox(tb, _get_page_h(pno))

                                cells.append(
                                    TableCell(
                                        row_index=r0,
                                        col_index=c0,
                                        text=c_txt,
                                        is_header=is_hdr,
                                        bbox=c_bbox
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
                                    cells.append(TableCell(row_index=0, col_index=c_idx, text=val, is_header=True))

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
                                            is_header=False
                                        )
                                    )
                        except Exception:
                            pass

                    tables.append(
                        TableStructure(
                            id=f"table-{tidx+1}",
                            page_number=pno,
                            num_rows=len(rows_raw),
                            num_cols=len(headers) if headers else (len(rows_raw[0]) if rows_raw else 0),
                            cells=cells,
                            bbox=bbox_list,
                            headers=headers,
                            rows_raw=rows_raw
                        )
                    )

            logger.info(format_doc_log(doc_id, f"Docling successfully extracted {len(elements)} structural elements and {len(tables)} tables."))
            
            return DoclingParseResult(
                elements=elements,
                tables=tables,
                page_count=len(pages_dimensions),
                pages_dimensions=pages_dimensions
            )

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
