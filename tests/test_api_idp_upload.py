import io
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_api_v1_documents_upload_success():
    """
    Test uploading a valid document payload to /api/v1/documents/upload via the main app gateway.
    """
    # Create synthetic test file content
    file_content = b"%PDF-1.4 Mock PDF content for IDP verification testing"
    files = {
        "file": ("test_sanction.pdf", io.BytesIO(file_content), "application/pdf")
    }
    data = {
        "document_id": "TEST_DOC_UPLOAD_001",
        "s3_bucket": "test-bucket"
    }

    response = client.post("/api/v1/documents/upload", files=files, data=data)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["document_id"] == "TEST_DOC_UPLOAD_001"
    assert res_data["status"] in ("completed", "processing", "success")
    assert "processing_time_seconds" in res_data
    assert "output_location" in res_data


def test_api_v1_documents_upload_without_optional_fields():
    """
    Test direct upload with auto-generated document_id.
    """
    file_content = b"Plain document test payload."
    files = {
        "file": ("document.pdf", io.BytesIO(file_content), "application/pdf")
    }

    response = client.post("/api/v1/documents/upload", files=files)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["document_id"].startswith("DOC-")
    assert res_data["status"] in ("completed", "processing", "success")


def test_api_v1_documents_get_not_found():
    """
    Test retrieving a non-existent document ID returns 404 with structured error response.
    """
    response = client.get("/api/v1/documents/NON_EXISTENT_DOC_99999")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data or "error" in data
