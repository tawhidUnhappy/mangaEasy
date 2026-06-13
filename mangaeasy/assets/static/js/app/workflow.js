/* workflow.js — "Make a video" tab: guided chapter flow
   download → crop → narrate → generate, with per-step progress badges. */

import { $, api, appendLog, store } from "./core.js";
import { pollStatus } from "./status.js";

let wf = null;          // last /api/workflow payload
let saveTimer = null;

function fields() {
  return {
    chapter: parseInt($("wf-chapter").value, 10) || 1,
    language: $("wf-lang").value,
  };
}

function setBadge(id, done, doneText, todoText) {
  const el = $(id);
  el.textContent = done ? doneText : todoText;
  el.className = `badge wf-badge ${done ? "installed" : "unconfigured"}`;
}

function updateMangaSummary(data) {
  const p = $("wf-manga-summary");
  if (!p) return;
  if (data.name || data.manga_id) {
    const nameStr = data.name ? `<b>${data.name}</b>` : "(unnamed)";
    const idStr = data.manga_id ? ` &nbsp;·&nbsp; <span class="mono" style="font-size:11px">${data.manga_id}</span>` : "";
    p.innerHTML = `Manga: ${nameStr}${idStr} &nbsp;<button class="btn small tab-link" data-tab="project">Edit in Project ↗</button>`;
  } else {
    p.innerHTML = `No manga configured yet — <button class="btn small tab-link" data-tab="project">Set it in the Project tab ↗</button>`;
  }
}

function render(data) {
  wf = data;
  store.mangaDir = data.manga_dir || "";   // shared with run.js for batch pipeline

  // Keep the Batch tab's manga path display in sync.
  const runSummary = document.getElementById("run-manga-summary");
  if (runSummary)
    runSummary.textContent = data.manga_dir || (data.name ? `mangas/${data.name}` : "(no manga set)");

  updateMangaSummary(data);
  // Don't clobber fields the user is actively typing in.
  for (const [id, value] of [
    ["wf-chapter", data.chapter], ["wf-lang", data.language],
  ]) {
    if (document.activeElement !== $(id)) $(id).value = value;
  }

  const st = data.status || {};
  setBadge("wf-st-download", st.downloads > 0, `${st.downloads} pages ✓`, "no pages yet");
  setBadge("wf-st-panels", st.panels > 0, `${st.panels} panels ✓`, "no panels yet");
  setBadge("wf-st-narration", !!st.narration && st.narration_items > 0,
    `${st.narration_items} lines ✓`, "not written yet");
  const audioDone = st.audio > 0;
  setBadge("wf-st-generate", !!st.video,
    "video ready ✓", audioDone ? `${st.audio} audio clips, no video yet` : "nothing generated yet");

  if (data.paths) {
    $("wf-narration-name").textContent = data.paths.narration.split(/[\\/]/).pop();
  }
}

export async function refreshWorkflow() {
  try {
    render(await api("/api/workflow"));
  } catch { /* no project yet — badges stay empty */ }
}

async function save() {
  try {
    render(await api("/api/workflow", { method: "POST", body: JSON.stringify(fields()) }));
  } catch (err) {
    appendLog("", `workflow: ${err.message}`);
  }
}

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 400);
}

async function runSingle(command, args = []) {
  await save();
  try {
    await api("/api/run", { method: "POST", body: JSON.stringify({ command, args }) });
  } catch (err) {
    appendLog("", `run: ${err.message}`);
  }
  pollStatus();
}

async function runChain(commands) {
  await save();
  try {
    await api("/api/run-chain", {
      method: "POST",
      body: JSON.stringify({ steps: commands.map((command) => ({ command, args: [] })) }),
    });
  } catch (err) {
    appendLog("", `run: ${err.message}`);
  }
  pollStatus();
}

async function launchEditor(name) {
  await save();
  try {
    await api(`/api/editor/${name}/launch`, { method: "POST" });
  } catch (err) {
    appendLog("", err.message);
  }
  pollStatus();
}

function videoSteps() {
  const steps = ["fade-audio", "render-video"];
  if (wf && wf.bgm_set) steps.push("add-bgm");
  return steps;
}

export function initWorkflow() {
  for (const id of ["wf-chapter", "wf-lang"]) {
    $(id).addEventListener("change", scheduleSave);
  }

  // wf-download removed — download is owned by download.js
  $("wf-cut").addEventListener("click", () => launchEditor("cut-page"));
  $("wf-arrange").addEventListener("click", () => launchEditor("panel-editor"));
  $("wf-narrate").addEventListener("click", () => launchEditor("narration-editor"));
  $("wf-audio").addEventListener("click", () => runSingle("index-tts"));
  $("wf-video").addEventListener("click", () => runChain(videoSteps()));
  $("wf-all").addEventListener("click", () => runChain(["index-tts", ...videoSteps()]));

  document.querySelectorAll("[data-wf-open]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!wf || !wf.paths) {
        appendLog("", "workflow: set a manga name first.");
        return;
      }
      try {
        await api("/api/open-folder", {
          method: "POST",
          body: JSON.stringify({ path: wf.paths[btn.dataset.wfOpen], create: true }),
        });
      } catch (err) {
        appendLog("", `open folder: ${err.message}`);
      }
    }));
}
