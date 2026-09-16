"""pyHanko PDF Digital Signature Inspector Engine.

Extracts and cryptographically verifies digital signatures from PDF documents using pyHanko,
supporting Indian CCA (Controller of Certifying Authorities) and global PKI trust chains.
Includes detection utilities to ensure verification is executed exclusively on Loan Agreements.
"""

from __future__ import annotations

import asyncio
import datetime
import glob
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import certifi
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.general import load_certs_from_pemder
from pyhanko.sign.validation import (
    EmbeddedPdfSignature,
    PdfSignatureStatus,
    async_validate_pdf_signature,
)
from pyhanko_certvalidator import ValidationContext

from config import (
    REQUIRE_TRUSTED_DIGITAL_SIGNATURE,
    TRUSTED_ROOTS_DIR,
    get_canonical_doc_type,
)

logger = logging.getLogger("disbursement_pipeline.pyhanko_inspector")

# Silence noisy certvalidator logs on untrusted/self-signed chains
logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)
logging.getLogger("pyhanko").setLevel(logging.WARNING)
logging.getLogger("pyhanko.sign.validation.generic_cms").setLevel(logging.CRITICAL)

_CACHED_TRUST_ROOTS: Optional[List[Any]] = None


def is_loan_agreement(name_or_key: str) -> bool:
    """Detects whether a document name, stem, or canonical key represents a Loan Agreement.

    Returns True if:
    1. get_canonical_doc_type(name_or_key).lower() == 'loan_agreement'
    2. 'loan_agreement' in name_or_key.lower()
    3. 'agreement' in name_or_key.lower()
    """
    if not name_or_key:
        return False

    name_lower = str(name_or_key).strip().lower()

    # Direct substring detection
    if "loan_agreement" in name_lower or "agreement" in name_lower:
        return True

    # Canonical mapping detection
    canonical = get_canonical_doc_type(name_or_key).lower()
    return canonical == "loan_agreement"


def load_all_trust_roots(custom_roots_dir: Optional[Union[str, Path]] = None) -> List[Any]:
    """Loads all trusted root certificates:

    1. Configured TRUSTED_ROOTS_DIR (CCA India 2022, 2014, Sub-CAs, custom roots).
    2. certifi global Mozilla CA bundle.
    """
    roots: List[Any] = []
    roots_dir = Path(custom_roots_dir or TRUSTED_ROOTS_DIR)

    # 1. Load from trusted roots directory
    if roots_dir.is_dir():
        for ext in ("*.cer", "*.crt", "*.pem", "*.der"):
            for cert_path in roots_dir.glob(ext):
                try:
                    loaded = list(load_certs_from_pemder([str(cert_path)]))
                    roots.extend(loaded)
                    logger.debug("Loaded %d root cert(s) from %s", len(loaded), cert_path.name)
                except Exception as e:
                    logger.warning("Could not load root cert %s: %s", cert_path, e)

    # 2. Load from certifi CA bundle
    try:
        ca_path = certifi.where()
        if Path(ca_path).exists():
            certifi_roots = list(load_certs_from_pemder([ca_path]))
            roots.extend(certifi_roots)
            logger.debug("Loaded %d certifi Mozilla root certs", len(certifi_roots))
    except Exception as e:
        logger.warning("Could not load certifi roots: %s", e)

    return roots


def get_all_trust_roots() -> List[Any]:
    """Returns cached trust roots, initializing once on first access."""
    global _CACHED_TRUST_ROOTS
    if _CACHED_TRUST_ROOTS is None:
        _CACHED_TRUST_ROOTS = load_all_trust_roots()
    return _CACHED_TRUST_ROOTS


def reset_trust_roots_cache() -> None:
    """Resets the cached trust roots (useful for isolated unit testing)."""
    global _CACHED_TRUST_ROOTS
    _CACHED_TRUST_ROOTS = None


def build_validation_context(extra_certs: Optional[List[Any]] = None) -> Optional[ValidationContext]:
    """Builds a pyHanko ValidationContext with all trust roots and optional embedded certs."""
    roots = get_all_trust_roots()
    if not roots:
        return None
    try:
        return ValidationContext(
            trust_roots=roots,
            other_certs=extra_certs,
            allow_fetching=False,
        )
    except Exception as e:
        logger.debug("Error creating ValidationContext: %s", e)
        return None


def _format_datetime(dt: Optional[datetime.datetime]) -> Optional[str]:
    if not dt:
        return None
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return str(dt)


