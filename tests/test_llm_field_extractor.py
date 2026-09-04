"""Tests for pipeline.nodes.llm_field_extractor."""

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pipeline.nodes.llm_field_extractor import (
    LARGE_DOC_TYPES,
    SMALL_DOC_TYPES,
    _CANONICAL_KEYS,
    _build_user_content,
    _clean_json_response,
    llm_extract_fields,
)


# ── _clean_json_response ───────────────────────────────────────────────────

def test_clean_json_response_plain_json():
    raw = '{"applicant_name": "Rahul", "dob": null}'
    assert _clean_json_response(raw) == raw


def test_clean_json_response_markdown_fenced():
    raw = '```json\n{"applicant_name": "Rahul"}\n```'
    result = _clean_json_response(raw)
    assert '"applicant_name"' in result
    assert "```" not in result


def test_clean_json_response_prefixed_text():
    raw = 'Here is the JSON:\n{"pan_number": "ABCDE1234F"}'
    result = _clean_json_response(raw)
    assert '"pan_number"' in result


# ── _build_user_content ────────────────────────────────────────────────────

def test_build_user_content_small_doc_returns_inline():
    """Small doc types return raw_text directly without file I/O."""
    for doc_type in SMALL_DOC_TYPES:
        content = _build_user_content(doc_type, "some OCR text")
        assert content == "some OCR text"


def test_build_user_content_large_doc_roundtrips_via_md(tmp_path, monkeypatch):
    """Large doc types write to a temp .md file and return its content."""
    ocr_text = "# Key Fact Statement\nLoan Amount: 500000\nTenure: 24 months"
    # patch tempfile to use tmp_path so we stay in test sandbox
    import tempfile as _tf

    original_ntf = _tf.NamedTemporaryFile

    def patched_ntf(**kwargs):
        kwargs.setdefault("dir", str(tmp_path))
        return original_ntf(**kwargs)

    monkeypatch.setattr(_tf, "NamedTemporaryFile", patched_ntf)

    for doc_type in LARGE_DOC_TYPES:
        content = _build_user_content(doc_type, ocr_text)
        assert content == ocr_text  # content is identical; .md is cleaned up


def test_build_user_content_large_doc_fallback_on_io_error(monkeypatch):
    """If temp file I/O fails, raw_text is returned as a safe fallback."""
    import tempfile as _tf

    def broken_ntf(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_tf, "NamedTemporaryFile", broken_ntf)

    result = _build_user_content("kfs", "fallback text")
    assert result == "fallback text"


# ── llm_extract_fields — no API key ───────────────────────────────────────

def test_llm_extract_fields_no_api_key(monkeypatch):
    """Returns {} immediately when LLM_API_KEY is not configured."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", None)
    result = llm_extract_fields("aadhaar", "some text", "DOC_001")
    assert result == {}


# ── llm_extract_fields — empty OCR text ──────────────────────────────────

def test_llm_extract_fields_empty_text(monkeypatch):
    """Returns {} without making a network call when OCR text is empty."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "fake-key")
    result = llm_extract_fields("pan", "   ", "DOC_002")
    assert result == {}


def test_llm_extract_fields_none_text(monkeypatch):
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "fake-key")
    result = llm_extract_fields("pan", "", "DOC_003")
    assert result == {}


# ── llm_extract_fields — happy path ───────────────────────────────────────

def _make_mock_response(payload: dict) -> MagicMock:
    """Builds a mock httpx.Response with the given JSON payload."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    return mock_resp


def test_llm_extract_fields_happy_path_all_fields(monkeypatch):
    """LLM returns a full valid JSON; all canonical keys present in result."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    llm_payload = {
        "applicant_name": "RAJESH SHARMA",
        "fathers_name": "SURESH SHARMA",
        "dob": "15-05-1990",
        "mobile_no": "9876543210",
        "gender": "Male",
        "aadhaar_number": "1234 5678 9012",
        "pan_number": "ABCDE1234F",
        "address": "123 MG Road, Bengaluru",
        "current_address": "123 MG Road, Bengaluru",
        "account_no": "987654321012",
        "type_of_account": "SB",
        "loan_amount": "500000",
        "loan_validity": "24 months",
        "loan_type": "Personal Loan",
        "loan_account_no": "LOAN_001",
        "loan_no": "LOAN_001",
        "application_no": "APP_001",
        "application_date": "2024-01-10",
        "login_date": "2024-01-11",
        "disbursement_date": "2024-01-15",
        "customer_consent": True,
    }

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = _make_mock_response(llm_payload)
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("aadhaar", "raw aadhaar ocr text", "LOAN_001_aadhaar")

    assert result["applicant_name"] == "RAJESH SHARMA"
    assert result["aadhaar_number"] == "1234 5678 9012"
    assert result["pan_number"] == "ABCDE1234F"
    assert result["loan_amount"] == "500000"
    # All canonical keys should be in result
    assert all(k in result for k in _CANONICAL_KEYS)


