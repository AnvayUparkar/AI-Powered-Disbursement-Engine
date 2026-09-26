"""Tests for per-document stage timings (idp pod) and per-run timing summaries (api/worker pod)."""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from config.tenant import ContextThreadPoolExecutor
from idp.utils.timing import (
    StageClock,
    build_run_summary,
    collect_run_timings,
    current_run_collector,
    format_run_summary,
    format_timing_table,
    record_checker,
    record_document,
    record_llm_call,
    record_llm_structure,
    record_node,
)


def _idp_processing(stages, models=None, engine="docling_rapidocr"):
    return {"ocr_engine": engine, "metrics": {"stage_timings": stages, "docling_model_timings": models or {}}}


# ---------------------------------------------------------------- StageClock

def test_stage_clock_attributes_consecutive_laps():
    ticks = iter([0.0, 1.0, 3.5, 3.5, 4.0, 10.0])
    with patch("idp.utils.timing.time.perf_counter", side_effect=lambda: next(ticks)):
        clock = StageClock()          # t=0
        clock.lap("download")         # 1.0
        clock.lap("docling")          # 2.5
        clock.skip()                  # discard 0
        clock.lap("vlm")              # 0.5
        clock.lap("docling")          # 6.0 -> accumulates
    assert clock.timings == {"download": 1.0, "docling": 8.5, "vlm": 0.5}
    assert list(clock.timings) == ["download", "docling", "vlm"]  # first-seen order kept


def test_format_timing_table_percentages_and_zero_total():
    table = format_timing_table("Doc", [("docling", 3.0, "note"), ("save", 1.0)], total=4.0)
    assert "Doc (total 4.00s)" in table
    assert "75.0%   note" in table and "25.0%" in table
    assert "0.0%" in format_timing_table("Empty", [("x", 0.0)], total=0.0)


# ---------------------------------------------------------------- collector

def test_record_functions_are_noops_without_a_collector():
    assert current_run_collector() is None
    record_node("fetch_los", 1.0)
    record_document("pan", "idp", 1.0)
    record_llm_call("adjudication", 1.0, True)  # must not raise


def test_collector_is_scoped_and_reset():
    with collect_run_timings("LOAN_1", "run") as c:
        assert current_run_collector() is c
    assert current_run_collector() is None


def test_records_from_context_copying_thread_pool_land_in_collector():
    with collect_run_timings("LOAN_1", "run") as c:
        with ContextThreadPoolExecutor(max_workers=4) as ex:
            for f in [ex.submit(record_llm_call, "field_extraction", 1.0, True) for _ in range(20)]:
                f.result()
    assert len(c.llm_calls) == 20


def test_plain_threads_without_context_do_not_record():
    """Edge case: a raw Thread does not inherit the context, so it must not crash or record."""
    with collect_run_timings("LOAN_1", "run") as c:
        t = threading.Thread(target=record_node, args=("orphan", 1.0))
        t.start(); t.join()
    assert c.nodes == []


# ---------------------------------------------------------------- run summary

def _populated_collector():
    with collect_run_timings("LOAN_7", "stream") as c:
        record_node("fetch_documents", 0.5)
        record_node("idp_scan", 40.0)
        record_node("llm_structure", 6.0)
        record_node("check_parallel", 3.0)
        record_node("compile_report", 0.2)
        record_checker("check_kyc", 2.9)
        # idp doc: 30s wall, 24s inside idp (docling 18, llm 5, save-free) -> 6s wait
        record_document("application_form", "idp", 30.0,
                        _idp_processing({"download": 1.0, "docling": 18.0, "llm_field_extraction": 5.0},
                                        {"layout": 7.0, "ocr": 9.0}))
        record_document("kfs", "idp", 12.0,
                        _idp_processing({"lightonocr": 10.0, "llm_field_extraction": 2.0}, engine="lightonocr_litellm"))
        record_document("pan", "cache", 0.01, _idp_processing({"docling": 99.0}))
        record_llm_structure("application_form", 4.0)
        record_llm_call("field_extraction", 4.0, True)
        record_llm_call("adjudication", 1.5, False)
    return c


