"""Timing collection and end-of-lifecycle summaries.

Two levels:
- Per document (idp pod): ``StageClock`` records how long each processing stage took; the result is
  stored in ``ProcessingMetrics.stage_timings`` and logged as a table when the document finishes.
- Per case run (api or worker pod): ``collect_run_timings()`` installs a ``RunTimingCollector`` in a
  context variable for the duration of a pipeline run. Graph nodes, per-document IDP calls,
  checkers and every LLM call made in that pod record into it (thread pools here copy the
  submitter's context, so records from worker threads land in the same collector).
  ``build_run_summary`` turns it into the summary saved as ``s3_result/<id>/timing_summary.json``.
"""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional


class StageClock:
    """Lap timer for consecutive processing stages. Repeated stage names accumulate."""

    def __init__(self) -> None:
        self.timings: Dict[str, float] = {}
        self._mark = time.perf_counter()

    def lap(self, stage: str) -> float:
        """Attribute the time since the previous lap (or construction) to ``stage``; return it."""
        now = time.perf_counter()
        elapsed = now - self._mark
        self._mark = now
        self.timings[stage] = round(self.timings.get(stage, 0.0) + elapsed, 3)
        return elapsed

    def skip(self) -> None:
        """Discard the time since the previous lap without attributing it to any stage."""
        self._mark = time.perf_counter()


def format_timing_table(title: str, rows: List[tuple], total: Optional[float] = None) -> str:
    """Render ``(label, seconds[, note])`` rows as an aligned text table with % of ``total``."""
    total = total if total is not None else sum(r[1] for r in rows)
    width = max([len(r[0]) for r in rows] + [10])
    lines = [f"{title} (total {total:.2f}s)"]
    for row in rows:
        label, seconds = row[0], row[1]
        note = row[2] if len(row) > 2 and row[2] else ""
        pct = (seconds / total * 100) if total else 0.0
        lines.append(f"  {label:<{width}}  {seconds:8.2f}s  {pct:5.1f}%{('   ' + note) if note else ''}")
    return "\n".join(lines)


