#!/usr/bin/env python3
"""One-command release cut: bump every version in lockstep, verify, tag.

Usage:
    uv run python scripts/release.py 1.2.3            # bump files only
    uv run python scripts/release.py 1.2.3 --tag      # + git commit and tag
    uv run python scripts/release.py --check          # verify versions agree

Then push:  git push origin main && git push origin v1.2.3
The pushed tag triggers .github/workflows/release.yml, which builds all
platforms and publishes the GitHub Release with download links.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "mangaeasy" / "__init__.py"
DESKTOP_PKG = ROOT / "desktop" / "package.json"
DESKTOP_LOCK = ROOT / "desktop" / "package-lock.json"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


def read_versions() -> dict[str, str]:
    pyproject = re.search(r'^version = "(.+?)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    init = re.search(r'^__version__ = "(.+?)"', INIT.read_text(encoding="utf-8"), re.M)
    desktop = json.loads(DESKTOP_PKG.read_text(encoding="utf-8"))["version"]
    assert pyproject and init, "version fields not found"
    return {"pyproject.toml": pyproject.group(1), "mangaeasy/__init__.py": init.group(1),
            "desktop/package.json": desktop}


def set_versions(version: str) -> None:
    PYPROJECT.write_text(
        re.sub(r'^version = ".+?"', f'version = "{version}"',
               PYPROJECT.read_text(encoding="utf-8"), count=1, flags=re.M),
        encoding="utf-8")
    INIT.write_text(
        re.sub(r'^__version__ = ".+?"', f'__version__ = "{version}"',
               INIT.read_text(encoding="utf-8"), count=1, flags=re.M),
        encoding="utf-8")
    for pkg_file in (DESKTOP_PKG, DESKTOP_LOCK):
        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        data["version"] = version
        if "packages" in data and "" in data["packages"]:  # package-lock v3
            data["packages"][""]["version"] = version
        pkg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", help="new version, e.g. 1.2.3")
    parser.add_argument("--check", action="store_true", help="verify all versions agree and exit")
    parser.add_argument("--tag", action="store_true", help="also git commit and create tag v<version>")
    args = parser.parse_args()

    if args.check or not args.version:
        versions = read_versions()
        for name, value in versions.items():
            print(f"  {name:28s} {value}")
        if len(set(versions.values())) != 1:
            print("MISMATCH — run scripts/release.py <version> to fix.")
            return 1
        print("All in sync.")
        return 0

    if not VERSION_RE.match(args.version):
        print(f"'{args.version}' is not a valid version (expected e.g. 1.2.3 or 1.2.3-beta1)")
        return 2

    set_versions(args.version)
    print(f"Set version {args.version} in:")
    for name, value in read_versions().items():
        print(f"  {name:28s} {value}")

    if args.tag:
        subprocess.run(["git", "add", str(PYPROJECT), str(INIT), str(DESKTOP_PKG), str(DESKTOP_LOCK)],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "commit", "-m", f"Release v{args.version}"], cwd=ROOT, check=True)
        subprocess.run(["git", "tag", f"v{args.version}"], cwd=ROOT, check=True)
        print(f"\nCommitted and tagged v{args.version}. Now push:\n"
              f"  git push origin main && git push origin v{args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
