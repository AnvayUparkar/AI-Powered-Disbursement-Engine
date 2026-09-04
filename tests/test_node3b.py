import copy

import pytest

from pipeline.nodes.node3b_financial import node3b_financial
from pipeline.state import PipelineState


def test_node3b_clean_match(mock_state_001: PipelineState):
    result = node3b_financial(mock_state_001)
    assert result["rollup"] == "Verified"
    assert len(result["records"]) > 10

    # Verify key checks exist and match
    assert any(r["field"] == "loan_amount" and r["sources"][0] == "kfs" and r["match_status"] == "MATCH" for r in result["records"])
    assert any(r["field"] == "customer_consent" and r["match_status"] == "MATCH" for r in result["records"])
    assert any(r["field"] == "bpi_charge" and r["match_status"] == "MATCH" for r in result["records"])


def test_node3b_loan_amount_threshold_success(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    # 475,000 / 500,000 = 0.95 (>= 90% threshold -> MATCH)
    state["extracted_data"]["kfs"]["loan_amount"] = 475000.0
    result = node3b_financial(state)
    kfs_amt_rec = next(r for r in result["records"] if r["sources"][0] == "kfs" and r["field"] == "loan_amount")
    assert kfs_amt_rec["match_status"] == "MATCH"
    assert kfs_amt_rec["confidence"] == 0.95


def test_node3b_loan_amount_threshold_failure(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    # 400,000 / 500,000 = 0.80 (< 90% threshold -> MISMATCH)
    state["extracted_data"]["kfs"]["loan_amount"] = 400000.0
    result = node3b_financial(state)
    assert result["rollup"] == "Discrepancy"

    kfs_amt_rec = next(r for r in result["records"] if r["sources"][0] == "kfs" and r["field"] == "loan_amount")
    assert kfs_amt_rec["match_status"] == "MISMATCH"
    assert kfs_amt_rec["confidence"] == 0.8


def test_node3b_customer_consent_false(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["kfs"]["customer_consent"] = False
    result = node3b_financial(state)
    assert result["rollup"] == "Discrepancy"

    consent_rec = next(r for r in result["records"] if r["field"] == "customer_consent")
    assert consent_rec["match_status"] == "MISMATCH"
    assert consent_rec["confidence"] == 0.0


def test_node3b_bpi_doc_to_doc_mismatch(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    # KFS has 1500, Disbursal memo has 3000 (diff exceeds 10% tolerance)
    state["extracted_data"]["kfs"]["bpi_charge"] = 1500.0
    state["extracted_data"]["disbursal_memo"]["bpi_charge"] = 3000.0
    result = node3b_financial(state)
    assert result["rollup"] == "Discrepancy"

    bpi_rec = next(r for r in result["records"] if r["field"] == "bpi_charge")
    assert bpi_rec["match_status"] == "MISMATCH"


def test_node3b_bpi_optional_absence_no_failure(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    # Neither doc has BPI
    del state["extracted_data"]["kfs"]["bpi_charge"]
    del state["extracted_data"]["kfs"]["broken_period_interest"]
    del state["extracted_data"]["disbursal_memo"]["bpi_charge"]
    result = node3b_financial(state)
    assert result["rollup"] == "Verified"
    assert not any(r["field"] == "bpi_charge" for r in result["records"])
