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
_DOCLING_CONVERTERS: Dict[str, Any] = {}   # options_key -> DocumentConverter instance
_DOCLING_CONVERTER_CACHE: Dict[str, Any] = _DOCLING_CONVERTERS  # alias for backwards/main compatibility


def _get_options_key(options: DoclingOptions) -> str:
    """Fingerprint of relevant DoclingOptions fields used for cache invalidation."""
    return (
        f"{options.table_mode}|{options.do_ocr}|{options.do_table_structure}"
        f"|{options.do_cell_matching}|{options.images_scale}|{options.layout_detection_threshold}"
        f"|{options.ocr_model_name}|{options.det_model_path}|{options.rec_model_path}"
        f"|{'_'.join(options.ocr_lang)}"
        f"|{options.enhance_contrast}|{options.deskew}"
        f"|{options.denoise}|{options.force_full_page_ocr}"
        # Device and thread count change the converter that gets built, so profiles that
        # differ only by these must not share a cached instance.
        f"|{options.use_gpu}|{options.num_threads}|{options.ocr_text_score}"
        f"|{sorted((options.rapidocr_params or {}).items())}"
        # Weights source changes which models get loaded into the converter.
        f"|{options.artifacts_path}"
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
            from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
            from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions

            from idp.services.docling.accelerator import build_accelerator_options
            from idp.services.docling.model_store import resolve_artifacts_path

            pipeline_options = PdfPipelineOptions()

            # Device selection. Previously use_gpu and num_threads were set on every profile
            # but read nowhere, so they had no effect at all: Docling fell back to its own
            # default of device="auto". AUTO resolves to the best available accelerator
            # (CUDA > MPS > XPU > CPU); CPU pins it explicitly.
            #
            # Delegated to accelerator.build_accelerator_options so an operator can pin a
            # concrete device via IDP_ACCELERATOR_DEVICE and so num_threads gets clamped to
            # the real core count -- Docling forwards num_threads to RapidOCR as
            # intra_op_num_threads, where oversubscription is a real cost.
            #
            # NOTE: this governs the torch-based models -- the layout model and TableFormer.
            # RapidOCR runs on ONNX Runtime and only special-cases CUDA and DirectML, so OCR
            # stays on CPU on macOS regardless of what is set here.
            accel_options, effective_device = build_accelerator_options(
                use_gpu=options.use_gpu, num_threads=options.num_threads
            )
            pipeline_options.accelerator_options = accel_options
            logger.info(
                "[DoclingCache] Accelerator: device=%s (effective=%s) num_threads=%s (use_gpu=%s)",
                accel_options.device,
                effective_device,
                accel_options.num_threads,
                options.use_gpu,
            )

            # Local weights. When a populated artifacts directory is available, every
            # model (layout, TableFormer AND RapidOCR) loads from disk with no network
            # access; when it is not, this resolves to None and Docling falls back to
            # its HuggingFace cache exactly as before.
            artifacts_path = resolve_artifacts_path(options.artifacts_path)
            if artifacts_path is not None:
                pipeline_options.artifacts_path = artifacts_path
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
                    
                    # OCR detection sensitivity.
                    #
                    # det_limit_side_len / det_db_thresh / det_db_box_thresh / rec_batch_num
                    # do NOT exist on RapidOcrOptions, so the hasattr guards that used to sit
                    # here never fired and every value configured for them was discarded.
                    # text_score is the real equivalent of det_db_box_thresh -- the minimum
                    # confidence a detected text box needs to survive -- and rapidocr_params
                    # is the passthrough for anything else the engine accepts.
                    ocr_opts.text_score = options.ocr_text_score
                    if options.rapidocr_params:
                        ocr_opts.rapidocr_params = dict(options.rapidocr_params)
                    
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

            # NOTE: do_image_enhancement / do_deskew / do_denoise do not exist on
            # PdfPipelineOptions in docling 2.126.0, so these three guards never fire.
            # Left in place for when Docling exposes them.
            if hasattr(pipeline_options, "do_image_enhancement"):
                pipeline_options.do_image_enhancement = options.enhance_contrast
            if hasattr(pipeline_options, "do_deskew"):
                pipeline_options.do_deskew = options.deskew
            if hasattr(pipeline_options, "do_denoise"):
                pipeline_options.do_denoise = options.denoise

            # Raw images (PNG/JPG/TIFF) resolve to InputFormat.IMAGE, a separate key from
            # "pdf": registering only the PDF entry silently left image uploads on Docling's
            # stock defaults, so none of the options above applied to them at all.
            #
            # Images also get their own OCR mode. An image carries no programmatic text
            # layer, so OcrMode.DEFAULT's cluster-based region selection has nothing to skip
            # and merely risks dropping regions the layout model missed. The PDF options are
            # deep-copied first so the PDF path keeps its own (profile-driven) mode.
            image_pipeline_options = pipeline_options.model_copy(deep=True)
            if image_pipeline_options.ocr_options is not None:
                image_pipeline_options.ocr_options.mode = OcrMode.FULL_PAGE

            format_options = {
                "pdf": PdfFormatOption(pipeline_options=pipeline_options),
                "image": ImageFormatOption(pipeline_options=image_pipeline_options),
            }
            converter = DocumentConverter(format_options=format_options)
            logger.info(
                "[DoclingCache] Registered format options: pdf (ocr_mode=%s), "
                "image (ocr_mode=%s)",
                getattr(pipeline_options.ocr_options, "mode", None),
                getattr(image_pipeline_options.ocr_options, "mode", None),
            )
            logger.info(
                "[DoclingCache] DocumentConverter built and cached. "
                f"images_scale={options.images_scale}, enhance_contrast={options.enhance_contrast}, "
                f"deskew={options.deskew}. "
                "ONNX models are now hot and will be reused for all future documents."
            )
        except FileNotFoundError:
            # DOCLING_OFFLINE was demanded but the local weights are absent
            # (raised by resolve_artifacts_path). Degrading to the MOCK converter
            # here would turn an explicit deployment misconfiguration into
            # silently empty extractions, so this one propagates.
            raise
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
        id_opts = DoclingOptions(do_table_structure=False, table_mode="ACCURATE")
        get_cached_converter(id_opts)

        # 2. Prewarm tabular configuration (TableFormer ACCURATE)
        table_opts = DoclingOptions(do_table_structure=True, table_mode="ACCURATE")
        get_cached_converter(table_opts)
        logger.info("[DoclingPrewarm] Both Docling converters successfully pre-warmed.")
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
