/* core.js — shared helpers: DOM lookup, API fetch, progress bar, SSE events. */

import { write as termWrite } from "./terminal.js";

export const $ = (id) => document.getElementById(id);

/* Mutable flags shared across modules. */
export const store = { jobRunning: false };

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

/* appendLog: writes app-level JS messages to the xterm terminal.
   Backend job output arrives via WebSocket directly (no DOM needed). */
export function appendLog(ts, msg) {
  const prefix = ts ? `\x1b[2m[${ts}]\x1b[0m ` : "\x1b[2m[app]\x1b[0m ";
  termWrite(prefix + msg + "\r\n");
}

/* ── Progress bar ──────────────────────────────────────────────────────── */

let _progressStart = null;
let _lastProg = { value: 0, total: 0, label: "" };

function _fmtTime(secs) {
  secs = Math.max(0, Math.floor(secs));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function startProgressTimer() {
  _progressStart = Date.now();
}

export function updateProgress(value, total, label = "") {
  _lastProg = { value, total, label };
  _renderProgress();
}

function _renderProgress() {
  const { value, total, label } = _lastProg;
  const wrap = $("job-progress-wrap");
  const fill = $("job-progress-fill");
  const info = $("job-progress-label");
  if (!wrap) return;

  wrap.style.display = "";

  if (total > 0) {
    wrap.classList.remove("indeterminate");
    const pct = Math.min(100, Math.round((value / total) * 100));
    fill.style.width = pct + "%";

    if (info) {
      info.style.display = "";
      let parts = [];
      if (label) parts.push(label);
      parts.push(`${value}/${total} (${pct}%)`);
      if (_progressStart && value > 0) {
        const elapsed = (Date.now() - _progressStart) / 1000;
        parts.push(_fmtTime(elapsed) + " elapsed");
        if (value < total) {
          const rate = value / elapsed;
          parts.push("~" + _fmtTime((total - value) / rate) + " left");
        }
      }
      info.textContent = parts.join("  ·  ");
    }
  } else {
    wrap.classList.add("indeterminate");
    fill.style.width = "35%";
    if (info) {
      info.style.display = "";
      let parts = [label || "working…"];
      if (_progressStart) {
        const elapsed = (Date.now() - _progressStart) / 1000;
        parts.push(_fmtTime(elapsed) + " elapsed");
      }
      info.textContent = parts.join("  ·  ");
    }
  }
}

/* Tick the elapsed time every second without waiting for new SSE events. */
setInterval(() => { if (_progressStart) _renderProgress(); }, 1000);

export function clearProgress() {
  _progressStart = null;
  _lastProg = { value: 0, total: 0, label: "" };
  const wrap = $("job-progress-wrap");
  const fill = $("job-progress-fill");
  const info = $("job-progress-label");
  if (wrap) { wrap.style.display = "none"; wrap.classList.remove("indeterminate"); }
  if (fill) fill.style.width = "0%";
  if (info) info.style.display = "none";
}

/* ── SSE — progress + action events only (no DOM log) ─────────────────── */

export function initSSE() {
  const events = new EventSource("/log_stream");
  let _firstOpen = true;

  events.onerror = () => {};
  events.onopen = () => {
    if (!_firstOpen) window.dispatchEvent(new CustomEvent("sse-reconnect"));
    _firstOpen = false;
  };

  events.onmessage = (e) => {
    try {
      const entry = JSON.parse(e.data);
      if (entry.ping) return;

      if (entry.action === "restart-app") {
        appendLog("", "[app] Restarting…");
        fetch("/api/restart", { method: "POST" }).catch(() => {});
        setTimeout(() => location.reload(), 3000);
        return;
      }
      if (entry.action) {
        window.dispatchEvent(new CustomEvent("sse-action", { detail: entry.action }));
        return;
      }
      if (entry.progress) {
        updateProgress(entry.progress.value, entry.progress.total, entry.progress.label);
        return;
      }
      // Legacy log lines (if any) — write to terminal
      if (entry.ts !== undefined && entry.msg !== undefined) {
        appendLog(entry.ts, entry.msg);
      }
    } catch { /* ignore malformed */ }
  };
}
