"""Multi-tenant auth — username/password signup, cookie sessions, and the require_tenant dependency.

Each signup creates its own tenant (one isolated data folder). Every request is resolved to a
tenant_id from a server-side session; the tenant is never taken from client-supplied input.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from config.settings import AUTH_DB_PATH, INTERNAL_API_TOKEN, SESSION_TTL_HOURS
from config.tenant import current_tenant_id, safe_id, set_tenant, validate_tenant_id

logger = logging.getLogger("disbursement_pipeline.auth")

SESSION_COOKIE = "dgcl_session"
INTERNAL_TOKEN_HEADER = "x-internal-token"
INTERNAL_TENANT_HEADER = "x-tenant-id"

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# Path parameters that become directory names; validated for every authenticated request.
_ID_PATH_PARAMS = ("case_id", "loan_id")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1

# Failed-login throttle: max attempts per (client, username) within a window.
_MAX_FAILED_ATTEMPTS = 8
_FAILED_WINDOW_SECONDS = 300.0
_failed_attempts: dict[str, list[float]] = {}
_failed_lock = threading.Lock()


class AuthError(Exception):
    """Base class for signup/login failures; message is safe to show to the client."""

    status_code = 400


class InvalidCredentialsError(AuthError):
    status_code = 401


class UsernameTakenError(AuthError):
    status_code = 409


class TooManyAttemptsError(AuthError):
    status_code = 429


def _db_path() -> str:
    return AUTH_DB_PATH


def _connect() -> sqlite3.Connection:
    Path(_db_path()).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                expires_at REAL NOT NULL
            );
            """
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when the username doesn't exist, so login timing doesn't reveal valid usernames.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_signup_input(username: str, password: str) -> None:
    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        raise AuthError("Username must be 3-32 characters: letters, digits, '.', '_' or '-'.")
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")


def create_tenant_user(username: str, password: str) -> str:
    """Create a new tenant with its first user. Returns the server-generated tenant_id."""
    validate_signup_input(username, password)
    tenant_id = validate_tenant_id(f"t_{secrets.token_hex(8)}")
    pw_hash = hash_password(password)
    now = time.time()
    try:
        with closing(_connect()) as conn, conn:
            conn.execute("INSERT INTO tenants (id, created_at) VALUES (?, ?)", (tenant_id, now))
            conn.execute(
                "INSERT INTO users (tenant_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, username, pw_hash, now),
            )
    except sqlite3.IntegrityError:
        raise UsernameTakenError("That username is already taken.")
    logger.info("Created tenant %s for a new signup", tenant_id)
    return tenant_id


def _throttle_key(client: str, username: str) -> str:
    return f"{client}|{username.lower()}"


def _check_throttle(key: str) -> None:
    now = time.time()
    with _failed_lock:
        recent = [t for t in _failed_attempts.get(key, []) if now - t < _FAILED_WINDOW_SECONDS]
        _failed_attempts[key] = recent
        if len(recent) >= _MAX_FAILED_ATTEMPTS:
            raise TooManyAttemptsError("Too many failed attempts. Try again in a few minutes.")


def _record_failure(key: str) -> None:
    with _failed_lock:
        _failed_attempts.setdefault(key, []).append(time.time())


def _clear_failures(key: str) -> None:
    with _failed_lock:
        _failed_attempts.pop(key, None)


def authenticate(username: str, password: str, client: str = "unknown") -> tuple[int, str]:
    """Verify credentials. Returns (user_id, tenant_id) or raises InvalidCredentialsError."""
    key = _throttle_key(client, username if isinstance(username, str) else "")
    _check_throttle(key)
    row = None
    if isinstance(username, str) and isinstance(password, str) and len(password) <= MAX_PASSWORD_LENGTH:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT id, tenant_id, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        ok = verify_password(password, row[2] if row else _DUMMY_HASH)
    else:
        ok = False
    if not row or not ok:
        _record_failure(key)
        raise InvalidCredentialsError("Invalid username or password.")
    _clear_failures(key)
    return row[0], row[1]


def create_session(user_id: int, tenant_id: str) -> str:
    """Create a session; returns the raw token (only its hash is stored)."""
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL_HOURS * 3600
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, tenant_id, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), user_id, tenant_id, expires_at),
        )
    return token


def lookup_session(token: Optional[str]) -> Optional[dict]:
    """Return {"tenant_id", "username"} for a live session token, else None."""
    if not token:
        return None
    query = (
        "SELECT s.tenant_id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?"
    )
    try:
        with closing(_connect()) as conn:
            row = conn.execute(query, (_hash_token(token), time.time())).fetchone()
    except sqlite3.OperationalError:
        # Schema not created yet in this process (e.g. the standalone IDP app on first cookie use).
        init_db()
        with closing(_connect()) as conn:
            row = conn.execute(query, (_hash_token(token), time.time())).fetchone()
    return {"tenant_id": row[0], "username": row[1]} if row else None


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def internal_headers(tenant_id: Optional[str] = None) -> dict:
    """Credentials for service-to-service calls (worker/pipeline -> IDP), scoped to a tenant."""
    return {INTERNAL_TOKEN_HEADER: INTERNAL_API_TOKEN, INTERNAL_TENANT_HEADER: tenant_id or current_tenant_id()}


def _internal_tenant(request: Request) -> Optional[str]:
    """Tenant from a trusted service-to-service call (Celery worker -> IDP), else None."""
    supplied = request.headers.get(INTERNAL_TOKEN_HEADER)
    if not supplied or not INTERNAL_API_TOKEN:
        return None
    if not hmac.compare_digest(supplied.encode("utf-8"), INTERNAL_API_TOKEN.encode("utf-8")):
        return None
    tenant_id = request.headers.get(INTERNAL_TENANT_HEADER, "")
    try:
        return validate_tenant_id(tenant_id)
    except ValueError:
        return None


async def require_tenant(request: Request) -> str:
    """FastAPI dependency: authenticate the request and bind its tenant to the current context.

    Must stay `async def`: a sync dependency runs in a threadpool copy of the context, so the
    tenant it binds would not be visible to the endpoint.
    """
    tenant_id = _internal_tenant(request)
    if tenant_id is None:
        session = await run_in_threadpool(lookup_session, request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        tenant_id = session["tenant_id"]
    for param in _ID_PATH_PARAMS:
        if param in request.path_params:
            safe_id(request.path_params[param], param)
    set_tenant(tenant_id)
    request.state.tenant_id = tenant_id
    return tenant_id
