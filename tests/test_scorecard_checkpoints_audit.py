"""Unit and integration tests for DGCL Scorecard checkpoints audit alignment."""
import json
from pathlib import Path
import pytest
from app.serializers.case_serializer import serialize_case


def test_checkpoint_1_loan_amount_three_fields_three_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Happy Path: When Application Form, KFS, and Sanction Letter are uploaded,
    Checkpoint 1 must produce 3 extracted fields and 3 distinct pieces of evidence."""
    loan_id = "LOAN_TEST_CP1_THREE_EVIDENCE"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {"loan_id": loan_id, "loan_amount": 500000.0, "applicant_name": "Test User"}
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "application_form.json").write_text(json.dumps({"loan_amount": 500000.0}))
    (ext_dir / "kfs.json").write_text(json.dumps({"loan_amount": 500000.0}))
    (ext_dir / "sanction_letter.json").write_text(json.dumps({"loan_amount": 500000.0}))

    res_dir = tmp_path / "s3_result" / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "comparison_results.json").write_text(json.dumps([]))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    cp1 = next(cp for cp in case["checkpoints"] if cp["id"] == 1)

    assert cp1["status"] == "VERIFIED"
    assert len(cp1["extractedFields"]) == 3
    assert len(cp1["evidence"]) == 3
    ev_doc_names = [e["documentName"] for e in cp1["evidence"]]
    assert "Application_Form.pdf" in ev_doc_names
    assert "KFS.pdf" in ev_doc_names
    assert "Sanction_Letter.pdf" in ev_doc_names
    assert cp1["validation"]["result"] == "MATCH"


def test_checkpoint_2_loan_validity_two_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Happy Path: When Application Form and Sanction Letter tenures are present,
    Checkpoint 2 must link both Application_Form.pdf and Sanction_Letter.pdf."""
    loan_id = "LOAN_TEST_CP2_TWO_EVIDENCE"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {"loan_id": loan_id, "loan_validity": 36}
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "application_form.json").write_text(json.dumps({"loan_validity": 36}))
    (ext_dir / "sanction_letter.json").write_text(json.dumps({"loan_validity": 36}))

    res_dir = tmp_path / "s3_result" / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "comparison_results.json").write_text(json.dumps([]))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    cp2 = next(cp for cp in case["checkpoints"] if cp["id"] == 2)

    assert cp2["status"] == "VERIFIED"
    assert len(cp2["evidence"]) == 2
    ev_doc_names = [e["documentName"] for e in cp2["evidence"]]
    assert "Application_Form.pdf" in ev_doc_names
    assert "Sanction_Letter.pdf" in ev_doc_names


def test_checkpoint_3_application_form_surfaces_fields_and_exact_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Failure mode: When Application Form has a field mismatch (e.g. mobile number),
    CP 3 must be DISCREPANCY and the validation block must highlight the mismatching values."""
    loan_id = "LOAN_TEST_CP3_MISMATCH"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Prakash Khatri",
        "applicant_mobile_no": "9166202777",
        "application_no": "APPL00343265",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    app_form_data = {
        "applicant_name": "Prakash Khatri",
        "mobile_no": "9079533555",
        "application_no": "APPL00343265",
        "application_date": "06/08/2026",
        "loan_type": "Business",
        "fathers_name": "Gyan Chand Khatri",
        "pan_number": "AOOPK6924P",
    }
    (ext_dir / "application_form.json").write_text(json.dumps(app_form_data))

    res_dir = tmp_path / "s3_result" / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)
    comp_results = [
        {
            "check_id": "chk_check_kyc_application_form_mobile_no_vs_los",
            "subnode": "check_loan_application",
            "field": "mobile_no",
            "values": ["9079533555", "9166202777"],
            "match_status": "MISMATCH",
            "notes": "Mobile mismatch",
        }
    ]
    (res_dir / "comparison_results.json").write_text(json.dumps(comp_results))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    cp3 = next(cp for cp in case["checkpoints"] if cp["id"] == 3)

    assert cp3["status"] == "DISCREPANCY"
    assert len(cp3["extractedFields"]) >= 6
    assert cp3["validation"]["left"] == "9079533555"
    assert cp3["validation"]["right"] == "9166202777"
    assert cp3["validation"]["result"] == "MISMATCH"


def test_checkpoint_10_bpi_matched_from_kfs_and_los(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Happy Path: KFS has 'BPI': 2361 and LOS has bpi_charges: 2361.0.
    Checkpoint 10 must be VERIFIED with MATCH and KFS.pdf evidence."""
    loan_id = "LOAN_TEST_CP10_BPI_MATCH"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {"loan_id": loan_id, "bpi_charges": 2361.0}
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    ext_dir = tmp_path / "s3_extracted" / loan_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "kfs.json").write_text(json.dumps({"BPI": 2361}))

    res_dir = tmp_path / "s3_result" / loan_id
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "comparison_results.json").write_text(json.dumps([]))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    cp10 = next(cp for cp in case["checkpoints"] if cp["id"] == 10)

    assert cp10["status"] == "VERIFIED"
    assert len(cp10["evidence"]) == 1
    assert cp10["evidence"][0]["documentName"] == "KFS.pdf"
    assert cp10["validation"]["result"] == "MATCH"


