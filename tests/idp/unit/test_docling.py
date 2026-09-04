import os
import tempfile
import threading
from idp.services.docling.parser import DoclingParser
from idp.services.docling.pipeline import (
    get_cached_converter,
    invalidate_converter_cache,
    DoclingPipeline,
)


def test_docling_parser_fallback():
    parser = DoclingParser()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 mock content")
        tmp_name = f.name

    try:
        res = parser.parse(tmp_name, doc_id="TEST-DOCLING")
        assert res is not None
        assert res.page_count >= 1
        assert isinstance(res.elements, list)
    finally:
        os.remove(tmp_name)


def test_docling_converter_cached_across_instances():
    """Docling converter must be initialized once and reused for all instances."""
    invalidate_converter_cache()

    p1 = DoclingPipeline()
    p2 = DoclingPipeline()

    c1 = p1.get_converter()
    c2 = p2.get_converter()

    assert c1 is c2, (
        "DoclingPipeline.get_converter() must return the same cached instance "
        "across different DoclingPipeline objects in the same process."
    )


def test_docling_converter_thread_safe_single_init():
    """Concurrent threads must not each build a separate converter."""
    invalidate_converter_cache()

    results = []
    errors = []

    def _get():
        try:
            results.append(get_cached_converter())
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=_get) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Threads raised errors: {errors}"
    assert len(results) == 6
    first = results[0]
    for conv in results[1:]:
        assert conv is first, "Different threads received different converter instances."


def test_docling_converter_invalidation_triggers_rebuild():
    """invalidate_converter_cache() must cause the next call to rebuild."""
    c_before = get_cached_converter()
    assert c_before is not None

    invalidate_converter_cache()

    c_after = get_cached_converter()
    assert c_after is not None
    assert c_before is not c_after, (
        "After invalidation, get_cached_converter() must return a freshly built converter."
    )
