"""
Standalone local runner for LightOnOCR-2-1B.
Exposes an OpenAI-compatible /v1/chat/completions endpoint on localhost,
allowing your local machine to run LightOnOCR without LiteLLM or access to HDB servers.
"""
import base64
import io
import os
import sys
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image
import uvicorn

app = FastAPI(title="Local LightOnOCR Mock/Server")

_model = None
_processor = None
MODEL_ID = os.getenv("LIGHTONOCR_MODEL", "lightonai/LightOnOCR-2-1B")


def get_model_and_processor():
    global _model, _processor
    if _model is None or _processor is None:
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForVision2Seq
        except ImportError:
            raise RuntimeError(
                "torch and transformers must be installed to run local inference: "
                "pip install torch transformers pillow"
            )

        print(f"Loading {MODEL_ID} on CPU...")
        _processor = AutoProcessor.from_pretrained(MODEL_ID)
        _model = AutoModelForVision2Seq.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float32,
            device_map="cpu",
            low_cpu_mem_usage=True,
        )
        print("Model loaded successfully.")
    return _model, _processor


class ImageUrl(BaseModel):
    url: str


class ContentPart(BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[ImageUrl] = None


class Message(BaseModel):
    role: str
    content: Any  # List[ContentPart] or str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = 0.0


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    # Extract base64 image data
    image_bytes = None
    for msg in req.messages:
        if isinstance(msg.content, list):
            for part in msg.content:
                part_dict = part if isinstance(part, dict) else part.dict()
                if part_dict.get("type") == "image_url":
                    img_url_data = part_dict.get("image_url", {})
                    url_str = (
                        img_url_data.get("url")
                        if isinstance(img_url_data, dict)
                        else getattr(img_url_data, "url", "")
                    )
                    if "," in url_str:
                        base64_data = url_str.split(",", 1)[1]
                        image_bytes = base64.b64decode(base64_data)
                        break

    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image_url payload found in messages.")

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image format: {e}")

    model, processor = get_model_and_processor()
    inputs = processor(images=image, return_tensors="pt")

    import torch
    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=req.max_tokens or 2048)

    # Decode generated OCR text
    text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

    return {
        "id": "chatcmpl-local-lightonocr",
        "object": "chat.completion",
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": len(text.split()),
            "total_tokens": 100 + len(text.split()),
        },
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8002"))
    uvicorn.run(app, host="127.0.0.1", port=port)
