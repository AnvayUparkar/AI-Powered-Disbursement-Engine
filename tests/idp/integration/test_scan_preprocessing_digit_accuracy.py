"""
Integration tests: scan preprocessing before/after digit accuracy comparison.

These tests require real scanned fixtures with known ground-truth digit fields
(account numbers, sanctioned amounts, etc.) and are SKIPPED unless the
environment variable ``OCR_SCAN_FIXTURES_DIR`` is set to a directory
containing fixture pairs:

    <OCR_SCAN_FIXTURES_DIR>/
        fixture_01/
            scan.pdf          ← raw scanned PDF (not checked in)
            ground_truth.json ← {"fields": {"account_no": "001234567890", ...}}
        fixture_02/
            scan.pdf
            ground_truth.json
        ...

Running locally
---------------
    OCR_SCAN_FIXTURES_DIR=/path/to/fixtures pytest tests/idp/integration/test_scan_preprocessing_digit_accuracy.py -v

Expected outcome after the fix: edit-distance ratios of digit-heavy fields
should improve or stay the same across ALL fixtures, with no regressions.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

FIXTURES_DIR = os.getenv("OCR_SCAN_FIXTURES_DIR", "")

pytestmark = pytest.mark.skipif(
    not FIXTURES_DIR or not os.path.isdir(FIXTURES_DIR),
    reason=(
        "OCR_SCAN_FIXTURES_DIR not set or not a directory — "
        "skipping integration scan-accuracy tests"
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edit_distance_ratio(a: str, b: str) -> float:
    """Levenshtein distance ratio (0.0 = completely different, 1.0 = identical)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    dist = dp[n]
    return 1.0 - dist / max(m, n)


def _digit_chars_only(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _collect_fixtures(root: str) -> List[Tuple[Path, Dict[str, Any]]]:
    """Return list of (pdf_path, ground_truth_fields) for every fixture dir."""
    fixtures = []
    for entry in sorted(Path(root).iterdir()):
        if not entry.is_dir():
            continue
        scan = entry / "scan.pdf"
        gt = entry / "ground_truth.json"
        if not scan.exists() or not gt.exists():
            continue
        with gt.open() as fh:
            data = json.load(fh)
        fixtures.append((scan, data.get("fields", {})))
    return fixtures


def _run_ocr_on_file(pdf_path: str, doc_id: str) -> str:
    """
    Run Docling OCR on *pdf_path* and return the concatenated text of all
    layout elements.  Uses the scanned-document profile so the test exercises
    the same code path as production.
    """
    from config.docling_profiles import SCANNED_DOCUMENTS_PROFILE
    from idp.services.docling.parser import DoclingParser

    parser = DoclingParser(SCANNED_DOCUMENTS_PROFILE)
    result = parser.parse(pdf_path, doc_id=doc_id)
    if result is None:
        return ""
    return " ".join(
        (elem.text or "") for elem in (result.elements or []) if elem.text
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDigitAccuracyImprovement:
    """
    For each fixture, compare OCR text extracted from:
      (a) the RAW scanned PDF  (baseline — current prod behaviour)
      (b) the PREPROCESSED PDF (after scan_preprocessor)

    For every digit-heavy ground-truth field, (b) must score >= (a).
    At least one fixture must show a measurable improvement (ratio delta > 0.01)
    so we know the test isn't vacuously passing.
    """

    @pytest.fixture(scope="class")
    def fixtures(self):
        return _collect_fixtures(FIXTURES_DIR)

    def test_digit_accuracy_does_not_regress(self, fixtures, tmp_path):
        from idp.services.ocr.scan_preprocessor import preprocess_scanned_document
        from config.docling_profiles import SCANNED_DOCUMENTS_PROFILE

        regressions: List[str] = []
        improvements: int = 0

        for scan_path, gt_fields in fixtures:
            doc_id = scan_path.parent.name

            # (a) Baseline: raw file
            raw_text = _run_ocr_on_file(str(scan_path), doc_id=f"{doc_id}_raw")

            # (b) Preprocessed
            scan_result = preprocess_scanned_document(
                file_path=str(scan_path),
                file_category="pdf",
                target_scale=SCANNED_DOCUMENTS_PROFILE.images_scale,
                doc_id=f"{doc_id}_pre",
                output_dir=str(tmp_path),
            )
            pre_text = _run_ocr_on_file(scan_result.processed_path, doc_id=f"{doc_id}_pre_ocr")

            for field_name, expected in gt_fields.items():
                expected_digits = _digit_chars_only(str(expected))
                if len(expected_digits) < 4:
                    # Skip non-digit-heavy fields
                    continue

                raw_digits = _digit_chars_only(raw_text)
                pre_digits = _digit_chars_only(pre_text)

                # Find best window match for expected string in extracted digits
                def _best_ratio(haystack: str, needle: str) -> float:
                    if len(needle) > len(haystack):
                        return _edit_distance_ratio(haystack, needle)
                    best = 0.0
                    for start in range(len(haystack) - len(needle) + 1):
                        r = _edit_distance_ratio(
                            haystack[start : start + len(needle)], needle
                        )
                        if r > best:
                            best = r
                    return best

                raw_ratio = _best_ratio(raw_digits, expected_digits)
                pre_ratio = _best_ratio(pre_digits, expected_digits)

                delta = pre_ratio - raw_ratio
                if delta < -0.01:  # Allow 1% tolerance for rounding noise
                    regressions.append(
                        f"[{doc_id}] field={field_name!r}: "
                        f"raw={raw_ratio:.3f} pre={pre_ratio:.3f} delta={delta:.3f}"
                    )
                if delta > 0.01:
                    improvements += 1

        assert not regressions, (
            "Scan preprocessing introduced digit accuracy REGRESSIONS:\n"
            + "\n".join(regressions)
        )
        assert improvements > 0, (
            "Scan preprocessing produced no measurable digit accuracy improvements "
            "across any fixture — the fix may not be exercising the correct code path."
        )
