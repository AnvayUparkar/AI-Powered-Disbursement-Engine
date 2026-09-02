from fastapi.testclient import TestClient
from idp.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "rapidocr" in data["ocr_engine"].lower()


def test_process_document_api():
    payload = {
        "document_id": "TEST-API-999",
        "s3_key": "raw-documents/loan_agreement.pdf"
    }
    response = client.post("/api/v1/documents/process", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == "TEST-API-999"
    assert data["status"] == "completed"
    assert "parsed-documents/" in data["output_location"]