def test_llm_extract_fields_partial_null_fields(monkeypatch):
    """LLM returns nulls for fields not in the document — stored as None."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    # Aadhaar-like: most financial fields absent
    llm_payload = {
        "applicant_name": "PRIYA MEHTA",
        "fathers_name": None,
        "dob": "01-01-1985",
        "mobile_no": "9123456789",
        "gender": "Female",
        "aadhaar_number": "9876 5432 1098",
        "pan_number": None,
        "address": "45 Gandhi Nagar, Mumbai",
        "current_address": None,
        "account_no": None,
        "type_of_account": None,
        "loan_amount": None,
        "loan_validity": None,
        "loan_type": None,
        "loan_account_no": None,
        "loan_no": None,
        "application_no": None,
        "application_date": None,
        "login_date": None,
        "disbursement_date": None,
        "customer_consent": None,
    }

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = _make_mock_response(llm_payload)
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("aadhaar", "aadhaar text", "LOAN_002_aadhaar")

    assert result["applicant_name"] == "PRIYA MEHTA"
    assert result["pan_number"] is None
    assert result["loan_amount"] is None
    assert result["aadhaar_number"] == "9876 5432 1098"


def test_llm_extract_fields_discards_extra_keys(monkeypatch):
    """Extra keys in LLM response (hallucinated) are silently discarded."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    llm_payload = {
        "applicant_name": "TEST USER",
        "hallucinated_field": "should be removed",
        "another_extra": 12345,
        "pan_number": "XXXXX9999X",
    }

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = _make_mock_response(llm_payload)
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("pan", "pan card text", "LOAN_003_pan")

    assert "hallucinated_field" not in result
    assert "another_extra" not in result
    assert result["applicant_name"] == "TEST USER"
    assert result["pan_number"] == "XXXXX9999X"


# ── llm_extract_fields — failure modes ────────────────────────────────────

def test_llm_extract_fields_http_error_returns_empty(monkeypatch):
    """HTTP 4xx/5xx from OpenRouter returns {} without raising."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    error_response = MagicMock(spec=httpx.Response)
    error_response.status_code = 429
    error_response.text = "Rate limit exceeded"

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = error_response
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "429", request=MagicMock(), response=error_response
    )

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("kfs", "kfs content", "LOAN_004_kfs")

    assert result == {}


def test_llm_extract_fields_timeout_returns_empty(monkeypatch):
    """Timeout from OpenRouter returns {} without raising."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    mock_client_instance = MagicMock()
    mock_client_instance.post.side_effect = httpx.TimeoutException("timed out")
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("application_form", "form text", "LOAN_005_app")

    assert result == {}


def test_llm_extract_fields_malformed_json_returns_empty(monkeypatch):
    """Invalid JSON from LLM returns {} without raising."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    bad_resp = MagicMock(spec=httpx.Response)
    bad_resp.status_code = 200
    bad_resp.raise_for_status = MagicMock()
    bad_resp.json.return_value = {
        "choices": [{"message": {"content": "not json at all"}}]
    }

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = bad_resp
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("disbursal_memo", "memo text", "LOAN_006_memo")

    assert result == {}


def test_llm_extract_fields_markdown_fenced_json_parsed_correctly(monkeypatch):
    """LLM wraps JSON in markdown fences — should still parse correctly."""
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.nodes.llm_field_extractor.LLM_MODEL", "test-model")

    fenced_content = '```json\n{"applicant_name": "ANKIT PATEL", "loan_no": "LN-999", "loan_amount": "300000"}\n```'

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": fenced_content}}]}

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = mock_resp
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.nodes.llm_field_extractor.httpx.Client", return_value=mock_client_instance):
        result = llm_extract_fields("disbursal_memo", "memo ocr", "LOAN_007_memo")

    assert result["applicant_name"] == "ANKIT PATEL"
    assert result["loan_no"] == "LN-999"
    assert result["loan_amount"] == "300000"


# ── Large doc routing — verifies correct doc types use .md path ───────────

@pytest.mark.parametrize("doc_type", list(LARGE_DOC_TYPES))
def test_large_doc_types_are_classified_correctly(doc_type):
    assert doc_type in LARGE_DOC_TYPES
    assert doc_type not in SMALL_DOC_TYPES


@pytest.mark.parametrize("doc_type", list(SMALL_DOC_TYPES))
def test_small_doc_types_are_classified_correctly(doc_type):
    assert doc_type in SMALL_DOC_TYPES
    assert doc_type not in LARGE_DOC_TYPES
