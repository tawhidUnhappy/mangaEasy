(() => {
  const body = document.body;

  const STORAGE_KEY = body.dataset.storageKey || "panel_marks_default";
  const MIN_PANEL_PX = Number(body.dataset.minPanel || "10");

  // Auto-mark panels when the tab opens.
  const ALWAYS_AUTO_ON_LOAD = true;

  let pages = [];
  let panels = [];             // [{top,bottom}] in global-original pixels
  let currentLineGlobal = null;
  let pendingImages = 0;
  let totalHeightGlobal = 0;

  let hoverPanelIndexGlobal = -1;
  let hoverYGlobal = null;

  const pagesContainer = document.getElementById('pages');
  const statusSpan = document.getElementById('status');
  const countSpan = document.getElementById('count');
  const saveBtn = document.getElementById('saveBtn');
  const clearAllBtn = document.getElementById('clearAllBtn');
  const autoBtn = document.getElementById('autoBtn');
  const finishBtn = document.getElementById('finishBtn');

  const DRAG_THRESHOLD_SCREEN_PX = 10;

  // --- DRAG/CLICK BUG FIX FLAGS ---
  // When we drag a handle, browsers often still emit a click after mouseup.
  // This prevents that click from creating a "pending line".
  let suppressNextClick = false;

  function suppressClickOnce() {
    suppressNextClick = true;
    // Clear on next tick so it only affects the immediate click after mouseup.
    setTimeout(() => { suppressNextClick = false; }, 0);
  }

  function setStatus(text) { statusSpan.textContent = text; }
  function updateCount() { countSpan.textContent = `Panels: ${panels.length}`; }

  function saveStateToLocal() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ panels })); } catch (e) {}
  }
  function loadStateFromLocal() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (!obj || !Array.isArray(obj.panels)) return null;
      return obj.panels;
    } catch (e) { return null; }
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function normalizePanels() {
    const cleaned = [];
    for (const p of panels) {
      let t = Number(p.top), b = Number(p.bottom);
      if (!Number.isFinite(t) || !Number.isFinite(b)) continue;
      t = clamp(t, 0, totalHeightGlobal);
      b = clamp(b, 0, totalHeightGlobal);
      if (b <= t) continue;
      cleaned.push({ top: t, bottom: b });
    }
    cleaned.sort((a, b) => a.top - b.top);

    const out = [];
    for (const p of cleaned) {
      if (!out.length) { out.push(p); continue; }
      const last = out[out.length - 1];
      if (p.top < last.bottom) {
        const shiftTop = last.bottom;
        if (p.bottom - shiftTop >= MIN_PANEL_PX) out.push({ top: shiftTop, bottom: p.bottom });
      } else out.push(p);
    }
    panels = out;
    updateCount();
    saveStateToLocal();
  }

  function redrawAllPages() {
    pages.forEach(ps => redrawPage(ps));
    updateCount();
  }

  // --- Load images ---
  async function loadImagesList() {
    const res = await fetch('/images');
    const data = await res.json();
    const imageNames = data.images || [];
    if (!imageNames.length) { setStatus("No images found."); return; }

    setStatus(`Loaded ${imageNames.length} pages.`);
    pendingImages = imageNames.length;
    pages = [];
    imageNames.forEach(name => createPage(name));
  }

  function createPage(imageName) {
    const pageEl = document.createElement('div');
    pageEl.className = 'page';
    const inner = document.createElement('div');
    inner.className = 'page-inner';

    const img = document.createElement('img');
    img.src = `/image/${encodeURIComponent(imageName)}`;
    img.alt = imageName;
    img.draggable = false;

    const overlay = document.createElement('canvas');
    const hitLayer = document.createElement('canvas');

    inner.appendChild(img);
    inner.appendChild(overlay);
    inner.appendChild(hitLayer);
    pageEl.appendChild(inner);
    pagesContainer.appendChild(pageEl);

    const state = { imageName, imgEl: img, overlay, hitLayer, origHeight: null, offsetTop: null, hoverPanelIndex: -1 };
    pages.push(state);

    img.addEventListener('load', () => {
      state.origHeight = img.naturalHeight || img.height;
      pendingImages--;
      if (pendingImages === 0) {
        computeOffsetsAndResize();
        initPanelsOnOpen();
      }
    });

    attachPageEvents(state);
  }

  function computeOffsetsAndResize() {
    let offset = 0;
    pages.forEach(ps => { ps.offsetTop = offset; offset += ps.origHeight; });
    totalHeightGlobal = offset;

    pages.forEach(ps => { resizePage(ps); redrawPage(ps); });
  }

  function resizePage(ps) {
    const rect = ps.imgEl.getBoundingClientRect();
    const w = rect.width, h = rect.height;
    if (!w || !h) return;
    ps.overlay.width = w; ps.overlay.height = h;
    ps.hitLayer.width = w; ps.hitLayer.height = h;
    ps.overlay.style.width = w + "px"; ps.overlay.style.height = h + "px";
    ps.hitLayer.style.width = w + "px"; ps.hitLayer.style.height = h + "px";
  }

  // --- Coords helpers ---
  function getGlobalYFromEvent(ps, evt) {
    if (ps.origHeight == null || ps.offsetTop == null) return null;
    const rect = ps.hitLayer.getBoundingClientRect();
    const yScaled = evt.clientY - rect.top;
    const scale = ps.origHeight / rect.height;
    return ps.offsetTop + (yScaled * scale);
  }

  function isInsidePanel(globalY) { return panels.some(p => globalY > p.top && globalY < p.bottom); }
  function panelsOverlap(a, b) { return !(a.bottom <= b.top || a.top >= b.bottom); }

  function wouldOverlapAny(top, bottom, ignoreIndex = null) {
    const candidate = { top, bottom };
    for (let i = 0; i < panels.length; i++) {
      if (ignoreIndex !== null && i === ignoreIndex) continue;
      if (panelsOverlap(candidate, panels[i])) return true;
    }
    return false;
  }

  // --- Drawing ---
  function redrawPage(ps) {
    if (ps.origHeight == null || ps.offsetTop == null) return;
    const overlay = ps.overlay;
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    const scale = overlay.height / ps.origHeight;

    panels.forEach((p, idx) => {
      const pageTop = ps.offsetTop;
      const pageBottom = ps.offsetTop + ps.origHeight;

      const overlapTop = Math.max(p.top, pageTop);
      const overlapBottom = Math.min(p.bottom, pageBottom);
      if (overlapBottom <= overlapTop) return;

      const localTopOrig = overlapTop - pageTop;
      const localBottomOrig = overlapBottom - pageTop;

      // Snap to pixel boundaries to avoid 1px seams between stacked pages
      const topY = Math.floor(localTopOrig * scale);
      const bottomY = Math.ceil(localBottomOrig * scale);
      const height = bottomY - topY;
      if (height <= 0) return;

      const isTrueTop = p.top >= pageTop && p.top <= pageBottom;
      const isTrueBottom = p.bottom >= pageTop && p.bottom <= pageBottom;

      ctx.save();
      if (idx === hoverPanelIndexGlobal) {
        ctx.fillStyle = "rgba(80, 180, 255, 0.26)";
        ctx.strokeStyle = "rgba(120, 210, 255, 0.95)";
        ctx.lineWidth = 2;
      } else {
        ctx.fillStyle = "rgba(50, 200, 140, 0.20)";
        ctx.strokeStyle = "rgba(80, 230, 180, 0.9)";
        ctx.lineWidth = 1.6;
      }

      ctx.fillRect(0, topY, overlay.width, height);

      ctx.beginPath();
      ctx.moveTo(0.5, topY); ctx.lineTo(0.5, bottomY);
      ctx.moveTo(overlay.width - 0.5, topY); ctx.lineTo(overlay.width - 0.5, bottomY);
      if (isTrueTop) { ctx.moveTo(0, topY + 0.5); ctx.lineTo(overlay.width, topY + 0.5); }
      if (isTrueBottom) { ctx.moveTo(0, bottomY - 0.5); ctx.lineTo(overlay.width, bottomY - 0.5); }
      ctx.stroke();

      ctx.strokeStyle = "rgba(255, 255, 255, 0.8)";
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      if (isTrueTop) { ctx.moveTo(0, topY); ctx.lineTo(overlay.width, topY); }
      if (isTrueBottom) { ctx.moveTo(0, bottomY); ctx.lineTo(overlay.width, bottomY); }
      ctx.stroke();
      ctx.setLineDash([]);

      const handleRadius = 4;
      ctx.fillStyle = "rgba(10, 10, 15, 0.9)";
      ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
      const handleYs = [];
      if (isTrueTop) handleYs.push(topY);
      if (isTrueBottom) handleYs.push(bottomY);

      handleYs.forEach(y => {
        ctx.beginPath(); ctx.arc(8, y, handleRadius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.arc(overlay.width - 8, y, handleRadius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      });

      ctx.restore();
    });

    if (currentLineGlobal !== null) {
      const pageTop = ps.offsetTop;
      const pageBottom = ps.offsetTop + ps.origHeight;
      if (currentLineGlobal >= pageTop && currentLineGlobal <= pageBottom) {
        const local = currentLineGlobal - pageTop;
        const y = Math.round(local * scale);
        ctx.save();
        ctx.setLineDash([5, 3]);
        ctx.strokeStyle = "rgba(255, 220, 120, 0.9)";
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(overlay.width, y); ctx.stroke();
        ctx.restore();
      }
    }
  }

  // --- Events ---
  function attachPageEvents(ps) {
    const hit = ps.hitLayer;

    let dragging = null; // {panelIndex, edge, startY, startTop, startBottom}
    let dragMoved = false;

    hit.addEventListener('mousemove', (evt) => {
      if (ps.origHeight == null || ps.offsetTop == null) return;
      const globalY = getGlobalYFromEvent(ps, evt);
      if (globalY == null) return;

      const rect = hit.getBoundingClientRect();
      const scale = ps.origHeight / rect.height;
      const thresholdOrig = DRAG_THRESHOLD_SCREEN_PX * scale;

      ps.hoverPanelIndex = -1;
      hoverPanelIndexGlobal = -1;
      hoverYGlobal = globalY;

      for (let i = 0; i < panels.length; i++) {
        const p = panels[i];
        if (globalY >= p.top && globalY <= p.bottom) {
          ps.hoverPanelIndex = i;
          hoverPanelIndexGlobal = i;
        }
      }

      if (dragging) {
        const dy = globalY - dragging.startY;
        if (Math.abs(dy) > thresholdOrig * 0.25) dragMoved = true;

        let newTop = dragging.startTop;
        let newBottom = dragging.startBottom;
        if (dragging.edge === "top") newTop = dragging.startTop + dy;
        if (dragging.edge === "bottom") newBottom = dragging.startBottom + dy;

        if (newBottom - newTop >= MIN_PANEL_PX && !wouldOverlapAny(newTop, newBottom, dragging.panelIndex)) {
          panels[dragging.panelIndex].top = newTop;
          panels[dragging.panelIndex].bottom = newBottom;
          normalizePanels();
          redrawAllPages();
        }
        return;
      }

      hit.style.cursor = "default";
      for (let i = panels.length - 1; i >= 0; i--) {
        const p = panels[i];
        const nearTop = Math.abs(globalY - p.top) <= thresholdOrig;
        const nearBottom = Math.abs(globalY - p.bottom) <= thresholdOrig;
        if (nearTop || nearBottom) { hit.style.cursor = "ns-resize"; break; }
      }

      redrawAllPages();
    });

    hit.addEventListener('mousedown', (evt) => {
      if (ps.origHeight == null || ps.offsetTop == null) return;
      const globalY = getGlobalYFromEvent(ps, evt);
      if (globalY == null) return;

      const rect = hit.getBoundingClientRect();
      const scale = ps.origHeight / rect.height;
      const thresholdOrig = DRAG_THRESHOLD_SCREEN_PX * scale;

      dragging = null;
      dragMoved = false;

      for (let i = panels.length - 1; i >= 0; i--) {
        const p = panels[i];
        const nearTop = Math.abs(globalY - p.top) <= thresholdOrig;
        const nearBottom = Math.abs(globalY - p.bottom) <= thresholdOrig;
        if (nearTop || nearBottom) {
          dragging = {
            panelIndex: i,
            edge: nearTop ? "top" : "bottom",
            startY: globalY,
            startTop: p.top,
            startBottom: p.bottom
          };

          // IMPORTANT: even if user doesn't "move" much, a click will often fire after mouseup.
          // Suppress that click so it can't set a pending line.
          suppressClickOnce();
          break;
        }
      }
    });

    window.addEventListener('mouseup', () => {
      // If we were dragging, also suppress the click that might happen right after mouseup.
      if (dragging) suppressClickOnce();

      dragging = null;
      dragMoved = false;
    });

    hit.addEventListener('mouseleave', () => {
      ps.hoverPanelIndex = -1;
      hoverPanelIndexGlobal = -1;
      hoverYGlobal = null;

      // If mouse leaves while dragging, treat like drag end (also suppress click)
      if (dragging) suppressClickOnce();
      dragging = null;
      dragMoved = false;

      redrawAllPages();
    });

    hit.addEventListener('click', (evt) => {
      // --- BUG FIX: don't treat a handle drag as a click-to-create-line ---
      if (suppressNextClick) return;
      if (dragging) return;
      if (dragMoved) return;

      const globalY = getGlobalYFromEvent(ps, evt);
      if (globalY == null) return;
      if (isInsidePanel(globalY)) return;

      if (currentLineGlobal === null) {
        currentLineGlobal = globalY;
        setStatus("Line set. Click again to create an area.");
      } else {
        const top = Math.min(currentLineGlobal, globalY);
        const bottom = Math.max(currentLineGlobal, globalY);
        if ((bottom - top) >= MIN_PANEL_PX && !wouldOverlapAny(top, bottom)) {
          panels.push({ top, bottom });
          normalizePanels();
        }
        currentLineGlobal = null;
        setStatus("");
      }
      redrawAllPages();
    });

    hit.addEventListener('contextmenu', (evt) => {
      evt.preventDefault();
      const globalY = getGlobalYFromEvent(ps, evt);
      if (globalY == null) return;

      const rect = hit.getBoundingClientRect();
      const scale = ps.origHeight / rect.height;
      const thresholdOrig = DRAG_THRESHOLD_SCREEN_PX * scale;

      if (currentLineGlobal !== null && Math.abs(globalY - currentLineGlobal) <= thresholdOrig) {
        currentLineGlobal = null;
        setStatus("Cancelled pending line.");
        redrawAllPages();
        return;
      }

      for (let i = panels.length - 1; i >= 0; i--) {
        const p = panels[i];
        const insideArea = globalY >= p.top && globalY <= p.bottom;
        const nearTop = Math.abs(globalY - p.top) <= thresholdOrig;
        const nearBottom = Math.abs(globalY - p.bottom) <= thresholdOrig;
        if (insideArea || nearTop || nearBottom) {
          panels.splice(i, 1);
          normalizePanels();
          setStatus("Deleted panel.");
          redrawAllPages();
          return;
        }
      }
    });
  }

  // --- Buttons ---
  saveBtn.addEventListener('click', async () => {
    normalizePanels();
    if (panels.length === 0) { setStatus("No panels to save."); return; }
    setStatus("Saving panels...");
    try {
      const res = await fetch('/save_panels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ panels })
      });
      const data = await res.json();
      if (data.status === "ok") setStatus(`Saved ${data.saved.length} panels.`);
      else setStatus(`Error: ${data.message || "unknown"}`);
    } catch (e) { setStatus("Save failed."); }
  });

  clearAllBtn.addEventListener('click', () => {
    panels = [];
    currentLineGlobal = null;
    normalizePanels();
    redrawAllPages();
    setStatus("Cleared all marks.");
  });

  autoBtn.addEventListener('click', async () => {
    currentLineGlobal = null;
    setStatus("Running auto marks...");
    try {
      const res = await fetch('/initial_panels?refresh=1');
      const data = await res.json();
      if (Array.isArray(data.panels)) {
        panels = data.panels;
        normalizePanels();
        redrawAllPages();
        const dev = data.device ? ` · device=${data.device}` : "";
        const gpu = data.gpu_name ? ` (${data.gpu_name})` : "";
        setStatus(`Loaded gutter auto marks (${panels.length})${dev}${gpu}.`);
      } else setStatus("Auto marks: no panels returned.");
    } catch (e) { setStatus("Auto marks failed."); }
  });

  finishBtn.addEventListener('click', async () => {
    normalizePanels();

    if (panels.length === 0) {
      setStatus("No panels to save.");
      return;
    }

    setStatus("Saving panels...");
    try {
      const res = await fetch('/save_panels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ panels })
      });
      const data = await res.json();
      if (data.status !== "ok") {
        setStatus(`Save error: ${data.message || "unknown"}`);
        return;
      }
      setStatus(`Saved ${data.saved.length} panels. Closing...`);
    } catch (e) {
      setStatus("Save failed (not closing).");
      return;
    }

    try { await fetch('/shutdown', { method: 'POST' }); } catch (e) {}

    // Best-effort tab close (may be blocked by browser rules).
    try { window.open('about:blank', '_self'); window.close(); } catch (e) {}
  });

  // N = line after last area (only if panels exist & no line)
  function createLineAfterLastPanel() {
    if (!pages.length) return;
    if (currentLineGlobal !== null) return;
    if (panels.length === 0) return;

    let lastBottom = 0;
    panels.forEach(p => { if (p.bottom > lastBottom) lastBottom = p.bottom; });
    currentLineGlobal = Math.min(lastBottom + 1, totalHeightGlobal - 1);

    for (const ps of pages) {
      const top = ps.offsetTop;
      const bottom = ps.offsetTop + ps.origHeight;
      if (currentLineGlobal >= top && currentLineGlobal <= bottom) {
        ps.imgEl.scrollIntoView({ behavior: "smooth", block: "start" });
        break;
      }
    }

    setStatus("Started line after last panel (click to define next area).");
    redrawAllPages();
  }

  function splitHoveredPanel() {
    // Split the hovered panel into two at the current mouse Y.
    if (hoverPanelIndexGlobal < 0) return;
    if (hoverYGlobal === null) return;

    const idx = hoverPanelIndexGlobal;
    if (idx < 0 || idx >= panels.length) return;

    const p = panels[idx];
    const y = Number(hoverYGlobal);
    if (!Number.isFinite(y)) return;

    // Only split if mouse is inside the panel body (not exactly on edges)
    if (!(y > p.top && y < p.bottom)) return;

    // Both halves must be >= MIN_PANEL_PX
    const splitY = clamp(y, p.top + MIN_PANEL_PX, p.bottom - MIN_PANEL_PX);
    if (splitY - p.top < MIN_PANEL_PX) return;
    if (p.bottom - splitY < MIN_PANEL_PX) return;

    const oldBottom = p.bottom;
    panels[idx] = { top: p.top, bottom: splitY };
    panels.splice(idx + 1, 0, { top: splitY, bottom: oldBottom });

    normalizePanels();
    redrawAllPages();
    setStatus("Split panel.");
  }

  document.addEventListener('keydown', (evt) => {
    const key = evt.key.toLowerCase();
    if (["arrowup", "arrowdown"].includes(key)) evt.preventDefault();

    if (key === "s" && (evt.ctrlKey || evt.metaKey)) { evt.preventDefault(); saveBtn.click(); return; }
    if (key === "s") { splitHoveredPanel(); return; }
    if (key === "c") clearAllBtn.click();
    if (key === "r") autoBtn.click();
    if (key === "f") finishBtn.click();
    if (key === "n") { createLineAfterLastPanel(); return; }

    if (key === "escape") {
      if (currentLineGlobal !== null) {
        currentLineGlobal = null;
        setStatus("Cancelled pending line.");
        redrawAllPages();
      }
    }
  });

  window.addEventListener('resize', () => {
    pages.forEach(ps => { resizePage(ps); redrawPage(ps); });
  });

  // --- Init: auto-mark on open ---
  async function initPanelsOnOpen() {
    if (!ALWAYS_AUTO_ON_LOAD) {
      const localPanels = loadStateFromLocal();
      if (localPanels && localPanels.length) {
        panels = localPanels;
        normalizePanels();
        redrawAllPages();
        setStatus("Restored marks from local storage.");
        return;
      }
    }

    try {
      setStatus("Detecting gutters...");
      const res = await fetch('/initial_panels?refresh=1');
      const data = await res.json();
      if (Array.isArray(data.panels)) {
        panels = data.panels;
        normalizePanels();
        redrawAllPages();
        const dev = data.device ? ` · device=${data.device}` : "";
        const gpu = data.gpu_name ? ` (${data.gpu_name})` : "";
        setStatus(`Loaded gutter auto marks (${panels.length})${dev}${gpu}.`);
      }
    } catch (e) {
      setStatus("Auto-detect failed. (You can still mark manually.)");
    }
  }

  loadImagesList();
})();
