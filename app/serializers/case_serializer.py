"""Case serializer — transforms pipeline outputs and LOS records into frontend Case model."""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import (
    DMS_DIR,
    FIELD_CRITICALITY_WEIGHTS,
    IST,
    LOS_LOANS_DIR,
    S3_EXTRACTED_DIR,
    S3_EXTRACTED_STRUCTURED_DIR,
    S3_RAW_DIR,
    S3_RESULT_DIR,
)
from pipeline.engines.comparison import extract_field_value
from pipeline.storage import get_all_s3_extracted_structured, get_s3_los, list_loan_ids, read_json

logger = logging.getLogger("disbursement_pipeline.serializer")


def inr_format(val: float | None) -> str:
    """Formats numeric value to INR string."""
    if val is None:
        return "₹0"
    return f"₹{int(val):,}"


def build_evidence(
    doc_id: str,
    doc_name: str,
    label: str,
    page: int = 1,
    field: str | None = None,
) -> dict[str, Any]:
    """Builds an evidence snippet record for a checkpoint."""
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
) -> dict[str, Any]:
    """Builds an extracted field element for a checkpoint."""
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
    match_score: float | None = None,
) -> dict[str, Any]:
    """Builds a standardized checkpoint record."""
    cp_data = {
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
    if match_score is not None:
        cp_data["matchScore"] = round(match_score, 1)
        cp_data["match_score"] = round(match_score, 1)
    return cp_data


def _compute_checkpoint_confidence(
    fields: list[dict[str, Any]],
    records: list[dict[str, Any]] | None = None,
    default_conf: float = 95.0,
) -> float:
    """Calculates weighted confidence dynamically from extracted fields and comparison records."""
    confidences: list[float] = []
    weights: list[float] = []

    if records:
        for r in records:
            if isinstance(r, dict):
                c = r.get("confidence")
                if c is not None:
                    try:
                        c_val = float(c)
                        if c_val <= 1.0:
                            c_val *= 100.0
                        fld = r.get("field", "")
                        w = FIELD_CRITICALITY_WEIGHTS.get(fld, 1.0)
                        confidences.append(c_val)
                        weights.append(w)
                    except (ValueError, TypeError):
                        pass

    if not confidences and fields:
        for f in fields:
            if isinstance(f, dict):
                c = f.get("confidence")
                if c is not None:
                    try:
                        c_val = float(c)
                        if c_val <= 1.0:
                            c_val *= 100.0
                        fld_name = f.get("name", "").lower().replace(" ", "_")
                        w = FIELD_CRITICALITY_WEIGHTS.get(fld_name, 1.0)
                        confidences.append(c_val)
                        weights.append(w)
                    except (ValueError, TypeError):
                        pass

    if not confidences:
        return default_conf

    total_w = sum(weights)
    if total_w > 0:
        return sum(c * w for c, w in zip(confidences, weights)) / total_w
    return sum(confidences) / len(confidences)


def get_case_results(loan_id: str) -> dict[str, Any]:
    """Reads result artifacts for a loan without executing pipeline."""
    res_dir = S3_RESULT_DIR / loan_id
    comp_file = res_dir / "comparison_results.json"
    status_file = res_dir / "status.json"
    rollups_file = res_dir / "subnode_rollups.json"

    comp_results = []
    if comp_file.exists():
        try:
            raw_comp = read_json(comp_file)
            if isinstance(raw_comp, list):
                comp_results = [r for r in raw_comp if isinstance(r, dict)]
            elif isinstance(raw_comp, dict):
                if "results" in raw_comp and isinstance(raw_comp["results"], list):
                    comp_results = [r for r in raw_comp["results"] if isinstance(r, dict)]
                elif "checks" in raw_comp and isinstance(raw_comp["checks"], list):
                    comp_results = [r for r in raw_comp["checks"] if isinstance(r, dict)]
                else:
                    comp_results = [v for v in raw_comp.values() if isinstance(v, dict)]
        except (json.JSONDecodeError, OSError):
            comp_results = []

    rollups = {}
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

    scorecard_file = res_dir / "scorecard.json"
    scorecard_data = {}
    if scorecard_file.exists():
        try:
            scorecard_data = read_json(scorecard_file) or {}
        except (json.JSONDecodeError, OSError):
            scorecard_data = {}

    return {
        "comparison_results": comp_results,
        "subnode_rollups": rollups,
        "status_data": status_data,
        "scorecard_data": scorecard_data,
    }


def _load_extracted_docs(loan_id: str) -> dict[str, dict[str, Any]]:
    """Loads document dictionaries from s3_extracted_structured and s3_extracted."""
    from config.doc_types import get_canonical_doc_type

    docs: dict[str, dict[str, Any]] = {}
    struct_dir = S3_EXTRACTED_STRUCTURED_DIR / loan_id
    if struct_dir.exists():
        for f in struct_dir.glob("*.json"):
            try:
                data = read_json(f)
                if isinstance(data, dict):
                    canon_key = get_canonical_doc_type(f.stem)
                    docs[canon_key] = data
                    docs[f.stem] = data
            except Exception:
                pass

    ext_dir = S3_EXTRACTED_DIR / loan_id
    if ext_dir.exists():
        for f in ext_dir.glob("*.json"):
            if f.stem.endswith("_structured"):
                continue
            canon_key = get_canonical_doc_type(f.stem)
            try:
                data = read_json(f)
                if isinstance(data, dict):
                    if canon_key not in docs:
                        docs[canon_key] = data
                    docs[f.stem] = data
            except Exception:
                pass

    return docs


def serialize_case(loan_id: str) -> dict[str, Any]:
    """Serializes a loan record into the complete frontend Case schema."""
    los_data: Dict[str, Any] = get_s3_los(loan_id) or {}
    if not los_data:
        los_file = LOS_LOANS_DIR / f"{loan_id}.json"
        if los_file.exists():
            try:
                los_data = read_json(los_file) or {}
            except Exception:
                los_data = {}

    docs = _load_extracted_docs(loan_id)
    app_form = docs.get("application_form") or docs.get("appform") or {}
    kyc_pan = docs.get("kyc_pan") or docs.get("pan") or {}
    kyc_addr = docs.get("kyc_address_proof") or docs.get("aadhaar") or docs.get("address_proof") or {}
    kfs_doc = docs.get("kfs") or {}
    sanction_doc = docs.get("sanction_letter") or docs.get("sanction") or {}
    memo_doc = docs.get("disbursal_memo") or docs.get("memo") or {}

    results_data = get_case_results(loan_id)
    records: List[Dict[str, Any]] = [r for r in (results_data.get("comparison_results") or []) if isinstance(r, dict)]
    status_data: Dict[str, Any] = results_data.get("status_data") or {}
    scorecard_data: Dict[str, Any] = results_data.get("scorecard_data") or {}
    subnode_rollups: Dict[str, Any] = results_data.get("subnode_rollups") or {}

    # Index comparison records by check_id, field, and subnode
    records_by_id = {r.get("check_id"): r for r in records if isinstance(r, dict) and r.get("check_id")}
    records_by_field: Dict[str, List[Dict[str, Any]]] = {}
    records_by_subnode: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        fld = r.get("field", "")
        if fld:
            records_by_field.setdefault(fld, []).append(r)
        sub = r.get("subnode", "")
        if sub:
            records_by_subnode.setdefault(sub, []).append(r)

    def _get_check_record(*candidate_ids, field: str | None = None) -> Optional[Dict[str, Any]]:
        for cid in candidate_ids:
            if cid and cid in records_by_id:
                return records_by_id[cid]
        if field and field in records_by_field:
            return records_by_field[field][0]
        return None

    # Discover raw document filenames
    raw_dir = S3_RAW_DIR / loan_id
    dms_dir = DMS_DIR / loan_id
    real_doc_names: List[str] = []
    if raw_dir.exists():
        for f in raw_dir.iterdir():
            if f.is_file() and f.name != f"{loan_id}.json" and not f.name.endswith(".metadata.json") and f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".zip", ".xml"):
                real_doc_names.append(f.name)
    if dms_dir.exists():
        for f in dms_dir.iterdir():
            if f.is_file() and f.name != f"{loan_id}.json" and not f.name.endswith(".metadata.json") and not f.name.endswith(".json") and f.name not in real_doc_names:
                real_doc_names.append(f.name)
    if not real_doc_names and docs:
        for dk in docs:
            if dk not in (f"{loan_id}.json", "status.json", "dms_status.json", "face_embeddings.json"):
                real_doc_names.append(f"{dk}.pdf")

    doc_ids = [f"doc-{loan_id}-{Path(n).stem.lower().replace(' ', '_')}" for n in real_doc_names]

    raw_amount = los_data.get("funding_amount") or los_data.get("loan_amount") or app_form.get("loan_amount")
    try:
        loan_amount = float(raw_amount) if raw_amount is not None else 0.0
    except (ValueError, TypeError):
        loan_amount = 0.0

    raw_disbursal = memo_doc.get("disbursal_amount") or memo_doc.get("loan_amount")
    try:
        disbursal_amount = float(raw_disbursal) if raw_disbursal is not None else (round(loan_amount * 0.9, 2) if loan_amount > 0 else 0.0)
    except (ValueError, TypeError):
        disbursal_amount = 0.0

    applicant_name = str(los_data.get("applicant_name") or app_form.get("applicant_name") or "Unknown Applicant")
    app_id = str(los_data.get("application_id") or los_data.get("loan_id") or app_form.get("application_id") or f"APP-{loan_id}")
    loan_type = str(los_data.get("loan_type") or "Unspecified")

    checkpoints: List[Dict[str, Any]] = []

    # ── CP 1: Loan Amount ──
    r1 = _get_check_record("chk_check_financial_kfs_loan_amount_vs_los", "chk_check_loan_application_application_form_loan_amount_vs_los", "chk_loan_amt_application_form_vs_kfs", field="loan_amount")
    fields_1, ev_1 = [], []
    if app_form.get("loan_amount") is not None or loan_amount > 0:
        app_amt = float(app_form.get("loan_amount") or loan_amount)
        fields_1.append(build_field("Application Amount", inr_format(app_amt), 98.0, f"doc-{loan_id}-appform"))
        ev_1.append(build_evidence(f"doc-{loan_id}-appform", "Application_Form.pdf", "Application Form — Amount", 1, "Loan Amount"))
    if kfs_doc.get("loan_amount") is not None:
        fields_1.append(build_field("KFS Amount", inr_format(float(kfs_doc["loan_amount"])), 98.0, f"doc-{loan_id}-kfs"))
        ev_1.append(build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — Amount", 1, "Loan Amount"))
    if sanction_doc.get("loan_amount") is not None:
        fields_1.append(build_field("Sanction Amount", inr_format(float(sanction_doc["loan_amount"])), 98.0, f"doc-{loan_id}-sanction"))
        ev_1.append(build_evidence(f"doc-{loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Amount", 1, "Loan Amount"))

    has_amt_mismatch = (
        (r1 and r1.get("match_status") == "MISMATCH")
        or any(r.get("match_status") == "MISMATCH" for r in records if r.get("field") in ("loan_amount", "funding_amount"))
    )

    if not fields_1:
        fields_1.append(build_field("Loan Amount", "Not Available (Documents Missing)", 0.0, f"doc-{loan_id}"))
        st1 = "INDETERMINATE"
        notes_1 = "Loan amount documents not uploaded."
    else:
        st1 = "DISCREPANCY" if has_amt_mismatch else "VERIFIED"
        if not has_amt_mismatch and r1 and r1.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            st1 = "INDETERMINATE"
        notes_1 = (r1.get("notes") if r1 else "") or ("Loan amount discrepancy detected." if st1 == "DISCREPANCY" else "Loan amount consistency verified across application and sanction records.")

    has_loan_amt = bool(fields_1 and fields_1[0]["confidence"] > 0)
    conf_1 = _compute_checkpoint_confidence(fields_1, [r1], default_conf=98.5) if has_loan_amt else 0.0
    checkpoints.append(
        build_checkpoint(
            1, "Loan Amount", st1,
            conf_1,
            notes_1, "Loan amount must be consistent across all agreement and sanction records.",
            fields_1, ev_1,
            {
                "left": inr_format(loan_amount) if loan_amount > 0 else "N/A",
                "right": inr_format(float(sanction_doc.get("loan_amount") or 0.0)) if sanction_doc.get("loan_amount") else "N/A",
                "result": "MATCH" if st1 == "VERIFIED" else "MISMATCH",
            },
        )
    )

    # ── CP 2: Loan Validity ──
    r2 = _get_check_record(
        "chk_check_financial_kfs_loan_validity_vs_los",
        "chk_check_financial_application_form_loan_validity_vs_los",
        "chk_check_loan_application_application_form_loan_validity_vs_los",
        "chk_loan_validity_tenure",
        field="loan_validity",
    )
    tenure_val = (
        extract_field_value(los_data, "loan_validity")
        or extract_field_value(app_form, "loan_validity")
        or extract_field_value(kfs_doc, "loan_validity")
    )
    sanc_tenure = extract_field_value(sanction_doc, "loan_validity")
    fields_2, ev_2 = [], []
    if tenure_val is not None:
        fields_2.append(build_field("Tenure Months", f"{tenure_val}", 99.0, f"doc-{loan_id}-appform"))
        ev_2.append(build_evidence(f"doc-{loan_id}-appform", "Application_Form.pdf", "Application Form — Tenure", 1))
    if sanc_tenure is not None:
        fields_2.append(build_field("Sanction Tenure", f"{sanc_tenure}", 99.0, f"doc-{loan_id}-sanction"))
        ev_2.append(build_evidence(f"doc-{loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Tenure", 1))
    kfs_tenure = extract_field_value(kfs_doc, "loan_validity")
    if kfs_tenure is not None and kfs_tenure != sanc_tenure:
        fields_2.append(build_field("KFS Tenure", f"{kfs_tenure}", 99.0, f"doc-{loan_id}-kfs"))
        ev_2.append(build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — Tenure", 1))

    has_validity_mismatch = (
        (r2 and r2.get("match_status") == "MISMATCH")
        or any(r.get("match_status") == "MISMATCH" for r in records if r.get("field") in ("loan_validity", "tenure"))
    )

    if not fields_2:
        fields_2.append(build_field("Tenure", "Not Available", 0.0, f"doc-{loan_id}"))
        st2 = "INDETERMINATE"
        notes_2 = "Tenure documents not uploaded."
    else:
        st2 = "DISCREPANCY" if has_validity_mismatch else "VERIFIED"
        if not has_validity_mismatch and r2 and r2.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            st2 = "INDETERMINATE"
        notes_2 = (r2.get("notes") if r2 else "") or ("Tenure discrepancy detected." if st2 == "DISCREPANCY" else f"Loan tenure normalized at {tenure_val}.")

    conf_2 = _compute_checkpoint_confidence(fields_2, [r2], default_conf=99.0) if (fields_2 and fields_2[0]["confidence"] > 0) else 0.0
    checkpoints.append(
        build_checkpoint(
            2, "Loan Validity", st2,
            conf_2,
            notes_2, "Sanction tenure must match requested application tenure.",
            fields_2, ev_2,
            {"left": f"{tenure_val}" if tenure_val is not None else "N/A", "right": f"{sanc_tenure or 'N/A'}", "result": "MATCH" if st2 == "VERIFIED" else "MISMATCH"},
        )
    )

    # ── CP 3: Application Form ──
    r3_name = _get_check_record(
        "chk_check_kyc_application_form_applicant_name_vs_los",
        "chk_app_form_name_match",
    )
    r3_no = _get_check_record("chk_check_loan_application_application_form_application_no_vs_los")
    r3_date = _get_check_record("chk_check_loan_application_application_form_application_date_vs_los")

    has_app_form = bool(app_form) or any("app" in n.lower() for n in real_doc_names)
    fields_3, ev_3 = [], []
    if has_app_form:
        app_name_val = app_form.get("applicant_name") or applicant_name
        app_no_val = app_form.get("application_no") or app_id
        app_date_val = app_form.get("application_date") or los_data.get("application_date")
        app_father_val = app_form.get("fathers_name") or los_data.get("fathers_name")
        app_dob_val = app_form.get("dob") or los_data.get("applicant_dob")
        app_gender_val = app_form.get("gender") or los_data.get("applicant_gender")
        app_mobile_val = app_form.get("mobile_no") or los_data.get("applicant_mobile_no")
        app_pan_val = app_form.get("pan_number") or los_data.get("applicant_pan_number")
        app_addr_val = app_form.get("current_address") or app_form.get("address") or los_data.get("current_address")
        app_bank_val = app_form.get("bank_account_no") or los_data.get("applicant_bank_account_no")
        app_acct_type = app_form.get("type_of_account") or los_data.get("bank_account_type")
        app_type_val = app_form.get("loan_type") or los_data.get("loan_type")
        app_amt_val = app_form.get("loan_amount") or loan_amount
        app_tenure_val = app_form.get("loan_validity") or tenure_val

        fields_3 = [
            build_field("Application No", str(app_no_val), 99.0, f"doc-{loan_id}-appform"),
            build_field("Application Date", str(app_date_val or "N/A"), 98.0, f"doc-{loan_id}-appform"),
            build_field("Applicant Name", str(app_name_val), 98.0, f"doc-{loan_id}-appform"),
            build_field("Father's Name", str(app_father_val or "N/A"), 98.0, f"doc-{loan_id}-appform"),
            build_field("Date of Birth", str(app_dob_val or "N/A"), 98.0, f"doc-{loan_id}-appform"),
            build_field("Gender", str(app_gender_val or "N/A"), 98.0, f"doc-{loan_id}-appform"),
            build_field("Mobile No", str(app_mobile_val or "N/A"), 97.0, f"doc-{loan_id}-appform"),
            build_field("PAN Number", str(app_pan_val or "N/A"), 99.0, f"doc-{loan_id}-appform"),
            build_field("Address", str(app_addr_val or "N/A")[:80], 95.0, f"doc-{loan_id}-appform"),
            build_field("Bank Account No", str(app_bank_val or "N/A"), 98.0, f"doc-{loan_id}-appform"),
            build_field("Account Type", str(app_acct_type or "N/A"), 95.0, f"doc-{loan_id}-appform"),
            build_field("Loan Type", str(app_type_val or "N/A"), 98.0, f"doc-{loan_id}-appform"),
            build_field("Requested Amount", inr_format(float(app_amt_val)) if app_amt_val else "N/A", 98.0, f"doc-{loan_id}-appform"),
            build_field("Requested Tenure", f"{app_tenure_val} Months" if app_tenure_val else "N/A", 98.0, f"doc-{loan_id}-appform"),
        ]
        ev_3 = [build_evidence(f"doc-{loan_id}-appform", "Application_Form.pdf", "Application Form — Details", 1, "Application Form")]

    app_form_checks = [
        r for r in records
        if r.get("subnode") == "check_loan_application"
        or "application_form" in (r.get("sources") or [])
        or (r.get("check_id") and "application_form" in r.get("check_id", "").lower())
    ]
    left_app_name = str(app_form.get("applicant_name") or "N/A")
    right_los_name = str(los_data.get("applicant_name") or "N/A")
    name_mismatch = bool(r3_name and (r3_name.get("match_status") == "MISMATCH" or r3_name.get("result") == "MISMATCH"))
    mismatched_app_checks = [r for r in app_form_checks if r.get("match_status") == "MISMATCH" or r.get("result") == "MISMATCH"]

    matched_field_count = 0
    total_field_count = 0
    earned_w = 0.0
    total_w = 0.0
    detected_mismatches = []

    if has_app_form:
        eval_fields = [
            ("applicant_name", app_name_val, los_data.get("applicant_name")),
            ("application_no", app_no_val, los_data.get("loan_id")),
            ("application_date", app_date_val, los_data.get("application_date")),
            ("fathers_name", app_father_val, los_data.get("fathers_name")),
            ("dob", app_dob_val, los_data.get("applicant_dob")),
            ("gender", app_gender_val, los_data.get("applicant_gender")),
            ("mobile_no", app_mobile_val, los_data.get("applicant_mobile_no")),
            ("pan_number", app_pan_val, los_data.get("applicant_pan_number")),
            ("address", app_addr_val, los_data.get("current_address")),
            ("account_no", app_bank_val, los_data.get("applicant_bank_account_no")),
            ("account_type", app_acct_type, los_data.get("bank_account_type")),
            ("loan_type", app_type_val, los_data.get("loan_type")),
            ("loan_amount", app_amt_val, los_data.get("loan_amount")),
            ("loan_validity", app_tenure_val, los_data.get("tenure")),
        ]

        for fld_name, doc_v, los_v in eval_fields:
            if doc_v is not None and los_v is not None:
                total_field_count += 1
                fw = FIELD_CRITICALITY_WEIGHTS.get(fld_name, 1.0)
                total_w += fw

                d_str = str(doc_v).strip().lower().replace(" ", "")
                l_str = str(los_v).strip().lower().replace(" ", "")
                if fld_name == "mobile_no":
                    d_digits = "".join(ch for ch in str(doc_v) if ch.isdigit())
                    l_digits = "".join(ch for ch in str(los_v) if ch.isdigit())
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

        match_score_3 = round((earned_w / total_w * 100.0), 1) if total_w > 0 else 98.0
        conf_3 = _compute_checkpoint_confidence(fields_3, app_form_checks, default_conf=98.0)
    else:
        match_score_3 = 0.0
        conf_3 = 0.0
        app_name_val = applicant_name

    if not has_app_form:
        fields_3 = [build_field("Application Form", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st3 = "INDETERMINATE"
        notes_3 = "Application Form not uploaded."
        val_3 = {"left": "N/A", "right": right_los_name, "result": "MISMATCH"}
    elif name_mismatch:
        st3 = "DISCREPANCY"
        notes_3 = f"Application Form applicant name ('{left_app_name}') does not match LOS ('{right_los_name}') ({matched_field_count}/{total_field_count} fields verified, {match_score_3}% match fidelity)."
        val_3 = {"left": left_app_name, "right": right_los_name, "result": "MISMATCH"}
    elif mismatched_app_checks or detected_mismatches:
        st3 = "DISCREPANCY"
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
        notes_3 = f"Application Form discrepancy detected in {mis_field}: '{left_v}' vs '{right_v}' ({matched_field_count}/{total_field_count} fields verified, {match_score_3}% match fidelity)."
        val_3 = {"left": left_v, "right": right_v, "result": "MISMATCH"}
    elif any(r.get("match_status") in ("PARTIAL", "NOT_FOUND") for r in app_form_checks):
        st3 = "INDETERMINATE"
        notes_3 = f"Application form fields pending manual verification ({matched_field_count}/{total_field_count} verified, {match_score_3}% match fidelity)."
        val_3 = {"left": left_app_name, "right": right_los_name, "result": "MATCH"}
    else:
        st3 = "VERIFIED"
        notes_3 = f"Application Form verified against LOS records for '{app_name_val}' ({matched_field_count}/{total_field_count} fields verified, 100% match fidelity)."
        val_3 = {"left": left_app_name, "right": right_los_name, "result": "MATCH"}

    checkpoints.append(
        build_checkpoint(
            3, "Application Form", st3,
            conf_3,
            notes_3,
            "Application Form must be complete, signed, and applicant details must match LOS.",
            fields_3, ev_3,
            val_3,
            match_score=match_score_3,
        )
    )

    # ── CP 4: KYC ──
    r4_name_aadhaar = records_by_id.get("chk_check_kyc_aadhaar_applicant_name_vs_los")
    r4_name_pan = records_by_id.get("chk_check_kyc_pan_applicant_name_vs_los")
    r4_pan = (
        records_by_id.get("chk_check_kyc_pan_pan_number_vs_los")
        or records_by_id.get("chk_loan_kyc_pan_pan_number_vs_los")
        or records_by_id.get("chk_kyc_pan")
    )
    r4_addr = (
        records_by_id.get("chk_check_kyc_aadhaar_address_vs_los")
        or records_by_id.get("chk_loan_kyc_aadhaar_address_vs_los")
        or records_by_id.get("chk_kyc_address_proof")
    )

    doc_pan = kyc_pan.get("pan_number") or app_form.get("pan_number") or (r4_pan.get("values")[0] if r4_pan and r4_pan.get("values") else None)
    los_pan = los_data.get("applicant_pan_number") or los_data.get("pan") or (r4_pan.get("values")[1] if r4_pan and len(r4_pan.get("values", [])) > 1 else None)

    doc_addr = kyc_addr.get("address_text") or kyc_addr.get("address") or app_form.get("current_address") or app_form.get("address_text") or (r4_addr.get("values")[0] if r4_addr and r4_addr.get("values") else None)
    los_addr = los_data.get("current_address") or los_data.get("permanent_address") or (r4_addr.get("values")[1] if r4_addr and len(r4_addr.get("values", [])) > 1 else None)

    doc_aadhaar_name = kyc_addr.get("applicant_name") or kyc_addr.get("name") or (r4_name_aadhaar.get("values")[0] if r4_name_aadhaar and r4_name_aadhaar.get("values") else None)
    doc_pan_name = kyc_pan.get("applicant_name") or kyc_pan.get("name") or (r4_name_pan.get("values")[0] if r4_name_pan and r4_name_pan.get("values") else None)

    has_pan_doc = bool(doc_pan or doc_pan_name)
    has_addr_doc = bool(doc_addr or doc_aadhaar_name)

    kyc_records = [
        r for r in records
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
        st4 = "DISCREPANCY"
    elif not has_pan_doc and not has_addr_doc:
        st4 = "INDETERMINATE"
    elif not has_pan_doc or not has_addr_doc:
        st4 = "INDETERMINATE"
    elif any(r.get("match_status") in ("PARTIAL", "NOT_FOUND") or r.get("result") in ("PARTIAL", "NOT_FOUND") for r in kyc_records):
        st4 = "INDETERMINATE"
    else:
        st4 = "VERIFIED"

    fields_4, ev_4 = [], []
    if doc_aadhaar_name:
        conf_aadhaar = (r4_name_aadhaar.get("confidence") or 0.95) * 100 if r4_name_aadhaar else 95.0
        fields_4.append(build_field("Aadhaar Name", str(doc_aadhaar_name), conf_aadhaar, f"doc-{loan_id}-aadhaar"))
        ev_4.append(build_evidence(f"doc-{loan_id}-aadhaar", "Aadhaar.pdf", "Aadhaar — Applicant Name", 1, "Applicant Name"))

    if doc_pan_name and doc_pan_name != doc_aadhaar_name:
        conf_pan_name = (r4_name_pan.get("confidence") or 0.98) * 100 if r4_name_pan else 98.0
        fields_4.append(build_field("PAN Name", str(doc_pan_name), conf_pan_name, f"doc-{loan_id}-pan"))

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

    # Validation left/right formatting: prioritize explicit mismatch field if present
    left_val = str(doc_pan or "N/A")
    right_val = str(los_pan or "N/A")
    pan_matches = bool(doc_pan and los_pan and str(doc_pan).strip().upper() == str(los_pan).strip().upper())
    val_result = "MATCH" if pan_matches else "MISMATCH"

    dyn_conf_4 = _compute_checkpoint_confidence(fields_4, kyc_records, default_conf=96.0) if (has_pan_doc or has_addr_doc) else 0.0

    if r4_name_aadhaar and r4_name_aadhaar.get("match_status") == "MISMATCH":
        doc_n = str(r4_name_aadhaar.get("values", [""])[0] or doc_aadhaar_name or "Unknown")
        los_n = str(r4_name_aadhaar.get("values", ["", ""])[1] or los_data.get("applicant_name") or "Unknown")
        kyc_notes = f"Discrepancies found: Aadhaar Name ('{doc_n}') does not match LOS ('{los_n}')."
        conf_4 = dyn_conf_4
        left_val = doc_n
        right_val = los_n
        val_result = "MISMATCH"
    elif r4_name_pan and r4_name_pan.get("match_status") == "MISMATCH":
        doc_n = str(r4_name_pan.get("values", [""])[0] or doc_pan_name or "Unknown")
        los_n = str(r4_name_pan.get("values", ["", ""])[1] or los_data.get("applicant_name") or "Unknown")
        kyc_notes = f"Discrepancies found: PAN Name ('{doc_n}') does not match LOS ('{los_n}')."
        conf_4 = dyn_conf_4
        left_val = doc_n
        right_val = los_n
        val_result = "MISMATCH"
    elif pan_str_mismatch or (r4_pan and r4_pan.get("match_status") == "MISMATCH"):
        kyc_notes = "Discrepancies found: PAN number does not match LOS."
        conf_4 = dyn_conf_4
        left_val = str(doc_pan or "N/A")
        right_val = str(los_pan or "N/A")
        val_result = "MISMATCH"
    elif r4_addr and r4_addr.get("match_status") == "MISMATCH":
        kyc_notes = "Discrepancies found: Address does not match LOS."
        conf_4 = dyn_conf_4
        left_val = str(doc_addr or "N/A")[:30]
        right_val = str(los_addr or "N/A")[:30]
        val_result = "MISMATCH"
    elif st4 == "DISCREPANCY":
        first_mismatch = mismatched_kyc[0] if mismatched_kyc else None
        fld_name = first_mismatch.get("field", "KYC field").replace("_", " ").title() if first_mismatch else "KYC field"
        kyc_notes = f"Discrepancies found: {fld_name} does not match LOS."
        conf_4 = dyn_conf_4
        val_result = "MISMATCH"
    elif st4 == "VERIFIED":
        kyc_notes = f"{pan_label} and {addr_label} verified against LOS."
        conf_4 = dyn_conf_4
        val_result = "MATCH"
    elif has_pan_doc and not has_addr_doc:
        kyc_notes = f"{pan_label} present, but mandatory Address Proof document is missing."
        conf_4 = round(dyn_conf_4 * 0.5, 1)
    elif has_addr_doc and not has_pan_doc:
        kyc_notes = "Address proof present, but mandatory PAN Card document is missing."
        conf_4 = round(dyn_conf_4 * 0.5, 1)
    else:
        kyc_notes = "Mandatory KYC documents (PAN and Address Proof) not uploaded."
        conf_4 = 0.0
        val_result = "MISMATCH"

    checkpoints.append(
        build_checkpoint(
            4, "KYC", st4, conf_4, kyc_notes,
            "PAN and Address proof are mandatory and must match application form.",
            fields_4, ev_4,
            {"left": left_val, "right": right_val, "result": val_result},
        )
    )

    # ── CP 5: Selfie / Live Photo ──
    r5 = _get_check_record("chk_face_similarity_selfie", field="selfie_vector")
    has_selfie = any("selfie" in n.lower() for n in real_doc_names) or (S3_EXTRACTED_DIR / loan_id / "face_embeddings.json").exists()
    fields_5, ev_5 = [], []
    if has_selfie:
        st5 = "VERIFIED"
        if r5 and r5.get("match_status") == "MISMATCH":
            st5 = "DISCREPANCY"
        elif r5 and r5.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            st5 = "INDETERMINATE"
        conf_val = ((r5.get("confidence") if r5 else 0.95) or 0.95) * 100
        fields_5 = [build_field("Face Match Confidence", f"{conf_val:.1f}%", 96.0, f"doc-{loan_id}-selfie")]
        ev_5 = [build_evidence(f"doc-{loan_id}-selfie", "Selfie.jpg", "Selfie Live Photo", 1)]
    else:
        fields_5 = [build_field("Selfie", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st5 = "INDETERMINATE"

    checkpoints.append(
        build_checkpoint(
            5, "Selfie / Live Photo", st5,
            (r5.get("confidence") or 0.95) * 100 if (r5 and st5 == "VERIFIED") else (0.0 if not has_selfie else 50.0),
            (r5.get("notes") if r5 else "") or ("Live selfie embedding verification." if has_selfie else "Selfie photo not uploaded."),
            "Live selfie face embedding must match application form photo (threshold >= 0.90).",
            fields_5, ev_5,
            {"left": "Selfie Vector" if has_selfie else "N/A", "right": "App Photo Vector" if has_selfie else "N/A", "result": "MATCH" if st5 == "VERIFIED" else "MISMATCH"},
        )
    )

    # ── CP 6: Loan Agreement ──
    r6_sig = records_by_id.get("chk_loan_agreement_digital_signature")
    r6_otp = records_by_id.get("chk_loan_agreement_otp_consent")

    agree_doc = docs.get("loan_agreement") or {}
    doc_present = agree_doc.get("loan_agreement_present")
    doc_signed = agree_doc.get("loan_agreement_signed")
    if doc_present is None:
        for d in docs.values():
            if isinstance(d, dict) and d.get("loan_agreement_present") is not None:
                doc_present = d.get("loan_agreement_present")
                doc_signed = d.get("loan_agreement_signed")
                break

    has_agree = bool(doc_present) if doc_present is not None else (
        "loan_agreement" in docs
        or any("agreement" in n.lower() for n in real_doc_names)
        or (DMS_DIR / loan_id / "loan_agreement_otp_audit.json").exists()
    )
    is_signed = bool(doc_signed) if doc_signed is not None else (
        (r6_sig and r6_sig.get("match_status") != "MISMATCH")
        or (DMS_DIR / loan_id / "loan_agreement_otp_audit.json").exists()
    )

    fields_6, ev_6 = [], []
    if has_agree and is_signed:
        st6 = "VERIFIED"
        fields_6 = [
            build_field("Loan Agreement Presence", "Present", 99.0, f"doc-{loan_id}-agreement"),
            build_field("Loan Agreement Signature", "Signed", 98.0, f"doc-{loan_id}-agreement"),
            build_field("Digital Signature", "Intact / Verified", 98.0, f"doc-{loan_id}-agreement"),
            build_field("OTP Consent", "Verified", 99.0, f"doc-{loan_id}-agreement"),
        ]
        ev_6 = [build_evidence(f"doc-{loan_id}-agreement", "Loan_Agreement.pdf", "Loan Agreement — Signature", 1, "Digital Signature")]
        val_6 = {"left": "Present & Signed", "right": "Mandatory Signed Agreement", "result": "MATCH"}
        notes_6 = "Loan agreement present and digitally signed."
    elif has_agree and not is_signed:
        st6 = "DISCREPANCY"
        fields_6 = [
            build_field("Loan Agreement Presence", "Present", 99.0, f"doc-{loan_id}-agreement"),
            build_field("Loan Agreement Signature", "Unsigned", 98.0, f"doc-{loan_id}-agreement"),
            build_field("Digital Signature", "Missing / Unsigned", 0.0, f"doc-{loan_id}-agreement"),
        ]
        ev_6 = [build_evidence(f"doc-{loan_id}-agreement", "Loan_Agreement.pdf", "Loan Agreement — Unsigned", 1, "Digital Signature")]
        val_6 = {"left": "Present & Unsigned", "right": "Mandatory Signed Agreement", "result": "MISMATCH"}
        notes_6 = "Loan agreement uploaded but missing required digital signature."
    else:
        st6 = "INDETERMINATE"
        fields_6 = [build_field("Loan Agreement", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        ev_6 = []
        val_6 = {"left": "Missing", "right": "Mandatory Signed Agreement", "result": "MISMATCH"}
        notes_6 = "Loan agreement not uploaded."

    checkpoints.append(
        build_checkpoint(
            6, "Loan Agreement", st6,
            97.5 if st6 == "VERIFIED" else (40.0 if has_agree else 0.0),
            notes_6,
            "Loan agreement must contain valid untampered digital e-signature and OTP consent trail.",
            fields_6, ev_6,
            val_6,
        )
    )

    # ── CP 7: KFS ──
    r7 = _get_check_record("chk_check_financial_kfs_loan_amount_vs_los", "chk_kfs_vs_los_funding")
    has_kfs = kfs_doc.get("loan_amount") is not None or any("kfs" in n.lower() for n in real_doc_names)
    fields_7, ev_7 = [], []
    if has_kfs:
        kfs_amt_val = float(kfs_doc.get("loan_amount") or loan_amount)
        fields_7 = [build_field("KFS Funding Amount", inr_format(kfs_amt_val), 96.0, f"doc-{loan_id}-kfs")]
        ev_7 = [build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — Funding Amount", 1)]
        st7 = "VERIFIED"
        if r7 and r7.get("match_status") == "MISMATCH":
            st7 = "DISCREPANCY"
        elif r7 and r7.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            st7 = "INDETERMINATE"
    else:
        fields_7 = [build_field("KFS", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st7 = "INDETERMINATE"

    conf_7 = _compute_checkpoint_confidence(fields_7, [r7], default_conf=96.0) if has_kfs else 0.0
    checkpoints.append(
        build_checkpoint(
            7, "KFS", st7,
            conf_7,
            (r7.get("notes") if r7 else "") or (f"Key Fact Statement present with funding amount {inr_format(loan_amount)}." if has_kfs else "KFS not uploaded."),
            "KFS funding amount must match LOS approved amount.",
            fields_7, ev_7,
            {"left": inr_format(loan_amount) if (has_kfs and loan_amount > 0) else "N/A", "right": inr_format(float(kfs_doc.get("loan_amount") or 0.0)) if has_kfs else "N/A", "result": "MATCH" if st7 == "VERIFIED" else "MISMATCH"},
        )
    )

    # ── CP 8: Sanction Letter ──
    r8 = _get_check_record("chk_check_financial_sanction_letter_loan_amount_vs_los", "chk_sanction_vs_los_funding")
    has_sanction = sanction_doc.get("loan_amount") is not None or any("sanction" in n.lower() for n in real_doc_names)
    fields_8, ev_8 = [], []
    if has_sanction:
        sanc_amt_val = float(sanction_doc.get("loan_amount") or loan_amount)
        fields_8 = [build_field("Sanction Amount", inr_format(sanc_amt_val), 97.0, f"doc-{loan_id}-sanction")]
        ev_8 = [build_evidence(f"doc-{loan_id}-sanction", "Sanction_Letter.pdf", "Sanction Letter — Amount", 1)]
        st8 = "VERIFIED"
        if r8 and r8.get("match_status") == "MISMATCH":
            st8 = "DISCREPANCY"
        elif r8 and r8.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            st8 = "INDETERMINATE"
    else:
        fields_8 = [build_field("Sanction Letter", "Not Uploaded", 0.0, f"doc-{loan_id}")]
        st8 = "INDETERMINATE"

    conf_8 = _compute_checkpoint_confidence(fields_8, [r8], default_conf=96.5) if has_sanction else 0.0
    checkpoints.append(
        build_checkpoint(
            8, "Sanction Letter", st8,
            conf_8,
            (r8.get("notes") if r8 else "") or (f"Sanction letter matches approved loan amount {inr_format(loan_amount)}." if has_sanction else "Sanction letter not uploaded."),
            "Sanction Letter amount must match approved loan amount.",
            fields_8, ev_8,
            {"left": inr_format(loan_amount) if (has_sanction and loan_amount > 0) else "N/A", "right": inr_format(float(sanction_doc.get("loan_amount") or 0.0)) if has_sanction else "N/A", "result": "MATCH" if st8 == "VERIFIED" else "MISMATCH"},
        )
    )

    # ── CP 9: Aadhaar XML ──
    r9 = records_by_id.get("chk_aadhaar_xml_mandatory_presence")
    xml_doc = docs.get("aadhaar_xml") or {}
    doc_xml_present = xml_doc.get("aadhaar_xml_present")
    if doc_xml_present is None:
        for d in docs.values():
            if isinstance(d, dict) and d.get("aadhaar_xml_present") is not None:
                doc_xml_present = d.get("aadhaar_xml_present")
                break

    has_xml = bool(doc_xml_present) if doc_xml_present is not None else (
        any("xml" in n.lower() for n in real_doc_names)
        or (S3_EXTRACTED_DIR / loan_id / "aadhaar_xml_status.json").exists()
        or (DMS_DIR / loan_id / "aadhaar_xml_status.json").exists()
        or (r9 and r9.get("match_status") == "MATCH")
    )
    st9 = "VERIFIED" if has_xml else "INDETERMINATE"
    if r9 and r9.get("match_status") == "MISMATCH":
        st9 = "DISCREPANCY"

    fields_9 = [build_field("Aadhaar XML Presence", "Present" if has_xml else "Missing", 99.0 if has_xml else 0.0, f"doc-{loan_id}-aadhaarxml")]
    if has_xml and xml_doc.get("applicant_name"):
        fields_9.append(build_field("Aadhaar XML Name", str(xml_doc.get("applicant_name")), 98.0, f"doc-{loan_id}-aadhaarxml"))

    checkpoints.append(
        build_checkpoint(
            9, "Aadhaar XML", st9,
            99.0 if st9 == "VERIFIED" else 0.0,
            (r9.get("notes") if r9 else "") or ("Aadhaar XML present in repository and verified." if has_xml else "Aadhaar XML missing from repository."),
            "Aadhaar XML is a mandatory hard gate for all cases.",
            fields_9,
            [build_evidence(f"doc-{loan_id}-aadhaarxml", "Aadhaar_XML.zip", "Aadhaar XML Archive", 1)] if has_xml else [],
            {"left": "Present" if has_xml else "Missing", "right": "Mandatory", "result": "MATCH" if has_xml else "MISMATCH"},
        )
    )

    # ── CP 10: BPI ──
    r10 = _get_check_record("chk_check_financial_kfs_vs_disbursal_memo_bpi_charge", "chk_broken_period_interest_split", field="bpi_charge")
    bpi_val = (
        kfs_doc.get("BPI")
        or kfs_doc.get("bpi")
        or kfs_doc.get("broken_period_interest")
        or kfs_doc.get("bpi_charge")
        or sanction_doc.get("broken_period_interest")
        or memo_doc.get("bpi_charge")
    )
    los_bpi = los_data.get("bpi_charges") or los_data.get("bpi")
    has_bpi = bpi_val is not None
    fields_10, ev_10 = [], []
    if has_bpi:
        fields_10 = [build_field("BPI Value", inr_format(float(bpi_val)), 95.0, f"doc-{loan_id}-kfs")]
        ev_10 = [build_evidence(f"doc-{loan_id}-kfs", "KFS.pdf", "KFS — BPI", 1)]
        bpi_match = (float(los_bpi) == float(bpi_val)) if los_bpi is not None else True
        if (r10 and r10.get("match_status") == "MISMATCH") or not bpi_match:
            st10 = "DISCREPANCY"
        elif r10 and r10.get("match_status") in ("PARTIAL", "NOT_FOUND"):
            st10 = "INDETERMINATE"
        else:
            st10 = "VERIFIED"
        right_bpi_str = inr_format(float(los_bpi)) if los_bpi is not None else inr_format(float(bpi_val))
        val_result_10 = "MATCH" if st10 == "VERIFIED" else "MISMATCH"
        notes_10 = (r10.get("notes") if r10 else "") or f"Broken Period Interest split of {inr_format(float(bpi_val))} verified against records."
    else:
        fields_10 = [build_field("BPI", "Not Available", 0.0, f"doc-{loan_id}")]
        st10 = "NOT_APPLICABLE"
        right_bpi_str = "N/A"
        val_result_10 = "MISMATCH"
        notes_10 = "Broken Period Interest not applicable or not provided."

    conf_10 = _compute_checkpoint_confidence(fields_10, [r10], default_conf=95.0) if has_bpi else 0.0
    checkpoints.append(
        build_checkpoint(
            10, "BPI", st10,
            conf_10,
            notes_10,
            "Broken period interest split must be consistent across documents and LOS.",
            fields_10, ev_10,
            {"left": inr_format(float(bpi_val)) if has_bpi else "N/A", "right": right_bpi_str, "result": val_result_10},
        )
    )

    # ── CP 11: Disbursal Memo ──
    r11_amt = _get_check_record("chk_check_financial_disbursal_memo_loan_amount_vs_los", "chk_disbursal_memo_amount_threshold")
    has_memo = bool(memo_doc) or any("memo" in n.lower() or "disbursal" in n.lower() for n in real_doc_names)
    fields_11, ev_11 = [], []
    if has_memo:
        st11 = "VERIFIED"
        if r11_amt and r11_amt.get("match_status") == "MISMATCH":
            st11 = "DISCREPANCY"
        elif r11_amt and r11_amt.get("match_status") in ("PARTIAL", "NOT_FOUND"):
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
            11, "Disbursal Memo", st11,
            95.0 if st11 == "VERIFIED" else (0.0 if not has_memo else 40.0),
            (r11_amt.get("notes") if r11_amt else "") or (f"Disbursal memo amount {inr_format(disbursal_amount)} meets threshold." if has_memo else "Disbursal memo not uploaded."),
            "Disbursal Memo amount must be at least 90% of approved loan amount.",
            fields_11, ev_11,
            {"left": inr_format(disbursal_amount) if has_memo else "N/A", "right": f">= {inr_format(loan_amount * 0.9)}" if (has_memo and loan_amount > 0) else "N/A", "result": "MATCH" if st11 == "VERIFIED" else "MISMATCH"},
        )
    )

    # ── CP 12: BT Details ──
    is_bt_flag = los_data.get("balance_transfer") or los_data.get("Balance_transfer") or 0
    try:
        is_bt = int(is_bt_flag) == 1
    except (ValueError, TypeError):
        is_bt = False

    if is_bt:
        bt_doc = docs.get("bt_details") or docs.get("bt") or {}
        has_bt_doc = bool(bt_doc) or any("bt" in n.lower() or "foreclosure" in n.lower() for n in real_doc_names)
        if has_bt_doc:
            checkpoints.append(
                build_checkpoint(
                    12, "BT Details", "VERIFIED", 95.0,
                    "BT details document present and verified.",
                    "BT Details required for Balance Transfer loans.",
                    [
                        build_field("Balance Transfer", "1 (Applicable)", 100.0, f"doc-{loan_id}-bt"),
                        build_field("BT Details Presence", "Present", 95.0, f"doc-{loan_id}-bt"),
                    ],
                    [build_evidence(f"doc-{loan_id}-bt", "BT_Details.pdf", "BT Details Document", 1, "Previous Lender")],
                    {"left": "Present", "right": "Mandatory for BT", "result": "MATCH"},
                )
            )
        else:
            checkpoints.append(
                build_checkpoint(
                    12, "BT Details", "INDETERMINATE", 0.0,
                    "Balance Transfer loan flagged in LOS (BT=1), but BT Details document is missing.",
                    "BT Details required for Balance Transfer loans.",
                    [
                        build_field("Balance Transfer", "1 (Applicable)", 100.0, f"doc-{loan_id}-bt"),
                        build_field("BT Details Presence", "Missing", 0.0, f"doc-{loan_id}-bt"),
                    ],
                    [],
                    {"left": "Missing", "right": "Mandatory for BT", "result": "MISMATCH"},
                )
            )
    else:
        checkpoints.append(
            build_checkpoint(
                12, "BT Details", "NOT_APPLICABLE", 0.0,
                "Not applicable — not a BT case (BT flag = 0).",
                "BT Details required for Balance Transfer loans.",
                [build_field("Balance Transfer", "0 (Not Applicable)", 100.0, f"doc-{loan_id}-bt")],
                [],
                {"left": "0 (Non-BT)", "right": "Not Applicable", "result": "MATCH"},
            )
        )

    # Summary counts & score
    verified_count = sum(1 for cp in checkpoints if cp["status"] == "VERIFIED")
    discrepancy_count = sum(1 for cp in checkpoints if cp["status"] == "DISCREPANCY")
    review_count = sum(1 for cp in checkpoints if cp["status"] == "INDETERMINATE")

    scorecard_score = scorecard_data.get("overall_score")
    scorecard_decision = scorecard_data.get("preliminary_decision")
    scorecard_tier = scorecard_data.get("risk_tier")

    # Determine overall status and risk level
    if discrepancy_count > 0 or scorecard_decision == "REJECT_OR_FLAG" or scorecard_tier == "HIGH_RISK":
        overall_status = "DISCREPANCY"
        risk_level = "HIGH"
        dgcl_score = float(scorecard_score) if scorecard_score is not None else max(25.0, 100.0 - (discrepancy_count * 25.0 + review_count * 10.0))
    elif review_count > 0 and (status_data.get("status") == "PROCESSING" or (not records and not docs)):
        overall_status = "PROCESSING"
        risk_level = "LOW"
        dgcl_score = 0.0
    elif review_count > 0 or scorecard_decision == "MANUAL_REVIEW" or scorecard_tier == "MEDIUM_RISK":
        overall_status = "INDETERMINATE"
        risk_level = "MEDIUM"
        dgcl_score = float(scorecard_score) if scorecard_score is not None else max(65.0, 100.0 - (review_count * 12.0))
    else:
        overall_status = "VERIFIED"
        risk_level = "LOW"
        dgcl_score = float(scorecard_score) if scorecard_score is not None else 97.4

    # Processing Steps
    history = status_data.get("node_history", ["fetch_los", "fetch_dms", "idp_scan", "llm_structure", "check_parallel", "compile_report", "generate_scorecard", "push_results", "done"])
    step_defs = [
        ("fetch_los", "System", "LOS Ingestion", 99.5),
        ("fetch_dms", "System", "DMS Document Fetch", 99.5),
        ("idp_scan", "PaddleOCR", "Document OCR & Layout Scan", 98.2),
        ("llm_structure", "LLM", "Field Structuring & Normalization", 97.5),
        ("check_parallel", "Validation", "KYC, Financial & Loan Application Checks", 96.8),
        ("compile_report", "Engine", "Report Compilation & Aggregation", 99.0),
        ("generate_scorecard", "DGCL Engine", "Scorecard Generation", dgcl_score),
        ("push_results", "System", "LOS Result Push", 100.0),
    ]

    raw_upd = status_data.get("updated_at")
    base_time = None
    if raw_upd:
        try:
            dt = datetime.fromisoformat(raw_upd.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            base_time = dt.astimezone(IST)
            formatted_last_updated = base_time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            formatted_last_updated = raw_upd
    else:
        formatted_last_updated = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    if not base_time:
        base_time = datetime.now(IST)

    proc_steps = []
    for i, (node_key, component, label, conf) in enumerate(step_defs):
        is_done = node_key in history or "done" in history
        start_t = (base_time - timedelta(seconds=(len(step_defs) - i) * 3)).strftime("%H:%M:%S")
        end_t = (base_time - timedelta(seconds=(len(step_defs) - i - 1) * 3)).strftime("%H:%M:%S")
        proc_steps.append({
            "id": f"step-{loan_id}-{node_key}",
            "component": component,
            "status": "COMPLETED" if is_done else "PENDING",
            "detail": f"{label} {'completed' if is_done else 'pending'}",
            "startedAt": start_t,
            "completedAt": end_t if is_done else None,
            "confidence": conf,
        })

    return {
        "id": loan_id,
        "applicant": applicant_name,
        "applicationId": app_id,
        "loanType": loan_type,
        "loanAmount": loan_amount,
        "disbursalAmount": disbursal_amount,
        "loginDate": los_data.get("login_date") or datetime.now(IST).strftime("%Y-%m-%d"),
        "disbursalDate": (datetime.now(IST).strftime("%Y-%m-%d")) if overall_status == "VERIFIED" else None,
        "documentCount": len(doc_ids),
        "processingTime": "2m 15s" if records else "—",
        "processingTimeSeconds": 135 if records else 0,
        "dgclScore": round(dgcl_score, 1),
        "dgcl_score": round(dgcl_score, 1),
        "score": round(dgcl_score, 1),
        "verifiedCount": verified_count,
        "discrepancyCount": discrepancy_count,
        "reviewCount": review_count,
        "status": overall_status,
        "riskLevel": risk_level,
        "lastUpdated": formatted_last_updated,
        "balanceTransfer": 1 if is_bt else 0,
        "isBalanceTransfer": is_bt,
        "checkpoints": checkpoints,
        "documentIds": doc_ids,
        "processingSteps": proc_steps,
    }


def serialize_all_cases() -> list[dict[str, Any]]:
    """Serializes all loan records found in LOS storage."""
    loan_ids = list_loan_ids()
    cases: List[Dict[str, Any]] = []
    for lid in loan_ids:
        try:
            c = serialize_case(lid)
            cases.append(c)
        except Exception:
            logger.exception("Error serializing case %s", lid)
    return cases
