import { app, shell, BrowserWindow } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { registerIpcHandlers } from './ipc-handlers'
import { terminateAllJobs } from './jobs'
import { appRoot, mangaeasyHome } from './paths'

// Every spawned mangaeasy backend process must agree with this Electron
// process on where "this install" lives — set once on our own process.env so
// every child (PTY-spawned via `trackedSpawn`) inherits it automatically,
// without each call site having to thread it through by hand. The Python
// side's `app_root()`/`mangaeasy_home()` read these same two names.
process.env.MANGAEASY_ROOT = appRoot()
process.env.MANGAEASY_HOME = mangaeasyHome()

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
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

  mainWindow.on('ready-to-show', () => mainWindow.show())

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
