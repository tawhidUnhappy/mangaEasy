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
  'video-add-bgm': 'Add background music to long video',
  'video-normalize-audio': 'Loudness-normalize joined audio',
  'video-clean-audio': 'Clear generated audio (kept, restorable later)',
  'video-clean-video': 'Delete rendered videos',
  'video-validate': 'Validate generated output',
  'video-clean-all': 'DELETE ALL generated output (start fresh)'
}

const AUDIO_STEPS = new Set(['video-check', 'video-validate', 'video-clean-audio'])
const OUTPUT_STEPS = new Set(['video-validate', 'video-clean-video'])

// One plain-language line per step, shown directly under the step picker so
// it's never ambiguous what clicking Start will actually do.
const STEP_DESCRIPTIONS: Record<string, string> = {
  video: 'Runs the full pipeline end to end: narration audio, rendered chapter videos, and (optionally) the joined long video with background music.',
  'video-check': 'Checks that panels, narration.json, and audio line up for each selected chapter. Nothing is generated.',
  'got-ocr2': 'Fills in missing narration text fields using OCR on the panel images.',
  'video-audio': 'Generates per-chapter narration audio with Kokoro TTS.',
  'video-audio-indextts': 'Generates per-chapter narration audio with IndexTTS.',
  'video-fade-audio': 'Copies narration audio with a tiny fade in/out to remove clicks. The raw audio is never deleted.',
  'video-render': 'Renders one video per chapter from panels + audio. Missing audio is generated first automatically.',
  'video-join': "Joins the rendered chapter videos into one long video. No background music here -- use 'Add background music' afterward.",
  'video-add-bgm': 'Mixes background music directly into the already-joined long video, without re-joining from chapter clips.',
  'video-normalize-audio': "Loudness-normalizes the joined long video's audio to YouTube's target level.",
  'video-clean-audio': 'Archives generated narration audio so it can be regenerated. Previous takes stay recoverable below.',
  'video-clean-video': 'Deletes rendered chapter videos.',
  'video-validate': 'Checks generated audio/video against the expected inputs and reports any mismatches.',
  'video-clean-all': 'Deletes ALL generated output for this manga (audio, videos, long video). Chapters/panels/narration are untouched.'
}

