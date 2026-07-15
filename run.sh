#!/usr/bin/env bash
# One-command bootstrap for MediaConductor from a source checkout (macOS/Linux).
# MediaConductor is a CLI + MCP server for LLM agents. This syncs Python
# dependencies and shows the curated command list.
#
# Usage: ./run.sh        (from the repo root)
#        bash run.sh
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
  echo "[FATAL] uv is not installed. Install it from https://docs.astral.sh/uv/ and re-run." >&2
  exit 1
fi

echo "==> Syncing Python dependencies (uv sync)..."
uv sync

echo "==> MediaConductor is ready. Start with:"
echo "      uv run mediaconductor modes        # choose a production mode"
echo "      uv run mediaconductor where --json # resolved paths + version"
echo "      uv run mediaconductor mcp          # run the MCP router for an agent host"
echo
uv run mediaconductor --help
