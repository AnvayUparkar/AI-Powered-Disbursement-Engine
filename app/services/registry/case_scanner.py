"""Filesystem scanner and enrichment service for case documents."""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import DMS_DIR, IST, S3_EXTRACTED_DIR, S3_EXTRACTED_STRUCTURED_DIR, S3_RAW_DIR
from config.tenant import current_tenant_id
from pipeline.engines.llm_field_extractor import format_template_json
from pipeline.storage import list_loan_ids

from config.doc_types import get_canonical_doc_type
from .normalizer import build_default_extracted_fields, build_document_timing, build_lightonocr_processing_step, build_ocr_engine_info
from .resolver import guess_doc_type

logger = logging.getLogger("disbursement_pipeline.document_registry.case_scanner")

_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".zip", ".xml"}
_EXCLUDED_EXTRACTED_NAMES = {"status.json", "dms_status.json", "face_embeddings.json"}

# Cache state for case documents, keyed by tenant_id -> (timestamp, docs)
_CASE_DOCS_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS: float = 3.0  # short TTL avoids stale reads while protecting against rapid request bursts


def invalidate_case_cache() -> None:
    """Invalidate the current tenant's cached case documents."""
    _CASE_DOCS_CACHE.pop(current_tenant_id(), None)


def _collect_case_candidates(
    c_id: str,
    case_s3_dir: Path,
    case_dms_dir: Path,
    case_ext_dir: Path,
) -> List[Tuple[str, Path, str]]:
    """Gather candidate document files from raw S3, DMS, or extracted directories."""
    seen_filenames: Set[str] = set()
    candidate_files: List[Tuple[str, Path, str]] = []

    # 1. Real files in S3 raw
    if case_s3_dir.exists():
        for rf in sorted(case_s3_dir.iterdir()):
            if (
                rf.is_file()
                and rf.name != f"{c_id}.json"
                and not rf.name.endswith(".metadata.json")
                and rf.suffix.lower() in _ALLOWED_EXTENSIONS
            ):
                if rf.name not in seen_filenames:
                    seen_filenames.add(rf.name)
                    candidate_files.append((rf.name, rf, "s3_raw"))

    # 2. Real files in DMS
    if case_dms_dir.exists():
        for rf in sorted(case_dms_dir.iterdir()):
            if (
                rf.is_file()
                and rf.name != f"{c_id}.json"
                and not rf.name.endswith(".metadata.json")
                and not rf.name.endswith(".json")
                and rf.suffix.lower() in _ALLOWED_EXTENSIONS
            ):
                if rf.name not in seen_filenames:
                    seen_filenames.add(rf.name)
                    candidate_files.append((rf.name, rf, "dms"))

    # 3. If no raw/dms files exist, check if extracted JSONs exist for pipeline runs
    if not candidate_files and case_ext_dir.exists():
        for ef in sorted(case_ext_dir.glob("*.json")):
            if ef.name != f"{c_id}.json" and ef.name not in _EXCLUDED_EXTRACTED_NAMES:
                fake_name = f"{ef.stem}.pdf"
                if fake_name not in seen_filenames:
                    seen_filenames.add(fake_name)
                    candidate_files.append((fake_name, ef, "extracted"))

    return candidate_files


def _find_extracted_and_structured_files(
    case_ext_dir: Path,
    c_id: str,
    doc_filename: str,
    doc_type: str,
) -> Tuple[Optional[Path], Optional[Path]]:
    """Locate matching extracted and structured JSON files for a given document."""
    if not case_ext_dir.exists():
        return None, None

    stem = Path(doc_filename).stem.lower().replace(" ", "_")
    canonical_key = get_canonical_doc_type(doc_filename)
    type_canonical = get_canonical_doc_type(doc_type)

    candidates = [
        f"{canonical_key}.json",
        f"{canonical_key}_structured.json",
        f"{type_canonical}.json",
        f"{type_canonical}_structured.json",
        f"{stem}.json",
        f"{stem}_structured.json",
    ]

    ext_file: Optional[Path] = None
    struct_file: Optional[Path] = None

    for cand_name in candidates:
        cand_path = case_ext_dir / cand_name
        if cand_path.exists():
            if cand_name.endswith("_structured.json"):
                struct_file = cand_path
            elif not ext_file:
                ext_file = cand_path

    # Fallback fuzzy matching
    if not ext_file:
        for ef in sorted(case_ext_dir.glob("*.json")):
            if ef.name == f"{c_id}.json" or ef.name in _EXCLUDED_EXTRACTED_NAMES:
                continue
            ef_stem = ef.stem.replace("_structured", "").lower()
            if ef_stem in fn_lower or ef_stem in mapped_key or (mapped_key and mapped_key in ef_stem):
                if ef.name.endswith("_structured.json"):
                    struct_file = ef
                else:
                    ext_file = ef
                    break

    if not struct_file and ext_file:
        cand_struct = ext_file.parent / f"{ext_file.stem}_structured.json"
        if cand_struct.exists():
            struct_file = cand_struct

    return ext_file, struct_file


