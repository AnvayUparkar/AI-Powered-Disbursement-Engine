import os
import threading
from typing import Any, Dict, Optional

# Register PyTorch CUDA library directory for ONNX Runtime / RapidOCR on Windows
try:
    import torch
    torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    if os.path.exists(torch_lib):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(torch_lib)
        os.environ["PATH"] = torch_lib + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

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
_DOCLING_CONVERTERS: Dict[str, Any] = {}   # options_key -> DocumentConverter instance
_DOCLING_CONVERTER_CACHE: Dict[str, Any] = _DOCLING_CONVERTERS  # alias for backwards/main compatibility

# Docling's native PDF backend (docling-parse) is not safe to call concurrently:
# two threads inside converter.convert() on the same cached instance corrupt its
# heap and take the whole worker process down with a SIGTRAP, leaving uvicorn's
# reloader holding the port so every later request hangs. Serialize conversions
# process-wide; building is already guarded by _DOCLING_CONVERTER_LOCK.
_DOCLING_CONVERT_LOCK: threading.RLock = threading.RLock()


def docling_convert_lock() -> threading.RLock:
    """Lock that must be held for the duration of any converter.convert() call."""
    return _DOCLING_CONVERT_LOCK



def _get_options_key(options: DoclingOptions) -> str:
    """Fingerprint of relevant DoclingOptions fields used for cache invalidation."""
    return (
        f"{options.table_mode}|{options.do_ocr}|{options.do_table_structure}"
        f"|{options.do_cell_matching}|{options.images_scale}|{options.layout_detection_threshold}"
        f"|{options.ocr_model_name}|{options.det_model_path}|{options.rec_model_path}"
        f"|{'_'.join(options.ocr_lang)}"
        f"|{options.images_scale}|{options.det_db_thresh}|{options.det_db_box_thresh}"
        f"|{options.det_limit_side_len}|{options.enhance_contrast}|{options.deskew}"
        f"|{options.denoise}|{options.force_full_page_ocr}"
        f"|{options.use_gpu}|{options.num_threads}"
    )


