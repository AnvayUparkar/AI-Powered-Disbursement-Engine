"""Device pinning and thread-count clamping for the Docling accelerator.

num_threads is not cosmetic here: Docling forwards it to RapidOCR as
EngineConfig.onnxruntime.intra_op_num_threads, so a value above the machine's
core count oversubscribes the thread pool of the pipeline's single most
expensive stage. These tests lock in the clamp and the IDP_ACCELERATOR_DEVICE
override that lets an operator pin a specific GPU.
"""
import pytest

from docling.datamodel.accelerator_options import AcceleratorDevice

from idp.services.docling import accelerator
from idp.services.docling.accelerator import (
    ENV_DEVICE,
    build_accelerator_options,
    resolve_device,
    resolve_num_threads,
)


# ── Thread clamping ────────────────────────────────────────────────────────

def test_num_threads_below_core_count_is_left_alone(monkeypatch):
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 10)
    assert resolve_num_threads(4) == 4


def test_num_threads_exactly_at_core_count_is_left_alone(monkeypatch):
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 10)
    assert resolve_num_threads(10) == 10


def test_num_threads_above_core_count_is_clamped(monkeypatch):
    """The active profile ships num_threads=30; on a 10-core host that must
    become 10, not 30."""
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 10)
    assert resolve_num_threads(30) == 10


@pytest.mark.parametrize("bad", [0, -1, -999])
def test_non_positive_num_threads_falls_back_to_one(monkeypatch, bad):
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 10)
    assert resolve_num_threads(bad) == 1


def test_single_core_host_clamps_everything_to_one(monkeypatch):
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 1)
    assert resolve_num_threads(16) == 1


# ── Device resolution ──────────────────────────────────────────────────────

def test_use_gpu_true_requests_auto(monkeypatch):
    monkeypatch.delenv(ENV_DEVICE, raising=False)
    assert resolve_device(use_gpu=True) is AcceleratorDevice.AUTO


def test_use_gpu_false_pins_cpu(monkeypatch):
    monkeypatch.delenv(ENV_DEVICE, raising=False)
    assert resolve_device(use_gpu=False) is AcceleratorDevice.CPU


@pytest.mark.parametrize("device", ["cuda", "mps", "cpu", "xpu", "auto"])
def test_env_override_wins_over_use_gpu(monkeypatch, device):
    monkeypatch.setenv(ENV_DEVICE, device)
    # use_gpu=False would otherwise force CPU -- the explicit pin must win.
    assert resolve_device(use_gpu=False) == device


def test_env_override_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(ENV_DEVICE, "CUDA")
    assert resolve_device(use_gpu=False) == "cuda"


def test_env_override_preserves_gpu_index(monkeypatch):
    """A bare AcceleratorDevice enum can only express "cuda"; multi-GPU hosts
    need "cuda:1" to survive to AcceleratorOptions intact."""
    monkeypatch.setenv(ENV_DEVICE, "cuda:1")
    assert resolve_device(use_gpu=True) == "cuda:1"


def test_invalid_env_override_is_ignored_rather_than_crashing(monkeypatch):
    monkeypatch.setenv(ENV_DEVICE, "tpu")
    assert resolve_device(use_gpu=True) is AcceleratorDevice.AUTO


def test_blank_env_override_is_ignored(monkeypatch):
    monkeypatch.setenv(ENV_DEVICE, "   ")
    assert resolve_device(use_gpu=False) is AcceleratorDevice.CPU


# ── Assembled options ──────────────────────────────────────────────────────

def test_build_returns_options_with_clamped_threads_and_effective_device(monkeypatch):
    monkeypatch.delenv(ENV_DEVICE, raising=False)
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 8)

    options, effective = build_accelerator_options(use_gpu=False, num_threads=64)

    assert options.num_threads == 8
    assert options.device is AcceleratorDevice.CPU
    # CPU is pinned, so decide_device() can only resolve to cpu.
    assert effective == "cpu"


def test_build_honours_a_pinned_cpu_device_string(monkeypatch):
    monkeypatch.setenv(ENV_DEVICE, "cpu")
    monkeypatch.setattr(accelerator, "_cpu_count", lambda: 4)

    options, effective = build_accelerator_options(use_gpu=True, num_threads=2)

    assert options.num_threads == 2
    assert effective == "cpu", "an explicit cpu pin must defeat use_gpu=True"
