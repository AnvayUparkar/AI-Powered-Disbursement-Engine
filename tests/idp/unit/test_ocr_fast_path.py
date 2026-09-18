import io
from PIL import Image, ImageDraw
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
