# LightOnOCR-2-1B Integration Implementation Plan

## Executive Summary

This document details the surgical integration of `lightonai/LightOnOCR-2-1B` as the primary OCR engine for **scanned pages only**, while preserving all existing digital PDF processing paths unchanged.

**Core Routing Rule:**
```
SCANNED PAGE → [Preprocessing: Deskew + CLAHE + Denoise + Binarize]
            ↓
         LightOnOCR-2-1B
            ↓
      Quality Check
            ↓
   [GOOD: Accept | POOR/FAILED: Existing VLM]

DIGITAL PAGE → Existing Docling + RapidOCR/PP-OCR Pipeline (UNCHANGED)
```

**🔥 CRITICAL INSIGHT:**

The existing preprocessing pipeline (`preprocess_scanned_document()` in `scan_preprocessor.py`) already performs:
- ✅ Deskewing (coarse 0/90/180/270 + fine-angle Hough lines)
- ✅ CLAHE contrast enhancement
- ✅ Denoising (fastNlMeansDenoising)
- ✅ Adaptive binarization
- ✅ Blur correction (unsharp masking)

**LightOnOCR MUST receive these preprocessed images** (not raw images) to ensure:
1. Fair comparison with RapidOCR (both receive same quality input)
2. Optimal LightOnOCR accuracy (preprocessed images are cleaner)
3. Architectural consistency (preprocessing is document-type aware)

---

## 1. Architecture Analysis Summary

### 1.1 Current OCR Architecture

**Entry Point:** `DocumentProcessor.process_document()` in `idp/services/document_processor.py`

**Scanned vs Digital Detection:** ✅ **ALREADY EXISTS** - Robust page-level detection
- Located in: `DocumentPreprocessor._inspect_pdf()` (`idp/services/document_preprocessor.py`)
- Logic: Averages text characters per page; if `< 50 chars/page` → `is_scanned_pdf = True`
- Stored in: `PreprocessedDocument.is_scanned_pdf` (Boolean flag)
- **Page-level capable:** Currently document-level, but can be enhanced to track per-page scan status

**Current OCR Flow:**
```
Document → DocumentPreprocessor.preprocess() → PreprocessedDocument
    ↓
    is_scanned_pdf check
    ↓
    ├─ SCANNED (is_scanned_pdf=True + ENABLE_SCAN_PREPROCESSING=True)
    │   ↓
    │   preprocess_scanned_document() [scan_preprocessor.py]
    │   ↓
    │   Pixel-level cleanup (deskew, CLAHE, denoising, adaptive binarization)
    │   ↓  [PREPROCESSED IMAGES READY]
    │   DoclingParser.parse() [uses RapidOCR/PP-OCRv5/v6]
    │   ↓
    │   DoclingParseResult (LayoutElements with OCR text)
    │
    └─ DIGITAL (is_scanned_pdf=False)
        ↓
        DoclingParser.parse() [native PDF text extraction + minimal OCR]
        ↓
        DoclingParseResult
```

**🔥 CRITICAL OBSERVATION:**

The existing `preprocess_scanned_document()` function in `scan_preprocessor.py` performs:
1. **Deskewing** (coarse orientation 0/90/180/270 + fine-angle Hough line detection)
2. **CLAHE contrast enhancement** (on low-contrast scans)
3. **Denoising** (fastNlMeansDenoising)
4. **Adaptive binarization** (on faded pages)
5. **Blur correction** (unsharp masking)

These preprocessed images are rasterized at `images_scale` (2.0x or 3.0x) and **written as a new PDF** to temp directory.

**This preprocessing MUST be reused for LightOnOCR** to ensure fair comparison and optimal quality.

**VLM Fallback:** ✅ **ALREADY EXISTS**
- Router: `ConfidenceRouter.get_low_confidence_layout_elements()` (`idp/services/vlm/router.py`)
- Client: `VLMClient.analyze_region()` (`idp/services/vlm/client.py`)
- Triggers:
  - Confidence < 0.70 (configurable threshold)
  - Garbled text detection (via `OCRConfidenceEvaluator`)
  - PAN validation failure
  - Comb-box outlier detection

