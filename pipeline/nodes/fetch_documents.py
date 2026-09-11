"""Worker 2: Fetch Documents — Validates and stages loan document package in S3 Raw tier."""
import logging
import shutil
from pathlib import Path
from typing import Dict

from config import DMS_DIR, S3_RAW_DIR
from pipeline.state import PipelineState
from pipeline.storage import copy_dir, update_status

logger = logging.getLogger("disbursement_pipeline.fetch_documents")


def fetch_documents(state: PipelineState) -> PipelineState:
    """Validates and stages raw document package into s3_raw/{loan_id}/."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("fetch_documents")

    logger.info("Executing fetch_documents for loan: %s", loan_id)

    raw_loan_dir = S3_RAW_DIR / loan_id
    raw_loan_dir.mkdir(parents=True, exist_ok=True)
    raw_doc_paths: Dict[str, str] = dict(state.get("raw_doc_paths", {}))

    # 1. Fallback: Ingest files from DMS directory if staged there during transition
    if DMS_DIR.exists():
        dms_loan_dir = DMS_DIR / loan_id
        if dms_loan_dir.exists():
            try:
                copy_dir(dms_loan_dir, raw_loan_dir)
                for item in raw_loan_dir.iterdir():
                    if item.is_file():
                        raw_doc_paths[item.name] = str(item)
                logger.info("Staged %d files from DMS staging directory to S3 Raw tier for %s", len(raw_doc_paths), loan_id)
            except (OSError, shutil.Error) as e:
                msg = f"Failed copying DMS folder {dms_loan_dir}: {e}"
                logger.error(msg)
                errors.append(msg)

    # 2. Pick up documents directly in S3 Raw tier (primary source of truth per PRD)
    if raw_loan_dir.exists():
        for item in raw_loan_dir.iterdir():
            if item.is_file() and item.name not in raw_doc_paths:
                raw_doc_paths[item.name] = str(item)

    logger.info("Total %d raw document(s) validated and staged in S3 Raw tier for %s", len(raw_doc_paths), loan_id)

    update_status(loan_id, current_node="fetch_documents", errors=errors, node_history=history)

    return {
        **state,
        "raw_doc_paths": raw_doc_paths,
        "errors": errors,
        "node_history": history,
    }


# Backward-compatibility alias
fetch_dms = fetch_documents
