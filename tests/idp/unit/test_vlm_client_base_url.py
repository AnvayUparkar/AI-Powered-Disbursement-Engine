from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idp.models.ocr import OCRElement
from idp.services.vlm.client import VLMClient

GATEWAY = "https://litellm.internal.example.com"


def _element() -> OCRElement:
    return OCRElement(
        id="elem-1",
        text="Original OCR Text",
        bbox=[10, 10, 50, 20],
        confidence=0.5,
        needs_vlm=True,
        page_number=1,
    )


def _ok_response() -> MagicMock:
    res = MagicMock()
    res.status_code = 200
    res.json.return_value = {
        "choices": [{"message": {"content": '{"text": "Corrected", "confidence": 0.88, "verified": true}'}}]
    }
    return res


def test_endpoint_defaults_to_openai_without_base_url():
    client = VLMClient(provider="openai", api_key="k", base_url=None)
    assert client._openai_endpoint_url() == "https://api.openai.com/v1/chat/completions"


@pytest.mark.parametrize("base_url", [GATEWAY, GATEWAY + "/", GATEWAY + "/chat/completions", "  " + GATEWAY + "  "])
def test_endpoint_uses_base_url_and_appends_path_once(base_url):
    client = VLMClient(provider="openai", api_key="k", base_url=base_url)
    assert client._openai_endpoint_url() == f"{GATEWAY}/chat/completions"


def test_blank_base_url_treated_as_unset():
    client = VLMClient(provider="openai", api_key="k", base_url="   ")
    assert client.base_url is None
    assert client._openai_endpoint_url() == "https://api.openai.com/v1/chat/completions"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_base_url_routes_any_provider_through_gateway(provider):
    client = VLMClient(provider=provider, model="gemini-1.5-flash", api_key="sk-litellm", base_url=GATEWAY)
    post = AsyncMock(return_value=_ok_response())
    with patch("httpx.AsyncClient.post", post):
        res = await client.analyze_region(b"png-bytes", _element(), doc_id="DOC-1")

    assert post.call_args.args[0] == f"{GATEWAY}/chat/completions"
    assert post.call_args.kwargs["json"]["model"] == "gemini-1.5-flash"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-litellm"
    assert res.text == "Corrected"
    assert res.confidence == 0.88


@pytest.mark.asyncio
async def test_mock_provider_ignores_base_url():
    client = VLMClient(provider="mock", api_key="sk-litellm", base_url=GATEWAY)
    post = AsyncMock()
    with patch("httpx.AsyncClient.post", post):
        res = await client.analyze_region(b"png-bytes", _element(), doc_id="DOC-2")

    post.assert_not_called()
    assert res.source == "vlm_corrected"


@pytest.mark.asyncio
async def test_gateway_error_falls_back_to_mock_without_raising():
    client = VLMClient(provider="openai", api_key="sk-litellm", base_url=GATEWAY)
    err = MagicMock()
    err.status_code = 502
    err.text = "bad gateway"
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=err)):
        res = await client.analyze_region(b"png-bytes", _element(), doc_id="DOC-3")

    assert res.ocr_original == "Original OCR Text"
    assert res.verified is True