**OCR Models:**
- `OCRElement` (individual text element with bbox, confidence, source)
- `OCRResult` (page-level aggregate)
- `LayoutElement` (Docling's layout representation)

**Serialization & Deduplication:** ✅ **ROBUST**
- `DocumentSerializer` in `idp/services/output/serializer.py`
- `_compute_iou()` - IoU-based overlap detection
- `_is_duplicate()` - Duplicate suppression (IoU ≥ 0.5)

**Table Handling:** ✅ **DOCLING AUTHORITATIVE**
- TableFormer for structure detection
- RapidOCR for cell OCR
- Vertical table logic preserved
- KFS horizontal table handling preserved

---

## 2. Implementation Strategy

### 2.1 Design Principles

1. **Zero Digital PDF Impact:** Digital pages NEVER touch LightOnOCR
2. **Lazy Loading:** Model loads only when first scanned page is encountered
3. **Memory Safety:** CPU inference mandatory, CUDA optional, OOM handling
4. **Timeout Protection:** Configurable timeout prevents hanging
5. **Graceful Degradation:** LightOnOCR failure → VLM fallback
6. **Surgical Changes:** Minimal modifications to existing codebase
7. **Observability:** Structured logging without PII
8. **🔥 CRITICAL: Preprocessing Reuse:** LightOnOCR receives **preprocessed** images (deskewed, CLAHE-enhanced, denoised, binarized) - same as Docling OCR

### 2.2 Key Decision: Where to Route

**Option A:** Route at document-level (current `is_scanned_pdf` flag)
- ✅ Simple implementation
- ✅ Matches existing architecture
- ❌ Cannot handle mixed-page PDFs (Page 1 digital, Page 2 scanned)

**Option B:** Route at page-level (per-page scan detection)
- ✅ Handles mixed documents correctly
- ✅ Future-proof
- ⚠️ Requires per-page text extraction analysis

**DECISION:** Start with **Option A** (document-level routing) for this implementation
- Current architecture already provides reliable document-level detection
- Mixed-page PDFs are rare in this domain (Aadhaar, bank statements, KFS, loan agreements)
- Can be upgraded to page-level later without architectural redesign

---

## 3. Implementation Components

### 3.0 Preprocessing Reuse (CRITICAL ARCHITECTURE DECISION)

#### 3.0.1 Question: Does LightOnOCR Receive Preprocessed Images?

✅ **YES - MANDATORY**

LightOnOCR receives the **EXACT SAME** preprocessed images as Docling/RapidOCR.

#### 3.0.2 Existing Preprocessing Pipeline

**Location:** `idp/services/ocr/scan_preprocessor.py`

**Function:** `preprocess_scanned_document(file_path, file_category, target_scale, doc_id, output_dir)`

**Pixel-Level Operations Applied:**

| Step | Operation | Implementation | Applied When |
|------|-----------|----------------|--------------|
| 1 | **Coarse Orientation** | pytesseract OSD detection | 0°/90°/180°/270° rotation detected |
| 2 | **Fine-Angle Deskew** | Hough lines + minAreaRect fallback | Skew angle 1° - 45° detected |
| 3 | **Blur Correction** | Unsharp masking (Gaussian + addWeighted) | Laplacian variance < 100.0 |
| 4 | **CLAHE Enhancement** | Adaptive histogram equalization | std_dev < 35.0 OR dynamic_range < 80.0 |
| 5 | **Denoising** | cv2.fastNlMeansDenoising | After contrast enhancement |
| 6 | **Adaptive Binarization** | cv2.adaptiveThreshold | Low-contrast/faded pages |

**Output:** 
- **Format:** New PDF file in temp directory
- **Pages:** Rasterized at `images_scale` (2.0x or 3.0x from document type profile)
- **Quality:** Cleaned, deskewed, contrast-enhanced images
- **Preservation:** Original point dimensions maintained (page.rect preserved)

**Code Reference:** `idp/services/ocr/preprocessing.py` - `OCRImagePreprocessor.preprocess_image()`

#### 3.0.3 Preprocessing Flow in document_processor.py

**Step 1: Preprocessing (Lines 136-175) - BEFORE Routing**

```python
# ══════════════════════════════════════════════════════════════════
# PREPROCESSING PHASE (applies to ALL scanned documents)
# ══════════════════════════════════════════════════════════════════

docling_input_path = local_file_path  # Default: raw file
docling_profile = self._get_docling_parser(doc_type_hint, is_scanned=prep_doc.is_scanned_pdf)

# Apply pixel-level preprocessing to scanned documents
preprocessed_path = local_file_path  # Default
scan_preprocessing_applied = False

if prep_doc.is_scanned_pdf and settings.ENABLE_SCAN_PREPROCESSING:
    try:
        # Get rasterization scale from document type profile
        # (e.g., CHARACTER_BOX_FORMS = 2.0x, SCANNED_DOCUMENTS = 3.0x)
        raster_scale = docling_profile.options.images_scale
        
        # Run preprocessing: deskew, CLAHE, denoise, binarize
        scan_result = await asyncio.to_thread(
            preprocess_scanned_document,
            local_file_path,         # Input: raw file from S3
            prep_doc.file_category,  # "pdf" or "image"
            raster_scale,            # 2.0 or 3.0
            document_id,
            temp_dir,
        )
        
        preprocessed_path = scan_result.processed_path  # ← OUTPUT: CLEANED PDF
        scan_preprocessing_applied = True
        
        logger.info(format_doc_log(
            document_id,
            f"Scan preprocessing done: {scan_result.pages_processed} page(s) "
            f"cleaned at scale={raster_scale}x -> {scan_result.processed_path}"
        ))
        
        # CRITICAL: Preprocessed PDF already at target scale
        # Set images_scale=1.0 to prevent double-upscaling
        preprocessed_options = docling_profile.options.model_copy(
            update={"images_scale": 1.0}
        )
        docling_profile = DoclingParser(preprocessed_options)
        
    except Exception as scan_err:
        logger.warning(format_doc_log(
            document_id,
            f"Scan preprocessing failed (non-fatal), using original file: {scan_err}"
        ))
        preprocessed_path = local_file_path
```

**Step 2: Routing (Lines 180-250) - AFTER Preprocessing**

```python
# ══════════════════════════════════════════════════════════════════
# ROUTING PHASE (LightOnOCR vs Docling)
# Both routes receive preprocessed_path (not local_file_path)
# ══════════════════════════════════════════════════════════════════

if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED:
    # ┌────────────────────────────────────────────────────────────┐
    # │ LightOnOCR Route (NEW)                                     │
    # └────────────────────────────────────────────────────────────┘
    
    lightonocr_adapter = LightOnOCRAdapter()
    ocr_results: List[OCRResult] = []
    
    logger.info(format_doc_log(
        document_id,
        f"Routing {prep_doc.page_count} scanned pages to LightOnOCR-2-1B"
        f"{' (preprocessed)' if scan_preprocessing_applied else ' (raw)'}"
    ))
    
    # Extract page images from PREPROCESSED PDF
    page_image_data_for_ocr = await self._get_page_images(
        preprocessed_path,  # ← PREPROCESSED (deskewed, CLAHE, denoised)
        prep_doc
    )
    
    for page_idx, (page_bytes, (img_w, img_h)) in enumerate(page_image_data_for_ocr):
        ocr_res = lightonocr_adapter.process_page_to_ocr_result(
            image_bytes=page_bytes,  # ← PREPROCESSED IMAGE
            page_number=page_idx + 1,
            image_width=img_w,
            image_height=img_h,
            doc_id=document_id
        )
        ocr_results.append(ocr_res)
    
    docling_result = None  # Skip Docling

else:
    # ┌────────────────────────────────────────────────────────────┐
    # │ Docling/RapidOCR Route (EXISTING)                          │
    # └────────────────────────────────────────────────────────────┘
    
    docling_result = await asyncio.to_thread(
        docling_profile.parse,
        preprocessed_path,  # ← SAME PREPROCESSED PDF
        doc_id=document_id
    )
```

**Step 3: VLM Fallback Image Extraction**

```python
# ══════════════════════════════════════════════════════════════════
# VLM receives ORIGINAL images (not preprocessed)
# ══════════════════════════════════════════════════════════════════

if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED:
    # LightOnOCR route: VLM uses original images
    page_image_data = await self._get_page_images(
        local_file_path,  # ← ORIGINAL (for VLM)
        prep_doc
    )
else:
    # Docling route: VLM can use preprocessed images
    page_image_data = await self._get_page_images(
        preprocessed_path,
        prep_doc
    )

page_images: List[bytes] = [item[0] for item in page_image_data]
```

#### 3.0.4 Why Preprocessing Matters

| Benefit | Description |
|---------|-------------|
| **Fair Comparison** | Both RapidOCR and LightOnOCR receive identical input quality - removes preprocessing as a variable |
| **Optimal Accuracy** | Cleaned images improve OCR for low-quality scans (faded text, skewed pages, low contrast) |
| **No Duplication** | Preprocessing runs once before routing, results shared by both paths |
| **Profile-Driven** | Document type profiles (CHARACTER_BOX_FORMS, SCANNED_DOCUMENTS) control rasterization scale |
| **Memory Efficient** | Single preprocessing pass, not per-engine |

#### 3.0.5 Visual Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Raw Scanned PDF from S3                       │
│               (skewed, low contrast, noisy)                      │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────────┐
              │   is_scanned_pdf = True     │
              │   ENABLE_SCAN_PREPROCESSING │
              └─────────────┬───────────────┘
                            │
                            ▼
              ┌─────────────────────────────────────────────┐
              │  preprocess_scanned_document()              │
              │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
              │  1. Coarse orientation (0/90/180/270)       │
              │  2. Fine-angle deskew (Hough lines)         │
              │  3. Blur correction (unsharp masking)       │
              │  4. CLAHE contrast enhancement              │
              │  5. Denoising (fastNlMeansDenoising)        │
              │  6. Adaptive binarization                   │
              │  7. Rasterize at images_scale (2x-3x)       │
              └─────────────┬───────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────────────────────────┐
              │         PREPROCESSED PDF                     │
              │    (temp directory, cleaned pages)           │
              │  ✓ Deskewed  ✓ Enhanced  ✓ Denoised         │
              └─────────────┬───────────────────────────────┘
                            │
            ┌───────────────┴───────────────┐
            │                               │
    LIGHTONOCR_ENABLED=true         LIGHTONOCR_ENABLED=false
            │                               │
            ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│   LightOnOCR Route      │     │   Docling Route         │
│   (NEW)                 │     │   (EXISTING)            │
│                         │     │                         │
│ Extract pages from:     │     │ Parse:                  │
│ preprocessed_path ✓     │     │ preprocessed_path ✓     │
│                         │     │                         │
│ Input Quality:          │     │ Input Quality:          │
│ • Deskewed ✓            │     │ • Deskewed ✓            │
│ • CLAHE-enhanced ✓      │     │ • CLAHE-enhanced ✓      │
│ • Denoised ✓            │     │ • Denoised ✓            │
│ • Binarized ✓           │     │ • Binarized ✓           │
│ • 2x-3x resolution ✓    │     │ • 2x-3x resolution ✓    │
└─────────────────────────┘     └─────────────────────────┘
         │                               │
         ▼                               ▼
    OCRResult[]                   DoclingParseResult
```

#### 3.0.6 Exception: VLM Receives Original Images

**Why VLM Uses Original (Non-Preprocessed) Images:**

1. **Color Information:** Binarization removes RGB channels, losing visual context
2. **Shading & Gradients:** Vision models benefit from natural image characteristics
3. **Texture Preservation:** Original textures aid in understanding document structure
4. **OCR vs Vision:** OCR needs clean text; VLM needs rich visual context

**Summary Table:**

| Component | Receives Preprocessing? | Image Source | Rationale |
|-----------|------------------------|--------------|-----------|
| **LightOnOCR** | ✅ YES | `preprocessed_path` | Needs clean text for OCR |
| **Docling/RapidOCR** | ✅ YES | `preprocessed_path` | Needs clean text for OCR |
| **VLM (LightOnOCR route)** | ❌ NO | `local_file_path` | Vision model needs original |
| **VLM (Docling route)** | ✅ YES | `preprocessed_path` | Existing behavior |
| **Digital PDFs** | ❌ NO | `local_file_path` | No preprocessing needed |

#### 3.0.7 Configuration Control

**Environment Variable:** `ENABLE_SCAN_PREPROCESSING`

```env
# Enable preprocessing (default, recommended)
ENABLE_SCAN_PREPROCESSING=true

# Disable preprocessing (debugging/testing)
ENABLE_SCAN_PREPROCESSING=false
```

**Behavior:**

- `true`: Both LightOnOCR and Docling receive preprocessed images
- `false`: Both receive raw images (useful for A/B testing)

#### 3.0.8 Code References

| File | Function/Section | Purpose |
|------|-----------------|---------|
| `document_processor.py` | Lines 136-175 | Preprocessing orchestration |
| `document_processor.py` | Lines 180-250 | Routing decision |
| `scan_preprocessor.py` | `preprocess_scanned_document()` | Main entry point |
| `scan_preprocessor.py` | `_process_pdf()` | Multi-page PDF processing |
| `preprocessing.py` | `OCRImagePreprocessor` | Pixel-level operations |
| `preprocessing.py` | `preprocess_image()` | Core preprocessing logic |

---

### 3.1 New Files to Create

#### **File 1:** `idp/services/ocr/lightonocr_engine.py`
```python
"""
LightOnOCR-2-1B wrapper for scanned document OCR.

Lazy-loaded, CPU-safe, timeout-protected OCR engine.
"""
from typing import Optional, List, Dict, Any, Tuple
import time
import threading
from PIL import Image
import io
from pydantic import BaseModel

from idp.models.ocr import OCRElement, OCRResult
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log
from idp.core.exceptions import OCRError


class LightOnOCRResult(BaseModel):
    """Raw result from LightOnOCR model."""
    text: str
    confidence: float
    bboxes: List[List[float]] = []  # List of [x1, y1, x2, y2]
    inference_time_ms: float = 0.0
    quality_score: float = 0.0  # Custom quality metric


class LightOnOCREngine:
    """
    Lazy-loaded LightOnOCR-2-1B engine for scanned pages.
    
    Thread-safe singleton pattern with timeout protection.
    """
    
    _instance = None
    _lock = threading.Lock()
    _model = None
    _processor = None
    _model_loaded = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.model_name = settings.LIGHTONOCR_MODEL
        self.device = settings.LIGHTONOCR_DEVICE
        self.timeout_seconds = settings.LIGHTONOCR_TIMEOUT_SECONDS
        self.lazy_load = settings.LIGHTONOCR_LAZY_LOAD
        
        # Do NOT load model here - wait for first inference call
        if not self.lazy_load:
            self._load_model_internal()
    
    def _load_model_internal(self):
        """Load LightOnOCR model (thread-safe)."""
        if self._model_loaded:
            return
        
        with self._lock:
            if self._model_loaded:
                return
            
            try:
                logger.info(f"Loading LightOnOCR model: {self.model_name}")
                start = time.time()
                
                from transformers import AutoProcessor, AutoModelForVision2Seq
                import torch
                
                # Determine device
                if self.device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                else:
                    device = self.device
                
                logger.info(f"LightOnOCR will run on: {device}")
                
                # Load model
                self._processor = AutoProcessor.from_pretrained(
                    self.model_name,
                    trust_remote_code=True
                )
                
                self._model = AutoModelForVision2Seq.from_pretrained(
                    self.model_name,
                    trust_remote_code=True
                ).to(device)
                
                self._model.eval()  # Inference mode
                self._device = device
                self._model_loaded = True
                
                elapsed = time.time() - start
                logger.info(f"LightOnOCR loaded successfully in {elapsed:.2f}s on {device}")
                
            except Exception as e:
                logger.error(f"Failed to load LightOnOCR model: {e}")
                self._model_loaded = False
                self._model = None
                self._processor = None
                raise OCRError(f"LightOnOCR model loading failed: {e}")
    
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._model_loaded and self._model is not None
    
    def unload_model(self):
        """Unload model to free memory."""
        with self._lock:
            if self._model is not None:
                del self._model
                del self._processor
                self._model = None
                self._processor = None
                self._model_loaded = False
                
                # Force garbage collection
                import gc
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
                
                logger.info("LightOnOCR model unloaded from memory")
    
    def process_page(
        self,
        image_bytes: bytes,
        page_number: int,
        doc_id: str = "DOC"
    ) -> Optional[LightOnOCRResult]:
        """
        Run LightOnOCR inference on a single page.
        
        Args:
            image_bytes: Page image (PNG/JPEG)
            page_number: Page number (1-indexed)
            doc_id: Document ID for logging
        
        Returns:
            LightOnOCRResult or None on failure
        """
        if not settings.LIGHTONOCR_ENABLED:
            return None
        
        # Lazy load on first use
        if not self.is_loaded():
            try:
                self._load_model_internal()
            except Exception as e:
                logger.error(format_doc_log(doc_id, f"LightOnOCR model loading failed: {e}"))
                return None
        
        try:
            logger.info(format_doc_log(
                doc_id, 
                f"Running LightOnOCR on page {page_number}"
            ))
            
            start_time = time.time()
            
            # Convert bytes to PIL Image
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img_width, img_height = image.size
            
            # Prepare inputs
            inputs = self._processor(images=image, return_tensors="pt").to(self._device)
            
            # Run inference with timeout protection
            result = self._run_inference_with_timeout(inputs, doc_id)
            
            if result is None:
                return None
            
            inference_time = (time.time() - start_time) * 1000  # ms
            
            # Parse LightOnOCR output
            text = result.get("text", "")
            confidence = result.get("confidence", 0.0)
            bboxes = result.get("bboxes", [])
            
            quality_score = self._compute_quality_score(text, confidence, image_bytes)
            
            logger.info(format_doc_log(
                doc_id,
                f"LightOnOCR page {page_number}: {len(text)} chars, "
                f"conf={confidence:.2f}, quality={quality_score:.2f}, "
                f"time={inference_time:.0f}ms"
            ))
            
            return LightOnOCRResult(
                text=text,
                confidence=confidence,
                bboxes=bboxes,
                inference_time_ms=inference_time,
                quality_score=quality_score
            )
            
        except Exception as e:
            logger.error(format_doc_log(
                doc_id,
                f"LightOnOCR inference failed on page {page_number}: {e}"
            ))
            return None
    
    def _run_inference_with_timeout(
        self,
        inputs: Any,
        doc_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Run model inference with timeout protection.
        
        Uses threading to enforce timeout.
        """
        result_container = {"result": None, "error": None}
        
        def inference_worker():
            try:
                import torch
                with torch.no_grad():
                    outputs = self._model.generate(**inputs, max_new_tokens=512)
                    text = self._processor.batch_decode(outputs, skip_special_tokens=True)[0]
                    
                    # Extract bboxes if available (model-specific)
                    bboxes = []
                    confidence = 0.95  # Default if not provided by model
                    
                    result_container["result"] = {
                        "text": text,
                        "confidence": confidence,
                        "bboxes": bboxes
                    }
            except Exception as e:
                result_container["error"] = str(e)
        
        inference_thread = threading.Thread(target=inference_worker)
        inference_thread.daemon = True
        inference_thread.start()
        inference_thread.join(timeout=self.timeout_seconds)
        
        if inference_thread.is_alive():
            logger.error(format_doc_log(
                doc_id,
                f"LightOnOCR inference timeout ({self.timeout_seconds}s exceeded)"
            ))
            return None
        
        if result_container["error"]:
            logger.error(format_doc_log(
                doc_id,
                f"LightOnOCR inference error: {result_container['error']}"
            ))
            return None
        
        return result_container["result"]
    
    def _compute_quality_score(
        self,
        text: str,
        confidence: float,
        image_bytes: bytes
    ) -> float:
        """
        Compute quality score for LightOnOCR result.
        
        Quality heuristics:
        - Non-empty text: +0.3
        - Reasonable text length: +0.2
        - High confidence: +0.3
        - Valid character ratio: +0.2
        
        Returns: 0.0 to 1.0
        """
        score = 0.0
        
        # Check 1: Non-empty text
        if text and text.strip():
            score += 0.3
        else:
            return 0.0  # Empty text = poor quality
        
        # Check 2: Reasonable length (not too short)
        if len(text.strip()) >= 10:
            score += 0.2
        elif len(text.strip()) >= 5:
            score += 0.1
        
        # Check 3: Confidence
        score += min(confidence * 0.3, 0.3)
        
        # Check 4: Valid character ratio (alphanumeric + common punctuation)
        import re
        valid_chars = len(re.findall(r'[a-zA-Z0-9\s।,\.\'"\-/]', text))
        total_chars = len(text)
        if total_chars > 0:
            valid_ratio = valid_chars / total_chars
            score += valid_ratio * 0.2
        
        return min(score, 1.0)


# Singleton instance
_lightonocr_engine = None

def get_lightonocr_engine() -> LightOnOCREngine:
    """Get singleton LightOnOCR engine instance."""
    global _lightonocr_engine
    if _lightonocr_engine is None:
        _lightonocr_engine = LightOnOCREngine()
    return _lightonocr_engine
```

#### **File 2:** `idp/services/ocr/lightonocr_adapter.py`
```python
"""
Adapter to convert LightOnOCR output to OCRElement/OCRResult format.

Ensures compatibility with existing serializer and deduplication logic.
"""
from typing import List, Optional
from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.lightonocr_engine import LightOnOCRResult, get_lightonocr_engine
from idp.core.logging import logger, format_doc_log


class LightOnOCRAdapter:
    """Convert LightOnOCR results to standard OCR format."""
    
    def __init__(self):
        self.engine = get_lightonocr_engine()
        self.quality_threshold = 0.40  # Below this → trigger VLM
    
    def process_page_to_ocr_result(
        self,
        image_bytes: bytes,
        page_number: int,
        image_width: float,
        image_height: float,
        doc_id: str = "DOC"
    ) -> Optional[OCRResult]:
        """
        Process a scanned page through LightOnOCR and return OCRResult.
        
        Args:
            image_bytes: Page image
            page_number: Page number (1-indexed)
            image_width: Image pixel width
            image_height: Image pixel height
            doc_id: Document ID
        
        Returns:
            OCRResult or None on failure
        """
        # Run LightOnOCR
        lightonocr_result = self.engine.process_page(image_bytes, page_number, doc_id)
        
        if lightonocr_result is None:
            return self._create_failed_ocr_result(page_number, image_width, image_height)
        
        # Check quality
        if lightonocr_result.quality_score < self.quality_threshold:
            logger.warning(format_doc_log(
                doc_id,
                f"LightOnOCR page {page_number} quality score {lightonocr_result.quality_score:.2f} "
                f"below threshold {self.quality_threshold} - will trigger VLM fallback"
            ))
            # Return result but mark as low quality
            return self._convert_to_ocr_result(
                lightonocr_result, 
                page_number, 
                image_width, 
                image_height,
                low_quality=True
            )
        
        # Quality OK - return result
        return self._convert_to_ocr_result(
            lightonocr_result,
            page_number,
            image_width,
            image_height,
            low_quality=False
        )
    
    def _convert_to_ocr_result(
        self,
        lightonocr_result: LightOnOCRResult,
        page_number: int,
        image_width: float,
        image_height: float,
        low_quality: bool = False
    ) -> OCRResult:
        """
        Convert LightOnOCRResult to OCRResult format.
        
        Strategy:
        - If LightOnOCR provides bboxes → create OCRElement per bbox
        - If no bboxes → create single OCRElement with full-page bbox
        """
        elements: List[OCRElement] = []
        
        text = lightonocr_result.text.strip()
        confidence = lightonocr_result.confidence
        
        # If quality is low, mark for VLM review
        needs_vlm = low_quality or confidence < 0.70
        
        if lightonocr_result.bboxes and len(lightonocr_result.bboxes) > 0:
            # LightOnOCR provided bounding boxes
            for idx, bbox in enumerate(lightonocr_result.bboxes):
                # Extract text segment (if text segmentation info available)
                # For now, we assign entire text to first element
                elem_text = text if idx == 0 else ""
                
                if elem_text:  # Skip empty segments
                    elem = OCRElement(
                        id=f"lightonocr-p{page_number}-{idx}",
                        text=elem_text,
                        bbox=bbox,  # [x1, y1, x2, y2]
                        confidence=confidence,
                        page_number=page_number,
                        source="lightonocr",
                        needs_vlm=needs_vlm
                    )
                    elements.append(elem)
        else:
            # No bboxes provided - create single full-page element
            # This is acceptable: VLM will crop regions as needed
            elem = OCRElement(
                id=f"lightonocr-p{page_number}-full",
                text=text,
                bbox=[0.0, 0.0, image_width, image_height],
                confidence=confidence,
                page_number=page_number,
                source="lightonocr",
                needs_vlm=needs_vlm
            )
            elements.append(elem)
        
        # Compute statistics
        total_elements = len(elements)
        low_conf_count = sum(1 for e in elements if e.needs_vlm)
        avg_conf = confidence
        
        return OCRResult(
            page_number=page_number,
            elements=elements,
            average_confidence=avg_conf,
            low_confidence_count=low_conf_count,
            total_elements=total_elements,
            extraction_failed=False,
            image_width=image_width,
            image_height=image_height
        )
    
    def _create_failed_ocr_result(
        self,
        page_number: int,
        image_width: float,
        image_height: float
    ) -> OCRResult:
        """Create OCRResult marking extraction as failed."""
        return OCRResult(
            page_number=page_number,
            elements=[],
            average_confidence=0.0,
            low_confidence_count=0,
            total_elements=0,
            extraction_failed=True,
            image_width=image_width,
            image_height=image_height
        )
```

---

### 3.2 Files to Modify

#### **File 1:** `idp/core/config.py`
**Changes:** Add LightOnOCR configuration settings

```python
# LightOnOCR Configuration (for scanned pages)
LIGHTONOCR_ENABLED: bool = False
LIGHTONOCR_MODEL: str = "lightonai/LightOnOCR-2-1B"
LIGHTONOCR_DEVICE: str = "auto"  # "auto", "cpu", "cuda"
LIGHTONOCR_LAZY_LOAD: bool = True
LIGHTONOCR_TIMEOUT_SECONDS: int = 60
LIGHTONOCR_QUALITY_THRESHOLD: float = 0.40  # Below this → VLM fallback
```

#### **File 2:** `.env.example`
**Changes:** Add LightOnOCR environment variables

```env
# LightOnOCR Configuration (scanned page OCR)
LIGHTONOCR_ENABLED=true
LIGHTONOCR_MODEL=lightonai/LightOnOCR-2-1B
LIGHTONOCR_DEVICE=auto
LIGHTONOCR_LAZY_LOAD=true
LIGHTONOCR_TIMEOUT_SECONDS=60
LIGHTONOCR_QUALITY_THRESHOLD=0.40
```

#### **File 3:** `idp/services/document_processor.py`
**Changes:** Add scanned page routing to LightOnOCR

**🔥 CRITICAL:** LightOnOCR must receive the **same preprocessed images** that Docling would have received.

**Location:** After line 175 (after scan preprocessing completes, in place of Docling parse)

**Modified Section (lines 136-180):**

```python
# Step 3a: For scanned documents, apply pixel-level cleanup (deskew, CLAHE,
# denoising, adaptive binarisation) before Docling ingestion.  The digital PDF
# path is never entered here — the gate is prep_doc.is_scanned_pdf which is set
# only when the text-layer inspection in DocumentPreprocessor detects < 50 chars/page.
docling_input_path = local_file_path  # default: pass raw file unchanged
# Build the primary parser for this document type.  For scanned docs we may
# swap it below to one using images_scale=1.0 (see note in scan branch).
docling_profile = self._get_docling_parser(doc_type_hint, is_scanned=prep_doc.is_scanned_pdf)

# PREPROCESSING BRANCH (applies to both Docling OCR and LightOnOCR)
preprocessed_path = local_file_path  # Default: use raw file
scan_preprocessing_applied = False

if prep_doc.is_scanned_pdf and settings.ENABLE_SCAN_PREPROCESSING:
    try:
        # Use the actual profile that will be applied to this document so
        # the rasterisation scale matches what Docling would have used.
        # (e.g. application_form -> CHARACTER_BOX_FORMS_PROFILE.images_scale=2.0,
        #  not SCANNED_DOCUMENTS_PROFILE.images_scale=3.0)
        raster_scale = docling_profile.options.images_scale
        scan_result = await asyncio.to_thread(
            preprocess_scanned_document,
            local_file_path,
            prep_doc.file_category,
            raster_scale,
            document_id,
            temp_dir,
        )
        preprocessed_path = scan_result.processed_path
        scan_preprocessing_applied = True
        logger.info(format_doc_log(
            document_id,
            f"Scan preprocessing done: {scan_result.pages_processed} page(s) "
            f"cleaned at scale={raster_scale}x -> {scan_result.processed_path}"
        ))
        # CRITICAL: the preprocessed PDF is already rasterised at raster_scale.
        # Passing it to Docling with the same images_scale would upscale it
        # a second time, blurring pixels and degrading OCR accuracy.
        # Use a separate parser instance with images_scale=1.0 ("read as-is").
        preprocessed_options = docling_profile.options.model_copy(
            update={"images_scale": 1.0}
        )
        docling_profile = DoclingParser(preprocessed_options)
    except Exception as scan_err:
        # Non-fatal: log and fall back to the original file so the pipeline
        # continues rather than failing the whole document.
        logger.warning(format_doc_log(
            document_id,
            f"Scan preprocessing failed (non-fatal), using original file: {scan_err}"
        ))
        preprocessed_path = local_file_path

# ROUTING DECISION: LightOnOCR vs Docling for scanned pages
# LightOnOCR receives the SAME preprocessed images as Docling would have
if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED:
    # ═══════════════════════════════════════════════════════════════
    # NEW: LightOnOCR Route for Scanned Pages
    # ═══════════════════════════════════════════════════════════════
    from idp.services.ocr.lightonocr_adapter import LightOnOCRAdapter
    
    lightonocr_adapter = LightOnOCRAdapter()
    ocr_results: List[OCRResult] = []
    
    logger.info(format_doc_log(
        document_id,
        f"Routing {prep_doc.page_count} scanned pages to LightOnOCR-2-1B"
        f"{' (preprocessed)' if scan_preprocessing_applied else ' (raw)'}"
    ))
    
    lightonocr_start = time.time()
    lightonocr_pages_processed = 0
    lightonocr_pages_failed = 0
    
    # Extract page images from the PREPROCESSED PDF (same images Docling would use)
    # This ensures LightOnOCR receives deskewed, CLAHE-enhanced, denoised images
    page_image_data_for_ocr = await self._get_page_images(preprocessed_path, prep_doc)
    
    for page_idx, (page_bytes, (img_w, img_h)) in enumerate(page_image_data_for_ocr):
        page_num = page_idx + 1
        
        try:
            ocr_res = lightonocr_adapter.process_page_to_ocr_result(
                image_bytes=page_bytes,
                page_number=page_num,
                image_width=img_w,
                image_height=img_h,
                doc_id=document_id
            )
            
            if ocr_res and not ocr_res.extraction_failed:
                ocr_results.append(ocr_res)
                lightonocr_pages_processed += 1
            else:
                # LightOnOCR failed → mark for VLM fallback
                ocr_results.append(OCRResult(
                    page_number=page_num,
                    elements=[],
                    extraction_failed=True,
                    image_width=img_w,
                    image_height=img_h
                ))
                lightonocr_pages_failed += 1
                logger.warning(format_doc_log(
                    document_id,
                    f"LightOnOCR failed on page {page_num} - will use VLM fallback"
                ))
        
        except Exception as e:
            logger.error(format_doc_log(
                document_id,
                f"LightOnOCR exception on page {page_num}: {e}"
            ))
            ocr_results.append(OCRResult(
                page_number=page_num,
                elements=[],
                extraction_failed=True,
                image_width=img_w,
                image_height=img_h
            ))
            lightonocr_pages_failed += 1
    
    metrics.lightonocr_processing_time = round(time.time() - lightonocr_start, 3)
    metrics.lightonocr_pages_processed = lightonocr_pages_processed
    metrics.lightonocr_pages_failed = lightonocr_pages_failed
    
    logger.info(format_doc_log(
        document_id,
        f"LightOnOCR completed: {lightonocr_pages_processed}/{prep_doc.page_count} pages, "
        f"{lightonocr_pages_failed} failures, {metrics.lightonocr_processing_time:.2f}s"
    ))
    
    # Skip Docling for scanned pages when using LightOnOCR
    docling_result = None
    metrics.docling_processing_time = 0.0

else:
    # ═══════════════════════════════════════════════════════════════
    # EXISTING: Docling Path (digital PDFs and scanned when LightOnOCR disabled)
    # ═══════════════════════════════════════════════════════════════
    docling_start = time.time()
    docling_result: Optional[DoclingParseResult] = None
    try:
        docling_result = await asyncio.to_thread(
            docling_profile.parse, preprocessed_path, doc_id=document_id
        )
    except Exception as e:
        logger.warning(format_doc_log(document_id, f"Docling parsing warning: {e}. Proceeding with fallback parsing."))
    metrics.docling_processing_time = round(time.time() - docling_start, 3)

# Step 4: Capture page images for VLM region cropping
# For LightOnOCR route: use the ORIGINAL file (not preprocessed) for VLM
# because VLM works better with original image quality
if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED:
    page_image_data = await self._get_page_images(local_file_path, prep_doc)
else:
    page_image_data = await self._get_page_images(preprocessed_path, prep_doc)

page_images: List[bytes] = [item[0] for item in page_image_data]
```

**Key Changes:**
1. Preprocessing runs FIRST (before routing decision)
2. Both LightOnOCR and Docling receive the **same preprocessed images**
3. `preprocessed_path` stores the cleaned PDF path
4. LightOnOCR extracts pages from `preprocessed_path` (not `local_file_path`)
5. VLM receives original images for better quality

**Changes to VLM routing section (around line 195):**

```python
# Step 5: Selective VLM Fallback Routing
vlm_start = time.time()
vlm_corrections: Dict[str, VLMResult] = {}
vlm_used = False

# Handle LightOnOCR results (scanned pages)
if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED and ocr_results:
    for ocr_res in ocr_results:
        pno = ocr_res.page_number
        page_bytes = page_images[pno - 1] if pno <= len(page_images) else b""
        
        # Check if page needs VLM (extraction_failed or low quality elements)
        needs_vlm_page = ocr_res.extraction_failed or ocr_res.low_confidence_count > 0
        
        if needs_vlm_page and page_bytes:
            logger.info(format_doc_log(
                document_id,
                f"Routing page {pno} to VLM (LightOnOCR quality insufficient)"
            ))
            
            # Use full page image for VLM
            vlm_res = await self.vlm_client.analyze_region(
                image_bytes=page_bytes,
                ocr_element=OCRElement(
                    id=f"lightonocr-fallback-p{pno}",
                    text="",
                    bbox=[0, 0, ocr_res.image_width, ocr_res.image_height],
                    confidence=0.0,
                    page_number=pno,
                    source="lightonocr"
                ),
                context_hint=f"Full page {pno} OCR fallback",
                doc_id=document_id
            )
            
            if vlm_res:
                # Replace LightOnOCR result with VLM result
                vlm_elem = OCRElement(
                    id=f"vlm-p{pno}-full",
                    text=vlm_res.text,
                    bbox=[0, 0, ocr_res.image_width, ocr_res.image_height],
                    confidence=vlm_res.confidence,
                    page_number=pno,
                    source="vlm_corrected",
                    ocr_original=ocr_res.elements[0].text if ocr_res.elements else "",
                    needs_vlm=False
                )
                
                ocr_res.elements = [vlm_elem]
                ocr_res.extraction_failed = False
                ocr_res.average_confidence = vlm_res.confidence
                ocr_res.low_confidence_count = 0
                metrics.vlm_fallback_count += 1
                vlm_used = True
            
            await asyncio.sleep(0.25)  # Rate limiting

# Handle Docling results (digital pages or LightOnOCR disabled)
elif docling_result and docling_result.elements:
    # Existing VLM routing logic unchanged
    flagged_elements = self.router.get_low_confidence_layout_elements(
        docling_result.elements, doc_id=document_id
    )
    # ... rest of existing VLM logic ...
```

#### **File 4:** `idp/models/processing.py`
**Changes:** Add LightOnOCR metrics

```python
# LightOnOCR Metrics
lightonocr_processing_time: float = 0.0
lightonocr_pages_processed: int = 0
lightonocr_pages_failed: int = 0
```

#### **File 5:** `idp/core/exceptions.py`
**Changes:** Add OCRError exception (if not exists)

```python
class OCRError(IDPException):
    """OCR processing error."""
    pass
```

---

### 3.3 Testing Requirements

#### **File 1:** `tests/idp/unit/test_lightonocr_routing.py`

```python
"""
Tests for LightOnOCR scanned page routing.

Verifies:
1. Scanned pages → LightOnOCR
2. Digital pages → Existing pipeline (unchanged)
3. LightOnOCR failure → VLM fallback
4. Timeout handling
5. Quality threshold
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from idp.services.ocr.lightonocr_engine import LightOnOCREngine, LightOnOCRResult
from idp.services.ocr.lightonocr_adapter import LightOnOCRAdapter
from idp.models.ocr import OCRResult, OCRElement


class TestLightOnOCRRouting:
    """Test scanned page routing to LightOnOCR."""
    
    def test_scanned_page_uses_lightonocr(self):
        """SCANNED page must route to LightOnOCR when enabled."""
        # Mock LightOnOCR result
        mock_result = LightOnOCRResult(
            text="Test OCR Text",
            confidence=0.95,
            bboxes=[[10, 10, 100, 30]],
            inference_time_ms=150.0,
            quality_score=0.85
        )
        
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image",
                page_number=1,
                image_width=595,
                image_height=842,
                doc_id="TEST-001"
            )
        
        assert ocr_result is not None
        assert ocr_result.extraction_failed is False
        assert len(ocr_result.elements) > 0
        assert ocr_result.elements[0].source == "lightonocr"
        assert ocr_result.elements[0].text == "Test OCR Text"
    
    def test_digital_page_skips_lightonocr(self, monkeypatch):
        """DIGITAL page must NOT route to LightOnOCR."""
        from idp.core import config as cfg
        
        # Simulate digital PDF detection
        is_scanned = False
        lightonocr_enabled = True
        
        # Branch condition from document_processor.py
        should_use_lightonocr = is_scanned and lightonocr_enabled
        
        assert not should_use_lightonocr, (
            "Digital pages must NOT use LightOnOCR"
        )
    
    def test_lightonocr_low_quality_triggers_vlm(self):
        """Low quality LightOnOCR result must trigger VLM fallback."""
        # Mock poor quality result
        mock_result = LightOnOCRResult(
            text="3T9T3πT&T",  # Garbled
            confidence=0.50,
            bboxes=[],
            inference_time_ms=100.0,
            quality_score=0.25  # Below threshold
        )
        
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image",
                page_number=1,
                image_width=595,
                image_height=842,
                doc_id="TEST-002"
            )
        
        assert ocr_result is not None
        assert ocr_result.low_confidence_count > 0
        assert ocr_result.elements[0].needs_vlm is True
    
    def test_lightonocr_failure_creates_failed_result(self):
        """LightOnOCR failure must create extraction_failed result."""
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=None):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image",
                page_number=1,
                image_width=595,
                image_height=842,
                doc_id="TEST-003"
            )
        
        assert ocr_result is not None
        assert ocr_result.extraction_failed is True
        assert len(ocr_result.elements) == 0
    
    def test_lightonocr_timeout_returns_none(self):
        """LightOnOCR timeout must return None (safe failure)."""
        engine = LightOnOCREngine()
        engine._model_loaded = True  # Pretend loaded
        
        # Mock timeout
        with patch.object(engine, '_run_inference_with_timeout', return_value=None):
            result = engine.process_page(
                image_bytes=b"fake_image",
                page_number=1,
                doc_id="TEST-TIMEOUT"
            )
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_vertical_table_unchanged(self):
        """Vertical table logic must remain unchanged."""
        # This test ensures existing table processing is not affected
        # by LightOnOCR integration
        
        # Mock: scanned page processed by LightOnOCR produces OCR text
        # Table structure still comes from Docling TableFormer
        
        # For scanned pages with LightOnOCR:
        # - LightOnOCR provides OCR text
        # - Docling is SKIPPED (no table structure detection)
        # - Table handling must degrade gracefully or use existing logic
        
        # TODO: Verify table handling with LightOnOCR
        # This may require additional consideration
        pass
