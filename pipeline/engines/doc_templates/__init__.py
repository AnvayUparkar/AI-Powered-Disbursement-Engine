"""Per-document-type templates.

One YAML file per canonical document type (``<doc_type>.yaml`` in this folder) defines both the
LLM extraction prompt and the JSON shown on the UI for that type. Document types without a
template use the generic every-key-value-pair prompt (``pipeline/engines/prompts/misc.py``) and
show whichever key-value pairs were found.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

from config.doc_types import DOC_TYPE_DISPLAY_NAMES, get_canonical_doc_type

TEMPLATE_DIR: Path = Path(__file__).resolve().parent

# First key of every UI JSON. Filled from the detected document type, never from the LLM.
DOCUMENT_TYPE_KEY = "document_type"

_SAFE_NAME_RE = re.compile(r"^[a-z0-9_]+$")

_TYPE_HINTS: Dict[str, str] = {
    "string": "text exactly as printed",
    "number": "digits only; strip currency symbols, commas and units",
    "date": "exactly as printed",
    "percent": "number only, without the % sign",
    "boolean": "true or false",
}

_BASE_RULES: tuple[str, ...] = (
    "Return exactly the keys in the JSON schema below, in that order. Do NOT add extra keys.",
    "If a field is not present in the OCR text, set its value to null.",
    "Do NOT guess, infer, or hallucinate values.",
)

_FINAL_RULE = "Return ONLY a valid JSON object - no markdown fences, no explanations."


ScalarType = Literal["string", "number", "date", "percent", "boolean"]


def _duplicates(values: List[str]) -> List[str]:
    return sorted({v for v in values if values.count(v) > 1})


class SubField(BaseModel):
    key: str
    label: str
    type: ScalarType = "string"
    description: str


class TemplateField(BaseModel):
    key: str
    label: str
    type: Literal["string", "number", "date", "percent", "boolean", "object"] = "string"
    description: str
    show_in_ui: bool = True
    # Only for type: object. One level deep: a sub-field cannot itself be an object.
    fields: List[SubField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_sub_fields(self) -> "TemplateField":
        if self.type != "object":
            if self.fields:
                raise ValueError(f"field {self.key!r} has sub-fields but type is {self.type!r}; use type: object")
            return self
        if not self.fields:
            raise ValueError(f"object field {self.key!r} needs at least one sub-field")
        dup_keys = _duplicates([s.key for s in self.fields])
        if dup_keys:
            raise ValueError(f"object field {self.key!r} has duplicate sub-keys: {dup_keys}")
        dup_labels = _duplicates([s.label for s in self.fields])
        if dup_labels:
            raise ValueError(f"object field {self.key!r} has duplicate sub-labels: {dup_labels}")
        return self


class DocTemplate(BaseModel):
    doc_type: str
    display_name: str
    version: int = 1
    intro: str
    rules: List[str] = Field(default_factory=list)
    fields: List[TemplateField]

    @model_validator(mode="after")
    def _check_fields(self) -> "DocTemplate":
        if not self.fields:
            raise ValueError(f"template {self.doc_type!r} defines no fields")
        keys = [f.key for f in self.fields]
        dup_keys = _duplicates(keys)
        if dup_keys:
            raise ValueError(f"template {self.doc_type!r} has duplicate keys: {dup_keys}")
        labels = [f.label for f in self.fields if f.show_in_ui]
        dup_labels = _duplicates(labels)
        if dup_labels:
            raise ValueError(f"template {self.doc_type!r} has duplicate UI labels: {dup_labels}")
        if DOCUMENT_TYPE_KEY in keys or DOCUMENT_TYPE_KEY in labels:
            raise ValueError(f"template {self.doc_type!r}: {DOCUMENT_TYPE_KEY!r} is reserved and added automatically")
        return self

    @property
    def ui_fields(self) -> List[TemplateField]:
        return [f for f in self.fields if f.show_in_ui]


def load_template_file(path: Path) -> DocTemplate:
    """Parse and validate one template file. Raises ValueError / pydantic.ValidationError when invalid."""
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: template must be a YAML mapping")
    template = DocTemplate.model_validate(data)
    if template.doc_type != path.stem:
        raise ValueError(f"{path.name}: doc_type {template.doc_type!r} must match the file name")
    return template


@lru_cache(maxsize=None)
def _load_by_canonical_type(canonical: str) -> Optional[DocTemplate]:
    if not _SAFE_NAME_RE.match(canonical):
        return None
    path = TEMPLATE_DIR / f"{canonical}.yaml"
    if not path.is_file():
        return None
    return load_template_file(path)


def get_doc_template(doc_type: Optional[str]) -> Optional[DocTemplate]:
    """Template for *doc_type* (any alias or filename accepted), or None when the type has none."""
    if not doc_type:
        return None
    return _load_by_canonical_type(get_canonical_doc_type(doc_type))


_OBJECT_RULE = (
    "For a key whose schema value is an object, return an object with exactly those sub-keys "
    "(null for a sub-key that is not present). Set the whole object to null only if that item "
    "does not appear in the document at all."
)


def _hint(description: str, field_type: str) -> str:
    return f"<{description} ({_TYPE_HINTS[field_type]}), or null>"


def build_system_prompt(template: DocTemplate) -> str:
    """Build the LLM field-extraction system prompt from a template."""
    has_objects = any(f.type == "object" for f in template.fields)
    rules = [*_BASE_RULES, *([_OBJECT_RULE] if has_objects else []), *template.rules, _FINAL_RULE]
    schema: Dict[str, Any] = {}
    for field in template.fields:
        if field.type == "object":
            schema[field.key] = {sub.key: _hint(sub.description, sub.type) for sub in field.fields}
        else:
            schema[field.key] = _hint(field.description, field.type)
    lines: List[str] = [
        template.intro.strip(),
        "Your task: extract the fields below from the raw OCR text and return them as a single valid JSON object.",
        "",
        "CRITICAL REQUIREMENTS:",
        *(f"{i}. {rule}" for i, rule in enumerate(rules, start=1)),
        "",
        "JSON SCHEMA (return exactly these keys):",
        json.dumps(schema, indent=2, ensure_ascii=False),
    ]
    return "\n".join(lines)


def document_type_name(doc_type: Optional[str]) -> Optional[str]:
    """Human-readable name for a detected document type (any alias, display name or filename)."""
    if not doc_type:
        return None
    template = get_doc_template(doc_type)
    if template is not None:
        return template.display_name
    return DOC_TYPE_DISPLAY_NAMES.get(get_canonical_doc_type(doc_type)) or doc_type


def build_ui_json(doc_type: Optional[str], fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The JSON shown on the UI for a document: ``document_type`` first, then template labels in
    template order when the type has a template, otherwise every key-value pair that was found
    (null values dropped)."""
    header: Dict[str, Any] = {}
    type_name = document_type_name(doc_type)
    if type_name:
        header[DOCUMENT_TYPE_KEY] = type_name

    template = get_doc_template(doc_type)
    if template is None:
        from pipeline.engines.llm_field_extractor import format_template_json

        found = {
            k: v for k, v in format_template_json(fields).items()
            if v is not None and k != DOCUMENT_TYPE_KEY
        }
        return {**header, **found}
    source = fields or {}
    return {**header, **{f.label: _ui_value(f, source.get(f.key)) for f in template.ui_fields}}


def _ui_value(field: TemplateField, value: Any) -> Any:
    """Object fields become {sub label: value}; a missing object shows every sub-label as null.
    A non-object value for an object field (e.g. a document extracted before the field became an
    object) is shown as-is rather than dropped."""
    if field.type != "object":
        return value
    if value is None:
        value = {}
    if not isinstance(value, dict):
        return value
    return {sub.label: value.get(sub.key) for sub in field.fields}


def format_ui_json_text(doc_type: Optional[str], fields: Optional[Dict[str, Any]]) -> str:
    return json.dumps(build_ui_json(doc_type, fields), indent=2, ensure_ascii=False)


def ui_label_for(doc_type: Optional[str], key: str) -> Optional[str]:
    """UI label for an extracted key ("reference_1", or "reference_1.name" for a sub-field), or
    None when the type has no template or the key is not in it."""
    template = get_doc_template(doc_type)
    if template is None:
        return None
    parent_key, _, sub_key = key.partition(".")
    for field in template.fields:
        if field.key != parent_key:
            continue
        if not sub_key:
            return field.label
        for sub in field.fields:
            if sub.key == sub_key:
                return f"{field.label} - {sub.label}"
        return None
    return None
