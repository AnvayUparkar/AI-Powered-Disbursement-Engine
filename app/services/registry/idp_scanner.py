"""Scanner for pre-parsed mock IDP documents and raw uploads on disk."""
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Set

from datetime import datetime
from config import IST
from config.paths import IDP_PARSED_DIR, IDP_RAW_DIR

logger = logging.getLogger("disbursement_pipeline.document_registry.idp_scanner")


def scan_idp_parsed_storage(
    known_doc_ids: Set[str],
    register_func: Callable[..., Any],
) -> None:
    """
    Scan disk storage for pre-existing parsed documents and raw uploads in the mock IDP store
    and register them via the provided callback.
    """
    try:
        parsed_dir = IDP_PARSED_DIR
        raw_dir = IDP_RAW_DIR

        # 1. Scan parsed documents JSONs
        if parsed_dir.exists():
            for json_file in parsed_dir.glob("*.json"):
                doc_id = json_file.stem
                if doc_id in known_doc_ids:
                    continue

                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    filename = data.get("source", {}).get("filename") or f"{doc_id}.pdf"
                    s3_key = data.get("source", {}).get("s3_key") or ""
                    inferred_case = None

                    from config import DMS_DIR, S3_RAW_DIR
                    m = re.search(r"(LOAN_\d+|HDB-[A-Za-z0-9\-]+|APPL\d+)", f"{doc_id}_{filename}_{s3_key}")
                    if m:
                        cand_case = m.group(1)
                        # Only associate with case if the raw physical document exists for this case in s3_raw
                        case_raw_exists = (
                            (S3_RAW_DIR / cand_case / filename).exists()
                            or (DMS_DIR / cand_case / filename).exists()
                        )
                        if case_raw_exists:
                            inferred_case = cand_case
                        else:
                            inferred_case = "GENERAL"

                    mtime = json_file.stat().st_mtime
                    up_at = datetime.fromtimestamp(mtime, tz=IST).strftime("%Y-%m-%d %H:%M IST")

                    register_func(
                        doc_id=doc_id,
                        filename=filename,
                        case_id=inferred_case,
                        parsed_result=data,
                        file_size_bytes=data.get("processing", {}).get("file_size_bytes", 150000),
                        uploaded_at=up_at,
                        uploaded_timestamp=mtime,
                    )
                    known_doc_ids.add(doc_id)
                except Exception as e:
                    logger.debug("Failed indexing parsed document file %s: %s", json_file, e)

        # 2. Scan raw uploads in case they are pending or parsed without JSON
        if raw_dir.exists():
            for raw_file in raw_dir.iterdir():
                if not raw_file.is_file():
                    continue
                name = raw_file.name
                if "_" in name:
                    doc_id, orig_filename = name.split("_", 1)
                else:
                    doc_id = raw_file.stem
                    orig_filename = raw_file.name

                if doc_id in known_doc_ids:
                    continue

                inferred_case = None
                m = re.search(r"(LOAN_\d+|HDB-[A-Za-z0-9\-]+|APPL\d+)", f"{doc_id}_{orig_filename}")
                if m:
                    cand_case = m.group(1)
                    if (S3_RAW_DIR / cand_case / orig_filename).exists() or (DMS_DIR / cand_case / orig_filename).exists():
                        inferred_case = cand_case
                    else:
                        inferred_case = "GENERAL"

                try:
                    size_bytes = raw_file.stat().st_size
                    mtime = raw_file.stat().st_mtime
                    up_at = datetime.fromtimestamp(mtime, tz=IST).strftime("%Y-%m-%d %H:%M IST")
                except OSError:
                    size_bytes = 0
                    mtime = None
                    up_at = None

                try:
                    register_func(
                        doc_id=doc_id,
                        filename=orig_filename,
                        case_id=inferred_case,
                        parsed_result=None,
                        file_size_bytes=size_bytes,
                        uploaded_at=up_at,
                        uploaded_timestamp=mtime,
                    )
                    known_doc_ids.add(doc_id)
                except Exception as e:
                    logger.debug("Failed indexing raw upload file %s: %s", raw_file, e)
    except Exception as e:
        logger.debug("Error during IDP storage scan: %s", e)

