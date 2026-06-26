/**
 * Single-job state shared across every view — mirrors the Python app's
 * global `state["job"]` + `start_buttons[]`/`stop_buttons[]` mutex (only one
 * job at a time; every "Start" button disables together while one runs).
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { CliCommand, JobProgress } from '../../shared/types'

interface JobContextValue {
  running: boolean
  progress: JobProgress | null
  lastExitCode: number | null
  runStartedAt: number | null
  run: (command: string, args?: string[]) => Promise<number>
  runChain: (commands: CliCommand[]) => Promise<number>
  stop: () => Promise<void>
}

const JobContext = createContext<JobContextValue | null>(null)

export function JobProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<JobProgress | null>(null)
  const [lastExitCode, setLastExitCode] = useState<number | null>(null)
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null)
  const runningRef = useRef(false)

  useEffect(() => {
    return window.api.onJobProgress((p) => setProgress(p))
  }, [])

  const guard = useCallback(async (fn: () => Promise<number>): Promise<number> => {
    if (runningRef.current) {
      throw new Error('A job is already running.')
    }
    runningRef.current = true
    setRunning(true)
    setProgress(null)
    setRunStartedAt(Date.now())
    try {
      const code = await fn()
      setLastExitCode(code)
      return code
    } finally {
      runningRef.current = false
      setRunning(false)
    }
  }, [])

  const run = useCallback((command: string, args: string[] = []) => guard(() => window.api.runCli(command, args)), [guard])

  const runChain = useCallback((commands: CliCommand[]) => guard(() => window.api.runChain(commands)), [guard])

  const stop = useCallback(async () => {
    await window.api.terminateJob()
  }, [])

  return (
    <JobContext.Provider value={{ running, progress, lastExitCode, runStartedAt, run, runChain, stop }}>
      {children}
    </JobContext.Provider>
  )
}

export function useJob(): JobContextValue {
  const ctx = useContext(JobContext)
  if (!ctx) throw new Error('useJob() must be used inside <JobProvider>')
  return ctx
}
