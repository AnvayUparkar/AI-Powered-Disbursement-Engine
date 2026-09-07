"""Node: Check Loan Application — Verifies application form, KFS, and sanction letter against LOS records."""
import logging
from typing import Any, Dict, List

from config import LOAN_APP_FIELD_CHECKS
from pipeline.engines.comparison import resolve_doc_data, run_field_checks
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.check_loan_application")


def check_loan_application(state: PipelineState) -> dict[str, Any]:
    """Runs loan application checks comparing Application Form, KFS, and Sanction Letter against LOS data."""
    loan_id = state.get("loan_id", "")
    extracted = state.get("extracted_structured_data") or state.get("extracted_data") or {}
    los = state.get("los_data") or {}
    records: List[Dict[str, Any]] = []

    logger.info("Executing check_loan_application for loan: %s", loan_id)

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

    rollup = compute_rollup(records)
    logger.info("check_loan_application completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }
