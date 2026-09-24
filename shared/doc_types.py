"""Re-export config.doc_types so IDP can import doc type helpers without touching pipeline config."""
from config.doc_types import (  # noqa: F401
    DOC_TYPE_ALIASES,
    DOC_TYPE_DISPLAY_NAMES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    TABULAR_OR_MISC_DOCS,
    TEMPLATE_FIELDS,
    format_template_json,
    get_canonical_doc_type,
    get_display_name,
    is_tabular_or_misc_doc,
)
