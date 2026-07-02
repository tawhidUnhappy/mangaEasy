import { useEffect, useState } from 'react'
import { useJob } from '../job-context'

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '?'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

/** Global progress row (with a post-job success/failure line so a failed
 * run is visible even if the user wasn't watching the terminal). */
export function ProgressBar(): React.JSX.Element | null {
  const { running, progress, runStartedAt, lastExitCode, stop } = useJob()
  const [, setTick] = useState(0)
  const [statusDismissed, setStatusDismissed] = useState(false)

  useEffect(() => {
    if (!running) return
    setStatusDismissed(false)
    const id = setInterval(() => setTick((t) => t + 1), 500)
    return () => clearInterval(id)
  }, [running])

  if (!running) {
    if (lastExitCode === null || statusDismissed) return null
    const ok = lastExitCode === 0
    return (
      <div className={`job-status ${ok ? 'ok' : 'fail'}`}>
        <span>
          {ok
            ? 'Last job finished successfully.'
            : `Last job FAILED (exit code ${lastExitCode}) — scroll the terminal below for the error.`}
        </span>
        <button onClick={() => setStatusDismissed(true)} title="Dismiss">
          ✕
        </button>
      </div>
    )
  }

  const determinate = !!progress && progress.total > 0
  const pct = determinate ? Math.round((progress!.value / progress!.total) * 100) : 0
  let etaText = ''
  if (determinate && runStartedAt && progress!.value > 0) {
    const elapsed = (Date.now() - runStartedAt) / 1000
    const rate = progress!.value / elapsed
    const remaining = rate > 0 ? (progress!.total - progress!.value) / rate : NaN
    etaText = ` · ${formatDuration(elapsed)} elapsed · ~${formatDuration(remaining)} left`
  }

  return (
    <div className="progress-row">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span>
          {progress?.label ?? 'Working'}
          {determinate
            ? ` · ${progress!.value}/${progress!.total} (${pct}%)${etaText}`
            : ' · working…'}
        </span>
        <button className="negative" onClick={stop} style={{ padding: '2px 10px' }}>
          ■ Stop
        </button>
      </div>
      {determinate && (
        <div className="progress-bar-track">
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  )
}
