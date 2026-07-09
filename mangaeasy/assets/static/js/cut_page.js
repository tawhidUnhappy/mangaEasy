/**
 * cut_page.js
 * Modular frontend architecture utilizing ES6 Classes.
 */

class AppState {
  constructor() {
    this.images = [];
    this.currentIndex = 0;
    this.globalCache = {};
    this.currentBoxes = [];
    this.isWPressed = false; // Tracks if 'W' is currently held down
    this.selectedBox = null;

    // Set by /api/config on init. Controls panel sort order within each page:
    //   "rtl" — manga (Japan):   right-to-left, top-to-bottom
    //   "ltr" — manhua (China):  left-to-right, top-to-bottom
    this.readingDirection = "rtl";
  }

  get currentImageName() { return this.images[this.currentIndex]; }

  loadBoxesForCurrentPage() {
    this.currentBoxes = this.globalCache[this.currentImageName] ? [...this.globalCache[this.currentImageName]] : [];
    // Always re-sort after load so a direction change in config takes effect immediately
    // even on pages whose boxes were previously cached under a different direction.
    this.sortCurrentBoxes();
  }

  saveBoxesToCache() {
    if (this.currentImageName) this.globalCache[this.currentImageName] = [...this.currentBoxes];
  }

  sortCurrentBoxes() {
    if (this.currentBoxes.length <= 1) return;
    const rtl = this.readingDirection === "rtl";
    const boxes = this.currentBoxes;
    const N = boxes.length;

    const inDegree = new Array(N).fill(0);
    const adj = Array.from({ length: N }, () =>[]);

    // 1. Build the Directed Acyclic Graph
    for (let i = 0; i < N; i++) {
      for (let j = 0; j < N; j++) {
        if (i === j) continue;

        const A = boxes[i], B = boxes[j];
        const cyA = (A.y1 + A.y2) / 2, cyB = (B.y1 + B.y2) / 2;
        const cxA = (A.x1 + A.x2) / 2, cxB = (B.x1 + B.x2) / 2;

        const overlapY = Math.max(0, Math.min(A.y2, B.y2) - Math.max(A.y1, B.y1));
        const minH = Math.min(A.y2 - A.y1, B.y2 - B.y1);

        let isBefore = false;
        // If panels share significant vertical space, sort horizontally
        if (overlapY > 0.3 * minH) {
          isBefore = rtl ? (cxA > cxB) : (cxA < cxB);
        } else {
          // Otherwise, sort top-to-bottom
          isBefore = cyA < cyB;
        }

        // Draw an arrow: i must be read before j
        if (isBefore) {
          adj[i].push(j);
          inDegree[j]++;
        }
      }
    }

    // 2. Kahn's Algorithm (Topological Sort)
    const result =[];
    const visited = new Set();

    while (result.length < N) {
      let candidates =[];
      for (let i = 0; i < N; i++) {
        if (!visited.has(i) && inDegree[i] === 0) {
          candidates.push(i);
        }
      }

      // 3. Cycle breaking: if locked, force-pick the node with minimum remaining dependencies
      if (candidates.length === 0) {
        let minInDegree = Infinity;
        for (let i = 0; i < N; i++) {
          if (!visited.has(i) && inDegree[i] < minInDegree) {
            minInDegree = inDegree[i];
          }
        }
        for (let i = 0; i < N; i++) {
          if (!visited.has(i) && inDegree[i] === minInDegree) {
            candidates.push(i);
          }
        }
      }

      // 4. Tie-breaker: Highest panel wins. If tied, use reading direction.
      candidates.sort((a, b) => {
        const A = boxes[a], B = boxes[b];
        const cyA = (A.y1 + A.y2) / 2, cyB = (B.y1 + B.y2) / 2;
        const cxA = (A.x1 + A.x2) / 2, cxB = (B.x1 + B.x2) / 2;

        // Group into ~10px vertical bands to handle slight misalignments
        const yBinA = Math.floor(cyA / 10);
        const yBinB = Math.floor(cyB / 10);

        if (yBinA !== yBinB) return yBinA - yBinB;
        return rtl ? (cxB - cxA) : (cxA - cxB);
      });

      const best = candidates[0];
      visited.add(best);
      result.push(boxes[best]);

      // Remove processed node's edges
      for (const neighbor of adj[best]) {
        inDegree[neighbor]--;
      }
    }

    this.currentBoxes = result;
  }

  get selectedBoxIndex() {
    return this.selectedBox ? this.currentBoxes.indexOf(this.selectedBox) : -1;
  }
}

