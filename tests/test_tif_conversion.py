"""Test TIFF to PNG conversion utility for safe OCR ingestion."""
from pathlib import Path
from PIL import Image
import pytest

from pipeline.utils.image_normalizer import ensure_png_for_idp


def test_tif_to_png_conversion(tmp_path: Path):
    """Verifies that a genuine .tif file is converted to a valid .png image."""
    # Create a real synthetic TIFF image
    tif_path = tmp_path / "sample_card.tif"
    img = Image.new("RGB", (400, 300), color=(73, 109, 137))
    img.save(tif_path, format="TIFF")

    assert tif_path.exists()
    assert tif_path.suffix.lower() == ".tif"

    # Convert using image normalizer
    converted_path = ensure_png_for_idp(tif_path)

    assert converted_path.exists()
    assert converted_path.suffix.lower() == ".png"

    # Verify converted image is openable and valid PNG
    with Image.open(converted_path) as converted_img:
        assert converted_img.format == "PNG"
        assert converted_img.size == (400, 300)
        assert converted_img.mode == "RGB"


def test_non_tif_passthrough(tmp_path: Path):
    """Verifies that PDF, PNG, JPG, and XML files pass through unchanged."""
    png_path = tmp_path / "sample.png"
    img = Image.new("RGB", (200, 200), color=(255, 0, 0))
    img.save(png_path, format="PNG")

    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    xml_path = tmp_path / "aadhaar.xml"
    xml_path.write_text("<UidData/>", encoding="utf-8")

    assert ensure_png_for_idp(png_path) == png_path
    assert ensure_png_for_idp(pdf_path) == pdf_path
    assert ensure_png_for_idp(xml_path) == xml_path
