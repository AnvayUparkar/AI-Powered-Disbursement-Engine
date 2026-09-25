"""Tenant isolation at the storage / registry / worker layers (no HTTP)."""
import asyncio
import threading
from pathlib import Path

import pytest

from config.tenant import (
    ContextThreadPoolExecutor,
    TenantNotSetError,
    UnsafePathError,
    current_tenant_id,
    is_within_tenant,
    iter_in_current_context,
    safe_id,
    safe_join,
    use_tenant,
    validate_tenant_id,
)

pytestmark = pytest.mark.no_default_tenant

TENANT_A = "t_aaaaaaaa"
TENANT_B = "t_bbbbbbbb"


@pytest.fixture(autouse=True)
def isolated_tenants_root(tmp_path, monkeypatch):
    monkeypatch.setattr("config.tenant.TENANTS_ROOT", tmp_path / "tenants")
    return tmp_path / "tenants"


# ── tenant id / path guards ──────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["LOAN_001", "HDB-2024.a", "APPL123", "GENERAL", "DOC-ABCD1234"])
def test_safe_id_accepts_real_case_ids(value):
    assert safe_id(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "../x", "a/b", "a\\b", "..", ".hidden", "a..b", "x" * 129, "id with space", "id\x00", None, 5],
)
def test_safe_id_rejects_traversal_and_junk(value):
    with pytest.raises(UnsafePathError):
        safe_id(value)


@pytest.mark.parametrize("value", ["t_abcdef01", "t_" + "0" * 16])
def test_validate_tenant_id_accepts_generated_ids(value):
    assert validate_tenant_id(value) == value


@pytest.mark.parametrize("value", ["", "T_UPPER", "../t_a", "t a", "ab", None])
def test_validate_tenant_id_rejects_bad_ids(value):
    with pytest.raises(ValueError):
        validate_tenant_id(value)


