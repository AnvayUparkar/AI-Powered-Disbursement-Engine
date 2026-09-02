import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response

from app.services.document_registry import document_registry
from config import DMS_DIR, S3_RAW_DIR

logger = logging.getLogger("disbursement_pipeline.api.documents")

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.get("", summary="List documents")
def list_documents(
    caseId: str | None = Query(None, description="Filter by case ID"),
    type: str | None = Query(None, description="Filter by document type"),
    query: str | None = Query(None, description="Search query across name, case, and type"),
):
    """
    List registered case documents and uploaded IDP documents.
    """
    return document_registry.list_all(case_id=caseId, doc_type=type, query=query)


@router.get("/types", summary="Get distinct document types")
def get_document_types():
    """
    Return all distinct document types available in the active registry.
    """
    return document_registry.get_distinct_types()


@router.get("/{doc_id}", summary="Get document by ID")
def get_document(doc_id: str):
    """
    Retrieve full document details with extracted fields and processing steps.
    """
    doc = document_registry.get_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return doc


@router.get("/preview/{case_id}/{doc_name}", summary="Preview document stream")
def preview_document(case_id: str, doc_name: str):
    # Reject suspicious path characters explicitly
    if "/" in doc_name or "\\" in doc_name or "/" in case_id or "\\" in case_id or "\x00" in doc_name or "\x00" in case_id:
        raise HTTPException(status_code=400, detail="Invalid document or case path identifier")

    s3_root = S3_RAW_DIR.resolve()
    dms_root = DMS_DIR.resolve()

    target_s3 = (S3_RAW_DIR / case_id / doc_name).resolve()
    target_dms = (DMS_DIR / case_id / doc_name).resolve()

    # Strict containment check to prevent directory traversal
    is_in_s3 = target_s3.is_relative_to(s3_root)
    is_in_dms = target_dms.is_relative_to(dms_root)

    if not (is_in_s3 or is_in_dms):
        raise HTTPException(status_code=400, detail="Invalid document path traversal")

    target_path: Path | None = None
    if target_s3.exists() and target_s3.is_file():
        target_path = target_s3
    elif target_dms.exists() and target_dms.is_file():
        target_path = target_dms

    if target_path is not None:
        try:
            content = target_path.read_bytes()
            media_type = "application/pdf" if doc_name.endswith(".pdf") else "application/octet-stream"
            return Response(content=content, media_type=media_type)
        except OSError as e:
            logger.error("Failed reading document %s: %s", target_path, e)
            raise HTTPException(status_code=500, detail="Error reading document file")

    raise HTTPException(status_code=404, detail="Document not found")

