from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.audit import append_audit_entry
from pipeline.nodes.node3a_loan_kyc import (
    _clean_numeric,
    _normalize_tenure_months,
    node3a_loan_kyc,
)
from pipeline.nodes.node3b_kfs_sanction import (
    _verify_pdf_signature_pyhanko,
)
from pipeline.nodes.node3c_topup_bt import node3c_topup_bt
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


def test_numeric_and_tenure_cleaning_edge_cases():
    assert _clean_numeric(None) is None
    assert _clean_numeric("₹ 1,500,000.50") == 1500000.50
    assert _clean_numeric(0) == 0.0
    assert _clean_numeric("invalid") is None

    assert _normalize_tenure_months(None) is None
    assert _normalize_tenure_months("3 years") == 36
    assert _normalize_tenure_months("24 months") == 24
    assert _normalize_tenure_months(12) == 12
    assert _normalize_tenure_months("invalid") is None


def test_fail_closed_signature_verification_missing_pdf(tmp_path: Path):
    non_existent = tmp_path / "non_existent.pdf"
    res = _verify_pdf_signature_pyhanko(non_existent, "LOAN_TEST_FAIL")
    assert res["match_status"] == "NOT_FOUND"


def test_fail_closed_signature_verification_no_sigs_missing_otp(tmp_path: Path, monkeypatch):
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4\n%dummy pdf content")
    monkeypatch.setattr("pipeline.nodes.node3b_kfs_sanction.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("pipeline.nodes.node3b_kfs_sanction.DMS_DIR", tmp_path / "dms")

    res = _verify_pdf_signature_pyhanko(dummy_pdf, "LOAN_NO_SIGS")
    # Must fail closed with MISMATCH because no signatures exist and no OTP audit file exists
    assert res["match_status"] == "MISMATCH"


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
        "errors": [],
        "node_history": [],
    }
    res = node3a_loan_kyc(state)
    assert res["rollup"] == "Indeterminate"
    assert len(res["records"]) > 0


def test_node3c_zero_and_threshold_logic():
    state: PipelineState = {
        "loan_id": "LOAN_ZERO_TEST",
        "los_data": {"funding_amount": 100000.0, "application_id": "APP_123"},
        "raw_doc_paths": {},
        "extracted_data": {
            "disbursal_memo": {
                "application_id": "APP_123",
                "disbursal_amount": 95000.0,
            }
        },
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "errors": [],
        "node_history": [],
    }
    res = node3c_topup_bt(state)
    # Disbursal memo amount is 95k >= 90k threshold -> MATCH
    memo_chk = next(r for r in res["records"] if r["check_id"] == "chk_disbursal_memo_amount_threshold")
    assert memo_chk["match_status"] == "MATCH"
