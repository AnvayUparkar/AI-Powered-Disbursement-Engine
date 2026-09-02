import os
import tempfile
from idp.services.docling.parser import DoclingParser


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
