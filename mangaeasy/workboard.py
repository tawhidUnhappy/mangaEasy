"""mangaeasy.workboard — multi-agent coordination for one project.

Three commands let several agents (or one agent across several sessions)
produce the same video without stepping on each other, and resume instantly
after any interruption. All state lives in ``library/<project>/.workboard/``
so it travels with the project — including over a network share — and the
dot-prefix keeps it invisible to item scanning:

- ``mangaeasy work-status`` — the resume command. Derives every item's
  pipeline stage (download → crop → transcribe → narrate → audio → render)
  from the **filesystem as ground truth**, so it is always accurate even if
  a previous agent died mid-run and left no record. ``--next`` emits a
  prioritized list of unclaimed, actionable tasks; ``--json`` for machines.
- ``mangaeasy work-claim`` — atomic TTL-leased claims on an ``(item, stage)``
  pair or a named ``--resource`` (e.g. ``gpu``: MAGI/DeepSeek/IndexTTS/
  Z-Image cannot share a consumer card). Acquire is an O_CREAT|O_EXCL file
  create — safe across processes and network filesystems. Leases expire, so
  a crashed agent never wedges the board; a live agent must ``--renew``.
- ``mangaeasy work-note`` — append-only shared notebook (``notes.jsonl``)
  for the facts that otherwise die with an agent's context window:
  character names and speaker conventions, tone decisions, per-chapter
  warnings. Topic-tagged; ``work-status`` surfaces the latest entries so
  every fresh agent discovers the notebook exists.

Concurrency model: claims are advisory (commands do not enforce them — an
agent that skips claiming can still collide), best-effort atomic, and leased.
That is deliberate: the filesystem stays the single source of truth for
*work done*, and the workboard is only the coordination layer for *work in
progress*. Exit codes follow the CLI contract: 0 = ok/acquired, 1 = claim
held by someone else (or runtime failure), 2 = usage error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mangaeasy.video_pipeline.check_items import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, files_by_stem
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    item_dirs,
    item_number,
    merge_item_selection,
    project_name,
)
from mangaeasy.video_pipeline.item_assets import load_narration

# Per-item pipeline stages, in production order. `join`/`thumbnail`/`upload`
# are project-level and appear only as claimable stage names + next-task
# suggestions, never as per-item state.
ITEM_STAGES = ("download", "crop", "transcribe", "narrate", "audio", "render")
PROJECT_STAGES = ("join", "thumbnail", "upload")
CLAIMABLE_STAGES = frozenset(ITEM_STAGES) | frozenset(PROJECT_STAGES)

# Stages that load a heavy model onto the GPU. Agents should hold the shared
# `gpu` resource claim while running these (see docs/multi-agent.md); `render`
# uses NVENC/Vulkan but no model, so it coexists with narration writing.
GPU_STAGES = frozenset({"crop", "transcribe", "audio"})

DEFAULT_TTL_MINUTES = 60


# ── shared plumbing ──────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def workboard_dir(project_root: Path) -> Path:
    return project_root / ".workboard"


def claims_dir(project_root: Path) -> Path:
    return workboard_dir(project_root) / "claims"


def notes_path(project_root: Path) -> Path:
    return workboard_dir(project_root) / "notes.jsonl"


def default_agent() -> str:
    env = os.environ.get("MANGAEASY_AGENT")
    if env:
        return env
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:  # noqa: BLE001 — identity is cosmetic, never fatal
        return "unknown-agent"


# ── work-status: filesystem-derived stage model ──────────────────────────────

def _count_images(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)


def _transcript_progress(item_dir: Path) -> tuple[int, int]:
    """(filled, total) OCR entries; (0, 0) when transcript.json is absent."""
    path = item_dir / "transcript.json"
    if not path.is_file():
        return 0, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 — a corrupt transcript reads as "redo OCR"
        return 0, 0
    if not isinstance(data, list):
        return 0, 0
    filled = sum(1 for e in data if isinstance(e, dict) and e.get("ocr"))
    return filled, len(data)


def _narration_entries(item_dir: Path) -> list[dict]:
    if not (item_dir / "narration.json").is_file():
        return []
    try:
        return load_narration(item_dir)
    except Exception:  # noqa: BLE001 — invalid narration reads as "not narrated"
        return []


def _rendered_video(output_root: Path, name: str, item_dir: Path) -> Path | None:
    items_dir = output_root / name / "items"
    exact = items_dir / f"item_{item_dir.name}.mp4"
    if exact.is_file():
        return exact
    try:
        numbered = items_dir / f"item_{item_number(item_dir.name):02d}.mp4"
    except ValueError:
        return None
    return numbered if numbered.is_file() else None


def item_status(item_dir: Path, name: str, audio_root: Path, output_root: Path) -> dict:
    """One item's pipeline state, derived purely from files on disk."""
    downloads = _count_images(item_dir / "download")
    panels = _count_images(item_dir / "panels")
    ocr_filled, ocr_total = _transcript_progress(item_dir)
    narration = _narration_entries(item_dir)
    stems = [Path(e.get("image", "")).stem for e in narration if isinstance(e, dict) and e.get("image")]
    audio_stems = files_by_stem(audio_root / name / item_dir.name, AUDIO_EXTENSIONS)
    audio_have = sum(1 for s in stems if s in audio_stems)

    video = _rendered_video(output_root, name, item_dir)
    narration_path = item_dir / "narration.json"
    render_stale = bool(
        video is not None
        and narration_path.is_file()
        and video.stat().st_mtime < narration_path.stat().st_mtime
    )

    if downloads == 0:
        next_stage = "download"
    elif panels == 0:
        next_stage = "crop"
    elif ocr_total == 0 or ocr_filled < ocr_total:
        next_stage = "transcribe"
    elif not narration:
        next_stage = "narrate"
    elif audio_have < len(stems):
        next_stage = "audio"
    elif video is None or render_stale:
        next_stage = "render"
    else:
        next_stage = None

    return {
        "item": item_dir.name,
        "download": downloads,
        "panels": panels,
        "transcript": {"filled": ocr_filled, "total": ocr_total},
        "narration_entries": len(narration),
        "audio": {"have": audio_have, "need": len(stems)},
        "rendered": video is not None,
        "render_stale": render_stale,
        "next_stage": next_stage,
    }


