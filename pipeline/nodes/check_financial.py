"""Node: Check Financial — Verifies financial documents and BPI consistency against LOS records."""
import logging
from typing import Any, Dict, List

from config import FINANCIAL_FIELD_CHECKS
from pipeline.engines.comparison import (
    compare_bpi_doc_to_doc,
    resolve_doc_data,
    run_field_checks,
)
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.check_financial")


def check_financial(state: PipelineState) -> dict[str, Any]:
    """Runs financial verification checks comparing Account Statement & Disbursal Memo against LOS data."""
    loan_id = state.get("loan_id", "")
    extracted = state.get("extracted_structured_data") or state.get("extracted_data") or {}
    los = state.get("los_data") or {}
    records: List[Dict[str, Any]] = []

    logger.info("Executing check_financial for loan: %s", loan_id)

    # 1. Configured field checks vs LOS
    for doc_type, checks in FINANCIAL_FIELD_CHECKS.items():
        doc_data = resolve_doc_data(extracted, doc_type)
        doc_records = run_field_checks(
            doc_type=doc_type,
            doc_data=doc_data,
            los_data=los,
            field_checks=checks,
            loan_id=loan_id,
            subnode_name="check_financial",
        )
        records.extend(doc_records)

    # 2. Doc-to-Doc Broken Period Interest (BPI) consistency check
    kfs_data = resolve_doc_data(extracted, "kfs")
    memo_data = resolve_doc_data(extracted, "disbursal_memo")
    bpi_record = compare_bpi_doc_to_doc(kfs_data, memo_data, subnode_name="check_financial")
    if bpi_record is not None:
        records.append(bpi_record)

    rollup = compute_rollup(records)
    logger.info("check_financial completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }
