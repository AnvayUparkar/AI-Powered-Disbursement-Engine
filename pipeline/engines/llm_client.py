"""Unified Multi-Provider LLM Client.

Supports Google Gemini, OpenRouter, Groq, OpenAI, and any OpenAI-compatible endpoint
using standard configuration variables:
  - LLM_API_KEY: Provider API key
  - LLM_MODEL: Model name (e.g., 'gemini-1.5-flash', 'deepseek/deepseek-chat', 'llama-3.3-70b-versatile')
  - LLM_BASE_URL: Optional endpoint URL (auto-detected if omitted)
  - LLM_TEMPERATURE: Float temperature (default: 0.0)
  - LLM_MAX_TOKENS: Max tokens for completion (default: 2048)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
)

logger = logging.getLogger("disbursement_pipeline.llm_client")

# Default endpoint URLs for known providers
_OPENROUTER_DEFAULT_URL = "https://openrouter.ai/api/v1/chat/completions"
_GROQ_DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
_OPENAI_DEFAULT_URL = "https://api.openai.com/v1/chat/completions"


def detect_provider(
    model: str,
    api_key: str,
    base_url: Optional[str] = None,
) -> str:
    """Detects provider type ('gemini', 'groq', 'openrouter', or 'openai_compatible')."""
    model_str = (model or "").lower()
    key_str = api_key or ""
    url_str = (base_url or "").lower()

    if "groq.com" in url_str or key_str.startswith("gsk_") or model_str.startswith("groq/"):
        return "groq"

    if "openrouter.ai" in url_str or key_str.startswith("sk-or-"):
        return "openrouter"

    # An explicit base URL (e.g. a self-hosted LiteLLM proxy) always wins over
    # model/key heuristics, so gateway aliases like 'gemini-1.5-flash' are not
    # diverted to the native Gemini client.
    if url_str:
        return "openai_compatible"

    if key_str.startswith("AQ.") or key_str.startswith("AIza") or "gemini" in model_str:
        return "gemini"

    # If model has provider prefix like 'deepseek/deepseek-chat' default to openrouter
    if "/" in model:
        return "openrouter"

    return "openai_compatible"


def resolve_endpoint_url(
    provider: str,
    base_url: Optional[str] = None,
) -> str:
    """Resolves the full /chat/completions URL for HTTP-based providers."""
    if base_url and base_url.strip():
        url = base_url.strip().rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        return url

    if provider == "groq":
        return _GROQ_DEFAULT_URL
    if provider == "openrouter":
        return _OPENROUTER_DEFAULT_URL
    return _OPENAI_DEFAULT_URL


def clean_json_response(raw: str) -> str:
    """Extracts JSON substring from LLM response text, stripping markdown codeblocks and thinking tags."""
    if not raw:
        return "{}"
    text = raw.strip()

    # Strip reasoning/thinking tags (e.g., DeepSeek / Qwen models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Markdown json block
    match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Raw outer object or array
    match_braces = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match_braces:
        return match_braces.group(1).strip()

    return text


def invoke_llm(
    system_prompt: str,
    user_prompt: str,
    json_response: bool = False,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 45.0,
) -> str:
    """Invokes the configured LLM provider and returns the raw response text string."""
    effective_key = api_key if api_key is not None else LLM_API_KEY
    effective_model = model if model is not None else LLM_MODEL
    effective_base_url = base_url if base_url is not None else LLM_BASE_URL
    effective_temp = LLM_TEMPERATURE if temperature is None else temperature
    effective_tokens = max_tokens if max_tokens is not None else LLM_MAX_TOKENS

    if not effective_key or not effective_key.strip():
        raise ValueError("LLM_API_KEY is not configured")
    if not effective_model or not effective_model.strip():
        raise ValueError("LLM_MODEL is not configured")

    provider = detect_provider(effective_model, effective_key, effective_base_url)

    if provider == "gemini":
        return _invoke_gemini(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=effective_model,
            api_key=effective_key,
            temperature=effective_temp,
            max_tokens=effective_tokens,
            timeout=timeout,
        )

    return _invoke_http_openai_compatible(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        provider=provider,
        model=effective_model,
        api_key=effective_key,
        base_url=effective_base_url,
        temperature=effective_temp,
        max_tokens=effective_tokens,
        json_response=json_response,
        timeout=timeout,
    )


def _invoke_gemini(
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    """Executes call via langchain_google_genai."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    model_name = model.split("/")[-1] if "/" in model else model

    client = ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=temperature,
        max_output_tokens=max_tokens,
        max_retries=2,
        timeout=timeout,
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    ai_msg = client.invoke(messages)

    if isinstance(ai_msg.content, str):
        return ai_msg.content
    if isinstance(ai_msg.content, list):
        texts = []
        for item in ai_msg.content:
            if isinstance(item, dict) and "text" in item:
                texts.append(str(item["text"]))
            elif isinstance(item, str):
                texts.append(item)
            elif hasattr(item, "text"):
                texts.append(str(item.text))
        return "\n".join(texts)
    return str(ai_msg.content)


def _invoke_http_openai_compatible(
    system_prompt: str,
    user_prompt: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: Optional[str],
    temperature: float,
    max_tokens: int,
    json_response: bool,
    timeout: float,
) -> str:
    """Executes call to any OpenAI-compatible chat completions endpoint."""
    endpoint_url = resolve_endpoint_url(provider, base_url)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/AnvayUparkar/AI-Powered-Disbursement-Engine"
        headers["X-Title"] = "AI-Powered Disbursement Engine"

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if provider == "openrouter":
        payload["include_reasoning"] = False
    if json_response:
        payload["response_format"] = {"type": "json_object"}

    with httpx.Client(timeout=timeout) as client:
        response = client.post(endpoint_url, headers=headers, json=payload)
        response.raise_for_status()

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise ValueError(f"Empty choices in LLM response from {endpoint_url}: {data}")

    content = choices[0].get("message", {}).get("content", "")
    return str(content or "")


def invoke_llm_json(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Invokes LLM and returns parsed JSON dict."""
    raw_response = invoke_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        json_response=True,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    cleaned = clean_json_response(raw_response)
    if not cleaned:
        return {}
    return json.loads(cleaned)
