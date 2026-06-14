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
from typing import Optional

from nicegui import run as ni_run, ui

from mangaeasy import __version__
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

_prog: dict = {"v": 0, "t": 0, "label": ""}
_prog_start: Optional[float] = None
_real_prog = _st.progress


def _prog_hook(value: int, total: int, label: str = "") -> None:
    global _prog_start
    _real_prog(value, total, label)
    if value > 0 and _prog_start is None:
        _prog_start = time.monotonic()
    _prog.update({"v": value, "t": total, "label": label})


_st.progress = _prog_hook

# ---------------------------------------------------------------------------
# Job runners
# ---------------------------------------------------------------------------


def _run_job(command: str, args: list[str] | None = None) -> None:
    args = args or []
    with lock:
        if jobs.job_running():
            ui.notify("Another job is already running", type="warning")
            return
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
        job: dict = {"kind": "run", "name": label, "thread": None, "proc": None}

        def _work() -> None:
            for cmd in commands:
                proc = jobs.spawn_cli(cmd, [], state["project_root"])
                job["proc"] = proc
                assert proc.stdout
                while chunk := proc.stdout.read(512):
                    terminal_broadcaster.write_raw(chunk)
                code = proc.wait()
                col = "\x1b[32m" if code == 0 else "\x1b[31m"
                log(f"{col}[{cmd}] exit {code}\x1b[0m")
                if code != 0:
                    log("\x1b[31m[chain] stopped\x1b[0m")
                    return
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

            for i, ch in enumerate(range(start, end + 1), 1):
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
                assert proc.stdout
                while chunk := proc.stdout.read(512):
                    terminal_broadcaster.write_raw(chunk)
                code = proc.wait()
                col = "\x1b[32m" if code == 0 else "\x1b[31m"
                log(f"{col}[download] ch{ch:02d} exit {code}\x1b[0m")
                if code != 0:
                    log("\x1b[31m[batch] stopped\x1b[0m")
                    return
                if ch < end:
                    log("[batch] pausing 10 s…"); time.sleep(10)
            log(f"\x1b[32m[batch] done ✓\x1b[0m")

        t = threading.Thread(target=_work, daemon=True)
        job["thread"] = t
        state["job"] = job
        t.start()


def _stop_job() -> None:
    job = state.get("job")
    if job and job.get("proc"):
        job["proc"].terminate()
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

def _launch_editor(name: str) -> None:
    existing = state["editors"].get(name)
    if existing and existing.poll() is None:
        url = state["editor_urls"].get(name)
        if url:
            import webbrowser
            webbrowser.open(url)
        return

    proc = jobs.spawn_cli(name, [], state["project_root"])
    state["editors"][name] = proc

    def _pump(p, n: str) -> None:
        opened = False
        for line in jobs.iter_lines(p.stdout):
            if line.startswith("MANGAEASY_OPEN_URL:"):
                url = line[len("MANGAEASY_OPEN_URL:"):]
                state["editor_urls"][n] = url
                log(f"[{n}] ready at {url}")
                if not opened:
                    import webbrowser
                    webbrowser.open(url)
                    opened = True
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
        "w-full bg-[#161616] border-b border-[#222] px-4 items-center h-5"
    ) as prog_row:
        prog_row.visible = False
        prog_bar = ui.linear_progress(0).props("rounded size=3px").classes("w-40")
        prog_lbl = ui.label("").classes("text-[11px] text-gray-500 font-mono ml-2")

    # ── tabs ──────────────────────────────────────────────────────────────────
    with ui.tabs().classes(
        "bg-[#252526] border-b border-[#3e3e42] px-2 min-h-[40px]"
    ) as tabs:
        ui.tab("setup",    label="1 · Setup")
        ui.tab("project",  label="2 · Project")
        ui.tab("workflow", label="3 · Make a video")
        ui.tab("run",      label="Batch videos")
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
                    prereq_spin.visible = True
                    data = await ni_run.io_bound(doctor)
                    prereq_spin.visible = False
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
                                                        install_tool(
                                                            name,
                                                            gpu="cpu" if cpu_cb.value else "auto",
                                                            skip_model=skip_cb.value,
                                                            log=log,
                                                        )
                                                    except Exception as exc:
                                                        log(f"[install] FAILED: {exc}")
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

            def _save_project() -> None:
                cfg, sc = _read_config()
                dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
                dl["manga_id"]            = manga_id_inp.value
                dl["name"]                = name_inp.value
                dl["chapter"]             = int(chapter_inp.value or 1)
                dl["translated_language"] = lang_inp.value
                cfg["download"] = dl
                if bgm_inp.value:
                    cfg.setdefault("audio", {})["bgm"] = bgm_inp.value
                if voice_inp.value:
                    cfg.setdefault("tts", {})["speaker_wav"] = voice_inp.value
                _write_config(cfg, None)
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
                bgm_inp.value      = (cfg.get("audio") or {}).get("bgm", "")
                voice_inp.value    = (cfg.get("tts") or {}).get("speaker_wav", "")
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
                        _save_wf_cfg_now(); _launch_editor("cut-page")
                    def _arrange() -> None:
                        _save_wf_cfg_now(); _launch_editor("panel-editor")
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
                        _save_wf_cfg_now(); _launch_editor("narration-editor")
                    ui.button("\U0001f4dd Open narration editor", on_click=_narr_edit
                              ).props("color=primary dense")

                    async def _zip() -> None:
                        ch = int(wf_ch.value or 1)
                        await ni_run.io_bound(_export_ai_zip, ch)
                        ui.notify("ZIP exported", type="positive")
                    ui.button("⬇ Export ZIP for AI", on_click=_zip).props("flat dense size=sm")

            # ── Step 4: Generate ──────────────────────────────────────────────
            with ui.card().classes("section w-full"):
                with ui.row().classes("items-center gap-3 mb-3"):
                    ui.badge("4", color="blue").classes("text-sm min-w-[22px]")
                    ui.label("Generate audio & video").classes("text-white font-semibold")
                    wf_gen_badge = ui.badge("–", color="grey").classes("text-xs ml-auto")

                norm_cb = ui.checkbox("YouTube loudness (−14 LUFS)").props("dense")

                def _video_steps() -> list[str]:
                    cfg, _ = _read_config()
                    steps = ["fade-audio", "render-video"]
                    if (cfg.get("audio") or {}).get("bgm"):
                        steps.append("add-bgm")
                    if norm_cb.value:
                        steps.append("normalize-chapter-audio")
                    return steps

                def _gen_all()   -> None: _save_wf_cfg_now(); _run_chain(["index-tts"] + _video_steps())
                def _gen_audio() -> None: _save_wf_cfg_now(); _run_job("index-tts")
                def _gen_video() -> None: _save_wf_cfg_now(); _run_chain(_video_steps())

                with ui.row().classes("gap-2"):
                    ui.button("▶ Everything", on_click=_gen_all).props("color=primary")
                    ui.button("\U0001f399 Audio only", on_click=_gen_audio).props("flat dense")
                    ui.button("\U0001f3ac Video only", on_click=_gen_video).props("flat dense")

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
            ui.label("Turn every chapter folder (panels + narration) into a narrated video."
                     ).classes("text-gray-400 text-[13px] mb-3")

            with ui.card().classes("section w-full mb-3"):
                ui.label("What to do").classes("text-white font-semibold mb-2")
                with ui.grid(columns=2).classes("gap-3 w-full"):
                    _PIPELINE_STEPS = {
                        "video":                 "Everything (audio + render + join)",
                        "video-check":           "Check items only",
                        "video-audio":           "Audio only (Kokoro)",
                        "video-audio-indextts":  "Audio only (IndexTTS)",
                        "video-render":          "Render videos only",
                        "video-join":            "Join into one long video",
                        "video-normalize-audio": "Loudness-normalize joined audio",
                        "video-clean-audio":     "Delete generated audio",
                        "video-clean-video":     "Delete rendered videos",
                    }
                    step_sel = ui.select(_PIPELINE_STEPS, value="video",
                                         label="Step").props("dense outlined")
                    _TTS = {"auto": "Auto", "indextts": "IndexTTS", "kokoro": "Kokoro"}
                    tts_sel = ui.select(_TTS, value="auto",
                                        label="Voice engine").props("dense outlined")
                with ui.row().classes("gap-4 mt-2"):
                    long_cb  = ui.checkbox("Join into one long video", value=True).props("dense")
                    norm_bat = ui.checkbox("YouTube loudness", value=True).props("dense")

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

                def _batch_start() -> None:
                    args = ["--output", out_dir_inp.value, "--tts", tts_sel.value]
                    if not long_cb.value:   args.append("--no-long")
                    if norm_bat.value:      args.append("--normalize")
                    _run_job(step_sel.value, args)

                with ui.row().classes("gap-3 items-center"):
                    ui.button("▶ Start", on_click=_batch_start).props("color=primary")
                    ui.button("■ Stop",  on_click=_stop_job).props("color=negative")

        # ══════════════════════════════════════════════════════════════════════
        # 5 · TERMINAL
        # ══════════════════════════════════════════════════════════════════════
        with ui.tab_panel("terminal").classes("p-0"):
            with ui.element("div").style("height:calc(100vh - 102px); width:100%"):
                term = ui.terminal(max_lines=20_000).classes("w-full h-full")

            _cur = {"i": 0}

            async def _drain() -> None:
                new = _out[_cur["i"]:]
                if new:
                    term.push("".join(new))
                    _cur["i"] = len(_out)

            ui.timer(0.05, _drain)

    # ── Global job-status timer ───────────────────────────────────────────────
    def _status_tick() -> None:
        global _prog_start
        running = jobs.job_running()
        job = state.get("job")
        if running and job:
            name = job.get("name", "job")[:45]
            job_badge.text = f"● {name}"
            job_badge.props("color=blue")
            prog_row.visible = True
            v, t, lbl = _prog["v"], _prog["t"], _prog["label"]
            if t > 0:
                prog_bar.value = v / t
                elapsed = _fmt(time.monotonic() - _prog_start) if _prog_start else ""
                eta = ""
                if _prog_start and v > 0 and t > v:
                    rate = v / max(time.monotonic() - _prog_start, 0.001)
                    eta = f"  ~{_fmt((t-v)/rate)} left" if rate > 0 else ""
                parts = [p for p in [lbl, f"{v}/{t} ({int(v/t*100)}%)", elapsed + eta] if p]
                prog_lbl.text = "  ·  ".join(parts)
            else:
                prog_bar.value = 0
                prog_lbl.text = (lbl or "working…") + (
                    f"  ·  {_fmt(time.monotonic()-_prog_start)}" if _prog_start else "")
        else:
            job_badge.text = "idle"
            job_badge.props("color=grey")
            if not running:
                prog_row.visible = False
                _prog_start = None
                _prog.update({"v": 0, "t": 0, "label": ""})

    ui.timer(2.0, _status_tick)


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
