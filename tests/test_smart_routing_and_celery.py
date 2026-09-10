"""Unit tests for smart document routing, direct Gemini routing, LLM deduplication, and Celery task execution."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from pipeline.nodes.idp_scan import is_tabular_or_misc_doc
from pipeline.nodes.llm_structure import _structure_single_document
from pipeline.celery_app import app as celery_app, run_pipeline_task
from app.main import app as fastapi_app


def test_smart_routing_identity_documents_bypass_tableformer():
    """Identity documents with no tables must bypass TableFormer."""
    assert is_tabular_or_misc_doc("pan") is False
    assert is_tabular_or_misc_doc("aadhaar") is False
    assert is_tabular_or_misc_doc("voter_id") is False
    assert is_tabular_or_misc_doc("passport") is False
    assert is_tabular_or_misc_doc("driving_license") is False
    assert is_tabular_or_misc_doc("PAN Card.pdf") is False
    assert is_tabular_or_misc_doc("Aadhaar Card.PDF") is False


def test_smart_routing_tabular_and_misc_documents_execute_tableformer():
    """Tabular and miscellaneous/unmapped documents must execute TableFormer in ACCURATE mode."""
    assert is_tabular_or_misc_doc("application_form") is True
    assert is_tabular_or_misc_doc("kfs") is True
    assert is_tabular_or_misc_doc("sanction_letter") is True
    assert is_tabular_or_misc_doc("account_statement") is True
    assert is_tabular_or_misc_doc("loan_agreement") is True
    assert is_tabular_or_misc_doc("disbursal_memo") is True
    assert is_tabular_or_misc_doc("miscellaneous") is True
    # Unmapped/unknown custom document types must also route to TableFormer
    assert is_tabular_or_misc_doc("unknown_custom_file.pdf") is True
    assert is_tabular_or_misc_doc("random_financial_contract") is True


def test_llm_structure_deduplication_skips_redundant_call():
    """If document data already has non-empty extracted fields, llm_structure skips duplicate extraction."""
    doc_data = {
        "applicant_name": "Rajesh Sharma",
        "pan_number": "ABCDE1234F",
        "dob": "1990-05-15",
        "loan_amount": 500000.0,
        "rawText": "Raw text content that would otherwise trigger LLM",
        "_raw_text": "Raw text content that would otherwise trigger LLM",
    }

    with patch("pipeline.nodes.llm_structure.llm_extract_fields") as mock_extract:
        result = _structure_single_document("pan", doc_data, "LOAN_001")
        # Must NOT call llm_extract_fields because fields were already extracted
        mock_extract.assert_not_called()
        assert result["applicant_name"] == "Rajesh Sharma"
        assert result["pan_number"] == "ABCDE1234F"
        assert result["dob"] == "1990-05-15"


def test_llm_structure_executes_extraction_when_fields_missing():
    """If document data only has rawText and no structured fields, llm_structure calls llm_extract_fields."""
    doc_data = {
        "rawText": "PAN: ABCDE1234F NAME: RAJESH SHARMA",
        "_raw_text": "PAN: ABCDE1234F NAME: RAJESH SHARMA",
    }

    with patch("pipeline.nodes.llm_structure.llm_extract_fields") as mock_extract:
        mock_extract.return_value = {
            "applicant_name": "Rajesh Sharma",
            "pan_number": "ABCDE1234F",
        }
        result = _structure_single_document("pan", doc_data, "LOAN_001")
        mock_extract.assert_called_once()
        assert result["applicant_name"] == "Rajesh Sharma"
        assert result["pan_number"] == "ABCDE1234F"


def test_celery_task_registration():
    """Celery application must have run_pipeline_task registered with json serializers."""
    assert "pipeline.tasks.run_pipeline_task" in celery_app.tasks
    task = celery_app.tasks["pipeline.tasks.run_pipeline_task"]
    assert task == run_pipeline_task
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.worker_pool == "threads"


def test_api_run_case_async_mode_enqueues_celery(monkeypatch):
    """POST /api/cases/{case_id}/run?async=true returns 200 with queued status."""
    mock_delay = MagicMock()
    mock_delay.return_value.id = "test-task-uuid-1234"
    monkeypatch.setattr("pipeline.celery_app.run_pipeline_task.delay", mock_delay)

    client = TestClient(fastapi_app)
    response = client.post("/api/cases/LOAN_001/run?async=true")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["task_id"] == "test-task-uuid-1234"
    mock_delay.assert_called_once_with("LOAN_001")


def test_docling_multi_key_cache():
    """Verify that Docling converter cache retains multiple configurations simultaneously."""
    from idp.services.docling.options import DoclingOptions
    from idp.services.docling.pipeline import _DOCLING_CONVERTERS, get_cached_converter, invalidate_converter_cache

    invalidate_converter_cache()
    assert len(_DOCLING_CONVERTERS) == 0

    id_opts = DoclingOptions(do_table_structure=False, table_mode="FAST")
    conv1 = get_cached_converter(id_opts)

    table_opts = DoclingOptions(do_table_structure=True, table_mode="ACCURATE")
    conv2 = get_cached_converter(table_opts)

    # Both configurations must coexist in cache
    assert len(_DOCLING_CONVERTERS) == 2

    # Querying id_opts again should return the exact same cached object without rebuild
    conv1_cached = get_cached_converter(id_opts)
    assert conv1_cached is conv1

    # Querying table_opts again should return the exact same cached object without rebuild
    conv2_cached = get_cached_converter(table_opts)
    assert conv2_cached is conv2