class CanvasEditor {
  constructor(containerId, workspaceId, state, onUpdate) {
    this.container = document.getElementById(containerId);
    this.workspace = document.getElementById(workspaceId);
    this.state = state;
    this.canvas = null;
    this.ctx = null;
    this.imageObj = null;

    this.isDrawing = false;
    this.dragAction = null;
    this.activeBoxIndex = -1;
    this.startX = 0; this.startY = 0;
    this.currX = 0; this.currY = 0;
    this.offsetX = 0; this.offsetY = 0;

    // Store last mouse position to update cursor accurately when toggling 'W'
    this.lastRawX = 0;
    this.lastRawY = 0;

    this.onUpdate = onUpdate || (() => {});
    this.bindGlobalEvents();
  }

  loadImage(src, callback) {
    this.imageObj = new Image();
    this.imageObj.src = src;
    this.imageObj.onload = () => {
      this.initCanvas();
      callback();
    };
  }

  initCanvas() {
    this.container.innerHTML = "";
    this.canvas = document.createElement("canvas");
    this.canvas.width = this.imageObj.width;
    this.canvas.height = this.imageObj.height;
    this.ctx = this.canvas.getContext("2d");
    this.container.appendChild(this.canvas);
  }

  getCoords(evt) {
    if (!this.canvas) return { x: 0, y: 0, rawX: 0, rawY: 0 };
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;

    // Raw Coordinates on the entire screen relative to image
    let rawX = (evt.clientX - rect.left) * scaleX;
    let rawY = (evt.clientY - rect.top) * scaleY;

    // Clamped coordinates locked inside the image bounds
    let x = Math.max(0, Math.min(this.canvas.width, rawX));
    let y = Math.max(0, Math.min(this.canvas.height, rawY));

    return { x, y, rawX, rawY };
  }

  getHit(rawX, rawY) {
    if (!this.canvas) return null;
    const scale = this.canvas.width / this.canvas.getBoundingClientRect().width;
    const r = 15 * scale;
    const boxes = this.state.currentBoxes;

    // Selected box is checked first so it stays reachable even when buried under others
    const selIdx = this.state.selectedBoxIndex;
    const order = [];
    if (selIdx >= 0) order.push(selIdx);
    for (let i = boxes.length - 1; i >= 0; i--) { if (i !== selIdx) order.push(i); }

    for (const i of order) {
      const b = boxes[i];
      if (Math.abs(rawX - b.x1) < r && Math.abs(rawY - b.y1) < r) return { index: i, action: 'tl' };
      if (Math.abs(rawX - b.x2) < r && Math.abs(rawY - b.y1) < r) return { index: i, action: 'tr' };
      if (Math.abs(rawX - b.x1) < r && Math.abs(rawY - b.y2) < r) return { index: i, action: 'bl' };
      if (Math.abs(rawX - b.x2) < r && Math.abs(rawY - b.y2) < r) return { index: i, action: 'br' };
      if (rawY > b.y1 && rawY < b.y2 && Math.abs(rawX - b.x1) < r) return { index: i, action: 'l' };
      if (rawY > b.y1 && rawY < b.y2 && Math.abs(rawX - b.x2) < r) return { index: i, action: 'r' };
      if (rawX > b.x1 && rawX < b.x2 && Math.abs(rawY - b.y1) < r) return { index: i, action: 't' };
      if (rawX > b.x1 && rawX < b.x2 && Math.abs(rawY - b.y2) < r) return { index: i, action: 'b' };
      if (rawX > b.x1 && rawX < b.x2 && rawY > b.y1 && rawY < b.y2) return { index: i, action: 'move' };
    }
    return null;
  }

  getAllBoxesAt(rawX, rawY) {
    if (!this.canvas) return [];
    return this.state.currentBoxes
      .map((b, i) => ({ index: i, b }))
      .filter(({ b }) => rawX > b.x1 && rawX < b.x2 && rawY > b.y1 && rawY < b.y2)
      .map(({ index }) => index);
  }

  updateCursor(rawX, rawY) {
    this.lastRawX = rawX;
    this.lastRawY = rawY;

    // Force draw mode overrides all other cursors
    if (this.state.isWPressed) {
      this.workspace.style.cursor = 'crosshair';
      return;
    }

    const hit = this.getHit(rawX, rawY);
    if (hit) {
      const cursors = {
        'tl': 'nwse-resize', 'br': 'nwse-resize', 'tr': 'nesw-resize', 'bl': 'nesw-resize',
        't': 'ns-resize', 'b': 'ns-resize', 'l': 'ew-resize', 'r': 'ew-resize', 'move': 'move'
      };
      this.workspace.style.cursor = cursors[hit.action];
    } else {
      const isOutside = (rawX < 0 || rawX > this.canvas.width || rawY < 0 || rawY > this.canvas.height);
      this.workspace.style.cursor = isOutside ? 'default' : 'crosshair';
    }
  }

  refreshCursor() {
    this.updateCursor(this.lastRawX, this.lastRawY);
  }

