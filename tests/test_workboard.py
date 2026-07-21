"""Multi-agent workboard: filesystem-derived stages, TTL claims, shared notes,
and the work-qa fix-until-clean loop small models drive."""

import json
from datetime import timedelta

from mediaconductor.audio.emotion import emotion_lint, indextts_kwargs, narration_emotion
from mediaconductor.qa_loop import qa_item
from mediaconductor.workboard import (
    _iso,
    _utcnow,
    acquire_claim,
    active_claims,
    add_note,
    add_todo,
    item_status,
    list_todos,
    next_tasks,
    release_claim,
    remove_todo,
    set_todo_status,
)

PNG = b"\x89PNG\r\n\x1a\n"  # suffix check only — content is never decoded


def make_item(root, name="01", *, panels=2, ocr=True, narration=True):
    item = root / name
    (item / "download").mkdir(parents=True)
    (item / "download" / "p1.jpg").write_bytes(PNG)
    if panels:
        (item / "panels").mkdir()
        for i in range(panels):
            (item / "panels" / f"{name}_00{i}_01.png").write_bytes(PNG)
    if ocr:
        entries = [{"image": f"{name}_00{i}_01.png", "ocr": "SOME TEXT"} for i in range(panels)]
        (item / "transcript.json").write_text(json.dumps(entries), encoding="utf-8")
    if narration:
        entries = [{"image": f"{name}_00{i}_01.png", "narration": f"Line {i}."} for i in range(panels)]
        (item / "narration.json").write_text(json.dumps(entries), encoding="utf-8")
    return item


def add_audio(tmp_path, project, item, stems):
    audio_dir = tmp_path / "audio" / project / item
    audio_dir.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (audio_dir / f"{stem}.wav").write_bytes(b"\x00" * 4096)
    return audio_dir


def test_stage_derivation_walks_the_pipeline(tmp_path):
    root = tmp_path / "proj"
    item = make_item(root, panels=0, ocr=False, narration=False)
    args = ("proj", tmp_path / "audio", tmp_path / "out")
    assert item_status(item, *args)["next_stage"] == "crop"

    (item / "panels").mkdir()
    (item / "panels" / "01_000_01.png").write_bytes(PNG)
    # OCR is optional: with panels cropped and no transcript started, the next
    # stage is narration (a vision agent reads the bubbles from the panels).
    assert item_status(item, *args)["next_stage"] == "narrate"

    # But a transcript that was seeded/started and left unfinished is an
    # interrupted panel-transcript run — surface it so it gets completed.
    (item / "transcript.json").write_text(json.dumps([{"image": "01_000_01.png"}]), encoding="utf-8")
    assert item_status(item, *args)["next_stage"] == "transcribe"

    (item / "transcript.json").write_text(json.dumps([{"image": "01_000_01.png", "ocr": "HI"}]), encoding="utf-8")
    assert item_status(item, *args)["next_stage"] == "narrate"

    (item / "narration.json").write_text(json.dumps([{"image": "01_000_01.png", "narration": "Hi."}]), encoding="utf-8")
    assert item_status(item, *args)["next_stage"] == "audio"

    add_audio(tmp_path, "proj", "01", ["01_000_01"])
    assert item_status(item, *args)["next_stage"] == "render"

    video = tmp_path / "out" / "proj" / "items" / "item_01.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    status = item_status(item, *args)
    assert status["next_stage"] is None and not status["render_stale"]


def test_textless_ocr_entry_counts_as_processed(tmp_path):
    root = tmp_path / "proj"
    item = make_item(root, panels=1, ocr=False, narration=False)
    args = ("proj", tmp_path / "audio", tmp_path / "out")

    # OCR cleanup deliberately stores an empty string for art-only panels.
    # The key's presence distinguishes this from an unprocessed seed entry.
    (item / "transcript.json").write_text(
        json.dumps([{"image": "01_000_01.png", "ocr": ""}]),
        encoding="utf-8",
    )
    status = item_status(item, *args)
    assert status["transcript"] == {"filled": 1, "total": 1}
    assert status["next_stage"] == "narrate"


