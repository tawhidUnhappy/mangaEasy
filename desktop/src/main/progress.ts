/**
 * Progress-line parsing, ported from `mangaeasy/web/app/jobs.py`'s
 * `report_progress_from_line`/`_parse_progress_buffer`. PTY output here
 * already arrives as decoded UTF-8 text (node-pty), so this only needs to
 * do the line-buffering + regex matching, not the manual byte/\r handling
 * jobs.py had to hand-roll for a plain pipe.
 */
import type { JobProgress } from '../shared/types'

// eslint-disable-next-line no-control-regex -- stripping ANSI escapes requires matching \x1b
const ANSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g
const COUNT_RE = /(?<!\d)(\d{1,6})\s*\/\s*(\d{1,6})(?!\d)/
const PENDING_RE =
  /(\d{1,6})\s+(?:panel(?:\(s\)|s)?|page(?:s)?|file(?:s)?|item(?:s)?|audio|frame(?:s)?|clip(?:s)?)\s+(?:pending|to\s+download|to\s+process)/i
const CHAPTER_RE = /^MANGAEASY_PROGRESS\s+(\d{1,6})\/(\d{1,6})(?:\s+(.*))?$/

function progressLabel(line: string, fallback: string): string {
  const lower = `${line} ${fallback}`.toLowerCase()
  if (lower.includes('deepseek-ocr') || lower.includes('ocr')) return 'OCR panels'
  if (lower.includes('[frame]') || lower.includes('render')) return 'Rendering frames'
  if (lower.includes('[pcm]') || lower.includes('fade')) return 'Preparing audio'
  if (lower.includes('download') || lower.includes('skip (exists)')) return 'Downloading pages'
  if (lower.includes('kokoro') || lower.includes('tts') || lower.includes('audio'))
    return 'Generating audio'
  if (lower.includes('panel')) return 'Processing panels'
  return fallback
}

/** Stateful per-job parser — tracks whether a chapter-level
 * `MANGAEASY_PROGRESS` marker has been seen, so noisier per-file counters
 * (a TTS clip count, a render segment count) stop overriding it afterward. */
export class ProgressParser {
  private chapterMode = false
  private lineBuffer = ''

  /** Feed a raw PTY chunk; returns every progress update found in it. */
  feed(chunk: string): JobProgress[] {
    this.lineBuffer += chunk
    const updates: JobProgress[] = []
    // PTY output is \r\n-terminated lines plus bare \r progress overwrites —
    // splitting on either is enough since we only care about the final
    // state of each visually-distinct line, not exact byte framing.
    const parts = this.lineBuffer.split(/\r\n|\r|\n/)
    this.lineBuffer = parts.pop() ?? ''
    for (const raw of parts) {
      const update = this.parseLine(raw)
      if (update) updates.push(update)
    }
    return updates
  }

  private parseLine(rawLine: string): JobProgress | null {
    const text = rawLine.replace(ANSI_RE, '').trim()
    if (!text) return null

    const chapter = CHAPTER_RE.exec(text)
    if (chapter) {
      this.chapterMode = true
      const value = parseInt(chapter[1], 10)
      const total = parseInt(chapter[2], 10)
      const label = chapter[3] || 'Working'
      if (total > 0) return { value: Math.max(0, Math.min(value, total)), total, label }
      return null
    }

    if (this.chapterMode) return null

    const pending = PENDING_RE.exec(text)
    if (pending) {
      const total = parseInt(pending[1], 10)
      if (total > 0) return { value: 0, total, label: progressLabel(text, 'Working') }
      return null
    }

    const match = COUNT_RE.exec(text)
    if (!match) return null
    const value = parseInt(match[1], 10)
    const total = parseInt(match[2], 10)
    if (total <= 0) return null
    return {
      value: Math.max(0, Math.min(value, total)),
      total,
      label: progressLabel(text, 'Working')
    }
  }
}
