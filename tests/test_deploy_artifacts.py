"""Guards for the image build, model bake and frontend config (previously broken go-live blockers)."""
import fnmatch
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import download_models  # noqa: E402  (scripts/ is not a package)

DOCKERFILES = sorted((ROOT / "docker").glob("Dockerfile.*"))


# ── Dockerfile COPY sources ──────────────────────────────────────────────────

def _dockerignore_patterns() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _is_ignored(rel: str, patterns: list[str]) -> bool:
    """Minimal .dockerignore semantics: later patterns win, '!' re-includes."""
    ignored = False
    for pat in patterns:
        negate = pat.startswith("!")
        core = pat[1:] if negate else pat
        base = core.rstrip("/")
        hit = (
            fnmatch.fnmatch(rel, base)
            or ("/" not in base and fnmatch.fnmatch(Path(rel).name, base))
            or rel == base
            or rel.startswith(base + "/")
        )
        if hit:
            ignored = not negate
    return ignored


def _copy_sources(dockerfile: Path) -> list[str]:
    text = dockerfile.read_text().replace("\\\n", " ")
    sources: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        tokens = [t for t in line.split()[1:] if not t.startswith("--")]
        if any(t.startswith("--from") for t in line.split()):
            continue  # copies from another build stage, not the build context
        sources.extend(tokens[:-1])
    return sources


def test_dockerfiles_are_found():
    assert {p.name for p in DOCKERFILES} >= {"Dockerfile.backend", "Dockerfile.backend-gpu", "Dockerfile.frontend"}


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_every_copy_source_exists_and_is_not_dockerignored(dockerfile):
    patterns = _dockerignore_patterns()
    problems = []
    for src in _copy_sources(dockerfile):
        if src == ".":
            continue
        if not (ROOT / src).exists():
            problems.append(f"{src}: does not exist in the repo")
        elif _is_ignored(src, patterns):
            problems.append(f"{src}: excluded by .dockerignore, so absent from the build context")
    assert problems == [], f"{dockerfile.name}: " + "; ".join(problems)


def test_dockerignore_helper_matches_real_patterns():
    patterns = _dockerignore_patterns()
    assert _is_ignored("poc_data/x.json", patterns)
    assert _is_ignored("tests/test_a.py", patterns)
    assert _is_ignored("README.md", patterns)
    assert _is_ignored(".env", patterns)
    assert not _is_ignored(".env.example", patterns)
    assert not _is_ignored("scripts/download_models.py", patterns)
    assert not _is_ignored("requirements.txt", patterns)


# ── model bake ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dockerfile", [p for p in DOCKERFILES if "backend" in p.name], ids=lambda p: p.name)
def test_backend_images_bake_models_before_copying_code_and_go_offline_after(dockerfile):
    text = dockerfile.read_text()
    # Match real instructions only (start of line), not mentions inside comments.
    bake = re.search(r"^RUN python scripts/download_models\.py --dest /opt/models/docling", text, re.M).start()
    copy_all = re.search(r"^COPY \. \.$", text, re.M).start()
    offline = re.search(r"^ENV DOCLING_ARTIFACTS_PATH=/opt/models/docling", text, re.M).start()
    assert "HF_HUB_OFFLINE=1" in text[offline:] and "TRANSFORMERS_OFFLINE=1" in text[offline:]
    assert bake < copy_all, "download must precede COPY . . so code edits reuse the cached weights layer"
    assert bake < offline, "offline env must be set after the download step, which needs the network"
    assert "DOCLING_OFFLINE" not in text, "DOCLING_OFFLINE is not read by any code"


def test_download_script_default_covers_the_onnx_ocr_backend_the_pipeline_uses():
    assert download_models.DEFAULT_RAPIDOCR_MODELS
    assert all(spec.startswith("onnxruntime:") for spec in download_models.DEFAULT_RAPIDOCR_MODELS)
    pipeline_src = (ROOT / "idp" / "services" / "docling" / "pipeline.py").read_text()
    assert 'backend="onnxruntime"' in pipeline_src


def _fake_bake(dest: Path, *, layout=True, tableformer=True, rapid_onnx=True):
    if layout:
        (dest / "docling-project--docling-layout-heron").mkdir(parents=True)
        (dest / "docling-project--docling-layout-heron" / "model.safetensors").write_bytes(b"x")
    if tableformer:
        (dest / "docling-project--docling-models").mkdir(parents=True)
        (dest / "docling-project--docling-models" / "tableformer.safetensors").write_bytes(b"x")
    (dest / "RapidOcr").mkdir(parents=True)
    if rapid_onnx:
        (dest / "RapidOcr" / "det.onnx").write_bytes(b"x")


