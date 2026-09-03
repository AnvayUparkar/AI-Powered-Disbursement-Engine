import time
import uuid
from typing import Dict, List, Optional, Union, Tuple, Any
from pydantic import BaseModel
from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.rapidocr_engine import RapidOCREngine
from idp.services.ocr.script_detector import ScriptDetector, ScriptCategory, ScriptDetectionResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator
from idp.core.config import settings
from idp.core.logging import logger, format_doc_log
from idp.utils.masking import mask_sensitive_pii


class OCRRoutingDecision(BaseModel):
    """Structured OCR routing decision object."""
    language: str
    script: str
    engine: str
    model_profile: str  # 'english', 'devanagari', 'multilingual', 'fallback'
    routing_reason: str


class OCRModelRouter:
    """
    Production-grade Script-Aware OCR Model Router supporting document-type hints,
    two-stage script detection, lazy model loading, and PII-masked observability logging.
    
    CRITICAL: For English-only documents (Bank Statements, PAN, Agreements), uses existing English PP-OCRv6.
    """

    def __init__(self, default_engine: Optional[RapidOCREngine] = None):
        self.default_engine = default_engine or RapidOCREngine()
        self.detector = ScriptDetector()
        self.evaluator = OCRConfidenceEvaluator()
        
        # Cache for lazy-loaded multilingual engine instances
        self._engine_cache: Dict[str, Any] = {}

    def resolve_routing_decision(
        self,
        doc_type_hint: Optional[str] = None,
        preview_text: Optional[str] = None
    ) -> OCRRoutingDecision:
        """
        Determines the optimal OCRRoutingDecision based on document type hints
        and/or preview script analysis.
        """
        hint_profile = None
        hint_script = None

        if doc_type_hint:
            normalized_hint = doc_type_hint.lower().replace(" ", "_")
            if any(k in normalized_hint for k in ["aadhaar", "aadhar", "adhar"]):
                hint_script = "devanagari"
                hint_profile = "devanagari"
            else:
                for profile_key, profile_info in settings.DOCUMENT_OCR_PROFILES.items():
                    if profile_key in normalized_hint:
                        hint_script = profile_info.get("preferred_script")
                        hint_profile = "devanagari" if hint_script == "devanagari" else "english"
                        break


        # Preview text script detection
        script_res: Optional[ScriptDetectionResult] = None
        if preview_text:
            script_res = self.detector.detect_script(preview_text)

        # 1. Devanagari detected or hinted
        if (script_res and script_res.primary_script in ["devanagari", "mixed"]) or (hint_script == "devanagari" and not (script_res and script_res.primary_script == "latin")):
            if settings.DEVANAGARI_OCR_ENABLED:
                return OCRRoutingDecision(
                    language="hi",
                    script=script_res.primary_script if script_res else "devanagari",
                    engine="rapidocr",
                    model_profile="devanagari",
                    routing_reason="devanagari_detected_or_hinted"
                )

        # 2. English / Latin default (for Bank statements, PAN, Loan agreements)
        return OCRRoutingDecision(
            language="en",
            script=script_res.primary_script if script_res else "latin",
            engine="rapidocr",
            model_profile="english",
            routing_reason="english_latin_default"
        )

    def select_engine_for_decision(self, decision: OCRRoutingDecision) -> Tuple[Any, str]:
        """
        Selects the engine instance corresponding to the routing decision.
        """
        if decision.model_profile == "english":
            return self.default_engine, "rapidocr_ppocrv6_english"

        if decision.model_profile == "devanagari" and settings.DEVANAGARI_OCR_ENABLED:
            engine = self._get_or_create_engine("devanagari", lang="hi")
            if engine:
                return engine, "rapidocr_devanagari"

        if decision.model_profile == "japanese" and settings.JAPANESE_OCR_ENABLED:
            engine = self._get_or_create_engine("japanese", lang="japan")
            if engine:
                return engine, "rapidocr_japanese"

        # Fallback to English PP-OCRv6 engine
        return self.default_engine, "rapidocr_ppocrv6_english_fallback"

    def _get_or_create_engine(self, cache_key: str, lang: str) -> Optional[Any]:
        """
        Lazy loads and caches specialized multilingual RapidOCR engine instances.
        """
        if cache_key in self._engine_cache:
            return self._engine_cache[cache_key]

        try:
            logger.info(f"Lazy loading multilingual OCR engine for route '{cache_key}' (lang='{lang}')...")
            
            class SpecializedRapidOCREngine(RapidOCREngine):
                def __init__(self, language: str):
                    super().__init__()
                    self.language = language

                def _get_engine(self):
                    if self._rapid_ocr is None:
                        if self.language in ["hi", "devanagari"]:
                            try:
                                from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType
                                self._rapid_ocr = RapidOCR(params={
                                    'Rec.ocr_version': OCRVersion.PPOCRV5,
                                    'Rec.lang_type': LangRec.DEVANAGARI,
                                    'Rec.model_type': ModelType.MOBILE
                                })
                                logger.info("RapidOCR Devanagari PP-OCRv5 engine initialized successfully.")
                            except Exception as e:
                                logger.warning(f"RapidOCR Devanagari init fallback: {e}")
                                from rapidocr_onnxruntime import RapidOCR as RapidOCROnnx
                                self._rapid_ocr = RapidOCROnnx()
                        else:
                            try:
                                from rapidocr_onnxruntime import RapidOCR as RapidOCROnnx
                                self._rapid_ocr = RapidOCROnnx()
                                logger.info(f"RapidOCR engine ({self.language}) initialized successfully.")
                            except Exception as e:
                                logger.warning(f"RapidOCR ({self.language}) init fallback: {e}")
                                self._rapid_ocr = "MOCK"
                    return self._rapid_ocr

            specialized_engine = SpecializedRapidOCREngine(language=lang)
            self._engine_cache[cache_key] = specialized_engine
            return specialized_engine

        except Exception as e:
            logger.warning(f"Failed to lazy load specialized OCR engine '{cache_key}': {e}. Using fallback.")
            return None

    def process_page(
        self,
        image_input: Union[str, bytes],
        page_number: int = 1,
        doc_id: str = "DOC",
        doc_type_hint: Optional[str] = None,
        preview_text: Optional[str] = None,
        skip_preprocessing: Optional[bool] = None
    ) -> OCRResult:
        """
        Processes a page using the Script-Aware Model Router:
        - If document type hint indicates English (e.g. Bank Statement, PAN) or script detection finds Latin text:
          uses existing English PP-OCRv6 engine with ZERO extra overhead.
        - If document type hint or script detection indicates Devanagari / Mixed (e.g. Aadhaar):
          uses Devanagari + English engine (lang='hi').
        """
        decision = self.resolve_routing_decision(doc_type_hint=doc_type_hint, preview_text=preview_text)
        selected_engine, model_name = self.select_engine_for_decision(decision)

        # Fast-path determination: skip heavy CV2 preprocessing (deskewing/rotation) for known clean English docs
        if skip_preprocessing is None:
            is_english_hint = decision.model_profile == "english" and doc_type_hint is not None
            skip_preprocessing = is_english_hint

        logger.info(
            format_doc_log(
                doc_id,
                f"Page {page_number} OCR Routing: profile='{decision.model_profile}', engine='{model_name}', "
                f"skip_preprocessing={skip_preprocessing}, reason='{decision.routing_reason}'"
            )
        )

        ocr_res: OCRResult = selected_engine.process(
            image_input, page_number=page_number, doc_id=doc_id, skip_preprocessing=skip_preprocessing
        )

        # Step 2: Evaluate script & quality on extracted OCR elements if not fast-pathed
        if ocr_res.elements:
            # Fast-path for confident English documents: skip secondary script re-detection if no garbled indicators
            is_fast_path_english = (
                decision.model_profile == "english" and 
                doc_type_hint and 
                ocr_res.average_confidence >= settings.OCR_CONFIDENCE_THRESHOLD
            )

            if not is_fast_path_english:
                sample_text = " ".join([e.text for e in ocr_res.elements if e.text])
                script_res = self.detector.detect_script(sample_text)
                
                # Check for region fallback / garbled OCR misreads if initial pass ran on English
                if decision.model_profile == "english" and settings.DEVANAGARI_OCR_ENABLED:
                    has_garbled = any(self.evaluator.is_garbled_text(e.text) for e in ocr_res.elements) or ocr_res.low_confidence_count > 0
                    if script_res.primary_script in ["devanagari", "mixed"] or has_garbled:
                        # Switch route to Devanagari engine and re-process
                        logger.info(format_doc_log(doc_id, f"Page {page_number}: Switching route to Devanagari engine due to detected Devanagari/garbled text"))
                        dev_decision = OCRRoutingDecision(
                            language="hi",
                            script=script_res.primary_script,
                            engine="rapidocr",
                            model_profile="devanagari",
                            routing_reason="script_detection_override"
                        )
                        dev_engine, dev_model_name = self.select_engine_for_decision(dev_decision)
                        ocr_res = dev_engine.process(image_input, page_number=page_number, doc_id=doc_id, skip_preprocessing=False)
                        model_name = dev_model_name
                        decision = dev_decision

                # Attach script & model metadata to elements
                for elem in ocr_res.elements:
                    if elem.metadata is None:
                        elem.metadata = {}
                    elem.metadata["ocr_model"] = model_name
                    elem.metadata["script"] = script_res.primary_script
            else:
                for elem in ocr_res.elements:
                    if elem.metadata is None:
                        elem.metadata = {}
                    elem.metadata["ocr_model"] = model_name
                    elem.metadata["script"] = "latin"


        # Re-evaluate with script-aware confidence evaluator
        ocr_res = self.evaluator.evaluate_result(ocr_res)

        logger.info(
            format_doc_log(
                doc_id,
                f"Page {page_number} OCR Summary: profile='{decision.model_profile}', model='{model_name}', "
                f"elements={len(ocr_res.elements)}, low_conf={ocr_res.low_confidence_count}, "
                f"avg_conf={ocr_res.average_confidence:.2f}"
            )
        )
        return ocr_res