def _serialize_cert_info(cert: Any) -> Dict[str, Any]:
    if cert is None:
        return {
            "common_name": "Unknown",
            "organization": "Unknown",
            "country": "Unknown",
            "subject_dn": "Unknown",
            "issuer_dn": "Unknown",
            "valid_from": None,
            "valid_until": None,
            "serial_number": None,
        }

    try:
        subject_native = getattr(cert.subject, "native", {}) or {}
    except Exception:
        subject_native = {}

    try:
        issuer_native = getattr(cert.issuer, "native", {}) or {}
    except Exception:
        issuer_native = {}

    common_name = subject_native.get("common_name", "N/A")
    organization = subject_native.get("organization_name", "N/A")
    country = subject_native.get("country_name", "N/A")

    subject_dn = getattr(cert.subject, "human_friendly", str(cert.subject))
    issuer_dn = getattr(cert.issuer, "human_friendly", str(cert.issuer))

    valid_from = getattr(cert, "not_valid_before", None)
    valid_until = getattr(cert, "not_valid_after", None)
    serial_number = getattr(cert, "serial_number", None)

    return {
        "common_name": str(common_name),
        "organization": str(organization),
        "country": str(country),
        "subject_dn": str(subject_dn),
        "issuer_dn": str(issuer_dn),
        "valid_from": _format_datetime(valid_from),
        "valid_until": _format_datetime(valid_until),
        "serial_number": hex(serial_number) if isinstance(serial_number, int) else str(serial_number) if serial_number else None,
    }


