"""
Field validation for extracted values with format checking and confidence scoring.

Validates field values against expected patterns, lengths, and character types.
Provides confidence adjustment based on validation results.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Union, Tuple
from config.pipeline_checks import COMB_BOX_FIELDS
from idp.core.logging import logger


@dataclass
class ValidationResult:
    """
    Result of field validation.
    
    Attributes:
        is_valid: Whether field passes basic validation
        confidence: Confidence score (0.0-1.0) adjusted by validation
        warnings: List of validation warnings/issues
        validation_details: Additional metadata about validation
    """
    is_valid: bool
    confidence: float
    warnings: List[str]
    validation_details: dict


class FieldValidator:
    """
    Validates extracted field values against expected formats.
    
    Used to verify comb-box reconstructed fields and adjust confidence scores
    based on format compliance.
    """
    
    def __init__(self, strict_mode: bool = False):
        """
        Initialize field validator.
        
        Args:
            strict_mode: If True, reject fields that don't match patterns exactly
                        If False (default), accept with lower confidence
        """
        self.strict_mode = strict_mode
    
    def validate_field(
        self,
        field_name: str,
        value: str,
        source_type: Optional[str] = None
    ) -> ValidationResult:
        """
        Validate a field value against expected format.
        
        Args:
            field_name: Name of the field being validated
            value: Extracted value to validate
            source_type: How the field was extracted (e.g., "comb_box_reconstruction")
            
        Returns:
            ValidationResult with validity, confidence, and warnings
        """
        if not value or not value.strip():
            return ValidationResult(
                is_valid=False,
                confidence=0.0,
                warnings=["Empty value"],
                validation_details={}
            )
        
        value = value.strip()
        
        # Check if field has comb-box configuration
        if field_name not in COMB_BOX_FIELDS:
            # No specific validation rules - accept with default confidence
            return ValidationResult(
                is_valid=True,
                confidence=0.80,
                warnings=[],
                validation_details={"validation_type": "no_rules"}
            )
        
        config = COMB_BOX_FIELDS[field_name]
        warnings = []
        confidence = 1.0
        
        # Length validation
        expected_length = config["expected_length"]
        if isinstance(expected_length, tuple):
            min_len, max_len = expected_length
            if not (min_len <= len(value) <= max_len):
                warnings.append(
                    f"Length {len(value)} outside expected range [{min_len}, {max_len}]"
                )
                confidence *= 0.7
                if self.strict_mode:
                    return ValidationResult(
                        is_valid=False,
                        confidence=0.3,
                        warnings=warnings,
                        validation_details={"failed_check": "length"}
                    )
        else:
            if len(value) != expected_length:
                warnings.append(
                    f"Length {len(value)} != expected {expected_length}"
                )
                confidence *= 0.6
                if self.strict_mode:
                    return ValidationResult(
                        is_valid=False,
                        confidence=0.3,
                        warnings=warnings,
                        validation_details={"failed_check": "length"}
                    )
        
        # Pattern validation
        pattern = config.get("pattern")
        if pattern and not re.match(pattern, value):
            warnings.append(f"Value doesn't match expected pattern: {pattern}")
            confidence *= 0.5
            if self.strict_mode:
                return ValidationResult(
                    is_valid=False,
                    confidence=0.4,
                    warnings=warnings,
                    validation_details={"failed_check": "pattern"}
                )
        
        # Character type validation
        char_types = config.get("char_types", [])
        type_issues = self._validate_char_types(value, char_types)
        if type_issues:
            warnings.extend(type_issues)
            confidence *= 0.8
        
        # Bonus for comb-box reconstruction (high confidence method)
        if source_type == "comb_box_reconstruction":
            confidence = min(1.0, confidence * 1.1)
        
        # Final validation decision (require confidence threshold AND valid pattern match if defined)
        pattern_matched = not (pattern and not re.match(pattern, value))
        is_valid = (confidence >= 0.5) and pattern_matched
        
        validation_details = {
            "validation_type": "comb_box_field",
            "expected_length": expected_length,
            "actual_length": len(value),
            "pattern_matched": bool(pattern and re.match(pattern, value)),
            "source_type": source_type
        }
        
        return ValidationResult(
            is_valid=is_valid,
            confidence=round(confidence, 3),
            warnings=warnings,
            validation_details=validation_details
        )
    
    def _validate_char_types(
        self,
        value: str,
        expected_types: List[str]
    ) -> List[str]:
        """
        Check if value contains only expected character types.
        
        Args:
            value: String to validate
            expected_types: List of allowed types ("upper", "lower", "digit", "symbol")
            
        Returns:
            List of warnings if unexpected characters found
        """
        warnings = []
        
        has_upper = any(c.isupper() for c in value)
        has_lower = any(c.islower() for c in value)
        has_digit = any(c.isdigit() for c in value)
        has_symbol = any(not c.isalnum() for c in value)
        
        if has_upper and "upper" not in expected_types:
            warnings.append("Contains unexpected uppercase letters")
        
        if has_lower and "lower" not in expected_types:
            warnings.append("Contains unexpected lowercase letters")
        
        if has_digit and "digit" not in expected_types:
            warnings.append("Contains unexpected digits")
        
        if has_symbol and "symbol" not in expected_types:
            warnings.append("Contains unexpected symbols")
        
        return warnings
    
    def validate_batch(
        self,
        fields: dict,
        source_types: Optional[dict] = None
    ) -> dict:
        """
        Validate multiple fields at once.
        
        Args:
            fields: Dict of {field_name: value}
            source_types: Optional dict of {field_name: source_type}
            
        Returns:
            Dict of {field_name: ValidationResult}
        """
        source_types = source_types or {}
        results = {}
        
        for field_name, value in fields.items():
            source_type = source_types.get(field_name)
            results[field_name] = self.validate_field(
                field_name, value, source_type
            )
        
        return results
    
    def get_validation_summary(
        self,
        validation_results: dict
    ) -> dict:
        """
        Generate summary statistics from validation results.
        
        Args:
            validation_results: Dict of {field_name: ValidationResult}
            
        Returns:
            Summary dict with counts and average confidence
        """
        total = len(validation_results)
        valid_count = sum(1 for r in validation_results.values() if r.is_valid)
        
        confidences = [r.confidence for r in validation_results.values()]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        
        warnings_count = sum(
            len(r.warnings) for r in validation_results.values()
        )
        
        return {
            "total_fields": total,
            "valid_count": valid_count,
            "invalid_count": total - valid_count,
            "validation_rate": valid_count / total if total > 0 else 0.0,
            "average_confidence": round(avg_confidence, 3),
            "total_warnings": warnings_count
        }
