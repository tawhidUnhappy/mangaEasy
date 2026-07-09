(() => {
  // -------------------------
  // Helpers
  // -------------------------
  function getQueryIndex() {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("i");
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  }

  function setQueryIndex(i) {
    const url = new URL(window.location.href);
    url.searchParams.set("i", String(i));
    window.history.pushState({}, "", url.toString());
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  // WAV encode (mono, 16-bit PCM)
  function writeString(view, offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  }

  function floatTo16BitPCM(view, offset, input) {
    for (let i = 0; i < input.length; i++) {
      let s = Math.max(-1, Math.min(1, input[i]));
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
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, 1, true); // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true); // byte rate
    view.setUint16(32, 2, true); // block align
    view.setUint16(34, 16, true); // bits
    writeString(view, 36, "data");
    view.setUint32(40, samples.length * 2, true);

    floatTo16BitPCM(view, 44, samples);
    return new Blob([buffer], { type: "audio/wav" });
  }

  function mergeBuffers(buffers, totalLength) {
    const result = new Float32Array(totalLength);
    let offset = 0;
    for (const b of buffers) {
      result.set(b, offset);
      offset += b.length;
    }
    return result;
  }

  // -------------------------
  // Elements
  // -------------------------
  const pageCounter = document.getElementById("pageCounter");
  const imageFilename = document.getElementById("imageFilename");
  const previewImage = document.getElementById("previewImage");
  const noImage = document.getElementById("noImage");

  const textArea = document.getElementById("narrationText");
  const statusLabel = document.getElementById("save-status");
  const loader = document.getElementById("loader");

  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");
  const finishBtn = document.getElementById("finishBtn");

  const micToggleBtn = document.getElementById("micToggleBtn");
  const micStatus = document.getElementById("micStatus");
  const whisperStatus = document.getElementById("whisperStatus");
  const lastTranscript = document.getElementById("lastTranscript");
  const insertModeBtn = document.getElementById("insertModeBtn");
  const clearLastBtn = document.getElementById("clearLastBtn");

  const resizer = document.getElementById("resizer");
  const leftPane = document.getElementById("left-pane");

  // -------------------------
  // State
  // -------------------------
  let currentIndex = 0;
  let totalItems = 0;
  let finished = false;

  let dirty = false;
  let saveTimeout = null;

  // Speech state
  let insertMode = "append"; // append | replace
  let isRecording = false;

  // Audio capture (WebAudio)
  let mediaStream = null;
  let audioContext = null;
  let sourceNode = null;
  let processorNode = null;
  let silentGain = null;

  let sampleRate = 16000;

  let recordingBuffers = [];
  let recordingLength = 0;

  let chunkBuffers = [];
  let chunkLength = 0;

  let chunkTimer = null;
  let chunkInFlight = false;

  let liveText = "";

  // Live update frequency (ms)
  const LIVE_CHUNK_MS = 2000;

  // -------------------------
  // API
  // -------------------------
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
      } else {
        statusLabel.textContent = "Error saving!";
        statusLabel.style.color = "#f44336";
      }
    } catch (err) {
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

  // -------------------------
  // Render
  // -------------------------
  function render(state) {
    if (state.status !== "ok") {
      alert("Failed to load state: " + (state.msg || "unknown"));
      return;
    }

    finished = !!state.finished;

    if (finished) {
      document.body.innerHTML =
        "<div style='color:white; text-align:center; margin-top:50px;'><h1>All Done!</h1><p>No narration items found.</p></div>";
      return;
    }

    totalItems = state.total;
    currentIndex = state.index;

    const item = state.item || {};
    const img = (item.image || "").trim();
    const narr = item.narration || "";

    pageCounter.textContent = `Page ${currentIndex + 1} of ${totalItems}`;
    imageFilename.textContent = img;

    textArea.value = narr;
    dirty = false;
    statusLabel.textContent = "All changes saved";
    statusLabel.style.color = "#888";

    // Buttons
    prevBtn.disabled = currentIndex <= 0;
    nextBtn.disabled = currentIndex >= totalItems - 1;

    // Image
    if (img) {
      previewImage.src = `/images/${encodeURIComponent(img)}`;
      previewImage.style.display = "block";
      noImage.style.display = "none";
    } else {
      previewImage.style.display = "none";
      noImage.style.display = "block";
    }

    // Whisper info
    if (state.whisper) {
      whisperStatus.textContent = `Whisper: ${state.whisper.model} | ${state.whisper.device}/${state.whisper.compute}`;
    }
  }

  async function loadIndex(idx) {
    // If recording, stop and discard (don't insert audio into wrong page)
    if (isRecording) await stopRecording({ discard: true });

    idx = clamp(idx, 0, Math.max(0, totalItems - 1));
    setQueryIndex(idx);

    const state = await fetchState(idx);
    render(state);
  }

  // -------------------------
  // Autosave
  // -------------------------
  function markDirty() {
    dirty = true;
    statusLabel.textContent = "Unsaved changes...";
    statusLabel.style.color = "#e5c07b";
  }

  function queueSave(delayMs = 800) {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(saveTextNow, delayMs);
  }

  textArea.addEventListener("input", () => {
    markDirty();
    queueSave(800);
  });

  // -------------------------
  // Navigation
  // -------------------------
  prevBtn.addEventListener("click", async () => {
    await saveTextNow();
    await loadIndex(currentIndex - 1);
  });

  nextBtn.addEventListener("click", async () => {
    await saveTextNow();
    await loadIndex(currentIndex + 1);
  });

  // -------------------------
  // Finish
  // -------------------------
  finishBtn.addEventListener("click", async () => {
    finishBtn.disabled = true;
    try {
      await saveTextNow();
      if (confirm("All narrations saved. Close the editor?")) {
        await fetch("/shutdown", { method: "POST" });

        document.body.innerHTML = `
          <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#1e1e1e; color:#fff; font-family:sans-serif; text-align:center;">
            <h1 style="color:#b7f0c8; font-size:48px; margin-bottom:10px;">✅ Success!</h1>
            <p style="font-size:18px; color:#aaa;">All narrations have been saved.</p>
            <p style="font-size:16px; color:#888;">The local backend server has safely shut down.</p>
            <p style="font-size:20px; margin-top:30px; font-weight:bold;">You may now safely close this window.</p>
          </div>
        `;

        setTimeout(() => {
          window.open('', '_self', '');
          window.close();
        }, 1000);

      } else {
        finishBtn.disabled = false;
      }
    } catch (e) {
      alert("Shutdown failed: " + e.message);
      finishBtn.disabled = false;
    }
  });

  // -------------------------
  // Split pane resizer
  // -------------------------
  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    resizer.classList.add("active");
    document.addEventListener("mousemove", resize);
    document.addEventListener("mouseup", stopResize);
  });

  function resize(e) {
    const newWidth = e.clientX;
    if (newWidth > 200 && newWidth < window.innerWidth - 100) {
      leftPane.style.width = newWidth + "px";
    }
  }

  function stopResize() {
    resizer.classList.remove("active");
    document.removeEventListener("mousemove", resize);
    document.removeEventListener("mouseup", stopResize);
  }

  // -------------------------
  // Speech controls
  // -------------------------
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
      try {
        audioContext = new AudioCtx({ sampleRate: 16000 });
      } catch {
        audioContext = new AudioCtx();
      }

      sampleRate = audioContext.sampleRate;

      sourceNode = audioContext.createMediaStreamSource(mediaStream);
      processorNode = audioContext.createScriptProcessor(4096, 1, 1);

      silentGain = audioContext.createGain();
      silentGain.gain.value = 0;

      recordingBuffers = [];
      recordingLength = 0;
      chunkBuffers = [];
      chunkLength = 0;
      liveText = "";
      lastTranscript.textContent = "";

      processorNode.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        const buf = new Float32Array(input.length);
        buf.set(input);
        recordingBuffers.push(buf);
        recordingLength += buf.length;
        chunkBuffers.push(buf);
        chunkLength += buf.length;
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
      micStatus.textContent = "Mic: Off";
      micStatus.style.color = "#888";
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
      if (chunkTimer) {
        clearInterval(chunkTimer);
        chunkTimer = null;
      }
      if (processorNode) processorNode.disconnect();
      if (sourceNode) sourceNode.disconnect();
      if (silentGain) silentGain.disconnect();
      if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
      if (audioContext) await audioContext.close();
    } catch (_) {
      // ignore
    } finally {
      mediaStream = null;
      audioContext = null;
      sourceNode = null;
      processorNode = null;
      silentGain = null;
    }

    if (discard) {
      recordingBuffers = [];
      recordingLength = 0;
      chunkBuffers = [];
      chunkLength = 0;
      chunkInFlight = false;
      return;
    }

    if (recordingLength < sampleRate * 0.2) {
      lastTranscript.textContent = "(no speech detected)";
      micStatus.textContent = "Mic: Off";
      micStatus.style.color = "#888";
      return;
    }

    loader.style.display = "block";
    try {
      const finalSamples = mergeBuffers(recordingBuffers, recordingLength);
      const finalWav = encodeWav(finalSamples, sampleRate);

      const data = await transcribe(finalWav, "final");

      if (data.status !== "ok") {
        lastTranscript.textContent = "Final transcribe error: " + (data.msg || "unknown");
        micStatus.textContent = "Mic: Off";
        micStatus.style.color = "#888";
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
        whisperStatus.textContent = `Whisper: ${data.whisper.model} | ${data.whisper.device}/${data.whisper.compute}`;
      }

      micStatus.textContent = "Mic: Off";
      micStatus.style.color = "#888";
    } catch (err) {
      lastTranscript.textContent = "Final request failed: " + err.message;
      micStatus.textContent = "Mic: Off";
      micStatus.style.color = "#888";
    } finally {
      loader.style.display = "none";
      recordingBuffers = [];
      recordingLength = 0;
      chunkBuffers = [];
      chunkLength = 0;
      chunkInFlight = false;
    }
  }

  async function sendChunk() {
    if (!isRecording) return;
    if (chunkInFlight) return;
    if (chunkLength <= 0) return;

    const samples = mergeBuffers(chunkBuffers, chunkLength);
    chunkBuffers = [];
    chunkLength = 0;

    if (samples.length < sampleRate * 0.25) return;

    chunkInFlight = true;
    try {
      const wav = encodeWav(samples, sampleRate);
      const data = await transcribe(wav, "chunk");

      if (data.status === "ok") {
        const t = (data.text || "").trim();
        if (t) {
          liveText = (liveText + " " + t).trim();
          lastTranscript.textContent = liveText;
        }
      }
    } catch (_) {
      // ignore chunk errors; final transcription still works
    } finally {
      chunkInFlight = false;
    }
  }

  // -------------------------
  // Keyboard shortcuts
  // -------------------------
  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey && !e.altKey && !e.metaKey && e.code === "Insert" && !e.repeat) {
      e.preventDefault();
      toggleRecording();
      return;
    }

    if (e.target === textArea) return;

    if (!e.ctrlKey && !e.altKey && !e.metaKey) {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        prevBtn.click();
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        nextBtn.click();
      }
    }
  });

  // -------------------------
  // Init
  // -------------------------
  (async () => {
    const idx = getQueryIndex();
    const state = await fetchState(idx);
    render(state);

    if (!state.finished) totalItems = state.total;

    window.addEventListener("popstate", async () => {
      const idx2 = getQueryIndex();
      const st = await fetchState(idx2);
      render(st);
    });
  })();
})();
