// edit_pdf.js (image-pixel accurate annotation editor)

const pageIndex = window.__PAGE_INDEX__;
const pageCount = window.__PAGE_COUNT__;
const DOC_KEY = (window.__DOC_KEY__ || "doc").toString();
const LS_PREFIX = "panelMark:" + DOC_KEY + ":";

// Persist UI state across page navigation
const MODE_KEY = LS_PREFIX + "ui:mode";

const imgUrl = "/render/" + pageIndex;
const annUrl = "/ann/" + pageIndex;

const img = document.getElementById("pageimg");
const canvas = document.getElementById("overlay");

const wrap = document.getElementById("wrap");
const sidePanel = document.getElementById("sidePanel");
const resizer = document.getElementById("resizer");
const ctx = canvas.getContext("2d");
const stage = document.querySelector(".stage");

const pnum = document.getElementById("pnum");
const ptotal = document.getElementById("ptotal");
const modepill = document.getElementById("modepill");

const itemlist = document.getElementById("itemlist");
const defaultLabelInput = document.getElementById("defaultLabel");
const defaultTextInput = document.getElementById("defaultText");

const selectedInfo = document.getElementById("selectedInfo");
const selectedText = document.getElementById("selectedText");

pnum.innerText = String(pageIndex + 1);
ptotal.innerText = String(pageCount);

// Data (stored in IMAGE PIXELS!)
let links = [];
let texts = [];
let pendingBubble = null;
let selection = null;
let mode = "link";

// Restore last used mode (prevents switching back to Link/Line mode on page change)
try {
  const m = localStorage.getItem(MODE_KEY);
  if (m === "link" || m === "text") mode = m;
} catch {}

let dragMode = null;
let isDragging = false;
let dragStart = null;

let dirty = false;
let _lsTimer = null;

const HIT = { EP_R: 18, LINE_D: 14, LABEL_PAD: 6, TEXT_PAD: 6 };
const LABEL_OFFSET = { x: 12, y: -10 };

function lsKey(page) { return LS_PREFIX + "page:" + String(page); }

function clearLocalPage(page) {
  try { localStorage.removeItem(lsKey(page)); } catch {}
}

function clearAllLocal() {
  try {
    const prefix = LS_PREFIX + "page:";
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(prefix)) keys.push(k);
    }
    keys.forEach(k => localStorage.removeItem(k));
  } catch {}
}

function readLocal(page) {
  try {
    const raw = localStorage.getItem(lsKey(page));
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
}

function writeLocal(page, payload) {
  try { localStorage.setItem(lsKey(page), JSON.stringify(payload)); } catch {}
}

function scheduleLocalSave() {
  if (_lsTimer) clearTimeout(_lsTimer);
  _lsTimer = setTimeout(() => {
    writeLocal(pageIndex, {
      page: pageIndex,
      image_w: canvas.width,
      image_h: canvas.height,
      links, texts,
      updated_at: Date.now(),
    });
  }, 150);
}

function markDirty() {
  dirty = true;
  scheduleLocalSave();
}

// ---------- Sidebar resize (Canva-like) ----------
const SIDEW_KEY = LS_PREFIX + "sidew";

function clamp(n, lo, hi){ return Math.max(lo, Math.min(hi, n)); }

function setSidebarWidth(px){
  document.documentElement.style.setProperty("--sidew", px + "px");
}

function getSidebarWidth(){
  const v = getComputedStyle(document.documentElement).getPropertyValue("--sidew").trim();
  const n = parseInt(v.replace("px",""), 10);
  return Number.isFinite(n) ? n : 380;
}

function fitSidebarToContent(){
  if (!sidePanel) return;
  // scrollWidth is the width needed to show content without horizontal scrollbar
  const w = clamp(sidePanel.scrollWidth + 24, 320, Math.min(560, window.innerWidth - 240));
  setSidebarWidth(w);
  try{ localStorage.setItem(SIDEW_KEY, String(w)); }catch{}
}

function initSidebarWidth(){
  if (!sidePanel) return;
  let w = 0;
  try { w = parseInt(localStorage.getItem(SIDEW_KEY) || "0", 10); } catch { w = 0; }
  if (!Number.isFinite(w) || w <= 0) {
    // Default: fit content
    // Wait a tick for layout/textareas to size
    setTimeout(fitSidebarToContent, 0);
  } else {
    setSidebarWidth(clamp(w, 320, Math.min(560, window.innerWidth - 240)));
  }
}

let _resizing = false;
let _startX = 0;
let _startW = 0;

if (resizer && sidePanel) {
  resizer.addEventListener("dblclick", (e) => {
    e.preventDefault();
    fitSidebarToContent();
  });

  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    _resizing = true;
    _startX = e.clientX;
    _startW = getSidebarWidth();
    document.body.classList.add("resizing");
  });

  window.addEventListener("mousemove", (e) => {
    if (!_resizing) return;
    const delta = e.clientX - _startX;
    const maxW = Math.min(560, window.innerWidth - 240);
    const w = clamp(_startW + delta, 320, maxW);
    setSidebarWidth(w);
  });

  window.addEventListener("mouseup", () => {
    if (!_resizing) return;
    _resizing = false;
    document.body.classList.remove("resizing");
    const w = getSidebarWidth();
    try { localStorage.setItem(SIDEW_KEY, String(w)); } catch {}
  });
}