async def _async_inspect_pdf(
    source: Union[str, Path, bytes, io.BytesIO],
    filename: str = "document.pdf",
    check_trust: bool = True,
) -> Dict[str, Any]:
    """Asynchronously inspect a PDF using pyHanko and return structured diagnostic details."""
    if isinstance(source, (str, Path)):
        src_path = Path(source)
        if not src_path.is_file():
            return {
                "filename": filename,
                "error": f"File not found: {source}",
                "is_signed": False,
                "signature_count": 0,
                "is_acceptable": False,
                "signatures": [],
            }
        file_size = src_path.stat().st_size
        with open(src_path, "rb") as f:
            pdf_bytes = f.read()
    elif isinstance(source, bytes):
        pdf_bytes = source
        file_size = len(source)
    elif hasattr(source, "read"):
        source.seek(0)
        pdf_bytes = source.read()
        file_size = len(pdf_bytes)
    else:
        return {
            "filename": filename,
            "error": "Unsupported input format",
            "is_signed": False,
            "signature_count": 0,
            "is_acceptable": False,
            "signatures": [],
        }

    stream = io.BytesIO(pdf_bytes)

    try:
        reader = PdfFileReader(stream, strict=False)
    except Exception as e:
        return {
            "filename": filename,
            "file_size_bytes": file_size,
            "error": f"Failed to parse PDF document: {str(e)}",
            "is_signed": False,
            "signature_count": 0,
            "page_count": 0,
            "is_acceptable": False,
            "signatures": [],
        }

    page_count = 0
    try:
        if "/Pages" in reader.root and "/Count" in reader.root["/Pages"]:
            page_count = int(reader.root["/Pages"]["/Count"])
    except Exception:
        page_count = 0

    try:
        embedded_sigs: List[EmbeddedPdfSignature] = list(reader.embedded_signatures)
    except Exception as e:
        return {
            "filename": filename,
            "file_size_bytes": file_size,
            "page_count": page_count,
            "error": f"Error scanning embedded signatures: {str(e)}",
            "is_signed": False,
            "signature_count": 0,
            "is_acceptable": False,
            "signatures": [],
        }

    sig_count = len(embedded_sigs)
    is_signed = sig_count > 0
    inspected_signatures: List[Dict[str, Any]] = []

    for idx, sig in enumerate(embedded_sigs, start=1):
        field_name = getattr(sig, "field_name", f"Signature{idx}") or f"Signature{idx}"

        # Signer Certificate info
        try:
            cert = sig.signer_cert
            signer_cert_info = _serialize_cert_info(cert)
        except Exception as e:
            signer_cert_info = {
                "common_name": f"Could not extract cert: {e}",
                "organization": "Unknown",
                "country": "Unknown",
                "subject_dn": "Unknown",
                "issuer_dn": "Unknown",
                "valid_from": None,
                "valid_until": None,
                "serial_number": None,
            }

        try:
            dt = sig.self_reported_timestamp
            signing_time_str = _format_datetime(dt)
        except Exception:
            signing_time_str = None

        # Intermediate certificates embedded in signature
        extra_certs = []
        try:
            if hasattr(sig, "other_embedded_certs") and sig.other_embedded_certs:
                extra_certs = list(sig.other_embedded_certs)
        except Exception:
            extra_certs = []

        val_context = build_validation_context(extra_certs=extra_certs) if check_trust else None

        status: Optional[PdfSignatureStatus] = None
        validation_error: Optional[str] = None
        try:
            status = await async_validate_pdf_signature(
                sig,
                signer_validation_context=val_context,
            )
        except Exception as e:
            try:
                status = await async_validate_pdf_signature(
                    sig,
                    signer_validation_context=None,
                )
            except Exception as e2:
                validation_error = f"Validation failed: {str(e2)}"

        if status:
            try:
                intact = bool(status.intact)
            except Exception:
                intact = False

            try:
                valid = bool(status.valid)
            except Exception:
                valid = False

            try:
                trusted = bool(status.trusted)
            except Exception:
                trusted = False

            try:
                bottom_line = bool(status.bottom_line)
            except Exception:
                bottom_line = False

            try:
                summary_str = status.summary()
            except Exception:
                summary_str = "INTACT" if intact else "INVALID"

            try:
                coverage_str = getattr(status.coverage, "name", str(status.coverage))
            except Exception:
                coverage_str = "UNKNOWN"

            try:
                mod_level = str(status.modification_level) if status.modification_level else "None (Untouched)"
            except Exception:
                mod_level = "Unknown"

            try:
                docmdp_ok = bool(status.docmdp_ok)
            except Exception:
                docmdp_ok = True

            try:
                raw_details = status.pretty_print_details()
            except Exception as e:
                raw_details = f"Diagnostic formatting unavailable: {e}"

            has_ts = False
            ts_time_str = None
            try:
                if status.timestamp_validity:
                    has_ts = True
                    ts_time_str = _format_datetime(status.timestamp_validity.timestamp)
            except Exception:
                pass

            trust_anchor_label = "Untrusted / Self-Signed Root"
            is_cca_india = False
            if trusted:
                anchor_str = str(getattr(status, "_trust_anchor", "")) + raw_details
                if "CCA India" in anchor_str or "India PKI" in anchor_str:
                    trust_anchor_label = "CCA India (Govt. of India)"
                    is_cca_india = True
                else:
                    trust_anchor_label = "Trusted Global Root CA"

        else:
            intact = False
            valid = False
            trusted = False
            bottom_line = False
            summary_str = "CHECK_FAILED"
            coverage_str = "UNKNOWN"
            mod_level = "Unknown"
            docmdp_ok = False
            has_ts = False
            ts_time_str = None
            trust_anchor_label = "Validation Error"
            is_cca_india = False
            raw_details = validation_error or "Unable to validate signature object."

        sig_data = {
            "index": idx,
            "field_name": field_name,
            "sig_type": getattr(sig, "sig_object_type", "signature"),
            "intact": intact,
            "valid": valid,
            "trusted": trusted,
            "is_cca_india": is_cca_india,
            "trust_anchor_label": trust_anchor_label,
            "bottom_line": bottom_line,
            "summary": summary_str,
            "coverage": coverage_str,
            "modification_level": mod_level,
            "docmdp_ok": docmdp_ok,
            "signer": signer_cert_info,
            "signing_time": signing_time_str,
            "has_timestamp_token": has_ts,
            "timestamp_time": ts_time_str,
            "validation_error": validation_error,
            "raw_details": raw_details,
        }
        inspected_signatures.append(sig_data)

    # Acceptance logic: All embedded signatures must be cryptographically intact and valid.
    # If REQUIRE_TRUSTED_DIGITAL_SIGNATURE is enabled, they must also be rooted in a trusted CA.
    if is_signed and inspected_signatures:
        is_acceptable = all(
            sig["intact"] and sig["valid"] and (sig["trusted"] if REQUIRE_TRUSTED_DIGITAL_SIGNATURE else True)
            for sig in inspected_signatures
        )
    else:
        is_acceptable = False

    return {
        "filename": filename,
        "file_size_bytes": file_size,
        "page_count": page_count,
        "is_signed": is_signed,
        "signature_count": sig_count,
        "is_acceptable": is_acceptable,
        "signatures": inspected_signatures,
        "status_message": (
            f"Found {sig_count} digital signature(s)."
            if is_signed
            else "No digital signatures found in this document."
        ),
        "error": None,
    }


def inspect_pdf_signatures(
    source: Union[str, Path, bytes, io.BytesIO],
    filename: str = "document.pdf",
    check_trust: bool = True,
) -> Dict[str, Any]:
    """Synchronously inspect a PDF document with pyHanko, handling event loop execution."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # In an active event loop (e.g., FastAPI), run in thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                _async_inspect_pdf(source, filename=filename, check_trust=check_trust),
            ).result()
    else:
        return asyncio.run(_async_inspect_pdf(source, filename=filename, check_trust=check_trust))
