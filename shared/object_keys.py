"""Shared object key builders and validators for the inter-service S3 contract."""
from pathlib import Path
from typing import Union


def raw_object_key(case_id: str, filename: str) -> str:
    """Returns relative object key: raw-documents/{case_id}/{filename}"""
    return f"raw-documents/{case_id}/{filename}"


def parsed_object_key(document_id: str) -> str:
    """Returns relative object key: parsed-documents/{document_id}.json"""
    return f"parsed-documents/{document_id}.json"


def validate_key(key: str) -> None:
    """Raises ValueError if key looks like a filesystem path."""
    if key.startswith("/") or key.startswith("\\"):
        raise ValueError(f"s3_key must be a relative object key, got absolute path: {key!r}")
    p = Path(key)
    if p.is_absolute():
        raise ValueError(f"s3_key must be a relative object key, got absolute path: {key!r}")
    if ".." in p.parts:
        raise ValueError(f"s3_key must not contain '..': {key!r}")
    # Windows drive letters: e.g. "C:\..." already caught by is_absolute(), but catch "C:" prefix too
    if len(key) >= 2 and key[1] == ":":
        raise ValueError(f"s3_key must be a relative object key, got drive-letter path: {key!r}")
