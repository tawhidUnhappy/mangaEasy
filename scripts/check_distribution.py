"""Verify that built distributions include agent docs/assets and no user media."""

from __future__ import annotations

from pathlib import Path
import sys
import tarfile
import zipfile


MEDIA_SUFFIXES = {".avi", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}
WHEEL_REQUIRED = {
    "mangaeasy/agent_skills/ai-story/SKILL.md",
    "mangaeasy/agent_skills/manga-video/SKILL.md",
    "mangaeasy/agent_skills/media-conductor/SKILL.md",
    "mangaeasy/agent_skills/song-video/SKILL.md",
    "mangaeasy/assets/fonts/edosz.ttf",
    "mangaeasy/assets/tools/generate_ace_step.py",
    "mangaeasy/assets/tools/generate_zimage.py",
    "mangaeasy/assets/tools/separate_demucs.py",
    "mangaeasy/assets/tools/transcribe_whisperx.py",
}
SDIST_REQUIRED_SUFFIXES = {
    "/AGENTS.md",
    "/LICENSE",
    "/README.md",
    "/SECURITY.md",
    "/THIRD_PARTY_NOTICES.md",
    "/mcp.example.json",
    "/skills/ai-story/SKILL.md",
    "/skills/manga-video/SKILL.md",
    "/skills/media-conductor/SKILL.md",
    "/skills/song-video/SKILL.md",
}


def _one(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        raise SystemExit(f"expected exactly one {label} in dist, found: {paths}")
    return paths[0]


def _reject_media(names: set[str], artifact: Path) -> None:
    media = sorted(name for name in names if Path(name).suffix.lower() in MEDIA_SUFFIXES)
    if media:
        raise SystemExit(f"user/generated media leaked into {artifact.name}: {media}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dist = Path(args[0] if args else "dist")
    wheel = _one(sorted(dist.glob("*.whl")), "wheel")
    sdist = _one(sorted(dist.glob("*.tar.gz")), "source archive")

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    missing_wheel = sorted(WHEEL_REQUIRED - wheel_names)
    if missing_wheel:
        raise SystemExit(f"wheel is missing required files: {missing_wheel}")
    _reject_media(wheel_names, wheel)

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = {member.name for member in archive.getmembers() if member.isfile()}
    missing_sdist = sorted(
        suffix for suffix in SDIST_REQUIRED_SUFFIXES
        if not any(name.endswith(suffix) for name in sdist_names)
    )
    if missing_sdist:
        raise SystemExit(f"source archive is missing required files: {missing_sdist}")
    if not any(name.endswith("/mangaeasy/assets/fonts/edosz.ttf") for name in sdist_names):
        raise SystemExit("source archive is missing mangaeasy/assets/fonts/edosz.ttf")
    _reject_media(sdist_names, sdist)

    print(f"Distribution payload passed: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
