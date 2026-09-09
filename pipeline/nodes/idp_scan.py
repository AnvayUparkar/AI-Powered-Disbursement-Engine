"""Node: IDP Scan — Ingests raw PDFs/images/XML via IDP and saves output to S3 Extracted tier."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import (
    DISABLE_IDP_EXTRACTION_CACHE,
    MAX_DOC_WORKERS,
    S3_EXTRACTED_DIR,
    S3_RAW_DIR,
    SKIP_IDP,
    get_canonical_doc_type,
)
from idp.services.document_processor import DocumentProcessor
from pipeline.engines.key_value_extractor import KeyValueExtractor
from pipeline.state import PipelineState
from pipeline.storage import (
    get_all_s3_extracted_structured,
    read_json,
    save_s3_extracted,
    update_status,
    write_json,
)

logger = logging.getLogger("disbursement_pipeline.idp_scan")

_processor: Optional[DocumentProcessor] = None


def get_processor() -> DocumentProcessor:
    global _processor
    if _processor is None:
        _processor = DocumentProcessor()
    return _processor


def _process_single_document(file_path: Path, doc_id: str, doc_key: str) -> Optional[Dict[str, Any]]:
    """Runs IDP processing on a single file to extract OCR text, elements, and layout tables."""
    try:
        from pipeline.nodes.node2_extract import _process_file_with_idp
        return _process_file_with_idp(file_path, doc_id=doc_id)
    except Exception as e:
        logger.warning("IDP scan encountered an issue for %s: %s", file_path, e)
    return None


def idp_scan(state: PipelineState) -> PipelineState:
    """Processes document files in raw_doc_paths with IDP and persists OCR/layout in S3 Extracted tier."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("idp_scan")

    logger.info("Executing idp_scan for loan: %s", loan_id)

    if SKIP_IDP:
        logger.info("SKIP_IDP is active — bypassing IDP scanning for loan %s", loan_id)
        staged_docs = get_all_s3_extracted_structured(loan_id)
        merged = dict(state.get("extracted_data", {}))
        merged.update(staged_docs)
        update_status(loan_id, current_node="idp_scan", errors=errors, node_history=history)
        return {
            **state,
            "extracted_data": merged,
            "face_embeddings": merged.get("face_embeddings", state.get("face_embeddings", {})),
            "dms_status": merged.get("dms_status", state.get("dms_status", {})),
            "errors": errors,
            "node_history": history,
        }

    raw_doc_paths = state.get("raw_doc_paths", {})
    extracted_data: Dict[str, Any] = dict(state.get("extracted_data", {}))
    face_embeddings: Dict[str, Any] = dict(state.get("face_embeddings", {}))
    dms_status: Dict[str, Any] = dict(state.get("dms_status", {}))
    otp_audit: Dict[str, Any] = dict(state.get("otp_audit", {}))

    # Ingest sidecar metadata JSONs & identify binary documents
    binary_tasks: List[Tuple[str, Path, str, str]] = []  # (fname, fpath, doc_key, doc_id)

    for fname, fpath_str in raw_doc_paths.items():
        fpath = Path(fpath_str)
        if not fpath.exists():
            continue

        name_lower = fname.lower()
        if name_lower == "face_embeddings.json":
            try:
                face_embeddings = read_json(fpath)
            except Exception as e:
                errors.append(f"Failed to read face embeddings: {e}")
            continue
        elif name_lower == "dms_status.json":
            try:
                dms_status = read_json(fpath)
            except Exception as e:
                errors.append(f"Failed to read dms status: {e}")
            continue
        elif name_lower == "loan_agreement_otp_audit.json":
            try:
                otp_audit = read_json(fpath)
            except Exception as e:
                errors.append(f"Failed to read OTP audit: {e}")
            continue
        elif name_lower.endswith(".json") and fpath.stem.upper() == loan_id.upper():
            continue
        elif name_lower.endswith(".json") and "metadata" in name_lower:
            continue
        elif name_lower.endswith(".json"):
            try:
                doc_key = get_canonical_doc_type(fpath.stem)
                extracted_data[doc_key] = read_json(fpath)
            except Exception as e:
                errors.append(f"Failed reading JSON document {fname}: {e}")
            continue

        if fpath.suffix.lower() in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".xml"]:
            doc_key = get_canonical_doc_type(fname)
            doc_id = f"{loan_id}_{doc_key}"
            binary_tasks.append((fname, fpath, doc_key, doc_id))

    # Process binary documents sequentially to avoid PyTorch/Docling access violations on Windows
    for fname, fpath, doc_key, doc_id in binary_tasks:
        try:
            # Check if valid cached IDP extraction already exists in S3 Extracted tier
            cached_path = S3_EXTRACTED_DIR / loan_id / f"{doc_key}.json"
            if not DISABLE_IDP_EXTRACTION_CACHE and cached_path.exists():
                try:
                    cached_data = read_json(cached_path)
                    raw_txt = cached_data.get("_raw_text") or cached_data.get("rawText") or ""
                    # Ensure cached extraction has content and is not older than the raw document file
                    if (raw_txt.strip() or cached_data.get("_components") or len(cached_data) > 0) and cached_path.stat().st_mtime >= fpath.stat().st_mtime:
                        logger.info("IDP scan cache hit for %s (%s) in loan %s. Skipping duplicate OCR.", doc_key, fname, loan_id)
                        extracted_data[doc_key] = cached_data
                        continue
                except Exception as cache_read_err:
                    logger.debug("Failed reading cached IDP extraction for %s: %s", doc_key, cache_read_err)

            scan_res = _process_single_document(fpath, doc_id=doc_id, doc_key=doc_key)
            if scan_res:
                extracted_data[doc_key] = scan_res
                save_s3_extracted(loan_id, doc_key, scan_res)
        except Exception as scan_err:
            logger.warning("Error processing %s: %s", fname, scan_err)

    # Fallback to pre-extracted data if present, preserving existing fields
    ext_dir = S3_EXTRACTED_DIR / loan_id
    if ext_dir.exists():
        for json_file in ext_dir.glob("*.json"):
            if json_file.stem.endswith("_structured"):
                continue
            doc_key = get_canonical_doc_type(json_file.stem)
            try:
                tier_doc = read_json(json_file)
                if doc_key not in extracted_data:
                    extracted_data[doc_key] = tier_doc
                elif isinstance(tier_doc, dict):
                    for fk, fv in tier_doc.items():
                        if fv is not None and (fk not in extracted_data[doc_key] or extracted_data[doc_key][fk] is None):
                            extracted_data[doc_key][fk] = fv
            except Exception:
                pass

    # Save all scanned outputs to S3 Extracted tier
    for doc_k, doc_v in extracted_data.items():
        if isinstance(doc_v, dict):
            save_s3_extracted(loan_id, doc_k, doc_v)

    update_status(loan_id, current_node="idp_scan", errors=errors, node_history=history)

    return {
        **state,
        "extracted_data": extracted_data,
        "face_embeddings": face_embeddings,
        "dms_status": dms_status,
        "otp_audit": otp_audit,
        "errors": errors,
        "node_history": history,
    }
