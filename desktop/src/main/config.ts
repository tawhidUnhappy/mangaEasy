/**
 * config.json / config.system.json read-write + on-disk chapter/library
 * helpers — ports of the inline Python helpers in
 * `mangaeasy/web/nicegui_app.py` (`_ch_status`, `_delete_chapter`, `_purge`,
 * `_library_manga_entries`) and
 * `mangaeasy/web/app/api_workflow.py`'s `_library_dir`. Reading these
 * directly in Node avoids a Python round-trip for simple filesystem checks.
 */
import {
  existsSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  unlinkSync,
  writeFileSync
} from 'fs'
import path from 'path'
import type {
  AppConfig,
  ChapterStatus,
  DeleteWhat,
  LibraryEntry,
  PurgeKind,
  SystemConfig
} from '../shared/types'

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif'])
const AUDIO_EXTS = new Set(['.wav', '.mp3', '.m4a'])

function readJsonFile<T>(filePath: string): T | Record<string, never> {
  if (!existsSync(filePath)) return {}
  try {
    return JSON.parse(readFileSync(filePath, 'utf-8')) as T
  } catch {
    return {}
  }
}

export function readConfig(projectRoot: string): { config: AppConfig; systemConfig: SystemConfig } {
  return {
    config: readJsonFile<AppConfig>(path.join(projectRoot, 'config.json')),
    systemConfig: readJsonFile<SystemConfig>(path.join(projectRoot, 'config.system.json'))
  }
}

export function writeConfig(
  projectRoot: string,
  config?: AppConfig,
  systemConfig?: SystemConfig
): void {
  if (config) {
    writeFileSync(
      path.join(projectRoot, 'config.json'),
      JSON.stringify(config, null, 2) + '\n',
      'utf-8'
    )
  }
  if (systemConfig) {
    writeFileSync(
      path.join(projectRoot, 'config.system.json'),
      JSON.stringify(systemConfig, null, 2) + '\n',
      'utf-8'
    )
  }
}

/** Matches `api_workflow._library_dir`: configurable, with legacy-folder fallback. */
export function libraryDir(projectRoot: string, systemConfig: SystemConfig): string {
  const configured = systemConfig.paths?.library_subdir
  if (configured) return path.join(projectRoot, configured)
  for (const candidate of ['mangas', 'library', 'manga']) {
    const full = path.join(projectRoot, candidate)
    if (existsSync(full) && statSync(full).isDirectory()) return full
  }
  return path.join(projectRoot, 'mangas')
}

function countFiles(folder: string, exts: Set<string>): number {
  if (!existsSync(folder) || !statSync(folder).isDirectory()) return 0
  return readdirSync(folder).filter((name) => exts.has(path.extname(name).toLowerCase())).length
}

export function chapterStatus(projectRoot: string, name: string, chapter: number): ChapterStatus {
  const { systemConfig } = readConfig(projectRoot)
  const lib = libraryDir(projectRoot, systemConfig)
  const chNum = String(chapter).padStart(2, '0')
  const dir = path.join(lib, name, chNum)
  const narrPath = path.join(dir, `narration_${chNum}.json`)
  let narrItems = 0
  if (existsSync(narrPath)) {
    try {
      const data = JSON.parse(readFileSync(narrPath, 'utf-8'))
      narrItems = Array.isArray(data) ? data.length : 0
    } catch {
      narrItems = 0
    }
  }
  const videoA = path.join(dir, `${chNum}_${name}.mp4`)
  const videoB = path.join(dir, `${chNum}_${name}_with_bgm.mp4`)
  return {
    dl: countFiles(path.join(dir, 'download'), IMAGE_EXTS),
    panels: countFiles(path.join(dir, 'panels'), IMAGE_EXTS),
    narr: existsSync(narrPath),
    narrItems,
    audio: countFiles(path.join(dir, 'audio'), AUDIO_EXTS),
    video: existsSync(videoA) || existsSync(videoB),
    dir
  }
}

