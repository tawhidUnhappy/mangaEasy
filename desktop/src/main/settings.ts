/**
 * Persisted app state — currently just the chosen project root. Stored at
 * `<app_root>/.mangaeasy/app_state.json` (self-contained, see paths.ts'
 * `mangaeasyHome()`). This Electron app is the sole reader/writer of this
 * file — the old Python-side `mangaeasy/web/app/state.py` equivalent has
 * been removed.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { mangaeasyHome, repoRoot } from './paths'

interface AppState {
  project_root?: string
}

function appStateFile(): string {
  return path.join(mangaeasyHome(), 'app_state.json')
}

function loadAppState(): AppState {
  const file = appStateFile()
  if (!existsSync(file)) return {}
  try {
    return JSON.parse(readFileSync(file, 'utf-8')) as AppState
  } catch {
    return {}
  }
}

export function getProjectRoot(): string {
  const saved = loadAppState()
  if (saved.project_root && existsSync(saved.project_root)) {
    return path.resolve(saved.project_root)
  }
  // Dev convenience default — this repo itself, same as the NiceGUI app
  // falling back to `Path.cwd()` when nothing's been picked yet.
  return repoRoot()
}

export function setProjectRoot(projectRoot: string): void {
  const home = mangaeasyHome()
  mkdirSync(home, { recursive: true })
  const current = loadAppState()
  current.project_root = projectRoot
  writeFileSync(appStateFile(), JSON.stringify(current, null, 2), 'utf-8')
}
