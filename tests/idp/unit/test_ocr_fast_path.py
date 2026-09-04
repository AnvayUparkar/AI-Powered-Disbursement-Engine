import io
from PIL import Image, ImageDraw
from idp.services.ocr.ocr_model_router import OCRModelRouter
from idp.services.ocr.preprocessing import OCRImagePreprocessor


def test_skip_preprocessing_fast_path():
    preprocessor = OCRImagePreprocessor()
    
    # Create test image bytes
    img = Image.new("RGB", (200, 50), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    # Preprocessing with skip_preprocessing=True should bypass heavy CV2 deskew/contrast checks
    processed_bytes, metadata = preprocessor.preprocess_image(raw_bytes, doc_id="TEST-FAST", skip_preprocessing=True)
    assert metadata["skipped"] is True
    assert metadata["rotation_applied"] is False
    assert processed_bytes == raw_bytes


def test_ocr_model_router_fast_path_english_hint():
    router = OCRModelRouter()

    img = Image.new("RGB", (400, 100), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((10, 30), "BANK STATEMENT ACCOUNT SUMMARY", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    # Hint English document type
    res = router.process_page(
        image_input=raw_bytes,
        page_number=1,
        doc_id="TEST-BANK-STMT",
        doc_type_hint="bank_statement"
    )

    assert res.page_number == 1
    assert len(res.elements) > 0
    assert res.elements[0].metadata.get("script") == "latin"
