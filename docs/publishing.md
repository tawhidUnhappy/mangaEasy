# Publishing Checklist

## Cutting a release

One script bumps every version in lockstep (`pyproject.toml`,
`mangaeasy/__init__.py`) and tags:

```bash
uv run python scripts/release.py 1.2.3 --tag
git push origin main && git push origin v1.2.3
```

Update `CHANGELOG.md` before tagging — the release page links to it.

The pushed `v*` tag triggers `.github/workflows/release.yml`, which on
Windows / Linux / macOS (Apple Silicon, plus best-effort Intel):

1. **Fails fast if the tag and source versions disagree** (so a forgotten
   `release.py` run can't ship mislabeled artifacts).
2. Runs ruff + pytest + a compile check.
3. Freezes the CLI with PyInstaller (`packaging/mediaconductor.spec`) into
   `dist/MediaConductor/` (or `dist/MediaConductor.app/` on macOS), verifies the
   macOS bundle contains its declared brand icon, and **smoke-tests it**
   (`--version`, `doctor --json`, and an MCP handshake).
4. Packages the frozen build into a per-OS archive
   (`media-conductor-<platform>.zip` / `.tar.gz`).
5. Publishes a GitHub Release with every platform's archive attached.

Core binaries (ffmpeg/ffprobe/uv/git-lfs) are **not** bundled — `mediaconductor
bootstrap-tools` downloads them once on first use, keeping archives small.

Monitor the build at `https://github.com/tawhidUnhappy/MediaConductor/actions`.
The Release appears at `https://github.com/tawhidUnhappy/MediaConductor/releases`
once the builds succeed (usually ~10-15 minutes).

### Pre-releases

Tags with a hyphen (e.g. `v1.1.0-beta1`) are automatically marked as
*pre-release* on the Release page so users on stable aren't bothered.

## Pre-push sanity (CI runs all of this too)

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
uv run python -m compileall -q mangaeasy
uv run python scripts/release.py --check
```

Make sure these are **not** committed (git-ignored by default): `.venv/`,
generated output (`audio/`, `output/`, `work/`), user media (`library/`,
`music/`, …), local `config.json`/`config.system.json`, `.mangaeasy/`, `dist/`.

## How users install

See [docs/install.md](install.md) — `uv tool install`, a frozen release
download (no Python needed), or from source.