window.addEventListener("resize", () => {
  // keep within bounds
  const maxW = Math.min(560, window.innerWidth - 240);
  setSidebarWidth(clamp(getSidebarWidth(), 320, maxW));
});
// -----------------------------------------------

function setMode(newMode) {
  mode = newMode;
  // Persist mode so it doesn't reset when navigating pages
  try { localStorage.setItem(MODE_KEY, mode); } catch {}
  pendingBubble = null;
  updateModeButtons();
  updateModePill();
  redraw();
}

// Buttons are defined in the template. Keep them in sync with current mode.
const modeLinkBtn = document.getElementById("modeLinkBtn");
const modeTextBtn = document.getElementById("modeTextBtn");
function updateModeButtons(){
  if (!modeLinkBtn || !modeTextBtn) return;
  modeLinkBtn.classList.toggle("active", mode === "link");
  modeTextBtn.classList.toggle("active", mode === "text");
}

function updateModePill() {
  if (isDragging) modepill.innerText = "Dragging";
  else if (pendingBubble) modepill.innerText = (mode === "link") ? "Creating link: click speaker" : "Text mode: click to place";
  else modepill.innerText = (mode === "link") ? "Link mode" : "Text mode";
}

function isTypingContext(ev) {
  const el = ev?.target || document.activeElement;
  if (!el) return false;
  const tag = (el.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (el.isContentEditable) return true;
  return false;
}

// ---- SCALE GUARANTEE ----
// canvas.width/height == img.naturalWidth/Height (image pixels)
// canvas is visually scaled via CSS to match displayed <img> size
function fitCanvasToImage() {
  // Constrain the <img> to the available viewport space first.
  // This prevents the image from spilling outside the window and also
  // makes the sidebar scroll reliably.
  if (stage) {
    const srect = stage.getBoundingClientRect();
    img.style.maxWidth = Math.max(0, Math.floor(srect.width)) + "px";
    img.style.maxHeight = Math.max(0, Math.floor(srect.height)) + "px";
  }

  const rect = img.getBoundingClientRect();

  // INTERNAL coords = IMAGE PIXELS
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;

  // Visual overlay matches the displayed img
  canvas.style.width = rect.width + "px";
  canvas.style.height = rect.height + "px";

  redraw();
}

function scheduleFit() {
  fitCanvasToImage();
  setTimeout(fitCanvasToImage, 50);
  setTimeout(fitCanvasToImage, 200);
}

function canvasPos(ev) {
  const r = canvas.getBoundingClientRect();

  // screen -> image pixels
  const sx = canvas.width / r.width;
  const sy = canvas.height / r.height;

  return {
    x: (ev.clientX - r.left) * sx,
    y: (ev.clientY - r.top) * sy,
  };
}

function clampToCanvas(p) {
  return {
    x: Math.max(0, Math.min(canvas.width, p.x)),
    y: Math.max(0, Math.min(canvas.height, p.y)),
  };
}

function drawDot(x, y, r) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}

function dist2(ax, ay, bx, by) {
  const dx = ax - bx, dy = ay - by;
  return dx * dx + dy * dy;
}

function pointSegmentDistance(px, py, x1, y1, x2, y2) {
  const vx = x2 - x1, vy = y2 - y1;
  const wx = px - x1, wy = py - y1;
  const c1 = vx * wx + vy * wy;
  if (c1 <= 0) return Math.hypot(px - x1, py - y1);
  const c2 = vx * vx + vy * vy;
  if (c2 <= c1) return Math.hypot(px - x2, py - y2);
  const b = c1 / c2;
  const bx = x1 + b * vx, by = y1 + b * vy;
  return Math.hypot(px - bx, py - by);
}

