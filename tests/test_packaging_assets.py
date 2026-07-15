"""Static release contracts that do not require PyInstaller or macOS."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def test_macos_bundle_uses_tracked_brand_icon_source():
    spec = (ROOT / "packaging" / "mediaconductor.spec").read_text(encoding="utf-8")
    assert (ROOT / "packaging" / "icon.png").is_file()
    assert 'ICON  = str(ROOT / "packaging" / "icon.png")' in spec
    assert 'ROOT / "packaging" / "icon.icns"' not in spec
    assert "ICON  = None" not in spec


def test_release_checks_declared_macos_icon_resource():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    build = "uv run --no-sync pyinstaller packaging/mediaconductor.spec"
    verify = "test -s \"$bundle/Contents/Resources/$icon\""
    assert build in workflow and verify in workflow
    assert workflow.index(build) < workflow.index(verify)


def test_windows_icon_contains_real_multi_resolution_frames():
    with Image.open(ROOT / "packaging" / "icon.ico") as icon:
        sizes = set(icon.info.get("sizes", []))
    assert {(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)} <= sizes
