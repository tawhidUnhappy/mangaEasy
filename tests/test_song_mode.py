from __future__ import annotations

import json

import pytest

from mangaeasy import command_spec
from mangaeasy.song.lyrics import (
    DEFAULT_LYRICS_STYLE,
    align_lyrics,
    lyric_lines,
    write_alignment,
)
from mangaeasy.song import workflow
from mangaeasy.song.workflow import DEFAULT_VISUAL_PROMPT, new_manifest, validate_manifest


def _transcript(words):
    return {"word_segments": [
        {"word": word, "start": index * 0.4, "end": index * 0.4 + 0.3}
        for index, word in enumerate(words)
    ]}


def _prepare_reviewed_song(root, data):
    audio = root / "audio" / "song.wav"
    vocals = root / "stems" / "vocals.wav"
    background = root / "visual" / "background.png"
    for path, content in (
        (audio, b"full song"),
        (vocals, b"isolated vocals"),
        (background, b"sky art"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / "audio" / "generation_state.json").write_text(json.dumps({
        "contract_digest": workflow._generation_contract(data),
        "audio_sha256": workflow._sha256_file(audio),
    }), encoding="utf-8")
    (root / "stems" / "separation_state.json").write_text(json.dumps({
        "audio_sha256": workflow._sha256_file(audio),
        "vocals_sha256": workflow._sha256_file(vocals),
    }), encoding="utf-8")
    (root / "visual" / "visual_state.json").write_text(json.dumps({
        "contract_digest": workflow._visual_contract(data),
        "background_sha256": workflow._sha256_file(background),
    }), encoding="utf-8")
    alignment_dir = root / "alignment"
    alignment_dir.mkdir(parents=True, exist_ok=True)
    alignment = align_lyrics(data["lyrics"], _transcript(data["lyrics"].split()))
    timed = alignment_dir / "timed_lyrics.json"
    timed.write_text(json.dumps(alignment), encoding="utf-8")
    alignment_state = alignment_dir / "alignment_state.json"
    alignment_state.write_text(json.dumps({
        "contract_digest": workflow._alignment_contract(data, workflow._sha256_file(vocals)),
    }), encoding="utf-8")
    data["alignment"].update({
        "approved": True,
        "approved_digest": workflow._alignment_artifact_digest(timed),
    })
    video = root / "output" / f"{workflow._slug(data['title'])}_lyrics.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"reviewed video")
    font = workflow._resolve_font_file(root, data["render"]["lyrics_style"]["font_file"])
    input_digest = workflow._video_input_digest(
        data, audio, background, data["alignment"]["approved_digest"], font,
    )
    state = root / "review" / "video_generation.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "video": str(video.resolve()),
        "sha256": workflow._sha256_file(video),
        "input_digest": input_digest,
    }), encoding="utf-8")
    data["review"].update({
        "video_approved": True,
        "approved_video_sha256": workflow._sha256_file(video),
    })
    return audio, vocals, background, timed, video


def test_canonical_lyrics_replace_asr_spelling_and_keep_lines(tmp_path):
    lyrics = "We rise tonight\nThe stars are bright"
    aligned = align_lyrics(lyrics, _transcript(["we", "rise", "tonite", "the", "stars", "are", "bright"]))
    assert [line["text"] for line in aligned["lines"]] == ["We rise tonight", "The stars are bright"]
    assert all(line["end"] > line["start"] for line in aligned["lines"])
    outputs = write_alignment(aligned, tmp_path)
    assert outputs["ass"].is_file() and outputs["srt"].is_file()
    assert "tonite" not in outputs["srt"].read_text(encoding="utf-8")
    ass = outputs["ass"].read_text(encoding="utf-8-sig")
    assert "Style: Lyrics,Edo SZ," in ass
    assert ",2.5,1.25,5," in ass
    assert r"{\fad(220,280)}We rise tonight" in ass