function normalizeLink(L) {
  if (!L.label_pos) L.label_pos = { x: L.speaker.x + LABEL_OFFSET.x, y: L.speaker.y + LABEL_OFFSET.y };
  if (typeof L.label !== "string") L.label = "";
}

function textBoxHit(px, py, x, y, text, pad) {
  if (!text) return false;
  ctx.font = "18px system-ui, Arial";
  const w = ctx.measureText(text).width;
  const h = 20;
  return (px >= x - pad && px <= x + w + pad && py >= y - h - pad && py <= y + pad);
}

function hitTest(p) {
  // texts first
  for (let i = texts.length - 1; i >= 0; i--) {
    const T = texts[i];
    if (textBoxHit(p.x, p.y, T.x, T.y, T.text, HIT.TEXT_PAD)) return { kind: "text", idx: i, part: "text" };
  }

  let best = { kind: null, idx: -1, part: null, score: Infinity };

  for (let i = 0; i < links.length; i++) {
    const L = links[i]; normalizeLink(L);
    const bx = L.bubble.x, by = L.bubble.y;
    const sx = L.speaker.x, sy = L.speaker.y;

    const dBubble = Math.sqrt(dist2(p.x, p.y, bx, by));
    if (dBubble < HIT.EP_R && dBubble < best.score) best = { kind: "link", idx: i, part: "bubble", score: dBubble };

    const dSpeaker = Math.sqrt(dist2(p.x, p.y, sx, sy));
    if (dSpeaker < HIT.EP_R && dSpeaker < best.score) best = { kind: "link", idx: i, part: "speaker", score: dSpeaker };

    if (L.label) {
      if (textBoxHit(p.x, p.y, L.label_pos.x, L.label_pos.y, L.label, HIT.LABEL_PAD)) {
        best = { kind: "link", idx: i, part: "label", score: 0.1 };
      }
    }

    const dLine = pointSegmentDistance(p.x, p.y, bx, by, sx, sy);
    if (dLine < HIT.LINE_D && dLine < best.score) best = { kind: "link", idx: i, part: "line", score: dLine };
  }

  if (best.idx === -1) return null;
  return { kind: best.kind, idx: best.idx, part: best.part };
}

function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // stroke/font in IMAGE PIXELS
  ctx.lineWidth = 3;
  ctx.font = "18px system-ui, Arial";

  links.forEach((L, idx) => {
    normalizeLink(L);
    const bx = L.bubble.x, by = L.bubble.y;
    const sx = L.speaker.x, sy = L.speaker.y;
    const isSel = (selection && selection.kind === "link" && selection.idx === idx);

    ctx.strokeStyle = isSel ? "rgba(0,200,255,0.95)" : "rgba(255,0,0,0.95)";
    ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(sx, sy); ctx.stroke();

    ctx.fillStyle = isSel ? "rgba(0,200,255,0.95)" : "rgba(255,0,0,0.95)";
    drawDot(bx, by, isSel ? 7 : 6);
    drawDot(sx, sy, isSel ? 7 : 6);

    if (L.label) ctx.fillText(L.label, L.label_pos.x, L.label_pos.y);
  });

  texts.forEach((T, idx) => {
    const isSel = (selection && selection.kind === "text" && selection.idx === idx);
    ctx.fillStyle = isSel ? "rgba(0,200,255,0.95)" : "rgba(255,0,0,0.95)";
    if (T.text) ctx.fillText(T.text, T.x, T.y);
  });

  if (pendingBubble) {
    ctx.fillStyle = "rgba(0,200,255,0.95)";
    drawDot(pendingBubble.x, pendingBubble.y, 8);
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.fillText("bubble ✓", pendingBubble.x + 10, Math.max(18, pendingBubble.y - 10));
  }

  updateModePill();
}

function setSelection(sel) {
  selection = sel;
  updateSidebarSelection();
  updateList();
  redraw();
}

function updateSidebarSelection() {
  if (!selection) {
    selectedInfo.innerText = "None";
    selectedText.value = "";
    selectedText.disabled = true;
    return;
  }
  selectedText.disabled = false;

  if (selection.kind === "link") {
    selectedInfo.innerText = `Link #${selection.idx + 1}`;
    const L = links[selection.idx];
    normalizeLink(L);
    if (document.activeElement !== selectedText) selectedText.value = L.label || "";
  } else {
    selectedInfo.innerText = `Text #${selection.idx + 1}`;
    const T = texts[selection.idx];
    if (document.activeElement !== selectedText) selectedText.value = T.text || "";
  }
}