def _read_claim(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 — unreadable claim = treat as absent
        return None


def _claim_expired(claim: dict, now: datetime | None = None) -> bool:
    now = now or _utcnow()
    try:
        return datetime.fromisoformat(claim["expires_at"]) <= now
    except Exception:  # noqa: BLE001 — malformed expiry = expired (never wedge)
        return True


def active_claims(project_root: Path) -> list[dict]:
    folder = claims_dir(project_root)
    if not folder.is_dir():
        return []
    claims = []
    for path in sorted(folder.glob("*.json")):
        claim = _read_claim(path)
        if claim is not None:
            claim["expired"] = _claim_expired(claim)
            claims.append(claim)
    return claims


def _recent_notes(project_root: Path, limit: int) -> list[dict]:
    path = notes_path(project_root)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    notes = []
    for line in lines:
        try:
            notes.append(json.loads(line))
        except Exception:  # noqa: BLE001 — skip torn/corrupt lines
            continue
    return notes[-limit:] if limit else notes


def next_tasks(statuses: list[dict], claims: list[dict]) -> list[dict]:
    """Unclaimed, actionable (item, stage) pairs — what a free agent should grab."""
    held = {
        (c.get("item"), c.get("stage"))
        for c in claims
        if c.get("kind") == "item" and not c["expired"]
    }
    tasks = [
        {"item": s["item"], "stage": s["next_stage"], "gpu": s["next_stage"] in GPU_STAGES,
         "reason": "stale render — inputs changed" if (s["next_stage"] == "render" and s["render_stale"]) else None}
        for s in statuses
        if s["next_stage"] and (s["item"], s["next_stage"]) not in held
    ]
    if statuses and all(s["next_stage"] is None for s in statuses):
        project_held = {c.get("stage") for c in claims if c.get("kind") == "item" and not c["expired"]}
        if "join" not in project_held:
            tasks.append({"item": None, "stage": "join", "gpu": False,
                          "reason": "every item rendered and fresh — build the long video"})
    return tasks


def status_main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy work-status",
        description="Multi-agent dashboard: per-item pipeline stage derived from the filesystem, "
                    "active claims, recent shared notes, and (--next) unclaimed actionable tasks. "
                    "Run this first in every session — it is the resume command.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08 (default: all).")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-22.")
    parser.add_argument("--next", action="store_true", dest="only_next",
                        help="Print only the unclaimed actionable tasks (what to grab next).")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = args.project_root
    if not root.is_dir():
        print(f"[ERROR] project root not found: {root}", file=sys.stderr)
        return 1
    name = project_name(root, args.project_name)
    selection = merge_item_selection(args.items, args.item_range)
    statuses = [item_status(d, name, args.audio_root, args.output_root)
                for d in item_dirs(root, selection)]
    claims = active_claims(root)
    tasks = next_tasks(statuses, claims)
    report = {
        "project": name,
        "items": statuses,
        "claims": claims,
        "next_tasks": tasks,
        "recent_notes": _recent_notes(root, limit=3),
    }

    if args.as_json:
        print(json.dumps({"next_tasks": tasks} if args.only_next else report, ensure_ascii=False))
        return 0

    if args.only_next:
        if not tasks:
            print("Nothing actionable — everything is done or claimed.")
        for t in tasks:
            where = t["item"] or "(project)"
            gpu = "  [GPU]" if t["gpu"] else ""
            reason = f"  ({t['reason']})" if t.get("reason") else ""
            print(f"{where}: {t['stage']}{gpu}{reason}")
        return 0

    print(f"Project {name} — {len(statuses)} item(s)")
    for s in statuses:
        stage = s["next_stage"] or "done"
        extra = " (stale render)" if s["render_stale"] else ""
        print(f"  {s['item']}: next={stage}{extra}  "
              f"panels={s['panels']} ocr={s['transcript']['filled']}/{s['transcript']['total']} "
              f"narr={s['narration_entries']} audio={s['audio']['have']}/{s['audio']['need']} "
              f"rendered={'yes' if s['rendered'] else 'no'}")
    live = [c for c in claims if not c["expired"]]
    if live:
        print("Active claims:")
        for c in live:
            print(f"  {_claim_label(c)} — {c['agent']} until {c['expires_at']}")
    for n in report["recent_notes"]:
        print(f"note [{n.get('topic', 'general')}] {n.get('agent', '?')}: {n.get('note', '')}")
    return 0


