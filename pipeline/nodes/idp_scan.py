"""Node: IDP Scan — Ingests raw PDFs/images/XML via IDP and saves output to S3 Extracted tier."""
import asyncio
import json
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
    ENABLE_TIERED_KV_CONFIDENCE,
)
from config.docling_profiles import get_profile_for_document_type
from pipeline.engines.key_value_extractor import KeyValueExtractor
from pipeline.engines.pyhanko_inspector import inspect_pdf_signatures, is_loan_agreement
from pipeline.state import PipelineState
from pipeline.storage import (
    get_all_s3_extracted_structured,
    read_json,
    save_s3_extracted,
    update_status,
    write_json,
)
from idp.models.document import ParsedDocument
from idp.services.extraction.field_location_resolver import FieldLocationResolver
from idp.services.output.serializer import DocumentSerializer
from pipeline.engines.llm_field_extractor import format_template_json, llm_extract_fields
from pipeline.utils.image_normalizer import ensure_png_for_idp

logger = logging.getLogger("disbursement_pipeline.idp_scan")


def build_idp_result_from_parsed(parsed: ParsedDocument, doc_type: str, doc_id: str) -> Dict[str, Any]:
    """Builds canonical extracted dictionary with elements, tables, and bounding boxes from a ParsedDocument."""
    if not parsed:
        return {}

    extracted_fields = (parsed.custom_metadata or {}).get("llm_extracted_fields") if (parsed and parsed.custom_metadata) else None
    if not extracted_fields:
        extracted_fields = llm_extract_fields(
            doc_type=doc_type,
            raw_text=parsed.text,
            doc_id=doc_id,
        )

    # For Aadhaar XML docs, inject the UID extracted from the <UidData uid="..."> attribute.
    # The LLM never sees this value since it's an XML attribute, not element text.
    if doc_type == "aadhaar_xml" and parsed and parsed.custom_metadata:
        aadhaar_uid = parsed.custom_metadata.get("aadhaar_uid")
        if aadhaar_uid and not (extracted_fields or {}).get("aadhaar_number"):
            extracted_fields = dict(extracted_fields or {})
            extracted_fields["aadhaar_number"] = aadhaar_uid

    template_fields = format_template_json(extracted_fields or {})

    raw_element_dicts = []
    for elem in parsed.elements:
        elem_dict = elem.model_dump()
        raw_element_dicts.append(elem_dict)

    is_scanned_pdf = None
    if parsed:
        if parsed.custom_metadata:
            is_scanned_pdf = parsed.custom_metadata.get("is_scanned_pdf")
        if is_scanned_pdf is None and hasattr(parsed, "processing") and parsed.processing:
            if hasattr(parsed.processing, "custom_metadata") and parsed.processing.custom_metadata:
                is_scanned_pdf = parsed.processing.custom_metadata.get("is_scanned_pdf")
        if is_scanned_pdf is None and parsed.elements:
            ocr_count = sum(1 for e in parsed.elements if getattr(e, "source", "") in ("docling_ocr", "rapidocr", "vlm_corrected") or (getattr(e, "confidence", 1.0) < 0.999))
            if ocr_count > len(parsed.elements) * 0.3:
                is_scanned_pdf = True

    if ENABLE_TIERED_KV_CONFIDENCE:
        profile = get_profile_for_document_type(doc_type, is_scanned=is_scanned_pdf)
        kv_extractor = KeyValueExtractor(
            hard_noise_floor=profile.kv_hard_noise_floor,
            confident_value_floor=profile.kv_confident_value_floor,
            min_pair_confidence=profile.kv_min_pair_confidence,
            emit_unmatched_stubs=True,
        )
    else:
        kv_extractor = KeyValueExtractor(
            hard_noise_floor=0.50,
            confident_value_floor=0.50,
            min_pair_confidence=0.72,
            emit_unmatched_stubs=False,
        )
    spatial_results = kv_extractor.extract(raw_element_dicts, doc_type=doc_type)
    kv_entries = spatial_results.get("key_values", {})
    if parsed and hasattr(parsed, "processing") and parsed.processing and hasattr(parsed.processing, "metrics") and parsed.processing.metrics:
        parsed.processing.metrics.kv_needs_review_count = sum(1 for v in kv_entries.values() if v.get("needs_review"))
        parsed.processing.metrics.kv_unmatched_label_count = sum(1 for v in kv_entries.values() if v.get("reason") == "no_value_matched")

    tables_data = []
    for tbl in (parsed.tables or []):
        tables_data.append({
            "id": tbl.id,
            "page_number": tbl.page_number,
            "table_type": getattr(tbl, "table_type", "STRUCTURED_TABLE"),
            "headers": tbl.headers,
            "rows": tbl.rows_raw,
        })

    template_fields = format_template_json(extracted_fields or {})
    formatted_json = json.dumps(template_fields, indent=2)

    page_dims = []
    for p in (parsed.pages or []):
        page_dims.append({"width": getattr(p, "width", 0.0), "height": getattr(p, "height", 0.0)})

    table_cells_dicts = []
    for tbl in (parsed.tables or []):
        for cell in (getattr(tbl, "cells", []) or []):
            table_cells_dicts.append({
                "id": getattr(cell, "id", None),
                "text": getattr(cell, "text", ""),
                "bbox": getattr(cell, "bbox", []),
                "page_number": getattr(tbl, "page_number", 1),
                "confidence": getattr(cell, "confidence", 1.0),
            })

    resolver = FieldLocationResolver()
    field_locs = resolver.resolve_field_locations(
        extracted_fields=template_fields,
        ocr_elements=raw_element_dicts,
        table_cells=table_cells_dicts,
        page_dimensions=page_dims,
        debug_mode=True,
    )
    field_locs_dict = {k: v.model_dump() for k, v in field_locs.items()}
    ocr_tokens_debug = [t.model_dump() for t in resolver.extract_debug_tokens(raw_element_dicts, page_dims)]

    components = {
        "document_type": doc_type,
        "key_values": spatial_results.get("key_values", {}),
        "checkboxes": spatial_results.get("checkboxes", {}),
        "tables": tables_data,
        "paragraphs": spatial_results.get("paragraphs", []),
        "field_locations": field_locs_dict,
        "ocr_tokens": ocr_tokens_debug,
        "page_dimensions": page_dims,
    }

    return {
        **template_fields,
        "_raw_text": parsed.text,
        "rawText": parsed.text,
        "_formatted_text": formatted_json,
        "formattedText": formatted_json,
        "_pages": len(parsed.pages),
        "_elements_count": len(parsed.elements),
        "_components": components,
        "_field_locations": field_locs_dict,
        "_kv_needs_review_count": sum(1 for v in kv_entries.values() if v.get("needs_review")),
        "_kv_unmatched_label_count": sum(1 for v in kv_entries.values() if v.get("reason") == "no_value_matched"),
    }


