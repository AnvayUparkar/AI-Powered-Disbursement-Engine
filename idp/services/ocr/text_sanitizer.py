"""
Production-grade OCR Text Sanitization Pipeline for IDP Systems.

This module provides a multi-stage, configurable text cleaning pipeline
specifically designed for Indian government documents (Aadhaar, PAN, DL, etc.)
with extensive OCR noise patterns from bilingual (English/Devanagari) documents.

Author: IDP Engineering Team
"""

import re
from typing import List, Tuple, Optional, Dict, Any
from enum import Enum
from dataclasses import dataclass
from idp.core.logging import logger


class CleaningStage(str, Enum):
    """Text cleaning pipeline stages for audit and debugging."""
    RAW = "raw"
    UNICODE_NORMALIZATION = "unicode_normalization"
    BILINGUAL_NOISE = "bilingual_noise"
    STRUCTURAL_PATTERNS = "structural_patterns"
    SEMANTIC_VALIDATION = "semantic_validation"
    FINAL = "final"


@dataclass
class SanitizationResult:
    """Result of text sanitization with full audit trail."""
    original: str
    cleaned: str
    removed_tokens: List[str]
    applied_rules: List[str]
    confidence_score: float
    is_valid: bool
    stage_outputs: Dict[str, str]  # Intermediate outputs for debugging