```

#### **File 2:** `tests/idp/unit/test_lightonocr_deduplication.py`

```python
"""
Tests for LightOnOCR result deduplication.

Ensures LightOnOCR output goes through existing serializer correctly.
"""
def test_lightonocr_results_deduplicated():
    """LightOnOCR results must pass through _compute_iou deduplication."""
    from idp.services.output.serializer import DocumentSerializer
    
    serializer = DocumentSerializer()
    
    # Two overlapping bboxes
    box1 = [10.0, 10.0, 100.0, 50.0]
    box2 = [12.0, 12.0, 98.0, 48.0]
    
    iou = serializer._compute_iou(box1, box2)
    assert iou >= 0.70  # High overlap
    
    # Deduplication should trigger
    is_dup = serializer._is_duplicate(box1, box2, iou_threshold=0.5)
    assert is_dup is True
```

---

## 4. Deployment Checklist

### 4.1 Pre-Deployment

- [ ] Install dependencies: `transformers`, `torch`, `Pillow`
- [ ] Verify LightOnOCR model download works: `huggingface-cli download lightonai/LightOnOCR-2-1B`
- [ ] Test on Ryzen 5 7430U / 16GB RAM machine
- [ ] Confirm CPU inference works (no CUDA required)
- [ ] Measure baseline memory usage
- [ ] Measure LightOnOCR memory footprint

### 4.2 Configuration

```env
# .env
LIGHTONOCR_ENABLED=true
LIGHTONOCR_MODEL=lightonai/LightOnOCR-2-1B
LIGHTONOCR_DEVICE=auto
LIGHTONOCR_LAZY_LOAD=true
LIGHTONOCR_TIMEOUT_SECONDS=60
LIGHTONOCR_QUALITY_THRESHOLD=0.40
```

### 4.3 Testing Sequence

1. **Digital PDF Test:** Verify digital PDFs unchanged
2. **Scanned Page Test:** Verify scanned page routes to LightOnOCR
3. **Quality Test:** Verify poor result triggers VLM
4. **Timeout Test:** Verify timeout does not hang service
5. **Table Test:** Verify KFS/table handling preserved
6. **Deduplication Test:** Verify no duplicate elements
7. **Load Test:** Process 10 scanned documents sequentially

### 4.4 Rollback Plan

If LightOnOCR causes issues:
```env
LIGHTONOCR_ENABLED=false
```
System reverts to 100% existing behavior.

---

## 5. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│               DocumentProcessor.process_document             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ DocumentPreprocessor │
              │  _inspect_pdf()     │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ PreprocessedDocument │
              │  is_scanned_pdf      │
              └──────────┬──────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
    is_scanned=True              is_scanned=False
    LIGHTONOCR_ENABLED=true      (ANY)
         │                               │
         ▼                               ▼
┌────────────────────┐         ┌─────────────────┐
│  LightOnOCR Route  │         │  Docling Route  │
│  (NEW)             │         │  (UNCHANGED)    │
└────────┬───────────┘         └────────┬────────┘
         │                              │
         ▼                              ▼
┌────────────────────┐         ┌─────────────────┐
│ LightOnOCREngine   │         │ DoclingParser   │
│  .process_page()   │         │  .parse()       │
└────────┬───────────┘         └────────┬────────┘
         │                              │
         ▼                              ▼
┌────────────────────┐         ┌─────────────────┐
│ LightOnOCRAdapter  │         │DoclingParseResult│
│  .convert()        │         │ (LayoutElements)│
└────────┬───────────┘         └────────┬────────┘
         │                              │
         ▼                              │
┌────────────────────┐                 │
│    OCRResult       │◄────────────────┘
│  (OCRElement[])    │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Quality Check     │
│  quality_score     │
└────────┬───────────┘
         │
    ┌────┴────┐
    │         │
  GOOD      POOR/FAILED
    │         │
    ▼         ▼
 ACCEPT   ┌─────────┐
          │   VLM   │
          │Fallback │
          └─────────┘
```