  bindGlobalEvents() {
    // We bind to the entire WORKSPACE now to catch clicks from outside the canvas image
    this.workspace.addEventListener("mousedown", (e) => this.onMouseDown(e));
    window.addEventListener("mousemove", (e) => this.onMouseMove(e));
    window.addEventListener("mouseup", (e) => this.onMouseUp(e));
    this.workspace.addEventListener("contextmenu", (e) => this.onContextMenu(e));
  }

  onMouseDown(e) {
    if (!this.canvas || e.button !== 0) return;
    const c = this.getCoords(e);

    // Alt+click: cycle selection through all overlapping boxes without starting a drag
    if (e.altKey && !this.state.isWPressed) {
      const overlapping = this.getAllBoxesAt(c.rawX, c.rawY);
      if (overlapping.length > 0) {
        const currSel = this.state.selectedBoxIndex;
        const pos = overlapping.indexOf(currSel);
        const nextIdx = overlapping[(pos + 1) % overlapping.length];
        this.state.selectedBox = this.state.currentBoxes[nextIdx];
        this.draw();
        this.onUpdate();
      }
      return;
    }

    // If 'W' is pressed, completely ignore hit logic and force drawing
    const hit = this.state.isWPressed ? null : this.getHit(c.rawX, c.rawY);

    this.isDrawing = true;
    if (hit) {
      this.dragAction = hit.action;
      this.activeBoxIndex = hit.index;
      const b = this.state.currentBoxes[hit.index];
      this.offsetX = c.rawX - b.x1;
      this.offsetY = c.rawY - b.y1;
    } else {
      this.dragAction = 'draw';
      this.startX = c.x; this.startY = c.y;
      this.currX = c.x; this.currY = c.y;
    }
  }

  onMouseMove(e) {
    if (!this.canvas) return;
    const c = this.getCoords(e);

    if (!this.isDrawing) { this.updateCursor(c.rawX, c.rawY); return; }

    if (this.dragAction === 'draw') {
      this.currX = c.x; this.currY = c.y;
    } else {
      const b = this.state.currentBoxes[this.activeBoxIndex];
      if (this.dragAction === 'move') {
        const w = b.x2 - b.x1; const h = b.y2 - b.y1;
        let nx = c.rawX - this.offsetX; let ny = c.rawY - this.offsetY;
        b.x1 = Math.max(0, Math.min(this.canvas.width - w, nx));
        b.y1 = Math.max(0, Math.min(this.canvas.height - h, ny));
        b.x2 = b.x1 + w; b.y2 = b.y1 + h;
      } else {
        if (this.dragAction.includes('l')) b.x1 = c.x;
        if (this.dragAction.includes('r')) b.x2 = c.x;
        if (this.dragAction.includes('t')) b.y1 = c.y;
        if (this.dragAction.includes('b')) b.y2 = c.y;

        if (b.x1 > b.x2) { [b.x1, b.x2] = [b.x2, b.x1]; this.dragAction = this.dragAction.replace('l','X').replace('r','l').replace('X','r'); }
        if (b.y1 > b.y2) { [b.y1, b.y2] = [b.y2, b.y1]; this.dragAction = this.dragAction.replace('t','X').replace('b','t').replace('X','b'); }
      }
    }
    this.draw();
  }

  onMouseUp(e) {
    if (!this.isDrawing) return;
    this.isDrawing = false;

    if (this.dragAction === 'draw') {
      const x1 = Math.min(this.startX, this.currX); const x2 = Math.max(this.startX, this.currX);
      const y1 = Math.min(this.startY, this.currY); const y2 = Math.max(this.startY, this.currY);

      if (x2 - x1 > 20 && y2 - y1 > 20) {
        this.state.currentBoxes.push({ x1, y1, x2, y2 });
      }
    }

    this.dragAction = null; this.activeBoxIndex = -1;
    this.state.sortCurrentBoxes();
    this.draw();
  }

  onContextMenu(e) {
    e.preventDefault();
    if (!this.canvas) return;
    const c = this.getCoords(e);

    // Ignore hits if holding W
    const hit = this.state.isWPressed ? null : this.getHit(c.rawX, c.rawY);
    if (hit) {
      if (this.state.currentBoxes[hit.index] === this.state.selectedBox) this.state.selectedBox = null;
      this.state.currentBoxes.splice(hit.index, 1);
      this.state.sortCurrentBoxes();
      this.draw();
    }
  }

