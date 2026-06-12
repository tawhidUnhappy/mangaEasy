/* editors.js — Editors tab: launch/stop the web editors. */

import { $, api, appendLog } from "./core.js";
import { pollStatus } from "./status.js";

const EDITORS = [
  { key: "cut-page", title: "Cut Page", desc: "Cut downloaded pages into panels (with AI panel detection)." },
  { key: "panel-editor", title: "Panel Editor", desc: "Arrange panels for vertical manhwa / webtoons." },
  { key: "narration-editor", title: "Narration Editor", desc: "Write narration for the current chapter." },
  { key: "narration-editor-all", title: "Narration Editor (All)", desc: "Write narration across all chapters." },
  { key: "narration-review", title: "Narration Review", desc: "Review and QA narration before TTS." },
];

let editorState = {};

export function renderEditors() {
  const cards = $("editor-cards");
  cards.innerHTML = "";
  for (const ed of EDITORS) {
    const running = !!editorState[ed.key];
    cards.insertAdjacentHTML("beforeend",
      `<div class="card">
         <div class="info">
           <div class="title">${ed.title}<span class="key">${ed.key}</span></div>
           <div class="desc">${ed.desc}</div>
         </div>
         ${running ? `<span class="badge running">running</span>
                      <button class="btn small danger" data-ed-stop="${ed.key}">Stop</button>`
                   : `<button class="btn primary" data-ed-launch="${ed.key}">Launch</button>`}
       </div>`);
  }
  cards.querySelectorAll("[data-ed-launch]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await api(`/api/editor/${btn.dataset.edLaunch}/launch`, { method: "POST" }); }
      catch (err) { appendLog("", err.message); }
      pollStatus();
    }));
  cards.querySelectorAll("[data-ed-stop]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try { await api(`/api/editor/${btn.dataset.edStop}/stop`, { method: "POST" }); }
      catch (err) { appendLog("", err.message); }
      pollStatus();
    }));
}

/* Called by status polling — re-renders only when something changed. */
export function updateEditors(next) {
  const changed = JSON.stringify(next || {}) !== JSON.stringify(editorState);
  editorState = next || {};
  if (changed) renderEditors();
}
