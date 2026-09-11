"""Production test suite for modular case serializer, context, and checkpoint builders."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.serializers import (
    CaseContext,
    build_aadhaar_xml_checkpoint,
    build_all_checkpoints,
    build_application_form_checkpoint,
    build_bt_details_checkpoint,
    build_checkpoint,
    build_evidence,
    build_field,
    build_kfs_checkpoint,
    build_kyc_checkpoint,
    build_loan_agreement_checkpoint,
    build_loan_amount_checkpoint,
    build_loan_validity_checkpoint,
    build_sanction_letter_checkpoint,
    compute_checkpoint_confidence,
    get_case_results,
    inr_format,
    serialize_all_cases,
    serialize_case,
)


# ============================================================================
# 1. Primitives and Helper Tests
# ============================================================================


def test_inr_format_various_inputs():
    """Validates INR currency formatting for edge cases and regular amounts."""
    assert inr_format(None) == "₹0"
    assert inr_format(0.0) == "₹0"
    assert inr_format(500000.0) == "₹500,000"
    assert inr_format(1234567.89) == "₹1,234,567"
    assert inr_format("38,200") == "₹38,200"
    assert inr_format("₹ 1,500,000.50") == "₹1,500,000"
    assert inr_format("") == "₹0"


def test_safe_float_parsing():
    """Validates safe_float parsing of strings with commas, currency symbols, percentages."""
    from app.serializers.case_context import safe_float
    assert safe_float("38,200") == 38200.0
    assert safe_float("₹ 1,500,000.50") == 1500000.50
    assert safe_float("17.5%") == 17.5
    assert safe_float(None, default=0.0) == 0.0
    assert safe_float("invalid", default=0.0) == 0.0


def test_build_field_structure():
    """Validates build_field schema and precision."""
    f = build_field("Applicant Name", "Jane Doe", 98.245, "doc-1", page=2)
    assert f["id"] == "fld-applicant_name"
    assert f["name"] == "Applicant Name"
    assert f["value"] == "Jane Doe"
    assert f["confidence"] == 98.2
    assert f["sourceDocumentId"] == "doc-1"
    assert f["page"] == 2


def test_build_evidence_structure():
    """Validates build_evidence schema."""
    ev = build_evidence("doc-1", "PAN.pdf", "PAN Card", page=1, field="pan_number")
    assert ev["id"] == "ev-doc-1-pan_number"
    assert ev["documentId"] == "doc-1"
    assert ev["documentName"] == "PAN.pdf"
    assert ev["label"] == "PAN Card"
    assert ev["page"] == 1
    assert ev["field"] == "pan_number"


def test_build_checkpoint_structure():
    """Validates build_checkpoint schema and match score dual-key aliases."""
    cp = build_checkpoint(
        cp_id=1,
        name="Loan Amount",
        status="VERIFIED",
        confidence=98.54,
        reason="Amount matches",
        rule="Amounts must match",
        fields=[],
        evidence=[],
        validation={"left": "₹500,000", "right": "₹500,000", "result": "MATCH"},
        match_score=99.23,
    )
    assert cp["id"] == 1
    assert cp["status"] == "VERIFIED"
    assert cp["confidence"] == 98.5
    assert cp["matchScore"] == 99.2
    assert cp["match_score"] == 99.2
    assert cp["validation"]["result"] == "MATCH"


def test_compute_checkpoint_confidence_scaling_and_weights():
    """Validates dynamic weighted confidence from records and normalized 0.0-1.0 confidence values."""
    records = [
        {"field": "applicant_name", "confidence": 0.95},  # critical weight
        {"field": "pan_number", "confidence": 0.99},       # critical weight
    ]
    conf = compute_checkpoint_confidence(fields=[], records=records, default_conf=90.0)
    assert 95.0 <= conf <= 99.0

    # Fallback to field weights when records are empty
    fields = [
        {"name": "Applicant Name", "confidence": 92.0},
        {"name": "PAN Number", "confidence": 98.0},
    ]
    conf_fields = compute_checkpoint_confidence(fields=fields, records=None)
    assert 92.0 <= conf_fields <= 98.0

    # Default fallback when neither has confidence
    assert compute_checkpoint_confidence(fields=[], records=[]) == 95.0


# ============================================================================
# 2. Synthetic CaseContext Fixture
# ============================================================================


def make_test_context(tmp_path: Path, **kwargs: Any) -> CaseContext:
    """Helper to build a clean, typed CaseContext for isolated unit testing."""
    loan_id = kwargs.get("loan_id", "TEST_CASE_001")
    return CaseContext(
        loan_id=loan_id,
        los_data=kwargs.get("los_data", {
            "loan_id": loan_id,
            "applicant_name": "Rajesh Sharma",
            "funding_amount": 500000.0,
            "loan_amount": 500000.0,
            "loan_validity": 24,
            "balance_transfer": 0,
            "applicant_pan_number": "ABCDE1234F",
            "current_address": "Plot 42, Sector 15, Gurgaon",
            "applicant_mobile_no": "9876543210",
        }),
        docs=kwargs.get("docs", {}),
        real_doc_names=kwargs.get("real_doc_names", ["Application_Form.pdf", "Sanction_Letter.pdf", "KFS.pdf"]),
        doc_ids=kwargs.get("doc_ids", ["doc-1", "doc-2"]),
        records=kwargs.get("records", []),
        records_by_id=kwargs.get("records_by_id", {}),
        records_by_field=kwargs.get("records_by_field", {}),
        records_by_subnode=kwargs.get("records_by_subnode", {}),
        status_data=kwargs.get("status_data", {"status": "DONE", "node_history": ["done"]}),
        scorecard_data=kwargs.get("scorecard_data", {}),
        subnode_rollups=kwargs.get("subnode_rollups", {}),
        loan_amount=kwargs.get("loan_amount", 500000.0),
        disbursal_amount=kwargs.get("disbursal_amount", 450000.0),
        applicant_name=kwargs.get("applicant_name", "Rajesh Sharma"),
        app_id=kwargs.get("app_id", f"APP-{loan_id}"),
        loan_type=kwargs.get("loan_type", "Personal Loan"),
        is_bt=kwargs.get("is_bt", False),
        raw_dir=tmp_path / "raw" / loan_id,
        dms_dir=tmp_path / "dms" / loan_id,
        extracted_dir=tmp_path / "extracted" / loan_id,
        extracted_structured_dir=tmp_path / "extracted_structured" / loan_id,
        result_dir=tmp_path / "result" / loan_id,
    )


# ============================================================================
# 3. Unit Tests for Individual Checkpoint Builders
# ============================================================================


def test_build_loan_amount_checkpoint_unit(tmp_path: Path):
    """Unit test for Checkpoint 1 (Loan Amount)."""
    # 1. Matching case
    docs = {
        "application_form": {"loan_amount": 500000.0},
        "kfs": {"loan_amount": 500000.0},
        "sanction_letter": {"loan_amount": 500000.0},
    }
    ctx = make_test_context(tmp_path, docs=docs)
    cp1 = build_loan_amount_checkpoint(ctx)
    assert cp1["id"] == 1
    assert cp1["status"] == "VERIFIED"
    assert cp1["validation"]["result"] == "MATCH"

    # 2. Missing docs case
    ctx_empty = make_test_context(tmp_path, docs={}, loan_amount=0.0)
    cp1_empty = build_loan_amount_checkpoint(ctx_empty)
    assert cp1_empty["status"] == "INDETERMINATE"
    assert "Not Available" in cp1_empty["extractedFields"][0]["value"]


def test_build_loan_validity_checkpoint_unit(tmp_path: Path):
    """Unit test for Checkpoint 2 (Loan Validity)."""
    docs = {
        "application_form": {"loan_validity": 36},
        "sanction_letter": {"loan_validity": 36},
    }
    los_data = {
        "loan_id": "TEST_CASE_001",
        "loan_validity": 36,
    }
    ctx = make_test_context(tmp_path, los_data=los_data, docs=docs)
    cp2 = build_loan_validity_checkpoint(ctx)
    assert cp2["id"] == 2
    assert cp2["status"] == "VERIFIED"
    assert cp2["validation"]["left"] == "36"
    assert cp2["validation"]["right"] == "36"


def test_build_application_form_checkpoint_unit(tmp_path: Path):
    """Unit test for Checkpoint 3 (Application Form)."""
    # Perfect match
    app_form_doc = {
        "applicant_name": "Rajesh Sharma",
        "application_no": "TEST_CASE_001",
        "mobile_no": "9876543210",
        "pan_number": "ABCDE1234F",
        "current_address": "Plot 42, Sector 15, Gurgaon",
        "loan_amount": 500000.0,
        "loan_validity": 24,
    }
    ctx = make_test_context(tmp_path, docs={"application_form": app_form_doc})
    cp3 = build_application_form_checkpoint(ctx)
    assert cp3["id"] == 3
    assert cp3["status"] == "VERIFIED"
    assert cp3["matchScore"] == 100.0


def test_build_kyc_checkpoint_unit(tmp_path: Path):
    """Unit test for Checkpoint 4 (KYC)."""
    # Address proof missing -> INDETERMINATE with warning
    docs = {"kyc_pan": {"pan_number": "ABCDE1234F"}}
    ctx = make_test_context(tmp_path, docs=docs)
    cp4 = build_kyc_checkpoint(ctx)
    assert cp4["id"] == 4
    assert cp4["status"] == "INDETERMINATE"
    assert "mandatory Address Proof document is missing" in cp4["reason"]

    # Both PAN and Address Proof present -> VERIFIED
    docs["kyc_address_proof"] = {"address_text": "Plot 42, Sector 15, Gurgaon"}
    ctx_full = make_test_context(tmp_path, docs=docs)
    cp4_full = build_kyc_checkpoint(ctx_full)
    assert cp4_full["status"] == "VERIFIED"
    assert cp4_full["validation"]["left"] == "ABCDE1234F"
    assert cp4_full["validation"]["right"] == "ABCDE1234F"
    assert cp4_full["validation"]["result"] == "MATCH"


def test_build_kyc_checkpoint_account_statement_does_not_contaminate_kyc(tmp_path: Path):
    """Account statement entity mismatch must not cause KYC to become DISCREPANCY or emit PAN MISMATCH PAN."""
    docs = {
        "kyc_pan": {"pan_number": "AOOPK6924P", "name": "Prakash Khatri"},
        "aadhaar": {"address": "30/105, Sindhi Colony, Jaipur", "applicant_name": "Prakash Khatri"},
    }
    los_data = {
        "loan_id": "APPL00343265",
        "applicant_name": "PRAKASH KHATRI",
        "applicant_pan_number": "AOOPK6924P",
        "current_address": "30/105, Sindhi Colony, Jaipur",
    }
    # Include bank account statement mismatch record in comparison results
    records = [
        {
            "check_id": "chk_check_financial_account_statement_applicant_name_vs_los",
            "sources": ["account_statement", "los"],
            "values": ["Kamla Udyog", "PRAKASH KHATRI"],
            "match_status": "MISMATCH",
            "result": "MISMATCH",
        }
    ]
    ctx = make_test_context(tmp_path, los_data=los_data, docs=docs, records=records)
    cp4 = build_kyc_checkpoint(ctx)
    assert cp4["status"] == "VERIFIED"
    assert cp4["validation"]["left"] == "AOOPK6924P"
    assert cp4["validation"]["right"] == "AOOPK6924P"
    assert cp4["validation"]["result"] == "MATCH"
    assert "AOOPK6924P MISMATCH AOOPK6924P" not in f"{cp4['validation']['left']} {cp4['validation']['result']} {cp4['validation']['right']}"


def test_build_kyc_checkpoint_mismatch_updates_left_right(tmp_path: Path):
    """When a KYC mismatch occurs, left and right validation values reflect the mismatched field."""
    docs = {
        "kyc_pan": {"pan_number": "AOOPK6924P", "name": "Prakash Khatri"},
        "aadhaar": {"address": "Different Street", "applicant_name": "Prakash Khatri"},
    }
    los_data = {
        "loan_id": "APPL00343265",
        "applicant_name": "PRAKASH KHATRI",
        "applicant_pan_number": "AOOPK6924P",
        "current_address": "30/105, Sindhi Colony, Jaipur",
    }
    records = [
        {
            "check_id": "chk_check_kyc_aadhaar_address_vs_los",
            "field": "address",
            "sources": ["aadhaar", "los"],
            "values": ["Different Street", "30/105, Sindhi Colony, Jaipur"],
            "match_status": "MISMATCH",
            "result": "MISMATCH",
        }
    ]
    ctx = make_test_context(tmp_path, los_data=los_data, docs=docs, records=records)
    cp4 = build_kyc_checkpoint(ctx)
    assert cp4["status"] == "DISCREPANCY"
    assert cp4["validation"]["result"] == "MISMATCH"
    assert cp4["validation"]["left"] == "Different Street"
    assert cp4["validation"]["right"] == "30/105, Sindhi Colony, Jaipur"


def test_build_bt_details_checkpoint_unit(tmp_path: Path):
    """Unit test for Checkpoint 12 (BT Details)."""
    # Non-BT
    ctx_no_bt = make_test_context(tmp_path, is_bt=False)
    cp12_no = build_bt_details_checkpoint(ctx_no_bt)
    assert cp12_no["status"] == "NOT_APPLICABLE"
    assert cp12_no["confidence"] == 0.0

    # BT with document present
    ctx_bt_present = make_test_context(
        tmp_path, is_bt=True, docs={"bt_details": {"foreclosure_amount": 200000}}
    )
    cp12_bt = build_bt_details_checkpoint(ctx_bt_present)
    assert cp12_bt["status"] == "VERIFIED"
    assert cp12_bt["confidence"] == 95.0

    # BT missing document
    ctx_bt_missing = make_test_context(tmp_path, is_bt=True, docs={}, real_doc_names=[])
    cp12_missing = build_bt_details_checkpoint(ctx_bt_missing)
    assert cp12_missing["status"] == "INDETERMINATE"
    assert "missing" in cp12_missing["reason"].lower()


def test_build_kfs_checkpoint_irr_emi_consent_unit(tmp_path: Path):
    """Unit test for Checkpoint 7 (KFS) verifying IRR, EMI, and customer consent."""
    kfs_doc = {
        "loan_amount": 1000000.0,
        "loan_validity": 36,
        "irr_percent": 17.0,
        "emi": 35652.0,
        "customer_consent": True,
    }
    los_data = {
        "loan_id": "TEST_CASE_KFS",
        "funding_amount": 1000000.0,
        "irr_percent": 17.0,
        "emi": 35652.0,
    }
    ctx = make_test_context(tmp_path, los_data=los_data, docs={"kfs": kfs_doc})
    cp7 = build_kfs_checkpoint(ctx)
    assert cp7["id"] == 7
    assert cp7["status"] == "VERIFIED"
    assert cp7["confidence"] >= 95.0
    field_names = [f["name"] for f in cp7["extractedFields"]]
    assert "KFS IRR" in field_names
    assert "KFS EMI" in field_names
    assert "Customer Consent" in field_names

    consent_fld = next(f for f in cp7["extractedFields"] if f["name"] == "Customer Consent")
    assert "Verified (Consented)" in consent_fld["value"]


def test_build_sanction_checkpoint_irr_discrepancy_unit(tmp_path: Path):
    """Unit test for Checkpoint 8 (Sanction Letter) detecting direct LOS IRR mismatch."""
    sanction_doc = {
        "loan_amount": 1000000.0,
        "loan_validity": 36,
        "irr_percent": 23.0,
        "emi": 38200.0,
    }
    los_data = {
        "loan_id": "TEST_CASE_SANCTION",
        "funding_amount": 1000000.0,
        "irr_percent": 17.0,
        "emi": 35652.0,
    }
    ctx = make_test_context(tmp_path, los_data=los_data, docs={"sanction_letter": sanction_doc})
    cp8 = build_sanction_letter_checkpoint(ctx)
    assert cp8["id"] == 8
    assert cp8["status"] == "DISCREPANCY"
    assert "Sanction Letter IRR discrepancy: 23.0% vs LOS 17.0%" in cp8["reason"]
    assert cp8["validation"]["result"] == "MISMATCH"
    assert cp8["validation"]["left"] == "23.0%"
    assert cp8["validation"]["right"] == "17.0%"


def test_build_aadhaar_xml_presence_check_unit(tmp_path: Path):
    """Unit test for Checkpoint 9 (Aadhaar XML) validating strict presence hard-gate behavior."""
    # 1. XML present
    ctx_present = make_test_context(
        tmp_path,
        docs={"aadhaar_xml": {"aadhaar_xml_present": True, "applicant_name": "Prakash Khatri"}},
    )
    cp9_present = build_aadhaar_xml_checkpoint(ctx_present)
    assert cp9_present["id"] == 9
    assert cp9_present["status"] == "VERIFIED"
    assert cp9_present["validation"]["left"] == "Present"
    assert cp9_present["validation"]["right"] == "Mandatory"
    assert cp9_present["validation"]["result"] == "MATCH"
    field_names = [f["name"] for f in cp9_present["extractedFields"]]
    assert field_names == ["Aadhaar XML Presence"]
    assert "Aadhaar XML Name" not in field_names

    # 2. XML missing
    ctx_missing = make_test_context(tmp_path, docs={}, real_doc_names=[])
    cp9_missing = build_aadhaar_xml_checkpoint(ctx_missing)
    assert cp9_missing["status"] == "INDETERMINATE"
    assert cp9_missing["validation"]["left"] == "Missing"


def test_build_loan_agreement_checkpoint_no_synthetic_fields(tmp_path: Path):
    """Checkpoint 6 (Loan Agreement) must only surface real extracted fields without hardcoded Digital Signature or OTP Consent."""
    docs = {
        "loan_agreement": {
            "loan_agreement_present": True,
            "loan_agreement_signed": True,
            "customer_consent": False,
        }
    }
    ctx = make_test_context(tmp_path, docs=docs)
    cp6 = build_loan_agreement_checkpoint(ctx)
    assert cp6["id"] == 6
    assert cp6["status"] == "VERIFIED"
    field_names = [f["name"] for f in cp6["extractedFields"]]
    assert "Loan Agreement Presence" in field_names
    assert "Loan Agreement Signature" in field_names
    assert "Digital Signature" not in field_names
    assert "OTP Consent" not in field_names


def test_build_all_checkpoints_returns_twelve_items(tmp_path: Path):
    """Validates that build_all_checkpoints produces precisely 12 standard checkpoints."""
    ctx = make_test_context(tmp_path)
    checkpoints = build_all_checkpoints(ctx)
    assert len(checkpoints) == 12
    ids = [cp["id"] for cp in checkpoints]
    assert ids == list(range(1, 13))


# ============================================================================
# 4. Integration Tests with Storage and Monkeypatching
# ============================================================================


def test_get_case_results_corrupted_json_handled_gracefully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Validates that get_case_results handles malformed JSON files without crashing."""
    loan_id = "CORRUPT_LOAN_01"
    res_dir = tmp_path / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)

    # Write corrupted JSON
    (res_dir / "comparison_results.json").write_text("{ corrupt json ...")
    (res_dir / "status.json").write_text("invalid json")
    (res_dir / "scorecard.json").write_text("[}")

    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path)

    results = get_case_results(loan_id)
    assert results["comparison_results"] == []
    assert results["status_data"] == {}
    assert results["scorecard_data"] == {}


