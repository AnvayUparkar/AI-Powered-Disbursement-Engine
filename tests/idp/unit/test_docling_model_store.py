"""Local weight resolution for Docling's layout / TableFormer / RapidOCR models.

Docling's artifacts_path is strict: point it at a directory that is missing a
single expected file and model construction raises instead of falling back. So
the contract enforced here is that resolve_artifacts_path only ever returns a
directory that is actually populated -- and that when DOCLING_OFFLINE demands
local weights, an unpopulated directory is a loud failure rather than a silent
network download.
"""
import pytest

from idp.services.docling.model_store import (
    ENV_ARTIFACTS_PATH,
    ENV_OFFLINE,
    _REQUIRED_SUBDIRS,
    get_default_artifacts_dir,
    is_offline_required,
    missing_artifacts,
    resolve_artifacts_path,
)


@pytest.fixture
def populated_store(tmp_path):
    """A directory shaped like a completed scripts/download_models.py run."""
    root = tmp_path / "docling"
    for name in _REQUIRED_SUBDIRS:
        sub = root / name
        sub.mkdir(parents=True)
        (sub / "weights.bin").write_bytes(b"\x00")
    return root


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV_ARTIFACTS_PATH, raising=False)
    monkeypatch.delenv(ENV_OFFLINE, raising=False)


# ── Presence detection ─────────────────────────────────────────────────────

def test_populated_directory_reports_nothing_missing(populated_store):
    assert missing_artifacts(populated_store) == []


def test_absent_directory_reports_every_model_missing(tmp_path):
    assert missing_artifacts(tmp_path / "nope") == list(_REQUIRED_SUBDIRS)


def test_partial_download_reports_only_the_absent_model(populated_store):
    """A half-finished download must not be advertised as usable -- Docling
    would raise on the missing file rather than re-fetching it."""
    import shutil
    shutil.rmtree(populated_store / "RapidOcr")
    assert missing_artifacts(populated_store) == ["RapidOcr"]


def test_empty_model_subdirectory_counts_as_missing(populated_store):
    """An interrupted download can leave the directory created but empty."""
    for stale in (populated_store / "RapidOcr").iterdir():
        stale.unlink()
    assert missing_artifacts(populated_store) == ["RapidOcr"]


# ── Resolution ─────────────────────────────────────────────────────────────

def test_explicit_populated_path_is_returned(populated_store):
    assert resolve_artifacts_path(str(populated_store)) == populated_store


def test_explicit_path_beats_the_environment(populated_store, tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_ARTIFACTS_PATH, str(tmp_path / "ignored"))
    assert resolve_artifacts_path(str(populated_store)) == populated_store


def test_environment_path_is_used_when_no_explicit_path(populated_store, monkeypatch):
    monkeypatch.setenv(ENV_ARTIFACTS_PATH, str(populated_store))
    assert resolve_artifacts_path(None) == populated_store


def test_unpopulated_path_resolves_to_none_so_docling_uses_its_hf_cache(tmp_path):
    """Returning the directory anyway would hard-fail model construction;
    None restores Docling's normal download-on-demand behaviour."""
    assert resolve_artifacts_path(str(tmp_path / "empty")) is None


def test_partial_download_also_resolves_to_none(populated_store):
    import shutil
    shutil.rmtree(populated_store / "docling-project--docling-models")
    assert resolve_artifacts_path(str(populated_store)) is None


# ── Offline enforcement ────────────────────────────────────────────────────

def test_offline_mode_raises_when_weights_are_absent(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_OFFLINE, "true")
    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_artifacts_path(str(tmp_path / "empty"))
    message = str(excinfo.value)
    assert ENV_OFFLINE in message
    # The error must tell an operator how to fix it.
    assert "scripts/download_models.py" in message


def test_offline_mode_names_the_specific_missing_models(populated_store, monkeypatch):
    import shutil
    shutil.rmtree(populated_store / "RapidOcr")
    monkeypatch.setenv(ENV_OFFLINE, "true")
    with pytest.raises(FileNotFoundError, match="RapidOcr"):
        resolve_artifacts_path(str(populated_store))


def test_offline_mode_succeeds_when_weights_are_present(populated_store, monkeypatch):
    monkeypatch.setenv(ENV_OFFLINE, "true")
    assert resolve_artifacts_path(str(populated_store)) == populated_store


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", " True "])
def test_offline_flag_accepts_the_repo_standard_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv(ENV_OFFLINE, value)
    assert is_offline_required() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "maybe"])
def test_offline_flag_is_off_for_anything_else(monkeypatch, value):
    monkeypatch.setenv(ENV_OFFLINE, value)
    assert is_offline_required() is False


def test_offline_flag_is_off_when_unset():
    assert is_offline_required() is False


# ── Default location ───────────────────────────────────────────────────────

def test_default_dir_is_inside_the_repo_not_the_user_cache():
    """Weights live in the repo so they survive venv rebuilds and can be baked
    into a container image."""
    default = get_default_artifacts_dir()
    assert default.name == "docling"
    assert default.parent.name == "models"


def test_environment_override_expands_user_home(monkeypatch):
    monkeypatch.setenv(ENV_ARTIFACTS_PATH, "~/weights/docling")
    resolved = get_default_artifacts_dir()
    assert "~" not in str(resolved)
    assert resolved.is_absolute()
