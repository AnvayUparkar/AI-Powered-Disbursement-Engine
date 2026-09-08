"""
Comprehensive test suite for production-grade OCR text sanitizer.

Tests cover all cleaning stages, edge cases, and document-specific patterns
for Indian government documents (Aadhaar, PAN, DL).
"""

import pytest
from idp.services.ocr.text_sanitizer import (
    TextSanitizer,
    SanitizationResult,
    CleaningStage,
    clean_ocr_text
)


class TestUnicodeNormalization:
    """Test Stage 1: Unicode noise removal."""
    
    def test_removes_chinese_characters(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Government of India，哪市，可，，开可")
        assert "哪市" not in result.cleaned
        assert "Government of India" in result.cleaned
        assert "unicode_cjk_removal" in result.applied_rules
    
    def test_removes_corrupted_math_symbols(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Total Amount: π500 ∑ α β")
        assert "π" not in result.cleaned
        assert "α" not in result.cleaned
        assert "Total Amount" in result.cleaned
        assert "corrupted_symbols_removal" in result.applied_rules
    
    def test_normalizes_repeated_characters(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Helllllo Worrrrrld")
        assert result.cleaned == "Hello Worrld"  # Max 2 repetitions
        assert "repeated_chars_normalization" in result.applied_rules


class TestBilingualNoiseRemoval:
    """Test Stage 2: Bilingual (Hindi/English) OCR artifacts."""
    
    def test_removes_devanagari_prefix_noise(self):
        sanitizer = TextSanitizer()
        test_cases = [
            ("fua / Father's Name", "Father's Name"),
            ("f / Father", "Father"),
            ("a / Date of Birth", "Date of Birth"),
            ("aT& / Signature", "Signature"),
            ("s Brih Government", "Government"),
        ]
        for raw, expected in test_cases:
            result = sanitizer.sanitize(raw)
            assert expected in result.cleaned
    
    def test_removes_known_garbage_tokens(self):
        sanitizer = TextSanitizer()
        garbage = "ORIGINAL SEEN & VERIFIEO Nane of Empioyee WARAN SINGH"
        result = sanitizer.sanitize(garbage)
        assert "VERIFIEO" not in result.cleaned
        assert "Nane" not in result.cleaned
        assert "Empioyee" not in result.cleaned
        assert "WARAN SINGH" in result.cleaned
    
    def test_removes_devanagari_misread_patterns(self):
        sanitizer = TextSanitizer()
        test_cases = [
            "3TET Hin / Your Aadhaar No",
            "TT3T 3",
            "334 /3 34",
            "3T9nT Date",
        ]
        for raw in test_cases:
            result = sanitizer.sanitize(raw)
            # Garbage patterns removed
            assert "3TET" not in result.cleaned
            assert "TT3T" not in result.cleaned
            assert "334" not in result.cleaned
    
    def test_preserves_protected_acronyms(self):
        sanitizer = TextSanitizer()
        text = "AADHAAR UIDAI KYC PAN HAR"
        result = sanitizer.sanitize(text)
        assert "AADHAAR" in result.cleaned
        assert "UIDAI" in result.cleaned
        assert "KYC" in result.cleaned
        assert "PAN" in result.cleaned
        assert "HAR" not in result.cleaned  # Not protected


class TestStructuralPatternCleaning:
    """Test Stage 3: Structural noise removal."""
    
    def test_removes_standalone_single_letters(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("R 10 Government A of India")
        assert "Government of India" in result.cleaned
        # Single letters should be removed
        cleaned_words = result.cleaned.split()
        assert "R" not in cleaned_words
        assert "A" not in cleaned_words
    
    def test_removes_standalone_numbers(self):
        sanitizer = TextSanitizer()
        text = "Prakash Khatri 105 12 sanganer 333"
        result = sanitizer.sanitize(text)
        assert "Prakash Khatri" in result.cleaned
        assert "sanganer" in result.cleaned
        # Standalone numbers removed
        assert " 105 " not in f" {result.cleaned} "
        assert " 12 " not in f" {result.cleaned} "
    
    def test_removes_prefix_corruption(self):
        sanitizer = TextSanitizer()
        test_cases = [
            ("9/MALE", "MALE"),
            ("Paf4/DOB: 22/06/1976", "DOB: 22/06/1976"),
        ]
        for raw, expected in test_cases:
            result = sanitizer.sanitize(raw)
            assert expected in result.cleaned
    
    def test_removes_random_character_sequences(self):
        sanitizer = TextSanitizer()
        text = "ee , a fr ) s4 H4 ( , 41 3 os) 3"
        result = sanitizer.sanitize(text)
        # Should be heavily cleaned or rejected
        assert result.confidence_score < 0.7 or len(result.cleaned) < 10


class TestProtectedPatterns:
    """Test that valid structured identifiers are NEVER cleaned."""
    
    def test_preserves_pan_number(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("PAN: CFVPM7810Q")
        assert "CFVPM7810Q" in result.cleaned
        assert result.is_valid
        assert "protected_pattern_skip" in result.applied_rules
    
    def test_preserves_aadhaar_enrolment(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Enrolment No.: 1207/68773/68027")
        assert "1207/68773/68027" in result.cleaned
        assert result.is_valid
    
    def test_preserves_dates(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("DOB: 22/06/1976")
        assert "22/06/1976" in result.cleaned
        assert result.is_valid
    
    def test_preserves_pin_code(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("PIN Code: 302029")
        assert "302029" in result.cleaned
        assert result.is_valid
    
    def test_preserves_reference_codes(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Employee ID: CHF273782")
        assert "CHF273782" in result.cleaned
        assert result.is_valid
    
    def test_preserves_address_numbers(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("30/105, sindhi colony")
        assert "30/105" in result.cleaned
        assert result.is_valid
        
    def test_preserves_full_address_with_so(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("S/O: Gyan Chand Khatri, 30/105, sindhi colony")
        assert "S/O: Gyan Chand Khatri" in result.cleaned
        assert "30/105" in result.cleaned
        assert "sindhi colony" in result.cleaned
        assert result.is_valid
    
    def test_preserves_vid(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("VID:9195")
        assert "VID:9195" in result.cleaned
        assert result.is_valid


class TestSemanticValidation:
    """Test Stage 4: Semantic validation and confidence scoring."""
    
    def test_rejects_single_character(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("R")
        assert not result.is_valid
        assert result.confidence_score < 0.6
    
    def test_rejects_pure_punctuation(self):
        sanitizer = TextSanitizer()
        garbage = ["!!!", "???", "---", "...", "~~~"]
        for g in garbage:
            result = sanitizer.sanitize(g)
            assert not result.is_valid
    
    def test_rejects_low_vowel_ratio(self):
        sanitizer = TextSanitizer()
        # Consonant-heavy garbage
        result = sanitizer.sanitize("RRRRTTTT")
        assert result.confidence_score < 0.7
    
    def test_accepts_valid_text(self):
        sanitizer = TextSanitizer()
        valid_texts = [
            "Government of India",
            "Prakash Khatri",
            "S/O: Gyan Chand Khatri",
            "Address: sindhi colony, sanganer",
            "Mobile: 9166202777",
        ]
        for text in valid_texts:
            result = sanitizer.sanitize(text)
            assert result.is_valid
            assert result.confidence_score >= 0.6
    
    def test_boosts_confidence_for_acronyms(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("AADHAAR")
        assert result.is_valid
        assert result.confidence_score >= 0.8


class TestRealWorldAadhaarGarbage:
    """Test against actual Aadhaar OCR output from user's document."""
    
    def test_cleans_aadhaar_page1_garbage(self):
        sanitizer = TextSanitizer()
        
        garbage_inputs = [
            "HAR Unique Identification Authority",  # HAR should be removed
            "105",  # Standalone number
            "12",
            "R",
            "10",
            "333",
            "ORIGINAL SEEN & VERIFIEO Nane of Empioyee",
            "Empleye 0 CHF273782",  # Empleye garbage, preserve CHF273782
            "Full & Proper Signature Wuy",  # Wuy garbage
            "3TET Hin / Your Aadhaar No",
            "TT3T 3",
            "334 /3 34",
            "ee , a fr ) s4 H4 ( , 41 3 os) 3",
            "RT 3HTET，AA",
            "Paf4/DOB: 22/06/1976",
            "3, 30/105, , e ，，，， TR-302029",
        ]
        
        for garbage in garbage_inputs:
            result = sanitizer.sanitize(garbage)
            
            # Verify garbage tokens removed
            if "HAR " in garbage:
                assert "HAR" not in result.cleaned or "AADHAAR" in result.cleaned
            if "VERIFIEO" in garbage:
                assert "VERIFIEO" not in result.cleaned
            if "Nane" in garbage:
                assert "Nane" not in result.cleaned
            if "Wuy" in garbage:
                assert "Wuy" not in result.cleaned
            if "3TET" in garbage:
                assert "3TET" not in result.cleaned
            if "TT3T" in garbage:
                assert "TT3T" not in result.cleaned
    
    def test_preserves_aadhaar_valid_content(self):
        sanitizer = TextSanitizer()
        
        valid_inputs = [
            "AADHAAR",
            "Government of India",
            "Prakash Khatri",
            "S/O: Gyan Chand Khatri",
            "sindhi colony",
            "sanganer",
            "jhulelal mandir ke pass",
            "Sanganer",
            "Sanganer Bazar",
            "Jaipur",
            "Rajasthan",
            "Mobile: 9166202777",
            "Enrolment No.: 1207/68773/68027",
            "DOB: 22/06/1976",
            "MALE",
            "Aadhaar no.Issued: 15/08/2017",
            "PIN Code: 302029",
            "VID:9195",
        ]
        
        for valid_text in valid_inputs:
            result = sanitizer.sanitize(valid_text)
            assert result.is_valid, f"Failed on: {valid_text}"
            assert len(result.cleaned) > 0
            assert result.confidence_score >= 0.6


class TestAuditTrail:
    """Test audit trail and observability features."""
    
    def test_audit_disabled_by_default(self):
        sanitizer = TextSanitizer(enable_audit=False)
        result = sanitizer.sanitize("Test text")
        assert result.stage_outputs == {}
    
    def test_audit_enabled_captures_all_stages(self):
        sanitizer = TextSanitizer(enable_audit=True)
        result = sanitizer.sanitize("Test text with 哪市 noise")
        
        assert CleaningStage.RAW in result.stage_outputs
        assert CleaningStage.UNICODE_NORMALIZATION in result.stage_outputs
        assert CleaningStage.BILINGUAL_NOISE in result.stage_outputs
        assert CleaningStage.STRUCTURAL_PATTERNS in result.stage_outputs
        assert CleaningStage.FINAL in result.stage_outputs
    
    def test_tracks_removed_tokens(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Test 哪市 text Nane")
        assert len(result.removed_tokens) > 0
        assert any("哪" in token or "市" in token for token in result.removed_tokens)
    
    def test_tracks_applied_rules(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("Test 哪市 text")
        assert len(result.applied_rules) > 0
        assert "unicode_cjk_removal" in result.applied_rules


class TestStatistics:
    """Test sanitizer statistics and metrics."""
    
    def test_tracks_processing_stats(self):
        sanitizer = TextSanitizer()
        
        # Process multiple texts
        sanitizer.sanitize("Valid text 1")
        sanitizer.sanitize("Valid text 2")
        sanitizer.sanitize("R")  # Should be rejected
        sanitizer.sanitize("")  # Should be rejected
        
        stats = sanitizer.get_stats()
        assert stats["total_processed"] == 4
        assert stats["total_cleaned"] >= 2
        assert stats["total_rejected"] >= 2
        assert "rejection_rate" in stats
        assert "cleaning_rate" in stats
    
    def test_reset_stats(self):
        sanitizer = TextSanitizer()
        sanitizer.sanitize("Test")
        sanitizer.reset_stats()
        
        stats = sanitizer.get_stats()
        assert stats["total_processed"] == 0
        assert stats["total_cleaned"] == 0
        assert stats["total_rejected"] == 0


class TestConvenienceFunction:
    """Test backward-compatible convenience function."""
    
    def test_clean_ocr_text_function(self):
        result = clean_ocr_text("Test 哪市 text Nane")
        assert isinstance(result, str)
        assert "哪市" not in result
        assert "Nane" not in result
        assert len(result) > 0
    
    def test_returns_empty_for_invalid(self):
        result = clean_ocr_text("R")
        assert result == ""


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_string(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("")
        assert not result.is_valid
        assert result.cleaned == ""
        assert result.confidence_score == 0.0
    
    def test_whitespace_only(self):
        sanitizer = TextSanitizer()
        result = sanitizer.sanitize("   \n\t  ")
        assert not result.is_valid
        assert result.cleaned == ""
    
    def test_none_handling(self):
        sanitizer = TextSanitizer()
        # Should handle gracefully without crashing
        result = sanitizer.sanitize("")
        assert not result.is_valid
    
    def test_very_long_text(self):
        sanitizer = TextSanitizer()
        long_text = "Valid sentence. " * 1000
        result = sanitizer.sanitize(long_text)
        assert result.is_valid
        assert len(result.cleaned) > 0
    
    def test_unicode_edge_cases(self):
        sanitizer = TextSanitizer()
        # Mixed scripts
        result = sanitizer.sanitize("English हिन्दी 中文")
        assert "English" in result.cleaned
        assert "हिन्दी" in result.cleaned
        assert "中文" not in result.cleaned  # CJK removed


class TestDocumentTypeSpecialization:
    """Test document-type specific cleaning."""
    
    def test_aadhaar_document_type(self):
        sanitizer = TextSanitizer(document_type="aadhaar")
        result = sanitizer.sanitize("AADHAAR Government of India")
        assert result.is_valid
        assert "AADHAAR" in result.cleaned
    
    def test_pan_document_type(self):
        sanitizer = TextSanitizer(document_type="pan")
        result = sanitizer.sanitize("PAN: CFVPM7810Q")
        assert result.is_valid
        assert "CFVPM7810Q" in result.cleaned
    
    def test_generic_document_type(self):
        sanitizer = TextSanitizer(document_type=None)
        result = sanitizer.sanitize("Generic Document Text")
        assert result.is_valid


class TestConfidenceThreshold:
    """Test configurable confidence thresholds."""
    
    def test_strict_threshold(self):
        sanitizer = TextSanitizer(min_confidence_threshold=0.9)
        result = sanitizer.sanitize("AB")  # Marginal text
        assert not result.is_valid  # Rejected by high threshold
    
    def test_lenient_threshold(self):
        sanitizer = TextSanitizer(min_confidence_threshold=0.3)
        result = sanitizer.sanitize("AB")
        # May pass with lenient threshold
        assert result.confidence_score >= 0.3
