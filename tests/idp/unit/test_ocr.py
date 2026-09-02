import io
from PIL import Image, ImageDraw
from idp.services.ocr.rapidocr_engine import RapidOCREngine


def test_ocr_engine_execution():
    ocr_engine = RapidOCREngine()

    # Create a real test PNG image with text
    img = Image.new("RGB", (400, 100), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((10, 30), "TEST DISBURSEMENT APPLICATION", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    valid_png_bytes = buf.getvalue()

    res = ocr_engine.process(valid_png_bytes, page_number=1, doc_id="TEST-OCR")

    assert res.page_number == 1
    assert len(res.elements) > 0
    assert res.elements[0].text is not None
    assert len(res.elements[0].bbox) == 4
