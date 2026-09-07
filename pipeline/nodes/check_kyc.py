"""Node: Check KYC — Verifies KYC documents (Aadhaar, PAN) against LOS records."""
import logging
from typing import Any, Dict, List

from config import KYC_FIELD_CHECKS
from pipeline.engines.comparison import resolve_doc_data, run_field_checks
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.check_kyc")


def check_kyc(state: PipelineState) -> dict[str, Any]:
    """Runs KYC verification checks comparing Aadhaar and PAN against LOS data."""
    loan_id = state.get("loan_id", "")
    extracted = state.get("extracted_structured_data") or state.get("extracted_data") or {}
    los = state.get("los_data") or {}
    records: List[Dict[str, Any]] = []

    logger.info("Executing check_kyc for loan: %s", loan_id)

    for doc_type, checks in KYC_FIELD_CHECKS.items():
        doc_data = resolve_doc_data(extracted, doc_type)
        doc_records = run_field_checks(
            doc_type=doc_type,
            doc_data=doc_data,
            los_data=los,
            field_checks=checks,
            loan_id=loan_id,
            subnode_name="check_kyc",
        )
        records.extend(doc_records)

    rollup = compute_rollup(records)
    logger.info("check_kyc completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }
