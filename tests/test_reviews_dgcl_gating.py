"""Review items are generated from DGCL checkpoint results (INDETERMINATE/DISCREPANCY), so
they must not appear while the DGCL verification pipeline is disabled — otherwise the case
detail page's "Review Items" panel, the Review Queue page and Human Review page all claim
DGCL flagged issues that were never actually checked."""
from fastapi.testclient import TestClient

from app.main import app
from app.routers.reviews import _generate_review_items

client = TestClient(app)


def test_generate_review_items_empty_when_pipeline_disabled(monkeypatch):
    """Happy path: flag off -> no review items are generated at all, regardless of how many
    cases have INDETERMINATE/DISCREPANCY checkpoints on disk."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: False)

    assert _generate_review_items() == []


def test_generate_review_items_populated_when_pipeline_enabled(monkeypatch):
    """Happy path: flag on -> the existing checkpoint-derived generation still runs."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: True)

    items = _generate_review_items()
    assert isinstance(items, list)
    assert len(items) > 0
    assert all("checkpointId" in item for item in items)


def test_list_reviews_endpoint_empty_when_pipeline_disabled(monkeypatch):
    """Edge case: the list endpoint (used by CasesPage, ReviewQueuePage, CaseDetailPage) must
    return 200 with an empty list, not an error, so pages render a normal empty state."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: False)

    res = client.get("/api/reviews")
    assert res.status_code == 200
    assert res.json() == []

    res_filtered = client.get("/api/reviews", params={"caseId": "LOAN_002"})
    assert res_filtered.status_code == 200
    assert res_filtered.json() == []


def test_get_single_review_404_when_pipeline_disabled(monkeypatch):
    """Failure mode: a review id that was valid while the pipeline was enabled must not be
    fetchable (or adjudicable) once it's turned off — there is nothing behind that id anymore."""
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: True)
    items = _generate_review_items()
    assert len(items) > 0
    review_id = items[0]["id"]

    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: False)

    res = client.get(f"/api/reviews/{review_id}")
    assert res.status_code == 404

    adj_res = client.post(
        f"/api/reviews/{review_id}/adjudicate",
        json={"decision": "APPROVE", "notes": "should not apply", "assignedTo": "Tester"},
    )
    assert adj_res.status_code == 404
