"""Step 4 ("capture page images for VLM region cropping") re-rasterises every page a second time
(the OCR/LightOnOCR pass already rasterised them once). It must be skipped whenever nothing
downstream can use its output: both VLM fallback branches require settings.VLM_ENABLED, and
comb-grid recovery only runs when docling_result is not None (never true on the LightOnOCR route).

Regression: this previously ran unconditionally, so with VLM_ENABLED=False (this deployment's
default) every scanned document paid for a full second full-document rasterisation for nothing --
directly compounding idp pod memory pressure on every LightOnOCR document.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idp.core.config import settings
from idp.services.document_processor import DocumentProcessor
from idp.services.document_preprocessor import PreprocessedDocument
from idp.services.docling.parser import DoclingParseResult
from idp.models.ocr import OCRElement, OCRResult


def _prep(is_scanned: bool, pages: int = 1) -> PreprocessedDocument:
    return PreprocessedDocument(
        filename="d.pdf", file_path="d.pdf", mime_type="application/pdf",
        file_size_bytes=1024, page_count=pages, file_category="pdf", is_scanned_pdf=is_scanned,
    )


def _wire_common(processor, monkeypatch, prep):
    monkeypatch.setattr(processor.storage, "download", AsyncMock(return_value="d.pdf"))
    monkeypatch.setattr(processor.preprocessor, "preprocess", lambda *a, **k: prep)
    monkeypatch.setattr(processor.serializer, "build_unified_document", MagicMock())
    monkeypatch.setattr(processor, "_save_and_upload_output", AsyncMock(return_value="s3://b/p.json"))
    monkeypatch.setattr("idp.services.document_processor.preprocess_scanned_document",
                         MagicMock(side_effect=RuntimeError("no real preprocessing in this test")))


@pytest.mark.asyncio
async def test_lightonocr_route_skips_step4_when_vlm_disabled(monkeypatch):
    """The exact bug: scanned doc, LightOnOCR route, VLM off -> no second rasterisation."""
    processor = DocumentProcessor()
    prep = _prep(is_scanned=True)
    _wire_common(processor, monkeypatch, prep)
    monkeypatch.setattr(settings, "LIGHTONOCR_ENABLED", True)
    monkeypatch.setattr(settings, "VLM_ENABLED", False)
    monkeypatch.setattr(settings, "ENABLE_SCAN_PREPROCESSING", False)

    ocr_res = OCRResult(page_number=1, elements=[
        OCRElement(id="e1", text="hello", bbox=[0, 0, 10, 10], confidence=0.9, page_number=1, source="lightonocr"),
    ], image_width=595.0, image_height=842.0)
    monkeypatch.setattr(
        "idp.services.ocr.lightonocr_adapter.LightOnOCRAdapter.process_page_to_ocr_result",
        MagicMock(return_value=ocr_res),
    )

    get_page_images = AsyncMock(return_value=[(b"png", 595.0, 842.0)])
    monkeypatch.setattr(processor, "_get_page_images", get_page_images)

    res = await processor.process_document(document_id="D1", s3_key="raw/d.pdf", s3_bucket="b")

    assert res["status"] == "completed"
    get_page_images.assert_awaited_once()  # only the OCR-pass call; Step 4's call must not fire


@pytest.mark.asyncio
async def test_lightonocr_route_still_captures_page_images_when_vlm_enabled(monkeypatch):
    """VLM on -> Step 4 must still run so the fallback branch has something to crop."""
    processor = DocumentProcessor()
    prep = _prep(is_scanned=True)
    _wire_common(processor, monkeypatch, prep)
    monkeypatch.setattr(settings, "LIGHTONOCR_ENABLED", True)
    monkeypatch.setattr(settings, "VLM_ENABLED", True)
    monkeypatch.setattr(settings, "ENABLE_SCAN_PREPROCESSING", False)

    failed_res = OCRResult(page_number=1, elements=[], extraction_failed=True, image_width=595.0, image_height=842.0)
    monkeypatch.setattr(
        "idp.services.ocr.lightonocr_adapter.LightOnOCRAdapter.process_page_to_ocr_result",
        MagicMock(return_value=failed_res),
    )
    monkeypatch.setattr(processor.vlm_client, "analyze_region", AsyncMock(return_value=None))

    get_page_images = AsyncMock(return_value=[(b"png", 595.0, 842.0)])
    monkeypatch.setattr(processor, "_get_page_images", get_page_images)

    res = await processor.process_document(document_id="D2", s3_key="raw/d.pdf", s3_bucket="b")

    assert res["status"] == "completed"
    assert get_page_images.await_count == 2  # OCR pass + Step 4 (VLM needs the crop source)


@pytest.mark.asyncio
async def test_docling_route_still_captures_page_images_for_comb_grid(monkeypatch):
    """Digital/Docling route with VLM off -> Step 4 must still run: comb-grid recovery needs it."""
    processor = DocumentProcessor()
    prep = _prep(is_scanned=False)
    _wire_common(processor, monkeypatch, prep)
    monkeypatch.setattr(settings, "LIGHTONOCR_ENABLED", False)
    monkeypatch.setattr(settings, "VLM_ENABLED", False)

    docling_res = DoclingParseResult(elements=[], tables=[], page_count=1,
                                     pages_dimensions=[{"width": 595.0, "height": 842.0}])
    mock_parser = MagicMock()
    mock_parser.parse.return_value = docling_res
    monkeypatch.setattr(processor, "_get_docling_parser", lambda doc_type, is_scanned=None: mock_parser)

    get_page_images = AsyncMock(return_value=[(b"png", 595.0, 842.0)])
    monkeypatch.setattr(processor, "_get_page_images", get_page_images)
    monkeypatch.setattr(processor, "_recover_comb_grids", MagicMock(return_value=0))

    res = await processor.process_document(document_id="D3", s3_key="raw/d.pdf", s3_bucket="b")

    assert res["status"] == "completed"
    get_page_images.assert_awaited_once()  # Step 4's single call: comb-grid recovery needs it
