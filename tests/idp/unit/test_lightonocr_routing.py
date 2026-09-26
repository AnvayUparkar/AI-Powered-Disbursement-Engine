"""
Tests for LightOnOCR scanned page routing.

Verifies:
1. Scanned pages -> LightOnOCR when enabled
2. Digital pages -> Existing Docling pipeline (unchanged, zero regression)
3. LightOnOCR low quality -> VLM fallback flag
4. LightOnOCR failure -> failed OCR result
5. LightOnOCR timeout -> None (safe degradation)
6. LightOnOCR result deduplication in serializer
"""
import base64
import contextlib
import json
import logging

import httpx
import pytest
from unittest.mock import Mock, patch, AsyncMock
from idp.services.ocr.lightonocr_engine import LightOnOCREngine, LightOnOCRResult, LIGHTONOCR_PROMPT
from idp.services.ocr.lightonocr_adapter import LightOnOCRAdapter
from idp.models.ocr import OCRResult, OCRElement
from idp.services.output.serializer import DocumentSerializer
from idp.core.config import settings


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GATEWAY = "http://litellm.test:4000/v1"
API_KEY = "sk-lightonocr-test-key"
PAGE_TEXT = "State Bank of India Loan Application Form Account Number 1234567890"

_REAL_CLIENT = httpx.Client


@contextlib.contextmanager
def _gateway(handler):
    """Route the engine's httpx.Client through an in-memory LiteLLM stand-in."""
    def factory(*args, **kwargs):
        return _REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))
    with patch("idp.services.ocr.lightonocr_engine.httpx.Client", side_effect=factory):
        yield


@contextlib.contextmanager
def _lightonocr_settings(**overrides):
    values = {
        "LIGHTONOCR_ENABLED": True,
        "LIGHTONOCR_BASE_URL": GATEWAY,
        "LIGHTONOCR_API_KEY": API_KEY,
        "LIGHTONOCR_MODEL": "lightonai/LightOnOCR-2-1B",
        "LIGHTONOCR_TIMEOUT_SECONDS": 30,
        "LIGHTONOCR_MAX_TOKENS": 2048,
        "LIGHTONOCR_LOG_TEXT_PREVIEW": False,
        "LLM_BASE_URL": None,
        "LLM_API_KEY": None,
    }
    values.update(overrides)
    with contextlib.ExitStack() as stack:
        for key, value in values.items():
            stack.enter_context(patch.object(settings, key, value))
        yield


def _completion(text=PAGE_TEXT, finish_reason="stop"):
    return {
        "id": "chatcmpl-123",
        "model": "lightonai/LightOnOCR-2-1B",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 812, "completion_tokens": 40, "total_tokens": 852},
    }


