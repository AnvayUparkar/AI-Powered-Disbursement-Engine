import threading
from typing import Any, Dict, Optional
from idp.services.docling.options import DoclingOptions
from idp.core.logging import logger

# ---------------------------------------------------------------------------
# Process-wide cache of Docling DocumentConverter instances, keyed by options
# fingerprint.
#
# Docling's DocumentConverter initializes multiple heavyweight ONNX runtime
# sessions (layout model, table model, OCR detection/recognition) on first
# construction.  Re-creating the converter on every document invocation wastes
# 3-8 seconds of cold-start time per document and defeats per-page/per-doc
# ThreadPoolExecutor parallelism.
#
# Documents are routed through per-document-type DoclingOptions profiles (see
# config/docling_profiles.py), so this is keyed by fingerprint rather than a
# single slot: each distinct profile gets its own converter built once and
# reused for the rest of the process lifetime, instead of successive calls
# with different profiles evicting/rebuilding a single shared instance.
# ---------------------------------------------------------------------------

_DOCLING_CONVERTER_LOCK: threading.RLock = threading.RLock()
_DOCLING_CONVERTER_CACHE: Dict[str, Any] = {}  # options_key -> converter (or "MOCK")


def _get_options_key(options: DoclingOptions) -> str:
    """Fingerprint of relevant DoclingOptions fields used for cache invalidation."""
    return (
        f"{options.table_mode}|{options.do_ocr}|{options.do_table_structure}"
        f"|{options.do_cell_matching}|{options.images_scale}|{options.layout_detection_threshold}"
        f"|{options.ocr_model_name}|{options.det_model_path}|{options.rec_model_path}"
        f"|{'_'.join(options.ocr_lang)}"
    )


