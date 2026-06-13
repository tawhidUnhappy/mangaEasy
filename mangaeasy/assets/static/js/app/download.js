/* download.js — unified chapter download: single chapter or range.
   Single source of truth for all download UI and API calls.
   Handles the mode toggle, run/stop buttons, and status display in Step 1. */

import { $, api, appendLog } from "./core.js";
import { pollStatus } from "./status.js";

let _mode = "single";

// ── Public API ────────────────────────────────────────────────────────────────

export function initDownload() {
  document.querySelectorAll(".dl-tab").forEach((btn) =>
    btn.addEventListener("click", () => _setMode(btn.dataset.dlmode))
  );
  $("dl-run").addEventListener("click", _run);
  $("dl-stop").addEventListener("click", async () => {
    try { await api("/api/stop", { method: "POST" }); }
    catch (err) { appendLog("", err.message); }
  });
}

/** Called by status.js on every poll to keep buttons in sync with job state. */
export function updateDownloadUI(jobRunning, job) {
  const isBatch = jobRunning && job && job.kind === "batch-download";
  const runBtn  = $("dl-run");
  const stopBtn = $("dl-stop");

  if (runBtn)  runBtn.disabled  = jobRunning;
  if (stopBtn) stopBtn.disabled = !isBatch;

  if (isBatch)       _setStatus(`${job.name}…`);
  else if (!jobRunning) _setStatus("");
}

// ── Mode toggle ───────────────────────────────────────────────────────────────

function _setMode(mode) {
  _mode = mode;
  document.querySelectorAll(".dl-tab").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.dlmode === mode)
  );
  const panel = $("dl-range-fields");
  if (panel) panel.classList.toggle("hidden", mode !== "range");
  if ($("dl-run"))
    $("dl-run").textContent = mode === "range" ? "⬇ Download range" : "⬇ Download";
}

// ── Download action ───────────────────────────────────────────────────────────

function _isFresh() {
  const cb = $("dl-fresh");
  return cb ? cb.checked : false;
}

async function _run() {
  const fresh = _isFresh();

  if (_mode === "single") {
    // Flush chapter + language to config.json before spawning the job,
    // because the debounced auto-save in workflow.js may not have fired yet.
    try {
      await api("/api/workflow", {
        method: "POST",
        body: JSON.stringify({
          chapter:  parseInt($("wf-chapter").value, 10) || 1,
          language: $("wf-lang").value,
        }),
      });
      // Pass --fresh to the CLI when the checkbox is checked.
      const args = fresh ? ["--fresh"] : [];
      await api("/api/run", {
        method: "POST",
        body: JSON.stringify({ command: "download", args }),
      });
    } catch (err) {
      appendLog("", `download: ${err.message}`);
    }
  } else {
    const start = parseInt($("dl-from").value, 10) || 1;
    const end   = parseInt($("dl-to").value,   10) || start;
    try {
      await api("/api/workflow/batch-download", {
        method: "POST",
        body: JSON.stringify({ start, end, fresh }),
      });
      _setStatus(
        `ch ${String(start).padStart(2, "0")}–${String(end).padStart(2, "0")} queued…`
      );
    } catch (err) {
      appendLog("", `batch-download: ${err.message}`);
    }
  }
  pollStatus();
}

function _setStatus(text) {
  const el = $("dl-status");
  if (el) el.textContent = text;
}