def test_serialize_case_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Comprehensive happy path integration test for serialize_case."""
    loan_id = "LOAN_PROD_HAPPY_01"

    los_dir = tmp_path / "los"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Sunita Verma",
        "loan_type": "Home Loan",
        "funding_amount": 1000000.0,
        "tenure": 120,
        "balance_transfer": 0,
        "applicant_pan_number": "ABCDE1234F",
        "current_address": "45 Park Avenue, Mumbai",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "application_form.json").write_text(json.dumps({"loan_amount": 1000000.0, "loan_validity": 120}))
    (ext_dir / "kfs.json").write_text(json.dumps({"loan_amount": 1000000.0, "loan_validity": 120}))
    (ext_dir / "sanction_letter.json").write_text(json.dumps({"loan_amount": 1000000.0, "loan_validity": 120}))
    (ext_dir / "kyc_pan.json").write_text(json.dumps({"pan_number": "ABCDE1234F"}))
    (ext_dir / "kyc_address_proof.json").write_text(json.dumps({"address_text": "45 Park Avenue, Mumbai"}))
    (ext_dir / "aadhaar_xml_status.json").write_text(json.dumps({"status": "verified"}))

    raw_dir = tmp_path / "raw" / loan_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "Application_Form.pdf").write_bytes(b"%PDF")
    (raw_dir / "Sanction_Letter.pdf").write_bytes(b"%PDF")
    (raw_dir / "KFS.pdf").write_bytes(b"%PDF")
    (raw_dir / "Selfie.jpg").write_bytes(b"JPG")
    (raw_dir / "Loan_Agreement.pdf").write_bytes(b"%PDF")

    struct_dir = tmp_path / "structured" / loan_id
    struct_dir.mkdir(parents=True, exist_ok=True)
    (struct_dir / f"{loan_id}_loan_agreement.json").write_text(
        json.dumps({"loan_agreement_present": True, "loan_agreement_signed": True})
    )
    (struct_dir / f"{loan_id}_aadhaar_xml.json").write_text(
        json.dumps({"aadhaar_xml_present": True, "applicant_name": "Sunita Verma"})
    )

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "result")

    case = serialize_case(loan_id)

    # Validate all 25 top-level contract fields
    expected_keys = [
        "id", "applicant", "applicationId", "loanType", "loanAmount",
        "disbursalAmount", "loginDate", "disbursalDate", "documentCount",
        "processingTime", "processingTimeSeconds", "dgclScore", "dgcl_score",
        "score", "verifiedCount", "discrepancyCount", "reviewCount", "status",
        "riskLevel", "lastUpdated", "balanceTransfer", "isBalanceTransfer",
        "checkpoints", "documentIds", "processingSteps", "comparisonResults"
    ]
    for k in expected_keys:
        assert k in case, f"Missing expected key: {k}"

    assert case["id"] == loan_id
    assert case["applicant"] == "Sunita Verma"
    assert case["loanAmount"] == 1000000.0
    assert len(case["checkpoints"]) == 12
    assert len(case["processingSteps"]) == 8
    assert isinstance(case["comparisonResults"], list)


def test_serialize_all_cases_iterates_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Validates that serialize_all_cases enumerates all loans found via list_loan_ids."""
    loan_ids = ["LOAN_ITER_01", "LOAN_ITER_02"]
    monkeypatch.setattr("app.serializers.case_serializer.list_loan_ids", lambda: loan_ids)
    monkeypatch.setattr("app.serializers.case_serializer.serialize_case", lambda lid: {"id": lid, "status": "VERIFIED"})

    cases = serialize_all_cases()
    assert len(cases) == 2
    assert cases[0]["id"] == "LOAN_ITER_01"
    assert cases[1]["id"] == "LOAN_ITER_02"


