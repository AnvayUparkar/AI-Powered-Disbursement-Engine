"""
Docling configuration profiles for different document types.

Provides pre-tuned settings optimized for specific use cases:
- Digital forms with character boxes (Indian government forms)
- Scanned documents
- Mixed content documents
- Performance-optimized settings
"""

from typing import Optional

from idp.services.docling.options import DoclingOptions


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: IDENTITY DOCUMENTS (Aadhaar, PAN, DL, Voter ID)
# ═══════════════════════════════════════════════════════════════════════════
# Pure identity cards do not contain financial tables.
# Bypasses TableFormer completely to eliminate unnecessary CPU transformer passes.
# ═══════════════════════════════════════════════════════════════════════════

IDENTITY_DOCUMENT_PROFILE = DoclingOptions(
    do_table_structure=True,
    table_mode="ACCURATE",  # Always ACCURATE -- FAST mode disabled repo-wide (see pipeline.py).
                            # Moot here since do_table_structure=False skips TableFormer entirely,
                            # but "FAST" previously triggered a misleading forced-override warning
                            # on every identity-document parse for a value that was never honored.
    do_ocr=True,
    force_full_page_ocr=True,
    ocr_lang=["en", "hindi"],
    images_scale=2.0,
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="column_aware",
    max_num_pages=10,
    use_gpu=True,
    num_threads=2,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: CHARACTER BOX FORMS (e.g., Application Forms)
# ═══════════════════════════════════════════════════════════════════════════
# Optimized for forms where each character has a separate box.
# TableFormer detects character boxes as individual cells.
# Post-processing merges them using comb-box detector.
# ═══════════════════════════════════════════════════════════════════════════

CHARACTER_BOX_FORMS_PROFILE = DoclingOptions(
    # Table Detection (relaxed to handle fine-grained grids)
    do_table_structure=True,
    table_mode="ACCURATE",
    table_confidence_threshold=0.1,  # Lower threshold to catch character grids
    table_min_rows=1,  # Allow single-row "tables" (character sequences)
    table_min_cols=2,  # Min 3 chars to qualify
    
    # Character Box Handling
    merge_character_boxes=True,
    character_box_max_width=30.0,  # Typical char box width
    character_box_gap_threshold=2.0,
    
    # Cell Merging (disabled to preserve individual chars for comb-box detector)
    merge_adjacent_cells=True,  # Let comb-box detector handle merging
    detect_cell_spans=True,
    
    # OCR Settings
    do_ocr=True,
    # PRODUCTION FIX: force_full_page_ocr=False leaves Docling's OCR mode at
    # its library default, OcrMode.DEFAULT -> PDF_AWARE_LAYOUT_REGIONS, which
    # explicitly "eliminates clusters that contain exclusively text PDF
    # cells" from OCR (see RapidOcrOptions / OcrMode in the installed
    # docling.datamodel.pipeline_options). A scanned/photographed form has no
    # native PDF text anywhere, so nothing gets eliminated and every cell
    # still gets OCR'd -- comb-box characters merge correctly "by accident".
    # A genuinely digital PDF's comb-box grid usually DOES have some native
    # text in that region (even if it's one mis-segmented multi-character
    # run rather than one token per printed cell), so PDF_AWARE_LAYOUT_REGIONS
    # skips OCR there and CombBoxDetector never gets clean per-character
    # tokens to merge. force_full_page_ocr=True forces OcrMode.FULL_PAGE
    # (see OcrOptions._apply_force_full_page_ocr), so this profile always
    # re-OCRs every character box regardless of what native text exists --
    # matching the already-correct scanned-document behavior for digital
    # inputs too, without touching CombBoxDetector/serializer.py at all.
    # NOTE: scanned application forms (is_scanned=True) never reach this profile;
    # they are routed to SCANNED_DOCUMENTS_PROFILE by get_profile_for_document_type.
    # This profile is digital-only, so force_full_page_ocr=False is correct here.
    force_full_page_ocr=False,
    ocr_lang=["en", "hindi"],
    
    # OCR Quality (balanced)
    det_limit_side_len=1536,
    det_db_thresh=0.05,
    det_db_box_thresh=0.1,
    rec_batch_num=6,
    
    # Image Processing
    images_scale=2.0,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="column_aware",
    
    # Performance
    max_num_pages=50,
    use_gpu= True,
    num_threads=2,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: SCANNED DOCUMENTS (Low Quality)
# ═══════════════════════════════════════════════════════════════════════════
# Aggressive OCR with image enhancement for poor quality scans.
# ═══════════════════════════════════════════════════════════════════════════

SCANNED_DOCUMENTS_PROFILE = DoclingOptions(
    # Table Detection (strict, avoid false positives)
    do_table_structure=True,
    table_mode="ACCURATE",
    table_confidence_threshold=0.1,  # Higher threshold for scans
    table_min_rows=1,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.8,
    detect_cell_spans=True,
    
    # OCR Settings (aggressive for scanned images)
    do_ocr=True,
    force_full_page_ocr=True,  # Always run OCR on scanned docs
    ocr_lang=["en", "hindi"],
    
    # OCR Quality (high quality for low-res scans)
    det_limit_side_len = 1536, # Detection input size (higher = slower, better)
    det_db_thresh = 0.05,  # Detection threshold (lower = more boxes)
    det_db_box_thresh = 0.1,  # Box confidence threshold (lower = detect low-contrast/faint text)
    rec_batch_num = 6,  # Batch size for recognition
    
    # Image Processing (enhanced)
    images_scale=3.0,  # 3x upscaling for low-res scans
    enhance_contrast=True,
    denoise=True,
    deskew=True,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="column_aware",
    
    # Performance
    max_num_pages=100,
    use_gpu=True,
    num_threads=2,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: SCANNED CHARACTER BOX FORMS (e.g., scanned Application Forms)
# ═══════════════════════════════════════════════════════════════════════════
# Combines:
#   - SCANNED_DOCUMENTS_PROFILE OCR aggressiveness:
#       force_full_page_ocr=True, images_scale=3.0, low det thresholds
#   - CHARACTER_BOX_FORMS_PROFILE structural settings:
#       fine-grained character_box_max_width, tight table_confidence_threshold,
#       merge_character_boxes for downstream CombBoxDetector
#
# Why both?  A scanned application form is a raster image (no native PDF
# text), so force_full_page_ocr=True is mandatory.  But its fields are
# individual character boxes that the comb-box detector must re-assemble, so
# the tight table detection settings are also required.  Using
# SCANNED_DOCUMENTS_PROFILE alone gets the OCR right but loses the comb-box
# structural tuning; CHARACTER_BOX_FORMS_PROFILE alone gets the structure
# right but force_full_page_ocr=False causes Docling to skip OCR on raster
# regions that happen to overlay any mis-detected native text cluster.
# ═══════════════════════════════════════════════════════════════════════════

SCANNED_CHARACTER_BOX_FORMS_PROFILE = DoclingOptions(
    # Table Detection — same relaxed gates as CHARACTER_BOX_FORMS_PROFILE
    # so fine-grained character grids (each cell = one printed character)
    # are caught by TableFormer before CombBoxDetector merges them.
    do_table_structure=True,
    table_mode="ACCURATE",
    table_confidence_threshold=0.1,  # Low: character-box grids score low in TableFormer
    table_min_rows=1,                # Single-row comb sequences (date, mobile, DOB)
    table_min_cols=2,

    # Character Box Handling — preserves individual tokens for CombBoxDetector
    merge_character_boxes=True,
    character_box_max_width=30.0,    # Typical scanned char-box cell width (pt)
    character_box_gap_threshold=2.0,
    merge_adjacent_cells=True,
    cell_merge_threshold=0.8,
    detect_cell_spans=True,

    # OCR — full-page, aggressive: raster pages have no native text layer
    do_ocr=True,
    force_full_page_ocr=True,        # MANDATORY for scanned raster images
    ocr_lang=["en", "hindi"],

    # OCR detector thresholds — same as SCANNED_DOCUMENTS_PROFILE
    det_limit_side_len=1536,
    det_db_thresh=0.05,              # Low: catch faint/low-contrast ink
    det_db_box_thresh=0.1,
    rec_batch_num=6,

    # Image scale — 3.0x so individual character-box cells have enough
    # pixels for reliable per-glyph detection; 2.0x is insufficient for
    # thin-stroked handwritten or low-DPI printed character grids.
    images_scale=3.0,
    enhance_contrast=True,
    denoise=True,
    deskew=True,

    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="column_aware",

    # Performance
    max_num_pages=100,
    use_gpu=True,
    num_threads=6,
)

# ═══════════════════════════════════════════════════════════════════════════
# Optimized for computer-generated PDFs with embedded text.
# Minimal OCR, fast processing.
# ═══════════════════════════════════════════════════════════════════════════

DIGITAL_PDF_PROFILE = DoclingOptions(
    # Table Detection
    # table_confidence_threshold: 0.4 (not 0.6) -- character-box grids score
    # lower confidence in TableFormer than proper tabular tables; 0.6 silently
    # drops the entire field grid before CombBoxDetector ever sees it.
    # table_min_rows: 1 (not 2) -- a single horizontal comb row (e.g. the
    # 10-digit mobile number or date field) is a 1-row "table"; 2 would discard
    # every single-row comb sequence even when per-char OCR tokens are correct.
    do_table_structure=True,
    table_mode="ACCURATE",  # Always ACCURATE -- FAST mode disabled repo-wide
    table_confidence_threshold=0.4,
    table_min_rows=1,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.9,  # Strict merging for clean text
    detect_cell_spans=True,
    
    # OCR Settings
    do_ocr=True,
    # PRODUCTION FIX: same reasoning as CHARACTER_BOX_FORMS_PROFILE (lines 70-85).
    # Docling's default OcrMode.PDF_AWARE_LAYOUT_REGIONS skips re-OCR for any
    # layout region that already carries native PDF text. For a digital comb-box
    # form the entire field region is excluded — CombBoxDetector never receives
    # per-character tokens and the field never merges (0 merged tokens, 0 bbox).
    # force_full_page_ocr=True switches to OcrMode.FULL_PAGE, re-OCRing every
    # region regardless of native text so digital comb-box grids follow the same
    # reliable per-character OCR path as scanned documents already do.
    force_full_page_ocr=False,
    ocr_on_tables_only=False,
    ocr_lang=["en"],
    
    # OCR Quality (standard, rarely used)
    det_limit_side_len=1536,
    det_db_thresh=0.05,
    det_db_box_thresh=0.2,
    rec_batch_num=6,
    
    # Image Processing
    # images_scale=2.0 (not 1.5): individual comb-box character cells are
    # ~18-22px wide at the native PDF resolution. At 1.5× RapidOCR struggles
    # to segment thin strokes cleanly; 2.0× (same as CHARACTER_BOX_FORMS)
    # gives the detector enough pixels per glyph for reliable per-char tokens.
    images_scale=2.0,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="column_aware",
    
    # Performance (fast)
    max_num_pages=100,
    use_gpu=True,
    num_threads=2,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: MIXED CONTENT (Digital + Scanned)
# ═══════════════════════════════════════════════════════════════════════════
# Balanced settings for documents with both native text and scanned pages.
# ═══════════════════════════════════════════════════════════════════════════

MIXED_CONTENT_PROFILE = DoclingOptions(
    # Table Detection
    do_table_structure=True,
    table_mode="ACCURATE",
    table_confidence_threshold=0.1,
    table_min_rows=1,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.8,
    detect_cell_spans=True,
    
    # OCR Settings (adaptive)
    do_ocr=True,
    force_full_page_ocr=True,  # Use native text when available
    ocr_lang=["en", "hindi"],
    
    # OCR Quality (balanced)
    det_limit_side_len=1536,
    det_db_thresh=0.05,
    det_db_box_thresh=0.2,
    rec_batch_num=6,
    
    # Image Processing (moderate)
    images_scale=2.0,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="column_aware",
    
    # Performance
    max_num_pages=100,
    use_gpu=True,
    num_threads=2,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: HIGH PERFORMANCE (Speed Optimized)
# ═══════════════════════════════════════════════════════════════════════════
# Fast processing for real-time/batch scenarios. Lower accuracy acceptable.
# ═══════════════════════════════════════════════════════════════════════════

HIGH_PERFORMANCE_PROFILE = DoclingOptions(
    # Table Detection (fast mode)
    do_table_structure=True,
    table_mode="ACCURATE",  # Always ACCURATE -- FAST mode disabled repo-wide
    table_confidence_threshold=0.6,
    table_min_rows=2,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.8,
    detect_cell_spans=False,  # Skip span detection
    
    # OCR Settings (fast)
    do_ocr=True,
    force_full_page_ocr=True,
    ocr_lang=["en"],
    
    # OCR Quality (lower resolution, faster)
    det_limit_side_len=1536,  # Lower resolution
    det_db_thresh=0.05,
    det_db_box_thresh=0.2,
    rec_batch_num=6,  # Larger batches
    
    # Image Processing (minimal)
    images_scale=1.5,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis (simplified)
    do_layout_analysis=True,
    detect_reading_order=False,  # Skip reading order
    
    # Performance (maximum speed)
    max_num_pages=100,
    use_gpu=True,
    num_threads=8,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

DOCLING_PROFILES = {
    "identity_document": IDENTITY_DOCUMENT_PROFILE,
    "character_box_forms": CHARACTER_BOX_FORMS_PROFILE,
    "scanned_character_box_forms": SCANNED_CHARACTER_BOX_FORMS_PROFILE,
    "scanned_documents": SCANNED_DOCUMENTS_PROFILE,
    "digital_pdf": DIGITAL_PDF_PROFILE,
    "mixed_content": MIXED_CONTENT_PROFILE,
    "high_performance": HIGH_PERFORMANCE_PROFILE,
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
    Auto-select profile based on document type, refined by actual scan-status
    inspection when available.

    Args:
        doc_type: Document type (e.g., "application_form", "aadhaar", "kfs")
        is_scanned: Result of DocumentPreprocessor's content-based text-layer
            inspection (PreprocessedDocument.is_scanned_pdf), when known.
            True/False takes precedence over the doc_type-based OCR guess
            below, since it reflects the actual PDF text layer rather than a
            filename/doc-type assumption. Pass None (default) to preserve the
            legacy doc_type-only heuristic for callers that haven't inspected
            the file (e.g. profile lookups made before preprocessing runs).

    Returns:
        Appropriate DoclingOptions profile
    """
    # Pure identity cards: bypass TableFormer completely (zero tables in Aadhaar/PAN/DL/Voter ID)
    # Structural choice, independent of scan status.
    if doc_type in ["aadhaar", "pan", "pan_card", "dl", "driving_license", "voter_id", "passport"]:
        return IDENTITY_DOCUMENT_PROFILE

    # Indian government forms with character boxes (e.g. application form).
    # Two sub-profiles exist — one per scan status — because force_full_page_ocr
    # must differ:
    #   Scanned  → SCANNED_CHARACTER_BOX_FORMS_PROFILE  (force_full_page_ocr=True
    #              + scanned OCR thresholds + char-box structural settings)
    #   Digital  → CHARACTER_BOX_FORMS_PROFILE           (force_full_page_ocr=False
    #              + char-box structural settings)
    elif doc_type in ["application_form"]:
        if is_scanned is True:
            return SCANNED_CHARACTER_BOX_FORMS_PROFILE
        return CHARACTER_BOX_FORMS_PROFILE


    # Content-inspected scan status takes precedence over the doc_type guess:
    # a "bank_statement" that is actually a clean digital export should not be
    # force-rasterized and re-OCR'd, and a "loan_agreement" that is actually a
    # scanned copy should get the aggressive scanned-document OCR settings.
    elif is_scanned is True:
        return SCANNED_DOCUMENTS_PROFILE

    elif is_scanned is False:
        return DIGITAL_PDF_PROFILE

    # is_scanned unknown -- fall back to the pre-inspection doc_type heuristic.
    # Financial documents (typically scanned)
    elif doc_type in ["bank_statement", "salary_slip"]:
        return SCANNED_DOCUMENTS_PROFILE

    # Legal/contract documents (typically digital)
    elif doc_type in ["loan_agreement", "sanction_letter", "nach_mandate"]:
        return DIGITAL_PDF_PROFILE

    # Default: mixed content
    else:
        return MIXED_CONTENT_PROFILE
