/**
 * Subprocess spawning over a real pseudo-console (node-pty), replacing
 * Python `jobs.py`'s plain-pipe `subprocess.Popen` for the Electron rewrite.
 *
 * A real PTY gives us, for free, everything jobs.py had to hand-roll on the
 * Python side this session: \r-only progress (tqdm/ffmpeg) flows through
 * immediately (no line-buffered `for line in stream` trap), and \n -> \r\n
 * translation happens in the pty's own line discipline, so a child emitting
 * bare \n (Python's print(), etc.) still renders as a proper new line in
 * xterm.js without any manual rewriting.
 */
import { execFile } from 'child_process'
import * as pty from 'node-pty'
import treeKill from 'tree-kill'

export type OnData = (chunk: string) => void

/** Renders a command for the echoed "$ ..." line — any argument containing
 * a newline (lyrics/reference-song/prompt text passed as a raw CLI arg) is
 * redacted to a length placeholder instead of printed verbatim. Printing it
 * as-is broke badly: this echo line is written directly to the pty, not
 * through a child process's own stdout, so the pty's normal \n -> \r\n
 * line-discipline translation never applies to it — every embedded \n just
 * advanced the cursor down without resetting its column, producing an
 * increasingly indented staircase instead of separate lines. Hiding multi-
 * line args entirely (rather than trying to reformat them) is both simpler
 * and avoids dumping a whole song's lyrics into the terminal pane. */
function formatCmdForDisplay(cmd: string[]): string {
  return cmd
    .map((arg) => (arg.includes('\n') ? `<text, ${arg.length} chars>` : arg))
    .join(' ')
}

interface CurrentJob {
  pid: number
}

let currentJob: CurrentJob | null = null
// Fire-and-forget jobs (e.g. ACE-Step's Gradio UI) that don't take the
// single-job lock but still need to die on app quit — mirrors jobs.py's
// `spawn_background`, tracked separately from `currentJob`.
const detachedPids = new Set<number>()

// Named subset of `detachedPids` — long-lived GUI processes (ACE-Step's
// and Z-Image's Gradio UIs) that the Setup view needs to individually stop
// on demand, as opposed to one-shot background jobs that just run until
// they exit on their own.
const namedDetached = new Map<string, number>()

// Every live pty, keyed by pid — needed so `setTerminalSize` can resize
// whichever job(s) happen to be running right now to match xterm.js's
// actual viewport. Without this, every pty spawns at a fixed size (cols
// below) regardless of how wide the Terminal pane really is: a child's own
// \r-redrawn progress line gets wrapped by Conpty at the wrong width, and
// xterm.js then only clears the first wrapped row on each redraw, leaving
// stale continuation rows behind it — i.e. exactly the "looks duplicated"
// symptom a real terminal was supposed to not have.
const livePtys = new Map<number, pty.IPty>()

// Updated by the renderer's xterm.js `onResize` (see Terminal.tsx) via the
// `terminal:resize` IPC channel — used as the spawn size for every new pty
// after the first resize report comes in.
let currentSize = { cols: 200, rows: 50 }

export function setTerminalSize(cols: number, rows: number): void {
  currentSize = { cols, rows }
  for (const proc of livePtys.values()) {
    proc.resize(cols, rows)
  }
}

function trackedSpawn(
  file: string,
  args: string[],
  cwd: string,
  env: Record<string, string | undefined>
): pty.IPty {
  const proc = pty.spawn(file, args, { cwd, cols: currentSize.cols, rows: currentSize.rows, env })
  livePtys.set(proc.pid, proc)
  proc.onExit(() => livePtys.delete(proc.pid))
  return proc
}

export function isJobRunning(): boolean {
  return currentJob !== null
}

/** Runs `cmd` to completion, streaming PTY output via `onData`, and
 * resolves with the exit code. Only one job at a time — mirrors jobs.py's
 * single-job lock (`state.set_current_job`/`is_job_running`). */
