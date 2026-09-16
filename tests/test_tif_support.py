"""Unit tests verifying full support for .tif and .tiff document files."""
import io
from pathlib import Path
import pytest
from PIL import Image

from config.doc_types import SUPPORTED_DOCUMENT_EXTENSIONS, get_canonical_doc_type
from idp.utils.file_utils import detect_file_type


def test_supported_document_extensions():
    """Verify that both .tif and .tiff are registered in canonical extensions."""
    assert ".tif" in SUPPORTED_DOCUMENT_EXTENSIONS
    assert ".tiff" in SUPPORTED_DOCUMENT_EXTENSIONS


def test_detect_file_type_tif(tmp_path: Path):
    """Verify detect_file_type handles .tif and .tiff files correctly."""
    tif_file = tmp_path / "document.tif"
    tif_file.write_bytes(b"dummy")
    category, mime = detect_file_type(str(tif_file))
    assert category == "image"
    assert mime in ("image/tiff", "image/tif", "image/x-tiff")

    tiff_file = tmp_path / "document.tiff"
    tiff_file.write_bytes(b"dummy")
    category, mime = detect_file_type(str(tiff_file))
    assert category == "image"
    assert mime in ("image/tiff", "image/tif", "image/x-tiff")


def test_canonical_doc_type_with_tif():
    """Verify canonical document type resolver correctly identifies document types from .tif filenames."""
    assert get_canonical_doc_type("pan.tif") == "pan"
    assert get_canonical_doc_type("APPL00343265_kfs.tif") == "kfs"
    assert get_canonical_doc_type("aadhaar.tiff") == "aadhaar"
    assert get_canonical_doc_type("sanction_letter.tif") == "sanction_letter"
    assert get_canonical_doc_type("disbursal_memo.tif") == "disbursal_memo"


def test_preview_document_tif_converts_to_png(tmp_path: Path):
    """Verify preview_document converts .tif files into viewable PNG images for browser compatibility."""
    from fastapi.testclient import TestClient
    from app.main import app
    from config import S3_RAW_DIR

    case_id = "LOAN_TEST_TIF_PREVIEW"
    raw_dir = S3_RAW_DIR / case_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Generate a real 10x10 RGB TIFF image
    img = Image.new("RGB", (10, 10), color="blue")
    tif_path = raw_dir / "pan_card.tif"
    img.save(tif_path, format="TIFF")

    client = TestClient(app)
    response = client.get(f"/api/documents/preview/{case_id}/pan_card.tif?format=image")
    
    # Cleanup file
    try:
        tif_path.unlink(missing_ok=True)
        raw_dir.rmdir()
    except OSError:
        pass

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