def test_repeated_chorus_alignment_is_monotonic():
    lyrics = "Fly away\nFly away\nHome today"
    aligned = align_lyrics(lyrics, _transcript("fly away fly away home today".split()))
    starts = [line["start"] for line in aligned["lines"]]
    assert starts == sorted(starts)
    assert aligned["confidence"] == 1.0


def test_structural_headings_are_not_treated_as_sung_lyrics():
    lyrics = """[Intro]
[Verse]
[Verse 2]
We rise
[Pre-Chorus]
[Chorus: Nova]
[Bridge]
[Instrumental]
[Intro]/[Outro]/[Instrumental]
[Whisper my name]
[Verse of fire]
[Outro]"""

    assert [line for line, _tokens in lyric_lines(lyrics)] == [
        "We rise",
        "[Whisper my name]",
        "[Verse of fire]",
    ]
    aligned = align_lyrics(
        lyrics,
        _transcript("we rise whisper my name verse of fire".split()),
    )
    assert [line["text"] for line in aligned["lines"]] == [
        "We rise",
        "[Whisper my name]",
        "[Verse of fire]",
    ]
    assert aligned["canonical_word_count"] == 8
    assert aligned["confidence"] == 1.0


def test_alignment_review_gate_uses_requested_minimum_confidence():
    transcript = _transcript(["we", "rize"])

    default_gate = align_lyrics("We rise", transcript)
    relaxed_gate = align_lyrics("We rise", transcript, minimum_confidence=0.5)

    assert default_gate["confidence"] == 0.5
    assert default_gate["minimum_confidence"] == 0.72
    assert default_gate["review_required"] is True
    assert relaxed_gate["minimum_confidence"] == 0.5
    assert relaxed_gate["review_required"] is False


@pytest.mark.parametrize(
    "minimum_confidence",
    [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"],
)
def test_alignment_rejects_invalid_minimum_confidence(minimum_confidence):
    with pytest.raises(ValueError, match="minimum_confidence"):
        align_lyrics(
            "We rise",
            _transcript(["we", "rise"]),
            minimum_confidence=minimum_confidence,
        )


def test_align_lyrics_command_schema_exposes_bounded_confidence():
    schema = command_spec.cli_args_schema("whisperx")
    assert schema is not None
    confidence = schema["minimum_confidence"]
    assert confidence["flag"] == "--minimum-confidence"
    assert confidence["minimum"] == 0
    assert confidence["maximum"] == 1
    assert confidence["default"] == 0.72


def test_song_manifest_defaults_and_publish_rights_gate(tmp_path):
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    assert data["visual"]["prompt"] == DEFAULT_VISUAL_PROMPT
    assert data["youtube"]["profile"] == "default"
    assert data["render"]["lyrics_style"] == DEFAULT_LYRICS_STYLE
    assert data["render"]["lyrics_style"] is not DEFAULT_LYRICS_STYLE
    assert data["render"]["lyrics_style"]["font_file"] == "@bundled/edosz.ttf"
    bundled_font = workflow._resolve_font_file(
        tmp_path, data["render"]["lyrics_style"]["font_file"]
    )
    assert bundled_font is not None and bundled_font.is_file()
    assert bundled_font.name == "edosz.ttf"
    assert data["separation"]["model"] == "htdemucs-ft"
    assert not [p for p in validate_manifest(data) if p["severity"] == "error"]
    assert any(p["path"] == "rights" for p in validate_manifest(data, for_publish=True))


def test_song_manifest_rejects_unsafe_youtube_profile():
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    data["youtube"]["profile"] = "../wrong-channel"
    assert "youtube.profile" in {problem["path"] for problem in validate_manifest(data)}


def test_song_separation_uses_pinned_demucs_without_obsolete_model_flag(
    tmp_path, monkeypatch, capsys,
):
    manifest = tmp_path / "song.json"
    data = new_manifest(
        "Sky Song", "One clear line", "ambient pop", str(tmp_path / "song.wav")
    )
    manifest.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "mediaconductor song-build", "--manifest", str(manifest),
            "--stage", "separate", "--dry-run",
        ],
    )

    assert workflow.build_main() == 0
    report = json.loads(capsys.readouterr().out)
    command = next(item for item in report["commands"] if "demucs" in item)
    assert "--model" not in command
    assert command[command.index("--device") + 1] == "auto"


