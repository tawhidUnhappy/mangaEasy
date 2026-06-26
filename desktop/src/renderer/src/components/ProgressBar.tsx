import { useEffect, useState } from 'react'
import { useJob } from '../job-context'

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '?'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

/** Global progress row — ported from nicegui_app.py's `_status_tick`/ETA
 * calculation (elapsed * (total-value)/value), shown above the tab bar. */
export function ProgressBar(): React.JSX.Element | null {
  const { running, progress, runStartedAt, stop } = useJob()
  const [, setTick] = useState(0)

  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setTick((t) => t + 1), 500)
    return () => clearInterval(id)
  }, [running])

  if (!running) return null

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
          {determinate ? ` · ${progress!.value}/${progress!.total} (${pct}%)${etaText}` : ' · working…'}
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
