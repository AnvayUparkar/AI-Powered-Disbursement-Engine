"""Case serializer — transforms pipeline outputs and LOS records into frontend Case model."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import (
    DMS_DIR,
    FIELD_CRITICALITY_WEIGHTS,
    IST,
    LOS_LOANS_DIR,
    S3_EXTRACTED_DIR,
    S3_EXTRACTED_STRUCTURED_DIR,
    S3_RAW_DIR,
    S3_RESULT_DIR,
)
from pipeline.storage import (
    get_s3_los,
    list_loan_ids,
    read_json,
)

from .case_context import (
    CaseContext,
    build_checkpoint,
    build_evidence,
    build_field,
    compute_checkpoint_confidence,
    format_date_dmy,
    format_datetime_dmy_12h,
    format_time_12h,
    inr_format,
)
from .checkpoint_builders import build_all_checkpoints

# Backward compatibility alias
_compute_checkpoint_confidence = compute_checkpoint_confidence

logger = logging.getLogger("disbursement_pipeline.serializer")


def get_case_results(loan_id: str) -> dict[str, Any]:
    """Reads result artifacts for a loan without executing pipeline."""
    res_dir = S3_RESULT_DIR / loan_id
    comp_file = res_dir / "comparison_results.json"
    status_file = res_dir / "status.json"
    rollups_file = res_dir / "subnode_rollups.json"
    scorecard_file = res_dir / "scorecard.json"

    comp_results: list[dict[str, Any]] = []
    if comp_file.exists():
        try:
            raw_comp = read_json(comp_file)
            if isinstance(raw_comp, list):
                comp_results = [r for r in raw_comp if isinstance(r, dict)]
            elif isinstance(raw_comp, dict):
                if "results" in raw_comp and isinstance(raw_comp["results"], list):
                    comp_results = [r for r in raw_comp["results"] if isinstance(r, dict)]
                elif "checks" in raw_comp and isinstance(raw_comp["checks"], list):
                    comp_results = [r for r in raw_comp["checks"] if isinstance(r, dict)]
                else:
                    comp_results = [v for v in raw_comp.values() if isinstance(v, dict)]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Error reading comparison results for %s: %s", loan_id, exc)
            comp_results = []

    rollups: dict[str, Any] = {}
    if rollups_file.exists():
        try:
            data = read_json(rollups_file)
            rollups = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Error reading subnode rollups for %s: %s", loan_id, exc)
            rollups = {}

    status_data: dict[str, Any] = {}
    if status_file.exists():
        try:
            data = read_json(status_file)
            status_data = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Error reading status data for %s: %s", loan_id, exc)
            status_data = {}
    else:
        status_data = {"status": "PROCESSING", "node_history": []}

    scorecard_data: dict[str, Any] = {}
    if scorecard_file.exists():
        try:
            data = read_json(scorecard_file)
            scorecard_data = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Error reading scorecard data for %s: %s", loan_id, exc)
            scorecard_data = {}

    return {
        "comparison_results": comp_results,
        "subnode_rollups": rollups,
        "status_data": status_data,
        "scorecard_data": scorecard_data,
    }


def _load_extracted_docs(loan_id: str) -> dict[str, dict[str, Any]]:
    """Loads document dictionaries from s3_extracted_structured and s3_extracted."""
    from config.doc_types import get_canonical_doc_type

    docs: dict[str, dict[str, Any]] = {}

    struct_dir = S3_EXTRACTED_STRUCTURED_DIR / loan_id
    if struct_dir.exists():
        for f in struct_dir.glob("*.json"):
            try:
                data = read_json(f)
                if isinstance(data, dict):
                    canon_key = get_canonical_doc_type(f.stem)
                    docs[canon_key] = data
                    docs[f.stem] = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read structured file %s for loan %s: %s", f.name, loan_id, exc)

    ext_dir = S3_EXTRACTED_DIR / loan_id
    if ext_dir.exists():
        for f in ext_dir.glob("*.json"):
            if f.stem.endswith("_structured"):
                continue
            canon_key = get_canonical_doc_type(f.stem)
            try:
                data = read_json(f)
                if isinstance(data, dict):
                    if canon_key not in docs:
                        docs[canon_key] = data
                    docs[f.stem] = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read extracted file %s for loan %s: %s", f.name, loan_id, exc)

    return docs


def _discover_raw_document_names(loan_id: str, docs: dict[str, dict[str, Any]]) -> list[str]:
    """Discovers real uploaded document filenames from S3_RAW_DIR and DMS_DIR."""
    raw_dir = S3_RAW_DIR / loan_id
    dms_dir = DMS_DIR / loan_id
    real_doc_names: list[str] = []

    valid_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".zip", ".xml"}

    if raw_dir.exists():
        for f in raw_dir.iterdir():
            if (
                f.is_file()
                and f.name != f"{loan_id}.json"
                and not f.name.endswith(".metadata.json")
                and f.suffix.lower() in valid_extensions
            ):
                real_doc_names.append(f.name)

    if dms_dir.exists():
        for f in dms_dir.iterdir():
            if (
                f.is_file()
                and f.name != f"{loan_id}.json"
                and not f.name.endswith(".metadata.json")
                and not f.name.endswith(".json")
                and f.name not in real_doc_names
            ):
                real_doc_names.append(f.name)

    from config.doc_types import get_canonical_doc_type

    if not real_doc_names and docs:
        ignored_keys = {f"{loan_id}.json", "status.json", "dms_status.json", "face_embeddings.json"}
        seen_keys: set[str] = set()
        for dk in docs:
            if dk in ignored_keys:
                continue
            canon = get_canonical_doc_type(dk)
            if canon not in seen_keys:
                seen_keys.add(canon)
                real_doc_names.append(f"{canon}.pdf")

    # Deduplicate while preserving order across canonical document types
    deduped: list[str] = []
    seen_canon: set[str] = set()
    for name in real_doc_names:
        canon = get_canonical_doc_type(Path(name).stem)
        if canon not in seen_canon:
            seen_canon.add(canon)
            deduped.append(name)

    return deduped


def _build_case_context(loan_id: str) -> CaseContext:
    """Builds and populates a complete typed CaseContext from storage artifacts."""
    los_data: dict[str, Any] = get_s3_los(loan_id) or {}
    if not los_data:
        los_file = LOS_LOANS_DIR / f"{loan_id}.json"
        if los_file.exists():
            try:
                loaded = read_json(los_file)
                los_data = loaded if isinstance(loaded, dict) else {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read LOS file for %s: %s", loan_id, exc)
                los_data = {}

    docs = _load_extracted_docs(loan_id)
    app_form = docs.get("application_form") or docs.get("appform") or {}
    memo_doc = docs.get("disbursal_memo") or docs.get("memo") or {}

    results_data = get_case_results(loan_id)
    records: list[dict[str, Any]] = [
        r for r in (results_data.get("comparison_results") or []) if isinstance(r, dict)
    ]
    status_data: dict[str, Any] = results_data.get("status_data") or {}
    scorecard_data: dict[str, Any] = results_data.get("scorecard_data") or {}
    subnode_rollups: dict[str, Any] = results_data.get("subnode_rollups") or {}

    records_by_id: dict[str, dict[str, Any]] = {
        r["check_id"]: r for r in records if r.get("check_id")
    }
    records_by_field: dict[str, list[dict[str, Any]]] = {}
    records_by_subnode: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        fld = r.get("field", "")
        if fld:
            records_by_field.setdefault(fld, []).append(r)
        sub = r.get("subnode", "")
        if sub:
            records_by_subnode.setdefault(sub, []).append(r)

    real_doc_names = _discover_raw_document_names(loan_id, docs)
    doc_ids = [
        f"doc-{loan_id}-{Path(n).stem.lower().replace(' ', '_')}" for n in real_doc_names
    ]

    raw_amount = los_data.get("funding_amount") or los_data.get("loan_amount") or app_form.get("loan_amount")
    try:
        loan_amount = float(raw_amount) if raw_amount is not None else 0.0
    except (ValueError, TypeError):
        loan_amount = 0.0

    raw_disbursal = memo_doc.get("disbursal_amount") or memo_doc.get("loan_amount")
    try:
        disbursal_amount = (
            float(raw_disbursal)
            if raw_disbursal is not None
            else (round(loan_amount * 0.9, 2) if loan_amount > 0 else 0.0)
        )
    except (ValueError, TypeError):
        disbursal_amount = 0.0

    applicant_name = str(
        los_data.get("applicant_name") or app_form.get("applicant_name") or "Unknown Applicant"
    )
    app_id = str(
        los_data.get("application_id")
        or los_data.get("loan_id")
        or app_form.get("application_id")
        or f"APP-{loan_id}"
    )
    loan_type = str(los_data.get("loan_type") or "Unspecified")

    is_bt_flag = los_data.get("balance_transfer") or los_data.get("Balance_transfer") or 0
    try:
        is_bt = int(is_bt_flag) == 1
    except (ValueError, TypeError):
        is_bt = False

    return CaseContext(
        loan_id=loan_id,
        los_data=los_data,
        docs=docs,
        real_doc_names=real_doc_names,
        doc_ids=doc_ids,
        records=records,
        records_by_id=records_by_id,
        records_by_field=records_by_field,
        records_by_subnode=records_by_subnode,
        status_data=status_data,
        scorecard_data=scorecard_data,
        subnode_rollups=subnode_rollups,
        loan_amount=loan_amount,
        disbursal_amount=disbursal_amount,
        applicant_name=applicant_name,
        app_id=app_id,
        loan_type=loan_type,
        is_bt=is_bt,
        raw_dir=S3_RAW_DIR / loan_id,
        dms_dir=DMS_DIR / loan_id,
        extracted_dir=S3_EXTRACTED_DIR / loan_id,
        extracted_structured_dir=S3_EXTRACTED_STRUCTURED_DIR / loan_id,
        result_dir=S3_RESULT_DIR / loan_id,
    )


def _resolve_case_status_and_score(
    checkpoints: list[dict[str, Any]],
    scorecard_data: dict[str, Any],
    status_data: dict[str, Any],
    records: list[dict[str, Any]],
    docs: dict[str, dict[str, Any]],
) -> tuple[str, str, float, int, int, int]:
    """Computes aggregate checkpoint counts, overall status, risk level, and DGCL score."""
    verified_count = sum(1 for cp in checkpoints if cp["status"] == "VERIFIED")
    discrepancy_count = sum(1 for cp in checkpoints if cp["status"] == "DISCREPANCY")
    review_count = sum(1 for cp in checkpoints if cp["status"] == "INDETERMINATE")

    scorecard_score = scorecard_data.get("overall_score")
    scorecard_decision = scorecard_data.get("preliminary_decision")
    scorecard_tier = scorecard_data.get("risk_tier")

    if (
        discrepancy_count > 0
        or scorecard_decision == "REJECT_OR_FLAG"
        or scorecard_tier == "HIGH_RISK"
    ):
        overall_status = "DISCREPANCY"
        risk_level = "HIGH"
        dgcl_score = (
            float(scorecard_score)
            if scorecard_score is not None
            else max(25.0, 100.0 - (discrepancy_count * 25.0 + review_count * 10.0))
        )
    elif review_count > 0 and (
        status_data.get("status") == "PROCESSING" or (not records and not docs)
    ):
        overall_status = "PROCESSING"
        risk_level = "LOW"
        dgcl_score = 0.0
    elif (
        review_count > 0
        or scorecard_decision == "MANUAL_REVIEW"
        or scorecard_tier == "MEDIUM_RISK"
    ):
        overall_status = "INDETERMINATE"
        risk_level = "MEDIUM"
        dgcl_score = (
            float(scorecard_score)
            if scorecard_score is not None
            else max(65.0, 100.0 - (review_count * 12.0))
        )
    else:
        overall_status = "VERIFIED"
        risk_level = "LOW"
        dgcl_score = float(scorecard_score) if scorecard_score is not None else 97.4

    return overall_status, risk_level, dgcl_score, verified_count, discrepancy_count, review_count


def _build_processing_steps(
    loan_id: str,
    status_data: dict[str, Any],
    dgcl_score: float,
) -> tuple[list[dict[str, Any]], str]:
    """Generates the sequential workflow steps and formatted updated timestamp in IST."""
    history = status_data.get(
        "node_history",
        [
            "fetch_los",
            "fetch_dms",
            "idp_scan",
            "llm_structure",
            "check_parallel",
            "compile_report",
            "generate_scorecard",
            "push_results",
            "done",
        ],
    )
    step_defs = [
        ("fetch_los", "System", "LOS Ingestion", 99.5),
        ("fetch_dms", "System", "DMS Document Fetch", 99.5),
        ("idp_scan", "PaddleOCR", "Document OCR & Layout Scan", 98.2),
        ("llm_structure", "LLM", "Field Structuring & Normalization", 97.5),
        ("check_parallel", "Validation", "KYC, Financial & Loan Application Checks", 96.8),
        ("compile_report", "Engine", "Report Compilation & Aggregation", 99.0),
        ("generate_scorecard", "DGCL Engine", "Scorecard Generation", dgcl_score),
        ("push_results", "System", "LOS Result Push", 100.0),
    ]

    raw_upd = status_data.get("updated_at")
    base_time: datetime | None = None
    if raw_upd:
        try:
            dt = datetime.fromisoformat(raw_upd.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            base_time = dt.astimezone(IST)
            formatted_last_updated = format_datetime_dmy_12h(base_time)
        except (ValueError, TypeError) as exc:
            logger.debug("Failed parsing updated_at '%s' for loan %s: %s", raw_upd, loan_id, exc)
            formatted_last_updated = format_datetime_dmy_12h(raw_upd)
    else:
        formatted_last_updated = format_datetime_dmy_12h(datetime.now(IST))

    if not base_time:
        base_time = datetime.now(IST)

    proc_steps: list[dict[str, Any]] = []
    for i, (node_key, component, label, conf) in enumerate(step_defs):
        is_done = node_key in history or "done" in history
        step_start_dt = base_time - timedelta(seconds=(len(step_defs) - i) * 3)
        step_end_dt = base_time - timedelta(seconds=(len(step_defs) - i - 1) * 3)
        start_t = format_time_12h(step_start_dt)
        end_t = format_time_12h(step_end_dt)
        proc_steps.append({
            "id": f"step-{loan_id}-{node_key}",
            "component": component,
            "status": "COMPLETED" if is_done else "PENDING",
            "detail": f"{label} {'completed' if is_done else 'pending'}",
            "startedAt": start_t,
            "completedAt": end_t if is_done else None,
            "confidence": conf,
        })

    return proc_steps, formatted_last_updated


def _compute_dynamic_processing_time(
    loan_id: str,
    status_data: dict[str, Any],
    doc_ids: list[str],
    has_records: bool,
) -> tuple[str, int]:
    """Computes dynamic processing duration and formatted string."""
    if not has_records and not doc_ids:
        return "—", 0

    started_at_str = status_data.get("started_at")
    completed_at_str = status_data.get("completed_at") or status_data.get("updated_at")

    elapsed_seconds: int | None = None
    if started_at_str and completed_at_str:
        try:
            start_dt = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
            diff = (end_dt - start_dt).total_seconds()
            if diff >= 0:
                elapsed_seconds = max(1, round(diff))
        except (ValueError, TypeError):
            elapsed_seconds = None

    if elapsed_seconds is None or elapsed_seconds <= 0:
        history = status_data.get("node_history", [])
        elapsed_seconds = max(1, len(history) * 2)

    minutes = elapsed_seconds // 60
    seconds = elapsed_seconds % 60
    if minutes > 0:
        time_str = f"{minutes}m {seconds:02d}s"
    else:
        time_str = f"{seconds}s"

    return time_str, elapsed_seconds


def serialize_case(loan_id: str) -> dict[str, Any]:
    """Serializes a loan record into the complete frontend Case schema."""
    ctx = _build_case_context(loan_id)
    checkpoints = build_all_checkpoints(ctx)

    overall_status, risk_level, dgcl_score, verified_cnt, discrepancy_cnt, review_cnt = (
        _resolve_case_status_and_score(
            checkpoints=checkpoints,
            scorecard_data=ctx.scorecard_data,
            status_data=ctx.status_data,
            records=ctx.records,
            docs=ctx.docs,
        )
    )

    proc_steps, formatted_last_updated = _build_processing_steps(
        loan_id=loan_id,
        status_data=ctx.status_data,
        dgcl_score=dgcl_score,
    )

    proc_time_str, proc_time_sec = _compute_dynamic_processing_time(
        loan_id=loan_id,
        status_data=ctx.status_data,
        doc_ids=ctx.doc_ids,
        has_records=bool(ctx.records),
    )

    raw_login = ctx.los_data.get("login_date")
    formatted_login = format_date_dmy(raw_login) or datetime.now(IST).strftime("%d/%m/%Y")
    formatted_disbursal = (
        datetime.now(IST).strftime("%d/%m/%Y") if overall_status == "VERIFIED" else None
    )

    return {
        "id": loan_id,
        "applicant": ctx.applicant_name,
        "applicationId": ctx.app_id,
        "loanType": ctx.loan_type,
        "loanAmount": ctx.loan_amount,
        "disbursalAmount": ctx.disbursal_amount,
        "loginDate": formatted_login,
        "disbursalDate": formatted_disbursal,
        "documentCount": len(ctx.doc_ids),
        "processingTime": proc_time_str,
        "processingTimeSeconds": proc_time_sec,
        "dgclScore": round(dgcl_score, 1),
        "dgcl_score": round(dgcl_score, 1),
        "score": round(dgcl_score, 1),
        "verifiedCount": verified_cnt,
        "discrepancyCount": discrepancy_cnt,
        "reviewCount": review_cnt,
        "status": overall_status,
        "riskLevel": risk_level,
        "lastUpdated": formatted_last_updated,
        "balanceTransfer": 1 if ctx.is_bt else 0,
        "isBalanceTransfer": ctx.is_bt,
        "checkpoints": checkpoints,
        "documentIds": ctx.doc_ids,
        "processingSteps": proc_steps,
    }


def serialize_all_cases() -> list[dict[str, Any]]:
    """Serializes all loan records found in LOS storage."""
    loan_ids = list_loan_ids()
    cases: list[dict[str, Any]] = []
    for lid in loan_ids:
        try:
            c = serialize_case(lid)
            cases.append(c)
        except Exception:
            logger.exception("Error serializing case %s", lid)
    return cases
