"""Comprehensive unit & integration tests for the refactored 10-node disbursement verification pipeline."""
import copy
import json
from pathlib import Path
import pytest

from pipeline.nodes.check_financial import check_financial
from pipeline.nodes.check_kyc import check_kyc
from pipeline.nodes.check_loan_app import check_loan_app as check_loan_application
from pipeline.nodes.compile_report import compile_report
from pipeline.nodes.fetch_documents import fetch_documents as fetch_dms
from pipeline.nodes.fetch_los import fetch_los
from pipeline.nodes.generate_scorecard import generate_scorecard
from pipeline.nodes.idp_scan import idp_scan
from pipeline.nodes.llm_structure import llm_structure
from pipeline.nodes.push_results import push_results
from pipeline.graph import run_pipeline, stream_pipeline
from pipeline.state import PipelineState, compute_rollup
from pipeline.storage import (
    get_all_s3_extracted_structured,
    get_s3_extracted,
    get_s3_extracted_structured,
    get_s3_los,
    get_s3_result,
    list_loan_ids,
)


def test_fetch_los_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test fetch_los stages LOS file into s3_los tier."""
    loan_id = "LOAN_TEST_FETCH"
    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_record = {
        "loan_id": loan_id,
        "applicant_name": "Anita Verma",
        "loan_amount": 750000.0,
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_record))

    s3_los_dir = tmp_path / "s3_los"
    s3_result_dir = tmp_path / "s3_result"
    monkeypatch.setattr("pipeline.nodes.fetch_los.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("pipeline.storage.S3_LOS_DIR", s3_los_dir)
    monkeypatch.setattr("pipeline.storage.S3_RESULT_DIR", s3_result_dir)

    init_state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {},
        "extracted_structured_data": {},
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

    out_state = fetch_los(init_state)
    assert out_state["los_data"]["applicant_name"] == "Anita Verma"
    assert "fetch_los" in out_state["node_history"]
    assert (s3_los_dir / f"{loan_id}.json").exists()


def test_fetch_dms_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test fetch_dms copies files into s3_raw tier."""
    loan_id = "LOAN_TEST_DMS"
    dms_loan_dir = tmp_path / "dms" / loan_id
    dms_loan_dir.mkdir(parents=True, exist_ok=True)
    (dms_loan_dir / "aadhaar.pdf").write_bytes(b"%PDF-1.4 test aadhaar")
    (dms_loan_dir / "pan.pdf").write_bytes(b"%PDF-1.4 test pan")

    s3_raw_dir = tmp_path / "s3_raw"
    s3_result_dir = tmp_path / "s3_result"
    monkeypatch.setattr("pipeline.nodes.fetch_documents.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("pipeline.nodes.fetch_documents.S3_RAW_DIR", s3_raw_dir)
    monkeypatch.setattr("pipeline.storage.S3_RESULT_DIR", s3_result_dir)

    init_state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {},
        "extracted_structured_data": {},
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

    out_state = fetch_dms(init_state)
    assert len(out_state["raw_doc_paths"]) == 2
    assert (s3_raw_dir / loan_id / "aadhaar.pdf").exists()
    assert (s3_raw_dir / loan_id / "pan.pdf").exists()


def test_llm_structure_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test llm_structure writes canonical structured fields into s3_extracted_structured tier."""
    loan_id = "LOAN_TEST_STRUCT"
    s3_struct_dir = tmp_path / "s3_extracted_structured"
    s3_result_dir = tmp_path / "s3_result"
    monkeypatch.setattr("pipeline.storage.S3_EXTRACTED_STRUCTURED_DIR", s3_struct_dir)
    monkeypatch.setattr("pipeline.storage.S3_RESULT_DIR", s3_result_dir)
    monkeypatch.setattr("pipeline.nodes.llm_structure.SKIP_IDP", False)

    init_state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {
            "aadhaar": {
                "applicant_name": "Anita Verma",
                "aadhaar_number": "112233445566",
                "dob": "1988-12-04",
                "address": "456 Park Avenue, Pune",
                "_raw_text": "Name: Anita Verma, UID: 112233445566",
            }
        },
        "extracted_structured_data": {},
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

    out_state = llm_structure(init_state)
    assert "aadhaar" in out_state["extracted_structured_data"]
    assert out_state["extracted_structured_data"]["aadhaar"]["applicant_name"] == "Anita Verma"
    assert (s3_struct_dir / loan_id / "aadhaar.json").exists()


def test_check_kyc_node(mock_state_001: PipelineState):
    """Test check_kyc node executes identity checks cleanly."""
    res = check_kyc(mock_state_001)
    assert res["rollup"] == "Verified"
    assert len(res["records"]) >= 9
    assert all(r["match_status"] == "MATCH" for r in res["records"])


def test_check_financial_node(mock_state_001: PipelineState):
    """Test check_financial node executes loan terms and BPI checks."""
    res = check_financial(mock_state_001)
    assert res["rollup"] == "Verified"
    assert len(res["records"]) >= 10
    assert any(r["field"] == "bpi_charge" and r["match_status"] == "MATCH" for r in res["records"])


def test_check_loan_application_node(mock_state_001: PipelineState):
    """Test check_loan_application node executes lifecycle dates and ids."""
    state = copy.deepcopy(mock_state_001)
    state["extracted_data"]["kfs"]["application_no"] = "LOAN_001"
    state["extracted_data"]["sanction_letter"]["application_no"] = "LOAN_001"
    res = check_loan_application(state)
    assert res["rollup"] == "Verified"
    assert not any(r["field"] == "application_date" for r in res["records"])
    assert any(r["field"] == "application_no" and r["match_status"] == "MATCH" for r in res["records"])


def test_compile_report_and_generate_scorecard(mock_state_001: PipelineState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test compile_report aggregates records and generate_scorecard computes 12 checkpoints."""
    s3_result_dir = tmp_path / "s3_result"
    monkeypatch.setattr("pipeline.storage.S3_RESULT_DIR", s3_result_dir)

    # Run checkers
    kyc_res = check_kyc(mock_state_001)
    fin_res = check_financial(mock_state_001)
    app_res = check_loan_application(mock_state_001)

    combined_records = kyc_res["records"] + fin_res["records"] + app_res["records"]
    state_with_records: PipelineState = {
        **mock_state_001,
        "comparison_results": combined_records,
        "subnode_rollups": {
            "check_kyc": kyc_res["rollup"],
            "check_financial": fin_res["rollup"],
            "check_loan_application": app_res["rollup"],
        },
    }

    # Compile report
    state_compiled = compile_report(state_with_records)
    assert state_compiled["compiled_report"]["summary"]["total_checks"] == len(combined_records)
    assert (s3_result_dir / mock_state_001["loan_id"] / "compiled_report.json").exists()

    # Generate scorecard
    state_scorecard = generate_scorecard(state_compiled)
    scorecard = state_scorecard["scorecard"]
    assert scorecard["overall_score"] > 90.0
    assert scorecard["preliminary_decision"] == "AUTO_APPROVE_ELIGIBLE"
    assert scorecard["risk_tier"] == "LOW_RISK"
    assert len(scorecard["checkpoints"]) == 12
    assert (s3_result_dir / mock_state_001["loan_id"] / "scorecard.json").exists()


def test_push_results_node(mock_state_001: PipelineState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test push_results writes to mock LOS received folder."""
    s3_result_dir = tmp_path / "s3_result"
    los_recv_dir = tmp_path / "los" / "scorecards_received"
    monkeypatch.setattr("pipeline.storage.S3_RESULT_DIR", s3_result_dir)
    monkeypatch.setattr("pipeline.nodes.push_results.LOS_RECEIVED_DIR", los_recv_dir)

    scorecard_sample = {"loan_id": mock_state_001["loan_id"], "overall_score": 98.0}
    state_to_push: PipelineState = {
        **mock_state_001,
        "scorecard": scorecard_sample,
    }

    state_pushed = push_results(state_to_push)
    assert "done" in state_pushed["node_history"]
    assert (los_recv_dir / f"{mock_state_001['loan_id']}_scorecard.json").exists()


def test_stream_pipeline_events(monkeypatch: pytest.MonkeyPatch):
    """Test stream_pipeline emits start, intermediate stages, and finish events."""
    monkeypatch.setattr(
        "pipeline.nodes.idp_scan._process_single_document",
        lambda *args, **kwargs: {"rawText": "Sample text for fast testing"},
    )

    events = list(stream_pipeline("LOAN_001"))
    assert len(events) >= 8
    assert events[0]["stage"] == "start"
    assert events[-1]["stage"] == "finish"
    stages = [e["stage"] for e in events]
    assert "fetch_los" in stages
    assert "fetch_documents" in stages or "fetch_dms" in stages
    assert "idp_scan" in stages
    assert "llm_structure" in stages
    assert "check_parallel" in stages
    assert "compile_report" in stages
    assert "generate_scorecard" in stages
    assert "push_results" in stages