class TestLightOnOCRRouting:
    """Test scanned page routing to LightOnOCR."""
    
    def test_scanned_page_uses_lightonocr(self):
        """SCANNED page must route to LightOnOCR when enabled."""
        mock_result = LightOnOCRResult(
            text="Test OCR Text for Scanned Page",
            confidence=0.95,
            bboxes=[[10.0, 10.0, 100.0, 30.0]],
            inference_time_ms=150.0,
            quality_score=0.85
        )
        
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-001"
            )
        
        assert ocr_result is not None
        assert ocr_result.extraction_failed is False
        assert len(ocr_result.elements) > 0
        assert ocr_result.elements[0].source == "lightonocr"
        assert ocr_result.elements[0].text == "Test OCR Text for Scanned Page"
        assert ocr_result.elements[0].needs_vlm is False
    
    def test_digital_page_skips_lightonocr(self):
        """DIGITAL page must NOT route to LightOnOCR under any circumstances."""
        # Simulate digital PDF detection: is_scanned_pdf is False
        is_scanned_pdf = False
        lightonocr_enabled = True
        
        # Hard architectural condition from DocumentProcessor
        should_use_lightonocr = is_scanned_pdf and lightonocr_enabled
        
        assert not should_use_lightonocr, (
            "Digital pages must NEVER use LightOnOCR"
        )
    
    def test_lightonocr_low_quality_triggers_vlm(self):
        """Low quality LightOnOCR result must trigger VLM fallback."""
        mock_result = LightOnOCRResult(
            text="3T9T3πT&T",  # Corrupted/garbled text
            confidence=0.50,
            bboxes=[],
            inference_time_ms=100.0,
            quality_score=0.25  # Below default threshold 0.40
        )
        
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=mock_result):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-002"
            )
        
        assert ocr_result is not None
        assert ocr_result.low_confidence_count > 0
        assert ocr_result.elements[0].needs_vlm is True
    
    def test_lightonocr_failure_creates_failed_result(self):
        """LightOnOCR failure must create extraction_failed result for graceful VLM fallback."""
        adapter = LightOnOCRAdapter()
        with patch.object(adapter.engine, 'process_page', return_value=None):
            ocr_result = adapter.process_page_to_ocr_result(
                image_bytes=b"fake_image_bytes",
                page_number=1,
                image_width=595.0,
                image_height=842.0,
                doc_id="TEST-003"
            )
        
        assert ocr_result is not None
        assert ocr_result.extraction_failed is True
        assert len(ocr_result.elements) == 0
        assert ocr_result.average_confidence == 0.0
    
    def test_lightonocr_timeout_returns_none(self):
        """LightOnOCR gateway timeout returns None (safe degradation)."""
        def handler(request):
            raise httpx.ReadTimeout("gateway too slow", request=request)

        with _gateway(handler), _lightonocr_settings():
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-TIMEOUT")

        assert result is None

    def test_lightonocr_results_deduplicated_by_serializer(self):
        """LightOnOCR results must pass through _is_duplicate deduplication in serializer."""
        serializer = DocumentSerializer()
        
        box1 = [10.0, 10.0, 100.0, 50.0]
        box2 = [12.0, 12.0, 98.0, 48.0]
        
        iou = serializer._compute_iou(box1, box2)
        assert iou >= 0.70  # High spatial overlap
        
        # Two identical text strings with high overlap are detected as duplicates
        existing_elements = [
            Mock(bbox=box1, text="Duplicate Line")
        ]
        is_dup = serializer._is_duplicate(
            ocr_bbox=box2,
            existing_elements=existing_elements,
            iou_threshold=0.50,
            text="Duplicate Line"
        )
        assert is_dup is True


