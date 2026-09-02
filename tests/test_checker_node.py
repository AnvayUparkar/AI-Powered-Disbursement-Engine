from pathlib import Path

from pipeline.config import (
    CHECKER_MIN_CONFIDENCE_THRESHOLD,
    CHECKER_REQUIRED_DOCUMENTS,
    CHECKER_REQUIRED_LOS_FIELDS,
    MAX_CHECKER_RETRIES,
)
from pipeline.graph import build_pipeline_graph, route_after_checker
from pipeline.nodes.node_checker import node_checker
from pipeline.state import PipelineState
from pipeline.storage import read_json


def _build_test_state(
    loan_id: str = "LOAN_TEST_CHECKER",
    los_data: dict | None = None,
    extracted_data: dict | None = None,
    comparison_results: list[dict] | None = None,
    retry_count: int = 0,
) -> PipelineState:
    if los_data is None:
        los_data = {
            "loan_id": loan_id,
            "applicant_name": "Ritesh Kumar",
            "loan_amount": 500000.0,
        }
    if extracted_data is None:
        extracted_data = {
            "application_form": {"loan_amount": 500000.0, "applicant_name": "Ritesh Kumar"},
            "pan_card": {"pan_number": "ABCDE1234F", "name": "Ritesh Kumar"},
            "loan_agreement": {"loan_amount": 500000.0, "borrower_name": "Ritesh Kumar"},
        }
    if comparison_results is None:
        comparison_results = [
            {"check_id": "chk_1", "match_status": "MATCH", "confidence": 1.0},
            {"check_id": "chk_2", "match_status": "MATCH", "confidence": 0.95},
            {"check_id": "chk_3", "match_status": "CAPTURED", "confidence": 1.0},
        ]

    return {
        "loan_id": loan_id,
        "los_data": los_data,
        "raw_doc_paths": {},
        "extracted_data": extracted_data,
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": comparison_results,
        "subnode_rollups": {"loan_kyc": "Verified"},
        "compiled_report": {"comparison_results": comparison_results},
        "scorecard": {},
        "retry_count": retry_count,
        "checker_result": {},
        "errors": [],
        "node_history": ["fetch", "extract", "comparison", "compile"],
    }


def test_node_checker_happy_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.nodes.node_checker.S3_RESULT_DIR", tmp_path)
    state = _build_test_state()
    res = node_checker(state)

    assert "checker" in res["node_history"]
    checker_result = res["checker_result"]
    assert checker_result["status"] == "PASSED"
    assert checker_result["will_retry"] is False
    assert checker_result["confidence_score"] >= CHECKER_MIN_CONFIDENCE_THRESHOLD
    assert checker_result["missing_los_fields"] == []
    assert checker_result["missing_documents"] == []
    assert res["retry_count"] == 0

    # Verify persisted artifact
    saved_file = tmp_path / "LOAN_TEST_CHECKER" / "checker_result.json"
    assert saved_file.exists()
    assert read_json(saved_file)["status"] == "PASSED"


def test_node_checker_missing_los_fields_triggers_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.nodes.node_checker.S3_RESULT_DIR", tmp_path)
    # Missing 'applicant_name' and 'loan_amount'
    incomplete_los = {"loan_id": "LOAN_INCOMPLETE"}
    state = _build_test_state(los_data=incomplete_los, retry_count=0)

    res = node_checker(state)
    checker_result = res["checker_result"]

    assert checker_result["status"] == "RETRYING"
    assert checker_result["will_retry"] is True
    assert "applicant_name" in checker_result["missing_los_fields"]
    assert "loan_amount" in checker_result["missing_los_fields"]
    assert res["retry_count"] == 1


def test_node_checker_missing_extracted_docs_triggers_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.nodes.node_checker.S3_RESULT_DIR", tmp_path)
    # Missing loan_agreement
    incomplete_docs = {
        "application_form": {"loan_amount": 500000.0},
        "pan_card": {"pan_number": "ABCDE1234F"},
    }
    state = _build_test_state(extracted_data=incomplete_docs, retry_count=1)

    res = node_checker(state)
    checker_result = res["checker_result"]

    assert checker_result["status"] == "RETRYING"
    assert checker_result["will_retry"] is True
    assert "loan_agreement" in checker_result["missing_documents"]
    assert res["retry_count"] == 2


def test_node_checker_max_retries_exhaustion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.nodes.node_checker.S3_RESULT_DIR", tmp_path)
    # Empty documents with retry_count already at MAX_CHECKER_RETRIES
    state = _build_test_state(
        extracted_data={},
        retry_count=MAX_CHECKER_RETRIES,
    )

    res = node_checker(state)
    checker_result = res["checker_result"]

    assert checker_result["status"] == "FAILED_MAX_RETRIES"
    assert checker_result["will_retry"] is False
    assert res["retry_count"] == MAX_CHECKER_RETRIES
    assert len(res["errors"]) > 0
    assert any("max retries" in err for err in res["errors"])


def test_route_after_checker():
    # Will retry state -> routes to "fetch"
    retry_state: PipelineState = {
        "loan_id": "TEST",
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {},
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "retry_count": 1,
        "checker_result": {"will_retry": True, "retry_attempt": 1, "max_retries": 2},
        "errors": [],
        "node_history": [],
    }
    assert route_after_checker(retry_state) == "fetch"

    # Completed/passed state -> routes to "scorecard"
    pass_state: PipelineState = {
        **retry_state,
        "checker_result": {"will_retry": False},
    }
    assert route_after_checker(pass_state) == "scorecard"


def test_graph_compilation_with_checker_node():
    app = build_pipeline_graph()
    assert app is not None
    # Verify graph includes checker node in nodes
    assert "checker" in app.get_graph().nodes