# ── work-claim: atomic TTL leases ────────────────────────────────────────────

def _claim_file(project_root: Path, *, item: str | None, stage: str | None,
                resource: str | None) -> Path:
    if resource:
        key = f"resource-{resource}"
    elif item:
        key = f"item-{item}--{stage}"
    else:
        key = f"project--{stage}"  # project-level stage: join / thumbnail / upload
    return claims_dir(project_root) / f"{key}.json"


def _claim_label(claim: dict) -> str:
    if claim.get("resource"):
        return claim["resource"]
    return f"{claim.get('item') or '(project)'}:{claim.get('stage')}"


def _write_atomic_new(path: Path, payload: dict) -> bool:
    """Create *path* with this payload only if it does not exist (O_EXCL)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return True


def acquire_claim(project_root: Path, *, agent: str, ttl_minutes: int,
                  item: str | None = None, stage: str | None = None,
                  resource: str | None = None, note: str | None = None) -> tuple[bool, dict]:
    """Try to take the lease. Returns (acquired, claim) — on failure, *claim*
    is the live holder's record so the caller can report who has it."""
    path = _claim_file(project_root, item=item, stage=stage, resource=resource)
    now = _utcnow()
    payload = {
        "kind": "resource" if resource else "item",
        "item": item,
        "stage": stage,
        "resource": resource,
        "agent": agent,
        "note": note,
        "acquired_at": _iso(now),
        "ttl_minutes": ttl_minutes,
        "expires_at": _iso(now + timedelta(minutes=ttl_minutes)),
    }
    if _write_atomic_new(path, payload):
        return True, payload
    current = _read_claim(path)
    if current is not None and not _claim_expired(current, now):
        return False, current
    # Expired (or unreadable) lease: take it over. remove + O_EXCL re-create is
    # a small race window; the loser of the race gets a clean failure.
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False, current or payload
    payload["took_over_from"] = (current or {}).get("agent")
    if _write_atomic_new(path, payload):
        return True, payload
    return False, _read_claim(path) or payload


