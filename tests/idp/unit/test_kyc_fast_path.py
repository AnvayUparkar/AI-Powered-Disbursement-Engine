import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from idp.services.document_processor import DocumentProcessor
from idp.services.document_preprocessor import PreprocessedDocument


@pytest.mark.asyncio
async def test_kyc_image_invokes_docling():
    """Verify that KYC / image documents now invoke Docling layout and integrated OCR."""
    processor = DocumentProcessor()
    processor.storage = MagicMock()
    processor.storage.download = AsyncMock(return_value=True)
    processor.preprocessor = MagicMock()
    processor.docling_parser = MagicMock()
    processor.ocr_router = MagicMock()
    processor.router = MagicMock()
    processor.vlm_client = MagicMock()
    processor.serializer = MagicMock()

    # Mock preprocessed doc as an image category
    prep_mock = PreprocessedDocument(
        file_path="mock_pan.png",
        filename="mock_pan.png",
        mime_type="image/png",
        file_size_bytes=1024,
        file_category="image",
        page_count=1,
        is_scanned_pdf=False,
    )
    processor.preprocessor.preprocess.return_value = prep_mock
    processor._get_page_images = AsyncMock(return_value=[(b"img_bytes", 500.0, 300.0)])
    processor._save_and_upload_output = AsyncMock(return_value="s3://bucket/parsed.json")

    mock_docling_result = MagicMock()
    mock_docling_result.elements = []
    processor.docling_parser.parse.return_value = mock_docling_result
    processor.router.get_low_confidence_layout_elements.return_value = []
    processor.serializer.build_unified_document.return_value = MagicMock()

    with patch("os.path.exists", return_value=True):
        res = await processor.process_document("doc-kyc-pan", "mock_pan.png")

    assert res["status"] == "completed"
    # Docling parser SHOULD be called for KYC / image
    processor.docling_parser.parse.assert_called_once()

    # Verify serializer was called with docling_used=True
    call_kwargs = processor.serializer.build_unified_document.call_args.kwargs
    assert call_kwargs["docling_used"] is True
    assert call_kwargs["docling_result"] is mock_docling_result


@pytest.mark.asyncio
async def test_bank_statement_pdf_invokes_docling():
    """Verify that multi-page tabular PDF documents (e.g. Bank Statements) DO invoke Docling."""
    processor = DocumentProcessor()
    processor.storage = MagicMock()
    processor.storage.download = AsyncMock(return_value=True)
    processor.preprocessor = MagicMock()
    processor.docling_parser = MagicMock()
    processor.ocr_router = MagicMock()
    processor.router = MagicMock()
    processor.vlm_client = MagicMock()
    processor.serializer = MagicMock()

    prep_mock = PreprocessedDocument(
        file_path="bank_statement.pdf",
        filename="bank_statement.pdf",
        mime_type="application/pdf",
        file_size_bytes=204800,
        file_category="pdf",
        page_count=3,
        is_scanned_pdf=False,
    )
    processor.preprocessor.preprocess.return_value = prep_mock
    processor._get_page_images = AsyncMock(return_value=[(b"img_bytes", 595.0, 842.0)])
    processor._save_and_upload_output = AsyncMock(return_value="s3://bucket/parsed.json")

    mock_docling_result = MagicMock()
    mock_docling_result.elements = []
    processor.docling_parser.parse.return_value = mock_docling_result

    mock_ocr = MagicMock()
    mock_ocr.page_number = 1
    mock_ocr.elements = []
    mock_ocr.average_confidence = 0.95
    mock_ocr.low_confidence_count = 0
    mock_ocr.image_width = 595.0
    mock_ocr.image_height = 842.0
    processor.ocr_router.process_page.return_value = mock_ocr
    processor.router.should_use_vlm.return_value = False
    processor.serializer.build_unified_document.return_value = MagicMock()

    with patch("os.path.exists", return_value=True):
        res = await processor.process_document("doc-bank-statement", "bank_statement.pdf")

    assert res["status"] == "completed"
    # Docling parser SHOULD be called for bank statement PDF
    processor.docling_parser.parse.assert_called_once()

    # Verify serializer was called with docling_used=True
    call_kwargs = processor.serializer.build_unified_document.call_args.kwargs
    assert call_kwargs["docling_used"] is True
    assert call_kwargs["docling_result"] is mock_docling_result
