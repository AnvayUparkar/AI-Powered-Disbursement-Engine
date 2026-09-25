"""Docling's native PDF backend crashes the worker when two threads convert at once.

These tests pin the process-wide serialization that prevents it: overlapping
DoclingParser.parse() calls must never be inside converter.convert() together.
"""
import threading
import time
from typing import Any, List, Optional

import pytest

from idp.services.docling import pipeline as docling_pipeline
from idp.services.docling.parser import DoclingParser


class _RecordingConverter:
    """Converter stub that records whether convert() calls ever overlap."""

    def __init__(self, hold_seconds: float = 0.05):
        self.hold_seconds = hold_seconds
        self.active = 0
        self.max_concurrent = 0
        self.calls: List[str] = []
        self._guard = threading.Lock()

    def convert(self, document_path: str) -> Any:
        with self._guard:
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
            self.calls.append(document_path)
        try:
            time.sleep(self.hold_seconds)
            return _ConversionResult()
        finally:
            with self._guard:
                self.active -= 1


class _PageSize:
    width = 595.0
    height = 842.0


class _Page:
    size = _PageSize()


class _Document:
    pages = {1: _Page()}
    texts: List[Any] = []
    tables: List[Any] = []


class _ConversionResult:
    document = _Document()


@pytest.fixture
def recording_converter(monkeypatch) -> _RecordingConverter:
    converter = _RecordingConverter()
    monkeypatch.setattr(
        docling_pipeline.DoclingPipeline, "get_converter", lambda self: converter
    )
    return converter


def _parse_in_threads(count: int, converter: _RecordingConverter) -> List[Optional[BaseException]]:
    errors: List[Optional[BaseException]] = [None] * count
    barrier = threading.Barrier(count)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            DoclingParser().parse(f"/tmp/doc_{idx}.pdf", doc_id=f"DOC-{idx}")
        except BaseException as exc:  # noqa: BLE001 - recorded and re-asserted below
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "parse() thread deadlocked under the convert lock"
    return errors


def test_concurrent_parses_never_overlap_inside_convert(recording_converter):
    """Happy path: 4 threads parsing at once still enter convert() one at a time."""
    errors = _parse_in_threads(4, recording_converter)

    assert errors == [None, None, None, None]
    assert recording_converter.max_concurrent == 1, (
        "converter.convert() ran concurrently; docling-parse's native backend "
        f"would corrupt its heap (max_concurrent={recording_converter.max_concurrent})"
    )
    assert len(recording_converter.calls) == 4
    assert sorted(recording_converter.calls) == [f"/tmp/doc_{i}.pdf" for i in range(4)]


def test_single_parse_still_converts_once(recording_converter):
    """Boundary: the lock must not change single-threaded behaviour."""
    result = DoclingParser().parse("/tmp/solo.pdf", doc_id="DOC-SOLO")

    assert recording_converter.calls == ["/tmp/solo.pdf"]
    assert recording_converter.max_concurrent == 1
    assert result.page_count >= 1


def test_convert_failure_releases_the_lock(monkeypatch, recording_converter):
    """Failure mode: a raising convert() must not leave the lock held forever."""
    def boom(document_path: str) -> Any:
        raise RuntimeError("native backend exploded")

    monkeypatch.setattr(recording_converter, "convert", boom)
    # parse() absorbs the error and returns its fallback result; what matters
    # here is that the lock is not still held afterwards.
    DoclingParser().parse("/tmp/broken.pdf", doc_id="DOC-BROKEN")

    lock = docling_pipeline.docling_convert_lock()
    acquired = lock.acquire(timeout=5)
    try:
        assert acquired, "convert lock was not released after a conversion failure"
    finally:
        if acquired:
            lock.release()


def test_convert_lock_is_process_wide_singleton():
    """Every caller must serialize on the same lock object."""
    assert docling_pipeline.docling_convert_lock() is docling_pipeline.docling_convert_lock()
