# Publishing Checklist

## Cutting a release

One script bumps every version in lockstep (pyproject.toml,
`mangaeasy/__init__.py`, desktop/package.json + lock) and tags:

```bash
uv run python scripts/release.py 1.2.3 --tag
git push origin main && git push origin v1.2.3
```

Update `CHANGELOG.md` before tagging — the release page links to it.

The pushed `v*` tag triggers `.github/workflows/release.yml`, which on
Windows / Linux / macOS (Apple Silicon, plus best-effort Intel):

1. **Fails fast if the tag and source versions disagree** (so a forgotten
   `release.py` run can't ship mislabeled artifacts), then stamps the tag
   version into `desktop/package.json`.
2. Runs ruff + pytest + a compile check.
3. Builds the PyInstaller backend (`packaging/mangaeasy.spec`) into
   `desktop/resources/backend/` and **smoke-tests it** (`--version`,
   `doctor --json`).
4. Runs `electron-builder` for that platform (Windows: portable exe only —
   no NSIS installer by design; macOS: dmg + zip; Linux: AppImage + deb +
   tar.gz), artifacts named `mangaEasy-<version>-<os>-<arch>[...]`.
5. Publishes a GitHub Release with every platform's artifacts attached.

Core binaries (ffmpeg/ffprobe/uv/git-lfs) are **not** bundled — the app
downloads them once on first launch (Setup tab → Download core tools). This
keeps installers ~50-70% smaller.

Monitor the build at `https://github.com/tawhidUnhappy/mangaEasy/actions`.
The Release appears at `https://github.com/tawhidUnhappy/mangaEasy/releases`
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
cd desktop && npm run lint && npm run build
```

Make sure these are **not** committed (git-ignored by default): `.venv/`,
generated output (`audio/`, `output/`, `work/`), user media (`library/`,
`music/`, …), local `config.json`/`config.system.json`, `.mangaeasy/`,
`desktop/node_modules|out|dist`, `desktop/resources/backend/`.

## How users install

See [docs/install.md](install.md) — Releases page download (no Python
needed), `uv tool install`, or from source.
