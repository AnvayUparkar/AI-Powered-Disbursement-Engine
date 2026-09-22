"""
LightOnOCR-2-1B wrapper for scanned document OCR.

Lazy-loaded, CPU-safe, timeout-protected OCR engine.
"""
from typing import Optional, List, Dict, Any, Tuple
import io
import re
import time
import threading
from PIL import Image
from pydantic import BaseModel, Field

from idp.models.ocr import OCRElement, OCRResult
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log
from idp.core.exceptions import OCRError


class LightOnOCRResult(BaseModel):
    """Raw result from LightOnOCR model."""
    text: str
    confidence: float
    bboxes: List[List[float]] = Field(default_factory=list)  # List of [x1, y1, x2, y2]
    inference_time_ms: float = 0.0
    quality_score: float = 0.0  # Custom quality metric


class LightOnOCREngine:
    """
    Lazy-loaded LightOnOCR-2-1B engine for scanned pages.
    
    Thread-safe singleton pattern with timeout protection.
    """
    
    _instance = None
    _lock = threading.Lock()
    _model = None
    _processor = None
    _model_loaded: bool = False
    _device: str = "cpu"
    
    def __new__(cls) -> "LightOnOCREngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        self.model_name: str = settings.LIGHTONOCR_MODEL
        self.device: str = settings.LIGHTONOCR_DEVICE
        self.timeout_seconds: int = settings.LIGHTONOCR_TIMEOUT_SECONDS
        self.lazy_load: bool = settings.LIGHTONOCR_LAZY_LOAD
        
        # Do NOT load model eagerly if lazy_load is True
        if not self.lazy_load and settings.LIGHTONOCR_ENABLED:
            self._load_model_internal()
    
    def _load_model_internal(self) -> None:
        """Load LightOnOCR model (thread-safe)."""
        if self._model_loaded:
            return
        
        with self._lock:
            if self._model_loaded:
                return
            
            try:
                logger.info(f"Loading LightOnOCR model: {self.model_name}")
                start = time.time()
                
                import torch
                
                # Determine device and precision
                if self.device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                else:
                    device = self.device
                
                dtype = torch.bfloat16 if (device == "cuda" and torch.cuda.is_bf16_supported()) else (torch.float16 if device == "cuda" else torch.float32)
                logger.info(f"LightOnOCR will run on: {device} (dtype={dtype})")
                
                # Dynamically resolve model and processor classes across transformers versions
                model_cls = None
                processor_cls = None
                
                # 1. Specialized LightOnOCR classes (transformers >= 5.0)
                try:
                    from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor
                    model_cls = LightOnOcrForConditionalGeneration
                    processor_cls = LightOnOcrProcessor
                except ImportError:
                    pass
                
                # 2. Vision2Seq from auto modeling module (transformers 4.x / 5.x)
                if model_cls is None:
                    try:
                        from transformers.models.auto.modeling_auto import AutoModelForVision2Seq
                        model_cls = AutoModelForVision2Seq
                    except ImportError:
                        pass
                
                # 3. ImageTextToText auto class
                if model_cls is None:
                    try:
                        from transformers.models.auto.modeling_auto import AutoModelForImageTextToText
                        model_cls = AutoModelForImageTextToText
                    except ImportError:
                        pass
                
                # 4. Seq2SeqLM or generic AutoModel
                if model_cls is None:
                    try:
                        from transformers import AutoModelForSeq2SeqLM
                        model_cls = AutoModelForSeq2SeqLM
                    except ImportError:
                        from transformers import AutoModel
                        model_cls = AutoModel
                
                if processor_cls is None:
                    from transformers import AutoProcessor
                    processor_cls = AutoProcessor
                
                logger.info(f"Using model class: {model_cls.__name__}, processor class: {processor_cls.__name__}")
                
                # Load processor and model with remote code trust
                self._processor = processor_cls.from_pretrained(
                    self.model_name,
                    trust_remote_code=True
                )
                
                self._model = model_cls.from_pretrained(
                    self.model_name,
                    torch_dtype=dtype,
                    trust_remote_code=True
                ).to(device)
                
                self._model.eval()  # Inference mode
                self._device = device
                self._model_loaded = True
                
                elapsed = time.time() - start
                logger.info(f"LightOnOCR loaded successfully in {elapsed:.2f}s on {device}")
                
            except Exception as e:
                logger.error(f"Failed to load LightOnOCR model: {e}")
                self._model_loaded = False
                self._model = None
                self._processor = None
                raise OCRError(f"LightOnOCR model loading failed: {e}")
    
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._model_loaded and self._model is not None
    
    def unload_model(self) -> None:
        """Unload model to free memory."""
        with self._lock:
            if self._model is not None:
                del self._model
                del self._processor
                self._model = None
                self._processor = None
                self._model_loaded = False
                
                # Force garbage collection
                import gc
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
                
                logger.info("LightOnOCR model unloaded from memory")
    
    def process_page(
        self,
        image_bytes: bytes,
        page_number: int,
        doc_id: str = "DOC"
    ) -> Optional[LightOnOCRResult]:
        """
        Run LightOnOCR inference on a single page.
        
        Args:
            image_bytes: Page image (PNG/JPEG)
            page_number: Page number (1-indexed)
            doc_id: Document ID for logging
        
        Returns:
            LightOnOCRResult or None on failure
        """
        if not settings.LIGHTONOCR_ENABLED:
            return None
        
        # Lazy load on first use
        if not self.is_loaded():
            try:
                self._load_model_internal()
            except Exception as e:
                logger.error(format_doc_log(doc_id, f"LightOnOCR model loading failed: {e}"))
                return None
        
        try:
            logger.info(format_doc_log(
                doc_id, 
                f"Running LightOnOCR on page {page_number}"
            ))
            
            start_time = time.time()
            
            # Convert bytes to PIL Image
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            
            # Prepare inputs
            inputs = None
            if hasattr(self._processor, "apply_chat_template"):
                try:
                    conversation = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "image": image},
                                {"type": "text", "text": "Extract the text from this document."}
                            ]
                        }
                    ]
                    inputs = self._processor.apply_chat_template(
                        conversation,
                        add_generation_prompt=True,
                        tokenize=True,
                        return_dict=True,
                        return_tensors="pt"
                    )
                    import torch
                    inputs = {k: v.to(device=self._device) if isinstance(v, torch.Tensor) else v for k, v in inputs.items()}
                except Exception:
                    inputs = None

            if inputs is None:
                inputs = self._processor(images=image, return_tensors="pt").to(self._device)
            
            # Run inference with timeout protection
            result = self._run_inference_with_timeout(inputs, doc_id)
            
            if result is None:
                return None
            
            inference_time = (time.time() - start_time) * 1000  # ms
            
            # Parse LightOnOCR output
            text = result.get("text", "")
            confidence = float(result.get("confidence", 0.0))
            bboxes = result.get("bboxes", [])
            
            quality_score = self._compute_quality_score(text, confidence, image_bytes)
            
            logger.info(format_doc_log(
                doc_id,
                f"LightOnOCR page {page_number}: {len(text)} chars, "
                f"conf={confidence:.2f}, quality={quality_score:.2f}, "
                f"time={inference_time:.0f}ms"
            ))
            
            return LightOnOCRResult(
                text=text,
                confidence=confidence,
                bboxes=bboxes,
                inference_time_ms=inference_time,
                quality_score=quality_score
            )
            
        except Exception as e:
            logger.error(format_doc_log(
                doc_id,
                f"LightOnOCR inference failed on page {page_number}: {e}"
            ))
            return None
    
    def _run_inference_with_timeout(
        self,
        inputs: Any,
        doc_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Run model inference with timeout protection.
        
        Uses threading to enforce timeout.
        """
        result_container: Dict[str, Any] = {"result": None, "error": None}
        
        def inference_worker() -> None:
            try:
                import torch
                with torch.no_grad():
                    outputs = self._model.generate(**inputs, max_new_tokens=1024)
                    
                    input_ids = inputs.get("input_ids") if isinstance(inputs, dict) else None
                    if input_ids is not None and hasattr(outputs, "shape") and len(outputs.shape) > 1 and outputs.shape[-1] > input_ids.shape[-1]:
                        generated_ids = outputs[0, input_ids.shape[1]:]
                    else:
                        generated_ids = outputs[0] if hasattr(outputs, "__getitem__") else outputs
                    
                    if hasattr(self._processor, "decode"):
                        text = self._processor.decode(generated_ids, skip_special_tokens=True)
                    elif hasattr(self._processor, "batch_decode"):
                        text = self._processor.batch_decode(outputs, skip_special_tokens=True)[0]
                    else:
                        text = str(generated_ids)
                    
                    # Extract bboxes if available (model-specific)
                    bboxes: List[List[float]] = []
                    confidence = 0.95  # Default if not provided by model
                    
                    result_container["result"] = {
                        "text": text,
                        "confidence": confidence,
                        "bboxes": bboxes
                    }
            except Exception as e:
                result_container["error"] = str(e)
        
        inference_thread = threading.Thread(target=inference_worker)
        inference_thread.daemon = True
        inference_thread.start()
        inference_thread.join(timeout=float(self.timeout_seconds))
        
        if inference_thread.is_alive():
            logger.error(format_doc_log(
                doc_id,
                f"LightOnOCR inference timeout ({self.timeout_seconds}s exceeded)"
            ))
            return None
        
        if result_container["error"]:
            logger.error(format_doc_log(
                doc_id,
                f"LightOnOCR inference error: {result_container['error']}"
            ))
            return None
        
        return result_container["result"]
    
    def _compute_quality_score(
        self,
        text: str,
        confidence: float,
        image_bytes: bytes
    ) -> float:
        """
        Compute quality score for LightOnOCR result.
        
        Quality heuristics:
        - Non-empty text with length gating: up to +0.3
        - Text depth (sufficient content for a full-page document): up to +0.2
        - Engine confidence: up to +0.3
        - Valid character ratio (scaled by length confidence): up to +0.2
        
        Returns: 0.0 to 1.0
        """
        cleaned = text.strip() if text else ""
        total_chars = len(cleaned)
        if total_chars == 0:
            return 0.0

        score = 0.0

        # Check 1: Non-empty & minimum usable length for a scanned document page
        # Scanned pages containing fewer than 5 characters are severe truncations/fragments
        if total_chars >= 10:
            score += 0.3
        elif total_chars >= 5:
            score += 0.2
        else:
            score += 0.05

        # Check 2: Content depth (reasonable document volume)
        if total_chars >= 25:
            score += 0.2
        elif total_chars >= 10:
            score += 0.1

        # Check 3: Confidence contribution (up to 0.3)
        score += min(max(confidence, 0.0), 1.0) * 0.3

        # Check 4: Valid character ratio scaled by sample length
        # A 1-2 char fragment cannot establish character distribution validity
        valid_chars = len(re.findall(r'[a-zA-Z0-9\s।,\.\'"\-/]', cleaned))
        valid_ratio = valid_chars / total_chars
        length_weight = min(1.0, total_chars / 5.0)
        score += valid_ratio * length_weight * 0.2

        return min(round(score, 4), 1.0)


# Singleton instance
_lightonocr_engine: Optional[LightOnOCREngine] = None


def get_lightonocr_engine() -> LightOnOCREngine:
    """Get singleton LightOnOCR engine instance."""
    global _lightonocr_engine
    if _lightonocr_engine is None:
        _lightonocr_engine = LightOnOCREngine()
    return _lightonocr_engine
