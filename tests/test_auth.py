"""Signup / login / session behaviour and end-to-end tenant isolation over HTTP (real auth, no bypass)."""
import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

import app.auth as auth
from app.main import app

pytestmark = [pytest.mark.real_auth, pytest.mark.no_default_tenant]

PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("config.tenant.TENANTS_ROOT", tmp_path / "tenants")
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setattr("app.services.registry.case_scanner._CASE_DOCS_CACHE", {})
    auth._failed_attempts.clear()
    auth.init_db()
    return tmp_path


def new_client() -> TestClient:
    """Each TestClient has its own cookie jar, i.e. its own browser."""
    return TestClient(app)


def signup(client: TestClient, username: str, password: str = PASSWORD):
    return client.post("/api/auth/signup", json={"username": username, "password": password})


@pytest.fixture
def alice(env):
    c = new_client()
    assert signup(c, "alice").status_code == 200
    return c


@pytest.fixture
def bob(env):
    c = new_client()
    assert signup(c, "bob").status_code == 200
    return c


# ── authentication required ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/cases"),
        ("get", "/api/cases/LOAN_001"),
        ("get", "/api/cases/next-id"),
        ("post", "/api/cases/create"),
        ("delete", "/api/cases/LOAN_001"),
        ("post", "/api/cases/LOAN_001/run"),
        ("get", "/api/cases/LOAN_001/stream"),
        ("get", "/api/documents"),
        ("get", "/api/documents/preview/LOAN_001/pan.pdf"),
        ("get", "/api/v1/documents/DOC-1"),
        ("post", "/api/v1/documents/process"),
        ("post", "/api/v1/documents/upload"),
    ],
)
def test_data_routes_require_login(env, method, path):
    assert getattr(new_client(), method)(path).status_code == 401


def test_every_data_route_is_protected(env):
    """No route except health and /api/auth/* may be reachable without a session."""
    open_paths = {"/health", "/api/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    client = new_client()
    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path in open_paths or path.startswith("/api/auth/") or not methods:
            continue
        concrete = path
        for m in methods - {"HEAD", "OPTIONS"}:
            concrete = re.sub(r"\{[^}]+\}", "x", concrete)
            resp = client.request(m, concrete)
            if resp.status_code != 401:
                unprotected.append((m, path, resp.status_code))
    assert unprotected == []


def test_health_stays_open(env):
    assert new_client().get("/health").status_code == 200


def test_api_health_alias_is_open_for_the_browser_client(env):
    """The UI's health check goes through the same /api proxy as everything else."""
    resp = new_client().get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_session_lookup_creates_schema_lazily_on_a_fresh_database(tmp_path, monkeypatch):
    """A process that never ran init_db() (the IDP app) must not crash on its first cookie."""
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(tmp_path / "fresh" / "auth.db"))
    assert auth.lookup_session("some-cookie-value") is None
    assert (tmp_path / "fresh" / "auth.db").exists()
    with sqlite3.connect(auth.AUTH_DB_PATH) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tenants", "users", "sessions"} <= tables


def test_session_lookup_without_cookie_never_touches_the_database(tmp_path, monkeypatch):
    db = tmp_path / "never" / "auth.db"
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(db))
    assert auth.lookup_session(None) is None
    assert auth.lookup_session("") is None
    assert not db.parent.exists()


def test_forged_or_garbage_cookie_is_rejected(env):
    c = new_client()
    c.cookies.set(auth.SESSION_COOKIE, "definitely-not-a-real-token")
    assert c.get("/api/cases").status_code == 401
    assert c.get("/api/auth/me").status_code == 401


# ── signup ───────────────────────────────────────────────────────────────────

def test_signup_logs_in_and_sets_secure_cookie_flags(env):
    c = new_client()
    resp = signup(c, "alice")
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice"}
    set_cookie = resp.headers["set-cookie"]
    assert auth.SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert c.get("/api/auth/me").json() == {"username": "alice"}
    assert c.get("/api/cases").status_code == 200