def _build_case_document_record(
    c_id: str,
    doc_filename: str,
    fpath: Path,
    source_kind: str,
    case_ext_dir: Path,
) -> Dict[str, Any]:
    """Construct document record from case file and extracted json files."""
    doc_id = f"doc-{c_id}-{Path(doc_filename).stem.lower().replace(' ', '_')}"
    doc_type = guess_doc_type(doc_filename)

    ext_file, struct_file = _find_extracted_and_structured_files(case_ext_dir, c_id, doc_filename, doc_type)

    ext_data: Dict[str, Any] = {}
    if ext_file and ext_file.exists():
        try:
            ext_data = json.loads(ext_file.read_text(encoding="utf-8")) or {}
        except Exception:
            ext_data = {}

    struct_data: Dict[str, Any] = {}
    if struct_file and struct_file.exists():
        try:
            struct_data = json.loads(struct_file.read_text(encoding="utf-8")) or {}
        except Exception:
            struct_data = {}

    raw_text = (
        ext_data.get("_raw_text")
        or ext_data.get("rawText")
        or ext_data.get("raw_text")
        or struct_data.get("rawText")
        or struct_data.get("_raw_text")
    )

    # Engine that produced the text, recorded by idp_scan.build_idp_result_from_parsed ("_processing").
    ocr_engine = build_ocr_engine_info(ext_data.get("_processing") or struct_data.get("_processing"))

    document_markdown = (
        ext_data.get("documentMarkdown")
        or ext_data.get("document_markdown")
        or struct_data.get("documentMarkdown")
        or struct_data.get("document_markdown")
    )

    paragraphs = (
        struct_data.get("paragraphs")
        or ext_data.get("_components", {}).get("paragraphs")
        or []
    )
    if not raw_text and paragraphs:
        lines = [p.get("text", "") for p in paragraphs if isinstance(p, dict) and p.get("text")]
        if lines:
            raw_text = "--- PAGE 1 ---\n" + "\n".join(lines)

    if not raw_text and ext_data:
        kv_lines = [
            f"{k.replace('_', ' ').title()}: {v}"
            for k, v in ext_data.items()
            if v is not None and not k.startswith("_") and not isinstance(v, (dict, list))
        ]
        if kv_lines:
            raw_text = "\n".join(kv_lines)

    pages = ext_data.get("_pages") or ext_data.get("pages") or struct_data.get("_pages") or 1
    if isinstance(pages, list):
        pages = len(pages)
    else:
        try:
            pages = int(pages)
        except (ValueError, TypeError):
            pages = 1

    field_locations = struct_data.get("field_locations") or {}
    ocr_tokens = struct_data.get("ocr_tokens") or []

    extracted_fields: List[Dict[str, Any]] = []
    for k, v in ext_data.items():
        if k.startswith("_") or isinstance(v, (dict, list)):
            continue
        fl = field_locations.get(k) or {}
        fl_bbox = fl.get("bbox")
        fl_status = fl.get("location_status", "resolved" if fl_bbox else "unresolved")
        raw_conf = fl.get("confidence", 0.97)
        fl_conf = round(raw_conf * 100, 1) if raw_conf <= 1.0 else round(raw_conf, 1)
        extracted_fields.append({
            "id": f"fld-{doc_id}-{k.lower().replace(' ', '_')}",
            "name": k.replace("_", " ").title(),
            "value": str(v),
            "confidence": fl_conf,
            "sourceDocumentId": doc_id,
            "page": fl.get("page", 1),
            "type": "key_value",
            "source": "OPENROUTER_LLM",
            "bbox": fl_bbox,
            "locationStatus": fl_status,
            "matchedText": fl.get("matched_text"),
            "matchConfidence": fl.get("match_confidence", 1.0),
            "reason": fl.get("reason"),
            "matchStrategy": fl.get("match_strategy"),
            "candidates": fl.get("candidates", []),
        })

    if paragraphs:
        for idx, p in enumerate(paragraphs):
            if not isinstance(p, dict):
                continue
            p_text = (p.get("text") or "").strip()
            if not p_text:
                continue
            p_conf = p.get("confidence", 0.95)
            conf_val = round(p_conf * 100, 1) if p_conf <= 1.0 else round(p_conf, 1)
            extracted_fields.append({
                "id": p.get("id") or f"fld-{doc_id}-p-{idx + 1}",
                "name": p.get("classification", "paragraph").replace("_", " ").title(),
                "value": p_text,
                "confidence": conf_val,
                "sourceDocumentId": doc_id,
                "page": p.get("page_number", 1),
                "type": "text",
                "bbox": p.get("bbox"),
                "source": "OCR",
            })

    tables = ext_data.get("_components", {}).get("tables") or struct_data.get("tables") or []
    for t_idx, tbl in enumerate(tables):
        if not isinstance(tbl, dict):
            continue
        rows = tbl.get("rows") or []
        headers = tbl.get("headers") or []
        extracted_fields.append({
            "id": tbl.get("id") or f"table-{doc_id}-{t_idx + 1}",
            "name": f"Table (Page {tbl.get('page_number', 1)})",
            "value": f"{len(rows)} rows x {len(headers) if headers else (len(rows[0]) if rows else 0)} cols",
            "confidence": 95,
            "sourceDocumentId": doc_id,
            "page": tbl.get("page_number", 1),
            "type": "table",
            "source": "docling",
            "bbox": tbl.get("bbox"),
            "headers": headers,
            "rows": rows,
            "cells": tbl.get("cells"),
            "markdown": tbl.get("markdown"),
        })

    if not extracted_fields:
        extracted_fields = build_default_extracted_fields(doc_id, doc_filename, doc_type, is_status=False)

    size_kb = 45
    if source_kind != "extracted" and fpath.exists():
        try:
            size_kb = max(1, round(fpath.stat().st_size / 1024))
        except OSError:
            size_kb = 45

    has_data = bool(ext_data or struct_data or raw_text)

    formatted_text = (
        ext_data.get("_formatted_text")
        or ext_data.get("formattedText")
        or struct_data.get("formattedText")
    )
    if ext_data:
        try:
            formatted_text = json.dumps(format_template_json(ext_data), indent=2)
        except Exception:
            llm_extracted_dict = {
                k: v for k, v in ext_data.items()
                if not k.startswith("_") and not isinstance(v, (dict, list))
            }
            formatted_text = json.dumps(llm_extracted_dict, indent=2)
    elif not formatted_text:
        formatted_text = ""

    if fpath.exists():
        mtime = fpath.stat().st_mtime
    elif ext_file and ext_file.exists():
        mtime = ext_file.stat().st_mtime
    elif struct_file and struct_file.exists():
        mtime = struct_file.stat().st_mtime
    else:
        mtime = time.time()
    uploaded_at = datetime.fromtimestamp(mtime, tz=IST).strftime("%Y-%m-%d %H:%M IST")

    return {
        "id": doc_id,
        "name": doc_filename,
        "type": doc_type,
        "pages": pages,
        "ocrStatus": "COMPLETED" if has_data else "PENDING",
        "extractionStatus": "COMPLETED" if has_data else "PENDING",
        "confidence": 98.0 if has_data else 95.0,
        "vlmUsed": bool(ext_data.get("_vlm_used", False)),
        "uploadedAt": uploaded_at,
        "uploadedTimestamp": mtime,
        "caseId": c_id,
        "sizeKb": size_kb,
        "extractedFields": extracted_fields,
        "rawText": raw_text or f"Document Name: {doc_filename}\nType: {doc_type}",
        "formattedText": formatted_text,
        "documentMarkdown": document_markdown,
        "debug": {
            "field_locations": field_locations,
            "ocr_tokens": ocr_tokens,
            "page_dimensions": struct_data.get("page_dimensions", []),
        },
        "ocrEngine": ocr_engine,
        "timing": build_document_timing(ext_data.get("_processing") or struct_data.get("_processing")),
        "processingSteps": [build_lightonocr_processing_step(doc_id, ocr_engine)] if ocr_engine and ocr_engine.get("engine") == "lightonocr" else [
            {
                "id": f"stp-{doc_id}-1",
                "component": "PaddleOCR",
                "status": "COMPLETED" if has_data else "PENDING",
                "detail": f"{doc_filename} OCR processing",
                "startedAt": "10:30:00",
                "confidence": 98.0,
            }
        ],
    }


