from typing import Optional, List
from pydantic import BaseModel


class DoclingOptions(BaseModel):
    """
    Configuration options for Docling document converter.
    
    Comprehensive control over OCR, table detection, and layout analysis.
    """
    
    # ═══════════════════════════════════════════════════════════════════════
    # TABLE STRUCTURE DETECTION (TableFormer)
    # ═══════════════════════════════════════════════════════════════════════
    do_table_structure: bool = True  # Enable/disable table detection
    table_mode: str = "ACCURATE"  # FAST mode is disabled repo-wide (see pipeline.py) -- this is always forced to ACCURATE regardless of value
    
    # TableFormer Model Selection
    table_model_type: str = "default"  # 'default', 'custom', 'none'
    table_model_path: Optional[str] = None  # Custom model checkpoint path

    # Cell matching: reconcile the predicted cell grid against the PDF's native
    # text layer when available. Wired -> table_structure_options.do_cell_matching.
    # Turn OFF for scanned/photographed forms where the native text layer (if any)
    # is unreliable/comb-boxed, so TableFormer's own recognized cell text is trusted
    # instead of snapping to (possibly misaligned) native PDF text spans.
    do_cell_matching: bool = False

    # Table Detection Thresholds
    # NOTE: Docling's TableStructureOptions has no native "confidence"/"min_rows"/
    # "min_cols" knobs -- these are enforced as a POST-FILTER in parser.py after
    # TableFormer returns its grid (tables that don't pass are dropped before
    # reaching the output, never silently ignored).
    table_confidence_threshold: float = 0.3  # Min non-empty-cell fill ratio to accept table (0.0-1.0)
    table_min_rows: int = 1  # Minimum rows to qualify as table
    table_min_cols: int = 1  # Minimum columns to qualify as table

    # Cell Merging & Structure
    # NOTE: not currently wired into the Docling pipeline or post-processing.
    # Reserved for a future comb-box/cell-merge post-processor.
    merge_adjacent_cells: bool = True  # Merge cells with same content
    cell_merge_threshold: float = 0.5  # Similarity threshold for merging
    detect_cell_spans: bool = True  # Detect row/col spans

    # Table Structure Refinement
    # NOTE: not currently wired -- reserved for a future post-processor.
    refine_table_structure: bool = True  # Post-process table grid
    remove_empty_rows: bool = False  # Filter out empty rows
    remove_empty_cols: bool = False  # Filter out empty columns

    # Character Box Handling (CRITICAL for forms with character-level boxes)
    # NOTE: not currently wired -- reserved for a future comb-box detector.
    merge_character_boxes: bool = True  # Merge adjacent single-char cells
    character_box_max_width: float = 300.0  # Max width (pixels) for char box
    character_box_gap_threshold: float = 2.0  # Max gap to merge chars
    
    # ═══════════════════════════════════════════════════════════════════════
    # OCR ENGINE (RapidOCR PP-OCRv6)
    # ═══════════════════════════════════════════════════════════════════════
    do_ocr: bool = True  # Enable OCR for text extraction
    ocr_engine_name: str = "rapidocr"  # Engine backend
    ocr_model_name: str = "PP-OCRv6_medium"  # Model variant
    
    # OCR Model Paths (optional custom models)
    det_model_path: Optional[str] = None  # Detection model
    rec_model_path: Optional[str] = None  # Recognition model
    cls_model_path: Optional[str] = None  # Classification model (orientation)
    
    # OCR Language & Script
    ocr_lang: List[str] = ["english"]  # Languages: ["english", "hindi", "mixed"]
    
    # OCR Mode
    force_full_page_ocr: bool = False  # False = use native text when available
    adaptive_full_page_ocr: bool = True  # Trigger full-page OCR when digital text density is low
    ocr_on_tables_only: bool = False  # OCR only table regions
    
    # OCR Quality & Performance
    det_limit_side_len: int = 1536  # Detection input size (higher = slower, better)
    det_db_thresh: float = 0.1  # Detection threshold (lower = more boxes)
    det_db_box_thresh: float = 0.35  # Box confidence threshold (lower = detect low-contrast/faint text)
    rec_batch_num: int = 6  # Batch size for recognition
    
    # ═══════════════════════════════════════════════════════════════════════
    # IMAGE PREPROCESSING
    # ═══════════════════════════════════════════════════════════════════════
    images_scale: float = 2.0  # Image upscaling factor -> pipeline_options.images_scale (rasterization DPI for OCR/TableFormer input)
    # NOTE: enhance_contrast/denoise/deskew are not wired into the Docling path --
    # Docling rasterizes the PDF internally and has no such hooks. Equivalent logic
    # already exists in idp/services/ocr/preprocessing.py (OCRImagePreprocessor) but
    # that class is currently only reachable from the bypassed standalone RapidOCR
    # engine, not from the Docling converter path used by DocumentProcessor.
    enhance_contrast: bool = False  # Apply contrast enhancement
    denoise: bool = False  # Apply denoising
    deskew: bool = False  # Auto-rotate skewed images
    
    # ═══════════════════════════════════════════════════════════════════════
    # LAYOUT ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════
    do_layout_analysis: bool = True  # Detect paragraphs, headings, lists
    layout_model_type: str = "default"  # Layout model variant

    # THE actual table-detection threshold: the layout model classifies page
    # regions (Table/Text/Title/Picture/...) BEFORE TableFormer ever runs --
    # TableFormer only processes regions the layout model already labeled
    # "Table". This is that classifier's confidence cutoff (Docling default:
    # 0.3). Lower it to catch faint/low-confidence table regions the layout
    # model would otherwise discard outright; this is a GLOBAL detection
    # threshold shared across all region classes, not table-specific, so
    # lowering it can also let in more low-confidence text/picture regions.
    # Wired -> pipeline_options.layout_options.engine_options.score_threshold.
    layout_detection_threshold: float = 0.1

    # Reading Order
    detect_reading_order: bool = True  # Determine element sequence
    reading_order_method: str = "spatial"  # 'spatial', 'column_aware'
    
    # ═══════════════════════════════════════════════════════════════════════
    # DOCUMENT PROCESSING
    # ═══════════════════════════════════════════════════════════════════════
    max_num_pages: int = 100  # Max pages to process
    process_images: bool = True  # Process embedded images
    extract_figures: bool = False  # Extract figure regions
    
    # ═══════════════════════════════════════════════════════════════════════
    # PERFORMANCE & DEBUGGING
    # ═══════════════════════════════════════════════════════════════════════
    use_gpu: bool = True  # Use GPU acceleration (if available)
    num_threads: int = 4  # CPU threads for processing
    debug_mode: bool = True  # Save debug visualizations
    log_level: str = "INFO"  # Logging verbosity

