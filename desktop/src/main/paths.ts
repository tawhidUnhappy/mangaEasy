/**
 * Path/command resolution for the mangaEasy Python backend.
 *
 * `desktop/` sits as a sibling of the `mangaeasy/` package at the repo root
 * (matches the sibling-project convention already used by AiSongTool's own
 * Electron rewrite at D:\AiSongTool\desktop). Three resolution modes, tried
 * in order:
 *   - Packaged: the PyInstaller-built backend bundled at build time under
 *     `resources/backend/` (see packaging/mangaeasy.spec + electron-builder.yml's
 *     extraResources) — no system Python/uv needed at all.
 *   - Dev (this repo, right now): the repo's own `.venv` (`uv run mangaeasy`).
 *   - Last resort: a `mangaeasy` PATH shim (`uv tool install` elsewhere).
 */
import { app } from 'electron'
import { existsSync } from 'fs'
import path from 'path'

/** Repo root (`D:\mangaEasy`), one level up from `desktop/out/main` at runtime. */
export function repoRoot(): string {
  return path.resolve(__dirname, '../../..')
}

/**
 * This install's own root folder — mirrors `mangaeasy.tools.external.app_root()`.
 * Dev: the repo root (this file lives at `<repo>/desktop/src/main/paths.ts`,
 * built to `<repo>/desktop/out/main/`). Packaged: the folder containing the
 * installed/portable app, i.e. the parent of Electron's resources dir — so
 * deleting that one folder removes the app and everything it ever wrote.
 */
export function appRoot(): string {
  const configured = process.env.MANGAEASY_ROOT
  if (configured) return path.resolve(configured)
  if (app.isPackaged) return path.dirname(process.resourcesPath)
  return repoRoot()
}

/** This install's own data dir — matches `mangaeasy.tools.external.mangaeasy_home()`. */
export function mangaeasyHome(): string {
  const configured = process.env.MANGAEASY_HOME
  if (configured) return path.resolve(configured)
  return path.join(appRoot(), '.mangaeasy')
}

function whichSync(name: string): string | null {
  const pathEnv = process.env.PATH ?? ''
  const exts = process.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : ['']
  for (const dir of pathEnv.split(path.delimiter)) {
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext)
      if (existsSync(candidate)) return candidate
    }
  }
  return null
}

/** Bundled backend exe path when packaged — `resources/backend/mangaeasy[.exe]`,
 * built by `packaging/mangaeasy.spec` and copied in before electron-builder runs. */
function bundledBackend(): string | null {
  if (!app.isPackaged) return null
  const exeName = process.platform === 'win32' ? 'mangaeasy.exe' : 'mangaeasy'
  const candidate = path.join(process.resourcesPath, 'backend', exeName)
  return existsSync(candidate) ? candidate : null
}

/** argv *prefix* that runs `mangaeasy <command> [...args]` — append the
 * command + its args to whatever this returns. */
export function mangaeasyCommand(): string[] {
  // Packaged: the frozen exe *is* the CLI (no `-m`/interpreter prefix needed,
  // same as mangaeasy/runtime.py's own `is_frozen()` branch) — and it's the
  // only mode where we deliberately don't fall through to a PATH shim, since
  // a packaged install must never depend on what else happens to be on PATH.
  const bundled = bundledBackend()
  if (bundled) return [bundled]

  const devPython = path.join(repoRoot(), '.venv', 'Scripts', 'python.exe')
  if (existsSync(devPython)) return [devPython, '-m', 'mangaeasy.cli']

  const shim = whichSync('mangaeasy')
  if (shim) return [shim]

  // Last resort: hope `mangaeasy` resolves some other way the shell knows
  // about (e.g. a non-Windows venv layout) — surfaces a clear ENOENT in the
  // terminal pane rather than silently doing nothing.
  return ['mangaeasy']
}

export function buildCli(command: string, args: string[] = []): string[] {
  return [...mangaeasyCommand(), command, ...args]
}