def get_cached_converter(options: Optional[DoclingOptions] = None) -> Any:
    """
    Return the process-wide Docling DocumentConverter for the given options
    profile, building it once per distinct profile.  Subsequent calls (from
    any thread, for any previously-seen profile) return the cached instance
    immediately without re-loading any ONNX models.

    Thread-safe via module-level RLock: concurrent callers block until the
    initial build completes rather than each spawning a duplicate build.
    """
    options = options or DoclingOptions()
    options_key = _get_options_key(options)

    # Fast-path: return cached converter if this exact profile was built before
    with _DOCLING_CONVERTER_LOCK:
        cached = _DOCLING_CONVERTER_CACHE.get(options_key)
        if cached is not None:
            logger.debug(f"[DoclingCache] Returning cached DocumentConverter for '{options_key}' (no re-init).")
            return cached

        # Cache miss: build once for this profile
        logger.info(
            "[DoclingCache] Building Docling DocumentConverter for the first time "
            f"(options_key='{options_key}'). Subsequent calls will reuse this instance."
        )

        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = options.do_ocr
            pipeline_options.do_table_structure = options.do_table_structure

            # Page rasterization scale used as input to OCR + TableFormer. This is
            # the single highest-leverage knob for faint/low-res table text: at the
            # default 1.0 Docling renders pages at native PDF point resolution
            # (~72dpi equivalent), which is often too low for small table cell text.
            pipeline_options.images_scale = options.images_scale

            # Layout model's region-detection confidence threshold -- this is the
            # actual gate that decides whether a page region gets classified as a
            # "Table" at all, before TableFormer ever sees it. Lower it to recover
            # faint/low-confidence tables the layout model would otherwise drop.
            try:
                pipeline_options.layout_options.engine_options.score_threshold = (
                    options.layout_detection_threshold
                )
                logger.info(
                    f"[DoclingCache] Layout detection score_threshold={options.layout_detection_threshold}"
                )
            except AttributeError as layout_err:
                logger.warning(
                    f"[DoclingCache] Could not set layout detection threshold "
                    f"(Docling API surface changed?): {layout_err}"
                )

            # Configure Docling-managed OCR engine (RapidOCR PP-OCRv6)
            if options.do_ocr:
                try:
                    from docling.datamodel.pipeline_options import RapidOcrOptions
                    ocr_opts = RapidOcrOptions(
                        backend="onnxruntime",
                        force_full_page_ocr=options.force_full_page_ocr,
                        lang=options.ocr_lang
                    )
                    
                    # Custom model paths
                    if options.det_model_path:
                        ocr_opts.det_model_path = options.det_model_path
                    if options.rec_model_path:
                        ocr_opts.rec_model_path = options.rec_model_path
                    if options.cls_model_path:
                        ocr_opts.cls_model_path = options.cls_model_path
                    
                    # OCR quality/performance settings
                    if hasattr(ocr_opts, "det_limit_side_len"):
                        ocr_opts.det_limit_side_len = options.det_limit_side_len
                    if hasattr(ocr_opts, "det_db_thresh"):
                        ocr_opts.det_db_thresh = options.det_db_thresh
                    if hasattr(ocr_opts, "det_db_box_thresh"):
                        ocr_opts.det_db_box_thresh = options.det_db_box_thresh
                    if hasattr(ocr_opts, "rec_batch_num"):
                        ocr_opts.rec_batch_num = options.rec_batch_num
                    
                    pipeline_options.ocr_options = ocr_opts
                    logger.info(
                        f"[DoclingCache] Configured RapidOCR: {options.ocr_model_name}, "
                        f"force_full_page={options.force_full_page_ocr}"
                    )
                except Exception as ocr_err:
                    logger.warning(f"[DoclingCache] RapidOcrOptions config skipped: {ocr_err}")

            # Configure TableFormer mode and cell matching
            if hasattr(pipeline_options, "table_structure_options"):
                from docling.datamodel.pipeline_options import TableFormerMode

                table_opts = pipeline_options.table_structure_options

                # FAST mode is disabled repo-wide: on this document set (scanned
                # KYC/comb-box forms, faint bank statement grids) FAST's speed
                # tradeoff costs real table structure accuracy, so ACCURATE is
                # forced regardless of what any profile's table_mode requests.
                # (TableFormerMode enum values are lowercase "fast"/"accurate";
                # options.table_mode is authored uppercase and assigning it as a
                # raw string bypasses pydantic validation -- this hardcode also
                # sidesteps that footgun entirely.)
                if options.table_mode.upper() != "ACCURATE":
                    logger.info(
                        f"[DoclingCache] table_mode='{options.table_mode}' requested but "
                        "FAST mode is disabled repo-wide -- forcing ACCURATE."
                    )
                table_opts.mode = TableFormerMode.ACCURATE

                table_opts.do_cell_matching = options.do_cell_matching

                # table_confidence_threshold / table_min_rows / table_min_cols have
                # NO equivalent in Docling's TableStructureOptions -- they are
                # enforced as a post-filter in DoclingParser.parse() after
                # TableFormer returns its grid.
                logger.info(
                    f"[DoclingCache] TableFormer mode={table_opts.mode}, "
                    f"do_cell_matching={table_opts.do_cell_matching}, "
                    f"images_scale={options.images_scale}"
                )

            format_options = {"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
            converter = DocumentConverter(format_options=format_options)
            logger.info(
                "[DoclingCache] DocumentConverter built and cached. "
                "ONNX models are now hot and will be reused for all future documents."
            )
        except Exception as e:
            logger.warning(
                f"[DoclingCache] Docling unavailable, falling back to MOCK converter: {e}"
            )
            converter = "MOCK"

        _DOCLING_CONVERTER_CACHE[options_key] = converter
        return converter


def invalidate_converter_cache() -> None:
    """Force the next call(s) to get_cached_converter() to rebuild every converter.

    Use only in tests or when OCR model paths change at runtime.
    """
    with _DOCLING_CONVERTER_LOCK:
        _DOCLING_CONVERTER_CACHE.clear()
        logger.info("[DoclingCache] Converter cache invalidated.")


class DoclingPipeline:
    """Pipeline factory for constructing Docling DocumentConverter instances.

    Delegates to the process-wide singleton cache (get_cached_converter) so
    that the heavy ONNX model initialization happens exactly once per process,
    regardless of how many DoclingPipeline / DoclingParser objects are created.
    """

    def __init__(self, options: Optional[DoclingOptions] = None):
        self.options = options or DoclingOptions()

    def get_converter(self) -> Any:
        """Return the process-wide cached DocumentConverter."""
        return get_cached_converter(self.options)
