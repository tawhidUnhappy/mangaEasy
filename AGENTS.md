# Agent notes for mangaEasy

**Using the tool** (turning images + narration into videos): the entire tool
surface is the `mangaeasy` CLI — read **[docs/ai-guide.md](docs/ai-guide.md)**
first; it is the complete operating manual (install modes, data anatomy,
recipes, JSON/exit-code contract, safety rules). Orient with:

```bash
mangaeasy where --json       # this install's data/tool paths + version
mangaeasy commands --json    # full command catalog
mangaeasy doctor --json      # machine readiness (ffmpeg, GPU, AI tools)
```

No command prompts for input. `--json` commands print one JSON object;
generation commands end with a `MANGAEASY_RESULT {"outputs": [...]}` line.
An MCP server is available: `mangaeasy mcp` (stdio).

Hard safety rules: never delete/rename inside `library/` source items; never
touch `narration.backup.json`; clear generated output only via the
`video-clean-*` commands; keep `--gpu-workers` ≤ 4.

**Developing this repo** (changing mangaEasy itself): read
[CLAUDE.md](CLAUDE.md) — architecture, conventions, gotchas, test/lint
requirements (`uv run pytest`, `uv run ruff check .`, desktop
`npm run typecheck`).