def test_checkpoints_missing_documents_indeterminate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Boundary / Edge Case: When Selfie, Agreement, or Aadhaar XML are absent,
    they evaluate to INDETERMINATE with 0 evidence items."""
    loan_id = "LOAN_TEST_MISSING_DOCS"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id}.json").write_text(json.dumps({"loan_id": loan_id}))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    cp5 = next(cp for cp in case["checkpoints"] if cp["id"] == 5)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    cp9 = next(cp for cp in case["checkpoints"] if cp["id"] == 9)

    assert cp5["status"] == "INDETERMINATE"
    assert len(cp5["evidence"]) == 0
    assert cp6["status"] == "INDETERMINATE"
    assert len(cp6["evidence"]) == 0
    assert cp9["status"] == "INDETERMINATE"
    assert len(cp9["evidence"]) == 0


def test_checkpoint_12_balance_transfer_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that balance_transfer=0 yields NOT_APPLICABLE and balance_transfer=1 triggers BT evaluation."""
    loan_id_no_bt = "LOAN_NO_BT"
    loan_id_bt = "LOAN_IS_BT"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id_no_bt}.json").write_text(json.dumps({"loan_id": loan_id_no_bt, "balance_transfer": 0}))
    (los_dir / f"{loan_id_bt}.json").write_text(json.dumps({"loan_id": loan_id_bt, "balance_transfer": 1}))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    # Case with balance_transfer = 0
    case_no_bt = serialize_case(loan_id_no_bt)
    cp12_no_bt = next(cp for cp in case_no_bt["checkpoints"] if cp["id"] == 12)
    assert cp12_no_bt["status"] == "NOT_APPLICABLE"
    assert case_no_bt["balanceTransfer"] == 0
    assert case_no_bt["isBalanceTransfer"] is False

    # Case with balance_transfer = 1 (missing BT doc)
    case_bt = serialize_case(loan_id_bt)
    cp12_bt = next(cp for cp in case_bt["checkpoints"] if cp["id"] == 12)
    assert cp12_bt["status"] == "INDETERMINATE"
    assert case_bt["balanceTransfer"] == 1
    assert case_bt["isBalanceTransfer"] is True


def test_checkpoint_6_dynamic_loan_agreement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that CP 6 evaluates dynamically based on loan_agreement_present and loan_agreement_signed."""
    loan_id = "LOAN_AGREE_TEST"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id}.json").write_text(json.dumps({"loan_id": loan_id}))

    struct_dir = tmp_path / "s3_extracted_structured" / loan_id
    struct_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_STRUCTURED_DIR", tmp_path / "s3_extracted_structured")
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    # Scenario 1: Present and Signed -> VERIFIED
    (struct_dir / f"{loan_id}_loan_agreement.json").write_text(
        json.dumps({"loan_agreement_present": True, "loan_agreement_signed": True})
    )
    case = serialize_case(loan_id)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    assert cp6["status"] == "VERIFIED"
    assert len(cp6["evidence"]) == 1

    # Scenario 2: Present but Unsigned -> DISCREPANCY
    (struct_dir / f"{loan_id}_loan_agreement.json").write_text(
        json.dumps({"loan_agreement_present": True, "loan_agreement_signed": False})
    )
    case = serialize_case(loan_id)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    assert cp6["status"] == "DISCREPANCY"
    assert cp6["validation"]["result"] == "MISMATCH"

    # Scenario 3: Missing -> INDETERMINATE
    (struct_dir / f"{loan_id}_loan_agreement.json").write_text(
        json.dumps({"loan_agreement_present": False, "loan_agreement_signed": False})
    )
    case = serialize_case(loan_id)
    cp6 = next(cp for cp in case["checkpoints"] if cp["id"] == 6)
    assert cp6["status"] == "INDETERMINATE"
    assert len(cp6["evidence"]) == 0


def test_checkpoint_9_dynamic_aadhaar_xml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that CP 9 evaluates dynamically based on aadhaar_xml_present."""
    loan_id = "LOAN_XML_TEST"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    (los_dir / f"{loan_id}.json").write_text(json.dumps({"loan_id": loan_id}))

    struct_dir = tmp_path / "s3_extracted_structured" / loan_id
    struct_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_STRUCTURED_DIR", tmp_path / "s3_extracted_structured")
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    # Scenario 1: Present -> VERIFIED
    (struct_dir / f"{loan_id}_aadhaar_xml.json").write_text(
        json.dumps({"aadhaar_xml_present": True, "applicant_name": "Tanishq Parmar"})
    )
    case = serialize_case(loan_id)
    cp9 = next(cp for cp in case["checkpoints"] if cp["id"] == 9)
    assert cp9["status"] == "VERIFIED"
    assert len(cp9["evidence"]) == 1
    assert any(f["name"] == "Aadhaar XML Name" and f["value"] == "Tanishq Parmar" for f in cp9["extractedFields"])

    # Scenario 2: Absent -> INDETERMINATE
    (struct_dir / f"{loan_id}_aadhaar_xml.json").write_text(
        json.dumps({"aadhaar_xml_present": False})
    )
    case = serialize_case(loan_id)
    cp9 = next(cp for cp in case["checkpoints"] if cp["id"] == 9)
    assert cp9["status"] == "INDETERMINATE"
    assert len(cp9["evidence"]) == 0


