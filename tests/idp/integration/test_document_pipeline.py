import pytest
from idp.services.document_processor import DocumentProcessor


@pytest.mark.asyncio
async def test_end_to_end_document_pipeline():
    processor = DocumentProcessor()
    # Test document pipeline with mock key
    res = await processor.process_document(
        document_id="TEST-E2E-101",
        s3_key="raw-documents/test_app.pdf"
    )

    assert res["status"] == "completed"
    assert res["document_id"] == "TEST-E2E-101"
    assert "parsed-documents/" in res["output_location"]
    assert res["processing_time_seconds"] > 0
