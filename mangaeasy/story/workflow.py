"""AI Story manifests and the deterministic build orchestration.

The LLM authors ``story.json``; this module owns validation, prompt expansion,
filesystem layout, model invocation, rendering, and publishing.  Keeping those
responsibilities separate makes the creative step flexible without making the
expensive production steps ambiguous.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import re
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import cli_command, popen_kwargs
from mangaeasy.utils import archive_before_overwrite, atomic_write_json, emit_result
from mangaeasy.youtube.store import validate_profile

SCHEMA_VERSION = 2
MANIFEST_NAME = "story.json"
REFERENCE_IMAGE_STYLE = "clean hand-drawn fantasy webcomic, restrained cel color, fine organic ink lines"
HARD_CUT = "hard-cut"
CONTINUOUS_TRANSITION = "continuous"
DEFAULT_CONTINUITY_STRENGTH = 0.45
MIN_CONTINUITY_STRENGTH = 0.35
MAX_CONTINUITY_STRENGTH = 0.65

# This is a medium-level, non-artist-specific description distilled from the
# user-provided visual references.  Keeping it structured makes the exact same
# style language available to reference sheets and every scene prompt.
REFERENCE_STYLE_CONTRACT = {
    "id": "clean-fantasy-webcomic-v1",
    "medium": "hand-drawn 2D fantasy webcomic illustration with anime-influenced proportions",
    "linework": "clean dark organic ink lines, fine facial lines, controlled line-weight variation",
    "color": "flat cel colors with a restrained palette and one or two clear accent colors",
    "shading": "minimal soft cel shading, sparse highlights, no painterly texture",
    # Wording note: this text is injected into guidance-0 prompts, where
    # concrete nouns act as attractors — "silhouette" drew black cutout
    # figures and "single-panel" drew comic panel grids in production.
    "characters": "elegant readable figure shapes, expressive eyes and hands, natural hair shapes, consistent anatomy",
    "environments": "simple readable geometry, selective prop detail, uncluttered atmospheric backgrounds",
    "composition": "one continuous full-bleed cinematic frame, strong figure-to-background separation, readable emotion, deliberate negative space",
    "lighting": "soft cinematic light; use charcoal-navy gradients for tense interiors and warm gold light for open scenes",
}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "ai-story"


def _read_text_arg(text: str | None, path: Path | None, parser: argparse.ArgumentParser) -> str:
    if bool(text) == bool(path):
        parser.error("pass exactly one of --story or --story-file")
    result = text if text is not None else path.read_text(encoding="utf-8")
    result = result.strip()
    if not result:
        parser.error("story text must not be empty")
    return result


def new_manifest(title: str, source_story: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "ai-story",
        "title": title,
        "source_story": source_story,
        "production": {
            "width": 1280,
            "height": 720,
            "fps": 24,
            "image_style": REFERENCE_IMAGE_STYLE,
            "style_contract": copy.deepcopy(REFERENCE_STYLE_CONTRACT),
            "negative_prompt": (
                "photorealism, 3D render, painterly brush texture, heavy rendering, neon saturation, "
                "text, speech bubble, caption, watermark, logo, malformed anatomy, extra limbs, "
                "duplicate subject, changed face, changed hair, changed horns, changed wardrobe"
            ),
            "image_steps": 9,
            "image_strategy": "auto",
            "continuous_transition_strength": DEFAULT_CONTINUITY_STRENGTH,
            "tts": "auto",
            "base_seed": int.from_bytes(hashlib.sha256(title.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF,
        },
        "continuity": {
            "visual_rules": [
                "Treat each character card as immutable: never redesign face, apparent age, body, hair, skin, eyes, or signature features.",
                "Use only the wardrobe ID recorded in each scene state; never invent a costume change.",
                "Treat each environment card as immutable; preserve layout, fixed props, palette, and light until the ledger records a change.",
                "Carry held objects, injuries, dirt, weather, time, and character positions forward through the scene-state ledger.",
            ],
            "characters": [],
            "locations": [],
        },
        "scenes": [],
        "review": {
            "references_approved": False,
            "approved_reference_digest": "",
            "approved_reference_artifact_digest": "",
            "images_approved": False,
            "approved_scene_digest": "",
            "approved_scene_artifact_digest": "",
            "video_approved": False,
            "approved_video_sha256": "",
            "notes": "",
        },
        "rights": {
            "content_rights_confirmed": False,
            "voice_consent_confirmed": False,
            "synthetic_media_disclosure_acknowledged": False,
            "provenance_notes": "",
        },
        "youtube": {
            "profile": "default",
            "title": title,
            "description": "",
            "tags": ["ai story", "story video"],
            "privacy": "private",
        },
    }


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"story manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("story manifest must contain one JSON object")
    return data


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _stable_digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def reference_contract_digest(data: dict) -> str:
    """Fingerprint everything an approved identity/environment sheet represents."""
    production = data.get("production", {})
    continuity = data.get("continuity", {})
    if not isinstance(production, dict):
        production = {}
    if not isinstance(continuity, dict):
        continuity = {}
    return _stable_digest({
        "schema_version": data.get("schema_version"),
        "size": [production.get("width"), production.get("height")],
        "base_seed": production.get("base_seed"),
        "image_steps": production.get("image_steps"),
        "image_strategy": production.get("image_strategy"),
        "image_style": production.get("image_style"),
        "style_contract": production.get("style_contract"),
        "negative_prompt": production.get("negative_prompt"),
        "visual_rules": continuity.get("visual_rules", []),
        "characters": continuity.get("characters", []),
        "locations": continuity.get("locations", []),
    })


def scene_contract_digest(data: dict) -> str:
    """Fingerprint the approved references plus the ordered scene ledger."""
    production = data.get("production", {})
    if not isinstance(production, dict):
        production = {}
    return _stable_digest({
        "reference_digest": reference_contract_digest(data),
        "base_seed": production.get("base_seed"),
        "image_steps": production.get("image_steps"),
        "continuous_transition_strength": production.get("continuous_transition_strength"),
        "scenes": data.get("scenes", []),
    })


def narration_contract_digest(
    data: dict,
    *,
    resolved_tts: str | None = None,
    speaker_wav_sha256: str | None = None,
) -> str:
    """Fingerprint every authored or selected input that changes narration audio."""
    production = data.get("production", {})
    if not isinstance(production, dict):
        production = {}
    requested_tts = production.get("tts")
    narration = []
    for scene in data.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        narration.append({
            "id": scene.get("id"),
            "narration": scene.get("narration"),
            "speaker": scene.get("speaker"),
            "emotion": scene.get("emotion"),
        })
    return _stable_digest({
        "schema_version": data.get("schema_version"),
        "tts_requested": requested_tts,
        "tts_resolved": resolved_tts or requested_tts,
        "speaker_wav_sha256": speaker_wav_sha256,
        "narration": narration,
    })


def video_contract_digest(
    data: dict,
    *,
    resolved_tts: str | None = None,
    speaker_wav_sha256: str | None = None,
) -> str:
    """Fingerprint the complete authored contract for one narrated video render."""
    production = data.get("production", {})
    if not isinstance(production, dict):
        production = {}
    return _stable_digest({
        "schema_version": data.get("schema_version"),
        "scene_digest": scene_contract_digest(data),
        "fps": production.get("fps"),
        "narration_contract_digest": narration_contract_digest(
            data,
            resolved_tts=resolved_tts,
            speaker_wav_sha256=speaker_wav_sha256,
        ),
    })


def _lock(prefix: str, value: dict) -> str:
    return f"{prefix}-{_stable_digest(value).upper()}"


def _valid_string_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def _load_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _recorded_digest(path: Path, field: str) -> str | None:
    value = _load_json_object(path)
    if not isinstance(value.get(field), str):
        return None
    return value[field]


def _artifact_digest(paths: list[Path]) -> str:
    """Fingerprint an ordered generated artifact set without binding its root path."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _invalidate_review(data: dict, manifest: Path, stage: str) -> None:
    """Persist approval invalidation before generated artifacts are replaced."""
    review = data["review"]
    if stage == "references":
        review.update({
            "references_approved": False,
            "approved_reference_digest": "",
            "approved_reference_artifact_digest": "",
            "images_approved": False,
            "approved_scene_digest": "",
            "approved_scene_artifact_digest": "",
            "video_approved": False,
            "approved_video_sha256": "",
        })
    elif stage == "images":
        review.update({
            "images_approved": False,
            "approved_scene_digest": "",
            "approved_scene_artifact_digest": "",
            "video_approved": False,
            "approved_video_sha256": "",
        })
    elif stage == "video":
        review.update({"video_approved": False, "approved_video_sha256": ""})
    else:  # pragma: no cover - internal programming guard
        raise ValueError(f"unknown review stage: {stage}")
    if not atomic_write_json(manifest, data):
        raise OSError(f"could not invalidate review state in {manifest}")


