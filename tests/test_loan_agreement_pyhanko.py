"""Tests for pyHanko Digital Signature Verification on Loan Agreements.

Validates:
1. Detection logic: pyHanko is triggered exclusively on documents identified as Loan Agreements
   (by name or canonical name) and skipped for all other document types.
2. pyHanko Inspector Engine: cryptographic signature validation, certificate metadata extraction,
   trust roots loading (Indian CCA + certifi), and error handling on corrupted/unsigned files.
3. Pipeline integration: idp_scan and check_loan_app node behavior, record generation,
   and Checkpoint 6 scorecard status serialization.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.serializers.case_serializer import serialize_case
from config import BASE_DIR, TRUSTED_ROOTS_DIR, get_canonical_doc_type
from idp.services.extraction.pyhanko_inspector import (
    build_validation_context,
    inspect_pdf_signatures,
    is_loan_agreement,
    load_all_trust_roots,
    reset_trust_roots_cache,
)
from pipeline.nodes.check_loan_app import check_loan_app
from pipeline.state import PipelineState

FIXTURES_DIR = BASE_DIR / "tests" / "fixtures" / "pyhanko"
SIGNED_PDF = FIXTURES_DIR / "sample_signed.pdf"
UNSIGNED_PDF = FIXTURES_DIR / "sample_unsigned.pdf"


# ============================================================================
# 1. Detection Invariants: is_loan_agreement
# ============================================================================

@pytest.mark.parametrize(
    "name_or_path",
    [
        "loan_agreement",
        "loan_agreement.pdf",
        "Loan_Agreement.pdf",
        "LOAN_AGREEMENT.PDF",
        "agreement",
        "agreement.pdf",
        "Borrower_Agreement.pdf",
        "appl00343265_loan_agreement.pdf",
        "appl00343265_agreement.pdf",
        "loan_001_loan_agreement.pdf",
        "Customer_Loan_Agreement_Signed.pdf",
        "/var/dms/APPL123/Loan_Agreement.pdf",
        "C:\\dms\\APPL123\\loan_agreement.pdf",
    ],
)
def test_is_loan_agreement_positive_matches(name_or_path: str):
    """Verifies that all name and path variations representing Loan Agreements evaluate to True."""
    assert is_loan_agreement(name_or_path) is True


@pytest.mark.parametrize(
    "non_agreement_name",
    [
        "aadhaar.pdf",
        "aadhaar_card.png",
        "pan.pdf",
        "pan_card.jpg",
        "kfs.pdf",
        "key_fact_statement.pdf",
        "sanction_letter.pdf",
        "sanction.pdf",
        "bank_statement.pdf",
        "account_statement.pdf",
        "disbursal_memo.pdf",
        "application_form.pdf",
        "app_form.pdf",
        "vkyc_audit.pdf",
        "bt_details.pdf",
        "foreclosure_letter.pdf",
    ],
)
def test_is_loan_agreement_negative_matches(non_agreement_name: str):
    """Verifies that non-agreement documents evaluate to False so pyHanko is never triggered on them."""
    assert is_loan_agreement(non_agreement_name) is False


@pytest.mark.parametrize("empty_val", ["", "   ", None])
def test_is_loan_agreement_empty_and_none(empty_val: Any):
    """Verifies edge case handling for empty or None inputs."""
    assert is_loan_agreement(empty_val) is False


# ============================================================================
# 2. pyHanko Inspector Engine Unit Tests
# ============================================================================

def test_inspect_pdf_signatures_on_signed_pdf():
    """Happy Path: Validates cryptographic integrity and metadata extraction on a digitally signed PDF."""
    assert SIGNED_PDF.exists(), f"Signed PDF fixture missing at {SIGNED_PDF}"

    result = inspect_pdf_signatures(SIGNED_PDF, filename="sample_signed.pdf")

    assert result["is_signed"] is True
    assert result["signature_count"] >= 1
    assert result["is_acceptable"] is True
    assert result["error"] is None
    assert len(result["signatures"]) == result["signature_count"]

    sig0 = result["signatures"][0]
    assert sig0["intact"] is True
    assert sig0["valid"] is True
    assert sig0["field_name"] is not None

    signer = sig0["signer"]
    assert signer["common_name"] != "Unknown"
    assert signer["issuer_dn"] != "Unknown"
    assert signer["valid_from"] is not None
    assert signer["valid_until"] is not None


def test_inspect_pdf_signatures_on_unsigned_pdf():
    """Validates that unsigned PDFs cleanly report is_signed=False without signatures."""
    assert UNSIGNED_PDF.exists(), f"Unsigned PDF fixture missing at {UNSIGNED_PDF}"

    result = inspect_pdf_signatures(UNSIGNED_PDF, filename="sample_unsigned.pdf")

    assert result["is_signed"] is False
    assert result["signature_count"] == 0
    assert result["is_acceptable"] is False
    assert result["signatures"] == []
    assert result["error"] is None
    assert "No digital signatures found" in result["status_message"]


def test_inspect_pdf_signatures_on_corrupted_input():
    """Failure Mode: Validates that invalid non-PDF bytes return a clean error without crashing."""
    corrupted_bytes = b"NOT_A_VALID_PDF_HEADER_DATA_12345"
    result = inspect_pdf_signatures(corrupted_bytes, filename="corrupted.pdf")

    assert result["is_signed"] is False
    assert result["is_acceptable"] is False
    assert result["error"] is not None
    assert "Failed to parse PDF" in result["error"] or "Error scanning" in result["error"]


def test_inspect_pdf_signatures_on_nonexistent_file(tmp_path: Path):
    """Boundary Condition: Validates graceful handling of missing file paths."""
    missing_file = tmp_path / "does_not_exist.pdf"
    result = inspect_pdf_signatures(missing_file, filename="missing.pdf")

    assert result["is_signed"] is False
    assert result["is_acceptable"] is False
    assert result["error"] is not None
    assert "File not found" in result["error"]


def test_trusted_roots_loading_and_validation_context():
    """Validates that Indian CCA roots in config/trusted_roots are loaded into ValidationContext."""
    reset_trust_roots_cache()
    roots = load_all_trust_roots()

    assert len(roots) >= 3, "Expected at least 3 trust roots (CCA India 2014, 2022, XtraTrust Sub-CA)"

    ctx = build_validation_context()
    assert ctx is not None
    assert ctx.certificate_registry is not None


def test_strict_trust_requirement_policy(monkeypatch: pytest.MonkeyPatch):
    """Validates that REQUIRE_TRUSTED_DIGITAL_SIGNATURE config strictly enforces CA root trust."""
    # When False (default), intact and valid self-signed signature is accepted
    monkeypatch.setattr("idp.services.extraction.pyhanko_inspector.REQUIRE_TRUSTED_DIGITAL_SIGNATURE", False)
    res_lenient = inspect_pdf_signatures(SIGNED_PDF, filename="sample_signed.pdf")
    assert res_lenient["is_acceptable"] is True

    # When True, if root is not in trusted CAs, is_acceptable becomes False
    monkeypatch.setattr("idp.services.extraction.pyhanko_inspector.REQUIRE_TRUSTED_DIGITAL_SIGNATURE", True)
    res_strict = inspect_pdf_signatures(SIGNED_PDF, filename="sample_signed.pdf")
    # If the sample is signed with an untrusted test certificate, is_acceptable must be False
    sig0 = res_strict["signatures"][0]
    if not sig0["trusted"]:
        assert res_strict["is_acceptable"] is False


# ============================================================================
# 3. Pipeline Checker & Scorecard Integration Tests
# ============================================================================

def test_check_loan_app_with_signed_loan_agreement():
    """Pipeline Checker Integration: Verifies that a signed Loan Agreement emits a MATCH record."""
    loan_id = "LOAN_SIG_TEST_01"
    inspection_report = inspect_pdf_signatures(SIGNED_PDF, filename="Loan_Agreement.pdf")

    state: PipelineState = {
        "loan_id": loan_id,
        "extracted_data": {
            "loan_agreement": {
                "pyhanko_inspection": inspection_report,
                "loan_agreement_present": True,
                "loan_agreement_signed": True,
            }
        },
        "los_data": {"loan_id": loan_id},
        "raw_doc_paths": {"Loan_Agreement.pdf": str(SIGNED_PDF)},
    }

    result = check_loan_app(state)
    records = result["records"]

    sig_records = [r for r in records if r.get("check_id") == "chk_loan_agreement_digital_signature"]
    assert len(sig_records) == 1, "Expected chk_loan_agreement_digital_signature record"

    rec = sig_records[0]
    assert rec["match_status"] == "MATCH"
    assert rec["confidence"] == 100.0
    assert "Digitally Signed" in rec["doc_value"]
    assert rec["signer_info"] is not None
    assert rec["signer_info"]["common_name"] != "Unknown"


def test_check_loan_app_with_unsigned_loan_agreement():
    """Pipeline Checker Integration: Verifies that an unsigned Loan Agreement emits a MISMATCH record."""
    loan_id = "LOAN_SIG_TEST_02"
    inspection_report = inspect_pdf_signatures(UNSIGNED_PDF, filename="Loan_Agreement.pdf")

    state: PipelineState = {
        "loan_id": loan_id,
        "extracted_data": {
            "loan_agreement": {
                "pyhanko_inspection": inspection_report,
                "loan_agreement_present": True,
                "loan_agreement_signed": False,
            }
        },
        "los_data": {"loan_id": loan_id},
        "raw_doc_paths": {"Loan_Agreement.pdf": str(UNSIGNED_PDF)},
    }

    result = check_loan_app(state)
    records = result["records"]

    sig_records = [r for r in records if r.get("check_id") == "chk_loan_agreement_digital_signature"]
    assert len(sig_records) == 1

    rec = sig_records[0]
    assert rec["match_status"] == "MISMATCH"
    assert rec["confidence"] == 0.0
    assert "Unsigned" in rec["doc_value"]


def test_check_loan_app_without_loan_agreement_skips_signature_record():
    """Invariance: When NO Loan Agreement is uploaded, NO digital signature record is generated."""
    loan_id = "LOAN_SIG_TEST_03"

    state: PipelineState = {
        "loan_id": loan_id,
        "extracted_data": {
            "kfs": {"application_no": loan_id},
            "sanction_letter": {"application_no": loan_id},
        },
        "los_data": {"loan_id": loan_id},
        "raw_doc_paths": {
            "kfs.pdf": "/path/to/kfs.pdf",
            "sanction_letter.pdf": "/path/to/sanction_letter.pdf",
        },
    }

    result = check_loan_app(state)
    records = result["records"]

    # Must NOT generate chk_loan_agreement_digital_signature when loan agreement was not uploaded
    sig_records = [r for r in records if r.get("check_id") == "chk_loan_agreement_digital_signature"]
    assert len(sig_records) == 0


def test_checkpoint_6_scorecard_serialization_with_pyhanko_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Scorecard Serialization: Validates Checkpoint 6 dynamic transitions driven by pyHanko signature checks."""
    loan_id = "LOAN_SCORECARD_PYHANKO"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id}.json").write_text(json.dumps({"loan_id": loan_id}))

    struct_dir = tmp_path / "s3_extracted_structured" / loan_id
    struct_dir.mkdir(parents=True, exist_ok=True)

    result_dir = tmp_path / "s3_result" / loan_id
    result_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_STRUCTURED_DIR", tmp_path / "s3_extracted_structured")
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    # Scenario 1: Loan Agreement signed with pyHanko -> Checkpoint 6 is VERIFIED
    (struct_dir / f"{loan_id}_loan_agreement.json").write_text(
        json.dumps({
            "loan_agreement_present": True,
            "loan_agreement_signed": True,
            "pyhanko_inspection": {"is_signed": True, "is_acceptable": True},
        })
    )
    (result_dir / "comparison_results.json").write_text(
        json.dumps([
            {
                "check_id": "chk_loan_agreement_digital_signature",
                "loan_id": loan_id,
                "doc_type": "loan_agreement",
                "field": "digital_signature",
                "match_status": "MATCH",
                "confidence": 100.0,
                "notes": "Loan agreement cryptographically intact and digitally signed by 'Test Signer'.",
            }
        ])
    )

    case = serialize_case(loan_id)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    assert cp6["status"] == "VERIFIED"
    assert cp6["confidence"] == 100.0
    assert cp6["validation"]["result"] == "MATCH"

    # Scenario 2: Loan Agreement unsigned -> Checkpoint 6 is DISCREPANCY
    (struct_dir / f"{loan_id}_loan_agreement.json").write_text(
        json.dumps({
            "loan_agreement_present": True,
            "loan_agreement_signed": False,
            "pyhanko_inspection": {"is_signed": False, "is_acceptable": False},
        })
    )
    (result_dir / "comparison_results.json").write_text(
        json.dumps([
            {
                "check_id": "chk_loan_agreement_digital_signature",
                "loan_id": loan_id,
                "doc_type": "loan_agreement",
                "field": "digital_signature",
                "match_status": "MISMATCH",
                "confidence": 0.0,
                "notes": "Loan agreement uploaded but missing required digital signature.",
            }
        ])
    )

    case = serialize_case(loan_id)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    assert cp6["status"] == "DISCREPANCY"
    assert cp6["validation"]["result"] == "MISMATCH"

    # Scenario 3: Loan Agreement missing completely -> Checkpoint 6 is INDETERMINATE
    (struct_dir / f"{loan_id}_loan_agreement.json").unlink(missing_ok=True)
    (result_dir / "comparison_results.json").write_text(json.dumps([]))

    case = serialize_case(loan_id)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    assert cp6["status"] == "INDETERMINATE"
    assert cp6.get("reason") == "Loan agreement not uploaded."


