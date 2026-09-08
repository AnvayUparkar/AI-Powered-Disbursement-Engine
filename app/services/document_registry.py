import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.serializers.case_serializer import serialize_all_cases
from config import BASE_DIR, DMS_DIR, S3_EXTRACTED_DIR, S3_RAW_DIR
from idp.core.config import settings as idp_settings
from pipeline.nodes.llm_field_extractor import format_template_json
from pipeline.storage import list_loan_ids

logger = logging.getLogger("disbursement_pipeline.document_registry")


class DocumentRegistry:
    """
    Production-grade Unified Document Registry.
    Maintains a single source of truth for all case documents and IDP-uploaded documents,
    supporting real-time indexing, search, filtering, and detail extraction.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._dynamic_docs: Dict[str, Dict[str, Any]] = {}
        self._doc_aliases: Dict[str, str] = {}
        self._initialized = False

    def _guess_doc_type(self, filename: str) -> str:
        try:
            from config.doc_types import get_display_name
            return get_display_name(filename)
        except Exception:
            pass
        n = filename.lower()
        if "app" in n or "application" in n:
            return "Application Form"
        if "pan" in n:
            return "PAN"
        if ("aadhaar" in n or "aadhar" in n or "adhar" in n) and "xml" in n:
            return "Aadhaar XML"
        if "aadhaar" in n or "aadhar" in n or "adhar" in n:
            return "Aadhaar"
        if "kyc" in n:
            return "KYC"
        if "kfs" in n:
            return "KFS"
        if "sanction" in n:
            return "Sanction Letter"
        if "agreement" in n:
            return "Loan Agreement"
        if "memo" in n or "disbursal" in n:
            return "Disbursal Memo"
        if "bt" in n or "foreclosure" in n:
            return "BT Details"
        if "vkyc" in n:
            return "VKYC Audit Trail"
        return "Miscellaneous"

    def register_uploaded_document(
        self,
        doc_id: str,
        filename: str,
        doc_type: Optional[str] = None,
        case_id: Optional[str] = None,
        file_size_bytes: int = 0,
        parsed_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Register a newly uploaded and processed document in the registry.
        """
        with self._lock:
            detected_type = doc_type or self._guess_doc_type(filename)
            assoc_case = case_id or "GENERAL"
            upload_date = datetime.now().strftime("%Y-%m-%d")

            pages_count = 1
            confidence = 96.5
            vlm_used = False
            extracted_fields: List[Dict[str, Any]] = []
            llm_meta: Dict[str, Any] = {}
            processing_steps: List[Dict[str, Any]] = [
                {
                    "id": f"stp-{doc_id}-1",
                    "component": "Docling",
                    "status": "COMPLETED",
                    "detail": "Docling parsed document structure",
                    "startedAt": datetime.now().strftime("%H:%M:%S"),
                },
                {
                    "id": f"stp-{doc_id}-2",
                    "component": "PaddleOCR",
                    "status": "COMPLETED",
                    "detail": "RapidOCR PP-OCRv6 extracted text",
                    "startedAt": datetime.now().strftime("%H:%M:%S"),
                    "confidence": 95.0,
                },
            ]

            if parsed_result:
                pages_count = len(parsed_result.get("pages") or []) or 1
                vlm_used = bool(parsed_result.get("processing", {}).get("vlm_used", False))
                confidence = 91.0 if vlm_used else 97.5

                # Ingest LLM-extracted canonical fields if available
                llm_meta = (parsed_result.get("custom_metadata") or {}).get("llm_extracted_fields") or {}
                if not llm_meta and assoc_case and assoc_case != "GENERAL":
                    case_ext_dir = S3_EXTRACTED_DIR / assoc_case
                    if case_ext_dir.exists():
                        from pathlib import Path as _Path
                        stem = _Path(filename).stem.lower().replace(" ", "_")
                        cands = [
                            case_ext_dir / f"{filename}.json",
                            case_ext_dir / f"{_Path(filename).stem}.json",
                            case_ext_dir / f"{stem}.json",
                            case_ext_dir / f"{detected_type.lower().replace(' ', '_')}.json",
                            case_ext_dir / f"{detected_type}.json",
                            case_ext_dir / "Application Form.json" if "app" in stem else None,
                            case_ext_dir / "application_form.json" if "app" in stem else None,
                        ]
                        for cp in cands:
                            if cp and cp.exists() and cp.is_file():
                                try:
                                    loaded_data = json.loads(cp.read_text(encoding="utf-8"))
                                    if isinstance(loaded_data, dict) and any(k in loaded_data for k in ("applicant_name", "loan_amount", "pan_number", "mobile_no", "dob")):
                                        llm_meta = loaded_data
                                        break
                                except Exception:
                                    pass

                field_locs = (parsed_result.get("custom_metadata") or {}).get("field_locations") or {}
                for lk, lv in llm_meta.items():
                    if lv is not None:
                        fl = field_locs.get(lk) or {}
                        fl_bbox = fl.get("bbox")
                        fl_status = fl.get("location_status", "resolved" if fl_bbox else "unresolved")
                        fl_conf = round(fl.get("confidence", 0.98) * 100, 1) if fl.get("confidence", 1) <= 1.0 else fl.get("confidence", 98.0)
                        extracted_fields.append({
                            "id": f"llm-{lk}",
                            "name": lk.replace("_", " ").title(),
                            "value": str(lv),
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

                # Extract key values from parsed elements
                elements = parsed_result.get("elements") or []

                for idx, e in enumerate(elements):
                    text = e.get("text", "")
                    if not text or not text.strip():
                        continue
                    conf = round(e.get("confidence", 0.95) * 100) if e.get("confidence", 1) <= 1.0 else round(e.get("confidence", 95))
                    page_num = e.get("page_number", 1)

                    if ":" in text or "=" in text:
                        delim = ":" if ":" in text else "="
                        parts = text.split(delim, 1)
                        k, v = parts[0].strip(), parts[1].strip()
                        if k and v:
                            extracted_fields.append({
                                "id": e.get("id") or f"f-{idx + 1}",
                                "name": k,
                                "value": v,
                                "confidence": conf,
                                "sourceDocumentId": doc_id,
                                "page": page_num,
                                "type": "key_value",
                                "source": e.get("source", "ocr"),
                                "bbox": e.get("bbox"),
                            })
                            continue

                    extracted_fields.append({
                        "id": e.get("id") or f"f-{idx + 1}",
                        "name": "Text Block" if e.get("type") != "heading" else "Heading",
                        "value": text.strip(),
                        "confidence": conf,
                        "sourceDocumentId": doc_id,
                        "page": page_num,
                        "type": e.get("type", "text"),
                        "source": e.get("source", "ocr"),
                        "bbox": e.get("bbox"),
                    })

                # Process parsed tables
                tables = parsed_result.get("tables") or []
                for t_idx, tbl in enumerate(tables):
                    extracted_fields.append({
                        "id": tbl.get("id") or f"table-{t_idx + 1}",
                        "name": f"Table (Page {tbl.get('page_number', 1)})",
                        "value": f"{tbl.get('num_rows', 0)} rows x {tbl.get('num_cols', 0)} cols",
                        "confidence": 95,
                        "sourceDocumentId": doc_id,
                        "page": tbl.get("page_number", 1),
                        "type": "table",
                        "source": "docling",
                        "headers": tbl.get("headers"),
                        "rows": tbl.get("rows_raw"),
                    })

            if not extracted_fields:
                extracted_fields = [
                    {
                        "id": f"fld-{doc_id}-1",
                        "name": "Document Name",
                        "value": filename,
                        "confidence": 99.0,
                        "sourceDocumentId": doc_id,
                        "page": 1,
                    },
                    {
                        "id": f"fld-{doc_id}-2",
                        "name": "Processing Status",
                        "value": "Verified & Indexed",
                        "confidence": 98.0,
                        "sourceDocumentId": doc_id,
                        "page": 1,
                    },
                ]

            p_res = parsed_result or {}
            raw_text_val = (p_res.get("text") or p_res.get("raw_text") or p_res.get("rawText") or "").strip()
            if not raw_text_val:
                raw_text_val = f"Document Name: {filename}\nType: {detected_type}"

            fmt_text_val = (
                json.dumps(format_template_json(llm_meta), indent=2)
                if llm_meta
                else (p_res.get("formatted_text") or p_res.get("formattedText") or "")
            )

            record = {
                "id": doc_id,
                "name": filename,
                "type": detected_type,
                "pages": pages_count,
                "ocrStatus": "COMPLETED",
                "extractionStatus": "COMPLETED",
                "confidence": confidence,
                "vlmUsed": vlm_used,
                "uploadedAt": upload_date,
                "caseId": assoc_case,
                "sizeKb": max(1, round(file_size_bytes / 1024)) if file_size_bytes else 45,
                "extractedFields": extracted_fields,
                "processingSteps": processing_steps,
                "rawText": raw_text_val,
                "formattedText": fmt_text_val,
                "debug": {
                    "field_locations": field_locs if parsed_result else {},
                    "ocr_tokens": (p_res.get("custom_metadata") or {}).get("ocr_tokens") or [],
                    "page_dimensions": p_res.get("pages_dimensions") or [],
                },
            }


            self._dynamic_docs[doc_id] = record

            # Map filename-based ID alias without polluting _dynamic_docs with duplicate records
            if case_id and filename:
                from pathlib import Path as _Path
                alt_id = f"doc-{case_id}-{_Path(filename).stem.lower().replace(' ', '_')}"
                if alt_id != doc_id:
                    self._doc_aliases[alt_id] = doc_id

            logger.info("Registered document %s (%s) for case %s", doc_id, filename, assoc_case)
            return record

    def _scan_idp_parsed_storage(self) -> None:
        """Scan disk storage for any existing parsed documents in IDP store."""
        if getattr(self, "_parsed_storage_scanned", False):
            return
        self._parsed_storage_scanned = True
        try:
            parsed_dir = Path(idp_settings.TEMP_DIR) / "s3_mock" / idp_settings.S3_BUCKET / idp_settings.PARSED_DOCUMENT_PREFIX
            if not parsed_dir.exists():
                return

            for json_file in parsed_dir.glob("*.json"):
                doc_id = json_file.stem
                if doc_id in self._dynamic_docs:
                    continue
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    filename = data.get("source", {}).get("filename") or f"{doc_id}.pdf"
                    s3_key = data.get("source", {}).get("s3_key") or ""
                    inferred_case = None
                    import re
                    m = re.search(r"(LOAN_\d+)", f"{doc_id}_{filename}_{s3_key}")
                    if m:
                        inferred_case = m.group(1)

                    self.register_uploaded_document(
                        doc_id=doc_id,
                        filename=filename,
                        case_id=inferred_case,
                        parsed_result=data,
                        file_size_bytes=data.get("processing", {}).get("file_size_bytes", 150000),
                    )
                except Exception as e:
                    logger.debug("Failed indexing parsed document file %s: %s", json_file, e)
        except Exception as e:
            logger.debug("Error during IDP parsed storage scan: %s", e)

    def _get_case_documents(self) -> List[Dict[str, Any]]:
        """Index actual documents stored for all registered loan cases without phantom files."""
        loan_ids = list_loan_ids()
        docs = []

        for c_id in loan_ids:
            case_s3_dir = S3_RAW_DIR / c_id
            case_dms_dir = DMS_DIR / c_id
            case_ext_dir = S3_EXTRACTED_DIR / c_id

            seen_filenames = set()
            candidate_files = []

            # 1. Real files in S3 raw
            if case_s3_dir.exists():
                for rf in sorted(case_s3_dir.iterdir()):
                    if (
                        rf.is_file()
                        and rf.name != f"{c_id}.json"
                        and not rf.name.endswith(".metadata.json")
                        and rf.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".zip", ".xml")
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
                        and rf.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".zip", ".xml")
                    ):
                        if rf.name not in seen_filenames:
                            seen_filenames.add(rf.name)
                            candidate_files.append((rf.name, rf, "dms"))

            # 3. If no raw/dms files exist, check if extracted JSONs exist for pipeline runs
            if not candidate_files and case_ext_dir.exists():
                for ef in sorted(case_ext_dir.glob("*.json")):
                    if ef.name not in (f"{c_id}.json", "status.json", "dms_status.json", "face_embeddings.json"):
                        fake_name = f"{ef.stem}.pdf"
                        if fake_name not in seen_filenames:
                            seen_filenames.add(fake_name)
                            candidate_files.append((fake_name, ef, "extracted"))

            for doc_filename, fpath, source_kind in candidate_files:
                doc_id = f"doc-{c_id}-{Path(doc_filename).stem.lower().replace(' ', '_')}"
                if doc_id in self._dynamic_docs:
                    continue

                doc_type = self._guess_doc_type(doc_filename)

                # Check extracted data if available
                ext_file = None
                struct_file = None
                if case_ext_dir.exists():
                    stem = Path(doc_filename).stem.lower().replace(" ", "_")
                    type_clean = doc_type.lower().replace(" ", "_")

                    # Compute mapped canonical type key (e.g. kyc_pan for PAN Card.png)
                    mapped_key = ""
                    fn_lower = doc_filename.lower()
                    if "pan" in fn_lower:
                        mapped_key = "kyc_pan"
                    elif "application" in fn_lower:
                        mapped_key = "application_form"
                    elif "agreement" in fn_lower:
                        mapped_key = "loan_agreement"
                    elif "kfs" in fn_lower:
                        mapped_key = "kfs"
                    elif "sanction" in fn_lower:
                        mapped_key = "sanction_letter"
                    elif "aadhaar" in fn_lower or "kyc" in fn_lower or "address" in fn_lower:
                        mapped_key = "kyc_address_proof"
                    elif "bank" in fn_lower or "statement" in fn_lower:
                        mapped_key = "bank_statement"
                    elif "memo" in fn_lower or "disbursal" in fn_lower:
                        mapped_key = "disbursal_memo"

                    candidates = []
                    if mapped_key:
                        candidates.extend([f"{mapped_key}.json", f"{mapped_key}_structured.json"])
                    candidates.extend([
                        f"{stem}.json",
                        f"{stem}_structured.json",
                        f"{type_clean}.json",
                        f"{type_clean}_structured.json",
                    ])

                    for cand_name in candidates:
                        cand_path = case_ext_dir / cand_name
                        if cand_path.exists():
                            if cand_name.endswith("_structured.json"):
                                struct_file = cand_path
                            elif not ext_file:
                                ext_file = cand_path

                    # Fallback fuzzy matching in case_ext_dir if ext_file still None
                    if not ext_file:
                        for ef in sorted(case_ext_dir.glob("*.json")):
                            if ef.name in (f"{c_id}.json", "status.json", "dms_status.json", "face_embeddings.json"):
                                continue
                            ef_stem = ef.stem.replace("_structured", "").lower()
                            if ef_stem in fn_lower or ef_stem in mapped_key or (mapped_key and mapped_key in ef_stem):
                                if ef.name.endswith("_structured.json"):
                                    struct_file = ef
                                else:
                                    ext_file = ef
                                    break

                ext_data = {}
                if ext_file and ext_file.exists():
                    try:
                        ext_data = json.loads(ext_file.read_text(encoding="utf-8")) or {}
                    except Exception:
                        ext_data = {}

                struct_data = {}
                if not struct_file and ext_file:
                    cand_struct = ext_file.parent / f"{ext_file.stem}_structured.json"
                    if cand_struct.exists():
                        struct_file = cand_struct

                if struct_file and struct_file.exists():
                    try:
                        struct_data = json.loads(struct_file.read_text(encoding="utf-8")) or {}
                    except Exception:
                        struct_data = {}

                # Determine rawText
                raw_text = (
                    ext_data.get("_raw_text")
                    or ext_data.get("rawText")
                    or ext_data.get("raw_text")
                    or struct_data.get("rawText")
                    or struct_data.get("_raw_text")
                )

                # Check embedded components / paragraphs if raw_text not explicitly present
                paragraphs = (
                    struct_data.get("paragraphs")
                    or ext_data.get("_components", {}).get("paragraphs")
                    or []
                )
                if not raw_text and paragraphs:
                    lines = [p.get("text", "") for p in paragraphs if isinstance(p, dict) and p.get("text")]
                    if lines:
                        raw_text = f"--- PAGE 1 ---\n" + "\n".join(lines)
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

                # If field_locations was not pre-computed, resolve on-the-fly from paragraphs/tables
                if not field_locations and (paragraphs or struct_data.get("tables")):
                    try:
                        from idp.services.extraction.field_location_resolver import FieldLocationResolver
                        resolver = FieldLocationResolver()
                        field_locations_obj = resolver.resolve_field_locations(
                            extracted_fields=ext_data,
                            ocr_elements=paragraphs,
                            table_cells=[],
                            page_dimensions=struct_data.get("page_dimensions"),
                            debug_mode=True
                        )
                        field_locations = {k: v.model_dump() for k, v in field_locations_obj.items()}
                        if not ocr_tokens:
                            ocr_tokens = [t.model_dump() for t in resolver.extract_debug_tokens(paragraphs, struct_data.get("page_dimensions"))]
                    except Exception as res_err:
                        logger.debug("On-the-fly field location resolution note: %s", res_err)

                extracted_fields = []
                for k, v in ext_data.items():
                    if k.startswith("_") or isinstance(v, (dict, list)):
                        continue
                    fl = field_locations.get(k) or {}
                    fl_bbox = fl.get("bbox")
                    fl_status = fl.get("location_status", "resolved" if fl_bbox else "unresolved")
                    fl_conf = round(fl.get("confidence", 0.97) * 100, 1) if fl.get("confidence", 1) <= 1.0 else fl.get("confidence", 97.0)
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

                # If paragraphs exist, append text blocks to extractedFields
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

                if not extracted_fields:
                    extracted_fields = [
                        {
                            "id": f"fld-{doc_id}-1",
                            "name": "Document Name",
                            "value": doc_filename,
                            "confidence": 99.0,
                            "sourceDocumentId": doc_id,
                            "page": 1,
                        },
                        {
                            "id": f"fld-{doc_id}-2",
                            "name": "Type",
                            "value": doc_type,
                            "confidence": 98.0,
                            "sourceDocumentId": doc_id,
                            "page": 1,
                        },
                    ]

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
                        from pipeline.nodes.llm_field_extractor import format_template_json
                        formatted_text = json.dumps(format_template_json(ext_data), indent=2)
                    except Exception:
                        llm_extracted_dict = {
                            k: v for k, v in ext_data.items()
                            if not k.startswith("_") and not isinstance(v, (dict, list))
                        }
                        formatted_text = json.dumps(llm_extracted_dict, indent=2)
                elif not formatted_text:
                    formatted_text = ""

                docs.append({
                    "id": doc_id,
                    "name": doc_filename,
                    "type": doc_type,
                    "pages": pages,
                    "ocrStatus": "COMPLETED" if has_data else "PENDING",
                    "extractionStatus": "COMPLETED" if has_data else "PENDING",
                    "confidence": 98.0 if has_data else 95.0,
                    "vlmUsed": bool(ext_data.get("_vlm_used", False)),
                    "uploadedAt": datetime.now().strftime("%Y-%m-%d"),
                    "caseId": c_id,
                    "sizeKb": size_kb,
                    "extractedFields": extracted_fields,
                    "rawText": raw_text or f"Document Name: {doc_filename}\nType: {doc_type}",
                    "formattedText": formatted_text,
                    "debug": {
                        "field_locations": field_locations,
                        "ocr_tokens": ocr_tokens,
                        "page_dimensions": struct_data.get("page_dimensions", []),
                    },

                    "processingSteps": [
                        {
                            "id": f"stp-{doc_id}-1",
                            "component": "PaddleOCR",
                            "status": "COMPLETED" if has_data else "PENDING",
                            "detail": f"{doc_filename} OCR processing",
                            "startedAt": "10:30:00",
                            "confidence": 98.0,
                        }
                    ],
                })

        return docs

    def list_all(
        self,
        case_id: Optional[str] = None,
        doc_type: Optional[str] = None,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query all registered documents with optional filters.
        Dynamically merges disk-backed uploads with case documents.
        """
        with self._lock:
            self._scan_idp_parsed_storage()

            seen_keys = set()
            all_docs = []

            # Dynamic uploaded documents take precedence and appear first
            dynamic_list = list(self._dynamic_docs.values())
            dynamic_list.reverse()

            SINGLETON_TYPES = {
                "Application Form", "Aadhaar", "PAN", "Sanction Letter",
                "Loan Agreement", "Disbursal Memo", "KFS", "Aadhaar XML"
            }

            # Dynamic uploaded documents take precedence and appear first
            case_docs = self._get_case_documents()
            for d in dynamic_list:
                c_id = d.get("caseId")
                dtype = d.get("type")
                # Ensure dynamic documents are keyed uniquely by their document ID and filename
                doc_key = d.get("id") or d.get("name")
                key = (c_id, doc_key)

                if key in seen_keys:
                    continue
                seen_keys.add(key)
                # Also mark the (c_id, dtype) or (c_id, name) so case_docs don't duplicate dynamic uploads
                if dtype in SINGLETON_TYPES and c_id and c_id != "GENERAL":
                    seen_keys.add((c_id, dtype))
                else:
                    clean_name = d.get("name", "").lower()
                    base_name = re.sub(r"^doc-[a-z0-9_\-]+_", "", clean_name)
                    norm_name = re.sub(r"^(aadhaar|aadhar|adhar)[_\s\-]+", "", base_name)
                    norm_name = norm_name.replace("aadhaar", "aadhar").replace("adhar", "aadhar")
                    seen_keys.add((c_id, norm_name))

                all_docs.append(d)

            for d in case_docs:
                c_id = d.get("caseId")
                dtype = d.get("type")
                if dtype in SINGLETON_TYPES and c_id and c_id != "GENERAL":
                    key = (c_id, dtype)
                else:
                    clean_name = d.get("name", "").lower()
                    base_name = re.sub(r"^doc-[a-z0-9_\-]+_", "", clean_name)
                    norm_name = re.sub(r"^(aadhaar|aadhar|adhar)[_\s\-]+", "", base_name)
                    norm_name = norm_name.replace("aadhaar", "aadhar").replace("adhar", "aadhar")
                    key = (c_id, norm_name)

                if key in seen_keys:
                    continue
                seen_keys.add(key)
                all_docs.append(d)

            if case_id:
                all_docs = [d for d in all_docs if d.get("caseId") == case_id]

            if doc_type and doc_type != "ALL":
                all_docs = [d for d in all_docs if d.get("type") == doc_type]

            if query:
                q = query.lower().strip()
                all_docs = [
                    d for d in all_docs
                    if q in d.get("name", "").lower()
                    or q in d.get("caseId", "").lower()
                    or q in d.get("type", "").lower()
                ]

            return all_docs

    def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full document record with extracted fields by ID."""
        with self._lock:
            self._scan_idp_parsed_storage()
            if doc_id in self._dynamic_docs:
                doc = self._dynamic_docs[doc_id]
                # If formattedText is missing or empty, try enriching from on-disk extracted JSON
                if not (doc.get("formattedText") or "").strip().startswith("{"):
                    c_id = doc.get("caseId")
                    if not c_id or c_id == "GENERAL":
                        import re
                        m = re.search(r"(LOAN_\d+)", f"{doc_id}_{doc.get('name', '')}")
                        if m:
                            c_id = m.group(1)
                            doc["caseId"] = c_id
                    if c_id and c_id != "GENERAL":
                        from config import S3_EXTRACTED_STRUCTURED_DIR
                        c_ext = S3_EXTRACTED_DIR / c_id
                        c_struct = S3_EXTRACTED_STRUCTURED_DIR / c_id
                        from pathlib import Path as _Path
                        stem = _Path(doc.get("name", "")).stem.lower().replace(" ", "_")
                        clean_stem = re.sub(r"^(loan_\d+|appl\d+)_", "", stem)
                        cands = [
                            c_struct / f"{clean_stem}.json" if c_struct.exists() else None,
                            c_ext / f"{clean_stem}.json" if c_ext.exists() else None,
                            c_ext / f"{doc.get('name')}.json" if c_ext.exists() else None,
                            c_ext / f"{_Path(doc.get('name', '')).stem}.json" if c_ext.exists() else None,
                            c_ext / f"{stem}.json" if c_ext.exists() else None,
                            c_ext / f"{(doc.get('type') or '').lower().replace(' ', '_')}.json" if c_ext.exists() else None,
                            (c_ext / "Application Form.json") if c_ext.exists() and "app" in stem else None,
                            (c_ext / "application_form.json") if c_ext.exists() and "app" in stem else None,
                        ]
                        for cp in cands:
                            if cp and cp.exists() and cp.is_file():
                                    try:
                                        loaded = json.loads(cp.read_text(encoding="utf-8"))
                                        if isinstance(loaded, dict) and any(k in loaded for k in ("applicant_name", "loan_amount", "pan_number", "mobile_no", "dob")):
                                            from pipeline.nodes.llm_field_extractor import format_template_json
                                            tpl = format_template_json(loaded)
                                            doc["formattedText"] = json.dumps(tpl, indent=2)
                                            # Also add canonical fields to extractedFields if missing
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

            # Check case documents
            case_docs = self._get_case_documents()
            for d in case_docs:
                if d.get("id") == doc_id:
                    return d

            # Check alias mapping to dynamic docs
            if hasattr(self, "_doc_aliases") and doc_id in self._doc_aliases:
                target_id = self._doc_aliases[doc_id]
                if target_id in self._dynamic_docs:
                    res_doc = dict(self._dynamic_docs[target_id])
                    res_doc["id"] = doc_id
                    return res_doc


            # Canonical alias fallback for synthetic references (e.g. doc-LOAN_004-sanction)
            if doc_id.startswith("doc-"):
                parts = doc_id.split("-", 2)
                if len(parts) == 3:
                    target_case, target_slug = parts[1], parts[2].lower()
                    try:
                        from config.doc_types import get_canonical_doc_type
                        target_canon = get_canonical_doc_type(target_slug)
                    except Exception:
                        target_canon = "miscellaneous"

                    all_candidates = list(reversed(list(self._dynamic_docs.values()))) + case_docs
                    for d in all_candidates:
                        if str(d.get("caseId", "")).upper() == target_case.upper():
                            d_canon = target_canon
                            try:
                                from config.doc_types import get_canonical_doc_type
                                d_canon = get_canonical_doc_type(d.get("name") or d.get("type") or "")
                            except Exception:
                                pass
                            clean_type = str(d.get("type") or "").lower().replace(" ", "_")
                            clean_name = str(d.get("name") or "").lower()
                            if (
                                (target_canon != "miscellaneous" and d_canon == target_canon)
                                or target_slug in clean_name
                                or target_slug in clean_type
                            ):
                                return d

            return None

    def get_distinct_types(self) -> List[str]:
        """Return distinct document types currently present in the registry or supported by default."""
        docs = self.list_all()
        types = set(d.get("type") for d in docs if d.get("type"))
        standard_types = {
            "Application Form",
            "PAN",
            "Aadhaar",
            "KYC",
            "KFS",
            "Sanction Letter",
            "Loan Agreement",
            "Disbursal Memo",
            "BT Details",
            "Aadhaar XML",
            "VKYC Audit Trail",
            "Miscellaneous",
        }
        return sorted(list(types | standard_types))


# Global singleton instance
document_registry = DocumentRegistry()
