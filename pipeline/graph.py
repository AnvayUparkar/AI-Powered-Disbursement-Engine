import logging
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, StateGraph

from pipeline.nodes.node1_fetch import node1_fetch
from pipeline.nodes.node2_extract import node2_extract
from pipeline.nodes.node3a_identity import node3a_identity
from pipeline.nodes.node3b_financial import node3b_financial
from pipeline.nodes.node3c_dates_ids import node3c_dates_ids
from pipeline.nodes.node4_compile import node4_compile
from pipeline.nodes.node5_scorecard import node5_scorecard
from pipeline.nodes.node6_push import node6_push
from pipeline.nodes.node_checker import node_checker
from pipeline.state import PipelineState
from pipeline.storage import update_status

logger = logging.getLogger("disbursement_pipeline.graph")


def _run_subnodes_parallel(state: PipelineState) -> dict:
    """Executes sub-nodes 3a, 3b, and 3c concurrently in a thread pool."""
    logger.info("Starting concurrent execution of sub-nodes 3a, 3b, 3c for loan %s", state["loan_id"])

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_3a = executor.submit(node3a_identity, state)
        future_3b = executor.submit(node3b_financial, state)
        future_3c = executor.submit(node3c_dates_ids, state)

        all_records: list[dict] = []
        rollups: dict[str, str] = {}
        subnode_futures = [
            ("loan_kyc", future_3a),
            ("kfs_sanction", future_3b),
            ("topup_bt", future_3c),
        ]

        for name, fut in subnode_futures:
            try:
                res = fut.result()
                all_records.extend(res.get("records", []))
                rollups[name] = res.get("rollup", "Indeterminate")
            except Exception as e:  # noqa: BLE001 - Boundary handler for isolated subnode execution
                logger.error("Error executing subnode %s for loan %s: %s", name, state["loan_id"], e)
                rollups[name] = "Indeterminate"

    return {
        "records": all_records,
        "rollups": rollups,
    }


def comparison_node(state: PipelineState) -> PipelineState:
    """Node 3 (Comparison Fan-out/Fan-in) wrapper."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("comparison")

    try:
        results = _run_subnodes_parallel(state)
        all_records = results["records"]
        rollups = results["rollups"]
    except Exception as e:  # noqa: BLE001 - Defensive boundary handler
        logger.error("Error during parallel subnode execution: %s", e)
        errors.append(f"Subnode execution error: {e}")
        all_records = state.get("comparison_results", [])
        rollups = state.get("subnode_rollups", {})

    update_status(loan_id, current_node="comparison", errors=errors, node_history=history)

    return {
        **state,
        "comparison_results": all_records,
        "subnode_rollups": rollups,
        "errors": errors,
        "node_history": history,
    }


def route_after_checker(state: PipelineState) -> str:
    """Determines next step after Checker node: retry from fetch or proceed to scorecard."""
    checker_result = state.get("checker_result", {})
    if checker_result.get("will_retry", False):
        logger.info(
            "Checker node triggered retry for loan %s (attempt %s/%s). Routing back to fetch.",
            state["loan_id"],
            checker_result.get("retry_attempt"),
            checker_result.get("max_retries"),
        )
        return "fetch"
    return "scorecard"


def build_pipeline_graph():
    """Builds and compiles the LangGraph StateGraph pipeline."""
    graph = StateGraph(PipelineState)

    graph.add_node("fetch", node1_fetch)
    graph.add_node("extract", node2_extract)
    graph.add_node("comparison", comparison_node)
    graph.add_node("compile", node4_compile)
    graph.add_node("checker", node_checker)
    graph.add_node("scorecard", node5_scorecard)
    graph.add_node("push", node6_push)

    graph.set_entry_point("fetch")
    graph.add_edge("fetch", "extract")
    graph.add_edge("extract", "comparison")
    graph.add_edge("comparison", "compile")
    graph.add_edge("compile", "checker")
    graph.add_conditional_edges(
        "checker",
        route_after_checker,
        {
            "fetch": "fetch",
            "scorecard": "scorecard",
        },
    )
    graph.add_edge("scorecard", "push")
    graph.add_edge("push", END)

    return graph.compile()


# Pre-compiled graph instance
pipeline_app = build_pipeline_graph()


def run_pipeline(loan_id: str) -> dict:
    """Synchronously executes the full disbursement verification pipeline for a given loan_id."""
    initial_state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {},
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "retry_count": 0,
        "checker_result": {},
        "errors": [],
        "node_history": [],
    }

    logger.info("Triggering pipeline execution for loan: %s", loan_id)
    final_state = pipeline_app.invoke(initial_state)
    logger.info("Pipeline execution completed for loan: %s", loan_id)
    return final_state


def stream_pipeline(loan_id: str):
    """Yields progress events as each node in the LangGraph verification pipeline executes."""
    initial_state: PipelineState = {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {},
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "retry_count": 0,
        "checker_result": {},
        "errors": [],
        "node_history": [],
    }

    node_labels = {
        "fetch": "Node 1: Fetch (LOS & Documents Ingestion)",
        "extract": "Node 2: Extract (Docling & RapidOCR Field Extraction)",
        "comparison": "Node 3: Comparison (3a Identity, 3b Financial, 3c Dates/IDs)",
        "compile": "Node 4: Compile (Validation Report Aggregation)",
        "checker": "Node Checker: Consistency & Gate Verification",
        "scorecard": "Node 5: Scorecard (12 Checkpoints DGCL Evaluation)",
        "push": "Node 6: Push (LOS Status & Decision Update)",
    }

    logger.info("Triggering streaming pipeline execution for loan: %s", loan_id)
    yield {
        "stage": "start",
        "loan_id": loan_id,
        "status": "started",
        "label": "Pipeline Initiated",
        "node_history": [],
    }

    for step_output in pipeline_app.stream(initial_state):
        for node_name, state_update in step_output.items():
            yield {
                "stage": node_name,
                "loan_id": loan_id,
                "status": "completed",
                "label": node_labels.get(node_name, f"Node: {node_name}"),
                "subnode_rollups": state_update.get("subnode_rollups", {}),
                "checker_result": state_update.get("checker_result", {}),
                "errors": state_update.get("errors", []),
                "node_history": state_update.get("node_history", []),
            }

    yield {
        "stage": "finish",
        "loan_id": loan_id,
        "status": "done",
        "label": "Verification Complete",
        "node_history": ["fetch", "extract", "comparison", "compile", "checker", "scorecard", "push", "done"],
    }



