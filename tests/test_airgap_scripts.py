"""verify-bundle.sh and load-and-push-images.sh, run against fake bundles and a stub container CLI."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AIRGAP = ROOT / "deploy" / "airgap"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is required to package the chart")

IMAGES = {
    "dgcl-engine-backend_v1.0.0_amd64.tar": "localhost/dgcl-engine-backend:v1.0.0",
    "dgcl-engine-frontend_v1.0.0_amd64.tar": "localhost/dgcl-engine-frontend:v1.0.0",
    "bitnami-redis_8.10.2_amd64.tar": "localhost/bitnami/redis:8.10.2",
}


def _fake_image_tar(path: Path, repo_tag: str, arch: str = "amd64") -> None:
    manifest = json.dumps([{"Config": "cfg.json", "RepoTags": [repo_tag], "Layers": []}], separators=(",", ":")).encode()
    config = json.dumps({"architecture": arch, "os": "linux"}, separators=(",", ":")).encode()
    with tarfile.open(path, "w") as tar:
        for name, data in (("manifest.json", manifest), ("cfg.json", config)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _write_sums(bundle: Path) -> None:
    lines = []
    for f in sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(bundle)}")
    (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n")


@pytest.fixture(scope="module")
def chart_tgz(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("chart")
    subprocess.run(["helm", "package", str(ROOT / "deploy/helm/dgcl-engine"), "--destination", str(out)],
                   check=True, capture_output=True)
    return next(out.glob("dgcl-engine-*.tgz"))


@pytest.fixture
def bundle(tmp_path, chart_tgz) -> Path:
    b = tmp_path / "airgap-bundle"
    (b / "images").mkdir(parents=True)
    (b / "charts").mkdir()
    (b / "scripts").mkdir()
    for name, tag in IMAGES.items():
        _fake_image_tar(b / "images" / name, tag)
    shutil.copy(chart_tgz, b / "charts" / chart_tgz.name)
    for script in ("verify-bundle.sh", "load-and-push-images.sh"):
        shutil.copy(AIRGAP / script, b / "scripts" / script)
    _write_sums(b)
    return b


def _run(bundle: Path, script: str, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", str(bundle / "scripts" / script)], capture_output=True, text=True, env=env)


# ── verify-bundle.sh ─────────────────────────────────────────────────────────

def test_verify_accepts_a_complete_bundle(bundle):
    res = _run(bundle, "verify-bundle.sh")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "BUNDLE OK" in res.stdout
    for ref in ("bitnami/redis:8.10.2", "dgcl-engine-backend:v1.0.0", "dgcl-engine-frontend:v1.0.0"):
        assert f"ok      {ref}" in res.stdout


def test_verify_detects_a_corrupted_image_tar(bundle):
    tar = bundle / "images" / "dgcl-engine-backend_v1.0.0_amd64.tar"
    tar.write_bytes(tar.read_bytes() + b"\0corruption")
    res = _run(bundle, "verify-bundle.sh")
    assert res.returncode != 0
    assert "BUNDLE OK" not in res.stdout


def test_verify_detects_a_tampered_chart(bundle):
    chart = next((bundle / "charts").glob("*.tgz"))
    chart.write_bytes(chart.read_bytes()[:-8] + b"tampered")
    assert _run(bundle, "verify-bundle.sh").returncode != 0


def test_verify_reports_a_missing_image_even_when_checksums_were_regenerated(bundle):
    (bundle / "images" / "bitnami-redis_8.10.2_amd64.tar").unlink()
    _write_sums(bundle)  # a careless re-zip that "fixes" the checksums must still be caught
    res = _run(bundle, "verify-bundle.sh")
    assert res.returncode != 0
    assert "MISSING bitnami/redis:8.10.2" in res.stderr


def test_verify_rejects_a_non_amd64_image(bundle):
    _fake_image_tar(bundle / "images" / "dgcl-engine-frontend_v1.0.0_amd64.tar", "localhost/dgcl-engine-frontend:v1.0.0", "arm64")
    _write_sums(bundle)
    res = _run(bundle, "verify-bundle.sh")
    assert res.returncode != 0
    assert "is not amd64" in res.stderr


def test_verify_rejects_an_image_tar_named_amd64_but_tagged_for_the_wrong_repo(bundle):
    _fake_image_tar(bundle / "images" / "dgcl-engine-backend_v1.0.0_amd64.tar", "localhost/something-else:v1.0.0")
    _write_sums(bundle)
    res = _run(bundle, "verify-bundle.sh")
    assert res.returncode != 0
    assert "MISSING dgcl-engine-backend:v1.0.0" in res.stderr


# ── load-and-push-images.sh (stub container CLI) ─────────────────────────────

@pytest.fixture
def stub_cli(tmp_path):
    """A fake `podman` that records its calls and answers load/inspect from the fake tarballs."""
    log = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "podman"
    cli.parent.mkdir()
    cli.write_text(
        '#!/bin/bash\n'
        'echo "$@" >> "$FAKE_LOG"\n'
        'case "$1" in\n'
        '  load) tar -xOf "$3" manifest.json | sed -n \'s/.*"\\(localhost\\/[^"]*\\)".*/Loaded image: \\1/p\' | head -1 ;;\n'
        '  image) echo "${FAKE_ARCH:-amd64}" ;;\n'
        'esac\n'
    )
    cli.chmod(0o755)
    return cli, log


def test_load_and_push_preserves_repository_paths_under_the_registry(bundle, stub_cli):
    cli, log = stub_cli
    res = _run(bundle, "load-and-push-images.sh", {"REGISTRY": "registry.internal:5000", "CONTAINER_CLI": str(cli), "FAKE_LOG": str(log)})
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text().splitlines()
    pushed = sorted(c.split()[-1] for c in calls if c.startswith("push"))
    assert pushed == [
        "registry.internal:5000/bitnami/redis:8.10.2",
        "registry.internal:5000/dgcl-engine-backend:v1.0.0",
        "registry.internal:5000/dgcl-engine-frontend:v1.0.0",
    ]
    assert "tag localhost/bitnami/redis:8.10.2 registry.internal:5000/bitnami/redis:8.10.2" in calls


def test_load_and_push_only_adds_tls_flag_when_asked(bundle, stub_cli):
    cli, log = stub_cli
    _run(bundle, "load-and-push-images.sh", {"REGISTRY": "r:5000", "CONTAINER_CLI": str(cli), "FAKE_LOG": str(log)})
    assert not any("--tls-verify=false" in c for c in log.read_text().splitlines())
    log.write_text("")
    _run(bundle, "load-and-push-images.sh", {"REGISTRY": "r:5000", "TLS_VERIFY": "false", "CONTAINER_CLI": str(cli), "FAKE_LOG": str(log)})
    assert all("--tls-verify=false" in c for c in log.read_text().splitlines() if c.startswith("push"))


def test_load_and_push_refuses_to_push_a_non_amd64_image(bundle, stub_cli):
    cli, log = stub_cli
    res = _run(bundle, "load-and-push-images.sh", {"REGISTRY": "r:5000", "CONTAINER_CLI": str(cli), "FAKE_LOG": str(log), "FAKE_ARCH": "arm64"})
    assert res.returncode != 0
    assert "expected amd64" in res.stderr
    assert not any(c.startswith("push") for c in log.read_text().splitlines())


def test_load_and_push_requires_a_registry(bundle, stub_cli):
    cli, log = stub_cli
    env = {k: v for k, v in os.environ.items() if k != "REGISTRY"}
    env.update({"CONTAINER_CLI": str(cli), "FAKE_LOG": str(log)})
    res = subprocess.run(["bash", str(bundle / "scripts" / "load-and-push-images.sh")], capture_output=True, text=True, env=env)
    assert res.returncode != 0
    assert "REGISTRY" in res.stderr
