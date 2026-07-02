/**
 * Path/command resolution for the mangaEasy Python backend.
 *
 * `desktop/` sits as a sibling of the `mangaeasy/` package at the repo root.
 * Three resolution modes, tried in order:
 *   - Packaged: the PyInstaller-built backend bundled at build time under
 *     `resources/backend/` (see packaging/mangaeasy.spec + electron-builder.yml's
 *     extraResources) — no system Python/uv needed at all.
 *   - Dev (this repo, right now): the repo's own `.venv` (`uv run mangaeasy`).
 *   - Last resort: a `mangaeasy` PATH shim (`uv tool install` elsewhere).
 */
import { app } from 'electron'
import { existsSync } from 'fs'
import os from 'os'
import path from 'path'

/** Repo root (`D:\mangaEasy`), one level up from `desktop/out/main` at runtime. */
export function repoRoot(): string {
  return path.resolve(__dirname, '../../..')
}

/**
 * This install's own root folder — where the app's data (`.mangaeasy/`,
 * default projects) lives. Mirrors `mangaeasy.tools.external.app_root()`.
 *
 * Packaged builds must NOT use the app's own install location: the Windows
 * portable exe self-extracts to a random %TEMP% dir every launch, the macOS
 * .app bundle is read-only (and translocated when quarantined), a Linux
 * AppImage runs from a read-only squashfs mount, and a .deb install lives in
 * root-owned /opt. So:
 *   - Windows portable: the folder containing the .exe the user ran
 *     (PORTABLE_EXECUTABLE_DIR) — data next to the app, delete-folder-and-
 *     it's-gone stays literally true.
 *   - macOS: ~/Library/Application Support/mangaEasy
 *   - Linux: $XDG_DATA_HOME/mangaEasy (default ~/.local/share/mangaEasy)
 * MANGAEASY_ROOT overrides everything (power users / tests).
 */
export function appRoot(): string {
  const configured = process.env.MANGAEASY_ROOT
  if (configured) return path.resolve(configured)
  if (!app.isPackaged) return repoRoot()

  if (process.platform === 'win32') {
    // Set by electron-builder's portable target: the directory holding the
    // .exe the user actually ran (resourcesPath points into the temp
    // self-extraction dir and must never be used for data).
    const portableDir = process.env.PORTABLE_EXECUTABLE_DIR
    if (portableDir) return path.resolve(portableDir)
    // Unpacked/zip layout: data next to the executable.
    return path.dirname(app.getPath('exe'))
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'mangaEasy')
  }
  const xdg = process.env.XDG_DATA_HOME
  const base = xdg && xdg.trim() ? xdg : path.join(os.homedir(), '.local', 'share')
  return path.join(base, 'mangaEasy')
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

  // Dev checkout: the repo's own venv (Windows and POSIX layouts differ).
  for (const rel of [
    ['Scripts', 'python.exe'],
    ['bin', 'python']
  ]) {
    const devPython = path.join(repoRoot(), '.venv', ...rel)
    if (existsSync(devPython)) return [devPython, '-m', 'mangaeasy.cli']
  }

  const shim = whichSync('mangaeasy')
  if (shim) return [shim]

  // Last resort: hope `mangaeasy` resolves some other way the shell knows
  // about — surfaces a clear ENOENT in the terminal pane rather than
  // silently doing nothing.
  return ['mangaeasy']
}

export function buildCli(command: string, args: string[] = []): string[] {
  return [...mangaeasyCommand(), command, ...args]
}