def validate_manifest(data: dict, *, for_publish: bool = False) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []

    def error(path: str, message: str) -> None:
        problems.append({"severity": "error", "path": path, "message": message})

    if data.get("schema_version") != SCHEMA_VERSION:
        error("schema_version", f"must equal {SCHEMA_VERSION}")
    if data.get("mode") != "ai-story":
        error("mode", "must equal 'ai-story'")
    for key in ("title", "source_story"):
        if not _nonempty(data.get(key)):
            error(key, "must be a non-empty string")

    production = data.get("production")
    if not isinstance(production, dict):
        error("production", "must be an object")
        production = {}
    for key in ("width", "height", "fps", "image_steps"):
        value = production.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            error(f"production.{key}", "must be a positive integer")
    if not isinstance(production.get("base_seed"), int) or isinstance(production.get("base_seed"), bool):
        error("production.base_seed", "must be an integer")
    if not _nonempty(production.get("image_style")):
        error("production.image_style", "must be a non-empty consistency style")
    elif production.get("image_style") != REFERENCE_IMAGE_STYLE:
        error("production.image_style", "must remain equal to the supplied-reference style lock")
    if not _nonempty(production.get("negative_prompt")):
        error("production.negative_prompt", "must be a non-empty exclusion lock")
    style_contract = production.get("style_contract")
    if not isinstance(style_contract, dict):
        error("production.style_contract", "must be an object copied from the style contract")
    else:
        for field in REFERENCE_STYLE_CONTRACT:
            if not _nonempty(style_contract.get(field)):
                error(f"production.style_contract.{field}", "must be a non-empty style lock")
            elif style_contract.get(field) != REFERENCE_STYLE_CONTRACT[field]:
                error(f"production.style_contract.{field}",
                      "must remain equal to the clean-fantasy-webcomic-v1 reference contract")
    if production.get("image_strategy") not in {"auto", "bf16", "nf4", "offload", "cpu"}:
        error("production.image_strategy", "must be auto, bf16, nf4, offload, or cpu")
    transition_strength = production.get("continuous_transition_strength")
    if (isinstance(transition_strength, bool)
            or not isinstance(transition_strength, (int, float))
            or not MIN_CONTINUITY_STRENGTH <= transition_strength <= MAX_CONTINUITY_STRENGTH):
        error(
            "production.continuous_transition_strength",
            f"must be a number from {MIN_CONTINUITY_STRENGTH} to {MAX_CONTINUITY_STRENGTH}",
        )
    if production.get("tts") not in {"auto", "kokoro", "indextts"}:
        error("production.tts", "must be auto, kokoro, or indextts")

    continuity = data.get("continuity")
    if not isinstance(continuity, dict):
        error("continuity", "must be an object")
        continuity = {}
    if not _valid_string_list(continuity.get("visual_rules")):
        error("continuity.visual_rules", "must contain non-empty immutable continuity rules")
    character_ids: set[str] = set()
    characters_value = continuity.get("characters", [])
    if not isinstance(characters_value, list):
        error("continuity.characters", "must be an array of immutable character cards")
        characters_value = []
    characters_by_id: dict[str, dict] = {}
    for index, character in enumerate(characters_value):
        path = f"continuity.characters[{index}]"
        if not isinstance(character, dict):
            error(path, "must be an object")
            continue
        char_id = character.get("id")
        if not _nonempty(char_id) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(char_id)):
            error(f"{path}.id", "must match [a-z0-9][a-z0-9-]*")
        elif char_id in character_ids:
            error(f"{path}.id", f"duplicate character id '{char_id}'")
        else:
            character_ids.add(char_id)
            characters_by_id[char_id] = character
        for field in ("name", "apparent_age", "appearance", "wardrobe"):
            if not _nonempty(character.get(field)):
                error(f"{path}.{field}", "must be non-empty; it is a required visual anchor")
        for field in ("signature_features", "never_change"):
            if not _valid_string_list(character.get(field)):
                error(f"{path}.{field}", "must contain non-empty immutable visual anchors")
        variants = character.get("wardrobe_variants", {})
        if not isinstance(variants, dict) or any(not _nonempty(key) or not _nonempty(value)
                                                 for key, value in variants.items()):
            error(f"{path}.wardrobe_variants", "must map stable wardrobe ids to exact descriptions")

    location_ids: set[str] = set()
    locations_value = continuity.get("locations", [])
    if not isinstance(locations_value, list):
        error("continuity.locations", "must be an array of immutable environment cards")
        locations_value = []
    for index, location in enumerate(locations_value):
        path = f"continuity.locations[{index}]"
        if not isinstance(location, dict):
            error(path, "must be an object")
            continue
        location_id = location.get("id")
        if not _nonempty(location_id) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(location_id)):
            error(f"{path}.id", "must match [a-z0-9][a-z0-9-]*")
        elif location_id in location_ids:
            error(f"{path}.id", f"duplicate location id '{location_id}'")
        else:
            location_ids.add(location_id)
        for field in ("name", "visual_anchor", "palette", "lighting"):
            if not _nonempty(location.get(field)):
                error(f"{path}.{field}", "must be a non-empty visual anchor")
        if not _valid_string_list(location.get("fixed_elements")):
            error(f"{path}.fixed_elements", "must list persistent geometry and props")

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        error("scenes", "must contain at least one completed scene")
        scenes = []
    scene_ids: set[str] = set()
    for index, scene in enumerate(scenes):
        path = f"scenes[{index}]"
        if not isinstance(scene, dict):
            error(path, "must be an object")
            continue
        scene_id = scene.get("id")
        if not _nonempty(scene_id) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(scene_id)):
            error(f"{path}.id", "must match [a-z0-9][a-z0-9-]*")
        elif scene_id in scene_ids:
            error(f"{path}.id", f"duplicate scene id '{scene_id}'")
        else:
            scene_ids.add(scene_id)
        for field in ("narration", "image_prompt"):
            if not _nonempty(scene.get(field)):
                error(f"{path}.{field}", "must be a non-empty string")
        if scene.get("render_mode") not in {"standard", "chibi"}:
            error(f"{path}.render_mode", "must be standard or chibi")
        transition = scene.get("transition")
        if not isinstance(transition, dict):
            error(f"{path}.transition", "must explicitly select hard-cut or continuous")
            transition = {}
        transition_kind = transition.get("kind")
        if transition_kind not in {HARD_CUT, CONTINUOUS_TRANSITION}:
            error(f"{path}.transition.kind", "must be hard-cut or continuous")
        if index == 0 and transition_kind == CONTINUOUS_TRANSITION:
            error(f"{path}.transition.kind", "the opening scene has no previous frame and must be hard-cut")
        scene_strength = transition.get("img2img_strength")
        if transition_kind == CONTINUOUS_TRANSITION and scene_strength is not None:
            if (isinstance(scene_strength, bool)
                    or not isinstance(scene_strength, (int, float))
                    or not MIN_CONTINUITY_STRENGTH <= scene_strength <= MAX_CONTINUITY_STRENGTH):
                error(
                    f"{path}.transition.img2img_strength",
                    f"must be a number from {MIN_CONTINUITY_STRENGTH} to {MAX_CONTINUITY_STRENGTH}",
                )
        elif transition_kind == HARD_CUT and scene_strength is not None:
            error(f"{path}.transition.img2img_strength", "is only valid for a continuous transition")
        refs = scene.get("characters", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            error(f"{path}.characters", "must be an array of character ids")
            refs = []
        else:
            for ref in refs:
                if ref not in character_ids:
                    error(f"{path}.characters", f"unknown character id '{ref}'")
        location = scene.get("location")
        if not _nonempty(location):
            error(f"{path}.location", "must reference one environment card")
        elif location not in location_ids:
            error(f"{path}.location", f"unknown location id '{location}'")
        if transition_kind == CONTINUOUS_TRANSITION and index > 0:
            previous = scenes[index - 1] if isinstance(scenes[index - 1], dict) else {}
            if location != previous.get("location"):
                error(
                    f"{path}.transition.kind",
                    "continuous img2img is only safe within the same location; use hard-cut for a location change",
                )
            if scene.get("render_mode") != previous.get("render_mode"):
                error(
                    f"{path}.transition.kind",
                    "continuous img2img cannot cross a render-mode change; use hard-cut",
                )
        state = scene.get("continuity_state")
        if not isinstance(state, dict):
            error(f"{path}.continuity_state", "must be an explicit scene-state ledger object")
            state = {}
        previous_scene_id = state.get("previous_scene_id")
        expected_previous_id = scenes[index - 1].get("id") if index > 0 and isinstance(scenes[index - 1], dict) else None
        if previous_scene_id != expected_previous_id:
            error(f"{path}.continuity_state.previous_scene_id",
                  f"must equal {expected_previous_id!r} to form an ordered continuity chain")
        for field in ("time_of_day", "weather"):
            if not _nonempty(state.get(field)):
                error(f"{path}.continuity_state.{field}", "must be a non-empty carried-forward state")
        for field in ("environment_state", "changes_from_previous"):
            if not _valid_string_list(state.get(field)):
                error(f"{path}.continuity_state.{field}", "must contain explicit state entries")
        changes = state.get("changes_from_previous", [])
        if isinstance(changes, list):
            has_opening = any(isinstance(item, str) and item.strip().lower() == "opening state" for item in changes)
            if index == 0 and not has_opening:
                error(f"{path}.continuity_state.changes_from_previous",
                      "the first scene must contain the exact entry 'opening state'")
            if index > 0 and has_opening:
                error(f"{path}.continuity_state.changes_from_previous",
                      "only the first scene may use 'opening state'; record the exact delta")
        character_state = state.get("character_state")
        if not isinstance(character_state, dict):
            error(f"{path}.continuity_state.character_state", "must map every visible character id to state")
            character_state = {}
        for state_id in character_state:
            if state_id not in refs:
                error(f"{path}.continuity_state.character_state.{state_id}",
                      "state is only allowed for a character listed in this scene")
        for char_id in refs:
            char_state = character_state.get(char_id)
            state_path = f"{path}.continuity_state.character_state.{char_id}"
            if not isinstance(char_state, dict):
                error(state_path, "must record wardrobe, position, condition, emotion, and held items")
                continue
            for field in ("wardrobe_id", "position", "condition", "emotion"):
                if not _nonempty(char_state.get(field)):
                    error(f"{state_path}.{field}", "must be a non-empty carried-forward state")
            held_items = char_state.get("held_items")
            if not isinstance(held_items, list) or any(not _nonempty(item) for item in held_items):
                error(f"{state_path}.held_items", "must be an array of exact prop names; [] is allowed")
            wardrobe_id = char_state.get("wardrobe_id")
            card = characters_by_id.get(char_id, {})
            wardrobe_variants = card.get("wardrobe_variants", {})
            if not isinstance(wardrobe_variants, dict):
                wardrobe_variants = {}
            valid_wardrobes = {"default", *wardrobe_variants.keys()}
            if _nonempty(wardrobe_id) and wardrobe_id not in valid_wardrobes:
                error(f"{state_path}.wardrobe_id", f"unknown wardrobe id '{wardrobe_id}'")
        seed = scene.get("seed")
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or seed < 0):
            error(f"{path}.seed", "must be a non-negative integer")

    youtube = data.get("youtube", {})
    if not isinstance(youtube, dict):
        error("youtube", "must be an object")
        youtube = {}
    else:
        profile = youtube.get("profile", "default")
        if not isinstance(profile, str):
            error("youtube.profile", "must be a safe profile name string")
        else:
            try:
                validate_profile(profile)
            except ValueError as exc:
                error("youtube.profile", str(exc))
        if youtube.get("privacy", "private") not in {"private", "unlisted", "public"}:
            error("youtube.privacy", "must be private, unlisted, or public")
        for field in ("title", "description"):
            if not isinstance(youtube.get(field), str):
                error(f"youtube.{field}", "must be a string")
        tags = youtube.get("tags")
        if not isinstance(tags, list) or any(not _nonempty(tag) for tag in tags):
            error("youtube.tags", "must be an array of non-empty tags")
    rights = data.get("rights", {})
    if not isinstance(rights, dict):
        error("rights", "must be an object")
        rights = {}
    for field in ("content_rights_confirmed", "voice_consent_confirmed",
                  "synthetic_media_disclosure_acknowledged"):
        if not isinstance(rights.get(field), bool):
            error(f"rights.{field}", "must be true or false")
    if not isinstance(rights.get("provenance_notes"), str):
        error("rights.provenance_notes", "must be a string")
    review = data.get("review", {})
    if not isinstance(review, dict):
        error("review", "must be an object")
        review = {}
    for field in ("references_approved", "images_approved", "video_approved"):
        if not isinstance(review.get(field), bool):
            error(f"review.{field}", "must be true or false")
    for field in (
        "approved_reference_digest", "approved_reference_artifact_digest",
        "approved_scene_digest", "approved_scene_artifact_digest",
        "approved_video_sha256", "notes",
    ):
        if not isinstance(review.get(field), str):
            error(f"review.{field}", "must be a string")
    expected_reference_digest = reference_contract_digest(data)
    expected_scene_digest = scene_contract_digest(data)
    if review.get("references_approved") is True and review.get("approved_reference_digest") != expected_reference_digest:
        error("review.approved_reference_digest", f"must equal current reference digest {expected_reference_digest}")
    if review.get("references_approved") is True and not re.fullmatch(
        r"[0-9a-f]{64}", review.get("approved_reference_artifact_digest", "")
    ):
        error("review.approved_reference_artifact_digest", "must be the 64-character digest of the reviewed references")
    if review.get("images_approved") is True and review.get("approved_scene_digest") != expected_scene_digest:
        error("review.approved_scene_digest", f"must equal current scene digest {expected_scene_digest}")
    if review.get("images_approved") is True and not re.fullmatch(
        r"[0-9a-f]{64}", review.get("approved_scene_artifact_digest", "")
    ):
        error("review.approved_scene_artifact_digest", "must be the 64-character digest of the reviewed scene frames")
    if review.get("video_approved") is True and not re.fullmatch(
        r"[0-9a-f]{64}", review.get("approved_video_sha256", "")
    ):
        error("review.approved_video_sha256", "must be the SHA-256 of the reviewed video")
    if for_publish:
        if review.get("references_approved") is not True:
            error("review.references_approved", "must be true before publishing")
        if review.get("images_approved") is not True:
            error("review.images_approved", "must be true before publishing")
        if review.get("video_approved") is not True:
            error("review.video_approved", "must be true before publishing")
        missing = [key for key in (
            "content_rights_confirmed", "voice_consent_confirmed",
            "synthetic_media_disclosure_acknowledged",
        ) if rights.get(key) is not True]
        if missing:
            error("rights", "publishing requires true confirmations: " + ", ".join(missing))
    return problems


