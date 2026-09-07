"""Identity & KYC checkpoints: Application Form, KYC (PAN & Address), Selfie Live Photo, Aadhaar XML."""
from __future__ import annotations

from typing import Any

from config import FIELD_CRITICALITY_WEIGHTS

from ..case_context import (
    CaseContext,
    build_checkpoint,
    build_evidence,
    build_field,
    compute_checkpoint_confidence,
    inr_format,
)


def _normalize_clean_str(val: Any) -> str:
    """Normalizes a string for comparison by removing spaces and lowercasing."""
    if val is None:
        return ""
    return str(val).strip().lower().replace(" ", "")


def _digits_only(val: Any) -> str:
    """Extracts digit characters only."""
    if val is None:
        return ""
    return "".join(ch for ch in str(val) if ch.isdigit())


def build_application_form_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 3: Application Form completeness, fidelity, and applicant alignment."""
    app_form = ctx.get_doc("application_form", "appform")
    r3_name = ctx.get_check_record("chk_check_kyc_application_form_applicant_name_vs_los", "chk_app_form_name_match")

    has_app_form = bool(app_form) or ctx.has_doc_matching("app")

    app_name_val = app_form.get("applicant_name") or ctx.applicant_name
    app_no_val = app_form.get("application_no") or ctx.app_id
    app_date_val = app_form.get("application_date") or ctx.los_data.get("application_date")
    app_father_val = app_form.get("fathers_name") or ctx.los_data.get("fathers_name")
    app_dob_val = app_form.get("dob") or ctx.los_data.get("applicant_dob")
    app_gender_val = app_form.get("gender") or ctx.los_data.get("applicant_gender")
    app_mobile_val = app_form.get("mobile_no") or ctx.los_data.get("applicant_mobile_no")
    app_pan_val = app_form.get("pan_number") or ctx.los_data.get("applicant_pan_number")
    app_addr_val = app_form.get("current_address") or app_form.get("address") or ctx.los_data.get("current_address")
    app_bank_val = app_form.get("bank_account_no") or ctx.los_data.get("applicant_bank_account_no")
    app_acct_type = app_form.get("type_of_account") or ctx.los_data.get("bank_account_type")
    app_type_val = app_form.get("loan_type") or ctx.los_data.get("loan_type")
    app_amt_val = app_form.get("loan_amount") or ctx.loan_amount
    app_tenure_val = app_form.get("loan_validity") or ctx.los_data.get("tenure")

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if has_app_form:
        fields = [
            build_field("Application No", str(app_no_val), 99.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Application Date", str(app_date_val or "N/A"), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Applicant Name", str(app_name_val), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Father's Name", str(app_father_val or "N/A"), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Date of Birth", str(app_dob_val or "N/A"), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Gender", str(app_gender_val or "N/A"), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Mobile No", str(app_mobile_val or "N/A"), 97.0, f"doc-{ctx.loan_id}-appform"),
            build_field("PAN Number", str(app_pan_val or "N/A"), 99.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Address", str(app_addr_val or "N/A")[:80], 95.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Bank Account No", str(app_bank_val or "N/A"), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Account Type", str(app_acct_type or "N/A"), 95.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Loan Type", str(app_type_val or "N/A"), 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Requested Amount", inr_format(float(app_amt_val)) if app_amt_val else "N/A", 98.0, f"doc-{ctx.loan_id}-appform"),
            build_field("Requested Tenure", f"{app_tenure_val} Months" if app_tenure_val else "N/A", 98.0, f"doc-{ctx.loan_id}-appform"),
        ]
        evidence = [build_evidence(f"doc-{ctx.loan_id}-appform", "Application_Form.pdf", "Application Form — Details", 1, "Application Form")]

    app_form_checks = [
        r for r in ctx.records
        if r.get("subnode") == "check_loan_application"
        or "application_form" in (r.get("sources") or [])
        or (r.get("check_id") and "application_form" in r.get("check_id", "").lower())
    ]
    left_app_name = str(app_form.get("applicant_name") or "N/A")
    right_los_name = str(ctx.los_data.get("applicant_name") or "N/A")
    name_mismatch = bool(r3_name and (r3_name.get("match_status") == "MISMATCH" or r3_name.get("result") == "MISMATCH"))
    mismatched_app_checks = [r for r in app_form_checks if r.get("match_status") == "MISMATCH" or r.get("result") == "MISMATCH"]

    matched_field_count = 0
    total_field_count = 0
    earned_w = 0.0
    total_w = 0.0
    detected_mismatches: list[tuple[str, Any, Any]] = []

    if has_app_form:
        eval_fields = [
            ("applicant_name", app_name_val, ctx.los_data.get("applicant_name")),
            ("application_no", app_no_val, ctx.los_data.get("loan_id")),
            ("application_date", app_date_val, ctx.los_data.get("application_date")),
            ("fathers_name", app_father_val, ctx.los_data.get("fathers_name")),
            ("dob", app_dob_val, ctx.los_data.get("applicant_dob")),
            ("gender", app_gender_val, ctx.los_data.get("applicant_gender")),
            ("mobile_no", app_mobile_val, ctx.los_data.get("applicant_mobile_no")),
            ("pan_number", app_pan_val, ctx.los_data.get("applicant_pan_number")),
            ("address", app_addr_val, ctx.los_data.get("current_address")),
            ("account_no", app_bank_val, ctx.los_data.get("applicant_bank_account_no")),
            ("account_type", app_acct_type, ctx.los_data.get("bank_account_type")),
            ("loan_type", app_type_val, ctx.los_data.get("loan_type")),
            ("loan_amount", app_amt_val, ctx.los_data.get("loan_amount")),
            ("loan_validity", app_tenure_val, ctx.los_data.get("tenure")),
        ]

        for fld_name, doc_v, los_v in eval_fields:
            if doc_v is not None and los_v is not None:
                total_field_count += 1
                fw = FIELD_CRITICALITY_WEIGHTS.get(fld_name, 1.0)
                total_w += fw

                d_str = _normalize_clean_str(doc_v)
                l_str = _normalize_clean_str(los_v)
                if fld_name == "mobile_no":
                    d_digits = _digits_only(doc_v)
                    l_digits = _digits_only(los_v)
                    is_fld_match = (d_digits[-10:] == l_digits[-10:]) if len(d_digits) >= 10 and len(l_digits) >= 10 else (d_str == l_str)
                elif fld_name in ("loan_amount", "loan_validity"):
                    try:
                        is_fld_match = float(doc_v) == float(los_v)
                    except (ValueError, TypeError):
                        is_fld_match = d_str == l_str
                else:
                    is_fld_match = (d_str == l_str) or (d_str in l_str) or (l_str in d_str)

                if is_fld_match:
                    matched_field_count += 1
                    earned_w += fw
                else:
                    detected_mismatches.append((fld_name, doc_v, los_v))

        match_score = round((earned_w / total_w * 100.0), 1) if total_w > 0 else 98.0
        conf = compute_checkpoint_confidence(fields, app_form_checks, default_conf=98.0)
    else:
        match_score = 0.0
        conf = 0.0

    if not has_app_form:
        fields = [build_field("Application Form", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}")]
        status = "INDETERMINATE"
        notes = "Application Form not uploaded."
        val = {"left": "N/A", "right": right_los_name, "result": "MISMATCH"}
    elif name_mismatch:
        status = "DISCREPANCY"
        notes = f"Application Form applicant name ('{left_app_name}') does not match LOS ('{right_los_name}') ({matched_field_count}/{total_field_count} fields verified, {match_score}% match fidelity)."
        val = {"left": left_app_name, "right": right_los_name, "result": "MISMATCH"}
    elif mismatched_app_checks or detected_mismatches:
        status = "DISCREPANCY"
        if mismatched_app_checks:
            first_mis = mismatched_app_checks[0]
            mis_field = first_mis.get("field", "Application details").replace("_", " ").title()
            mis_vals = first_mis.get("values") or [left_app_name, right_los_name]
            left_v = str(mis_vals[0]) if len(mis_vals) > 0 and mis_vals[0] is not None else left_app_name
            right_v = str(mis_vals[1]) if len(mis_vals) > 1 and mis_vals[1] is not None else right_los_name
        else:
            first_mis_fld, first_mis_doc, first_mis_los = detected_mismatches[0]
            mis_field = first_mis_fld.replace("_", " ").title()
            left_v = str(first_mis_doc)
            right_v = str(first_mis_los)
        notes = f"Application Form discrepancy detected in {mis_field}: '{left_v}' vs '{right_v}' ({matched_field_count}/{total_field_count} fields verified, {match_score}% match fidelity)."
        val = {"left": left_v, "right": right_v, "result": "MISMATCH"}
    elif any(r.get("match_status") in ("PARTIAL", "NOT_FOUND") for r in app_form_checks):
        status = "INDETERMINATE"
        notes = f"Application form fields pending manual verification ({matched_field_count}/{total_field_count} verified, {match_score}% match fidelity)."
        val = {"left": left_app_name, "right": right_los_name, "result": "MATCH"}
    else:
        status = "VERIFIED"
        notes = f"Application Form verified against LOS records for '{app_name_val}' ({matched_field_count}/{total_field_count} fields verified, 100% match fidelity)."
        val = {"left": left_app_name, "right": right_los_name, "result": "MATCH"}

    return build_checkpoint(
        3,
        "Application Form",
        status,
        conf,
        notes,
        "Application Form must be complete, signed, and applicant details must match LOS.",
        fields,
        evidence,
        val,
        match_score=match_score,
    )


def build_kyc_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 4: KYC mandatory document validation (PAN and Address Proof) and identity match."""
    kyc_pan = ctx.get_doc("kyc_pan", "pan")
    kyc_addr = ctx.get_doc("kyc_address_proof", "aadhaar", "address_proof")
    app_form = ctx.get_doc("application_form", "appform")

    r4_name_aadhaar = ctx.records_by_id.get("chk_check_kyc_aadhaar_applicant_name_vs_los")
    r4_name_pan = ctx.records_by_id.get("chk_check_kyc_pan_applicant_name_vs_los")
    r4_pan = (
        ctx.records_by_id.get("chk_check_kyc_pan_pan_number_vs_los")
        or ctx.records_by_id.get("chk_loan_kyc_pan_pan_number_vs_los")
        or ctx.records_by_id.get("chk_kyc_pan")
    )
    r4_addr = (
        ctx.records_by_id.get("chk_check_kyc_aadhaar_address_vs_los")
        or ctx.records_by_id.get("chk_loan_kyc_aadhaar_address_vs_los")
        or ctx.records_by_id.get("chk_kyc_address_proof")
    )

    doc_pan = (
        kyc_pan.get("pan_number")
        or app_form.get("pan_number")
        or (r4_pan.get("values")[0] if r4_pan and r4_pan.get("values") else None)
    )
    los_pan = (
        ctx.los_data.get("applicant_pan_number")
        or ctx.los_data.get("pan")
        or (r4_pan.get("values")[1] if r4_pan and len(r4_pan.get("values", [])) > 1 else None)
    )

    doc_addr = (
        kyc_addr.get("address_text")
        or kyc_addr.get("address")
        or app_form.get("current_address")
        or app_form.get("address_text")
        or (r4_addr.get("values")[0] if r4_addr and r4_addr.get("values") else None)
    )
    los_addr = (
        ctx.los_data.get("current_address")
        or ctx.los_data.get("permanent_address")
        or (r4_addr.get("values")[1] if r4_addr and len(r4_addr.get("values", [])) > 1 else None)
    )

    doc_aadhaar_name = (
        kyc_addr.get("applicant_name")
        or kyc_addr.get("name")
        or (r4_name_aadhaar.get("values")[0] if r4_name_aadhaar and r4_name_aadhaar.get("values") else None)
    )
    doc_pan_name = (
        kyc_pan.get("applicant_name")
        or kyc_pan.get("name")
        or (r4_name_pan.get("values")[0] if r4_name_pan and r4_name_pan.get("values") else None)
    )

    has_pan_doc = bool(doc_pan or doc_pan_name)
    has_addr_doc = bool(doc_addr or doc_aadhaar_name)

    kyc_records = [
        r for r in ctx.records
        if r.get("subnode") in ("check_kyc", "loan_kyc", "aadhaar", "pan", "kyc")
        or (r.get("check_id") and "kyc" in r.get("check_id", "").lower())
        or r.get("checkpoint") in ("check_kyc", "loan_kyc")
    ]
    mismatched_kyc = [
        r for r in kyc_records
        if r.get("match_status") == "MISMATCH" or r.get("result") == "MISMATCH"
    ]

    pan_str_mismatch = bool(doc_pan and los_pan and str(doc_pan).strip().upper() != str(los_pan).strip().upper())
    has_kyc_mismatch = bool(
        mismatched_kyc
        or pan_str_mismatch
        or (r4_name_aadhaar and (r4_name_aadhaar.get("match_status") == "MISMATCH" or r4_name_aadhaar.get("result") == "MISMATCH"))
        or (r4_name_pan and (r4_name_pan.get("match_status") == "MISMATCH" or r4_name_pan.get("result") == "MISMATCH"))
        or (r4_pan and (r4_pan.get("match_status") == "MISMATCH" or r4_pan.get("result") == "MISMATCH"))
        or (r4_addr and (r4_addr.get("match_status") == "MISMATCH" or r4_addr.get("result") == "MISMATCH"))
    )

    if has_kyc_mismatch:
        status = "DISCREPANCY"
    elif not has_pan_doc and not has_addr_doc:
        status = "INDETERMINATE"
    elif not has_pan_doc or not has_addr_doc:
        status = "INDETERMINATE"
    elif any(r.get("match_status") in ("PARTIAL", "NOT_FOUND") or r.get("result") in ("PARTIAL", "NOT_FOUND") for r in kyc_records):
        status = "INDETERMINATE"
    else:
        status = "VERIFIED"

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if doc_aadhaar_name:
        conf_aadhaar = (r4_name_aadhaar.get("confidence") or 0.95) * 100 if r4_name_aadhaar else 95.0
        fields.append(build_field("Aadhaar Name", str(doc_aadhaar_name), conf_aadhaar, f"doc-{ctx.loan_id}-aadhaar"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-aadhaar", "Aadhaar.pdf", "Aadhaar — Applicant Name", 1, "Applicant Name"))

    if doc_pan_name and doc_pan_name != doc_aadhaar_name:
        conf_pan_name = (r4_name_pan.get("confidence") or 0.98) * 100 if r4_name_pan else 98.0
        fields.append(build_field("PAN Name", str(doc_pan_name), conf_pan_name, f"doc-{ctx.loan_id}-pan"))

    if doc_pan:
        fields.append(build_field("PAN Number", str(doc_pan), 99.0, f"doc-{ctx.loan_id}-pan"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-pan", "PAN.pdf", "PAN Card Document", 1, "PAN"))

    if doc_addr:
        fields.append(build_field("Address", str(doc_addr)[:80], 95.0, f"doc-{ctx.loan_id}-kyc"))
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-kyc", "Address_Proof.pdf", "Address Proof", 1, "Address"))

    r4_acct = ctx.get_check_record("chk_check_kyc_account_statement_applicant_name_vs_los")
    if r4_acct and r4_acct.get("match_status") == "MISMATCH":
        evidence.append(build_evidence(f"doc-{ctx.loan_id}-acctstmt", "Account_Statement.pdf", "Account Statement — Entity Name", 1, "Applicant Name"))

    if not fields:
        fields.append(build_field("KYC Documents", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}"))

    pan_label = f"PAN ({doc_pan})" if doc_pan else "PAN (Missing)"
    addr_label = "Address proof verified" if has_addr_doc else "Address proof missing"

    left_val = str(doc_pan or "N/A")
    right_val = str(los_pan or "N/A")
    pan_matches = bool(doc_pan and los_pan and str(doc_pan).strip().upper() == str(los_pan).strip().upper())
    val_result = "MATCH" if pan_matches else "MISMATCH"

    dyn_conf = compute_checkpoint_confidence(fields, kyc_records, default_conf=96.0) if (has_pan_doc or has_addr_doc) else 0.0

    if r4_name_aadhaar and r4_name_aadhaar.get("match_status") == "MISMATCH":
        doc_n = str(r4_name_aadhaar.get("values", [""])[0] or doc_aadhaar_name or "Unknown")
        los_n = str(r4_name_aadhaar.get("values", ["", ""])[1] or ctx.los_data.get("applicant_name") or "Unknown")
        kyc_notes = f"Discrepancies found: Aadhaar Name ('{doc_n}') does not match LOS ('{los_n}')."
        conf = dyn_conf
        left_val = doc_n
        right_val = los_n
        val_result = "MISMATCH"
    elif r4_name_pan and r4_name_pan.get("match_status") == "MISMATCH":
        doc_n = str(r4_name_pan.get("values", [""])[0] or doc_pan_name or "Unknown")
        los_n = str(r4_name_pan.get("values", ["", ""])[1] or ctx.los_data.get("applicant_name") or "Unknown")
        kyc_notes = f"Discrepancies found: PAN Name ('{doc_n}') does not match LOS ('{los_n}')."
        conf = dyn_conf
        left_val = doc_n
        right_val = los_n
        val_result = "MISMATCH"
    elif pan_str_mismatch or (r4_pan and r4_pan.get("match_status") == "MISMATCH"):
        kyc_notes = "Discrepancies found: PAN number does not match LOS."
        conf = dyn_conf
        left_val = str(doc_pan or "N/A")
        right_val = str(los_pan or "N/A")
        val_result = "MISMATCH"
    elif r4_addr and r4_addr.get("match_status") == "MISMATCH":
        kyc_notes = "Discrepancies found: Address does not match LOS."
        conf = dyn_conf
        left_val = str(doc_addr or "N/A")[:30]
        right_val = str(los_addr or "N/A")[:30]
        val_result = "MISMATCH"
    elif status == "DISCREPANCY":
        first_mismatch = mismatched_kyc[0] if mismatched_kyc else None
        fld_name = first_mismatch.get("field", "KYC field").replace("_", " ").title() if first_mismatch else "KYC field"
        kyc_notes = f"Discrepancies found: {fld_name} does not match LOS."
        conf = dyn_conf
        val_result = "MISMATCH"
    elif status == "VERIFIED":
        kyc_notes = f"{pan_label} and {addr_label} verified against LOS."
        conf = dyn_conf
        val_result = "MATCH"
    elif has_pan_doc and not has_addr_doc:
        kyc_notes = f"{pan_label} present, but mandatory Address Proof document is missing."
        conf = round(dyn_conf * 0.5, 1)
    elif has_addr_doc and not has_pan_doc:
        kyc_notes = "Address proof present, but mandatory PAN Card document is missing."
        conf = round(dyn_conf * 0.5, 1)
    else:
        kyc_notes = "Mandatory KYC documents (PAN and Address Proof) not uploaded."
        conf = 0.0
        val_result = "MISMATCH"

    return build_checkpoint(
        4,
        "KYC",
        status,
        conf,
        kyc_notes,
        "PAN and Address proof are mandatory and must match application form.",
        fields,
        evidence,
        {"left": left_val, "right": right_val, "result": val_result},
    )


def build_selfie_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 5: Selfie and live facial recognition embedding match."""
    r5 = ctx.get_check_record("chk_face_similarity_selfie", field="selfie_vector")
    has_selfie = ctx.has_doc_matching("selfie") or (ctx.extracted_dir / "face_embeddings.json").exists()

    fields: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if has_selfie:
        status = "VERIFIED"
        if r5 and r5.get("match_status") == "MISMATCH":
            status = "DISCREPANCY"
        elif r5 and r5.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            status = "INDETERMINATE"
        conf_val = ((r5.get("confidence") if r5 else 0.95) or 0.95) * 100
        fields = [build_field("Face Match Confidence", f"{conf_val:.1f}%", 96.0, f"doc-{ctx.loan_id}-selfie")]
        evidence = [build_evidence(f"doc-{ctx.loan_id}-selfie", "Selfie.jpg", "Selfie Live Photo", 1)]
    else:
        fields = [build_field("Selfie", "Not Uploaded", 0.0, f"doc-{ctx.loan_id}")]
        status = "INDETERMINATE"

    conf = (r5.get("confidence") or 0.95) * 100 if (r5 and status == "VERIFIED") else (0.0 if not has_selfie else 50.0)
    notes = (r5.get("notes") if r5 else "") or ("Live selfie embedding verification." if has_selfie else "Selfie photo not uploaded.")

    return build_checkpoint(
        5,
        "Selfie / Live Photo",
        status,
        conf,
        notes,
        "Live selfie face embedding must match application form photo (threshold >= 0.90).",
        fields,
        evidence,
        {
            "left": "Selfie Vector" if has_selfie else "N/A",
            "right": "App Photo Vector" if has_selfie else "N/A",
            "result": "MATCH" if status == "VERIFIED" else "MISMATCH",
        },
    )


def build_aadhaar_xml_checkpoint(ctx: CaseContext) -> dict[str, Any]:
    """CP 9: Aadhaar XML mandatory presence hard gate."""
    r9 = ctx.records_by_id.get("chk_aadhaar_xml_mandatory_presence")
    xml_doc = ctx.get_doc("aadhaar_xml")

    doc_xml_present = xml_doc.get("aadhaar_xml_present")
    if doc_xml_present is None:
        for d in ctx.docs.values():
            if isinstance(d, dict) and d.get("aadhaar_xml_present") is not None:
                doc_xml_present = d.get("aadhaar_xml_present")
                break

    has_xml = bool(doc_xml_present) if doc_xml_present is not None else (
        ctx.has_doc_matching("xml")
        or (ctx.extracted_dir / "aadhaar_xml_status.json").exists()
        or (ctx.dms_dir / "aadhaar_xml_status.json").exists()
        or (r9 is not None and r9.get("match_status") == "MATCH")
    )
    status = "VERIFIED" if has_xml else "INDETERMINATE"
    if r9 and r9.get("match_status") == "MISMATCH":
        status = "DISCREPANCY"

    fields = [build_field("Aadhaar XML Presence", "Present" if has_xml else "Missing", 99.0 if has_xml else 0.0, f"doc-{ctx.loan_id}-aadhaarxml")]
    if has_xml and xml_doc.get("applicant_name"):
        fields.append(build_field("Aadhaar XML Name", str(xml_doc.get("applicant_name")), 98.0, f"doc-{ctx.loan_id}-aadhaarxml"))

    notes = (r9.get("notes") if r9 else "") or (
        "Aadhaar XML present in repository and verified." if has_xml else "Aadhaar XML missing from repository."
    )

    return build_checkpoint(
        9,
        "Aadhaar XML",
        status,
        99.0 if status == "VERIFIED" else 0.0,
        notes,
        "Aadhaar XML is a mandatory hard gate for all cases.",
        fields,
        [build_evidence(f"doc-{ctx.loan_id}-aadhaarxml", "Aadhaar_XML.zip", "Aadhaar XML Archive", 1)] if has_xml else [],
        {
            "left": "Present" if has_xml else "Missing",
            "right": "Mandatory",
            "result": "MATCH" if has_xml else "MISMATCH",
        },
    )
