"""
Docling configuration profiles for different document types.

Provides pre-tuned settings optimized for specific use cases:
- Digital forms with character boxes (Indian government forms)
- Scanned documents
- Mixed content documents
- Performance-optimized settings
"""

from idp.services.docling.options import DoclingOptions


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: IDENTITY DOCUMENTS (Aadhaar, PAN, DL, Voter ID)
# ═══════════════════════════════════════════════════════════════════════════
# Pure identity cards do not contain financial tables.
# Bypasses TableFormer completely to eliminate unnecessary CPU transformer passes.
# ═══════════════════════════════════════════════════════════════════════════

IDENTITY_DOCUMENT_PROFILE = DoclingOptions(
    do_table_structure=False,
    table_mode="FAST",
    do_ocr=True,
    force_full_page_ocr=False,
    ocr_lang=["english", "hindi"],
    images_scale=2.0,
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="spatial",
    max_num_pages=10,
    use_gpu=False,
    num_threads=4,
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
    table_confidence_threshold=0.4,  # Lower threshold to catch character grids
    table_min_rows=1,  # Allow single-row "tables" (character sequences)
    table_min_cols=3,  # Min 3 chars to qualify
    
    # Character Box Handling
    merge_character_boxes=True,
    character_box_max_width=30.0,  # Typical char box width
    character_box_gap_threshold=5.0,
    
    # Cell Merging (disabled to preserve individual chars for comb-box detector)
    merge_adjacent_cells=False,  # Let comb-box detector handle merging
    detect_cell_spans=True,
    
    # OCR Settings (use native text when available)
    do_ocr=True,
    force_full_page_ocr=False,  # Extract digital text natively
    ocr_lang=["english", "hindi"],
    
    # OCR Quality (balanced)
    det_limit_side_len=960,
    det_db_thresh=0.3,
    det_db_box_thresh=0.6,
    rec_batch_num=6,
    
    # Image Processing
    images_scale=2.0,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="spatial",
    
    # Performance
    max_num_pages=50,
    use_gpu=False,
    num_threads=4,
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
    table_confidence_threshold=0.7,  # Higher threshold for scans
    table_min_rows=2,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.8,
    detect_cell_spans=True,
    
    # OCR Settings (aggressive for scanned images)
    do_ocr=True,
    force_full_page_ocr=True,  # Always run OCR on scanned docs
    ocr_lang=["english", "hindi"],
    
    # OCR Quality (high quality for low-res scans)
    det_limit_side_len=1280,  # Higher resolution
    det_db_thresh=0.2,  # Lower threshold = more text boxes
    det_db_box_thresh=0.5,
    rec_batch_num=4,
    
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
    use_gpu=False,
    num_threads=4,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE: DIGITAL PDFS (Native Text)
# ═══════════════════════════════════════════════════════════════════════════
# Optimized for computer-generated PDFs with embedded text.
# Minimal OCR, fast processing.
# ═══════════════════════════════════════════════════════════════════════════

DIGITAL_PDF_PROFILE = DoclingOptions(
    # Table Detection (standard)
    do_table_structure=True,
    table_mode="ACCURATE",  # Always ACCURATE -- FAST mode disabled repo-wide
    table_confidence_threshold=0.6,
    table_min_rows=2,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.9,  # Strict merging for clean text
    detect_cell_spans=True,
    
    # OCR Settings (minimal, only for images/missing text)
    do_ocr=True,
    force_full_page_ocr=False,  # Use native PDF text
    ocr_on_tables_only=False,
    ocr_lang=["english"],
    
    # OCR Quality (standard, rarely used)
    det_limit_side_len=960,
    det_db_thresh=0.3,
    det_db_box_thresh=0.6,
    rec_batch_num=6,
    
    # Image Processing (minimal)
    images_scale=1.5,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="spatial",
    
    # Performance (fast)
    max_num_pages=100,
    use_gpu=False,
    num_threads=6,
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
    table_confidence_threshold=0.5,
    table_min_rows=2,
    table_min_cols=2,
    
    # Cell Merging
    merge_adjacent_cells=True,
    cell_merge_threshold=0.8,
    detect_cell_spans=True,
    
    # OCR Settings (adaptive)
    do_ocr=True,
    force_full_page_ocr=False,  # Use native text when available
    ocr_lang=["english", "hindi"],
    
    # OCR Quality (balanced)
    det_limit_side_len=960,
    det_db_thresh=0.3,
    det_db_box_thresh=0.6,
    rec_batch_num=6,
    
    # Image Processing (moderate)
    images_scale=2.0,
    enhance_contrast=False,
    denoise=False,
    deskew=False,
    
    # Layout Analysis
    do_layout_analysis=True,
    detect_reading_order=True,
    reading_order_method="spatial",
    
    # Performance
    max_num_pages=100,
    use_gpu=False,
    num_threads=4,
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
    force_full_page_ocr=False,
    ocr_lang=["english"],
    
    # OCR Quality (lower resolution, faster)
    det_limit_side_len=640,  # Lower resolution
    det_db_thresh=0.4,
    det_db_box_thresh=0.7,
    rec_batch_num=8,  # Larger batches
    
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
    use_gpu=False,
    num_threads=8,
)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

DOCLING_PROFILES = {
    "identity_document": IDENTITY_DOCUMENT_PROFILE,
    "character_box_forms": CHARACTER_BOX_FORMS_PROFILE,
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


def get_profile_for_document_type(doc_type: str) -> DoclingOptions:
    """
    Auto-select profile based on document type.
    
    Args:
        doc_type: Document type (e.g., "application_form", "aadhaar", "kfs")
        
    Returns:
        Appropriate DoclingOptions profile
    """
    # Pure identity cards: bypass TableFormer completely (zero tables in Aadhaar/PAN/DL/Voter ID)
    if doc_type in ["aadhaar", "pan", "pan_card", "dl", "driving_license", "voter_id", "passport"]:
        return IDENTITY_DOCUMENT_PROFILE

    # Indian government forms with character boxes (e.g. application form)
    elif doc_type in ["application_form"]:
        return CHARACTER_BOX_FORMS_PROFILE
    
    # Financial documents (typically scanned)
    elif doc_type in ["bank_statement", "salary_slip"]:
        return SCANNED_DOCUMENTS_PROFILE
    
    # Legal/contract documents (typically digital)
    elif doc_type in ["loan_agreement", "sanction_letter", "nach_mandate"]:
        return DIGITAL_PDF_PROFILE
    
    # Default: mixed content
    else:
        return MIXED_CONTENT_PROFILE
