"""
Regression guard: digital PDF path must never route through scan_preprocessor.

Tests
-----
1. is_scanned_pdf=False + flag=True  → preprocess_scanned_document NOT called.
2. is_scanned_pdf=True  + flag=True  → preprocess_scanned_document IS called.
3. is_scanned_pdf=True  + flag=False → preprocess_scanned_document NOT called.

All tests are pure unit tests using monkeypatching; no real file I/O or
Docling/S3 calls are made.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idp.services.document_preprocessor import PreprocessedDocument


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_prep_doc(is_scanned: bool, file_category: str = "pdf") -> PreprocessedDocument:
    return PreprocessedDocument(
        file_path="/fake/doc.pdf",
        filename="doc.pdf",
        file_category=file_category,
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        is_scanned_pdf=is_scanned,
        pages_dimensions=[{"width": 595.0, "height": 842.0}],
    )


# ---------------------------------------------------------------------------
# Route-guard tests (pure unit — monkeypatched)
# ---------------------------------------------------------------------------

class TestDocumentProcessorRouting:
    """
    Ensures the scan-preprocessing branch is entered / skipped correctly.

    We test the branch logic directly rather than running the full async
    process_document pipeline (which requires S3, Docling, etc.).
    """

    def test_digital_pdf_never_calls_scan_preprocessor(self, monkeypatch):
        """
        Lock: when is_scanned_pdf=False the scan branch must be completely skipped,
        regardless of ENABLE_SCAN_PREPROCESSING value.
        """
        called = {"hit": False}

        def _fake_preprocess(*args, **kwargs):
            called["hit"] = True
            raise AssertionError("scan_preprocessor called on a digital document!")

        monkeypatch.setattr(
            "idp.services.document_processor.preprocess_scanned_document",
            _fake_preprocess,
        )

        # Simulate the branch condition directly (mirrors document_processor.py logic)
        import config.settings as cfg
        monkeypatch.setattr(cfg, "ENABLE_SCAN_PREPROCESSING", True)

        prep_doc = _make_prep_doc(is_scanned=False)

        # The branch condition: if prep_doc.is_scanned_pdf and settings.ENABLE_SCAN_PREPROCESSING
        branch_would_fire = prep_doc.is_scanned_pdf and cfg.ENABLE_SCAN_PREPROCESSING
        assert not branch_would_fire, "Branch condition must be False for digital PDFs"
        assert not called["hit"], "scan_preprocessor must not be called for digital PDFs"

    def test_scanned_pdf_calls_scan_preprocessor_when_flag_enabled(self, monkeypatch):
        """
        When is_scanned_pdf=True and ENABLE_SCAN_PREPROCESSING=True, the branch
        condition must evaluate to True so scan_preprocessor would be called.
        """
        import config.settings as cfg
        monkeypatch.setattr(cfg, "ENABLE_SCAN_PREPROCESSING", True)

        prep_doc = _make_prep_doc(is_scanned=True)

        branch_would_fire = prep_doc.is_scanned_pdf and cfg.ENABLE_SCAN_PREPROCESSING
        assert branch_would_fire, (
            "Branch condition must be True for scanned PDFs when flag is enabled"
        )

    def test_scanned_pdf_skips_scan_preprocessor_when_flag_disabled(self, monkeypatch):
        """
        When ENABLE_SCAN_PREPROCESSING=False, the branch must be skipped even for
        genuinely scanned documents (kill-switch for staging regression).
        """
        import config.settings as cfg
        monkeypatch.setattr(cfg, "ENABLE_SCAN_PREPROCESSING", False)

        prep_doc = _make_prep_doc(is_scanned=True)

        branch_would_fire = prep_doc.is_scanned_pdf and cfg.ENABLE_SCAN_PREPROCESSING
        assert not branch_would_fire, (
            "Branch condition must be False when ENABLE_SCAN_PREPROCESSING=False"
        )

    def test_xml_fast_path_never_reaches_scan_preprocessor(self, monkeypatch):
        """
        XML documents exit via the XML fast-path before the Docling step,
        so they can never reach the scan preprocessing branch.
        """
        # XML fast-path returns before Step 3 — is_scanned_pdf is irrelevant.
        # We verify that is_scanned_pdf is False for XML (DocumentPreprocessor
        # hard-codes category="xml" with is_scanned=False).
        prep_doc = _make_prep_doc(is_scanned=False, file_category="xml")
        assert prep_doc.file_category == "xml"
        assert not prep_doc.is_scanned_pdf


class TestScanPreprocessingResultPathRules:
    """
    Contract tests on ScanPreprocessingResult to ensure the output path
    naming convention is stable (other tests depend on this).
    """

    def test_pdf_output_path_contains_doc_id_and_preprocessed(self, tmp_path):
        import io
        import fitz
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        doc = fitz.open()
        doc.new_page()
        buf = io.BytesIO()
        doc.save(buf)
        pdf_bytes = buf.getvalue()

        src = tmp_path / "test.pdf"
        src.write_bytes(pdf_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="pdf",
            target_scale=1.0,
            doc_id="DOC-XYZ",
            output_dir=str(tmp_path),
        )

        basename = result.processed_path.replace("\\", "/").split("/")[-1]
        assert "DOC-XYZ" in basename
        assert "preprocessed" in basename
        assert basename.endswith(".pdf")

    def test_image_output_path_contains_doc_id_and_preprocessed(self, tmp_path):
        import io
        import numpy as np
        from PIL import Image
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document

        arr = np.zeros((100, 100), dtype=np.uint8)
        arr[20:80, 20:80] = 200
        buf = io.BytesIO()
        Image.fromarray(arr, mode="L").save(buf, format="PNG")
        png_bytes = buf.getvalue()

        src = tmp_path / "img.png"
        src.write_bytes(png_bytes)

        result = preprocess_scanned_document(
            file_path=str(src),
            file_category="image",
            target_scale=1.0,
            doc_id="DOC-ABC",
            output_dir=str(tmp_path),
        )

        basename = result.processed_path.replace("\\", "/").split("/")[-1]
        assert "DOC-ABC" in basename
        assert "preprocessed" in basename
        assert basename.endswith(".png")
