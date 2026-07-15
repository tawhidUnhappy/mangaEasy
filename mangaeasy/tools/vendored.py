"""mangaeasy.tools.vendored — small isolated binaries (ffmpeg, uv, git-lfs).

These aren't AI tools (see install.py for those) — they're plain executables
the pipeline shells out to by bare name (``"ffmpeg"``, ``"uv"``, ``"git-lfs"``).
Rather than patch every call site to resolve a path, this module prepends
each vendored tool's own ``bin/`` folder to ``PATH`` once at process start
(:func:`ensure_vendored_path`, called from ``cli.py``'s ``main()``) — every
existing and future bare-name subprocess call picks the vendored copy up
for free, falling back to the system PATH automatically if it isn't vendored.

:func:`ensure_core_tools` (run by ``mediaconductor bootstrap-tools``) is the
on-demand fetcher: it downloads whatever of ffmpeg/uv/git-lfs is missing
straight into the install's own self-contained tools dir, so a user never
needs them on the system PATH.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from mangaeasy.tools.external import tools_home

LogFn = Callable[[str], None]

VENDORED_TOOLS = ("ffmpeg", "uv", "git-lfs")
DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_ARCHIVE_BYTES = 1_500_000_000
MAX_MEMBER_BYTES = 750_000_000
UV_VERSION = "0.11.16"
GIT_LFS_VERSION = "3.7.1"

UV_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("windows", "x64"): (
        "uv-x86_64-pc-windows-msvc.zip",
        "dd9d6d6554bfab265bfa98aa8e8a406c5c3a7b97582f93de1f4d48d9154a0395",
    ),
    ("linux", "x64"): (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "74947fe2c03315cf07e82ab3acc703eddef01aba4d5232a98e4c6825ec116131",
    ),
    ("linux", "arm64"): (
        "uv-aarch64-unknown-linux-gnu.tar.gz",
        "8c9d0f0ee98166ae6ab198747519ba6f25db29d185bd2ae5960ecebc91a5c22a",
    ),
    ("darwin", "x64"): (
        "uv-x86_64-apple-darwin.tar.gz",
        "6b91ae3de155f51bd1f5b74814821c79f016a176561f252cd9ddfb976939af2e",
    ),
    ("darwin", "arm64"): (
        "uv-aarch64-apple-darwin.tar.gz",
        "2b25be1af546be330b340b0a76b99f989daa6d92678fdffb87438e661e9d88fb",
    ),
}

GIT_LFS_ASSETS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("windows", "x64"): (
        "git-lfs-windows-amd64-v3.7.1.zip", "git-lfs.exe",
        "8683cdc3d6c029b49393dcebbaa6265bd6efd9abdcf837be855b4cd42e5e80b6",
    ),
    ("linux", "x64"): (
        "git-lfs-linux-amd64-v3.7.1.tar.gz", "git-lfs",
        "1c0b6ee5200ca708c5cebebb18fdeb0e1c98f1af5c1a9cba205a4c0ab5a5ec08",
    ),
    ("linux", "arm64"): (
        "git-lfs-linux-arm64-v3.7.1.tar.gz", "git-lfs",
        "73a9c90eeb4312133a63c3eaee0c38c019ea7bfa0953d174809d25b18588dd8d",
    ),
    ("darwin", "x64"): (
        "git-lfs-darwin-amd64-v3.7.1.zip", "git-lfs",
        "b5b1b641c0648c83661fa9eda991cd3eff945264dabc2cdf411a80dfe7ec0970",
    ),
    ("darwin", "arm64"): (
        "git-lfs-darwin-arm64-v3.7.1.zip", "git-lfs",
        "76260fb34f4ee622ff0a66b857e5954aa49c7e343a92e57a1ec4a760618c94b2",
    ),
}


def _vendored_root(tool: str) -> Path:
    return tools_home() / "_vendor" / tool


def _bin_dir(tool: str) -> Path:
    return _vendored_root(tool) / "bin"


def vendored_bin_dirs() -> list[Path]:
    return [_bin_dir(tool) for tool in VENDORED_TOOLS if _bin_dir(tool).is_dir()]


def ensure_vendored_path() -> None:
    """Prepend every vendored tool's bin/ dir to this process' PATH.

    Safe to call unconditionally and often — pure filesystem checks, no
    network access. Vendored copies win over the system PATH (listed first);
    anything not vendored here just falls through to whatever's already on
    PATH, exactly like before this module existed.
    """
    dirs = vendored_bin_dirs()
    if not dirs:
        return
    prefix = os.pathsep.join(str(d) for d in dirs)
    os.environ["PATH"] = f"{prefix}{os.pathsep}{os.environ.get('PATH', '')}"


def _is_vendored(tool: str) -> bool:
    system, _arch = _platform_arch()
    exe = ".exe" if system == "windows" else ""
    expected = {
        "ffmpeg": (f"ffmpeg{exe}", f"ffprobe{exe}"),
        "uv": (f"uv{exe}", f"uvx{exe}"),
        "git-lfs": (f"git-lfs{exe}",),
    }.get(tool, ())
    return bool(expected) and all(
        (path := _bin_dir(tool) / name).is_file() and path.stat().st_size >= 1024
        for name in expected
    )


def _make_executable(path: Path) -> None:
    if sys.platform != "win32":
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _download(
    url: str,
    dest: Path,
    log: LogFn,
    *,
    expected_sha256: str | None = None,
    max_bytes: int = MAX_ARCHIVE_BYTES,
) -> Path:
    if not url.lower().startswith("https://"):
        raise RuntimeError(f"refusing non-HTTPS download: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {url}")
    part = dest.with_name(f".{dest.name}.part")
    part.unlink(missing_ok=True)
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "MediaConductor-bootstrap/2"})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, part.open("wb") as f:
            final_url = response.geturl()
            if not final_url.lower().startswith("https://"):
                raise RuntimeError(f"download redirected to non-HTTPS URL: {final_url}")
            total = int(response.headers.get("Content-Length") or 0)
            if total > max_bytes:
                raise RuntimeError(f"download is too large ({total} bytes; limit {max_bytes})")
            done = 0
            next_report = 0.10
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                done += len(chunk)
                if done > max_bytes:
                    raise RuntimeError(f"download exceeded the {max_bytes}-byte limit")
                digest.update(chunk)
                f.write(chunk)
                if total and done / total >= next_report:
                    log(
                        f"  ... {done / total:4.0%} "
                        f"({done // (1024 * 1024)} / {total // (1024 * 1024)} MB)"
                    )
                    next_report += 0.10
                elif not total and done % (32 * 1024 * 1024) < 1024 * 256:
                    log(f"  ... {done // (1024 * 1024)} MB")
            if total and done != total:
                raise RuntimeError(f"download truncated: expected {total} bytes, received {done}")
        if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
            raise RuntimeError(
                f"SHA-256 mismatch for {dest.name}: expected {expected_sha256}, "
                f"received {digest.hexdigest()}"
            )
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    log(f"  done ({done // (1024 * 1024)} MB, sha256={digest.hexdigest()[:12]}…)")
    return dest


def _extract_member(archive_path: Path, member_suffix: str, dest: Path, log: LogFn) -> bool:
    """Find a single file inside a zip/tar.* archive by suffix match and
    extract just that file to `dest`. Returns False if nothing matched."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                if name.replace("\\", "/").endswith(member_suffix):
                    info = zf.getinfo(name)
                    if info.file_size > MAX_MEMBER_BYTES:
                        raise RuntimeError(f"archive member is too large: {name}")
                    part = dest.with_name(f".{dest.name}.part")
                    with zf.open(name) as src, part.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    os.replace(part, dest)
                    return True
        return False
    try:
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                if member.name.endswith(member_suffix) and member.isfile():
                    if member.size > MAX_MEMBER_BYTES:
                        raise RuntimeError(f"archive member is too large: {member.name}")
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    part = dest.with_name(f".{dest.name}.part")
                    with part.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    os.replace(part, dest)
                    return True
    except tarfile.ReadError:
        log(f"[warn] could not read archive {archive_path}")
    return False


def _remote_checksum(manifest_url: str, asset: str) -> str:
    """Read one SHA-256 from a small HTTPS checksum manifest."""
    if not manifest_url.lower().startswith("https://"):
        raise RuntimeError(f"refusing non-HTTPS checksum URL: {manifest_url}")
    request = urllib.request.Request(
        manifest_url,
        headers={"User-Agent": "MediaConductor-bootstrap/2"},
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        if not response.geturl().lower().startswith("https://"):
            raise RuntimeError("checksum manifest redirected to a non-HTTPS URL")
        content_length = int(response.headers.get("Content-Length") or 0)
        if content_length > 2_000_000:
            raise RuntimeError("checksum manifest is unexpectedly large")
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise RuntimeError("checksum manifest exceeded the 2 MB limit")
    for line in raw.decode("utf-8", errors="strict").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        if filename.lstrip("*") == asset and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
    raise RuntimeError(f"checksum manifest does not contain {asset}")


def _download_with_manifest_checksum(
    url: str,
    manifest_url: str,
    dest: Path,
    log: LogFn,
) -> Path:
    """Fetch a rolling asset only when its concurrently published hash matches."""
    asset = Path(url).name
    for attempt in range(2):
        expected = _remote_checksum(manifest_url, asset)
        try:
            return _download(url, dest, log, expected_sha256=expected)
        except RuntimeError as exc:
            if attempt or "SHA-256 mismatch" not in str(exc):
                raise
            log("[warn] rolling release changed during download; refreshing its checksum once")
    raise AssertionError("unreachable")


def _platform_arch() -> tuple[str, str]:
    system = platform.system().lower()  # "windows", "linux", "darwin"
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return system, arch


def _fetch_ffmpeg_macos(arch: str, log: LogFn) -> bool:
    """macOS static builds from ffmpeg.martin-riedl.de — one zip per binary
    (top-level `ffmpeg` / `ffprobe` member), published for both arm64 and
    x86_64 with a stable `latest` redirect URL."""
    riedl_arch = "arm64" if arch == "arm64" else "amd64"
    bin_dir = _bin_dir("ffmpeg")
    ok = True
    log(
        "[warn] the macOS FFmpeg provider does not publish archive checksums; "
        "prefer a trusted system ffmpeg/ffprobe when available"
    )
    for name in ("ffmpeg", "ffprobe"):
        url = f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{riedl_arch}/release/{name}.zip"
        archive = _download(url, _vendored_root("ffmpeg") / "_dl" / f"{name}.zip", log)
        extracted = _extract_member(archive, name, bin_dir / name, log)
        if extracted:
            _make_executable(bin_dir / name)
        ok = ok and extracted
    shutil.rmtree(_vendored_root("ffmpeg") / "_dl", ignore_errors=True)
    return ok


def fetch_ffmpeg(log: LogFn) -> bool:
    """Vendor a static ffmpeg+ffprobe build.

    Windows/Linux use BtbN/FFmpeg-Builds' GPL static releases (well-known,
    stable URLs); macOS uses ffmpeg.martin-riedl.de's static builds (both
    Apple Silicon and Intel). Runs on demand on first launch — nothing is
    bundled into the installer, so the download (~50-120 MB) happens once
    per install, with progress logged to the terminal pane.
    """
    if _is_vendored("ffmpeg"):
        return True
    system, arch = _platform_arch()
    if system == "darwin":
        return _fetch_ffmpeg_macos(arch, log)
    ext = "exe" if system == "windows" else ""
    if system == "windows" and arch == "x64":
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
    elif system == "linux" and arch == "x64":
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"
    elif system == "linux" and arch == "arm64":
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
    else:
        log(f"[warn] no vendored ffmpeg build for {system}/{arch} -- install it yourself "
            f"and it'll be picked up from PATH.")
        return False

    archive = _download_with_manifest_checksum(
        url,
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256",
        _vendored_root("ffmpeg") / "_dl" / Path(url).name,
        log,
    )
    bin_dir = _bin_dir("ffmpeg")
    ok_ffmpeg = _extract_member(archive, f"bin/ffmpeg{('.' + ext) if ext else ''}",
                                 bin_dir / f"ffmpeg{('.' + ext) if ext else ''}", log)
    ok_ffprobe = _extract_member(archive, f"bin/ffprobe{('.' + ext) if ext else ''}",
                                  bin_dir / f"ffprobe{('.' + ext) if ext else ''}", log)
    shutil.rmtree(archive.parent, ignore_errors=True)
    if ok_ffmpeg:
        _make_executable(bin_dir / f"ffmpeg{('.' + ext) if ext else ''}")
    if ok_ffprobe:
        _make_executable(bin_dir / f"ffprobe{('.' + ext) if ext else ''}")
    return ok_ffmpeg and ok_ffprobe


def fetch_uv(log: LogFn) -> bool:
    """Vendor a static `uv`/`uvx` build from astral-sh/uv's GitHub releases."""
    if _is_vendored("uv"):
        return True
    system, arch = _platform_arch()
    target = UV_ASSETS.get((system, arch))
    if target is None:
        log(f"[warn] no vendored uv build for {system}/{arch}")
        return False
    asset, sha256 = target
    url = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{asset}"
    archive = _download(
        url,
        _vendored_root("uv") / "_dl" / asset,
        log,
        expected_sha256=sha256,
    )
    bin_dir = _bin_dir("uv")
    ext = ".exe" if system == "windows" else ""
    ok_uv = _extract_member(archive, f"uv{ext}", bin_dir / f"uv{ext}", log)
    ok_uvx = _extract_member(archive, f"uvx{ext}", bin_dir / f"uvx{ext}", log)
    shutil.rmtree(archive.parent, ignore_errors=True)
    if ok_uv:
        _make_executable(bin_dir / f"uv{ext}")
    if ok_uvx:
        _make_executable(bin_dir / f"uvx{ext}")
    return ok_uv and ok_uvx


def fetch_git_lfs(log: LogFn) -> bool:
    """Vendor a static `git-lfs` build from git-lfs/git-lfs's GitHub releases."""
    if _is_vendored("git-lfs"):
        return True
    system, arch = _platform_arch()
    target = GIT_LFS_ASSETS.get((system, arch))
    if target is None:
        log(f"[warn] no vendored git-lfs build for {system}/{arch}")
        return False
    asset, exe_name, sha256 = target
    url = f"https://github.com/git-lfs/git-lfs/releases/download/v{GIT_LFS_VERSION}/{asset}"
    archive = _download(
        url,
        _vendored_root("git-lfs") / "_dl" / asset,
        log,
        expected_sha256=sha256,
    )
    bin_dir = _bin_dir("git-lfs")
    ok = _extract_member(archive, exe_name, bin_dir / exe_name, log)
    shutil.rmtree(archive.parent, ignore_errors=True)
    if ok:
        _make_executable(bin_dir / exe_name)
    return ok


def bootstrap_main() -> int:
    """`mediaconductor bootstrap-tools` — fetch ffmpeg/uv/git-lfs into this
    install's own tools dir. The installers deliberately don't bundle these
    binaries to keep downloads small; the Setup step (or a dev on a checkout)
    runs this to grab them."""
    results = ensure_core_tools(print)
    ok = all(results.values())
    for name, success in results.items():
        status = "ok" if success else "FAILED (see warning above)"
        print(f"  {name:10s} {status}")
    if not ok:
        print("\nERROR: some core tools could not be downloaded. Check your "
              "internet connection and re-run Setup -> Download core tools.")
    return 0 if ok else 1


def ensure_core_tools(log: LogFn) -> dict[str, bool]:
    """Fetch whatever core binaries (ffmpeg, uv, git-lfs) aren't already on
    PATH and aren't already vendored. Used by `mediaconductor bootstrap-tools`
    (manual/CI) and as a same-process fallback when something needed at
    runtime is missing entirely."""
    results = {}
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        results["ffmpeg"] = True
    else:
        results["ffmpeg"] = fetch_ffmpeg(log)
    if shutil.which("uv") and shutil.which("uvx"):
        results["uv"] = True
    else:
        results["uv"] = fetch_uv(log)
    if shutil.which("git-lfs"):
        results["git-lfs"] = True
    else:
        results["git-lfs"] = fetch_git_lfs(log)
    ensure_vendored_path()
    return results
