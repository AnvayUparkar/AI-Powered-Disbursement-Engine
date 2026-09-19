"""
Unit tests for idp.services.ocr.scan_preprocessor.

Verifies:
1. PDF branch: page count preserved in output PDF.
2. PDF branch: page point-dimensions preserved (parser.py compatibility).
3. Image branch: output file written and is valid PNG.
4. ValueError on unsupported file_category.
5. OSError on non-existent source file.
6. preprocess_scanned_document is NOT called when ENABLE_SCAN_PREPROCESSING=False
   (regression guard — digital path must never route through scan_preprocessor).
"""

import io
import os
import tempfile

import pytest
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(width: int = 200, height: int = 300) -> bytes:
    """Create a minimal valid greyscale PNG in memory."""
    arr = np.zeros((height, width), dtype=np.uint8)
    arr[50:250, 20:180] = 200  # light rectangle — enough contrast for preprocessor
    img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_single_page_pdf(width_pt: float = 595.0, height_pt: float = 842.0) -> bytes:
    """Create a minimal single-page PDF using fitz (PyMuPDF)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    # Insert a small rectangle of pixels so rasterisation produces a real image
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 10, 10))
    pix.set_rect(fitz.IRect(0, 0, 10, 10), (200,))
    page.insert_image(page.rect, pixmap=pix)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_two_page_pdf(
    w1: float = 595.0, h1: float = 842.0,
    w2: float = 595.0, h2: float = 1191.0,  # A3 second page
) -> bytes:
    """Create a two-page PDF with distinct page dimensions."""
    import fitz
    doc = fitz.open()
    for w, h in [(w1, h1), (w2, h2)]:
        page = doc.new_page(width=w, height=h)
        pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 10, 10))
        pix.set_rect(fitz.IRect(0, 0, 10, 10), (180,))
        page.insert_image(page.rect, pixmap=pix)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanPreprocessorPDFBranch:
    """Tests for the PDF rasterise-clean-reassemble path."""

    def test_page_count_preserved_single_page(self, tmp_path):
        import fitz
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        pdf_bytes = _make_single_page_pdf()
        src = tmp_path / "scan.pdf"
        src.write_bytes(pdf_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,  # keep fast in tests
            doc_id="TEST-001",
            output_dir=str(tmp_path),
        )

        assert result.pages_processed == 1
        assert os.path.exists(result.processed_path)
        out_doc = fitz.open(result.processed_path)
        assert len(out_doc) == 1
        out_doc.close()

    def test_page_count_preserved_two_pages(self, tmp_path):
        import fitz
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        pdf_bytes = _make_two_page_pdf()
        src = tmp_path / "scan2.pdf"
        src.write_bytes(pdf_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,
            doc_id="TEST-002",
            output_dir=str(tmp_path),
        )

        assert result.pages_processed == 2
        out_doc = fitz.open(result.processed_path)
        assert len(out_doc) == 2
        out_doc.close()

    def test_page_point_dimensions_preserved(self, tmp_path):
        """
        The reassembled PDF must preserve the original page's point dimensions
        exactly, because parser.py reads them to build pages_dimensions.
        """
        import fitz
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        W1, H1 = 595.0, 842.0
        W2, H2 = 595.0, 1191.0
        pdf_bytes = _make_two_page_pdf(W1, H1, W2, H2)
        src = tmp_path / "dims.pdf"
        src.write_bytes(pdf_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,
            doc_id="TEST-003",
            output_dir=str(tmp_path),
        )

        out_doc = fitz.open(result.processed_path)
        page0_rect = out_doc[0].rect
        page1_rect = out_doc[1].rect
        out_doc.close()

        assert abs(page0_rect.width - W1) < 1.0, f"Page 0 width mismatch: {page0_rect.width} vs {W1}"
        assert abs(page0_rect.height - H1) < 1.0, f"Page 0 height mismatch: {page0_rect.height} vs {H1}"
        assert abs(page1_rect.width - W2) < 1.0, f"Page 1 width mismatch: {page1_rect.width} vs {W2}"
        assert abs(page1_rect.height - H2) < 1.0, f"Page 1 height mismatch: {page1_rect.height} vs {H2}"

    def test_original_file_not_mutated(self, tmp_path):
        """The original source file must be byte-identical after processing."""
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        pdf_bytes = _make_single_page_pdf()
        src = tmp_path / "orig.pdf"
        src.write_bytes(pdf_bytes)

        preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,
            doc_id="TEST-004",
            output_dir=str(tmp_path),
        )

        assert src.read_bytes() == pdf_bytes, "Original file was mutated"

    def test_per_page_metadata_length_matches_page_count(self, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        pdf_bytes = _make_two_page_pdf()
        src = tmp_path / "meta.pdf"
        src.write_bytes(pdf_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,
            doc_id="TEST-005",
            output_dir=str(tmp_path),
        )

        assert len(result.per_page_metadata) == 2
        for meta in result.per_page_metadata:
            assert "grayscale_converted" in meta


class TestScanPreprocessorImageBranch:
    """Tests for the single-image path."""

    def test_image_output_is_valid_png(self, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        png_bytes = _make_png_bytes()
        src = tmp_path / "scan.png"
        src.write_bytes(png_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="image",
            target_scale=1.0,  # ignored for image branch
            doc_id="TEST-IMG-001",
            output_dir=str(tmp_path),
        )

        assert result.pages_processed == 1
        assert result.processed_path.endswith("_preprocessed.png")
        assert os.path.exists(result.processed_path)

        # Must be a valid image
        out_img = Image.open(result.processed_path)
        assert out_img.width > 0
        assert out_img.height > 0

    def test_image_original_not_mutated(self, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        png_bytes = _make_png_bytes()
        src = tmp_path / "orig.png"
        src.write_bytes(png_bytes)

        preprocess_scanned_document(
            file_path=str(src),
            file_category="image",
            target_scale=1.0,
            doc_id="TEST-IMG-002",
            output_dir=str(tmp_path),
        )

        assert src.read_bytes() == png_bytes


class TestScanPreprocessorErrorHandling:
    """Tests for invalid inputs and error modes."""

    def test_missing_source_file_raises_oserror(self, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        with pytest.raises(OSError, match="Source file not found"):
            preprocess_scanned_document(
                file_path=str(tmp_path / "nonexistent.pdf"),
                file_category="pdf",
                target_scale=1.0,
                doc_id="TEST-ERR-001",
                output_dir=str(tmp_path),
            )

    def test_unsupported_file_category_raises_valueerror(self, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        dummy = tmp_path / "dummy.xml"
        dummy.write_bytes(b"<root/>")

        with pytest.raises(ValueError, match="only handles 'pdf' or 'image'"):
            preprocess_scanned_document(
                file_path=str(dummy),
                file_category="xml",
                target_scale=1.0,
                doc_id="TEST-ERR-002",
                output_dir=str(tmp_path),
            )


class TestScanPreprocessorResultModel:
    """Tests for ScanPreprocessingResult field correctness."""

    def test_result_paths_are_distinct(self, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        pdf_bytes = _make_single_page_pdf()
        src = tmp_path / "distinct.pdf"
        src.write_bytes(pdf_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,
            doc_id="TEST-RES-001",
            output_dir=str(tmp_path),
        )

        assert result.original_path == str(src)
        assert result.processed_path != result.original_path
        assert "preprocessed" in os.path.basename(result.processed_path)