def get_cached_converter(options: Optional[DoclingOptions] = None) -> Any:
    """
    Return the process-wide Docling DocumentConverter for the given options
    profile, building it once per distinct profile. Subsequent calls (from
    any thread, for any previously-seen profile) return the cached instance
    immediately without re-loading any ONNX models.

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

        # Cache miss: build once for this profile
        logger.info(
            "[DoclingCache] Building Docling DocumentConverter for configuration "
            f"(options_key='{options_key}'). Subsequent calls will reuse this instance."
        )

        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.datamodel.settings import settings as docling_settings
            from idp.core.config import settings as idp_settings

            # Makes conv_result.timings report per-model time (layout, ocr, table_structure, ...)
            # for the per-document timing summary. Global Docling switch; only records timestamps.
            docling_settings.debug.profile_pipeline_timings = idp_settings.DOCLING_PROFILE_TIMINGS

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = options.do_ocr
            pipeline_options.do_table_structure = options.do_table_structure

            # Wire AcceleratorOptions (GPU / CPU acceleration with graceful fallback)
            try:
                from docling.datamodel.pipeline_options import (
                    AcceleratorOptions,
                    AcceleratorDevice,
                )
                try:
                    import torch
                    has_cuda = bool(torch.cuda.is_available())
                except Exception:
                    has_cuda = False

                if options.use_gpu and has_cuda:
                    device = AcceleratorDevice.CUDA
                elif options.use_gpu:
                    device = AcceleratorDevice.AUTO
                else:
                    device = AcceleratorDevice.CPU

                num_threads = max(1, int(options.num_threads or 4))
                pipeline_options.accelerator_options = AcceleratorOptions(
                    num_threads=num_threads,
                    device=device,
                )
                logger.info(
                    f"[DoclingCache] Configured AcceleratorOptions: device={device}, "
                    f"num_threads={num_threads}, use_gpu={options.use_gpu} (CUDA torch={has_cuda})"
                )
            except Exception as acc_err:
                logger.warning(f"[DoclingCache] AcceleratorOptions config skipped: {acc_err}")

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
                    from docling.datamodel.pipeline_options import RapidOcrOptions, OcrMode
                    ocr_opts = RapidOcrOptions(
                        backend="onnxruntime",
                        mode=OcrMode.FULL_PAGE if options.force_full_page_ocr else OcrMode.DEFAULT,
                        lang=options.ocr_lang
                    )
                    
                    # Custom model paths
                    if options.det_model_path:
                        ocr_opts.det_model_path = options.det_model_path
                    if options.rec_model_path:
                        ocr_opts.rec_model_path = options.rec_model_path
                    if options.cls_model_path:
                        ocr_opts.cls_model_path = options.cls_model_path
                    
                    ocr_opts.scale = options.images_scale
                    if ocr_opts.rapidocr_params is None:
                        ocr_opts.rapidocr_params = {}
                    if options.det_limit_side_len is not None:
                        ocr_opts.rapidocr_params["Det.limit_side_len"] = options.det_limit_side_len
                    if options.det_db_thresh is not None:
                        ocr_opts.rapidocr_params["Det.thresh"] = options.det_db_thresh
                    if options.det_db_box_thresh is not None:
                        ocr_opts.rapidocr_params["Det.box_thresh"] = options.det_db_box_thresh
                    if options.rec_batch_num is not None:
                        ocr_opts.rapidocr_params["Rec.batch_num"] = options.rec_batch_num
                    
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

            try:
                from docling.datamodel.base_models import InputFormat
                format_options = {
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
                try:
                    from docling.document_converter import ImageFormatOption
                    format_options[InputFormat.IMAGE] = ImageFormatOption(pipeline_options=pipeline_options)
                except Exception:
                    pass
            except Exception:
                format_options = {"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
            converter = DocumentConverter(format_options=format_options)
            logger.info(
                "[DoclingCache] DocumentConverter built and cached. "
                f"images_scale={options.images_scale}. "
                # enhance_contrast/deskew are intentionally omitted here: the
                # installed Docling has no do_image_enhancement/do_deskew hook
                # on PdfPipelineOptions (see the hasattr guards above), so
                # these DoclingOptions fields never affect this converter --
                # logging them next to real wired options implied otherwise.
                # Actual scan deskew now runs pre-Docling in
                # DocumentProcessor._apply_scan_correction (see its own
                # "Applied scan correction" log line for per-document status).
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
    """Pre-initialize a Docling DocumentConverter for every active document profile.

    Iterates DOCLING_PROFILES and calls get_cached_converter() for each one so that
    the heavyweight ONNX model load happens at pod startup — not on the first real
    document request.  Each profile produces a distinct options_key (num_threads,
    ocr_lang, force_full_page_ocr, etc. all vary), so every profile that will be
    used at runtime must be explicitly warmed here.

    The previous implementation warmed two bare DoclingOptions() instances with
    default num_threads=6, but all profiles use num_threads=2 (or 8 for
    HIGH_PERFORMANCE).  The key mismatch caused a cold-build cache miss on every
    real request, defeating the purpose of the cache entirely.
    """
    from config.docling_profiles import DOCLING_PROFILES

    logger.info("[DoclingPrewarm] Pre-warming Docling converters...")
    try:
        warmed = []
        for profile_name, opts in DOCLING_PROFILES.items():
            get_cached_converter(opts)
            warmed.append(profile_name)
        logger.info(
            f"[DoclingPrewarm] All {len(warmed)} Docling converters successfully "
            f"pre-warmed: {', '.join(warmed)}"
        )
    except Exception as exc:
        logger.warning(f"[DoclingPrewarm] Error pre-warming Docling converters: {exc}")


def invalidate_converter_cache() -> None:
    """Force subsequent calls to get_cached_converter() to rebuild every converter.

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
