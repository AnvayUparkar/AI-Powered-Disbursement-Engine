from fastapi import APIRouter, HTTPException, status
from idp.schemas.document import ProcessDocumentRequest, DocumentStatusResponse
from idp.schemas.response import ErrorResponse
from idp.services.document_processor import processor
from idp.services.output.canonical_builder import build_canonical_extracted_dict
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
            processing_time_seconds=result["processing_time_seconds"],
            result={
                "raw_text": result.get("raw_text", ""),
                "formatted_text": result.get("formatted_text", ""),
                "extracted_fields": result.get("extracted_fields", {}),
                "field_locations": result.get("field_locations", {}),
                "ocr_tokens": result.get("ocr_tokens", []),
            },
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


@router.get(
    "/{document_id}/canonical",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Document not found or not yet processed"},
        500: {"model": ErrorResponse, "description": "Canonical build error"},
    }
)
async def get_document_canonical(document_id: str):
    """
    Returns the canonical storage-tier extracted JSON for a processed document.
    This is the format written to s3_extracted/{loan_id}/{doc_key}.json.
    Called by pipeline's idp_scan node — never deserializes ParsedDocument on the pipeline side.
    """
    parsed = await processor.get_parsed_document(document_id)
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NotFound", "message": f"Parsed document for {document_id} not found."}
        )

    try:
        doc_type = (parsed.custom_metadata or {}).get("doc_type") or "miscellaneous"
        return build_canonical_extracted_dict(parsed=parsed, doc_type=doc_type, doc_id=document_id)
    except Exception as e:
        logger.error(format_doc_log(document_id, f"Canonical build error: {e}"))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "InternalServerError", "message": str(e), "document_id": document_id}
        )