class TextSanitizer:
    """
    Production-grade text sanitization engine for OCR output.
    
    Features:
    - Multi-stage pipeline with audit trail
    - Configurable cleaning rules
    - Document-type specific cleaning
    - Performance optimized with compiled patterns
    - Full observability and metrics
    """
    
    # Pre-compiled regex patterns for performance
    UNICODE_NOISE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\uff00-\uffef]")
    CORRUPTED_SYMBOLS = re.compile(r"[παβγδεζηθικλμνξοπρστυφχψω∫∑√∝∞∠∧∨∩∪≈≠≡≤≥ąęįųπ×]")
    REPEATED_CHARS = re.compile(r"(.)\1{4,}")
    GARBAGE_PUNCTUATION = re.compile(r"^[~`!@#$%^&*()_+={}\[\]|\\:;\"'<>,?\/]+$")
    
    # Bilingual label noise patterns (Devanagari → Latin misreads)
    # ONLY REMOVE CLEAR GARBAGE PREFIXES, NOT CONTENT
    BILINGUAL_PREFIXES = [
        (re.compile(r"^[fF](ua)?\s*/\s*(?=[A-Z])"), ""),  # "fua/Name" → "Name" (only if followed by capital)
        (re.compile(r"^[aA]\s*/\s*(?=[A-Z][a-z]{2,})"), ""),  # "a /Date" → "Date" (only if followed by word)
        (re.compile(r"^[a-z]\s+(?=[A-Z][a-z]{2,})"), ""),  # "s Government" → "Government" (single lowercase prefix)
    ]
    
    # VERY SELECTIVE garbage tokens - only obvious OCR errors
    GARBAGE_TOKENS = re.compile(
        r"\b(VERIFIEO|Nane|Empioyee|OSV|Wuy)\b",  # Only confirmed garbage
        re.IGNORECASE
    )
    
    # VERY SELECTIVE Devanagari misreads - only if NOT part of valid content
    DEVANAGARI_GARBAGE = [
        re.compile(r"^[0-9]{1,3}[A-Z]{2,4}\s+(?=[A-Z][a-z])"),  # "3TET Name" → "Name" (prefix only)
    ]
    
    # Structural noise patterns - ONLY REMOVE ISOLATED GARBAGE
    STRUCTURAL_NOISE = [
        re.compile(r"^[A-Z]\s+$"),  # ONLY single letter with whitespace (not "Name of Employee")
        re.compile(r"^\d{1,3}\s*$"),  # ONLY standalone numbers (not "30/105")
    ]
    
    # Address-specific cleanup patterns
    ADDRESS_NOISE = [
        re.compile(r",\s*,+"),  # Multiple consecutive commas: ", ," → ","
        re.compile(r"^\s*,\s*"),  # Leading comma
        re.compile(r"\s*,\s*$"),  # Trailing comma
    ]
    
    # Valid structured identifiers (DO NOT CLEAN)
    PROTECTED_PATTERNS = re.compile(
        r"\b("
        r"[A-Z]{5}\s?[0-9]{4}[A-Z]|"  # PAN: CFVPM7810Q
        r"[A-Z]{4}0[A-Z0-9]{6}|"  # IFSC: HDFC0001234
        r"\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}|"  # GSTIN
        r"\d{4}/\d{5}/\d{5}|"  # Aadhaar Enrolment
        r"\d{2}/\d{2}/\d{4}|"  # Date: DD/MM/YYYY
        r"\d{1,5}/\d{1,5}|"  # Address numbers: 30/105
        r"[A-Z]{2,}-\d+|"  # Reference codes: CHF273782
        r"VID:\d+|"  # VID numbers
        r"PIN\s*Code:\s*\d{6}|"  # PIN codes
        r"[SD]/O:\s*[A-Za-z\s]+(?:Khatri|Singh|Kumar|Sharma|Patel|Gupta)"  # S/O, D/O with common surnames
        r")\b",
        re.IGNORECASE
    )
    
    # Whitelisted acronyms and abbreviations
    PROTECTED_ACRONYMS = {
        "AADHAAR", "PAN", "KYC", "VKYC", "IFSC", "NEFT", "RTGS", "GST",
        "HDFC", "ICICI", "UTI", "UIDAI", "DPI", "OCR", "VLM", "API",
        "S/O", "D/O", "W/O", "VTC", "PO", "PIN", "DOB", "DIST"
    }
    
    def __init__(self, 
                 enable_audit: bool = False,
                 min_confidence_threshold: float = 0.4,  # REDUCED from 0.6 - be more lenient
                 document_type: Optional[str] = None):
        """
        Initialize text sanitizer with configuration.
        
        Args:
            enable_audit: Enable detailed audit trail (performance impact)
            min_confidence_threshold: Minimum confidence for accepting cleaned text (default 0.4 - lenient)
            document_type: Document type for specialized cleaning (aadhaar, pan, dl, etc.)
        """
        self.enable_audit = enable_audit
        self.min_confidence_threshold = min_confidence_threshold
        self.document_type = document_type
        self.stats = {"total_processed": 0, "total_cleaned": 0, "total_rejected": 0}
    
    def sanitize(self, text: str, context: Optional[Dict[str, Any]] = None) -> SanitizationResult:
        """
        Execute full multi-stage text sanitization pipeline.
        
        Args:
            text: Raw OCR text to sanitize
            context: Optional context (bbox, page_number, element_type, etc.)
        
        Returns:
            SanitizationResult with cleaned text and audit trail
        """
        if not text or not text.strip():
            return SanitizationResult(
                original="",
                cleaned="",
                removed_tokens=[],
                applied_rules=[],
                confidence_score=0.0,
                is_valid=False,
                stage_outputs={}
            )
        
        self.stats["total_processed"] += 1
        original = text
        stage_outputs = {CleaningStage.RAW: text} if self.enable_audit else {}
        applied_rules = []
        removed_tokens = []
        
        # Stage 1: Unicode Normalization
        text = self._stage_unicode_normalization(text, stage_outputs, applied_rules, removed_tokens)
        
        # Stage 2: Bilingual Noise Removal
        text = self._stage_bilingual_noise(text, stage_outputs, applied_rules, removed_tokens)
        
        # Stage 3: Structural Pattern Cleaning
        text = self._stage_structural_patterns(text, stage_outputs, applied_rules, removed_tokens)
        
        # Stage 4: Semantic Validation
        is_valid, confidence = self._stage_semantic_validation(text, context)
        
        # Final cleanup
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", text, flags=re.UNICODE).strip()
        
        if self.enable_audit:
            stage_outputs[CleaningStage.FINAL] = text
        
        if is_valid and text:
            self.stats["total_cleaned"] += 1
        else:
            self.stats["total_rejected"] += 1
        
        return SanitizationResult(
            original=original,
            cleaned=text,
            removed_tokens=removed_tokens,
            applied_rules=applied_rules,
            confidence_score=confidence,
            is_valid=is_valid,
            stage_outputs=stage_outputs
        )
    
    def _stage_unicode_normalization(self, text: str, outputs: Dict, rules: List, removed: List) -> str:
        """Stage 1: Remove corrupted Unicode and normalize."""
        # Remove Chinese/CJK characters
        if self.UNICODE_NOISE.search(text):
            removed_cjk = self.UNICODE_NOISE.findall(text)
            removed.extend(removed_cjk)
            text = self.UNICODE_NOISE.sub("", text)
            rules.append("unicode_cjk_removal")
        
        # Remove corrupted math/Greek symbols
        if self.CORRUPTED_SYMBOLS.search(text):
            removed_symbols = self.CORRUPTED_SYMBOLS.findall(text)
            removed.extend(removed_symbols)
            text = self.CORRUPTED_SYMBOLS.sub("", text)
            rules.append("corrupted_symbols_removal")
        
        # Remove repeated character noise
        if self.REPEATED_CHARS.search(text):
            text = self.REPEATED_CHARS.sub(r"\1\1", text)
            rules.append("repeated_chars_normalization")
        
        if self.enable_audit:
            outputs[CleaningStage.UNICODE_NORMALIZATION] = text
        
        return text
    
    def _stage_bilingual_noise(self, text: str, outputs: Dict, rules: List, removed: List) -> str:
        """Stage 2: Remove bilingual (English/Devanagari) OCR artifacts."""
        # Apply bilingual prefix patterns
        for pattern, replacement in self.BILINGUAL_PREFIXES:
            if pattern.search(text):
                text = pattern.sub(replacement, text)
                rules.append(f"bilingual_prefix_{pattern.pattern[:20]}")
        
        # Remove known garbage tokens (but protect acronyms)
        words = text.split()
        cleaned_words = []
        for word in words:
            if self.GARBAGE_TOKENS.search(word) and word.upper() not in self.PROTECTED_ACRONYMS:
                removed.append(word)
                rules.append(f"garbage_token_{word}")
            else:
                cleaned_words.append(word)
        text = " ".join(cleaned_words)
        
        # Remove Devanagari misread patterns
        for pattern in self.DEVANAGARI_GARBAGE:
            matches = pattern.findall(text)
            if matches:
                # Don't remove if protected
                for match in matches:
                    if not self.PROTECTED_PATTERNS.search(match):
                        text = pattern.sub("", text)
                        removed.append(match)
                        rules.append("devanagari_misread")
        
        if self.enable_audit:
            outputs[CleaningStage.BILINGUAL_NOISE] = text
        
        return text
    
    def _stage_structural_patterns(self, text: str, outputs: Dict, rules: List, removed: List) -> str:
        """Stage 3: Remove structural noise patterns."""
        # Check if this is a protected pattern (skip cleaning)
        if self.PROTECTED_PATTERNS.search(text):
            if self.enable_audit:
                outputs[CleaningStage.STRUCTURAL_PATTERNS] = text
            rules.append("protected_pattern_skip")
            return text
        
        # Apply structural noise removal
        for pattern in self.STRUCTURAL_NOISE:
            matches = pattern.findall(text)
            if matches:
                text = pattern.sub("", text)
                removed.extend(matches if isinstance(matches, list) else [matches])
                rules.append(f"structural_noise")
        
        # Clean address-specific noise
        for pattern in self.ADDRESS_NOISE:
            if pattern.search(text):
                text = pattern.sub("", text)
                rules.append("address_noise_cleanup")
        
        # Normalize whitespace after cleaning
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s*,\s*", ", ", text)  # Normalize comma spacing
        
        if self.enable_audit:
            outputs[CleaningStage.STRUCTURAL_PATTERNS] = text
        
        return text
    
    def _stage_semantic_validation(self, text: str, context: Optional[Dict[str, Any]]) -> Tuple[bool, float]:
        """
        Stage 4: Semantic validation - determine if cleaned text is valid.
        
        IMPORTANT: Be LENIENT - preserve text unless clearly garbage.
        
        Returns:
            (is_valid, confidence_score)
        """
        if not text or not text.strip():
            return False, 0.0
        
        cleaned = text.strip()
        score = 1.0  # Start with perfect score
        
        # REDUCED penalties - be more accepting
        if len(cleaned) == 1:
            score -= 0.3  # Was 0.6 - too harsh
        elif len(cleaned) == 2:
            score -= 0.1  # Was 0.3 - too harsh
        
        # Check for garbage patterns that survived
        if self.GARBAGE_PUNCTUATION.match(cleaned):
            return False, 0.0
        
        # RELAXED vowel ratio check - many technical terms have few vowels
        if re.search(r"[a-zA-Z]{5,}", cleaned):
            latin_letters = re.findall(r"[a-zA-Z]", cleaned)
            if len(latin_letters) >= 5:
                vowels = len(re.findall(r"[aeiouyAEIOUY]", cleaned))
                vowel_ratio = vowels / len(latin_letters)
                if vowel_ratio < 0.10:  # Was 0.15 - only extreme cases
                    score -= 0.3  # Was 0.5 - reduced penalty
        
        # Check if it's a valid script (very permissive)
        has_valid_chars = bool(re.search(r"[\u0900-\u097F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AFa-zA-Z0-9]", cleaned))
        if not has_valid_chars:
            return False, 0.0
        
        # Bonus for protected patterns or acronyms
        if cleaned.upper() in self.PROTECTED_ACRONYMS or self.PROTECTED_PATTERNS.search(cleaned):
            score = min(1.0, score + 0.2)
        
        # ACCEPT if score >= threshold (default 0.6, but we're more lenient)
        is_valid = score >= max(0.4, self.min_confidence_threshold - 0.2)  # Effective threshold: 0.4
        return is_valid, round(score, 3)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get sanitization statistics."""
        return {
            **self.stats,
            "rejection_rate": round(self.stats["total_rejected"] / max(1, self.stats["total_processed"]), 3),
            "cleaning_rate": round(self.stats["total_cleaned"] / max(1, self.stats["total_processed"]), 3)
        }
    
    def reset_stats(self):
        """Reset statistics counters."""
        self.stats = {"total_processed": 0, "total_cleaned": 0, "total_rejected": 0}


# Convenience function for backward compatibility
def clean_ocr_text(text: str, document_type: Optional[str] = None) -> str:
    """
    Quick text cleaning without audit trail.
    
    Args:
        text: Raw OCR text
        document_type: Optional document type hint
    
    Returns:
        Cleaned text string
    """
    sanitizer = TextSanitizer(enable_audit=False, document_type=document_type)
    result = sanitizer.sanitize(text)
    return result.cleaned if result.is_valid else ""
