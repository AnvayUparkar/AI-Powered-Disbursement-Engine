"""
tests/idp/test_options_sweep.py
================================
Parametrized sweep over key DoclingOptions parameters to find the optimal
combination for both digital and handwritten document types.

Marked @pytest.mark.slow — excluded from default test runs.

Run explicitly with:
    pytest tests/idp/test_options_sweep.py -m slow -v -s

Results are ranked and saved to tests/idp/sweep_results.json.
"""

from __future__ import annotations

import itertools
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

# ── Fixture paths (shared with benchmark) ─────────────────────────────────────
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "ocr_bench"
DIGITAL_PDF = FIXTURE_DIR / "digital.pdf"
HANDWRITTEN_PDF = FIXTURE_DIR / "handwritten.pdf"
DIGITAL_GT = FIXTURE_DIR / "digital_ground_truth.json"
HANDWRITTEN_GT = FIXTURE_DIR / "handwritten_ground_truth.json"

SWEEP_RESULTS_PATH = Path(__file__).parent / "sweep_results.json"


# ── Parameter grid ─────────────────────────────────────────────────────────────
# Each combination is one Docling configuration to evaluate.
# Add or remove values here to expand/shrink the search space.
SWEEP_GRID: Dict[str, List[Any]] = {
    "images_scale":        [1.5, 2.0, 2.5, 3.0],
    "det_limit_side_len":  [1024, 1536, 2048],
    "det_db_thresh":       [0.15, 0.20, 0.30],
    "det_db_box_thresh":   [0.25, 0.35, 0.45],
    "force_full_page_ocr": [False, True],
}