function escapeHtml(s) {
  return s
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function updateList() {
  itemlist.innerHTML = "";

  links.forEach((L, idx) => {
    normalizeLink(L);
    const div = document.createElement("div");
    div.className = "item" + (selection && selection.kind === "link" && selection.idx === idx ? " sel" : "");
    div.onclick = () => setSelection({ kind: "link", idx });

    const labelPart = L.label ? `• <span class="kbd">${escapeHtml(L.label)}</span>` : "";
    div.innerHTML = `
      <div><b>Link #${idx + 1}</b> ${labelPart}</div>
      <div class="muted">bubble=(${L.bubble.x.toFixed(0)}, ${L.bubble.y.toFixed(0)}) → speaker=(${L.speaker.x.toFixed(0)}, ${L.speaker.y.toFixed(0)})</div>
      <div class="actions">
        <button class="btn smallbtn" data-act="sel">Select</button>
        <button class="btn smallbtn danger" data-act="del">Delete</button>
      </div>
    `;
    div.querySelector('[data-act="sel"]').onclick = (e) => { e.stopPropagation(); setSelection({kind:"link", idx}); };
    div.querySelector('[data-act="del"]').onclick  = (e) => { e.stopPropagation(); setSelection({kind:"link", idx}); deleteSelected(); };
    itemlist.appendChild(div);
  });

  texts.forEach((T, idx) => {
    const div = document.createElement("div");
    div.className = "item" + (selection && selection.kind === "text" && selection.idx === idx ? " sel" : "");
    div.onclick = () => setSelection({ kind: "text", idx });

    const preview = (T.text || "").slice(0, 40);
    div.innerHTML = `
      <div><b>Text #${idx + 1}</b> ${preview ? `• <span class="kbd">${escapeHtml(preview)}</span>` : ""}</div>
      <div class="muted">pos=(${T.x.toFixed(0)}, ${T.y.toFixed(0)})</div>
      <div class="actions">
        <button class="btn smallbtn" data-act="sel">Select</button>
        <button class="btn smallbtn danger" data-act="del">Delete</button>
      </div>
    `;
    div.querySelector('[data-act="sel"]').onclick = (e) => { e.stopPropagation(); setSelection({kind:"text", idx}); };
    div.querySelector('[data-act="del"]').onclick  = (e) => { e.stopPropagation(); setSelection({kind:"text", idx}); deleteSelected(); };
    itemlist.appendChild(div);
  });

  updateSidebarSelection();
}

// ---------- persistence ----------
async function loadAnn() {
  const local = readLocal(pageIndex);

  let server = null;
  try {
    const res = await fetch(annUrl, { cache: "no-store" });
    if (res.ok) server = await res.json();
  } catch {}

  // If the server annotation file was deleted, do NOT resurrect stale
  // marks from localStorage. Clear the local page cache and use the
  // server's empty state.
  if (server && server._exists === false) {
    // Assume the user reset/wiped server annotations (e.g., deleted the folder).
    // Clear the entire chapter cache so it doesn't come back from the browser.
    clearAllLocal();
  }

  const chosen = (server && server._exists === false)
    ? (server || { links: [], texts: [] })
    : (local || server || { links: [], texts: [] });

  links = (chosen.links || []).map(L => {
    if (!L.label_pos) L.label_pos = { x: L.speaker.x + LABEL_OFFSET.x, y: L.speaker.y + LABEL_OFFSET.y };
    return L;
  });

  texts = (chosen.texts || []).map(T => ({ x: Number(T.x), y: Number(T.y), text: String(T.text || "") }));

  // persist baseline to local
  if (!local || (server && server._exists === false)) {
    writeLocal(pageIndex, {
      page: pageIndex,
      image_w: canvas.width,
      image_h: canvas.height,
      links, texts,
      updated_at: Date.now(),
    });
  }

  pendingBubble = null;
  selection = null;
  dirty = false;
  updateList();
  redraw();
}

async function savePage(silent=false, keepalive=false) {
  const payload = {
    page: pageIndex,
    image_w: canvas.width,
    image_h: canvas.height,
    links, texts,
    updated_at: Date.now(),
  };

  // local always
  writeLocal(pageIndex, payload);

  const res = await fetch("/save/" + pageIndex, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    keepalive: !!keepalive,
  });

  if (!res.ok) {
    if (!silent) alert("Save failed: " + res.status);
    return false;
  }
  dirty = false;
  if (!silent) alert("Saved!");
  return true;
}

