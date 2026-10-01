import os
import tempfile
from idp.services.document_preprocessor import DocumentPreprocessor
from idp.core.exceptions import UnsupportedFileType


def test_preprocessor_image():
    preprocessor = DocumentPreprocessor()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")
        tmp_name = f.name

    try:
        doc = preprocessor.preprocess(tmp_name, doc_id="TEST-IMG")
        assert doc.file_category == "image"
        assert doc.page_count == 1
        assert doc.is_scanned_pdf is True
    finally:
        os.remove(tmp_name)


def test_preprocessor_unsupported_extension():
    preprocessor = DocumentPreprocessor()
    with tempfile.NamedTemporaryFile(suffix=".invalid", delete=False) as f:
        f.write(b"invalid data")
        tmp_name = f.name

    try:
        raised = False
        try:
            preprocessor.preprocess(tmp_name, doc_id="TEST-INV")
        except UnsupportedFileType:
            raised = True
        assert raised is True
    finally:
        os.remove(tmp_name)


def test_scanned_quality_gate_detect_skew_line_shapes():
    """Verify _detect_skew_angle safely handles both (N, 4) and (N, 1, 4) HoughLinesP output arrays."""
    from unittest.mock import patch
    import numpy as np
    from idp.services.scanned_quality_gate import ScannedDocQualityGate

    gate = ScannedDocQualityGate()
    dummy_gray = np.zeros((100, 100), dtype=np.uint8)

    # 1. 2D array of shape (N, 4) where line is 1D array of 4 integers
    lines_2d = np.array([[10, 10, 90, 10], [5, 5, 95, 5]], dtype=np.int32)
    with patch("cv2.HoughLinesP", return_value=lines_2d):
        angle = gate._detect_skew_angle(dummy_gray)
        assert isinstance(angle, float)
        assert abs(angle) < 1.0

    # 2. 3D array of shape (N, 1, 4)
    lines_3d = np.array([[[10, 10, 90, 10]], [[5, 5, 95, 5]]], dtype=np.int32)
    with patch("cv2.HoughLinesP", return_value=lines_3d):
        angle = gate._detect_skew_angle(dummy_gray)
        assert isinstance(angle, float)
        assert abs(angle) < 1.0


def test_preprocessor_multi_page_pdf(tmp_path):
    """Verify that multi-page scanned PDFs correctly identify all pages without fallback reset."""
    import fitz
    doc = fitz.open()
    for _ in range(4):
        page = doc.new_page(width=595.0, height=842.0)
        pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 10, 10))
        pix.set_rect(fitz.IRect(0, 0, 10, 10), (180,))
        page.insert_image(page.rect, pixmap=pix)

    pdf_file = str(tmp_path / "test_4page.pdf")
    doc.save(pdf_file)
    doc.close()

    preprocessor = DocumentPreprocessor()
    prep = preprocessor.preprocess(pdf_file, doc_id="TEST-4P")
    assert prep.page_count == 4
    assert prep.is_scanned_pdf is True
    assert len(prep.pages_dimensions) == 4
    assert len(prep.pages_quality) == 4