@pytest.mark.parametrize("username", ["ab", "has space", "bad/slash", "../x", "", "x" * 33, "üser"])
def test_signup_rejects_bad_usernames(env, username):
    assert signup(new_client(), username).status_code == 400


@pytest.mark.parametrize("password", ["", "short", "x" * 7, "x" * 129])
def test_signup_rejects_bad_passwords(env, password):
    assert signup(new_client(), "carol", password).status_code == 400


def test_signup_duplicate_username_is_rejected_case_insensitively(env, alice):
    assert signup(new_client(), "alice").status_code == 409
    assert signup(new_client(), "ALICE").status_code == 409


def test_signup_rejects_non_string_credentials(env):
    resp = new_client().post("/api/auth/signup", json={"username": 123, "password": ["x"]})
    assert resp.status_code == 422


def test_each_signup_gets_its_own_tenant(env, alice, bob):
    with sqlite3.connect(auth.AUTH_DB_PATH) as conn:
        tenants = [r[0] for r in conn.execute("SELECT tenant_id FROM users ORDER BY id")]
    assert len(tenants) == 2 and tenants[0] != tenants[1]
    assert all(t.startswith("t_") for t in tenants)


def test_password_and_session_token_are_stored_hashed(env):
    c = new_client()
    signup(c, "alice")
    raw_token = c.cookies.get(auth.SESSION_COOKIE)
    with sqlite3.connect(auth.AUTH_DB_PATH) as conn:
        (pw_hash,) = conn.execute("SELECT password_hash FROM users").fetchone()
        (token_hash,) = conn.execute("SELECT token_hash FROM sessions").fetchone()
    assert PASSWORD not in pw_hash and pw_hash.startswith("scrypt$")
    assert raw_token != token_hash and raw_token not in token_hash


# ── login / logout / session lifetime ────────────────────────────────────────

def test_login_with_correct_password_creates_new_session(env, alice):
    other_browser = new_client()
    resp = other_browser.post("/api/auth/login", json={"username": "ALICE", "password": PASSWORD})
    assert resp.status_code == 200
    assert other_browser.get("/api/auth/me").status_code == 200


def test_login_failures_are_generic(env, alice):
    wrong_pw = new_client().post("/api/auth/login", json={"username": "alice", "password": "wrong password!"})
    no_user = new_client().post("/api/auth/login", json={"username": "nobody", "password": "wrong password!"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_logout_invalidates_the_session_server_side(env, alice):
    stolen = alice.cookies.get(auth.SESSION_COOKIE)
    assert alice.post("/api/auth/logout").status_code == 200
    assert alice.get("/api/auth/me").status_code == 401

    replay = new_client()
    replay.cookies.set(auth.SESSION_COOKIE, stolen)
    assert replay.get("/api/cases").status_code == 401


def test_expired_session_is_rejected(env, alice):
    with sqlite3.connect(auth.AUTH_DB_PATH) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
    assert alice.get("/api/cases").status_code == 401


def test_login_is_throttled_after_repeated_failures(env, alice):
    c = new_client()
    codes = [
        c.post("/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"}).status_code
        for _ in range(auth._MAX_FAILED_ATTEMPTS + 2)
    ]
    assert codes[: auth._MAX_FAILED_ATTEMPTS] == [401] * auth._MAX_FAILED_ATTEMPTS
    assert codes[-1] == 429
    # even the right password is refused while throttled
    assert c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 429


def test_successful_login_clears_failure_counter(env, alice):
    c = new_client()
    for _ in range(auth._MAX_FAILED_ATTEMPTS - 1):
        c.post("/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"})
    assert c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    for _ in range(auth._MAX_FAILED_ATTEMPTS - 1):
        assert c.post("/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"}).status_code == 401