def release_claim(project_root: Path, *, agent: str, force: bool,
                  item: str | None = None, stage: str | None = None,
                  resource: str | None = None) -> tuple[bool, str]:
    path = _claim_file(project_root, item=item, stage=stage, resource=resource)
    current = _read_claim(path)
    if current is None:
        return True, "no claim to release"
    if current.get("agent") != agent and not force:
        return False, f"held by {current.get('agent')} — pass --force to override"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return True, "released"


def claim_main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy work-claim",
        description="Atomically claim an (item, stage) pair or a shared --resource (e.g. gpu) "
                    "with a TTL lease so concurrent agents never do the same work twice. "
                    "Exit 0 = acquired/released, 1 = held by another live agent.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--item", help="Item folder name, e.g. 05.")
    parser.add_argument("--stage", choices=sorted(CLAIMABLE_STAGES),
                        help="Pipeline stage being claimed for --item.")
    parser.add_argument("--resource", help="Named shared resource instead of an item+stage (e.g. gpu).")
    parser.add_argument("--agent", default=None,
                        help="Claim owner (default: $MANGAEASY_AGENT or user@host).")
    parser.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES,
                        help=f"Lease length (default {DEFAULT_TTL_MINUTES}); expired leases can be taken over.")
    parser.add_argument("--note", default=None, help="Free-text context stored on the claim.")
    parser.add_argument("--release", action="store_true", help="Release instead of acquire.")
    parser.add_argument("--renew", action="store_true", help="Extend an existing own claim by --ttl-minutes.")
    parser.add_argument("--force", action="store_true", help="With --release: release someone else's claim.")
    parser.add_argument("--list", action="store_true", dest="list_claims", help="List all claims and exit.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = args.project_root
    agent = args.agent or default_agent()

    if args.list_claims:
        claims = active_claims(root)
        if args.as_json:
            print(json.dumps({"claims": claims}, ensure_ascii=False))
        elif not claims:
            print("No claims.")
        else:
            for c in claims:
                state = "EXPIRED" if c["expired"] else f"until {c['expires_at']}"
                print(f"{_claim_label(c)} — {c['agent']} ({state})")
        return 0

    if bool(args.resource) == bool(args.item or args.stage):
        parser.error("claim either --item + --stage, or --resource")
    if args.item and not args.stage:
        parser.error("--item requires --stage")
    if not root.is_dir():
        print(f"[ERROR] project root not found: {root}", file=sys.stderr)
        return 1

    kwargs = {"item": args.item, "stage": args.stage, "resource": args.resource}

    if args.release:
        ok, message = release_claim(root, agent=agent, force=args.force, **kwargs)
        if args.as_json:
            print(json.dumps({"released": ok, "message": message}, ensure_ascii=False))
        else:
            print(message)
        return 0 if ok else 1

    if args.renew:
        path = _claim_file(root, **kwargs)
        current = _read_claim(path)
        if current is None or current.get("agent") != agent:
            holder = (current or {}).get("agent", "nobody")
            print(json.dumps({"renewed": False, "holder": holder}) if args.as_json
                  else f"cannot renew — held by {holder}")
            return 1
        current["expires_at"] = _iso(_utcnow() + timedelta(minutes=args.ttl_minutes))
        current["renewed_at"] = _iso(_utcnow())
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"renewed": True, "claim": current}, ensure_ascii=False) if args.as_json
              else f"renewed until {current['expires_at']}")
        return 0

    acquired, claim = acquire_claim(root, agent=agent, ttl_minutes=args.ttl_minutes,
                                    note=args.note, **kwargs)
    if args.as_json:
        print(json.dumps({"acquired": acquired, "claim": claim}, ensure_ascii=False))
    elif acquired:
        what = _claim_label(claim)
        took = f" (took over expired lease from {claim['took_over_from']})" if claim.get("took_over_from") else ""
        print(f"claimed {what} until {claim['expires_at']}{took}")
    else:
        what = _claim_label(claim)
        print(f"BUSY: {what} held by {claim.get('agent')} until {claim.get('expires_at')}")
    return 0 if acquired else 1


