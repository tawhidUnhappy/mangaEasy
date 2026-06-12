/* setup.js — Setup tab: prerequisite checks and AI tool installs. */

import { $, api, appendLog, store } from "./core.js";

const PREREQ_LABELS = {
  git: "Git", uv: "uv", uvx: "uvx",
  ffmpeg: "FFmpeg", ffprobe: "FFprobe", "nvidia-smi": "NVIDIA GPU",
};

export async function loadDoctor() {
  let report;
  try {
    report = await api("/api/doctor");
  } catch (err) {
    appendLog("", `doctor failed: ${err.message}`);
    return;
  }

  $("tools-home").textContent = `Tools folder: ${report.tools_home}`;

  const grid = $("prereq-grid");
  grid.innerHTML = "";
  for (const [exe, where] of Object.entries(report.executables)) {
    const optional = exe === "nvidia-smi";
    const cls = where ? "ok" : optional ? "na" : "bad";
    grid.insertAdjacentHTML("beforeend",
      `<div class="prereq" title="${where || "not found on PATH"}">
         <span class="dot ${cls}"></span>${PREREQ_LABELS[exe] || exe}</div>`);
  }
  grid.insertAdjacentHTML("beforeend",
    `<div class="prereq"><span class="dot ${report.git_lfs ? "ok" : "bad"}"></span>git-lfs</div>`);

  const cards = $("tool-cards");
  cards.innerHTML = "";
  for (const [key, info] of Object.entries(report.tools)) {
    let badge, action = "";
    if (info.installed) {
      badge = `<span class="badge installed">installed</span>`;
      action = `<button class="btn small" data-install="${key}" ${store.jobRunning ? "disabled" : ""}>Reinstall</button>`;
    } else if (!info.configured) {
      badge = `<span class="badge unconfigured">repo URL not set</span>`;
    } else {
      badge = `<span class="badge missing">not installed</span>`;
      action = `<button class="btn primary" data-install="${key}" ${store.jobRunning ? "disabled" : ""}>Install</button>`;
    }
    cards.insertAdjacentHTML("beforeend",
      `<div class="card">
         <div class="info">
           <div class="title">${info.title}<span class="key">${key}</span></div>
           <div class="desc">${info.notes}</div>
           ${info.path ? `<div class="path">${info.path}</div>` : ""}
         </div>
         ${badge}${action}
       </div>`);
  }

  cards.querySelectorAll("[data-install]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.dataset.install;
      btn.disabled = true;
      try {
        await api(`/api/install-tool/${name}`, {
          method: "POST",
          body: JSON.stringify({
            cpu: $("opt-cpu").checked,
            skip_model: $("opt-skip-model").checked,
          }),
        });
        appendLog("", `install started: ${name} (watch the logs below)`);
      } catch (err) {
        appendLog("", `install failed to start: ${err.message}`);
        btn.disabled = false;
      }
    });
  });
}

export function initSetup() {
  $("doctor-refresh").addEventListener("click", loadDoctor);
}
