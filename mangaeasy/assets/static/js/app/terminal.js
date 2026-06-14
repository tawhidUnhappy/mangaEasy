/* terminal.js — single integrated xterm.js terminal.
   Connects to /ws/terminal (git bash PTY + job output broadcast).
   Exports write() so other modules can send text without a DOM dependency. */

let term = null;
let fitAddon = null;
let ws = null;
let _earlyBuf = [];   // buffer writes that arrive before term is ready

export function write(text) {
  if (term) {
    term.write(text);
  } else {
    _earlyBuf.push(text);
  }
}

function _wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/terminal`;
}

function _sendResize() {
  if (!ws || ws.readyState !== WebSocket.OPEN || !term) return;
  ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
}

let _reconnectTimer = null;

function _connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;
  clearTimeout(_reconnectTimer);

  ws = new WebSocket(_wsUrl());
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    _sendResize();
  };

  ws.onmessage = (e) => {
    if (!term) return;
    term.write(typeof e.data === "string" ? e.data : new Uint8Array(e.data));
  };

  ws.onclose = () => {
    if (term) term.write("\r\n\x1b[2m[shell disconnected — reconnecting…]\x1b[0m\r\n");
    _reconnectTimer = setTimeout(_connect, 3000);
  };

  ws.onerror = () => {
    // onclose fires after onerror — reconnect handled there
  };
}

export function initTerminal() {
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
      selectionBackground: "rgba(255,255,255,0.2)",
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
    scrollback: 10000,
    allowProposedApi: true,
  });

  fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(container);

  // Flush buffered early writes
  _earlyBuf.forEach(t => term.write(t));
  _earlyBuf = [];

  requestAnimationFrame(() => {
    fitAddon.fit();
    _connect();
  });

  // User keystrokes → PTY stdin
  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
  });

  // Resize → PTY winsize
  term.onResize(() => _sendResize());

  // Re-fit when the terminal tab becomes visible
  document.querySelector('.tab[data-tab="terminal"]')
    ?.addEventListener("click", () => {
      requestAnimationFrame(() => { if (fitAddon) fitAddon.fit(); });
    });

  // Keep sized to container
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      if (fitAddon) { fitAddon.fit(); _sendResize(); }
    }).observe(container);
  }
}
