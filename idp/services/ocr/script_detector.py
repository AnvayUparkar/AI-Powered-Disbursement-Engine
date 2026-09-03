import re
from enum import Enum
from typing import Dict, List, Tuple, Optional
from pydantic import BaseModel
from idp.core.logging import logger


class ScriptCategory(str, Enum):
    LATIN = "latin"
    ENGLISH = "english"
    DEVANAGARI = "devanagari"
    JAPANESE = "japanese"
    CHINESE = "chinese"
    KOREAN = "korean"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ScriptDetectionResult(BaseModel):
    """Structured detection result for script classification."""
    primary_script: str
    scripts_detected: List[str]
    is_mixed: bool
    confidence: float
    reason: str


class ScriptDetector:
    """Lightweight, deterministic Unicode-range script detection engine."""

    # Unicode range regexes
    DEVANAGARI_REGEX = re.compile(r"[\u0900-\u097F]")
    JAPANESE_KANA_REGEX = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
    CJK_KANJI_REGEX = re.compile(r"[\u4E00-\u9FFF]")
    KOREAN_HANGUL_REGEX = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")
    LATIN_REGEX = re.compile(r"[a-zA-Z]")
    NUMERIC_PUNCT_REGEX = re.compile(r"[\d\s\.,;:!\?\-\(\)\/\\\[\]\{\}\"\']")

    def detect_script(self, text: str) -> ScriptDetectionResult:
        """
        Detects script classification from text and returns a structured ScriptDetectionResult object.
        """
        if not text or not text.strip():
            return ScriptDetectionResult(
                primary_script=ScriptCategory.UNKNOWN.value,
                scripts_detected=[],
                is_mixed=False,
                confidence=1.0,
                reason="empty_text"
            )

        cleaned = text.strip()

        # Count occurrences of each script family
        devanagari_count = len(self.DEVANAGARI_REGEX.findall(cleaned))
        japanese_kana_count = len(self.JAPANESE_KANA_REGEX.findall(cleaned))
        cjk_kanji_count = len(self.CJK_KANJI_REGEX.findall(cleaned))
        korean_hangul_count = len(self.KOREAN_HANGUL_REGEX.findall(cleaned))
        latin_count = len(self.LATIN_REGEX.findall(cleaned))

        letter_count = devanagari_count + japanese_kana_count + cjk_kanji_count + korean_hangul_count + latin_count

        if letter_count == 0:
            return ScriptDetectionResult(
                primary_script=ScriptCategory.LATIN.value,
                scripts_detected=["latin"],
                is_mixed=False,
                confidence=0.90,
                reason="numeric_or_symbols_default_latin"
            )

        scripts_detected = []
        if devanagari_count > 0:
            scripts_detected.append("devanagari")
        if latin_count > 0:
            scripts_detected.append("latin")
        if japanese_kana_count > 0:
            scripts_detected.append("japanese")
        if cjk_kanji_count > 0 and japanese_kana_count == 0:
            scripts_detected.append("chinese")
        if korean_hangul_count > 0:
            scripts_detected.append("korean")

        devanagari_ratio = devanagari_count / letter_count
        latin_ratio = latin_count / letter_count
        japanese_ratio = (japanese_kana_count + (cjk_kanji_count if japanese_kana_count > 0 else 0)) / letter_count
        korean_ratio = korean_hangul_count / letter_count
        chinese_ratio = (cjk_kanji_count if japanese_kana_count == 0 else 0) / letter_count

        # Mixed Devanagari + Latin
        if devanagari_count > 0 and latin_count > 0:
            if devanagari_ratio >= 0.20 and latin_ratio >= 0.20:
                return ScriptDetectionResult(
                    primary_script=ScriptCategory.MIXED.value,
                    scripts_detected=scripts_detected,
                    is_mixed=True,
                    confidence=round(max(devanagari_ratio, latin_ratio), 2),
                    reason=f"mixed_devanagari_latin (dev={devanagari_ratio:.2f}, lat={latin_ratio:.2f})"
                )
            elif devanagari_ratio > latin_ratio:
                return ScriptDetectionResult(
                    primary_script=ScriptCategory.DEVANAGARI.value,
                    scripts_detected=scripts_detected,
                    is_mixed=True,
                    confidence=round(devanagari_ratio, 2),
                    reason=f"devanagari_dominant (ratio={devanagari_ratio:.2f})"
                )
            else:
                return ScriptDetectionResult(
                    primary_script=ScriptCategory.LATIN.value,
                    scripts_detected=scripts_detected,
                    is_mixed=True,
                    confidence=round(latin_ratio, 2),
                    reason=f"latin_dominant (ratio={latin_ratio:.2f})"
                )

        if devanagari_count > 0 and devanagari_ratio >= 0.15:
            return ScriptDetectionResult(
                primary_script=ScriptCategory.DEVANAGARI.value,
                scripts_detected=scripts_detected,
                is_mixed=False,
                confidence=round(devanagari_ratio, 2),
                reason=f"devanagari_script_detected (ratio={devanagari_ratio:.2f})"
            )

        if japanese_kana_count > 0 or (cjk_kanji_count > 0 and japanese_ratio >= 0.20):
            return ScriptDetectionResult(
                primary_script=ScriptCategory.JAPANESE.value,
                scripts_detected=scripts_detected,
                is_mixed=False,
                confidence=round(japanese_ratio, 2),
                reason=f"japanese_script_detected (ratio={japanese_ratio:.2f})"
            )

        if korean_hangul_count > 0:
            return ScriptDetectionResult(
                primary_script=ScriptCategory.KOREAN.value,
                scripts_detected=scripts_detected,
                is_mixed=False,
                confidence=round(korean_ratio, 2),
                reason=f"korean_script_detected (ratio={korean_ratio:.2f})"
            )

        if chinese_ratio >= 0.30:
            return ScriptDetectionResult(
                primary_script=ScriptCategory.CHINESE.value,
                scripts_detected=scripts_detected,
                is_mixed=False,
                confidence=round(chinese_ratio, 2),
                reason=f"chinese_script_detected (ratio={chinese_ratio:.2f})"
            )

        return ScriptDetectionResult(
            primary_script=ScriptCategory.LATIN.value,
            scripts_detected=scripts_detected or ["latin"],
            is_mixed=False,
            confidence=round(latin_ratio if latin_ratio > 0 else 0.90, 2),
            reason=f"latin_script_detected (ratio={latin_ratio:.2f})"
        )

    def detect_script_from_text(self, text: str) -> ScriptCategory:
        """Helper returning primary ScriptCategory enum."""
        res = self.detect_script(text)
        return ScriptCategory(res.primary_script)

    def detect_script_with_reason(self, text: str) -> Tuple[ScriptCategory, str]:
        """Helper returning Tuple[ScriptCategory, reason_string]."""
        res = self.detect_script(text)
        return ScriptCategory(res.primary_script), res.reason


_global_detector = ScriptDetector()


def detect_script(text: str) -> str:
    """Convenience top-level function returning primary script name ('latin', 'devanagari', 'mixed', etc.)."""
    return _global_detector.detect_script(text).primary_script


def is_script_mismatch(expected_script: str, text: str) -> bool:
    """
    Returns True if text significantly deviates from expected script.
    E.g., if expected_script is 'devanagari', but text has 0 Devanagari characters and produces only Latin text.
    """
    if not text or not text.strip():
        return False
    exp = expected_script.lower()
    actual = detect_script(text)
    if exp in ["devanagari", "hindi", "marathi"]:
        has_dev = any(0x0900 <= ord(c) <= 0x097F for c in text)
        if not has_dev and actual == "latin":
            return True
    elif exp in ["latin", "english"]:
        has_dev = any(0x0900 <= ord(c) <= 0x097F for c in text)
        if has_dev:
            return True
    return False

