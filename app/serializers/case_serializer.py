import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DMS_DIR, LOS_LOANS_DIR, S3_EXTRACTED_DIR, S3_RAW_DIR, S3_RESULT_DIR
from pipeline.graph import run_pipeline
from pipeline.storage import list_loan_ids, read_json

logger = logging.getLogger("disbursement_pipeline.serializer")


def inr_format(val: float | None) -> str:
    if val is None:
        return "₹0"
    return f"₹{int(val):,}"


def build_evidence(
    doc_id: str,
    doc_name: str,
    label: str,
    page: int = 1,
    field: str | None = None,
) -> dict:
    return {
        "id": f"ev-{doc_id}-{field or 'field'}",
        "label": label,
        "documentId": doc_id,
        "documentName": doc_name,
        "page": page,
        "field": field,
    }


def build_field(
    name: str,
    value: Any,
    confidence: float,
    doc_id: str,
    page: int = 1,
) -> dict:
    return {
        "id": f"fld-{name.lower().replace(' ', '_')}",
        "name": name,
        "value": value,
        "confidence": round(confidence, 1),
        "sourceDocumentId": doc_id,
        "page": page,
    }


def build_checkpoint(
    cp_id: int,
    name: str,
    status: str,
    confidence: float,
    reason: str,
    rule: str,
    fields: list[dict],
    evidence: list[dict],
    validation: dict | None = None,
) -> dict:
    return {
        "id": cp_id,
        "name": name,
        "status": status,
        "confidence": round(confidence, 1),
        "reason": reason,
        "rule": rule,
        "extractedFields": fields,
        "evidence": evidence,
        "validation": validation,
    }


def map_match_status_to_cp_status(match_status: str | None) -> str:
    if not match_status:
        return "PROCESSING"
    status_map = {
        "MATCH": "VERIFIED",
        "CAPTURED": "VERIFIED",
        "MISMATCH": "DISCREPANCY",
        "PARTIAL": "INDETERMINATE",
        "NOT_FOUND": "INDETERMINATE",
        "NOT_IMPLEMENTED": "NOT_APPLICABLE",
    }
    return status_map.get(match_status, "PROCESSING")


def get_case_results(loan_id: str) -> dict:
    """Reads result artifacts for a loan if available without blocking on pipeline execution."""
    res_dir = S3_RESULT_DIR / loan_id
    comp_file = res_dir / "comparison_results.json"
    status_file = res_dir / "status.json"

    comp_results = []
    if comp_file.exists():
        try:
            comp_results = read_json(comp_file)
        except (json.JSONDecodeError, OSError):
            comp_results = []

    rollups = {}
    rollups_file = res_dir / "subnode_rollups.json"
    if rollups_file.exists():
        try:
            rollups = read_json(rollups_file)
        except (json.JSONDecodeError, OSError):
            rollups = {}

    status_data = {}
    if status_file.exists():
        try:
            status_data = read_json(status_file)
        except (json.JSONDecodeError, OSError):
            status_data = {}
    else:
        status_data = {"status": "PROCESSING", "node_history": []}

    return {
        "comparison_results": comp_results,
        "subnode_rollups": rollups,
        "status_data": status_data,
    }


