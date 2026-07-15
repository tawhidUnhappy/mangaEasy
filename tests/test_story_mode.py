from __future__ import annotations

import json

from mangaeasy.story import workflow
from mangaeasy.story.workflow import (
    expand_scene_prompt,
    materialize,
    new_manifest,
    validate_manifest,
    video_preflight_problems,
)
from mangaeasy.story.workflow import reference_contract_digest, scene_contract_digest


def complete_story():
    data = new_manifest("The Lantern", "Ari crosses the old bridge with a lantern.")
    data["continuity"]["characters"] = [{
        "id": "ari", "name": "Ari",
        "apparent_age": "23",
        "appearance": "young adult, oval brown face, amber eyes, short coiled black hair",
        "wardrobe": "mustard raincoat, navy trousers, brown boots",
        "wardrobe_variants": {},
        "signature_features": ["amber eyes", "short coiled black hair"],
        "never_change": ["oval face shape", "warm brown skin tone"],
    }]
    data["continuity"]["locations"] = [{
        "id": "bridge", "name": "Old bridge",
        "visual_anchor": "mossy stone arch over a narrow river at blue hour",
        "fixed_elements": ["one mossy stone arch", "iron lantern post at east entrance"],
        "palette": "slate blue, moss green, warm amber lantern accent",
        "lighting": "soft blue-hour ambient light with one warm lantern source",
    }]
    data["scenes"] = [{
        "id": "crossing", "characters": ["ari"], "location": "bridge",
        "render_mode": "standard",
        "transition": {"kind": "hard-cut"},
        "image_prompt": "Ari raises the lantern while stepping through light rain, wide shot.",
        "narration": "Ari crossed the old bridge before night erased the path.",
        "continuity_state": {
            "previous_scene_id": None,
            "time_of_day": "blue hour",
            "weather": "light rain",
            "environment_state": ["river flowing normally", "east lantern post unlit"],
            "changes_from_previous": ["opening state"],
            "character_state": {
                "ari": {
                    "wardrobe_id": "default",
                    "position": "east end, moving west",
                    "condition": "uninjured, raincoat lightly wet",
                    "emotion": "alert",
                    "held_items": ["lit brass lantern"],
                },
            },
        },
    }]
    return data


def write_reference_state(data, root):
    outputs = []
    for name in ("character_ari.png", "location_bridge.png"):
        path = root / "review" / "references" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("reference:" + name).encode())
        outputs.append(path)
    artifact_digest = workflow._artifact_digest(outputs)
    state = root / "review" / "reference_generation.json"
    state.write_text(json.dumps({
        "reference_digest": reference_contract_digest(data),
        "artifact_digest": artifact_digest,
    }), encoding="utf-8")
    data["review"].update({
        "references_approved": True,
        "approved_reference_digest": reference_contract_digest(data),
        "approved_reference_artifact_digest": artifact_digest,
    })
    return artifact_digest


def write_scene_state(data, root):
    panel = root / "content" / "01" / "panels" / "scene_001.png"
    panel.parent.mkdir(parents=True, exist_ok=True)
    panel.write_bytes(b"scene frame")
    artifact_digest = workflow._artifact_digest([panel])
    state = root / "review" / "scene_generation.json"
    state.write_text(json.dumps({
        "scene_digest": scene_contract_digest(data),
        "artifact_digest": artifact_digest,
        "reference_artifact_digest": data["review"]["approved_reference_artifact_digest"],
    }), encoding="utf-8")
    data["review"].update({
        "images_approved": True,
        "approved_scene_digest": scene_contract_digest(data),
        "approved_scene_artifact_digest": artifact_digest,
    })
    return artifact_digest


def test_story_validation_and_prompt_anchor_expansion():
    data = complete_story()
    assert data["youtube"]["profile"] == "default"
    assert validate_manifest(data) == []
    prompt = expand_scene_prompt(data, data["scenes"][0])
    assert "mustard raincoat" in prompt
    assert "mossy stone arch" in prompt
    assert "Ari raises the lantern" in prompt
    assert "IDENTITY LOCK" in prompt
    assert "ENVIRONMENT LOCK" in prompt
    assert "east end, moving west" in prompt