def test_song_check_machine_schema_exposes_publish_rights_gate():
    schema = command_spec.cli_args_schema("song-check")
    assert schema is not None
    assert schema["for_publish"]["flag"] == "--for-publish"
    assert schema["for_publish"]["kind"] == "flag"


def test_song_manifest_rejects_unsafe_or_out_of_range_ass_style():
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    style = data["render"]["lyrics_style"]
    style["font_name"] = "Edo SZ,Arial"
    style["font_file"] = "fonts/readme.txt"
    style["shadow"] = -1
    style["fade_in_ms"] = 6000
    problems = validate_manifest(data)
    error_paths = {problem["path"] for problem in problems if problem["severity"] == "error"}
    assert "render.lyrics_style.font_name" in error_paths
    assert "render.lyrics_style.font_file" in error_paths
    assert "render.lyrics_style.shadow" in error_paths
    assert "render.lyrics_style.fade_in_ms" in error_paths


def test_song_manifest_rejects_unprovisioned_alignment_language():
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    data["language"] = "ja"
    problems = validate_manifest(data)
    assert any(
        problem["path"] == "language" and problem["severity"] == "error"
        for problem in problems
    )


def test_song_manifest_reports_invalid_sections_without_crashing():
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    data["youtube"] = []
    data["rights"] = None
    paths = {problem["path"] for problem in validate_manifest(data, for_publish=True)}
    assert "youtube" in paths
    assert "youtube.profile" in paths
    assert "rights" in paths


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        (lambda data: data["visual"].update(prompt=7), "visual.prompt"),
        (lambda data: data["render"].pop("width"), "render.width"),
        (lambda data: data["audio"].update(source=7), "audio.source"),
        (lambda data: data["audio"].update(duration=float("nan")), "audio.duration"),
        (lambda data: data["youtube"].update(tags="not-an-array"), "youtube.tags"),
        (lambda data: data.update(schema_version=True), "schema_version"),
        (lambda data: data["separation"].update(device="mps"), "separation.device"),
        (lambda data: data["separation"].update(device=[]), "separation.device"),
        (lambda data: data["alignment"].update(device={}), "alignment.device"),
        (lambda data: data["youtube"].update(privacy=[]), "youtube.privacy"),
        (
            lambda data: data["render"]["lyrics_style"].update(shadow=float("inf")),
            "render.lyrics_style.shadow",
        ),
    ],
)
def test_song_manifest_totally_validates_consumed_fields(mutation, expected_path):
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    mutation(data)

    problems = validate_manifest(data)

    assert expected_path in {
        problem["path"] for problem in problems if problem["severity"] == "error"
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["visual"].update(prompt=7),
        lambda data: data["render"].pop("width"),
        lambda data: data["audio"].update(source=7),
    ],
)
def test_song_build_reports_malformed_llm_manifest_instead_of_crashing(
    tmp_path, monkeypatch, capsys, mutation,
):
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    mutation(data)
    manifest = tmp_path / "song.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor song-build", "--manifest", str(manifest), "--stage", "all", "--dry-run"],
    )

    assert workflow.build_main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any(problem["severity"] == "error" for problem in report["problems"])


@pytest.mark.parametrize(
    ("line_change", "message"),
    [
        ({"index": None}, "index must equal 1"),
        ({"index": 2}, "index must equal 1"),
        ({"start": float("nan")}, "numeric 0 <= start < end"),
        ({"end": float("inf")}, "numeric 0 <= start < end"),
    ],
)
def test_reviewed_timing_requires_renderable_indices_and_finite_times(
    tmp_path, line_change, message,
):
    aligned = align_lyrics("One clear line", _transcript(["one", "clear", "line"]))
    aligned["lines"][0].update(line_change)
    timed = tmp_path / "timed_lyrics.json"
    timed.write_text(json.dumps(aligned), encoding="utf-8")

    _value, problems = workflow._alignment_data(timed, "One clear line")

    assert any(message in problem for problem in problems)


