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
