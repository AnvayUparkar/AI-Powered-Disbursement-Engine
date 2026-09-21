"""Tenant context — request-scoped tenant identity, tenant-aware storage paths, and id/path guards."""
import contextvars
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

# All tenant data lives under <TENANTS_ROOT>/<tenant_id>/... (tests may monkeypatch TENANTS_ROOT).
TENANTS_ROOT: Path = Path(
    os.getenv("TENANTS_ROOT")
    or Path(__file__).resolve().parent.parent / "poc_data" / "tenants"
)

# Server-generated tenant ids only (see app.auth) -- never derived from user input.
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9_]{3,64}$")
# Case / loan / document ids that become directory names.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_current_tenant: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_tenant", default=None)


class TenantNotSetError(RuntimeError):
    """Raised when tenant-scoped storage is touched outside an authenticated tenant context."""


class UnsafePathError(ValueError):
    """Raised when an id or path would escape its tenant-scoped directory."""


def validate_tenant_id(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not TENANT_ID_PATTERN.match(tenant_id):
        raise ValueError(f"Invalid tenant id: {tenant_id!r}")
    return tenant_id


def set_tenant(tenant_id: str) -> contextvars.Token:
    """Bind the current context to a tenant. Returns a token for reset_tenant()."""
    return _current_tenant.set(validate_tenant_id(tenant_id))


def reset_tenant(token: contextvars.Token) -> None:
    _current_tenant.reset(token)


@contextmanager
def use_tenant(tenant_id: str) -> Iterator[str]:
    """Run a block (CLI script, Celery task, test) as the given tenant."""
    token = set_tenant(tenant_id)
    try:
        yield tenant_id
    finally:
        reset_tenant(token)


def current_tenant_id() -> str:
    tenant_id = _current_tenant.get()
    if tenant_id is None:
        raise TenantNotSetError("No tenant bound to the current context; refusing to touch shared storage.")
    return tenant_id


def tenant_root() -> Path:
    """Root directory of the current tenant's data."""
    return TENANTS_ROOT / current_tenant_id()


def safe_id(value: Any, what: str = "id") -> str:
    """Validate an id that becomes a path segment (case_id, loan_id, doc_id)."""
    if not isinstance(value, str) or not SAFE_ID_PATTERN.match(value) or ".." in value:
        raise UnsafePathError(f"Invalid {what}: {value!r}")
    return value


def safe_join(base: "os.PathLike[str] | str", *parts: str) -> Path:
    """Join parts under base and guarantee the result stays inside base (no ../ or absolute escape)."""
    base_path = Path(base).resolve()
    target = base_path.joinpath(*parts).resolve()
    if not target.is_relative_to(base_path):
        raise UnsafePathError(f"Path escapes its directory: {parts!r}")
    return target


def is_within_tenant(path: "os.PathLike[str] | str") -> bool:
    """True if path resolves inside the current tenant's root."""
    return Path(path).resolve().is_relative_to(tenant_root().resolve())


class TenantPath(os.PathLike):
    """Path-like constant that resolves to the current tenant's copy of a storage directory.

    Lets existing call sites keep writing `S3_RAW_DIR / loan_id`, `S3_LOS_DIR.glob(...)`, etc.
    while every access is transparently scoped to the authenticated tenant.
    """

    def __init__(self, subdir: str):
        self._subdir = subdir

    def _real(self) -> Path:
        path = tenant_root() / self._subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def __fspath__(self) -> str:
        return str(self._real())

    def __str__(self) -> str:
        return str(self._real())

    def __repr__(self) -> str:
        return f"TenantPath({self._subdir!r})"

    def __truediv__(self, other: Any) -> Path:
        return self._real() / other

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._real(), name)


def iter_in_current_context(iterable: Iterable[Any]) -> Iterator[Any]:
    """Drive an iterator inside the caller's current context, whichever thread pulls each item.

    Streaming responses pull a sync generator from a threadpool, one item per call; this keeps the
    tenant bound for every step of the generator.
    """
    ctx = contextvars.copy_context()
    iterator = iter(iterable)

    def _drive() -> Iterator[Any]:
        while True:
            try:
                item = ctx.run(next, iterator)
            except StopIteration:
                return
            yield item

    return _drive()


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor whose tasks inherit the submitter's context (so the tenant follows into workers)."""

    def submit(self, fn, /, *args, **kwargs):
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)
