"""Unit and integration tests verifying the elimination of silent LOS fallbacks in checkpoint serializers.
No monkeypatching: pure dataclass inputs and real case testing.
"""
from pathlib import Path
from app.serializers.case_context import CaseContext
from app.serializers.case_serializer import serialize_case
from app.serializers.checkpoints.financial import (
    build_kfs_checkpoint,
    build_loan_amount_checkpoint,
    build_sanction_letter_checkpoint,
)
from app.serializers.checkpoints.identity_kyc import (
    build_application_form_checkpoint,
    build_kyc_checkpoint,
)


def _make_context(
    loan_id: str = "TEST_LOAN",
    los_data: dict | None = None,
    docs: dict | None = None,
    real_doc_names: list[str] | None = None,
    records: list[dict] | None = None,
    tmp_path: Path | None = None,
) -> CaseContext:
    """Builds an isolated CaseContext directly without filesystem mocking or monkeypatching."""
    base_dir = tmp_path or Path(".")
    los = los_data or {}
    docs_dict = docs or {}
    rec_list = records or []
    rec_by_id = {r["check_id"]: r for r in rec_list if r.get("check_id")}
    rec_by_field: dict[str, list] = {}
    rec_by_subnode: dict[str, list] = {}
    for r in rec_list:
        if r.get("field"):
            rec_by_field.setdefault(r["field"], []).append(r)
        if r.get("subnode"):
            rec_by_subnode.setdefault(r["subnode"], []).append(r)

    doc_names = real_doc_names or list(docs_dict.keys())
    loan_amount = float(los.get("loan_amount") or 0.0)

    return CaseContext(
        loan_id=loan_id,
        los_data=los,
        docs=docs_dict,
        real_doc_names=doc_names,
        doc_ids=[f"doc-{loan_id}-{d}" for d in doc_names],
        records=rec_list,
        records_by_id=rec_by_id,
        records_by_field=rec_by_field,
        records_by_subnode=rec_by_subnode,
        status_data={},
        scorecard_data={},
        subnode_rollups={},
        loan_amount=loan_amount,
        disbursal_amount=round(loan_amount * 0.9, 2),
        applicant_name=str(los.get("applicant_name") or "Test Applicant"),
        app_id=str(los.get("loan_id") or loan_id),
        loan_type=str(los.get("loan_type") or "Personal Loan"),
        is_bt=bool(los.get("balance_transfer", 0)),
        raw_dir=base_dir,
        dms_dir=base_dir,
        extracted_dir=base_dir,
        extracted_structured_dir=base_dir,
        result_dir=base_dir,
    )


def test_cp3_unextracted_fields_do_not_fallback_to_los(tmp_path: Path):
    """Edge: When Application Form has null fields, CP3 must NOT inject LOS values."""
    los_data = {
        "loan_id": "APPL_TEST",
        "applicant_name": "FALLBACK_NAME_FROM_LOS",
        "applicant_pan_number": "FALLBACK_PAN_FROM_LOS",
        "fathers_name": "FALLBACK_FATHER_FROM_LOS",
        "loan_type": "FALLBACK_LOAN_TYPE",
        "applicant_mobile_no": "9166202777",
    }
    app_form_doc = {
        "applicant_name": None,
        "pan_number": None,
        "fathers_name": None,
        "loan_type": None,
        "mobile_no": "9079533555",
        "dob": "22061976",
        "_field_locations": {
            "mobile_no": {"confidence": 0.97},
            "dob": {"confidence": 0.98},
        },
    }
    ctx = _make_context(
        loan_id="APPL_TEST",
        los_data=los_data,
        docs={"application_form": app_form_doc},
        real_doc_names=["Application_Form.pdf"],
        tmp_path=tmp_path,
    )

    cp3 = build_application_form_checkpoint(ctx)
    field_map = {f["name"]: f for f in cp3["extractedFields"]}

    # Unextracted fields must be None with 0.0 confidence (never fallback to LOS)
    assert field_map["Applicant Name"]["value"] is None
    assert field_map["Applicant Name"]["confidence"] == 0.0
    assert field_map["PAN Number"]["value"] is None
    assert field_map["PAN Number"]["confidence"] == 0.0
    assert field_map["Father's Name"]["value"] is None
    assert field_map["Father's Name"]["confidence"] == 0.0
    assert field_map["Loan Type"]["value"] is None
    assert field_map["Loan Type"]["confidence"] == 0.0

    # Actually extracted fields must have real values and confidence
    assert field_map["Mobile No"]["value"] == "9079533555"
    assert field_map["Mobile No"]["confidence"] == 97.0
    assert field_map["Date of Birth"]["value"] == "22061976"
    assert field_map["Date of Birth"]["confidence"] == 98.0