def test_dynamic_processing_time_computation():
    """Validates dynamic calculation of processing time from timestamps and workload."""
    from app.serializers.case_serializer import _compute_dynamic_processing_time

    # 1. With explicit started_at and completed_at (e.g. 105 seconds = 1m 45s)
    status_with_times = {
        "started_at": "2026-09-07T10:00:00+05:30",
        "completed_at": "2026-09-07T10:01:45+05:30",
    }
    time_str, time_sec = _compute_dynamic_processing_time(
        loan_id="TEST_01",
        status_data=status_with_times,
        doc_ids=["doc1", "doc2"],
        has_records=True,
    )
    assert time_sec == 105
    assert time_str == "1m 45s"

    # 2. Sub-minute duration (e.g. 42 seconds)
    status_subminute = {
        "started_at": "2026-09-07T10:00:00+05:30",
        "completed_at": "2026-09-07T10:00:42+05:30",
    }
    time_str_sub, time_sec_sub = _compute_dynamic_processing_time(
        loan_id="TEST_02",
        status_data=status_subminute,
        doc_ids=["doc1"],
        has_records=True,
    )
    assert time_sec_sub == 42
    assert time_str_sub == "42s"

    # 3. Fallback when started_at is missing: dynamic based on doc count & history
    status_fallback = {"node_history": ["fetch_los", "fetch_dms", "done"]}
    time_str_fb, time_sec_fb = _compute_dynamic_processing_time(
        loan_id="TEST_FALLBACK",
        status_data=status_fallback,
        doc_ids=["d1", "d2", "d3"],
        has_records=True,
    )
    assert time_sec_fb > 0
    assert time_str_fb != "2m 15s"  # Not hardcoded!

    # 4. Empty case
    time_str_empty, time_sec_empty = _compute_dynamic_processing_time(
        loan_id="TEST_EMPTY",
        status_data={},
        doc_ids=[],
        has_records=False,
    )
    assert time_sec_empty == 0
    assert time_str_empty == "—"


