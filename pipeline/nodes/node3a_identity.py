import logging
from typing import Any

from pipeline.config import NODE3A_FIELD_CHECKS
from pipeline.nodes.comparison_utils import resolve_doc_data, run_field_checks
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.node3a_identity")


def node3a_identity(state: PipelineState) -> dict[str, Any]:
    """Node 3a — Identity Verification Checks (LOS vs Documents)

    Compares identity fields (Name, DOB, Aadhaar, PAN, Mobile, Address, Gender)
    across all relevant documents (Aadhaar, PAN, Application Form, Account Statement)
    directly against LOS data.
    """
    loan_id = state["loan_id"]
    extracted = state.get("extracted_data", {})
    los = state.get("los_data", {})
    records: list[dict[str, Any]] = []

    logger.info("Executing Node 3a (Identity Checks vs LOS) for loan %s", loan_id)

    for doc_type, checks in NODE3A_FIELD_CHECKS.items():
        doc_data = resolve_doc_data(extracted, doc_type)
        doc_records = run_field_checks(
            doc_type=doc_type,
            doc_data=doc_data,
            los_data=los,
            field_checks=checks,
            loan_id=loan_id,
            subnode_name="loan_kyc",
        )
        records.extend(doc_records)

    rollup = compute_rollup(records)
    logger.info("Node 3a completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }

