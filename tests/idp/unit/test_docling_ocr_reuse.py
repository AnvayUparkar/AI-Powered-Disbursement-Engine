import pytest
from unittest.mock import AsyncMock, MagicMock
from idp.services.document_processor import DocumentProcessor
from idp.services.docling.parser import DoclingParseResult
from idp.models.layout import LayoutElement, ElementType
from idp.models.ocr import OCRResult, OCRElement
from idp.services.document_preprocessor import PreprocessedDocument


@pytest.mark.asyncio
async def test_reuse_docling_ocr_skips_step4_rapidocr(monkeypatch):
    """When Docling already extracted text elements, Step 4 RapidOCR is skipped and Docling elements are reused."""
    processor = DocumentProcessor()

    # Create synthetic docling result with 2 pages of elements
    docling_res = DoclingParseResult(
        elements=[
            LayoutElement(
                id="docling-1",
                type=ElementType.HEADING,
                text="KEY FACT STATEMENT",
                bbox=[50.0, 50.0, 200.0, 70.0],
                confidence=0.98,
                page_number=1,
                reading_order=1,
            ),
            LayoutElement(
                id="docling-2",
                type=ElementType.PARAGRAPH,
                text="Loan Proposal Number 12345",
                bbox=[50.0, 80.0, 300.0, 100.0],
                confidence=0.95,
                page_number=1,
                reading_order=2,
            ),
            LayoutElement(
                id="docling-3",
                type=ElementType.PARAGRAPH,
                text="Repayment Schedule Summary",
                bbox=[50.0, 50.0, 300.0, 70.0],
                confidence=0.96,
                page_number=2,
                reading_order=1,
            ),
        ],
        tables=[],
        page_count=2,
        pages_dimensions=[
            {"width": 600.0, "height": 800.0},
            {"width": 600.0, "height": 800.0},
        ]
    )

    # Mock docling parser to return our synthetic docling_res
    mock_docling_parser = MagicMock()
    mock_docling_parser.parse.return_value = docling_res
    monkeypatch.setattr(processor, "_get_docling_parser", lambda doc_type: mock_docling_parser)

    # Mock storage and preprocessing
    monkeypatch.setattr(processor.storage, "download", AsyncMock(return_value="dummy_path.pdf"))
    prep_mock = PreprocessedDocument(
        filename="dummy_path.pdf",
        file_path="dummy_path.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=2,
        file_category="pdf"
    )
    monkeypatch.setattr(processor.preprocessor, "preprocess", lambda *args, **kwargs: prep_mock)

    # Spy on _get_page_images to ensure it is NOT called during Step 4
    get_page_images_mock = AsyncMock(return_value=[(b"fake_png_1", 600.0, 800.0), (b"fake_png_2", 600.0, 800.0)])
    monkeypatch.setattr(processor, "_get_page_images", get_page_images_mock)

    # Spy on ocr_router.process_page to ensure it is NOT called
    process_page_mock = MagicMock()
    monkeypatch.setattr(processor.ocr_router, "process_page", process_page_mock)

    # Mock serializer and save
    monkeypatch.setattr(processor.serializer, "build_unified_document", MagicMock())
    monkeypatch.setattr(processor, "_save_and_upload_output", AsyncMock(return_value="s3://bucket/parsed.json"))

    res = await processor.process_document(
        document_id="TEST_REUSE_DOC",
        s3_key="raw-documents/test.pdf",
        s3_bucket="test-bucket"
    )

    assert res["status"] == "completed"
    # Verify Step 4 RapidOCR was completely skipped
    get_page_images_mock.assert_not_called()
    process_page_mock.assert_not_called()

    # Verify serializer was called with the reused Docling elements as OCRResult
    serializer_call_kwargs = processor.serializer.build_unified_document.call_args.kwargs
    ocr_results: list[OCRResult] = serializer_call_kwargs["ocr_results"]
    assert len(ocr_results) == 2
    assert ocr_results[0].page_number == 1
    assert len(ocr_results[0].elements) == 2
    assert ocr_results[0].elements[0].text == "KEY FACT STATEMENT"
    assert ocr_results[0].elements[0].source == "docling"
    assert ocr_results[1].page_number == 2
    assert len(ocr_results[1].elements) == 1
    assert ocr_results[1].elements[0].text == "Repayment Schedule Summary"


@pytest.mark.asyncio
async def test_fallback_to_step4_rapidocr_when_no_docling_text(monkeypatch):
    """When Docling returns no text elements, the pipeline cleanly falls back to Step 4 RapidOCR."""
    processor = DocumentProcessor()

    # Docling returns empty elements
    docling_res = DoclingParseResult(
        elements=[],
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}]
    )
    mock_docling_parser = MagicMock()
    mock_docling_parser.parse.return_value = docling_res
    monkeypatch.setattr(processor, "_get_docling_parser", lambda doc_type: mock_docling_parser)
    monkeypatch.setattr(processor.storage, "download", AsyncMock(return_value="dummy_path.pdf"))
    prep_mock = PreprocessedDocument(
        filename="dummy_path.pdf",
        file_path="dummy_path.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        file_category="pdf"
    )
    monkeypatch.setattr(processor.preprocessor, "preprocess", lambda *args, **kwargs: prep_mock)

    # Mock _get_page_images
    get_page_images_mock = AsyncMock(return_value=[(b"fake_png", 595.0, 842.0)])
    monkeypatch.setattr(processor, "_get_page_images", get_page_images_mock)

    # Mock ocr_router.process_page to return a valid OCRResult
    dummy_ocr_elem = OCRElement(
        id="rapid-1",
        text="Fallback Extracted Line",
        bbox=[10.0, 20.0, 100.0, 40.0],
        confidence=0.99,
        page_number=1,
        source="rapidocr"
    )
    dummy_ocr_res = OCRResult(
        page_number=1,
        elements=[dummy_ocr_elem],
        average_confidence=0.99,
        total_elements=1,
        image_width=595.0,
        image_height=842.0
    )
    process_page_mock = MagicMock(return_value=dummy_ocr_res)
    monkeypatch.setattr(processor.ocr_router, "process_page", process_page_mock)

    monkeypatch.setattr(processor.serializer, "build_unified_document", MagicMock())
    monkeypatch.setattr(processor, "_save_and_upload_output", AsyncMock(return_value="s3://bucket/parsed.json"))

    res = await processor.process_document(
        document_id="TEST_FALLBACK_DOC",
        s3_key="raw-documents/fallback.pdf",
        s3_bucket="test-bucket"
    )

    assert res["status"] == "completed"
    # Since Docling had no text, _get_page_images and process_page MUST have been called
    get_page_images_mock.assert_called_once()
    process_page_mock.assert_called_once()


@pytest.mark.asyncio
async def test_lazy_page_image_loading_when_vlm_fallback_needed(monkeypatch):
    """When reused Docling elements trigger VLM fallback, page images are loaded lazily on demand."""
    processor = DocumentProcessor()

    # Docling result with low confidence element that will trigger VLM
    docling_res = DoclingParseResult(
        elements=[
            LayoutElement(
                id="low-conf-1",
                type=ElementType.PARAGRAPH,
                text="Unclear PAN Number",
                bbox=[20.0, 20.0, 100.0, 40.0],
                confidence=0.50,  # Below threshold 0.80
                page_number=1,
                reading_order=1,
            )
        ],
        tables=[],
        page_count=1,
        pages_dimensions=[{"width": 595.0, "height": 842.0}]
    )
    mock_docling_parser = MagicMock()
    mock_docling_parser.parse.return_value = docling_res
    monkeypatch.setattr(processor, "_get_docling_parser", lambda doc_type: mock_docling_parser)
    monkeypatch.setattr(processor.storage, "download", AsyncMock(return_value="dummy_path.pdf"))
    prep_mock = PreprocessedDocument(
        filename="dummy_path.pdf",
        file_path="dummy_path.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        file_category="pdf"
    )
    monkeypatch.setattr(processor.preprocessor, "preprocess", lambda *args, **kwargs: prep_mock)

    # _get_page_images should only be called lazily during VLM step
    get_page_images_mock = AsyncMock(return_value=[(b"dummy_bytes", 595.0, 842.0)])
    monkeypatch.setattr(processor, "_get_page_images", get_page_images_mock)

    # Force router to flag VLM
    monkeypatch.setattr(processor.router, "should_use_vlm", lambda ocr_res, doc_id: True)
    low_elem = OCRElement(
        id="low-conf-1",
        text="Unclear PAN Number",
        bbox=[20.0, 20.0, 100.0, 40.0],
        confidence=0.50,
        page_number=1,
        source="docling",
        needs_vlm=True
    )
    monkeypatch.setattr(processor.router, "get_low_confidence_elements", lambda ocr_res: [low_elem])
    monkeypatch.setattr(processor.vlm_client, "analyze_region", AsyncMock(return_value=None))

    monkeypatch.setattr(processor.serializer, "build_unified_document", MagicMock())
    monkeypatch.setattr(processor, "_save_and_upload_output", AsyncMock(return_value="s3://bucket/parsed.json"))

    res = await processor.process_document(
        document_id="TEST_LAZY_VLM_DOC",
        s3_key="raw-documents/vlm.pdf",
        s3_bucket="test-bucket"
    )

    assert res["status"] == "completed"
    # Verified lazy image loading was called for VLM cropping
    get_page_images_mock.assert_called_once()