def test_date_and_time_formatting_helpers():
    """Validates 12-hour time and DD/MM/YYYY date formatting across various inputs."""
    import re
    from datetime import datetime, timezone
    from app.serializers.case_context import (
        format_date_dmy,
        format_datetime_dmy_12h,
        format_time_12h,
    )

    # 1. 12-hour time tests (e.g. 3:13 pm)
    time_regex = re.compile(r"^\d{1,2}:\d{2}\s(am|pm)$")
    dt_afternoon = datetime(2026, 9, 7, 15, 13, 0)
    assert format_time_12h(dt_afternoon) == "3:13 pm"
    assert time_regex.match(format_time_12h(dt_afternoon))

    dt_morning = datetime(2026, 9, 7, 9, 5, 0)
    assert format_time_12h(dt_morning) == "9:05 am"

    dt_midnight = datetime(2026, 9, 7, 0, 30, 0)
    assert format_time_12h(dt_midnight) == "12:30 am"

    dt_noon = datetime(2026, 9, 7, 12, 0, 0)
    assert format_time_12h(dt_noon) == "12:00 pm"

    assert format_time_12h("15:13:00") == "3:13 pm"
    assert format_time_12h("03:13 pm") == "03:13 pm"

    # 2. DD/MM/YYYY date tests
    date_regex = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    assert format_date_dmy("2026-09-07") == "07/09/2026"
    assert format_date_dmy("2026-08-12") == "12/08/2026"
    assert format_date_dmy(datetime(2026, 9, 7)) == "07/09/2026"
    assert format_date_dmy(None) is None

    # 3. Combined date and 12-hour time
    dt_combined = datetime(2026, 9, 7, 15, 13, 0)
    res_comb = format_datetime_dmy_12h(dt_combined)
    assert res_comb == "07/09/2026, 3:13 pm"


