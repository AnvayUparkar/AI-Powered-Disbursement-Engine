"""Document registry services and modular components."""
from .dedup import filter_documents, get_all_distinct_types, merge_and_deduplicate
from .models import DocumentRecord, ExtractedFieldRecord, ProcessingStepRecord
from .normalizer import normalize_uploaded_record
from .resolver import guess_doc_type, normalize_doc_name, resolve_synthetic_alias

__all__ = [
    "DocumentRecord",
    "ExtractedFieldRecord",
    "ProcessingStepRecord",
    "normalize_uploaded_record",
    "guess_doc_type",
    "normalize_doc_name",
    "resolve_synthetic_alias",
    "merge_and_deduplicate",
    "filter_documents",
    "get_all_distinct_types",
]
