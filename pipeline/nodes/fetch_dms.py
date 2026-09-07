"""Node: Fetch DMS — Fetches raw documents from DMS source and stages to S3 Raw tier."""
import logging
import shutil
from pathlib import Path
from typing import Dict

from config import DMS_DIR, S3_RAW_DIR
from pipeline.state import PipelineState
from pipeline.storage import copy_dir, update_status

logger = logging.getLogger("disbursement_pipeline.fetch_dms")


def fetch_dms(state: PipelineState) -> PipelineState:
    """Fetches raw documents from DMS for an application ID and stages them in S3 Raw tier."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("fetch_dms")

    logger.info("Executing fetch_dms for loan: %s", loan_id)

    raw_loan_dir = S3_RAW_DIR / loan_id
    raw_loan_dir.mkdir(parents=True, exist_ok=True)
    raw_doc_paths: Dict[str, str] = dict(state.get("raw_doc_paths", {}))

    # 1. Ingest files from DMS directory
    dms_loan_dir = DMS_DIR / loan_id
    if dms_loan_dir.exists():
        try:
            copy_dir(dms_loan_dir, raw_loan_dir)
            for item in raw_loan_dir.iterdir():
                if item.is_file():
                    raw_doc_paths[item.name] = str(item)
            logger.info("Copied %d files from DMS directory to S3 Raw tier for %s", len(raw_doc_paths), loan_id)
        except (OSError, shutil.Error) as e:
            msg = f"Failed to copy DMS folder {dms_loan_dir}: {e}"
            logger.error(msg)
            errors.append(msg)
    else:
        logger.info("No DMS folder found at %s; checking S3 Raw directly", dms_loan_dir)

    # 2. Pick up documents already in S3 raw (e.g. uploaded via UI or pre-staged)
    if raw_loan_dir.exists():
        for item in raw_loan_dir.iterdir():
            if item.is_file() and item.name not in raw_doc_paths:
                raw_doc_paths[item.name] = str(item)

    logger.info("Total %d raw document(s) staged in S3 Raw tier for %s", len(raw_doc_paths), loan_id)

    update_status(loan_id, current_node="fetch_dms", errors=errors, node_history=history)

    return {
        **state,
        "raw_doc_paths": raw_doc_paths,
        "errors": errors,
        "node_history": history,
    }
