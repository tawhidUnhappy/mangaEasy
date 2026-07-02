# Installing mangaEasy

Three ways to get mangaEasy, from easiest to most hands-on.

---

## Option 1 — Download the desktop app (recommended for most users)

No Python, no Node, no dependencies — the app bundles its Python backend.
The first launch offers a one-time ~100 MB download of core tools (ffmpeg
and friends) into the app's own data folder; nothing is installed
system-wide.

### Step 1: Download

Go to the [**Releases page**](https://github.com/tawhidUnhappy/mangaEasy/releases/latest)
and download the file for your platform:

#### Windows
| File | Type | Notes |
|---|---|---|
| `mangaEasy-X.Y.Z-windows-x64-portable.exe` | Portable | Run directly, no install |

There is no Windows `.exe` installer by design — an installer would write a
registry uninstall key and Start Menu shortcut outside the folder you put
the app in, so deleting that folder wouldn't fully remove it. Portable-only
keeps "delete the folder, it's gone" literally true.

#### macOS
| File | Type | Notes |
|---|---|---|
| `mangaEasy-X.Y.Z-mac-arm64.dmg` | **Installer** | Apple Silicon — drag to Applications |
| `mangaEasy-X.Y.Z-mac-arm64.zip` | Portable | Extract and run without installing |
| `mangaEasy-X.Y.Z-mac-x64.*` | | Intel Macs (when available) |

#### Linux
| File | Type | Notes |
|---|---|---|
| `mangaEasy-X.Y.Z-linux-x86_64.AppImage` | Portable | `chmod +x`, run — no install |
| `mangaEasy-X.Y.Z-linux-amd64.deb` | **Installer** | For Ubuntu / Debian and derivatives |
| `mangaEasy-X.Y.Z-linux-x64.tar.gz` | Portable | Works on any Linux distro |

### Step 2: Install or extract

**Windows — Portable exe**
- Put `mangaEasy-X.Y.Z-windows-x64-portable.exe` in a folder of your choice
  and run it. The app keeps all its data **next to the exe**, so that one
  folder is the whole install.
- SmartScreen will warn the first time ("Windows protected your PC") because
  the exe is not code-signed — this is free software without a paid
  certificate. Click **More info → Run anyway**.

**macOS — .dmg installer**
- Open the `.dmg`, drag mangaEasy to Applications.
- The app is unsigned, so the first launch needs one of:
  - Right-click the app → **Open** → **Open**, or
  - `xattr -cr /Applications/mangaEasy.app` once in Terminal.

**Linux — .deb installer (Ubuntu / Debian)**
```bash
sudo dpkg -i mangaEasy-X.Y.Z-linux-amd64.deb
mangaeasy-desktop
```

**Linux — AppImage / tar.gz (any distro)**
```bash
chmod +x mangaEasy-X.Y.Z-linux-x86_64.AppImage
./mangaEasy-X.Y.Z-linux-x86_64.AppImage
```

### Where your data lives

| Platform | App data folder |
|---|---|
| Windows (portable) | next to the `.exe` you ran |
| macOS | `~/Library/Application Support/mangaEasy` |
| Linux | `~/.local/share/mangaEasy` (or `$XDG_DATA_HOME/mangaEasy`) |

Everything mangaEasy writes — installed AI tools, models, settings, logs,
Electron's own caches, and (by default) your projects — lives under that one
folder. The Setup tab's **About** section shows the exact path with an Open
button. Delete that folder (plus the app itself) and no trace remains.
Power users can override the location with the `MANGAEASY_ROOT` environment
variable.

### First-run checklist

The **Setup** tab guides you through the rest:

1. **Core tools** — if ffmpeg/ffprobe are missing, a banner offers a one-time
   ~100 MB **Download core tools** (works on Windows, macOS, and Linux).
2. **Kokoro TTS** — lightweight voice, runs on any CPU. Click **Install**.
3. **IndexTTS** (optional) — high-quality voice cloning; works best with an
   NVIDIA GPU. Click **Install** if you want it.
4. **MAGI v3** (optional) — automatic panel detection for manga pages.

These tools download once into the data folder's `.mangaeasy/tools/` and are
shared across all your projects. GPU acceleration (NVIDIA CUDA / Apple
Silicon) is detected automatically — nothing to choose.

---

## Option 2 — Install with uv (for developers / power users)

Requires [uv](https://docs.astral.sh/uv/) installed on your system.

```bash
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git
```

This puts a `mangaeasy` command on your `PATH`. Update later:

```bash
uv tool upgrade mangaeasy
```

Run without installing (useful for a quick test):

```bash
uvx --from git+https://github.com/tawhidUnhappy/mangaEasy.git mangaeasy --help
```

---

## Option 3 — From source (contributors)

```bash
git clone https://github.com/tawhidUnhappy/mangaEasy.git
cd mangaEasy
uv sync
uv run mangaeasy --help
```

Or skip the manual steps below and just run `./run.sh` (macOS/Linux) /
`run.bat` (Windows) from the repo root — it runs `uv sync`, builds the
desktop app's dev bundle, and launches `mangaeasy app`.

Build the desktop app yourself: PyInstaller bundles the Python backend, then
electron-builder wraps it into the portable app (plus a macOS `.dmg`/Linux
`.deb` if you build those targets) — `desktop/scripts/bundle-backend.mjs`
runs the first step for you.

```bash
uv sync --dev
cd desktop
npm install
npm run build:win    # or build:mac / build:linux
# Output lands in desktop/dist/
```

(`npm run build:win` etc. internally run `bundle:backend`, which builds
`packaging/mangaeasy.spec` and copies the result into `desktop/resources/backend/`
before electron-builder packages everything.)

---

## Updating

The app checks the Releases page (at most once a day) and shows a banner
when a newer version exists; Setup → About has a manual check too.

Because app data lives outside the app (macOS/Linux) or next to the exe
(Windows portable), replacing the app with a new version keeps your
installed AI tools, settings, and projects as they are:

- **Windows**: put the new portable exe in the same folder as the old one
  (data is picked up automatically), then delete the old exe.
- **macOS**: install the new `.dmg` over the old app.
- **Linux**: install the new `.deb` / replace the AppImage.