def test_story_manifest_rejects_unsafe_youtube_profile():
    data = complete_story()
    data["youtube"]["profile"] = "../wrong-channel"
    assert "youtube.profile" in {problem["path"] for problem in validate_manifest(data)}


def test_story_materialization_is_deterministic(tmp_path):
    data = complete_story()
    first = materialize(data, tmp_path)
    batch1 = json.loads(first["batch"].read_text(encoding="utf-8"))
    second = materialize(data, tmp_path)
    batch2 = json.loads(second["batch"].read_text(encoding="utf-8"))
    assert batch1 == batch2
    assert [path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for path in batch2[0]["reference_images"]] == [
        "character_ari.png", "location_bridge.png",
    ]
    narration = json.loads(second["narration"].read_text(encoding="utf-8"))
    assert narration == [{"image": "scene_001.png", "narration": data["scenes"][0]["narration"]}]
    references = json.loads(second["reference_batch"].read_text(encoding="utf-8"))
    assert [entry["label"] for entry in references] == ["character: ari", "location: bridge"]
    assert second["contract"].is_file()


def test_story_approval_digests_are_invalidated_by_card_or_scene_edits():
    data = complete_story()
    data["review"].update({
        "references_approved": True,
        "approved_reference_digest": reference_contract_digest(data),
        "approved_reference_artifact_digest": "a" * 64,
        "images_approved": True,
        "approved_scene_digest": scene_contract_digest(data),
        "approved_scene_artifact_digest": "b" * 64,
    })
    assert validate_manifest(data) == []

    data["continuity"]["characters"][0]["appearance"] += ", small cheek scar"
    paths = {problem["path"] for problem in validate_manifest(data)}
    assert "review.approved_reference_digest" in paths
    assert "review.approved_scene_digest" in paths


def test_story_reference_digest_covers_rules_seed_steps_and_strategy():
    baseline = complete_story()
    digest = reference_contract_digest(baseline)
    for field, value in (
        ("base_seed", baseline["production"]["base_seed"] + 1),
        ("image_steps", baseline["production"]["image_steps"] + 1),
        ("image_strategy", "cpu"),
    ):
        changed = json.loads(json.dumps(baseline))
        changed["production"][field] = value
        assert reference_contract_digest(changed) != digest
    changed = json.loads(json.dumps(baseline))
    changed["continuity"]["visual_rules"].append("Keep screen direction fixed.")
    assert reference_contract_digest(changed) != digest


def test_story_scene_ledger_requires_visible_character_state():
    data = complete_story()
    del data["scenes"][0]["continuity_state"]["character_state"]["ari"]
    paths = {problem["path"] for problem in validate_manifest(data)}
    assert "scenes[0].continuity_state.character_state.ari" in paths


def test_story_scene_ledger_forms_an_ordered_chain():
    data = complete_story()
    second = json.loads(json.dumps(data["scenes"][0]))
    second.update({
        "id": "far-bank",
        "image_prompt": "Medium rear three-quarter shot as Ari reaches the west bank.",
        "narration": "The far bank appeared through the rain.",
    })
    second["continuity_state"].update({
        "previous_scene_id": "wrong-scene",
        "changes_from_previous": ["Ari moved from the arch to the west bank"],
    })
    data["scenes"].append(second)
    paths = {problem["path"] for problem in validate_manifest(data)}
    assert "scenes[1].continuity_state.previous_scene_id" in paths


def test_story_continuous_transition_materializes_previous_frame_img2img(tmp_path):
    data = complete_story()
    second = json.loads(json.dumps(data["scenes"][0]))
    second.update({
        "id": "under-arch",
        "transition": {"kind": "continuous", "img2img_strength": 0.5},
        "image_prompt": "Medium tracking shot as Ari walks beneath the same arch.",
        "narration": "Rain followed Ari beneath the arch.",
    })
    second["continuity_state"].update({
        "previous_scene_id": "crossing",
        "changes_from_previous": ["Ari moved from the east entrance to beneath the arch"],
    })
    data["scenes"].append(second)

    assert validate_manifest(data) == []
    batch = json.loads(materialize(data, tmp_path)["batch"].read_text(encoding="utf-8"))
    assert batch[0]["generation_mode"] == "text-to-image"
    assert batch[1]["generation_mode"] == "continue-previous"
    assert batch[1]["init_image"].endswith("scene_001.png")
    assert batch[1]["strength"] == 0.5