def test_checkpoint_3_weighted_match_and_dynamic_confidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that CP 3 calculates high matchScore and dynamic confidence on single field mismatch, not 50.0%."""
    loan_id = "LOAN_APP_SCORE_TEST"

    los_dir = tmp_path / "los" / "loans"
    los_dir.mkdir(parents=True, exist_ok=True)
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Ravi Kumar",
        "applicant_mobile_no": "9876543210",
        "applicant_pan_number": "ABCDE1234F",
        "applicant_dob": "15/05/1990",
        "applicant_gender": "Male",
        "fathers_name": "Suresh Kumar",
        "current_address": "123 Main St, Bangalore",
        "applicant_bank_account_no": "1122334455",
        "bank_account_type": "Savings",
        "loan_type": "Personal Loan",
        "loan_amount": 500000.0,
        "tenure": 24,
        "application_date": "10/01/2024",
    }
    (los_dir / f"{loan_id}.json").write_text(json.dumps(los_data))

    struct_dir = tmp_path / "s3_extracted_structured" / loan_id
    struct_dir.mkdir(parents=True, exist_ok=True)

    # 13 fields match, only mobile number mismatches
    app_form_data = {
        "applicant_name": "Ravi Kumar",
        "mobile_no": "9111122222",  # Mismatch
        "pan_number": "ABCDE1234F",
        "dob": "15/05/1990",
        "gender": "Male",
        "fathers_name": "Suresh Kumar",
        "address": "123 Main St, Bangalore",
        "bank_account_no": "1122334455",
        "type_of_account": "Savings",
        "loan_type": "Personal Loan",
        "loan_amount": 500000.0,
        "loan_validity": 24,
        "application_no": loan_id,
        "application_date": "10/01/2024",
    }
    (struct_dir / f"{loan_id}_loan_application.json").write_text(json.dumps(app_form_data))

    monkeypatch.setattr("app.serializers.case_serializer.LOS_LOANS_DIR", los_dir)
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_STRUCTURED_DIR", tmp_path / "s3_extracted_structured")
    monkeypatch.setattr("app.serializers.case_serializer.S3_EXTRACTED_DIR", tmp_path / "s3_extracted")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RAW_DIR", tmp_path / "s3_raw")
    monkeypatch.setattr("app.serializers.case_serializer.DMS_DIR", tmp_path / "dms")
    monkeypatch.setattr("app.serializers.case_serializer.S3_RESULT_DIR", tmp_path / "s3_result")

    case = serialize_case(loan_id)
    cp3 = next(cp for cp in case["checkpoints"] if cp["id"] == 3)

    # Status must be DISCREPANCY due to business policy on mobile mismatch
    assert cp3["status"] == "DISCREPANCY"
    # matchScore must reflect weighted fidelity (~96.2%), NOT a hardcoded 50.0%
    assert cp3["matchScore"] > 90.0
    # confidence must be dynamic and not hardcoded to 50.0
    assert cp3["confidence"] != 50.0
    assert cp3["confidence"] > 70.0
    assert "Discrepancy detected in Mobile No" in cp3["reason"] or "Mobile No" in cp3["reason"]