def _style_prompt(data: dict) -> str:
    production = data["production"]
    contract = production["style_contract"]
    fields = [field for field in REFERENCE_STYLE_CONTRACT if field != "id"]
    details = "; ".join(f"{field}: {contract[field].strip()}" for field in fields)
    return (
        f"STYLE LOCK {_lock('STYLE', contract)} ({contract['id']}): "
        f"{production['image_style'].strip()}. {details}."
    )


def _character_prompt(char: dict, state: dict | None = None) -> str:
    wardrobe_id = (state or {}).get("wardrobe_id", "default")
    wardrobe = char["wardrobe"] if wardrobe_id == "default" else char["wardrobe_variants"][wardrobe_id]
    signature = "; ".join(char["signature_features"])
    never_change = "; ".join(char["never_change"])
    result = (
        f"IDENTITY LOCK {_lock('CHAR', char)} — {char['name']}, apparent age {char['apparent_age']}: "
        f"{char['appearance']}. Signature features: {signature}. "
        f"WARDROBE LOCK {wardrobe_id}: {wardrobe}. Never change: {never_change}."
    )
    if state:
        held = ", ".join(state.get("held_items", [])) or "nothing"
        result += (
            f" Current state: position {state['position']}; condition {state['condition']}; "
            f"emotion {state['emotion']}; holding {held}."
        )
    return result


