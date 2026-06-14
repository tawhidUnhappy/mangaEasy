/* status.js — polls /api/status and updates the job indicator, run buttons,
   editor cards, and tool cards. */

import { $, api, store, updateProgress, clearProgress, startProgressTimer } from "./core.js";
import { loadDoctor } from "./setup.js";
import { updateEditors } from "./editors.js";
import { refreshWorkflow } from "./workflow.js";
import { loadChapters } from "./chapters.js";
import { updateDownloadUI } from "./download.js";

export async function pollStatus() {
  let st;
  try { st = await api("/api/status"); } catch { return; }

  const wasRunning = store.jobRunning;
  store.jobRunning = !!(st.job && st.job.running);

  const ind = $("job-indicator");
  if (store.jobRunning) {
    ind.className = "busy";
    ind.dataset.jobName = `${st.job.kind}: ${st.job.name}`;
    ind.textContent = ind.dataset.jobName;
    // Show indeterminate bar when a job starts; start the elapsed-time clock.
    if (!wasRunning) { startProgressTimer(); updateProgress(0, 0); }
  } else {
    ind.className = "idle";
    ind.textContent = "idle";
    delete ind.dataset.jobName;
    // Clear bar 1.5 s after the job finishes so the user sees it complete.
    if (wasRunning) setTimeout(clearProgress, 1500);
  }

  $("run-start").disabled = store.jobRunning;
  $("chap-run").disabled  = store.jobRunning;
  $("run-stop").disabled  = !(store.jobRunning && st.job && st.job.kind === "run");
  updateDownloadUI(store.jobRunning, st.job);

  if (store.jobRunning && st.job && st.job.kind === "run") {
    $("run-status").textContent = `running: ${st.job.name}…`;
  } else if (wasRunning && !store.jobRunning) {
    $("run-status").textContent = "finished — see the log below ✓";
    setTimeout(() => {
      if (!store.jobRunning) $("run-status").textContent = "";
    }, 8000);
  }

  updateEditors(st.editors);

  // Always refresh workflow so panel/page/narration counts stay current even
  // when no job is running (e.g. after saving panels in the editor, which is
  // a separate process and never triggers the job-finished event).
  refreshWorkflow();

  // Heavier refreshes only when a job just finished.
  if (wasRunning && !store.jobRunning) {
    loadDoctor();
    loadChapters();
  }
}