def test_stale_render_detected_after_narration_edit(tmp_path):
    import os

    root = tmp_path / "proj"
    item = make_item(root)
    add_audio(tmp_path, "proj", "01", ["01_000_01", "01_001_01"])
    video = tmp_path / "out" / "proj" / "items" / "item_01.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    os.utime(video, (1_000_000, 1_000_000))  # render far older than narration
    status = item_status(item, "proj", tmp_path / "audio", tmp_path / "out")
    assert status["render_stale"] and status["next_stage"] == "render"


def test_claim_conflict_expiry_and_takeover(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    ok, claim = acquire_claim(root, agent="alice", ttl_minutes=60, item="05", stage="narrate")
    assert ok and claim["agent"] == "alice"

    ok, holder = acquire_claim(root, agent="bob", ttl_minutes=60, item="05", stage="narrate")
    assert not ok and holder["agent"] == "alice"

    # different stage on the same item is a separate lease
    ok, _ = acquire_claim(root, agent="bob", ttl_minutes=60, item="05", stage="audio")
    assert ok

    # expire alice's lease -> bob takes over
    path = root / ".workboard" / "claims" / "item-05--narrate.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expires_at"] = _iso(_utcnow() - timedelta(minutes=1))
    path.write_text(json.dumps(data), encoding="utf-8")
    ok, claim = acquire_claim(root, agent="bob", ttl_minutes=60, item="05", stage="narrate")
    assert ok and claim["took_over_from"] == "alice"

    ok, msg = release_claim(root, agent="alice", force=False, item="05", stage="narrate")
    assert not ok and "bob" in msg
    ok, _ = release_claim(root, agent="bob", force=False, item="05", stage="narrate")
    assert ok
    assert all(c["stage"] != "narrate" for c in active_claims(root))


