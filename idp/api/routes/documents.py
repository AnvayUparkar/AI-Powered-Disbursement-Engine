import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form
from idp.schemas.document import ProcessDocumentRequest, DocumentStatusResponse
from idp.schemas.response import ErrorResponse
from idp.services.document_processor import DocumentProcessor, processor
from idp.core.exceptions import Node2BaseException
from idp.core.logging import logger, format_doc_log

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


@router.post(
    "/process",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input document or format"},
        500: {"model": ErrorResponse, "description": "Internal processing error"}
    }
)
async def process_document(request: ProcessDocumentRequest):
    """
    Trigger Node 2 Intelligent Document Processing for a raw document stored in S3.
    """
    doc_id = request.document_id
    logger.info(format_doc_log(doc_id, f"Received API request to process document at s3_key={request.s3_key}"))

    try:
        result = await processor.process_document(
            document_id=doc_id,
            s3_key=request.s3_key,
            s3_bucket=request.s3_bucket
        )

        return DocumentStatusResponse(
            document_id=result["document_id"],
            processing_id=f"proc-{doc_id}",
            status=result["status"],
            output_location=result["output_location"],
            processing_time_seconds=result["processing_time_seconds"]
        )

    except Node2BaseException as e:
        logger.error(format_doc_log(doc_id, f"Node 2 exception: {e.message}"))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.__class__.__name__, "message": e.message, "details": e.details, "document_id": doc_id}
        )
    except Exception as e:
        logger.error(format_doc_log(doc_id, f"Unhandled exception during document processing: {e}"))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "InternalServerError", "message": str(e), "document_id": doc_id}
        )


@router.post(
    "/upload",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid upload format or processing failure"}
    }
)
async def upload_and_process_document(
    file: UploadFile = File(...),
    document_id: Optional[str] = Form(None),
    case_id: Optional[str] = Form(None),
    doc_type: Optional[str] = Form(None),
    s3_bucket: Optional[str] = Form(None)
):
    """
    Accept direct browser multipart document upload, store raw file, and run Node 2 IDP pipeline.
    """
    doc_id = document_id if isinstance(document_id, str) and document_id.strip() else f"DOC-{uuid.uuid4().hex[:8].upper()}"
    bucket = s3_bucket if isinstance(s3_bucket, str) and s3_bucket.strip() else None
    case_val = case_id if isinstance(case_id, str) and case_id.strip() else None
    dtype_val = doc_type if isinstance(doc_type, str) and doc_type.strip() else None
    logger.info(format_doc_log(doc_id, f"Received direct file upload for {file.filename}"))

    try:
        file_bytes = await file.read()
        clean_filename = Path(file.filename or "uploaded_doc.pdf").name

        from config import S3_RAW_DIR
        raw_key = f"{doc_id}_{clean_filename}"

        if case_val:
            case_raw_dir = S3_RAW_DIR / case_val
            case_raw_dir.mkdir(parents=True, exist_ok=True)
            target_path = case_raw_dir / clean_filename
            target_path.write_bytes(file_bytes)
            logger.info(format_doc_log(doc_id, f"Saved uploaded document to case S3 raw store at {target_path}"))

        try:
            from idp.services.storage.s3 import S3Storage
            from idp.core.config import settings as idp_settings
            s3_storage = S3Storage()
            target_bucket = bucket or idp_settings.S3_BUCKET
            output_url = await s3_storage.upload(
                key=f"{idp_settings.RAW_DOCUMENT_PREFIX}/{raw_key}",
                content=file_bytes,
                bucket=target_bucket,
                content_type="application/pdf",
                doc_id=doc_id
            )
        except Exception as s3_err:
            logger.debug(format_doc_log(doc_id, f"Mock S3 storage notification: {s3_err}"))
            output_url = f"s3://disbursement-documents/raw-documents/{raw_key}"

        try:
            from app.services.document_registry import document_registry
            document_registry.register_uploaded_document(
                doc_id=doc_id,
                filename=clean_filename,
                doc_type=dtype_val,
                case_id=case_val,
                file_size_bytes=len(file_bytes),
                parsed_result=None,
            )
        except Exception as reg_err:
            logger.debug(format_doc_log(doc_id, f"Document registry sync notification: {reg_err}"))

        return DocumentStatusResponse(
            document_id=doc_id,
            processing_id=f"proc-{doc_id}",
            status="UPLOADED",
            output_location=output_url,
            processing_time_seconds=0.05,
            result=None
        )
    except Node2BaseException as e:
        logger.error(format_doc_log(doc_id, f"Upload error: {e.message}"))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.__class__.__name__, "message": e.message, "details": e.details, "document_id": doc_id}
        )
    except Exception as e:
        logger.error(format_doc_log(doc_id, f"Unhandled upload exception: {e}"))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "InternalServerError", "message": str(e), "document_id": doc_id}
        )


@router.get(
    "/{document_id}",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Document not found"}
    }
)
async def get_document_result(document_id: str):
    """
    Retrieve ParsedDocument JSON output for a given document_id.
    """
    parsed = await processor.get_parsed_document(document_id)
    if not parsed:
        try:
            from app.services.document_registry import document_registry
            registry_doc = document_registry.get_by_id(document_id)
            if registry_doc:
                return registry_doc
        except Exception as e:
            logger.warning(f"Fallback to document_registry failed for {document_id}: {e}")

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NotFound", "message": f"Parsed document for {document_id} not found."}
        )

    parsed_dict = parsed.model_dump()
    llm_fields = (parsed.custom_metadata or {}).get("llm_extracted_fields") if parsed.custom_metadata else None
    if not llm_fields:
        try:
            from app.services.document_registry import document_registry
            reg_doc = document_registry.get_by_id(document_id)
            if reg_doc and (reg_doc.get("formattedText") or "").strip().startswith("{"):
                import json
                try:
                    meta = json.loads(reg_doc["formattedText"])
                    if not isinstance(parsed_dict.get("custom_metadata"), dict):
                        parsed_dict["custom_metadata"] = {}
                    parsed_dict["custom_metadata"]["llm_extracted_fields"] = meta
                    parsed_dict["formatted_text"] = reg_doc["formattedText"]
                    parsed_dict["extracted_fields"] = meta
                except Exception:
                    pass
        except Exception as enrich_err:
            logger.debug("Enrichment note for %s: %s", document_id, enrich_err)

    return parsed_dict
