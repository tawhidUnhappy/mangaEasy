/**
 * Minimal main-process file logger — everything lands in
 * `<mangaeasyHome>/logs/main.log` so "Open logs folder" in the UI (and bug
 * reports) have one place to look. No external deps; size-capped rotation
 * (main.log -> main.old.log) instead of a scheduler.
 */
import { appendFileSync, existsSync, mkdirSync, renameSync, rmSync, statSync } from 'fs'
import path from 'path'
import { mangaeasyHome } from './paths'

const MAX_LOG_BYTES = 2 * 1024 * 1024

export function logsDir(): string {
  return path.join(mangaeasyHome(), 'logs')
}

function logFile(): string {
  return path.join(logsDir(), 'main.log')
}

function rotateIfNeeded(file: string): void {
  try {
    if (existsSync(file) && statSync(file).size > MAX_LOG_BYTES) {
      const old = file.replace(/\.log$/, '.old.log')
      if (existsSync(old)) rmSync(old)
      renameSync(file, old)
    }
  } catch {
    // Rotation is best-effort; never let logging break the app.
  }
}

export function logLine(level: 'info' | 'warn' | 'error', message: string): void {
  const line = `${new Date().toISOString()} [${level}] ${message}\n`
  try {
    mkdirSync(logsDir(), { recursive: true })
    const file = logFile()
    rotateIfNeeded(file)
    appendFileSync(file, line, 'utf-8')
  } catch {
    // Logging must never throw.
  }
  if (level === 'error') console.error(line.trimEnd())
  else console.log(line.trimEnd())
}
