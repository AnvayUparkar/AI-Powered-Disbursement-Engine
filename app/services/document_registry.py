"""Unified Document Registry facade coordinating in-memory and disk-backed documents."""
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry.case_scanner import (
    enrich_document_record,
    invalidate_case_cache,
    scan_case_documents,
)
from .registry.dedup import (
    filter_documents,
    get_all_distinct_types,
    merge_and_deduplicate,
)
from .registry.idp_scanner import scan_idp_parsed_storage
from .registry.normalizer import normalize_uploaded_record
from .registry.resolver import (
    guess_doc_type,
    resolve_synthetic_alias,
)

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
        self._parsed_storage_scanned = False
        self._initialized = False

    def _guess_doc_type(self, filename: str) -> str:
        """Infer document type from filename."""
        return guess_doc_type(filename)

    def register_uploaded_document(
        self,
        doc_id: str,
        filename: str,
        doc_type: Optional[str] = None,
        case_id: Optional[str] = None,
        file_size_bytes: int = 0,
        parsed_result: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None,
        uploaded_at: Optional[str] = None,
        uploaded_timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Register a newly uploaded and processed document in the registry."""
        with self._lock:
            detected_type = doc_type or self._guess_doc_type(filename)
            assoc_case = case_id or "GENERAL"

            record = normalize_uploaded_record(
                doc_id=doc_id,
                filename=filename,
                detected_type=detected_type,
                assoc_case=assoc_case,
                file_size_bytes=file_size_bytes,
                parsed_result=parsed_result,
                status=status,
                uploaded_at=uploaded_at,
                uploaded_timestamp=uploaded_timestamp,
            )

            # If a dynamic record already exists for this case and filename, supersede only if newer or equal
            if assoc_case and assoc_case != "GENERAL":
                for existing_id, existing_rec in list(self._dynamic_docs.items()):
                    if (
                        existing_id != doc_id
                        and existing_rec.get("caseId") == assoc_case
                        and existing_rec.get("name") == filename
                    ):
                        existing_ts = existing_rec.get("uploadedTimestamp", 0.0) or 0.0
                        new_ts = record.get("uploadedTimestamp", 0.0) or 0.0
                        if existing_ts > new_ts:
                            return existing_rec
                        del self._dynamic_docs[existing_id]

            self._dynamic_docs[doc_id] = record

            # Map filename-based ID alias without polluting _dynamic_docs with duplicate records
            if case_id and filename:
                alt_id = f"doc-{case_id}-{Path(filename).stem.lower().replace(' ', '_')}"
                if alt_id != doc_id:
                    self._doc_aliases[alt_id] = doc_id

            if case_id and case_id != "GENERAL":
                invalidate_case_cache()

            logger.info("Registered document %s (%s) for case %s", doc_id, filename, assoc_case)
            return record

    def update_extracted_result(self, doc_id: str, result: dict) -> None:
        """Merge IDP extracted result into an existing registry record."""
        with self._lock:
            if doc_id not in self._dynamic_docs:
                filename = result.get("filename") or (result.get("source") or {}).get("filename") or f"{doc_id}.pdf"
                case_id = result.get("case_id") or result.get("caseId")
                self.register_uploaded_document(doc_id=doc_id, filename=filename, case_id=case_id)

            rec = self._dynamic_docs[doc_id]
            raw_txt = result.get("raw_text", "") or rec.get("rawText", "")
            fmt_txt = result.get("formatted_text", "") or rec.get("formattedText", "")
            ext_fields_raw = result.get("extracted_fields") or {}
            field_locs = result.get("field_locations") or {}
            ocr_tokens = result.get("ocr_tokens") or []

            from .registry.normalizer import parse_extracted_fields
            llm_meta = ext_fields_raw if isinstance(ext_fields_raw, dict) else {}
            parsed_shim = {
                "custom_metadata": {
                    "field_locations": field_locs,
                    "ocr_tokens": ocr_tokens,
                },
                "elements": result.get("elements", []),
                "tables": result.get("tables", []),
            }
            extracted_fields_list = parse_extracted_fields(doc_id, parsed_shim, llm_meta)

            raw_pages = result.get("pages")
            if isinstance(raw_pages, list):
                pages_count = len(raw_pages)
            elif raw_pages is not None:
                pages_count = raw_pages
            else:
                pages_count = rec.get("pages", 1)

            try:
                pages_val = int(pages_count)
            except (ValueError, TypeError):
                pages_val = 1

            rec.update({
                "status": "processed",
                "ocrStatus": "COMPLETED",
                "extractionStatus": "COMPLETED",
                "confidence": 97.5,
                "pages": max(1, pages_val),
                "rawText": raw_txt,
                "formattedText": fmt_txt,
                "extractedFields": extracted_fields_list or rec.get("extractedFields", []),
                "debug": {
                    "field_locations": field_locs,
                    "ocr_tokens": ocr_tokens,
                    "page_dimensions": result.get("page_dimensions", []),
                },
            })
            logger.info("Updated extracted result for doc %s", doc_id)

    def _scan_idp_parsed_storage(self) -> None:
        """Scan disk storage for any existing parsed documents in IDP store."""
        if self._parsed_storage_scanned:
            return
        self._parsed_storage_scanned = True
        scan_idp_parsed_storage(
            known_doc_ids=set(self._dynamic_docs.keys()),
            register_func=self.register_uploaded_document,
        )

    def _get_case_documents(self) -> List[Dict[str, Any]]:
        """Index actual documents stored for all registered loan cases without phantom files."""
        return scan_case_documents(dynamic_doc_ids=set(self._dynamic_docs.keys()))

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

            # Dynamic reconciliation: Sync any pending/processing upload if parsed JSON is ready on disk
            for d_id, d_rec in list(self._dynamic_docs.items()):
                if d_rec.get("ocrStatus") == "PROCESSING" or d_rec.get("extractionStatus") == "PROCESSING":
                    self._sync_parsed_doc_from_disk(d_id)

            dynamic_list = list(self._dynamic_docs.values())
            case_docs = self._get_case_documents()

            all_docs = merge_and_deduplicate(dynamic_list, case_docs)
            return filter_documents(all_docs, case_id=case_id, doc_type=doc_type, query=query)

    def _sync_parsed_doc_from_disk(self, doc_id: str) -> None:
        """Check if a freshly parsed JSON for doc_id exists on disk in mock S3 storage and update registry."""
        try:
            from idp.core.config import settings as idp_settings
            base_dir = Path(idp_settings.TEMP_DIR) / "s3_mock" / idp_settings.S3_BUCKET
            parsed_path = base_dir / idp_settings.PARSED_DOCUMENT_PREFIX / f"{doc_id}.json"
            if parsed_path.exists() and parsed_path.is_file():
                with open(parsed_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                filename = data.get("source", {}).get("filename")
                case_val = self._dynamic_docs.get(doc_id, {}).get("caseId")
                self.register_uploaded_document(
                    doc_id=doc_id,
                    filename=filename or f"{doc_id}.pdf",
                    case_id=case_val,
                    parsed_result=data,
                    file_size_bytes=data.get("processing", {}).get("file_size_bytes", 0),
                )
        except Exception as e:
            logger.debug("Disk sync note for %s: %s", doc_id, e)

    def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full document record with extracted fields by ID."""
        with self._lock:
            self._scan_idp_parsed_storage()
            self._sync_parsed_doc_from_disk(doc_id)

            if doc_id in self._dynamic_docs:
                doc = self._dynamic_docs[doc_id]
                return enrich_document_record(doc)

            # Check case documents
            case_docs = self._get_case_documents()
            for d in case_docs:
                if d.get("id") == doc_id:
                    return d

            # Check alias mapping to dynamic docs
            if doc_id in self._doc_aliases:
                target_id = self._doc_aliases[doc_id]
                if target_id in self._dynamic_docs:
                    res_doc = dict(self._dynamic_docs[target_id])
                    res_doc["id"] = doc_id
                    return res_doc

            # Canonical alias fallback for synthetic references (e.g. doc-LOAN_004-sanction)
            all_candidates = list(reversed(list(self._dynamic_docs.values()))) + case_docs
            return resolve_synthetic_alias(doc_id, all_candidates)

    def delete_case(self, case_id: str) -> int:
        """Purge registry entries for a deleted case: in-memory records/aliases, plus their
        mock-S3 idp_temp files (raw upload + parsed-document JSON).

        pipeline.storage.delete_loan_data() separately handles the per-case-id directory
        tiers (S3_RAW_DIR/{case_id}, S3_EXTRACTED_DIR/{case_id}, etc.) and a best-effort
        loan-id-substring glob over idp_temp. That glob misses uploads whose doc_id doesn't
        embed the case_id (e.g. a random "DOC-abc123" doc_id) -- this method closes that gap
        by using the registry's own doc_id/filename association (recorded at upload time) to
        delete those exact idp_temp files precisely.

        Returns the number of dynamic document records removed.
        """
        with self._lock:
            self._scan_idp_parsed_storage()

            doc_ids_to_remove = [
                doc_id for doc_id, rec in self._dynamic_docs.items()
                if rec.get("caseId") == case_id
            ]

            for doc_id in doc_ids_to_remove:
                rec = self._dynamic_docs[doc_id]
                self._delete_idp_temp_files(doc_id, rec.get("name"))
                del self._dynamic_docs[doc_id]

            aliases_to_remove = [
                alias for alias, target in self._doc_aliases.items()
                if target in doc_ids_to_remove
            ]
            for alias in aliases_to_remove:
                del self._doc_aliases[alias]

            invalidate_case_cache()
            logger.info("Purged %d document record(s) for deleted case %s", len(doc_ids_to_remove), case_id)
            return len(doc_ids_to_remove)

    @staticmethod
    def _delete_idp_temp_files(doc_id: str, filename: Optional[str]) -> None:
        """Remove a document's mock-S3 raw upload and parsed-document JSON from idp_temp."""
        from idp.core.config import settings as idp_settings

        base = Path(idp_settings.TEMP_DIR) / "s3_mock" / idp_settings.S3_BUCKET

        parsed_path = base / idp_settings.PARSED_DOCUMENT_PREFIX / f"{doc_id}.json"
        if parsed_path.exists():
            try:
                parsed_path.unlink()
            except OSError as e:
                logger.warning("Failed deleting parsed doc %s: %s", parsed_path, e)

        if filename:
            raw_path = base / idp_settings.RAW_DOCUMENT_PREFIX / f"{doc_id}_{filename}"
            if raw_path.exists():
                try:
                    raw_path.unlink()
                except OSError as e:
                    logger.warning("Failed deleting raw upload %s: %s", raw_path, e)

    def get_distinct_types(self) -> List[str]:
        """Return distinct document types currently present in the registry or supported by default."""
        with self._lock:
            dynamic_types = {d.get("type") for d in self._dynamic_docs.values() if d.get("type")}
            return get_all_distinct_types(dynamic_types)


# Global singleton instance
document_registry = DocumentRegistry()
