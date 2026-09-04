from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.audit import append_audit_entry
from pipeline.nodes.comparison_utils import clean_numeric
from pipeline.nodes.node3a_identity import node3a_identity
from pipeline.nodes.node3b_financial import node3b_financial
from pipeline.nodes.node3c_dates_ids import node3c_dates_ids
from pipeline.state import PipelineState, compute_rollup
from pipeline.storage import read_json, write_json


def test_compute_rollup_empty_records():
    """Verify that an empty record set returns 'Indeterminate' instead of 'Verified'."""
    assert compute_rollup([]) == "Indeterminate"


def test_compute_rollup_states():
    # Discrepancy priority
    assert compute_rollup([
        {"match_status": "MATCH"},
        {"match_status": "MISMATCH"},
        {"match_status": "PARTIAL"},
    ]) == "Discrepancy"

    # Indeterminate when no mismatch
    assert compute_rollup([
        {"match_status": "MATCH"},
        {"match_status": "PARTIAL"},
    ]) == "Indeterminate"

    assert compute_rollup([
        {"match_status": "MATCH"},
        {"match_status": "NOT_FOUND"},
    ]) == "Indeterminate"

    # Verified when all match or captured
    assert compute_rollup([
        {"match_status": "MATCH"},
        {"match_status": "CAPTURED"},
    ]) == "Verified"


def test_numeric_cleaning_edge_cases():
    assert clean_numeric(None) is None
    assert clean_numeric("₹ 1,500,000.50") == 1500000.50
    assert clean_numeric(0) == 0.0
    assert clean_numeric("invalid") is None


def test_node3b_financial_empty_state():
    state: PipelineState = {
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
    res = node3b_financial(state)
    assert res["rollup"] == "Indeterminate"
    assert len(res["records"]) > 0


def test_concurrent_audit_logging(tmp_path: Path, monkeypatch):
    """Verify that multiple threads concurrently appending audit logs do not lose entries."""
    monkeypatch.setattr("pipeline.audit.S3_RESULT_DIR", tmp_path)
    loan_id = "LOAN_CONCURRENT_TEST"
    num_entries = 50

    def log_entry(i: int):
        append_audit_entry(loan_id, {
            "type": "concurrent_test",
            "index": i,
            "data": f"thread_data_{i}",
        })

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(log_entry, range(num_entries)))

    audit_file = tmp_path / loan_id / "audit_log.json"
    assert audit_file.exists()
    entries = read_json(audit_file)
    assert len(entries) == num_entries
    indices = {e["index"] for e in entries}
    assert indices == set(range(num_entries))


def test_atomic_write_json(tmp_path: Path):
    target = tmp_path / "sub" / "data.json"
    data = {"key": "value", "numbers": [1, 2, 3]}
    write_json(target, data)
    assert target.exists()
    assert read_json(target) == data


def test_node3a_empty_state():
    state: PipelineState = {
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
    res = node3a_identity(state)
    assert res["rollup"] == "Indeterminate"
    assert len(res["records"]) > 0


def test_node3b_disbursal_memo_threshold_logic():
    state: PipelineState = {
        "loan_id": "LOAN_THRESHOLD_TEST",
        "los_data": {"loan_amount": 100000.0, "loan_id": "LOAN_THRESHOLD_TEST"},
        "raw_doc_paths": {},
        "extracted_data": {
            "disbursal_memo": {
                "loan_no": "LOAN_THRESHOLD_TEST",
                "loan_amount": 95000.0,
            }
        },
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
    res = node3b_financial(state)
    # Disbursal memo amount is 95k >= 90k threshold -> MATCH
    memo_chk = next(r for r in res["records"] if r["sources"][0] == "disbursal_memo" and r["field"] == "loan_amount")
    assert memo_chk["match_status"] == "MATCH"