def test_song_render_passes_configured_font_directory_to_libass(tmp_path, monkeypatch):
    background = tmp_path / "background.png"
    audio = tmp_path / "song.wav"
    subtitles = tmp_path / "lyrics.ass"
    style = new_manifest("Sky Song", "One clear line", "ambient pop", None)["render"]["lyrics_style"]
    font = workflow._resolve_font_file(tmp_path, style["font_file"])
    assert font is not None
    output = tmp_path / "lyrics.mp4"
    for path in (background, audio, subtitles):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    commands = []
    monkeypatch.setattr(workflow, "choose_h264_encoder", lambda _requested: "libx264")
    monkeypatch.setattr(workflow, "h264_encoder_args", lambda *_args: ["-c:v", "libx264"])
    monkeypatch.setattr(workflow.subprocess, "run", lambda command, **_kwargs: commands.append(command))

    workflow.render_video(background, audio, subtitles, output, 1920, 1080, 30, False, font)

    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "ass=filename=" in video_filter
    assert ":fontsdir=" in video_filter
    assert "assets/fonts" in video_filter.replace("\\", "/")


def test_song_publish_dry_run_routes_selected_youtube_profile(
    tmp_path, monkeypatch, capsys,
):
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    for key in (
        "lyrics_rights_confirmed", "audio_rights_confirmed", "voice_consent_confirmed",
        "synthetic_media_disclosure_acknowledged",
    ):
        data["rights"][key] = True
    data["youtube"]["profile"] = "song"
    _prepare_reviewed_song(tmp_path, data)
    manifest = tmp_path / "song.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor song-build", "--manifest", str(manifest),
         "--stage", "publish", "--dry-run"],
    )

    assert workflow.build_main() == 0
    report = json.loads(capsys.readouterr().out)
    command = report["commands"][0]
    assert command[command.index("--profile") + 1] == "song"


def test_song_alignment_resume_is_invalidated_by_canonical_lyric_edit(
    tmp_path, monkeypatch, capsys,
):
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    _audio, vocals, _background, timed, _video = _prepare_reviewed_song(tmp_path, data)
    old_contract = workflow._alignment_contract(data, workflow._sha256_file(vocals))
    data["lyrics"] = "One corrected line"
    assert workflow._alignment_contract(data, workflow._sha256_file(vocals)) != old_contract
    manifest = tmp_path / "song.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert timed.is_file()
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor song-build", "--manifest", str(manifest), "--stage", "align", "--dry-run"],
    )

    assert workflow.build_main() == 0
    report = json.loads(capsys.readouterr().out)
    assert any("whisperx" in command for command in report["commands"])


def test_song_render_rebuilds_ass_from_reviewed_timed_json(
    tmp_path, monkeypatch, capsys,
):
    data = new_manifest("Sky Song", "One clear line", "ambient pop", None)
    _audio, _vocals, _background, timed, video = _prepare_reviewed_song(tmp_path, data)
    video.unlink()
    alignment = json.loads(timed.read_text(encoding="utf-8"))
    alignment["lines"][0].update({"start": 4.0, "end": 6.0})
    timed.write_text(json.dumps(alignment), encoding="utf-8")
    data["alignment"]["approved_digest"] = workflow._alignment_artifact_digest(timed)
    manifest = tmp_path / "song.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    def fake_render(_background, _audio, _subtitles, output, *_args):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rerendered video")

    monkeypatch.setattr(workflow, "render_video", fake_render)
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor song-build", "--manifest", str(manifest), "--stage", "render"],
    )

    assert workflow.build_main() == 3
    capsys.readouterr()
    ass = (tmp_path / "alignment" / "lyrics.ass").read_text(encoding="utf-8-sig")
    assert "0:00:04.00,0:00:06.00" in ass