def test_verify_accepts_a_complete_bake(tmp_path):
    _fake_bake(tmp_path)
    assert download_models.verify_artifacts(tmp_path) == []


def test_verify_rejects_missing_directory(tmp_path):
    problems = download_models.verify_artifacts(tmp_path / "nope")
    assert problems and "does not exist" in problems[0]


def test_verify_rejects_empty_directory(tmp_path):
    problems = download_models.verify_artifacts(tmp_path)
    assert any("RapidOcr" in p for p in problems)
    assert any("no model weight files" in p for p in problems)


def test_verify_rejects_ocr_folder_without_onnx_weights(tmp_path):
    _fake_bake(tmp_path, rapid_onnx=False)
    assert any("no .onnx weights" in p for p in download_models.verify_artifacts(tmp_path))


def test_verify_rejects_bake_missing_layout_and_tableformer(tmp_path):
    _fake_bake(tmp_path, layout=False, tableformer=False)
    assert any("expected layout, TableFormer and RapidOCR" in p for p in download_models.verify_artifacts(tmp_path))


def test_main_verify_only_exit_codes(tmp_path, capsys):
    assert download_models.main(["--dest", str(tmp_path), "--verify-only"]) == 1
    assert "ERROR" in capsys.readouterr().err
    _fake_bake(tmp_path)
    assert download_models.main(["--dest", str(tmp_path), "--verify-only"]) == 0


def test_main_downloads_only_the_models_the_pipeline_uses(tmp_path, monkeypatch):
    captured = {}

    def fake_download_models(**kwargs):
        captured.update(kwargs)
        _fake_bake(Path(kwargs["output_dir"]))
        return kwargs["output_dir"]

    monkeypatch.setattr("docling.utils.model_downloader.download_models", fake_download_models)
    dest = tmp_path / "models"
    assert download_models.main(["--dest", str(dest), "--rapidocr-models", "onnxruntime:en, onnxruntime:ch"]) == 0

    assert captured["output_dir"] == dest
    assert captured["with_layout"] and captured["with_tableformer"] and captured["with_rapidocr"]
    assert captured["with_code_formula"] is False and captured["with_picture_classifier"] is False
    assert captured["rapidocr_models"] == ["onnxruntime:en", "onnxruntime:ch"]


def test_main_fails_the_build_when_download_leaves_an_incomplete_bake(tmp_path, monkeypatch):
    monkeypatch.setattr("docling.utils.model_downloader.download_models", lambda **kw: kw["output_dir"])
    assert download_models.main(["--dest", str(tmp_path / "m")]) == 1


# ── chart values agree with the image ────────────────────────────────────────

def test_chart_values_use_real_offline_settings_and_single_api_replica():
    yaml = pytest.importorskip("yaml")
    values = yaml.safe_load((ROOT / "deploy/helm/dgcl-engine/values.yaml").read_text())
    config = values["config"]
    assert "DOCLING_OFFLINE" not in config
    assert config["HF_HUB_OFFLINE"] == "1"
    backend = (ROOT / "docker/Dockerfile.backend").read_text()
    assert f'DOCLING_ARTIFACTS_PATH={config["DOCLING_ARTIFACTS_PATH"]}' in backend
    assert values["api"]["replicaCount"] == 1
    assert values["api"]["autoscaling"]["enabled"] is False


def test_chart_no_longer_seeds_from_a_poc_data_directory_the_image_does_not_have():
    helpers = (ROOT / "deploy/helm/dgcl-engine/templates/_helpers.tpl").read_text()
    assert "cp -a /srv/app/poc_data" not in helpers
    assert "poc_data" in (ROOT / ".dockerignore").read_text()  # the reason the old seed could never work


# ── frontend must never default to the user's own machine ────────────────────

def test_frontend_source_has_no_localhost_defaults():
    offenders = []
    for path in (ROOT / "frontend" / "src").rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or "mock" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            if re.search(r"localhost|127\.0\.0\.1", line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {stripped}")
    assert offenders == []


def test_frontend_api_clients_share_one_same_origin_base_url():
    config = (ROOT / "frontend/src/config.ts").read_text()
    assert "VITE_API_BASE_URL || '/api'" in config
    for rel in ("frontend/src/api/node2.ts", "frontend/src/services/apiClient.ts", "frontend/src/services/cases.ts"):
        text = (ROOT / rel).read_text()
        assert "import.meta.env" not in text, f"{rel} must use config.ts, not read the env var itself"
        assert "@/config" in text


# ── chart risks: redis, ingress, api sizing, security context ────────────────

def _chart_values():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / "deploy/helm/dgcl-engine/values.yaml").read_text())


