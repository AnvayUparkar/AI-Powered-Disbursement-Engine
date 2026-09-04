import copy

import pytest

from pipeline.nodes.node3c_dates_ids import node3c_dates_ids
from pipeline.state import PipelineState


def test_node3c_clean_match(mock_state_001: PipelineState):
    result = node3c_dates_ids(mock_state_001)
    assert result["rollup"] == "Verified"
    records = result["records"]
    assert len(records) >= 5

    # Check key checks are present and matched
    assert any(r["field"] == "application_date" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "application_no" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "login_date" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "disbursement_date" and r["match_status"] == "MATCH" for r in records)
    assert any(r["sources"][0] == "disbursal_memo" and r["field"] == "loan_no" and r["match_status"] == "MATCH" for r in records)


def test_node3c_application_date_mismatch(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["application_form"]["application_date"] = "2024-02-28"
    result = node3c_dates_ids(state)
    assert result["rollup"] == "Discrepancy"

    date_rec = next(r for r in result["records"] if r["field"] == "application_date")
    assert date_rec["match_status"] == "MISMATCH"
    assert date_rec["confidence"] == 0.0


def test_node3c_application_no_mismatch(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["application_form"]["application_no"] = "LOAN_WRONG_999"
    result = node3c_dates_ids(state)
    assert result["rollup"] == "Discrepancy"

    app_no_rec = next(r for r in result["records"] if r["field"] == "application_no")
    assert app_no_rec["match_status"] == "MISMATCH"
    assert app_no_rec["confidence"] == 0.0


def test_node3c_disbursal_memo_loan_no_mismatch(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["disbursal_memo"]["loan_no"] = "LOAN_MISMATCH_888"
    result = node3c_dates_ids(state)
    assert result["rollup"] == "Discrepancy"

    memo_rec = next(r for r in result["records"] if r["sources"][0] == "disbursal_memo" and r["field"] == "loan_no")
    assert memo_rec["match_status"] == "MISMATCH"


def test_node3c_missing_application_form_emits_not_found(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    del state["extracted_data"]["application_form"]
    result = node3c_dates_ids(state)
    assert result["rollup"] == "Indeterminate"

    app_recs = [r for r in result["records"] if r["sources"][0] == "application_form"]
    assert len(app_recs) == 4
    assert all(r["match_status"] == "NOT_FOUND" for r in app_recs)


def test_node3c_empty_state():
    empty_state: PipelineState = {
        "loan_id": "LOAN_EMPTY",
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
        "retry_count": 0,
        "checker_result": {},
        "errors": [],
        "node_history": [],
    }
    result = node3c_dates_ids(empty_state)
    assert result["rollup"] == "Indeterminate"
    assert all(r["match_status"] == "NOT_FOUND" for r in result["records"])