def _location_prompt(location: dict, state: dict | None = None) -> str:
    fixed = "; ".join(location["fixed_elements"])
    result = (
        f"ENVIRONMENT LOCK {_lock('ENV', location)} — {location['name']}: {location['visual_anchor']}. "
        f"Fixed elements: {fixed}. Palette: {location['palette']}. Lighting: {location['lighting']}."
    )
    if state:
        result += (
            f" Current time: {state['time_of_day']}; weather: {state['weather']}; "
            f"current environment state: {'; '.join(state['environment_state'])}."
        )
    return result


def expand_scene_prompt(data: dict, scene: dict) -> str:
    continuity = data["continuity"]
    characters = {item["id"]: item for item in continuity.get("characters", [])}
    locations = {item["id"]: item for item in continuity.get("locations", [])}
    state = scene["continuity_state"]
    parts = [_style_prompt(data)]
    rules = continuity.get("visual_rules", [])
    if rules:
        parts.append("IMMUTABLE CONTINUITY RULES: " + " ".join(rule.strip() for rule in rules))
    for char_id in scene.get("characters", []):
        parts.append(_character_prompt(characters[char_id], state["character_state"][char_id]))
    if scene.get("location"):
        parts.append(_location_prompt(locations[scene["location"]], state))
    parts.append(f"CONTINUITY LINK: previous scene {state['previous_scene_id'] or 'OPENING'}.")
    parts.append("STATE CHANGE FROM PREVIOUS FRAME: " + "; ".join(state["changes_from_previous"]))
    if scene["transition"]["kind"] == CONTINUOUS_TRANSITION:
        parts.append(
            "FRAME TRANSITION: continuous evolution from the immediately previous approved frame; preserve its "
            "identity, stable geometry, screen direction, and unchanged objects while applying only the recorded delta."
        )
    else:
        parts.append("FRAME TRANSITION: hard cut; construct this frame independently from the immutable locks.")
    parts.append("RENDER MODE: " + scene["render_mode"] + ".")
    parts.append("SHOT INSTRUCTION: " + scene["image_prompt"].strip())
    parts.append(
        "Render exactly one continuous 16:9 frame that fills the entire canvas. Match all locks literally. "
        "Preserve identity, costume, screen direction, props, injuries, weather, and light. "
        "No captions, speech bubbles, logo, or watermark."
    )
    negative = data["production"].get("negative_prompt", "").strip()
    if negative:
        parts.append("Avoid: " + negative)
    return "\n".join(parts)


def expand_character_reference_prompt(data: dict, character: dict) -> str:
    return "\n".join([
        _style_prompt(data),
        _character_prompt(character),
        "REFERENCE SHEET: one character only, neutral standing three-quarter full-body pose, face and hands clearly "
        "visible, exact default wardrobe, simple mid-gray gradient background, even neutral light, no action, no props "
        "unless they are an immutable signature feature, no labels, no text.",
        "Avoid: " + data["production"].get("negative_prompt", "").strip(),
    ])


def expand_location_reference_prompt(data: dict, location: dict) -> str:
    return "\n".join([
        _style_prompt(data),
        _location_prompt(location),
        "ENVIRONMENT REFERENCE: empty wide establishing frame, eye-level view, show the complete stable layout and "
        "every fixed element clearly, no characters, no action, no labels, no text.",
        "Avoid: " + data["production"].get("negative_prompt", "").strip(),
    ])


