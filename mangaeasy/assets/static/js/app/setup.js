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

  // Show CUDA / torch status separately from nvidia-smi so the user can
  // tell the difference between "no GPU" and "GPU present but CPU-only torch".
  if (report.gpu) {
    const cudaOk = report.cuda;
    const label = cudaOk
      ? `CUDA · ${report.cuda_device || "GPU"}`
      : "CUDA torch not installed (gutter detection runs on CPU)";
    grid.insertAdjacentHTML("beforeend",
      `<div class="prereq" title="${cudaOk ? "torch.cuda.is_available() = True" : "torch.cuda.is_available() = False in mangaeasy env"}">
         <span class="dot ${cudaOk ? "ok" : "bad"}"></span>${label}
         ${cudaOk ? "" : `<button id="btn-install-cuda-torch" class="btn small" style="margin-left:8px" ${store.jobRunning ? "disabled" : ""}>Install CUDA torch</button>`}
       </div>`);

    if (!cudaOk) {
      document.getElementById("btn-install-cuda-torch")?.addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = "Installing…";
        try {
          await api("/api/setup-gpu", { method: "POST" });
          appendLog("", "Installing CUDA torch for mangaeasy built-in tools (watch logs). Restart app when done.");
        } catch (err) {
          appendLog("", `CUDA torch install failed: ${err.message}`);
          btn.disabled = false;
          btn.textContent = "Install CUDA torch";
        }
      });
    }
  }

  const cards = $("tool-cards");
  cards.innerHTML = "";
  for (const [key, info] of Object.entries(report.tools)) {
    let badge, action = "";
    if (info.installed) {
      badge = `<span class="badge installed">installed</span>`;
      const dis = store.jobRunning ? "disabled" : "";
      action = `<button class="btn small" data-install="${key}" ${dis}>Reinstall</button>
                <button class="btn small danger" data-tool-del="${key}" ${dis}>Delete</button>`;
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

  cards.querySelectorAll("[data-tool-del]").forEach((btn) => {
    let timer = null;
    btn.addEventListener("click", async () => {
      if (!btn._confirming) {
        // First click — ask for confirmation
        btn._confirming = true;
        btn.textContent = "Sure?";
        timer = setTimeout(() => {
          btn._confirming = false;
          btn.textContent = "Delete";
        }, 3000);
      } else {
        // Second click within 3 s — go ahead
        clearTimeout(timer);
        btn._confirming = false;
        btn.disabled = true;
        btn.textContent = "Deleting…";
        const name = btn.dataset.toolDel;
        try {
          await api(`/api/install-tool/${name}`, { method: "DELETE" });
          appendLog("", `deleted: ${name}`);
        } catch (err) {
          appendLog("", `delete failed: ${err.message}`);
        }
        loadDoctor();
      }
    });
  });
}

export function initSetup() {
  $("doctor-refresh").addEventListener("click", loadDoctor);
  // Auto-refresh after any install job finishes or after the app restarts.
  window.addEventListener("sse-action", (e) => {
    if (e.detail === "refresh-doctor") loadDoctor();
  });
  window.addEventListener("sse-reconnect", loadDoctor);
}
