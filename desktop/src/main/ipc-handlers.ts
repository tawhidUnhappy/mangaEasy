/**
 * IPC handler registration. Mirrors the Python NiceGUI app's command
 * contracts (`_run_job`/`_run_chain`, `_read_config`/`_write_config`,
 * `_pick_dir`/`_pick_file`, `_open_folder_in_manager`) but routed through
 * Electron's own dialog/shell APIs and `jobs.ts`'s PTY-backed job runner.
 */
import { app, dialog, ipcMain, shell, type IpcMainInvokeEvent } from 'electron'
import { existsSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { logsDir } from './log'
import {
  chapterStatus,
  deleteChapter,
  libraryDir,
  libraryMangaEntries,
  purge,
  readConfig,
  writeConfig
} from './config'
import {
  isJobRunning,
  isNamedGuiRunning,
  runBlocking,
  runCapture,
  setTerminalSize,
  spawnDetached,
  stopNamedGui,
  terminateCurrentJob
} from './jobs'
import { appRoot, buildCli, mangaeasyCommand, mangaeasyHome } from './paths'
import { ProgressParser } from './progress'
import {
  getProjectRoot,
  getUpdateLastCheckedMs,
  setProjectRoot,
  setUpdateLastCheckedMs
} from './settings'
import type { AppConfig, CliCommand, DeleteWhat, PurgeKind, SystemConfig } from '../shared/types'

const AUDIO_EXTENSIONS = ['wav', 'mp3', 'm4a', 'flac', 'aac']

function send(event: IpcMainInvokeEvent, channel: string, payload: unknown): void {
  event.sender.send(channel, payload)
}

/** Parses the JSON object out of a CLI command's stdout, tolerating stray
 * warnings/log lines printed before or after it — a bare JSON.parse(stdout)
 * broke whenever anything else wrote to stdout. */
function parseJsonOutput<T>(stdout: string): T {
  const start = stdout.indexOf('{')
  const end = stdout.lastIndexOf('}')
  if (start === -1 || end <= start) {
    throw new Error(`expected JSON in command output, got: ${stdout.slice(0, 200)}`)
  }
  return JSON.parse(stdout.slice(start, end + 1)) as T
}

/** "1.2.10" vs "1.2.9" — numeric per-segment compare; non-numeric tags never
 * count as newer. */
function isNewerVersion(latest: string, current: string): boolean {
  const a = latest.split('.').map((s) => parseInt(s, 10))
  const b = current.split('.').map((s) => parseInt(s, 10))
  if (a.some(Number.isNaN) || b.some(Number.isNaN)) return false
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return false
}

const RELEASES_URL = 'https://github.com/tawhidUnhappy/mangaEasy/releases/latest'
const UPDATE_CHECK_MIN_INTERVAL_MS = 20 * 60 * 60 * 1000

/** Runs one CLI command, streaming terminal output + parsed progress to the
 * renderer. Shared by `run-cli` and each step of `run-chain`. */
async function runCliStreamed(
  event: IpcMainInvokeEvent,
  command: string,
  args: string[]
): Promise<number> {
  const parser = new ProgressParser()
  const cmd = buildCli(command, args)
  return runBlocking(cmd, getProjectRoot(), (chunk) => {
    send(event, 'terminal:data', chunk)
    for (const update of parser.feed(chunk)) {
      send(event, 'job:progress', update)
    }
  })
}

// Picks the newest plain join output (<name>_full_<timestamp>.mp4) in a
// long-video directory, skipping background-music mixes ("_bgm_" in the
// name) -- mirrors find_latest_long_video() in
// mangaeasy/video_pipeline/common.py, since joining no longer writes one
// fixed filename: every join keeps its own timestamped file so re-running
// it never overwrites a previous one.
function findLatestLongVideo(longVideoDir: string, mangaName: string): string | null {
  if (!existsSync(longVideoDir)) return null
  let best: { path: string; mtimeMs: number } | null = null
  for (const entry of readdirSync(longVideoDir, { withFileTypes: true })) {
    if (!entry.isFile()) continue
    const lower = entry.name.toLowerCase()
    if (
      !lower.endsWith('.mp4') ||
      !lower.startsWith(`${mangaName.toLowerCase()}_full`) ||
      lower.includes('_bgm_')
    ) {
      continue
    }
    const full = path.join(longVideoDir, entry.name)
    const mtimeMs = statSync(full).mtimeMs
    if (!best || mtimeMs > best.mtimeMs) best = { path: full, mtimeMs }
  }
  return best?.path ?? null
}

export function registerIpcHandlers(): void {
  // ---- Job execution -------------------------------------------------------

  ipcMain.handle('run-cli', (event, command: string, args: string[] = []) =>
    runCliStreamed(event, command, args)
  )

  ipcMain.handle('run-chain', async (event, commands: CliCommand[]) => {
    for (let i = 0; i < commands.length; i++) {
      const { command, args } = commands[i]
      send(event, 'job:progress', {
        value: i,
        total: commands.length,
        label: `Step ${i + 1}/${commands.length}: ${command}`
      })
      const code = await runCliStreamed(event, command, args)
      if (code !== 0) return code
    }
    send(event, 'job:progress', { value: commands.length, total: commands.length, label: 'Done' })
    return 0
  })

  ipcMain.handle('terminate-job', () => terminateCurrentJob())
  ipcMain.handle('is-job-running', () => isJobRunning())
  ipcMain.on('terminal:resize', (_event, cols: number, rows: number) => setTerminalSize(cols, rows))

  ipcMain.handle('get-doctor-status', async (_event, checkUpdates = false) => {
    const args = checkUpdates ? ['--json', '--check-updates'] : ['--json']
    const stdout = await runCapture(buildCli('doctor', args), appRoot())
    return parseJsonOutput(stdout)
  })

  ipcMain.handle('list-audio-takes', async (_event, projectRoot: string, audioRoot: string) => {
    const stdout = await runCapture(
      buildCli('audio-takes-list', [
        '--project-root',
        projectRoot,
        '--audio-root',
        audioRoot,
        '--json'
      ]),
      appRoot()
    )
    return parseJsonOutput(stdout)
  })

  // ---- YouTube --------------------------------------------------------------

  ipcMain.handle('youtube:status', async () => {
    const stdout = await runCapture(buildCli('youtube-status', ['--json']), appRoot())
    return parseJsonOutput(stdout)
  })

  // Live check: refreshes the token and queries the channel — proves the
  // connection actually works, not just that the files exist. runCapture
  // rejects on non-zero exit, but --json mode always exits 0 with the
  // verified/verify_error fields carrying the outcome.
  ipcMain.handle('youtube:verify', async () => {
    const stdout = await runCapture(buildCli('youtube-status', ['--json', '--verify']), appRoot())
    return parseJsonOutput(stdout)
  })

  // ---- App info + update check ---------------------------------------------

  ipcMain.handle('app:get-info', () => ({
    version: app.getVersion(),
    dataRoot: appRoot(),
    home: mangaeasyHome(),
    logsDir: logsDir(),
    platform: process.platform,
    packaged: app.isPackaged,
    // The exact argv prefix that runs this install's CLI — shown in About so
    // scripts/AI agents can drive the same engine (with MANGAEASY_ROOT set
    // to dataRoot they share this GUI's projects and tools).
    cli: mangaeasyCommand()
  }))

  // Checks the GitHub Releases page for a newer version. `force` bypasses
  // the ~daily throttle (used by the explicit button; the automatic launch
  // check respects it). Offline or rate-limited → quietly "no update".
  ipcMain.handle('app:check-updates', async (_event, force = false) => {
    const current = app.getVersion()
    const none = {
      current,
      latest: null as string | null,
      updateAvailable: false,
      url: RELEASES_URL
    }
    if (!force && Date.now() - getUpdateLastCheckedMs() < UPDATE_CHECK_MIN_INTERVAL_MS) return none
    try {
      const res = await fetch(
        'https://api.github.com/repos/tawhidUnhappy/mangaEasy/releases/latest',
        {
          headers: { accept: 'application/vnd.github+json' }
        }
      )
      if (!res.ok) return none
      const data = (await res.json()) as { tag_name?: string; html_url?: string }
      setUpdateLastCheckedMs(Date.now())
      const latest = String(data.tag_name ?? '').replace(/^v/, '')
      return {
        current,
        latest,
        updateAvailable: isNewerVersion(latest, current),
        url: data.html_url ?? RELEASES_URL
      }
    } catch {
      return none
    }
  })

  ipcMain.handle(
    'restore-audio-take',
    async (_event, projectRoot: string, audioRoot: string, run: string, items?: string[]) => {
      const args = ['--project-root', projectRoot, '--audio-root', audioRoot, '--run', run]
      if (items?.length) args.push('--items', ...items)
      const stdout = await runCapture(buildCli('audio-takes-restore', args), appRoot())
      return stdout
    }
  )

  // ---- Project root + config -------------------------------------------------

  ipcMain.handle('get-project-root', () => getProjectRoot())
  ipcMain.handle('set-project-root', (_event, projectRoot: string) => setProjectRoot(projectRoot))

  ipcMain.handle('get-config', () => readConfig(getProjectRoot()))
  ipcMain.handle('set-config', (_event, config?: AppConfig, systemConfig?: SystemConfig) =>
    writeConfig(getProjectRoot(), config, systemConfig)
  )

  // ---- Library / chapter status --------------------------------------------

  ipcMain.handle('library:list', () => libraryMangaEntries(getProjectRoot()))
  ipcMain.handle('chapter:status', (_event, name: string, chapter: number) =>
    chapterStatus(getProjectRoot(), name, chapter)
  )
  ipcMain.handle('chapter:delete', (_event, chapter: number, what: DeleteWhat) =>
    deleteChapter(getProjectRoot(), chapter, what)
  )
  ipcMain.handle('chapter:purge', (_event, kind: PurgeKind) => purge(getProjectRoot(), kind))
  ipcMain.handle('chapter:export-ai-zip', async (event, chapter: number) => {
    const { config, systemConfig } = readConfig(getProjectRoot())
    const name = String(config.download?.name ?? '')
    if (!name) return { ok: false, reason: 'no manga name configured' }
    const lib = libraryDir(getProjectRoot(), systemConfig)
    const chNum = String(chapter).padStart(2, '0')
    const chDir = path.join(lib, name, chNum)
    const panelsSub = systemConfig.paths?.panels_subdir ?? 'panels'
    const panelsDir = path.join(chDir, panelsSub)
    const safe = name.replace(/ /g, '_')
    const out = path.join(chDir, `${safe}_ch${chNum}_panels_for_ai.zip`)
    const code = await runCliStreamed(event, 'ai-zip', ['--panels-dir', panelsDir, '--output', out])
    return { ok: code === 0, output: out }
  })

  // ---- Batch tab path resolution ---------------------------------------------

  // Mirrors `_batch_output_root`/`_batch_project_output_dir`/`_batch_audio_root`/
  // `_batch_faded_audio_root`. A relative outDir (the default, "output")
  // resolves *inside the manga's own library folder* -- so everything
  // generated for a project (audio, rendered videos, the final long video)
  // lives under library/<manga>/output/ instead of a separate top-level
  // output/ tree, keeping each manga's data in one place. An absolute outDir
  // (the user explicitly browsed to a custom shared location) keeps the old
  // behavior of nesting per-manga under that shared root, since it may hold
  // output for more than one project.
  ipcMain.handle('batch:resolve-paths', (_event, outDir: string, mangaPath: string) => {
    const mangaName = path.basename(mangaPath)
    const outputRoot = path.isAbsolute(outDir) ? outDir : path.join(mangaPath, outDir)
    const projectOutputDir = path.isAbsolute(outDir) ? path.join(outputRoot, mangaName) : outputRoot
    const audioRoot = path.join(projectOutputDir, 'audio')
    const fadedAudioRoot = `${audioRoot}_faded`
    // Long videos always live at <output-root>/<name>/ regardless of how
    // outputRoot/projectOutputDir above were computed, because the CLI
    // itself appends /<name>/ to whatever --output-root it's given
    // (make_long_video.py / add_long_video_bgm.py). projectOutputDir is
    // NOT the same directory in the common case (relative outDir).
    const longVideoDir = path.join(outputRoot, mangaName)
    const latestLongVideoPath = findLatestLongVideo(longVideoDir, mangaName)
    return {
      outputRoot,
      projectOutputDir,
      audioRoot,
      fadedAudioRoot,
      longVideoDir,
      latestLongVideoPath
    }
  })

  // Lists .mp4 files a "pick which video" control can offer: every join's
  // own timestamped output, any background-music mixes, plus anything
  // archived under old/run_NNNN/ (the same archive-before-overwrite
  // folders audio takes use) -- so re-mixing music onto an older take
  // doesn't require digging through Explorer first.
  ipcMain.handle('batch:list-videos', (_event, longVideoDir: string) => {
    const results: { path: string; label: string; mtimeMs: number }[] = []
    if (!longVideoDir || !existsSync(longVideoDir)) return results

    for (const entry of readdirSync(longVideoDir, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.toLowerCase().endsWith('.mp4')) {
        const full = path.join(longVideoDir, entry.name)
        results.push({ path: full, label: entry.name, mtimeMs: statSync(full).mtimeMs })
      }
    }
    const oldDir = path.join(longVideoDir, 'old')
    if (existsSync(oldDir)) {
      for (const runEntry of readdirSync(oldDir, { withFileTypes: true })) {
        if (!runEntry.isDirectory()) continue
        const runDir = path.join(oldDir, runEntry.name)
        for (const fileEntry of readdirSync(runDir, { withFileTypes: true })) {
          if (fileEntry.isFile() && fileEntry.name.toLowerCase().endsWith('.mp4')) {
            const full = path.join(runDir, fileEntry.name)
            results.push({
              path: full,
              label: `${runEntry.name}/${fileEntry.name}`,
              mtimeMs: statSync(full).mtimeMs
            })
          }
        }
      }
    }
    results.sort((a, b) => b.mtimeMs - a.mtimeMs)
    return results
  })

  // ---- Editors (Flask sub-apps embedded as <webview>) -----------------------

  ipcMain.handle('editor:launch', (event, name: string) => launchEditor(event, name))
  ipcMain.handle('editor:stop', (_event, name: string) => stopNamedGui(name))
  ipcMain.handle('editor:is-running', (_event, name: string) => isNamedGuiRunning(name))

  // ---- Native dialogs / shell ------------------------------------------------

  ipcMain.handle('dialog:pick-dir', async () => {
    const result = await dialog.showOpenDialog({ properties: ['openDirectory'] })
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })

  ipcMain.handle('dialog:pick-file', async (_event, extensions?: string[]) => {
    const filters = extensions?.length ? [{ name: 'Files', extensions }] : undefined
    const result = await dialog.showOpenDialog({ properties: ['openFile'], filters })
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })

  ipcMain.handle('dialog:pick-audio-file', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openFile'],
      filters: [{ name: 'Audio', extensions: AUDIO_EXTENSIONS }]
    })
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })

  ipcMain.handle('shell:open-folder', (_event, targetPath: string) => shell.openPath(targetPath))
}

