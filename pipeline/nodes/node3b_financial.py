import logging
from typing import Any

from pipeline.config import NODE3B_FIELD_CHECKS
from pipeline.nodes.comparison_utils import (
    compare_bpi_doc_to_doc,
    resolve_doc_data,
    run_field_checks,
)
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.node3b_financial")


def node3b_financial(state: PipelineState) -> dict[str, Any]:
    """Node 3b — Financial & Sanction Checks (LOS vs Documents + BPI Consistency)

    Compares financial fields (Loan Amount with 90% threshold, Loan Validity,
    Account No, Bank Account Type, Loan Type, Customer Consent) across Application Form,
    KFS, Disbursal Memo, and Sanction Letter against LOS data.
    Also executes cross-document BPI consistency check between KFS and Disbursal Memo.
    """
    loan_id = state["loan_id"]
    extracted = state.get("extracted_data", {})
    los = state.get("los_data", {})
    records: list[dict[str, Any]] = []

    logger.info("Executing Node 3b (Financial Checks vs LOS) for loan %s", loan_id)

    # 1. Configured field checks vs LOS
    for doc_type, checks in NODE3B_FIELD_CHECKS.items():
        doc_data = resolve_doc_data(extracted, doc_type)
        doc_records = run_field_checks(
            doc_type=doc_type,
            doc_data=doc_data,
            los_data=los,
            field_checks=checks,
            loan_id=loan_id,
            subnode_name="kfs_sanction",
        )
        records.extend(doc_records)

    # 2. Doc-to-Doc Broken Period Interest (BPI) consistency check
    kfs_data = resolve_doc_data(extracted, "kfs")
    memo_data = resolve_doc_data(extracted, "disbursal_memo")
    bpi_record = compare_bpi_doc_to_doc(kfs_data, memo_data, subnode_name="kfs_sanction")
    if bpi_record is not None:
        records.append(bpi_record)

    rollup = compute_rollup(records)
    logger.info("Node 3b completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }

