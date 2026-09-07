"""Node: Fetch LOS — Fetches loan data from LOS source and stages to S3 LOS tier."""
import json
import logging
from typing import Any, Dict

from config import LOS_LOANS_DIR
from pipeline.state import PipelineState
from pipeline.storage import read_json, save_s3_los, update_status

logger = logging.getLogger("disbursement_pipeline.fetch_los")


def fetch_los(state: PipelineState) -> PipelineState:
    """Fetches LOS loan data given an application/loan ID and persists it into S3 LOS tier."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("fetch_los")

    logger.info("Executing fetch_los for loan: %s", loan_id)

    los_file = LOS_LOANS_DIR / f"{loan_id}.json"
    los_data: Dict[str, Any] = {}

    if los_file.exists():
        try:
            los_data = read_json(los_file)
            save_s3_los(loan_id, los_data)
            logger.info("Successfully fetched and staged LOS data for %s", loan_id)
        except (json.JSONDecodeError, OSError) as e:
            msg = f"Failed to read/stage LOS file {los_file}: {e}"
            logger.error(msg)
            errors.append(msg)
    else:
        # Fallback: Check loans.db SQLite database
        db_path = LOS_LOANS_DIR / "loans.db"
        if db_path.exists():
            import sqlite3
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM loan_applications WHERE loan_id = ?", (loan_id,))
                row = cur.fetchone()
                conn.close()
                if row:
                    los_data = dict(row)
                    save_s3_los(loan_id, los_data)
                    logger.info("Successfully fetched %s from loans.db and staged to S3 LOS", loan_id)
            except Exception as e:
                logger.warning("Error querying loans.db for %s: %s", loan_id, e)

        if not los_data:
            msg = f"LOS loan record not found for ID: {loan_id}"
            logger.warning(msg)
            errors.append(msg)

    update_status(loan_id, current_node="fetch_los", errors=errors, node_history=history)

    return {
        **state,
        "los_data": los_data,
        "errors": errors,
        "node_history": history,
    }