TABULAR_OR_MISC_DOCS = frozenset({
    "application_form",
    "kfs",
    "sanction_letter",
    "account_statement",
    "loan_agreement",
    "disbursal_memo",
    "miscellaneous",
})


def is_tabular_or_misc_doc(doc_type: str) -> bool:
    """Returns True if the document type requires TableFormer table structure detection.

    Pure identity cards (Aadhaar, PAN, voter ID, passport, driving license) do not contain
    financial tables and bypass TableFormer, saving ~45s/page of CPU transformer inference.
    All tabular, financial, and miscellaneous/unmapped document types execute TableFormer.
    """
    canonical = get_canonical_doc_type(doc_type).lower()
    if canonical in {"aadhaar", "pan", "voter_id", "passport", "driving_license"}:
        return False
    return True


def _process_single_document(file_path: Path, doc_id: str, doc_key: str) -> Optional[Dict[str, Any]]:
    """Runs IDP via 8001 HTTP microservice. XML fast-path runs locally via DocumentSerializer.
    
    Port 8000 NEVER runs local Docling, OCR, or heavy DL models.
    """
    # Normalize .tif/.tiff images to standard RGB PNG to prevent ONNX OCR memory crashes
    file_path = ensure_png_for_idp(file_path)

    # XML fast-path runs locally on 8000 (pure deterministic XML tree parsing, zero heavy ML models)
    if file_path.suffix.lower() == ".xml" or doc_key == "aadhaar_xml":
        try:
            serializer = DocumentSerializer()
            parsed = serializer.parse_xml_fast_path(str(file_path), doc_id=doc_id)
            if parsed:
                return build_idp_result_from_parsed(parsed=parsed, doc_type=doc_key, doc_id=doc_id)
        except Exception as e:
            logger.warning("Local XML fast-path parsing failed for %s: %s", file_path, e)
            return None

    # Enforce HTTP delegation to Port 8001 IDP microservice
    if USE_REMOTE_IDP:
        try:
            with httpx.Client(timeout=IDP_REQUEST_TIMEOUT) as client:
                resp = client.post(
                    f"{IDP_SERVICE_URL}/api/v1/documents/process",
                    json={"document_id": doc_id, "s3_key": str(file_path)},
                )
                resp.raise_for_status()

                get_resp = client.get(f"{IDP_SERVICE_URL}/api/v1/documents/{doc_id}")
                get_resp.raise_for_status()

                parsed = ParsedDocument.model_validate(get_resp.json())
                return build_idp_result_from_parsed(parsed=parsed, doc_type=doc_key, doc_id=doc_id)
        except Exception as exc:
            logger.warning(
                "Remote IDP call to %s failed for %s (%s): %s",
                IDP_SERVICE_URL,
                doc_id,
                file_path,
                exc,
            )
            return None

    logger.warning("USE_REMOTE_IDP is disabled and local OCR execution is disabled on Port 8000 for %s", file_path)
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
                            # Ensure Loan Agreement digital signature verification is populated even on cache hit
                            if (is_loan_agreement(fname) or is_loan_agreement(doc_key)) and fpath.suffix.lower() == ".pdf":
                                if "pyhanko_inspection" not in cached_data:
                                    try:
                                        sig_res = inspect_pdf_signatures(fpath, filename=fname)
                                        cached_data["pyhanko_inspection"] = sig_res
                                        cached_data["loan_agreement_present"] = True
                                        cached_data["loan_agreement_signed"] = bool(sig_res.get("is_acceptable", False))
                                    except Exception as sig_err:
                                        logger.warning("pyHanko signature inspection failed on cache hit for %s: %s", fname, sig_err)
                            return fname, fpath, doc_key, cached_data
                    except Exception as cache_read_err:
                        logger.debug("Failed reading cached IDP extraction for %s: %s", doc_key, cache_read_err)

                # For Loan Agreements: bypass heavy OCR/Docling on multi-page agreements
                # and directly execute fast pyHanko digital signature inspection.
                if (is_loan_agreement(fname) or is_loan_agreement(doc_key)) and fpath.suffix.lower() == ".pdf":
                    logger.info("Bypassing OCR for Loan Agreement %s (%s); running pyHanko directly.", fname, loan_id)
                    try:
                        sig_res = inspect_pdf_signatures(fpath, filename=fname)
                    except Exception as sig_err:
                        logger.warning("pyHanko signature inspection failed for %s: %s", fname, sig_err)
                        sig_res = {"error": str(sig_err), "is_signed": False, "is_acceptable": False, "signatures": []}

                    is_signed = bool(sig_res.get("is_signed", False))
                    is_acceptable = bool(sig_res.get("is_acceptable", False))
                    sig_count = int(sig_res.get("signature_count", 0))
                    signatures = sig_res.get("signatures", [])
                    page_count = sig_res.get("page_count", 0)
                    signer_cn = signatures[0]["signer"]["common_name"] if signatures else "N/A"

                    status_label = (
                        "DIGITALLY SIGNED (VALID)"
                        if is_acceptable
                        else "UNSIGNED"
                        if not is_signed
                        else "SIGNATURE INVALID/TAMPERED"
                    )
                    diag_text = (
                        f"Document: {fname}\n"
                        f"Document Type: Loan Agreement\n"
                        f"Pages: {page_count}\n"
                        f"Digital Signature Status: {status_label}\n"
                        f"Signatures Detected: {sig_count}\n"
                    )
                    if is_signed and signatures:
                        sig0 = signatures[0]
                        diag_text += (
                            f"Signer CN: {signer_cn}\n"
                            f"Issuer: {sig0['signer'].get('issuer_dn')}\n"
                            f"Validity Window: {sig0['signer'].get('valid_from')} to {sig0['signer'].get('valid_until')}\n"
                            f"Trust Anchor: {sig0.get('trust_anchor_label')}\n"
                            f"Cryptographic Integrity: {'INTACT' if sig0.get('intact') else 'TAMPERED'}\n"
                        )

                    scan_res = {
                        "_raw_text": diag_text,
                        "rawText": diag_text,
                        "loan_agreement_present": True,
                        "loan_agreement_signed": is_acceptable,
                        "pyhanko_inspection": sig_res,
                        "_components": {
                            "raw_elements": [
                                {
                                    "type": "paragraph",
                                    "text": diag_text,
                                    "bbox": [0, 0, 100, 100],
                                    "page": 1,
                                }
                            ]
                        },
                        "_field_locations": {},
                    }
                    return fname, fpath, doc_key, scan_res

                # Standard IDP OCR processing for non-agreement documents (KYC, Statements, KFS, etc.)
                scan_res = _process_single_document(fpath, doc_id=doc_id, doc_key=doc_key)
                # Aadhaar XML presence is proven by the file being classified as aadhaar_xml —
                # the LLM cannot infer this from raw UIDAI XML tag content, so force it here.
                if scan_res and doc_key == "aadhaar_xml":
                    scan_res["aadhaar_xml_present"] = True
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
