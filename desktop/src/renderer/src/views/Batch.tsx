import { useCallback, useEffect, useState } from 'react'
import { useJob } from '../job-context'
import type { AudioTakesStatus, LibraryEntry } from '../../../shared/types'

const STEPS: Record<string, string> = {
  video: 'Everything (IndexTTS + blur + long video)',
  'video-check': 'Check items only',
  'got-ocr2': 'Fill OCR fields (GOT-OCR 2.0)',
  'video-audio': 'Audio only (Kokoro)',
  'video-audio-indextts': 'Audio only (IndexTTS)',
  'video-fade-audio': 'Create faded audio copies (de-click)',
  'video-render': 'Render videos only',
  'video-join': 'Join into one long video',
  'video-normalize-audio': 'Loudness-normalize joined audio',
  'video-clean-audio': 'Clear generated audio (kept, restorable later)',
  'video-clean-video': 'Delete rendered videos',
  'video-validate': 'Validate generated output',
  'video-clean-all': 'DELETE ALL generated output (start fresh)'
}

const AUDIO_STEPS = new Set(['video-check', 'video-validate', 'video-clean-audio'])
const OUTPUT_STEPS = new Set(['video-validate', 'video-clean-video'])

// Persist the user's choices across app reloads/restarts -- this is pure UI
// convenience (which manga/step/options were last selected), not config the
// CLI itself reads, so plain localStorage is enough; no IPC round trip needed.
const PREFS_KEY = 'mangaeasy.batch.prefs.v1'

interface BatchPrefs {
  mangaPath: string
  useRange: boolean
  rangeFrom: number
  rangeTo: number
  step: string
  tts: 'auto' | 'indextts' | 'kokoro'
  audioSource: 'raw' | 'faded'
  longVideo: boolean
  normalize: boolean
  bgm: boolean
  resume: boolean
  overwriteAudio: boolean
  skipAudio: boolean
  ocrForce: boolean
  renderWorkers: number
  gpuWorkers: number
  outDir: string
}

