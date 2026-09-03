import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.serializers.case_serializer import serialize_all_cases, serialize_case
from config import LOS_LOANS_DIR, S3_RAW_DIR, S3_RESULT_DIR
from pipeline.graph import run_pipeline, stream_pipeline
from pipeline.storage import list_loan_ids, read_json, write_json

logger = logging.getLogger("disbursement_pipeline.api.cases")

router = APIRouter(prefix="/api/cases", tags=["Cases"])


def _get_next_loan_id() -> str:
    loan_ids = list_loan_ids()
    max_num = 0
    for lid in loan_ids:
        match = re.search(r"LOAN_(\d+)", lid, re.IGNORECASE)
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"LOAN_{max_num + 1:03d}"


class CreateCaseRequest(BaseModel):
    case_id: Optional[str] = None
    applicant_name: Optional[str] = None
    loan_type: Optional[str] = None
    loan_amount: Optional[float] = None
    tenure_months: Optional[int] = None


@router.get("/next-id", summary="Get next auto-assigned Case ID")
def get_next_case_id():
    return {"nextId": _get_next_loan_id()}


@router.post("/create", summary="Create a new loan case")
def create_case(payload: CreateCaseRequest):
    case_id = payload.case_id or _get_next_loan_id()
    LOS_LOANS_DIR.mkdir(parents=True, exist_ok=True)
    raw_dir = S3_RAW_DIR / case_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    los_file = LOS_LOANS_DIR / f"{case_id}.json"
    los_data = {
        "loan_id": case_id,
        "application_id": f"APP-{case_id}",
        "applicant_name": payload.applicant_name or "Unknown Applicant",
        "loan_type": payload.loan_type or "Unspecified",
        "funding_amount": payload.loan_amount,
        "tenure_months": payload.tenure_months,
        "login_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "DRAFT",
    }
    write_json(los_file, los_data)
    logger.info("Created new case %s", case_id)
    return {
        "caseId": case_id,
        "status": "created",
        "case": los_data,
    }


@router.get("", summary="List cases with search, filtering, and pagination")
def list_cases(
    query: str | None = Query(None, description="Search term for applicant name, case ID, or application ID"),
    status: str | None = Query(None, description="Filter by status (VERIFIED, DISCREPANCY, INDETERMINATE, ALL)"),
    risk: str | None = Query(None, description="Filter by risk level (LOW, MEDIUM, HIGH, ALL)"),
    loanType: str | None = Query(None, description="Filter by loan type"),
    dateFrom: str | None = Query(None),
    dateTo: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
    sortKey: str | None = Query(None),
    sortDir: str | None = Query("asc"),
):
    items = serialize_all_cases()

    if query:
        q = query.lower()
        items = [
            c for c in items
            if q in c["id"].lower() or q in c["applicant"].lower() or q in c["applicationId"].lower()
        ]

    if status and status != "ALL":
        items = [c for c in items if c["status"] == status]

    if risk and risk != "ALL":
        items = [c for c in items if c["riskLevel"] == risk]

    if loanType and loanType != "ALL":
        items = [c for c in items if c["loanType"] == loanType]

    if dateFrom:
        items = [c for c in items if c["loginDate"] >= dateFrom]

    if dateTo:
        items = [c for c in items if c["loginDate"] <= dateTo]

    if sortKey:
        reverse = (sortDir == "desc")
        items.sort(
            key=lambda x: (x.get(sortKey) is None, x.get(sortKey)),
            reverse=reverse,
        )

    total = len(items)
    start = (page - 1) * pageSize
    paged_items = items[start : start + pageSize]

    return {
        "items": paged_items,
        "total": total,
        "page": page,
        "pageSize": pageSize,
    }


@router.get("/recent", summary="Get recent cases")
def get_recent_cases(limit: int = 8):
    all_cases = serialize_all_cases()
    return all_cases[:limit]


@router.get("/loan-types", summary="Get distinct loan types")
def get_loan_types():
    all_cases = serialize_all_cases()
    types = sorted({c["loanType"] for c in all_cases})
    return types or ["Personal Loan", "Home Loan", "Auto Loan"]


@router.get("/{case_id}", summary="Get case details by ID")
def get_case(case_id: str):
    try:
        return serialize_case(case_id)
    except Exception:
        logger.exception("Failed getting case %s", case_id)
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}") from None


@router.post("/{case_id}/run", summary="Trigger verification engine pipeline")
def run_case_verification(case_id: str):
    try:
        run_pipeline(case_id)
        updated_case = serialize_case(case_id)
        return {
            "status": "completed",
            "case": updated_case,
        }
    except Exception:
        logger.exception("Pipeline run error for %s", case_id)
        raise HTTPException(status_code=500, detail="Pipeline run failed") from None


@router.get("/{case_id}/stream", summary="Stream live pipeline execution events via SSE")
def stream_case_verification(case_id: str):
    def event_generator():
        try:
            for event in stream_pipeline(case_id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.exception("Error in pipeline stream for %s", case_id)
            err_event = {
                "stage": "error",
                "loan_id": case_id,
                "status": "error",
                "message": str(e),
            }
            yield f"data: {json.dumps(err_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{case_id}/status", summary="Get case pipeline live status")
def get_case_status(case_id: str):
    status_file = S3_RESULT_DIR / case_id / "status.json"
    if not status_file.exists():
        return {
            "loan_id": case_id,
            "current_node": "pending",
            "node_history": [],
            "errors": [],
        }
    return read_json(status_file)