  draw() {
    if (!this.canvas || !this.imageObj) return;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.drawImage(this.imageObj, 0, 0);

    const scale = this.canvas.width / this.canvas.getBoundingClientRect().width;
    this.ctx.lineWidth = 3 * scale;
    const hSize = 5 * scale;
    const fontSize = Math.max(20, 30 * scale);
    this.ctx.font = `bold ${fontSize}px Arial`;
    this.ctx.textBaseline = "top";

    const selIdx = this.state.selectedBoxIndex;
    this.state.currentBoxes.forEach((box, i) => {
      const isSel = (i === selIdx);
      this.ctx.fillStyle = isSel ? "rgba(0, 191, 255, 0.2)" : "rgba(0, 255, 0, 0.15)";
      this.ctx.fillRect(box.x1, box.y1, box.x2 - box.x1, box.y2 - box.y1);

      this.ctx.strokeStyle = isSel ? "#00BFFF" : "#00FF00";
      this.ctx.strokeRect(box.x1, box.y1, box.x2 - box.x1, box.y2 - box.y1);

      this.ctx.fillStyle = "white"; this.ctx.strokeStyle = "black";
      const crns = [{x:box.x1,y:box.y1}, {x:box.x2,y:box.y1}, {x:box.x1,y:box.y2}, {x:box.x2,y:box.y2}];
      crns.forEach(c => {
        this.ctx.fillRect(c.x - hSize, c.y - hSize, hSize*2, hSize*2);
        this.ctx.strokeRect(c.x - hSize, c.y - hSize, hSize*2, hSize*2);
      });

      const txt = box.panelNum != null ? String(box.panelNum) : String(i + 1);
      const txtX = box.x2 - (fontSize + 10 * scale); const txtY = box.y1 + (5 * scale);
      this.ctx.fillStyle = "black";
      this.ctx.fillRect(txtX - (5*scale), txtY - (5*scale), fontSize + (10*scale), fontSize + (10*scale));
      this.ctx.fillStyle = "yellow";
      this.ctx.fillText(txt, txtX, txtY);
    });

    if (this.isDrawing && this.dragAction === 'draw') {
      this.ctx.strokeStyle = "rgba(255, 0, 0, 0.8)";
      this.ctx.lineWidth = 3 * scale;
      this.ctx.strokeRect(this.startX, this.startY, this.currX - this.startX, this.currY - this.startY);
    }
    this.onUpdate();
  }
}

class AppController {
  constructor() {
    this.state = new AppState();
    this.editor = new CanvasEditor("canvas-container", "workspace", this.state, () => {
      this.updatePanelCount();
      this.renderBoxProps();
    });
    this.autoAllES = null;

    this.ui = {
      status: document.getElementById("status"),
      counter: document.getElementById("imageCounter"),
      prevBtn: document.getElementById("prevBtn"),
      nextBtn: document.getElementById("nextBtn")
    };

    this.bindButtons();
    this.initApp();
  }

  setStatus(msg) { this.ui.status.textContent = msg; }

  updatePanelCount() {
    const count = this.state.currentBoxes.length;
    const el = document.getElementById("panelCount");
    if (el) el.textContent = `${count} panel${count !== 1 ? 's' : ''}`;
    this.updateTotalPanelCount();
  }

  updateTotalPanelCount() {
    const curr = this.state.currentImageName;
    let total = 0;
    for (const name of this.state.images) {
      if (name === curr) {
        total += this.state.currentBoxes.length;
      } else if (this.state.globalCache[name]) {
        total += this.state.globalCache[name].length;
      }
    }
    const el = document.getElementById("totalPanelCount");
    if (el) el.textContent = total > 0 ? `· ${total} total` : "";
    const badge = document.getElementById("layerCountBadge");
    if (badge) badge.textContent = this.state.currentBoxes.length;
  }

  updateUI() {
    this.updatePanelCount();
    this.renderLayerList();
    this.renderBoxProps();
  }

  renderBoxProps() {
    const emptyEl = document.getElementById("boxPropsEmpty");
    const formEl  = document.getElementById("boxPropsForm");
    if (!emptyEl || !formEl) return;

    const box = this.state.selectedBox;
    if (!box) {
      emptyEl.style.display = "block";
      formEl.style.display  = "none";
      return;
    }

    emptyEl.style.display = "none";
    formEl.style.display  = "";

    // Only update inputs not currently focused to avoid clobbering user typing
    const active = document.activeElement;
    const propX = document.getElementById("propX");
    const propY = document.getElementById("propY");
    const propW = document.getElementById("propW");
    const propH = document.getElementById("propH");
    const propPN = document.getElementById("propPanelNum");

    if (active !== propX) propX.value = Math.round(box.x1);
    if (active !== propY) propY.value = Math.round(box.y1);
    if (active !== propW) propW.value = Math.round(box.x2 - box.x1);
    if (active !== propH) propH.value = Math.round(box.y2 - box.y1);
    if (active !== propPN) propPN.value = box.panelNum != null ? box.panelNum : "";
  }

