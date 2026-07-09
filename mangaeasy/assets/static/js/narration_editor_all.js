(() => {
  // ── URL state ──────────────────────────────────────────────────────────────
  function getQueryIndex() {
    const n = Number(new URLSearchParams(window.location.search).get("i"));
    return Number.isFinite(n) ? n : 0;
  }

  function setQueryIndex(i) {
    const url = new URL(window.location.href);
    url.searchParams.set("i", String(i));
    window.history.pushState({}, "", url.toString());
  }

  function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }

  // ── WAV encode (mono 16-bit PCM) ───────────────────────────────────────────
  function writeString(view, offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  }

  function floatTo16BitPCM(view, offset, input) {
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(offset + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }

  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    writeString(view, 0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(view, 8, "WAVE");
    writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(view, 36, "data");
    view.setUint32(40, samples.length * 2, true);
    floatTo16BitPCM(view, 44, samples);
    return new Blob([buffer], { type: "audio/wav" });
  }

  function mergeBuffers(buffers, totalLength) {
    const result = new Float32Array(totalLength);
    let offset = 0;
    for (const b of buffers) { result.set(b, offset); offset += b.length; }
    return result;
  }

  // ── DOM refs ───────────────────────────────────────────────────────────────
  const pageCounter    = document.getElementById("pageCounter");
  const imageFilename  = document.getElementById("imageFilename");
  const chapterBadge   = document.getElementById("chapterBadge");
  const previewImage   = document.getElementById("previewImage");
  const noImage        = document.getElementById("noImage");
  const textArea       = document.getElementById("narrationText");
  const statusLabel    = document.getElementById("save-status");
  const loader         = document.getElementById("loader");
  const prevBtn        = document.getElementById("prevBtn");
  const nextBtn        = document.getElementById("nextBtn");
  const finishBtn      = document.getElementById("finishBtn");
  const micToggleBtn   = document.getElementById("micToggleBtn");
  const micStatus      = document.getElementById("micStatus");
  const whisperStatus  = document.getElementById("whisperStatus");
  const lastTranscript = document.getElementById("lastTranscript");
  const insertModeBtn  = document.getElementById("insertModeBtn");
  const clearLastBtn   = document.getElementById("clearLastBtn");
  const panelList      = document.getElementById("panel-list");
  const panelJumpInput = document.getElementById("panelJumpInput");
  const panelJumpBtn   = document.getElementById("panelJumpBtn");
  const matchCount     = document.getElementById("panel-match-count");

  // ── State ──────────────────────────────────────────────────────────────────
  let currentIndex = 0;
  let totalItems   = 0;
  let finished     = false;
  let dirty        = false;
  let saveTimeout  = null;
  let insertMode   = "append";
  let isRecording  = false;

  // Audio capture
  let mediaStream    = null;
  let audioContext   = null;
  let sourceNode     = null;
  let processorNode  = null;
  let silentGain     = null;
  let sampleRate     = 16000;
  let recordingBuffers = [];
  let recordingLength  = 0;
  let chunkBuffers   = [];
  let chunkLength    = 0;
  let chunkTimer     = null;
  let chunkInFlight  = false;
  let liveText       = "";
  const LIVE_CHUNK_MS = 2000;

  // Panel navigator state
  let allPanels        = [];
  let filteredPanels   = [];
  let collapsedChapters = new Set();

  // ── API ────────────────────────────────────────────────────────────────────
  async function fetchState(idx) {
    const res = await fetch(`/api/state?i=${encodeURIComponent(idx)}`);
    return res.json();
  }

  async function saveTextNow() {
    if (!dirty) return;
    loader.style.display = "block";
    try {
      const res = await fetch("/api/update_text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: currentIndex, text: textArea.value }),
      });
      const data = await res.json();
      if (data.status === "ok") {
        dirty = false;
        statusLabel.textContent = "All changes saved";
        statusLabel.style.color = "#888";
        // Update hasNarration indicator in panel list
        updatePanelNarrationStatus(currentIndex, !!textArea.value.trim());
      } else {
        statusLabel.textContent = "Error saving!";
        statusLabel.style.color = "#f44336";
      }
    } catch {
      statusLabel.textContent = "Connection Error";
      statusLabel.style.color = "#f44336";
    } finally {
      loader.style.display = "none";
    }
  }

  async function transcribe(blob, mode) {
    const form = new FormData();
    form.append("audio", blob, `speech_${mode}.wav`);
    form.append("mode", mode);
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    return res.json();
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function render(state) {
    if (state.status !== "ok") {
      alert("Failed to load state: " + (state.msg || "unknown"));
      return;
    }

    finished = !!state.finished;
    if (finished) {
      document.body.innerHTML =
        "<div style='color:white;text-align:center;margin-top:50px'><h1>All Done!</h1><p>No narration items found.</p></div>";
      return;
    }

    totalItems   = state.total;
    currentIndex = state.index;

    const item  = state.item || {};
    const img   = (item.image   || "").trim();
    const narr  = item.narration || "";
    const ch    = item.chapter   || "";

    pageCounter.textContent   = `Page ${currentIndex + 1} of ${totalItems}`;
    imageFilename.textContent = img;

    if (ch) {
      chapterBadge.textContent = `Ch.${ch}`;
      chapterBadge.classList.add("visible");
    } else {
      chapterBadge.classList.remove("visible");
    }

    textArea.value = narr;
    dirty = false;
    statusLabel.textContent = "All changes saved";
    statusLabel.style.color = "#888";

    prevBtn.disabled = currentIndex <= 0;
    nextBtn.disabled = currentIndex >= totalItems - 1;

    if (img && ch) {
      previewImage.src = `/images/${encodeURIComponent(ch)}/${encodeURIComponent(img)}`;
      previewImage.style.display = "block";
      noImage.style.display = "none";
    } else {
      previewImage.style.display = "none";
      noImage.style.display = "block";
    }

    if (state.whisper) {
      whisperStatus.textContent =
        `Whisper: ${state.whisper.model} | ${state.whisper.device}/${state.whisper.compute}`;
    }

    // Sync panel list highlight
    highlightActivePanel(currentIndex);
  }

  async function loadIndex(idx) {
    if (isRecording) await stopRecording({ discard: true });
    idx = clamp(idx, 0, Math.max(0, totalItems - 1));
    setQueryIndex(idx);
    const state = await fetchState(idx);
    render(state);
  }

  // ── Autosave ───────────────────────────────────────────────────────────────
  function markDirty() {
    dirty = true;
    statusLabel.textContent = "Unsaved changes...";
    statusLabel.style.color = "#e5c07b";
  }

  function queueSave(delayMs = 800) {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(saveTextNow, delayMs);
  }

  textArea.addEventListener("input", () => { markDirty(); queueSave(800); });

  // ── Navigation buttons ─────────────────────────────────────────────────────
  prevBtn.addEventListener("click", async () => { await saveTextNow(); await loadIndex(currentIndex - 1); });
  nextBtn.addEventListener("click", async () => { await saveTextNow(); await loadIndex(currentIndex + 1); });

  // ── Finish ─────────────────────────────────────────────────────────────────
  finishBtn.addEventListener("click", async () => {
    if (!confirm("All done? Save and close the editor?")) return;
    finishBtn.disabled = true;
    try {
      await saveTextNow();
      await fetch("/shutdown", { method: "POST" });
      document.body.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
          height:100vh;background:#1e1e1e;color:#fff;font-family:sans-serif;text-align:center;">
          <h1 style="color:#b7f0c8;font-size:48px;margin-bottom:10px;">✅ Success!</h1>
          <p style="font-size:18px;color:#aaa;">All narrations have been saved.</p>
          <p style="font-size:16px;color:#888;">The local backend server has safely shut down.</p>
          <p style="font-size:20px;margin-top:30px;font-weight:bold;">You may now safely close this window.</p>
        </div>`;
      setTimeout(() => { window.open('', '_self', ''); window.close(); }, 1000);
    } catch (e) {
      alert("Shutdown failed: " + e.message);
      finishBtn.disabled = false;
    }
  });

  // ── Split pane resizers ────────────────────────────────────────────────────
  function makeResizer(resizerEl, getPaneEl, getWidthFn) {
    resizerEl.addEventListener("mousedown", (e) => {
      e.preventDefault();
      resizerEl.classList.add("active");
      const pane = getPaneEl();
      const onMove = (ev) => {
        const newW = getWidthFn(ev);
        if (newW > 160 && newW < window.innerWidth - 200) pane.style.width = newW + "px";
      };
      const onUp = () => {
        resizerEl.classList.remove("active");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }

  const leftPane  = document.getElementById("left-pane");
  const panelNav  = document.getElementById("panel-nav");

  makeResizer(
    document.getElementById("resizer-left"),
    () => leftPane,
    (e) => e.clientX,
  );
  makeResizer(
    document.getElementById("resizer-right"),
    () => panelNav,
    (e) => window.innerWidth - e.clientX,
  );

  // ── Panel navigator ────────────────────────────────────────────────────────
  async function loadPanels() {
    try {
      const res  = await fetch("/api/panels");
      const data = await res.json();
      allPanels      = data.panels || [];
      filteredPanels = [...allPanels];
      // Collapse all chapters by default; highlightActivePanel will expand the active one
      collapsedChapters = new Set(allPanels.map((p) => p.chapter));
      renderPanelList();
      highlightActivePanel(currentIndex);
    } catch (e) {
      console.error("Failed to load panels:", e);
    }
  }

  function renderPanelList() {
    panelList.innerHTML = "";

    // Group filteredPanels by chapter, preserving encounter order
    const groups = [];
    const chapterMap = new Map();
    for (const panel of filteredPanels) {
      if (!chapterMap.has(panel.chapter)) {
        const g = { chapter: panel.chapter, panels: [] };
        groups.push(g);
        chapterMap.set(panel.chapter, g);
      }
      chapterMap.get(panel.chapter).panels.push(panel);
    }

    for (const { chapter, panels } of groups) {
      const doneCount  = panels.filter((p) => p.hasNarration).length;
      const totalCount = panels.length;
      const isCollapsed = collapsedChapters.has(chapter);

      const groupEl = document.createElement("div");
      groupEl.className = "chapter-group" + (isCollapsed ? " collapsed" : "");
      groupEl.dataset.chapter = chapter;

      // ── Chapter header ──
      const headerEl = document.createElement("div");
      headerEl.className = "chapter-header";
      headerEl.setAttribute("role", "button");
      headerEl.setAttribute("aria-expanded", String(!isCollapsed));

      const chevron = document.createElement("span");
      chevron.className = "chapter-chevron";
      chevron.textContent = "▼";

      const label = document.createElement("span");
      label.className = "chapter-header-label";
      label.textContent = `Chapter ${chapter}`;

      const count = document.createElement("span");
      count.className = "chapter-header-count" + (doneCount === totalCount ? " complete" : "");
      count.textContent = `${doneCount}/${totalCount}`;

      headerEl.appendChild(chevron);
      headerEl.appendChild(label);
      headerEl.appendChild(count);
      headerEl.addEventListener("click", () => toggleChapter(chapter));
      groupEl.appendChild(headerEl);

      // ── Panels container ──
      const panelsEl = document.createElement("div");
      panelsEl.className = "chapter-panels";

      for (const panel of panels) {
        const item = document.createElement("div");
        item.className = "panel-item" + (panel.index === currentIndex ? " active" : "");
        item.dataset.index = panel.index;

        const nameSpan = document.createElement("span");
        nameSpan.className = "panel-name";
        nameSpan.textContent = panel.image || `panel_${panel.index + 1}`;
        nameSpan.title = panel.image;

        const dot = document.createElement("span");
        dot.className = "panel-dot" + (panel.hasNarration ? " has-narration" : "");
        dot.textContent = "●";
        dot.title = panel.hasNarration ? "Has narration" : "No narration";

        item.appendChild(nameSpan);
        item.appendChild(dot);

        item.addEventListener("click", async () => {
          await saveTextNow();
          await loadIndex(panel.index);
        });

        panelsEl.appendChild(item);
      }

      groupEl.appendChild(panelsEl);
      panelList.appendChild(groupEl);
    }

    matchCount.textContent = filteredPanels.length < allPanels.length
      ? `${filteredPanels.length} of ${allPanels.length} panels`
      : `${allPanels.length} panels`;
  }

  function toggleChapter(chapter) {
    if (collapsedChapters.has(chapter)) {
      collapsedChapters.delete(chapter);
    } else {
      collapsedChapters.add(chapter);
    }
    const groupEl = panelList.querySelector(`.chapter-group[data-chapter="${CSS.escape(chapter)}"]`);
    if (groupEl) {
      const nowCollapsed = collapsedChapters.has(chapter);
      groupEl.classList.toggle("collapsed", nowCollapsed);
      const hdr = groupEl.querySelector(".chapter-header");
      if (hdr) hdr.setAttribute("aria-expanded", String(!nowCollapsed));
    }
  }

  function highlightActivePanel(idx) {
    const prev = panelList.querySelector(".panel-item.active");
    if (prev) prev.classList.remove("active");

    const items = panelList.querySelectorAll(".panel-item");
    for (const item of items) {
      if (Number(item.dataset.index) === idx) {
        // Auto-expand the chapter group if it is currently collapsed
        const group = item.closest(".chapter-group");
        if (group && group.classList.contains("collapsed")) {
          const ch = group.dataset.chapter;
          collapsedChapters.delete(ch);
          group.classList.remove("collapsed");
          const hdr = group.querySelector(".chapter-header");
          if (hdr) hdr.setAttribute("aria-expanded", "true");
        }
        item.classList.add("active");
        item.scrollIntoView({ block: "nearest", behavior: "smooth" });
        break;
      }
    }
  }

  function updatePanelNarrationStatus(idx, hasNarration) {
    const panel = allPanels.find((p) => p.index === idx);
    if (panel) panel.hasNarration = hasNarration;

    const items = panelList.querySelectorAll(".panel-item");
    for (const item of items) {
      if (Number(item.dataset.index) === idx) {
        const dot = item.querySelector(".panel-dot");
        if (dot) {
          dot.className = "panel-dot" + (hasNarration ? " has-narration" : "");
          dot.title = hasNarration ? "Has narration" : "No narration";
        }
        // Recount narration in the chapter group and update its badge
        const group = item.closest(".chapter-group");
        if (group) {
          const groupItems = group.querySelectorAll(".panel-item");
          let done = 0;
          for (const gi of groupItems) {
            if (gi.querySelector(".panel-dot.has-narration")) done++;
          }
          const countEl = group.querySelector(".chapter-header-count");
          if (countEl) {
            const total = groupItems.length;
            countEl.textContent = `${done}/${total}`;
            countEl.className = "chapter-header-count" + (done === total ? " complete" : "");
          }
        }
        break;
      }
    }
  }

  // Search / jump
  function applyFilter(query) {
    const q = query.trim().toLowerCase();
    if (!q) {
      filteredPanels = [...allPanels];
    } else {
      // Pure number → match 1-indexed global position
      const asNum = parseInt(q, 10);
      const isNum = String(asNum) === q && asNum > 0;

      filteredPanels = allPanels.filter((p) => {
        if (isNum && p.index + 1 === asNum) return true;
        if ((p.image || "").toLowerCase().includes(q)) return true;
        if (p.chapter.toLowerCase().includes(q)) return true;
        return false;
      });
    }
    renderPanelList();
    // Restore active highlight if still visible
    highlightActivePanel(currentIndex);
  }

  panelJumpInput.addEventListener("input", (e) => applyFilter(e.target.value));

  panelJumpInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (filteredPanels.length > 0) {
        await saveTextNow();
        await loadIndex(filteredPanels[0].index);
      }
    }
    if (e.key === "Escape") {
      panelJumpInput.value = "";
      applyFilter("");
    }
  });

  panelJumpBtn.addEventListener("click", async () => {
    if (filteredPanels.length > 0) {
      await saveTextNow();
      await loadIndex(filteredPanels[0].index);
    }
  });

  // ── Speech controls ────────────────────────────────────────────────────────
  insertModeBtn.addEventListener("click", () => {
    insertMode = insertMode === "append" ? "replace" : "append";
    insertModeBtn.textContent = "Insert: " + (insertMode === "append" ? "Append" : "Replace");
  });

  clearLastBtn.addEventListener("click", () => {
    liveText = "";
    lastTranscript.textContent = "…";
  });

  micToggleBtn.addEventListener("click", () => toggleRecording());

  async function toggleRecording() {
    if (!isRecording) await startRecording();
    else await stopRecording({ discard: false });
  }

  async function startRecording() {
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      try { audioContext = new AudioCtx({ sampleRate: 16000 }); }
      catch { audioContext = new AudioCtx(); }
      sampleRate = audioContext.sampleRate;
      sourceNode    = audioContext.createMediaStreamSource(mediaStream);
      processorNode = audioContext.createScriptProcessor(4096, 1, 1);
      silentGain    = audioContext.createGain();
      silentGain.gain.value = 0;

      recordingBuffers = []; recordingLength = 0;
      chunkBuffers = []; chunkLength = 0;
      liveText = ""; lastTranscript.textContent = "";

      processorNode.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        const buf = new Float32Array(input.length);
        buf.set(input);
        recordingBuffers.push(buf); recordingLength += buf.length;
        chunkBuffers.push(buf);    chunkLength    += buf.length;
      };

      sourceNode.connect(processorNode);
      processorNode.connect(silentGain);
      silentGain.connect(audioContext.destination);
      chunkTimer = setInterval(sendChunk, LIVE_CHUNK_MS);

      isRecording = true;
      micToggleBtn.textContent = "🛑 Stop";
      micStatus.textContent = "Mic: Recording… (live)";
      micStatus.style.color = "#e5c07b";
    } catch (err) {
      lastTranscript.textContent = "Mic error: " + err.message;
      micStatus.textContent = "Mic: Off"; micStatus.style.color = "#888";
      isRecording = false;
    }
  }

  async function stopRecording({ discard }) {
    if (!isRecording) return;
    isRecording = false;
    micToggleBtn.textContent = "🎤 Start";
    micStatus.textContent = discard ? "Mic: Off (discarded)" : "Mic: Processing final…";
    micStatus.style.color = "#888";

    try {
      if (chunkTimer) { clearInterval(chunkTimer); chunkTimer = null; }
      if (processorNode) processorNode.disconnect();
      if (sourceNode)    sourceNode.disconnect();
      if (silentGain)    silentGain.disconnect();
      if (mediaStream)   mediaStream.getTracks().forEach((t) => t.stop());
      if (audioContext)  await audioContext.close();
    } catch (_) { /* ignore */ }
    finally {
      mediaStream = null; audioContext = null;
      sourceNode = null; processorNode = null; silentGain = null;
    }

    if (discard) {
      recordingBuffers = []; recordingLength = 0;
      chunkBuffers = [];     chunkLength = 0;
      chunkInFlight = false;
      return;
    }

    if (recordingLength < sampleRate * 0.2) {
      lastTranscript.textContent = "(no speech detected)";
      micStatus.textContent = "Mic: Off"; micStatus.style.color = "#888";
      return;
    }

    loader.style.display = "block";
    try {
      const finalSamples = mergeBuffers(recordingBuffers, recordingLength);
      const finalWav     = encodeWav(finalSamples, sampleRate);
      const data         = await transcribe(finalWav, "final");

      if (data.status !== "ok") {
        lastTranscript.textContent = "Final transcribe error: " + (data.msg || "unknown");
        micStatus.textContent = "Mic: Off"; micStatus.style.color = "#888";
        return;
      }

      const text = (data.text || "").trim();
      lastTranscript.textContent = text || "(no speech detected)";
      liveText = text || "";

      if (text) {
        if (insertMode === "replace") {
          textArea.value = text;
        } else {
          const prefix = textArea.value.trim().length ? "\n" : "";
          textArea.value = textArea.value + prefix + text;
        }
        markDirty();
        queueSave(400);
      }

      if (data.whisper) {
        whisperStatus.textContent =
          `Whisper: ${data.whisper.model} | ${data.whisper.device}/${data.whisper.compute}`;
      }
      micStatus.textContent = "Mic: Off"; micStatus.style.color = "#888";
    } catch (err) {
      lastTranscript.textContent = "Final request failed: " + err.message;
      micStatus.textContent = "Mic: Off"; micStatus.style.color = "#888";
    } finally {
      loader.style.display = "none";
      recordingBuffers = []; recordingLength = 0;
      chunkBuffers = []; chunkLength = 0; chunkInFlight = false;
    }
  }

  async function sendChunk() {
    if (!isRecording || chunkInFlight || chunkLength <= 0) return;
    const samples = mergeBuffers(chunkBuffers, chunkLength);
    chunkBuffers = []; chunkLength = 0;
    if (samples.length < sampleRate * 0.25) return;
    chunkInFlight = true;
    try {
      const wav  = encodeWav(samples, sampleRate);
      const data = await transcribe(wav, "chunk");
      if (data.status === "ok") {
        const t = (data.text || "").trim();
        if (t) { liveText = (liveText + " " + t).trim(); lastTranscript.textContent = liveText; }
      }
    } catch (_) { /* ignore chunk errors */ }
    finally { chunkInFlight = false; }
  }

  // ── Keyboard shortcuts ─────────────────────────────────────────────────────
  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey && !e.altKey && !e.metaKey && e.code === "Insert" && !e.repeat) {
      e.preventDefault();
      toggleRecording();
      return;
    }
    // Skip arrow keys if focused in textarea or jump input
    if (e.target === textArea || e.target === panelJumpInput) return;
    if (!e.ctrlKey && !e.altKey && !e.metaKey) {
      if (e.key === "ArrowLeft")  { e.preventDefault(); prevBtn.click(); }
      if (e.key === "ArrowRight") { e.preventDefault(); nextBtn.click(); }
    }
  });

  // ── Init ───────────────────────────────────────────────────────────────────
  (async () => {
    const idx   = getQueryIndex();
    const state = await fetchState(idx);
    render(state);
    if (!state.finished) totalItems = state.total;

    // Load panel list (async, doesn't block render)
    loadPanels();

    window.addEventListener("popstate", async () => {
      const i  = getQueryIndex();
      const st = await fetchState(i);
      render(st);
      highlightActivePanel(currentIndex);
    });
  })();
})();
