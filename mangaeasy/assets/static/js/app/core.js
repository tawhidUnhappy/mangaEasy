/* core.js — shared helpers: DOM lookup, API fetch, log console, global flags. */

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

/* ── Log console (SSE) ─────────────────────────────────────────────────── */

let logLines = null;

export function appendLog(ts, msg) {
  if (!logLines) return;
  const div = document.createElement("div");
  div.className = "log-line";
  if (/\b(error|failed|fatal)\b/i.test(msg)) div.classList.add("err");
  else if (/\[warn\]|warning/i.test(msg)) div.classList.add("warn");
  const tsSpan = document.createElement("span");
  tsSpan.className = "ts";
  tsSpan.textContent = ts;
  div.appendChild(tsSpan);
  div.appendChild(document.createTextNode(msg));
  logLines.appendChild(div);
  while (logLines.childNodes.length > 2000) logLines.removeChild(logLines.firstChild);
  logLines.scrollTop = logLines.scrollHeight;
}

export function initLogConsole() {
  logLines = $("log-lines");

  const events = new EventSource("/log_stream");
  events.onmessage = (e) => {
    try {
      const entry = JSON.parse(e.data);
      if (entry.ping) return;
      appendLog(entry.ts, entry.msg);
    } catch { /* ignore malformed entries */ }
  };

  $("log-clear").addEventListener("click", () => (logLines.innerHTML = ""));
  $("console-toggle").addEventListener("click", () => {
    const c = $("console");
    c.classList.toggle("collapsed");
    $("console-toggle").textContent = c.classList.contains("collapsed") ? "Show" : "Hide";
  });
}
