"""Celery application and asynchronous tasks for the Automated Disbursement Scorecard."""
import os
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import logging
from typing import Any, Dict
import httpx
from celery import Celery

from config.settings import (
    IDP_REQUEST_TIMEOUT,
    IDP_SERVICE_URL,
    MAX_DOC_WORKERS,
    REDIS_URL,
)
from app.services.document_registry import document_registry

logger = logging.getLogger("disbursement_pipeline.celery")

def _is_redis_available(redis_url: str) -> bool:
    try:
        import redis
        client = redis.Redis.from_url(redis_url, socket_timeout=1.0)
        return client.ping()
    except Exception:
        return False

redis_online = _is_redis_available(REDIS_URL)

app = Celery(
    "disbursement_scorecard",
    broker=REDIS_URL if redis_online else "memory://",
    backend=REDIS_URL if redis_online else "cache+memory://",
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    worker_pool="threads",  # Windows compatibility mode
    worker_concurrency=MAX_DOC_WORKERS,  # Configured dynamically from MAX_DOC_WORKERS in .env
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

if not redis_online:
    logger.warning("Redis server is offline at %s. Enabling Celery ALWAYS_EAGER in-memory execution mode.", REDIS_URL)
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )


@app.task(bind=True, name="pipeline.tasks.run_pipeline_task")
def run_pipeline_task(self, loan_id: str) -> Dict[str, Any]:
    """Executes the end-to-end LangGraph pipeline asynchronously in a background worker."""
    logger.info("Celery task %s started for loan: %s", self.request.id, loan_id)
    try:
        from pipeline.graph import run_pipeline
        result_state = run_pipeline(loan_id)
        scorecard = result_state.get("scorecard", {})
        logger.info(
            "Celery task %s completed for loan: %s. Decision: %s, Score: %s",
            self.request.id,
            loan_id,
            scorecard.get("preliminary_decision"),
            scorecard.get("overall_score"),
        )
        return {
            "loan_id": loan_id,
            "status": "completed",
            "scorecard": scorecard,
            "errors": result_state.get("errors", []),
        }
    except Exception as e:
        logger.exception("Celery task %s failed for loan %s: %s", self.request.id, loan_id, e)
        return {
            "loan_id": loan_id,
            "status": "failed",
            "error": str(e),
        }


@app.task(bind=True, name="pipeline.tasks.process_document_task", max_retries=2, default_retry_delay=10)
def process_document_task(self, doc_id: str, file_path: str, case_id: str | None = None) -> dict:
    """Call 8001 via HTTP to run IDP on a single document. Result is written back to document_registry and S3 extracted tier."""
    logger.info("process_document_task %s started for doc: %s (case: %s)", self.request.id, doc_id, case_id)
    try:
        with httpx.Client(timeout=IDP_REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{IDP_SERVICE_URL}/api/v1/documents/process",
                json={"document_id": doc_id, "s3_key": file_path},
            )
            resp.raise_for_status()

        payload = resp.json()
        result = payload.get("result") or {}
        parsed_json = None

        try:
            with httpx.Client(timeout=IDP_REQUEST_TIMEOUT) as client:
                get_r = client.get(f"{IDP_SERVICE_URL}/api/v1/documents/{doc_id}")
                if get_r.status_code == 200:
                    parsed_json = get_r.json()
                    result = {
                        **result,
                        **parsed_json,
                        "raw_text": parsed_json.get("text") or parsed_json.get("raw_text") or result.get("raw_text", ""),
                        "formatted_text": parsed_json.get("formatted_text") or result.get("formatted_text", ""),
                        "extracted_fields": (parsed_json.get("custom_metadata") or {}).get("llm_extracted_fields") or parsed_json.get("extracted_fields") or result.get("extracted_fields", {}),
                        "field_locations": (parsed_json.get("custom_metadata") or {}).get("field_locations") or result.get("field_locations", {}),
                        "ocr_tokens": (parsed_json.get("custom_metadata") or {}).get("ocr_tokens") or result.get("ocr_tokens", []),
                        "elements": parsed_json.get("elements", []),
                        "tables": parsed_json.get("tables", []),
                    }
        except Exception as enrich_err:
            logger.debug("Additional document enrichment note: %s", enrich_err)

        # Write extracted result back to document_registry so 8000 can serve it
        document_registry.update_extracted_result(doc_id, result)

        # Sync Celery output to S3 Extracted tier for the case so idp_scan hits cache instantly
        resolved_case_id = case_id
        if not resolved_case_id and "s3_raw" in file_path:
            parts = Path(file_path).parts
            if "s3_raw" in parts:
                idx = parts.index("s3_raw")
                if idx + 1 < len(parts) - 1:
                    resolved_case_id = parts[idx + 1]

        if resolved_case_id and resolved_case_id != "GENERAL":
            from config.doc_types import get_canonical_doc_type
            from pipeline.storage import save_s3_extracted
            from pipeline.nodes.idp_scan import build_idp_result_from_parsed
            from idp.models.document import ParsedDocument

            doc_key = get_canonical_doc_type(Path(file_path).name)
            if parsed_json:
                try:
                    parsed_doc = ParsedDocument.model_validate(parsed_json)
                    idp_scan_dict = build_idp_result_from_parsed(parsed_doc, doc_type=doc_key, doc_id=doc_id)
                    save_s3_extracted(resolved_case_id, doc_key, idp_scan_dict)
                    logger.info("Persisted Celery IDP scan extraction to S3 tier for case %s, doc %s", resolved_case_id, doc_key)
                except Exception as save_err:
                    logger.warning("Failed writing IDP scan extraction to S3 tier for case %s: %s", resolved_case_id, save_err)

        try:
            from app.services.registry.case_scanner import invalidate_case_cache
            invalidate_case_cache()
        except Exception as cache_err:
            logger.debug("Cache invalidation note: %s", cache_err)

        logger.info("process_document_task %s completed for doc: %s", self.request.id, doc_id)
        return {"doc_id": doc_id, "status": "completed", "result": result}

    except httpx.ConnectError as exc:
        logger.error("process_document_task: 8001 unreachable for %s: %s", doc_id, exc)
        raise self.retry(exc=exc)
    except httpx.TimeoutException as exc:
        logger.error("process_document_task timed out waiting for 8001 for %s after %ss: %s. Not retrying to prevent duplicate OCR execution.", doc_id, IDP_REQUEST_TIMEOUT, exc)
        return {"doc_id": doc_id, "status": "failed", "error": f"IDP processing timed out after {IDP_REQUEST_TIMEOUT}s"}
    except Exception as exc:
        logger.exception("process_document_task failed for %s: %s", doc_id, exc)
        return {"doc_id": doc_id, "status": "failed", "error": str(exc)}