export function libraryMangaEntries(projectRoot: string): {
  library: string
  entries: LibraryEntry[]
} {
  const { config, systemConfig } = readConfig(projectRoot)
  const currentName = String(config.download?.name ?? '')
  const library = libraryDir(projectRoot, systemConfig)
  const entries: LibraryEntry[] = []
  if (existsSync(library) && statSync(library).isDirectory()) {
    const folders = readdirSync(library, { withFileTypes: true })
      .filter((d) => d.isDirectory() && !d.name.startsWith('.'))
      .map((d) => d.name)
      .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))
    for (const folderName of folders) {
      const folderPath = path.join(library, folderName)
      const chapterCount = readdirSync(folderPath, { withFileTypes: true }).filter(
        (d) => d.isDirectory() && /^\d+$/.test(d.name)
      ).length
      entries.push({
        path: folderPath,
        label: `${folderName} (${chapterCount} chapter${chapterCount === 1 ? '' : 's'})`,
        chapterCount,
        selected: folderName === currentName
      })
    }
  }
  return { library, entries }
}

export function deleteChapter(projectRoot: string, chapter: number, what: DeleteWhat): string[] {
  const { config, systemConfig } = readConfig(projectRoot)
  const name = String(config.download?.name ?? '')
  if (!name) return []
  const lib = libraryDir(projectRoot, systemConfig)
  const chNum = String(chapter).padStart(2, '0')
  const chDir = path.join(lib, name, chNum)
  const panelsSub = systemConfig.paths?.panels_subdir ?? 'panels'
  const audioSub = systemConfig.paths?.audio_subdir ?? 'audio'
  const removed: string[] = []

  const rmTree = (rel: string): void => {
    const full = path.join(chDir, rel)
    if (existsSync(full) && statSync(full).isDirectory()) {
      rmSync(full, { recursive: true, force: true })
      removed.push(`${rel}/`)
    }
  }
  const rmGlob = (suffix: string): void => {
    if (!existsSync(chDir)) return
    const files = readdirSync(chDir).filter((f) => f.endsWith(suffix))
    for (const f of files) unlinkSync(path.join(chDir, f))
    if (files.length) removed.push(`${files.length}x*${suffix}`)
  }

  if (what === 'download' || what === 'all') rmTree('download')
  if (what === 'panels' || what === 'all') rmTree(panelsSub)
  if (what === 'audio' || what === 'all') rmTree(audioSub)
  if (what === 'video' || what === 'all') rmGlob('.mp4')
  if (what === 'all') rmTree('work')
  return removed
}

export function purge(projectRoot: string, kind: PurgeKind): number {
  const { config, systemConfig } = readConfig(projectRoot)
  const name = String(config.download?.name ?? '')
  if (!name) return 0
  const lib = libraryDir(projectRoot, systemConfig)
  const mangaDir = path.join(lib, name)
  if (!existsSync(mangaDir) || !statSync(mangaDir).isDirectory()) return 0
  const audioSub = systemConfig.paths?.audio_subdir ?? 'audio'
  let removed = 0
  const chapterDirs = readdirSync(mangaDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^\d+$/.test(d.name))
    .map((d) => path.join(mangaDir, d.name))
    .sort()

  for (const chDir of chapterDirs) {
    if (kind === 'ai-zip') {
      for (const f of readdirSync(chDir).filter((f) => f.endsWith('_panels_for_ai.zip'))) {
        unlinkSync(path.join(chDir, f))
        removed++
      }
    } else if (kind === 'narration') {
      for (const f of readdirSync(chDir).filter(
        (f) => f.startsWith('narration_') && f.endsWith('.json')
      )) {
        unlinkSync(path.join(chDir, f))
        removed++
      }
    } else if (kind === 'audio') {
      const ad = path.join(chDir, audioSub)
      if (existsSync(ad) && statSync(ad).isDirectory()) {
        rmSync(ad, { recursive: true, force: true })
        removed++
      }
    } else if (kind === 'video') {
      for (const f of readdirSync(chDir).filter((f) => f.endsWith('.mp4'))) {
        unlinkSync(path.join(chDir, f))
        removed++
      }
    }
  }
  return removed
}