def test_cp3_all_fields_extracted_happy_path(tmp_path: Path):
    """Happy Path: When Application Form has all fields extracted, they are faithfully populated."""
    los_data = {
        "loan_id": "APPL_HAPPY",
        "applicant_name": "Prakash Khatri",
        "applicant_pan_number": "AOOPK6924P",
        "fathers_name": "Gyan Chand Khatri",
        "loan_type": "Business Loan",
        "applicant_mobile_no": "9079533555",
    }
    app_form_doc = {
        "applicant_name": "Prakash Khatri",
        "pan_number": "AOOPK6924P",
        "fathers_name": "Gyan Chand Khatri",
        "loan_type": "Business Loan",
        "mobile_no": "9079533555",
        "loan_amount": 1000000,
        "dob": "22061976",
        "gender": "Male",
        "_field_locations": {
            "applicant_name": {"confidence": 0.98},
            "pan_number": {"confidence": 0.99},
            "fathers_name": {"confidence": 0.97},
            "loan_type": {"confidence": 0.95},
            "mobile_no": {"confidence": 0.97},
            "dob": {"confidence": 0.98},
            "gender": {"confidence": 0.99},
            "loan_amount": {"confidence": 0.98},
        },
    }
    ctx = _make_context(
        loan_id="APPL_HAPPY",
        los_data=los_data,
        docs={"application_form": app_form_doc},
        real_doc_names=["Application_Form.pdf"],
        tmp_path=tmp_path,
    )

    cp3 = build_application_form_checkpoint(ctx)
    field_map = {f["name"]: f for f in cp3["extractedFields"]}

    assert field_map["Applicant Name"]["value"] == "Prakash Khatri"
    assert field_map["Applicant Name"]["confidence"] == 98.0
    assert field_map["PAN Number"]["value"] == "AOOPK6924P"
    assert field_map["PAN Number"]["confidence"] == 99.0
    assert cp3["matchScore"] >= 90.0


def test_extracted_field_without_telemetry_returns_none_confidence(tmp_path: Path):
    """Zero hardcoding: An extracted field with no pipeline comparison record and no OCR telemetry must return None confidence."""
    app_form_doc = {
        "applicant_name": "Prakash Khatri",
        "mobile_no": "9079533555",
    }
    ctx = _make_context(
        loan_id="APPL_NO_TELEMETRY",
        docs={"application_form": app_form_doc},
        real_doc_names=["Application_Form.pdf"],
        tmp_path=tmp_path,
    )
    cp3 = build_application_form_checkpoint(ctx)
    field_map = {f["name"]: f for f in cp3["extractedFields"]}

    assert field_map["Applicant Name"]["value"] == "Prakash Khatri"
    assert field_map["Applicant Name"]["confidence"] is None
    assert field_map["Applicant Name"]["hasTelemetry"] is False


def test_cp1_cp7_cp8_no_amount_fallback(tmp_path: Path):
    """Edge: Documents lacking loan_amount must NOT fabricate it from LOS loan_amount."""
    los_data = {"loan_id": "LOAN_NO_AMT", "loan_amount": 750000.0}
    docs = {
        "application_form": {"applicant_name": "Test"},
        "kfs": {"irr_percent": 14.5},
        "sanction_letter": {"emi": 25000},
    }
    ctx = _make_context(
        loan_id="LOAN_NO_AMT",
        los_data=los_data,
        docs=docs,
        real_doc_names=["Application_Form.pdf", "KFS.pdf", "Sanction_Letter.pdf"],
        tmp_path=tmp_path,
    )

    cp1 = build_loan_amount_checkpoint(ctx)
    cp7 = build_kfs_checkpoint(ctx)
    cp8 = build_sanction_letter_checkpoint(ctx)

    # CP1 should not contain Application Amount
    cp1_names = [f["name"] for f in cp1["extractedFields"]]
    assert "Application Amount" not in cp1_names

    # CP7 should not contain KFS Funding Amount
    cp7_names = [f["name"] for f in cp7["extractedFields"]]
    assert "KFS Funding Amount" not in cp7_names
    assert "KFS IRR" in cp7_names

    # CP8 should not contain Sanction Amount
    cp8_names = [f["name"] for f in cp8["extractedFields"]]
    assert "Sanction Amount" not in cp8_names
    assert "Sanction EMI" in cp8_names


def test_appl00343265_real_data_cp3_silent_fallback_eliminated():
    """Integration: APPL00343265 real storage data must surface null for unextracted fields."""
    case = serialize_case("APPL00343265")
    cp3 = next(cp for cp in case["checkpoints"] if cp["id"] == 3)
    field_map = {f["name"]: f for f in cp3["extractedFields"]}

    # Unextracted fields in application_form.json surface None honestly (no silent synthetic fallback)
    assert field_map["Applicant Name"]["value"] is None
    assert field_map["Applicant Name"]["confidence"] == 0.0

    assert field_map["Father's Name"]["value"] is None
    assert field_map["Father's Name"]["confidence"] == 0.0

    assert field_map["PAN Number"]["value"] is None
    assert field_map["PAN Number"]["confidence"] == 0.0

    assert field_map["Loan Type"]["value"] is None
    assert field_map["Loan Type"]["confidence"] == 0.0

    # Values that were successfully extracted
    assert field_map["Mobile No"]["value"] == "9079533552"
    assert field_map["Mobile No"]["confidence"] == 0.0

    assert field_map["Date of Birth"]["value"] == "22061976"
    assert field_map["Date of Birth"]["confidence"] == 100.0