def _scene_seed(data: dict, scene: dict, index: int) -> int:
    if scene.get("seed") is not None:
        return int(scene["seed"])
    source = f"{data['production']['base_seed']}:{scene.get('id', index)}"
    return int.from_bytes(hashlib.sha256(source.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF


def _scene_batch_entry(
    data: dict,
    scene: dict,
    index: int,
    prompt_path: Path,
    output: Path,
    references_dir: Path,
) -> dict:
    """Create the executable, provenance-rich Z-Image batch entry for one scene."""
    transition = scene["transition"]
    entry = {
        "prompt_file": str(prompt_path.resolve()),
        "output": str(output.resolve()),
        "width": data["production"]["width"],
        "height": data["production"]["height"],
        "steps": data["production"]["image_steps"],
        "seed": _scene_seed(data, scene, index),
        "generation_mode": "text-to-image",
        # These approved references remain explicit QA/provenance metadata.
        # Z-Image's previous-frame img2img path does not treat them as
        # multi-reference identity-conditioning inputs.
        "reference_images": [
            str((references_dir / f"character_{_slug(char_id)}.png").resolve())
            for char_id in scene.get("characters", [])
        ],
        "reference_digest": reference_contract_digest(data),
        "scene_id": scene["id"],
        "transition_kind": transition["kind"],
    }
    if scene.get("location"):
        entry["reference_images"].append(
            str((references_dir / f"location_{_slug(scene['location'])}.png").resolve())
        )
    if transition["kind"] == CONTINUOUS_TRANSITION:
        entry.update({
            "generation_mode": "continue-previous",
            "init_image": str(output.with_name(f"scene_{index - 1:03d}.png").resolve()),
            "strength": transition.get(
                "img2img_strength",
                data["production"]["continuous_transition_strength"],
            ),
        })
    return entry


def materialize(data: dict, project_root: Path) -> dict[str, Path | list[Path]]:
    prompts_dir = project_root / "prompts"
    reference_prompts_dir = prompts_dir / "references"
    references_dir = project_root / "review" / "references"
    panels_dir = project_root / "content" / "01" / "panels"
    reference_prompts_dir.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)
    panels_dir.mkdir(parents=True, exist_ok=True)
    reference_batch: list[dict] = []
    for kind, entries, expander in (
        ("character", data["continuity"].get("characters", []), expand_character_reference_prompt),
        ("location", data["continuity"].get("locations", []), expand_location_reference_prompt),
    ):
        for index, entry in enumerate(entries, start=1):
            stem = f"{kind}_{_slug(entry['id'])}"
            prompt_path = reference_prompts_dir / f"{stem}.txt"
            prompt_path.write_text(expander(data, entry) + "\n", encoding="utf-8")
            reference_batch.append({
                "prompt_file": str(prompt_path.resolve()),
                "output": str((references_dir / f"{stem}.png").resolve()),
                "width": data["production"]["width"],
                "height": data["production"]["height"],
                "steps": data["production"]["image_steps"],
                "seed": _scene_seed(data, {"id": f"reference-{kind}-{entry['id']}"}, index),
                "label": f"{kind}: {entry['id']}",
            })
    prompts: list[Path] = []
    narration: list[dict] = []
    batch: list[dict] = []
    for index, scene in enumerate(data["scenes"], start=1):
        stem = f"scene_{index:03d}"
        prompt_path = prompts_dir / f"{stem}.txt"
        prompt_path.write_text(expand_scene_prompt(data, scene) + "\n", encoding="utf-8")
        prompts.append(prompt_path)
        batch.append(_scene_batch_entry(
            data,
            scene,
            index,
            prompt_path,
            panels_dir / f"{stem}.png",
            references_dir,
        ))
        entry = {"image": f"{stem}.png", "narration": scene["narration"].strip()}
        for key in ("emotion", "speaker"):
            if scene.get(key):
                entry[key] = scene[key]
        narration.append(entry)
    narration_path = project_root / "content" / "01" / "narration.json"
    if not atomic_write_json(narration_path, narration):
        raise OSError(f"could not write narration: {narration_path}")
    batch_path = project_root / "prompts" / "image_batch.json"
    if not atomic_write_json(batch_path, batch):
        raise OSError(f"could not write image batch: {batch_path}")
    reference_batch_path = project_root / "prompts" / "reference_batch.json"
    if not atomic_write_json(reference_batch_path, reference_batch):
        raise OSError(f"could not write reference batch: {reference_batch_path}")
    contract_path = project_root / "review" / "current_contract.json"
    if not atomic_write_json(contract_path, {
        "schema_version": 1,
        "reference_digest": reference_contract_digest(data),
        "scene_digest": scene_contract_digest(data),
        "approval_instructions": (
            "Review each contact sheet, then copy both its contract digest and the generated artifact digest from "
            "the matching review/*_generation.json into story.json. Any input or file change invalidates approval."
        ),
    }):
        raise OSError(f"could not write review contract: {contract_path}")
    return {"prompts": prompts, "panels_dir": panels_dir, "narration": narration_path,
            "batch": batch_path, "reference_batch": reference_batch_path,
            "references_dir": references_dir, "contract": contract_path}


def make_contact_sheet(data: dict, project_root: Path) -> Path:
    from PIL import Image, ImageDraw, ImageOps

    panels = _scene_outputs(data, project_root)
    missing = [path for path in panels if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"generated story images missing from review sheet: {missing}")
    cell_w, cell_h, label_h = 384, 216, 32
    columns = min(4, len(panels))
    rows = (len(panels) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), "#111827")
    draw = ImageDraw.Draw(sheet)
    for index, panel in enumerate(panels):
        with Image.open(panel) as image:
            thumb = ImageOps.fit(image.convert("RGB"), (cell_w, cell_h))
        x, y = (index % columns) * cell_w, (index // columns) * (cell_h + label_h)
        sheet.paste(thumb, (x, y))
        scene_id = data["scenes"][index].get("id", f"scene-{index + 1}")
        draw.text((x + 8, y + cell_h + 8), f"{index + 1:03d}  {scene_id}", fill="white")
    review_dir = project_root / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / "story_contact_sheet.jpg"
    sheet.save(path, quality=90)
    return path


def make_reference_contact_sheet(data: dict, project_root: Path) -> Path:
    from PIL import Image, ImageDraw, ImageOps

    entries = [
        (f"character_{_slug(item['id'])}.png", f"CHAR  {item['id']}")
        for item in data["continuity"].get("characters", [])
    ] + [
        (f"location_{_slug(item['id'])}.png", f"ENV  {item['id']}")
        for item in data["continuity"].get("locations", [])
    ]
    reference_dir = project_root / "review" / "references"
    available = [(reference_dir / filename, label) for filename, label in entries
                 if (reference_dir / filename).is_file()]
    if not available:
        raise FileNotFoundError("no generated identity or environment references available")
    cell_w, cell_h, label_h = 384, 216, 32
    columns = min(4, len(available))
    rows = (len(available) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), "#111827")
    draw = ImageDraw.Draw(sheet)
    for index, (path, label) in enumerate(available):
        with Image.open(path) as source:
            thumb = ImageOps.fit(source.convert("RGB"), (cell_w, cell_h))
        x, y = (index % columns) * cell_w, (index // columns) * (cell_h + label_h)
        sheet.paste(thumb, (x, y))
        draw.text((x + 8, y + cell_h + 8), label, fill="white")
    path = project_root / "review" / "reference_contact_sheet.jpg"
    sheet.save(path, quality=90)
    return path


def _run_streaming(argv: list[str]) -> dict:
    print("[run] " + subprocess.list2cmdline(argv), flush=True)
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", **popen_kwargs(),
    )
    result: dict = {}
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        if line.startswith("MANGAEASY_RESULT "):
            try:
                result = json.loads(line.partition(" ")[2])
            except ValueError:
                pass
    rc = process.wait()
    if rc:
        raise RuntimeError(f"command failed with exit code {rc}: {subprocess.list2cmdline(argv)}")
    return result


def _latest_video(project_root: Path, project_name: str) -> Path | None:
    output_dir = project_root / "output" / project_name
    candidates = sorted(output_dir.glob("*_full*.mp4"), key=lambda path: path.stat().st_mtime)
    return candidates[-1] if candidates else None


def _reference_outputs(data: dict, project_root: Path) -> list[Path]:
    directory = project_root / "review" / "references"
    return [
        directory / f"{kind}_{_slug(entry['id'])}.png"
        for kind, entries in (
            ("character", data.get("continuity", {}).get("characters", [])),
            ("location", data.get("continuity", {}).get("locations", [])),
        )
        for entry in entries
    ]


def _scene_outputs(data: dict, project_root: Path) -> list[Path]:
    directory = project_root / "content" / "01" / "panels"
    return [directory / f"scene_{index:03d}.png" for index, _scene in enumerate(data.get("scenes", []), 1)]


def _artifact_approval_problems(
    *,
    approved: bool,
    approved_contract_digest: str,
    expected_contract_digest: str,
    approved_artifact_digest: str,
    state_path: Path,
    contract_field: str,
    outputs: list[Path],
    approval_path: str,
    contract_path: str,
    artifact_path: str,
) -> list[dict[str, str]]:
    if approved is not True:
        return [{"path": approval_path, "message": "must be true after reviewing the current contact sheet"}]
    problems: list[dict[str, str]] = []
    if approved_contract_digest != expected_contract_digest:
        problems.append({"path": contract_path, "message": f"must equal current digest {expected_contract_digest}"})
    missing = [path for path in outputs if not path.is_file()]
    if missing:
        problems.extend({"path": artifact_path, "message": f"generated artifact is missing: {path}"} for path in missing)
        return problems
    actual_artifact_digest = _artifact_digest(outputs)
    recorded_contract = _recorded_digest(state_path, contract_field)
    recorded_artifact = _recorded_digest(state_path, "artifact_digest")
    if recorded_contract != expected_contract_digest or recorded_artifact != actual_artifact_digest:
        problems.append({
            "path": artifact_path,
            "message": "generation record is stale or does not match the current files; regenerate and review them",
        })
    elif approved_artifact_digest != actual_artifact_digest:
        problems.append({
            "path": artifact_path,
            "message": f"must equal current artifact digest {actual_artifact_digest}",
        })
    return problems


def reference_approval_problems(data: dict, project_root: Path) -> list[dict[str, str]]:
    review = data.get("review", {})
    return _artifact_approval_problems(
        approved=review.get("references_approved") is True,
        approved_contract_digest=review.get("approved_reference_digest", ""),
        expected_contract_digest=reference_contract_digest(data),
        approved_artifact_digest=review.get("approved_reference_artifact_digest", ""),
        state_path=project_root / "review" / "reference_generation.json",
        contract_field="reference_digest",
        outputs=_reference_outputs(data, project_root),
        approval_path="review.references_approved",
        contract_path="review.approved_reference_digest",
        artifact_path="review.approved_reference_artifact_digest",
    )


