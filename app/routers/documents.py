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


def _generate_fallback_pdf(title: str, case_id: str) -> bytes:
    """Generates synthetic 1-page PDF for registered documents whose raw source PDF is unavailable."""
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), f"Document Preview: {title}", fontsize=16)
        page.insert_text((50, 80), f"Case ID: {case_id}", fontsize=12)
        page.insert_text((50, 110), "Document extracted & registered in pipeline registry", fontsize=10)
        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes
    except Exception:
        return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 595 842]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000108 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"


@router.get("/preview/{case_id}/{doc_name}", summary="Preview document stream")
def preview_document(
    case_id: str,
    doc_name: str,
    page: int = Query(1, description="Page number (1-indexed)"),
    format: str | None = Query(None, description="Set to 'image' to render as PNG image"),
):
    # Reject suspicious path characters explicitly
    if "/" in doc_name or "\\" in doc_name or "/" in case_id or "\\" in case_id or "\x00" in doc_name or "\x00" in case_id:
        raise HTTPException(status_code=400, detail="Invalid document or case path identifier")

    s3_root = S3_RAW_DIR.resolve()
    dms_root = DMS_DIR.resolve()

    target_s3 = (S3_RAW_DIR / case_id / doc_name).resolve()
    target_dms = (DMS_DIR / case_id / doc_name).resolve()

    target_path: Path | None = None
    if target_s3.exists() and target_s3.is_file() and target_s3.is_relative_to(s3_root):
        target_path = target_s3
    elif target_dms.exists() and target_dms.is_file() and target_dms.is_relative_to(dms_root):
        target_path = target_dms
    else:
        # Fallback 1: Resolve case_id or doc_name via document_registry
        all_docs = document_registry.list_all()
        for doc_item in all_docs:
            if doc_item.get("name") == doc_name or doc_name.lower() in doc_item.get("name", "").lower():
                real_case = doc_item.get("caseId")
                if real_case:
                    cand_s3 = (S3_RAW_DIR / real_case / doc_name).resolve()
                    cand_dms = (DMS_DIR / real_case / doc_name).resolve()
                    if cand_s3.exists() and cand_s3.is_file() and cand_s3.is_relative_to(s3_root):
                        target_path = cand_s3
                        break
                    elif cand_dms.exists() and cand_dms.is_file() and cand_dms.is_relative_to(dms_root):
                        target_path = cand_dms
                        break

        # Fallback 2: Search across all case subfolders in S3_RAW_DIR and DMS_DIR
        if not target_path:
            for parent_dir, root_dir in [(S3_RAW_DIR, s3_root), (DMS_DIR, dms_root)]:
                if parent_dir.exists():
                    matches = list(parent_dir.glob(f"*/{doc_name}"))
                    if not matches:
                        stem = Path(doc_name).stem.lower()
                        matches = [p for p in parent_dir.glob("*/*") if stem in p.stem.lower() and p.is_file()]
                    if matches and matches[0].is_file() and matches[0].resolve().is_relative_to(root_dir):
                        target_path = matches[0].resolve()
                        break

    name_lower = doc_name.lower()
    is_image_req = format == "image" or name_lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tiff"))

    if target_path is not None:
        if is_image_req:
            if name_lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tiff")):
                try:
                    content = target_path.read_bytes()
                    mtype = "image/png" if name_lower.endswith(".png") else "image/jpeg"
                    return Response(content=content, media_type=mtype)
                except OSError as e:
                    logger.error("Failed reading document %s: %s", target_path, e)
                    raise HTTPException(status_code=500, detail="Error reading document file")
            elif name_lower.endswith(".pdf"):
                try:
                    import fitz
                    doc_fitz = fitz.open(target_path)
                    p_idx = max(0, min(len(doc_fitz) - 1, page - 1))
                    pix = doc_fitz[p_idx].get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    doc_fitz.close()
                    return Response(content=img_bytes, media_type="image/png")
                except Exception as e:
                    logger.warning("PDF page image rendering exception for %s: %s", target_path, e)

        try:
            content = target_path.read_bytes()
            media_type = "application/pdf" if doc_name.endswith(".pdf") else "application/octet-stream"
            return Response(content=content, media_type=media_type)
        except OSError as e:
            logger.error("Failed reading document %s: %s", target_path, e)
            raise HTTPException(status_code=500, detail="Error reading document file")

    # Fallback for synthetic/registered documents without raw PDF on disk
    doc_exists = any(
        d.get("name") == doc_name
        or doc_name.lower() in str(d.get("name", "")).lower()
        or str(d.get("name", "")).lower() in doc_name.lower()
        or Path(doc_name).stem.lower() in str(d.get("name", "")).lower()
        for d in all_docs
    )
    if not doc_exists:
        raise HTTPException(status_code=404, detail="Document not found")

    pdf_bytes = _generate_fallback_pdf(doc_name, case_id)
    if is_image_req:
        try:
            import fitz
            doc_fitz = fitz.open("pdf", pdf_bytes)
            pix = doc_fitz[0].get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            doc_fitz.close()
            return Response(content=img_bytes, media_type="image/png")
        except Exception as e:
            logger.warning("Fallback PDF rendering exception: %s", e)

    return Response(content=pdf_bytes, media_type="application/pdf")


@router.get("/{doc_id}/page/{page_number}/image", summary="Get rendered page image for document ID")
def get_document_page_image(doc_id: str, page_number: int = 1):
    """Renders and returns a page of a document as a PNG image."""
    doc = document_registry.get_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    case_id = doc.get("caseId")
    doc_name = doc.get("name")
    if not case_id or not doc_name:
        raise HTTPException(status_code=400, detail="Document lacks caseId or filename")
    return preview_document(case_id=case_id, doc_name=doc_name, page=page_number, format="image")

