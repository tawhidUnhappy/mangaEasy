/* run.js — Create videos tab: pipeline steps and chapter commands. */

import { $, api, appendLog, store } from "./core.js";
import { pollStatus } from "./status.js";

const STEPS_WITH_OUTPUT = new Set([
  "video", "video-render", "video-join", "video-normalize-audio", "video-validate",
]);
const STEPS_WITHOUT_ITEMS = new Set(["video-normalize-audio"]);

export function updateStepUI() {
  const step = $("run-step").value;
  $("run-tts").disabled = step !== "video";
  $("run-long").disabled = step !== "video";
  $("run-normalize").disabled = step !== "video";
  $("run-output-dir").disabled = !STEPS_WITH_OUTPUT.has(step);
}

function buildRunArgs() {
  const step     = $("run-step").value;
  // manga dir comes from the workflow state (mangas/<name>) — no manual input needed
  const mangaDir = store.mangaDir || "mangas";
  const outputDir = $("run-output-dir").value.trim() || "output";
  const name  = $("run-name").value.trim();
  const items = $("run-items").value.trim();
  const args  = ["--project-root", mangaDir];

  if (STEPS_WITH_OUTPUT.has(step)) args.push("--output-root", outputDir);
  if (items && !STEPS_WITHOUT_ITEMS.has(step)) args.push("--item-range", items);
  if (name) args.push("--project-name", name);

  if (step === "video") {
    args.push("--tts", $("run-tts").value);
    args.push("--encoder", $("run-encoder").value, "--device", $("run-device").value);
    if ($("run-long").checked) {
      args.push("--build-long-video");
      const bgm = $("cfg-bgm").value.trim();
      if (bgm) args.push("--background-music", bgm);
      if ($("run-normalize").checked) args.push("--normalize-audio");
    }
    if ($("run-ow-audio").checked) args.push("--overwrite-audio");
    if ($("run-ow-video").checked) args.push("--overwrite-video");
  } else if (step === "video-normalize-audio") {
    args.push("--replace");
  } else if (step === "video-check") {
    args.push("--strict");
  } else if (step === "video-audio") {
    args.push("--device", $("run-device").value);
    if ($("run-ow-audio").checked) args.push("--overwrite");
  } else if (step === "video-audio-indextts") {
    if ($("run-ow-audio").checked) args.push("--overwrite");
  } else if (step === "video-render") {
    args.push("--encoder", $("run-encoder").value);
    if ($("run-ow-video").checked) args.push("--overwrite");
  }
  return { command: step, args };
}

export function initRun() {
  $("run-step").addEventListener("change", updateStepUI);

  $("run-start").addEventListener("click", async () => {
    try {
      const payload = buildRunArgs();
      await api("/api/run", { method: "POST", body: JSON.stringify(payload) });
    } catch (err) {
      appendLog("", `run: ${err.message}`);
    }
    pollStatus();
  });

  $("run-stop").addEventListener("click", async () => {
    try { await api("/api/stop", { method: "POST" }); } catch (err) { appendLog("", err.message); }
  });

  $("chap-run").addEventListener("click", async () => {
    const command = $("chap-cmd").value;
    const extra = $("chap-args").value.trim();
    const args = extra ? extra.split(/\s+/) : [];
    try {
      await api("/api/run", { method: "POST", body: JSON.stringify({ command, args }) });
    } catch (err) {
      appendLog("", `run: ${err.message}`);
    }
    pollStatus();
  });
}
