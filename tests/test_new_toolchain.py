from __future__ import annotations

import json
from pathlib import Path

import pytest

from mangaeasy.tools import install
from mangaeasy.assets.tools.generate_ace_step import _require_initialized


def test_song_toolchain_is_immutably_pinned():
    ace = install.TOOLS["ace-step"]
    demucs = install.TOOLS["demucs"]
    whisperx = install.TOOLS["whisperx"]
    assert ace.ref == "dce621408bee8c31b4fcf4811682eb9359e1bc94"
    assert ace.model_revision == "19671f406d603126926c1b7e2adc169acbcade22"
    assert ace.preserve_upstream_torch is True and ace.sync_args == ["--frozen"]
    assert demucs.ref and demucs.model_revision
    assert whisperx.ref and whisperx.model_revision
    assert "whisperx==3.8.6" in whisperx.env_deps
    assert whisperx.extra_models[0].repo == "facebook/wav2vec2-base-960h"
    assert whisperx.extra_models[0].revision == "22aad52d435eb6dbaf354bdad9b0da84ce7d6156"


@pytest.mark.parametrize(
    ("tool_name", "source_revision", "model_revision"),
    [
        (
            "index-tts",
            "13495845e3028f0bb6ca1462ad22aa0e76349e40",
            "740dcaff396282ffb241903d150ac011cd4b1ede",
        ),
        (
            "deepseek-ocr2",
            "2f3699ebbb96fa8af32212e8c170f2cc28730fad",
            "aaa02f3811945a91062062994c5c4a3f4c0af2b0",
        ),
        (
            "z-image-turbo",
            None,
            "f332072aa78be7aecdf3ee76d5c247082da564a6",
        ),
    ],
)
def test_installer_managed_legacy_models_are_now_immutable(
    tool_name, source_revision, model_revision
):
    spec = install.TOOLS[tool_name]
    assert spec.ref == source_revision
    assert spec.model_revision == model_revision
    assert spec.required_model_files


def test_optional_source_clones_are_commit_pinned():
    assert install.TOOLS["magi-v3"].ref == "2a45bf09b43adc80778270a366372aaa148e2291"
    assert install.TOOLS["kokoro-82m"].ref == "dfb907a02bba8152ca444717ca5d78747ccb4bec"


def test_ace_step_adapter_checks_second_tuple_member():
    _require_initialized("fixture", ("ready", True))
    with pytest.raises(RuntimeError, match="not ready"):
        _require_initialized("fixture", ("not ready", False))


def test_whisperx_generated_env_routes_complete_torch_trio(tmp_path):
    install._write_managed_pyproject(install.TOOLS["whisperx"], tmp_path, "cuda")
    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'torch = [{ index = "pytorch" }]' in text
    assert 'torchvision = [{ index = "pytorch" }]' in text
    assert 'torchaudio = [{ index = "pytorch" }]' in text
    assert "cu128" in text


def test_hf_download_includes_model_revision(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install, "_require", lambda *_args, **_kwargs: None)
    spec = install.TOOLS["whisperx"]

    def fake_run(command, *_args, **_kwargs):
        calls.append(command)
        target = Path(command[command.index("--local-dir") + 1])
        target.mkdir(parents=True, exist_ok=True)
        required = (
            spec.required_model_files
            if command[command.index("download") + 1] == spec.model_repo
            else spec.extra_models[0].required_files
        )
        for filename in required:
            (target / filename).write_bytes(b"fixture")

    monkeypatch.setattr(install, "_run", fake_run)
    install._download_model(install.TOOLS["whisperx"], tmp_path, lambda _message: None)
    command = calls[0]
    assert command[command.index("--from") + 1] == "huggingface-hub==1.23.0"
    assert "--revision" in command
    assert command[command.index("--revision") + 1] == install.TOOLS["whisperx"].model_revision
    assert len(calls) == 2
    assert calls[1][calls[1].index("--revision") + 1] == spec.extra_models[0].revision
    assert "--include" in calls[1]


