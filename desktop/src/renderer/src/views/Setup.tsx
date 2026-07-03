import { useCallback, useEffect, useState } from 'react'
import { useJob } from '../job-context'
import type { AppInfo, DoctorStatus, UpdateCheck } from '../../../shared/types'

const CORE_EXES = ['ffmpeg', 'ffprobe', 'uv', 'git']

/** Setup tab — prerequisite checklist, one-click core-tools download, the
 * external AI tool installer, and an About section (version, data folder,
 * logs, update check). Backed by `mangaeasy doctor --json`,
 * `mangaeasy bootstrap-tools` and `mangaeasy install-tool <name>`. */
export function Setup(): React.JSX.Element {
  const { run, running } = useJob()
  const [raw, setDoctor] = useState<DoctorStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [checkingUpdates, setCheckingUpdates] = useState(false)
  const [installing, setInstalling] = useState<string | null>(null)
  const [info, setInfo] = useState<AppInfo | null>(null)
  const [appUpdate, setAppUpdate] = useState<UpdateCheck | null>(null)
  const [checkingAppUpdate, setCheckingAppUpdate] = useState(false)

  const refresh = useCallback(async (checkUpdates = false) => {
    setLoading(true)
    try {
      setDoctor(await window.api.getDoctorStatus(checkUpdates))
    } catch (err) {
      console.error('doctor check failed', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    window.api.getAppInfo().then(setInfo).catch(console.error)
  }, [refresh])

  const checkForUpdates = async (): Promise<void> => {
    setCheckingUpdates(true)
    try {
      await refresh(true)
    } finally {
      setCheckingUpdates(false)
    }
  }

  const checkAppUpdate = async (): Promise<void> => {
    setCheckingAppUpdate(true)
    try {
      setAppUpdate(await window.api.checkAppUpdate(true))
    } finally {
      setCheckingAppUpdate(false)
    }
  }

  const installTool = async (name: string, update = false): Promise<void> => {
    setInstalling(name)
    try {
      await run('install-tool', update ? [name, '--update'] : [name])
    } finally {
      setInstalling(null)
      refresh()
    }
  }

  const downloadCoreTools = async (): Promise<void> => {
    setInstalling('core-tools')
    try {
      await run('bootstrap-tools')
    } finally {
      setInstalling(null)
      refresh()
    }
  }

  const coreMissing = raw
    ? CORE_EXES.filter((exe) => exe in raw.executables && !raw.executables[exe])
    : []
  const gpuLabel =
    raw?.gpu_backend === 'cuda'
      ? `NVIDIA CUDA — ${raw.cuda_device ?? 'GPU'}`
      : raw?.gpu_backend === 'mps'
        ? 'Apple GPU (MPS)'
        : 'CPU only (no supported GPU detected)'

  return (
    <div className="tab-panel">
      <div className="section">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h3>Prerequisites</h3>
          <button onClick={() => refresh()} disabled={loading}>
            Refresh
          </button>
        </div>
        {!raw ? (
          <p className="hint">{loading ? 'Checking…' : 'No data yet.'}</p>
        ) : (
          <>
            {coreMissing.length > 0 && (
              <div className="row" style={{ marginBottom: 8, gap: 10 }}>
                <span className="badge warning">first-run setup</span>
                <span>
                  Missing core tools: <strong>{coreMissing.join(', ')}</strong> — a one-time
                  download (~100 MB) into this app&apos;s own data folder. Nothing is installed
                  system-wide.
                </span>
                <button onClick={downloadCoreTools} disabled={running || installing !== null}>
                  {installing === 'core-tools' ? 'Downloading…' : 'Download core tools'}
                </button>
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              {Object.entries(raw.executables).map(([exe, where]) => (
                <div key={exe} className="row">
                  <span className={where ? 'badge positive' : 'badge negative'}>
                    {where ? 'OK' : 'MISSING'}
                  </span>
                  <span className="mono">{exe}</span>
                </div>
              ))}
              <div className="row">
                <span className={raw.git_lfs ? 'badge positive' : 'badge negative'}>
                  {raw.git_lfs ? 'OK' : 'MISSING'}
                </span>
                <span className="mono">git-lfs</span>
              </div>
              <div className="row">
                <span className={raw.gpu ? 'badge positive' : 'badge grey'}>
                  {raw.gpu ? 'GPU' : 'CPU'}
                </span>
                <span className="mono">{gpuLabel}</span>
              </div>
            </div>
          </>
        )}
      </div>

      {raw && (
        <div className="section">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h3>External AI tools</h3>
            <button onClick={checkForUpdates} disabled={checkingUpdates || running}>
              {checkingUpdates ? 'Checking…' : 'Check for updates'}
            </button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {Object.entries(raw.tools).map(([key, tool]) => (
              <div key={key} className="row" style={{ justifyContent: 'space-between' }}>
                <div>
                  <div>
                    <strong>{tool.title}</strong>{' '}
                    <span className={tool.installed ? 'badge positive' : 'badge warning'}>
                      {tool.installed ? 'installed' : 'not installed'}
                    </span>
                    {tool.needs_gpu && <span className="badge grey">needs GPU</span>}
                    {tool.update_available && (
                      <span className="badge warning">update available</span>
                    )}
                  </div>
                  <div className="hint">{tool.notes}</div>
                </div>
                {!tool.installed && (
                  <button
                    onClick={() => installTool(key)}
                    disabled={running || installing !== null}
                  >
                    {installing === key ? 'Installing…' : 'Install'}
                  </button>
                )}
                {tool.installed && tool.update_available && (
                  <button
                    onClick={() => installTool(key, true)}
                    disabled={running || installing !== null}
                  >
                    {installing === key ? 'Updating…' : 'Update'}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="section">
        <h3>About</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="row" style={{ gap: 8 }}>
            <span>
              mangaEasy <strong>{info?.version ?? '…'}</strong>
            </span>
            <button onClick={checkAppUpdate} disabled={checkingAppUpdate}>
              {checkingAppUpdate ? 'Checking…' : 'Check for app updates'}
            </button>
            {appUpdate &&
              (appUpdate.updateAvailable ? (
                <span>
                  <span className="badge warning">update</span> version {appUpdate.latest} is out —{' '}
                  <a href={appUpdate.url} target="_blank" rel="noreferrer">
                    open the download page
                  </a>
                </span>
              ) : (
                <span className="hint">
                  {appUpdate.latest
                    ? 'You are on the latest version.'
                    : 'Could not reach GitHub (offline?).'}
                </span>
              ))}
          </div>
          {info && (
            <>
              <div className="row" style={{ gap: 8 }}>
                <span className="hint">All app data lives in:</span>
                <span className="mono">{info.dataRoot}</span>
                <button onClick={() => window.api.openFolder(info.dataRoot)}>Open</button>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className="hint">CLI / AI-agent access (same engine, same data):</span>
                <span className="mono" style={{ userSelect: 'all' }}>
                  {info.cli.join(' ')} --help
                </span>
                <button
                  onClick={() =>
                    navigator.clipboard.writeText(
                      `MANGAEASY_ROOT="${info.dataRoot}" ${info.cli.join(' ')} --help`
                    )
                  }
                >
                  Copy
                </button>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className="hint">
                  Deleting that folder{info.platform === 'win32' ? ' (and the app itself)' : ''}{' '}
                  removes everything mangaEasy ever wrote.
                </span>
                <button onClick={() => window.api.openFolder(info.logsDir)}>
                  Open logs folder
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
