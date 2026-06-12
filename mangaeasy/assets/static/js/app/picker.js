/* picker.js — folder & file choosing.
   Browse… first asks the server for the native OS dialog (desktop window).
   In browser mode it falls back to the in-app picker modal. */

import { $, api, appendLog } from "./core.js";

let fmOnSelect = null;
let fmCurrent = "";
let fmMode = { files: false, exts: "" };

function fmJoin(name) {
  return fmCurrent.endsWith("\\") || fmCurrent.endsWith("/")
    ? fmCurrent + name : `${fmCurrent}/${name}`;
}

async function fmLoad(path) {
  let data;
  const query = `path=${encodeURIComponent(path || "")}` +
    (fmMode.files ? `&files=1&exts=${encodeURIComponent(fmMode.exts)}` : "");
  try {
    data = await api(`/api/fs/list?${query}`);
  } catch (err) {
    $("fm-error").textContent = err.message;
    return;
  }
  $("fm-error").textContent = "";
  fmCurrent = data.path;
  $("fm-path").value = data.path;
  $("fm-up").disabled = !data.parent;
  $("fm-up").dataset.parent = data.parent || "";

  const shortcuts = $("fm-shortcuts");
  shortcuts.innerHTML = "";
  const links = [["🏠 Home", data.home], ...data.drives.map((d) => [d, d])];
  for (const [label, target] of links) {
    const b = document.createElement("button");
    b.className = "btn small";
    b.textContent = label;
    b.addEventListener("click", () => fmLoad(target));
    shortcuts.appendChild(b);
  }

  const list = $("fm-list");
  list.innerHTML = "";
  if (!data.dirs.length && !(data.files || []).length) {
    list.innerHTML = fmMode.files
      ? `<div class="fm-empty">No matching files here.</div>`
      : `<div class="fm-empty">No subfolders — click “Use this folder” to pick it.</div>`;
  }
  for (const name of data.dirs) {
    const row = document.createElement("div");
    row.className = "fm-dir";
    row.textContent = `📁 ${name}`;
    row.addEventListener("click", () => fmLoad(fmJoin(name)));
    list.appendChild(row);
  }
  for (const name of data.files || []) {
    const row = document.createElement("div");
    row.className = "fm-dir fm-file";
    row.textContent = `🎵 ${name}`;
    row.addEventListener("click", () => {
      if (fmOnSelect) fmOnSelect(fmJoin(name));
      closeFolderModal();
    });
    list.appendChild(row);
  }
}

function openFolderModal(start, onSelect, mode = { files: false, exts: "" }) {
  fmOnSelect = onSelect;
  fmMode = mode;
  $("fm-title").textContent = mode.files ? "Choose a file" : "Choose a folder";
  $("fm-select").style.display = mode.files ? "none" : "";
  $("folder-modal").classList.remove("hidden");
  fmLoad(start || "");
}

function closeFolderModal() {
  $("folder-modal").classList.add("hidden");
  fmOnSelect = null;
}

async function pickFolder(input) {
  const start = input.value.trim();
  try {
    const res = await api("/api/pick-folder", {
      method: "POST",
      body: JSON.stringify({ start }),
    });
    if (res.folder) {
      input.value = res.folder;
      input.dispatchEvent(new Event("change"));
      return;
    }
    if (!res.unsupported) return; // native dialog shown, user cancelled
  } catch { /* fall through to the in-app picker */ }
  openFolderModal(start, (folder) => {
    input.value = folder;
    input.dispatchEvent(new Event("change"));
  });
}

/* Prefers paths relative to the project folder so the config stays portable. */
function relativeToProject(p) {
  const root = $("project-root").value.trim().replace(/\//g, "\\").replace(/[\\]+$/, "");
  const norm = p.replace(/\//g, "\\");
  if (root && norm.toLowerCase().startsWith(root.toLowerCase() + "\\")) {
    return norm.slice(root.length + 1).replace(/\\/g, "/");
  }
  return p;
}

async function pickFile(input, exts) {
  const start = input.value.trim();
  const pattern = exts.split(",").map((e) => `*.${e.trim()}`).join(";");
  try {
    const res = await api("/api/pick-file", {
      method: "POST",
      body: JSON.stringify({
        start,
        file_types: [`Supported files (${pattern})`, "All files (*.*)"],
      }),
    });
    if (res.file) {
      input.value = res.relative || res.file;
      input.dispatchEvent(new Event("change"));
      return;
    }
    if (!res.unsupported) return; // native dialog shown, user cancelled
  } catch { /* fall through to the in-app picker */ }
  openFolderModal(start, (file) => {
    input.value = relativeToProject(file);
    input.dispatchEvent(new Event("change"));
  }, { files: true, exts });
}

export function initPicker() {
  $("fm-up").addEventListener("click", () => fmLoad($("fm-up").dataset.parent));
  $("fm-go").addEventListener("click", () => fmLoad($("fm-path").value.trim()));
  $("fm-path").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fmLoad($("fm-path").value.trim());
  });
  $("fm-cancel").addEventListener("click", closeFolderModal);
  $("folder-modal").addEventListener("click", (e) => {
    if (e.target === $("folder-modal")) closeFolderModal();
  });
  $("fm-select").addEventListener("click", () => {
    if (fmOnSelect && fmCurrent) fmOnSelect(fmCurrent);
    closeFolderModal();
  });

  document.querySelectorAll("[data-browse]").forEach((btn) =>
    btn.addEventListener("click", () => pickFolder($(btn.dataset.browse))));

  document.querySelectorAll("[data-browse-file]").forEach((btn) =>
    btn.addEventListener("click", () =>
      pickFile($(btn.dataset.browseFile), btn.dataset.exts || "")));

  document.querySelectorAll("[data-open]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const path = $(btn.dataset.open).value.trim();
      try {
        await api("/api/open-folder", { method: "POST", body: JSON.stringify({ path }) });
      } catch (err) {
        appendLog("", `open folder: ${err.message}`);
      }
    }));
}
