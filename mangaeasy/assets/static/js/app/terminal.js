/* terminal.js — VS Code-style integrated shell using xterm.js + WebSocket PTY. */

let term = null;
let fitAddon = null;
let ws = null;

function _wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/terminal`;
}

function _sendResize() {
  if (!ws || ws.readyState !== WebSocket.OPEN || !term) return;
  ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
}

function _connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  ws = new WebSocket(_wsUrl());
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    _sendResize();
    term.focus();
  };

  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      term.write(e.data);
    } else {
      term.write(new Uint8Array(e.data));
    }
  };

  ws.onclose = () => {
    term.write("\r\n\x1b[2m[session ended — click Reconnect to start a new shell]\x1b[0m\r\n");
  };

  ws.onerror = () => {
    term.write("\r\n\x1b[31m[WebSocket error — is the app server running?]\x1b[0m\r\n");
  };
}

function _ensureTerminal() {
  if (term) {
    _connect();
    return;
  }

  const container = document.getElementById("xterm-container");
  if (!container || !window.Terminal) return;

  term = new window.Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: "Consolas, 'Courier New', monospace",
    theme: {
      background:    "#0d0d0d",
      foreground:    "#d4d4d4",
      cursor:        "#ffffff",
      black:         "#000000",
      red:           "#cc0000",
      green:         "#4caf50",
      yellow:        "#e6c000",
      blue:          "#4d9de0",
      magenta:       "#af87d7",
      cyan:          "#00bcd4",
      white:         "#d4d4d4",
      brightBlack:   "#555555",
      brightRed:     "#f87171",
      brightGreen:   "#6fd388",
      brightYellow:  "#fbbf24",
      brightBlue:    "#6cc0ff",
      brightMagenta: "#c084fc",
      brightCyan:    "#67e8f9",
      brightWhite:   "#ffffff",
    },
    scrollback: 5000,
  });

  fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(container);

  requestAnimationFrame(() => {
    fitAddon.fit();
    _connect();
  });

  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
  });

  term.onResize(() => _sendResize());
}

export function initTerminal() {
  // Sub-tab toggle
  document.querySelectorAll(".term-stab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".term-stab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".term-pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const pane = document.getElementById(`term-pane-${btn.dataset.stab}`);
      if (pane) pane.classList.add("active");

      if (btn.dataset.stab === "shell") {
        _ensureTerminal();
        requestAnimationFrame(() => { if (fitAddon) fitAddon.fit(); });
      }
    });
  });

  document.getElementById("xterm-reconnect")?.addEventListener("click", () => {
    if (ws) ws.close();
    if (!term) {
      _ensureTerminal();
    } else {
      _connect();
    }
  });

  // Keep xterm sized to its container
  const container = document.getElementById("xterm-container");
  if (container && window.ResizeObserver) {
    new ResizeObserver(() => {
      if (fitAddon) {
        fitAddon.fit();
        _sendResize();
      }
    }).observe(container);
  }
}
