import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form
from idp.schemas.document import ProcessDocumentRequest, DocumentStatusResponse
from idp.schemas.response import ErrorResponse
from idp.services.document_processor import DocumentProcessor
from idp.core.exceptions import Node2BaseException
from idp.core.logging import logger, format_doc_log

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])
processor = DocumentProcessor()


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
    doc_id = document_id or f"DOC-{uuid.uuid4().hex[:8].upper()}"
    logger.info(format_doc_log(doc_id, f"Received direct file upload for {file.filename}"))

    try:
        file_bytes = await file.read()
        res = await processor.process_uploaded_file(
            file_bytes=file_bytes,
            filename=file.filename or "uploaded_doc",
            document_id=doc_id,
            s3_bucket=s3_bucket
        )

        try:
            from pathlib import Path
            from config import S3_RAW_DIR
            if case_id:
                case_raw_dir = S3_RAW_DIR / case_id
                case_raw_dir.mkdir(parents=True, exist_ok=True)
                raw_filename = Path(file.filename or "uploaded_doc.pdf").name
                clean_name = raw_filename
                if doc_type and not any(k in clean_name.lower() for k in ["app", "pan", "aadhaar", "kfs", "sanction", "agreement", "memo", "bt", "kyc"]):
                    clean_name = f"{doc_type.replace(' ', '_')}_{raw_filename}"
                target_path = case_raw_dir / clean_name
                target_path.write_bytes(file_bytes)
                logger.info(format_doc_log(doc_id, f"Synced uploaded document to case S3 raw store at {target_path}"))
        except Exception as sync_err:
            logger.debug(format_doc_log(doc_id, f"Case S3 raw store sync notification: {sync_err}"))

        try:
            from app.services.document_registry import document_registry
            document_registry.register_uploaded_document(
                doc_id=res["document_id"],
                filename=file.filename or "uploaded_doc",
                doc_type=doc_type,
                case_id=case_id,
                file_size_bytes=len(file_bytes),
                parsed_result=res.get("result"),
            )
        except Exception as reg_err:
            logger.debug(format_doc_log(doc_id, f"Document registry sync notification: {reg_err}"))

        return DocumentStatusResponse(
            document_id=res["document_id"],
            processing_id=f"proc-{doc_id}",
            status=res["status"],
            output_location=res["output_location"],
            processing_time_seconds=res["processing_time_seconds"],
            result=res.get("result")
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NotFound", "message": f"Parsed document for {document_id} not found."}
        )
    return parsed.model_dump()
