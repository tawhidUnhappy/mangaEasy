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
let autoScroll = true;

export function appendLog(ts, msg) {
  if (!logLines) return;
  const div = document.createElement("div");
  div.className = "log-line";
  if (/\b(error|failed|fatal)\b/i.test(msg)) div.classList.add("err");
  else if (/\[warn\]|warning/i.test(msg)) div.classList.add("warn");
  const tsSpan = document.createElement("span");
  tsSpan.className = "ts";
  tsSpan.textContent = ts;
  const msgSpan = document.createElement("span");
  msgSpan.className = "msg";
  msgSpan.textContent = msg;
  div.appendChild(tsSpan);
  div.appendChild(msgSpan);
  logLines.appendChild(div);
  while (logLines.childNodes.length > 2000) logLines.removeChild(logLines.firstChild);
  if (autoScroll) logLines.scrollTop = logLines.scrollHeight;
}

export function initLogConsole() {
  logLines = $("log-lines");
  const statusEl = $("term-status");
  const scrollCb = $("term-autoscroll");

  if (scrollCb) {
    scrollCb.addEventListener("change", () => { autoScroll = scrollCb.checked; });
  }

  $("log-clear").addEventListener("click", () => (logLines.innerHTML = ""));

  $("log-copy").addEventListener("click", () => {
    const text = [...logLines.querySelectorAll(".log-line")]
      .map(el => {
        const ts  = el.querySelector(".ts")?.textContent  || "";
        const msg = el.querySelector(".msg")?.textContent || "";
        return ts ? `${ts}  ${msg}` : msg;
      })
      .join("\n");
    navigator.clipboard.writeText(text).then(() => {
      const btn = $("log-copy");
      const prev = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = prev; }, 1500);
    }).catch(() => {});
  });

  const events = new EventSource("/log_stream");
  events.onopen = () => { if (statusEl) statusEl.textContent = "connected"; };
  events.onerror = () => { if (statusEl) statusEl.textContent = "reconnecting…"; };
  events.onmessage = (e) => {
    try {
      const entry = JSON.parse(e.data);
      if (entry.ping) return;
      if (entry.action) {
        window.dispatchEvent(new CustomEvent("sse-action", { detail: entry.action }));
        return;
      }
      appendLog(entry.ts, entry.msg);
    } catch { /* ignore malformed entries */ }
  };
}