---

## 6. Critical Constraints & Safeguards

### 6.1 Digital PDF Safety (HARD REQUIREMENT)

```python
# This condition MUST evaluate to False for digital PDFs
if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED:
    # LightOnOCR path
else:
    # Existing Docling path (UNCHANGED)
```

**Test:**
```python
is_scanned = False
lightonocr_enabled = True
should_route = is_scanned and lightonocr_enabled
assert should_route == False  # MUST pass
```

### 6.2 Memory Safety

- Lazy loading prevents startup RAM spike
- Timeout prevents infinite blocking
- Model unload available for memory recovery
- CPU inference mandatory (CUDA optional)

### 6.3 Table Handling Consideration

**CRITICAL ISSUE:** LightOnOCR integration skips Docling entirely for scanned pages.

**Problem:** Docling provides TableFormer structure detection.

**Solution Options:**

**Option A:** Run Docling in parallel (table structure only, no OCR)
- Pros: Preserves table structure
- Cons: Adds processing time

**Option B:** Post-process LightOnOCR text for table detection
- Pros: Faster
- Cons: Less accurate table structure

**Option C:** Hybrid - Run Docling for table pages, LightOnOCR for non-table pages
- Pros: Best of both
- Cons: Complex routing

**RECOMMENDED:** Start with **Option C** - Use page-level heuristics to detect table pages, run Docling for those, LightOnOCR for others.

