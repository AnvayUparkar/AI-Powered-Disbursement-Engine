"""Node: LLM Structure — Structures raw extracted OCR text via LLM into S3 Extracted Structured tier."""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict

from config import MAX_DOC_WORKERS, S3_EXTRACTED_STRUCTURED_DIR, SKIP_IDP, get_canonical_doc_type
from pipeline.engines.llm_field_extractor import llm_extract_fields
from pipeline.state import PipelineState
from pipeline.storage import (
    get_all_s3_extracted_structured,
    read_json,
    save_s3_extracted_structured,
    update_status,
)

logger = logging.getLogger("disbursement_pipeline.llm_structure")


def _structure_single_document(doc_key: str, doc_data: dict[str, Any], loan_id: str) -> dict[str, Any]:
    """Applies LLM field extraction or merges structured fields for a document."""
    from pipeline.nodes.llm_field_extractor import format_template_json
    raw_text = doc_data.get("_raw_text") or doc_data.get("rawText") or ""
    structured = {}

    # If raw text is present, extract canonical fields with LLM
    if raw_text.strip():
        doc_id = f"{loan_id}_{doc_key}"
        structured = llm_extract_fields(doc_type=doc_key, raw_text=raw_text, doc_id=doc_id)

    # Blend any preexisting structured keys (from spatial extraction, XML, or mock fallback)
    for k, v in doc_data.items():
        if not k.startswith("_") and k not in ("rawText", "formattedText"):
            if k not in structured or structured[k] is None:
                structured[k] = v

    # Format into canonical 22-field template
    template_fields = format_template_json(structured)
    structured.update(template_fields)

    # Preserve essential layout metadata for frontend inspection
    components = doc_data.get("_components", {})
    if "_components" in doc_data:
        structured["_components"] = components
    if "_raw_text" in doc_data:
        structured["_raw_text"] = doc_data["_raw_text"]
        structured["rawText"] = doc_data["_raw_text"]

    # Preserve or compute field locations
    if "_field_locations" in doc_data:
        structured["_field_locations"] = doc_data["_field_locations"]
    elif components and "raw_elements" in components:
        try:
            from idp.services.extraction.field_location_resolver import FieldLocationResolver
            resolver = FieldLocationResolver()
            field_locs = resolver.resolve_field_locations(
                extracted_fields=template_fields,
                ocr_elements=components.get("raw_elements", []),
                table_cells=components.get("table_cells", []),
                page_dimensions=components.get("page_dimensions", []),
                debug_mode=True
            )
            field_locs_dict = {k: v.model_dump() for k, v in field_locs.items()}
            structured["_field_locations"] = field_locs_dict
            components["field_locations"] = field_locs_dict
        except Exception as e:
            logger.debug("Field location resolution skipped for %s: %s", doc_key, e)

    import json
    formatted_json = doc_data.get("_formatted_text") or doc_data.get("formattedText") or json.dumps(template_fields, indent=2)
    structured["_formatted_text"] = formatted_json
    structured["formattedText"] = formatted_json

    return structured


def llm_structure(state: PipelineState) -> PipelineState:
    """Transforms raw OCR/IDP output into canonical structured JSON in S3 Extracted Structured tier."""
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("llm_structure")

    logger.info("Executing llm_structure for loan: %s", loan_id)

    if SKIP_IDP:
        logger.info("SKIP_IDP is active — bypassing LLM structuring for loan %s", loan_id)
        staged_docs = get_all_s3_extracted_structured(loan_id)
        merged = dict(state.get("extracted_data", {}))
        merged.update(staged_docs)
        update_status(loan_id, current_node="llm_structure", errors=errors, node_history=history)
        return {
            **state,
            "extracted_data": merged,
            "extracted_structured_data": merged,
            "errors": errors,
            "node_history": history,
        }

    extracted_data = dict(state.get("extracted_data", {}))
    structured_data: Dict[str, dict[str, Any]] = dict(state.get("extracted_structured_data", {}))


    # Parallelize LLM extraction across documents
    tasks = []
    for doc_k, doc_v in extracted_data.items():
        if isinstance(doc_v, dict) and not doc_k.startswith("_"):
            tasks.append((doc_k, doc_v))

    if tasks:
        worker_count = min(len(tasks), MAX_DOC_WORKERS)

        def _worker(t: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
            dk, dv = t
            return dk, _structure_single_document(dk, dv, loan_id)

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="llm_struct_worker") as executor:
            futures = [executor.submit(_worker, t) for t in tasks]
            for fut in futures:
                doc_key, structured = fut.result()
                structured_data[doc_key] = structured
                save_s3_extracted_structured(loan_id, doc_key, structured)

    # Ingest any fallback structured docs already in tier, preserving non-empty values
    tier_docs = get_all_s3_extracted_structured(loan_id)
    for k, v in tier_docs.items():
        if k not in structured_data:
            structured_data[k] = v
        elif isinstance(v, dict):
            for fk, fv in v.items():
                if fv is not None and (fk not in structured_data[k] or structured_data[k][fk] is None):
                    structured_data[k][fk] = fv

    # Downstream checkers expect fields in extracted_data (or structured_data)
    merged_extracted = dict(extracted_data)
    merged_extracted.update(structured_data)

    logger.info("Structured %d document(s) in S3 Extracted Structured tier for %s", len(structured_data), loan_id)

    update_status(loan_id, current_node="llm_structure", errors=errors, node_history=history)

    return {
        **state,
        "extracted_data": merged_extracted,
        "extracted_structured_data": structured_data,
        "errors": errors,
        "node_history": history,
    }
