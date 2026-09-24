"""
Gateway Documents Router (Port 8000 Core Application)

Handles document upload orchestration, registry synchronization, Celery task dispatching,
and HTTP proxying to the IDP microservice (Port 8001).
"""
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.document_registry import document_registry
from app.services.registry.dedup import SINGLETON_CANONICAL_TYPES
from config import (
    IDP_REQUEST_TIMEOUT,
    IDP_SERVICE_URL,
    S3_RAW_DIR,
    get_canonical_doc_type,
)

logger = logging.getLogger("disbursement_pipeline.gateway_documents")
router = APIRouter(prefix="/api/v1/documents", tags=["Gateway Documents"])

from shared.idp_contracts import DocumentStatusResponse, ProcessDocumentRequest


@router.post(
    "/upload",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_and_process_document(
    file: UploadFile = File(...),
    document_id: Optional[str] = Form(None),
    case_id: Optional[str] = Form(None),
    doc_type: Optional[str] = Form(None),
    s3_bucket: Optional[str] = Form(None),
    run_idp: Optional[bool] = Form(None),
):
    """
    Accept direct browser multipart document upload, store raw file in S3 tier,
    register in document_registry, and enqueue background Celery pipeline task.
    """
    case_val = (case_id.strip() if isinstance(case_id, str) and case_id.strip() else None) or "GENERAL"
    dtype_val = doc_type if isinstance(doc_type, str) and doc_type.strip() else None
    clean_filename = Path(file.filename or "uploaded_doc.pdf").name

    if isinstance(document_id, str) and document_id.strip():
        doc_id = document_id.strip()
    elif case_val != "GENERAL":
        canon = get_canonical_doc_type(dtype_val or clean_filename)
        clean_stem = Path(clean_filename).stem.lower().replace(" ", "_")
        if canon in SINGLETON_CANONICAL_TYPES and canon != "miscellaneous":
            doc_id = f"{case_val}_{canon}"
        else:
            doc_id = f"{case_val}_{clean_stem}"
    else:
        doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"

    logger.info("Received file upload for %s (doc_id=%s, case=%s)", clean_filename, doc_id, case_val)

    try:
        file_bytes = await file.read()
        raw_key = f"{doc_id}_{clean_filename}"

        case_raw_dir = S3_RAW_DIR / case_val
        case_raw_dir.mkdir(parents=True, exist_ok=True)
        target_path = case_raw_dir / clean_filename
        target_path.write_bytes(file_bytes)
        logger.info("Saved uploaded document to case S3 raw store at %s", target_path)

        output_url = f"s3://disbursement-documents/raw-documents/{raw_key}"

        # Also upload to shared mock S3 so IDP can download by relative key
        from shared.storage import S3Storage
        from shared.object_keys import raw_object_key
        _s3 = S3Storage()
        _key = raw_object_key(case_val, clean_filename)
        try:
            await _s3.upload(_key, file_bytes, content_type=file.content_type or "application/octet-stream")
        except Exception as _s3_err:
            logger.warning("Mock S3 upload failed for %s: %s", _key, _s3_err)

        # Determine whether to execute immediate background IDP extraction:
        # 1. If run_idp is explicitly requested, honor it.
        # 2. If uploaded directly to a specific loan case, default to False (pure S3 raw staging).
        # 3. If uploaded to General / Documents tab, default to True (immediate IDP).
        should_run_idp = run_idp if run_idp is not None else (case_val == "GENERAL")
        initial_status = "PROCESSING" if should_run_idp else "PENDING"

        try:
            document_registry.register_uploaded_document(
                doc_id=doc_id,
                filename=clean_filename,
                doc_type=dtype_val,
                case_id=case_val,
                file_size_bytes=len(file_bytes),
                parsed_result=None,
                status=initial_status,
            )
        except Exception as reg_err:
            logger.debug("Document registry sync notification for %s: %s", doc_id, reg_err)

        if should_run_idp:
            try:
                from pipeline.celery_app import process_document_task
                process_document_task.delay(doc_id, _key, case_val)
            except Exception as celery_err:
                logger.warning("Celery task enqueue notification for %s: %s", doc_id, celery_err)

        return DocumentStatusResponse(
            document_id=doc_id,
            processing_id=f"proc-{doc_id}",
            status="queued" if should_run_idp else "UPLOADED",
            output_location=output_url,
            processing_time_seconds=0.0,
            result=None,
        )
    except Exception as exc:
        logger.error("Unhandled upload exception for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "InternalServerError", "message": str(exc), "document_id": doc_id},
        )


@router.get(
    "/{document_id}",
    response_model=None,
    status_code=status.HTTP_200_OK,
)
async def get_document(document_id: str):
    """
    Retrieve document record from document_registry, falling back to IDP microservice over HTTP.
    """
    registry_doc = document_registry.get_by_id(document_id)
    if registry_doc:
        return registry_doc

    # Query IDP microservice over HTTP (Port 8001)
    try:
        async with httpx.AsyncClient(timeout=IDP_REQUEST_TIMEOUT) as client:
            resp = await client.get(f"{IDP_SERVICE_URL}/api/v1/documents/{document_id}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("IDP microservice query fallback for %s: %s", document_id, exc)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "NotFound", "message": f"Document {document_id} not found."},
    )


@router.post(
    "/process",
    response_model=None,
    status_code=status.HTTP_200_OK,
)
async def process_document_proxy(request: ProcessDocumentRequest):
    """
    Gateway proxy delegating document processing to IDP microservice on Port 8001.
    """
    try:
        async with httpx.AsyncClient(timeout=IDP_REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{IDP_SERVICE_URL}/api/v1/documents/process",
                json=request.model_dump(),
            )
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as exc:
        logger.error("Failed proxying process request for %s to IDP: %s", request.document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "BadGateway", "message": f"IDP service unavailable: {exc}"},
        )
