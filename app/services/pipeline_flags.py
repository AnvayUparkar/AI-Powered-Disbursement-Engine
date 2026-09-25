"""System-wide feature flags — currently just the DGCL verification pipeline kill-switch.

This POC's core value is the OCR/IDP scan; the full LangGraph verification pipeline
(check_kyc/check_financial/check_loan_app/generate_scorecard) is kept off by default and must
be turned on explicitly. The flag is global (not per-tenant) and lives on the shared RWX volume
so every pod (api, worker, idp) reads/writes the same value — see config/paths.py.
"""
import logging
import threading

from config.paths import PIPELINE_FLAGS_FILE
from pipeline.storage import read_json, write_json

logger = logging.getLogger("disbursement_pipeline.pipeline_flags")

_lock = threading.Lock()

# Absent file == disabled. This is the safe default: a fresh deployment, or a volume that lost
# the file, must never silently run the full pipeline.
_DEFAULT_ENABLED = False


def is_dgcl_pipeline_enabled() -> bool:
    with _lock:
        try:
            data = read_json(PIPELINE_FLAGS_FILE)
        except FileNotFoundError:
            return _DEFAULT_ENABLED
        except (OSError, ValueError) as exc:
            logger.warning("Could not read pipeline flags (%s); defaulting to disabled.", exc)
            return _DEFAULT_ENABLED
    return bool(data.get("dgcl_pipeline_enabled", _DEFAULT_ENABLED))


def set_dgcl_pipeline_enabled(enabled: bool) -> None:
    with _lock:
        write_json(PIPELINE_FLAGS_FILE, {"dgcl_pipeline_enabled": bool(enabled)})
    logger.info("DGCL verification pipeline %s", "ENABLED" if enabled else "DISABLED")
