import logging
from typing import Any

from pipeline.config import NODE3C_FIELD_CHECKS
from pipeline.nodes.comparison_utils import resolve_doc_data, run_field_checks
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.node3c_dates_ids")


def node3c_dates_ids(state: PipelineState) -> dict[str, Any]:
    """Node 3c — Dates & Identifier Checks (LOS vs Documents)

    Compares Date fields (Application Date, Login Date, Disbursement Date)
    and Identifiers (Application No, Loan No) across Application Form and Disbursal Memo
    directly against LOS data.
    """
    loan_id = state.get("loan_id", "")
    extracted = state.get("extracted_data") or {}
    los = state.get("los_data") or {}
    records: list[dict[str, Any]] = []

    logger.info("Executing Node 3c (Dates & Identifiers vs LOS) for loan %s", loan_id)

    for doc_type, checks in NODE3C_FIELD_CHECKS.items():
        doc_data = resolve_doc_data(extracted, doc_type)
        doc_records = run_field_checks(
            doc_type=doc_type,
            doc_data=doc_data,
            los_data=los,
            field_checks=checks,
            loan_id=loan_id,
            subnode_name="topup_bt",
        )
        records.extend(doc_records)

    rollup = compute_rollup(records)
    logger.info("Node 3c completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }

