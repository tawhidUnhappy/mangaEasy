/* chapters.js — chapter progress overview table + batch-download form. */

import { $, api, appendLog } from "./core.js";
import { pollStatus } from "./status.js";

function renderTable(data) {
  const grid = $("chapters-grid");
  const summary = $("chapters-summary");
  if (!grid) return;

  const chapters = data.chapters || [];

  if (chapters.length === 0) {
    const msg = data.name
      ? `No chapter folders found for <b>${data.name}</b> yet — download one first.`
      : "Set a manga name in the Project tab first.";
    grid.innerHTML = `<span class="ch-empty">${msg}</span>`;
    if (summary) summary.textContent = "";
    return;
  }

  const total      = chapters.length;
  const dlCnt      = chapters.filter(c => c.downloaded > 0).length;
  const panCnt     = chapters.filter(c => c.panels > 0).length;
  const audCnt     = chapters.filter(c => c.audio > 0).length;
  const vidCnt     = chapters.filter(c => c.video).length;
  const incomplete = chapters.filter(
    c => c.expected != null && c.downloaded < c.expected && c.downloaded > 0
  ).length;

  if (summary) {
    let text = `${total} chapters — ${dlCnt} downloaded · ${panCnt} cropped · ${audCnt} audio · ${vidCnt} video`;
    if (incomplete) text += ` · ⚠ ${incomplete} incomplete`;
    summary.textContent = text;
  }

  let html = `<div class="ch-table">
    <div class="ch-row ch-header">
      <span class="ch-num">Ch</span>
      <span class="ch-cell">Pages</span>
      <span class="ch-cell">Panels</span>
      <span class="ch-cell">Audio</span>
      <span class="ch-cell">Video</span>
      <span class="ch-cell"></span>
    </div>`;

  for (const ch of chapters) {
    const n   = String(ch.chapter).padStart(2, "0");
    const dl  = ch.downloaded > 0;
    const pan = ch.panels > 0;
    const aud = ch.audio > 0;
    const vid = ch.video;

    // Pages cell: show "X/Y ⚠" when we know the total and some are missing.
    const hasExpected = ch.expected != null && ch.expected > 0;
    const incomplete  = hasExpected && ch.downloaded < ch.expected;
    let pagesText, pagesCls;
    if (!dl) {
      pagesText = "—";
      pagesCls  = "";
    } else if (incomplete) {
      pagesText = `${ch.downloaded}/${ch.expected} ⚠`;
      pagesCls  = "warn";
    } else if (hasExpected) {
      pagesText = `${ch.downloaded} ✓`;
      pagesCls  = "done";
    } else {
      pagesText = String(ch.downloaded);
      pagesCls  = "done";
    }

    html += `<div class="ch-row">
      <span class="ch-num">${n}</span>
      <span class="ch-cell ${pagesCls}" title="${hasExpected ? ch.expected + " pages total" : "total unknown — download to cache metadata"}">${pagesText}</span>
      <span class="ch-cell ${pan ? "done" : ""}">${pan ? ch.panels     : "—"}</span>
      <span class="ch-cell ${aud ? "done" : ""}">${aud ? ch.audio      : "—"}</span>
      <span class="ch-cell ${vid ? "done" : ""}">${vid ? "✓"           : "—"}</span>
      <button class="ch-del-btn" data-ch="${ch.chapter}" title="Delete chapter data">🗑</button>
    </div>
    <div class="ch-del-row" id="chdel-${ch.chapter}">
      <span class="ch-del-label">ch ${n}:</span>
      <button class="btn small danger ch-del-action ${dl  ? "has-data" : ""}" data-ch="${ch.chapter}" data-what="download">Pages</button>
      <button class="btn small danger ch-del-action ${pan ? "has-data" : ""}" data-ch="${ch.chapter}" data-what="panels">Panels</button>
      <button class="btn small danger ch-del-action ${aud ? "has-data" : ""}" data-ch="${ch.chapter}" data-what="audio">Audio</button>
      <button class="btn small danger ch-del-action ${vid ? "has-data" : ""}" data-ch="${ch.chapter}" data-what="video">Video</button>
      <button class="btn small danger ch-del-action ch-del-all has-data" data-ch="${ch.chapter}" data-what="all">All</button>
      <button class="ch-del-cancel" data-ch="${ch.chapter}" title="Close">✕</button>
    </div>`;
  }

  html += "</div>";
  grid.innerHTML = html;
}

export async function loadChapters() {
  try {
    renderTable(await api("/api/workflow/chapters"));
  } catch { /* no project yet */ }
}

export function initChapters() {
  const refreshBtn = $("chapters-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", loadChapters);

  // Delegated delete handlers — on the persistent grid element so they
  // survive innerHTML replacements when the table re-renders.
  const grid = $("chapters-grid");
  if (grid) {
    grid.addEventListener("click", async (e) => {
      // Toggle the delete-options row for a chapter.
      const delBtn = e.target.closest(".ch-del-btn");
      if (delBtn) {
        const ch = delBtn.dataset.ch;
        const row = document.getElementById(`chdel-${ch}`);
        if (!row) return;
        // Close any other open delete rows first.
        grid.querySelectorAll(".ch-del-row.open").forEach((r) => {
          if (r.id !== `chdel-${ch}`) r.classList.remove("open");
        });
        row.classList.toggle("open");
        return;
      }

      // Cancel: close the delete-options row.
      const cancelBtn = e.target.closest(".ch-del-cancel");
      if (cancelBtn) {
        const row = document.getElementById(`chdel-${cancelBtn.dataset.ch}`);
        if (row) row.classList.remove("open");
        return;
      }

      // Delete action: call the API then refresh.
      const actionBtn = e.target.closest(".ch-del-action");
      if (actionBtn) {
        const ch   = actionBtn.dataset.ch;
        const what = actionBtn.dataset.what;
        try {
          await api(`/api/workflow/chapters/${ch}/delete`, {
            method: "POST",
            body: JSON.stringify({ what }),
          });
        } catch (err) {
          appendLog("", `delete ch${ch}: ${err.message}`);
        }
        await loadChapters();
      }
    });
  }

  // Batch download form (bdl-*) removed — download.js owns all download UI.
}
