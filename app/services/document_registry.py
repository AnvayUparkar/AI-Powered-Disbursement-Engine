"""Unified Document Registry facade coordinating in-memory and disk-backed documents."""
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
            )

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
            dynamic_list = list(self._dynamic_docs.values())
            case_docs = self._get_case_documents()

            all_docs = merge_and_deduplicate(dynamic_list, case_docs)
            return filter_documents(all_docs, case_id=case_id, doc_type=doc_type, query=query)

    def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full document record with extracted fields by ID."""
        with self._lock:
            self._scan_idp_parsed_storage()

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

    def get_distinct_types(self) -> List[str]:
        """Return distinct document types currently present in the registry or supported by default."""
        with self._lock:
            dynamic_types = {d.get("type") for d in self._dynamic_docs.values() if d.get("type")}
            return get_all_distinct_types(dynamic_types)


# Global singleton instance
document_registry = DocumentRegistry()
