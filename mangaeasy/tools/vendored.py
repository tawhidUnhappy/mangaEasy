"""mangaeasy.tools.vendored — small isolated binaries (ffmpeg, uv, git-lfs).

These aren't AI tools (see install.py for those) — they're plain executables
the pipeline shells out to by bare name (``"ffmpeg"``, ``"uv"``, ``"git-lfs"``).
Rather than patch every call site to resolve a path, this module prepends
each vendored tool's own ``bin/`` folder to ``PATH`` once at process start
(:func:`ensure_vendored_path`, called from ``cli.py``'s ``main()``) — every
existing and future bare-name subprocess call picks the vendored copy up
for free, falling back to the system PATH automatically if it isn't vendored.

CI pre-fetches all three at build time and bundles them into the installer
(see desktop/electron-builder.yml's extraResources), so a normal install
never downloads anything here. :func:`ensure_core_tools` is the on-demand
fallback for everyone else (dev checkouts, future platforms CI hasn't
pre-built yet, or the bare `pip install mangaeasy` path) — it fetches
whatever's missing straight into the same self-contained tools dir.
"""

from __future__ import annotations

import os
import platform
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


def _vendored_root(tool: str) -> Path:
    return tools_home() / "_vendor" / tool


def _bin_dir(tool: str) -> Path:
    if tool == "node" and platform.system().lower() == "windows":
        # Windows Node.js distributions put node.exe/npm.cmd/npx.cmd (and
        # the node_modules/npm they depend on) at the archive's top level,
        # not in a bin/ subfolder like Unix tarballs do.
        return _vendored_root(tool)
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
    return _bin_dir(tool).is_dir() and any(_bin_dir(tool).iterdir())


def _make_executable(path: Path) -> None:
    if sys.platform != "win32":
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _download(url: str, dest: Path, log: LogFn) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {url}")
    with urllib.request.urlopen(url) as response, dest.open("wb") as f:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        next_report = 0.10
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done / total >= next_report:
                log(f"  ... {done / total:4.0%} ({done // (1024 * 1024)} / {total // (1024 * 1024)} MB)")
                next_report += 0.10
            elif not total and done % (32 * 1024 * 1024) < 1024 * 256:
                log(f"  ... {done // (1024 * 1024)} MB")
    log(f"  done ({done // (1024 * 1024)} MB)")
    return dest


