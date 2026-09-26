"""Tests for deleting a single document (registry + DELETE /api/documents/{id}).

Every test runs as its own fresh tenant under tmp_path, with the idp mock-S3 TEMP_DIR also
redirected to tmp_path, so no real poc_data file can be touched.
"""
import json
import uuid
from pathlib import Path

import pytest

import config.tenant as tenant_mod
from config.tenant import use_tenant
from idp.core.config import settings as idp_settings


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated tenant + idp TEMP_DIR; yields (registry, tenant_root, idp_tenant_dir)."""
    monkeypatch.setattr(tenant_mod, "TENANTS_ROOT", tmp_path / "tenants")
    monkeypatch.setattr(idp_settings, "TEMP_DIR", str(tmp_path / "idp_temp"))
    tenant = f"t_del{uuid.uuid4().hex[:10]}"
    with use_tenant(tenant):
        from app.services.document_registry import DocumentRegistry
        root = tmp_path / "tenants" / tenant
        idp_dir = tmp_path / "idp_temp" / "s3_mock" / idp_settings.S3_BUCKET / tenant
        yield DocumentRegistry(), root, idp_dir


def _write(path: Path, content: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def _general_upload(reg, root, idp_dir, doc_id, name="PAN CARD.pdf"):
    raw = _write(root / "s3_raw" / "GENERAL" / name)
    idp_raw = _write(idp_dir / idp_settings.RAW_DOCUMENT_PREFIX / f"{doc_id}_{name}")
    parsed = {"document_id": doc_id, "text": "PERMANENT ACCOUNT NUMBER ABCDE1234F",
              "source": {"filename": name}, "processing": {}}
    idp_parsed = _write(idp_dir / idp_settings.PARSED_DOCUMENT_PREFIX / f"{doc_id}.json", json.dumps(parsed))
    reg.register_uploaded_document(doc_id=doc_id, filename=name, case_id=None, parsed_result=parsed)
    return raw, idp_raw, idp_parsed


def test_delete_general_upload_removes_record_and_every_copy(iso):
    reg, root, idp_dir = iso
    raw, idp_raw, idp_parsed = _general_upload(reg, root, idp_dir, "DOC-111111")
    assert reg.get_by_id("DOC-111111")["rawText"].startswith("PERMANENT")

    result = reg.delete_document("DOC-111111")

    assert result["caseId"] == "GENERAL" and result["errors"] == []
    assert not raw.exists() and not idp_raw.exists() and not idp_parsed.exists()
    assert reg.get_by_id("DOC-111111") is None
    assert all(d["id"] != "DOC-111111" for d in reg.list_all())


def test_shared_raw_file_kept_until_last_document_is_deleted(iso):
    """Edge case: two General uploads with the same filename share s3_raw/GENERAL/<name>."""
    reg, root, idp_dir = iso
    raw, _, _ = _general_upload(reg, root, idp_dir, "DOC-AAAAAA")
    _general_upload(reg, root, idp_dir, "DOC-BBBBBB")

    reg.delete_document("DOC-AAAAAA")
    assert raw.exists(), "still referenced by DOC-BBBBBB"
    assert reg.get_by_id("DOC-BBBBBB") is not None

    reg.delete_document("DOC-BBBBBB")
    assert not raw.exists()


def test_delete_case_document_removes_raw_dms_and_ocr_tiers(iso):
    reg, root, idp_dir = iso
    case = "LOAN_900"
    _write(root / "los" / "loans" / f"{case}.json", json.dumps({"loan_id": case}))
    raw = _write(root / "s3_raw" / case / "pan_card.pdf")
    dms = _write(root / "dms" / case / "pan_card.pdf")
    extracted = _write(root / "s3_extracted" / case / "kyc_pan.json", json.dumps({"_raw_text": "PAN TEXT"}))
    structured = _write(root / "s3_extracted_structured" / case / "pan.json", json.dumps({"pan_number": "ABCDE1234F"}))
    idp_parsed = _write(idp_dir / idp_settings.PARSED_DOCUMENT_PREFIX / f"{case}_pan.json",
                        json.dumps({"document_id": f"{case}_pan", "text": "PAN TEXT", "source": {"filename": "pan_card.pdf"}}))
    other = _write(root / "s3_raw" / case / "kfs.pdf")

    doc = next(d for d in reg.list_all(case_id=case) if d.get("name") == "pan_card.pdf")
    result = reg.delete_document(doc["id"])

    assert result["caseId"] == case and result["errors"] == []
    for p in (raw, dms, extracted, structured, idp_parsed):
        assert not p.exists(), p
    assert other.exists(), "other case documents are untouched"
    assert all(d.get("name") != "pan_card.pdf" for d in reg.list_all(case_id=case))


def test_unknown_document_returns_none(iso):
    reg, _, _ = iso
    assert reg.delete_document("DOC-NOPE") is None


def test_unsafe_filename_is_never_joined_onto_storage(iso):
    """Failure mode: a record whose name escapes the directory must not delete anything outside it."""
    reg, root, idp_dir = iso
    victim = _write(root / "s3_raw" / "victim.pdf")
    reg.register_uploaded_document(doc_id="DOC-EVIL01", filename="../victim.pdf", case_id=None, parsed_result=None)
    reg._dynamic_docs["DOC-EVIL01"]["name"] = "../victim.pdf"  # normalizer may strip it; force the bad value

    result = reg.delete_document("DOC-EVIL01")

    assert victim.exists()
    assert any("unsafe document name" in e for e in result["errors"])
    assert reg.get_by_id("DOC-EVIL01") is None


def test_api_delete_unknown_document_is_404():
    from fastapi.testclient import TestClient
    from app.main import app

    resp = TestClient(app).delete("/api/documents/DOC-DOES-NOT-EXIST-XYZ")
    assert resp.status_code == 404