def scan_case_documents(dynamic_doc_ids: Optional[Set[str]] = None, use_cache: bool = True) -> List[Dict[str, Any]]:
    """
    Index actual documents stored for all registered loan cases without phantom files.
    Cached for brief intervals to prevent I/O thrashing during repeated requests.
    """
    tenant_id = current_tenant_id()

    now = time.time()
    cached = _CASE_DOCS_CACHE.get(tenant_id)
    if use_cache and cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    loan_ids = list_loan_ids()
    docs: List[Dict[str, Any]] = []
    dynamic_set = dynamic_doc_ids or set()

    for c_id in loan_ids:
        case_s3_dir = S3_RAW_DIR / c_id
        case_dms_dir = DMS_DIR / c_id
        case_ext_dir = S3_EXTRACTED_DIR / c_id

        candidates = _collect_case_candidates(c_id, case_s3_dir, case_dms_dir, case_ext_dir)
        for doc_filename, fpath, source_kind in candidates:
            doc_id = f"doc-{c_id}-{Path(doc_filename).stem.lower().replace(' ', '_')}"
            if doc_id in dynamic_set:
                continue

            doc_record = _build_case_document_record(c_id, doc_filename, fpath, source_kind, case_ext_dir)
            docs.append(doc_record)

    _CASE_DOCS_CACHE[tenant_id] = (now, docs)
    return docs


