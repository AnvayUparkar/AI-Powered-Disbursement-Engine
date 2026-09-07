"""Node: Compile Report — Aggregates all checker results and rollups into a unified validation report."""
import logging
from collections import Counter
from typing import Any, Dict, List

from pipeline.state import PipelineState
from pipeline.storage import save_s3_result, update_status

logger = logging.getLogger("disbursement_pipeline.compile_report")


def compile_report(state: PipelineState) -> PipelineState:
    """Aggregates all field-level comparison records and subnode rollups into compiled_report."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("compile_report")

    logger.info("Executing compile_report for loan: %s", loan_id)

    comparison_results: List[Dict[str, Any]] = state.get("comparison_results", [])
    subnode_rollups: Dict[str, str] = state.get("subnode_rollups", {})

    status_counts = Counter(r.get("match_status") for r in comparison_results)

    compiled_report = {
        "loan_id": loan_id,
        "subnode_rollups": subnode_rollups,
        "summary": {
            "total_checks": len(comparison_results),
            "match_count": status_counts.get("MATCH", 0),
            "mismatch_count": status_counts.get("MISMATCH", 0),
            "partial_count": status_counts.get("PARTIAL", 0),
            "not_found_count": status_counts.get("NOT_FOUND", 0),
            "captured_count": status_counts.get("CAPTURED", 0),
            "not_implemented_count": status_counts.get("NOT_IMPLEMENTED", 0),
        },
        "comparison_results": comparison_results,
    }

    # Persist report artifacts to S3 Result tier
    save_s3_result(loan_id, "comparison_results.json", comparison_results)
    save_s3_result(loan_id, "subnode_rollups.json", subnode_rollups)
    save_s3_result(loan_id, "compiled_report.json", compiled_report)

    update_status(loan_id, current_node="compile_report", errors=errors, node_history=history)

    return {
        **state,
        "compiled_report": compiled_report,
        "errors": errors,
        "node_history": history,
    }
