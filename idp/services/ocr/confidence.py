import re
import math
from typing import List, Set, Optional
from idp.models.ocr import OCRElement, OCRResult
from idp.core.config import settings


class OCRConfidenceEvaluator:
    """
    Production-grade script-aware evaluator for OCR element confidence scores,
    statistical noise detection, vowel/consonant distribution, and garbled text validation.
    """

    # 1. Garbage Punctuation Pattern
    GARBAGE_SYMBOL_PATTERN = re.compile(r"^[~`!@#$%^&*()_+={}\[\]|\\:;\"'<>,?\/]+$")
    
    # 2. Repeated Character Noise (e.g. "aaaaa")
    REPEATED_CHARS_PATTERN = re.compile(r"(.)\1{4,}")
    
    # 3. Corrupted Mathematical / Greek / Foreign Symbol Noise
    CORRUPTED_SYMBOL_NOISE = re.compile(r"[παβγδεζηθικλμνξοπρστυφχψω∫∑√∝∞∠∧∨∩∪≈≠≡≤≥ąęįųπ×]")
    
    # 4. Pure Consonant Clusters (e.g. "HRTRR", "RHR", "HTT")
    PURE_CONSONANTS_PATTERN = re.compile(r"\b[BCDFGHJKLMNPQRSTVWXYZbcdfghjklmnpqrstvwxyz]{3,}\b")
    
    # 5. Invalid English Consonant-Vowel N-grams from Indic OCR misreads (e.g. "RROR", "HRAR", "3QRR")
    INVALID_NGRAM_MISREADS = re.compile(r"\b(RROR|HRAR|HRTRR|RHR|HTT|3T9T3πT&T|3QRR|mąhil|3×ML|oalh|2alalehule|3ITETT|31CT|3HTETR)\b", re.IGNORECASE)

    # 6. Structured Financial & Identity Identifiers (PAN, IFSC, GSTIN)
    IDENTIFIER_PATTERNS = re.compile(
        r"\b("
        r"[A-Z]{5}\s?[0-9]{4}[A-Z]"  # Indian PAN (e.g. CFVPM7810Q)
        r"|[A-Z]{4}0[A-Z0-9]{6}"     # Indian IFSC (e.g. HDFC0001234)
        r"|\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}"  # Indian GSTIN
        r")\b",
        re.IGNORECASE
    )

    # Common financial, technical, and regulatory acronyms (whitelisted from consonant check)
    COMMON_ACRONYMS: Set[str] = {
        "HTML", "HTTP", "HTTPS", "PDF", "JSON", "KYC", "PAN", "VKYC", "IFSC", 
        "NEFT", "RTGS", "GST", "HDFC", "ICICI", "UTI", "UIDAI", "DPI", "OCR", 
        "VLM", "API", "XML", "S3", "URL", "ID", "DOB", "S/O", "D/O", "W/O", "VTC"
    }

    # Valid Unicode Script Character Ranges
    VALID_SCRIPT_REGEX = re.compile(r"[\u0900-\u097F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AFa-zA-Z0-9]")
    LETTER_REGEX = re.compile(r"[\u0900-\u097F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AFa-zA-Z]")
    LATIN_LETTER_REGEX = re.compile(r"[a-zA-Z]")
    LATIN_VOWEL_REGEX = re.compile(r"[aeiouyAEIOUY]")
    SYMBOL_REGEX = re.compile(r"[^a-zA-Z0-9\u0900-\u097F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF\s]")

    def __init__(self, threshold: float = settings.OCR_CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    @classmethod
    def clean_bilingual_label_noise(cls, text: str) -> str:
        """
        Sanitizes bilingual misreads on Indian Identity & Financial Documents (PAN, Aadhaar, Driving License),
        stripping out garbage English letter clusters resulting from Devanagari label misreads.
        """
        if not text or not text.strip():
            return text

        cleaned = text.strip()

        # 1. Strip leading noise prefixes before standard labels
        # e.g., "fua/Father's Name" -> "Father's Name", "f /Father's Name" -> "Father's Name"
        cleaned = re.sub(r"^[fF](ua)?\s*/\s*", "", cleaned)
        # e.g., "a/Date of Birth" -> "Date of Birth", "a/DateofBirth" -> "DateofBirth"
        cleaned = re.sub(r"^[aA]\s*/\s*(?=[Dd]ate|[sS]ignature|[dD][oO][bB])", "", cleaned)
        # e.g., "aT&/Signature" -> "Signature"
        cleaned = re.sub(r"^[aA][tT]&?\s*/\s*", "", cleaned)
        # e.g., "GR@/DOB" -> "DOB"
        cleaned = re.sub(r"^GR@\s*/?\s*", "", cleaned)

        # 2. Remove standalone English misread noise tokens for PAN & Aadhaar headers
        cleaned = re.sub(r"\b(FarHToT|3RRTO|HRAHRR|PA ROR)\b", "", cleaned, flags=re.IGNORECASE)

        # Clean up double spaces or dangling leading slashes
        cleaned = re.sub(r"^\s*/\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return cleaned

    def is_garbled_text(self, text: str) -> bool:
        """
        Determines whether text is garbled OCR noise using statistical, linguistic,
        and n-gram metrics while ensuring valid Devanagari, Marathi, Spanish,
        and English text are NOT falsely flagged.
        """
        if not text or not text.strip():
            return False

        cleaned = text.strip()
        total_len = len(cleaned)

        # 1. Check for corrupted math/greek/accented symbols
        if self.CORRUPTED_SYMBOL_NOISE.search(cleaned):
            return True

        # 2. Check for pure symbol noise
        if self.GARBAGE_SYMBOL_PATTERN.match(cleaned):
            return True

        # 3. Check for repeated character noise (e.g. "aaaaa")
        if self.REPEATED_CHARS_PATTERN.search(cleaned):
            return True

        # 4. Check for known Indic OCR misread n-grams
        if self.INVALID_NGRAM_MISREADS.search(cleaned):
            return True

        # 5. Check for pure consonant clusters without vowels in Latin tokens (e.g. "HRTRR")
        for match in self.PURE_CONSONANTS_PATTERN.finditer(cleaned):
            token = match.group(0).upper()
            if token not in self.COMMON_ACRONYMS and not self.IDENTIFIER_PATTERNS.search(cleaned):
                return True

        # 6. Statistical Latin Vowel-to-Consonant Ratio check for non-acronym words
        words = cleaned.split()
        for word in words:
            # Exempt structured financial/identity identifiers (PAN, IFSC, GSTIN) and alphanumeric tokens
            if self.IDENTIFIER_PATTERNS.search(cleaned) or self.IDENTIFIER_PATTERNS.search(word) or any(c.isdigit() for c in word):
                continue
            latin_letters = self.LATIN_LETTER_REGEX.findall(word)
            if len(latin_letters) >= 4:
                clean_token = "".join(latin_letters).upper()
                if clean_token not in self.COMMON_ACRONYMS:
                    vowels = len(self.LATIN_VOWEL_REGEX.findall(clean_token))
                    vowel_ratio = vowels / len(clean_token)
                    # If word has length >= 4 with < 15% vowels (e.g. "RROR"), flag as garbled
                    if vowel_ratio < 0.15:
                        return True

        # 7. Check zero valid script characters
        if not self.VALID_SCRIPT_REGEX.search(cleaned):
            return True

        # 8. Excessive symbol ratio (> 40% non-alphanumeric symbols)
        symbols_count = len(self.SYMBOL_REGEX.findall(cleaned))
        letters_count = len(self.LETTER_REGEX.findall(cleaned))
        if total_len > 4 and letters_count > 0:
            if (symbols_count / total_len) > 0.40:
                return True

        return False

    def evaluate_element(self, element: OCRElement, expected_script: Optional[str] = None) -> OCRElement:
        """Evaluate a single OCR element and mark if VLM inspection is required."""
        text = element.text.strip()

        # Check threshold
        if element.confidence < self.threshold:
            element.needs_vlm = True

        if len(text) > 0:
            if self.is_garbled_text(text):
                element.needs_vlm = True

            # Script mismatch check if expected_script is specified
            target_script = expected_script or element.metadata.get("expected_script")
            if target_script:
                from idp.services.ocr.script_detector import is_script_mismatch
                if is_script_mismatch(target_script, text):
                    element.needs_vlm = True

        return element

    def evaluate_result(self, result: OCRResult, expected_script: Optional[str] = None) -> OCRResult:
        """Evaluate aggregate OCR result for a page."""
        low_count = 0
        total_conf = 0.0

        for elem in result.elements:
            self.evaluate_element(elem, expected_script=expected_script)
            if elem.needs_vlm:
                low_count += 1
            total_conf += elem.confidence

        result.low_confidence_count = low_count
        result.total_elements = len(result.elements)
        result.average_confidence = (total_conf / len(result.elements)) if result.elements else 1.0

        return result

