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
    hard_fail_reason: Optional[str] = None  # "truncated" | "repetition" | "empty"


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
    
    @staticmethod
    def _prepare_image(image: Image.Image) -> Image.Image:
        """
        Normalize input image for LightOnOCR:
        - Ensure RGB mode
        - Downscale so longest edge <= LIGHTONOCR_MAX_EDGE_PX using LANCZOS
        - Never upscale
        """
        if image.mode != "RGB":
            image = image.convert("RGB")
        max_edge = getattr(settings, "LIGHTONOCR_MAX_EDGE_PX", 1540)
        w, h = image.size
        if max(w, h) > max_edge:
            image = image.copy()
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return image

    @staticmethod
    def _compute_ink_ratio(image: Optional[Image.Image]) -> float:
        """Estimate ink ratio (fraction of dark foreground pixels) on page."""
        if image is None:
            return 0.05
        try:
            import numpy as np
            gray = np.array(image.convert("L"))
            ink_pixels = np.sum(gray < 200)
            total_pixels = gray.size
            return float(ink_pixels / max(1, total_pixels))
        except Exception:
            return 0.05

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
            
            # Convert bytes to PIL Image and normalize
            raw_image = Image.open(io.BytesIO(image_bytes))
            image = self._prepare_image(raw_image)
            
            # Prepare inputs - image-only user turn (no text prefix prompt)
            inputs = None
            if hasattr(self._processor, "apply_chat_template"):
                try:
                    conversation = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "image": image}
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
            result = self._run_inference_with_timeout(inputs, doc_id, image=image)
            
            if result is None:
                return None
            
            inference_time = (time.time() - start_time) * 1000  # ms
            
            # Parse LightOnOCR output
            text = result.get("text", "")
            confidence = float(result.get("confidence", 0.0))
            bboxes = result.get("bboxes", [])
            hard_fail_reason = result.get("hard_fail_reason")
            ink_ratio = result.get("ink_ratio")
            
            quality_score = self._compute_quality_score(
                text=text,
                confidence=confidence,
                image=image,
                ink_ratio=ink_ratio
            )
            
            logger.info(format_doc_log(
                doc_id,
                f"LightOnOCR page {page_number}: {len(text)} chars, "
                f"conf={confidence:.2f}, quality={quality_score:.2f}, "
                f"hard_fail={hard_fail_reason}, time={inference_time:.0f}ms"
            ))
            
            return LightOnOCRResult(
                text=text,
                confidence=confidence,
                bboxes=bboxes,
                inference_time_ms=inference_time,
                quality_score=quality_score,
                hard_fail_reason=hard_fail_reason
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
        doc_id: str,
        image: Optional[Image.Image] = None
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
                    max_new_tokens = getattr(settings, "LIGHTONOCR_MAX_NEW_TOKENS", 4096)
                    gen_kwargs: Dict[str, Any] = {
                        "max_new_tokens": max_new_tokens,
                        "output_scores": True,
                        "return_dict_in_generate": True,
                    }
                    if getattr(settings, "LIGHTONOCR_DETERMINISTIC", False):
                        gen_kwargs["do_sample"] = False
                    else:
                        gen_kwargs["do_sample"] = True
                        gen_kwargs["temperature"] = 0.2
                        gen_kwargs["top_p"] = 0.9

                    outputs = self._model.generate(**inputs, **gen_kwargs)
                    
                    input_ids = inputs.get("input_ids") if isinstance(inputs, dict) else None
                    if hasattr(outputs, "sequences"):
                        seq = outputs.sequences
                        if input_ids is not None and len(seq.shape) > 1 and seq.shape[-1] > input_ids.shape[-1]:
                            generated_ids = seq[0, input_ids.shape[1]:]
                        else:
                            generated_ids = seq[0]
                    else:
                        if input_ids is not None and hasattr(outputs, "shape") and len(outputs.shape) > 1 and outputs.shape[-1] > input_ids.shape[-1]:
                            generated_ids = outputs[0, input_ids.shape[1]:]
                        else:
                            generated_ids = outputs[0] if hasattr(outputs, "__getitem__") else outputs
                    
                    num_generated_tokens = len(generated_ids) if hasattr(generated_ids, "__len__") else 0

                    if hasattr(self._processor, "decode"):
                        text = self._processor.decode(generated_ids, skip_special_tokens=True)
                    elif hasattr(self._processor, "batch_decode"):
                        text = self._processor.batch_decode(outputs, skip_special_tokens=True)[0]
                    else:
                        text = str(generated_ids)
                    
                    # Compute real confidence from token probabilities
                    conf_mean = 0.95
                    conf_p05 = 0.95
                    try:
                        if hasattr(self._model, "compute_transition_scores") and hasattr(outputs, "scores") and outputs.scores:
                            import numpy as np
                            seq_to_score = outputs.sequences if hasattr(outputs, "sequences") else outputs
                            transition_scores = self._model.compute_transition_scores(
                                seq_to_score, outputs.scores, normalize_logits=True
                            )
                            probs = torch.exp(transition_scores[0]).cpu().float().numpy()
                            probs = np.clip(probs, 0.0, 1.0)
                            if len(probs) > 0:
                                conf_mean = float(np.mean(probs))
                                if len(probs) >= 8:
                                    window_means = [float(np.mean(probs[i:i+8])) for i in range(len(probs) - 7)]
                                    conf_p05 = float(np.percentile(window_means, 5))
                                else:
                                    conf_p05 = float(np.percentile(probs, 5))
                    except Exception as score_err:
                        logger.debug(format_doc_log(doc_id, f"Transition score calculation fallback: {score_err}"))

                    # Hard-fail checks
                    hard_fail_reason = None

                    # 1. Truncation
                    if num_generated_tokens >= max_new_tokens:
                        hard_fail_reason = "truncated"

                    # 2. Repetition
                    words = text.split()
                    ngram_size = getattr(settings, "LIGHTONOCR_REPETITION_NGRAM", 6)
                    max_reps = getattr(settings, "LIGHTONOCR_REPETITION_MAX", 5)
                    if hard_fail_reason is None and len(words) >= ngram_size:
                        from collections import Counter
                        ngrams = [tuple(words[i:i+ngram_size]) for i in range(len(words) - ngram_size + 1)]
                        counts = Counter(ngrams)
                        if any(c >= max_reps for c in counts.values()):
                            hard_fail_reason = "repetition"

                    if hard_fail_reason is None and len(text) >= 100:
                        import zlib
                        compressed = zlib.compress(text.encode("utf-8", errors="ignore"))
                        comp_ratio = len(compressed) / max(1, len(text.encode("utf-8", errors="ignore")))
                        if comp_ratio < 0.15:
                            hard_fail_reason = "repetition"

                    # 3. Empty on high ink page
                    non_ws_chars = len(re.sub(r"\s+", "", text))
                    ink_ratio = self._compute_ink_ratio(image) if image is not None else 0.05
                    if hard_fail_reason is None and non_ws_chars < 5 and ink_ratio > 0.02:
                        hard_fail_reason = "empty"

                    bboxes: List[List[float]] = []
                    result_container["result"] = {
                        "text": text,
                        "confidence": float(conf_p05),
                        "conf_mean": float(conf_mean),
                        "bboxes": bboxes,
                        "hard_fail_reason": hard_fail_reason,
                        "ink_ratio": ink_ratio,
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
        text: Optional[str],
        confidence: float,
        image: Optional[Image.Image] = None,
        ink_ratio: Optional[float] = None
    ) -> float:
        """
        Compute quality score for LightOnOCR result:
        quality = conf_p05 * (1 - garble_ratio) * coverage_factor
        coverage_factor = min(1, chars_out / expected_chars(ink_ratio, page_area))
        where expected_chars = 0.6 * ink_pixels / avg_glyph_area.
        garble_ratio from OCRConfidenceEvaluator.is_garbled_text over lines.
        """
        if not text or not text.strip():
            return 0.0

        cleaned = text.strip()
        chars_out = len(cleaned)
        if chars_out == 0:
            return 0.0

        # Garble ratio from lines
        lines = [l.strip() for l in cleaned.split("\n") if l.strip()]
        if lines:
            from idp.services.ocr.confidence import OCRConfidenceEvaluator
            evaluator = OCRConfidenceEvaluator()
            garbled_count = sum(1 for l in lines if evaluator.is_garbled_text(l))
            garble_ratio = garbled_count / len(lines)
        else:
            garble_ratio = 0.0

        # Page area and ink pixels
        if image is not None:
            w, h = image.size
            page_area = float(w * h)
            if ink_ratio is None:
                ink_ratio = self._compute_ink_ratio(image)
            ink_pixels = ink_ratio * page_area
        else:
            page_area = 1500.0 * 2000.0
            if ink_ratio is None:
                ink_ratio = 0.05
            ink_pixels = ink_ratio * page_area

        avg_glyph_area = 250.0  # approximate glyph area in pixels at ~150-200 DPI
        expected_chars = max(10.0, (0.6 * ink_pixels) / avg_glyph_area)
        coverage_factor = min(1.0, chars_out / expected_chars)

        conf_p05 = min(max(confidence, 0.0), 1.0)
        quality = conf_p05 * (1.0 - garble_ratio) * coverage_factor
        return min(max(round(float(quality), 4), 0.0), 1.0)


# Singleton instance
_lightonocr_engine: Optional[LightOnOCREngine] = None


def get_lightonocr_engine() -> LightOnOCREngine:
    """Get singleton LightOnOCR engine instance."""
    global _lightonocr_engine
    if _lightonocr_engine is None:
        _lightonocr_engine = LightOnOCREngine()
    return _lightonocr_engine
