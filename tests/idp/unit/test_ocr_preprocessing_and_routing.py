import io
import numpy as np
from PIL import Image, ImageFilter
from unittest.mock import MagicMock

from idp.models.ocr import OCRResult, OCRElement
from idp.services.ocr.preprocessing import OCRImagePreprocessor
from idp.services.ocr.ocr_model_router import OCRModelRouter
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.services.vlm.router import ConfidenceRouter


def _create_synthetic_image(sharp: bool = True, high_contrast: bool = True) -> bytes:
    """Helper to create synthetic test images with controllable sharpness and contrast."""
    if sharp and high_contrast:
        # High contrast, sharp edges
        arr = np.zeros((100, 100), dtype=np.uint8)
        arr[:, 50:] = 255
        img = Image.fromarray(arr)
    else:
        # Low contrast, blurry
        arr = np.full((100, 100), 128, dtype=np.uint8)
        # slight low-contrast noise
        rng = np.random.default_rng(42)
        noise = rng.integers(-5, 5, size=(100, 100), dtype=np.int16)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
        img = img.filter(ImageFilter.GaussianBlur(radius=3))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_preprocessing_pipeline_blur_and_binarization():
    """(a) Preprocessing triggers blur correction, CLAHE, denoising, and binarization on low-quality image."""
    preprocessor = OCRImagePreprocessor(blur_threshold=100.0, contrast_std_threshold=35.0)
    low_quality_bytes = _create_synthetic_image(sharp=False, high_contrast=False)

    processed_bytes, metadata = preprocessor.preprocess_image(
        low_quality_bytes, doc_id="TEST-PREP"
    )

    assert metadata["skipped"] is False
    assert metadata["grayscale_converted"] is True
    assert metadata["blur_corrected"] is True
    assert metadata["contrast_enhanced"] is True
    assert metadata["denoised"] is True
    assert metadata["binarized"] is True
    assert len(processed_bytes) > 0


def test_preprocessing_assess_quality():
    """Verify lightweight assess_quality accurately classifies sharp vs degraded images."""
    preprocessor = OCRImagePreprocessor(blur_threshold=100.0, contrast_std_threshold=35.0)

    sharp_bytes = _create_synthetic_image(sharp=True, high_contrast=True)
    sharp_quality = preprocessor.assess_quality(sharp_bytes)
    assert sharp_quality["is_sharp"] is True
    assert sharp_quality["is_well_contrasted"] is True
    assert sharp_quality["needs_preprocessing"] is False

    degraded_bytes = _create_synthetic_image(sharp=False, high_contrast=False)
    degraded_quality = preprocessor.assess_quality(degraded_bytes)
    assert degraded_quality["is_sharp"] is False
    assert degraded_quality["is_well_contrasted"] is False
    assert degraded_quality["needs_preprocessing"] is True


def test_ocr_model_router_no_longer_skips_from_filename_hint():
    """(b) Router does not skip preprocessing for degraded images even with English doc_type_hint."""
    router = OCRModelRouter()
    mock_engine = MagicMock()
    mock_engine.process.return_value = OCRResult(page_number=1, elements=[])
    router.default_engine = mock_engine

    degraded_bytes = _create_synthetic_image(sharp=False, high_contrast=False)

    # Pass an English hint that previously skipped preprocessing unconditionally
    router.process_page(
        image_input=degraded_bytes,
        page_number=1,
        doc_id="TEST-DEGRADED-HINT",
        doc_type_hint="bank_statement"
    )

    assert mock_engine.process.called
    call_kwargs = mock_engine.process.call_args.kwargs
    # Must NOT skip preprocessing on degraded image
    assert call_kwargs.get("skip_preprocessing") is False


def test_confidence_evaluator_flags_empty_ocr_result():
    """(c) Confidence evaluator sets average_confidence=0.0 and extraction_failed=True on empty elements."""
    evaluator = OCRConfidenceEvaluator()
    empty_result = OCRResult(page_number=1, elements=[])

    evaluated = evaluator.evaluate_result(empty_result)

    assert evaluated.average_confidence == 0.0
    assert evaluated.extraction_failed is True
    assert evaluated.total_elements == 0


def test_vlm_router_escalates_on_empty_and_failed_ocr():
    """(d) VLM router escalates immediately when total_elements == 0 or extraction_failed == True."""
    vlm_router = ConfidenceRouter(threshold=0.80, vlm_enabled=True)

    # Case 1: total_elements == 0
    empty_result = OCRResult(page_number=1, elements=[], total_elements=0, average_confidence=0.0)
    assert vlm_router.should_use_vlm(empty_result) is True

    # Case 2: extraction_failed == True
    failed_result = OCRResult(page_number=1, elements=[], total_elements=0, extraction_failed=True)
    assert vlm_router.should_use_vlm(failed_result) is True

    # Case 3: normal high confidence result should NOT escalate
    valid_elem = OCRElement(
        id="1", text="Clean text", bbox=[0, 0, 10, 10], confidence=0.95, page_number=1
    )
    good_result = OCRResult(
        page_number=1,
        elements=[valid_elem],
        total_elements=1,
        average_confidence=0.95,
        low_confidence_count=0,
        extraction_failed=False
    )
    assert vlm_router.should_use_vlm(good_result) is False
