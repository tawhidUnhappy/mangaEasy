import { app, dialog, shell, BrowserWindow } from 'electron'
import { mkdirSync } from 'fs'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { registerIpcHandlers } from './ipc-handlers'
import { terminateAllJobs } from './jobs'
import { logLine } from './log'
import { appRoot, mangaeasyHome } from './paths'
import { getWindowBounds, setWindowBounds } from './settings'

// Every spawned mangaeasy backend process must agree with this Electron
// process on where "this install" lives — set once on our own process.env so
// every child (PTY-spawned via `trackedSpawn`) inherits it automatically,
// without each call site having to thread it through by hand. The Python
// side's `app_root()`/`mangaeasy_home()` read these same two names.
process.env.MANGAEASY_ROOT = appRoot()
process.env.MANGAEASY_HOME = mangaeasyHome()

// Keep Electron's own writes (Chromium caches, GPU cache, local storage)
// inside our one data folder too — otherwise the "delete the data folder and
// nothing is left behind" promise is broken by Electron itself writing to
// the OS-default appData location.
if (app.isPackaged) {
  const electronData = join(mangaeasyHome(), 'electron')
  mkdirSync(electronData, { recursive: true })
  app.setPath('userData', electronData)
}

process.on('uncaughtException', (err) => {
  logLine('error', `uncaughtException: ${err.stack ?? err.message}`)
  try {
    dialog.showErrorBox(
      'mangaEasy — unexpected error',
      `${err.message}\n\nDetails were written to the log file (Setup tab → Open logs folder).`
    )
  } catch {
    // dialog may not be available before app is ready — the log has it.
  }
})

function createWindow(): void {
  const saved = getWindowBounds()
  const mainWindow = new BrowserWindow({
    width: saved?.width ?? 1360,
    height: saved?.height ?? 900,
    x: saved?.x,
    y: saved?.y,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#1e1e1e',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      // The Editor view embeds the Flask sub-editors via <webview>, which
      // requires webviewTag — everything still goes through contextBridge,
      // not nodeIntegration, in the main window's own renderer.
      webviewTag: true,
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    if (saved?.maximized) mainWindow.maximize()
    mainWindow.show()
  })

  // Persist window size/position on close so the next launch opens where
  // the user left it (bounds saved un-maximized so restoring works).
  mainWindow.on('close', () => {
    const maximized = mainWindow.isMaximized()
    const bounds = maximized ? mainWindow.getNormalBounds() : mainWindow.getBounds()
    setWindowBounds({ ...bounds, maximized })
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId('com.mangaeasy.app')
  logLine('info', `mangaEasy ${app.getVersion()} starting (root=${appRoot()})`)

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  registerIpcHandlers()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// Graceful-quit cleanup — kills the current job and any detached editor
// processes so nothing keeps a Flask dev server or a GPU-bound job alive
// after the window closes.
app.on('before-quit', () => {
  terminateAllJobs()
})