function loadPrefs(): Partial<BatchPrefs> {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

/** Batch videos tab — the multi-chapter ("item"-based) pipeline. Ports
 * nicegui_app.py's Batch tab's argument-building functions
 * (`_batch_base_args`/`_append_audio_root`/`_append_bgm`) 1:1 so the CLI
 * contract stays identical. */
export function Batch(): React.JSX.Element {
  const { run, runChain, running } = useJob()
  const initialPrefs = useState(loadPrefs)[0]
  const [entries, setEntries] = useState<LibraryEntry[]>([])
  const [mangaPath, setMangaPath] = useState(initialPrefs.mangaPath ?? '')
  const [useRange, setUseRange] = useState(initialPrefs.useRange ?? true)
  const [rangeFrom, setRangeFrom] = useState(initialPrefs.rangeFrom ?? 1)
  const [rangeTo, setRangeTo] = useState(initialPrefs.rangeTo ?? 24)
  const [step, setStep] = useState(initialPrefs.step ?? 'video')
  const [tts, setTts] = useState<'auto' | 'indextts' | 'kokoro'>(initialPrefs.tts ?? 'indextts')
  const [audioSource, setAudioSource] = useState<'raw' | 'faded'>(initialPrefs.audioSource ?? 'raw')
  const [longVideo, setLongVideo] = useState(initialPrefs.longVideo ?? true)
  const [normalize, setNormalize] = useState(initialPrefs.normalize ?? true)
  const [bgm, setBgm] = useState(initialPrefs.bgm ?? true)
  const [resume, setResume] = useState(initialPrefs.resume ?? false)
  const [overwriteAudio, setOverwriteAudio] = useState(initialPrefs.overwriteAudio ?? false)
  const [skipAudio, setSkipAudio] = useState(initialPrefs.skipAudio ?? false)
  const [takes, setTakes] = useState<AudioTakesStatus | null>(null)
  const [takesLoading, setTakesLoading] = useState(false)
  const [ocrForce, setOcrForce] = useState(initialPrefs.ocrForce ?? false)
  const [renderWorkers, setRenderWorkers] = useState(initialPrefs.renderWorkers ?? 3)
  const [gpuWorkers, setGpuWorkers] = useState(initialPrefs.gpuWorkers ?? 1)
  const [outDir, setOutDir] = useState(initialPrefs.outDir ?? 'output')
  const [bgmFile, setBgmFile] = useState('')
  const [bgmVolumeDb, setBgmVolumeDb] = useState<number | null>(null)
  const [paths, setPaths] = useState({ outputRoot: '', projectOutputDir: '', audioRoot: '', fadedAudioRoot: '' })

  useEffect(() => {
    const prefs: BatchPrefs = {
      mangaPath, useRange, rangeFrom, rangeTo, step, tts, audioSource, longVideo,
      normalize, bgm, resume, overwriteAudio, skipAudio, ocrForce, renderWorkers, gpuWorkers, outDir
    }
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
  }, [
    mangaPath, useRange, rangeFrom, rangeTo, step, tts, audioSource, longVideo,
    normalize, bgm, resume, overwriteAudio, skipAudio, ocrForce, renderWorkers, gpuWorkers, outDir
  ])

  const refreshMangas = useCallback(async () => {
    const { entries: found } = await window.api.listLibrary()
    setEntries(found)
    if (!mangaPath && found.length) {
      const selected = found.find((e) => e.selected) ?? found[0]
      setMangaPath(selected.path)
    }
  }, [mangaPath])

  useEffect(() => {
    refreshMangas()
    window.api.getConfig().then(({ systemConfig }) => {
      setBgmFile(systemConfig.bgm?.file ?? '')
      setBgmVolumeDb(systemConfig.bgm?.volume_db ?? -25)
    })
  }, [refreshMangas])

  // Resolve --output-root/--audio-root against the actual project root
  // (Node's `path`, not string concatenation) whenever the inputs change —
  // mirrors `_batch_output_root`/`_batch_audio_root`/`_batch_faded_audio_root`.
  useEffect(() => {
    if (!mangaPath) return
    window.api.resolveBatchPaths(outDir, mangaPath).then(setPaths)
  }, [outDir, mangaPath])

  useEffect(() => {
    setTakes(null)
  }, [mangaPath])

  const refreshTakes = useCallback(async (): Promise<void> => {
    if (!mangaPath || !paths.audioRoot) return
    setTakesLoading(true)
    try {
      setTakes(await window.api.listAudioTakes(mangaPath, paths.audioRoot))
    } finally {
      setTakesLoading(false)
    }
  }, [mangaPath, paths.audioRoot])

  const restoreTake = async (run: string): Promise<void> => {
    if (!mangaPath || !paths.audioRoot) return
    await window.api.restoreAudioTake(mangaPath, paths.audioRoot, run)
    await refreshTakes()
  }

  const rangeArg = (): string | null => {
    if (!useRange) return null
    const [start, end] = rangeFrom <= rangeTo ? [rangeFrom, rangeTo] : [rangeTo, rangeFrom]
    return `${String(start).padStart(2, '0')}-${String(end).padStart(2, '0')}`
  }

  const baseArgs = (opts: { output?: boolean; items?: boolean } = {}): string[] | null => {
    if (!mangaPath) return null
    const args = ['--project-root', mangaPath]
    if (opts.output) args.push('--output-root', paths.outputRoot)
    const range = opts.items !== false ? rangeArg() : null
    if (range) args.push('--item-range', range)
    return args
  }

  const audioRoot = (faded: boolean): string => (faded ? paths.fadedAudioRoot : paths.audioRoot)

  const appendAudioRoot = (args: string[]): void => {
    args.push('--audio-root', audioRoot(audioSource === 'faded'))
  }
  const appendBgm = (args: string[]): void => {
    if (!bgm || !bgmFile) return
    args.push('--background-music', bgmFile)
    if (bgmVolumeDb !== null) args.push('--music-volume-db', String(bgmVolumeDb))
  }

  // setConfig overwrites config.system.json wholesale, so any save here must
  // start from the full current file (not just the bgm fields) or it would
  // silently wipe out unrelated settings like the voice reference WAV.
  const browseBgm = async (): Promise<void> => {
    const picked = await window.api.pickAudioFile()
    if (!picked) return
    const { systemConfig } = await window.api.getConfig()
    const updated = { ...systemConfig, bgm: { ...systemConfig.bgm, file: picked } }
    await window.api.setConfig(undefined, updated)
    setBgmFile(picked)
  }

  const setBgmVolume = async (db: number): Promise<void> => {
    setBgmVolumeDb(db)
    const { systemConfig } = await window.api.getConfig()
    await window.api.setConfig(undefined, { ...systemConfig, bgm: { ...systemConfig.bgm, volume_db: db } })
  }

  const start = async (): Promise<void> => {
    if (step === 'got-ocr2') {
      const args = baseArgs()
      if (!args) return
      args.push('--device', 'auto')
      if (ocrForce) args.push('--force')
      await run('got-ocr2', args)
      return
    }

    if (step === 'video') {
      const args = baseArgs({ output: true, items: true })
      if (!args) return
      args.push('--audio-root', audioRoot(false))
      args.push('--tts', tts, '--background-style', 'blur', '--blur-backend', 'auto')
      args.push('--video-workers', String(renderWorkers))
      if (gpuWorkers !== 1) args.push('--gpu-workers', String(gpuWorkers))
      if (audioSource === 'faded') args.push('--audio-source', 'faded')
      if (overwriteAudio) {
        // Forcing fresh audio but not the video re-render would leave the
        // rendered/joined video stuck on the stale narration -- the render
        // step skips already-existing item videos by default.
        args.push('--overwrite-audio', '--overwrite-video')
      }
      if (resume) args.push('--resume-audio')
      if (skipAudio) args.push('--skip-audio')
      if (longVideo) {
        args.push('--build-long-video')
        appendBgm(args)
        if (normalize) args.push('--normalize-audio')
      }
      await run(step, args)
      return
    }

    if (step === 'video-render') {
      const renderArgs = baseArgs({ output: true, items: true })
      if (!renderArgs) return
      appendAudioRoot(renderArgs)
      renderArgs.push('--background-style', 'blur', '--blur-backend', 'auto')
      renderArgs.push('--workers', String(renderWorkers))

      // Rendering needs every narration entry's audio to already exist (this
      // is the only step where mismatches actually surface, as a crash
      // rather than a hang -- see the chapter1_trailer_*.wav incident). Rather
      // than make the user remember to run audio generation again whenever
      // narration/intro.json changes, backfill whatever's missing first --
      // a no-op if everything's already there, since these steps skip
      // existing files by default.
      const backfillArgs = baseArgs({ items: true })
      if (!backfillArgs) return
      backfillArgs.push('--audio-root', audioRoot(false), '--device', 'auto')
      const chain = [{ command: 'video-audio', args: backfillArgs }]
      if (audioSource === 'faded') {
        const fadeArgs = baseArgs({ items: true }) ?? []
        fadeArgs.push(
          '--source-audio-root', audioRoot(false),
          '--output-audio-root', audioRoot(true),
        )
        chain.push({ command: 'video-fade-audio', args: fadeArgs })
      }
      chain.push({ command: step, args: renderArgs })
      await runChain(chain)
      return
    }

    if (step === 'video-join') {
      const args = baseArgs({ output: true, items: true })
      if (!args) return
      appendAudioRoot(args)
      args.push('--overwrite')
      appendBgm(args)
      await run(step, args)
      return
    }

    if (step === 'video-normalize-audio') {
      const args = baseArgs({ output: true, items: false })
      if (!args) return
      args.push('--replace')
      await run(step, args)
      return
    }

    if (step === 'video-audio') {
      const args = baseArgs({ items: true })
      if (!args) return
      appendAudioRoot(args)
      args.push('--device', 'auto')
      if (overwriteAudio) args.push('--overwrite')
      if (resume) args.push('--resume')
      if (gpuWorkers !== 1) args.push('--gpu-workers', String(gpuWorkers))
      await run(step, args)
      return
    }

    if (step === 'video-audio-indextts') {
      const args = baseArgs({ items: true })
      if (!args) return
      appendAudioRoot(args)
      if (overwriteAudio) args.push('--overwrite')
      if (resume) args.push('--resume')
      if (gpuWorkers !== 1) args.push('--gpu-workers', String(gpuWorkers))
      await run(step, args)
      return
    }

    if (step === 'video-clean-all') {
      if (!mangaPath || !paths.projectOutputDir) return
      const mangaLabel = entries.find((e) => e.path === mangaPath)?.label ?? mangaPath
      const confirmed = window.confirm(
        `Delete ALL generated output for "${mangaLabel}"?\n\n${paths.projectOutputDir}\n\n` +
          'This removes every narration audio take (including archived ones), rendered chapter videos, ' +
          'and the joined long video. Chapters, panels, downloads, and narration are NOT touched. ' +
          'This cannot be undone.'
      )
      if (!confirmed) return
      await run(step, ['--project-root', mangaPath, '--dir', paths.projectOutputDir, '--yes'])
      return
    }

    if (step === 'video-fade-audio') {
      const args = baseArgs({ items: true })
      if (!args) return
      args.push('--source-audio-root', audioRoot(false))
      args.push('--output-audio-root', audioRoot(true))
      args.push('--overwrite')
      await run(step, args)
      return
    }

    const args = baseArgs({ output: OUTPUT_STEPS.has(step), items: true })
    if (!args) return
    if (AUDIO_STEPS.has(step)) appendAudioRoot(args)
    if (step === 'video-clean-audio' || step === 'video-clean-video') args.push('--yes')
    await run(step, args)
  }

  const showAudioSource = ['video', 'video-render', 'video-join', 'video-check', 'video-validate', 'video-clean-audio'].includes(step)
  const showResume = ['video', 'video-audio', 'video-audio-indextts'].includes(step)
  const showSkipAudio = step === 'video'
  const showOcrForce = step === 'got-ocr2'
  const showRenderWorkers = ['video', 'video-render'].includes(step)
  const showGpuWorkers = ['video', 'video-audio', 'video-audio-indextts'].includes(step)

  return (
    <div className="tab-panel">
      <p className="hint">Pick a manga from your library, choose a chapter range, then generate narrated videos.</p>

      <div className="section">
        <h3>Manga and chapters</h3>
        <div className="row">
          <select className="flex-1 mono" value={mangaPath} onChange={(e) => setMangaPath(e.target.value)}>
            {entries.map((e) => (
              <option key={e.path} value={e.path}>
                {e.label}
              </option>
            ))}
          </select>
          <button onClick={refreshMangas}>Refresh</button>
          <button onClick={() => mangaPath && window.api.openFolder(mangaPath)}>Open</button>
        </div>
        <p className="hint">{entries.length} manga folder(s) found</p>
        <div className="row">
          <label>
            <input type="checkbox" checked={useRange} onChange={(e) => setUseRange(e.target.checked)} /> Use chapter range
          </label>
          <label>
            From
            <input type="number" style={{ width: 60 }} value={rangeFrom} onChange={(e) => setRangeFrom(Number(e.target.value))} />
          </label>
          <label>
            To
            <input type="number" style={{ width: 60 }} value={rangeTo} onChange={(e) => setRangeTo(Number(e.target.value))} />
          </label>
        </div>
      </div>

      <div className="section">
        <h3>What to do</h3>
        <div className="row">
          <label>
            Step
            <select value={step} onChange={(e) => setStep(e.target.value)}>
              {Object.entries(STEPS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Voice engine
            <select value={tts} onChange={(e) => setTts(e.target.value as typeof tts)}>
              <option value="auto">Auto</option>
              <option value="indextts">IndexTTS</option>
              <option value="kokoro">Kokoro</option>
            </select>
          </label>
          {showAudioSource && (
            <label title="Faded copies have tiny fade-in/out to remove clicks/pops. The raw audio is never deleted.">
              Audio source
              <select value={audioSource} onChange={(e) => setAudioSource(e.target.value as typeof audioSource)}>
                <option value="raw">Raw audio</option>
                <option value="faded">Faded audio (de-click)</option>
              </select>
            </label>
          )}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <label>
            <input type="checkbox" checked={longVideo} onChange={(e) => setLongVideo(e.target.checked)} /> Generate one long video
          </label>
          <label>
            <input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} /> YouTube loudness
          </label>
          <label>
            <input type="checkbox" checked={bgm} onChange={(e) => setBgm(e.target.checked)} /> Background music
          </label>
          {showResume && (
            <>
              <label title="Force narration audio to regenerate even if a file already exists for it (e.g. after fixing a narration line). The previous take is archived first, never lost -- see 'Previous audio takes' below.">
                <input type="checkbox" checked={overwriteAudio} onChange={(e) => setOverwriteAudio(e.target.checked)} /> Regenerate audio
              </label>
              <label title="If a previous audio run was interrupted, re-verify the most recent audio file plus the previous 5 (archived first, then regenerated).">
                <input type="checkbox" checked={resume} onChange={(e) => setResume(e.target.checked)} /> Resume (re-verify last 5 audio)
              </label>
            </>
          )}
          {showSkipAudio && (
            <label title="Skip narration audio generation entirely and just re-render + re-join using whatever audio already exists.">
              <input type="checkbox" checked={skipAudio} onChange={(e) => setSkipAudio(e.target.checked)} /> Regenerate video only
            </label>
          )}
          {showOcrForce && (
            <label>
              <input type="checkbox" checked={ocrForce} onChange={(e) => setOcrForce(e.target.checked)} /> Redo all OCR
            </label>
          )}
          {showRenderWorkers && (
            <label title="Render this many item folders in parallel. Consumer NVIDIA GPUs typically cap at ~3 concurrent NVENC encode sessions, so going much higher won't add throughput.">
              Parallel render workers
              <input
                type="number"
                min={1}
                max={8}
                style={{ width: 50 }}
                value={renderWorkers}
                onChange={(e) => setRenderWorkers(Math.max(1, Number(e.target.value)))}
              />
            </label>
          )}
          {showGpuWorkers && (
            <label title="Run this many TTS worker processes in parallel, each loading its own model copy. Multiplies VRAM use by this count — only raise it on a GPU with headroom (e.g. 24GB+).">
              GPU audio workers
              <input
                type="number"
                min={1}
                max={8}
                style={{ width: 50 }}
                value={gpuWorkers}
                onChange={(e) => setGpuWorkers(Math.max(1, Number(e.target.value)))}
              />
            </label>
          )}
        </div>
        {bgm && (
          <div className="row" style={{ marginTop: 4, alignItems: 'center' }}>
            <p className="hint mono" style={{ flex: 1, margin: 0 }}>
              {bgmFile || 'No background music file selected'}
            </p>
            <button onClick={browseBgm}>Browse…</button>
            <label title="Background music loudness in dB. More negative = quieter.">
              Volume (dB)
              <input
                type="number"
                style={{ width: 70 }}
                value={bgmVolumeDb ?? -25}
                onChange={(e) => setBgmVolume(Number(e.target.value))}
              />
            </label>
          </div>
        )}
      </div>

      <div className="section">
        <h3>Output</h3>
        <div className="row">
          <input className="flex-1 mono" type="text" value={outDir} onChange={(e) => setOutDir(e.target.value)} />
          <button
            onClick={async () => {
              const picked = await window.api.pickDir()
              if (picked) setOutDir(picked)
            }}
          >
            Browse…
          </button>
        </div>
        <p className="hint">
          Reusable files: {paths.projectOutputDir || `${outDir}/<manga name>`} (audio cache under {paths.audioRoot || '…'})
        </p>
      </div>

      <div className="section">
        <h3>Previous audio takes</h3>
        <p className="hint">
          Audio is never overwritten silently — regenerating or clearing it archives the previous take first, so you
          can pick an older one back up instead of generating a new one.
        </p>
        <div className="row">
          <button onClick={refreshTakes} disabled={!mangaPath || takesLoading}>
            {takesLoading ? 'Loading…' : 'Refresh takes'}
          </button>
          {takes && (
            <span className="hint">
              Active: {takes.active.total_files} file(s)
              {Object.keys(takes.active.items).length > 0 &&
                ` (${Object.entries(takes.active.items).map(([item, n]) => `${item}: ${n}`).join(', ')})`}
            </span>
          )}
        </div>
        {takes && takes.runs.length === 0 && <p className="hint">No archived takes yet.</p>}
        {takes && takes.runs.length > 0 && (
          <table className="mono" style={{ width: '100%', marginTop: 8 }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>Take</th>
                <th style={{ textAlign: 'left' }}>Archived</th>
                <th style={{ textAlign: 'left' }}>Files</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {[...takes.runs].reverse().map((run) => (
                <tr key={run.run}>
                  <td>{run.run}</td>
                  <td>{new Date(run.archived_at).toLocaleString()}</td>
                  <td>
                    {Object.entries(run.items)
                      .map(([item, n]) => `${item}: ${n}`)
                      .join(', ')}
                  </td>
                  <td>
                    <button onClick={() => restoreTake(run.run)} disabled={running}>
                      Restore
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="row">
        <button className="primary" onClick={start} disabled={running || !mangaPath}>
          ▶ Start
        </button>
      </div>
    </div>
  )
}
