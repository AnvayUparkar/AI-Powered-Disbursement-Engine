"""Celery application and asynchronous tasks for the Automated Disbursement Scorecard."""
import os
import logging
from typing import Any, Dict
from celery import Celery

logger = logging.getLogger("disbursement_pipeline.celery")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

app = Celery(
    "disbursement_scorecard",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    worker_pool="threads",  # Windows compatibility mode
    task_track_started=True,
    broker_connection_retry_on_startup=True,
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
