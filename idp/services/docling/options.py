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
    table_mode: str = "ACCURATE"  # 'ACCURATE' (slow, precise) or 'FAST'
    
    # TableFormer Model Selection
    table_model_type: str = "default"  # 'default', 'custom', 'none'
    table_model_path: Optional[str] = None  # Custom model checkpoint path
    
    # Table Detection Thresholds
    table_confidence_threshold: float = 0.5  # Min confidence to accept table (0.0-1.0)
    table_min_rows: int = 2  # Minimum rows to qualify as table
    table_min_cols: int = 2  # Minimum columns to qualify as table
    
    # Cell Merging & Structure
    merge_adjacent_cells: bool = True  # Merge cells with same content
    cell_merge_threshold: float = 0.8  # Similarity threshold for merging
    detect_cell_spans: bool = True  # Detect row/col spans
    
    # Table Structure Refinement
    refine_table_structure: bool = True  # Post-process table grid
    remove_empty_rows: bool = True  # Filter out empty rows
    remove_empty_cols: bool = True  # Filter out empty columns
    
    # Character Box Handling (CRITICAL for forms with character-level boxes)
    merge_character_boxes: bool = True  # Merge adjacent single-char cells
    character_box_max_width: float = 30.0  # Max width (pixels) for char box
    character_box_gap_threshold: float = 5.0  # Max gap to merge chars
    
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
    det_db_thresh: float = 0.2  # Detection threshold (lower = more boxes)
    det_db_box_thresh: float = 0.35  # Box confidence threshold (lower = detect low-contrast/faint text)
    rec_batch_num: int = 6  # Batch size for recognition
    
    # ═══════════════════════════════════════════════════════════════════════
    # IMAGE PREPROCESSING
    # ═══════════════════════════════════════════════════════════════════════
    images_scale: float = 2.0  # Image upscaling factor (2.0 = 2x resolution)
    enhance_contrast: bool = False  # Apply contrast enhancement
    denoise: bool = False  # Apply denoising
    deskew: bool = False  # Auto-rotate skewed images
    
    # ═══════════════════════════════════════════════════════════════════════
    # LAYOUT ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════
    do_layout_analysis: bool = True  # Detect paragraphs, headings, lists
    layout_model_type: str = "default"  # Layout model variant
    
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
    use_gpu: bool = False  # Use GPU acceleration (if available)
    num_threads: int = 4  # CPU threads for processing
    debug_mode: bool = False  # Save debug visualizations
    log_level: str = "INFO"  # Logging verbosity

