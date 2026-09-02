import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import LOS_LOANS_DIR, S3_RESULT_DIR


logger = logging.getLogger("disbursement_pipeline.storage")


def read_json(path: Path | str) -> dict:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path | str, data: Any) -> None:
    """Atomically writes JSON to file using a temporary file in the target directory."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to a temporary file in the same directory to ensure atomic replace on the same filesystem
    with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False, encoding="utf-8") as tmp_file:
        tmp_name = tmp_file.name
        json.dump(data, tmp_file, indent=2, ensure_ascii=False)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())

    os.replace(tmp_name, file_path)


def copy_file(src: Path | str, dst: Path | str) -> None:
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)


def copy_dir(src: Path | str, dst: Path | str) -> None:
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

    status_data = {
        "loan_id": loan_id,
        "current_node": current_node,
        "node_history": node_history if node_history is not None else history,
        "errors": errs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(status_path, status_data)
    return status_data


def list_loan_ids() -> list[str]:
    if not LOS_LOANS_DIR.exists():
        return []
    loan_ids = []
    for file in sorted(LOS_LOANS_DIR.glob("*.json")):
        loan_ids.append(file.stem)
    return loan_ids

