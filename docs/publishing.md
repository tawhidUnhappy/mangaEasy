# Publishing Checklist

Before pushing a release, sanity-check the package:

```bash
uv sync
uv run python -m compileall mangaeasy
uv run mangaeasy --help
uv build
git status --ignored
```

Make sure these are **not** committed (they are git-ignored by default):

- `.venv/`, `.cache/`, `.hf_cache/`
- generated runtime output: `audio/`, `output/`, `work/`, `download_logs/`
- user media: `library/` (chapters; `manga/` on old projects), `music/`, `fonts/`, `background_image/`, `vocal/`
- local `config.json` and `config.system.json`
- sibling external tools: `kokoro-82m/`, `index-tts/`, `magi-v3/`

## First publish

```bash
git init
git add .
git commit -m "Initial public package"
git branch -M main
git remote add origin https://github.com/tawhidUnhappy/mangaEasy.git
git push -u origin main
```

## Cutting a new version

1. Bump `version` in `pyproject.toml` and `__version__` in
   `mangaeasy/__init__.py` (keep them in sync).
2. Commit, tag, and push:
   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
3. GitHub Actions (`.github/workflows/release.yml`) picks up the `v*` tag and:
   - Builds the standalone bundle on `windows-latest`, `ubuntu-latest`, `macos-latest`
   - Packages each as `mangaEasy-windows.zip`, `mangaEasy-linux.tar.gz`, `mangaEasy-macos.tar.gz`
   - Creates a GitHub Release and uploads all three as downloadable assets

Monitor the build at `https://github.com/tawhidUnhappy/mangaEasy/actions`.
The Release page appears at `https://github.com/tawhidUnhappy/mangaEasy/releases`
once all three builds succeed (usually ~10 minutes).

### Pre-releases

Tags with a hyphen (e.g. `v0.6.0-beta1`) are automatically marked as
*pre-release* in the Release page so users on stable aren't bothered.

## How users install

### Standalone (no Python required)
Download from the Releases page — see [docs/install.md](install.md).

### uv tool
```bash
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git
# or run once, without installing:
uvx --from git+https://github.com/tawhidUnhappy/mangaEasy.git mangaeasy --help
```