  applyBoxProps() {
    const box = this.state.selectedBox;
    if (!box) return;
    const x = parseInt(document.getElementById("propX").value, 10);
    const y = parseInt(document.getElementById("propY").value, 10);
    const w = parseInt(document.getElementById("propW").value, 10);
    const h = parseInt(document.getElementById("propH").value, 10);
    if (isNaN(x) || isNaN(y) || isNaN(w) || isNaN(h) || w < 1 || h < 1) return;
    box.x1 = Math.max(0, x);
    box.y1 = Math.max(0, y);
    box.x2 = box.x1 + w;
    box.y2 = box.y1 + h;
    this.editor.draw();
  }

  renderLayerList() {
    const list = document.getElementById("layerList");
    if (!list) return;
    const boxes = this.state.currentBoxes;
    const selIdx = this.state.selectedBoxIndex;
    list.innerHTML = "";
    boxes.forEach((box, i) => {
      const item = document.createElement("div");
      item.className = "layer-item" + (i === selIdx ? " selected" : "");

      const numSpan = document.createElement("span");
      numSpan.className = "layer-num";
      numSpan.textContent = i + 1;
      numSpan.title = "Double-click to change panel order";

      const coords = document.createElement("span");
      coords.className = "layer-coords";
      coords.textContent = `${Math.round(box.x1)},${Math.round(box.y1)}→${Math.round(box.x2)},${Math.round(box.y2)}`;

      const acts = document.createElement("div");
      acts.className = "layer-actions";

      const upBtn = document.createElement("button");
      upBtn.textContent = "↑";
      upBtn.title = "Move earlier in order";
      upBtn.disabled = i === 0;
      upBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        [boxes[i - 1], boxes[i]] = [boxes[i], boxes[i - 1]];
        this.editor.draw();
        this.updateUI();
      });

      const dnBtn = document.createElement("button");
      dnBtn.textContent = "↓";
      dnBtn.title = "Move later in order";
      dnBtn.disabled = i === boxes.length - 1;
      dnBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        [boxes[i], boxes[i + 1]] = [boxes[i + 1], boxes[i]];
        this.editor.draw();
        this.updateUI();
      });

      acts.appendChild(upBtn);
      acts.appendChild(dnBtn);
      item.appendChild(numSpan);
      item.appendChild(coords);

      // Custom panel number chip (shown only when override is set)
      if (box.panelNum != null) {
        const chip = document.createElement("span");
        chip.className = "layer-custom-num";
        chip.textContent = `#${box.panelNum}`;
        chip.title = `Output panel number overridden to ${box.panelNum}`;
        item.appendChild(chip);
      }

      item.appendChild(acts);

      item.addEventListener("click", () => {
        this.state.selectedBox = (this.state.selectedBox === box) ? null : box;
        this.editor.draw();
        this.renderLayerList();
      });

      numSpan.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        this.startEditOrder(numSpan, box);
      });

      list.appendChild(item);
    });
  }

  startEditOrder(numSpan, box) {
    const currentIdx = this.state.currentBoxes.indexOf(box);
    if (currentIdx < 0) return;
    const input = document.createElement("input");
    input.type = "number";
    input.value = currentIdx + 1;
    input.min = 1;
    input.max = this.state.currentBoxes.length;
    input.className = "layer-num-input";
    let committed = false;
    const commit = () => {
      if (committed) return;
      committed = true;
      const newPos = parseInt(input.value, 10);
      const fromIdx = this.state.currentBoxes.indexOf(box);
      if (!isNaN(newPos) && fromIdx >= 0) {
        const toIdx = Math.max(0, Math.min(this.state.currentBoxes.length - 1, newPos - 1));
        if (fromIdx !== toIdx) {
          this.state.currentBoxes.splice(fromIdx, 1);
          this.state.currentBoxes.splice(toIdx, 0, box);
        }
      }
      this.editor.draw();
      this.updateUI();
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { committed = true; this.updateUI(); }
    });
    input.addEventListener("blur", commit);
    numSpan.replaceWith(input);
    input.focus();
    input.select();
  }

  async initApp() {
    this.setStatus("Loading session...");

    // Load reading direction from system config before anything else
    try {
      const cfgRes = await fetch("/api/config");
      const cfg = await cfgRes.json();
      this.state.readingDirection = cfg.reading_direction || "rtl";
    } catch(e) { console.warn("Could not load /api/config, defaulting to rtl."); }

    try {
      const pRes = await fetch("/load_progress");
      const pData = await pRes.json();
      if (pData.progress) this.state.globalCache = pData.progress;
    } catch(e) { console.warn("No previous progress found."); }

    const res = await fetch("/images");
    const data = await res.json();
    this.state.images = data.images || [];

    if (this.state.images.length === 0) {
      this.setStatus("No images found in directory."); return;
    }
    this.changePage(0);

    // Auto-start AI detection for all pages on launch.
    // Pages that already have saved boxes are skipped automatically.
    setTimeout(() => document.getElementById("autoAllBtn").click(), 600);
  }

  async syncProgressToServer() {
    if (!this.state.currentImageName) return;
    this.state.saveBoxesToCache();
    try {
      await fetch("/save_progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_name: this.state.currentImageName, boxes: this.state.currentBoxes })
      });
      this.setStatus("Progress auto-saved.");
    } catch (e) { this.setStatus("Failed to auto-save!"); }
  }

  async changePage(index) {
    if (index < 0 || index >= this.state.images.length) return;
    if (this.editor.imageObj) await this.syncProgressToServer();

    this.state.currentIndex = index;
    this.state.selectedBox = null;
    this.state.loadBoxesForCurrentPage();

    this.ui.counter.textContent = `Image: ${index + 1} / ${this.state.images.length}`;
    this.setStatus(`Loaded ${this.state.currentImageName}`);

    this.ui.prevBtn.disabled = (index === 0);
    this.ui.nextBtn.disabled = (index === this.state.images.length - 1);

    const src = `/image/${encodeURIComponent(this.state.currentImageName)}`;
    this.editor.loadImage(src, () => { this.editor.draw(); this.updateUI(); });
  }

  bindButtons() {
    this.ui.prevBtn.addEventListener("click", () => this.changePage(this.state.currentIndex - 1));
    this.ui.nextBtn.addEventListener("click", () => this.changePage(this.state.currentIndex + 1));

    document.getElementById("undoBtn").addEventListener("click", () => { this.state.currentBoxes.pop(); this.editor.draw(); this.updateUI(); });
    document.getElementById("clearBtn").addEventListener("click", () => { this.state.currentBoxes = []; this.state.selectedBox = null; this.editor.draw(); this.updateUI(); });

    document.getElementById("autoDetectBtn").addEventListener("click", async () => {
      if (!this.state.currentImageName) return;
      const btn = document.getElementById("autoDetectBtn");
      btn.disabled = true;
      this.setStatus("🤖 MAGI v2 AI detecting…");
      try {
        const res = await fetch(`/auto_detect/${encodeURIComponent(this.state.currentImageName)}`);
        const data = await res.json();
        if (data.status === "ok") {
          this.state.currentBoxes = data.boxes;
          this.state.selectedBox = null;
          this.state.sortCurrentBoxes();
          this.editor.draw();
          this.updateUI();
          if (data.source === "magi_ai") {
            this.setStatus(`🤖 AI: ${data.panels_found} panel(s) detected. Adjust if needed.`);
          } else if (data.source === "none") {
            this.setStatus("⚠️ AI found no panels — draw manually.");
          } else {
            this.setStatus(`❌ AI error — draw manually.`);
          }
        } else {
          this.setStatus(`❌ Error: ${data.message}`);
        }
      } catch(e) {
        this.setStatus("AI detect request failed.");
      } finally {
        btn.disabled = false;
      }
    });

    // Auto-detect all pages via SSE
    document.getElementById("autoAllBtn").addEventListener("click", () => {
      if (this.autoAllES) { this.autoAllES.close(); this.autoAllES = null; }

      const overlay   = document.getElementById("autoAllOverlay");
      const logEl     = document.getElementById("autoAllLog");
      const fillEl    = document.getElementById("autoAllFill");
      const pctEl     = document.getElementById("autoAllPercent");
      const currentEl = document.getElementById("autoAllCurrent");
      const stopBtn   = document.getElementById("autoAllStopBtn");
      const closeBtn  = document.getElementById("autoAllCloseBtn");

      overlay.style.display = "flex";
      logEl.innerHTML = "";
      fillEl.style.width = "0%";
      pctEl.textContent = "0%";
      currentEl.textContent = "Loading MAGI v2 AI model (first run may take ~30s)…";
      stopBtn.style.display = "inline-block";
      closeBtn.style.display = "none";

      this.autoAllES = new EventSource("/auto_detect_all");

      this.autoAllES.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.done) {
          this.autoAllES.close(); this.autoAllES = null;
          fillEl.style.width = "100%";
          pctEl.textContent = "100%";
          currentEl.textContent = `✅ Done — ${data.total} pages processed`;
          stopBtn.style.display = "none";
          closeBtn.style.display = "inline-block";
          this.setStatus(`Auto-detect complete: ${data.total} pages.`);
          return;
        }

        // Update in-memory cache so navigating shows results immediately
        this.state.globalCache[data.page] = data.boxes;

        // Refresh canvas if this is the page currently displayed
        if (data.page === this.state.currentImageName) {
          this.state.loadBoxesForCurrentPage();
          this.editor.draw();
          this.updateUI();
        }

        const pct = Math.round((data.index + 1) / data.total * 100);
        fillEl.style.width = pct + "%";
        pctEl.textContent = pct + "%";
        currentEl.textContent = `Page ${data.index + 1} / ${data.total} — ${data.page}`;

        const badge =
          data.source === "magi_ai" ? `<span class="badge-ai">🤖 AI · ${data.panels_found} panels</span>`
        : data.source === "cached"  ? `<span class="badge-cached">💾 ${data.panels_found} cached</span>`
        : data.source === "error"   ? `<span class="badge-error">❌ Error</span>`
        :                             `<span class="badge-none">⚠️ No panels</span>`;

        const entry = document.createElement("div");
        entry.className = "log-entry";
        entry.innerHTML = `<span class="log-idx">${data.index + 1}</span><span class="log-name">${data.page}</span>${badge}`;
        logEl.appendChild(entry);
        logEl.scrollTop = logEl.scrollHeight;
      };

      this.autoAllES.onerror = () => {
        if (this.autoAllES) { this.autoAllES.close(); this.autoAllES = null; }
        currentEl.textContent = "❌ Connection error — see server console.";
        stopBtn.style.display = "none";
        closeBtn.style.display = "inline-block";
      };
    });

    document.getElementById("autoAllStopBtn").addEventListener("click", () => {
      if (this.autoAllES) { this.autoAllES.close(); this.autoAllES = null; }
      document.getElementById("autoAllCurrent").textContent = "⏹ Stopped by user.";
      document.getElementById("autoAllStopBtn").style.display = "none";
      document.getElementById("autoAllCloseBtn").style.display = "inline-block";
    });

    document.getElementById("autoAllCloseBtn").addEventListener("click", () => {
      document.getElementById("autoAllOverlay").style.display = "none";
    });

    document.getElementById("layerToggleBtn").addEventListener("click", () => {
      const sidebar = document.getElementById("layerSidebar");
      const isOpen = sidebar.classList.toggle("open");
      document.getElementById("layerToggleBtn").classList.toggle("active", isOpen);
      if (isOpen) this.updateUI();
    });

    // Properties panel — position/size inputs
    const commitProps = () => this.applyBoxProps();
    for (const id of ["propX", "propY", "propW", "propH"]) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.addEventListener("change", commitProps);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commitProps(); } });
    }

    // Properties panel — panel number override input
    const propPN = document.getElementById("propPanelNum");
    if (propPN) {
      const commitPN = () => {
        const box = this.state.selectedBox;
        if (!box) return;
        const val = propPN.value.trim();
        const num = parseInt(val, 10);
        box.panelNum = (val === "" || isNaN(num) || num < 1) ? null : num;
        this.editor.draw();
        this.renderLayerList();
      };
      propPN.addEventListener("change", commitPN);
      propPN.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commitPN(); } });
    }

    document.getElementById("layerSortBtn").addEventListener("click", () => {
      this.state.selectedBox = null;
      this.state.sortCurrentBoxes();
      this.editor.draw();
      this.updateUI();
    });

    document.getElementById("fullPageBtn").addEventListener("click", () => {
      if (!this.editor.imageObj) return;
      this.state.currentBoxes.push({ x1: 0, y1: 0, x2: this.editor.imageObj.width, y2: this.editor.imageObj.height });
      this.state.sortCurrentBoxes();
      this.editor.draw();
      this.updateUI();
    });

    document.getElementById("cropAllBtn").addEventListener("click", async () => {
      await this.syncProgressToServer();
      if (!confirm("Are you done? This will crop and save all pages now.")) return;

      this.setStatus("Cropping all images... This may take a moment.");
      try {
        const res = await fetch("/crop_all", { method: "POST" });
        const data = await res.json();
        if (data.status === "ok") {

          // Call shutdown API which waits 1 second on backend
          await fetch("/shutdown", { method: "POST" });

          // Build a graceful exit / completion screen
          document.body.innerHTML = `
            <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#121212; color:#fff; font-family:sans-serif; text-align:center;">
              <h1 style="color:#b7f0c8; font-size:48px; margin-bottom:10px;">✅ Success!</h1>
              <p style="font-size:18px; color:#aaa;">Successfully cropped and saved ${data.saved_count} panels.</p>
              <p style="font-size:16px; color:#888;">The local backend server has safely shut down.</p>
              <p style="font-size:20px; margin-top:30px; font-weight:bold;">You may now safely close this window.</p>
            </div>
          `;

          // Attempt to close the tab programmatically
          setTimeout(() => {
            window.open('', '_self', '');
            window.close();
          }, 1000);

        } else { this.setStatus(`Error: ${data.message}`); }
      } catch (err) { this.setStatus("Network error during crop."); }
    });

    // Keyboard Tracking
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      // Track 'W' key for force-draw
      if (e.key.toLowerCase() === "w") {
        this.state.isWPressed = true;
        this.editor.refreshCursor();
      }

      if (e.key.toLowerCase() === "z" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); document.getElementById("undoBtn").click(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      if (e.key === "ArrowLeft") { e.preventDefault(); this.ui.prevBtn.click(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); this.ui.nextBtn.click(); }
      else if (e.key.toLowerCase() === "f") { e.preventDefault(); document.getElementById("fullPageBtn").click(); }
      else if (e.key.toLowerCase() === "c") { e.preventDefault(); document.getElementById("clearBtn").click(); }
      else if (e.key.toLowerCase() === "a") { e.preventDefault(); document.getElementById("autoDetectBtn").click(); }
      else if (e.key.toLowerCase() === "l") { e.preventDefault(); document.getElementById("layerToggleBtn").click(); }
    });

    document.addEventListener("keyup", (e) => {
      // Release 'W' key
      if (e.key.toLowerCase() === "w") {
        this.state.isWPressed = false;
        this.editor.refreshCursor();
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  new AppController();
  initLogSidebar();
  initGpuBadge();
});

// ── GPU Badge ────────────────────────────────────────────────────────────────
function initGpuBadge() {
  const badge = document.getElementById("gpuBadge");

  async function refresh() {
    try {
      const res  = await fetch("/api/gpu_info");
      const data = await res.json();
      if (data.cuda) {
        const usedPct = Math.round(data.used_mb / data.total_mb * 100);
        badge.textContent = `🟢 ${data.name}  ${data.used_mb}/${data.total_mb} MB`;
        badge.title = `CUDA · ${data.torch}\nVRAM: ${data.used_mb} MB used / ${data.total_mb} MB total (${usedPct}% used)`;
        badge.className = "gpu-badge gpu-ok";
      } else {
        badge.textContent = "🔴 CPU only";
        badge.title = "No CUDA GPU detected — AI will run on CPU (slower)";
        badge.className = "gpu-badge gpu-cpu";
      }
    } catch {
      badge.textContent = "⚠ GPU?";
      badge.className = "gpu-badge gpu-pending";
    }
  }

  refresh();
  setInterval(refresh, 10_000);   // update VRAM usage every 10 s
}

// ── Log Sidebar ───────────────────────────────────────────────────────────────
function initLogSidebar() {
  const sidebar    = document.getElementById("logSidebar");
  const body       = document.getElementById("logBody");
  const toggleBtn  = document.getElementById("logToggleBtn");
  const clearBtn   = document.getElementById("logClearBtn");
  const badge      = document.getElementById("logUnreadBadge");

  let open   = false;
  let unread = 0;
  let logES  = null;

  function classify(msg) {
    const m = msg.toLowerCase();
    if (m.includes("error") || m.includes("traceback") || m.includes("exception")
        || m.includes("unavailable") || m.includes("failed") || m.includes("❌"))
      return "error";
    if (m.includes("warn") || m.includes("⚠"))
      return "warn";
    if (m.includes("[manga-ai]") || m.includes("magi") || m.includes("deepseek-ocr")
        || m.includes("✅") || m.includes("ready"))
      return "ai";
    if (m.includes("[info]") || m.includes("loading") || m.includes("starting")
        || m.includes("server") || m.includes("port"))
      return "info";
    if (m.includes("127.0.0.1"))
      return "dim";
    return "";
  }

  function esc(s) {
    return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  function append(ts, msg) {
    const line = document.createElement("div");
    line.className = "log-line " + classify(msg);
    line.innerHTML = `<span class="log-ts">${ts}</span><span class="log-msg">${esc(msg)}</span>`;
    body.appendChild(line);
    while (body.children.length > 500) body.removeChild(body.firstChild);

    if (open) {
      body.scrollTop = body.scrollHeight;
    } else {
      unread++;
      badge.textContent = unread > 99 ? "99+" : String(unread);
      badge.style.display = "block";
    }
  }

  function setOpen(on) {
    open = on;
    sidebar.classList.toggle("open", on);
    toggleBtn.classList.toggle("active", on);
    if (on) {
      unread = 0;
      badge.style.display = "none";
      body.scrollTop = body.scrollHeight;
    }
  }

  toggleBtn.addEventListener("click", () => setOpen(!open));
  clearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    body.innerHTML = "";
    unread = 0;
    badge.style.display = "none";
  });

  function connect() {
    if (logES) logES.close();
    logES = new EventSource("/log_stream");
    logES.onmessage = (e) => {
      const d = JSON.parse(e.data);
      if (d.ping) return;
      append(d.ts, d.msg);
    };
    logES.onerror = () => { logES.close(); setTimeout(connect, 3000); };
  }

  connect();
}