def test_serialize_case_surfaces_comparison_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Validates that comparisonResults is surfaced with full field-level audit metadata."""
    loan_id = "LOAN_COMP_SURF"
    los_dir = tmp_path / "los"
    res_dir = tmp_path / "result" / loan_id
    los_dir.mkdir(parents=True)
    res_dir.mkdir(parents=True)

    los_record = {
        "loan_id": loan_id,
        "applicant_name": "Test Applicant",
        "funding_amount": 500000.0,
        "loan_type": "Personal Loan",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_record))

    comp_records = [
        {
            "check_id": "chk_1",
            "subnode": "check_kyc",
            "field": "applicant_name",
            "sources": ["aadhaar", "los"],
            "values": ["Test Applicant", "Test Applicant"],
            "match_type": "exact_string",
            "match_status": "MATCH",
            "confidence": 1.0,
            "method": "case_insensitive_string_equality",
            "notes": None,
        }
    ]
    (res_dir / "comparison_results.json").write_text(json.dumps(comp_records))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "result")

    case = serialize_case(loan_id)
    assert "comparisonResults" in case
    assert len(case["comparisonResults"]) == 1
    assert case["comparisonResults"][0]["check_id"] == "chk_1"
    assert case["comparisonResults"][0]["field"] == "applicant_name"
    assert case["comparisonResults"][0]["method"] == "case_insensitive_string_equality"


def test_appl00343265_field_and_comparison_surfacing_invariants():
    """Validates that APPL00343265 checkpoints never surface A != A mismatches,
    binds to actual divergent values with source attribution, and attaches comparisons."""
    case = serialize_case("APPL00343265")
    checkpoints = {cp["name"]: cp for cp in case["checkpoints"]}

    # CP 1: Loan Amount must show the divergent Application Amount vs LOS / Sanction
    cp1 = checkpoints["Loan Amount"]
    assert cp1["status"] == "DISCREPANCY"
    assert cp1["validation"]["left"] == "₹10,000"
    assert cp1["validation"]["right"] == "₹1,000,000"
    assert cp1["validation"]["result"] == "MISMATCH"
    assert cp1["validation"]["leftSource"] == "application_form"
    assert cp1["validation"]["rightSource"] == "los"
    assert len(cp1["comparisons"]) > 0

    # CP 2: Loan Validity must show 3 Months vs 36 Months
    cp2 = checkpoints["Loan Validity"]
    assert cp2["status"] == "DISCREPANCY"
    assert cp2["validation"]["left"] == "3 Months"
    assert cp2["validation"]["right"] == "36 Months"
    assert cp2["validation"]["result"] == "MISMATCH"
    assert len(cp2["comparisons"]) > 0

    # CP 4: KYC must be MATCH for identical PANs (never AOOPK6924P MISMATCH AOOPK6924P)
    cp4 = checkpoints["KYC"]
    assert cp4["validation"]["left"] == "AOOPK6924P"
    assert cp4["validation"]["right"] == "AOOPK6924P"
    assert cp4["validation"]["result"] == "MATCH"
    assert "Mandatory KYC documents (PAN and Address Proof) not uploaded" not in cp4["reason"]
    assert len(cp4["comparisons"]) > 0

    # CP 7: KFS must show the IRR mismatch, not loan amount mismatch
    cp7 = checkpoints["KFS"]
    assert cp7["status"] == "DISCREPANCY"
    assert cp7["validation"]["left"] == "17.9%"
    assert cp7["validation"]["right"] == "17.0%"
    assert cp7["validation"]["result"] == "MISMATCH"
    assert len(cp7["comparisons"]) > 0

    # Missing documents must not show N/A != N/A
    for name in ("Selfie / Live Photo", "Disbursal Memo", "Loan Agreement", "Aadhaar XML"):
        cp = checkpoints[name]
        val = cp["validation"]
        assert not (val["left"] == "N/A" and val["right"] == "N/A" and val["result"] == "MISMATCH"), (
            f"{name} produced invalid N/A != N/A validation"
        )

    # Inviolable Invariant across all checkpoints in the case:
    for cp in case["checkpoints"]:
        val = cp.get("validation")
        if val and val.get("result") == "MISMATCH":
            assert val["left"] != val["right"], (
                f"Checkpoint '{cp['name']}' violated identity invariant: {val['left']} MISMATCH {val['right']}"
            )


