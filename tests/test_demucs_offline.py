from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from mangaeasy import command_spec
from mangaeasy.tools import demucs, install


def _load_adapter():
    path = Path(install.__file__).resolve().parents[1] / "assets" / "tools" / "separate_demucs.py"
    spec = importlib.util.spec_from_file_location("separate_demucs_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demucs_spec_pins_complete_snapshot_and_offline_adapter():
    spec = install.TOOLS["demucs"]
    assert spec.adapter == "separate_demucs.py"
    assert spec.model_repo == "adefossez/HTDemucs-ft"
    assert spec.model_revision == "478be8a68f85418addd6f7baefd4be76522a4034"
    assert spec.required_model_files == (
        "htdemucs_ft.yaml",
        "04573f0d.safetensors",
        "92cfc3b6.safetensors",
        "d12395a8.safetensors",
        "f7e0c4bc.safetensors",
    )
    assert "torch>=2.1,<3" in spec.env_deps


def test_demucs_managed_env_routes_torch_to_selected_index(tmp_path):
    install._write_managed_pyproject(install.TOOLS["demucs"], tmp_path, "cuda")
    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'torch = [{ index = "pytorch" }]' in text
    assert "https://download.pytorch.org/whl/cu128" in text


def test_local_hf_router_only_serves_allowlisted_snapshot_files(tmp_path, monkeypatch):
    adapter = _load_adapter()
    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(safe_load=lambda _text: {"models": ["04573f0d"]}),
    )
    (tmp_path / "htdemucs_ft.yaml").write_text("models:\n  - 04573f0d\n", encoding="utf-8")
    weights = tmp_path / "04573f0d.safetensors"
    weights.write_bytes(b"weights")

    allowed = adapter._validate_model_dir(tmp_path)
    download = adapter._local_hf_download(tmp_path, allowed)
    assert download("adefossez/HTDemucs-ft", "04573f0d.safetensors") == str(weights)
    with pytest.raises(RuntimeError, match="unexpected repository"):
        download("someone/floating-model", "04573f0d.safetensors")
    with pytest.raises(RuntimeError, match="unexpected file"):
        download("adefossez/HTDemucs-ft", "../04573f0d.safetensors")


@pytest.mark.parametrize(("cuda_available", "expected"), [(True, "cuda"), (False, "cpu")])
def test_demucs_auto_device_uses_isolated_torch_capability(cuda_available, expected):
    adapter = _load_adapter()
    torch_module = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda_available),
    )

    assert adapter._resolve_device("auto", torch_module) == expected
    assert adapter._resolve_device("cpu", torch_module) == "cpu"
    assert adapter._resolve_device("cuda", torch_module) == "cuda"


def test_demucs_wrapper_invokes_offline_adapter_and_keeps_output_contract(tmp_path, monkeypatch):
    tool_dir = tmp_path / "tool"
    model_dir = tool_dir / "models" / "htdemucs-ft"
    model_dir.mkdir(parents=True)
    (tool_dir / "separate_demucs.py").write_text("# adapter\n", encoding="utf-8")
    (model_dir / "htdemucs_ft.yaml").write_text("models: []\n", encoding="utf-8")
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"audio")
    output_dir = tmp_path / "stems"
    seen: dict = {}

    def fake_run(command, *, cwd, env, **_kwargs):
        seen["command"] = command
        seen["cwd"] = cwd
        seen["env"] = env
        generated = output_dir / ".demucs" / "adefossez_htdemucs_ft" / audio.stem
        generated.mkdir(parents=True)
        (generated / "vocals.wav").write_bytes(b"vocals")
        (generated / "no_vocals.wav").write_bytes(b"music")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(demucs, "resolve_tool_dir", lambda *_args, **_kwargs: tool_dir)
    monkeypatch.setattr(demucs, "python_command", lambda _path: ["python"])
    monkeypatch.setattr(demucs, "tool_env", lambda: {})
    monkeypatch.setattr(demucs.subprocess, "run", fake_run)
    monkeypatch.setattr(demucs, "emit_result", lambda **payload: seen.update(result=payload))
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor demucs", "--audio", str(audio), "--output-dir", str(output_dir)],
    )

    assert demucs.main() == 0
    command = seen["command"]
    assert command[1] == str(tool_dir / "separate_demucs.py")
    assert command[command.index("--model-dir") + 1] == str(model_dir)
    assert command[command.index("--device") + 1] == "auto"
    assert "hf://" not in " ".join(command)
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert (output_dir / "vocals.wav").read_bytes() == b"vocals"
    assert (output_dir / "accompaniment.wav").read_bytes() == b"music"
    assert seen["result"]["offline"] is True


def test_demucs_command_schema_has_no_unprovisioned_fast_model():
    schema = command_spec.cli_args_schema("demucs")
    assert schema is not None
    assert "model" not in schema


def test_demucs_health_checks_actual_snapshot_files(tmp_path):
    spec = install.TOOLS["demucs"]
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (tmp_path / "separate_demucs.py").write_text("# adapter\n", encoding="utf-8")
    (tmp_path / "READY.json").write_text(
        json.dumps({"tool": "demucs", "model_downloaded": True}), encoding="utf-8"
    )

    healthy, reasons = install._tool_health(tmp_path, spec)
    assert not healthy
    assert any("model snapshot directory is missing" in reason for reason in reasons)

    model_dir = tmp_path / "models" / "htdemucs-ft"
    model_dir.mkdir(parents=True)
    for filename in spec.required_model_files:
        (model_dir / filename).write_bytes(b"model")
    healthy, reasons = install._tool_health(tmp_path, spec)
    assert healthy and not reasons
