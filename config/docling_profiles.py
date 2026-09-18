"""
Docling configuration -- single, unified OCR-first pipeline.

Previously this module shipped six document-type profiles (identity docs,
character-box forms, scanned documents, digital PDFs, mixed content, high
performance), each tuning Docling's OCR mode differently -- most importantly,
several of them set `force_full_page_ocr=False`, which tells Docling to TRUST
a PDF's own embedded text layer for any region that already contains
characters and skip OCR there entirely (see OcrMode.DEFAULT ->
PDF_AWARE_LAYOUT_REGIONS in the installed docling.datamodel.pipeline_options).

That trust assumption is exactly what broke digital-PDF extraction: a PDF
whose font has no ToUnicode CMap (common with custom/subsetted fonts in
bank-statement and government PDF generators) still "has text" by Docling's
own check -- it just decodes to glyph IDs or garbage -- and nothing ever
re-verifies it via OCR.

REPLACED WITH: one profile, ALWAYS full-page OCR, regardless of document
type or whether the file has a native text layer. Every document goes
through the exact same pipeline scanned/handwritten documents already used
(the one that was working correctly), and every threshold governing whether
a region/box survives is set to its most permissive value, trading precision
for recall across the board.

Consequence worth knowing: this is uniformly slower (every page is always
fully re-rasterized and OCR'd, even a clean digital PDF that had a perfectly
good text layer) and noisier (lower thresholds mean more false-positive
boxes on scan artifacts, watermarks, and faint marks). That trade was made
deliberately here, not accidentally.
"""

from typing import Optional

from idp.services.docling.options import DoclingOptions


# ═══════════════════════════════════════════════════════════════════════════
# THE ONLY PROFILE -- applied to every document type
# ═══════════════════════════════════════════════════════════════════════════

