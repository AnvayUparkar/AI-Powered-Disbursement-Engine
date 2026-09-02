import logging

from fastapi import APIRouter, HTTPException

from config import S3_RESULT_DIR
from pipeline.graph import run_pipeline
from pipeline.storage import list_loan_ids, read_json

logger = logging.getLogger("disbursement_pipeline.api.loans")

router = APIRouter(prefix="/api/loans", tags=["Loans"])


@router.get("", summary="List available loan IDs")
def get_loans():
    """Lists all available loan IDs present in mock LOS directory."""
    loan_ids = list_loan_ids()
    return {"loan_ids": loan_ids, "total": len(loan_ids)}


@router.post("/{loan_id}/run", summary="Trigger full pipeline run for loan")
def run_loan_pipeline(loan_id: str):
    """Synchronously triggers the full LangGraph verification pipeline and returns the scorecard."""
    try:
        final_state = run_pipeline(loan_id)
        return {
            "loan_id": loan_id,
            "status": "completed",
            "scorecard": final_state.get("scorecard"),
            "subnode_rollups": final_state.get("subnode_rollups"),
            "errors": final_state.get("errors"),
        }
    except Exception:
        logger.exception("Failed pipeline run for loan %s", loan_id)
        raise HTTPException(status_code=500, detail="Pipeline execution failed") from None


@router.get("/{loan_id}/status", summary="Get pipeline execution status")
def get_loan_status(loan_id: str):
    """Retrieves current node, history, and errors from status.json."""
    status_file = S3_RESULT_DIR / loan_id / "status.json"
    if not status_file.exists():
        raise HTTPException(status_code=404, detail=f"Status not found for loan {loan_id}. Has the pipeline run?")
    try:
        return read_json(status_file)
    except Exception:
        logger.exception("Error reading status for loan %s", loan_id)
        raise HTTPException(status_code=500, detail="Error reading status artifact") from None


@router.get("/{loan_id}/results", summary="Get comparison results and subnode rollups")
def get_loan_results(loan_id: str):
    """Retrieves comparison_results.json and subnode_rollups.json for a loan."""
    res_file = S3_RESULT_DIR / loan_id / "comparison_results.json"
    rollups_file = S3_RESULT_DIR / loan_id / "subnode_rollups.json"

    if not res_file.exists():
        raise HTTPException(status_code=404, detail=f"Results not found for loan {loan_id}. Has the pipeline run?")

    try:
        results = read_json(res_file)
        rollups = read_json(rollups_file) if rollups_file.exists() else {}
        return {
            "loan_id": loan_id,
            "subnode_rollups": rollups,
            "comparison_results": results,
        }
    except Exception:
        logger.exception("Error reading results for loan %s", loan_id)
        raise HTTPException(status_code=500, detail="Error reading results artifact") from None


@router.get("/{loan_id}/scorecard", summary="Get loan scorecard")
def get_loan_scorecard(loan_id: str):
    """Retrieves scorecard.json for a loan."""
    scorecard_file = S3_RESULT_DIR / loan_id / "scorecard.json"
    if not scorecard_file.exists():
        raise HTTPException(status_code=404, detail=f"Scorecard not found for loan {loan_id}. Has the pipeline run?")
    try:
        return read_json(scorecard_file)
    except Exception:
        logger.exception("Error reading scorecard for loan %s", loan_id)
        raise HTTPException(status_code=500, detail="Error reading scorecard artifact") from None