def respect_claims_gate(project_root: Path, items: list[str] | None, item_range: str | None,
                        stages: tuple[str, ...], agent: str | None = None) -> bool:
    """Opt-in enforcement for heavy commands (``--respect-claims``).

    True = clear to proceed. False = another live agent holds a claim on one
    of the selected (item, stage) pairs; the holder is printed so the caller
    can abort with exit 1. Claims stay advisory by default — this gate only
    runs when a command passes ``--respect-claims`` — so single-agent flows
    never pay for coordination they don't use.
    """
    agent = agent or default_agent()
    try:
        names = {d.name for d in item_dirs(project_root, merge_item_selection(items, item_range))}
    except OSError:
        return True  # unreadable project root fails later with its own error
    conflicts = [
        c for c in active_claims(project_root)
        if not c["expired"] and c.get("kind") == "item" and c.get("agent") != agent
        and c.get("stage") in stages and c.get("item") in names
    ]
    for c in conflicts:
        print(f"[respect-claims] BUSY: {c['item']}:{c['stage']} is held by {c['agent']} "
              f"until {c['expires_at']} — pick another task (see work-status --next).", flush=True)
    return not conflicts


# ── work-note: shared append-only notebook ───────────────────────────────────

def add_note(project_root: Path, *, agent: str, topic: str, text: str) -> dict:
    entry = {"ts": _iso(_utcnow()), "agent": agent, "topic": topic, "note": text}
    path = notes_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A single small O_APPEND write per note keeps concurrent appends whole.
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return entry


def note_main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy work-note",
        description="Shared append-only project notebook for agent handoff: character names and "
                    "speaker conventions, tone decisions, warnings. Add with --add; read with --list.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--add", default=None, metavar="TEXT", help="Append one note.")
    parser.add_argument("--topic", default=None,
                        help="Note topic, e.g. characters / speakers / tone / decisions / warnings "
                             "(default 'general' when adding; with --list, filters to the topic).")
    parser.add_argument("--agent", default=None, help="Author (default: $MANGAEASY_AGENT or user@host).")
    parser.add_argument("--list", action="store_true", dest="list_notes", help="Print notes (default when no --add).")
    parser.add_argument("--limit", type=int, default=0, help="With --list: only the last N notes.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = args.project_root
    if not root.is_dir():
        print(f"[ERROR] project root not found: {root}", file=sys.stderr)
        return 1

    if args.add:
        entry = add_note(root, agent=args.agent or default_agent(), topic=args.topic or "general", text=args.add)
        print(json.dumps({"added": entry}, ensure_ascii=False) if args.as_json
              else f"noted [{entry['topic']}]")
        return 0

    notes = _recent_notes(root, limit=args.limit)
    if args.topic:
        notes = [n for n in notes if n.get("topic") == args.topic]
    if args.as_json:
        print(json.dumps({"notes": notes}, ensure_ascii=False))
    elif not notes:
        print("No notes yet.")
    else:
        for n in notes:
            print(f"[{n.get('topic', 'general')}] {n.get('agent', '?')} {n.get('ts', '')}: {n.get('note', '')}")
    return 0
