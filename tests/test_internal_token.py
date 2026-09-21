"""Auto-provisioned shared secret for service-to-service (worker/pipeline -> IDP) calls."""
import stat
import threading

import pytest

from config.settings import _load_or_create_internal_token


def test_creates_token_with_owner_only_permissions(tmp_path):
    path = tmp_path / "nested" / ".internal_token"
    token = _load_or_create_internal_token(path)

    assert len(token) >= 32
    assert path.read_text() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_second_call_returns_same_token(tmp_path):
    path = tmp_path / ".internal_token"
    assert _load_or_create_internal_token(path) == _load_or_create_internal_token(path)


def test_concurrent_starters_agree_on_one_token(tmp_path):
    path = tmp_path / ".internal_token"
    results: list[str] = []
    barrier = threading.Barrier(8)

    def start():
        barrier.wait()
        results.append(_load_or_create_internal_token(path))

    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert len(set(results)) == 1
    assert path.read_text() == results[0]


def test_empty_token_file_that_never_fills_raises(tmp_path, monkeypatch):
    path = tmp_path / ".internal_token"
    path.write_text("")
    monkeypatch.setattr("config.settings.time.sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        _load_or_create_internal_token(path)


def test_configured_token_is_what_the_api_accepts(monkeypatch):
    """The token used to sign outgoing calls is the same one require_tenant verifies."""
    import app.auth as auth

    monkeypatch.setattr(auth, "INTERNAL_API_TOKEN", "abc123")
    headers = auth.internal_headers("t_aaaaaaaa")
    assert headers == {"x-internal-token": "abc123", "x-tenant-id": "t_aaaaaaaa"}