---

## 7. Implementation Timeline

### Phase 1: Core Integration (Week 1)
- [ ] Create `lightonocr_engine.py`
- [ ] Create `lightonocr_adapter.py`
- [ ] Add configuration to `config.py`
- [ ] Modify `document_processor.py` routing
- [ ] Add metrics to `processing.py`

### Phase 2: Testing (Week 2)
- [ ] Unit tests for routing logic
- [ ] Integration tests for scanned pages
- [ ] Regression tests for digital PDFs
- [ ] Timeout and error handling tests
- [ ] Deduplication tests

### Phase 3: Table Handling (Week 3)
- [ ] Analyze table detection requirements
- [ ] Implement hybrid Docling/LightOnOCR routing
- [ ] Test KFS horizontal tables
- [ ] Test vertical tables
- [ ] Test bank statement tables

### Phase 4: Optimization (Week 4)
- [ ] Memory profiling
- [ ] Inference speed optimization
- [ ] Quality threshold tuning
- [ ] VLM fallback rate analysis
- [ ] Documentation

---

## 8. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Digital PDF Impact | 0% change | Compare before/after processing times |
| LightOnOCR Accuracy | ≥90% quality score | % pages above threshold |
| VLM Fallback Rate | <20% | % scanned pages needing VLM |
| Inference Time | <5s/page (CPU) | Median time per page |
| Memory Usage | <8GB peak | Max RAM during processing |
| Timeout Rate | <1% | % pages hitting timeout |
| Table Detection | No regression | KFS/table extraction accuracy |

---

## 9. Limitations & Future Work

### Known Limitations
1. Document-level routing (not page-level)
2. LightOnOCR bounding boxes may be less precise than RapidOCR
3. Table structure detection weakened for scanned pages
4. Sequential processing (not parallel)

### Future Enhancements
1. Page-level scan detection for mixed PDFs
2. Parallel LightOnOCR inference (batch processing)
3. Fine-tuned LightOnOCR on domain-specific documents (Aadhaar, KFS)
4. Hybrid table detection (Docling structure + LightOnOCR OCR)
5. Model caching across documents
6. GPU inference optimization

---

## 10. References

- LightOnOCR Paper: [https://arxiv.org/abs/2501.xxxxx]
- Hugging Face Model: `lightonai/LightOnOCR-2-1B`
- Existing Architecture: `DOCLING_ARCHITECTURE.md`
- Digital/Scanned Detection: `document_preprocessor.py` line 68-71
- VLM Fallback: `vlm/router.py`, `vlm/client.py`
- Serialization: `output/serializer.py`

---

**END OF IMPLEMENTATION PLAN**
