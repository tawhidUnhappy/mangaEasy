#!/usr/bin/env node
// Builds the PyInstaller mangaeasy backend (packaging/mangaeasy.spec) from
// the repo root and copies it into desktop/resources/backend/, where
// electron-builder.yml's extraResources picks it up. Run before
// `electron-builder` — `npm run build:win/:mac/:linux` already does this.
import { execFileSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const desktopDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(desktopDir, '..')
const distDir = path.join(repoRoot, 'dist')
const backendOut = path.join(desktopDir, 'resources', 'backend')

console.log('[bundle-backend] running PyInstaller…')
execFileSync(
  'uv',
  [
    'run',
    'pyinstaller',
    'packaging/mangaeasy.spec',
    '--distpath',
    'dist',
    '--workpath',
    'build-tmp',
    '--noconfirm',
    '--clean'
  ],
  { cwd: repoRoot, stdio: 'inherit' }
)

// PyInstaller's spec produces dist/mangaEasy/ everywhere, plus
// dist/mangaEasy.app/ on macOS — prefer the .app bundle's Contents/MacOS
// payload when present so the backend folder layout is the same shape
// (a runnable "mangaeasy" entry point alongside its _internal/ payload).
const macApp = path.join(distDir, 'mangaEasy.app', 'Contents', 'MacOS')
const plainDir = path.join(distDir, 'mangaEasy')
const sourceDir = existsSync(macApp) ? macApp : plainDir

if (!existsSync(sourceDir)) {
  console.error(`[bundle-backend] expected PyInstaller output at ${sourceDir}, found nothing.`)
  process.exit(1)
}

rmSync(backendOut, { recursive: true, force: true })
mkdirSync(backendOut, { recursive: true })
cpSync(sourceDir, backendOut, { recursive: true })

console.log(`[bundle-backend] copied ${sourceDir} -> ${backendOut}`)