const EDITOR_URL_RE = /MANGAEASY_OPEN_URL:(\S+)/

// First-launch of the frozen backend can be slow (one-time OS/antivirus
// scan of the PyInstaller payload), so the URL wait is generous. On timeout
// the spawned process is killed rather than left running headless.
const EDITOR_URL_TIMEOUT_MS = 60_000

function launchEditor(event: IpcMainInvokeEvent, name: string): Promise<string> {
  return new Promise((resolve, reject) => {
    if (isNamedGuiRunning(name)) {
      reject(new Error(`${name} is already running — use editor:is-running to get its URL.`))
      return
    }
    let resolved = false
    const cmd = buildCli(name, [])
    spawnDetached(
      cmd,
      getProjectRoot(),
      (chunk) => {
        send(event, 'terminal:data', chunk)
        if (!resolved) {
          const match = EDITOR_URL_RE.exec(chunk)
          if (match) {
            resolved = true
            resolve(match[1])
          }
        }
      },
      { MANGAEASY_APP_MODE: '1' },
      name
    )
    setTimeout(() => {
      if (!resolved) {
        resolved = true
        void stopNamedGui(name)
        reject(
          new Error(
            `${name} did not start within ${EDITOR_URL_TIMEOUT_MS / 1000}s — ` +
              `see the terminal pane for its output, then try again.`
          )
        )
      }
    }, EDITOR_URL_TIMEOUT_MS)
  })
}