export function runBlocking(cmd: string[], cwd: string, onData: OnData): Promise<number> {
  if (currentJob) {
    throw new Error('A job is already running.')
  }
  const [file, ...args] = cmd
  return new Promise((resolve) => {
    const proc = trackedSpawn(file, args, cwd, process.env)
    currentJob = { pid: proc.pid }
    onData(`$ (cwd=${cwd}) ${formatCmdForDisplay(cmd)}\r\n`)
    proc.onData((chunk) => onData(chunk))
    proc.onExit(({ exitCode }) => {
      currentJob = null
      resolve(exitCode)
    })
  })
}

/** Like `runBlocking`, but doesn't take the single-job lock and doesn't wait
 * for exit — for long-lived background services (ACE-Step's Gradio UI) that
 * should keep running while one-shot jobs come and go independently.
 * Mirrors jobs.py's `spawn_background`. */
export function spawnDetached(
  cmd: string[],
  cwd: string,
  onData: OnData,
  extraEnv?: Record<string, string>,
  name?: string
): number {
  const [file, ...args] = cmd
  const proc = trackedSpawn(file, args, cwd, { ...process.env, ...extraEnv })
  detachedPids.add(proc.pid)
  if (name) namedDetached.set(name, proc.pid)
  onData(`$ (cwd=${cwd}) ${formatCmdForDisplay(cmd)}\r\n`)
  proc.onData((chunk) => onData(chunk))
  proc.onExit(() => {
    detachedPids.delete(proc.pid)
    if (name && namedDetached.get(name) === proc.pid) namedDetached.delete(name)
  })
  return proc.pid
}

export function isNamedGuiRunning(name: string): boolean {
  return namedDetached.has(name)
}

/** Stops a named GUI process (see `spawnDetached`'s `name` param) — no-op
 * if it's not running. Used by the Setup view's "Stop UI" buttons. */
export function stopNamedGui(name: string): Promise<void> {
  const pid = namedDetached.get(name)
  if (pid === undefined) return Promise.resolve()
  namedDetached.delete(name)
  return killProcessTree(pid)
}

/** Kills the currently running job's whole process tree, not just the
 * immediate child — same reasoning as jobs.py's `terminate_tree` (a `uv run
 * <entry>`-style invocation forks descendants a plain kill wouldn't reach). */
export function terminateCurrentJob(): void {
  if (!currentJob) return
  treeKill(currentJob.pid)
  currentJob = null
}

/** Kills an arbitrary tracked detached job's process tree by pid — used by
 * create-pipeline.ts to explicitly shut down the ACE-Step API server right
 * after a generation finishes (frees the GPU before Demucs/WhisperX run
 * next), same as jobs.py's `terminate_tree(server_proc)` call.
 *
 * Returns a promise that resolves once `taskkill` itself has finished, not
 * just once it's been *requested* — tree-kill's callback-less form (the
 * earlier version of this function) returns the instant the kill is fired
 * off, before the target process (and the GPU driver's reclaim of its CUDA
 * context) has actually finished tearing down. That race let the very next
 * GPU-heavy step (Z-Image-Turbo loading right after ACE-Step's ~8GB server)
 * start while the old process was still mid-exit and still holding VRAM,
 * producing a CUDA OOM that had nothing to do with how much memory either
 * step needed on its own. */
export function killProcessTree(pid: number): Promise<void> {
  detachedPids.delete(pid)
  return new Promise((resolve) => {
    treeKill(pid, () => resolve())
  })
}

/** Graceful-quit cleanup — kills every tracked job (the current one, if
 * any, and any detached background ones) so nothing keeps holding the GPU
 * after the window closes. See index.ts's `before-quit` handler. */
export function terminateAllJobs(): void {
  terminateCurrentJob()
  for (const pid of detachedPids) {
    treeKill(pid)
  }
  detachedPids.clear()
}

/** One-shot, non-PTY call for commands that just print something and exit
 * (e.g. `aisongtool doctor`'s JSON) — no need to stream this one through
 * the terminal pane. */
export function runCapture(cmd: string[], cwd: string): Promise<string> {
  const [file, ...args] = cmd
  return new Promise((resolve, reject) => {
    execFile(file, args, { cwd }, (error, stdout) => {
      if (error) {
        reject(error)
        return
      }
      resolve(stdout)
    })
  })
}