def enrich_document_record(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich an existing document record with raw text, markdown, formatted text, and canonical
    fields from on-disk extracted JSONs if raw text or formattedText is missing or empty.
    """
    raw_val = (doc.get("rawText") or "").strip()
    is_placeholder_raw = not raw_val or raw_val.startswith("Document Name:")

    doc_id = doc.get("id", "")
    c_id = doc.get("caseId")
    if not c_id or c_id == "GENERAL":
        m = re.search(r"(LOAN_\d+)", f"{doc_id}_{doc.get('name', '')}")
        if m:
            c_id = m.group(1)
            doc["caseId"] = c_id

    if not c_id or c_id == "GENERAL":
        return doc

    c_ext = S3_EXTRACTED_DIR / c_id
    c_struct = S3_EXTRACTED_STRUCTURED_DIR / c_id
    stem = Path(doc.get("name", "")).stem.lower().replace(" ", "_")
    clean_stem = re.sub(r"^(loan_\d+|appl\d+)_", "", stem)
    canonical_key = get_canonical_doc_type(doc.get("name", ""))
    type_canonical = get_canonical_doc_type(doc.get("type", ""))

    cands = [
        c_struct / f"{canonical_key}.json" if c_struct.exists() else None,
        c_ext / f"{canonical_key}.json" if c_ext.exists() else None,
        c_struct / f"{type_canonical}.json" if c_struct.exists() else None,
        c_ext / f"{type_canonical}.json" if c_ext.exists() else None,
        c_struct / f"{clean_stem}.json" if c_struct.exists() else None,
        c_ext / f"{clean_stem}.json" if c_ext.exists() else None,
        c_ext / f"{doc.get('name')}.json" if c_ext.exists() else None,
        c_ext / f"{Path(doc.get('name', '')).stem}.json" if c_ext.exists() else None,
        c_ext / f"{stem}.json" if c_ext.exists() else None,
    ]

    for cp in cands:
        if cp and cp.exists() and cp.is_file():
            try:
                loaded = json.loads(cp.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    loaded_raw = (loaded.get("_raw_text") or loaded.get("rawText") or loaded.get("raw_text") or "").strip()
                    if is_placeholder_raw and loaded_raw:
                        doc["rawText"] = loaded_raw
                        is_placeholder_raw = False

                    if not doc.get("documentMarkdown") and (loaded.get("documentMarkdown") or loaded.get("document_markdown")):
                        doc["documentMarkdown"] = loaded.get("documentMarkdown") or loaded.get("document_markdown")

                    if not doc.get("timing") and loaded.get("_processing"):
                        doc["timing"] = build_document_timing(loaded.get("_processing"))

                    if loaded_raw or any(k in loaded for k in ("applicant_name", "loan_amount", "pan_number", "mobile_no", "dob")):
                        doc["ocrStatus"] = "COMPLETED"
                        doc["extractionStatus"] = "COMPLETED"
                        doc["status"] = "processed"

                    if not (doc.get("formattedText") or "").strip().startswith("{"):
                        if any(k in loaded for k in ("applicant_name", "loan_amount", "pan_number", "mobile_no", "dob")):
                            tpl = format_template_json(loaded)
                            doc["formattedText"] = json.dumps(tpl, indent=2)

                            existing_fnames = {f.get("name") for f in doc.get("extractedFields", [])}
                            for tk, tv in tpl.items():
                                nice_name = tk.replace("_", " ").title()
                                if tv is not None and nice_name not in existing_fnames:
                                    doc.setdefault("extractedFields", []).append({
                                        "id": f"llm-{tk}",
                                        "name": nice_name,
                                        "value": str(tv),
                                        "confidence": 98.0,
                                        "sourceDocumentId": doc_id,
                                        "page": 1,
                                        "type": "key_value",
                                        "source": "OPENROUTER_LLM",
                                    })
                    break
            except Exception:
                pass

    return doc

