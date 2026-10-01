"""
Unit tests validating that local_lightonocr_server works with LightOnOCREngine.
"""
import base64
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import pytest

from scripts.local_lightonocr_server import app
from idp.services.ocr.lightonocr_engine import LightOnOCREngine, LightOnOCRResult


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_models_endpoint(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert len(response.json()["data"]) >= 1


def test_chat_completions_mock_inference(client):
    # Mock get_model_and_processor to avoid downloading weights in unit tests
    mock_model = MagicMock()
    mock_model.device = "cpu"
    mock_processor = MagicMock()
    mock_processor.apply_chat_template.return_value = "Extract the text from this document."
    mock_processor.return_value = {"input_ids": [1, 2, 3]}
    mock_processor.batch_decode.return_value = ["Extracted Document Text Sample"]

    with patch("scripts.local_lightonocr_server.get_model_and_processor", return_value=(mock_model, mock_processor)):
        import io
        from PIL import Image
        img = Image.new("RGB", (10, 10), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        payload = {
            "model": "lightonai/LightOnOCR-2-1B",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": "Extract the text from this document."}
                    ]
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.0
        }

        resp = client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        assert data["choices"][0]["message"]["content"] == "Extracted Document Text Sample"
