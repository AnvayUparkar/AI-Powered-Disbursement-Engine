"""RCA (2026-09-23): DocumentSerializer._is_duplicate()'s text-match branch used to fire at
just 20% bbox overlap. Two DISTINCT boxes with the same short repeated text -- comb-box grid
digits, repeated checkbox marks, repeated labels like "Date"/"Yes" -- routinely sit that close
together on a real form, so the second box's bbox was silently discarded as a false
"duplicate" even though OCR genuinely detected and located it. Raised to 60%.

These construct bboxes directly (rather than running the full pipeline) so the exact overlap
fraction is controlled precisely: for two equal-size boxes offset only horizontally,
_compute_overlap_score(a, b) == the fraction of a's width covered by b.
"""
import pytest

from idp.models.layout import ElementType, LayoutElement
from idp.services.output.serializer import DocumentSerializer


def _box(overlap_fraction: float, width: float = 20.0, height: float = 20.0) -> list[float]:
    """A box the same size as [0, 0, width, height], shifted right so it overlaps that
    reference box by exactly `overlap_fraction` of its own width."""
    shift = width * (1 - overlap_fraction)
    return [shift, 0.0, shift + width, height]


def _existing(text: str, bbox: list[float]) -> list[LayoutElement]:
    return [
        LayoutElement(
            id="existing-1",
            type=ElementType.PARAGRAPH,
            text=text,
            bbox=bbox,
            page_number=1,
        )
    ]


REFERENCE_BOX = [0.0, 0.0, 20.0, 20.0]


def test_repeated_text_at_old_threshold_is_no_longer_a_duplicate():
    """Happy path / the actual bug: two distinct comb-box cells both reading "1", 30% apart
    -- comfortably over the old 0.20 threshold -- must now be kept as separate elements."""
    existing = _existing("1", REFERENCE_BOX)
    candidate_bbox = _box(0.30)

    assert DocumentSerializer._is_duplicate(candidate_bbox, existing, text="1") is False


def test_heavily_overlapping_repeated_text_is_still_deduped():
    """Happy path: a genuine duplicate -- the same token re-detected at ~85% overlap -- must
    still be caught, so the fix doesn't just disable dedup entirely."""
    existing = _existing("Date", REFERENCE_BOX)
    candidate_bbox = _box(0.85)

    assert DocumentSerializer._is_duplicate(candidate_bbox, existing, text="Date") is True


@pytest.mark.parametrize(
    "overlap_fraction,expected_duplicate",
    [
        (0.59, False),  # just under the new threshold
        (0.60, True),   # exactly at the new threshold
        (0.61, True),   # just over
    ],
)
def test_threshold_boundary_is_exactly_0_60(overlap_fraction, expected_duplicate):
    """Edge case: the boundary itself, on both sides, for both the IoU and overlap gates."""
    existing = _existing("Yes", REFERENCE_BOX)
    candidate_bbox = _box(overlap_fraction)

    assert DocumentSerializer._is_duplicate(candidate_bbox, existing, text="Yes") is expected_duplicate


def test_different_text_pure_spatial_dedup_is_unaffected():
    """Failure mode / regression guard: the OTHER branch of _is_duplicate (near-identical
    bbox, DIFFERENT text, IoU >= 0.85) is a separate rule this change must not touch."""
    existing = _existing("Applicant Name", REFERENCE_BOX)
    # ~97% overlap -> IoU well above 0.85 regardless of text.
    candidate_bbox = _box(0.97)

    assert DocumentSerializer._is_duplicate(candidate_bbox, existing, text="Some Other Value") is True


def test_different_text_moderate_overlap_is_not_deduped():
    """Failure mode: adjacent key-value form fields (different text, ~50% IoU) must stay
    independent -- this is the exact case the 0.85 pure-spatial gate exists to protect."""
    existing = _existing("Applicant Name", REFERENCE_BOX)
    candidate_bbox = _box(0.50)

    assert DocumentSerializer._is_duplicate(candidate_bbox, existing, text="Co-Applicant Name") is False
