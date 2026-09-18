"""Parallel Checker C: Check Loan App — Verifies application form, KFS, and sanction letter against LOS records."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import LOAN_APP_FIELD_CHECKS
from pipeline.engines.comparison import resolve_doc_data, run_field_checks
from pipeline.engines.pyhanko_inspector import inspect_pdf_signatures, is_loan_agreement
from pipeline.state import PipelineState, compute_rollup
from pipeline.storage import get_s3_los

logger = logging.getLogger("disbursement_pipeline.check_loan_app")


def _evaluate_loan_agreement_signature(
    state: PipelineState,
    extracted: dict[str, Any],
    loan_id: str,
) -> Optional[Dict[str, Any]]:
    """Evaluates pyHanko digital signature verification exclusively for Loan Agreement."""
    agree_data = resolve_doc_data(extracted, "loan_agreement")
    pyhanko_info = None

    if agree_data and isinstance(agree_data, dict):
        pyhanko_info = agree_data.get("pyhanko_inspection")

    # If not in extracted_data, check raw_doc_paths for a Loan Agreement PDF
    if not pyhanko_info:
        raw_paths = state.get("raw_doc_paths") or {}
        for fname, fpath_str in raw_paths.items():
            if is_loan_agreement(fname) and Path(fpath_str).suffix.lower() == ".pdf":
                try:
                    pyhanko_info = inspect_pdf_signatures(fpath_str, filename=fname)
                    break
                except Exception as e:
                    logger.warning("pyHanko direct inspection failed for %s: %s", fname, e)
                    pyhanko_info = {"is_signed": False, "is_acceptable": False, "error": str(e)}
                    break

    # If no Loan Agreement was uploaded or found, do not create a spurious record (preserves INDETERMINATE)
    if not pyhanko_info and not agree_data:
        return None

    is_signed = bool(pyhanko_info.get("is_signed", False)) if pyhanko_info else False
    is_acceptable = bool(pyhanko_info.get("is_acceptable", False)) if pyhanko_info else False
    sig_count = int(pyhanko_info.get("signature_count", 0)) if pyhanko_info else 0
    signatures = pyhanko_info.get("signatures", []) if pyhanko_info else []
    first_signer = signatures[0].get("signer", {}) if signatures else {}

    if is_acceptable:
        match_status = "MATCH"
        doc_value = f"Digitally Signed ({sig_count} signature{'s' if sig_count != 1 else ''})"
        confidence = 100.0
        anchor = signatures[0].get("trust_anchor_label", "Trusted PKI") if signatures else "Trusted PKI"
        signer_cn = first_signer.get("common_name", "Unknown Signer")
        notes = f"Loan agreement cryptographically intact and digitally signed by '{signer_cn}' [{anchor}]."
    elif is_signed and not is_acceptable:
        match_status = "MISMATCH"
        doc_value = "Digital Signature Verification Failed"
        confidence = 0.0
        notes = "Loan agreement digital signature failed cryptographic integrity, validity, or trust requirements."
    else:
        # Agreement is present but not digitally signed
        if agree_data and agree_data.get("loan_agreement_signed") is True:
            match_status = "MATCH"
            doc_value = "Signed (e-Sign/Physical)"
            confidence = 85.0
            notes = "Loan agreement signed according to extracted document audit."
        else:
            match_status = "MISMATCH"
            doc_value = "Unsigned"
            confidence = 0.0
            notes = "Loan agreement uploaded but missing required digital signature."

    return {
        "check_id": "chk_loan_agreement_digital_signature",
        "loan_id": loan_id,
        "doc_type": "loan_agreement",
        "field": "digital_signature",
        "los_field": "loan_agreement_signed",
        "los_value": "Required",
        "doc_value": doc_value,
        "match_status": match_status,
        "match_type": "exact",
        "confidence": confidence,
        "notes": notes,
        "subnode": "check_loan_application",
        "category": "Loan Application",
        "signer_info": first_signer if is_signed else None,
        "pyhanko_inspection": pyhanko_info,
    }


def check_loan_app(state: PipelineState) -> dict[str, Any]:
    """Runs loan application checks comparing Application Form, KFS, and Sanction Letter against LOS data."""
    loan_id = state.get("loan_id", "")
    extracted = state.get("extracted_structured_data") or state.get("extracted_data") or {}
    los = state.get("los_data") or get_s3_los(loan_id)
    records: List[Dict[str, Any]] = []

    logger.info("Executing check_loan_app for loan: %s", loan_id)

    for doc_type, checks in LOAN_APP_FIELD_CHECKS.items():
        doc_data = resolve_doc_data(extracted, doc_type)
        doc_records = run_field_checks(
            doc_type=doc_type,
            doc_data=doc_data,
            los_data=los,
            field_checks=checks,
            loan_id=loan_id,
            subnode_name="check_loan_application",
        )
        records.extend(doc_records)

    # Evaluate Loan Agreement digital signature verification via pyHanko
    sig_record = _evaluate_loan_agreement_signature(state, extracted, loan_id)
    if sig_record:
        records.append(sig_record)

    rollup = compute_rollup(records)
    logger.info("check_loan_app completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }


# Backward-compatibility alias
check_loan_application = check_loan_app