def test_safe_join_blocks_escape_and_allows_inside(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    assert safe_join(base, "sub", "f.pdf") == (base / "sub" / "f.pdf").resolve()
    with pytest.raises(UnsafePathError):
        safe_join(base, "..", "other")
    with pytest.raises(UnsafePathError):
        safe_join(base, "/etc/passwd")


def test_current_tenant_id_raises_when_unset():
    with pytest.raises(TenantNotSetError):
        current_tenant_id()


def test_use_tenant_restores_previous_binding():
    with use_tenant(TENANT_A):
        with use_tenant(TENANT_B):
            assert current_tenant_id() == TENANT_B
        assert current_tenant_id() == TENANT_A
    with pytest.raises(TenantNotSetError):
        current_tenant_id()


# ── tenant-aware path constants ──────────────────────────────────────────────

def test_storage_constants_refuse_to_resolve_without_tenant():
    from config import S3_RAW_DIR

    with pytest.raises(TenantNotSetError):
        S3_RAW_DIR / "LOAN_001"


def test_storage_constants_resolve_under_current_tenant(isolated_tenants_root):
    from config import DMS_DIR, LOS_LOANS_DIR, S3_RAW_DIR

    with use_tenant(TENANT_A):
        assert S3_RAW_DIR / "LOAN_001" == isolated_tenants_root / TENANT_A / "s3_raw" / "LOAN_001"
        assert LOS_LOANS_DIR / "x.json" == isolated_tenants_root / TENANT_A / "los" / "loans" / "x.json"
        assert DMS_DIR.exists()
        assert (isolated_tenants_root / TENANT_A / "dms").is_dir()
    with use_tenant(TENANT_B):
        assert S3_RAW_DIR / "LOAN_001" == isolated_tenants_root / TENANT_B / "s3_raw" / "LOAN_001"


def test_is_within_tenant(tmp_path, isolated_tenants_root):
    with use_tenant(TENANT_A):
        inside = isolated_tenants_root / TENANT_A / "s3_raw" / "f.pdf"
        other = isolated_tenants_root / TENANT_B / "s3_raw" / "f.pdf"
        assert is_within_tenant(inside)
        assert not is_within_tenant(other)
        assert not is_within_tenant(tmp_path / "elsewhere.pdf")
        assert not is_within_tenant(isolated_tenants_root / TENANT_A / ".." / TENANT_B / "f.pdf")


# ── pipeline.storage isolation ───────────────────────────────────────────────

def test_same_loan_id_is_isolated_between_tenants():
    from pipeline.storage import get_s3_result, list_loan_ids, save_s3_los, save_s3_result

    with use_tenant(TENANT_A):
        save_s3_los("LOAN_001", {"loan_id": "LOAN_001", "applicant_name": "Alice"})
        save_s3_result("LOAN_001", "scorecard.json", {"score": 91})
    with use_tenant(TENANT_B):
        assert list_loan_ids() == []
        assert get_s3_result("LOAN_001", "scorecard.json") is None
        save_s3_los("LOAN_001", {"loan_id": "LOAN_001", "applicant_name": "Bob"})
        save_s3_result("LOAN_001", "scorecard.json", {"score": 12})

    with use_tenant(TENANT_A):
        assert list_loan_ids() == ["LOAN_001"]
        assert get_s3_result("LOAN_001", "scorecard.json") == {"score": 91}
    with use_tenant(TENANT_B):
        assert get_s3_result("LOAN_001", "scorecard.json") == {"score": 12}


def test_storage_accessors_reject_unsafe_loan_ids():
    from pipeline.storage import (
        delete_loan_data,
        get_s3_extracted,
        get_s3_los,
        save_s3_result,
        update_status,
    )

    with use_tenant(TENANT_A):
        for bad in ("../t_bbbbbbbb/LOAN_001", "..", "a/b", ""):
            with pytest.raises(UnsafePathError):
                save_s3_result(bad, "x.json", {})
            with pytest.raises(UnsafePathError):
                get_s3_los(bad)
            with pytest.raises(UnsafePathError):
                get_s3_extracted(bad, "pan")
            with pytest.raises(UnsafePathError):
                update_status(bad, "fetch_los")
            with pytest.raises(UnsafePathError):
                delete_loan_data(bad)


def test_delete_loan_data_only_touches_current_tenant(tmp_path, monkeypatch):
    from config import S3_RAW_DIR
    from pipeline.storage import delete_loan_data, get_s3_result, save_s3_los, save_s3_result

    monkeypatch.setattr("idp.core.config.settings.TEMP_DIR", str(tmp_path / "idp_temp"))
    for tenant in (TENANT_A, TENANT_B):
        with use_tenant(tenant):
            save_s3_los("LOAN_001", {"loan_id": "LOAN_001"})
            save_s3_result("LOAN_001", "scorecard.json", {"t": tenant})
            (S3_RAW_DIR / "LOAN_001").mkdir(parents=True, exist_ok=True)
            (S3_RAW_DIR / "LOAN_001" / "pan.pdf").write_bytes(b"%PDF")

    with use_tenant(TENANT_A):
        result = delete_loan_data("LOAN_001")
        assert result["errors"] == []
        assert result["deleted"]
        assert not (S3_RAW_DIR / "LOAN_001").exists()

    with use_tenant(TENANT_B):
        assert (S3_RAW_DIR / "LOAN_001" / "pan.pdf").read_bytes() == b"%PDF"
        assert get_s3_result("LOAN_001", "scorecard.json") == {"t": TENANT_B}


def test_delete_loan_data_idp_temp_cleanup_is_tenant_scoped_and_exact(tmp_path, monkeypatch):
    """LOAN_1 must not delete LOAN_10's uploads, and never another tenant's files."""
    from idp.core.config import settings as idp_settings
    from pipeline.storage import delete_loan_data, save_s3_los

    temp_dir = tmp_path / "idp_temp"
    monkeypatch.setattr("idp.core.config.settings.TEMP_DIR", str(temp_dir))
    base = temp_dir / "s3_mock" / idp_settings.S3_BUCKET

    def touch(tenant: str, name: str) -> Path:
        f = base / tenant / "raw-documents" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        return f

    mine = touch(TENANT_A, "DOC-LOAN_1-9560_sanction.pdf")
    longer_id = touch(TENANT_A, "DOC-LOAN_10-1111_sanction.pdf")
    theirs = touch(TENANT_B, "DOC-LOAN_1-9560_sanction.pdf")
    with use_tenant(TENANT_A):
        save_s3_los("LOAN_1", {"loan_id": "LOAN_1"})
        delete_loan_data("LOAN_1")

    assert not mine.exists()
    assert longer_id.exists()
    assert theirs.exists()


# ── per-tenant in-memory state ───────────────────────────────────────────────

def test_document_registry_is_per_tenant():
    from app.services.document_registry import document_registry

    with use_tenant(TENANT_A):
        document_registry.register_uploaded_document(doc_id="DOC-A1", filename="pan.pdf", case_id="LOAN_001")
        assert document_registry.get_by_id("DOC-A1") is not None
    with use_tenant(TENANT_B):
        assert document_registry.get_by_id("DOC-A1") is None
        assert "DOC-A1" not in {d["id"] for d in document_registry.list_all()}
        document_registry.register_uploaded_document(doc_id="DOC-B1", filename="pan.pdf", case_id="LOAN_001")
    with use_tenant(TENANT_A):
        assert document_registry.get_by_id("DOC-B1") is None
        assert document_registry.get_by_id("DOC-A1") is not None


def test_document_registry_refuses_use_without_tenant():
    from app.services.document_registry import document_registry

    with pytest.raises(TenantNotSetError):
        document_registry.list_all()


def test_case_document_cache_is_keyed_per_tenant():
    from app.services.registry import case_scanner
    from config import S3_RAW_DIR
    from pipeline.storage import save_s3_los

    case_scanner._CASE_DOCS_CACHE.clear()
    with use_tenant(TENANT_A):
        save_s3_los("LOAN_001", {"loan_id": "LOAN_001"})
        (S3_RAW_DIR / "LOAN_001").mkdir(parents=True, exist_ok=True)
        (S3_RAW_DIR / "LOAN_001" / "pan.pdf").write_bytes(b"%PDF")
        a_docs = case_scanner.scan_case_documents()
        assert any(d["caseId"] == "LOAN_001" for d in a_docs)

    with use_tenant(TENANT_B):
        # A's cache entry is fresh (within TTL), yet B must not see it.
        assert case_scanner.scan_case_documents() == []
        case_scanner.invalidate_case_cache()

    with use_tenant(TENANT_A):
        assert TENANT_A in case_scanner._CASE_DOCS_CACHE  # B's invalidation did not evict A
        assert any(d["caseId"] == "LOAN_001" for d in case_scanner.scan_case_documents())


# ── context propagation into threads / generators ────────────────────────────

def test_context_thread_pool_propagates_tenant():
    with use_tenant(TENANT_A):
        with ContextThreadPoolExecutor(max_workers=3) as pool:
            seen = [f.result() for f in [pool.submit(current_tenant_id) for _ in range(6)]]
            mapped = list(pool.map(lambda _: current_tenant_id(), range(3)))
    assert seen == [TENANT_A] * 6
    assert mapped == [TENANT_A] * 3


def test_context_thread_pool_does_not_leak_across_submitters():
    with ContextThreadPoolExecutor(max_workers=1) as pool:
        with use_tenant(TENANT_A):
            assert pool.submit(current_tenant_id).result() == TENANT_A
        with use_tenant(TENANT_B):
            assert pool.submit(current_tenant_id).result() == TENANT_B
        with pytest.raises(TenantNotSetError):
            pool.submit(current_tenant_id).result()


def test_iter_in_current_context_keeps_tenant_when_pulled_from_other_threads():
    def gen():
        for _ in range(3):
            yield current_tenant_id()

    with use_tenant(TENANT_A):
        it = iter_in_current_context(gen())

    results = []

    def pull():
        results.append(next(it))

    for _ in range(3):
        t = threading.Thread(target=pull)  # bare threads start with an EMPTY context
        t.start()
        t.join()
    assert results == [TENANT_A] * 3


def test_parallel_checkers_run_as_the_calling_tenant(monkeypatch):
    import pipeline.graph as graph

    seen = {}

    def fake(name):
        def _checker(state):
            seen[name] = current_tenant_id()
            return {"records": [], "rollup": "Verified"}
        return _checker

    monkeypatch.setattr(graph, "check_kyc", fake("kyc"))
    monkeypatch.setattr(graph, "check_financial", fake("fin"))
    monkeypatch.setattr(graph, "check_loan_app", fake("app"))
    monkeypatch.setattr(graph, "update_status", lambda *a, **k: {})

    with use_tenant(TENANT_B):
        graph._run_parallel_checkers({"loan_id": "LOAN_001"})
    assert seen == {"kyc": TENANT_B, "fin": TENANT_B, "app": TENANT_B}


def test_langgraph_nodes_inherit_tenant_context():
    """The real pipeline runs on LangGraph; its sync nodes must see the invoker's tenant."""
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class S(TypedDict, total=False):
        tenant: str

    def node(state: S) -> S:
        return {"tenant": current_tenant_id()}

    builder = StateGraph(S)
    builder.add_node("n", node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    compiled = builder.compile()

    with use_tenant(TENANT_A):
        assert compiled.invoke({})["tenant"] == TENANT_A
        streamed = [chunk["n"]["tenant"] for chunk in compiled.stream({})]
    assert streamed == [TENANT_A]


# ── S3 storage (mock mode) ───────────────────────────────────────────────────

@pytest.fixture
def s3(tmp_path, monkeypatch):
    from idp.services.storage.s3 import S3Storage

    monkeypatch.setattr("idp.core.config.settings.TEMP_DIR", str(tmp_path / "idp_temp"))
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    storage = S3Storage()
    storage._boto_client = "MOCK"
    return storage


def test_s3_upload_then_download_roundtrip_within_tenant(s3, tmp_path):
    key = f"{TENANT_A}/raw-documents/DOC-1_pan.pdf"
    with use_tenant(TENANT_A):
        asyncio.run(s3.upload(key=key, content=b"%PDF-mine", doc_id="DOC-1"))
        dest = tmp_path / "out" / "pan.pdf"
        asyncio.run(s3.download(key=key, dest_path=str(dest), doc_id="DOC-1"))
    assert dest.read_bytes() == b"%PDF-mine"


def test_s3_rejects_keys_outside_current_tenant_namespace(s3, tmp_path):
    from idp.core.exceptions import S3Error

    with use_tenant(TENANT_B):
        for key in (
            f"{TENANT_A}/raw-documents/DOC-1_pan.pdf",        # another tenant's namespace
            "raw-documents/DOC-1_pan.pdf",                    # no tenant prefix at all
            f"{TENANT_B}/../{TENANT_A}/raw-documents/x.pdf",  # traversal back out
        ):
            with pytest.raises(S3Error):
                asyncio.run(s3.upload(key=key, content=b"x"))
            with pytest.raises(S3Error):
                asyncio.run(s3.download(key=key, dest_path=str(tmp_path / "o.pdf")))


def test_s3_tenant_cannot_read_other_tenants_object(s3, tmp_path):
    from idp.core.exceptions import S3Error

    with use_tenant(TENANT_A):
        asyncio.run(s3.upload(key=f"{TENANT_A}/raw-documents/secret.pdf", content=b"%PDF-secret"))
    with use_tenant(TENANT_B):
        with pytest.raises(S3Error):
            asyncio.run(s3.download(key=f"{TENANT_A}/raw-documents/secret.pdf", dest_path=str(tmp_path / "s.pdf")))


def test_s3_download_local_path_must_be_inside_tenant(s3, tmp_path, isolated_tenants_root):
    from idp.core.exceptions import S3Error

    outside = tmp_path / "server_secret.txt"
    outside.write_bytes(b"root:x:0:0")
    other_tenant_file = isolated_tenants_root / TENANT_A / "s3_raw" / "LOAN_001" / "pan.pdf"
    other_tenant_file.parent.mkdir(parents=True)
    other_tenant_file.write_bytes(b"%PDF-A")
    own_file = isolated_tenants_root / TENANT_B / "s3_raw" / "LOAN_001" / "pan.pdf"
    own_file.parent.mkdir(parents=True)
    own_file.write_bytes(b"%PDF-B")

    with use_tenant(TENANT_B):
        for forbidden in (outside, other_tenant_file):
            with pytest.raises(S3Error):
                asyncio.run(s3.download(key=str(forbidden), dest_path=str(tmp_path / "d1")))
        dest = tmp_path / "d2.pdf"
        asyncio.run(s3.download(key=str(own_file), dest_path=str(dest)))
        assert dest.read_bytes() == b"%PDF-B"


def test_s3_bucket_override_is_rejected(s3):
    from idp.core.exceptions import S3Error

    with use_tenant(TENANT_A):
        for bucket in ("someone-elses-bucket", "../../etc"):
            with pytest.raises((S3Error, UnsafePathError)):
                asyncio.run(s3.upload(key=f"{TENANT_A}/raw-documents/x.pdf", content=b"x", bucket=bucket))


# ── Celery tasks carry the tenant ────────────────────────────────────────────

def test_run_pipeline_task_executes_as_given_tenant_and_resets(monkeypatch):
    from pipeline.celery_app import run_pipeline_task

    # DGCL verification pipeline is disabled by default (app/services/pipeline_flags.py);
    # this test exercises tenant propagation itself, not the flag, so enable it explicitly.
    monkeypatch.setattr("app.services.pipeline_flags.is_dgcl_pipeline_enabled", lambda: True)

    seen = {}

    def fake_run_pipeline(loan_id):
        seen["tenant"] = current_tenant_id()
        return {"scorecard": {"overall_score": 1}, "errors": []}

    monkeypatch.setattr("pipeline.graph.run_pipeline", fake_run_pipeline)
    result = run_pipeline_task.apply(args=["LOAN_001", TENANT_B]).get()

    assert seen["tenant"] == TENANT_B
    assert result["status"] == "completed"
    with pytest.raises(TenantNotSetError):
        current_tenant_id()


def test_run_pipeline_task_rejects_invalid_tenant_id():
    from pipeline.celery_app import run_pipeline_task

    res = run_pipeline_task.apply(args=["LOAN_001", "../t_other"])
    assert res.failed()
    assert isinstance(res.result, ValueError)


def test_process_document_task_sends_tenant_credentials_to_idp(monkeypatch, tmp_path):
    import app.auth as auth
    import pipeline.celery_app as celery_app_module

    monkeypatch.setattr(auth, "INTERNAL_API_TOKEN", "s3cret-internal")
    calls = []

    class MockResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class MockClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            calls.append(("post", headers, current_tenant_id()))
            return MockResponse({"status": "completed", "result": {}})

        def get(self, url, headers=None):
            calls.append(("get", headers, current_tenant_id()))
            return MockResponse({})

    monkeypatch.setattr(celery_app_module.httpx, "Client", MockClient)
    doc = tmp_path / "pan.pdf"
    doc.write_bytes(b"%PDF")

    with use_tenant(TENANT_A):  # the caller's own tenant must not leak into the task's tenant
        res = celery_app_module.process_document_task.apply(args=["DOC-9", str(doc), TENANT_B, None]).get()

    assert res["status"] == "completed"
    assert [c[0] for c in calls] == ["post", "get"]
    for _, headers, tenant_at_call in calls:
        assert headers == {"x-internal-token": "s3cret-internal", "x-tenant-id": TENANT_B}
        assert tenant_at_call == TENANT_B

    from app.services.document_registry import document_registry

    with use_tenant(TENANT_B):
        assert document_registry.get_by_id("DOC-9") is not None
    with use_tenant(TENANT_A):
        assert document_registry.get_by_id("DOC-9") is None
