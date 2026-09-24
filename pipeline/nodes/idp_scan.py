"""Node: IDP Scan — Ingests raw PDFs/images/XML via IDP and saves output to S3 Extracted tier."""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import (
    DISABLE_IDP_EXTRACTION_CACHE,
    IDP_REQUEST_TIMEOUT,
    IDP_SERVICE_URL,
    MAX_DOC_WORKERS,
    S3_EXTRACTED_DIR,
    S3_RAW_DIR,
    SKIP_IDP,
    USE_REMOTE_IDP,
    get_canonical_doc_type,
)
from pipeline.state import PipelineState
from pipeline.storage import (
    get_all_s3_extracted_structured,
    read_json,
    save_s3_extracted,
    update_status,
)
logger = logging.getLogger("disbursement_pipeline.idp_scan")


def _call_idp_service(
    file_path: Path,
    doc_id: str,
    doc_key: str,
    loan_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Delegates document extraction to the IDP HTTP microservice (Port 8001).

    Port 8001 routes internally: XML → fast-path, Loan Agreement → pyHanko,
    all others → Docling + OCR. Returns the canonical extracted dict ready for
    s3_extracted/, or None on failure.
    """
    if not USE_REMOTE_IDP:
        logger.warning(
            "USE_REMOTE_IDP is disabled — cannot extract %s (%s). "
            "Enable USE_REMOTE_IDP and ensure Port 8001 is running.",
            doc_key,
            file_path,
        )
        return None

    try:
        from shared.object_keys import raw_object_key, validate_key
        key = raw_object_key(loan_id, file_path.name)
        validate_key(key)
        with httpx.Client(timeout=IDP_REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{IDP_SERVICE_URL}/api/v1/documents/process",
                json={"document_id": doc_id, "s3_key": key},
            )
            resp.raise_for_status()

            canonical_resp = client.get(
                f"{IDP_SERVICE_URL}/api/v1/documents/{doc_id}/canonical",
            )
            canonical_resp.raise_for_status()
            return canonical_resp.json()
    except Exception as exc:
        logger.warning(
            "IDP service call failed for %s (%s): %s",
            doc_id,
            file_path,
            exc,
        )
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

        if fpath.suffix.lower() in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".xml"]:
            doc_key = get_canonical_doc_type(fname)
            doc_id = f"{loan_id}_{doc_key}"
            binary_tasks.append((fname, fpath, doc_key, doc_id))

    # Process binary documents with ThreadPoolExecutor (governed by MAX_DOC_WORKERS)
    if binary_tasks:
        worker_count = min(len(binary_tasks), MAX_DOC_WORKERS) if MAX_DOC_WORKERS > 0 else 1

        def _worker_task(task_tuple: Tuple[str, Path, str, str]) -> Tuple[str, Path, str, Optional[Dict[str, Any]]]:
            fname, fpath, doc_key, doc_id = task_tuple
            try:
                # Check if valid cached IDP extraction already exists in S3 Extracted tier
                cached_path = S3_EXTRACTED_DIR / loan_id / f"{doc_key}.json"
                if not DISABLE_IDP_EXTRACTION_CACHE and cached_path.exists():
                    try:
                        cached_data = read_json(cached_path)
                        raw_txt = cached_data.get("_raw_text") or cached_data.get("rawText") or ""
                        if (raw_txt.strip() or cached_data.get("_components") or len(cached_data) > 0) and cached_path.stat().st_mtime >= fpath.stat().st_mtime:
                            logger.info("IDP scan cache hit for %s (%s) in loan %s. Skipping duplicate OCR.", doc_key, fname, loan_id)
                            return fname, fpath, doc_key, cached_data
                    except Exception as cache_read_err:
                        logger.debug("Failed reading cached IDP extraction for %s: %s", doc_key, cache_read_err)

                scan_res = _call_idp_service(fpath, doc_id=doc_id, doc_key=doc_key, loan_id=loan_id)
                return fname, fpath, doc_key, scan_res
            except Exception as scan_err:
                logger.warning("Error processing %s: %s", fname, scan_err)
                return fname, fpath, doc_key, None

        if worker_count > 1:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="idp_doc_worker") as executor:
                futures = [executor.submit(_worker_task, task) for task in binary_tasks]
                for future in futures:
                    fname, fpath, doc_key, scan_res = future.result()
                    if scan_res:
                        extracted_data[doc_key] = scan_res
                        save_s3_extracted(loan_id, doc_key, scan_res)
        else:
            for task in binary_tasks:
                fname, fpath, doc_key, scan_res = _worker_task(task)
                if scan_res:
                    extracted_data[doc_key] = scan_res
                    save_s3_extracted(loan_id, doc_key, scan_res)

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