def test_redis_image_tag_is_pinned_to_what_the_readme_pushes():
    values = _chart_values()
    tag = values["redis"]["image"]["tag"]
    assert tag != "latest"
    readme = (ROOT / "deploy/helm/README.md").read_text()
    assert f"bitnami/redis:{tag}" in readme, "README must retag/push the same tag the chart pulls"


def test_redis_subchart_networkpolicy_is_off_so_ours_is_the_only_one():
    assert _chart_values()["redis"]["networkPolicy"]["enabled"] is False
    ours = (ROOT / "deploy/helm/dgcl-engine/templates/networkpolicy.yaml").read_text()
    assert '-redis' in ours and "port: 6379" in ours


def test_generated_passwords_use_hex_not_base64():
    """base64 can emit / + = which corrupt redis://:<password>@host:6379/0."""
    readme = (ROOT / "deploy/helm/README.md").read_text()
    assert "openssl rand -base64" not in readme
    assert 'redis-password="$(openssl rand -hex' in readme


def test_ingress_supports_long_runs_large_uploads_and_the_sse_stream():
    ingress = _chart_values()["ingress"]
    assert ingress["proxyBuffering"] == "off"
    assert ingress["proxyReadTimeoutSeconds"] >= 600
    assert int(ingress["proxyBodySize"].rstrip("m")) > 50  # MAX_DOCUMENT_SIZE_MB is 50
    template = (ROOT / "deploy/helm/dgcl-engine/templates/ingress.yaml").read_text()
    for annotation in ("proxy-buffering", "proxy-body-size", "proxy-read-timeout", "proxy-send-timeout"):
        assert f"nginx.ingress.kubernetes.io/{annotation}" in template


def test_api_runs_one_uvicorn_worker_because_state_is_per_process():
    args = _chart_values()["api"]["args"]
    assert args[args.index("--workers") + 1] == "1"


def test_api_memory_fits_an_in_process_pipeline():
    def mib(q: str) -> int:
        return int(q[:-2]) * (1024 if q.endswith("Gi") else 1)

    resources = _chart_values()["api"]["resources"]
    assert mib(resources["limits"]["memory"]) >= 2048
    assert mib(resources["requests"]["memory"]) >= 1024
    yaml = pytest.importorskip("yaml")
    poc = yaml.safe_load((ROOT / "deploy/helm/dgcl-engine/values-poc.yaml").read_text())
    assert mib(poc["api"]["resources"]["limits"]["memory"]) >= 1024


def test_pods_use_a_numeric_uid_that_matches_the_image_user():
    ctx = _chart_values()["podSecurityContext"]
    for backend in ("Dockerfile.backend", "Dockerfile.backend-gpu"):
        assert f"--uid {ctx['runAsUser']}" in (ROOT / "docker" / backend).read_text()
    assert ctx["runAsGroup"] == ctx["fsGroup"] == ctx["runAsUser"]


# ── image build portability ──────────────────────────────────────────────────

def test_frontend_js_build_stage_runs_on_the_builder_platform():
    """esbuild (Go) crashes under QEMU when cross-building; static assets are arch-independent anyway."""
    text = (ROOT / "docker/Dockerfile.frontend").read_text()
    assert re.search(r"^FROM --platform=\$BUILDPLATFORM node:\S+ AS build$", text, re.M)
    assert re.search(r"^FROM nginx:\S+$", text, re.M), "final stage must not pin BUILDPLATFORM: it takes the target arch"


def test_cpu_image_installs_cpu_only_torch_before_requirements_and_gpu_image_does_not():
    cpu = (ROOT / "docker/Dockerfile.backend").read_text()
    torch_line = re.search(r"^RUN pip install .*torch==\S+ .*download\.pytorch\.org/whl/cpu$", cpu, re.M)
    reqs = re.search(r"^RUN pip install --no-cache-dir -r requirements\.txt$", cpu, re.M)
    assert torch_line and reqs and torch_line.start() < reqs.start()
    assert "download.pytorch.org/whl/cpu" not in (ROOT / "docker/Dockerfile.backend-gpu").read_text()