async function saveIfDirtySilent() {
  if (!dirty) return true;
  return await savePage(true, true);
}

async function navPage(delta) {
  // always store local before leaving
  writeLocal(pageIndex, {
    page: pageIndex,
    image_w: canvas.width,
    image_h: canvas.height,
    links, texts,
    updated_at: Date.now(),
  });

  await saveIfDirtySilent();

  const next = pageIndex + delta;
  if (next < 0 || next >= pageCount) return;
  window.location = "/page/" + next;
}

async function finishAndClose() {
  await savePage(true, true);

  let ok = false;
  let out = "";
  try {
    const res = await fetch("/finish", { method: "POST" });
    ok = res.ok;
    if (ok) {
      const j = await res.json();
      out = j.out || "";
    }
  } catch {}

  if (!ok) {
    alert("Finish failed. Check server console.");
    return;
  }

  // Tab close is often blocked by browsers; attempt anyway.
  try { window.close(); } catch {}

  setTimeout(() => {
    document.body.innerHTML = `<div style="font-family:system-ui;padding:24px;background:#111;color:#eee">
      <h2>Finished ✅</h2>
      <p>Exported: <b>${out || "chapter_mark.pdf"}</b></p>
      <p>Server is stopping. If this tab didn’t close automatically, close it manually.</p>
    </div>`;
  }, 200);
}

// ---------- mouse ----------
canvas.addEventListener("mousedown", (ev) => {
  const p0 = clampToCanvas(canvasPos(ev));
  const hit = hitTest(p0);

  if (hit) {
    setSelection({ kind: hit.kind, idx: hit.idx });

    if (hit.kind === "text") {
      dragMode = "text_move";
      dragStart = p0;
      isDragging = true;
      return;
    }

    if (hit.part === "line") {
      dragMode = "move";
      dragStart = p0;
    } else {
      dragMode = hit.part;
      dragStart = null;
    }
    isDragging = true;
  }
});

window.addEventListener("mousemove", (ev) => {
  if (!isDragging || !selection) return;
  const p = clampToCanvas(canvasPos(ev));

  if (selection.kind === "text") {
    const T = texts[selection.idx];
    const dx = p.x - dragStart.x;
    const dy = p.y - dragStart.y;
    T.x += dx; T.y += dy;
    T.x = Math.max(0, Math.min(canvas.width, T.x));
    T.y = Math.max(0, Math.min(canvas.height, T.y));
    dragStart = p;
    markDirty();
    updateList(); redraw();
    return;
  }

  const L = links[selection.idx];
  normalizeLink(L);

  if (dragMode === "bubble") L.bubble = { x: p.x, y: p.y };
  else if (dragMode === "speaker") L.speaker = { x: p.x, y: p.y };
  else if (dragMode === "label") L.label_pos = { x: p.x, y: p.y };
  else if (dragMode === "move") {
    const dx = p.x - dragStart.x;
    const dy = p.y - dragStart.y;
    L.bubble = clampToCanvas({ x: L.bubble.x + dx, y: L.bubble.y + dy });
    L.speaker = clampToCanvas({ x: L.speaker.x + dx, y: L.speaker.y + dy });
    L.label_pos = clampToCanvas({ x: L.label_pos.x + dx, y: L.label_pos.y + dy });
    dragStart = p;
  }

  markDirty();
  updateList();
  redraw();
});

window.addEventListener("mouseup", () => {
  if (!isDragging) return;
  isDragging = false;
  dragMode = null;
  dragStart = null;
  markDirty();
  redraw();
});

canvas.addEventListener("click", (ev) => {
  if (isDragging) return;
  const p = clampToCanvas(canvasPos(ev));

  const hit = hitTest(p);
  if (!pendingBubble && hit) {
    setSelection({ kind: hit.kind, idx: hit.idx });
    return;
  }

  if (mode === "text") {
    const txt = (defaultTextInput.value || "").trim();
    texts.push({ x: p.x, y: p.y, text: txt });
    markDirty();
    setSelection({ kind: "text", idx: texts.length - 1 });
    return;
  }

  if (!pendingBubble) {
    pendingBubble = { x: p.x, y: p.y };
    setSelection(null);
    redraw();
    return;
  }

  const label = (defaultLabelInput.value || "").trim();
  const newLink = {
    bubble: pendingBubble,
    speaker: { x: p.x, y: p.y },
    label,
    label_pos: { x: p.x + LABEL_OFFSET.x, y: p.y + LABEL_OFFSET.y },
  };
  links.push(newLink);
  markDirty();

  pendingBubble = null;
  setSelection({ kind: "link", idx: links.length - 1 });
});

