import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

/**
 * Real terminal rendering — xterm.js interprets the raw PTY output (ANSI
 * colors, \r-overwrites, cursor-addressed redraws) the same way a real
 * terminal emulator does. No hand-rolled buffer/state-machine on our side
 * at all, unlike `terminal.py`'s pyte-based renderer on the Flet app.
 */
export function Terminal(): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<XTerm | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!containerRef.current) return

    const term = new XTerm({
      convertEol: false,
      fontFamily: 'Consolas, "Cascadia Mono", monospace',
      fontSize: 13,
      theme: { background: '#1e1e1e' }
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(containerRef.current)
    fitAddon.fit()
    termRef.current = term
    window.api.resizeTerminal(term.cols, term.rows)

    // Keep every spawned pty's size in sync with the actual visible
    // viewport — otherwise a child's own \r-redrawn progress line gets
    // formatted (wrapped) for whatever fixed size the pty was spawned
    // with, and xterm.js then only clears the first wrapped row on each
    // redraw, leaving stale continuation rows stacking up below it.
    const resizeDisposable = term.onResize(({ cols, rows }) => window.api.resizeTerminal(cols, rows))

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
    }
  }, [])

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
      <button
        onClick={copyLog}
        style={{ position: 'absolute', top: 4, right: 4, zIndex: 1, fontSize: 12 }}
      >
        {copied ? 'Copied!' : 'Copy log'}
      </button>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
    </div>
  )
}
