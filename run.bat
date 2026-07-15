@echo off
REM One-command bootstrap for MediaConductor from a source checkout (Windows).
REM MediaConductor is a CLI + MCP server for LLM agents. This syncs Python
REM dependencies and shows the curated command list.
REM
REM Usage: run it from a terminal in the repo root.
setlocal
title MediaConductor
cd /d "%~dp0"

REM uv's installer adds %USERPROFILE%\.local\bin to your user PATH, but a
REM shortcut/double-click launches through Explorer, which can be running
REM with a PATH cached from before that install. Fall back to the known
REM install location if a plain `where uv` can't find it.
where uv >nul 2>nul
if errorlevel 1 if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>nul
if errorlevel 1 (
  echo [FATAL] uv is not installed or not on PATH.
  echo         Install it from https://docs.astral.sh/uv/ and re-run.
  echo.
  exit /b 1
)

echo ==^> Syncing Python dependencies (uv sync)...
call uv sync
if errorlevel 1 (
  echo.
  echo [FATAL] uv sync failed -- see the error above.
  exit /b 1
)

echo ==^> MediaConductor is ready. Start with:
echo       uv run mediaconductor modes        ^(choose a production mode^)
echo       uv run mediaconductor where --json ^(resolved paths + version^)
echo       uv run mediaconductor mcp           ^(run the MCP router for an agent host^)
echo.
call uv run mediaconductor --help
endlocal
