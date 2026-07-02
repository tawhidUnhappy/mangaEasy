import { useCallback, useEffect, useState } from 'react'
import { useJob } from '../job-context'
import { useEditor } from '../editor-context'
import type { ChapterStatus, DeleteWhat, PurgeKind } from '../../../shared/types'

const LANGS: Record<string, string> = {
  en: 'English',
  es: 'Spanish',
  'es-la': 'Spanish (LATAM)',
  'pt-br': 'Portuguese BR',
  fr: 'French',
  de: 'German',
  it: 'Italian',
  ru: 'Russian',
  id: 'Indonesian',
  vi: 'Vietnamese',
  th: 'Thai',
  zh: 'Chinese',
  ja: 'Japanese',
  ko: 'Korean'
}

function configuredBgmFile(audioBgm: string | undefined, sysBgmFile: string | undefined): string {
  return (sysBgmFile || audioBgm || '').trim()
}

// dlMode/dlFrom/dlTo/dlFresh/normalize are pure UI convenience (not saved to
// config.json like chapter/lang/audioSource below), so plain localStorage is
// enough to survive a reload.
const WF_PREFS_KEY = 'mangaeasy.workflow.prefs.v1'

interface WorkflowPrefs {
  dlMode: 'single' | 'range'
  dlFrom: number
  dlTo: number
  dlFresh: boolean
  normalize: boolean
}