def test_story_continuous_transition_is_explicit_and_safely_bounded():
    data = complete_story()
    del data["scenes"][0]["transition"]
    assert "scenes[0].transition" in {problem["path"] for problem in validate_manifest(data)}

    data = complete_story()
    data["production"]["continuous_transition_strength"] = 0.9
    assert "production.continuous_transition_strength" in {
        problem["path"] for problem in validate_manifest(data)
    }

    data = complete_story()
    data["scenes"][0]["transition"] = {"kind": "continuous"}
    assert "scenes[0].transition.kind" in {problem["path"] for problem in validate_manifest(data)}


def test_story_publish_gate_requires_qa_and_rights():
    data = complete_story()
    paths = {problem["path"] for problem in validate_manifest(data, for_publish=True)}
    assert "review.video_approved" in paths
    assert "rights" in paths


def test_story_video_preflight_requires_reference_and_scene_approvals(tmp_path):
    data = complete_story()
    data["review"]["approved_reference_artifact_digest"] = ""
    write_reference_state(data, tmp_path)
    write_scene_state(data, tmp_path)
    data["review"].update({
        "references_approved": False,
        "approved_reference_digest": "",
        "approved_reference_artifact_digest": "",
    })

    paths = {problem["path"] for problem in video_preflight_problems(data, tmp_path)}
    assert "review.references_approved" in paths
    assert "review.images_approved" not in paths


def test_story_video_preflight_lists_missing_scene_frames(tmp_path):
    data = complete_story()
    write_reference_state(data, tmp_path)
    data["review"].update({
        "images_approved": True,
        "approved_scene_digest": scene_contract_digest(data),
        "approved_scene_artifact_digest": "c" * 64,
    })

    problems = video_preflight_problems(data, tmp_path)
    assert problems == [{
        "path": "review.approved_scene_artifact_digest",
        "message": f"generated artifact is missing: {tmp_path / 'content' / '01' / 'panels' / 'scene_001.png'}",
    }]


def test_story_scene_approval_is_bound_to_current_reference_artifacts(tmp_path):
    data = complete_story()
    old_reference_digest = write_reference_state(data, tmp_path)
    write_scene_state(data, tmp_path)

    reference = tmp_path / "review" / "references" / "character_ari.png"
    reference.write_bytes(b"regenerated character reference")
    outputs = workflow._reference_outputs(data, tmp_path)
    new_reference_digest = workflow._artifact_digest(outputs)
    assert new_reference_digest != old_reference_digest
    (tmp_path / "review" / "reference_generation.json").write_text(json.dumps({
        "reference_digest": reference_contract_digest(data),
        "artifact_digest": new_reference_digest,
    }), encoding="utf-8")
    data["review"]["approved_reference_artifact_digest"] = new_reference_digest

    problems = video_preflight_problems(data, tmp_path)
    assert {
        "path": "review.approved_scene_artifact_digest",
        "message": (
            "scene generation is bound to stale identity/environment references; "
            "regenerate and review the scene frames"
        ),
    } in problems


def test_story_video_contract_covers_fps_tts_narration_and_voice_inputs():
    baseline = complete_story()
    digest = workflow.video_contract_digest(baseline, resolved_tts="kokoro")

    for mutate in (
        lambda data: data["production"].update({"fps": 60}),
        lambda data: data["production"].update({"tts": "kokoro"}),
        lambda data: data["scenes"][0].update({"narration": "A corrected spoken line."}),
        lambda data: data["scenes"][0].update({"speaker": "ari"}),
        lambda data: data["scenes"][0].update({"emotion": "urgent"}),
    ):
        changed = json.loads(json.dumps(baseline))
        mutate(changed)
        assert workflow.video_contract_digest(changed, resolved_tts="kokoro") != digest
    assert workflow.video_contract_digest(
        baseline, resolved_tts="indextts", speaker_wav_sha256="a" * 64,
    ) != workflow.video_contract_digest(
        baseline, resolved_tts="indextts", speaker_wav_sha256="b" * 64,
    )


