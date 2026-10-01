"""
Tests for DocumentProcessor._run_lightonocr_pages: the concurrent page fan-out that
replaced the previous strictly-sequential per-page LightOnOCR loop.

Verifies:
1. Happy path: pages processed concurrently but results returned in page order, with
   correct pages_processed/pages_failed counts.
2. MAX_PAGE_WORKERS actually bounds how many pages run at once for this document.
3. Empty page list -> empty result, zero counts, no crash.
4. A page whose adapter call raises is converted to a failed OCRResult (not propagated),
   and does not stop the other pages from being processed.
5. A page returning extraction_failed=True is counted as failed, not processed.
"""
import asyncio
from types import SimpleNamespace

import pytest

from idp.services.document_processor import DocumentProcessor
from idp.models.ocr import OCRResult


def _fake_adapter():
    """Stand-in with the attribute `_run_lightonocr_pages` accesses before dispatch; the actual
    call is intercepted by the asyncio.to_thread patch in each test, so this is never invoked."""
    return SimpleNamespace(process_page_to_ocr_result=lambda **kwargs: None)


def _ocr_result(page_number: int, failed: bool = False) -> OCRResult:
    return OCRResult(
        page_number=page_number,
        elements=[],
        extraction_failed=failed,
        image_width=100.0,
        image_height=100.0,
    )


@pytest.fixture
def processor(monkeypatch):
    proc = DocumentProcessor()
    monkeypatch.setattr(proc, "_get_redis_client", lambda: _immediate_none())
    return proc


async def _immediate_none():
    return None


def _pages(n: int):
    return [(b"page-bytes", 100.0, 100.0) for _ in range(n)]


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_results_in_page_order_with_correct_counts(self, processor, monkeypatch):
        async def fake_to_thread(fn, **kwargs):
            return _ocr_result(kwargs["page_number"])

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        adapter = _fake_adapter()
        results, processed, failed = await processor._run_lightonocr_pages(_pages(5), "DOC1", adapter)

        assert [r.page_number for r in results] == [1, 2, 3, 4, 5]
        assert processed == 5
        assert failed == 0

    @pytest.mark.asyncio
    async def test_empty_pages_returns_empty(self, processor):
        adapter = _fake_adapter()
        results, processed, failed = await processor._run_lightonocr_pages([], "DOC1", adapter)
        assert results == []
        assert processed == 0
        assert failed == 0


class TestConcurrencyBound:
    @pytest.mark.asyncio
    async def test_max_page_workers_bounds_in_flight_pages(self, processor, monkeypatch):
        in_flight = 0
        max_seen = 0

        async def fake_to_thread(fn, **kwargs):
            nonlocal in_flight, max_seen
            in_flight += 1
            max_seen = max(max_seen, in_flight)
            await asyncio.sleep(0.03)
            in_flight -= 1
            return _ocr_result(kwargs["page_number"])

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        monkeypatch.setattr("idp.services.document_processor.settings.MAX_PAGE_WORKERS", 2)

        adapter = _fake_adapter()
        results, processed, failed = await processor._run_lightonocr_pages(_pages(6), "DOC1", adapter)

        assert max_seen == 2
        assert processed == 6
        assert failed == 0
        assert [r.page_number for r in results] == [1, 2, 3, 4, 5, 6]


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_exception_on_one_page_becomes_failed_result_others_still_processed(self, processor, monkeypatch):
        async def fake_to_thread(fn, **kwargs):
            if kwargs["page_number"] == 2:
                raise RuntimeError("simulated LiteLLM transport error")
            return _ocr_result(kwargs["page_number"])

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        adapter = _fake_adapter()
        results, processed, failed = await processor._run_lightonocr_pages(_pages(3), "DOC1", adapter)

        assert [r.page_number for r in results] == [1, 2, 3]
        assert results[1].extraction_failed is True
        assert processed == 2
        assert failed == 1

    @pytest.mark.asyncio
    async def test_extraction_failed_result_counted_as_failed(self, processor, monkeypatch):
        async def fake_to_thread(fn, **kwargs):
            return _ocr_result(kwargs["page_number"], failed=(kwargs["page_number"] == 1))

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        adapter = _fake_adapter()
        results, processed, failed = await processor._run_lightonocr_pages(_pages(2), "DOC1", adapter)

        assert results[0].extraction_failed is True
        assert results[1].extraction_failed is False
        assert processed == 1
        assert failed == 1
