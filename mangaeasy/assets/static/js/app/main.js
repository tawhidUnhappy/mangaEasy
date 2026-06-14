/* main.js — mangaEasy control center entry point.

   Modules:
     core.js     $ / api / log console / shared flags
     picker.js   folder & file Browse… (native dialog or in-app modal)
     uistate.js  remembered field values (/api/appstate)
     setup.js    Setup tab
     project.js  Project tab
     run.js      Create videos tab
     editors.js  Editors tab
     status.js   job status polling
*/

import { initLogConsole, $ } from "./core.js";
import { initPicker } from "./picker.js";
import { initUiState, loadUiState } from "./uistate.js";
import { initSetup, loadDoctor } from "./setup.js";
import { initProject, loadProject } from "./project.js";
import { initWorkflow, refreshWorkflow } from "./workflow.js";
import { initDownload } from "./download.js";
import { initRun, updateStepUI } from "./run.js";
import { initChapters, loadChapters } from "./chapters.js";
import { renderEditors } from "./editors.js";
import { pollStatus } from "./status.js";
import { initTerminal } from "./terminal.js";

function switchTab(name) {
  const btn = document.querySelector(`.tab[data-tab="${name}"]`);
  if (btn) btn.click();
}

function initTabs() {
  // Use event delegation on #tabs so dynamically added editor tabs (which
  // manage themselves in editors.js) don't need extra wiring here.
  const tabsNav = document.getElementById("tabs");
  tabsNav.addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn || btn.classList.contains("editor-tab")) return; // editors.js owns these
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-page").forEach(p => p.classList.remove("active"));
    // Return to main view when any non-editor tab is clicked
    document.querySelector("main").style.display = "";
    document.getElementById("editor-frames").classList.remove("active");
    document.querySelectorAll(".editor-frame-wrap").forEach(f => f.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`)?.classList.add("active");
    if (btn.dataset.tab === "run") loadChapters();
  });
  // Delegated handler for .tab-link buttons rendered by JS (e.g. workflow summary).
  document.addEventListener("click", (e) => {
    const el = e.target.closest(".tab-link[data-tab]");
    if (el) switchTab(el.dataset.tab);
  });
}

(async function init() {
  initLogConsole();
  initTabs();
  initPicker();
  initUiState();
  initSetup();
  initProject();
  initWorkflow();
  initDownload();
  initRun();
  initChapters();
  renderEditors();
  initTerminal();

  await Promise.allSettled([
    loadDoctor(), loadProject(), refreshWorkflow(), loadUiState(), pollStatus(), loadChapters(),
  ]);
  updateStepUI();
  setInterval(pollStatus, 2000);
})();
