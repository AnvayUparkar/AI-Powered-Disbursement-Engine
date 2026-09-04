"""Node 2 (OCR / Extract) — Intelligent Document Processing & Field Extraction.

Natively integrates the IDP engine (Docling layout analysis, RapidOCR PP-OCRv6, VLM fallback)
and applies regex field normalization to construct structured data for downstream verification.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional,List,Tuple

from config import DMS_DIR, S3_EXTRACTED_DIR, S3_RAW_DIR
from idp.models.document import ParsedDocument
from idp.services.document_processor import DocumentProcessor
from pipeline.state import PipelineState
from pipeline.storage import read_json, update_status

logger = logging.getLogger("disbursement_pipeline.node2_extract")

# Initialize global DocumentProcessor instance for reuse
_processor: Optional[DocumentProcessor] = None


def get_processor() -> DocumentProcessor:
    global _processor
    if _processor is None:
        _processor = DocumentProcessor()
    return _processor


def _clean_numeric(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)", val_str)
    if not match:
        return None
    cleaned = match.group(1).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_tenure_months(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    val_str = str(val).strip().lower()
    match = re.search(r"(\d+)", val_str)
    if not match:
        return None
    num = int(match.group(1))
    if "year" in val_str or "yr" in val_str:
        return num * 12
    return num


def _map_doc_type_from_filename(filename: str) -> str:
    """Maps raw document filename to canonical extracted_data key."""
    fn = filename.lower()
    if "application" in fn:
        return "application_form"
    if "agreement" in fn:
        return "loan_agreement"
    if "kfs" in fn:
        return "kfs"
    if "sanction" in fn:
        return "sanction_letter"
    if "pan" in fn:
        return "kyc_pan"
    if "aadhaar" in fn or "kyc" in fn or "address" in fn:
        return "kyc_address_proof"
    if "memo" in fn or "disbursal" in fn:
        return "disbursal_memo"
    return Path(filename).stem


def _extract_fields_with_regex(doc_type: str, full_text: str, elements: list) -> Dict[str, Any]:
    """Applies domain regex rules to raw OCR text and layout elements."""
    fields: Dict[str, Any] = {}
    text = full_text or ""

    # Parse key-value element pairs if present
    for elem in elements:
        elem_text = elem.get("text", "") if isinstance(elem, dict) else getattr(elem, "text", "")
        if ":" in elem_text or "=" in elem_text:
            delimiter = ":" if ":" in elem_text else "="
            parts = elem_text.split(delimiter, 1)
            k = parts[0].strip().lower().replace(" ", "_")
            v = parts[1].strip()
            if k and v and k not in fields:
                fields[k] = v

    # Document-specific regex extraction
    if doc_type == "application_form":
        m_amt = re.search(r"(?:loan\s*amount|sanctioned\s*amount|approved\s*amount|principal\s*amount)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_amt:
            fields["loan_amount"] = _clean_numeric(m_amt.group(1))

        m_tenure = re.search(r"(?:tenure|loan\s*period|repayment\s*period)[:\s]*([\d]+)\s*(months?|yrs?\.?|years?)?", text, re.IGNORECASE)
        if m_tenure:
            num = int(m_tenure.group(1))
            unit = (m_tenure.group(2) or "").lower()
            fields["tenure_months"] = num * 12 if "yr" in unit or "year" in unit else num

        m_name = re.search(r"(?:applicant(?:'s)?\s*name|name\s*of\s*applicant|customer\s*name|borrower\s*name)[:\s]*([A-Za-z\s\.]+?)(?=\n|$|,|;)", text, re.IGNORECASE)
        if m_name:
            fields["applicant_name"] = m_name.group(1).strip()

        m_pan = re.search(r"([A-Z]{5}[0-9]{4}[A-Z]{1})", text)
        if m_pan:
            fields["pan_number"] = m_pan.group(1).strip().upper()

        m_addr = re.search(r"(?:address|residential\s*address|permanent\s*address)[:\s]*([^\n\r]+(?:\n[^\n\r]+)?)", text, re.IGNORECASE)
        if m_addr:
            fields["address_text"] = m_addr.group(1).strip()

        m_app_id = re.search(r"(?:application\s*(?:no\.?|id|number|code))[:\s]*([A-Za-z0-9\-_]+)", text, re.IGNORECASE)
        if m_app_id:
            fields["application_id"] = m_app_id.group(1).strip().upper()

    elif doc_type == "loan_agreement":
        m_amt = re.search(r"(?:loan\s*amount|principal\s*amount|sanctioned\s*amount)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_amt:
            fields["loan_amount"] = _clean_numeric(m_amt.group(1))

    elif doc_type == "kfs":
        m_amt = re.search(r"(?:loan\s*amount|net\s*disbursement|amount\s*financed)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_amt:
            amt = _clean_numeric(m_amt.group(1))
            fields["loan_amount"] = amt
            fields["funding_amount"] = amt

        m_bpi = re.search(r"(?:broken\s*period\s*interest|BPI)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_bpi:
            fields["broken_period_interest"] = _clean_numeric(m_bpi.group(1))

    elif doc_type == "sanction_letter":
        m_amt = re.search(r"(?:sanctioned\s*amount|loan\s*amount|approved\s*amount)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_amt:
            amt = _clean_numeric(m_amt.group(1))
            fields["loan_amount"] = amt
            fields["funding_amount"] = amt

        m_tenure = re.search(r"(?:tenure|loan\s*period)[:\s]*([\d]+)\s*(months?|yrs?\.?|years?)?", text, re.IGNORECASE)
        if m_tenure:
            num = int(m_tenure.group(1))
            unit = (m_tenure.group(2) or "").lower()
            fields["tenure_months"] = num * 12 if "yr" in unit or "year" in unit else num

        m_bpi = re.search(r"(?:broken\s*period\s*interest|BPI)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_bpi:
            fields["broken_period_interest"] = _clean_numeric(m_bpi.group(1))

    elif doc_type == "kyc_pan":
        m_pan = re.search(r"([A-Z]{5}[0-9]{4}[A-Z]{1})", text)
        if m_pan:
            fields["pan_number"] = m_pan.group(1).strip().upper()
        else:
            # Fallback: OCR character confusable normalization for PAN format
            pan_candidate = re.search(r"([A-Z0-9]{10})", text)
            if pan_candidate:
                raw_c = pan_candidate.group(1).upper()
                # Pos 0-4: Alpha, Pos 5-8: Digit, Pos 9: Alpha
                alpha_part1 = re.sub(r'0', 'O', re.sub(r'1', 'I', re.sub(r'5', 'S', raw_c[:5])))
                digit_part = re.sub(r'O', '0', re.sub(r'I', '1', re.sub(r'S', '5', re.sub(r'Z', '2', re.sub(r'B', '8', raw_c[5:9])))))
                alpha_part2 = re.sub(r'0', 'O', re.sub(r'1', 'I', raw_c[9]))
                cleaned_pan = f"{alpha_part1}{digit_part}{alpha_part2}"
                if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", cleaned_pan):
                    fields["pan_number"] = cleaned_pan

    elif doc_type == "kyc_address_proof":
        m_addr = re.search(r"(?:address|s/o|w/o|d/o|c/o|house\s*no\.?|village|district)[:\s]*(.+)", text, re.IGNORECASE | re.DOTALL)
        if m_addr:
            fields["address_text"] = m_addr.group(1).strip()
        else:
            fields["address_text"] = text.strip()

    elif doc_type == "disbursal_memo":
        m_app_id = re.search(r"(?:application\s*(?:no\.?|id|number)|appl\.?\s*no)[:\s]*([A-Za-z0-9\-_]+)", text, re.IGNORECASE)
        if m_app_id:
            fields["application_id"] = m_app_id.group(1).strip().upper()

        m_cls_id = re.search(r"(?:closure\s*(?:id|no\.?|account\s*no\.?)|loan\s*closure\s*no)[:\s]*([A-Za-z0-9\-_]+)", text, re.IGNORECASE)
        if m_cls_id:
            fields["closure_id"] = m_cls_id.group(1).strip().upper()

        m_amt = re.search(r"(?:disbursal\s*amount|disbursed\s*amount|net\s*disbursal|amount)[:\s]*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_amt:
            fields["disbursal_amount"] = _clean_numeric(m_amt.group(1))

    return fields


def _process_file_with_idp(file_path: Path, doc_id: str) -> Optional[Dict[str, Any]]:
    """Runs IDP DocumentProcessor asynchronously in synchronous loop."""
    processor = get_processor()
    try:
        # Preprocess directly via processor
        prep = processor.preprocessor.preprocess(str(file_path), doc_id=doc_id)
        if prep.file_category == "xml":
            parsed = processor.serializer.parse_xml_fast_path(str(file_path), doc_id=doc_id)
        else:
            # Execute async pipeline
            coro = processor.process_document(
                document_id=doc_id,
                s3_key=str(file_path)
            )
            try:
                res = asyncio.run(coro)
            except RuntimeError:
                # If an event loop is already running in current thread
                loop = asyncio.get_event_loop()
                res = loop.run_until_complete(coro)

            # Retrieve parsed document model
            parsed = asyncio.run(processor.get_parsed_document(doc_id))

        if parsed:
            doc_type = _map_doc_type_from_filename(file_path.name)
            from pipeline.nodes.llm_field_extractor import llm_extract_fields
            extracted_fields = llm_extract_fields(
                doc_type=doc_type,
                raw_text=parsed.text,
                doc_id=doc_id,
            )

            # Convert parsed elements to dictionary list preserving bounding boxes, confidence, and source
            raw_element_dicts = []
            for elem in parsed.elements:
                elem_dict = elem.model_dump()
                raw_element_dicts.append(elem_dict)

            # Run Layout-Aware Spatial Key-Value & Checkbox Extraction
            from pipeline.nodes.key_value_extractor import KeyValueExtractor
            kv_extractor = KeyValueExtractor()
            spatial_results = kv_extractor.extract(raw_element_dicts, doc_type=doc_type)

            tables_data = []
            for tbl in (parsed.tables or []):
                tables_data.append({
                    "id": tbl.id,
                    "page_number": tbl.page_number,
                    "table_type": getattr(tbl, "table_type", "STRUCTURED_TABLE"),
                    "headers": tbl.headers,
                    "rows": tbl.rows_raw
                })

            components = {
                "document_type": doc_type,
                "key_values": spatial_results.get("key_values", {}),
                "checkboxes": spatial_results.get("checkboxes", {}),
                "tables": tables_data,
                "paragraphs": spatial_results.get("paragraphs", [])
            }

            return {
                **extracted_fields,
                "_raw_text": parsed.text,
                "_pages": len(parsed.pages),
                "_elements_count": len(parsed.elements),
                "_components": components,
            }
    except Exception as e:
        logger.warning("Native IDP processing encountered an issue for %s: %s", file_path, e)
    return None


def node2_extract(state: PipelineState) -> PipelineState:
    """Node 2 (OCR/Extract) — Native IDP Processing & Regex Field Normalization.

    1. Processes document PDFs / images in raw_doc_paths via native IDP DocumentProcessor.
    2. Runs extracted text through regex rulebook to populate structured fields for Node 3.
    3. Ingests sidecar metadata JSONs (face embeddings, DMS status, OTP audit).
    4. Falls back gracefully to pre-extracted data if document yields no fields.
    """
    loan_id = state["loan_id"]
    errors = list(state.get("errors", []))
    history = list(state.get("node_history", []))
    history.append("extract")

    logger.info("Executing Node 2 (Native IDP & Field Extraction) for loan: %s", loan_id)

    raw_doc_paths = state.get("raw_doc_paths", {})
    extracted_data: Dict[str, Any] = dict(state.get("extracted_data", {}))
    face_embeddings: Dict[str, Any] = dict(state.get("face_embeddings", {}))
    dms_status: Dict[str, Any] = dict(state.get("dms_status", {}))
    otp_audit: Dict[str, Any] = dict(state.get("otp_audit", {}))

    # 1. Inspect raw documents from Node 1
    raw_dir = S3_RAW_DIR / loan_id
    if not raw_doc_paths and raw_dir.exists():
        for f in raw_dir.iterdir():
            raw_doc_paths[f.name] = str(f)

    # 2. Process sidecar JSONs & binary documents
    binary_doc_tasks: List[Tuple[str, Path, str, str]] = []  # (fname, fpath, doc_key, doc_id)

    for fname, fpath_str in raw_doc_paths.items():
        fpath = Path(fpath_str)
        if not fpath.exists():
            continue

        name_lower = fname.lower()

        # Handle sidecars
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
            # LOS loan metadata, skip
            continue
        elif name_lower.endswith(".json") and "metadata" in name_lower:
            continue
        elif name_lower.endswith(".json"):
            try:
                data = read_json(fpath)
                doc_key = _map_doc_type_from_filename(fpath.stem)
                extracted_data[doc_key] = data
            except Exception as e:
                errors.append(f"Failed reading JSON sidecar {fname}: {e}")
            continue

        # Collect PDFs and Images for parallel IDP processing
        if fpath.suffix.lower() in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".xml"]:
            doc_key = _map_doc_type_from_filename(fname)
            doc_id = f"{loan_id}_{doc_key}"
            binary_doc_tasks.append((fname, fpath, doc_key, doc_id))

    # Parallelize document ingestion via ThreadPoolExecutor
    if binary_doc_tasks:
        from concurrent.futures import ThreadPoolExecutor
        from idp.core.config import settings

        max_doc_workers = getattr(settings, "MAX_DOC_WORKERS", 4)
        worker_count = min(len(binary_doc_tasks), max_doc_workers)

        def _worker_task(task_tuple: Tuple[str, Path, str, str]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
            fname, fpath, doc_key, doc_id = task_tuple
            res = _process_file_with_idp(fpath, doc_id=doc_id)
            return fname, doc_key, res

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="idp_doc_worker") as executor:
            futures = [executor.submit(_worker_task, task) for task in binary_doc_tasks]
            for future in futures:
                fname, doc_key, idp_result = future.result()
                if idp_result:
                    extracted_data[doc_key] = idp_result
                else:
                    logger.info("IDP yielded no output for %s; checking fallback stores", fname)

    # 3. Fallback / Merge with pre-extracted mock data if available
    extracted_dir = S3_EXTRACTED_DIR / loan_id
    if extracted_dir.exists():
        for json_file in extracted_dir.glob("*.json"):
            if json_file.stem.endswith("_structured"):
                continue
            try:
                data = read_json(json_file)
                key = json_file.stem
                if key == "face_embeddings" and not face_embeddings:
                    face_embeddings = data
                elif key == "dms_status" and not dms_status:
                    dms_status = data
                else:
                    doc_key = _map_doc_type_from_filename(key)
                    if doc_key not in extracted_data:
                        extracted_data[doc_key] = data
                    else:
                        # Fill in missing checkpoint fields from fallback store if OCR missed them
                        for k, v in data.items():
                            if k not in extracted_data[doc_key] or extracted_data[doc_key][k] is None:
                                extracted_data[doc_key][k] = v
            except (json.JSONDecodeError, OSError) as e:
                msg = f"Failed to load extracted fallback file {json_file}: {e}"
                logger.warning(msg)

    # Load OTP audit if present in DMS or S3 raw
    if not otp_audit:
        otp_audit_file = S3_RAW_DIR / loan_id / "loan_agreement_otp_audit.json"
        if not otp_audit_file.exists():
            otp_audit_file = DMS_DIR / loan_id / "loan_agreement_otp_audit.json"

        if otp_audit_file.exists():
            try:
                otp_audit = read_json(otp_audit_file)
            except (json.JSONDecodeError, OSError) as e:
                msg = f"Failed to load OTP audit file {otp_audit_file}: {e}"
                logger.error(msg)
                errors.append(msg)

    # Persist extracted data to S3_EXTRACTED_DIR for downstream serializers and consumers
    extracted_out_dir = S3_EXTRACTED_DIR / loan_id
    extracted_out_dir.mkdir(parents=True, exist_ok=True)
    for doc_k, doc_v in extracted_data.items():
        if isinstance(doc_v, dict):
            try:
                from pipeline.storage import write_json
                # Write the standard file expected by downstream nodes (preserving backward compatibility)
                write_json(extracted_out_dir / f"{doc_k}.json", doc_v)

                # Save structured components (key-values, tables, paragraphs) in a dedicated new file
                if "_components" in doc_v and isinstance(doc_v["_components"], dict):
                    write_json(extracted_out_dir / f"{doc_k}_structured.json", doc_v["_components"])
            except Exception as save_err:
                logger.debug("Failed caching extracted file %s for %s: %s", doc_k, loan_id, save_err)

    update_status(loan_id, current_node="extract", errors=errors, node_history=history)

    return {
        **state,
        "extracted_data": extracted_data,
        "face_embeddings": face_embeddings,
        "dms_status": dms_status,
        "otp_audit": otp_audit,
        "errors": errors,
        "node_history": history,
    }