def test_idp_scan_bypasses_ocr_and_populates_pyhanko(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that idp_scan fast-tracks Loan Agreements via pyHanko without running full OCR."""
    from pipeline.nodes.idp_scan import idp_scan

    loan_id = "LOAN_OCR_BYPASS_TEST"
    s3_ext = tmp_path / "s3_extracted"
    s3_raw = tmp_path / "s3_raw"
    s3_ext.mkdir(parents=True, exist_ok=True)
    s3_raw.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("pipeline.nodes.idp_scan.S3_EXTRACTED_DIR", s3_ext)
    monkeypatch.setattr("pipeline.nodes.idp_scan.S3_RAW_DIR", s3_raw)

    from idp.services.output.serializer import DocumentSerializer
    from idp.services.output.canonical_builder import build_canonical_extracted_dict

    def _mock_idp_call(fpath, doc_id, doc_key):
        serializer = DocumentSerializer()
        parsed = serializer.parse_loan_agreement_fast_path(str(fpath), doc_id=doc_id, filename=Path(fpath).name)
        return build_canonical_extracted_dict(parsed, doc_type=doc_key, doc_id=doc_id)

    monkeypatch.setattr("pipeline.nodes.idp_scan._call_idp_service", _mock_idp_call)

    state: PipelineState = {
        "loan_id": loan_id,
        "raw_doc_paths": {
            "Loan_Agreement.pdf": str(SIGNED_PDF),
        },
        "extracted_data": {},
        "errors": [],
        "node_history": [],
    }

    out_state = idp_scan(state)

    extracted = out_state.get("extracted_data", {})

    assert "loan_agreement" in extracted, "Loan agreement must be present in extracted_data"
    agree_doc = extracted["loan_agreement"]

    # Verify pyHanko fast-path outputs
    assert agree_doc["loan_agreement_present"] is True
    assert agree_doc["loan_agreement_signed"] is True
    assert "pyhanko_inspection" in agree_doc
    assert agree_doc["pyhanko_inspection"]["is_signed"] is True
    assert "DIGITALLY SIGNED (VALID)" in agree_doc["_raw_text"]
    assert agree_doc["_components"]["raw_elements"][0]["type"] == "paragraph"


def test_parse_loan_agreement_fast_path():
    """Verifies that DocumentSerializer.parse_loan_agreement_fast_path outputs standard ParsedDocument."""
    from idp.services.output.serializer import DocumentSerializer

    serializer = DocumentSerializer()
    parsed_doc = serializer.parse_loan_agreement_fast_path(
        file_path=str(SIGNED_PDF),
        doc_id="DOC-TEST-LOAN-AGREEMENT",
        filename="Loan_Agreement.pdf",
    )

    assert parsed_doc.document_id == "DOC-TEST-LOAN-AGREEMENT"
    assert parsed_doc.source.filename == "Loan_Agreement.pdf"
    assert parsed_doc.processing.docling_used is False
    assert parsed_doc.processing.ocr_engine == "none"
    assert parsed_doc.custom_metadata["loan_agreement_present"] is True
    assert parsed_doc.custom_metadata["loan_agreement_signed"] is True
    assert parsed_doc.custom_metadata["pyhanko_inspection"]["is_signed"] is True
    assert "DIGITALLY SIGNED (VALID)" in parsed_doc.text

