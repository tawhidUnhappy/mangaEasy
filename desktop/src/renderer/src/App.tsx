import { useCallback, useEffect, useRef, useState } from 'react'
import { JobProvider } from './job-context'
import { EditorProvider } from './editor-context'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ProgressBar } from './components/ProgressBar'
import { Terminal } from './components/Terminal'
import { Setup } from './views/Setup'
import { Project } from './views/Project'
import { Workflow } from './views/Workflow'
import { Batch } from './views/Batch'
import { Editor } from './views/Editor'
import type { UpdateCheck } from '../../shared/types'

const TABS = [
  ['setup', 'Setup'],
  ['project', 'Project'],
  ['workflow', 'Make a video'],
  ['batch', 'Batch videos'],
  ['editor', 'Editor']
] as const

type TabKey = (typeof TABS)[number][0]

const TERMINAL_HEIGHT_KEY = 'mangaeasy.terminalHeight'
const DEFAULT_TERMINAL_HEIGHT = 320
const MIN_TERMINAL_HEIGHT = 100
const MIN_VIEW_HEIGHT = 220

function loadTerminalHeight(): number {
  const raw = Number(localStorage.getItem(TERMINAL_HEIGHT_KEY))
  return Number.isFinite(raw) && raw >= MIN_TERMINAL_HEIGHT ? raw : DEFAULT_TERMINAL_HEIGHT
}

function App(): React.JSX.Element {
  const [tab, setTab] = useState<TabKey>('setup')
  const [update, setUpdate] = useState<UpdateCheck | null>(null)
  const [updateDismissed, setUpdateDismissed] = useState(false)

  // ---- Resizable terminal pane --------------------------------------------
  const [terminalHeight, setTerminalHeight] = useState<number>(loadTerminalHeight)
  const dragState = useRef<{ startY: number; startHeight: number } | null>(null)

  const onSplitterMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      dragState.current = { startY: e.clientY, startHeight: terminalHeight }

      const onMove = (ev: MouseEvent): void => {
        if (!dragState.current) return
        const delta = dragState.current.startY - ev.clientY
        const max = window.innerHeight - MIN_VIEW_HEIGHT
        const next = Math.min(
          max,
          Math.max(MIN_TERMINAL_HEIGHT, dragState.current.startHeight + delta)
        )
        setTerminalHeight(next)
      }
      const onUp = (): void => {
        dragState.current = null
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        setTerminalHeight((h) => {
          localStorage.setItem(TERMINAL_HEIGHT_KEY, String(h))
          return h
        })
      }
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [terminalHeight]
  )

  const resetTerminalHeight = useCallback(() => {
    setTerminalHeight(DEFAULT_TERMINAL_HEIGHT)
    localStorage.setItem(TERMINAL_HEIGHT_KEY, String(DEFAULT_TERMINAL_HEIGHT))
  }, [])

  // ---- Launch-time update check (throttled to ~daily by the main process) --
  useEffect(() => {
    window.api
      .checkAppUpdate(false)
      .then((result) => {
        if (result.updateAvailable) setUpdate(result)
      })
      .catch(() => {})
  }, [])

  return (
    <JobProvider>
      <EditorProvider>
        <div className="app-shell">
          {update && !updateDismissed && (
            <div className="update-banner">
              <span>
                mangaEasy {update.latest} is available (you have {update.current}) —{' '}
                <a href={update.url} target="_blank" rel="noreferrer">
                  download it from the releases page
                </a>
                .
              </span>
              <button onClick={() => setUpdateDismissed(true)} title="Dismiss">
                ✕
              </button>
            </div>
          )}
          <div className="tab-bar">
            {TABS.map(([key, label]) => (
              <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </div>
          <ProgressBar />

          <div
            style={{
              flex: 1,
              minHeight: MIN_VIEW_HEIGHT,
              display: 'flex',
              flexDirection: 'column'
            }}
          >
            <ErrorBoundary>
              {/* Every view stays mounted, just hidden — a batch run in progress
                  shouldn't be torn down by switching tabs and back. */}
              <Tab active={tab === 'setup'}>
                <Setup />
              </Tab>
              <Tab active={tab === 'project'}>
                <Project />
              </Tab>
              <Tab active={tab === 'workflow'}>
                <Workflow />
              </Tab>
              <Tab active={tab === 'batch'}>
                <Batch />
              </Tab>
              <Tab active={tab === 'editor'}>
                <Editor />
              </Tab>
            </ErrorBoundary>
          </div>

          <div
            className="terminal-splitter"
            onMouseDown={onSplitterMouseDown}
            onDoubleClick={resetTerminalHeight}
            title="Drag to resize the terminal — double-click to reset"
          />
          <div style={{ height: terminalHeight, minHeight: MIN_TERMINAL_HEIGHT, flexShrink: 0 }}>
            <Terminal />
          </div>
        </div>
      </EditorProvider>
    </JobProvider>
  )
}

function Tab({
  active,
  children
}: {
  active: boolean
  children: React.ReactNode
}): React.JSX.Element {
  return <div style={{ display: active ? 'flex' : 'none', flex: 1, minHeight: 0 }}>{children}</div>
}

export default App