def scene_approval_problems(data: dict, project_root: Path) -> list[dict[str, str]]:
    review = data.get("review", {})
    outputs = _scene_outputs(data, project_root)
    problems = _artifact_approval_problems(
        approved=review.get("images_approved") is True,
        approved_contract_digest=review.get("approved_scene_digest", ""),
        expected_contract_digest=scene_contract_digest(data),
        approved_artifact_digest=review.get("approved_scene_artifact_digest", ""),
        state_path=project_root / "review" / "scene_generation.json",
        contract_field="scene_digest",
        outputs=outputs,
        approval_path="review.images_approved",
        contract_path="review.approved_scene_digest",
        artifact_path="review.approved_scene_artifact_digest",
    )
    if review.get("images_approved") is True and all(path.is_file() for path in outputs):
        reference_state = project_root / "review" / "reference_generation.json"
        scene_state = project_root / "review" / "scene_generation.json"
        current_reference_artifact = _recorded_digest(reference_state, "artifact_digest")
        scene_reference_artifact = _recorded_digest(scene_state, "reference_artifact_digest")
        if not current_reference_artifact or scene_reference_artifact != current_reference_artifact:
            problems.append({
                "path": "review.approved_scene_artifact_digest",
                "message": (
                    "scene generation is bound to stale identity/environment references; "
                    "regenerate and review the scene frames"
                ),
            })
    return problems


def video_preflight_problems(data: dict, project_root: Path) -> list[dict[str, str]]:
    """Return concise blockers before entering the nested TTS/video pipeline."""
    return reference_approval_problems(data, project_root) + scene_approval_problems(data, project_root)


def _resolve_story_voice(production: dict, speaker_wav: Path | None) -> tuple[str, Path | None, str | None]:
    """Resolve the actual TTS path once and fingerprint any IndexTTS voice reference."""
    from mangaeasy.defaults import default_speaker_wav
    from mangaeasy.video_pipeline.run_pipeline import resolve_tts_engine

    # The nested video command reports its own selection. Avoid printing a
    # second copy before JSON dry-run/gate output while making the same choice.
    with redirect_stdout(io.StringIO()):
        resolved_tts = resolve_tts_engine(str(production["tts"]), speaker_wav)
    if resolved_tts == "kokoro":
        return resolved_tts, None, None
    if resolved_tts != "indextts":  # pragma: no cover - guarded by the nested resolver
        raise ValueError(f"unsupported resolved TTS engine: {resolved_tts}")
    effective_speaker = (speaker_wav or default_speaker_wav()).expanduser().resolve()
    if not effective_speaker.is_file():
        raise FileNotFoundError(f"IndexTTS speaker reference WAV not found: {effective_speaker}")
    return resolved_tts, effective_speaker, _sha256_file(effective_speaker)


def _voice_provenance_from_state(state: dict) -> tuple[str | None, str | None, list[dict[str, str]]]:
    """Revalidate the voice input recorded for a rendered video."""
    problems: list[dict[str, str]] = []
    resolved_tts = state.get("resolved_tts")
    if resolved_tts not in {"kokoro", "indextts"}:
        problems.append({
            "path": "review.approved_video_sha256",
            "message": "video generation record has no valid resolved TTS provenance; render and review again",
        })
        return None, None, problems

    speaker_value = state.get("speaker_wav")
    recorded_sha256 = state.get("speaker_wav_sha256")
    if resolved_tts == "kokoro":
        if speaker_value is not None or recorded_sha256 is not None:
            problems.append({
                "path": "review.approved_video_sha256",
                "message": "video generation record has inconsistent Kokoro voice provenance; render and review again",
            })
        return resolved_tts, None, problems

    if not isinstance(speaker_value, str) or not re.fullmatch(r"[0-9a-f]{64}", str(recorded_sha256 or "")):
        problems.append({
            "path": "review.approved_video_sha256",
            "message": "video generation record has incomplete IndexTTS speaker provenance; render and review again",
        })
        return resolved_tts, None, problems
    try:
        speaker_path = Path(speaker_value).expanduser().resolve()
    except (OSError, ValueError):
        speaker_path = Path()
    if not speaker_path.is_file():
        problems.append({
            "path": "review.approved_video_sha256",
            "message": f"recorded IndexTTS speaker reference is missing: {speaker_path}",
        })
        return resolved_tts, None, problems
    actual_sha256 = _sha256_file(speaker_path)
    if recorded_sha256 != actual_sha256:
        problems.append({
            "path": "review.approved_video_sha256",
            "message": "IndexTTS speaker reference changed after render; render and review the video again",
        })
    return resolved_tts, actual_sha256, problems


def approved_video(data: dict, project_root: Path) -> tuple[Path | None, list[dict[str, str]]]:
    """Resolve and verify the exact rendered file approved for publication."""
    state_path = project_root / "review" / "video_generation.json"
    state = _load_json_object(state_path)
    if not state:
        return None, [{
            "path": "review.approved_video_sha256",
            "message": "video generation record is missing or invalid; render and review the video again",
        }]
    if not isinstance(state, dict) or not isinstance(state.get("video"), str):
        return None, [{
            "path": "review.approved_video_sha256",
            "message": "video generation record has no valid video path",
        }]
    video = Path(state["video"]).resolve()
    output_root = (project_root / "output").resolve()
    try:
        video.relative_to(output_root)
    except ValueError:
        return None, [{
            "path": "review.approved_video_sha256",
            "message": "recorded video is outside this project's output directory",
        }]
    if not video.is_file():
        return None, [{"path": "review.approved_video_sha256", "message": f"reviewed video is missing: {video}"}]
    problems = video_preflight_problems(data, project_root)
    resolved_tts, speaker_wav_sha256, voice_problems = _voice_provenance_from_state(state)
    problems.extend(voice_problems)
    expected_narration_contract = narration_contract_digest(
        data,
        resolved_tts=resolved_tts,
        speaker_wav_sha256=speaker_wav_sha256,
    )
    expected_video_contract = video_contract_digest(
        data,
        resolved_tts=resolved_tts,
        speaker_wav_sha256=speaker_wav_sha256,
    )
    actual_sha256 = _sha256_file(video)
    scene_artifact_digest = _recorded_digest(
        project_root / "review" / "scene_generation.json", "artifact_digest"
    )
    if (
        state.get("schema_version") != 2
        or state.get("scene_digest") != scene_contract_digest(data)
        or state.get("scene_artifact_digest") != scene_artifact_digest
        or state.get("narration_contract_digest") != expected_narration_contract
        or state.get("video_contract_digest") != expected_video_contract
        or state.get("sha256") != actual_sha256
    ):
        problems.append({
            "path": "review.approved_video_sha256",
            "message": "video generation record is stale or the rendered file changed; render and review it again",
        })
    elif data.get("review", {}).get("approved_video_sha256") != actual_sha256:
        problems.append({
            "path": "review.approved_video_sha256",
            "message": f"must equal the reviewed video SHA-256 {actual_sha256}",
        })
    return video, problems


