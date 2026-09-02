from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_cases_list():
    res = client.get("/api/cases")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert data["total"] >= 3
    case_ids = [c["id"] for c in data["items"]]
    assert "LOAN_001" in case_ids
    assert "LOAN_002" in case_ids
    assert "LOAN_003" in case_ids


def test_api_case_detail_loan_001():
    res = client.get("/api/cases/LOAN_001")
    assert res.status_code == 200
    case = res.json()
    assert case["id"] == "LOAN_001"
    assert case["status"] == "VERIFIED"
    assert len(case["checkpoints"]) == 12
    assert case["verifiedCount"] >= 10
    assert case["discrepancyCount"] == 0
    assert len(case["processingSteps"]) >= 5


def test_api_case_detail_loan_002_discrepancy():
    res = client.get("/api/cases/LOAN_002")
    assert res.status_code == 200
    case = res.json()
    assert case["id"] == "LOAN_002"
    assert case["status"] == "DISCREPANCY"
    assert case["riskLevel"] == "HIGH"
    assert case["discrepancyCount"] > 0


def test_api_case_run_and_status():
    run_res = client.post("/api/cases/LOAN_001/run")
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["status"] == "completed"
    assert "case" in run_data

    st_res = client.get("/api/cases/LOAN_001/status")
    assert st_res.status_code == 200
    st_data = st_res.json()
    assert st_data["current_node"] == "done"


def test_api_dashboard_and_reports():
    kpis_res = client.get("/api/dashboard/kpis")
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert "casesProcessedToday" in kpis
    assert "dgclValidation" in kpis

    rep_res = client.get("/api/reports/summary")
    assert rep_res.status_code == 200
    rep = rep_res.json()
    assert "checkpointPerformance" in rep
    assert len(rep["checkpointPerformance"]) == 12
