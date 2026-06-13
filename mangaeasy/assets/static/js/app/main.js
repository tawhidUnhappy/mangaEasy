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
import { initRun, updateStepUI } from "./run.js";
import { renderEditors } from "./editors.js";
import { pollStatus } from "./status.js";

function switchTab(name) {
  const btn = document.querySelector(`.tab[data-tab="${name}"]`);
  if (btn) btn.click();
}

function initTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-page").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`tab-${btn.dataset.tab}`).classList.add("active");
    });
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
  initRun();
  renderEditors();

  await Promise.allSettled([
    loadDoctor(), loadProject(), refreshWorkflow(), loadUiState(), pollStatus(),
  ]);
  updateStepUI();
  setInterval(pollStatus, 2000);
})();
