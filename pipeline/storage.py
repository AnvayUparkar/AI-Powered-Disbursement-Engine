"""Pipeline storage abstraction — atomic operations, status tracking, and tier accessors."""
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import (
    IST,
    LOS_LOANS_DIR,
    S3_EXTRACTED_DIR,
    S3_EXTRACTED_STRUCTURED_DIR,
    S3_LOS_DIR,
    S3_RAW_DIR,
    S3_RESULT_DIR,
)

logger = logging.getLogger("disbursement_pipeline.storage")


def read_json(path: Path | str) -> dict:
    """Reads JSON from file path. Raises FileNotFoundError if missing."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path | str, data: Any) -> None:
    """Atomically writes JSON to file using a temporary file in the target directory."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False, encoding="utf-8") as tmp_file:
        tmp_name = tmp_file.name
        json.dump(data, tmp_file, indent=2, ensure_ascii=False)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())

    os.replace(tmp_name, file_path)


def copy_file(src: Path | str, dst: Path | str) -> None:
    """Copies a file creating parent destination directory if needed."""
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)


def copy_dir(src: Path | str, dst: Path | str) -> None:
    """Recursively copies files and subdirectories from src to dst."""
    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.exists():
        return
    dst_path.mkdir(parents=True, exist_ok=True)
    for item in src_path.iterdir():
        if item.is_file():
            shutil.copy2(item, dst_path / item.name)
        elif item.is_dir():
            copy_dir(item, dst_path / item.name)


def update_status(
    loan_id: str,
    current_node: str,
    errors: list[str] | None = None,
    node_history: list[str] | None = None,
) -> dict:
    """Updates status.json for a loan in S3_RESULT_DIR."""
    status_path = S3_RESULT_DIR / loan_id / "status.json"
    status_data = {}
    if status_path.exists():
        try:
            status_data = read_json(status_path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed reading status.json for %s: %s. Resetting status.", loan_id, e)
            status_data = {}

    history = status_data.get("node_history", [])
    if current_node and (not history or history[-1] != current_node):
        history.append(current_node)

    errs = status_data.get("errors", [])
    if errors:
        for e in errors:
            if e not in errs:
                errs.append(e)

    now_iso = datetime.now(IST).isoformat()
    # Reset timestamps on a fresh pipeline execution
    if current_node == "fetch_los":
        started_at = now_iso
        completed_at = None
    else:
        started_at = status_data.get("started_at") or now_iso
        completed_at = status_data.get("completed_at")

    if current_node == "done":
        completed_at = now_iso

    status_data = {
        "loan_id": loan_id,
        "current_node": current_node,
        "node_history": node_history if node_history is not None else history,
        "errors": errs,
        "started_at": started_at,
        "updated_at": now_iso,
    }
    if completed_at:
        status_data["completed_at"] = completed_at

    write_json(status_path, status_data)
    return status_data



def list_loan_ids() -> list[str]:
    """Lists all available loan IDs in LOS loans directory, loans.db, or S3 LOS directory."""
    loan_ids = set()
    if LOS_LOANS_DIR.exists():
        for f in LOS_LOANS_DIR.glob("*.json"):
            loan_ids.add(f.stem)
        db_path = LOS_LOANS_DIR / "loans.db"
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("SELECT loan_id FROM loan_applications")
                for (lid,) in cur.fetchall():
                    if lid:
                        loan_ids.add(lid)
                conn.close()
            except Exception:
                pass
    if S3_LOS_DIR.exists():
        for f in S3_LOS_DIR.glob("*.json"):
            loan_ids.add(f.stem)
    return sorted(loan_ids)


# ── Tier Accessors ────────────────────────────────────────────────────────────

def save_s3_los(loan_id: str, data: dict[str, Any]) -> Path:
    """Saves LOS loan data into s3_los/{loan_id}.json."""
    out_path = S3_LOS_DIR / f"{loan_id}.json"
    write_json(out_path, data)
    return out_path


def get_s3_los(loan_id: str) -> dict[str, Any]:
    """Retrieves LOS data from s3_los or fallback LOS_LOANS_DIR."""
    s3_path = S3_LOS_DIR / f"{loan_id}.json"
    if s3_path.exists():
        return read_json(s3_path)
    los_path = LOS_LOANS_DIR / f"{loan_id}.json"
    if los_path.exists():
        return read_json(los_path)
    return {}


def save_s3_extracted(loan_id: str, doc_key: str, data: dict[str, Any]) -> Path:
    """Saves raw OCR/layout output to s3_extracted/{loan_id}/{doc_key}.json."""
    out_dir = S3_EXTRACTED_DIR / loan_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_key}.json"
    write_json(out_path, data)
    return out_path


def get_s3_extracted(loan_id: str, doc_key: str) -> dict[str, Any]:
    """Retrieves raw extracted document data."""
    path = S3_EXTRACTED_DIR / loan_id / f"{doc_key}.json"
    if path.exists():
        return read_json(path)
    return {}


def save_s3_extracted_structured(loan_id: str, doc_key: str, data: dict[str, Any]) -> Path:
    """Saves LLM-structured document JSON to s3_extracted_structured/{loan_id}/{doc_key}.json."""
    out_dir = S3_EXTRACTED_STRUCTURED_DIR / loan_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_key}.json"
    write_json(out_path, data)
    return out_path


def get_s3_extracted_structured(loan_id: str, doc_key: str) -> dict[str, Any]:
    """Retrieves structured document JSON from s3_extracted_structured."""
    path = S3_EXTRACTED_STRUCTURED_DIR / loan_id / f"{doc_key}.json"
    if path.exists():
        return read_json(path)
    return {}


def get_all_s3_extracted_structured(loan_id: str) -> dict[str, dict[str, Any]]:
    """Retrieves all structured document JSONs for a loan, indexing by both canonical key and file stem."""
    from config.doc_types import get_canonical_doc_type

    docs: dict[str, dict[str, Any]] = {}
    struct_dir = S3_EXTRACTED_STRUCTURED_DIR / loan_id
    if struct_dir.exists():
        for f in struct_dir.glob("*.json"):
            try:
                data = read_json(f)
                if isinstance(data, dict):
                    canon_key = get_canonical_doc_type(f.stem)
                    docs[canon_key] = data
                    docs[f.stem] = data
            except Exception:
                pass

    # Fallback to s3_extracted for any missing docs
    ext_dir = S3_EXTRACTED_DIR / loan_id
    if ext_dir.exists():
        for f in ext_dir.glob("*.json"):
            if f.stem.endswith("_structured"):
                continue
            canon_key = get_canonical_doc_type(f.stem)
            if canon_key not in docs:
                try:
                    data = read_json(f)
                    if isinstance(data, dict):
                        docs[canon_key] = data
                        docs[f.stem] = data
                except Exception:
                    pass

    return docs


def save_s3_result(loan_id: str, filename: str, data: Any) -> Path:
    """Saves a result artifact in s3_result/{loan_id}/{filename}."""
    out_dir = S3_RESULT_DIR / loan_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    write_json(out_path, data)
    return out_path


def get_s3_result(loan_id: str, filename: str) -> Any:
    """Retrieves a result artifact from s3_result/{loan_id}/{filename}."""
    path = S3_RESULT_DIR / loan_id / filename
    if path.exists():
        return read_json(path)
    return None
