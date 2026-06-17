"""mangaeasy.web.nicegui_app — NiceGUI desktop UI.

One process. One window. No hidden terminal, no external Flask server.
NiceGUI opens the window via pywebview; the app IS the main process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from nicegui import run as ni_run, ui

from mangaeasy import __version__
from mangaeasy.utils import numeric_sort_key
from mangaeasy.web.app import jobs
from mangaeasy.web.app.state import lock, log, save_app_state, state
from mangaeasy.web.flask_utils import terminal_broadcaster

# ---------------------------------------------------------------------------
# Live output ring buffer — terminal_broadcaster collects all Python
# stdout/stderr + subprocess raw bytes.  We add a second sink (_sink) that
# mirrors everything into a plain list so the in-app terminal can drain it.
# ---------------------------------------------------------------------------
_out: list[str] = []


def _sink(text: str) -> None:
    _out.append(text)
    if len(_out) > 20_000:
        del _out[:5_000]


terminal_broadcaster.add_client(_sink)
terminal_broadcaster.install_tee()

# ---------------------------------------------------------------------------
# Progress hook — intercept state.progress() so we can drive the header bar
# ---------------------------------------------------------------------------
import mangaeasy.web.app.state as _st

_prog: dict = {"v": 0, "t": 0, "label": "", "active": False, "done_until": 0.0}
_prog_start: Optional[float] = None
_real_prog = _st.progress


def _prog_hook(value: int, total: int, label: str = "") -> None:
    global _prog_start
    _real_prog(value, total, label)
    active = total <= 0 or value < total
    if active and _prog_start is None:
        _prog_start = time.monotonic()
    _prog.update({
        "v": value,
        "t": total,
        "label": label,
        "active": active,
        "done_until": time.monotonic() + 2.0 if total > 0 and value >= total else 0.0,
    })


_st.progress = _prog_hook


def _begin_progress(label: str) -> None:
    _prog_hook(0, 0, label)


def _finish_progress(label: str = "Done") -> None:
    _prog_hook(1, 1, label)

# ---------------------------------------------------------------------------
# Job runners
# ---------------------------------------------------------------------------


def _run_job(command: str, args: list[str] | None = None) -> None:
    args = args or []
    with lock:
        if jobs.job_running():
            ui.notify("Another job is already running", type="warning")
            return
        _begin_progress(f"Starting {command}")
        proc = jobs.spawn_cli(command, args, state["project_root"])
        t = threading.Thread(target=jobs.pump, args=(proc, command), daemon=True)
        state["job"] = {"kind": "run", "name": command, "thread": t, "proc": proc}
        t.start()


def _run_chain(commands: list[str]) -> None:
    with lock:
        if jobs.job_running():
            ui.notify("Another job is already running", type="warning")
            return
        label = " → ".join(commands)
        _begin_progress(f"Starting {label}")
        job: dict = {"kind": "run", "name": label, "thread": None, "proc": None}

        def _work() -> None:
            for index, cmd in enumerate(commands, 1):
                _st.progress(index - 1, len(commands), f"Step {index}/{len(commands)}: {cmd}")
                proc = jobs.spawn_cli(cmd, [], state["project_root"])
                job["proc"] = proc
                jobs.pump(proc, cmd)
                code = proc.returncode
                col = "\x1b[32m" if code == 0 else "\x1b[31m"
                log(f"{col}[{cmd}] exit {code}\x1b[0m")
                if code != 0:
                    _finish_progress(f"{cmd} failed")
                    log("\x1b[31m[chain] stopped\x1b[0m")
                    return
                _st.progress(index, len(commands), f"Finished {cmd}")
            _finish_progress("Chain complete")
            log("\x1b[32m[chain] all steps done ✓\x1b[0m")

        t = threading.Thread(target=_work, daemon=True)
        job["thread"] = t
        state["job"] = job
        t.start()


def _batch_download(start: int, end: int, fresh: bool) -> None:
    """Batch-download chapters start..end, calling `mangaeasy download` per chapter."""
    with lock:
        if jobs.job_running():
            ui.notify("Another job is already running", type="warning")
            return
        _begin_progress(f"Downloading chapters {start:02d}-{end:02d}")
        root = state["project_root"]
        cfg_path = root / "config.json"
        job: dict = {"kind": "batch-download", "name": f"ch{start:02d}–{end:02d}",
                     "thread": None, "proc": None}

        def _work() -> None:
            from mangaeasy.web.app.api_workflow import _library_dir, _count_files, IMAGE_EXTS
            cfg0 = _read_json_file(cfg_path)
            sc   = _read_json_file(root / "config.system.json")
            dl0  = cfg0.get("download") if isinstance(cfg0.get("download"), dict) else {}
            name = str(dl0.get("name") or "")
            lib  = _library_dir(root, sc) if name else None

            total = max(1, end - start + 1)
            for i, ch in enumerate(range(start, end + 1), 1):
                _st.progress(i - 1, total, f"Chapter {ch:02d}")
                if lib and name:
                    dl_dir = lib / name / f"{ch:02d}" / "download"
                    if _count_files(dl_dir, IMAGE_EXTS) > 0:
                        log(f"[batch] ch{ch:02d} already downloaded — skip")
                        continue
                log(f"[batch] ch{ch:02d} ({i}/{end-start+1})…")
                cfg = _read_json_file(cfg_path)
                dl  = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
                dl["chapter"] = ch
                cfg["download"] = dl
                cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
                proc = jobs.spawn_cli("download", ["--fresh"] if fresh else [], root)
                job["proc"] = proc
                jobs.pump(proc, f"download ch{ch:02d}")
                code = proc.returncode
                col = "\x1b[32m" if code == 0 else "\x1b[31m"
                log(f"{col}[download] ch{ch:02d} exit {code}\x1b[0m")
                if code != 0:
                    _finish_progress(f"ch{ch:02d} failed")
                    log("\x1b[31m[batch] stopped\x1b[0m")
                    return
                _st.progress(i, total, f"Finished ch{ch:02d}")
                if ch < end:
                    log("[batch] pausing 10 s…"); time.sleep(10)
            log(f"\x1b[32m[batch] done ✓\x1b[0m")

            _finish_progress("Batch download complete")

        t = threading.Thread(target=_work, daemon=True)
        job["thread"] = t
        state["job"] = job
        t.start()


def _stop_job() -> None:
    job = state.get("job")
    if job and job.get("proc"):
        job["proc"].terminate()
    _finish_progress("Stop requested")
    ui.notify("Stop requested", type="warning")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _write_config(cfg: dict | None = None, sys_cfg: dict | None = None) -> None:
    root = state["project_root"]
    if cfg is not None:
        (root / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        log("[app] saved config.json")
    if sys_cfg is not None:
        (root / "config.system.json").write_text(
            json.dumps(sys_cfg, indent=2) + "\n", encoding="utf-8")
        log("[app] saved config.system.json")


def _read_config() -> tuple[dict, dict]:
    root = state["project_root"]
    return _read_json_file(root / "config.json"), _read_json_file(root / "config.system.json")


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------
_IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AUD = {".wav", ".mp3", ".m4a"}


def _count(folder: Path, exts: set) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.suffix.lower() in exts)


def _ch_status(name: str, chapter: int) -> dict:
    from mangaeasy.web.app.api_workflow import _library_dir
    root = state["project_root"]
    _, sc = _read_config()
    lib = _library_dir(root, sc)
    d = lib / name / f"{chapter:02d}"
    narr = d / f"narration_{chapter:02d}.json"
    ni = 0
    if narr.exists():
        try:
            ni = len(json.loads(narr.read_text()))
        except Exception:
            pass
    vid_a = d / f"{chapter:02d}_{name}.mp4"
    vid_b = d / f"{chapter:02d}_{name}_with_bgm.mp4"
    return {
        "dl": _count(d / "download", _IMG),
        "panels": _count(d / "panels", _IMG),
        "narr": narr.exists(), "narr_items": ni,
        "audio": _count(d / "audio", _AUD),
        "video": vid_a.exists() or vid_b.exists(),
        "dir": d,
    }


def _delete_chapter(chapter: int, what: str) -> None:
    from mangaeasy.web.app.api_workflow import _library_dir
    root = state["project_root"]
    cfg, sc = _read_config()
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    name = str(dl.get("name") or "")
    if not name:
        log("[delete] no manga name configured"); return
    lib = _library_dir(root, sc)
    ch_dir = lib / name / f"{chapter:02d}"
    paths_cfg = sc.get("paths") or {}
    panels_sub = paths_cfg.get("panels_subdir", "panels")
    audio_sub  = paths_cfg.get("audio_subdir", "audio")
    removed: list[str] = []

    def rm_tree(p: Path) -> None:
        if p.is_dir(): shutil.rmtree(p); removed.append(p.name + "/")

    def rm_glob(pat: str) -> None:
        fs = list(ch_dir.glob(pat))
        for f in fs: f.unlink(missing_ok=True)
        if fs: removed.append(f"{len(fs)}×{pat}")

    if what in ("download", "all"): rm_tree(ch_dir / "download")
    if what in ("panels",   "all"): rm_tree(ch_dir / panels_sub)
    if what in ("audio", "av", "all"): rm_tree(ch_dir / audio_sub)
    if what in ("video", "av", "all"): rm_glob("*.mp4")
    if what == "all": rm_tree(ch_dir / "work")
    log(f"[delete] ch{chapter:02d} {what}: {', '.join(removed) or 'nothing removed'}")


def _purge(kind: str) -> int:
    from mangaeasy.web.app.api_workflow import _library_dir
    root = state["project_root"]
    cfg, sc = _read_config()
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    name = str(dl.get("name") or "")
    if not name: return 0
    lib = _library_dir(root, sc)
    manga_dir = lib / name
    if not manga_dir.is_dir(): return 0
    audio_sub = (sc.get("paths") or {}).get("audio_subdir", "audio")
    removed = 0
    for ch_dir in sorted(d for d in manga_dir.iterdir() if d.is_dir() and d.name.isdigit()):
        if kind == "ai-zip":
            for f in ch_dir.glob("*_panels_for_ai.zip"): f.unlink(); removed += 1
        elif kind == "narration":
            for f in ch_dir.glob("narration_*.json"): f.unlink(); removed += 1
        elif kind == "audio":
            ad = ch_dir / audio_sub
            if ad.is_dir(): shutil.rmtree(ad); removed += 1
        elif kind == "video":
            for f in ch_dir.glob("*.mp4"): f.unlink(); removed += 1
    log(f"[purge] {kind}: {removed} items")
    return removed


def _export_ai_zip(chapter: int) -> None:
    from mangaeasy.images.ai_zip import panels_to_ai_zip
    from mangaeasy.web.app.api_workflow import _library_dir
    root = state["project_root"]
    cfg, sc = _read_config()
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    name = str(dl.get("name") or "")
    if not name: log("[ai-zip] no manga name configured"); return
    ch_dir = _library_dir(root, sc) / name / f"{chapter:02d}"
    panels_path = ch_dir / ((sc.get("paths") or {}).get("panels_subdir", "panels"))
    if not panels_path.is_dir(): log("[ai-zip] panels folder not found"); return
    safe = name.replace(" ", "_")
    out  = ch_dir / f"{safe}_ch{chapter:02d}_panels_for_ai.zip"
    n = panels_to_ai_zip(panels_path, out, log=log, progress=_st.progress)
    log(f"[ai-zip] {n} panels → {out.name}")


def _ensure_narration_for_ocr(chapter: int) -> Path | None:
    """Return the current chapter narration JSON, creating a blank panel template if needed."""
    from mangaeasy.web.app.api_workflow import _library_dir

    root = state["project_root"]
    cfg, sc = _read_config()
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    name = str(dl.get("name") or "")
    if not name:
        log("[got-ocr2] no manga name configured")
        return None

    ch_dir = _library_dir(root, sc) / name / f"{chapter:02d}"
    narr = ch_dir / f"narration_{chapter:02d}.json"
    if narr.exists():
        return narr

    panels_sub = (sc.get("paths") or {}).get("panels_subdir", "panels")
    panels_dir = ch_dir / panels_sub
    if not panels_dir.is_dir():
        log("[got-ocr2] panels folder not found; cut panels first")
        return None

    images = sorted(
        [p.name for p in panels_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMG],
        key=numeric_sort_key,
    )
    if not images:
        log("[got-ocr2] no panel images found")
        return None

    narr.parent.mkdir(parents=True, exist_ok=True)
    narr.write_text(
        json.dumps([{"image": image, "narration": ""} for image in images], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(f"[got-ocr2] created {narr.name} from {len(images)} panels")
    return narr


# ---------------------------------------------------------------------------
# File pickers (blocking — always call via ni_run.io_bound from async handlers)
# ---------------------------------------------------------------------------

def _pick_dir() -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk(); r.withdraw(); r.wm_attributes("-topmost", 1)
        p = filedialog.askdirectory(); r.destroy()
        return p or None
    except Exception:
        return None


def _pick_file(*exts: str) -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk(); r.withdraw(); r.wm_attributes("-topmost", 1)
        ft = [("Media", " ".join(f"*.{e}" for e in exts))] if exts else [("All", "*")]
        p = filedialog.askopenfilename(filetypes=ft); r.destroy()
        return p or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Editor launcher (Flask-based editors open as browser pages)
# ---------------------------------------------------------------------------

def _expected_editor_url(name: str) -> str | None:
    _, sc = _read_config()
    ports = sc.get("ports") if isinstance(sc.get("ports"), dict) else {}
    port_map = {
        "cut-page": int(ports.get("cut_page", 5000)),
        "panel-editor": 5000,
        "narration-editor": int(ports.get("narration_editor", 5003)),
        "narration-editor-all": int(ports.get("narration_editor_all", 5005)),
        "narration-review": int(ports.get("narration_review", 5006)),
    }
    port = port_map.get(name)
    return f"http://127.0.0.1:{port}/" if port else None


_EDITOR_LABELS = {
    "cut-page": "Crop",
    "panel-editor": "Panel Editor",
    "narration-editor": "Narration Writer",
    "narration-editor-all": "Narration Writer (All)",
    "narration-review": "Narration Review",
}


def _launch_editor(name: str, open_in_app: Callable[[str, str], None] | None = None) -> None:
    label = _EDITOR_LABELS.get(name, name)
    if open_in_app:
        _begin_progress(f"Opening {label}")
    existing = state["editors"].get(name)
    if existing and existing.poll() is None:
        url = state["editor_urls"].get(name) or _expected_editor_url(name)
        if url:
            if open_in_app:
                open_in_app(name, url)
                _finish_progress(f"{label} open")
            else:
                ui.navigate.to(url, new_tab=True)
        return

    proc = jobs.spawn_cli(name, [], state["project_root"])
    state["editors"][name] = proc
    url = _expected_editor_url(name)
    if url:
        if open_in_app:
            open_in_app(name, url)
            _finish_progress(f"{label} open")
        else:
            ui.navigate.to(url, new_tab=True)

    def _pump(p, n: str) -> None:
        for line in jobs.iter_lines(p.stdout):
            if line.startswith("MANGAEASY_OPEN_URL:"):
                url = line[len("MANGAEASY_OPEN_URL:"):]
                state["editor_urls"][n] = url
                log(f"[{n}] ready at {url}")
            else:
                log(line)
        p.wait()
        state["editors"].pop(n, None)
        state["editor_urls"].pop(n, None)

    threading.Thread(target=_pump, args=(proc, name), daemon=True).start()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def _fmt(secs: float) -> str:
    s = max(0, int(secs)); m, s = divmod(s, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _set_badge(badge, count: int, done_fmt: str, todo_fmt: str) -> None:
    if count:
        badge.text = f"{count} {done_fmt}"; badge.props("color=positive")
    else:
        badge.text = todo_fmt; badge.props("color=grey")


def _configured_bgm_file(cfg: dict, sys_cfg: dict) -> str:
    bgm_cfg = sys_cfg.get("bgm") if isinstance(sys_cfg.get("bgm"), dict) else {}
    audio_cfg = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}
    return str(bgm_cfg.get("file") or audio_cfg.get("bgm") or "").strip()


def _library_manga_entries() -> tuple[Path, list[dict[str, object]]]:
    from mangaeasy.web.app.api_workflow import _library_dir

    root = state["project_root"]
    cfg, sc = _read_config()
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    current_name = str(dl.get("name") or "")
    library = _library_dir(root, sc)
    entries: list[dict[str, object]] = []
    if library.is_dir():
        for folder in sorted(
            (p for p in library.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        ):
            chapters = sorted(
                (p.name for p in folder.iterdir() if p.is_dir() and p.name.isdigit()),
                key=lambda name: int(name),
            )
            count = len(chapters)
            label = f"{folder.name} ({count} chapter{'s' if count != 1 else ''})"
            entries.append({
                "name": folder.name,
                "path": folder.resolve(),
                "label": label,
                "chapter_count": count,
                "selected": folder.name == current_name,
            })
    return library, entries


def _open_folder_in_manager(path: Path) -> None:
    path = path.resolve()
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - local desktop convenience
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# Language options (shared by Project + Workflow tabs)
_LANGS = {
    "en": "English", "es": "Spanish", "es-la": "Spanish (LATAM)",
    "pt-br": "Portuguese BR", "fr": "French", "de": "German",
    "it": "Italian", "ru": "Russian", "id": "Indonesian",
    "vi": "Vietnamese", "th": "Thai", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean",
}

# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@ui.page("/")
def page() -> None:  # noqa: C901 PLR0912 PLR0915
    ui.dark_mode(True)
    ui.add_head_html("""<style>
      body { font-family: 'Segoe UI', Roboto, Arial, sans-serif; background:#1e1e1e; }
      .section { background:#232324 !important; border:1px solid #3a3a3e !important;
                 border-radius:8px; padding:14px 18px; margin:10px 0; }
      .mono { font-family:Consolas,'Courier New',monospace !important; font-size:12px; }
      .q-tab-panels { background:#1e1e1e !important; }
      .q-tab-panel { padding:18px 24px; max-width:980px; }

      /* Fill the real window height instead of overflowing it (was hard-coded
         vh-minus-pixels guesses that drifted whenever the header/progress bar
         changed height — most visible when the window is maximized/fullscreen
         and the slack shows up as dead space or a stray scrollbar). */
      .q-page { height: calc(100vh - 52px) !important; overflow: hidden;
                display: flex; flex-direction: column; }
      .nicegui-content { display: flex; flex-direction: column; flex: 1 1 auto;
                          min-height: 0; overflow: hidden; }
      .q-tab-panels { flex: 1 1 auto; min-height: 0; }
      .q-tab-panels .q-panel.scroll { height: 100%; }
      .q-tab-panel { height: 100%; box-sizing: border-box; }
      .q-tab-panel.full-bleed { padding: 0 !important; max-width: none !important; overflow: hidden; }
      .q-tabs .q-tab {
        min-height: 38px; color: #a8a8a8; border-radius: 6px 6px 0 0;
        margin: 4px 2px 0; padding: 0 14px;
      }
      .q-tabs .q-tab:hover { background: #2d2d30; color: #fff; }
      .q-tabs .q-tab.q-tab--active {
        background: #0e3a5e; color: #fff; box-shadow: inset 0 -3px #6cc0ff;
      }
      .q-tabs .q-tab.q-tab--active .q-tab__label { font-weight: 600; }
      .q-tabs .q-tab .q-tab__indicator { height: 3px; background: transparent; }
      .q-tabs .q-tab.q-tab--active .q-tab__indicator { background: #6cc0ff; }
    </style>""")

    # ── header ────────────────────────────────────────────────────────────────
    with ui.header(elevated=False).classes(
        "bg-[#252526] border-b border-[#3e3e42] items-center gap-6 px-4 h-[52px]"
    ):
        ui.label("mangaEasy").classes("text-white font-semibold text-[15px]")
        ui.label(f"v{__version__}").classes("text-gray-500 text-xs")
        ui.space()
        job_badge = ui.badge("idle", color="grey").classes("text-xs")

    # ── slim progress bar below header ────────────────────────────────────────
    with ui.row().classes(
        "w-full bg-[#161616] border-b border-[#222] px-4 items-center gap-3 h-8"
    ) as prog_row:
        prog_row.visible = False
        with ui.element("div").classes("flex-1 min-w-[180px]"):
            prog_bar = ui.linear_progress(0).props("rounded size=6px").classes("w-full")
            prog_busy = ui.linear_progress(0).props("indeterminate rounded size=6px").classes("w-full")
            prog_busy.visible = False
        prog_lbl = ui.label("").classes("text-[11px] text-gray-400 font-mono whitespace-nowrap")

    # ── tabs ──────────────────────────────────────────────────────────────────
    with ui.tabs().classes(
        "bg-[#252526] border-b border-[#3e3e42] px-2 min-h-[40px]"
    ) as tabs:
        ui.tab("setup",    label="1 · Setup")
        ui.tab("project",  label="2 · Project")
        ui.tab("workflow", label="3 · Make a video")
        ui.tab("run",      label="Batch videos")
        ui.tab("editor",   label="Editor")
        ui.tab("terminal", label="Terminal")

    with ui.tab_panels(tabs, value="setup").classes("w-full flex-1 bg-[#1e1e1e]"):

        # ══════════════════════════════════════════════════════════════════════
        # 1 · SETUP
        # ══════════════════════════════════════════════════════════════════════
        with ui.tab_panel("setup"):
            ui.label(
                "Check that the required programs are on PATH, then install the AI tools you need."
            ).classes("text-gray-400 text-[13px] mb-3")

            with ui.row().classes("items-center gap-3 mb-1"):
                ui.label("Prerequisites").classes("text-white font-semibold")
                prereq_spin = ui.spinner("dots", size="xs").classes("text-gray-400")

            prereq_grid = ui.grid(columns=4).classes("gap-2 mb-4 w-full")

            with ui.row().classes("items-center gap-3 mt-2 mb-1"):
                ui.label("AI tools").classes("text-white font-semibold")
                cpu_cb  = ui.checkbox("CPU-only").props("dense")
                skip_cb = ui.checkbox("Skip model download").props("dense")
                ui.space()

                async def _refresh_all() -> None:
                    from mangaeasy.tools.install import doctor
                    _begin_progress("Checking tools")
                    prereq_spin.visible = True
                    try:
                        data = await ni_run.io_bound(doctor)
                    finally:
                        prereq_spin.visible = False
                        _finish_progress("Tool check complete")
                    prereq_grid.clear()
                    with prereq_grid:
                        for exe, where in data.get("executables", {}).items():
                            ok = bool(where)
                            with ui.card().tight().classes("section"):
                                with ui.row().classes("items-center gap-2 p-2"):
                                    ui.icon("check_circle" if ok else "cancel",
                                            color="positive" if ok else "negative"
                                            ).classes("text-base")
                                    ui.label(exe).classes("text-[12px]")
                        for lbl, ok in [
                            ("git-lfs",   data.get("git_lfs", False)),
                            ("NVIDIA GPU", data.get("gpu",     False)),
                            ("CUDA torch", data.get("cuda",    False)),
                        ]:
                            with ui.card().tight().classes("section"):
                                with ui.row().classes("items-center gap-2 p-2"):
                                    ui.icon("check_circle" if ok else "cancel",
                                            color="positive" if ok else "grey"
                                            ).classes("text-base")
                                    ui.label(lbl).classes("text-[12px]")
                    tool_col.clear()
                    with tool_col:
                        for key, info in data.get("tools", {}).items():
                            installed = info.get("installed", False)
                            with ui.card().classes("section w-full"):
                                with ui.row().classes("items-center gap-3"):
                                    ui.badge(
                                        "installed" if installed else "not installed",
                                        color="positive" if installed else "warning",
                                    ).classes("text-xs")
                                    ui.label(info.get("title", key)).classes("text-white font-semibold")
                                    if info.get("notes"):
                                        ui.label(info["notes"]).classes("text-gray-400 text-xs")
                                    ui.space()
                                    if not installed:
                                        async def _install(k: str = key) -> None:
                                            from mangaeasy.tools.install import TOOLS, InstallError, install_tool
                                            if k not in TOOLS:
                                                return
                                            with lock:
                                                if jobs.job_running():
                                                    ui.notify("Another job is running", type="warning")
                                                return
                                                def _work(name: str = k) -> None:
                                                    try:
                                                        _st.progress(0, 0, f"Installing {name}")
                                                        install_tool(
                                                            name,
                                                            gpu="cpu" if cpu_cb.value else "auto",
                                                            skip_model=skip_cb.value,
                                                            log=log,
                                                        )
                                                    except Exception as exc:
                                                        log(f"[install] FAILED: {exc}")
                                                        _finish_progress(f"{name} failed")
                                                    else:
                                                        _finish_progress(f"{name} installed")
                                                t = threading.Thread(target=_work, daemon=True)
                                                state["job"] = {"kind": "install", "name": k,
                                                                "thread": t, "proc": None}
                                                t.start()
                                            ui.notify(f"Installing {k}…", type="info")
                                        ui.button("Install", on_click=_install, icon="download"
                                                  ).props("flat dense size=sm color=blue")

                ui.button("Refresh", on_click=_refresh_all, icon="refresh"
                          ).props("flat dense size=sm")

            tool_col = ui.column().classes("gap-3 w-full")
            ui.timer(0.1, _refresh_all, once=True)

        # ══════════════════════════════════════════════════════════════════════
        # 2 · PROJECT
        # ══════════════════════════════════════════════════════════════════════
        with ui.tab_panel("project"):
            ui.label(
                "The project folder is your workspace — all downloaded chapters, "
                "configs and output live under it."
            ).classes("text-gray-400 text-[13px] mb-3")

            ui.label("Project folder").classes("text-white font-semibold")
            with ui.row().classes("items-center gap-2 w-full mb-4"):
                root_inp = ui.input(value=str(state["project_root"])).props(
                    "dense outlined").classes("flex-1 mono")

                async def _browse_root() -> None:
                    p = await ni_run.io_bound(_pick_dir)
                    if p:
                        root_inp.value = p

                def _use_root() -> None:
                    p = Path(root_inp.value.strip()).expanduser()
                    if not p.is_dir():
                        ui.notify(f"Not a folder: {p}", type="negative"); return
                    state["project_root"] = p.resolve()
                    save_app_state()
                    log(f"[app] project → {p.resolve()}")
                    ui.notify("Project folder set", type="positive")
                    _load_fields()

                ui.button("Browse…", on_click=_browse_root).props("flat dense size=sm")
                ui.button("Use this folder", on_click=_use_root).props("dense color=primary")

            ui.label("Manga settings").classes("text-white font-semibold mt-2")
            with ui.grid(columns=2).classes("gap-3 mb-3 w-full"):
                manga_id_inp = ui.input(label="Manga URL / ID").props("dense outlined")
                name_inp     = ui.input(label="Name").props("dense outlined")
                chapter_inp  = ui.number(label="Chapter", min=0, step=1, value=1).props("dense outlined")
                lang_inp     = ui.select(_LANGS, value="en", label="Language").props("dense outlined")

            ui.label("Background music").classes("text-white font-semibold mt-1")
            with ui.row().classes("items-center gap-2 w-full mb-2"):
                bgm_inp = ui.input(placeholder="music/background.mp3").props(
                    "dense outlined").classes("flex-1 mono")
                bgm_db_inp = ui.number(label="BGM volume", step=1, value=-25).props(
                    "dense outlined suffix=dB").classes("w-32")

                async def _browse_bgm() -> None:
                    p = await ni_run.io_bound(_pick_file, "mp3", "wav", "m4a", "flac")
                    if p:
                        bgm_inp.value = p

                ui.button("Browse…", on_click=_browse_bgm).props("flat dense size=sm")

            ui.label("Voice reference").classes("text-white font-semibold mt-1")
            with ui.row().classes("items-center gap-2 w-full mb-3"):
                voice_inp = ui.input(placeholder="vocal/my_voice.wav").props(
                    "dense outlined").classes("flex-1 mono")

                async def _browse_voice() -> None:
                    p = await ni_run.io_bound(_pick_file, "wav", "mp3", "flac", "m4a")
                    if p:
                        voice_inp.value = p

                ui.button("Browse…", on_click=_browse_voice).props("flat dense size=sm")

            def _sync_media_settings(cfg: dict, sc: dict) -> None:
                bgm_value = str(bgm_inp.value or "").strip()
                bgm_cfg = sc.get("bgm") if isinstance(sc.get("bgm"), dict) else {}
                sc["bgm"] = bgm_cfg
                audio_cfg = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}

                if bgm_value:
                    bgm_cfg["file"] = bgm_value
                    audio_cfg["bgm"] = bgm_value
                    cfg["audio"] = audio_cfg
                else:
                    bgm_cfg.pop("file", None)
                    audio_cfg.pop("bgm", None)
                    if audio_cfg:
                        cfg["audio"] = audio_cfg
                    else:
                        cfg.pop("audio", None)

                try:
                    bgm_cfg["volume_db"] = float(bgm_db_inp.value)
                except (TypeError, ValueError):
                    bgm_cfg["volume_db"] = -25.0

                voice_value = str(voice_inp.value or "").strip()
                tts_cfg = sc.get("tts") if isinstance(sc.get("tts"), dict) else {}
                sc["tts"] = tts_cfg
                cfg_tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
                if voice_value:
                    tts_cfg["speaker_wav"] = voice_value
                    cfg_tts["speaker_wav"] = voice_value
                    cfg["tts"] = cfg_tts
                else:
                    tts_cfg.pop("speaker_wav", None)
                    cfg_tts.pop("speaker_wav", None)
                    if cfg_tts:
                        cfg["tts"] = cfg_tts
                    else:
                        cfg.pop("tts", None)

            def _save_project() -> None:
                cfg, sc = _read_config()
                dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
                dl["manga_id"]            = manga_id_inp.value
                dl["name"]                = name_inp.value
                dl["chapter"]             = int(chapter_inp.value or 1)
                dl["translated_language"] = lang_inp.value
                cfg["download"] = dl
                _sync_media_settings(cfg, sc)
                _write_config(cfg, sc)
                syscfg_area.value = json.dumps(sc, indent=2)
                ui.notify("Settings saved", type="positive")

            ui.button("Save settings", on_click=_save_project, icon="save").props("color=primary")

            with ui.expansion("Advanced: config.system.json", icon="settings").classes("mt-4 w-full"):
                syscfg_area = ui.textarea().props("dense outlined rows=16").classes("w-full mono")

                def _save_sys() -> None:
                    try:
                        sc = json.loads(syscfg_area.value)
                        _write_config(None, sc)
                        ui.notify("config.system.json saved", type="positive")
                    except json.JSONDecodeError as e:
                        ui.notify(f"JSON error: {e}", type="negative")

                ui.button("Save config.system.json", on_click=_save_sys, icon="save").props(
                    "dense color=primary mt-2")

            def _load_fields() -> None:
                cfg, sc = _read_config()
                dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
                manga_id_inp.value = dl.get("manga_id", "")
                name_inp.value     = dl.get("name", "")
                chapter_inp.value  = dl.get("chapter", 1)
                lang_inp.value     = dl.get("translated_language", "en")
                bgm_cfg = sc.get("bgm") if isinstance(sc.get("bgm"), dict) else {}
                audio_cfg = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}
                bgm_inp.value = bgm_cfg.get("file") or audio_cfg.get("bgm", "")
                try:
                    bgm_db_inp.value = float(bgm_cfg.get("volume_db", -25))
                except (TypeError, ValueError):
                    bgm_db_inp.value = -25
                sys_tts = sc.get("tts") if isinstance(sc.get("tts"), dict) else {}
                cfg_tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
                voice_inp.value = sys_tts.get("speaker_wav") or cfg_tts.get("speaker_wav", "")
                try:
                    syscfg_area.value = json.dumps(sc, indent=2)
                except Exception:
                    syscfg_area.value = ""

            ui.timer(0.1, _load_fields, once=True)

        # ══════════════════════════════════════════════════════════════════════
        # 3 · WORKFLOW  (Make a video)
        # ══════════════════════════════════════════════════════════════════════
        with ui.tab_panel("workflow"):
            ui.label("One chapter, start to finish. Work top to bottom."
                     ).classes("text-gray-400 text-[13px] mb-3")

            # ── Step 1: Download ──────────────────────────────────────────────
            with ui.card().classes("section w-full"):
                with ui.row().classes("items-center gap-3 mb-3"):
                    ui.badge("1", color="blue").classes("text-sm min-w-[22px]")
                    ui.label("Download pages").classes("text-white font-semibold")
                    wf_dl_badge = ui.badge("–", color="grey").classes("text-xs ml-auto")

                with ui.row().classes("items-center gap-3 mb-2"):
                    wf_ch   = ui.number(label="Chapter", min=1, step=1, value=1).props(
                        "dense outlined").classes("w-28")
                    wf_lang = ui.select(_LANGS, value="en", label="Language").props(
                        "dense outlined").classes("w-44")

                with ui.row().classes("items-center gap-2 mb-2") as dl_range_row:
                    dl_range_row.visible = False
                    dl_from = ui.number(label="From ch", min=1, step=1, value=1).props(
                        "dense outlined").classes("w-24")
                    dl_to   = ui.number(label="To ch",   min=1, step=1, value=5).props(
                        "dense outlined").classes("w-24")

                dl_mode = ui.radio({"single": "Single", "range": "Range"},
                                   value="single").props("dense inline")
                dl_mode.on("update:model-value",
                           lambda e: setattr(dl_range_row, "visible", e == "range"))
                dl_fresh = ui.checkbox("Force fresh metadata").props("dense")

                def _dl_run() -> None:
                    _save_wf_cfg(int(wf_ch.value or 1), wf_lang.value)
                    if dl_mode.value == "range":
                        _batch_download(int(dl_from.value or 1), int(dl_to.value or 1),
                                        dl_fresh.value)
                    else:
                        _run_job("download", ["--fresh"] if dl_fresh.value else [])

                with ui.row().classes("gap-2 mt-1"):
                    ui.button("⬇ Download", on_click=_dl_run).props("color=primary dense")
                    ui.button("■ Stop", on_click=_stop_job).props("color=negative dense")

            # ── Step 2: Panels ────────────────────────────────────────────────
            with ui.card().classes("section w-full"):
                with ui.row().classes("items-center gap-3 mb-3"):
                    ui.badge("2", color="blue").classes("text-sm min-w-[22px]")
                    ui.label("Crop into panels").classes("text-white font-semibold")
                    wf_panel_badge = ui.badge("–", color="grey").classes("text-xs ml-auto")

                with ui.row().classes("gap-2"):
                    def _cut() -> None:
                        _save_wf_cfg_now(); _launch_editor("cut-page", _set_editor_frame)
                    def _arrange() -> None:
                        _save_wf_cfg_now(); _launch_editor("panel-editor", _set_editor_frame)
                    ui.button("✂ Cut pages (manga / manhua)", on_click=_cut
                              ).props("color=primary dense")
                    ui.button("⬇ Arrange strips (webtoon)", on_click=_arrange
                              ).props("color=primary dense")

            # ── Step 3: Narration ─────────────────────────────────────────────
            with ui.card().classes("section w-full"):
                with ui.row().classes("items-center gap-3 mb-3"):
                    ui.badge("3", color="blue").classes("text-sm min-w-[22px]")
                    ui.label("Write the narration").classes("text-white font-semibold")
                    wf_narr_badge = ui.badge("–", color="grey").classes("text-xs ml-auto")

                with ui.row().classes("gap-2"):
                    def _narr_edit() -> None:
                        _save_wf_cfg_now()
                        _ensure_narration_for_ocr(int(wf_ch.value or 1))
                        _launch_editor("narration-editor", _set_editor_frame)
                    ui.button("\U0001f4dd Open narration editor", on_click=_narr_edit
                              ).props("color=primary dense")

                    def _ocr_current(force: bool = False) -> None:
                        _save_wf_cfg_now()
                        narr = _ensure_narration_for_ocr(int(wf_ch.value or 1))
                        if not narr:
                            ui.notify("No panels/narration found for OCR", type="warning")
                            return
                        args = ["--narration", str(narr), "--device", "auto"]
                        if force:
                            args.append("--force")
                        _run_job("got-ocr2", args)

                    ui.button("Run GOT-OCR", on_click=lambda: _ocr_current(False)).props("flat dense")
                    ui.button("Re-run GOT-OCR", on_click=lambda: _ocr_current(True)).props("flat dense")

                    async def _zip() -> None:
                        ch = int(wf_ch.value or 1)
                        _begin_progress("Exporting AI ZIP")
                        try:
                            await ni_run.io_bound(_export_ai_zip, ch)
                            _finish_progress("ZIP exported")
                            ui.notify("ZIP exported", type="positive")
                        except Exception as exc:
                            _finish_progress("ZIP export failed")
                            ui.notify(f"ZIP export failed: {exc}", type="negative")
                    ui.button("⬇ Export ZIP for AI", on_click=_zip).props("flat dense size=sm")

            # ── Step 4: Generate ──────────────────────────────────────────────
            with ui.card().classes("section w-full"):
                with ui.row().classes("items-center gap-3 mb-3"):
                    ui.badge("4", color="blue").classes("text-sm min-w-[22px]")
                    ui.label("Generate audio & video").classes("text-white font-semibold")
                    wf_gen_badge = ui.badge("–", color="grey").classes("text-xs ml-auto")

                norm_cb = ui.checkbox("YouTube loudness (−14 LUFS)").props("dense")

                def _video_steps(include_audio_prep: bool = True) -> list[str]:
                    cfg, sc = _read_config()
                    steps = ["render-video"]
                    if include_audio_prep:
                        steps.insert(0, "fade-audio")
                    if _configured_bgm_file(cfg, sc):
                        steps.append("add-bgm")
                    if norm_cb.value:
                        steps.append("normalize-chapter-audio")
                    return steps

                def _gen_all()   -> None: _save_wf_cfg_now(); _run_chain(["index-tts"] + _video_steps())
                def _gen_audio() -> None: _save_wf_cfg_now(); _run_job("index-tts")
                def _gen_video() -> None: _save_wf_cfg_now(); _run_chain(_video_steps())
                def _rerender_video() -> None: _save_wf_cfg_now(); _run_chain(_video_steps(False))

                with ui.row().classes("gap-2"):
                    ui.button("▶ Everything", on_click=_gen_all).props("color=primary")
                    ui.button("\U0001f399 Audio only", on_click=_gen_audio).props("flat dense")
                    ui.button("\U0001f3ac Video only", on_click=_gen_video).props("flat dense")
                    ui.button("Re-render video", on_click=_rerender_video).props("flat dense")

                with ui.expansion("Delete chapter data…", icon="delete"
                                  ).classes("mt-2 w-full"):
                    def _del(w: str) -> None:
                        _delete_chapter(int(wf_ch.value or 1), w)
                        _refresh_wf_badges()
                    with ui.row().classes("gap-2 mt-1"):
                        ui.button("✕ Downloads", on_click=lambda: _del("download")
                                  ).props("flat dense size=sm color=negative")
                        ui.button("✕ Panels", on_click=lambda: _del("panels")
                                  ).props("flat dense size=sm color=negative")
                        ui.button("✕ Audio", on_click=lambda: _del("audio")
                                  ).props("flat dense size=sm color=negative")
                        ui.button("✕ Video", on_click=lambda: _del("video")
                                  ).props("flat dense size=sm color=negative")
                        ui.button("✕ Everything", on_click=lambda: _del("all")
                                  ).props("flat dense size=sm color=negative")

            # ── Purge all chapters ─────────────────────────────────────────────
            with ui.card().classes("section w-full border-[#3a2020]"):
                ui.label("\U0001f9f9 Purge across all chapters").classes(
                    "text-white font-semibold mb-1")
                ui.label("Remove a file category from every chapter of this manga."
                         ).classes("text-gray-400 text-xs mb-2")
                def _do_purge(k: str) -> None:
                    n = _purge(k); ui.notify(f"Purged {k}: {n} items", type="positive")
                with ui.row().classes("gap-2"):
                    ui.button("✕ AI ZIPs",   on_click=lambda: _do_purge("ai-zip")
                              ).props("flat dense size=sm color=negative")
                    ui.button("✕ Narration",  on_click=lambda: _do_purge("narration")
                              ).props("flat dense size=sm color=negative")
                    ui.button("✕ Audio",      on_click=lambda: _do_purge("audio")
                              ).props("flat dense size=sm color=negative")
                    ui.button("✕ Video",      on_click=lambda: _do_purge("video")
                              ).props("flat dense size=sm color=negative")

            # ── Helpers for this tab ──────────────────────────────────────────
            def _save_wf_cfg(ch: int | None = None, lang: str | None = None) -> None:
                cfg, _ = _read_config()
                dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
                if ch is not None:   dl["chapter"] = ch
                if lang is not None: dl["translated_language"] = lang
                cfg["download"] = dl
                _write_config(cfg, None)

            def _save_wf_cfg_now() -> None:
                _save_wf_cfg(int(wf_ch.value or 1), wf_lang.value)

            def _refresh_wf_badges() -> None:
                cfg, _ = _read_config()
                dl   = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
                name = str(dl.get("name") or "")
                ch   = int(wf_ch.value or dl.get("chapter") or 1)
                if dl.get("chapter") and int(wf_ch.value or 1) == 1:
                    wf_ch.value = dl["chapter"]
                if dl.get("translated_language"):
                    wf_lang.value = dl["translated_language"]
                if not name:
                    for b in (wf_dl_badge, wf_panel_badge, wf_narr_badge, wf_gen_badge):
                        b.text = "set manga name first"; b.props("color=grey")
                    return
                st = _ch_status(name, ch)
                _set_badge(wf_dl_badge,    st["dl"],     "pages",     "no pages")
                _set_badge(wf_panel_badge, st["panels"], "panels",    "no panels")
                if st["narr"] and st["narr_items"]:
                    wf_narr_badge.text = f"{st['narr_items']} lines"
                    wf_narr_badge.props("color=positive")
                else:
                    wf_narr_badge.text = "not written"; wf_narr_badge.props("color=grey")
                if st["video"]:
                    wf_gen_badge.text = "video ready"; wf_gen_badge.props("color=positive")
                elif st["audio"]:
                    wf_gen_badge.text = f"{st['audio']} clips (no video)"
                    wf_gen_badge.props("color=warning")
                else:
                    wf_gen_badge.text = "not generated"; wf_gen_badge.props("color=grey")

            wf_ch.on("update:model-value", lambda _: _refresh_wf_badges())
            ui.timer(0.1, _refresh_wf_badges, once=True)
            ui.timer(8.0, _refresh_wf_badges)

        # ══════════════════════════════════════════════════════════════════════
        # 4 · BATCH VIDEOS
        # ══════════════════════════════════════════════════════════════════════
        with ui.tab_panel("run"):
            ui.label("Pick a manga from library/, choose a chapter range, then generate narrated videos."
                     ).classes("text-gray-400 text-[13px] mb-3")

            with ui.card().classes("section w-full mb-3"):
                ui.label("Manga and chapters").classes("text-white font-semibold mb-2")
                with ui.row().classes("items-center gap-2 w-full mb-1"):
                    manga_sel = ui.select({}, label="Manga folder").props(
                        "dense outlined options-dense").classes("flex-1 mono")
                    ui.button("Refresh", on_click=lambda: _refresh_batch_mangas()).props(
                        "flat dense size=sm")

                    async def _open_selected_manga() -> None:
                        selected = _selected_batch_manga()
                        if selected is None:
                            return
                        await ni_run.io_bound(_open_folder_in_manager, selected)

                    ui.button("Open", on_click=_open_selected_manga).props("flat dense size=sm")
                manga_hint = ui.label("").classes("text-gray-500 text-xs mb-2")
                with ui.row().classes("items-center gap-3"):
                    use_range_cb = ui.checkbox("Use chapter range", value=True).props("dense")
                    range_from = ui.number(label="From", min=1, step=1, value=1).props(
                        "dense outlined").classes("w-24")
                    range_to = ui.number(label="To", min=1, step=1, value=24).props(
                        "dense outlined").classes("w-24")

            with ui.card().classes("section w-full mb-3"):
                ui.label("What to do").classes("text-white font-semibold mb-2")
                with ui.grid(columns=2).classes("gap-3 w-full"):
                    _PIPELINE_STEPS = {
                        "video":                 "Everything (IndexTTS + blur + long video)",
                        "video-check":           "Check items only",
                        "got-ocr2":              "Fill OCR fields (GOT-OCR 2.0)",
                        "video-audio":           "Audio only (Kokoro)",
                        "video-audio-indextts":  "Audio only (IndexTTS)",
                        "video-render":          "Render videos only",
                        "video-join":            "Join into one long video",
                        "video-normalize-audio": "Loudness-normalize joined audio",
                        "video-clean-audio":     "Delete generated audio",
                        "video-clean-video":     "Delete rendered videos",
                        "video-validate":        "Validate generated output",
                    }
                    step_sel = ui.select(_PIPELINE_STEPS, value="video",
                                         label="Step").props("dense outlined")
                    _TTS = {"auto": "Auto", "indextts": "IndexTTS", "kokoro": "Kokoro"}
                    tts_sel = ui.select(_TTS, value="indextts",
                                        label="Voice engine").props("dense outlined")
                with ui.row().classes("gap-4 mt-2"):
                    long_cb  = ui.checkbox("Generate one long video", value=True).props("dense")
                    norm_bat = ui.checkbox("YouTube loudness", value=True).props("dense")
                    bgm_cb   = ui.checkbox("Background music", value=True).props("dense")
                    ocr_force_cb = ui.checkbox("Redo all OCR (overwrite existing)", value=False).props("dense")
                    ocr_force_cb.bind_visibility_from(step_sel, "value", backward=lambda v: v == "got-ocr2")
                bgm_hint = ui.label("").classes("text-gray-500 text-xs mt-1")

            with ui.card().classes("section w-full"):
                ui.label("Output").classes("text-white font-semibold mb-2")
                with ui.row().classes("items-center gap-2 w-full mb-3"):
                    out_dir_inp = ui.input(value="output", label="Output folder").props(
                        "dense outlined").classes("flex-1 mono")

                    async def _browse_out() -> None:
                        p = await ni_run.io_bound(_pick_dir)
                        if p:
                            out_dir_inp.value = p

                    ui.button("Browse…", on_click=_browse_out).props("flat dense size=sm")

                output_hint = ui.label("").classes("text-gray-500 text-xs mb-2")

                def _selected_batch_manga() -> Path | None:
                    raw = str(manga_sel.value or "").strip()
                    if not raw:
                        ui.notify("Pick a manga folder from library/ first", type="warning")
                        return None
                    path = Path(raw).expanduser()
                    if not path.is_dir():
                        ui.notify(f"Manga folder not found: {path}", type="negative")
                        return None
                    return path.resolve()

                def _batch_output_root() -> Path:
                    raw = str(out_dir_inp.value or "output").strip() or "output"
                    path = Path(raw).expanduser()
                    if not path.is_absolute():
                        path = state["project_root"] / path
                    return path.resolve()

                def _batch_project_output_dir(manga_path: Path | None = None) -> Path | None:
                    selected = manga_path or _selected_batch_manga()
                    if selected is None:
                        return None
                    return _batch_output_root() / selected.name

                def _batch_audio_root(manga_path: Path | None = None) -> Path | None:
                    project_output = _batch_project_output_dir(manga_path)
                    return None if project_output is None else project_output / "audio"

                def _batch_range_arg() -> str | None:
                    if not use_range_cb.value:
                        return None
                    start = int(range_from.value or 1)
                    end = int(range_to.value or start)
                    if end < start:
                        start, end = end, start
                    return f"{start:02d}-{end:02d}"

                def _batch_base_args(*, output: bool = False, items: bool = True) -> list[str] | None:
                    manga_path = _selected_batch_manga()
                    if manga_path is None:
                        return None
                    args = ["--project-root", str(manga_path)]
                    if output:
                        args += ["--output-root", str(_batch_output_root())]
                    item_range = _batch_range_arg() if items else None
                    if item_range:
                        args += ["--item-range", item_range]
                    return args

                def _append_audio_root(args: list[str], manga_path: Path | None = None) -> None:
                    audio_root = _batch_audio_root(manga_path)
                    if audio_root is not None:
                        args += ["--audio-root", str(audio_root)]

                def _append_bgm(args: list[str]) -> None:
                    cfg, sc = _read_config()
                    bgm = _configured_bgm_file(cfg, sc)
                    if bgm_cb.value and bgm:
                        args += ["--background-music", bgm]
                    elif bgm_cb.value:
                        ui.notify("No background music set in Project tab", type="warning")

                def _refresh_batch_hints() -> None:
                    library, entries = _library_manga_entries()
                    selected = str(manga_sel.value or "")
                    current = next((entry for entry in entries if str(entry["path"]) == selected), None)
                    if current is not None:
                        manga_hint.text = f"{current['chapter_count']} chapter folder(s) selected from {library}"
                    elif entries:
                        manga_hint.text = f"{len(entries)} manga folder(s) found in {library}"
                    else:
                        manga_hint.text = f"No manga folders found in {library}"
                    cfg, sc = _read_config()
                    bgm = _configured_bgm_file(cfg, sc)
                    bgm_hint.text = f"BGM: {bgm}" if bgm else "BGM: not set in Project tab"
                    selected_path = Path(selected) if selected else None
                    audio_root = _batch_audio_root(selected_path) if selected_path else None
                    if current is not None and audio_root is not None:
                        output_hint.text = (
                            f"Reusable files: {_batch_output_root() / selected_path.name} "
                            f"(audio cache under {audio_root})"
                        )
                    else:
                        output_hint.text = "Reusable files will be grouped under output/<manga name>/"

                def _refresh_batch_mangas() -> None:
                    _, entries = _library_manga_entries()
                    options = {str(entry["path"]): str(entry["label"]) for entry in entries}
                    selected = str(manga_sel.value or "")
                    if selected not in options:
                        selected_entry = next((entry for entry in entries if entry["selected"]), None)
                        selected = str((selected_entry or entries[0])["path"]) if entries else ""
                    manga_sel.options = options
                    manga_sel.value = selected or None
                    manga_sel.update()
                    _refresh_batch_hints()

                def _batch_start() -> None:
                    step = step_sel.value
                    if step == "got-ocr2":
                        args = _batch_base_args(items=True)
                        if args is None:
                            return
                        args += ["--device", "auto"]
                        if ocr_force_cb.value:
                            args.append("--force")
                        _run_job("got-ocr2", args)
                        return

                    if step == "video":
                        args = _batch_base_args(output=True, items=True)
                        if args is None:
                            return
                        _append_audio_root(args)
                        args += ["--tts", tts_sel.value, "--background-style", "blur", "--blur-backend", "auto"]
                        if long_cb.value:
                            args.append("--build-long-video")
                            _append_bgm(args)
                            if norm_bat.value:
                                args.append("--normalize-audio")
                        _run_job(step, args)
                        return

                    if step == "video-render":
                        args = _batch_base_args(output=True, items=True)
                        if args is None:
                            return
                        _append_audio_root(args)
                        args += ["--background-style", "blur", "--blur-backend", "auto"]
                        _run_job(step, args)
                        return

                    if step == "video-join":
                        args = _batch_base_args(output=True, items=True)
                        if args is None:
                            return
                        args.append("--overwrite")
                        _append_bgm(args)
                        _run_job(step, args)
                        return

                    if step == "video-normalize-audio":
                        args = _batch_base_args(output=True, items=False)
                        if args is None:
                            return
                        args.append("--replace")
                        _run_job(step, args)
                        return

                    if step == "video-audio":
                        args = _batch_base_args(items=True)
                        if args is None:
                            return
                        _append_audio_root(args)
                        args += ["--device", "auto"]
                        _run_job(step, args)
                        return

                    if step == "video-audio-indextts":
                        args = _batch_base_args(items=True)
                        if args is None:
                            return
                        _append_audio_root(args)
                        _run_job(step, args)
                        return

                    output_steps = {"video-validate", "video-clean-video"}
                    audio_steps = {"video-check", "video-validate", "video-clean-audio"}
                    args = _batch_base_args(output=step in output_steps, items=True)
                    if args is None:
                        return
                    if step in audio_steps:
                        _append_audio_root(args)
                    if step in {"video-clean-audio", "video-clean-video"}:
                        args.append("--yes")
                    _run_job(step, args)

                manga_sel.on("update:model-value", lambda _: _refresh_batch_hints())
                ui.timer(0.1, _refresh_batch_mangas, once=True)
                ui.timer(5.0, _refresh_batch_hints)

                with ui.row().classes("gap-3 items-center"):
                    ui.button("▶ Start", on_click=_batch_start).props("color=primary")
                    ui.button("■ Stop",  on_click=_stop_job).props("color=negative")

        # ══════════════════════════════════════════════════════════════════════
        # 5 · TERMINAL
        # ══════════════════════════════════════════════════════════════════════
        with ui.tab_panel("editor").classes("p-0 full-bleed"):
            with ui.column().classes("w-full h-full gap-0"):
                with ui.row().classes(
                    "w-full h-10 bg-[#252526] border-b border-[#3e3e42] items-center px-3 gap-2"
                ):
                    editor_title = ui.label("Editor").classes("text-white text-sm font-semibold")
                    ui.space()
                    ui.button("Reload", on_click=lambda: _reload_editor_frame()).props("flat dense size=sm")
                    ui.button("Close", on_click=lambda: _close_editor_frame()).props("flat dense size=sm color=negative")
                editor_frame = ui.html(
                    '<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#777;">'
                    'Open an editor from the workflow tab.</div>',
                    sanitize=False,
                ).classes("w-full flex-1")

            current_editor = {"name": "", "url": ""}

            def _set_editor_frame(name: str, url: str) -> None:
                current_editor.update({"name": name, "url": url})
                title = _EDITOR_LABELS.get(name, name)
                editor_title.text = title
                frame_url = json.dumps(url)
                frame_title = json.dumps(title)
                editor_frame.set_content(
                    '<div style="height:100%;position:relative;background:#111;">'
                    '<div id="editor-loading" style="position:absolute;inset:0;display:flex;'
                    'align-items:center;justify-content:center;color:#888;font:13px Segoe UI,Arial;">'
                    'Loading editor...</div>'
                    '<iframe id="editor-frame" '
                    'style="width:100%;height:100%;border:0;background:#111;" '
                    f'title={frame_title}></iframe>'
                    '</div>'
                )
                ui.run_javascript(
                    # Probe the editor's own server in the background and point the
                    # iframe at it exactly once it actually answers — repeatedly
                    # reassigning frame.src (the old approach) forced a fresh reload
                    # each attempt, which is what caused the visible flashing.
                    '(() => {'
                    f'const editorUrl = {frame_url};'
                    'const frame = document.getElementById("editor-frame");'
                    'const loading = document.getElementById("editor-loading");'
                    'let attempts = 0;'
                    'let started = false;'
                    'function probe(){'
                    '  if (started) return;'
                    '  attempts += 1;'
                    '  fetch(editorUrl, {mode: "no-cors", cache: "no-store"}).then(() => {'
                    '    if (started) return;'
                    '    started = true;'
                    '    frame.src = editorUrl;'
                    '  }).catch(() => {'
                    '    if (attempts < 10) setTimeout(probe, 700);'
                    '  });'
                    '}'
                    'frame.addEventListener("load", () => { loading.style.display = "none"; });'
                    'probe();'
                    '})();'
                )
                tabs.set_value("editor")

            def _reload_editor_frame() -> None:
                if current_editor["url"]:
                    _set_editor_frame(current_editor["name"], current_editor["url"])

            def _close_editor_frame() -> None:
                name = current_editor.get("name")
                if name:
                    proc = state["editors"].get(name)
                    if proc and proc.poll() is None:
                        proc.terminate()
                current_editor.update({"name": "", "url": ""})
                editor_title.text = "Editor"
                # WebView2 (the native window's renderer) can keep an iframe's
                # last painted frame on screen until its src is cleared, even
                # after the element itself is removed from the DOM.
                ui.run_javascript(
                    'const f = document.getElementById("editor-frame");'
                    'if (f) f.src = "about:blank";'
                )
                editor_frame.set_content(
                    '<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#777;">'
                    'Open an editor from the workflow tab.</div>'
                )

        with ui.tab_panel("terminal").classes("p-0 full-bleed"):
            with ui.element("div").style("height:100%; width:100%"):
                term = ui.xterm({
                    "cursorBlink": False,
                    "disableStdin": True,
                    "fontSize": 13,
                    "fontFamily": "Consolas, 'Courier New', monospace",
                    "scrollback": 20_000,
                    "allowProposedApi": True,
                    "theme": {
                        "background": "#0d0d0d",
                        "foreground": "#d4d4d4",
                        "cursor": "#ffffff",
                        "selectionBackground": "rgba(255,255,255,0.2)",
                        "black": "#000000", "red": "#cc0000",
                        "green": "#4caf50", "yellow": "#e6c000",
                        "blue": "#4d9de0", "magenta": "#af87d7",
                        "cyan": "#00bcd4", "white": "#d4d4d4",
                        "brightBlack": "#555555", "brightRed": "#f87171",
                        "brightGreen": "#6fd388", "brightYellow": "#fbbf24",
                        "brightBlue": "#6cc0ff", "brightMagenta": "#c084fc",
                        "brightCyan": "#67e8f9", "brightWhite": "#ffffff",
                    },
                }).classes("w-full h-full")

            _cur = {"i": 0}

            async def _drain() -> None:
                new = _out[_cur["i"]:]
                if new:
                    term.write("".join(new))
                    _cur["i"] = len(_out)

            ui.timer(0.05, _drain)

            # xterm.js sizes its canvas to its container only when told to —
            # without this it keeps its initial (tiny) size, which looks
            # especially broken once the window is maximized/fullscreen.
            def _fit_terminal() -> None:
                if tabs.value == "terminal":
                    term.fit()

            ui.timer(0.3, _fit_terminal, once=True)
            ui.timer(1.0, _fit_terminal)

    # ── Global job-status timer ───────────────────────────────────────────────
    def _status_tick() -> None:
        global _prog_start
        running = jobs.job_running()
        job = state.get("job")
        active = bool(running or _prog.get("active") or time.monotonic() < float(_prog.get("done_until") or 0.0))
        if active:
            name = str((job or {}).get("name") or _prog.get("label") or "working")[:45]
            job_badge.text = f"● {name}"
            job_badge.props("color=blue")
            prog_row.visible = True
            v, t, lbl = _prog["v"], _prog["t"], _prog["label"]
            if t > 0:
                prog_bar.visible = True
                prog_busy.visible = False
                prog_bar.value = v / t
                elapsed = _fmt(time.monotonic() - _prog_start) if _prog_start else ""
                eta = ""
                if _prog_start and v > 0 and t > v:
                    rate = v / max(time.monotonic() - _prog_start, 0.001)
                    eta = f"  ~{_fmt((t-v)/rate)} left" if rate > 0 else ""
                parts = [p for p in [lbl, f"{v}/{t} ({int(v/t*100)}%)", elapsed + eta] if p]
                prog_lbl.text = "  ·  ".join(parts)
            else:
                prog_bar.visible = False
                prog_busy.visible = True
                prog_bar.value = 0
                prog_lbl.text = (lbl or "working…") + (
                    f"  ·  {_fmt(time.monotonic()-_prog_start)}" if _prog_start else "")
        else:
            job_badge.text = "idle"
            job_badge.props("color=grey")
            if not running:
                prog_row.visible = False
                prog_bar.visible = True
                prog_busy.visible = False
                _prog_start = None
                _prog.update({"v": 0, "t": 0, "label": "", "active": False, "done_until": 0.0})

    ui.timer(0.5, _status_tick)


# ---------------------------------------------------------------------------
# Entry point — replaces Flask + pywebview
# ---------------------------------------------------------------------------

def run() -> None:
    from mangaeasy.web.app.jobs import cleanup
    try:
        ui.run(
            title="mangaEasy",
            native=True,
            dark=True,
            window_size=(1200, 820),
            port=0,      # OS-assigned; NiceGUI manages its own pywebview window
            show=False,  # don't also open the system browser
            reload=False,
            favicon="\U0001f3cc",
        )
    finally:
        cleanup()