def test_story_video_voice_provenance_detects_changed_speaker_reference(tmp_path):
    speaker = tmp_path / "speaker.wav"
    speaker.write_bytes(b"reviewed voice reference")
    state = {
        "resolved_tts": "indextts",
        "speaker_wav": str(speaker),
        "speaker_wav_sha256": workflow._sha256_file(speaker),
    }
    assert workflow._voice_provenance_from_state(state) == (
        "indextts", state["speaker_wav_sha256"], [],
    )

    speaker.write_bytes(b"different voice reference")
    _resolved_tts, _digest, problems = workflow._voice_provenance_from_state(state)
    assert any("speaker reference changed" in problem["message"] for problem in problems)


def test_story_review_invalidation_is_persisted_before_replacement(tmp_path):
    data = complete_story()
    data["review"].update({
        "references_approved": True,
        "approved_reference_digest": reference_contract_digest(data),
        "approved_reference_artifact_digest": "a" * 64,
        "images_approved": True,
        "approved_scene_digest": scene_contract_digest(data),
        "approved_scene_artifact_digest": "b" * 64,
        "video_approved": True,
        "approved_video_sha256": "c" * 64,
    })
    manifest = tmp_path / "story.json"
    workflow._invalidate_review(data, manifest, "references")
    persisted = json.loads(manifest.read_text(encoding="utf-8"))
    assert persisted["review"]["references_approved"] is False
    assert persisted["review"]["images_approved"] is False
    assert persisted["review"]["video_approved"] is False


def test_story_video_dry_run_overwrites_stale_audio_and_video_inputs(
    tmp_path, monkeypatch, capsys,
):
    data = complete_story()
    data["production"]["tts"] = "kokoro"
    write_reference_state(data, tmp_path)
    write_scene_state(data, tmp_path)
    manifest = tmp_path / "story.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor story-build", "--manifest", str(manifest), "--stage", "video", "--dry-run"],
    )

    assert workflow.build_main() == 0
    report = json.loads(capsys.readouterr().out)
    command = report["commands"][0]
    assert report["resolved_tts"] == "kokoro"
    assert command[command.index("--tts") + 1] == "kokoro"
    assert "--overwrite-audio" in command
    assert "--overwrite-video" in command


def test_story_publish_dry_run_routes_selected_youtube_profile(
    tmp_path, monkeypatch, capsys,
):
    data = complete_story()
    data["review"].update({
        "video_approved": True,
    })
    write_reference_state(data, tmp_path)
    scene_artifact_digest = write_scene_state(data, tmp_path)
    for key in (
        "content_rights_confirmed", "voice_consent_confirmed",
        "synthetic_media_disclosure_acknowledged",
    ):
        data["rights"][key] = True
    data["youtube"]["profile"] = "ai-story"
    manifest = tmp_path / "story.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    video = tmp_path / "output" / "the-lantern" / "the-lantern_full.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    video_sha256 = workflow._sha256_file(video)
    data["review"]["approved_video_sha256"] = video_sha256
    manifest.write_text(json.dumps(data), encoding="utf-8")
    video_state = tmp_path / "review" / "video_generation.json"
    narration_digest = workflow.narration_contract_digest(data, resolved_tts="kokoro")
    video_state.write_text(json.dumps({
        "schema_version": 2,
        "video": str(video.resolve()),
        "sha256": video_sha256,
        "scene_digest": scene_contract_digest(data),
        "scene_artifact_digest": scene_artifact_digest,
        "narration_contract_digest": narration_digest,
        "video_contract_digest": workflow.video_contract_digest(data, resolved_tts="kokoro"),
        "resolved_tts": "kokoro",
        "speaker_wav": None,
        "speaker_wav_sha256": None,
    }), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["mediaconductor story-build", "--manifest", str(manifest),
         "--stage", "publish", "--dry-run"],
    )

    assert workflow.build_main() == 0
    report = json.loads(capsys.readouterr().out)
    command = report["commands"][0]
    assert command[command.index("--profile") + 1] == "ai-story"

    data["production"]["fps"] += 1
    _video, stale_problems = workflow.approved_video(data, tmp_path)
    assert any("video generation record is stale" in problem["message"] for problem in stale_problems)
