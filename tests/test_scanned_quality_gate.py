"""
Unit and integration tests for ScannedDocQualityGate and selective scan enhancement.

Covers:
1. Happy path: Crisp high-resolution scan pass-through without modifications.
2. Defect: Mobile phone shadow / illumination gradient detection and remediation.
3. Defect: Skewed scan detection and deskew remediation.
4. Defect: Low-DPI scan super-resolution upsampling.
5. Mixed-quality multi-page document per-page triage.
6. Profile selection routing based on overall_scan_grade.
"""

import numpy as np
import cv2
import pytest
from idp.services.scanned_quality_gate import ScannedDocQualityGate, ScannedQualityReport
from config.docling_profiles import (
    get_profile_for_document_type,
    SCANNED_CLEAN_PROFILE,
    SCANNED_DOCUMENTS_PROFILE,
    SCANNED_DEGRADED_PROFILE,
)


def _create_synthetic_clean_scan(w: int = 800, h: int = 1000) -> np.ndarray:
    """Creates a high-contrast, razor-sharp synthetic document page."""
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    for y in range(80, h - 80, 40):
        cv2.putText(img, "STANDARD INVOICE PAYMENT RECORD - CONFIRMED", (60, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
    return img


def _create_synthetic_shadow_scan(w: int = 800, h: int = 1000) -> np.ndarray:
    """Creates a document page with a heavy shadow gradient on the right side."""
    base = _create_synthetic_clean_scan(w, h).astype(np.float32)
    gradient = np.linspace(1.0, 0.05, w, dtype=np.float32)
    gradient = np.tile(gradient, (h, 1))
    for c in range(3):
        base[:, :, c] *= gradient
    return np.clip(base, 0, 255).astype(np.uint8)


def _create_synthetic_skewed_scan(angle_deg: float = 5.0, w: int = 800, h: int = 1000) -> np.ndarray:
    """Creates a document rotated by a specific angle."""
    base = _create_synthetic_clean_scan(w, h)
    center = (w // 2, h // 2)
    rot_mat = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    return cv2.warpAffine(base, rot_mat, (w, h), borderValue=(255, 255, 255))


def test_clean_scan_happy_path_pass_through():
    """Crisp 300 DPI flatbed scan passes through unmodified with zero filter artifacts."""
    gate = ScannedDocQualityGate(min_dpi=180, min_sharpness=100.0, min_contrast=35.0)
    clean_img = _create_synthetic_clean_scan()

    processed_img, report = gate.process_scanned_page(clean_img, effective_dpi=300, page_number=1)

    assert report.quality_grade == "CLEAN"
    assert report.applied_remedies == []
    assert report.effective_dpi == 300
    assert report.sharpness > 100.0
    # Image must be byte-for-byte identical (pass-through)
    np.testing.assert_array_equal(processed_img, clean_img)


def test_shadow_detection_and_normalization():
    """Phone camera scan with heavy dark shadow triggers SHADOW_REMOVAL."""
    gate = ScannedDocQualityGate(min_dpi=180)
    shadow_img = _create_synthetic_shadow_scan()

    processed_img, report = gate.process_scanned_page(shadow_img, effective_dpi=300, page_number=1)

    assert "SHADOW_REMOVAL" in report.applied_remedies
    assert report.dark_ratio > 0.08

    # Verify that the right side is normalized (mean intensity of right half significantly lifted)
    orig_right_mean = np.mean(shadow_img[:, 400:, :])
    proc_right_mean = np.mean(processed_img[:, 400:, :])
    assert proc_right_mean > orig_right_mean + 40.0


def test_skew_detection_and_correction():
    """Skewed document triggers DESKEW and restores alignment."""
    gate = ScannedDocQualityGate(max_skew_deg=0.5)
    skewed_img = _create_synthetic_skewed_scan(angle_deg=5.0)

    processed_img, report = gate.process_scanned_page(skewed_img, effective_dpi=300, page_number=1)

    assert "DESKEW" in report.applied_remedies
    assert abs(report.skew_angle_deg) > 0.5

    # Re-checking the processed image should result in near-zero skew
    re_report = gate.analyze_scanned_image(processed_img, effective_dpi=300)
    assert abs(re_report.skew_angle_deg) < 1.0


def test_low_dpi_triggers_super_resolution():
    """Low DPI (< 180) triggers Lanczos upscaling to standard 300 DPI."""
    gate = ScannedDocQualityGate(min_dpi=180)
    low_res_img = cv2.resize(_create_synthetic_clean_scan(), (400, 500))

    processed_img, report = gate.process_scanned_page(low_res_img, effective_dpi=100, page_number=1)

    assert "SUPER_RESOLUTION" in report.applied_remedies
    assert report.quality_grade == "DEGRADED"

    # Scale factor from 100 to 300 is 3.0x
    expected_w = int(400 * (300 / 100))
    expected_h = int(500 * (300 / 100))
    assert abs(processed_img.shape[1] - expected_w) <= 2
    assert abs(processed_img.shape[0] - expected_h) <= 2


def test_mixed_quality_multipage_handling():
    """
    A multi-page document with mixed page qualities (Page 1: Clean, Page 2: Shadow, Page 3: Low DPI)
    ensures clean pages are untouched while degraded pages are repaired individually.
    """
    gate = ScannedDocQualityGate(min_dpi=180)
    p1 = _create_synthetic_clean_scan()
    p2 = _create_synthetic_shadow_scan()
    p3 = cv2.resize(_create_synthetic_clean_scan(), (400, 500))

    pages = [
        (p1, 300),
        (p2, 300),
        (p3, 100),
    ]

    processed_results = []
    for idx, (img, dpi) in enumerate(pages):
        proc_img, rep = gate.process_scanned_page(img, effective_dpi=dpi, page_number=idx + 1)
        processed_results.append((proc_img, rep))

    # Page 1 must remain untouched
    np.testing.assert_array_equal(processed_results[0][0], p1)
    assert processed_results[0][1].quality_grade == "CLEAN"

    # Page 2 must have shadow removal
    assert "SHADOW_REMOVAL" in processed_results[1][1].applied_remedies

    # Page 3 must have super-resolution
    assert "SUPER_RESOLUTION" in processed_results[2][1].applied_remedies


def test_profile_routing_by_overall_scan_grade():
    """Verify get_profile_for_document_type routes CLEAN vs DEGRADED appropriately."""
    # CLEAN scan gets the lightweight SCANNED_CLEAN_PROFILE (images_scale=2.0, zero destructive filters)
    clean_profile = get_profile_for_document_type("bank_statement", is_scanned=True, overall_scan_grade="CLEAN")
    assert clean_profile is SCANNED_CLEAN_PROFILE
    assert clean_profile.images_scale == 2.0
    assert clean_profile.enhance_contrast is False
    assert clean_profile.denoise is False
    assert clean_profile.deskew is False

    # DEGRADED or unspecified scan gets SCANNED_DOCUMENTS_PROFILE (images_scale=3.0, full filters)
    degraded_profile = get_profile_for_document_type("bank_statement", is_scanned=True, overall_scan_grade="DEGRADED")
    assert degraded_profile is SCANNED_DOCUMENTS_PROFILE
    assert degraded_profile.images_scale == 3.0

    # Backwards compatibility: overall_scan_grade=None falls back to SCANNED_DOCUMENTS_PROFILE
    default_profile = get_profile_for_document_type("bank_statement", is_scanned=True, overall_scan_grade=None)
    assert default_profile is SCANNED_DOCUMENTS_PROFILE