def test_requirements_use_plain_onnxruntime_that_the_gpu_image_swaps_to_gpu():
    """onnxruntime-gpu==1.27.1 does not exist on PyPI, so the CPU image could never build. Base
    requirements carry the CPU package; docker/Dockerfile.backend-gpu rewrites it with sed."""
    reqs = (ROOT / "requirements.txt").read_text().splitlines()
    assert not any(line.startswith("onnxruntime-gpu") for line in reqs)
    assert any(re.match(r"^onnxruntime>=", line) for line in reqs)
    gpu = (ROOT / "docker/Dockerfile.backend-gpu").read_text()
    sed = re.search(r"sed '(s/[^']+)' requirements\.txt", gpu).group(1)
    pattern = sed.split("/")[1]
    assert any(re.match(pattern, line) for line in reqs), "GPU Dockerfile sed matches no line in requirements.txt"


def test_download_retries_transient_failures_then_succeeds(tmp_path, monkeypatch):
    calls = []

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("No address associated with hostname")
        _fake_bake(Path(kwargs["output_dir"]))
        return kwargs["output_dir"]

    monkeypatch.setattr("docling.utils.model_downloader.download_models", flaky)
    monkeypatch.setattr(download_models.time, "sleep", lambda _s: None)
    assert download_models.main(["--dest", str(tmp_path / "m")]) == 0
    assert len(calls) == 3


def test_download_gives_up_after_all_attempts_and_raises(tmp_path, monkeypatch):
    calls = []

    def always_down(**kwargs):
        calls.append(1)
        raise ConnectionError("offline")

    monkeypatch.setattr("docling.utils.model_downloader.download_models", always_down)
    monkeypatch.setattr(download_models.time, "sleep", lambda _s: None)
    with pytest.raises(ConnectionError):
        download_models.download(tmp_path / "m", attempts=3)
    assert len(calls) == 3


# ── init container script, executed for real ─────────────────────────────────

def _init_script() -> str:
    helpers = (ROOT / "deploy/helm/dgcl-engine/templates/_helpers.tpl").read_text()
    block = helpers[helpers.index('define "dgcl.sharedStorageInitContainer"'):]
    body = block[block.index("- |\n") + 4: block.index("  volumeMounts:")]
    return "\n".join(line[6:] if line.startswith("      ") else line for line in body.splitlines())


def test_init_container_creates_tenants_dir_on_a_writable_volume(tmp_path):
    import subprocess

    vol = tmp_path / "shared"
    vol.mkdir()
    res = subprocess.run(["/bin/sh", "-c", _init_script().replace("/shared", str(vol))], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert (vol / "tenants").is_dir()
    assert not list(vol.glob(".write-probe-*")), "probe file must be cleaned up"


def test_init_container_explains_an_unwritable_volume_instead_of_a_bare_mkdir_error(tmp_path):
    import os
    import subprocess

    if os.geteuid() == 0:
        pytest.skip("root can write anywhere; the permission failure cannot be reproduced")
    vol = tmp_path / "shared"
    vol.mkdir()
    vol.chmod(0o555)
    try:
        res = subprocess.run(["/bin/sh", "-c", _init_script().replace("/shared", str(vol))], capture_output=True, text=True)
    finally:
        vol.chmod(0o755)
    assert res.returncode == 1
    assert "is not writable by uid" in res.stderr
    assert "mkdir" not in res.stderr, "the friendly message must come first, not a raw mkdir error"


# ── chart must work under ANY release name ───────────────────────────────────

def _helm_render(release: str, *sets: str) -> list:
    import shutil
    import subprocess

    if not shutil.which("helm"):
        pytest.skip("helm not installed")
    yaml = pytest.importorskip("yaml")
    args = ["helm", "template", release, str(ROOT / "deploy/helm/dgcl-engine")]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _config(docs) -> dict:
    return next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-config"))["data"]


@pytest.mark.parametrize("release", ["dgcl-engine", "dgcl", "prod-dgcl", "x"])
def test_idp_service_url_follows_the_release_name(release):
    docs = _helm_render(release)
    services = {d["metadata"]["name"] for d in docs if d["kind"] == "Service"}
    url = _config(docs)["IDP_SERVICE_URL"]
    assert url == f"http://{release}-idp:8001"
    assert f"{release}-idp" in services, "the URL must point at a Service the chart actually creates"


def test_idp_service_url_can_still_be_overridden():
    docs = _helm_render("dgcl", "config.IDP_SERVICE_URL=http://elsewhere:9000")
    assert _config(docs)["IDP_SERVICE_URL"] == "http://elsewhere:9000"


def test_every_service_reference_in_config_and_frontend_resolves_to_a_real_service():
    docs = _helm_render("relname")
    services = {d["metadata"]["name"] for d in docs if d["kind"] == "Service"}
    fe = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-frontend"))
    upstream = next(e["value"] for e in fe["spec"]["template"]["spec"]["containers"][0]["env"] if e["name"] == "API_UPSTREAM")
    assert upstream.split(":")[0] in services
    assert _config(docs)["IDP_SERVICE_URL"].split("//")[1].split(":")[0] in services


# ── values.yaml is the file the k8s team edits ───────────────────────────────

def _values_lines():
    return (ROOT / "deploy/helm/dgcl-engine/values.yaml").read_text().splitlines()


def test_every_placeholder_in_values_is_marked_fill_in():
    """A placeholder without the marker would be missed by someone searching for '>>> FILL IN'."""
    placeholder = re.compile(r'"<[^"]+>"|REPLACE_WITH|example\.(?:internal|com)')
    unmarked = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(_values_lines(), 1)
        if not line.lstrip().startswith("#") and placeholder.search(line.split("#", 1)[0]) and ">>> FILL IN" not in line
    ]
    assert unmarked == []


