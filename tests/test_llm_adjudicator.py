from unittest.mock import MagicMock
import pytest

from pipeline.engines.llm_adjudicator import llm_adjudicate
from pipeline.engines.llm_client import clean_json_response


def test_clean_json_text_markdown_block():
    markdown_str = "```json\n{\n  \"match_status\": \"MATCH\",\n  \"confidence\": 0.95,\n  \"reason\": \"Same name\"\n}\n```"
    cleaned = clean_json_response(markdown_str)
    assert "\"match_status\": \"MATCH\"" in cleaned


def test_llm_adjudication_mock_match(monkeypatch):
    """Test successful MATCH adjudication using a deterministic mocked client."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "pipeline.engines.llm_adjudicator.invoke_llm_json",
        lambda **kwargs: {
            "match_status": "MATCH",
            "confidence": 0.95,
            "reason": "Name variation of the same individual.",
        },
    )

    result = llm_adjudicate("Mohd Rizwan", "Mohammad Rizwan", "applicant_name", "LOAN_MOCK_001")
    assert result["match_status"] == "MATCH"
    assert result["llm_used"] is True
    assert result["confidence"] == 0.95
    assert len(result["reason"]) > 0


def test_llm_adjudication_mock_mismatch(monkeypatch):
    """Test successful MISMATCH adjudication using a deterministic mocked client."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "pipeline.engines.llm_adjudicator.invoke_llm_json",
        lambda **kwargs: {
            "match_status": "MISMATCH",
            "confidence": 0.98,
            "reason": "Completely different applicants.",
        },
    )

    result = llm_adjudicate("Riteshraj Panda", "Suresh Kumar", "applicant_name", "LOAN_MOCK_002")
    assert result["match_status"] == "MISMATCH"
    assert result["llm_used"] is True
    assert result["confidence"] == 0.98


def test_llm_adjudication_no_api_key_fallback(monkeypatch):
    """Test fallback when no LLM API key is configured."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr("pipeline.engines.llm_adjudicator.LLM_API_KEY", None)

    result = llm_adjudicate("Val A", "Val B", "address", "LOAN_NO_CLIENT")
    assert result["match_status"] == "PARTIAL"
    assert result["llm_used"] is False
    assert result["confidence"] == 0.5


def test_llm_adjudication_exception_fallback(monkeypatch):
    """Test graceful fallback when LLM API call fails (e.g. rate limit, timeout)."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def _failing_invoke(**kwargs):
        raise RuntimeError("429 Resource Exhausted")

    monkeypatch.setattr("pipeline.engines.llm_adjudicator.invoke_llm_json", _failing_invoke)

    result = llm_adjudicate("Val A", "Val B", "address", "LOAN_ERR")
    assert result["match_status"] == "PARTIAL"
    assert result["llm_used"] is False
    assert result["confidence"] == 0.5
    assert "service error" in result["reason"].lower()
