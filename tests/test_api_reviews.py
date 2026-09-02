from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_reviews_list_and_adjudicate():
    # 1. Get reviews list
    res = client.get("/api/reviews")
    assert res.status_code == 200
    reviews = res.json()
    assert isinstance(reviews, list)
    assert len(reviews) > 0

    target_review = reviews[0]
    review_id = target_review["id"]

    # 2. Get single review
    get_res = client.get(f"/api/reviews/{review_id}")
    assert get_res.status_code == 200
    item = get_res.json()
    assert item["id"] == review_id

    # 3. Adjudicate review
    adj_payload = {
        "decision": "APPROVE",
        "notes": "Verified applicant identity manually via secondary government ID.",
        "assignedTo": "Senior Credit Officer",
    }
    adj_res = client.post(f"/api/reviews/{review_id}/adjudicate", json=adj_payload)
    assert adj_res.status_code == 200
    adj_data = adj_res.json()
    assert adj_data["status"] == "success"
    assert adj_data["decision"] == "APPROVE"


def test_api_documents_list_and_preview():
    res = client.get("/api/documents")
    assert res.status_code == 200
    docs = res.json()
    assert isinstance(docs, list)
    assert len(docs) > 0

    doc_id = docs[0]["id"]
    get_doc = client.get(f"/api/documents/{doc_id}")
    assert get_doc.status_code == 200

    # Preview stream
    prev_res = client.get("/api/documents/preview/LOAN_001/kfs.pdf")
    assert prev_res.status_code == 200
    assert prev_res.content is not None
