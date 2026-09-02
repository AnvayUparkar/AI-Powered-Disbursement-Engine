import logging
import re
import sys
from typing import Any, Dict, Optional
from idp.core.config import settings


# PII Redaction patterns
AADHAAR_REGEX = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
PAN_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b")
BANK_ACC_REGEX = re.compile(r"\b\d{9,18}\b")
API_KEY_REGEX = re.compile(r"(?i)(api_key|secret|password|token)\s*=\s*['\"]?[a-zA-Z0-9_\-]+['\"]?")


class PIISanitizerFilter(logging.Filter):
    """Filter that sanitizes PII (Aadhaar, PAN, Bank Accounts, Secrets) from logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self.sanitize(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self.sanitize(str(arg)) for arg in record.args)
        return True

    @staticmethod
    def sanitize(text: str) -> str:
        text = AADHAAR_REGEX.sub("[REDACTED-AADHAAR]", text)
        text = PAN_REGEX.sub("[REDACTED-PAN]", text)
        text = API_KEY_REGEX.sub(r"\1=[REDACTED-SECRET]", text)
        return text


def get_logger(name: str = "node2_idp") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.addFilter(PIISanitizerFilter())
        logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    return logger


logger = get_logger("node2_idp")


def format_doc_log(doc_id: str, message: str, proc_id: Optional[str] = None) -> str:
    """Format log message with document_id and optional processing_id."""
    if proc_id:
        return f"[{doc_id}] [{proc_id}] {message}"
    return f"[{doc_id}] {message}"
