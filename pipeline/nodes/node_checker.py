import logging
from typing import Any

from config import S3_RESULT_DIR
from pipeline.config import (
    CHECKER_MIN_CONFIDENCE_THRESHOLD,
    CHECKER_REQUIRED_DOCUMENTS,
    CHECKER_REQUIRED_LOS_FIELDS,
    MAX_CHECKER_RETRIES,
)

from pipeline.state import PipelineState
from pipeline.storage import update_status, write_json

logger = logging.getLogger("disbursement_pipeline.node_checker")


def node_checker(state: PipelineState) -> PipelineState:
    """Node 4b (Checker) — Sits after Compile and verifies required data presence

    and confidence thresholds before proceeding to scorecard. Triggers retry if
    confidence is below threshold and retries remain.
    """
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("checker")

    retry_count = int(state.get("retry_count", 0))

    logger.info(
        "Executing Node 4b (Checker) for loan %s (Attempt %d / %d)",
        loan_id,
        retry_count,
        MAX_CHECKER_RETRIES,
    )

    los_data = state.get("los_data", {})
    extracted_data = state.get("extracted_data", {})
    comparison_results = state.get("comparison_results", [])
    compiled_report = state.get("compiled_report", {})

    # 1. Check Required LOS Fields
    missing_los_fields: list[str] = []
    for field in CHECKER_REQUIRED_LOS_FIELDS:
        val = los_data.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing_los_fields.append(field)

    # 2. Check Required Extracted Documents
    missing_docs: list[str] = []
    for doc_name in CHECKER_REQUIRED_DOCUMENTS:
        doc_content = extracted_data.get(doc_name)
        if not doc_content or not isinstance(doc_content, dict):
            missing_docs.append(doc_name)

    # 3. Calculate Completeness & Confidence Metric
    total_expected_los = len(CHECKER_REQUIRED_LOS_FIELDS)
    present_los_count = total_expected_los - len(missing_los_fields)
    los_presence_ratio = (
        present_los_count / total_expected_los if total_expected_los > 0 else 1.0
    )

    total_expected_docs = len(CHECKER_REQUIRED_DOCUMENTS)
    present_doc_count = total_expected_docs - len(missing_docs)
    doc_presence_ratio = (
        present_doc_count / total_expected_docs if total_expected_docs > 0 else 1.0
    )

    data_presence_score = (los_presence_ratio + doc_presence_ratio) / 2.0

    # Verification checks confidence
    total_checks = len(comparison_results)
    match_count = sum(
        1 for r in comparison_results if r.get("match_status") in ("MATCH", "CAPTURED")
    )
    match_ratio = (match_count / total_checks) if total_checks > 0 else 0.0

    # Aggregate Confidence Score: blends data presence (60%) and match quality (40%)
    if total_checks > 0:
        aggregate_confidence = round(
            (0.60 * data_presence_score) + (0.40 * match_ratio),
            3,
        )
    else:
        aggregate_confidence = round(data_presence_score * 0.5, 3)

    is_data_complete = (len(missing_los_fields) == 0) and (len(missing_docs) == 0)
    is_confidence_met = (
        is_data_complete
        and (aggregate_confidence >= CHECKER_MIN_CONFIDENCE_THRESHOLD)
    )

    # 4. Determine Retry Decision
    if is_confidence_met:
        status_str = "PASSED"
        will_retry = False
        next_retry_count = retry_count
        note = (
            f"All required data present and confidence verified "
            f"({aggregate_confidence:.2f} >= {CHECKER_MIN_CONFIDENCE_THRESHOLD:.2f})."
        )
    else:
        if retry_count < MAX_CHECKER_RETRIES:
            status_str = "RETRYING"
            will_retry = True
            next_retry_count = retry_count + 1
            note = (
                f"Confidence {aggregate_confidence:.2f} is below threshold "
                f"{CHECKER_MIN_CONFIDENCE_THRESHOLD:.2f} (Missing LOS: {missing_los_fields}, "
                f"Missing Docs: {missing_docs}). Retrying pipeline from start "
                f"({next_retry_count}/{MAX_CHECKER_RETRIES})."
            )
            logger.warning(
                "Loan %s checker below confidence. Retrying (%d/%d): %s",
                loan_id,
                next_retry_count,
                MAX_CHECKER_RETRIES,
                note,
            )
        else:
            status_str = "FAILED_MAX_RETRIES"
            will_retry = False
            next_retry_count = retry_count
            note = (
                f"Confidence {aggregate_confidence:.2f} is below threshold "
                f"{CHECKER_MIN_CONFIDENCE_THRESHOLD:.2f}, but max retries "
                f"({MAX_CHECKER_RETRIES}) reached. Proceeding to scorecard."
            )
            logger.warning("Loan %s reached max retries in checker node. Proceeding.", loan_id)
            errors.append(note)

    checker_result: dict[str, Any] = {
        "status": status_str,
        "confidence_score": aggregate_confidence,
        "threshold": CHECKER_MIN_CONFIDENCE_THRESHOLD,
        "missing_los_fields": missing_los_fields,
        "missing_documents": missing_docs,
        "total_checks": total_checks,
        "passed_checks": match_count,
        "retry_attempt": retry_count,
        "max_retries": MAX_CHECKER_RETRIES,
        "will_retry": will_retry,
        "notes": note,
    }

    # 5. Persist Checker Artifacts
    loan_result_dir = S3_RESULT_DIR / loan_id
    loan_result_dir.mkdir(parents=True, exist_ok=True)
    write_json(loan_result_dir / "checker_result.json", checker_result)

    update_status(loan_id, current_node="checker", errors=errors, node_history=history)

    return {
        **state,
        "retry_count": next_retry_count,
        "checker_result": checker_result,
        "errors": errors,
        "node_history": history,
    }