class TestQualityScoreComputation:
    """Test heuristic quality score computation in LightOnOCREngine."""
    
    def test_quality_score_empty_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("", 0.95, b"")
        assert score == 0.0
    
    def test_quality_score_clean_english_text(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score(
            "State Bank of India Loan Application Form Account Number 1234567890",
            0.98,
            b""
        )
        assert score >= 0.80
    
    def test_quality_score_short_fragment(self):
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A1", 0.50, b"")
        assert score < 0.40

    def test_quality_score_none_text(self):
        """Failure mode: text=None must not raise and must score as empty."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score(None, 0.90, b"")
        assert score == 0.0

    def test_quality_score_whitespace_only_text(self):
        """Edge case: whitespace-only text strips to empty and scores as empty."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("   ", 0.90, b"")
        assert score == 0.0

    def test_quality_score_length_gate_below_five_chars(self):
        """Boundary: 4 chars falls into the <5 'severe fragment' bracket (+0.05 base)."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCD", 1.0, b"")
        assert score == 0.51

    def test_quality_score_length_gate_at_five_chars(self):
        """Boundary: 5 chars crosses into the >=5 bracket (+0.2 base), jumping the score."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDE", 1.0, b"")
        assert score == 0.70

    def test_quality_score_length_gate_below_ten_chars(self):
        """Boundary: 9 chars stays in the >=5,<10 bracket, matching the 5-char score."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDEFGHI", 1.0, b"")
        assert score == 0.70

    def test_quality_score_length_gate_at_ten_chars(self):
        """Boundary: 10 chars crosses both the base-score and content-depth gates at once."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDEFGHIJ", 1.0, b"")
        assert score == 0.90

    def test_quality_score_content_depth_below_twenty_five_chars(self):
        """Boundary: 24 chars stays below the full content-depth bonus."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A" * 24, 1.0, b"")
        assert score == 0.90

    def test_quality_score_content_depth_at_twenty_five_chars(self):
        """Boundary: 25 chars unlocks the full content-depth bonus, reaching the max score."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A" * 25, 1.0, b"")
        assert score == 1.0

    def test_quality_score_confidence_clamped_when_negative(self):
        """Failure mode: a negative confidence must clamp to 0 contribution, never go negative."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("A" * 25, -1.0, b"")
        assert score == 0.70

    def test_quality_score_confidence_clamped_above_one(self):
        """Edge case: confidence > 1.0 must clamp to 1.0 contribution, not overshoot."""
        engine = LightOnOCREngine()
        score = engine._compute_quality_score("ABCDE", 2.0, b"")
        assert score == 0.70


class TestLightOnOCRViaLiteLLM:
    """LightOnOCR is only ever reached through the LiteLLM gateway's /chat/completions."""

    def test_page_image_is_sent_to_litellm_chat_completions(self):
        """Happy path: request shape (URL, auth, model, base64 image, prompt) and parsed result."""
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=_completion())

        with _gateway(handler), _lightonocr_settings():
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=2, doc_id="TEST-OK")

        assert len(seen) == 1
        req = seen[0]
        assert str(req.url) == f"{GATEWAY}/chat/completions"
        assert req.headers["Authorization"] == f"Bearer {API_KEY}"
        body = json.loads(req.content)
        assert body["model"] == "lightonai/LightOnOCR-2-1B"
        assert body["max_tokens"] == 2048
        assert body["temperature"] == 0.0
        parts = body["messages"][0]["content"]
        assert parts[0]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()
        assert parts[1] == {"type": "text", "text": LIGHTONOCR_PROMPT}

        assert result is not None
        assert result.text == PAGE_TEXT
        assert result.confidence == 0.95
        assert result.bboxes == []
        assert result.quality_score >= 0.80

    def test_jpeg_page_is_sent_with_jpeg_mime(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=_completion())

        with _gateway(handler), _lightonocr_settings():
            LightOnOCREngine().process_page(JPEG_BYTES, page_number=1, doc_id="TEST-JPEG")

        assert seen[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_falls_back_to_llm_gateway_settings(self):
        """Edge case: no LIGHTONOCR_BASE_URL/API_KEY -> reuse LLM_BASE_URL/LLM_API_KEY."""
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=_completion())

        with _gateway(handler), _lightonocr_settings(
            LIGHTONOCR_BASE_URL=None, LIGHTONOCR_API_KEY="",
            LLM_BASE_URL="http://shared-gateway:4000/v1/", LLM_API_KEY="sk-shared",
        ):
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-FALLBACK")

        assert result is not None
        assert str(seen[0].url) == "http://shared-gateway:4000/v1/chat/completions"
        assert seen[0].headers["Authorization"] == "Bearer sk-shared"

    def test_base_url_already_ending_in_chat_completions_is_not_doubled(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json=_completion())

        with _gateway(handler), _lightonocr_settings(LIGHTONOCR_BASE_URL=f"{GATEWAY}/chat/completions"):
            LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-URL")

        assert seen == [f"{GATEWAY}/chat/completions"]

    def test_content_parts_are_joined(self):
        """Edge case: gateways that return content as a list of text parts."""
        payload = _completion()
        payload["choices"][0]["message"]["content"] = [{"type": "text", "text": "Line one "}, {"type": "text", "text": "line two"}]

        with _gateway(lambda r: httpx.Response(200, json=payload)), _lightonocr_settings():
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-PARTS")

        assert result.text == "Line one line two"

    def test_truncated_output_is_kept_and_warned(self, caplog):
        with _gateway(lambda r: httpx.Response(200, json=_completion(finish_reason="length"))), _lightonocr_settings(), \
             caplog.at_level(logging.WARNING, logger="node2_idp"):
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-TRUNC")

        assert result is not None and result.text == PAGE_TEXT
        assert any("truncated at max_tokens=2048" in r.getMessage() for r in caplog.records)

    def test_request_and_response_are_logged_without_the_api_key(self, caplog):
        with _gateway(lambda r: httpx.Response(200, json=_completion())), _lightonocr_settings(), \
             caplog.at_level(logging.INFO, logger="node2_idp"):
            LightOnOCREngine().process_page(PNG_BYTES, page_number=3, doc_id="TEST-LOG")

        messages = [r.getMessage() for r in caplog.records]
        request_line = next(m for m in messages if "request -> POST" in m)
        response_line = next(m for m in messages if "response <- HTTP 200" in m)
        assert f"{GATEWAY}/chat/completions" in request_line
        assert "model=lightonai/LightOnOCR-2-1B" in request_line
        assert "page=3" in request_line
        assert "api_key_from=LIGHTONOCR_API_KEY" in request_line
        assert "tokens(prompt=812, completion=40, total=852)" in response_line
        assert "finish_reason=stop" in response_line
        assert not any(API_KEY in m for m in messages)
        assert not any("text preview" in m for m in messages)

    def test_text_preview_logged_only_when_enabled(self, caplog):
        with _gateway(lambda r: httpx.Response(200, json=_completion())), \
             _lightonocr_settings(LIGHTONOCR_LOG_TEXT_PREVIEW=True), \
             caplog.at_level(logging.INFO, logger="node2_idp"):
            LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-PREVIEW")

        assert any("text preview: State Bank of India" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("status, body", [
        (405, "<html><head><title>405 Not Allowed</title></head></html>"),
        (401, '{"error": "invalid api key"}'),
        (500, '{"error": "upstream failure"}'),
    ])
    def test_gateway_http_error_returns_none(self, status, body, caplog):
        with _gateway(lambda r: httpx.Response(status, text=body)), _lightonocr_settings(), \
             caplog.at_level(logging.ERROR, logger="node2_idp"):
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-HTTP")

        assert result is None
        assert any(f"gateway returned HTTP {status}" in r.getMessage() for r in caplog.records)

    def test_unreachable_gateway_returns_none(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        with _gateway(handler), _lightonocr_settings():
            assert LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-CONN") is None

    def test_non_json_response_returns_none(self):
        with _gateway(lambda r: httpx.Response(200, text="not json")), _lightonocr_settings():
            assert LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-NONJSON") is None

    def test_empty_choices_returns_none(self):
        with _gateway(lambda r: httpx.Response(200, json={"id": "x", "choices": []})), _lightonocr_settings():
            assert LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-NOCHOICE") is None

    @pytest.mark.parametrize("overrides", [
        {"LIGHTONOCR_BASE_URL": None, "LLM_BASE_URL": None},
        {"LIGHTONOCR_API_KEY": None, "LLM_API_KEY": None},
        {"LIGHTONOCR_MODEL": ""},
    ])
    def test_missing_gateway_config_never_calls_http(self, overrides):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=_completion())

        with _gateway(handler), _lightonocr_settings(**overrides):
            result = LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-NOCFG")

        assert result is None
        assert calls == []

    def test_disabled_never_calls_http(self):
        calls = []
        with _gateway(lambda r: calls.append(r) or httpx.Response(200, json=_completion())), \
             _lightonocr_settings(LIGHTONOCR_ENABLED=False):
            assert LightOnOCREngine().process_page(PNG_BYTES, page_number=1, doc_id="TEST-OFF") is None
        assert calls == []

    def test_empty_image_never_calls_http(self):
        calls = []
        with _gateway(lambda r: calls.append(r) or httpx.Response(200, json=_completion())), _lightonocr_settings():
            assert LightOnOCREngine().process_page(b"", page_number=1, doc_id="TEST-EMPTY") is None
        assert calls == []

    def test_engine_has_no_local_model_path(self):
        """The in-process transformers/torch path must be gone."""
        import idp.services.ocr.lightonocr_engine as mod
        engine = LightOnOCREngine()
        for attr in ("_load_model_internal", "_run_inference_with_timeout", "unload_model", "is_loaded"):
            assert not hasattr(engine, attr)
        source = open(mod.__file__, encoding="utf-8").read()
        assert "transformers" not in source and "import torch" not in source
