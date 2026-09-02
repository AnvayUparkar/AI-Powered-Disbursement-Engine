from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from pipeline.storage import read_json, write_json

client = TestClient(app)


def test_cors_headers():
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_document_preview_path_traversal_blocked():
    # Attempting directory traversal via case_id or doc_name must be rejected and never leak sensitive files
    res1 = client.get("/api/documents/preview/../.env")
    assert res1.status_code in (400, 404)

    res2 = client.get("/api/documents/preview/LOAN_001/..%2f..%2fconfig.py")
    assert res2.status_code in (400, 404)

    res3 = client.get("/api/documents/preview/LOAN_001/..%2f..%2f.env")
    assert res3.status_code in (400, 404)



def test_document_preview_nonexistent_returns_404():
    res = client.get("/api/documents/preview/LOAN_001/completely_non_existent_file.pdf")
    assert res.status_code == 404
    assert res.json()["detail"] == "Document not found"


def test_document_preview_valid_file(tmp_path: Path, monkeypatch):
    # Setup mock file inside S3_RAW_DIR
    mock_loan_dir = tmp_path / "LOAN_SEC_001"
    mock_loan_dir.mkdir(parents=True, exist_ok=True)
    mock_pdf = mock_loan_dir / "sample.pdf"
    mock_pdf.write_bytes(b"%PDF-1.4 Mock valid content")

    monkeypatch.setattr("app.routers.documents.S3_RAW_DIR", tmp_path)
    monkeypatch.setattr("app.routers.documents.DMS_DIR", tmp_path / "dms")

    res = client.get("/api/documents/preview/LOAN_SEC_001/sample.pdf")
    assert res.status_code == 200
    assert res.content == b"%PDF-1.4 Mock valid content"
    assert res.headers["content-type"] == "application/pdf"


def test_human_adjudication_state_persistence(tmp_path: Path, monkeypatch):
    """Test that submitting an adjudication decision actually updates comparison_results.json and scorecard.json."""
    loan_id = "LOAN_002"
    res_dir = tmp_path / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)

    # Initial discrepancy records
    comp_records = [
        {
            "check_id": "chk_pan_number",
            "subnode": "loan_kyc",
            "field": "pan_number",
            "match_status": "MISMATCH",
            "confidence": 1.0,
            "notes": "PAN mismatch",
        },
        {
            "check_id": "chk_kyc_name_pan",
            "subnode": "loan_kyc",
            "field": "applicant_name",
            "match_status": "MATCH",
            "confidence": 1.0,
            "notes": "Name match",
        }
    ]
    write_json(res_dir / "comparison_results.json", comp_records)
    write_json(res_dir / "subnode_rollups.json", {"loan_kyc": "Discrepancy"})
    write_json(res_dir / "status.json", {
        "loan_id": loan_id,
        "current_node": "done",
        "node_history": ["fetch", "extract", "comparison", "compile", "scorecard", "push", "done"],
        "errors": [],
    })
    write_json(res_dir / "scorecard.json", {
        "loan_id": loan_id,
        "preliminary_decision": "REJECT_OR_FLAG",
        "subnode_rollups": {"loan_kyc": "Discrepancy"},
    })

    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path)
    monkeypatch.setattr("app.serializers.case_serializer.list_loan_ids", lambda: [loan_id])
    monkeypatch.setattr("app.routers.reviews.S3_RESULT_DIR", tmp_path)
    monkeypatch.setattr("pipeline.audit.S3_RESULT_DIR", tmp_path)


    # Fetch review item for this discrepancy
    reviews_res = client.get(f"/api/reviews?caseId={loan_id}")
    assert reviews_res.status_code == 200
    items = reviews_res.json()
    assert len(items) > 0
    target_review = next((it for it in items if it["checkpointId"] in (4, "chk_pan_number")), items[0])

    # Adjudicate APPROVE / OVERRIDE
    adj_payload = {
        "decision": "OVERRIDE",
        "notes": "Verified physically against original document",
        "assignedTo": "Senior Credit Officer",
    }
    adj_res = client.post(f"/api/reviews/{target_review['id']}/adjudicate", json=adj_payload)
    assert adj_res.status_code == 200
    assert adj_res.json()["status"] == "success"
    assert adj_res.json()["records_updated"] >= 1

    # Verify updated files on disk
    updated_records = read_json(res_dir / "comparison_results.json")
    pan_chk = next(r for r in updated_records if r["check_id"] == "chk_pan_number")
    assert pan_chk["match_status"] == "MATCH"
    assert "Verified physically" in pan_chk["notes"]

    updated_rollups = read_json(res_dir / "subnode_rollups.json")
    assert updated_rollups["loan_kyc"] == "Verified"

    updated_scorecard = read_json(res_dir / "scorecard.json")
    assert updated_scorecard["preliminary_decision"] == "AUTO_APPROVE_ELIGIBLE"

