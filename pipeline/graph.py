"""Pipeline graph definition — LangGraph orchestration for disbursement verification."""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterator, List

from langgraph.graph import END, StateGraph

from pipeline.nodes.check_financial import check_financial
from pipeline.nodes.check_kyc import check_kyc
from pipeline.nodes.check_loan_application import check_loan_application
from pipeline.nodes.compile_report import compile_report
from pipeline.nodes.fetch_dms import fetch_dms
from pipeline.nodes.fetch_los import fetch_los
from pipeline.nodes.generate_scorecard import generate_scorecard
from pipeline.nodes.idp_scan import idp_scan
from pipeline.nodes.llm_structure import llm_structure
from pipeline.nodes.push_results import push_results
from pipeline.state import PipelineState
from pipeline.storage import update_status

logger = logging.getLogger("disbursement_pipeline.graph")


def _run_parallel_checkers(state: PipelineState) -> PipelineState:
    """Runs check_kyc, check_financial, and check_loan_application concurrently."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("check_parallel")

    logger.info("Executing concurrent verification checkers for loan: %s", loan_id)

    all_records: List[Dict[str, Any]] = []
    rollups: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="checker_worker") as executor:
        future_kyc = executor.submit(check_kyc, state)
        future_fin = executor.submit(check_financial, state)
        future_app = executor.submit(check_loan_application, state)

        checker_futures = [
            ("check_kyc", future_kyc),
            ("check_financial", future_fin),
            ("check_loan_application", future_app),
        ]

        for name, fut in checker_futures:
            try:
                res = fut.result()
                all_records.extend(res.get("records", []))
                rollups[name] = res.get("rollup", "Indeterminate")
            except Exception as e:  # noqa: BLE001
                logger.error("Error executing %s for loan %s: %s", name, loan_id, e)
                errors.append(f"{name} execution error: {e}")
                rollups[name] = "Indeterminate"

    # Also retain legacy rollup keys for backward-compatible frontend components
    legacy_rollups = {
        "loan_kyc": rollups.get("check_kyc", "Indeterminate"),
        "kfs_sanction": rollups.get("check_financial", "Indeterminate"),
        "topup_bt": rollups.get("check_loan_application", "Indeterminate"),
        **rollups,
    }

    update_status(loan_id, current_node="check_parallel", errors=errors, node_history=history)

    return {
        **state,
        "comparison_results": all_records,
        "subnode_rollups": legacy_rollups,
        "errors": errors,
        "node_history": history,
    }


def build_pipeline_graph():
    """Builds and compiles the clean LangGraph StateGraph pipeline without checker loops."""
    graph = StateGraph(PipelineState)

    graph.add_node("fetch_los", fetch_los)
    graph.add_node("fetch_dms", fetch_dms)
    graph.add_node("idp_scan", idp_scan)
    graph.add_node("llm_structure", llm_structure)
    graph.add_node("check_parallel", _run_parallel_checkers)
    graph.add_node("compile_report", compile_report)
    graph.add_node("generate_scorecard", generate_scorecard)
    graph.add_node("push_results", push_results)

    graph.set_entry_point("fetch_los")
    graph.add_edge("fetch_los", "fetch_dms")
    graph.add_edge("fetch_dms", "idp_scan")
    graph.add_edge("idp_scan", "llm_structure")
    graph.add_edge("llm_structure", "check_parallel")
    graph.add_edge("check_parallel", "compile_report")
    graph.add_edge("compile_report", "generate_scorecard")
    graph.add_edge("generate_scorecard", "push_results")
    graph.add_edge("push_results", END)

    return graph.compile()


pipeline_app = build_pipeline_graph()


def create_initial_state(loan_id: str) -> PipelineState:
    """Constructs a clean initial PipelineState."""
    return {
        "loan_id": loan_id,
        "los_data": {},
        "raw_doc_paths": {},
        "extracted_data": {},
        "extracted_structured_data": {},
        "face_embeddings": {},
        "dms_status": {},
        "otp_audit": {},
        "comparison_results": [],
        "subnode_rollups": {},
        "compiled_report": {},
        "scorecard": {},
        "errors": [],
        "node_history": [],
    }


def run_pipeline(loan_id: str) -> dict:
    """Synchronously executes the full disbursement verification pipeline for a given loan_id."""
    initial_state = create_initial_state(loan_id)
    logger.info("Triggering verification pipeline execution for loan: %s", loan_id)
    final_state = pipeline_app.invoke(initial_state)
    logger.info("Verification pipeline completed for loan: %s", loan_id)
    return final_state


def stream_pipeline(loan_id: str) -> Iterator[dict]:
    """Yields progress events as each node in the verification pipeline executes."""
    initial_state = create_initial_state(loan_id)

    node_labels = {
        "fetch_los": "Node 1: Fetch LOS (Loan Record Ingestion)",
        "fetch_dms": "Node 2: Fetch DMS (Document Ingestion)",
        "idp_scan": "Node 3: IDP Scan (Docling & OCR Processing)",
        "llm_structure": "Node 4: LLM Structure (Field Normalization)",
        "check_parallel": "Node 5: Verification (KYC, Financial & Loan Application)",
        "compile_report": "Node 6: Compile (Validation Report Aggregation)",
        "generate_scorecard": "Node 7: Scorecard (12 Checkpoints DGCL Evaluation)",
        "push_results": "Node 8: Push (LOS Status & Decision Update)",
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
                "errors": state_update.get("errors", []),
                "node_history": state_update.get("node_history", []),
            }

    yield {
        "stage": "finish",
        "loan_id": loan_id,
        "status": "done",
        "label": "Verification Complete",
        "node_history": ["fetch_los", "fetch_dms", "idp_scan", "llm_structure", "check_parallel", "compile_report", "generate_scorecard", "push_results", "done"],
    }
