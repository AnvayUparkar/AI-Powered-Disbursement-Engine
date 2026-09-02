from fastapi.testclient import TestClient

from app.main import app
from config import LOS_RECEIVED_DIR, S3_RESULT_DIR
from pipeline.graph import run_pipeline

from pipeline.storage import read_json

client = TestClient(app)


def test_integration_full_pipeline_loan_001():
    loan_id = "LOAN_001"
    final_state = run_pipeline(loan_id)

    assert final_state["loan_id"] == loan_id
    assert len(final_state["node_history"]) >= 5
    assert final_state["subnode_rollups"]["loan_kyc"] == "Verified"

    # Verify files created in S3 result
    loan_res_dir = S3_RESULT_DIR / loan_id
    assert (loan_res_dir / "status.json").exists()
    assert (loan_res_dir / "comparison_results.json").exists()
    assert (loan_res_dir / "subnode_rollups.json").exists()
    assert (loan_res_dir / "compiled_report.json").exists()
    assert (loan_res_dir / "checker_result.json").exists()
    assert (loan_res_dir / "scorecard.json").exists()


    # Verify push to LOS scorecards_received
    los_scorecard_file = LOS_RECEIVED_DIR / f"{loan_id}_scorecard.json"
    assert los_scorecard_file.exists()


def test_integration_pipeline_loan_002_discrepancies():
    loan_id = "LOAN_002"
    final_state = run_pipeline(loan_id)

    rollups = final_state["subnode_rollups"]
    # Should have Discrepancy due to amount mismatch, Aadhaar XML missing, etc.
    assert rollups["loan_kyc"] == "Discrepancy"
    assert rollups["kfs_sanction"] == "Discrepancy"

    scorecard = final_state["scorecard"]
    assert scorecard["preliminary_decision"] == "REJECT_OR_FLAG"


def test_integration_pipeline_loan_003_llm_adjudication():
    loan_id = "LOAN_003"
    final_state = run_pipeline(loan_id)

    # Name in PARTIAL band is adjudicated by Gemini
    assert final_state["subnode_rollups"]["loan_kyc"] in ("Verified", "Indeterminate")

    # Audit log should contain the LLM adjudication entry
    audit_file = S3_RESULT_DIR / loan_id / "audit_log.json"
    assert audit_file.exists()
    audit_entries = read_json(audit_file)
    assert any(e.get("type") in ("llm_adjudication", "llm_adjudication_stub") for e in audit_entries)


def test_api_endpoints():
    # 1. GET /api/loans
    res = client.get("/api/loans")
    assert res.status_code == 200
    data = res.json()
    assert "LOAN_001" in data["loan_ids"]
    assert "LOAN_002" in data["loan_ids"]
    assert "LOAN_003" in data["loan_ids"]

    # 2. POST /api/loans/LOAN_001/run
    run_res = client.post("/api/loans/LOAN_001/run")
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["status"] == "completed"
    assert "scorecard" in run_data

    # 3. GET /api/loans/LOAN_001/status
    status_res = client.get("/api/loans/LOAN_001/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["current_node"] == "done"

    # 4. GET /api/loans/LOAN_001/results
    results_res = client.get("/api/loans/LOAN_001/results")
    assert results_res.status_code == 200
    results_data = results_res.json()
    assert "comparison_results" in results_data
    assert "subnode_rollups" in results_data

    # 5. GET /api/loans/LOAN_001/scorecard
    scorecard_res = client.get("/api/loans/LOAN_001/scorecard")
    assert scorecard_res.status_code == 200
    sc_data = scorecard_res.json()
    assert sc_data["scoring_status"] == "NOT_IMPLEMENTED"