def test_notes_roundtrip_and_next_tasks(tmp_path):
    root = tmp_path / "proj"
    make_item(root)
    add_note(root, agent="a1", topic="characters", text="Chrome = MC, time mage")
    add_note(root, agent="a2", topic="speakers", text="Labyris calls Chrome onii-chan")
    notes = (root / ".workboard" / "notes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(notes) == 2 and json.loads(notes[0])["topic"] == "characters"

    statuses = [item_status(root / "01", "proj", tmp_path / "audio", tmp_path / "out")]
    tasks = next_tasks(statuses, active_claims(root))
    assert tasks == [{"item": "01", "stage": "audio", "gpu": True, "reason": None}]

    # claimed tasks disappear from the suggestion list
    acquire_claim(root, agent="x", ttl_minutes=60, item="01", stage="audio")
    assert next_tasks(statuses, active_claims(root)) == []


def test_qa_reports_problems_with_fix_commands(tmp_path):
    root = tmp_path / "proj"
    item = make_item(root)
    # inject an unspeakable line + a bad emotion field + a dangling image
    entries = [
        {"image": "01_000_01.png", "narration": "?!"},
        {"image": "01_001_01.png", "narration": "A real line.", "emotion": "   "},
        {"image": "missing.png", "narration": "Ghost panel."},
    ]
    (item / "narration.json").write_text(json.dumps(entries), encoding="utf-8")
    add_audio(tmp_path, "proj", "01", ["01_000_01"])
    (tmp_path / "audio" / "proj" / "01" / "01_001_01.wav").write_bytes(b"tiny")  # corrupt

    problems = qa_item(item, "proj", root, tmp_path / "audio", tmp_path / "out", tmp_path / "work")
    kinds = {p["kind"] for p in problems}
    assert {"narration:structure", "narration:unspeakable", "narration:emotion",
            "audio:missing", "audio:corrupt"} <= kinds
    assert all(p["fix"] for p in problems)


def test_qa_blocks_loud_narration_delivery(tmp_path):
    root = tmp_path / "proj"
    item = make_item(root, panels=1)
    (item / "narration.json").write_text(json.dumps([
        {"image": "01_000_01.png", "narration": "GHAHA! The phoenix is here.", "emotion": "excited"},
    ]), encoding="utf-8")
    add_audio(tmp_path, "proj", "01", ["01_000_01"])

    problems = qa_item(item, "proj", root, tmp_path / "audio", tmp_path / "out", tmp_path / "work")
    errors = {(problem["kind"], problem["severity"]) for problem in problems}
    assert ("narration:emotion", "error") in errors
    assert ("narration:delivery", "error") in errors


def test_qa_clean_item_has_no_errors(tmp_path):
    root = tmp_path / "proj"
    item = make_item(root)
    add_audio(tmp_path, "proj", "01", ["01_000_01", "01_001_01"])
    video = tmp_path / "out" / "proj" / "items" / "item_01.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    problems = qa_item(item, "proj", root, tmp_path / "audio", tmp_path / "out", tmp_path / "work")
    assert [p for p in problems if p["severity"] == "error"] == []


def test_emotion_field_contract():
    assert narration_emotion({"emotion": " slightly sad "}) == "slightly sad"
    assert narration_emotion({"narration": "no field"}) is None
    assert narration_emotion({"emotion": 42}) is None
    assert narration_emotion({"emotion": "x" * 61}) is None

    assert emotion_lint({"image": "a", "narration": "b"}) is None
    assert emotion_lint({"emotion": ""}) is not None
    assert emotion_lint({"emotion": "x" * 61}) is not None

    assert indextts_kwargs(None) == {}
    assert indextts_kwargs("cold, menacing", 0.5) == {}
    kwargs = indextts_kwargs("slightly happy", 0.5)
    assert kwargs == {"emo_text": "slightly happy", "use_emo_text": True, "emo_alpha": 0.5}


def test_todo_lifecycle_add_start_done_reopen(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    entry = add_todo(root, agent="alice", text="Redo ch07 thumbnail text", topic="publishing")
    assert entry == {
        "id": 1, "text": "Redo ch07 thumbnail text", "topic": "publishing",
        "status": "pending", "created_by": "alice", "created_at": entry["created_at"],
        "updated_at": entry["created_at"], "updated_by": "alice",
    }

    second = add_todo(root, agent="bob", text="Confirm speaker names ch08")
    assert second["id"] == 2 and second["topic"] == "general"

    todos = list_todos(root)
    assert [t["id"] for t in todos] == [1, 2]

    ok, updated = set_todo_status(root, agent="bob", todo_id=1, op="start")
    assert ok and updated["status"] == "in_progress" and updated["updated_by"] == "bob"

    ok, updated = set_todo_status(root, agent="bob", todo_id=1, op="done")
    assert ok and updated["status"] == "done"
    assert list_todos(root, pending_only=True) == [
        t for t in list_todos(root) if t["id"] == 2
    ]

    ok, updated = set_todo_status(root, agent="alice", todo_id=1, op="reopen")
    assert ok and updated["status"] == "pending"

    ok, _ = set_todo_status(root, agent="alice", todo_id=999, op="start")
    assert not ok

    assert remove_todo(root, agent="alice", todo_id=1)
    assert [t["id"] for t in list_todos(root)] == [2]
    assert not remove_todo(root, agent="alice", todo_id=1)  # already gone


def test_todo_ids_are_never_reused_after_removal(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    first = add_todo(root, agent="alice", text="one")
    remove_todo(root, agent="alice", todo_id=first["id"])
    again = add_todo(root, agent="alice", text="two")
    assert again["id"] == first["id"] + 1
    assert [t["text"] for t in list_todos(root)] == ["two"]


def test_respect_claims_gate_blocks_only_live_foreign_claims(tmp_path):
    from mediaconductor.workboard import respect_claims_gate

    root = tmp_path / "proj"
    make_item(root, "05")
    # own claim never blocks
    acquire_claim(root, agent="me", ttl_minutes=60, item="05", stage="crop")
    assert respect_claims_gate(root, ["05"], None, ("crop",), agent="me")
    # a live foreign claim blocks
    assert not respect_claims_gate(root, ["05"], None, ("crop",), agent="other")
    # different stage does not block
    assert respect_claims_gate(root, ["05"], None, ("audio",), agent="other")
    # unselected item does not block
    make_item(root, "06")
    assert respect_claims_gate(root, ["06"], None, ("crop",), agent="other")
