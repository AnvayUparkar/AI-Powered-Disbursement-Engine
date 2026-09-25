"""DGCL verification pipeline kill-switch: default-off, the /run endpoints honoring it, and the
Celery task re-checking it at execution time (not just at enqueue)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.pipeline_flags import is_dgcl_pipeline_enabled, set_dgcl_pipeline_enabled
from config.paths import PIPELINE_FLAGS_FILE

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_flag_file():
    """Every test starts from a clean (absent) flags file, isolated from other tests' writes."""
    if PIPELINE_FLAGS_FILE.exists():
        PIPELINE_FLAGS_FILE.unlink()
    yield
    if PIPELINE_FLAGS_FILE.exists():
        PIPELINE_FLAGS_FILE.unlink()


def test_default_is_disabled():
    assert is_dgcl_pipeline_enabled() is False


def test_toggle_persists():
    set_dgcl_pipeline_enabled(True)
    assert is_dgcl_pipeline_enabled() is True
    set_dgcl_pipeline_enabled(False)
    assert is_dgcl_pipeline_enabled() is False


def test_settings_endpoint_get_default():
    res = client.get("/api/settings/dgcl-pipeline")
    assert res.status_code == 200
    assert res.json() == {"enabled": False}


def test_settings_endpoint_round_trip():
    res = client.post("/api/settings/dgcl-pipeline", json={"enabled": True})
    assert res.status_code == 200
    assert res.json() == {"enabled": True}
    assert client.get("/api/settings/dgcl-pipeline").json() == {"enabled": True}


def test_case_run_blocked_when_disabled():
    res = client.post("/api/cases/LOAN_001/run")
    assert res.status_code == 403
    assert "disabled" in res.json()["detail"].lower()


def test_case_run_ocr_unaffected_by_flag():
    """OCR-only stays available regardless of the pipeline flag — it's the POC's core path."""
    res = client.post("/api/cases/LOAN_001/run-ocr")
    assert res.status_code != 403


def test_loan_run_blocked_when_disabled():
    res = client.post("/api/loans/LOAN_001/run")
    assert res.status_code == 403
    assert "disabled" in res.json()["detail"].lower()


def test_case_run_allowed_when_enabled(monkeypatch):
    set_dgcl_pipeline_enabled(True)
    monkeypatch.setattr("pipeline.graph.run_pipeline", lambda case_id: {"scorecard": {}, "errors": []})
    monkeypatch.setattr("app.routers.cases.run_pipeline", lambda case_id: {"scorecard": {}, "errors": []})
    res = client.post("/api/cases/LOAN_001/run")
    assert res.status_code == 200
    assert res.json()["status"] == "completed"


def test_celery_task_skips_when_flag_off_even_if_already_queued(monkeypatch):
    """Simulates a task enqueued while the flag was on, then executed after it was turned off."""
    from pipeline.celery_app import _run_pipeline_task

    set_dgcl_pipeline_enabled(True)
    set_dgcl_pipeline_enabled(False)  # flips off "after enqueue, before execution"

    called = {"run_pipeline": False}

    def _fake_run_pipeline(loan_id):
        called["run_pipeline"] = True
        return {"scorecard": {}, "errors": []}

    monkeypatch.setattr("pipeline.graph.run_pipeline", _fake_run_pipeline)

    class _FakeSelf:
        class request:
            id = "test-task-id"

    result = _run_pipeline_task(_FakeSelf(), "LOAN_001")
    assert result["status"] == "disabled"
    assert called["run_pipeline"] is False
