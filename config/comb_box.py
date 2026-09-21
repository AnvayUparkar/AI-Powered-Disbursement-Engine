"""Configuration for segmented character comb-box detection and validation."""
from typing import Any, Dict

# ============================================================================
# COMB-BOX FIELD CONFIGURATION
# ============================================================================
# Fields known to use comb-box (segmented character) rendering in forms.
# Used for detection, validation, and format checking.
# ============================================================================

COMB_BOX_FIELDS: Dict[str, Dict[str, Any]] = {
    "application_no": {
        "expected_length": (10, 15),  # Min, max length
        "pattern": r"^[A-Z0-9\-_/]{10,15}$",
        "char_types": ["upper", "digit", "symbol"],
        "description": "Application number or loan ID",
        "examples": ["APPL00343265", "LN-2024-001234"]
    },
    "bank_account_no": {
        "expected_length": (9, 18),
        "pattern": r"^\d{9,18}$",
        "char_types": ["digit"],
        "description": "Bank account number",
        "examples": ["987654321012", "1234567890123456"]
    },
    "aadhaar_number": {
        "expected_length": 12,
        "pattern": r"^\d{12}$",
        "char_types": ["digit"],
        "description": "Aadhaar unique identification number",
        "examples": ["123456789012"]
    },
    "pan_number": {
        "expected_length": 10,
        "pattern": r"^[A-Z]{5}\d{4}[A-Z]$",
        "char_types": ["upper", "digit"],
        "description": "Permanent Account Number",
        "examples": ["ABCDE1234F", "CFVPM7810Q"]
    },
    "loan_amount": {
        "expected_length": (5, 10),
        "pattern": r"^\d{5,10}$",
        "char_types": ["digit"],
        "description": "Loan amount in digits",
        "examples": ["500000", "1125000"]
    },
    "mobile_no": {
        "expected_length": 10,
        "pattern": r"^\d{10}$",
        "char_types": ["digit"],
        "description": "10-digit mobile number",
        "examples": ["9166202777", "9876543210"]
    },
    "ifsc_code": {
        "expected_length": 11,
        "pattern": r"^[A-Z]{4}0[A-Z0-9]{6}$",
        "char_types": ["upper", "digit"],
        "description": "IFSC bank code",
        "examples": ["HDFC0001234", "SBIN0012345"]
    }
}

# Configuration for comb-box detection engine
COMB_BOX_DETECTION_CONFIG: Dict[str, Any] = {
    "enabled": True,  # Feature flag for comb-box detection
    "y_tolerance": 0.045,  # Row clustering vertical threshold
    "spacing_uniformity_threshold": 0.30,  # Max CV for gap uniformity
    "size_uniformity_threshold": 0.25,  # Max CV for width uniformity
    "min_sequence_length": 4,  # Minimum consecutive characters
    "max_char_length": 3,  # Maximum chars per element (1-3 typical)
    "validation_strict_mode": False,  # Lenient validation in production
    "audit_enabled": True,  # Log reconstruction events
}
