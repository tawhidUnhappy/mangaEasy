import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type {
  AppConfig,
  AppInfo,
  AudioTakesStatus,
  ChapterStatus,
  CliCommand,
  ConfigPair,
  DeleteWhat,
  DoctorStatus,
  JobProgress,
  LibraryEntry,
  PurgeKind,
  SystemConfig,
  UpdateCheck
} from '../shared/types'

const api = {
  // Job execution
  runCli: (command: string, args: string[] = []): Promise<number> =>
    ipcRenderer.invoke('run-cli', command, args),
  runChain: (commands: CliCommand[]): Promise<number> => ipcRenderer.invoke('run-chain', commands),
  terminateJob: (): Promise<void> => ipcRenderer.invoke('terminate-job'),
  isJobRunning: (): Promise<boolean> => ipcRenderer.invoke('is-job-running'),
  onTerminalData: (callback: (chunk: string) => void): (() => void) => {
    const listener = (_e: Electron.IpcRendererEvent, chunk: string): void => callback(chunk)
    ipcRenderer.on('terminal:data', listener)
    return () => ipcRenderer.removeListener('terminal:data', listener)
  },
  onJobProgress: (callback: (progress: JobProgress) => void): (() => void) => {
    const listener = (_e: Electron.IpcRendererEvent, progress: JobProgress): void =>
      callback(progress)
    ipcRenderer.on('job:progress', listener)
    return () => ipcRenderer.removeListener('job:progress', listener)
  },
  resizeTerminal: (cols: number, rows: number): void =>
    ipcRenderer.send('terminal:resize', cols, rows),

  getDoctorStatus: (checkUpdates = false): Promise<DoctorStatus> =>
    ipcRenderer.invoke('get-doctor-status', checkUpdates),

  // App info + update check
  getAppInfo: (): Promise<AppInfo> => ipcRenderer.invoke('app:get-info'),
  checkAppUpdate: (force = false): Promise<UpdateCheck> =>
    ipcRenderer.invoke('app:check-updates', force),

  listAudioTakes: (projectRoot: string, audioRoot: string): Promise<AudioTakesStatus> =>
    ipcRenderer.invoke('list-audio-takes', projectRoot, audioRoot),
  restoreAudioTake: (
    projectRoot: string,
    audioRoot: string,
    run: string,
    items?: string[]
  ): Promise<string> =>
    ipcRenderer.invoke('restore-audio-take', projectRoot, audioRoot, run, items),

  // Project root + config
  getProjectRoot: (): Promise<string> => ipcRenderer.invoke('get-project-root'),
  setProjectRoot: (projectRoot: string): Promise<void> =>
    ipcRenderer.invoke('set-project-root', projectRoot),
  getConfig: (): Promise<ConfigPair> => ipcRenderer.invoke('get-config'),
  setConfig: (config?: AppConfig, systemConfig?: SystemConfig): Promise<void> =>
    ipcRenderer.invoke('set-config', config, systemConfig),

  // Library / chapter status
  listLibrary: (): Promise<{ library: string; entries: LibraryEntry[] }> =>
    ipcRenderer.invoke('library:list'),
  getChapterStatus: (name: string, chapter: number): Promise<ChapterStatus> =>
    ipcRenderer.invoke('chapter:status', name, chapter),
  deleteChapter: (chapter: number, what: DeleteWhat): Promise<string[]> =>
    ipcRenderer.invoke('chapter:delete', chapter, what),
  purgeChapters: (kind: PurgeKind): Promise<number> => ipcRenderer.invoke('chapter:purge', kind),
  ensureNarrationForOcr: (chapter: number): Promise<{ path: string | null; reason?: string }> =>
    ipcRenderer.invoke('chapter:ensure-narration', chapter),
  exportAiZip: (chapter: number): Promise<{ ok: boolean; output?: string; reason?: string }> =>
    ipcRenderer.invoke('chapter:export-ai-zip', chapter),
  resolveBatchPaths: (
    outDir: string,
    mangaPath: string
  ): Promise<{
    outputRoot: string
    projectOutputDir: string
    audioRoot: string
    fadedAudioRoot: string
    longVideoDir: string
    latestLongVideoPath: string | null
  }> => ipcRenderer.invoke('batch:resolve-paths', outDir, mangaPath),
  listBatchVideos: (
    longVideoDir: string
  ): Promise<{ path: string; label: string; mtimeMs: number }[]> =>
    ipcRenderer.invoke('batch:list-videos', longVideoDir),

  // Editors
  launchEditor: (name: string): Promise<string> => ipcRenderer.invoke('editor:launch', name),
  stopEditor: (name: string): Promise<void> => ipcRenderer.invoke('editor:stop', name),
  isEditorRunning: (name: string): Promise<boolean> =>
    ipcRenderer.invoke('editor:is-running', name),

  // Native dialogs / shell
  pickDir: (): Promise<string | null> => ipcRenderer.invoke('dialog:pick-dir'),
  pickFile: (extensions?: string[]): Promise<string | null> =>
    ipcRenderer.invoke('dialog:pick-file', extensions),
  pickAudioFile: (): Promise<string | null> => ipcRenderer.invoke('dialog:pick-audio-file'),
  openFolder: (targetPath: string): Promise<string> =>
    ipcRenderer.invoke('shell:open-folder', targetPath)
}

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}

export type Api = typeof api