function loadWfPrefs(): Partial<WorkflowPrefs> {
  try {
    const raw = localStorage.getItem(WF_PREFS_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

/** "Make a video" tab — single-chapter pipeline, ports nicegui_app.py's
 * Workflow tab (download -> crop -> narrate -> generate audio+video). */
export function Workflow(): React.JSX.Element {
  const { run, runChain, running } = useJob()
  const { launch: launchEditor } = useEditor()
  const initialWfPrefs = useState(loadWfPrefs)[0]
  const [chapter, setChapter] = useState(1)
  const [lang, setLang] = useState('en')
  const [dlMode, setDlMode] = useState<'single' | 'range'>(initialWfPrefs.dlMode ?? 'single')
  const [dlFrom, setDlFrom] = useState(initialWfPrefs.dlFrom ?? 1)
  const [dlTo, setDlTo] = useState(initialWfPrefs.dlTo ?? 5)
  const [dlFresh, setDlFresh] = useState(initialWfPrefs.dlFresh ?? false)
  const [normalize, setNormalize] = useState(initialWfPrefs.normalize ?? false)
  const [audioSource, setAudioSource] = useState<'raw' | 'faded'>('raw')
  const [mangaName, setMangaName] = useState('')
  const [bgmFile, setBgmFile] = useState('')
  const [audioDuck, setAudioDuck] = useState(false)
  const [status, setStatus] = useState<ChapterStatus | null>(null)

  useEffect(() => {
    const prefs: WorkflowPrefs = { dlMode, dlFrom, dlTo, dlFresh, normalize }
    localStorage.setItem(WF_PREFS_KEY, JSON.stringify(prefs))
  }, [dlMode, dlFrom, dlTo, dlFresh, normalize])

  const refreshStatus = useCallback(async () => {
    if (!mangaName) return
    const s = await window.api.getChapterStatus(mangaName, chapter)
    setStatus(s)
  }, [mangaName, chapter])

  useEffect(() => {
    window.api.getConfig().then(({ config, systemConfig }) => {
      setMangaName(String(config.download?.name ?? ''))
      setChapter(Number(config.download?.chapter ?? 1))
      setLang(String(config.download?.translated_language ?? 'en'))
      setBgmFile(configuredBgmFile(config.audio?.bgm, systemConfig.bgm?.file))
      setAudioSource((systemConfig.video?.audio_source as 'raw' | 'faded') ?? 'raw')
      setAudioDuck(Boolean(systemConfig.bgm?.duck ?? false))
    })
  }, [])

  useEffect(() => {
    refreshStatus()
  }, [refreshStatus])

  const saveWfConfig = async (): Promise<void> => {
    const { config, systemConfig } = await window.api.getConfig()
    config.download = { ...config.download, chapter, translated_language: lang }
    systemConfig.video = { ...systemConfig.video, audio_source: audioSource }
    await window.api.setConfig(config, systemConfig)
  }

  const download = async (): Promise<void> => {
    await saveWfConfig()
    if (dlMode === 'range') {
      for (let ch = dlFrom; ch <= dlTo; ch++) {
        const { config } = await window.api.getConfig()
        config.download = { ...config.download, chapter: ch }
        await window.api.setConfig(config, undefined)
        await run('download', dlFresh ? ['--fresh'] : [])
      }
    } else {
      await run('download', dlFresh ? ['--fresh'] : [])
    }
    refreshStatus()
  }

  const launchCrop = async (): Promise<void> => {
    await saveWfConfig()
    await launchEditor('cut-page')
  }
  const launchArrange = async (): Promise<void> => {
    await saveWfConfig()
    await launchEditor('panel-editor')
  }
  const launchNarration = async (): Promise<void> => {
    await saveWfConfig()
    await window.api.ensureNarrationForOcr(chapter)
    await launchEditor('narration-editor')
  }

  const runOcr = async (force: boolean): Promise<void> => {
    await saveWfConfig()
    const { path: narrPath, reason } = await window.api.ensureNarrationForOcr(chapter)
    if (!narrPath) {
      setStatus((s) => s)
      console.warn('got-ocr2:', reason)
      return
    }
    const args = ['--narration', narrPath, '--device', 'auto']
    if (force) args.push('--force')
    await run('got-ocr2', args)
  }

  const exportZip = async (): Promise<void> => {
    await saveWfConfig()
    await window.api.exportAiZip(chapter)
  }

  const videoSteps = (includeAudioPrep: boolean): { command: string; args: string[] }[] => {
    const steps: { command: string; args: string[] }[] = [{ command: 'render-video', args: [] }]
    if (includeAudioPrep && audioSource === 'faded')
      steps.unshift({ command: 'fade-audio', args: [] })
    if (bgmFile) steps.push({ command: 'add-bgm', args: [] })
    if (normalize) steps.push({ command: 'normalize-chapter-audio', args: [] })
    return steps
  }

  const genAll = async (): Promise<void> => {
    await saveWfConfig()
    await runChain([{ command: 'index-tts', args: [] }, ...videoSteps(true)])
    refreshStatus()
  }
  const genAudio = async (): Promise<void> => {
    await saveWfConfig()
    await run('index-tts', [])
    refreshStatus()
  }
  const genVideo = async (): Promise<void> => {
    await saveWfConfig()
    // Rendering (raw OR faded source) and fading both require raw audio to
    // already exist on disk -- on a chapter that's never had audio generated,
    // jumping straight to fade-audio/render-video would just fail with
    // "no raw audio found". Generate it first instead of making the user
    // remember to click "Generate audio" before this.
    const liveStatus = await window.api.getChapterStatus(mangaName, chapter)
    const steps =
      liveStatus.audio === 0
        ? [{ command: 'index-tts', args: [] }, ...videoSteps(true)]
        : videoSteps(true)
    await runChain(steps)
    refreshStatus()
  }
  const rerenderVideo = async (): Promise<void> => {
    await saveWfConfig()
    await runChain(videoSteps(false))
    refreshStatus()
  }

  const setAudioDuckAndSave = async (value: boolean): Promise<void> => {
    setAudioDuck(value)
    const { systemConfig } = await window.api.getConfig()
    await window.api.setConfig(undefined, {
      ...systemConfig,
      bgm: { ...systemConfig.bgm, duck: value }
    })
  }

  const deleteData = async (what: DeleteWhat): Promise<void> => {
    await window.api.deleteChapter(chapter, what)
    refreshStatus()
  }
  const purgeData = async (kind: PurgeKind): Promise<void> => {
    await window.api.purgeChapters(kind)
    refreshStatus()
  }

  return (
    <div className="tab-panel">
      <div className="section">
        <h3>1 · Download pages</h3>
        <div className="row">
          <label>
            Chapter
            <input
              type="number"
              min={1}
              style={{ width: 70 }}
              value={chapter}
              onChange={(e) => setChapter(Number(e.target.value))}
            />
          </label>
          <label>
            Language
            <select value={lang} onChange={(e) => setLang(e.target.value)}>
              {Object.entries(LANGS).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="row">
          <label>
            <input
              type="radio"
              checked={dlMode === 'single'}
              onChange={() => setDlMode('single')}
            />{' '}
            Single
          </label>
          <label>
            <input type="radio" checked={dlMode === 'range'} onChange={() => setDlMode('range')} />{' '}
            Range
          </label>
          {dlMode === 'range' && (
            <>
              <label>
                From
                <input
                  type="number"
                  style={{ width: 60 }}
                  value={dlFrom}
                  onChange={(e) => setDlFrom(Number(e.target.value))}
                />
              </label>
              <label>
                To
                <input
                  type="number"
                  style={{ width: 60 }}
                  value={dlTo}
                  onChange={(e) => setDlTo(Number(e.target.value))}
                />
              </label>
            </>
          )}
          <label>
            <input
              type="checkbox"
              checked={dlFresh}
              onChange={(e) => setDlFresh(e.target.checked)}
            />{' '}
            Force fresh metadata
          </label>
        </div>
        <div className="row">
          <button className="primary" onClick={download} disabled={running}>
            ⬇ Download
          </button>
        </div>
        {status && <p className="hint">{status.dl} page(s) downloaded</p>}
      </div>

      <div className="section">
        <h3>2 · Crop into panels</h3>
        <div className="row">
          <button onClick={launchCrop} disabled={running}>
            ✂ Cut pages (manga / manhua)
          </button>
          <button onClick={launchArrange} disabled={running}>
            ⬇ Arrange strips (webtoon)
          </button>
        </div>
        {status && <p className="hint">{status.panels} panel(s)</p>}
      </div>

      <div className="section">
        <h3>3 · Write the narration</h3>
        <div className="row">
          <button className="primary" onClick={launchNarration} disabled={running}>
            📝 Open narration editor
          </button>
          <button onClick={() => runOcr(false)} disabled={running}>
            Run GOT-OCR
          </button>
          <button onClick={() => runOcr(true)} disabled={running}>
            Re-run GOT-OCR
          </button>
          <button onClick={exportZip} disabled={running}>
            ⬇ Export ZIP for AI
          </button>
        </div>
        {status && (
          <p className="hint">
            {status.narr ? `${status.narrItems} narration line(s)` : 'not written'}
          </p>
        )}
      </div>

      <div className="section">
        <h3>4 · Generate audio &amp; video</h3>
        <div className="row">
          <label>
            <input
              type="checkbox"
              checked={normalize}
              onChange={(e) => setNormalize(e.target.checked)}
            />{' '}
            YouTube loudness (-14 LUFS)
          </label>
          <label title="Audio ducking: background music automatically lowers when narration is playing, so narration is never drowned out.">
            <input
              type="checkbox"
              checked={audioDuck}
              onChange={(e) => setAudioDuckAndSave(e.target.checked)}
            />{' '}
            Audio ducking
          </label>
          <label>
            Audio source
            <select
              value={audioSource}
              onChange={(e) => setAudioSource(e.target.value as 'raw' | 'faded')}
            >
              <option value="raw">Raw audio</option>
              <option value="faded">Faded audio (de-click)</option>
            </select>
          </label>
        </div>
        <div className="row">
          <button className="primary" onClick={genAll} disabled={running}>
            ▶ Everything
          </button>
          <button onClick={genAudio} disabled={running}>
            🎙 Audio only
          </button>
          <button onClick={genVideo} disabled={running}>
            🎬 Video only
          </button>
          <button onClick={rerenderVideo} disabled={running}>
            Re-render video
          </button>
        </div>
        {status && (
          <p className="hint">
            {status.video
              ? 'video ready'
              : status.audio
                ? `${status.audio} audio clip(s) (no video)`
                : 'not generated'}
          </p>
        )}

        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer' }}>Delete chapter data…</summary>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="negative" onClick={() => deleteData('download')}>
              ✕ Downloads
            </button>
            <button className="negative" onClick={() => deleteData('panels')}>
              ✕ Panels
            </button>
            <button className="negative" onClick={() => deleteData('audio')}>
              ✕ Audio
            </button>
            <button className="negative" onClick={() => deleteData('video')}>
              ✕ Video
            </button>
            <button className="negative" onClick={() => deleteData('all')}>
              ✕ Everything
            </button>
          </div>
        </details>
      </div>

      <div className="section" style={{ borderColor: '#5e2222' }}>
        <h3>🧹 Purge across all chapters</h3>
        <p className="hint">Remove a file category from every chapter of this manga.</p>
        <div className="row">
          <button className="negative" onClick={() => purgeData('ai-zip')}>
            ✕ AI ZIPs
          </button>
          <button className="negative" onClick={() => purgeData('narration')}>
            ✕ Narration
          </button>
          <button className="negative" onClick={() => purgeData('audio')}>
            ✕ Audio
          </button>
          <button className="negative" onClick={() => purgeData('video')}>
            ✕ Video
          </button>
        </div>
      </div>
    </div>
  )
}
