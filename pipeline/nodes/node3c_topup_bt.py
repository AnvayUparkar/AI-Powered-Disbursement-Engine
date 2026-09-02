import logging
import re
from typing import Any

from pipeline.config import (
    DISBURSAL_MEMO_THRESHOLD_PCT,
    FUNDING_AMOUNT_SOURCE_FIELD,
)
from pipeline.state import PipelineState, compute_rollup

logger = logging.getLogger("disbursement_pipeline.node3c")


def _clean_numeric(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val)
    cleaned = re.sub(r"[^\d.]", "", val_str)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_id(val: Any) -> str | None:
    if val is None:
        return None
    return re.sub(r"\s+", "", str(val)).upper()


def node3c_topup_bt(state: PipelineState) -> dict:
    """Node 3c — Top-up & Balance Transfer (BT) Checks

    - Broken period interest split (placeholder threshold logic)
    - Disbursal memo IDs match (Closure ID, Application ID)
    - Disbursal memo amount threshold vs loan amount
    - BT/closure amount vs final FC amount (NOT_IMPLEMENTED stub — source doc pending)
    """
    loan_id = state["loan_id"]
    extracted = state.get("extracted_data", {})
    los = state.get("los_data", {})
    records: list[dict] = []

    logger.info("Executing Node 3c (Top-up & BT) for loan %s", loan_id)

    # 1. Broken Period Interest Split (KFS vs Sanction Letter)
    kfs_doc = extracted.get("kfs", {})
    sanction_doc = extracted.get("sanction_letter", {})

    kfs_bpi = _clean_numeric(kfs_doc.get("broken_period_interest"))
    sanction_bpi = _clean_numeric(sanction_doc.get("broken_period_interest"))

    check_id = "chk_broken_period_interest_split"
    if kfs_bpi is None and sanction_bpi is None:
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "broken_period_interest",
            "sources": ["kfs", "sanction_letter"],
            "values": [None, None],
            "match_type": "threshold",
            "match_status": "CAPTURED",
            "confidence": 1.0,
            "method": "placeholder_split_rule",
            "llm_used": False,
            "notes": "No broken period interest applicable for this loan",
        })
    elif kfs_bpi is None or sanction_bpi is None:
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "broken_period_interest",
            "sources": ["kfs", "sanction_letter"],
            "values": [kfs_bpi, sanction_bpi],
            "match_type": "threshold",
            "match_status": "PARTIAL",
            "confidence": 0.5,
            "method": "placeholder_split_rule",
            "llm_used": False,
            "notes": "Broken period interest present in one document only",
        })
    else:
        diff = abs(kfs_bpi - sanction_bpi)
        denom = max(kfs_bpi, sanction_bpi, 1.0)
        is_match = (diff / denom) <= 0.10
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "broken_period_interest",
            "sources": ["kfs", "sanction_letter"],
            "values": [kfs_bpi, sanction_bpi],
            "match_type": "threshold",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0,
            "method": "placeholder_split_rule",
            "llm_used": False,
            "notes": "Placeholder 10% tolerance comparison (exact 100%/90% rule TBD)",
        })

    # 2. Disbursal Memo — ID Checks
    memo_doc = extracted.get("disbursal_memo", {})
    app_doc = extracted.get("application_form", {})

    memo_app_id = _clean_id(memo_doc.get("application_id"))
    raw_app_id = app_doc.get("application_id") if app_doc.get("application_id") is not None else los.get("application_id", loan_id)
    expected_app_id = _clean_id(raw_app_id)

    check_id = "chk_disbursal_memo_application_id"
    if not memo_app_id or not expected_app_id:
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "application_id",
            "sources": ["disbursal_memo", "application_form"],
            "values": [memo_app_id, expected_app_id],
            "match_type": "exact_id",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": "string_equality",
            "llm_used": False,
            "notes": "Application ID missing in disbursal memo",
        })
    else:
        is_match = (memo_app_id == expected_app_id)
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "application_id",
            "sources": ["disbursal_memo", "application_form"],
            "values": [memo_app_id, expected_app_id],
            "match_type": "exact_id",
            "match_status": "MATCH" if is_match else "MISMATCH",
            "confidence": 1.0,
            "method": "string_equality",
            "llm_used": False,
            "notes": None if is_match else f"Application ID mismatch: {memo_app_id} vs {expected_app_id}",
        })

    # Closure ID check (if present in memo)
    closure_id = _clean_id(memo_doc.get("closure_id"))
    if closure_id:
        records.append({
            "check_id": "chk_disbursal_memo_closure_id",
            "subnode": "topup_bt",
            "field": "closure_id",
            "sources": ["disbursal_memo"],
            "values": [closure_id],
            "match_type": "exact_id",
            "match_status": "CAPTURED",
            "confidence": 1.0,
            "method": "capture_only",
            "llm_used": False,
            "notes": f"Closure ID captured: {closure_id}",
        })

    # 3. Disbursal Memo — Amount Check (Threshold comparison)
    memo_amt = _clean_numeric(memo_doc.get("disbursal_amount") if memo_doc.get("disbursal_amount") is not None else memo_doc.get("amount"))
    
    raw_loan_amt = los.get(FUNDING_AMOUNT_SOURCE_FIELD) if los.get(FUNDING_AMOUNT_SOURCE_FIELD) is not None else app_doc.get("loan_amount")
    loan_amt = _clean_numeric(raw_loan_amt)

    check_id = "chk_disbursal_memo_amount_threshold"
    if memo_amt is None or loan_amt is None or loan_amt <= 0:
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "disbursal_amount",
            "sources": ["disbursal_memo", "los"],
            "values": [memo_amt, loan_amt],
            "match_type": "threshold",
            "match_status": "NOT_FOUND",
            "confidence": 0.0,
            "method": "threshold_numeric_compare",
            "llm_used": False,
            "notes": "Disbursal memo amount or valid loan amount missing",
        })
    else:
        threshold_val = DISBURSAL_MEMO_THRESHOLD_PCT * loan_amt
        is_pass = memo_amt >= threshold_val
        records.append({
            "check_id": check_id,
            "subnode": "topup_bt",
            "field": "disbursal_amount",
            "sources": ["disbursal_memo", "los"],
            "values": [memo_amt, loan_amt],
            "match_type": "threshold",
            "match_status": "MATCH" if is_pass else "MISMATCH",
            "confidence": 1.0,
            "method": "threshold_numeric_compare",
            "llm_used": False,
            "notes": (
                f"Memo amount ({memo_amt}) >= {int(DISBURSAL_MEMO_THRESHOLD_PCT*100)}% of loan ({threshold_val})"
                if is_pass
                else f"Memo amount ({memo_amt}) below {int(DISBURSAL_MEMO_THRESHOLD_PCT*100)}% threshold ({threshold_val})"
            ),
        })

    # 4. BT / Closure Amount vs Final Foreclosure (FC) Amount
    records.append({
        "check_id": "chk_bt_closure_vs_final_fc",
        "subnode": "topup_bt",
        "field": "bt_closure_vs_final_fc_amount",
        "sources": ["disbursal_memo", "final_fc_doc_pending"],
        "values": [closure_id, None],
        "match_type": "threshold",
        "match_status": "NOT_IMPLEMENTED",
        "confidence": 0.0,
        "method": "stub_pending_source_document",
        "llm_used": False,
        "notes": "Source document for final FC amount pending — implement when confirmed",
    })

    rollup = compute_rollup(records)
    logger.info("Node 3c completed with rollup: %s (%d records)", rollup, len(records))

    return {
        "records": records,
        "rollup": rollup,
    }