def _extract_member(archive_path: Path, member_suffix: str, dest: Path, log: LogFn) -> bool:
    """Find a single file inside a zip/tar.* archive by suffix match and
    extract just that file to `dest`. Returns False if nothing matched."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                if name.replace("\\", "/").endswith(member_suffix):
                    with zf.open(name) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    return True
        return False
    try:
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                if member.name.endswith(member_suffix) and member.isfile():
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    return True
    except tarfile.ReadError:
        log(f"[warn] could not read archive {archive_path}")
    return False


def _extract_all(archive_path: Path, dest_dir: Path, log: LogFn) -> bool:
    """Extract an entire zip/tar.* archive into dest_dir, then flatten it one
    level if the archive contained exactly one top-level folder (Node.js
    distributions are shipped as a single `node-vX.Y.Z-<platform>/` folder).
    Unlike :func:`_extract_member`, this is for tools shipped as a whole
    directory tree (npm's node_modules) rather than one standalone binary."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(dest_dir)
        else:
            with tarfile.open(archive_path) as tf:
                tf.extractall(dest_dir)
    except (zipfile.BadZipFile, tarfile.ReadError) as exc:
        log(f"[warn] could not read archive {archive_path}: {exc}")
        return False

    entries = list(dest_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        top = entries[0]
        for child in top.iterdir():
            shutil.move(str(child), str(dest_dir / child.name))
        top.rmdir()
    return True


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

    archive = _download(url, _vendored_root("ffmpeg") / "_dl" / Path(url).name, log)
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
    targets = {
        ("windows", "x64"): "uv-x86_64-pc-windows-msvc.zip",
        ("linux", "x64"): "uv-x86_64-unknown-linux-gnu.tar.gz",
        ("linux", "arm64"): "uv-aarch64-unknown-linux-gnu.tar.gz",
        ("darwin", "x64"): "uv-x86_64-apple-darwin.tar.gz",
        ("darwin", "arm64"): "uv-aarch64-apple-darwin.tar.gz",
    }
    asset = targets.get((system, arch))
    if asset is None:
        log(f"[warn] no vendored uv build for {system}/{arch}")
        return False
    url = f"https://github.com/astral-sh/uv/releases/latest/download/{asset}"
    archive = _download(url, _vendored_root("uv") / "_dl" / asset, log)
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
    # git-lfs publishes versioned filenames (no "latest" alias), so this
    # pins to a known-good release rather than guessing a moving target.
    version = "3.5.1"
    targets = {
        ("windows", "x64"): (f"git-lfs-windows-amd64-v{version}.zip", "git-lfs.exe"),
        ("linux", "x64"): (f"git-lfs-linux-amd64-v{version}.tar.gz", "git-lfs"),
        ("linux", "arm64"): (f"git-lfs-linux-arm64-v{version}.tar.gz", "git-lfs"),
        ("darwin", "x64"): (f"git-lfs-darwin-amd64-v{version}.zip", "git-lfs"),
        ("darwin", "arm64"): (f"git-lfs-darwin-arm64-v{version}.zip", "git-lfs"),
    }
    target = targets.get((system, arch))
    if target is None:
        log(f"[warn] no vendored git-lfs build for {system}/{arch}")
        return False
    asset, exe_name = target
    url = f"https://github.com/git-lfs/git-lfs/releases/download/v{version}/{asset}"
    archive = _download(url, _vendored_root("git-lfs") / "_dl" / asset, log)
    bin_dir = _bin_dir("git-lfs")
    ok = _extract_member(archive, exe_name, bin_dir / exe_name, log)
    shutil.rmtree(archive.parent, ignore_errors=True)
    if ok:
        _make_executable(bin_dir / exe_name)
    return ok


def fetch_node(log: LogFn) -> bool:
    """Vendor a portable Node.js + npm build from nodejs.org, for building
    the Electron desktop app from a source checkout without requiring a
    system-wide Node install. Only called on demand by run.sh/run.bat (and
    `mangaeasy ensure-node`) -- the core video pipeline never needs Node, so
    this is intentionally separate from :func:`ensure_core_tools`/CI."""
    if _is_vendored("node"):
        return True
    system, arch = _platform_arch()
    # nodejs.org has no "latest LTS" alias URL, so this pins a known-good
    # LTS release rather than guessing a moving target.
    version = "22.23.1"
    exts = {"windows": "zip", "linux": "tar.xz", "darwin": "tar.gz"}
    ext = exts.get(system)
    if ext is None:
        log(f"[warn] no vendored Node.js build for {system}/{arch}")
        return False
    asset = f"node-v{version}-{system if system != 'windows' else 'win'}-{arch}.{ext}"
    url = f"https://nodejs.org/dist/v{version}/{asset}"
    archive = _download(url, _vendored_root("node") / "_dl" / asset, log)
    # Extract into a scratch dir, not _vendored_root("node") itself -- the
    # download above already lives under _dl/ inside that same root, and
    # _extract_all's one-level flatten only kicks in when the destination
    # contains exactly the archive's single top-level folder.
    tmp_extract = _vendored_root("node") / "_extract_tmp"
    shutil.rmtree(tmp_extract, ignore_errors=True)
    ok = _extract_all(archive, tmp_extract, log)
    if ok:
        for child in tmp_extract.iterdir():
            dest = _vendored_root("node") / child.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(child), str(dest))
    shutil.rmtree(tmp_extract, ignore_errors=True)
    shutil.rmtree(archive.parent, ignore_errors=True)
    if ok and system != "windows":
        bin_dir = _bin_dir("node")
        for name in ("node", "npm", "npx"):
            exe = bin_dir / name
            if exe.exists():
                _make_executable(exe)
    return ok and _is_vendored("node")


def bootstrap_main() -> int:
    """`mangaeasy bootstrap-tools` — fetch ffmpeg/uv/git-lfs into this
    install's own tools dir. The desktop app runs this on first launch (the
    installers deliberately don't bundle these binaries to keep downloads
    small); anyone can also run it by hand on a dev checkout."""
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
    PATH and aren't already vendored. Used by `mangaeasy bootstrap-tools`
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


def ensure_node(log: LogFn) -> bool:
    """Fetch a portable Node.js/npm into this install's tools dir if npm
    isn't already on PATH or already vendored, then add it to this
    process' PATH. Returns False only on a genuine download/extract
    failure (no vendored build for this platform, network error, etc)."""
    if shutil.which("npm") and shutil.which("node"):
        return True
    ok = fetch_node(log)
    ensure_vendored_path()
    return ok


def ensure_node_main() -> int:
    """`mangaeasy ensure-node` -- vendor Node.js/npm on demand for building
    the Electron desktop app from source (see run.sh/run.bat). Prints the
    resolved bin dir on success so callers can confirm it without
    duplicating mangaeasy's path logic."""
    if ensure_node(print):
        print(f"  node       ok ({_bin_dir('node')})")
        return 0
    print("  node       FAILED (see warning above)")
    return 1
