/**
 * Persisted app state — currently just the chosen project root. Stored at
 * `<app_root>/.mangaeasy/app_state.json` (self-contained, see paths.ts'
 * `mangaeasyHome()`). This Electron app is the sole reader/writer of this
 * file — the old Python-side `mangaeasy/web/app/state.py` equivalent has
 * been removed.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { appRoot, mangaeasyHome } from './paths'

export interface WindowBounds {
  x?: number
  y?: number
  width: number
  height: number
  maximized?: boolean
}

interface AppState {
  project_root?: string
  window_bounds?: WindowBounds
  update_last_checked_ms?: number
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

function saveAppState(mutate: (state: AppState) => void): void {
  const home = mangaeasyHome()
  mkdirSync(home, { recursive: true })
  const current = loadAppState()
  mutate(current)
  writeFileSync(appStateFile(), JSON.stringify(current, null, 2), 'utf-8')
}

export function getProjectRoot(): string {
  const saved = loadAppState()
  if (saved.project_root && existsSync(saved.project_root)) {
    return path.resolve(saved.project_root)
  }
  // Nothing picked yet: this install's own data root (the repo in dev, the
  // per-platform data dir when packaged) — always writable, unlike the old
  // repoRoot() fallback which pointed inside the install when packaged.
  return appRoot()
}

export function setProjectRoot(projectRoot: string): void {
  saveAppState((state) => {
    state.project_root = projectRoot
  })
}

export function getWindowBounds(): WindowBounds | null {
  return loadAppState().window_bounds ?? null
}

export function setWindowBounds(bounds: WindowBounds): void {
  saveAppState((state) => {
    state.window_bounds = bounds
  })
}

export function getUpdateLastCheckedMs(): number {
  return loadAppState().update_last_checked_ms ?? 0
}

export function setUpdateLastCheckedMs(ms: number): void {
  saveAppState((state) => {
    state.update_last_checked_ms = ms
  })
}
