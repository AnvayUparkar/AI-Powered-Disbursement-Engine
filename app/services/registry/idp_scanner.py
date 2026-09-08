"""Scanner for pre-parsed mock IDP documents on disk."""
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Set

from idp.core.config import settings as idp_settings

logger = logging.getLogger("disbursement_pipeline.document_registry.idp_scanner")


def scan_idp_parsed_storage(
    known_doc_ids: Set[str],
    register_func: Callable[..., Any],
) -> None:
    """
    Scan disk storage for pre-existing parsed documents in the mock IDP store
    and register them via the provided callback.
    """
    try:
        parsed_dir = Path(idp_settings.TEMP_DIR) / "s3_mock" / idp_settings.S3_BUCKET / idp_settings.PARSED_DOCUMENT_PREFIX
        if not parsed_dir.exists():
            return

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

                m = re.search(r"(LOAN_\d+)", f"{doc_id}_{filename}_{s3_key}")
                if m:
                    inferred_case = m.group(1)

                register_func(
                    doc_id=doc_id,
                    filename=filename,
                    case_id=inferred_case,
                    parsed_result=data,
                    file_size_bytes=data.get("processing", {}).get("file_size_bytes", 150000),
                )
            except Exception as e:
                logger.debug("Failed indexing parsed document file %s: %s", json_file, e)
    except Exception as e:
        logger.debug("Error during IDP parsed storage scan: %s", e)