def test_password_hash_verifies_and_rejects_tampering():
    stored = auth.hash_password("s3cret-pass")
    assert auth.verify_password("s3cret-pass", stored)
    assert not auth.verify_password("S3cret-pass", stored)
    assert not auth.verify_password("s3cret-pass", stored[:-4] + "AAAA")
    assert not auth.verify_password("s3cret-pass", "garbage")
    assert auth.hash_password("s3cret-pass") != stored  # salted


# ── end-to-end isolation between two logged-in tenants ───────────────────────

def create_case(client: TestClient, case_id: str, applicant: str):
    return client.post("/api/cases/create", json={"case_id": case_id, "applicant_name": applicant, "loan_amount": 1000})


def test_tenants_only_see_their_own_cases(alice, bob):
    assert create_case(alice, "LOAN_001", "Alice Applicant").status_code == 200

    assert [c["id"] for c in alice.get("/api/cases").json()["items"]] == ["LOAN_001"]
    assert bob.get("/api/cases").json()["items"] == []
    assert bob.get("/api/cases/recent").json() == []
    # Unknown ids get the app's usual empty placeholder -- never Alice's record.
    bobs_view = bob.get("/api/cases/LOAN_001").json()
    assert bobs_view["applicant"] == "Unknown Applicant"
    assert bobs_view["loanAmount"] == 0.0
    assert _doc_list(bob) == []


def test_same_case_id_can_exist_in_both_tenants_without_collision(alice, bob):
    create_case(alice, "LOAN_001", "Alice Applicant")
    create_case(bob, "LOAN_001", "Bob Borrower")

    assert alice.get("/api/cases/LOAN_001").json()["applicant"] == "Alice Applicant"
    assert bob.get("/api/cases/LOAN_001").json()["applicant"] == "Bob Borrower"


def test_next_case_id_is_per_tenant(alice, bob):
    create_case(alice, "LOAN_007", "Alice Applicant")
    assert alice.get("/api/cases/next-id").json() == {"nextId": "LOAN_008"}
    assert bob.get("/api/cases/next-id").json() == {"nextId": "LOAN_001"}


def test_deleting_a_case_never_touches_another_tenant(alice, bob):
    create_case(alice, "LOAN_001", "Alice Applicant")
    create_case(bob, "LOAN_001", "Bob Borrower")

    assert bob.delete("/api/cases/LOAN_001").status_code == 200
    assert bob.get("/api/cases").json()["items"] == []
    assert alice.get("/api/cases/LOAN_001").json()["applicant"] == "Alice Applicant"


def test_bob_cannot_delete_a_case_that_only_alice_has(alice, bob):
    create_case(alice, "LOAN_042", "Alice Applicant")
    assert bob.delete("/api/cases/LOAN_042").status_code == 404
    assert alice.get("/api/cases/LOAN_042").status_code == 200


def test_upload_and_preview_are_isolated(alice, bob):
    create_case(alice, "LOAN_001", "Alice Applicant")
    up = alice.post(
        "/api/v1/documents/upload",
        files={"file": ("pan.pdf", b"%PDF-1.4 alice pan", "application/pdf")},
        data={"case_id": "LOAN_001", "document_id": "DOC-ALICE1"},
    )
    assert up.status_code == 200, up.text

    mine = alice.get("/api/documents/preview/LOAN_001/pan.pdf")
    assert mine.status_code == 200
    assert b"alice pan" in mine.content

    theirs = bob.get("/api/documents/preview/LOAN_001/pan.pdf")
    assert b"alice pan" not in theirs.content
    assert "DOC-ALICE1" not in {d["id"] for d in _doc_list(bob)}
    assert "DOC-ALICE1" in {d["id"] for d in _doc_list(alice)}


def _doc_list(client: TestClient):
    body = client.get("/api/documents").json()
    return body if isinstance(body, list) else body.get("items", [])


# ── hostile ids ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../t_other", "../../x", "a/b", "..", ".hidden", "x" * 200, "with space"])
def test_create_case_rejects_unsafe_case_ids(alice, bad):
    assert create_case(alice, bad, "Mallory").status_code == 400


