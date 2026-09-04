import copy

import pytest

from pipeline.nodes.node3a_identity import node3a_identity
from pipeline.state import PipelineState


def test_node3a_clean_match(mock_state_001: PipelineState):
    result = node3a_identity(mock_state_001)
    assert result["rollup"] == "Verified"
    assert len(result["records"]) > 10
    for r in result["records"]:
        assert r["match_status"] == "MATCH"
        assert r["confidence"] is not None


def test_node3a_pan_mismatch(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["pan"]["pan_number"] = "ZZZZZ9999Z"
    result = node3a_identity(state)
    assert result["rollup"] == "Discrepancy"

    mismatches = [r for r in result["records"] if r["match_status"] == "MISMATCH"]
    assert len(mismatches) > 0
    assert any(r["field"] == "pan_number" for r in mismatches)


def test_node3a_missing_aadhaar_document(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    # Remove aadhaar and any alias keys
    state["extracted_data"].pop("aadhaar", None)
    state["extracted_data"].pop("kyc_address_proof", None)
    result = node3a_identity(state)
    assert result["rollup"] == "Indeterminate"

    not_found = [r for r in result["records"] if r["match_status"] == "NOT_FOUND"]
    assert len(not_found) >= 5
    assert all(r["sources"][0] == "aadhaar" for r in not_found)


def test_node3a_missing_field_in_document(mock_state_001: PipelineState):
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["pan"]["fathers_name"] = None
    result = node3a_identity(state)
    assert result["rollup"] == "Indeterminate"

    fn_rec = next(r for r in result["records"] if r["sources"][0] == "pan" and r["field"] == "fathers_name")
    assert fn_rec["match_status"] == "NOT_FOUND"
    assert fn_rec["confidence"] == 0.0


def test_node3a_partial_name_triggers_llm_adjudication(mock_state_001: PipelineState, monkeypatch):
    import pipeline.nodes.comparison_utils as comp_mod

    monkeypatch.setattr(
        comp_mod,
        "llm_adjudicate",
        lambda a, b, f, lid: {
            "match_status": "MATCH",
            "confidence": 0.98,
            "reason": "'Mohd' is a standard abbreviation for 'Mohammad'.",
            "llm_used": True,
        },
    )

    state = copy.deepcopy(mock_state_001)
    # "Mohd Rizwan" vs "Mohammad Rizwan" yields Jaro-Winkler ~0.87 (PARTIAL)
    state["extracted_data"]["application_form"]["applicant_name"] = "Mohd Rizwan"
    state["los_data"]["applicant_name"] = "Mohammad Rizwan"

    result = node3a_identity(state)
    app_name_rec = next(
        r for r in result["records"]
        if r["sources"][0] == "application_form" and r["field"] == "applicant_name"
    )

    assert app_name_rec["llm_used"] is True
    assert app_name_rec["match_status"] == "MATCH"
    assert app_name_rec["confidence"] == 0.98
    assert "Mohd" in (app_name_rec["notes"] or "")