// sidebar edits
selectedText.addEventListener("input", () => {
  if (!selection) return;
  if (selection.kind === "link") links[selection.idx].label = selectedText.value;
  else texts[selection.idx].text = selectedText.value;
  markDirty();
  updateList();
  redraw();
});
selectedText.disabled = true;

// actions
function cancelPending() { pendingBubble = null; redraw(); }

function deleteSelected() {
  if (!selection) return;
  if (selection.kind === "link") links.splice(selection.idx, 1);
  else texts.splice(selection.idx, 1);
  markDirty();
  setSelection(null);
}

function undo() {
  if (pendingBubble) { pendingBubble = null; redraw(); return; }
  if (mode === "text" && texts.length) texts.pop();
  else if (links.length) links.pop();
  markDirty();
  setSelection(null);
}

function clearAll() {
  if (!confirm("Clear all items on this page?")) return;
  pendingBubble = null;
  links = [];
  texts = [];
  markDirty();
  setSelection(null);
}

// buttons
document.getElementById("prevBtn").onclick = async () => await navPage(-1);
document.getElementById("nextBtn").onclick = async () => await navPage(1);
document.getElementById("saveBtn").onclick = () => savePage(false, false);
document.getElementById("exportBtn").onclick = async () => { await savePage(true, true); window.location = "/export"; };
document.getElementById("deleteBtn").onclick = deleteSelected;
document.getElementById("undoBtn").onclick = undo;
document.getElementById("clearBtn").onclick = clearAll;
document.getElementById("cancelBtn").onclick = cancelPending;
document.getElementById("modeLinkBtn").onclick = () => setMode("link");
document.getElementById("modeTextBtn").onclick = () => setMode("text");
const clearCacheBtn = document.getElementById("clearCacheBtn");
if (clearCacheBtn) {
  clearCacheBtn.onclick = () => {
    if (!confirm("Clear local browser cache for this chapter?\n\nThis removes any saved marks stored in your browser (localStorage).")) return;
    clearAllLocal();
    // Reload to fetch server state (which might be empty if you deleted the annotations folder).
    window.location.reload();
  };
}
const finishBtn = document.getElementById("finishBtn");
if (finishBtn) finishBtn.onclick = finishAndClose;

// hotkeys
window.addEventListener("keydown", async (ev) => {
  const k = ev.key;

  if (k === "Escape") { ev.preventDefault(); cancelPending(); return; }
  if (isTypingContext(ev)) return;

  if (k === "ArrowLeft") { ev.preventDefault(); await navPage(-1); return; }
  if (k === "ArrowRight") { ev.preventDefault(); await navPage(1); return; }

  if (k.toLowerCase() === "l") { ev.preventDefault(); setMode("link"); return; }
  if (k.toLowerCase() === "t") { ev.preventDefault(); setMode("text"); return; }
  if (k.toLowerCase() === "f") { ev.preventDefault(); await finishAndClose(); return; }

  // clear local cache (chapter) and reload
  if (k.toLowerCase() === "k") {
    ev.preventDefault();
    if (confirm("Clear local browser cache for this chapter?\n\nThis removes saved marks stored in your browser (localStorage).")) {
      clearAllLocal();
      window.location.reload();
    }
    return;
  }

  if (k.toLowerCase() === "z") { ev.preventDefault(); undo(); return; }
  if (k === "Delete" || k === "Backspace") { ev.preventDefault(); deleteSelected(); return; }
});

// beforeunload: keep local + best-effort save
window.addEventListener("beforeunload", () => {
  writeLocal(pageIndex, {
    page: pageIndex,
    image_w: canvas.width,
    image_h: canvas.height,
    links, texts,
    updated_at: Date.now(),
  });
  if (!dirty) return;
  savePage(true, true);
});

// load
img.onload = () => {
  scheduleFit();
  loadAnn();
};
img.src = imgUrl;

window.addEventListener("resize", () => scheduleFit());
updateModeButtons();
updateModePill();
initSidebarWidth();
