from pipeline.nodes.node3a_loan_kyc import node3a_loan_kyc
from pipeline.state import PipelineState


def test_node3a_clean_match(mock_state_001: PipelineState):
    result = node3a_loan_kyc(mock_state_001)
    assert result["rollup"] == "Verified"
    assert len(result["records"]) >= 5
    for r in result["records"]:
        assert r["match_status"] == "MATCH"


def test_node3a_loan_amount_mismatch(mock_state_001: PipelineState):
    state = dict(mock_state_001)
    # Modify application form loan amount by 1 unit
    state["extracted_data"]["application_form"]["loan_amount"] = "500001"
    result = node3a_loan_kyc(state)
    assert result["rollup"] == "Discrepancy"

    mismatches = [r for r in result["records"] if r["match_status"] == "MISMATCH"]
    assert len(mismatches) > 0
    assert any("loan_amount" in r["field"] for r in mismatches)


def test_node3a_pan_mismatch_and_missing(mock_state_001: PipelineState):
    # Test mismatch
    state_mismatch = dict(mock_state_001)
    state_mismatch["extracted_data"]["kyc_pan"]["pan_number"] = "ZZZZZ9999Z"
    res1 = node3a_loan_kyc(state_mismatch)
    assert res1["rollup"] == "Discrepancy"

    # Test missing PAN
    state_missing = dict(mock_state_001)
    state_missing["extracted_data"]["kyc_pan"]["pan_number"] = None
    res2 = node3a_loan_kyc(state_missing)
    assert res2["rollup"] == "Indeterminate"
    pan_record = next(r for r in res2["records"] if r["field"] == "pan_number")
    assert pan_record["match_status"] == "NOT_FOUND"


def test_node3a_partial_name_triggers_llm_adjudication(mock_state_001: PipelineState, monkeypatch):
    import pipeline.nodes.node3a_loan_kyc as node3a_mod

    # Verify unit integration when Gemini returns MATCH
    monkeypatch.setattr(
        node3a_mod,
        "llm_adjudicate",
        lambda a, b, f, lid: {
            "match_status": "MATCH",
            "confidence": 0.98,
            "reason": "'Mohd' is a standard abbreviation for 'Mohammad'.",
            "llm_used": True,
        },
    )

    state = dict(mock_state_001)
    state["extracted_data"]["application_form"]["applicant_name"] = "Mohd Rizwan"
    state["los_data"]["applicant_name"] = "Mohammad Rizwan"

    result = node3a_loan_kyc(state)
    name_record = next(r for r in result["records"] if r["field"] == "applicant_name")
    
    assert name_record["llm_used"] is True
    assert name_record["match_status"] == "MATCH"
    assert name_record["confidence"] == 0.98
    assert "Mohd" in (name_record["notes"] or "")
    assert result["rollup"] == "Verified"


def test_llm_adjudicate_fallback_when_no_key(monkeypatch):
    import pipeline.nodes.llm_adjudicator as stub_module
    from pipeline.nodes.llm_adjudicator import llm_adjudicate

    # Simulate missing API key
    monkeypatch.setattr(stub_module, "_get_gemini_client", lambda: None)
    res = llm_adjudicate("Mohd Rizwan", "Mohammad Rizwan", "applicant_name", "LOAN_TEST_FALLBACK")
    assert res["match_status"] == "PARTIAL"
    assert res["llm_used"] is False
    assert "Gemini API key not configured" in res["reason"]
