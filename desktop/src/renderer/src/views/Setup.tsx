import { useCallback, useEffect, useState } from 'react'
import { useJob } from '../job-context'
import type { DoctorStatus } from '../../../shared/types'

/** Setup tab — prerequisite checklist + external AI tool installer. Ports
 * nicegui_app.py's Setup tab, backed by `mangaeasy doctor --json` and
 * `mangaeasy install-tool <name>` instead of importing `mangaeasy.tools.install`
 * directly (Electron's main process never imports Python — it only spawns it). */
export function Setup(): React.JSX.Element {
  const { run, running } = useJob()
  const [raw, setDoctor] = useState<DoctorStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [checkingUpdates, setCheckingUpdates] = useState(false)
  const [installing, setInstalling] = useState<string | null>(null)

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
  }, [refresh])

  const checkForUpdates = async (): Promise<void> => {
    setCheckingUpdates(true)
    try {
      await refresh(true)
    } finally {
      setCheckingUpdates(false)
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
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            {Object.entries(raw.executables).map(([exe, where]) => (
              <div key={exe} className="row">
                <span className={where ? 'badge positive' : 'badge negative'}>{where ? 'OK' : 'MISSING'}</span>
                <span className="mono">{exe}</span>
              </div>
            ))}
            <div className="row">
              <span className={raw.git_lfs ? 'badge positive' : 'badge negative'}>{raw.git_lfs ? 'OK' : 'MISSING'}</span>
              <span className="mono">git-lfs</span>
            </div>
            <div className="row">
              <span className={raw.gpu ? 'badge positive' : 'badge grey'}>{raw.gpu ? 'GPU' : 'CPU only'}</span>
              <span className="mono">{raw.cuda_device ?? 'no NVIDIA GPU detected'}</span>
            </div>
          </div>
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
            {Object.entries(raw.tools).map(([key, info]) => (
              <div key={key} className="row" style={{ justifyContent: 'space-between' }}>
                <div>
                  <div>
                    <strong>{info.title}</strong>{' '}
                    <span className={info.installed ? 'badge positive' : 'badge warning'}>
                      {info.installed ? 'installed' : 'not installed'}
                    </span>
                    {info.needs_gpu && <span className="badge grey">needs GPU</span>}
                    {info.update_available && <span className="badge warning">update available</span>}
                  </div>
                  <div className="hint">{info.notes}</div>
                </div>
                {!info.installed && (
                  <button onClick={() => installTool(key)} disabled={running || installing !== null}>
                    {installing === key ? 'Installing…' : 'Install'}
                  </button>
                )}
                {info.installed && info.update_available && (
                  <button onClick={() => installTool(key, true)} disabled={running || installing !== null}>
                    {installing === key ? 'Updating…' : 'Update'}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
