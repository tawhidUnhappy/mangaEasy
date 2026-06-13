/* status.js — polls /api/status and updates the job indicator, run buttons,
   editor cards, and tool cards. */

import { $, api, store } from "./core.js";
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
    ind.textContent = `${st.job.kind}: ${st.job.name}`;
  } else {
    ind.className = "idle";
    ind.textContent = "idle";
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

  // refresh tool cards, workflow progress, and chapter overview when any job finishes
  if (wasRunning && !store.jobRunning) {
    loadDoctor();
    refreshWorkflow();
    loadChapters();
  }
}
