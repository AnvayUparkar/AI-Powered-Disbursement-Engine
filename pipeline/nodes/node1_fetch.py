import json
import logging
import shutil

from config import DMS_DIR, LOS_LOANS_DIR, S3_RAW_DIR

from pipeline.state import PipelineState
from pipeline.storage import copy_dir, copy_file, read_json, update_status

logger = logging.getLogger("disbursement_pipeline.node1_fetch")


def node1_fetch(state: PipelineState) -> PipelineState:
    """Node 1 (Fetch) — LOS + DMS -> S3 raw.

    Copies pre-placed files into s3_raw/LOAN_<id>/ and loads LOS data.
    """
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("fetch")

    logger.info("Running Node 1 (Fetch) for loan: %s", loan_id)

    raw_loan_dir = S3_RAW_DIR / loan_id
    raw_loan_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch LOS JSON
    los_file = LOS_LOANS_DIR / f"{loan_id}.json"
    los_data = {}
    if los_file.exists():
        try:
            los_data = read_json(los_file)
            copy_file(los_file, raw_loan_dir / f"{loan_id}.json")
        except (json.JSONDecodeError, OSError, shutil.Error) as e:
            msg = f"Failed to load/copy LOS file {los_file}: {e}"
            logger.error(msg)
            errors.append(msg)
    else:
        msg = f"LOS file not found: {los_file}"
        logger.warning(msg)
        errors.append(msg)

    # 2. Copy DMS documents to S3 raw
    dms_loan_dir = DMS_DIR / loan_id
    raw_doc_paths = {}
    if dms_loan_dir.exists():
        try:
            copy_dir(dms_loan_dir, raw_loan_dir)
            for item in raw_loan_dir.iterdir():
                raw_doc_paths[item.name] = str(item)
        except (OSError, shutil.Error) as e:
            msg = f"Failed to copy DMS folder {dms_loan_dir}: {e}"
            logger.error(msg)
            errors.append(msg)
    else:
        msg = f"DMS folder not found: {dms_loan_dir}"
        logger.warning(msg)
        errors.append(msg)

    # 3. Pick up documents already in S3 raw (uploaded via the UI upload endpoint)
    #    These are not in DMS, so the DMS copy above misses them entirely.
    for item in raw_loan_dir.iterdir():
        if item.name not in raw_doc_paths and item.is_file():
            raw_doc_paths[item.name] = str(item)

    if raw_doc_paths:
        logger.info("Node 1: %d document(s) staged for processing: %s", len(raw_doc_paths), list(raw_doc_paths.keys()))


    update_status(loan_id, current_node="fetch", errors=errors, node_history=history)

    return {
        **state,
        "los_data": los_data,
        "raw_doc_paths": raw_doc_paths,
        "errors": errors,
        "node_history": history,
    }