def _build_combinations() -> List[Dict[str, Any]]:
    keys = list(SWEEP_GRID.keys())
    values = [SWEEP_GRID[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


PARAM_COMBOS = _build_combinations()


# ══════════════════════════════════════════════════════════════════════════════
# Metric helpers (self-contained copy so sweep has no import dependency on
# the benchmark module — avoids module-scope fixture collisions)
# ══════════════════════════════════════════════════════════════════════════════

def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    return re.sub(r"\s+", " ", text)


def _lev_chars(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def cer(predicted: str, reference: str) -> float:
    ref = _normalise(reference)
    pred = _normalise(predicted)
    if not ref:
        return 0.0 if not pred else 1.0
    return min(1.0, _lev_chars(pred, ref) / len(ref))


def field_recall_fast(predicted_text: str, expected_fields: Dict[str, str]) -> float:
    """Fast substring-only field recall for sweep (speed priority over precision)."""
    pred_norm = _normalise(predicted_text)
    if not expected_fields:
        return 1.0
    found = sum(
        1 for val in expected_fields.values()
        if _normalise(str(val)) in pred_norm
    )
    return found / len(expected_fields)


def iou_bbox(pred: List[float], gt: List[float]) -> float:
    inter_l = max(pred[0], gt[0])
    inter_t = max(pred[1], gt[1])
    inter_r = min(pred[2], gt[2])
    inter_b = min(pred[3], gt[3])
    inter_area = max(0.0, inter_r - inter_l) * max(0.0, inter_b - inter_t)
    pred_area = max(0.0, pred[2] - pred[0]) * max(0.0, pred[3] - pred[1])
    gt_area = max(0.0, gt[2] - gt[0]) * max(0.0, gt[3] - gt[1])
    union = pred_area + gt_area - inter_area
    return 0.0 if union <= 0 else inter_area / union


# ══════════════════════════════════════════════════════════════════════════════
# Docling runner with custom options
# ══════════════════════════════════════════════════════════════════════════════

def _run_with_options(pdf_path: Path, params: Dict[str, Any]):
    """
    Invalidate the Docling converter cache and re-build with the given params.
    Returns DoclingParseResult.
    """
    from idp.services.docling.options import DoclingOptions
    from idp.services.docling.parser import DoclingParser
    from idp.services.docling.pipeline import invalidate_converter_cache

    invalidate_converter_cache()
    opts = DoclingOptions(**params)
    parser = DoclingParser(options=opts)
    return parser.parse(str(pdf_path), doc_id="sweep")


def _quick_eval(pdf_path: Path, gt_path: Path, params: Dict[str, Any]) -> Dict:
    """Run one sweep combination and return a compact metrics dict."""
    gt_data = json.loads(gt_path.read_text(encoding="utf-8"))
    gt_pages = {p["page_number"]: p for p in gt_data["pages"]}

    result = _run_with_options(pdf_path, params)

    # Full text for field recall
    full_text = "\n".join(
        e.text.strip() for e in result.elements if e.text and e.text.strip()
    )

    all_cer: List[float] = []
    all_recall: List[float] = []
    all_iou: List[float] = []

    for page_num, gt_page in gt_pages.items():
        ref_text = gt_page.get("expected_text", "")
        exp_fields = gt_page.get("expected_fields", {})

        page_pred = "\n".join(
            e.text.strip()
            for e in result.elements
            if e.page_number == page_num and e.text and e.text.strip()
        )

        all_cer.append(cer(page_pred, ref_text))
        all_recall.append(field_recall_fast(full_text, exp_fields))

        # BBox IoU (fast: mean over all GT boxes)
        page_dims = (
            result.pages_dimensions[page_num - 1]
            if page_num <= len(result.pages_dimensions)
            else {"width": 595.0, "height": 842.0}
        )
        w = page_dims.get("width", 595.0) or 595.0
        h = page_dims.get("height", 842.0) or 842.0
        pred_bboxes = [
            [e.bbox[0] / w, e.bbox[1] / h, e.bbox[2] / w, e.bbox[3] / h]
            for e in result.elements
            if e.page_number == page_num and e.bbox and any(v > 0 for v in e.bbox)
        ]
        for entry in gt_page.get("expected_bboxes", []):
            gt_box = entry["bbox_normalized"]
            best = max((iou_bbox(p, gt_box) for p in pred_bboxes), default=0.0)
            all_iou.append(best)

    def _avg(lst: List[float], default: float) -> float:
        return round(sum(lst) / len(lst), 4) if lst else default

    avg_cer = _avg(all_cer, 1.0)
    avg_recall = _avg(all_recall, 0.0)
    avg_iou = _avg(all_iou, 0.0)

    # Composite score: lower CER is better, higher recall/iou are better.
    # Weighted: CER 40%, field_recall 35%, IoU 25%
    composite = round((1 - avg_cer) * 0.40 + avg_recall * 0.35 + avg_iou * 0.25, 4)

    return {
        "params": params,
        "avg_cer": avg_cer,
        "avg_field_recall": avg_recall,
        "avg_mean_iou": avg_iou,
        "composite_score": composite,
        "elements_extracted": len(result.elements),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Pytest parametrize
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def sweep_docling_available() -> bool:
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.slow
@pytest.mark.parametrize("params", PARAM_COMBOS, ids=[
    "_".join(f"{k}={v}" for k, v in c.items()) for c in PARAM_COMBOS
])
def test_sweep_digital(params: Dict[str, Any], sweep_docling_available, tmp_path):
    """Sweep one parameter combination against the digital PDF fixture."""
    if not sweep_docling_available:
        pytest.skip("Docling not installed")
    assert DIGITAL_PDF.exists(), f"Missing: {DIGITAL_PDF}"
    assert DIGITAL_GT.exists(), f"Missing: {DIGITAL_GT}"

    metrics = _quick_eval(DIGITAL_PDF, DIGITAL_GT, params)
    print(
        f"\n[sweep/digital] composite={metrics['composite_score']:.4f} "
        f"cer={metrics['avg_cer']:.4f} recall={metrics['avg_field_recall']:.4f} "
        f"iou={metrics['avg_mean_iou']:.4f}  params={params}"
    )
    # Soft gate: composite score must be at least 0.30 to surface regressions
    assert metrics["composite_score"] >= 0.30, (
        f"Combo {params} composite={metrics['composite_score']:.4f} < 0.30 on digital"
    )


@pytest.mark.slow
@pytest.mark.parametrize("params", PARAM_COMBOS, ids=[
    "_".join(f"{k}={v}" for k, v in c.items()) for c in PARAM_COMBOS
])
def test_sweep_handwritten(params: Dict[str, Any], sweep_docling_available, tmp_path):
    """Sweep one parameter combination against the handwritten scanned PDF fixture."""
    if not sweep_docling_available:
        pytest.skip("Docling not installed")
    assert HANDWRITTEN_PDF.exists(), f"Missing: {HANDWRITTEN_PDF}"
    assert HANDWRITTEN_GT.exists(), f"Missing: {HANDWRITTEN_GT}"

    metrics = _quick_eval(HANDWRITTEN_PDF, HANDWRITTEN_GT, params)
    print(
        f"\n[sweep/handwritten] composite={metrics['composite_score']:.4f} "
        f"cer={metrics['avg_cer']:.4f} recall={metrics['avg_field_recall']:.4f} "
        f"iou={metrics['avg_mean_iou']:.4f}  params={params}"
    )
    assert metrics["composite_score"] >= 0.15, (
        f"Combo {params} composite={metrics['composite_score']:.4f} < 0.15 on handwritten"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Standalone runner: produces ranked sweep_results.json
# Run directly: python -m pytest tests/idp/test_options_sweep.py -m slow -s
# ══════════════════════════════════════════════════════════════════════════════

def run_full_sweep() -> None:
    """
    Run every combination against both documents and write a ranked report.
    Intended for direct invocation: python tests/idp/test_options_sweep.py
    """
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
    except ImportError:
        print("Docling not installed — sweep cannot run.")
        return

    all_results = []
    total = len(PARAM_COMBOS)
    for idx, params in enumerate(PARAM_COMBOS, 1):
        print(f"[{idx}/{total}] Testing {params}")
        try:
            d = _quick_eval(DIGITAL_PDF, DIGITAL_GT, params)
            h = _quick_eval(HANDWRITTEN_PDF, HANDWRITTEN_GT, params)
            combined = round((d["composite_score"] + h["composite_score"]) / 2, 4)
            all_results.append({
                "params": params,
                "combined_composite": combined,
                "digital": d,
                "handwritten": h,
            })
        except Exception as e:
            print(f"  ERROR: {e}")

    all_results.sort(key=lambda x: x["combined_composite"], reverse=True)

    SWEEP_RESULTS_PATH.write_text(
        json.dumps({"sweep_results": all_results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n{'='*60}")
    print(f"TOP 5 COMBINATIONS (by combined composite score)")
    print(f"{'='*60}")
    for i, r in enumerate(all_results[:5], 1):
        print(f"#{i}  composite={r['combined_composite']:.4f}  {r['params']}")
    print(f"\nFull results → {SWEEP_RESULTS_PATH}")


if __name__ == "__main__":
    run_full_sweep()