@pytest.mark.parametrize("path", ["/api/cases/.hidden", "/api/cases/.hidden/status", "/api/loans/.hidden/status"])
def test_unsafe_path_params_are_rejected(alice, path):
    assert alice.get(path).status_code == 400


def test_upload_rejects_unsafe_ids_and_writes_nothing(alice, env):
    for form in ({"case_id": "../t_other"}, {"document_id": "../evil"}, {"case_id": "a/b"}):
        resp = alice.post(
            "/api/v1/documents/upload",
            files={"file": ("pan.pdf", b"%PDF-1.4", "application/pdf")},
            data=form,
        )
        assert resp.status_code == 400, form
    assert not list((env / "tenants").rglob("evil*"))


def test_process_cannot_read_arbitrary_server_paths(alice, tmp_path):
    secret = tmp_path / "server_secret.pdf"
    secret.write_bytes(b"%PDF-1.4 top secret")
    resp = alice.post("/api/v1/documents/process", json={"document_id": "DOC-X1", "s3_key": str(secret)})
    assert resp.status_code in (400, 500)
    assert b"top secret" not in resp.content


def test_process_rejects_bucket_override(alice):
    resp = alice.post(
        "/api/v1/documents/process",
        json={"document_id": "DOC-X2", "s3_key": "t_whatever/raw-documents/x.pdf", "s3_bucket": "other-bucket"},
    )
    assert resp.status_code in (400, 500)


# ── service-to-service (Celery worker -> IDP) ────────────────────────────────

def test_internal_token_grants_access_as_the_named_tenant(env, alice, monkeypatch):
    monkeypatch.setattr(auth, "INTERNAL_API_TOKEN", "internal-secret")
    create_case(alice, "LOAN_001", "Alice Applicant")
    with sqlite3.connect(auth.AUTH_DB_PATH) as conn:
        (alice_tenant,) = conn.execute("SELECT tenant_id FROM users WHERE username='alice'").fetchone()

    service = new_client()
    ok = service.get("/api/cases", headers={"X-Internal-Token": "internal-secret", "X-Tenant-Id": alice_tenant})
    assert ok.status_code == 200
    assert [c["id"] for c in ok.json()["items"]] == ["LOAN_001"]

    other = service.get("/api/cases", headers={"X-Internal-Token": "internal-secret", "X-Tenant-Id": "t_someoneelse1"})
    assert other.status_code == 200
    assert other.json()["items"] == []


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Internal-Token": "wrong", "X-Tenant-Id": "t_aaaaaaaa"},
        {"X-Internal-Token": "internal-secret"},
        {"X-Internal-Token": "internal-secret", "X-Tenant-Id": "../t_a"},
        {"X-Internal-Token": "internal-secret", "X-Tenant-Id": "T_UPPER"},
        {"X-Tenant-Id": "t_aaaaaaaa"},
    ],
)
def test_internal_token_rejects_bad_credentials(env, monkeypatch, headers):
    monkeypatch.setattr(auth, "INTERNAL_API_TOKEN", "internal-secret")
    assert new_client().get("/api/cases", headers=headers).status_code == 401


def test_internal_access_is_disabled_when_no_token_configured(env, monkeypatch):
    monkeypatch.setattr(auth, "INTERNAL_API_TOKEN", "")
    resp = new_client().get("/api/cases", headers={"X-Internal-Token": "", "X-Tenant-Id": "t_aaaaaaaa"})
    assert resp.status_code == 401


def test_standalone_idp_app_requires_auth_too(env):
    from idp.main import app as idp_app

    idp_client = TestClient(idp_app)
    assert idp_client.get("/api/v1/documents/DOC-1").status_code == 401
    assert idp_client.post("/api/v1/documents/process", json={"document_id": "D", "s3_key": "k"}).status_code == 401