def test_hf_download_rejects_metadata_only_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_require", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(install, "_run", lambda *_args, **_kwargs: None)
    cache = tmp_path / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / "download.metadata").write_text("metadata", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("*.bin filter=lfs", encoding="utf-8")

    with pytest.raises(install.InstallError, match="contains no payload files"):
        install._download_hf_snapshot(
            "example/model", None, tmp_path, (), (), lambda _message: None
        )

    (tmp_path / "model.safetensors").write_bytes(b"model")
    install._download_hf_snapshot(
        "example/model", None, tmp_path, (), (), lambda _message: None
    )


def test_pinned_tool_clone_is_shallow_filtered_and_skips_lfs(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, _log, cwd=None, env=None):
        calls.append((command, cwd, env))

    monkeypatch.setattr(install, "_run", fake_run)
    monkeypatch.setattr(install, "tool_env", lambda: {"BASE": "managed"})
    destination = tmp_path / "tool"
    revision = "a" * 40

    install._clone_or_update(
        "https://github.com/example/tool", destination, revision,
        lambda _message: None, skip_lfs_smudge=True,
    )

    commands = [entry[0] for entry in calls]
    assert commands[0][:7] == [
        "git", "clone", "--filter=blob:none", "--depth", "1", "--no-tags", "--no-checkout",
    ]
    assert commands[1][-2:] == ["origin", revision]
    assert commands[2][-2:] == ["--detach", "FETCH_HEAD"]
    assert all(entry[2]["GIT_LFS_SKIP_SMUDGE"] == "1" for entry in calls)


def test_existing_pinned_clone_fetches_only_requested_revision(tmp_path, monkeypatch):
    destination = tmp_path / "tool"
    (destination / ".git").mkdir(parents=True)
    commands = []
    monkeypatch.setattr(
        install, "_run",
        lambda command, *_args, **_kwargs: commands.append(command),
    )
    revision = "b" * 40

    install._clone_or_update(
        "https://github.com/example/tool", destination, revision,
        lambda _message: None,
    )

    assert len(commands) == 2
    assert commands[0][-2:] == ["origin", revision]
    assert "--depth" in commands[0] and "--filter=blob:none" in commands[0]
    assert "--all" not in commands[0] and "--tags" not in commands[0]
    assert commands[1][-2:] == ["--detach", "FETCH_HEAD"]


def test_ready_health_rejects_partial_and_accepts_complete(tmp_path):
    spec = install.TOOLS["whisperx"]
    healthy, reasons = install._tool_health(tmp_path, spec)
    assert not healthy and reasons
    python = tmp_path / ".venv" / ("Scripts" if __import__("sys").platform == "win32" else "bin") / (
        "python.exe" if __import__("sys").platform == "win32" else "python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (tmp_path / "transcribe_whisperx.py").write_text("# adapter\n", encoding="utf-8")
    (tmp_path / "READY.json").write_text(json.dumps({
        "tool": "whisperx", "model_downloaded": True,
    }), encoding="utf-8")
    for filename in spec.required_model_files:
        path = tmp_path / spec.model_subdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
    for model in spec.extra_models:
        for filename in model.required_files:
            path = tmp_path / model.subdir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")
    healthy, reasons = install._tool_health(tmp_path, spec)
    assert healthy and not reasons


@pytest.mark.parametrize("marker_text", ["[]\n", "null\n", '"ready"\n', "{}\n"])
def test_ready_health_rejects_malformed_marker_contract(tmp_path, marker_text):
    spec = install.TOOLS["index-tts"]
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    model_root = tmp_path / spec.model_subdir
    model_root.mkdir(parents=True)
    (model_root / "model.fixture").write_bytes(b"model")
    (tmp_path / "READY.json").write_text(marker_text, encoding="utf-8")

    healthy, reasons = install._tool_health(tmp_path, spec)
    assert not healthy
    assert any(reason.startswith("READY.json") for reason in reasons)


@pytest.mark.parametrize(
    "tool_name",
    ["ace-step", "index-tts", "deepseek-ocr2", "z-image-turbo"],
)
def test_ready_health_requires_complete_declared_model_payload(tmp_path, tool_name):
    spec = install.TOOLS[tool_name]
    python = tmp_path / ".venv" / (
        "Scripts/python.exe" if __import__("sys").platform == "win32" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    for adapter in ([spec.adapter] if spec.adapter else []) + spec.extra_adapters:
        (tmp_path / adapter).write_text("# adapter\n", encoding="utf-8")
    (tmp_path / "READY.json").write_text(
        json.dumps({"tool": tool_name, "model_downloaded": True}),
        encoding="utf-8",
    )

    healthy, reasons = install._tool_health(tmp_path, spec)
    assert not healthy
    assert any("model snapshot directory is missing" in reason for reason in reasons)

    model_root = tmp_path / (spec.model_subdir or "checkpoints")
    cache = model_root / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / "download.metadata").write_text("metadata", encoding="utf-8")
    healthy, reasons = install._tool_health(tmp_path, spec)
    assert not healthy
    assert any("model snapshot file is missing or empty" in reason for reason in reasons)

    for filename in spec.required_model_files:
        path = model_root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
    healthy, reasons = install._tool_health(tmp_path, spec)
    assert healthy and not reasons


def test_snapshot_health_rejects_empty_required_file(tmp_path):
    spec = install.TOOLS["demucs"]
    model_root = tmp_path / spec.model_subdir
    model_root.mkdir(parents=True)
    for filename in spec.required_model_files:
        (model_root / filename).write_bytes(b"model")
    (model_root / spec.required_model_files[-1]).write_bytes(b"")

    assert not install._required_model_files_present(spec, tmp_path)


def test_ready_marker_does_not_claim_runtime_model_was_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_install_managed_env", lambda *_args, **_kwargs: None)

    install.install_tool("kokoro-82m", dest=tmp_path, gpu="cpu", log=lambda _message: None)

    marker = json.loads((tmp_path / "READY.json").read_text(encoding="utf-8"))
    assert marker["model"] is None
    assert marker["model_revision"] is None
    assert marker["model_downloaded"] is None