const DISABLED_STYLE: React.CSSProperties = { opacity: 0.45 }

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
      await run(step, args)
      return
    }

    if (step === 'video-add-bgm') {
      // A separate step from video-join on purpose: re-running the full join
      // (re-concatenating every item clip) just to change the music or its
      // volume is wasteful when only the music layer actually changed. This
      // mixes into the already-joined video directly, archiving the
      // previous one first the same way every other generation step does.
      if (!bgmFile) {
        window.alert('Set a background music file first (Browse… below).')
        return
      }
      const args = baseArgs({ output: true, items: false })
      if (!args) return
      args.push('--background-music', bgmFile)
      if (bgmVolumeDb !== null) args.push('--music-volume-db', String(bgmVolumeDb))
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

  // Whether each control actually does anything for the currently selected
  // step -- used to gray controls out (not hide them) so the panel doesn't
  // jump around as you switch steps, but it's still obvious at a glance
  // which settings this particular run will actually use.
  const usesTts = step === 'video'
  const usesAudioSource = ['video', 'video-render', 'video-join', 'video-check', 'video-validate', 'video-clean-audio'].includes(step)
  const usesItemRange = !['video-add-bgm', 'video-normalize-audio', 'video-clean-all'].includes(step)
  const usesLongVideoToggle = step === 'video'
  const usesNormalizeToggle = step === 'video'
  const usesBgmToggle = step === 'video'
  const usesBgmFields = (step === 'video' && bgm) || step === 'video-add-bgm'
  const usesResume = ['video', 'video-audio', 'video-audio-indextts'].includes(step)
  const usesSkipAudio = step === 'video'
  const usesOcrForce = step === 'got-ocr2'
  const usesRenderWorkers = ['video', 'video-render'].includes(step)
  const usesGpuWorkers = ['video', 'video-audio', 'video-audio-indextts'].includes(step)

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
        <div className="row" style={!usesItemRange ? DISABLED_STYLE : undefined}>
          <label title={!usesItemRange ? 'This step always works on the whole project, not a chapter range.' : undefined}>
            <input
              type="checkbox"
              checked={useRange}
              disabled={!usesItemRange}
              onChange={(e) => setUseRange(e.target.checked)}
            />{' '}
            Use chapter range
          </label>
          <label>
            From
            <input
              type="number"
              style={{ width: 60 }}
              disabled={!usesItemRange}
              value={rangeFrom}
              onChange={(e) => setRangeFrom(Number(e.target.value))}
            />
          </label>
          <label>
            To
            <input
              type="number"
              style={{ width: 60 }}
              disabled={!usesItemRange}
              value={rangeTo}
              onChange={(e) => setRangeTo(Number(e.target.value))}
            />
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
          <label style={!usesTts ? DISABLED_STYLE : undefined} title={!usesTts ? 'Only used by the Everything step -- other steps use a fixed engine implied by their name.' : undefined}>
            Voice engine
            <select value={tts} disabled={!usesTts} onChange={(e) => setTts(e.target.value as typeof tts)}>
              <option value="auto">Auto</option>
              <option value="indextts">IndexTTS</option>
              <option value="kokoro">Kokoro</option>
            </select>
          </label>
          <label
            style={!usesAudioSource ? DISABLED_STYLE : undefined}
            title={
              usesAudioSource
                ? 'Faded copies have tiny fade-in/out to remove clicks/pops. The raw audio is never deleted.'
                : "This step doesn't read narration audio."
            }
          >
            Audio source
            <select disabled={!usesAudioSource} value={audioSource} onChange={(e) => setAudioSource(e.target.value as typeof audioSource)}>
              <option value="raw">Raw audio</option>
              <option value="faded">Faded audio (de-click)</option>
            </select>
          </label>
        </div>
        <p className="hint" style={{ marginTop: 4 }}>{STEP_DESCRIPTIONS[step]}</p>
        <div className="row" style={{ marginTop: 8 }}>
          <label style={!usesLongVideoToggle ? DISABLED_STYLE : undefined} title={!usesLongVideoToggle ? "Only used by the Everything step. Use 'Join into one long video' to do this on its own." : undefined}>
            <input type="checkbox" disabled={!usesLongVideoToggle} checked={longVideo} onChange={(e) => setLongVideo(e.target.checked)} /> Generate one long video
          </label>
          <label style={!usesNormalizeToggle ? DISABLED_STYLE : undefined} title={!usesNormalizeToggle ? "Only used by the Everything step. Use 'Loudness-normalize joined audio' to do this on its own." : undefined}>
            <input type="checkbox" disabled={!usesNormalizeToggle} checked={normalize} onChange={(e) => setNormalize(e.target.checked)} /> YouTube loudness
          </label>
          <label style={!usesBgmToggle ? DISABLED_STYLE : undefined} title={!usesBgmToggle ? "Only used by the Everything step. Use 'Add background music to long video' to do this on its own, on an existing long video, without re-joining." : undefined}>
            <input type="checkbox" disabled={!usesBgmToggle} checked={bgm} onChange={(e) => setBgm(e.target.checked)} /> Background music
          </label>
          <label style={!usesResume ? DISABLED_STYLE : undefined} title={
            usesResume
              ? "Force narration audio to regenerate even if a file already exists for it (e.g. after fixing a narration line). The previous take is archived first, never lost -- see 'Previous audio takes' below."
              : "This step doesn't generate narration audio."
          }>
            <input type="checkbox" disabled={!usesResume} checked={overwriteAudio} onChange={(e) => setOverwriteAudio(e.target.checked)} /> Regenerate audio
          </label>
          <label style={!usesResume ? DISABLED_STYLE : undefined} title={
            usesResume
              ? "If a previous audio run was interrupted, re-verify the most recent audio file plus the previous 5 (archived first, then regenerated)."
              : "This step doesn't generate narration audio."
          }>
            <input type="checkbox" disabled={!usesResume} checked={resume} onChange={(e) => setResume(e.target.checked)} /> Resume (re-verify last 5 audio)
          </label>
          <label style={!usesSkipAudio ? DISABLED_STYLE : undefined} title={!usesSkipAudio ? 'Only used by the Everything step.' : 'Skip narration audio generation entirely and just re-render + re-join using whatever audio already exists.'}>
            <input type="checkbox" disabled={!usesSkipAudio} checked={skipAudio} onChange={(e) => setSkipAudio(e.target.checked)} /> Regenerate video only
          </label>
          <label style={!usesOcrForce ? DISABLED_STYLE : undefined} title={!usesOcrForce ? 'Only used by the OCR step.' : undefined}>
            <input type="checkbox" disabled={!usesOcrForce} checked={ocrForce} onChange={(e) => setOcrForce(e.target.checked)} /> Redo all OCR
          </label>
          <label style={!usesRenderWorkers ? DISABLED_STYLE : undefined} title={
            usesRenderWorkers
              ? 'Render this many item folders in parallel. Consumer NVIDIA GPUs typically cap at ~3 concurrent NVENC encode sessions, so going much higher won\'t add throughput.'
              : "This step doesn't render video."
          }>
            Parallel render workers
            <input
              type="number"
              min={1}
              max={8}
              style={{ width: 50 }}
              disabled={!usesRenderWorkers}
              value={renderWorkers}
              onChange={(e) => setRenderWorkers(Math.max(1, Number(e.target.value)))}
            />
          </label>
          <label style={!usesGpuWorkers ? DISABLED_STYLE : undefined} title={
            usesGpuWorkers
              ? 'Run this many TTS worker processes in parallel, each loading its own model copy. Multiplies VRAM use by this count — only raise it on a GPU with headroom (e.g. 24GB+).'
              : "This step doesn't generate narration audio."
          }>
            GPU audio workers
            <input
              type="number"
              min={1}
              max={8}
              style={{ width: 50 }}
              disabled={!usesGpuWorkers}
              value={gpuWorkers}
              onChange={(e) => setGpuWorkers(Math.max(1, Number(e.target.value)))}
            />
          </label>
        </div>
        <div className="row" style={{ marginTop: 4, alignItems: 'center', ...(!usesBgmFields ? DISABLED_STYLE : {}) }}>
          <p className="hint mono" style={{ flex: 1, margin: 0 }}>
            {bgmFile || 'No background music file selected'}
          </p>
          <button disabled={!usesBgmFields} onClick={browseBgm}>Browse…</button>
          <label title="Background music loudness in dB. More negative = quieter.">
            Volume (dB)
            <input
              type="number"
              style={{ width: 70 }}
              disabled={!usesBgmFields}
              value={bgmVolumeDb ?? -25}
              onChange={(e) => setBgmVolume(Number(e.target.value))}
            />
          </label>
        </div>
        {step === 'video-add-bgm' && (
          <p className="hint">
            Re-applies music to the existing long video directly — much faster than re-running the join when you
            just want to try a different track or volume.
          </p>
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
