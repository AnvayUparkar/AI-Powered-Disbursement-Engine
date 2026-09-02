from pipeline.nodes.node3c_topup_bt import node3c_topup_bt
from pipeline.state import PipelineState


def test_node3c_clean_match(mock_state_001: PipelineState):
    result = node3c_topup_bt(mock_state_001)
    records = result["records"]
    assert any(r["field"] == "application_id" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "disbursal_amount" and r["match_status"] == "MATCH" for r in records)
    assert any(r["field"] == "bt_closure_vs_final_fc_amount" and r["match_status"] == "NOT_IMPLEMENTED" for r in records)


def test_node3c_disbursal_memo_amount_below_threshold(mock_state_001: PipelineState):
    state = dict(mock_state_001)
    # 500,000 loan -> 90% is 450,000. Set memo to 400,000 (< 90%)
    state["extracted_data"]["disbursal_memo"]["disbursal_amount"] = 400000.0
    result = node3c_topup_bt(state)
    amt_rec = next(r for r in result["records"] if r["field"] == "disbursal_amount")
    assert amt_rec["match_status"] == "MISMATCH"
    assert result["rollup"] == "Discrepancy"


def test_node3c_disbursal_memo_application_id_mismatch(mock_state_001: PipelineState):
    state = dict(mock_state_001)
    state["extracted_data"]["disbursal_memo"]["application_id"] = "APP_WRONG_999"
    result = node3c_topup_bt(state)
    app_id_rec = next(r for r in result["records"] if r["field"] == "application_id")
    assert app_id_rec["match_status"] == "MISMATCH"
    assert result["rollup"] == "Discrepancy"


def test_node3c_bt_fc_stub_not_implemented(mock_state_001: PipelineState):
    result = node3c_topup_bt(mock_state_001)
    bt_rec = next(r for r in result["records"] if r["field"] == "bt_closure_vs_final_fc_amount")
    assert bt_rec["match_status"] == "NOT_IMPLEMENTED"
    assert "pending" in (bt_rec["notes"] or "").lower()
