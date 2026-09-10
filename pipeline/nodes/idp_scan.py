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

from idp.models.document import ParsedDocument
from idp.services.extraction.field_location_resolver import FieldLocationResolver
from pipeline.engines.llm_field_extractor import format_template_json, llm_extract_fields

logger = logging.getLogger("disbursement_pipeline.idp_scan")

_processor: Optional[DocumentProcessor] = None


def get_processor() -> DocumentProcessor:
    global _processor
    if _processor is None:
        _processor = DocumentProcessor()
    return _processor


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

    raw_element_dicts = []
    for elem in parsed.elements:
        elem_dict = elem.model_dump()
        raw_element_dicts.append(elem_dict)

    kv_extractor = KeyValueExtractor()
    spatial_results = kv_extractor.extract(raw_element_dicts, doc_type=doc_type)

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
    """Runs IDP DocumentProcessor asynchronously in synchronous loop with smart routing."""
    processor = get_processor()
    try:
        prep = processor.preprocessor.preprocess(str(file_path), doc_id=doc_id)
        if prep.file_category == "xml":
            parsed = processor.serializer.parse_xml_fast_path(str(file_path), doc_id=doc_id)
        else:
            coro = processor.process_document(
                document_id=doc_id,
                s3_key=str(file_path),
            )
            try:
                asyncio.run(coro)
            except RuntimeError:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(coro)

            parsed = asyncio.run(processor.get_parsed_document(doc_id))

        if parsed:
            return build_idp_result_from_parsed(parsed=parsed, doc_type=doc_key, doc_id=doc_id)
    except Exception as e:
        logger.warning("Native IDP processing encountered an issue for %s: %s", file_path, e)
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

                scan_res = _process_single_document(fpath, doc_id=doc_id, doc_key=doc_key)
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
