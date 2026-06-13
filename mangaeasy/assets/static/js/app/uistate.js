/* uistate.js — remembers UI field values (folders, step, options) across
   launches via /api/appstate. */

import { $, api } from "./core.js";

const PERSIST_VALUES = [
  "run-output-dir", "run-step", "run-tts",
  "run-encoder", "run-device", "run-items", "run-name",
];
const PERSIST_CHECKS = ["run-long", "run-normalize", "run-ow-audio", "run-ow-video", "wf-normalize"];

let saveTimer = null;

function scheduleSaveState() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const ui = {};
    for (const id of PERSIST_VALUES) ui[id] = $(id).value;
    for (const id of PERSIST_CHECKS) ui[id] = $(id).checked;
    try {
      await api("/api/appstate", { method: "POST", body: JSON.stringify(ui) });
    } catch { /* persistence is best-effort */ }
  }, 400);
}

export function initUiState() {
  for (const id of [...PERSIST_VALUES, ...PERSIST_CHECKS]) {
    $(id).addEventListener("change", scheduleSaveState);
  }
}

export async function loadUiState() {
  try {
    const { ui } = await api("/api/appstate");
    for (const id of PERSIST_VALUES) if (ui[id] != null) $(id).value = ui[id];
    for (const id of PERSIST_CHECKS) if (ui[id] != null) $(id).checked = !!ui[id];
  } catch { /* fresh defaults are fine */ }
}
