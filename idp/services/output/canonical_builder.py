"""
Converts a processed ParsedDocument into the canonical storage-tier JSON format
written to s3_extracted/{loan_id}/{doc_key}.json.

Single source of truth for the extraction output contract. Called from
idp/api/routes/documents.py's /canonical endpoint — never from pipeline/.
"""
import json
import logging
from typing import Any, Dict

from idp.models.document import ParsedDocument
from idp.services.extraction.field_location_resolver import FieldLocationResolver
from pipeline.engines.key_value_extractor import KeyValueExtractor
from pipeline.engines.llm_field_extractor import format_template_json

logger = logging.getLogger("disbursement_idp.canonical_builder")


def build_canonical_extracted_dict(
    parsed: ParsedDocument,
    doc_type: str,
    doc_id: str,
) -> Dict[str, Any]:
    """Builds the canonical storage-tier extracted dict from a processed ParsedDocument.

    This is `build_idp_result_from_parsed` moved from pipeline/nodes/idp_scan.py into
    the IDP service, with two fixes:
      1. DRY fix: `format_template_json` is no longer called twice.
      2. Removed `llm_extract_fields` fallback: by the time this is called from Port 8001,
         `custom_metadata[\"llm_extracted_fields\"]` is already populated by the document
         processor. If it is absent, that is an unexpected state — log a warning.
    """
    if not parsed:
        return {}

    extracted_fields = (
        (parsed.custom_metadata or {}).get("llm_extracted_fields")
        if (parsed and parsed.custom_metadata)
        else None
    )

    if not extracted_fields:
        if doc_type == "aadhaar_xml":
            aadhaar_uid = (parsed.custom_metadata or {}).get("aadhaar_uid") if parsed else None
            extracted_fields = {"aadhaar_number": aadhaar_uid, "aadhaar_xml_present": True}
        elif doc_type == "loan_agreement":
            extracted_fields = {"loan_agreement_present": True}
        else:
            logger.warning(
                "llm_extracted_fields absent from custom_metadata for doc_type=%s doc_id=%s",
                doc_type,
                doc_id,
            )
            extracted_fields = {}

    # For Aadhaar XML docs, inject the UID extracted from the <UidData uid="..."> attribute.
    if doc_type == "aadhaar_xml":
        extracted_fields = dict(extracted_fields or {})
        extracted_fields["aadhaar_xml_present"] = True
        if parsed and parsed.custom_metadata:
            aadhaar_uid = parsed.custom_metadata.get("aadhaar_uid")
            if aadhaar_uid and not extracted_fields.get("aadhaar_number"):
                extracted_fields["aadhaar_number"] = aadhaar_uid

    template_fields = format_template_json(extracted_fields or {})

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
        "raw_elements": raw_element_dicts,
    }

    result = {
        **template_fields,
        **(extracted_fields or {}),
        "_raw_text": parsed.text,
        "rawText": parsed.text,
        "_formatted_text": formatted_json,
        "formattedText": formatted_json,
        "_pages": len(parsed.pages),
        "_elements_count": len(parsed.elements),
        "_components": components,
        "_field_locations": field_locs_dict,
    }

    if parsed.custom_metadata:
        for k, v in parsed.custom_metadata.items():
            if k not in result and k != "llm_extracted_fields":
                result[k] = v

    return result

