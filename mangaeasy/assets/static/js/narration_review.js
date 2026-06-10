(() => {
  // ── DOM refs ───────────────────────────────────────────────────────────────
  const panelImg        = document.getElementById("panelImg");
  const noImageMsg      = document.getElementById("noImageMsg");
  const narrationText   = document.getElementById("narrationText");
  const segmentLabel    = document.getElementById("segmentLabel");
  const noteCountLabel  = document.getElementById("noteCountLabel");
  const noteImageLabel  = document.getElementById("noteImageLabel");
  const noteLockOverlay = document.getElementById("noteLockOverlay");
  const noteTextarea    = document.getElementById("noteTextarea");
  const noteSaveStatus  = document.getElementById("noteSaveStatus");
  const saveNoteBtn     = document.getElementById("saveNoteBtn");
  const clearNoteBtn    = document.getElementById("clearNoteBtn");
  const prevBtn         = document.getElementById("prevBtn");
  const playPauseBtn    = document.getElementById("playPauseBtn");
  const nextBtn         = document.getElementById("nextBtn");
  const closeBtn        = document.getElementById("closeBtn");
  const progressBar     = document.getElementById("progressBar");
  const progressFill    = document.getElementById("progressFill");
  const progressThumb   = document.getElementById("progressThumb");
  const timeElapsed     = document.getElementById("timeElapsed");
  const timeTotal       = document.getElementById("timeTotal");
  const segmentTrack    = document.getElementById("segment-track");
  const audioPlayer     = document.getElementById("audioPlayer");
  const speedSelect     = document.getElementById("speedSelect");

  // ── State ──────────────────────────────────────────────────────────────────
  let segments   = [];   // [{image, chapter, narration, audio_url, image_url}, ...]
  let notes      = {};   // {image_filename: note_text}
  let durations  = [];   // per-segment audio duration in seconds
  let offsets    = [];   // cumulative: offsets[i] = sum of durations[0..i-1]
  let totalDur   = 0;
  let durReady   = 0;    // how many durations have resolved

  let curIdx     = 0;
  let isPlaying  = false;
  let noteDirty  = false;
  let rafHandle  = null;

  // ── Utility ────────────────────────────────────────────────────────────────
  function fmtTime(s) {
    if (!Number.isFinite(s) || s < 0) return "0:00";
    const m   = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  }

  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  function setSaveStatus(msg, type = "") {
    noteSaveStatus.textContent = msg;
    noteSaveStatus.className   = "note-save-status" + (type ? ` ${type}` : "");
  }

  function updateNoteCountBadge() {
    const count = Object.values(notes).filter(v => v && v.trim()).length;
    if (count > 0) {
      noteCountLabel.textContent = `${count} note${count === 1 ? "" : "s"}`;
      noteCountLabel.classList.add("visible");
    } else {
      noteCountLabel.classList.remove("visible");
    }
  }

  // ── Duration management ────────────────────────────────────────────────────
  function rebuildOffsets() {
    let acc = 0;
    for (let i = 0; i < durations.length; i++) {
      offsets[i] = acc;
      acc += (durations[i] || 0);
    }
    totalDur = acc;
    if (durReady === segments.length) {
      timeTotal.textContent = fmtTime(totalDur);
    }
  }

  function prefetchDurations() {
    // Pre-fetch all audio durations concurrently (browser caps at ~6 connections).
    // Durations are needed for the global progress bar seek calculation.
    durations = new Array(segments.length).fill(0);
    offsets   = new Array(segments.length).fill(0);
    durReady  = 0;

    for (let i = 0; i < segments.length; i++) {
      const idx   = i;
      const probe = new Audio();
      probe.preload = "metadata";
      const finish = () => {
        if (probe.duration && Number.isFinite(probe.duration)) {
          durations[idx] = probe.duration;
        }
        durReady++;
        rebuildOffsets();
        probe.src = "";
      };
      probe.addEventListener("loadedmetadata", finish, { once: true });
      probe.addEventListener("error",           finish, { once: true });
      probe.src = segments[i].audio_url;
    }
  }

  // ── Segment track (bottom bar) ─────────────────────────────────────────────
  function buildSegmentTrack() {
    segmentTrack.innerHTML = "";
    let prevChapter = null;

    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];

      if (seg.chapter !== prevChapter) {
        const div = document.createElement("span");
        div.className   = "seg-divider";
        div.textContent = `Ch.${seg.chapter}`;
        segmentTrack.appendChild(div);
        prevChapter = seg.chapter;
      }

      const chip = document.createElement("div");
      chip.className  = "seg-chip";
      chip.dataset.idx = String(i);
      chip.title      = seg.image;
      if (i === curIdx)            chip.classList.add("active");
      if (notes[seg.image]?.trim()) chip.classList.add("has-note");

      const dot = document.createElement("span");
      dot.className = "seg-note-dot";
      chip.appendChild(dot);

      const lbl = document.createElement("span");
      lbl.textContent = seg.image.replace(/\.[^.]+$/, ""); // stem only
      chip.appendChild(lbl);

      chip.addEventListener("click", () => jumpTo(i));
      segmentTrack.appendChild(chip);
    }
  }

  function setActiveChip(i) {
    const prev = segmentTrack.querySelector(".seg-chip.active");
    if (prev) prev.classList.remove("active");
    const next = segmentTrack.querySelector(`.seg-chip[data-idx="${i}"]`);
    if (next) {
      next.classList.add("active");
      next.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    }
  }

  function setChipHasNote(i, hasNote) {
    const chip = segmentTrack.querySelector(`.seg-chip[data-idx="${i}"]`);
    if (chip) chip.classList.toggle("has-note", !!hasNote);
  }

  // ── Render segment ─────────────────────────────────────────────────────────
  function renderSegment(i) {
    const seg = segments[i];
    if (!seg) return;

    curIdx = i;
    panelImg.src = seg.image_url;
    panelImg.classList.add("visible");
    noImageMsg.style.display = "none";

    narrationText.textContent = seg.narration || "(no narration)";
    segmentLabel.textContent  = `${i + 1} / ${segments.length}  —  ${seg.image}`;
    noteImageLabel.textContent = seg.image;
    setActiveChip(i);
  }

  // ── Note panel ─────────────────────────────────────────────────────────────
  function lockNotePanel() {
    noteLockOverlay.classList.remove("hidden");
    noteTextarea.disabled = true;
    saveNoteBtn.disabled  = true;
    clearNoteBtn.disabled = true;
  }

  function unlockNotePanel() {
    noteLockOverlay.classList.add("hidden");
    noteTextarea.disabled = false;
    saveNoteBtn.disabled  = false;
    clearNoteBtn.disabled = false;

    // Load saved note for current segment (only if no unsaved edits)
    if (!noteDirty) {
      const seg = segments[curIdx];
      const existing = seg ? (notes[seg.image] || "") : "";
      noteTextarea.value = existing;
      setSaveStatus(existing ? "● Note saved" : "", existing ? "saved" : "");
    }
  }

  // ── Saving ─────────────────────────────────────────────────────────────────
  async function saveCurrentNote() {
    const seg = segments[curIdx];
    if (!seg) return;
    const text = noteTextarea.value.trim();
    try {
      const res = await fetch("/api/notes/save", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ image: seg.image, note: text }),
      });
      const data = await res.json();
      if (data.status === "ok") {
        if (text) notes[seg.image] = text;
        else      delete notes[seg.image];
        noteDirty = false;
        setSaveStatus("● Note saved", "saved");
        setChipHasNote(curIdx, !!text);
        updateNoteCountBadge();
      } else {
        setSaveStatus("Save failed", "error");
      }
    } catch {
      setSaveStatus("Connection error", "error");
    }
  }

  noteTextarea.addEventListener("input", () => {
    noteDirty = true;
    setSaveStatus("✎ Unsaved changes…", "unsaved");
  });

  saveNoteBtn.addEventListener("click", saveCurrentNote);

  clearNoteBtn.addEventListener("click", async () => {
    noteTextarea.value = "";
    noteDirty = true;
    setSaveStatus("✎ Unsaved changes…", "unsaved");
    await saveCurrentNote();
  });

  // ── Playback ───────────────────────────────────────────────────────────────
  function playSegment(i) {
    if (i >= segments.length) { pausePlayer(); return; }

    renderSegment(i);
    audioPlayer.src         = segments[i].audio_url;
    audioPlayer.playbackRate = parseFloat(speedSelect.value) || 1;
    audioPlayer.currentTime = 0;
    audioPlayer.play().catch(err => console.warn("[narration-review] play error:", err));
  }

  audioPlayer.addEventListener("ended", () => {
    if (!isPlaying) return;
    if (curIdx + 1 < segments.length) {
      playSegment(curIdx + 1);
    } else {
      pausePlayer();
    }
  });

  async function playPlayer() {
    if (!segments.length) return;
    // Save any pending note before resuming playback
    if (noteDirty) await saveCurrentNote();
    isPlaying = true;
    playPauseBtn.innerHTML = "&#9646;&#9646;"; // pause icon
    lockNotePanel();

    if (!audioPlayer.src || audioPlayer.ended || audioPlayer.paused) {
      if (audioPlayer.ended) {
        playSegment(curIdx);
      } else if (!audioPlayer.src) {
        playSegment(curIdx);
      } else {
        audioPlayer.playbackRate = parseFloat(speedSelect.value) || 1;
        audioPlayer.play().catch(err => console.warn("[narration-review] resume error:", err));
      }
    }
    startProgressLoop();
  }

  function pausePlayer() {
    isPlaying = false;
    playPauseBtn.innerHTML = "&#9654;"; // play icon
    audioPlayer.pause();
    stopProgressLoop();
    unlockNotePanel();
  }

  async function togglePlayPause() {
    if (isPlaying) pausePlayer();
    else await playPlayer();
  }

  // ── Jump ───────────────────────────────────────────────────────────────────
  async function jumpTo(i) {
    i = clamp(i, 0, segments.length - 1);
    if (i === curIdx && !isPlaying) return;

    // Auto-save dirty note before leaving current segment
    if (noteDirty) await saveCurrentNote();
    noteDirty = false;

    const wasPlaying = isPlaying;

    audioPlayer.pause();
    renderSegment(i);
    audioPlayer.src         = segments[i].audio_url;
    audioPlayer.currentTime = 0;

    if (wasPlaying) {
      isPlaying = true;
      lockNotePanel();
      audioPlayer.playbackRate = parseFloat(speedSelect.value) || 1;
      audioPlayer.play().catch(err => console.warn("[narration-review] jump-play error:", err));
      startProgressLoop();
    } else {
      stopProgressLoop();
      updateProgressUI();
      unlockNotePanel();
    }
  }

  // ── Progress bar ───────────────────────────────────────────────────────────
  function getCurrentElapsed() {
    return (offsets[curIdx] || 0) + (audioPlayer.currentTime || 0);
  }

  function updateProgressUI() {
    const elapsed = getCurrentElapsed();
    const pct     = totalDur > 0 ? clamp(elapsed / totalDur, 0, 1) * 100 : 0;
    progressFill.style.width = pct + "%";
    progressThumb.style.left = pct + "%";
    timeElapsed.textContent  = fmtTime(elapsed);
    if (durReady === segments.length) {
      timeTotal.textContent = fmtTime(totalDur);
    }
  }

  function startProgressLoop() {
    stopProgressLoop();
    const tick = () => {
      updateProgressUI();
      if (isPlaying) rafHandle = requestAnimationFrame(tick);
    };
    rafHandle = requestAnimationFrame(tick);
  }

  function stopProgressLoop() {
    if (rafHandle) { cancelAnimationFrame(rafHandle); rafHandle = null; }
    updateProgressUI();
  }

  progressBar.addEventListener("click", async (e) => {
    if (!totalDur || segments.length === 0) return;
    const rect      = progressBar.getBoundingClientRect();
    const pct       = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    const targetTime = pct * totalDur;

    // Binary search for which segment contains targetTime
    let targetIdx = 0;
    for (let i = offsets.length - 1; i >= 0; i--) {
      if ((offsets[i] || 0) <= targetTime) { targetIdx = i; break; }
    }
    const intoSegment = clamp(targetTime - (offsets[targetIdx] || 0), 0, durations[targetIdx] || 0);

    if (noteDirty) await saveCurrentNote();
    noteDirty = false;

    const wasPlaying = isPlaying;
    audioPlayer.pause();
    renderSegment(targetIdx);
    audioPlayer.src         = segments[targetIdx].audio_url;
    audioPlayer.currentTime = intoSegment;

    if (wasPlaying) {
      isPlaying = true;
      lockNotePanel();
      audioPlayer.playbackRate = parseFloat(speedSelect.value) || 1;
      audioPlayer.play().catch(err => console.warn("[narration-review] seek-play error:", err));
      startProgressLoop();
    } else {
      stopProgressLoop();
      updateProgressUI();
      unlockNotePanel();
    }
  });

  // ── Controls ───────────────────────────────────────────────────────────────
  playPauseBtn.addEventListener("click", togglePlayPause);
  prevBtn.addEventListener("click", () => jumpTo(curIdx - 1));
  nextBtn.addEventListener("click", () => jumpTo(curIdx + 1));

  speedSelect.addEventListener("change", () => {
    audioPlayer.playbackRate = parseFloat(speedSelect.value) || 1;
  });

  closeBtn.addEventListener("click", async () => {
    const noteCount = Object.values(notes).filter(v => v?.trim()).length;
    const msg = noteCount > 0
      ? `Close? You have ${noteCount} saved note(s). They are already written to disk.`
      : "Close the narration review tool?";
    if (!confirm(msg)) return;
    if (noteDirty) await saveCurrentNote();
    try {
      await fetch("/shutdown", { method: "POST" });
      document.body.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
          height:100vh;background:#111118;color:#fff;font-family:sans-serif;text-align:center;gap:12px;">
          <div style="font-size:40px;color:#8080cc;">✓</div>
          <p style="font-size:18px;color:#aaa;">Review session closed.</p>
          <p style="font-size:13px;color:#555;">Notes were saved to <code style="color:#8080cc">tmp/</code>. You may close this window.</p>
        </div>`;
      setTimeout(() => { window.open("", "_self", ""); window.close(); }, 1000);
    } catch (err) {
      alert("Shutdown failed: " + err.message);
    }
  });

  // ── Keyboard shortcuts ─────────────────────────────────────────────────────
  document.addEventListener("keydown", async (e) => {
    // Ctrl+S works everywhere (note textarea or not)
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      await saveCurrentNote();
      return;
    }

    // While note textarea is focused: Insert blurs it, returning control to player keys
    if (e.target === noteTextarea) {
      if (e.code === "Insert") { e.preventDefault(); noteTextarea.blur(); }
      return;
    }

    if (e.key === " " && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      await togglePlayPause();
      return;
    }
    if (e.key === "MediaPlayPause" || e.key === "F5") {
      e.preventDefault();
      await togglePlayPause();
      return;
    }
    if (e.code === "Insert") {
      e.preventDefault();
      if (isPlaying) pausePlayer();
      noteTextarea.focus();
      return;
    }
    if (e.key === "ArrowLeft"  && !e.ctrlKey) { e.preventDefault(); await jumpTo(curIdx - 1); return; }
    if (e.key === "ArrowRight" && !e.ctrlKey) { e.preventDefault(); await jumpTo(curIdx + 1); return; }
  });

  // ── Column resizer ─────────────────────────────────────────────────────────
  const colResizer = document.getElementById("col-resizer");
  const noteCol    = document.getElementById("note-col");

  colResizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    colResizer.classList.add("dragging");
    const onMove = (ev) => {
      const newW = clamp(window.innerWidth - ev.clientX, 180, 600);
      noteCol.style.width = newW + "px";
    };
    const onUp = () => {
      colResizer.classList.remove("dragging");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup",   onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup",   onUp);
  });

  // ── Segment bar: mouse-wheel horizontal scroll ─────────────────────────────
  const segmentBar = document.getElementById("segment-bar");
  segmentBar.addEventListener("wheel", (e) => {
    if (e.deltaY === 0) return;
    e.preventDefault();
    segmentBar.scrollLeft += e.deltaY * 2;
  }, { passive: false });

  // ── Init ───────────────────────────────────────────────────────────────────
  (async () => {
    lockNotePanel();

    try {
      const [segRes, noteRes] = await Promise.all([
        fetch("/api/segments"),
        fetch("/api/notes"),
      ]);
      const segData  = await segRes.json();
      const noteData = await noteRes.json();

      segments = segData.segments || [];
      notes    = noteData.notes   || {};
    } catch (err) {
      segmentLabel.textContent = "Failed to load data";
      console.error("[narration-review] init error:", err);
      return;
    }

    if (segments.length === 0) {
      segmentLabel.textContent = "No segments found — check that audio files exist.";
      noImageMsg.textContent   = "No segments loaded.";
      noteLockOverlay.classList.add("hidden");
      return;
    }

    buildSegmentTrack();
    prefetchDurations();
    renderSegment(0);
    updateNoteCountBadge();
    unlockNotePanel();
  })();
})();
