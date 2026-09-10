import threading
from typing import Any, Dict, Optional
from idp.services.docling.options import DoclingOptions
from idp.core.logging import logger

# ---------------------------------------------------------------------------
# Process-wide singleton for the Docling DocumentConverter.
#
# Docling's DocumentConverter initializes multiple heavyweight ONNX runtime
# sessions (layout model, table model, OCR detection/recognition) on first
# construction.  Re-creating the converter on every document invocation wastes
# 3-8 seconds of cold-start time per document and defeats per-page/per-doc
# ThreadPoolExecutor parallelism.
#
# This module-level cache ensures the converter is built exactly ONCE for the
# entire process lifetime and shared safely across all threads via an RLock.
# ---------------------------------------------------------------------------

_DOCLING_CONVERTER_LOCK: threading.RLock = threading.RLock()
_DOCLING_CONVERTERS: Dict[str, Any] = {}   # options_key -> DocumentConverter instance


def _get_options_key(options: DoclingOptions) -> str:
    """Fingerprint of relevant DoclingOptions fields used for cache invalidation."""
    return (
        f"{options.table_mode}|{options.do_ocr}|{options.do_table_structure}"
        f"|{options.ocr_model_name}|{options.det_model_path}|{options.rec_model_path}"
        f"|{'_'.join(options.ocr_lang)}"
        f"|{options.images_scale}|{options.det_db_thresh}|{options.det_db_box_thresh}"
        f"|{options.det_limit_side_len}|{options.enhance_contrast}|{options.deskew}"
        f"|{options.denoise}|{options.force_full_page_ocr}"
    )


def get_cached_converter(options: Optional[DoclingOptions] = None) -> Any:
    """
    Return the process-wide Docling DocumentConverter, building it once per
    unique configuration. Subsequent calls (from any thread) return the cached
    instance immediately without re-loading models.

    Thread-safe via module-level RLock: concurrent callers block until the
    initial build completes rather than each spawning a duplicate build.
    """
    global _DOCLING_CONVERTERS

    options = options or DoclingOptions()
    options_key = _get_options_key(options)

    # Fast-path: return cached converter if already built with this options key
    with _DOCLING_CONVERTER_LOCK:
        if options_key in _DOCLING_CONVERTERS:
            logger.debug(f"[DoclingCache] Returning cached DocumentConverter for key: {options_key}")
            return _DOCLING_CONVERTERS[options_key]

        # Cache miss: build once for this configuration
        logger.info(
            "[DoclingCache] Building Docling DocumentConverter for configuration "
            f"(options_key='{options_key}'). Subsequent calls will reuse this instance."
        )

        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = options.do_ocr
            pipeline_options.do_table_structure = options.do_table_structure



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

            # Configure TableFormer mode and thresholds
            if hasattr(pipeline_options, "table_structure_options"):
                table_opts = pipeline_options.table_structure_options
                table_opts.mode = options.table_mode
                
                # Apply table detection thresholds if available
                if hasattr(table_opts, "min_confidence"):
                    table_opts.min_confidence = options.table_confidence_threshold
                if hasattr(table_opts, "min_rows"):
                    table_opts.min_rows = options.table_min_rows
                if hasattr(table_opts, "min_cols"):
                    table_opts.min_cols = options.table_min_cols
                
                logger.info(
                    f"[DoclingCache] TableFormer mode={options.table_mode}, "
                    f"confidence>={options.table_confidence_threshold}"
                )

            # Wire image scaling (controls render DPI for OCR)
            if hasattr(pipeline_options, "images_scale"):
                pipeline_options.images_scale = options.images_scale
                logger.info(f"[DoclingCache] images_scale={options.images_scale}")

            # Wire image quality options if Docling exposes them
            if hasattr(pipeline_options, "do_image_enhancement"):
                pipeline_options.do_image_enhancement = options.enhance_contrast
            if hasattr(pipeline_options, "do_deskew"):
                pipeline_options.do_deskew = options.deskew
            if hasattr(pipeline_options, "do_denoise"):
                pipeline_options.do_denoise = options.denoise

            format_options = {"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
            converter = DocumentConverter(format_options=format_options)
            logger.info(
                "[DoclingCache] DocumentConverter built and cached. "
                f"images_scale={options.images_scale}, enhance_contrast={options.enhance_contrast}, "
                f"deskew={options.deskew}. "
                "ONNX models are now hot and will be reused for all future documents."
            )
        except Exception as e:
            logger.warning(
                f"[DoclingCache] Docling unavailable, falling back to MOCK converter: {e}"
            )
            converter = "MOCK"

        _DOCLING_CONVERTERS[options_key] = converter
        return converter


def prewarm_docling_converters() -> None:
    """Pre-initialize both Docling converter variants into memory.

    1. Table-disabled converter (for identity documents: Aadhaar, PAN, DL, Voter ID).
    2. Table-enabled ACCURATE converter (for tabular docs: Sanction Letter, KFS, App Form).

    Eliminates cold-start and re-initialization latency during API pipeline runs.
    """
    logger.info("[DoclingPrewarm] Pre-warming Docling converters...")
    try:
        # 1. Prewarm identity configuration (no tables)
        id_opts = DoclingOptions(do_table_structure=False, table_mode="FAST")
        get_cached_converter(id_opts)

        # 2. Prewarm tabular configuration (TableFormer ACCURATE)
        table_opts = DoclingOptions(do_table_structure=True, table_mode="ACCURATE")
        get_cached_converter(table_opts)
        logger.info("[DoclingPrewarm] Both Docling converters successfully pre-warmed.")
    except Exception as exc:
        logger.warning(f"[DoclingPrewarm] Error pre-warming Docling converters: {exc}")


def invalidate_converter_cache() -> None:
    """Force subsequent calls to get_cached_converter() to rebuild the converter.

    Use only in tests or when OCR model paths change at runtime.
    """
    global _DOCLING_CONVERTERS
    with _DOCLING_CONVERTER_LOCK:
        _DOCLING_CONVERTERS.clear()
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
