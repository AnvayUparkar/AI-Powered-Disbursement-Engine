import threading
from typing import Any, Optional
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
_DOCLING_CONVERTER_INSTANCE: Any = None   # None = not yet initialized
_DOCLING_CONVERTER_OPTIONS_KEY: Optional[str] = None  # tracks options fingerprint


def _get_options_key(options: DoclingOptions) -> str:
    """Fingerprint of relevant DoclingOptions fields used for cache invalidation."""
    return (
        f"{options.table_mode}|{options.do_ocr}|{options.do_table_structure}"
        f"|{options.ocr_model_name}|{options.det_model_path}|{options.rec_model_path}"
        f"|{'_'.join(options.ocr_lang)}"
    )


def get_cached_converter(options: Optional[DoclingOptions] = None) -> Any:
    """
    Return the process-wide Docling DocumentConverter, building it once on
    first call.  Subsequent calls (from any thread) return the cached instance
    immediately without re-loading any ONNX models.

    Thread-safe via module-level RLock: concurrent callers block until the
    initial build completes rather than each spawning a duplicate build.
    """
    global _DOCLING_CONVERTER_INSTANCE, _DOCLING_CONVERTER_OPTIONS_KEY

    options = options or DoclingOptions()
    options_key = _get_options_key(options)

    # Fast-path: return cached converter if already built with same options
    with _DOCLING_CONVERTER_LOCK:
        if _DOCLING_CONVERTER_INSTANCE is not None and _DOCLING_CONVERTER_OPTIONS_KEY == options_key:
            logger.debug("[DoclingCache] Returning cached DocumentConverter (no re-init).")
            return _DOCLING_CONVERTER_INSTANCE

        # Cache miss or options changed: build once
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

            # Configure Docling-managed OCR engine (RapidOCR PP-OCRv6)
            if options.do_ocr:
                try:
                    from docling.datamodel.pipeline_options import RapidOcrOptions
                    ocr_opts = RapidOcrOptions(
                        backend="onnxruntime",
                        force_full_page_ocr=True,
                        lang=options.ocr_lang
                    )
                    if options.det_model_path:
                        ocr_opts.det_model_path = options.det_model_path
                    if options.rec_model_path:
                        ocr_opts.rec_model_path = options.rec_model_path
                    pipeline_options.ocr_options = ocr_opts
                    logger.info(
                        f"[DoclingCache] Configured Docling-managed RapidOCR engine: {options.ocr_model_name}"
                    )
                except Exception as ocr_err:
                    logger.warning(f"[DoclingCache] RapidOcrOptions config skipped: {ocr_err}")

            # Configure TableFormer mode if available
            if hasattr(pipeline_options, "table_structure_options"):
                pipeline_options.table_structure_options.mode = options.table_mode

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

        _DOCLING_CONVERTER_INSTANCE = converter
        _DOCLING_CONVERTER_OPTIONS_KEY = options_key
        return _DOCLING_CONVERTER_INSTANCE


def invalidate_converter_cache() -> None:
    """Force the next call to get_cached_converter() to rebuild the converter.

    Use only in tests or when OCR model paths change at runtime.
    """
    global _DOCLING_CONVERTER_INSTANCE, _DOCLING_CONVERTER_OPTIONS_KEY
    with _DOCLING_CONVERTER_LOCK:
        _DOCLING_CONVERTER_INSTANCE = None
        _DOCLING_CONVERTER_OPTIONS_KEY = None
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
