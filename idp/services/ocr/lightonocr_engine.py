"""
LightOnOCR-2-1B client for scanned document OCR.

LightOnOCR is never loaded in-process: every page is sent to the LiteLLM gateway's OpenAI-compatible
/chat/completions endpoint as a base64 image, and the model's text reply is the page's OCR text.
"""
from typing import Optional, List, Dict, Any, Tuple
import base64
import re
import time
import uuid

import httpx
from pydantic import BaseModel, Field

from idp.core.config import settings
from idp.core.logging import logger, format_doc_log


# ProcessingMetadata.ocr_engine value recorded when a document went through this engine; the API and
# UI key off it to show that LightOnOCR (via LiteLLM) produced the text.
LIGHTONOCR_ENGINE_ID = "lightonocr_litellm"

# Instruction sent alongside each page image.
LIGHTONOCR_PROMPT = "Extract the text from this document."

# The chat/completions API returns no per-token OCR confidence, so a fixed engine confidence is used
# (same value the previous in-process implementation reported). The quality score below is what
# actually gates the VLM fallback.
_DEFAULT_CONFIDENCE = 0.95

# Upper bound on how long a single connection attempt may take, independent of the overall request
# timeout, so an unreachable gateway fails fast instead of consuming the whole page timeout.
_CONNECT_TIMEOUT_SECONDS = 10.0

# Response bodies are truncated to this many characters in error logs.
_ERROR_BODY_LOG_CHARS = 500


class LightOnOCRResult(BaseModel):
    """Raw result from LightOnOCR model."""
    text: str
    confidence: float
    bboxes: List[List[float]] = Field(default_factory=list)  # List of [x1, y1, x2, y2]
    inference_time_ms: float = 0.0
    quality_score: float = 0.0  # Custom quality metric


def _image_mime_type(image_bytes: bytes) -> str:
    """Return the MIME type of a page image from its magic bytes (PNG unless it is clearly JPEG)."""
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


def _endpoint_url(base_url: str) -> str:
    """Resolve the full /chat/completions URL from a gateway base URL ending in /v1."""
    url = base_url.strip().rstrip("/")
    return url if url.endswith("/chat/completions") else f"{url}/chat/completions"


