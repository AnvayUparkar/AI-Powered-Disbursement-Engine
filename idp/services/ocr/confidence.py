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
    
    # 2. Repeated Character Noise (e.g. "aaaaa", preserving numeric zeros in financial amounts like 500000)
    REPEATED_CHARS_PATTERN = re.compile(r"([a-zA-Z])\1{4,}")
    
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

    # 7. OCR Artifact & Quality Patterns
    CAMEL_OR_GLUED_PATTERN = re.compile(r"[a-z][A-Z]")
    SANDWICHED_LOWERCASE_PATTERN = re.compile(r"[A-Z]{1,}[a-z]+[A-Z]+")
    SUSPICIOUS_INITIAL_PATTERN = re.compile(r"\b[bcdfghjklmnpqrstvwxz]{2}[a-z]{3,}\b")
    PUNCT_NOISE_PATTERN = re.compile(r"([,.;:!?_\-\/\\|]){2,}")
    DIGIT_INSIDE_WORD_PATTERN = re.compile(r"\b[A-Za-z]+[0-9]+[A-Za-z]+\b")
    VALID_INITIAL_CONSONANTS = (
        "th", "sh", "ch", "wh", "ph", "sc", "sk", "sl", "sm", "sn",
        "sp", "st", "sw", "tr", "pr", "br", "cr", "dr", "fr", "gr",
        "pl", "cl", "bl", "fl", "gl"
    )

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
        
        # 3. Remove leading single-letter noise before words (e.g., "s Brih" -> "Brih", "a Government" -> "Government")
        cleaned = re.sub(r"^[a-z]\s+(?=[A-Z])", "", cleaned)
        
        # 4. Remove standalone garbled tokens: TE, RAA, HOTRT, Signralid, HAR, etc.
        cleaned = re.sub(r"\b(TE|RAA|HOTRT|Signralid|HAR|Hin|Wuy|Nane|Empioyee|OSV)\b", "", cleaned)
        
        # 5. Remove Chinese/CJK characters and full-width punctuation entirely
        cleaned = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\uff00-\uffef]", "", cleaned)
        
        # 6. Remove Devanagari-to-Latin garbled patterns: "3TET", "TT3T", "3TR3", "334"
        # Matches: digits+letters+digits OR letters+digits+letters OR pure digits with letters mixed
        cleaned = re.sub(r"\b\d+[A-Z]{2,}\d*\b", "", cleaned)  # e.g., "3TET", "334"
        cleaned = re.sub(r"\b[A-Z]{2,}\d+\s?\d*\b", "", cleaned)  # e.g., "TT3T 3"
        
        # 7. Remove standalone short noise: single letters on their own
        cleaned = re.sub(r"\b[A-Z]\b(?!\w)", "", cleaned)  # Single uppercase letters: "R", "A" (preserve numbers)
        
        # 8. Remove random character sequences with mixed punctuation
        cleaned = re.sub(r"\b[a-z]{1,2}\s*[,\)\(]\s*[a-z0-9\s,\)\(]{5,}\b", "", cleaned)  # e.g., "ee , a fr ) s4 H4"
        
        # 9. Remove leading digit+slash patterns from fields like "9/MALE" -> "MALE", "Paf4/DOB" -> "DOB" (preserve dates)
        cleaned = re.sub(r"^[A-Za-z]*\d+/(?=[A-Za-z])", "", cleaned)
        
        # 10. Remove patterns like "RT 3HTET" or "3 34" (mixed letter-digit garbage)
        cleaned = re.sub(r"\b[A-Z]{1,2}\s+\d[A-Z]+\b", "", cleaned)

        # Clean up double spaces or dangling leading slashes
        cleaned = re.sub(r"^\s*/\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        
        # Remove leading/trailing garbage punctuation or symbols (preserve valid brackets/periods)
        cleaned = re.sub(r"^[^\w\s\(\[\{\#\$]+|[^\w\s\)\}\]\.\,\:\-\%]+$", "", cleaned, flags=re.UNICODE)

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
        
        # 5. Check for Chinese/CJK characters and full-width punctuation
        if re.search(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\uff00-\uffef]", cleaned):
            return True
        
        # 6. Check for specific garbage tokens seen in Aadhaar/PAN misreads
        garbage_tokens = r"\b(TE|RAA|HOTRT|Signralid|Brih|HAR(?!I)|Hin|Wuy|Nane|Empioyee|OSV)\b"
        if re.search(garbage_tokens, cleaned, re.IGNORECASE):
            return True
        
        # 7. Check for Devanagari misread patterns: "3TET", "TT3T", "334", "3 34"
        if re.search(r"\b\d+[A-Z]{2,}\d*\b", cleaned):  # "3TET", "334"
            return True
        if re.search(r"\b[A-Z]{2,}\d+\s?\d*\b", cleaned):  # "TT3T 3"
            return True
        
        # 8. Check for random character sequences with excessive punctuation
        if re.search(r"[a-z]{1,2}\s*[,\)\(]\s*[a-z0-9\s,\)\(]{8,}", cleaned):
            return True
        
        # 9. Standalone very short tokens (1-2 chars) that are just noise
        if total_len <= 2 and cleaned.isalpha() and cleaned.isupper():
            return True  # Single letters like "R", "A"
        
        # 10. Standalone numeric digits (valid data like years, amounts, codes, EMI)
        if cleaned.isdigit():
            return False  # Preserve numbers

        # 8. Check for pure consonant clusters without vowels in Latin tokens (e.g. "HRTRR")
        for match in self.PURE_CONSONANTS_PATTERN.finditer(cleaned):
            token = match.group(0).upper()
            if token not in self.COMMON_ACRONYMS and not self.IDENTIFIER_PATTERNS.search(cleaned):
                return True

        # 9. Statistical Latin Vowel-to-Consonant Ratio check for non-acronym words
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

        # 10. Check zero valid script characters
        if not self.VALID_SCRIPT_REGEX.search(cleaned):
            return True

        # 11. Excessive symbol ratio (> 40% non-alphanumeric symbols)
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
        if result.elements:
            result.average_confidence = (total_conf / len(result.elements))
            result.extraction_failed = False
        else:
            result.average_confidence = 0.0
            result.extraction_failed = True

        return result

    def compute_text_confidence(self, text: str, bbox: Optional[List[float]] = None) -> float:
        """
        Dynamically calculates confidence score (0.0 to 0.99) for extracted text
        based on linguistic, casing, character distribution, and OCR artifact heuristics.
        """
        if not text or not text.strip():
            return 0.0

        cleaned = text.strip()

        # 1. Immediate severe penalty if garbled or corrupted
        if self.is_garbled_text(cleaned):
            return 0.35

        score = 0.96  # High-confidence baseline for clean OCR text

        words = cleaned.split()
        penalties: float = 0.0

        for word in words:
            # Skip structured identifiers (PAN, IFSC, GSTIN) and known acronyms
            if self.IDENTIFIER_PATTERNS.search(word) or word.upper() in self.COMMON_ACRONYMS:
                continue

            # Check 1: Sandwiched lowercase in uppercase (e.g. "KAmLA", "NAMeS")
            if self.SANDWICHED_LOWERCASE_PATTERN.search(word):
                penalties += 0.18

            # Check 2: Glued words / casing jump without space (e.g. "NamePRAKASHKHATRI")
            if self.CAMEL_OR_GLUED_PATTERN.search(word):
                penalties += 0.15

            # Check 3: Suspicious initial double consonant from clipped leading letter (e.g. "pplicant", "ddress")
            if self.SUSPICIOUS_INITIAL_PATTERN.search(word) and not word.lower().startswith(self.VALID_INITIAL_CONSONANTS):
                penalties += 0.15

            # Check 4: Digit inside word (e.g. "L0AN", "F0RM")
            if self.DIGIT_INSIDE_WORD_PATTERN.search(word):
                penalties += 0.12

            # Check 5: Unusually long token without spaces/hyphens (likely run-on OCR concatenation)
            if len(word) > 22 and not any(c in word for c in "-_./\\@"):
                penalties += 0.10

        # Check 6: Consecutive punctuation / noise characters
        if self.PUNCT_NOISE_PATTERN.search(cleaned) and not any(k in cleaned for k in ["--", "..."]):
            penalties += 0.08

        # Check 7: Extremely short single-character tokens (unless digit or valid single letter)
        if len(cleaned) == 1 and cleaned not in "0123456789aAiI":
            penalties += 0.20

        # Check 8: Small/Degenerate BBox anomaly (when bbox coordinates are actually present)
        if bbox and len(bbox) >= 4 and any(c > 0 for c in bbox):
            w = abs(bbox[2] - bbox[0])
            h = abs(bbox[3] - bbox[1])
            if w <= 0.001 or h <= 0.001:
                penalties += 0.15

        # Clean bonus for standard titles / clean standard multi-word uppercase names
        if cleaned in {"Mr.", "Mrs.", "Ms.", "Dr.", "Shri", "Smt."} or (cleaned.isupper() and 2 <= len(words) <= 5 and penalties == 0.0):
            score = 0.98

        final_score = max(0.15, min(0.99, score - penalties))
        return round(final_score, 4)


_default_evaluator = OCRConfidenceEvaluator()


def compute_text_confidence(text: str, bbox: Optional[List[float]] = None) -> float:
    """Module-level helper: delegates to OCRConfidenceEvaluator.compute_text_confidence."""
    return _default_evaluator.compute_text_confidence(text, bbox=bbox)
