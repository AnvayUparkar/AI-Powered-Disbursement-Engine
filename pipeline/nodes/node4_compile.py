import logging
from collections import Counter

from config import S3_RESULT_DIR

from pipeline.state import PipelineState
from pipeline.storage import update_status, write_json

logger = logging.getLogger("disbursement_pipeline.node4_compile")


def node4_compile(state: PipelineState) -> PipelineState:
    """Node 4 (Compile) — Aggregates all field-level records + sub-node rollups

    into compiled_report and persists result artifacts.
    """
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("compile")

    logger.info("Executing Node 4 (Compile) for loan %s", loan_id)

    comparison_results = state.get("comparison_results", [])
    subnode_rollups = state.get("subnode_rollups", {})

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

    loan_result_dir = S3_RESULT_DIR / loan_id
    loan_result_dir.mkdir(parents=True, exist_ok=True)

    write_json(loan_result_dir / "comparison_results.json", comparison_results)
    write_json(loan_result_dir / "subnode_rollups.json", subnode_rollups)
    write_json(loan_result_dir / "compiled_report.json", compiled_report)

    update_status(loan_id, current_node="compile", errors=errors, node_history=history)

    return {
        **state,
        "compiled_report": compiled_report,
        "errors": errors,
        "node_history": history,
    }

