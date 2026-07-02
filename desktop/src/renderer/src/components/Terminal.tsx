import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const FONT_SIZE_KEY = 'mangaeasy.terminalFontSize'
const DEFAULT_FONT_SIZE = 13
const MIN_FONT_SIZE = 8
const MAX_FONT_SIZE = 24

function loadFontSize(): number {
  const raw = Number(localStorage.getItem(FONT_SIZE_KEY))
  return Number.isFinite(raw) && raw >= MIN_FONT_SIZE && raw <= MAX_FONT_SIZE
    ? raw
    : DEFAULT_FONT_SIZE
}

/**
 * Real terminal rendering — xterm.js interprets the raw PTY output (ANSI
 * colors, \r-overwrites, cursor-addressed redraws) the same way a real
 * terminal emulator does. Font size is adjustable (A−/A+, persisted) and the
 * whole pane is resizable via the splitter above it (see App.tsx).
 */
export function Terminal(): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<XTerm | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const [copied, setCopied] = useState(false)
  const [fontSize, setFontSize] = useState<number>(loadFontSize)

  useEffect(() => {
    if (!containerRef.current) return

    const term = new XTerm({
      convertEol: false,
      fontFamily: 'Consolas, "Cascadia Mono", monospace',
      fontSize: loadFontSize(),
      theme: { background: '#1e1e1e' }
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(containerRef.current)
    fitAddon.fit()
    termRef.current = term
    fitRef.current = fitAddon
    window.api.resizeTerminal(term.cols, term.rows)

    // Keep every spawned pty's size in sync with the actual visible
    // viewport — otherwise a child's own \r-redrawn progress line gets
    // formatted (wrapped) for whatever fixed size the pty was spawned
    // with, and xterm.js then only clears the first wrapped row on each
    // redraw, leaving stale continuation rows stacking up below it.
    const resizeDisposable = term.onResize(({ cols, rows }) =>
      window.api.resizeTerminal(cols, rows)
    )

    const resizeObserver = new ResizeObserver(() => fitAddon.fit())
    resizeObserver.observe(containerRef.current)

    const unsubscribe = window.api.onTerminalData((chunk) => {
      term.write(chunk)
    })

    return () => {
      unsubscribe()
      resizeDisposable.dispose()
      resizeObserver.disconnect()
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
  }, [])

  const changeFontSize = (delta: number): void => {
    setFontSize((current) => {
      const next = Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, current + delta))
      localStorage.setItem(FONT_SIZE_KEY, String(next))
      const term = termRef.current
      if (term) {
        term.options.fontSize = next
        fitRef.current?.fit()
      }
      return next
    })
  }

  const copyLog = async (): Promise<void> => {
    const term = termRef.current
    if (!term) return
    const buffer = term.buffer.active
    const lines: string[] = []
    for (let i = 0; i < buffer.length; i++) {
      lines.push(buffer.getLine(i)?.translateToString(true) ?? '')
    }
    await navigator.clipboard.writeText(lines.join('\n').replace(/\s+$/, '\n'))
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div style={{ position: 'relative', height: '100%', width: '100%' }}>
      <div className="terminal-toolbar">
        <button
          onClick={() => changeFontSize(-1)}
          disabled={fontSize <= MIN_FONT_SIZE}
          title="Smaller text"
        >
          A−
        </button>
        <button
          onClick={() => changeFontSize(1)}
          disabled={fontSize >= MAX_FONT_SIZE}
          title="Larger text"
        >
          A+
        </button>
        <button onClick={copyLog}>{copied ? 'Copied!' : 'Copy log'}</button>
      </div>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
    </div>
  )
}
