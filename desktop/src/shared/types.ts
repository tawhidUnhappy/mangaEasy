/**
 * Shared IPC contract types between main and renderer.
 *
 * Mirrors the data shapes the Python NiceGUI app (`mangaeasy/web/nicegui_app.py`)
 * used inline — `config.json`/`config.system.json` content, per-chapter status,
 * and library entries — so the Electron rewrite reads/writes the exact same
 * on-disk files without needing any new Python-side schema.
 */

export interface DownloadConfig {
  manga_id?: string
  name?: string
  chapter?: number
  translated_language?: string
}

export interface AppConfig {
  download?: DownloadConfig
  audio?: { bgm?: string; speaker_wav?: string }
  tts?: { speaker_wav?: string }
  [key: string]: unknown
}

export interface BgmConfig {
  file?: string
  volume_db?: number
  duck?: boolean
  duck_ratio?: number
  duck_attack?: number
  duck_release?: number
}

export interface SystemConfig {
  bgm?: BgmConfig
  tts?: { speaker_wav?: string }
  video?: { audio_source?: 'raw' | 'faded' }
  paths?: {
    library_subdir?: string
    panels_subdir?: string
    audio_subdir?: string
    processed_subdir?: string
  }
  ports?: Record<string, number>
  [key: string]: unknown
}

export interface ConfigPair {
  config: AppConfig
  systemConfig: SystemConfig
}

export interface ChapterStatus {
  dl: number
  panels: number
  narr: boolean
  narrItems: number
  audio: number
  video: boolean
  dir: string
}

export interface LibraryEntry {
  path: string
  label: string
  chapterCount: number
  selected: boolean
}

export interface CliCommand {
  command: string
  args: string[]
}

export type DeleteWhat = 'download' | 'panels' | 'audio' | 'video' | 'all'
export type PurgeKind = 'ai-zip' | 'narration' | 'audio' | 'video'

export interface JobProgress {
  value: number
  total: number
  label: string
}

export interface DoctorTool {
  title: string
  installed: boolean
  path: string | null
  configured: boolean
  needs_gpu: boolean
  notes: string
  /** Only populated when doctor was run with --check-updates; null = not checked or not applicable. */
  update_available: boolean | null
}

/** Shape of `mangaeasy doctor --json`'s output (`mangaeasy/tools/install.py`'s `doctor()`). */
export interface DoctorStatus {
  tools_home: string
  git_lfs: boolean
  gpu: boolean
  cuda: boolean
  cuda_device: string | null
  mps: boolean
  gpu_backend: 'cuda' | 'mps' | 'cpu'
  whisper: boolean
  executables: Record<string, string | null>
  tools: Record<string, DoctorTool>
}

/** Shape of the `app:get-info` IPC reply — version + where this install keeps its data. */
export interface AppInfo {
  version: string
  dataRoot: string
  home: string
  logsDir: string
  platform: string
  packaged: boolean
  /** argv prefix that runs this install's `mangaeasy` CLI (backend exe when packaged). */
  cli: string[]
}

/** Shape of the `app:check-updates` IPC reply. */
export interface UpdateCheck {
  current: string
  latest: string | null
  updateAvailable: boolean
  url: string
}

/** One archived audio run (`mangaeasy/video_pipeline/audio_takes.py`'s `list_runs()`). */
export interface AudioTakeRun {
  run: string
  archived_at: string
  items: Record<string, number>
  total_files: number
}

/** Shape of `mangaeasy audio-takes-list --json`'s output. */
export interface AudioTakesStatus {
  active: { items: Record<string, number>; total_files: number }
  runs: AudioTakeRun[]
}
