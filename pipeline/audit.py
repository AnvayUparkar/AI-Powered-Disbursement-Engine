import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from config import S3_RESULT_DIR
from pipeline.storage import read_json, write_json


logger = logging.getLogger("disbursement_pipeline.audit")

_audit_lock = threading.RLock()


def append_audit_entry(loan_id: str, entry: dict[str, Any]) -> None:
    """Thread-safe append of an audit record to the loan's audit log."""
    with _audit_lock:
        audit_file = S3_RESULT_DIR / loan_id / "audit_log.json"
        audit_data: list[dict[str, Any]] = []
        if audit_file.exists():
            try:
                content = read_json(audit_file)
                if isinstance(content, list):
                    audit_data = content
                elif isinstance(content, dict):
                    audit_data = [content]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed reading existing audit log for loan %s: %s. Starting fresh list.", loan_id, e)
                audit_data = []

        timestamped_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **entry,
        }
        audit_data.append(timestamped_entry)
        write_json(audit_file, audit_data)
        logger.info("Audit entry logged for loan %s: %s", loan_id, timestamped_entry.get("type", "general"))