class RunTimingCollector:
    """Thread-safe accumulator for one case pipeline run."""

    def __init__(self, loan_id: str, entry: str) -> None:
        self.loan_id = loan_id
        self.entry = entry
        self.started_at = datetime.now(timezone.utc)
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()
        self.nodes: List[Dict[str, Any]] = []
        self.checkers: Dict[str, float] = {}
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.llm_structure: Dict[str, float] = {}
        self.llm_calls: List[Dict[str, Any]] = []

    def elapsed(self) -> float:
        return time.perf_counter() - self._t0

    def add_node(self, node: str, seconds: float) -> None:
        with self._lock:
            self.nodes.append({"node": node, "seconds": round(seconds, 3)})

    def add_checker(self, name: str, seconds: float) -> None:
        with self._lock:
            self.checkers[name] = round(seconds, 3)

    def add_document(self, doc_key: str, source: str, seconds: float, processing: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self.documents[doc_key] = {
                "doc_key": doc_key,
                "source": source,
                "seconds": round(seconds, 3),
                "processing": processing if isinstance(processing, dict) else None,
            }

    def add_llm_structure(self, doc_key: str, seconds: float) -> None:
        with self._lock:
            self.llm_structure[doc_key] = round(seconds, 3)

    def add_llm_call(self, purpose: str, seconds: float, ok: bool) -> None:
        with self._lock:
            self.llm_calls.append({"purpose": purpose, "seconds": round(seconds, 3), "ok": ok})


_RUN_COLLECTOR: contextvars.ContextVar[Optional[RunTimingCollector]] = contextvars.ContextVar(
    "dgcl_run_timing_collector", default=None
)


@contextmanager
def collect_run_timings(loan_id: str, entry: str) -> Iterator[RunTimingCollector]:
    """Install a collector for the current context (and any thread pool that copies it)."""
    collector = RunTimingCollector(loan_id, entry)
    token = _RUN_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _RUN_COLLECTOR.reset(token)


def current_run_collector() -> Optional[RunTimingCollector]:
    return _RUN_COLLECTOR.get()


def record_node(node: str, seconds: float) -> None:
    c = _RUN_COLLECTOR.get()
    if c is not None:
        c.add_node(node, seconds)


def record_checker(name: str, seconds: float) -> None:
    c = _RUN_COLLECTOR.get()
    if c is not None:
        c.add_checker(name, seconds)


def record_document(doc_key: str, source: str, seconds: float, processing: Optional[Dict[str, Any]] = None) -> None:
    c = _RUN_COLLECTOR.get()
    if c is not None:
        c.add_document(doc_key, source, seconds, processing)


def record_llm_structure(doc_key: str, seconds: float) -> None:
    c = _RUN_COLLECTOR.get()
    if c is not None:
        c.add_llm_structure(doc_key, seconds)


def record_llm_call(purpose: str, seconds: float, ok: bool) -> None:
    c = _RUN_COLLECTOR.get()
    if c is not None:
        c.add_llm_call(purpose, seconds, ok)


# Stage keys written by the idp pod (idp/services/document_processor.py) that are LLM/LightOnOCR calls
# to the LiteLLM gateway rather than work done inside the idp pod itself.
IDP_LLM_STAGE = "llm_field_extraction"
IDP_LIGHTONOCR_STAGE = "lightonocr"


def _idp_stage_timings(processing: Optional[Dict[str, Any]]) -> Dict[str, float]:
    metrics = (processing or {}).get("metrics") or {}
    stages = metrics.get("stage_timings") or {}
    return {k: float(v) for k, v in stages.items() if isinstance(v, (int, float))}


def build_run_summary(collector: RunTimingCollector, total_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Build the per-run timing summary (wall-clock per node + busy time per service).

    ``services`` sums busy time across documents. Documents are processed several at a time, so
    those sums can exceed the run's wall-clock total; ``nodes`` is the wall-clock timeline.
    """
    total = round(total_seconds if total_seconds is not None else collector.elapsed(), 3)

    documents: List[Dict[str, Any]] = []
    idp_ocr = lightonocr = idp_llm = idp_wait = 0.0
    for doc in collector.documents.values():
        stages = _idp_stage_timings(doc.get("processing")) if doc["source"] == "idp" else {}
        idp_total = sum(stages.values())
        wait = max(0.0, doc["seconds"] - idp_total) if stages else 0.0
        if doc["source"] == "idp":
            idp_llm += stages.get(IDP_LLM_STAGE, 0.0)
            lightonocr += stages.get(IDP_LIGHTONOCR_STAGE, 0.0)
            idp_ocr += idp_total - stages.get(IDP_LLM_STAGE, 0.0) - stages.get(IDP_LIGHTONOCR_STAGE, 0.0)
            idp_wait += wait
        metrics = ((doc.get("processing") or {}).get("metrics") or {}) if doc["source"] == "idp" else {}
        documents.append({
            "doc_key": doc["doc_key"],
            "source": doc["source"],
            "seconds": doc["seconds"],
            "ocr_engine": (doc.get("processing") or {}).get("ocr_engine") if doc["source"] == "idp" else None,
            "idp_stages": {k: round(v, 3) for k, v in stages.items()},
            "docling_models": metrics.get("docling_model_timings") or {},
            "wait_seconds": round(wait, 3),
            "llm_structure_seconds": collector.llm_structure.get(doc["doc_key"]),
        })
    documents.sort(key=lambda d: d["seconds"], reverse=True)

    llm_by_purpose: Dict[str, float] = {}
    llm_failures = 0
    for call in collector.llm_calls:
        llm_by_purpose[call["purpose"]] = llm_by_purpose.get(call["purpose"], 0.0) + call["seconds"]
        llm_failures += 0 if call["ok"] else 1

    node_seconds = {n["node"]: n["seconds"] for n in collector.nodes}
    orchestration = sum(node_seconds.get(n, 0.0) for n in (
        "fetch_los", "fetch_documents", "compile_report", "generate_scorecard", "push_results"))

    services = [
        {"service": "idp pod — OCR & layout (Docling / RapidOCR / TableFormer)", "seconds": idp_ocr},
        {"service": "LightOnOCR via LiteLLM (called from idp pod)", "seconds": lightonocr},
        {"service": "LiteLLM — field extraction (called from idp pod)", "seconds": idp_llm},
        {"service": "Waiting on idp pod (queue, lock, network)", "seconds": idp_wait},
    ]
    run_pod = "worker pod" if collector.entry == "celery" else "api pod"
    for purpose, secs in sorted(llm_by_purpose.items(), key=lambda kv: -kv[1]):
        services.append({"service": f"LiteLLM — {purpose.replace('_', ' ')} (called from {run_pod})", "seconds": secs})
    services.append({"service": "Verification checks (check_parallel)", "seconds": node_seconds.get("check_parallel", 0.0)})
    services.append({"service": "Storage, LOS & reporting nodes", "seconds": orchestration})
    services = [dict(s, seconds=round(s["seconds"], 3)) for s in services if s["seconds"] > 0]

    return {
        "loan_id": collector.loan_id,
        "entry": collector.entry,
        "started_at": collector.started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "total_seconds": total,
        "nodes": [dict(n) for n in collector.nodes],
        "checkers": dict(collector.checkers),
        "documents": documents,
        "llm_calls": {"count": len(collector.llm_calls), "failed": llm_failures,
                      "by_purpose": {k: round(v, 3) for k, v in llm_by_purpose.items()}},
        "services": services,
    }


def format_run_summary(summary: Dict[str, Any]) -> str:
    """Multi-table text rendering of ``build_run_summary`` output for the pod log."""
    total = summary["total_seconds"]
    parts = [format_timing_table(
        f"[{summary['loan_id']}] Pipeline timing summary ({summary['entry']}) — wall clock by node",
        [(n["node"], n["seconds"]) for n in summary["nodes"]], total)]
    if summary["services"]:
        parts.append(format_timing_table(
            "Busy time by service (documents run in parallel, so this can exceed wall clock)",
            [(s["service"], s["seconds"]) for s in summary["services"]], total))
    if summary["documents"]:
        rows = []
        for d in summary["documents"]:
            stage_note = ", ".join(f"{k} {v:.1f}s" for k, v in sorted(d["idp_stages"].items(), key=lambda kv: -kv[1])[:4])
            note = f"[{d['source']}]" + (f" {stage_note}" if stage_note else "")
            if d["wait_seconds"]:
                note += f" · waited {d['wait_seconds']:.1f}s"
            rows.append((d["doc_key"], d["seconds"], note))
        parts.append(format_timing_table("Per document (wall clock seen by the caller)", rows, total))
    return "\n".join(parts)
