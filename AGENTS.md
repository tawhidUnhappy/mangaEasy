# Agent notes for mangaEasy

**New here? Open [START_HERE.md](START_HERE.md) first** — the single entry map
to the whole repo (what it is, the pipeline, which doc for which job, the code
map). This file is the quick agent-facing summary; START_HERE points to
everything.

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
An MCP server is available: `mangaeasy mcp` (stdio). Fresh clone or fresh
machine? Follow the agent runbook in [docs/setup.md](docs/setup.md):
`uv sync` → `mangaeasy setup` → `mangaeasy doctor --json` → `mangaeasy
smoke-test` (renders and verifies a tiny real video — proof the env works,
not just that its parts are installed).

**Several agents on one project / resuming after interruption**: follow
[docs/multi-agent.md](docs/multi-agent.md) — `work-status` (resume), `work-claim`
(don't collide), `work-note` (share story facts), `work-qa` (fix-until-clean loop),
`work-artifacts` (reuse instead of regenerate).

**Producing a recap series** (MangaDex URL → uploaded videos, 12 chapters per
video): follow the skill at
[.claude/skills/manga-recap/SKILL.md](.claude/skills/manga-recap/SKILL.md) —
Claude Code loads it automatically; other agents can read it as a runbook.

Hard safety rules: never delete/rename inside `library/` source items; never
touch `narration.backup.json`; clear generated output only via the
`video-clean-*` commands; keep `--gpu-workers` ≤ 4.

**Developing this repo** (changing mangaEasy itself): read
[CLAUDE.md](CLAUDE.md) — architecture, conventions, gotchas, test/lint
requirements (`uv run pytest`, `uv run ruff check .`).