def test_values_banner_lists_the_required_and_the_do_not_touch_settings():
    head = "\n".join(_values_lines()[:40])
    for required in ("global.imageRegistry", "sharedStorage.storageClassName", "ingress.host", "ingress.tls.secretName",
                     "ALLOWED_ORIGINS", "LLM_BASE_URL", "LLM_MODEL"):
        assert required in head
    for guarded in ("api.replicaCount", "redis.image.tag", "podSecurityContext"):
        assert guarded in head


def test_values_has_no_stale_references():
    text = "\n".join(_values_lines())
    assert "accelerator.py" not in text
    assert "overriding\n#     global.imageRegistry alone does NOT cover it" not in text
    assert "on EKS" not in (ROOT / "deploy/helm/dgcl-engine/Chart.yaml").read_text()


def test_bundle_ships_the_full_values_file_not_an_overlay():
    script = (ROOT / "deploy/airgap/build-bundle.sh").read_text()
    assert 'cp "$CHART_DIR/values.yaml" "$OUT/values-airgap.yaml"' in script
    assert not (ROOT / "deploy/airgap/values-airgap.example.yaml").exists()
    assert "values-airgap.yaml" in (ROOT / "deploy/airgap/INSTALL.md").read_text()


def test_llm_model_is_the_litellm_listed_bedrock_haiku_name_not_a_placeholder():
    values = _chart_values()
    assert values["config"]["LLM_MODEL"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert "REPLACE_WITH" not in values["config"]["LLM_MODEL"]


# ── access path: NodePort 47777, no ingress ──────────────────────────────────

def test_frontend_is_exposed_on_nodeport_47777_and_ingress_is_off_by_default():
    values = _chart_values()
    assert values["frontend"]["service"] == {"type": "NodePort", "nodePort": 47777}
    assert values["ingress"]["enabled"] is False
    assert values["api"]["service"]["type"] == "ClusterIP", "only the frontend is exposed; the API stays internal"


def test_plain_http_default_does_not_set_a_secure_only_cookie():
    """A Secure cookie is never sent over http, so login would silently do nothing on a NodePort."""
    assert _chart_values()["config"]["SESSION_COOKIE_SECURE"] == "false"


def test_rendered_chart_has_one_nodeport_service_and_no_ingress_by_default():
    docs = _helm_render("rel")
    assert not [d for d in docs if d["kind"] == "Ingress"]
    node_ports = [
        (d["metadata"]["name"], p["nodePort"])
        for d in docs if d["kind"] == "Service"
        for p in d["spec"]["ports"] if d["spec"].get("type") == "NodePort" and "nodePort" in p
    ]
    assert node_ports == [("rel-frontend", 47777)]


def test_enabling_the_ingress_still_renders_with_its_limits():
    docs = _helm_render("rel", "ingress.enabled=true", "frontend.service.type=ClusterIP")
    ing = next(d for d in docs if d["kind"] == "Ingress")
    ann = ing["metadata"]["annotations"]
    assert ann["nginx.ingress.kubernetes.io/proxy-body-size"] == "60m"
    assert ann["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"


def test_frontend_nginx_carries_the_limits_the_ingress_no_longer_provides():
    """With NodePort the frontend container's nginx is the entry point for uploads, long runs and SSE."""
    conf = (ROOT / "docker/nginx.frontend.conf.template").read_text()
    assert "client_max_body_size 60m;" in conf
    api_block = conf[conf.index("location /api/"): conf.index("location / {")]
    assert "proxy_read_timeout 600s;" in api_block
    assert "proxy_send_timeout 600s;" in api_block
    assert "proxy_buffering off;" in api_block
    assert "proxy_http_version 1.1;" in api_block