def test_run_summary_services_nodes_and_documents():
    s = build_run_summary(_populated_collector(), total_seconds=50.0)
    assert s["loan_id"] == "LOAN_7" and s["entry"] == "stream" and s["total_seconds"] == 50.0
    assert [n["node"] for n in s["nodes"]] == ["fetch_documents", "idp_scan", "llm_structure", "check_parallel", "compile_report"]
    services = {x["service"]: x["seconds"] for x in s["services"]}
    assert services["idp pod — OCR & layout (Docling / RapidOCR / TableFormer)"] == 19.0  # 1 + 18
    assert services["LightOnOCR via LiteLLM (called from idp pod)"] == 10.0
    assert services["LiteLLM — field extraction (called from idp pod)"] == 7.0
    assert services["Waiting on idp pod (queue, lock, network)"] == 6.0  # 30-24 + 12-12
    assert services["LiteLLM — field extraction (called from api pod)"] == 4.0
    assert services["LiteLLM — adjudication (called from api pod)"] == 1.5
    assert services["Verification checks (check_parallel)"] == 3.0
    assert services["Storage, LOS & reporting nodes"] == 0.7  # fetch_documents + compile_report
    assert s["llm_calls"] == {"count": 2, "failed": 1, "by_purpose": {"field_extraction": 4.0, "adjudication": 1.5}}

    docs = {d["doc_key"]: d for d in s["documents"]}
    assert [d["doc_key"] for d in s["documents"]][0] == "application_form"  # slowest first
    assert docs["application_form"]["wait_seconds"] == 6.0
    assert docs["application_form"]["docling_models"] == {"layout": 7.0, "ocr": 9.0}
    assert docs["application_form"]["llm_structure_seconds"] == 4.0
    assert docs["kfs"]["ocr_engine"] == "lightonocr_litellm"
    # cached docs cost nothing in this run: their old stage timings must not be counted
    assert docs["pan"]["idp_stages"] == {} and docs["pan"]["wait_seconds"] == 0.0
    json.dumps(s)  # persisted as JSON


def test_worker_run_labels_llm_calls_as_worker_pod():
    with collect_run_timings("LOAN_8", "celery") as c:
        record_llm_call("adjudication", 2.0, True)
    services = [x["service"] for x in build_run_summary(c, 5.0)["services"]]
    assert "LiteLLM — adjudication (called from worker pod)" in services


def test_empty_run_summary_and_log_rendering():
    with collect_run_timings("LOAN_9", "run_ocr") as c:
        pass
    s = build_run_summary(c, 0.0)
    assert s["services"] == [] and s["documents"] == [] and s["nodes"] == []
    assert "Pipeline timing summary (run_ocr)" in format_run_summary(s)


def test_run_summary_log_mentions_every_document_and_wait():
    text = format_run_summary(build_run_summary(_populated_collector(), 50.0))
    assert "application_form" in text and "[idp]" in text and "waited 6.0s" in text
    assert "Busy time by service" in text


# ---------------------------------------------------------------- graph integration

def test_run_ocr_pipeline_records_nodes_and_persists_summary(tmp_path):
    from pipeline import graph

    saved = {}
    def fake_save(loan_id, filename, data):
        saved[(loan_id, filename)] = data
        return tmp_path / filename

    def node(name):
        def fn(state):
            record_llm_call("field_extraction", 0.1, True) if name == "llm_structure" else None
            return {**state, "node_history": [*state.get("node_history", []), name]}
        return fn

    with patch.object(graph, "fetch_documents", node("fetch_documents")), \
         patch.object(graph, "idp_scan", node("idp_scan")), \
         patch.object(graph, "llm_structure", node("llm_structure")), \
         patch.object(graph, "save_s3_result", side_effect=fake_save):
        state = graph.run_ocr_pipeline("LOAN_OCR")

    assert state["node_history"] == ["fetch_documents", "idp_scan", "llm_structure"]
    summary = saved[("LOAN_OCR", "timing_summary.json")]
    assert summary["entry"] == "run_ocr"
    assert [n["node"] for n in summary["nodes"]] == ["fetch_documents", "idp_scan", "llm_structure"]
    assert summary["llm_calls"]["count"] == 1
    assert current_run_collector() is None


