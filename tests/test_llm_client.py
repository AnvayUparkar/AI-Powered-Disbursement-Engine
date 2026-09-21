from unittest.mock import MagicMock, patch
import pytest

from pipeline.engines.llm_client import (
    clean_json_response,
    detect_provider,
    invoke_llm,
    invoke_llm_json,
    resolve_endpoint_url,
)


def test_detect_provider():
    # Gemini
    assert detect_provider("gemini-1.5-flash", "AQ.12345") == "gemini"
    assert detect_provider("google/gemini-pro", "AIzaSy...") == "gemini"
    assert detect_provider("gemini-2.0-flash", "sk-custom") == "gemini"

    # Groq
    assert detect_provider("llama-3.3-70b", "gsk_test123") == "groq"
    assert detect_provider("groq/llama3", "test") == "groq"
    assert detect_provider("llama-3.3-70b", "key", "https://api.groq.com/openai/v1") == "groq"

    # OpenRouter
    assert detect_provider("deepseek/deepseek-chat", "sk-or-v1-abc") == "openrouter"
    assert detect_provider("meta-llama/llama-3.3-70b", "test-key") == "openrouter"
    assert detect_provider("mistral", "test", "https://openrouter.ai/api/v1") == "openrouter"

    # Custom OpenAI
    assert detect_provider("gpt-4o", "sk-proj-xxx", "https://api.openai.com/v1") == "openai_compatible"


def test_resolve_endpoint_url():
    assert resolve_endpoint_url("groq") == "https://api.groq.com/openai/v1/chat/completions"
    assert resolve_endpoint_url("openrouter") == "https://openrouter.ai/api/v1/chat/completions"
    assert resolve_endpoint_url("openai_compatible") == "https://api.openai.com/v1/chat/completions"
    assert resolve_endpoint_url("custom", "http://localhost:11434/v1") == "http://localhost:11434/v1/chat/completions"
    assert resolve_endpoint_url("custom", "http://localhost:11434/v1/chat/completions") == "http://localhost:11434/v1/chat/completions"


def test_clean_json_response():
    assert clean_json_response("") == "{}"
    assert clean_json_response("```json\n{\"key\": \"value\"}\n```") == '{"key": "value"}'
    assert clean_json_response("Some prefix {\"status\": \"ok\"} suffix") == '{"status": "ok"}'
    assert clean_json_response("[1, 2, 3]") == "[1, 2, 3]"


def test_invoke_llm_http_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {"message": {"content": '{"name": "Asha Rao"}'}}
        ]
    }

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        result = invoke_llm_json(
            system_prompt="Extract JSON",
            user_prompt="Customer name: Asha Rao",
            model="deepseek/deepseek-chat",
            api_key="sk-or-v1-test",
            base_url="https://openrouter.ai/api/v1",
            temperature=0.0,
        )
        assert result == {"name": "Asha Rao"}
        assert mock_post.called


def test_invoke_llm_missing_api_key_raises():
    with pytest.raises(ValueError, match="LLM_API_KEY is not configured"):
        invoke_llm(
            system_prompt="sys",
            user_prompt="user",
            api_key="",
            model="gemini-1.5-flash",
        )


def test_detect_provider_base_url_overrides_gemini_heuristics():
    litellm_url = "https://litellm.internal.example.com"
    # Gateway aliases containing 'gemini' or Google-style keys must not bypass the base URL
    assert detect_provider("gemini-1.5-flash", "sk-litellm-key", litellm_url) == "openai_compatible"
    assert detect_provider("gemini-2.0-flash", "AIzaSyFake", litellm_url) == "openai_compatible"
    assert detect_provider("gpt-4o", "sk-litellm-key", litellm_url) == "openai_compatible"
    # Empty / whitespace-free absence of base URL keeps native Gemini routing
    assert detect_provider("gemini-1.5-flash", "sk-custom", "") == "gemini"
    assert detect_provider("gemini-1.5-flash", "sk-custom", None) == "gemini"


def test_invoke_llm_gemini_alias_with_base_url_uses_litellm_endpoint():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}

    with patch("httpx.Client.post", return_value=mock_response) as mock_post, \
            patch("pipeline.engines.llm_client._invoke_gemini") as mock_gemini:
        result = invoke_llm_json(
            system_prompt="sys",
            user_prompt="user",
            model="gemini-1.5-flash",
            api_key="sk-litellm-key",
            base_url="https://litellm.internal.example.com",
        )

    assert result == {"ok": True}
    mock_gemini.assert_not_called()
    assert mock_post.call_args.args[0] == "https://litellm.internal.example.com/chat/completions"
