import json
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_audit_all_cases():
    res = client.get("/api/audit")
    assert res.status_code == 200
    events = res.json()
    assert isinstance(events, list)
    if events:
        first = events[0]
        assert "id" in first
        assert "timestamp" in first
        assert "action" in first
        assert "component" in first
        assert "result" in first
        assert "caseId" in first


def test_api_audit_filtered_by_case():
    res = client.get("/api/audit?case_id=LOAN_001")
    assert res.status_code == 200
    events = res.json()
    assert isinstance(events, list)
    for ev in events:
        assert ev["caseId"] == "LOAN_001"


def test_api_audit_nonexistent_case():
    res = client.get("/api/audit?case_id=NONEXISTENT_CASE_999")
    assert res.status_code == 200
    events = res.json()
    assert isinstance(events, list)
    assert len(events) == 0


def test_api_audit_synthetic_artifacts(tmp_path, monkeypatch):
    test_s3_dir = tmp_path / "s3_result"
    loan_dir = test_s3_dir / "TEST_LOAN_01"
    loan_dir.mkdir(parents=True)

    # 1. audit_log.json
    audit_log = [
        {
            "timestamp": "2026-09-05T14:30:15",
            "type": "llm_adjudication",
            "field_type": "Applicant Name",
            "adjudication_status": "MATCH",
            "confidence": 0.95,
            "value_a": "John Doe",
            "value_b": "Johnathan Doe",
            "reason": "Spelling variation with identical identity details",
        },
        {
            "timestamp": "2026-09-05T14:40:00",
            "type": "human_adjudication_decision",
            "checkpoint_name": "KYC",
            "decision": "APPROVE",
            "adjudicated_by": "OpsLead",
            "notes": "Verified against physical file",
        },
        {
            "timestamp": "2026-09-05T14:42:00",
            "type": "llm_adjudication_error",
            "field_type": "Address",
            "error": "Rate limit exceeded",
        },
    ]
    with open(loan_dir / "audit_log.json", "w", encoding="utf-8") as f:
        json.dump(audit_log, f)

    # 2. status.json
    status_data = {
        "updated_at": "2026-09-05T14:45:00",
        "node_history": ["fetch", "extract", "comparison", "checker", "scorecard", "push"],
        "errors": [],
    }
    with open(loan_dir / "status.json", "w", encoding="utf-8") as f:
        json.dump(status_data, f)

    # 3. scorecard.json
    scorecard_data = {
        "preliminary_decision": "AUTO_APPROVE_ELIGIBLE",
    }
    with open(loan_dir / "scorecard.json", "w", encoding="utf-8") as f:
        json.dump(scorecard_data, f)

    monkeypatch.setattr("app.routers.dashboard_reports.S3_RESULT_DIR", test_s3_dir)

    res = client.get("/api/audit?case_id=TEST_LOAN_01")
    assert res.status_code == 200
    events = res.json()
    assert len(events) == 3 + 6 + 1  # 3 audit entries + 6 nodes + 1 scorecard

    action_names = [e["action"] for e in events]
    assert "LLM Adjudication: Applicant Name" in action_names
    assert "Human review override: KYC" in action_names
    assert "LLM Adjudication Fallback: Address" in action_names
    assert "Scorecard Decision: AUTO_APPROVE_ELIGIBLE" in action_names