def test_timing_failure_never_breaks_the_run():
    """Failure mode: if persisting the summary fails, the pipeline result still comes back."""
    from pipeline import graph
    with patch.object(graph, "fetch_documents", lambda s: s), patch.object(graph, "idp_scan", lambda s: s), \
         patch.object(graph, "llm_structure", lambda s: s), \
         patch.object(graph, "save_s3_result", side_effect=OSError("disk full")):
        state = graph.run_ocr_pipeline("LOAN_X")
    assert state["loan_id"] == "LOAN_X"


def test_node_wrapper_records_even_when_node_raises():
    from pipeline.graph import _timed

    def boom(state):
        raise RuntimeError("node failed")

    with collect_run_timings("LOAN_E", "run") as c:
        with pytest.raises(RuntimeError):
            _timed("idp_scan", boom)({})
    assert [n["node"] for n in c.nodes] == ["idp_scan"]


# ---------------------------------------------------------------- llm_client

def test_invoke_llm_records_purpose_and_failure():
    from pipeline.engines import llm_client

    with collect_run_timings("LOAN_L", "run") as c, \
         patch.object(llm_client, "_invoke_llm_unmetered", side_effect=[("{}"), RuntimeError("405")]):
        llm_client.invoke_llm_json("s", "u", purpose="adjudication")
        with pytest.raises(RuntimeError):
            llm_client.invoke_llm("s", "u", purpose="field_extraction")
    assert [(x["purpose"], x["ok"]) for x in c.llm_calls] == [("adjudication", True), ("field_extraction", False)]


# ---------------------------------------------------------------- docling model timings + document timing for UI

def test_docling_model_timings_from_profiler():
    from idp.services.docling.parser import _docling_model_timings

    item = MagicMock(); item.total.return_value = 2.34567
    broken = MagicMock(); broken.total.side_effect = TypeError("bad")
    conv = MagicMock(timings={"layout": item, "ocr": broken})
    assert _docling_model_timings(conv) == {"layout": 2.346}
    assert _docling_model_timings(MagicMock(timings=None)) == {}


def test_build_document_timing_for_ui():
    from app.services.registry.normalizer import build_document_timing

    t = build_document_timing(_idp_processing({"download": 0.5, "docling": 9.5}, {"layout": 3.0}))
    assert t == {"stages": [{"stage": "download", "seconds": 0.5}, {"stage": "docling", "seconds": 9.5}],
                 "totalSeconds": 10.0, "doclingModels": {"layout": 3.0}}
    assert build_document_timing(None) is None
    assert build_document_timing({"metrics": {}}) is None  # older results


def test_case_serializer_loads_timing_summary(tmp_path):
    from app.serializers import case_serializer

    (tmp_path / "LOAN_S").mkdir()
    (tmp_path / "LOAN_S" / "timing_summary.json").write_text(json.dumps({"total_seconds": 12.0}))
    (tmp_path / "LOAN_BAD").mkdir()
    (tmp_path / "LOAN_BAD" / "timing_summary.json").write_text("{not json")
    with patch.object(case_serializer, "S3_RESULT_DIR", tmp_path):
        assert case_serializer._load_timing_summary("LOAN_S") == {"total_seconds": 12.0}
        assert case_serializer._load_timing_summary("LOAN_NONE") is None
        assert case_serializer._load_timing_summary("LOAN_BAD") is None
