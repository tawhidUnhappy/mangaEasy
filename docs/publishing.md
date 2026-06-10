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
- user media: `manga/`, `music/`, `fonts/`, `background_image/`, `vocal/`
- local `config.json` and `config.system.json`
- sibling external tools: `kokoro-82m/`, `index-tts/`, `f5-tts/`, `magi-v3/`

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
2. Commit and tag:
   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push --tags
   ```

## How users install

```bash
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git
# or run once, without installing:
uvx --from git+https://github.com/tawhidUnhappy/mangaEasy.git mangaeasy --help
```
