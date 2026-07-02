import Editor from '@monaco-editor/react'
import { useEffect, useState } from 'react'
import type { AppConfig, SystemConfig } from '../../../shared/types'

/** Project tab — project root + manga/BGM/voice config, ports
 * nicegui_app.py's Project tab. The raw config.system.json editor uses
 * Monaco (real JSON syntax highlighting/validation) instead of a plain
 * textarea — the one place in this app a real code editor earns its keep. */
export function Project(): React.JSX.Element {
  const [projectRoot, setProjectRootState] = useState('')
  const [config, setConfig] = useState<AppConfig>({})
  const [systemConfig, setSystemConfig] = useState<SystemConfig>({})
  const [sysJsonText, setSysJsonText] = useState('{}')
  const [sysJsonError, setSysJsonError] = useState<string | null>(null)
  const [status, setStatus] = useState('')

  const load = async (): Promise<void> => {
    const root = await window.api.getProjectRoot()
    setProjectRootState(root)
    const { config: cfg, systemConfig: sysCfg } = await window.api.getConfig()
    setConfig(cfg)
    setSystemConfig(sysCfg)
    setSysJsonText(JSON.stringify(sysCfg, null, 2))
  }

  useEffect(() => {
    load()
  }, [])

  const browseProjectRoot = async (): Promise<void> => {
    const picked = await window.api.pickDir()
    if (picked) setProjectRootState(picked)
  }

  const useThisFolder = async (): Promise<void> => {
    await window.api.setProjectRoot(projectRoot)
    setStatus(`Project root set to ${projectRoot}`)
    load()
  }

  // The structured fields below (BGM file/volume, voice WAV) edit systemConfig,
  // but Save always writes sysJsonText (the raw Monaco editor's own state) --
  // those never synced automatically, so structured edits were silently
  // discarded on save unless the JSON text happened to already match. Route
  // every structured-field edit through here so sysJsonText always reflects
  // the latest value too.
  const updateSystemConfig = (updater: (sc: SystemConfig) => SystemConfig): void => {
    setSystemConfig((sc) => {
      const next = updater(sc)
      setSysJsonText(JSON.stringify(next, null, 2))
      return next
    })
  }

  const browseBgm = async (): Promise<void> => {
    const picked = await window.api.pickAudioFile()
    if (picked) updateSystemConfig((sc) => ({ ...sc, bgm: { ...sc.bgm, file: picked } }))
  }

  const browseVoice = async (): Promise<void> => {
    const picked = await window.api.pickAudioFile()
    if (picked) updateSystemConfig((sc) => ({ ...sc, tts: { ...sc.tts, speaker_wav: picked } }))
  }

  const save = async (): Promise<void> => {
    let sysCfgToSave: SystemConfig = systemConfig
    try {
      sysCfgToSave = JSON.parse(sysJsonText)
      setSysJsonError(null)
    } catch (err) {
      setSysJsonError(String(err))
      return
    }
    await window.api.setConfig(config, sysCfgToSave)
    setStatus('Saved config.json and config.system.json')
  }

  const setDownload = (patch: Partial<AppConfig['download']>): void =>
    setConfig((c) => ({ ...c, download: { ...c.download, ...patch } }))

  return (
    <div className="tab-panel">
      <div className="section">
        <h3>Project folder</h3>
        <div className="row">
          <input
            className="flex-1 mono"
            type="text"
            value={projectRoot}
            onChange={(e) => setProjectRootState(e.target.value)}
          />
          <button onClick={browseProjectRoot}>Browse…</button>
          <button className="primary" onClick={useThisFolder}>
            Use this folder
          </button>
        </div>
        {status && <p className="hint">{status}</p>}
      </div>

      <div className="section">
        <h3>Manga settings</h3>
        <div className="row">
          <label className="flex-1">
            Manga URL/ID
            <input
              className="flex-1"
              type="text"
              value={config.download?.manga_id ?? ''}
              onChange={(e) => setDownload({ manga_id: e.target.value })}
            />
          </label>
        </div>
        <div className="row">
          <label className="flex-1">
            Name
            <input
              className="flex-1"
              type="text"
              value={config.download?.name ?? ''}
              onChange={(e) => setDownload({ name: e.target.value })}
            />
          </label>
        </div>
      </div>

      <div className="section">
        <h3>Background music</h3>
        <div className="row">
          <input
            className="flex-1 mono"
            type="text"
            value={systemConfig.bgm?.file ?? ''}
            onChange={(e) =>
              updateSystemConfig((sc) => ({ ...sc, bgm: { ...sc.bgm, file: e.target.value } }))
            }
          />
          <button onClick={browseBgm}>Browse…</button>
          <label>
            Volume (dB)
            <input
              type="number"
              style={{ width: 70 }}
              value={systemConfig.bgm?.volume_db ?? -25}
              onChange={(e) =>
                updateSystemConfig((sc) => ({
                  ...sc,
                  bgm: { ...sc.bgm, volume_db: Number(e.target.value) }
                }))
              }
            />
          </label>
        </div>
      </div>

      <div className="section">
        <h3>Voice reference</h3>
        <div className="row">
          <input
            className="flex-1 mono"
            type="text"
            value={systemConfig.tts?.speaker_wav ?? ''}
            onChange={(e) =>
              updateSystemConfig((sc) => ({
                ...sc,
                tts: { ...sc.tts, speaker_wav: e.target.value }
              }))
            }
          />
          <button onClick={browseVoice}>Browse…</button>
        </div>
      </div>

      <details className="section">
        <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
          Advanced: raw config.system.json
        </summary>
        <div style={{ height: 320, marginTop: 8, border: '1px solid #3a3a3e' }}>
          <Editor
            language="json"
            theme="vs-dark"
            value={sysJsonText}
            onChange={(value) => setSysJsonText(value ?? '{}')}
            options={{ minimap: { enabled: false }, fontSize: 13 }}
          />
        </div>
        {sysJsonError && <p style={{ color: '#e05c5c' }}>{sysJsonError}</p>}
      </details>

      <div className="row">
        <button className="primary" onClick={save}>
          Save
        </button>
      </div>
    </div>
  )
}
