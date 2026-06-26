/**
 * IPC handler registration. Mirrors the Python NiceGUI app's command
 * contracts (`_run_job`/`_run_chain`, `_read_config`/`_write_config`,
 * `_pick_dir`/`_pick_file`, `_open_folder_in_manager`) but routed through
 * Electron's own dialog/shell APIs and `jobs.ts`'s PTY-backed job runner.
 */
import { dialog, ipcMain, shell, type IpcMainInvokeEvent } from 'electron'
import path from 'path'
import {
  chapterStatus,
  deleteChapter,
  ensureNarrationForOcr,
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
import { appRoot, buildCli } from './paths'
import { ProgressParser } from './progress'
import { getProjectRoot, setProjectRoot } from './settings'
import type { AppConfig, CliCommand, DeleteWhat, PurgeKind, SystemConfig } from '../shared/types'

const AUDIO_EXTENSIONS = ['wav', 'mp3', 'm4a', 'flac', 'aac']

function send(event: IpcMainInvokeEvent, channel: string, payload: unknown): void {
  event.sender.send(channel, payload)
}

/** Runs one CLI command, streaming terminal output + parsed progress to the
 * renderer. Shared by `run-cli` and each step of `run-chain`. */
async function runCliStreamed(event: IpcMainInvokeEvent, command: string, args: string[]): Promise<number> {
  const parser = new ProgressParser()
  const cmd = buildCli(command, args)
  return runBlocking(cmd, getProjectRoot(), (chunk) => {
    send(event, 'terminal:data', chunk)
    for (const update of parser.feed(chunk)) {
      send(event, 'job:progress', update)
    }
  })
}

export function registerIpcHandlers(): void {
  // ---- Job execution -------------------------------------------------------

  ipcMain.handle('run-cli', (event, command: string, args: string[] = []) => runCliStreamed(event, command, args))

  ipcMain.handle('run-chain', async (event, commands: CliCommand[]) => {
    for (let i = 0; i < commands.length; i++) {
      const { command, args } = commands[i]
      send(event, 'job:progress', { value: i, total: commands.length, label: `Step ${i + 1}/${commands.length}: ${command}` })
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
    return JSON.parse(stdout)
  })

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
  ipcMain.handle('chapter:ensure-narration', (_event, chapter: number) =>
    ensureNarrationForOcr(getProjectRoot(), chapter)
  )

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
  // `_batch_faded_audio_root` — relative outDir resolves against the project
  // root, not whatever cwd the renderer thinks it's in.
  ipcMain.handle('batch:resolve-paths', (_event, outDir: string, mangaPath: string) => {
    const outputRoot = path.isAbsolute(outDir) ? outDir : path.join(getProjectRoot(), outDir)
    const mangaName = path.basename(mangaPath)
    const projectOutputDir = path.join(outputRoot, mangaName)
    const audioRoot = path.join(projectOutputDir, 'audio')
    const fadedAudioRoot = `${audioRoot}_faded`
    return { outputRoot, projectOutputDir, audioRoot, fadedAudioRoot }
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
      if (!resolved) reject(new Error(`${name} did not report a URL within 15s.`))
    }, 15000)
  })
}
