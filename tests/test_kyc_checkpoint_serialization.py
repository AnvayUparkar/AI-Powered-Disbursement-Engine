import json
from pathlib import Path
import pytest
from app.serializers.case_serializer import serialize_case


def test_kyc_checkpoint_only_one_pan_uploaded_is_indeterminate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If only 1 PAN is uploaded and address proof is missing, KYC must be INDETERMINATE (not VERIFIED),
    and must compare document PAN against LOS PAN."""
    loan_id = "LOAN_TEST_KYC_ONE_PAN"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Test User",
        "applicant_pan_number": "ABCDE1234F",
        "current_address": "123 Main Street",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    # Only PAN is present
    (ext_dir / "kyc_pan.json").write_text(json.dumps({"pan_number": "ABCDE1234F"}))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    kyc_cp = next(cp for cp in case["checkpoints"] if cp["id"] == 4)

    assert kyc_cp["status"] == "INDETERMINATE"
    assert "mandatory Address Proof document is missing" in kyc_cp["reason"]
    assert kyc_cp["validation"]["left"] == "ABCDE1234F"
    assert kyc_cp["validation"]["right"] == "ABCDE1234F"
    assert kyc_cp["validation"]["result"] == "MATCH"


def test_kyc_checkpoint_no_documents_no_false_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If no documents and no LOS data, must be INDETERMINATE with MISMATCH validation (never N/A = N/A match)."""
    loan_id = "LOAN_TEST_KYC_EMPTY"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id}.json").write_text(json.dumps({"loan_id": loan_id}))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    kyc_cp = next(cp for cp in case["checkpoints"] if cp["id"] == 4)

    assert kyc_cp["status"] == "INDETERMINATE"
    assert kyc_cp["validation"]["left"] == "N/A"
    assert kyc_cp["validation"]["right"] == "N/A"
    assert kyc_cp["validation"]["result"] == "MISMATCH"


def test_kyc_checkpoint_both_pan_and_address_present_and_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Happy path: Both PAN and Address Proof uploaded and match LOS -> VERIFIED."""
    loan_id = "LOAN_TEST_KYC_FULL_MATCH"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Test User",
        "applicant_pan_number": "ABCDE1234F",
        "current_address": "123 Main Street",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "kyc_pan.json").write_text(json.dumps({"pan_number": "ABCDE1234F"}))
    (ext_dir / "kyc_address_proof.json").write_text(json.dumps({"address_text": "123 Main Street"}))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    kyc_cp = next(cp for cp in case["checkpoints"] if cp["id"] == 4)

    assert kyc_cp["status"] == "VERIFIED"
    assert kyc_cp["validation"]["left"] == "ABCDE1234F"
    assert kyc_cp["validation"]["right"] == "ABCDE1234F"
    assert kyc_cp["validation"]["result"] == "MATCH"


def test_kyc_checkpoint_pan_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Failure mode: Document PAN differs from LOS PAN -> DISCREPANCY."""
    loan_id = "LOAN_TEST_KYC_PAN_MISMATCH"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Test User",
        "applicant_pan_number": "XYZPK9988A",
        "current_address": "123 Main Street",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "kyc_pan.json").write_text(json.dumps({"pan_number": "ABCDE1234F"}))
    (ext_dir / "kyc_address_proof.json").write_text(json.dumps({"address_text": "123 Main Street"}))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    kyc_cp = next(cp for cp in case["checkpoints"] if cp["id"] == 4)

    assert kyc_cp["status"] == "DISCREPANCY"
    assert kyc_cp["validation"]["left"] == "ABCDE1234F"
    assert kyc_cp["validation"]["right"] == "XYZPK9988A"
    assert kyc_cp["validation"]["result"] == "MISMATCH"


def test_kyc_checkpoint_aadhaar_name_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Failure mode: Aadhaar applicant name differs from LOS name in comparison_results.json -> DISCREPANCY."""
    loan_id = "LOAN_TEST_AADHAAR_NAME_MISMATCH"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "PRAKASH KHATRI",
        "applicant_pan_number": "ABCDE1234F",
        "current_address": "123 Main Street",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "kyc_pan.json").write_text(json.dumps({"pan_number": "ABCDE1234F"}))
    (ext_dir / "kyc_address_proof.json").write_text(json.dumps({"address_text": "123 Main Street"}))

    res_dir = tmp_path / "s3_result" / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)
    comp_results = {
        "results": [
            {
                "check_id": "chk_check_kyc_aadhaar_applicant_name_vs_los",
                "checkpoint": "check_kyc",
                "subnode": "check_kyc",
                "field": "applicant_name",
                "values": ["Tanishq Parmar", "PRAKASH KHATRI"],
                "match_status": "MISMATCH",
                "result": "MISMATCH",
                "confidence": 0.52,
                "reason": "Name mismatch between Aadhaar and LOS",
            }
        ]
    }
    (res_dir / "comparison_results.json").write_text(json.dumps(comp_results))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    kyc_cp = next(cp for cp in case["checkpoints"] if cp["id"] == 4)

    assert kyc_cp["status"] == "DISCREPANCY"
    assert "Discrepancies found:" in kyc_cp["reason"]
    assert kyc_cp["validation"]["left"] == "Tanishq Parmar"
    assert kyc_cp["validation"]["right"] == "PRAKASH KHATRI"
    assert kyc_cp["validation"]["result"] == "MISMATCH"
    assert case["status"] == "DISCREPANCY"


def test_case_status_derived_from_scorecard_reject_or_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Authoritative scorecard.json with preliminary_decision REJECT_OR_FLAG sets overall status to DISCREPANCY."""
    loan_id = "LOAN_TEST_SCORECARD_SYNC"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id}.json").write_text(json.dumps({"loan_id": loan_id}))

    res_dir = tmp_path / "s3_result" / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)
    scorecard_data = {
        "overall_score": 21.5,
        "preliminary_decision": "REJECT_OR_FLAG",
        "risk_tier": "HIGH_RISK",
        "checkpoints": [],
    }
    (res_dir / "scorecard.json").write_text(json.dumps(scorecard_data))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    assert case["dgcl_score"] == 21.5
    assert case["status"] == "DISCREPANCY"

