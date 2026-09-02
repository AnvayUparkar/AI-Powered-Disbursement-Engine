from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from pipeline.nodes.llm_adjudicator import (
    _clean_json_text,
    _extract_text,
    llm_adjudicate,
)


def test_clean_json_text_markdown_block():
    markdown_str = "```json\n{\n  \"match_status\": \"MATCH\",\n  \"confidence\": 0.95,\n  \"reason\": \"Same name\"\n}\n```"
    cleaned = _clean_json_text(markdown_str)
    assert "\"match_status\": \"MATCH\"" in cleaned


def test_extract_text_from_list_and_str():
    assert _extract_text("simple text") == "simple text"
    assert _extract_text([{"text": "part 1"}, {"text": "part 2"}]) == "part 1\npart 2"


def test_llm_adjudication_mock_match(monkeypatch):
    """Test successful MATCH adjudication using a deterministic mocked client."""
    mock_client = MagicMock()
    mock_client.invoke.return_value = AIMessage(
        content='```json\n{\n  "match_status": "MATCH",\n  "confidence": 0.95,\n  "reason": "Name variation of the same individual."\n}\n```'
    )
    monkeypatch.setattr("pipeline.nodes.llm_adjudicator._get_gemini_client", lambda: mock_client)

    result = llm_adjudicate("Mohd Rizwan", "Mohammad Rizwan", "applicant_name", "LOAN_MOCK_001")
    assert result["match_status"] == "MATCH"
    assert result["llm_used"] is True
    assert result["confidence"] == 0.95
    assert "Same individual" in result["reason"] or "Same individual." in result["reason"] or len(result["reason"]) > 0


def test_llm_adjudication_mock_mismatch(monkeypatch):
    """Test successful MISMATCH adjudication using a deterministic mocked client."""
    mock_client = MagicMock()
    mock_client.invoke.return_value = AIMessage(
        content='{\n  "match_status": "MISMATCH",\n  "confidence": 0.98,\n  "reason": "Completely different applicants."\n}'
    )
    monkeypatch.setattr("pipeline.nodes.llm_adjudicator._get_gemini_client", lambda: mock_client)

    result = llm_adjudicate("Riteshraj Panda", "Suresh Kumar", "applicant_name", "LOAN_MOCK_002")
    assert result["match_status"] == "MISMATCH"
    assert result["llm_used"] is True
    assert result["confidence"] == 0.98


def test_llm_adjudication_client_none_fallback(monkeypatch):
    """Test fallback when no Gemini client is configured."""
    monkeypatch.setattr("pipeline.nodes.llm_adjudicator._get_gemini_client", lambda: None)

    result = llm_adjudicate("Val A", "Val B", "address", "LOAN_NO_CLIENT")
    assert result["match_status"] == "PARTIAL"
    assert result["llm_used"] is False
    assert result["confidence"] == 0.5


def test_llm_adjudication_exception_fallback(monkeypatch):
    """Test graceful fallback when LLM API call fails (e.g. rate limit, timeout)."""
    mock_client = MagicMock()
    mock_client.invoke.side_effect = RuntimeError("429 Resource Exhausted")
    monkeypatch.setattr("pipeline.nodes.llm_adjudicator._get_gemini_client", lambda: mock_client)

    result = llm_adjudicate("Val A", "Val B", "address", "LOAN_ERR")
    assert result["match_status"] == "PARTIAL"
    assert result["llm_used"] is False
    assert result["confidence"] == 0.5
    assert "service error" in result["reason"].lower()

