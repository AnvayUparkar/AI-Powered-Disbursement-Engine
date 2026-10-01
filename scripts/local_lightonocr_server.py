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
            from transformers import AutoProcessor

            # Prefer dedicated LightOnOCR classes (transformers >= 5.0.0), then fallback to Auto classes
            processor_class = AutoProcessor
            try:
                from transformers import LightOnOcrProcessor as processor_class
            except ImportError:
                pass

            model_class = None
            try:
                from transformers import LightOnOcrForConditionalGeneration as model_class
            except ImportError:
                pass
            if model_class is None:
                try:
                    from transformers import AutoModelForImageTextToText as model_class
                except ImportError:
                    pass
            if model_class is None:
                try:
                    from transformers import AutoModelForVision2Seq as model_class
                except ImportError:
                    pass
            if model_class is None:
                from transformers import AutoModel as model_class
        except ImportError as e:
            raise RuntimeError(
                f"torch and transformers must be installed to run local inference: {e}"
            )

        use_cuda = torch.cuda.is_available()
        device = "cuda" if use_cuda else "cpu"
        dtype = torch.bfloat16 if use_cuda else torch.float32

        if use_cuda:
            print(f"Loading {MODEL_ID} on {device} ({dtype})...")
        else:
            threads = max(1, (os.cpu_count() or 4) - 1)
            torch.set_num_threads(threads)
            print(f"Loading {MODEL_ID} on CPU ({threads} threads)...")

        _processor = processor_class.from_pretrained(MODEL_ID, trust_remote_code=True)
        load_kwargs = {
            "device_map": device,
            "trust_remote_code": True,
        }
        if not use_cuda:
            load_kwargs["low_cpu_mem_usage"] = True

        try:
            _model = model_class.from_pretrained(
                MODEL_ID,
                torch_dtype=dtype,
                **load_kwargs,
            )
        except TypeError:
            _model = model_class.from_pretrained(
                MODEL_ID,
                dtype=dtype,
                **load_kwargs,
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
    # Extract base64 image data and text prompt
    image_bytes = None
    text_prompt = "Extract the text from this document."
    for msg in req.messages:
        if isinstance(msg.content, list):
            for part in msg.content:
                part_dict = (
                    part
                    if isinstance(part, dict)
                    else (
                        part.model_dump()
                        if hasattr(part, "model_dump")
                        else part.dict()
                    )
                )
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
                elif part_dict.get("type") == "text" and part_dict.get("text"):
                    text_prompt = part_dict.get("text")
        elif isinstance(msg.content, str) and msg.content.strip():
            text_prompt = msg.content.strip()

    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image_url payload found in messages.")

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        # Rescale overly large page scans to maintain acceptable inference latency
        max_dim = 1540
        if max(image.width, image.height) > max_dim:
            image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image format: {e}")

    model, processor = get_model_and_processor()

    content_list = [{"type": "image"}]
    if text_prompt and text_prompt.strip():
        content_list.append({"type": "text", "text": text_prompt.strip()})

    chat_messages = [
        {
            "role": "user",
            "content": content_list,
        }
    ]
    try:
        if hasattr(processor, "apply_chat_template"):
            prompt_str = processor.apply_chat_template(chat_messages, add_generation_prompt=True)
            inputs = processor(images=image, text=prompt_str, return_tensors="pt")
        else:
            inputs = processor(images=image, text=text_prompt or "Extract the text from this document.", return_tensors="pt")

        device = getattr(model, "device", None)
        if device is not None:
            inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}

        import torch
        with torch.no_grad():
            generated_ids = model.generate(**inputs, max_new_tokens=req.max_tokens or 2048)

        # When generate prefixes prompt input_ids, trim them before decoding
        if "input_ids" in inputs and hasattr(generated_ids, "shape") and hasattr(inputs["input_ids"], "shape") and generated_ids.shape[1] > inputs["input_ids"].shape[1]:
            prompt_len = inputs["input_ids"].shape[1]
            new_ids = generated_ids[:, prompt_len:]
        else:
            new_ids = generated_ids

        # Decode generated OCR text
        text = processor.batch_decode(new_ids, skip_special_tokens=True)[0].strip()
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Inference error: {e}")

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

