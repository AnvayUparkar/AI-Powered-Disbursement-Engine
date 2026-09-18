"""Raw layout cluster capture for the debug overlay.

Docling replaces page.predictions.layout with the POST-processed clusters, so
the layout model's original predictions are unrecoverable after the fact. These
tests cover the hook that snapshots them on the way past, the id()-keyed store
that must not leak clusters between pages, and the bbox/label normalisation the
overlay depends on.
"""
import gc
import weakref

import pytest

from idp.services.docling import layout_capture
from idp.services.docling.layout_capture import (
    _remember,
    clear,
    get_raw_clusters,
    snapshot_cluster,
)


class FakeBBox:
    """Stand-in for Docling's BoundingBox with a top-left conversion."""

    def __init__(self, l, t, r, b, needs_flip=False, page_h=100.0):
        self.l, self.t, self.r, self.b = l, t, r, b
        self._needs_flip = needs_flip
        self._page_h = page_h

    def to_top_left_origin(self, page_height):
        if not self._needs_flip:
            return self
        return FakeBBox(self.l, page_height - self.t, self.r, page_height - self.b)


class FakeLabel:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return f"DocItemLabel.{self.value.upper()}"


class FakeCluster:
    def __init__(self, cid, label, confidence, bbox, cells=()):
        self.id = cid
        self.label = label
        self.confidence = confidence
        self.bbox = bbox
        self.cells = list(cells)


class FakeSize:
    def __init__(self, height=100.0, width=80.0):
        self.height = height
        self.width = width


class FakePage:
    """Weakref-able but deliberately unhashable, like Docling's Page."""

    __slots__ = ("size", "__weakref__")
    __hash__ = None  # type: ignore[assignment]

    def __init__(self, height=100.0):
        self.size = FakeSize(height=height)


@pytest.fixture(autouse=True)
def _clean_store():
    clear()
    yield
    clear()


# ── Snapshot shape ─────────────────────────────────────────────────────────

def test_snapshot_extracts_the_fields_the_overlay_needs():
    cluster = FakeCluster(7, FakeLabel("section_header"), 0.873,
                          FakeBBox(10.0, 20.0, 60.0, 35.0), cells=[object(), object()])
    snap = snapshot_cluster(cluster, page_height=100.0)

    assert snap == {
        "id": 7,
        "label": "section_header",
        "confidence": pytest.approx(0.873),
        "bbox": [10.0, 20.0, 60.0, 35.0],
        "cell_count": 2,
    }


def test_snapshot_unwraps_enum_labels_rather_than_stringifying_them():
    """str(label) would render "DocItemLabel.TEXT", which the UI would show verbatim."""
    snap = snapshot_cluster(
        FakeCluster(1, FakeLabel("text"), 0.5, FakeBBox(0, 0, 1, 1)), page_height=100.0
    )
    assert snap["label"] == "text"


def test_snapshot_falls_back_to_str_for_a_plain_string_label():
    cluster = FakeCluster(1, "table", 0.5, FakeBBox(0, 0, 1, 1))
    assert snapshot_cluster(cluster, page_height=100.0)["label"] == "table"


def test_snapshot_converts_bottom_left_bboxes_to_top_left_origin():
    """Overlay coordinates are top-left; a bbox that reports a bottom-left
    origin must be flipped against the page height, not copied through."""
    cluster = FakeCluster(1, FakeLabel("text"), 0.9,
                          FakeBBox(10.0, 90.0, 50.0, 70.0, needs_flip=True))
    assert snapshot_cluster(cluster, page_height=100.0)["bbox"] == [10.0, 10.0, 50.0, 30.0]


def test_snapshot_of_a_region_with_no_ocr_cells_reports_zero():
    """These are the only regions on a page whose bbox is pure layout geometry,
    since there were no OCR cells to snap onto."""
    snap = snapshot_cluster(
        FakeCluster(3, FakeLabel("picture"), 0.42, FakeBBox(0, 0, 10, 10)), page_height=100.0
    )
    assert snap["cell_count"] == 0


def test_snapshot_returns_none_for_an_unreadable_cluster():
    class Broken:
        id = 1
        label = FakeLabel("text")
        confidence = 0.5
        cells = []

        @property
        def bbox(self):
            raise AttributeError("no bbox")

    assert snapshot_cluster(Broken(), page_height=100.0) is None


def test_snapshot_tolerates_a_missing_confidence():
    cluster = FakeCluster(1, FakeLabel("text"), None, FakeBBox(0, 0, 1, 1))
    assert snapshot_cluster(cluster, page_height=100.0)["confidence"] == 0.0


# ── Per-page store ─────────────────────────────────────────────────────────

def test_clusters_are_retrievable_for_the_page_they_were_captured_for():
    page = FakePage()
    _remember(page, [FakeCluster(1, FakeLabel("text"), 0.9, FakeBBox(0, 0, 5, 5))])
    captured = get_raw_clusters(page)
    assert len(captured) == 1 and captured[0]["id"] == 1


def test_pages_do_not_share_clusters():
    p1, p2 = FakePage(), FakePage()
    _remember(p1, [FakeCluster(1, FakeLabel("text"), 0.9, FakeBBox(0, 0, 5, 5))])
    _remember(p2, [FakeCluster(2, FakeLabel("table"), 0.8, FakeBBox(1, 1, 6, 6))])
    assert [c["id"] for c in get_raw_clusters(p1)] == [1]
    assert [c["id"] for c in get_raw_clusters(p2)] == [2]


def test_unknown_page_returns_empty_rather_than_raising():
    assert get_raw_clusters(FakePage()) == []


def test_unreadable_clusters_are_dropped_not_stored_as_none():
    class Broken:
        id = 9
        label = FakeLabel("text")
        confidence = 0.5
        cells = []

        @property
        def bbox(self):
            raise AttributeError

    page = FakePage()
    _remember(page, [FakeCluster(1, FakeLabel("text"), 0.9, FakeBBox(0, 0, 5, 5)), Broken()])
    captured = get_raw_clusters(page)
    assert len(captured) == 1
    assert all(c is not None for c in captured)


def test_page_height_is_read_off_the_page_for_the_origin_flip():
    page = FakePage(height=200.0)
    _remember(page, [FakeCluster(1, FakeLabel("text"), 0.9,
                                 FakeBBox(0.0, 180.0, 10.0, 160.0, needs_flip=True))])
    assert get_raw_clusters(page)[0]["bbox"] == [0.0, 20.0, 10.0, 40.0]


def test_entry_is_released_when_the_page_is_collected():
    """id() is reused by CPython, so a stale entry would let a future page
    silently inherit another page's clusters."""
    page = FakePage()
    key = id(page)
    _remember(page, [FakeCluster(1, FakeLabel("text"), 0.9, FakeBBox(0, 0, 5, 5))])
    assert key in layout_capture._RAW_CLUSTERS

    ref = weakref.ref(page)
    del page
    gc.collect()

    assert ref() is None
    assert key not in layout_capture._RAW_CLUSTERS


def test_empty_cluster_list_is_recorded_as_empty():
    page = FakePage()
    _remember(page, [])
    assert get_raw_clusters(page) == []


# ── Hook installation ──────────────────────────────────────────────────────

def test_install_is_idempotent_and_does_not_stack_wrappers():
    from docling.utils.layout_postprocessor import LayoutPostprocessor

    assert layout_capture.install() is True
    first = LayoutPostprocessor.__init__
    assert layout_capture.install() is True
    assert LayoutPostprocessor.__init__ is first, "install() must not re-wrap"
    assert getattr(first, "_idp_layout_capture", False) is True
