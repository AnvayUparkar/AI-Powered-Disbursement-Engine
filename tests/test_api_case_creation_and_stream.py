import io
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from config import LOS_LOANS_DIR, S3_RAW_DIR
from pipeline.storage import read_json


@pytest.fixture
def client():
    return TestClient(app)


def test_get_next_case_id(client):
    response = client.get("/api/cases/next-id")
    assert response.status_code == 200
    data = response.json()
    assert "nextId" in data
    assert data["nextId"].startswith("LOAN_")


def test_create_case_endpoint(client):
    case_id = "LOAN_TEST_CREATE_099"
    payload = {
        "case_id": case_id,
        "applicant_name": "Test Applicant",
        "loan_type": "Personal Loan",
        "loan_amount": 750000.0,
        "tenure_months": 36,
    }

    try:
        response = client.post("/api/cases/create", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["caseId"] == case_id
        assert data["status"] == "created"

        # Verify LOS file was created
        los_file = LOS_LOANS_DIR / f"{case_id}.json"
        assert los_file.exists()
        los_data = read_json(los_file)
        assert los_data["applicant_name"] == "Test Applicant"
        assert los_data["funding_amount"] == 750000.0

        # Verify S3 raw dir was created
        raw_dir = S3_RAW_DIR / case_id
        assert raw_dir.exists()
    finally:
        # Clean up test files
        los_file = LOS_LOANS_DIR / f"{case_id}.json"
        if los_file.exists():
            los_file.unlink(missing_ok=True)
        raw_dir = S3_RAW_DIR / case_id
        if raw_dir.exists():
            import shutil
            shutil.rmtree(raw_dir, ignore_errors=True)


def test_document_upload_syncs_to_case_s3_raw(client):
    case_id = "LOAN_TEST_UPLOAD_SYNC_088"
    try:
        client.post("/api/cases/create", json={"case_id": case_id})

        dummy_pdf = b"%PDF-1.4 dummy application content"
        files = {
            "file": ("Application_Form_Test.pdf", dummy_pdf, "application/pdf")
        }
        data = {
            "case_id": case_id,
            "doc_type": "Application Form",
        }

        response = client.post("/api/v1/documents/upload", files=files, data=data)
        assert response.status_code == 200
        res_data = response.json()
        assert res_data["status"] == "completed"

        # Verify file was written to S3_RAW_DIR / case_id
        case_s3_dir = S3_RAW_DIR / case_id
        assert case_s3_dir.exists()
        matching_files = list(case_s3_dir.glob("*Application_Form*"))
        assert len(matching_files) > 0
    finally:
        # Clean up test files
        los_file = LOS_LOANS_DIR / f"{case_id}.json"
        if los_file.exists():
            los_file.unlink(missing_ok=True)
        case_s3_dir = S3_RAW_DIR / case_id
        if case_s3_dir.exists():
            import shutil
            shutil.rmtree(case_s3_dir, ignore_errors=True)



def test_stream_case_pipeline_events(client):
    case_id = "LOAN_001"
    response = client.get(f"/api/cases/{case_id}/stream")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    events = []
    for line in response.iter_lines():
        if line and line.startswith("data: "):
            payload_str = line[6:]
            evt = json.loads(payload_str)
            events.append(evt)

    # Must contain start and finish
    stages = [e.get("stage") for e in events]
    assert "start" in stages
    assert "fetch" in stages
    assert "extract" in stages
    assert "comparison" in stages
    assert "compile" in stages
    assert "scorecard" in stages
    assert "push" in stages
    assert "finish" in stages