OCR_FIRST_PROFILE = DoclingOptions(
    # ── OCR: always full-page, never trust native PDF/embedded text ────────
    do_ocr=True,
    # Wired -> OcrMode.FULL_PAGE. This is the whole point of this file: every
    # region on every page is OCR'd regardless of what the PDF's own text
    # layer (if any) claims. Nothing is ever read from PDF metadata/embedded
    # text alone.
    force_full_page_ocr=True,
    ocr_lang=["english", "hindi"],

    # Lowest real detection-stage thresholds (verified this session to reach
    # the actual rapidocr engine via the params passthrough in
    # idp/services/docling/pipeline.py -- NOT det_db_thresh/det_db_box_thresh
    # below, which are [INERT], kept only as a historical/documentation
    # reference to what earlier profiles intended but never actually applied).
    # rapidocr's own defaults are thresh=0.3, box_thresh=0.2; these are
    # pushed to the floor for maximum bbox recall.
    #
    # max_candidates raised from the engine default (1000) as a safety margin
    # for dense character-box/comb-box grid forms, where the number of
    # individually-detected small regions on one page can approach that cap.
    rapidocr_params={"Det.thresh": 0.01, "Det.box_thresh": 0.05, "Det.max_candidates": 3000},

    # Lowest recognition-confidence floor: a recognized text box is never
    # discarded for being low-confidence. This does NOT create boxes the
    # detector missed (see rapidocr_params above for that) -- it only stops
    # already-detected text from being filtered out after the fact.
    ocr_text_score=0.01,

    # [INERT] -- det_limit_side_len/det_db_thresh/det_db_box_thresh/
    # rec_batch_num have no equivalent attribute on RapidOcrOptions in
    # docling 2.126.0 and are silently discarded by the pipeline. Left unset
    # here deliberately so nobody mistakes them for live knobs; use
    # rapidocr_params above for anything the real engine needs to see.

    # ── Layout: lowest region-detection gate, catch every faint region ─────
    # Wired -> pipeline_options.layout_options.engine_options.score_threshold.
    # This runs BEFORE OCR: it decides whether the layout model creates a
    # region/cluster at all. If a region is never created here, no OCR
    # threshold downstream can recover it -- this is the single most
    # upstream "bbox sensitivity" knob in the whole pipeline.
    layout_detection_threshold=0.01,

    # Maximum rasterization resolution: the biggest lever for small/faint
    # text regardless of source (scan or digital), since every page is now
    # always re-rasterized and OCR'd from a raster image.
    images_scale=3.0,

    # ── Tables: keep TableFormer on for every document type (some identity
    # documents have zero tables; the model simply reports none for those --
    # a small amount of wasted compute traded for one uniform pipeline) ────
    do_table_structure=True,
    table_mode="ACCURATE",  # FAST is forced to ACCURATE repo-wide regardless
                            # (see idp/services/docling/pipeline.py); stated
                            # here to avoid the misleading forced-override
                            # warning log that a "FAST" declaration triggers.

    # Reconcile TableFormer's predicted grid against the actual OCR'd text
    # cells (page.parsed_page, which force_full_page_ocr above populates
    # with OCR output -- see base_ocr_model.post_process_cells) rather than
    # the backend's own get_text_in_rect() path. For a raw image upload,
    # get_text_in_rect() is hardcoded to return "" (idp/... image backend
    # has no native text layer at all), so do_cell_matching=True is what
    # keeps table-cell text coming from OCR on every input type, images
    # included -- do_cell_matching=False would silently produce empty table
    # cells for image uploads specifically.
    do_cell_matching=True,

    # Lowest table-survival thresholds -- a table is never dropped by the
    # parser.py post-filter (Docling itself has no native confidence/
    # min-rows/min-cols knobs; these are enforced there after TableFormer
    # returns its grid).
    table_confidence_threshold=0.01,
    table_min_rows=1,
    table_min_cols=1,

    # ── Compute ──────────────────────────────────────────────────────────
    # Wired -> pipeline_options.accelerator_options via
    # idp/services/docling/accelerator.py. True requests AUTO (best available:
    # CUDA > MPS > XPU > CPU); an operator can pin a concrete device -- including
    # a specific GPU like "cuda:1" -- with IDP_ACCELERATOR_DEVICE, which
    # overrides this flag.
    #
    # Measured on Apple silicon: the layout model and TableFormer genuinely run
    # on MPS, but RapidOCR (~85% of per-page time) stays on ONNX Runtime's CPU
    # execution provider, because Docling only ever sets use_cuda/use_dml for it.
    # CoreML was benchmarked as an alternative and was ~2x SLOWER than the CPU
    # provider, so it is deliberately not enabled. Only a CUDA host moves OCR
    # itself onto the GPU.
    use_gpu=True,

    # Also forwarded to RapidOCR as EngineConfig.onnxruntime.intra_op_num_threads.
    # Clamped at runtime to the machine's real core count, so this reads as "use
    # every core, up to 30" rather than as a literal thread count -- values above
    # the core count would only oversubscribe the ONNX Runtime intra-op pool.
    num_threads=30,

    # Weights source. None -> $DOCLING_ARTIFACTS_PATH, else <repo>/models/docling,
    # else Docling's HuggingFace cache with on-demand downloads. When the local
    # directory is populated (python scripts/download_models.py) the layout model,
    # TableFormer AND RapidOCR all load from disk with no network access. Set
    # DOCLING_OFFLINE=true to make a missing local copy a hard startup error
    # instead of a silent fallback to downloading.
    artifacts_path=None,
)


# Every existing call site (idp/services/document_processor.py) resolves a
# profile through get_profile_for_document_type(doc_type, is_scanned=...).
# Both arguments are accepted and ignored -- kept only so no caller needs to
# change -- since there is now exactly one profile for every document type
# and every scan-status.
DOCLING_PROFILES = {
    "ocr_first": OCR_FIRST_PROFILE,
}


def get_profile(profile_name: str) -> DoclingOptions:
    """
    Get a Docling configuration profile by name.

    Args:
        profile_name: Profile identifier

    Returns:
        DoclingOptions instance

    Raises:
        KeyError: If profile not found
    """
    if profile_name not in DOCLING_PROFILES:
        available = ", ".join(DOCLING_PROFILES.keys())
        raise KeyError(
            f"Profile '{profile_name}' not found. "
            f"Available profiles: {available}"
        )
    return DOCLING_PROFILES[profile_name]


def get_profile_for_document_type(doc_type: str, is_scanned: Optional[bool] = None) -> DoclingOptions:
    """
    Return the single OCR-first profile, regardless of document type or
    scan-status.

    Args:
        doc_type: Ignored. Kept for call-site compatibility
            (idp/services/document_processor.py passes it and uses it as
            part of a parser cache key).
        is_scanned: Ignored. Every document -- digital or scanned -- goes
            through the same full-page OCR path now, so the preprocessor's
            content-based text-layer inspection no longer changes routing.

    Returns:
        OCR_FIRST_PROFILE, always.
    """
    return OCR_FIRST_PROFILE