def init_main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} story-init")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--story")
    parser.add_argument("--story-file", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    source_story = _read_text_arg(args.story, args.story_file, parser)
    root = args.project_root.resolve()
    path = root / MANIFEST_NAME
    if path.exists() and not args.force:
        print(f"[error] manifest already exists: {path} (pass --force to replace)")
        return 1
    if path.exists():
        archive_before_overwrite(path)
    root.mkdir(parents=True, exist_ok=True)
    if not atomic_write_json(path, new_manifest(args.title.strip(), source_story)):
        return 1
    payload = {"ok": True, "manifest": str(path), "next": f"Complete continuity and scenes, then run: {CLI_NAME} story-check --manifest \"{path}\" --json"}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Created: {path}\n{payload['next']}")
    return 0


def check_main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} story-check")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--project-root", type=Path)
    parser.add_argument("--for-publish", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    path = (args.manifest or args.project_root / MANIFEST_NAME).resolve()
    data = load_manifest(path)
    problems = validate_manifest(data, for_publish=args.for_publish)
    video_sha256 = None
    if args.for_publish and not problems:
        video, artifact_problems = approved_video(data, path.parent)
        problems.extend({"severity": "error", **problem} for problem in artifact_problems)
        video_sha256 = _sha256_file(video) if video and video.is_file() else None
    report = {
        "ok": not problems,
        "manifest": str(path),
        "scene_count": len(data.get("scenes", [])),
        "reference_digest": reference_contract_digest(data),
        "reference_artifact_digest": _recorded_digest(
            path.parent / "review" / "reference_generation.json", "artifact_digest"
        ),
        "scene_digest": scene_contract_digest(data),
        "scene_artifact_digest": _recorded_digest(
            path.parent / "review" / "scene_generation.json", "artifact_digest"
        ),
        "narration_contract_digest": _recorded_digest(
            path.parent / "review" / "video_generation.json", "narration_contract_digest"
        ),
        "video_contract_digest": _recorded_digest(
            path.parent / "review" / "video_generation.json", "video_contract_digest"
        ),
        "video_sha256": video_sha256,
        "problems": problems,
    }
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("Story manifest: " + ("OK" if report["ok"] else f"{len(problems)} problem(s)"))
        print(f"  reference digest: {report['reference_digest']}")
        print(f"  scene digest: {report['scene_digest']}")
        for problem in problems:
            print(f"  [{problem['severity']}] {problem['path']}: {problem['message']}")
    return 0 if report["ok"] else 1


def build_main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} story-build")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--project-root", type=Path)
    parser.add_argument("--stage", choices=("prepare", "images", "video", "publish", "all"), default="all")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--speaker-wav", type=Path)
    parser.add_argument("--privacy", choices=("private", "unlisted", "public"))
    args = parser.parse_args()
    manifest = (args.manifest or args.project_root / MANIFEST_NAME).resolve()
    project_root = manifest.parent
    data = load_manifest(manifest)
    problems = validate_manifest(data, for_publish=args.stage == "publish")
    if problems:
        print(json.dumps({"ok": False, "manifest": str(manifest), "problems": problems}, ensure_ascii=False))
        return 1
    assets = materialize(data, project_root) if not args.dry_run else {
        "prompts": [project_root / "prompts" / f"scene_{i:03d}.txt" for i in range(1, len(data["scenes"]) + 1)],
        "panels_dir": project_root / "content" / "01" / "panels",
        "narration": project_root / "content" / "01" / "narration.json",
        "batch": project_root / "prompts" / "image_batch.json",
        "reference_batch": project_root / "prompts" / "reference_batch.json",
        "references_dir": project_root / "review" / "references",
        "contract": project_root / "review" / "current_contract.json",
    }
    # "all" intentionally stops at local video production. YouTube upload is
    # an explicit rights/QA-gated stage.
    stages = {args.stage} if args.stage != "all" else {"prepare", "images", "video"}
    production = data["production"]
    results = []
    if "images" in stages:
        reference_entries = json.loads(Path(assets["reference_batch"]).read_text(encoding="utf-8")) if not args.dry_run else [
            {
                "prompt_file": str(project_root / "prompts" / "references" / f"{kind}_{_slug(entry['id'])}.txt"),
                "output": str(assets["references_dir"] / f"{kind}_{_slug(entry['id'])}.png"),
                "width": production["width"], "height": production["height"],
                "steps": production["image_steps"],
                "seed": _scene_seed(data, {"id": f"reference-{kind}-{entry['id']}"}, index),
            }
            for kind, entries in (("character", data["continuity"].get("characters", [])),
                                  ("location", data["continuity"].get("locations", [])))
            for index, entry in enumerate(entries, start=1)
        ]
        reference_state_path = project_root / "review" / "reference_generation.json"
        current_reference_digest = reference_contract_digest(data)
        reference_contract_changed = (
            _recorded_digest(reference_state_path, "reference_digest") != current_reference_digest
        )
        reference_pending = []
        for entry in reference_entries:
            output = Path(entry["output"])
            if output.exists() and not args.overwrite and not reference_contract_changed:
                continue
            reference_pending.append(entry)
        if reference_pending:
            pending_path = project_root / "prompts" / "reference_batch.pending.json"
            if not args.dry_run:
                _invalidate_review(data, manifest, "references")
                for entry in reference_pending:
                    output = Path(entry["output"])
                    if output.exists():
                        archive_before_overwrite(output)
                if not atomic_write_json(pending_path, reference_pending):
                    raise OSError(f"could not write pending reference batch: {pending_path}")
            command = cli_command(
                "zimage", "--batch-manifest", str(pending_path),
                "--strategy", production["image_strategy"],
            )
            if args.dry_run:
                print(json.dumps({
                    "ok": True, "dry_run": True, "manifest": str(manifest), "commands": [command],
                    "gate": "Approve generated identity/environment references before scene generation.",
                    "reference_digest": current_reference_digest,
                    "publish": "explicit --stage publish only",
                }, ensure_ascii=False))
                return 0
            results.append(_run_streaming(command))
            reference_artifact_digest = _artifact_digest(
                [Path(entry["output"]) for entry in reference_entries]
            )
            if not atomic_write_json(reference_state_path, {
                "schema_version": 1,
                "reference_digest": current_reference_digest,
                "artifact_digest": reference_artifact_digest,
                "outputs": [entry["output"] for entry in reference_entries],
            }):
                raise OSError(f"could not write reference generation state: {reference_state_path}")
        if reference_entries and not args.dry_run:
            reference_sheet = make_reference_contact_sheet(data, project_root)
            print(f"[review] inspect {reference_sheet}")
            reference_gate = reference_approval_problems(data, project_root)
            if reference_gate:
                artifact_digest = _recorded_digest(reference_state_path, "artifact_digest") or "<regenerate>"
                print(
                    "[review required] Approve the identity/environment sheet, then set "
                    f"review.references_approved=true and review.approved_reference_digest="
                    f"'{current_reference_digest}' and review.approved_reference_artifact_digest="
                    f"'{artifact_digest}'."
                )
                return 3

        pending = []
        scene_state_path = project_root / "review" / "scene_generation.json"
        current_scene_digest = scene_contract_digest(data)
        current_reference_artifact_digest = _recorded_digest(reference_state_path, "artifact_digest")
        scene_contract_changed = (
            _recorded_digest(scene_state_path, "scene_digest") != current_scene_digest
            or _recorded_digest(scene_state_path, "reference_artifact_digest")
            != current_reference_artifact_digest
        )
        batch_entries = json.loads(Path(assets["batch"]).read_text(encoding="utf-8")) if not args.dry_run else [
            _scene_batch_entry(
                data,
                scene,
                i,
                path,
                assets["panels_dir"] / f"scene_{i:03d}.png",
                assets["references_dir"],
            )
            for i, (scene, path) in enumerate(zip(data["scenes"], assets["prompts"], strict=True), start=1)
        ]
        previous_scheduled = False
        for entry in batch_entries:
            output = Path(entry["output"])
            must_follow_regenerated_init = (
                entry.get("generation_mode") == "continue-previous" and previous_scheduled
            )
            needs_generation = (
                not output.exists() or args.overwrite or scene_contract_changed or must_follow_regenerated_init
            )
            if not needs_generation:
                previous_scheduled = False
                continue
            pending.append(entry)
            previous_scheduled = True
        if pending:
            pending_path = project_root / "prompts" / "image_batch.pending.json"
            if not args.dry_run:
                _invalidate_review(data, manifest, "images")
                for entry in pending:
                    output = Path(entry["output"])
                    if output.exists():
                        archive_before_overwrite(output)
                if not atomic_write_json(pending_path, pending):
                    raise OSError(f"could not write pending image batch: {pending_path}")
            command = cli_command(
                "zimage", "--batch-manifest", str(pending_path),
                "--strategy", production["image_strategy"],
            )
            if args.dry_run:
                print(json.dumps({"ok": True, "dry_run": True, "manifest": str(manifest),
                                  "commands": [command], "scene_digest": current_scene_digest,
                                  "generation_plan": [
                                      {
                                          "scene_id": entry.get("scene_id"),
                                          "mode": entry.get("generation_mode", "text-to-image"),
                                          "init_image": entry.get("init_image"),
                                          "strength": entry.get("strength"),
                                      }
                                      for entry in pending
                                  ],
                                  "publish": "explicit --stage publish only"},
                                 ensure_ascii=False))
                return 0
            results.append(_run_streaming(command))
            scene_artifact_digest = _artifact_digest(
                [Path(entry["output"]) for entry in batch_entries]
            )
            if not atomic_write_json(scene_state_path, {
                "schema_version": 1,
                "scene_digest": current_scene_digest,
                "reference_artifact_digest": current_reference_artifact_digest,
                "artifact_digest": scene_artifact_digest,
                "outputs": [entry["output"] for entry in batch_entries],
                "generation_plan": [
                    {
                        "scene_id": entry.get("scene_id"),
                        "mode": entry.get("generation_mode", "text-to-image"),
                        "init_image": entry.get("init_image"),
                        "strength": entry.get("strength"),
                        "seed": entry.get("seed"),
                    }
                    for entry in batch_entries
                ],
            }):
                raise OSError(f"could not write scene generation state: {scene_state_path}")
        if not args.dry_run:
            contact_sheet = make_contact_sheet(data, project_root)
            print(f"[review] inspect {contact_sheet}")
            scene_gate = scene_approval_problems(data, project_root)
            if scene_gate:
                artifact_digest = _recorded_digest(scene_state_path, "artifact_digest") or "<regenerate>"
                print(
                    "[review required] Approve the ordered story contact sheet, then set "
                    f"review.images_approved=true and review.approved_scene_digest='{current_scene_digest}' "
                    f"and review.approved_scene_artifact_digest='{artifact_digest}'."
                )
                return 3
    project_name = _slug(data["title"])
    if "video" in stages:
        preflight = video_preflight_problems(data, project_root)
        if preflight:
            print(json.dumps({
                "ok": False,
                "gate": "video-preflight",
                "manifest": str(manifest),
                "problems": preflight,
            }, ensure_ascii=False))
            return 3
        try:
            resolved_tts, effective_speaker_wav, speaker_wav_sha256 = _resolve_story_voice(
                production, args.speaker_wav,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(json.dumps({
                "ok": False,
                "gate": "video-voice-preflight",
                "manifest": str(manifest),
                "problems": [{"path": "production.tts", "message": str(exc)}],
            }, ensure_ascii=False))
            return 1
        current_narration_contract = narration_contract_digest(
            data,
            resolved_tts=resolved_tts,
            speaker_wav_sha256=speaker_wav_sha256,
        )
        current_video_contract = video_contract_digest(
            data,
            resolved_tts=resolved_tts,
            speaker_wav_sha256=speaker_wav_sha256,
        )
        current_scene_artifact = _recorded_digest(
            project_root / "review" / "scene_generation.json", "artifact_digest"
        )
        video_state_path = project_root / "review" / "video_generation.json"
        previous_video_state = _load_json_object(video_state_path)
        existing_video = _latest_video(project_root, project_name)
        existing_video_sha256 = _sha256_file(existing_video) if existing_video and existing_video.is_file() else None
        narration_inputs_changed = (
            args.overwrite
            or previous_video_state.get("narration_contract_digest") != current_narration_contract
        )
        video_inputs_changed = (
            args.overwrite
            or narration_inputs_changed
            or previous_video_state.get("video_contract_digest") != current_video_contract
            or previous_video_state.get("scene_artifact_digest") != current_scene_artifact
            or previous_video_state.get("sha256") != existing_video_sha256
        )
        command = cli_command(
            "video", "--project-root", str(project_root / "content"),
            "--audio-root", str(project_root / "audio"),
            "--output-root", str(project_root / "output"),
            "--work-dir", str(project_root / "work"), "--project-name", project_name,
            "--items", "01", "--tts", resolved_tts, "--build-long-video",
            "--normalize-audio", "--no-background-music", "--fps", str(production["fps"]),
        )
        if effective_speaker_wav:
            command += ["--speaker-wav", str(effective_speaker_wav)]
        if narration_inputs_changed:
            command.append("--overwrite-audio")
        if video_inputs_changed:
            command.append("--overwrite-video")
        if args.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "manifest": str(manifest),
                              "commands": [command], "resolved_tts": resolved_tts,
                              "narration_contract_digest": current_narration_contract,
                              "video_contract_digest": current_video_contract,
                              "publish": "explicit --stage publish only"},
                             ensure_ascii=False))
            return 0
        _invalidate_review(data, manifest, "video")
        results.append(_run_streaming(command))
        if effective_speaker_wav and _sha256_file(effective_speaker_wav) != speaker_wav_sha256:
            print("[error] IndexTTS speaker reference changed during video generation; rerun the video stage")
            return 1
        rendered_video = _latest_video(project_root, project_name)
        if rendered_video is None:
            print("[error] video pipeline completed without a discoverable long video")
            return 1
        video_sha256 = _sha256_file(rendered_video)
        if not atomic_write_json(video_state_path, {
            "schema_version": 2,
            "video": str(rendered_video.resolve()),
            "sha256": video_sha256,
            "scene_digest": scene_contract_digest(data),
            "scene_artifact_digest": current_scene_artifact,
            "narration_contract_digest": current_narration_contract,
            "video_contract_digest": current_video_contract,
            "resolved_tts": resolved_tts,
            "speaker_wav": str(effective_speaker_wav) if effective_speaker_wav else None,
            "speaker_wav_sha256": speaker_wav_sha256,
        }):
            raise OSError(f"could not write video generation state: {video_state_path}")
        print(
            "[review required] Inspect the complete rendered video, then set "
            f"review.video_approved=true and review.approved_video_sha256='{video_sha256}'."
        )
        return 3
    video = _latest_video(project_root, project_name)
    if "publish" in stages:
        state_path = project_root / "publish.json"
        if state_path.exists():
            print(f"[error] this project is already recorded as published: {state_path}")
            return 1
        video, publish_problems = approved_video(data, project_root)
        if publish_problems or video is None:
            print(json.dumps({
                "ok": False,
                "gate": "publish-artifact-preflight",
                "manifest": str(manifest),
                "problems": publish_problems,
            }, ensure_ascii=False))
            return 1
        youtube = data.get("youtube", {})
        command = cli_command(
            "youtube-upload", "--profile", str(youtube.get("profile", "default")),
            "--video", str(video),
            "--title", str(youtube.get("title") or data["title"]),
            "--description", str(youtube.get("description", "")),
            "--tags", ",".join(youtube.get("tags", [])),
            "--privacy", args.privacy or youtube.get("privacy", "private"),
            "--contains-synthetic-media", "--json",
        )
        if args.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "manifest": str(manifest),
                              "commands": [command]}, ensure_ascii=False))
            return 0
        upload_result = _run_streaming(command)
        if not atomic_write_json(
            state_path,
            {"schema_version": 1, "video": str(video), "youtube": upload_result},
        ):
            raise OSError(
                f"upload succeeded but idempotency state could not be saved: {state_path}; "
                "do not retry until the published video is reconciled"
            )
        results.append(upload_result)
    if video is None:
        video = _latest_video(project_root, project_name)
    emit_result(manifest=manifest, video=video, results=results)
    return 0
