"""Tests for equal vs tiered field weights flag and dictionary structures.

Validates that both tiered and equal weight mappings are precisely defined,
key sets align 1-to-1, selector function returns the correct mapping,
and no monkeypatching is used.
"""
from typing import Any, Dict
import pytest

from config import (
    EQUAL_FIELD_CRITICALITY_WEIGHTS,
    FIELD_CRITICALITY_WEIGHTS,
    TIERED_FIELD_CRITICALITY_WEIGHTS,
    USE_EQUAL_FIELD_WEIGHTS,
    get_field_criticality_weights,
)


def test_tiered_field_weights_structure():
    """Validates the tiered criticality weights dictionary structure and values."""
    # Tier 1: Core Identity (3.0)
    for field in ("applicant_name", "pan_number", "dob", "aadhaar_number", "customer_name", "applicant_pan_number"):
        assert field in TIERED_FIELD_CRITICALITY_WEIGHTS
        assert TIERED_FIELD_CRITICALITY_WEIGHTS[field] == 3.0

    # Tier 2: Core Financials (2.0)
    for field in ("loan_amount", "account_no", "emi", "irr_percent", "tenure", "loan_validity", "bank_account_no"):
        assert field in TIERED_FIELD_CRITICALITY_WEIGHTS
        assert TIERED_FIELD_CRITICALITY_WEIGHTS[field] == 2.0

    # Tier 3: Contact & Demographics (1.0)
    for field in ("mobile_no", "gender", "fathers_name", "address", "current_address", "loan_type", "application_no"):
        assert field in TIERED_FIELD_CRITICALITY_WEIGHTS
        assert TIERED_FIELD_CRITICALITY_WEIGHTS[field] == 1.0

    # Fallback behavior for unlisted fields
    assert TIERED_FIELD_CRITICALITY_WEIGHTS.get("unlisted_audit_field", 1.0) == 1.0
    assert TIERED_FIELD_CRITICALITY_WEIGHTS.get("custom_score_field", 2.5) == 2.5


def test_equal_field_weights_structure():
    """Validates that EQUAL_FIELD_CRITICALITY_WEIGHTS matches all keys with weight 1.0."""
    # Key sets must be strictly identical
    assert set(EQUAL_FIELD_CRITICALITY_WEIGHTS.keys()) == set(TIERED_FIELD_CRITICALITY_WEIGHTS.keys())

    # Every entry must have weight 1.0
    for field_name, weight in EQUAL_FIELD_CRITICALITY_WEIGHTS.items():
        assert weight == 1.0, f"Field '{field_name}' in equal weights should be 1.0, got {weight}"

    # Fallback behavior for unlisted fields
    assert EQUAL_FIELD_CRITICALITY_WEIGHTS.get("unlisted_audit_field", 1.0) == 1.0


def test_get_field_criticality_weights_selector():
    """Validates selector function explicitly returning equal or tiered weights without monkeypatching."""
    # Explicitly requesting equal weights (all 1.0)
    equal_weights = get_field_criticality_weights(use_equal=True)
    assert equal_weights is EQUAL_FIELD_CRITICALITY_WEIGHTS
    assert equal_weights["applicant_name"] == 1.0
    assert equal_weights["loan_amount"] == 1.0
    assert equal_weights["mobile_no"] == 1.0

    # Explicitly requesting tiered weights (3.0, 2.0, 1.0)
    tiered_weights = get_field_criticality_weights(use_equal=False)
    assert tiered_weights is TIERED_FIELD_CRITICALITY_WEIGHTS
    assert tiered_weights["applicant_name"] == 3.0
    assert tiered_weights["loan_amount"] == 2.0
    assert tiered_weights["mobile_no"] == 1.0

    # Default call without arguments matches the active FIELD_CRITICALITY_WEIGHTS
    default_weights = get_field_criticality_weights()
    assert default_weights is FIELD_CRITICALITY_WEIGHTS


def test_field_criticality_weights_active_mapping():
    """Validates that FIELD_CRITICALITY_WEIGHTS is a valid dictionary conforming to expected types."""
    assert isinstance(FIELD_CRITICALITY_WEIGHTS, dict)
    assert len(FIELD_CRITICALITY_WEIGHTS) > 0

    # Check key types and values
    for k, v in FIELD_CRITICALITY_WEIGHTS.items():
        assert isinstance(k, str)
        assert isinstance(v, (int, float))
        assert v > 0.0

    # Empty string or non-existent key fallback
    assert FIELD_CRITICALITY_WEIGHTS.get("", 1.0) == 1.0
    assert FIELD_CRITICALITY_WEIGHTS.get("nonexistent_field_xyz", 1.0) == 1.0
