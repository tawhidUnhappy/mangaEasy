/* project.js — Project tab: project folder, manga settings, music & voice. */

import { $, api, appendLog } from "./core.js";
import { pollStatus } from "./status.js";

export async function loadProject() {
  const data = await api("/api/config");
  $("project-root").value = data.root;

  const cfg = data.config || data.config_example || {};
  const dl = cfg.download || {};
  $("cfg-manga-id").value = dl.manga_id || "";
  $("cfg-name").value = dl.name || "";
  $("cfg-chapter").value = dl.chapter ?? 1;
  $("cfg-status").textContent = data.config ? "" : "config.json not found yet — Save creates it.";

  const sys = data.system || data.system_example || {};
  $("cfg-bgm").value = (sys.bgm || {}).file || "";
  $("cfg-voice").value = (sys.tts || {}).speaker_wav || "";
  $("syscfg").value = JSON.stringify(sys, null, 2);
  $("syscfg-status").textContent = data.system ? "" : "config.system.json not found yet — Save creates it.";
}

async function setProjectRoot() {
  try {
    await api("/api/project", { method: "POST", body: JSON.stringify({ root: $("project-root").value }) });
    await loadProject();
    await pollStatus();
  } catch (err) {
    appendLog("", `project: ${err.message}`);
  }
}

async function saveSettings() {
  const data = await api("/api/config");
  const cfg = data.config || data.config_example || {};
  cfg.download = {
    ...(cfg.download || {}),
    manga_id: $("cfg-manga-id").value.trim(),
    name: $("cfg-name").value.trim(),
    chapter: parseInt($("cfg-chapter").value, 10) || 1,
  };
  delete cfg._comment;

  // Music & voice live in config.system.json (read by both workflows).
  const sys = data.system || data.system_example || {};
  sys.bgm = { ...(sys.bgm || {}), file: $("cfg-bgm").value.trim() };
  sys.tts = { ...(sys.tts || {}), speaker_wav: $("cfg-voice").value.trim() };
  delete sys._comment;

  await api("/api/config", { method: "POST", body: JSON.stringify({ config: cfg, system: sys }) });
  $("syscfg").value = JSON.stringify(sys, null, 2);
  $("cfg-status").textContent = "saved ✓";
  setTimeout(() => ($("cfg-status").textContent = ""), 2500);
}

async function saveSystemConfig() {
  let parsed;
  try {
    parsed = JSON.parse($("syscfg").value);
  } catch (err) {
    $("syscfg-status").textContent = `invalid JSON: ${err.message}`;
    return;
  }
  delete parsed._comment;
  await api("/api/config", { method: "POST", body: JSON.stringify({ system: parsed }) });
  $("syscfg-status").textContent = "saved ✓";
  setTimeout(() => ($("syscfg-status").textContent = ""), 2500);
}

export function initProject() {
  $("project-set").addEventListener("click", setProjectRoot);
  // Browsing to a folder is a clear intent — apply it without a second click.
  $("project-root").addEventListener("change", setProjectRoot);
  $("cfg-save").addEventListener("click", saveSettings);
  $("syscfg-save").addEventListener("click", saveSystemConfig);
}