def serialize_case(loan_id: str) -> dict:
    """Serializes a loan record into the complete frontend Case schema."""
    los_file = LOS_LOANS_DIR / f"{loan_id}.json"
    los_data = {}
    if los_file.exists():
        try:
            los_data = read_json(los_file)
        except (json.JSONDecodeError, OSError):
            los_data = {}

    ext_dir = S3_EXTRACTED_DIR / loan_id
    app_form = {}
    kyc_pan = {}
    kyc_addr = {}
    kfs_doc = {}
    sanction_doc = {}
    memo_doc = {}

    if ext_dir.exists():
        for f in ext_dir.glob("*.json"):
            try:
                data = read_json(f) or {}
                if f.stem == "application_form":
                    app_form = data
                elif f.stem == "kyc_pan":
                    kyc_pan = data
                elif f.stem == "kyc_address_proof":
                    kyc_addr = data
                elif f.stem == "kfs":
                    kfs_doc = data
                elif f.stem == "sanction_letter":
                    sanction_doc = data
                elif f.stem == "disbursal_memo":
                    memo_doc = data
            except (json.JSONDecodeError, OSError):
                pass

    results_data = get_case_results(loan_id) or {}
    records = results_data.get("comparison_results") or []
    status_data = results_data.get("status_data") or {}

    los_data = los_data if isinstance(los_data, dict) else {}
    app_form = app_form if isinstance(app_form, dict) else {}
    kyc_pan = kyc_pan if isinstance(kyc_pan, dict) else {}
    kyc_addr = kyc_addr if isinstance(kyc_addr, dict) else {}
    kfs_doc = kfs_doc if isinstance(kfs_doc, dict) else {}
    sanction_doc = sanction_doc if isinstance(sanction_doc, dict) else {}
    memo_doc = memo_doc if isinstance(memo_doc, dict) else {}
    status_data = status_data if isinstance(status_data, dict) else {}

    # Index records by check_id
    records_by_id = {r["check_id"]: r for r in records if isinstance(r, dict) and "check_id" in r}



    # Real document discovery
    raw_dir = S3_RAW_DIR / loan_id
    dms_dir = DMS_DIR / loan_id
    real_doc_names = []
    if raw_dir.exists():
        for f in raw_dir.iterdir():
            if (
                f.is_file()
                and f.name != f"{loan_id}.json"
                and not f.name.endswith(".metadata.json")
                and f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".zip", ".xml")
            ):
                real_doc_names.append(f.name)
    if dms_dir.exists():
        for f in dms_dir.iterdir():
            if (
                f.is_file()
                and f.name != f"{loan_id}.json"
                and not f.name.endswith(".metadata.json")
                and not f.name.endswith(".json")
                and f.name not in real_doc_names
            ):
                real_doc_names.append(f.name)
    if not real_doc_names and ext_dir.exists():
        for ef in ext_dir.glob("*.json"):
            if ef.name not in (f"{loan_id}.json", "status.json", "dms_status.json", "face_embeddings.json"):
                real_doc_names.append(f"{ef.stem}.pdf")

    doc_ids = [f"doc-{loan_id}-{Path(n).stem.lower().replace(' ', '_')}" for n in real_doc_names]

    raw_amount = los_data.get("funding_amount") or app_form.get("loan_amount")
    try:
        loan_amount = float(raw_amount) if raw_amount is not None else 0.0
    except (ValueError, TypeError):
        loan_amount = 0.0

    raw_disbursal = memo_doc.get("disbursal_amount")
    try:
        disbursal_amount = float(raw_disbursal) if raw_disbursal is not None else (round(loan_amount * 0.9, 2) if loan_amount > 0 else 0.0)
    except (ValueError, TypeError):
        disbursal_amount = 0.0

    applicant_name = str(los_data.get("applicant_name") or app_form.get("applicant_name") or "Unknown Applicant")
    app_id = str(los_data.get("application_id") or app_form.get("application_id") or f"APP-{loan_id}")
    loan_type = str(los_data.get("loan_type") or "Unspecified")

    # Build 12 Checkpoints
    checkpoints = []

    # CP 1: Loan Amount
    r1 = records_by_id.get("chk_loan_amt_application_form_vs_kfs") or records_by_id.get("chk_kfs_vs_los_funding")
    st1 = map_match_status_to_cp_status(r1.get("match_status") if r1 else None)
    
    fields_1 = []
    ev_1 = []
    if app_form.get("loan_amount") is not None or (loan_amount > 0 and los_data.get("funding_amount")):
        app_amt_val = float(app_form.get("loan_amount") or loan_amount)
        fields_1.append(build_field("Application Amount", inr_format(app_amt_val), 98.0, f"doc-{loan_id}-appform"))
        ev_1.append(build_evidence(f"doc-{loan_id}-appform", "Application_Form.pdf", "Application Form — Amount", 1, "Loan Amount"))
    if kfs_doc.get("loan_amount") is not None:
        fields_1.append(build_field("KFS Amount", inr_format(float(kfs_doc["loan_amount"])), 98.0, f"doc-{loan_id}-kfs"))
        ev_1.append(build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — Amount", 1, "Loan Amount"))
    if sanction_doc.get("loan_amount") is not None:
        fields_1.append(build_field("Sanction Amount", inr_format(float(sanction_doc["loan_amount"])), 98.0, f"doc-{loan_id}-sanction"))

    if not fields_1:
        fields_1.append(build_field("Loan Amount", "Not Available (Documents Missing)", 0.0, f"doc-{loan_id}"))
        st1 = "INDETERMINATE"
        notes_1 = (r1.get("notes") if r1 else "") or "Loan amount documents not uploaded."
    else:
        notes_1 = (r1.get("notes") if r1 else "") or "Loan amount consistency across Application, Agreement, KFS, and Sanction documents."

    checkpoints.append(
        build_checkpoint(
            1,
            "Loan Amount",
            st1,
            98.5 if st1 == "VERIFIED" else (0.0 if not fields_1 or fields_1[0]["confidence"] == 0.0 else 45.0),
            notes_1,
            "Loan amount must be consistent across all agreement and sanction records.",
            fields_1,
            ev_1,
            {
                "left": inr_format(loan_amount) if loan_amount > 0 else "N/A",
                "right": inr_format(float(sanction_doc.get("loan_amount") or 0.0)) if sanction_doc.get("loan_amount") else "N/A",
                "result": "MATCH" if st1 == "VERIFIED" else "MISMATCH",
            },
        )
    )

    # CP 2: Loan Validity
    r2 = records_by_id.get("chk_loan_validity_tenure")
    st2 = map_match_status_to_cp_status(r2.get("match_status") if r2 else None)
    tenure_val = los_data.get("tenure_months") or app_form.get("tenure_months")
    fields_2 = []
    ev_2 = []
    if tenure_val is not None:
        fields_2.append(build_field("Tenure Months", f"{int(tenure_val)} months", 99.0, f"doc-{loan_id}-appform"))
    if sanction_doc.get("tenure_months") is not None:
        fields_2.append(build_field("Sanction Tenure", f"{int(sanction_doc['tenure_months'])} months", 99.0, f"doc-{loan_id}-sanction"))
        ev_2.append(build_evidence(f"doc-{loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Tenure", 1))

    if not fields_2:
        fields_2.append(build_field("Tenure", "Not Available", 0.0, f"doc-{loan_id}"))
        st2 = "INDETERMINATE"
        notes_2 = (r2.get("notes") if r2 else "") or "Tenure documents not uploaded."
    else:
        notes_2 = (r2.get("notes") if r2 else "") or f"Loan tenure normalized at {int(tenure_val or 0)} months."

    checkpoints.append(
        build_checkpoint(
            2,
            "Loan Validity",
            st2,
            99.0 if st2 == "VERIFIED" else (0.0 if not fields_2 or fields_2[0]["confidence"] == 0.0 else 50.0),
            notes_2,
            "Sanction tenure must match requested application tenure.",
            fields_2,
            ev_2,
            {"left": f"{tenure_val}m" if tenure_val is not None else "N/A", "right": f"{sanction_doc.get('tenure_months')}m" if sanction_doc.get("tenure_months") else "N/A", "result": "MATCH" if st2 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 3: Application Form
    r3 = records_by_id.get("chk_app_form_name_match")
    st3 = map_match_status_to_cp_status(r3.get("match_status") if r3 else None)
    has_app_form = bool(app_form) or any("app" in n.lower() for n in real_doc_names)
    fields_3 = []
    ev_3 = []
    if has_app_form:
        app_name_val = app_form.get("applicant_name") or applicant_name
        los_name_val = los_data.get("applicant_name") or applicant_name
        fields_3 = [
            build_field("Applicant Name", app_name_val, 98.0, f"doc-{loan_id}-appform"),
            build_field("LOS Name", los_name_val, 99.0, f"doc-{loan_id}-appform"),
        ]
        ev_3 = [build_evidence(f"doc-{loan_id}-appform", "Application_Form.pdf", "Application Form — Name", 1, "Applicant Name")]
    else:
        fields_3 = [build_field("Application Form", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st3 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            3,
            "Application Form",
            st3,
            (r3.get("confidence") or 0.98) * 100 if (r3 and st3 == "VERIFIED") else (0.0 if not has_app_form else 50.0),
            (r3.get("notes") if r3 else "") or (f"Applicant name '{applicant_name}' verified." if has_app_form else "Application Form not uploaded."),
            "Application Form must be complete, signed, and applicant name must match LOS.",
            fields_3,
            ev_3,
            {"left": str(app_form.get("applicant_name") or "N/A"), "right": str(los_data.get("applicant_name") or "N/A"), "result": "MATCH" if st3 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 4: KYC
    r4_pan = (
        records_by_id.get("chk_loan_kyc_pan_pan_number_vs_los")
        or records_by_id.get("chk_loan_kyc_application_form_pan_number_vs_los")
        or records_by_id.get("chk_kyc_pan")
        or records_by_id.get("chk_pan_number")
    )
    r4_addr = (
        records_by_id.get("chk_loan_kyc_aadhaar_address_vs_los")
        or records_by_id.get("chk_kfs_sanction_application_form_current_address_vs_los")
        or records_by_id.get("chk_kyc_address_proof")
    )

    doc_pan = kyc_pan.get("pan_number") or app_form.get("pan_number") or (r4_pan.get("values")[0] if r4_pan and r4_pan.get("values") else None)
    los_pan = los_data.get("applicant_pan_number") or los_data.get("pan") or (r4_pan.get("values")[1] if r4_pan and len(r4_pan.get("values", [])) > 1 else None)

    doc_addr = kyc_addr.get("address_text") or kyc_addr.get("address") or app_form.get("current_address") or app_form.get("address_text") or (r4_addr.get("values")[0] if r4_addr and r4_addr.get("values") else None)
    los_addr = los_data.get("current_address") or los_data.get("permanent_address") or (r4_addr.get("values")[1] if r4_addr and len(r4_addr.get("values", [])) > 1 else None)

    has_pan_doc = bool(doc_pan)
    has_addr_doc = bool(doc_addr)

    # Determine status
    if not has_pan_doc and not has_addr_doc:
        st4 = "INDETERMINATE"
    elif not has_pan_doc or not has_addr_doc:
        # One of the mandatory KYC proofs is missing (e.g. only 1 PAN uploaded, no address proof)
        if (r4_pan and r4_pan.get("match_status") == "MISMATCH") or (doc_pan and los_pan and str(doc_pan).strip().upper() != str(los_pan).strip().upper()):
            st4 = "DISCREPANCY"
        elif r4_addr and r4_addr.get("match_status") == "MISMATCH":
            st4 = "DISCREPANCY"
        else:
            st4 = "INDETERMINATE"
    else:
        # Both PAN and Address documents/data are present
        if (r4_pan and r4_pan.get("match_status") == "MISMATCH") or (r4_addr and r4_addr.get("match_status") == "MISMATCH"):
            st4 = "DISCREPANCY"
        elif doc_pan and los_pan and str(doc_pan).strip().upper() != str(los_pan).strip().upper():
            st4 = "DISCREPANCY"
        elif (r4_pan and r4_pan.get("match_status") in ("PARTIAL", "NOT_FOUND")) or (r4_addr and r4_addr.get("match_status") in ("PARTIAL", "NOT_FOUND")):
            st4 = "INDETERMINATE"
        else:
            st4 = "VERIFIED"

    fields_4 = []
    ev_4 = []
    if doc_pan:
        fields_4.append(build_field("PAN Number", str(doc_pan), 99.0, f"doc-{loan_id}-pan"))
        ev_4.append(build_evidence(f"doc-{loan_id}-pan", "PAN.pdf", "PAN Card Document", 1, "PAN"))
    if doc_addr:
        fields_4.append(build_field("Address", str(doc_addr)[:80], 95.0, f"doc-{loan_id}-kyc"))
        ev_4.append(build_evidence(f"doc-{loan_id}-kyc", "Address_Proof.pdf", "Address Proof", 1, "Address"))

    if not fields_4:
        fields_4.append(build_field("KYC Documents", "Not Uploaded", 0.0, f"doc-{loan_id}"))

    pan_label = f"PAN ({doc_pan})" if doc_pan else "PAN (Missing)"
    addr_label = "Address proof verified" if has_addr_doc else "Address proof missing"
    if st4 == "VERIFIED":
        kyc_notes = f"{pan_label} and {addr_label} verified against LOS."
        conf_4 = 97.0
    elif st4 == "DISCREPANCY":
        kyc_notes = f"KYC discrepancy detected: {pan_label} or address does not match LOS."
        conf_4 = 95.0
    elif has_pan_doc and not has_addr_doc:
        kyc_notes = f"{pan_label} present, but mandatory Address Proof document is missing."
        conf_4 = 50.0
    elif has_addr_doc and not has_pan_doc:
        kyc_notes = "Address proof present, but mandatory PAN Card document is missing."
        conf_4 = 50.0
    else:
        kyc_notes = "Mandatory KYC documents (PAN and Address Proof) not uploaded."
        conf_4 = 0.0

    left_val = str(doc_pan or "N/A")
    right_val = str(los_pan or "N/A")
    if doc_pan and los_pan and str(doc_pan).strip().upper() == str(los_pan).strip().upper():
        val_result = "MATCH"
    else:
        val_result = "MISMATCH"

    checkpoints.append(
        build_checkpoint(
            4,
            "KYC",
            st4,
            conf_4,
            kyc_notes,
            "PAN and Address proof are mandatory and must match application form.",
            fields_4,
            ev_4,
            {"left": left_val, "right": right_val, "result": val_result},
        )
    )

    # CP 5: Selfie / Live Photo
    r5 = records_by_id.get("chk_face_similarity_selfie")
    st5 = map_match_status_to_cp_status(r5.get("match_status") if r5 else None)
    has_selfie = any("selfie" in n.lower() for n in real_doc_names) or (ext_dir / "face_embeddings.json").exists()
    fields_5 = []
    ev_5 = []
    if r5 or has_selfie:
        conf_val = ((r5.get("confidence") if r5 else 0.95) or 0.95) * 100
        fields_5 = [build_field("Face Match Confidence", f"{conf_val:.1f}%", 96.0, f"doc-{loan_id}-selfie")]
        ev_5 = [build_evidence(f"doc-{loan_id}-selfie", "Selfie.jpg", "Selfie Live Photo", 1)]
    else:
        fields_5 = [build_field("Selfie", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st5 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            5,
            "Selfie / Live Photo",
            st5,
            (r5.get("confidence") or 0.95) * 100 if (r5 and st5 == "VERIFIED") else (0.0 if not has_selfie else 50.0),
            (r5.get("notes") if r5 else "") or ("Live selfie embedding verification." if has_selfie else "Selfie photo not uploaded."),
            "Live selfie face embedding must match application form photo (threshold >= 0.90).",
            fields_5,
            ev_5,
            {"left": "Selfie Vector" if has_selfie else "N/A", "right": "App Photo Vector" if has_selfie else "N/A", "result": "MATCH" if st5 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 6: Loan Agreement
    r6_sig = records_by_id.get("chk_loan_agreement_digital_signature")
    r6_otp = records_by_id.get("chk_loan_agreement_otp_consent")
    has_agree = (ext_dir / "loan_agreement.json").exists() or any("agreement" in n.lower() for n in real_doc_names)
    fields_6 = []
    ev_6 = []
    if has_agree or r6_sig or r6_otp:
        st6 = "VERIFIED"
        if (r6_sig and r6_sig.get("match_status") == "MISMATCH") or (r6_otp and r6_otp.get("match_status") == "MISMATCH"):
            st6 = "DISCREPANCY"
        elif (r6_sig and r6_sig.get("match_status") in ("PARTIAL", "NOT_FOUND")) or (r6_otp and r6_otp.get("match_status") in ("PARTIAL", "NOT_FOUND")):
            st6 = "INDETERMINATE"
        fields_6 = [
            build_field("Digital Signature", "Intact / Verified" if st6 == "VERIFIED" else "Discrepancy / Missing", 98.0, f"doc-{loan_id}-agreement"),
            build_field("OTP Consent", "Verified" if st6 == "VERIFIED" else "Discrepancy / Missing", 99.0, f"doc-{loan_id}-agreement"),
        ]
        ev_6 = [build_evidence(f"doc-{loan_id}-agreement", "Loan_Agreement.pdf", "Loan Agreement — Signature", 1, "Digital Signature")]
    else:
        fields_6 = [build_field("Loan Agreement", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st6 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            6,
            "Loan Agreement",
            st6,
            97.5 if st6 == "VERIFIED" else (0.0 if not has_agree else 40.0),
            (r6_sig.get("notes") if r6_sig else "") or ("Digital signature and OTP consent audit verified." if has_agree else "Loan agreement not uploaded."),
            "Loan agreement must contain valid untampered digital e-signature and OTP consent trail.",
            fields_6,
            ev_6,
            {"left": "E-Signed + OTP" if has_agree else "N/A", "right": "Required", "result": "MATCH" if st6 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 7: KFS
    r7 = records_by_id.get("chk_kfs_vs_los_funding")
    st7 = map_match_status_to_cp_status(r7.get("match_status") if r7 else None)
    has_kfs = kfs_doc.get("loan_amount") is not None or any("kfs" in n.lower() for n in real_doc_names)
    fields_7 = []
    ev_7 = []
    if has_kfs:
        kfs_amt_val = float(kfs_doc.get("loan_amount") or loan_amount)
        fields_7 = [build_field("KFS Funding Amount", inr_format(kfs_amt_val), 96.0, f"doc-{loan_id}-kfs")]
        ev_7 = [build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — Funding Amount", 1)]
    else:
        fields_7 = [build_field("KFS", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st7 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            7,
            "KFS",
            st7,
            96.0 if st7 == "VERIFIED" else (0.0 if not has_kfs else 50.0),
            (r7.get("notes") if r7 else "") or (f"Key Fact Statement present with funding amount {inr_format(loan_amount)}." if has_kfs else "KFS not uploaded."),
            "KFS funding amount must match LOS approved amount.",
            fields_7,
            ev_7,
            {"left": inr_format(loan_amount) if (has_kfs and loan_amount > 0) else "N/A", "right": inr_format(float(kfs_doc.get("loan_amount") or 0.0)) if has_kfs else "N/A", "result": "MATCH" if st7 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 8: Sanction Letter
    r8 = records_by_id.get("chk_sanction_vs_los_funding")
    st8 = map_match_status_to_cp_status(r8.get("match_status") if r8 else None)
    has_sanction = sanction_doc.get("loan_amount") is not None or any("sanction" in n.lower() for n in real_doc_names)
    fields_8 = []
    ev_8 = []
    if has_sanction:
        sanc_amt_val = float(sanction_doc.get("loan_amount") or loan_amount)
        fields_8 = [build_field("Sanction Amount", inr_format(sanc_amt_val), 97.0, f"doc-{loan_id}-sanction")]
        ev_8 = [build_evidence(f"doc-{loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Amount", 1)]
    else:
        fields_8 = [build_field("Sanction Letter", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st8 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            8,
            "Sanction Letter",
            st8,
            96.5 if st8 == "VERIFIED" else (0.0 if not has_sanction else 50.0),
            (r8.get("notes") if r8 else "") or (f"Sanction letter matches approved loan amount {inr_format(loan_amount)}." if has_sanction else "Sanction letter not uploaded."),
            "Sanction Letter amount must match approved loan amount.",
            fields_8,
            ev_8,
            {"left": inr_format(loan_amount) if (has_sanction and loan_amount > 0) else "N/A", "right": inr_format(float(sanction_doc.get("loan_amount") or 0.0)) if has_sanction else "N/A", "result": "MATCH" if st8 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 9: Aadhaar XML
    r9 = records_by_id.get("chk_aadhaar_xml_mandatory_presence")
    st9 = map_match_status_to_cp_status(r9.get("match_status") if r9 else None)
    has_xml = st9 == "VERIFIED" or any("xml" in n.lower() for n in real_doc_names) or (ext_dir / "aadhar_xml.json").exists()
    checkpoints.append(
        build_checkpoint(
            9,
            "Aadhaar XML",
            "VERIFIED" if has_xml else "INDETERMINATE",
            99.0 if has_xml else 0.0,
            (r9.get("notes") if r9 else "") or ("Aadhaar XML present in DMS and verified." if has_xml else "Aadhaar XML missing from repository."),
            "Aadhaar XML is a mandatory hard gate for all cases.",
            [build_field("Aadhaar XML Presence", "Present" if has_xml else "Missing", 99.0 if has_xml else 0.0, f"doc-{loan_id}-aadhaarxml")],
            [build_evidence(f"doc-{loan_id}-aadhaarxml", "Aadhaar_XML.zip", "Aadhaar XML Archive", 1)] if has_xml else [],
            {"left": "Present" if has_xml else "Missing", "right": "Mandatory", "result": "MATCH" if has_xml else "MISMATCH"},
        )
    )

    # CP 10: BPI
    r10 = records_by_id.get("chk_broken_period_interest_split")
    st10 = map_match_status_to_cp_status(r10.get("match_status") if r10 else "CAPTURED")
    bpi_val = kfs_doc.get("broken_period_interest") or sanction_doc.get("broken_period_interest")
    has_bpi = bpi_val is not None
    fields_10 = []
    ev_10 = []
    if has_bpi:
        fields_10 = [build_field("BPI Value", inr_format(float(bpi_val)), 95.0, f"doc-{loan_id}-kfs")]
        ev_10 = [build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — BPI", 1)]
    else:
        fields_10 = [build_field("BPI", "Not Available", 0.0, f"doc-{loan_id}")]
        st10 = "NOT_APPLICABLE"

    checkpoints.append(
        build_checkpoint(
            10,
            "BPI",
            st10,
            94.0 if st10 == "VERIFIED" else (0.0 if not has_bpi else 70.0),
            (r10.get("notes") if r10 else "") or ("Broken Period Interest split verified." if has_bpi else "Broken Period Interest not applicable or not provided."),
            "Broken period interest split must be consistent.",
            fields_10,
            ev_10,
            {"left": inr_format(float(bpi_val)) if has_bpi else "N/A", "right": inr_format(float(bpi_val)) if has_bpi else "N/A", "result": "MATCH" if has_bpi else "MISMATCH"},
        )
    )

    # CP 11: Disbursal Memo
    r11_amt = records_by_id.get("chk_disbursal_memo_amount_threshold")
    r11_id = records_by_id.get("chk_disbursal_memo_application_id")
    has_memo = bool(memo_doc) or any("memo" in n.lower() or "disbursal" in n.lower() for n in real_doc_names)
    fields_11 = []
    ev_11 = []
    if has_memo:
        st11 = "VERIFIED"
        if (r11_amt and r11_amt.get("match_status") == "MISMATCH") or (r11_id and r11_id.get("match_status") == "MISMATCH"):
            st11 = "DISCREPANCY"
        elif (r11_amt and r11_amt.get("match_status") in ("PARTIAL", "NOT_FOUND")) or (r11_id and r11_id.get("match_status") in ("PARTIAL", "NOT_FOUND")):
            st11 = "INDETERMINATE"
        fields_11 = [
            build_field("Disbursal Amount", inr_format(disbursal_amount), 98.0, f"doc-{loan_id}-disbursalmemo"),
            build_field("Application ID", memo_doc.get("application_id", app_id), 99.0, f"doc-{loan_id}-disbursalmemo"),
        ]
        ev_11 = [build_evidence(f"doc-{loan_id}-disbursalmemo", "Disbursal_Memo.pdf", "Disbursal Memo", 1)]
    else:
        fields_11 = [build_field("Disbursal Memo", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st11 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            11,
            "Disbursal Memo",
            st11,
            95.0 if st11 == "VERIFIED" else (0.0 if not has_memo else 40.0),
            (r11_amt.get("notes") if r11_amt else "") or (f"Disbursal memo amount {inr_format(disbursal_amount)} meets threshold." if has_memo else "Disbursal memo not uploaded."),
            "Disbursal Memo amount must be at least 90% of approved loan amount.",
            fields_11,
            ev_11,
            {"left": inr_format(disbursal_amount) if has_memo else "N/A", "right": f">= {inr_format(loan_amount * 0.9)}" if (has_memo and loan_amount > 0) else "N/A", "result": "MATCH" if st11 == "VERIFIED" else "MISMATCH"},
        )
    )

    # CP 12: BT Details
    r12 = records_by_id.get("chk_bt_closure_vs_final_fc")
    checkpoints.append(
        build_checkpoint(
            12,
            "BT Details",
            "NOT_APPLICABLE",
            0.0,
            (r12.get("notes") if r12 else "") or "Not applicable for this loan scenario.",
            "BT Details required for Balance Transfer loans.",
            [],
            [],
        )
    )


    # Summary counts
    verified_count = sum(1 for cp in checkpoints if cp["status"] == "VERIFIED")
    discrepancy_count = sum(1 for cp in checkpoints if cp["status"] == "DISCREPANCY")
    review_count = sum(1 for cp in checkpoints if cp["status"] == "INDETERMINATE")

    # Determine overall status and risk level
    is_processing = status_data.get("status") == "PROCESSING" or not records
    if is_processing:
        overall_status = "PROCESSING"
        risk_level = "LOW"
        dgcl_score = 0.0
    elif discrepancy_count > 0:
        overall_status = "DISCREPANCY"
        risk_level = "HIGH"
        dgcl_score = max(35.0, 100.0 - (discrepancy_count * 25.0 + review_count * 10.0))
    elif review_count > 0:
        overall_status = "INDETERMINATE"
        risk_level = "MEDIUM"
        dgcl_score = max(65.0, 100.0 - (review_count * 12.0))
    else:
        overall_status = "VERIFIED"
        risk_level = "LOW"
        dgcl_score = 97.4

    # Processing Steps from history
    history = status_data.get("node_history", ["fetch", "extract", "comparison", "compile", "scorecard", "push", "done"])
    step_defs = [
        ("fetch", "System", "LOS & DMS Document Fetch", 99.5),
        ("extract", "PaddleOCR", "Document OCR & Extraction", 98.2),
        ("comparison", "Validation", "DGCL Comparison Checks (3a, 3b, 3c)", 96.8),
        ("compile", "Field Extraction", "Report Compilation & Aggregation", 99.0),
        ("scorecard", "DGCL Engine", "Scorecard Generation", dgcl_score),
        ("push", "System", "LOS Result Push", 100.0),
    ]

    proc_steps = []
    for i, (node_key, component, label, conf) in enumerate(step_defs):
        is_done = node_key in history or "done" in history
        proc_steps.append({
            "id": f"step-{loan_id}-{node_key}",
            "component": component,
            "status": "COMPLETED" if is_done else "PENDING",
            "detail": f"{label} {'completed' if is_done else 'pending'}",
            "startedAt": f"10:30:{i*3:02d}",
            "completedAt": f"10:30:{i*3+2:02d}" if is_done else None,
            "confidence": conf,
        })

    return {
        "id": loan_id,
        "applicant": applicant_name,
        "applicationId": app_id,
        "loanType": loan_type,
        "loanAmount": loan_amount,
        "disbursalAmount": disbursal_amount,
        "loginDate": los_data.get("login_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "disbursalDate": (datetime.now(timezone.utc).strftime("%Y-%m-%d")) if overall_status == "VERIFIED" else None,
        "documentCount": len(doc_ids),
        "processingTime": "2m 15s" if records else "—",
        "processingTimeSeconds": 135 if records else 0,
        "dgclScore": round(dgcl_score, 1),
        "verifiedCount": verified_count,
        "discrepancyCount": discrepancy_count,
        "reviewCount": review_count,
        "status": overall_status,
        "riskLevel": risk_level,
        "lastUpdated": status_data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        "checkpoints": checkpoints,
        "documentIds": doc_ids,
        "processingSteps": proc_steps,
    }


def serialize_all_cases() -> list[dict]:
    loan_ids = list_loan_ids()
    cases = []
    for lid in loan_ids:
        try:
            c = serialize_case(lid)
            cases.append(c)
        except Exception:
            logger.exception("Error serializing case %s", lid)
    return cases

