"""_build_processing_steps must not surface DGCL-only pipeline stages while the DGCL
verification pipeline is disabled — the Processing Pipeline card on the case detail page
would otherwise show "KYC, Financial & Loan Application Checks", "Report Compilation &
Aggregation" and "Scorecard Generation" happening even though the flag turns that work off."""
import pytest

from app.serializers.case_serializer import _build_processing_steps

DGCL_ONLY_NODE_KEYS = {"check_parallel", "compile_report", "generate_scorecard"}
NON_DGCL_NODE_KEYS = {"fetch_los", "fetch_dms", "idp_scan", "llm_structure", "push_results"}


def _node_keys(proc_steps: list[dict]) -> set[str]:
    # ids are "step-{loan_id}-{node_key}"; node_key itself may contain underscores, so split
    # on the loan_id boundary instead of a fixed number of "-" tokens.
    return {s["id"].split("-", 2)[2] for s in proc_steps}


def test_dgcl_steps_excluded_when_pipeline_disabled(monkeypatch):
    """Happy path: flag off -> only the 5 non-DGCL nodes are returned, in original order."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: False)

    proc_steps, _ = _build_processing_steps(
        loan_id="LOAN_GATING_01",
        status_data={"node_history": ["fetch_los", "fetch_dms", "idp_scan", "llm_structure", "push_results", "done"]},
        dgcl_score=0.0,
    )

    node_keys = _node_keys(proc_steps)
    assert node_keys == NON_DGCL_NODE_KEYS
    assert node_keys.isdisjoint(DGCL_ONLY_NODE_KEYS)
    assert len(proc_steps) == 5


def test_dgcl_steps_included_when_pipeline_enabled(monkeypatch):
    """Happy path: flag on -> all 8 nodes are returned, matching the documented pipeline."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: True)

    proc_steps, _ = _build_processing_steps(
        loan_id="LOAN_GATING_02",
        status_data={"node_history": ["done"]},
        dgcl_score=96.4,
    )

    node_keys = _node_keys(proc_steps)
    assert node_keys == NON_DGCL_NODE_KEYS | DGCL_ONLY_NODE_KEYS
    assert len(proc_steps) == 8


def test_disabled_pipeline_step_ids_and_order_are_stable(monkeypatch):
    """Edge case: the remaining steps must keep their original relative order and identity,
    not just their count, so the UI timeline doesn't reorder or relabel anything."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: False)

    proc_steps, _ = _build_processing_steps(
        loan_id="LOAN_GATING_03",
        status_data={"node_history": []},
        dgcl_score=0.0,
    )

    ids_in_order = [s["id"] for s in proc_steps]
    assert ids_in_order == [
        "step-LOAN_GATING_03-fetch_los",
        "step-LOAN_GATING_03-fetch_dms",
        "step-LOAN_GATING_03-idp_scan",
        "step-LOAN_GATING_03-llm_structure",
        "step-LOAN_GATING_03-push_results",
    ]
    # None of the surviving steps claim to be the "Engine"/"Validation"/"DGCL Engine" stages.
    assert {s["component"] for s in proc_steps} == {"System", "PaddleOCR", "LLM"}


def test_disabled_pipeline_never_reports_scorecard_generation_as_done(monkeypatch):
    """Failure mode: even if node_history (e.g. stale/corrupt status data) claims the DGCL
    nodes already ran, a disabled flag must still drop them rather than surface a false
    'Scorecard Generation completed' step."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: False)

    proc_steps, _ = _build_processing_steps(
        loan_id="LOAN_GATING_04",
        status_data={"node_history": list(NON_DGCL_NODE_KEYS | DGCL_ONLY_NODE_KEYS) + ["done"]},
        dgcl_score=99.9,
    )

    assert _node_keys(proc_steps).isdisjoint(DGCL_ONLY_NODE_KEYS)
    assert all("Scorecard Generation" not in s["detail"] for s in proc_steps)
    assert all("Report Compilation" not in s["detail"] for s in proc_steps)
    assert all(s["component"] != "DGCL Engine" for s in proc_steps)