class LightOnOCREngine:
    """LightOnOCR-2-1B served through the LiteLLM gateway (OpenAI-compatible chat/completions)."""

    def __init__(self) -> None:
        self.model_name: str = settings.LIGHTONOCR_MODEL
        self.timeout_seconds: int = settings.LIGHTONOCR_TIMEOUT_SECONDS
        self.max_tokens: int = settings.LIGHTONOCR_MAX_TOKENS

    def _resolve_gateway(self) -> Tuple[Optional[str], Optional[str], str, str]:
        """Return (base_url, api_key, base_url_source, api_key_source).

        LIGHTONOCR_BASE_URL / LIGHTONOCR_API_KEY win; otherwise the LLM gateway settings are reused,
        since both are served by the same LiteLLM deployment.
        """
        if settings.LIGHTONOCR_BASE_URL:
            base_url, base_src = settings.LIGHTONOCR_BASE_URL, "LIGHTONOCR_BASE_URL"
        else:
            base_url, base_src = settings.LLM_BASE_URL, "LLM_BASE_URL (fallback)"
        if settings.LIGHTONOCR_API_KEY:
            api_key, key_src = settings.LIGHTONOCR_API_KEY, "LIGHTONOCR_API_KEY"
        else:
            api_key, key_src = settings.LLM_API_KEY, "LLM_API_KEY (fallback)"
        return base_url, api_key, base_src, key_src

    def process_page(
        self,
        image_bytes: bytes,
        page_number: int,
        doc_id: str = "DOC"
    ) -> Optional[LightOnOCRResult]:
        """
        Run LightOnOCR on a single page through the LiteLLM gateway.

        Args:
            image_bytes: Page image (PNG/JPEG)
            page_number: Page number (1-indexed)
            doc_id: Document ID for logging

        Returns:
            LightOnOCRResult or None on failure
        """
        if not settings.LIGHTONOCR_ENABLED:
            logger.debug(format_doc_log(doc_id, f"LightOnOCR disabled; skipping page {page_number}"))
            return None

        call_id = uuid.uuid4().hex[:12]
        tag = f"[LightOnOCR call={call_id} page={page_number}]"

        base_url, api_key, base_src, key_src = self._resolve_gateway()
        missing = [name for name, val in (("base URL", base_url), ("API key", api_key), ("model", self.model_name)) if not val]
        if missing:
            logger.error(format_doc_log(
                doc_id,
                f"{tag} not configured: missing {', '.join(missing)}. Set LIGHTONOCR_BASE_URL/LIGHTONOCR_MODEL "
                f"(config) and LIGHTONOCR_API_KEY or LLM_API_KEY (secret)."
            ))
            return None

        if not image_bytes:
            logger.error(format_doc_log(doc_id, f"{tag} empty page image; nothing to send"))
            return None

        endpoint = _endpoint_url(base_url)
        mime = _image_mime_type(image_bytes)
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_img}"}},
                        {"type": "text", "text": LIGHTONOCR_PROMPT},
                    ],
                }
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        logger.info(format_doc_log(
            doc_id,
            f"{tag} request -> POST {endpoint} model={self.model_name} image={mime} "
            f"{len(image_bytes)} bytes (base64 {len(b64_img)} chars) max_tokens={self.max_tokens} "
            f"timeout={self.timeout_seconds}s base_url_from={base_src} api_key_from={key_src}"
        ))

        start_time = time.time()
        timeout = httpx.Timeout(float(self.timeout_seconds), connect=min(_CONNECT_TIMEOUT_SECONDS, float(self.timeout_seconds)))
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(format_doc_log(
                doc_id, f"{tag} timed out after {elapsed:.0f}ms ({type(e).__name__}; limit {self.timeout_seconds}s) at {endpoint}"
            ))
            return None
        except httpx.HTTPError as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(format_doc_log(
                doc_id, f"{tag} transport error after {elapsed:.0f}ms calling {endpoint}: {type(e).__name__}: {e}"
            ))
            return None

        elapsed_ms = (time.time() - start_time) * 1000
        if response.status_code >= 400:
            logger.error(format_doc_log(
                doc_id,
                f"{tag} gateway returned HTTP {response.status_code} after {elapsed_ms:.0f}ms: "
                f"{response.text[:_ERROR_BODY_LOG_CHARS]}"
            ))
            return None

        try:
            data = response.json()
        except ValueError as e:
            logger.error(format_doc_log(
                doc_id,
                f"{tag} response is not JSON (HTTP {response.status_code}, {elapsed_ms:.0f}ms): {e}; "
                f"body={response.text[:_ERROR_BODY_LOG_CHARS]}"
            ))
            return None

        choices = data.get("choices") or []
        if not choices:
            logger.error(format_doc_log(
                doc_id, f"{tag} response has no choices (HTTP {response.status_code}, {elapsed_ms:.0f}ms): {str(data)[:_ERROR_BODY_LOG_CHARS]}"
            ))
            return None

        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        text = content if isinstance(content, str) else ""
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage") or {}

        confidence = _DEFAULT_CONFIDENCE
        quality_score = self._compute_quality_score(text, confidence, image_bytes)

        logger.info(format_doc_log(
            doc_id,
            f"{tag} response <- HTTP {response.status_code} in {elapsed_ms:.0f}ms id={data.get('id')} "
            f"served_model={data.get('model')} finish_reason={finish_reason} "
            f"tokens(prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')}, "
            f"total={usage.get('total_tokens')}) chars={len(text)} conf={confidence:.2f} quality={quality_score:.2f}"
        ))
        if finish_reason == "length":
            logger.warning(format_doc_log(
                doc_id, f"{tag} output truncated at max_tokens={self.max_tokens}; raise LIGHTONOCR_MAX_TOKENS"
            ))
        if not text.strip():
            logger.warning(format_doc_log(doc_id, f"{tag} gateway returned empty text"))
        if settings.LIGHTONOCR_LOG_TEXT_PREVIEW:
            preview = text[:300].replace("\n", " | ")
            logger.info(format_doc_log(doc_id, f"{tag} text preview: {preview}"))

        return LightOnOCRResult(
            text=text,
            confidence=confidence,
            bboxes=[],
            inference_time_ms=elapsed_ms,
            quality_score=quality_score
        )

    def _compute_quality_score(
        self,
        text: str,
        confidence: float,
        image_bytes: bytes
    ) -> float:
        """
        Compute quality score for LightOnOCR result.

        Quality heuristics:
        - Non-empty text with length gating: up to +0.3
        - Text depth (sufficient content for a full-page document): up to +0.2
        - Engine confidence: up to +0.3
        - Valid character ratio (scaled by length confidence): up to +0.2

        Returns: 0.0 to 1.0
        """
        cleaned = text.strip() if text else ""
        total_chars = len(cleaned)
        if total_chars == 0:
            return 0.0

        score = 0.0

        # Check 1: Non-empty & minimum usable length for a scanned document page
        # Scanned pages containing fewer than 5 characters are severe truncations/fragments
        if total_chars >= 10:
            score += 0.3
        elif total_chars >= 5:
            score += 0.2
        else:
            score += 0.05

        # Check 2: Content depth (reasonable document volume)
        if total_chars >= 25:
            score += 0.2
        elif total_chars >= 10:
            score += 0.1

        # Check 3: Confidence contribution (up to 0.3)
        score += min(max(confidence, 0.0), 1.0) * 0.3

        # Check 4: Valid character ratio scaled by sample length
        # A 1-2 char fragment cannot establish character distribution validity
        valid_chars = len(re.findall(r'[a-zA-Z0-9\s।,\.\'"\-/]', cleaned))
        valid_ratio = valid_chars / total_chars
        length_weight = min(1.0, total_chars / 5.0)
        score += valid_ratio * length_weight * 0.2

        return min(round(score, 4), 1.0)


# Singleton instance
_lightonocr_engine: Optional[LightOnOCREngine] = None


def get_lightonocr_engine() -> LightOnOCREngine:
    """Get singleton LightOnOCR engine instance."""
    global _lightonocr_engine
    if _lightonocr_engine is None:
        _lightonocr_engine = LightOnOCREngine()
    return _lightonocr_engine
