"""Versioned Song Video manifests and explicit, resumable production stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import cli_command, popen_kwargs
from mangaeasy.song.lyrics import (
    DEFAULT_LYRICS_STYLE,
    lyric_lines,
    resolved_lyrics_style,
    write_ass,
    write_srt,
)
from mangaeasy.utils import archive_before_overwrite, atomic_write_json, emit_result
from mangaeasy.video_pipeline.ffmpeg_tools import choose_h264_encoder, h264_encoder_args
from mangaeasy.youtube.store import validate_profile

SCHEMA_VERSION = 1
MANIFEST_NAME = "song.json"
DEFAULT_VISUAL_PROMPT = (
    "minimalistic sky, serene gradient from deep blue to warm dawn, sparse soft clouds, "
    "clean cinematic negative space, subtle atmospheric glow, no text, 16:9"
)
BUNDLED_FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "song"


def _seed(title: str) -> int:
    return int.from_bytes(hashlib.sha256(title.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF


def _exclusive_text(text: str | None, path: Path | None, parser: argparse.ArgumentParser) -> str:
    if bool(text) == bool(path):
        parser.error("pass exactly one of --lyrics or --lyrics-file")
    value = text if text is not None else path.read_text(encoding="utf-8")
    if not value.strip():
        parser.error("lyrics must not be empty")
    return value.strip()


def new_manifest(title: str, lyrics: str, music_prompt: str | None, audio: str | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "song-video",
        "title": title,
        "lyrics": lyrics,
        "language": "en",
        "audio": {
            "source": audio,
            "generation_prompt": music_prompt or "emotional cinematic pop, clear lead vocal, memorable chorus",
            "duration": -1,
            "bpm": None,
            "seed": _seed(title),
        },
        # Runtime selection is deliberately not user-configurable: the
        # installed adapter always uses the revision-pinned local HTDemucs-ft
        # snapshot. Keep the name only as honest manifest provenance.
        "separation": {"model": "htdemucs-ft", "device": "auto"},
        "alignment": {
            "device": "auto",
            "minimum_confidence": 0.72,
            "approved": False,
            "approved_digest": "",
        },
        "visual": {"prompt": DEFAULT_VISUAL_PROMPT, "seed": _seed(title + "-visual"), "width": 1280, "height": 720},
        "render": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "lyrics_style": dict(DEFAULT_LYRICS_STYLE),
        },
        "rights": {
            "lyrics_rights_confirmed": False,
            "audio_rights_confirmed": False,
            "voice_consent_confirmed": False,
            "synthetic_media_disclosure_acknowledged": False,
            "provenance_notes": "",
        },
        "review": {
            "video_approved": False,
            "approved_video_sha256": "",
        },
        "youtube": {
            "profile": "default",
            "title": title,
            "description": "",
            "tags": ["lyrics", "music", "ai music"],
            "privacy": "private",
        },
    }


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"song manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("song manifest must contain one JSON object")
    return data


def _stable_digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _generation_contract(data: dict) -> str:
    audio = data.get("audio", {})
    return _stable_digest({
        "lyrics": data.get("lyrics"),
        "language": data.get("language"),
        "prompt": audio.get("generation_prompt"),
        "duration": audio.get("duration"),
        "bpm": audio.get("bpm"),
        "seed": audio.get("seed"),
    })


def _visual_contract(data: dict) -> str:
    return _stable_digest(data.get("visual", {}))


def _alignment_contract(data: dict, vocals_sha256: str | None) -> str:
    alignment = data.get("alignment", {})
    return _stable_digest({
        "lyrics": data.get("lyrics"),
        "language": data.get("language"),
        "minimum_confidence": alignment.get("minimum_confidence"),
        "vocals_sha256": vocals_sha256,
    })


def _alignment_artifact_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _stable_digest(value) if isinstance(value, dict) else None


def _invalidate_alignment(data: dict, manifest: Path) -> None:
    data["alignment"].update({"approved": False, "approved_digest": ""})
    data["review"].update({"video_approved": False, "approved_video_sha256": ""})
    if not atomic_write_json(manifest, data):
        raise OSError(f"could not invalidate alignment/video review state in {manifest}")


def _invalidate_video(data: dict, manifest: Path) -> None:
    data["review"].update({"video_approved": False, "approved_video_sha256": ""})
    if not atomic_write_json(manifest, data):
        raise OSError(f"could not invalidate video review state in {manifest}")


def validate_manifest(data: dict, *, for_publish: bool = False) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []

    def add(severity: str, path: str, message: str) -> None:
        problems.append({"severity": severity, "path": path, "message": message})

    if (
        isinstance(data.get("schema_version"), bool)
        or not isinstance(data.get("schema_version"), int)
        or data.get("schema_version") != SCHEMA_VERSION
    ):
        add("error", "schema_version", f"must equal {SCHEMA_VERSION}")
    if data.get("mode") != "song-video":
        add("error", "mode", "must equal 'song-video'")
    for key in ("title", "lyrics"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            add("error", key, "must be a non-empty string")
    if data.get("language") != "en":
        add(
            "error", "language",
            "the production-pinned offline alignment bundle currently supports 'en' only",
        )
    audio = data.get("audio")
    if not isinstance(audio, dict):
        add("error", "audio", "must be an object")
        audio = {}
    source = audio.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        add("error", "audio.source", "must be null or a non-empty path string")
    generation_prompt = audio.get("generation_prompt")
    if not source and (not isinstance(generation_prompt, str) or not generation_prompt.strip()):
        add("error", "audio.generation_prompt", "is required when no source audio is supplied")
    elif generation_prompt is not None and not isinstance(generation_prompt, str):
        add("error", "audio.generation_prompt", "must be a string")
    if not isinstance(audio.get("seed"), int) or isinstance(audio.get("seed"), bool) or audio.get("seed", -1) < 0:
        add("error", "audio.seed", "must be a non-negative integer")
    duration = audio.get("duration")
    if not _finite_number(duration) or (duration != -1 and not 1 <= duration <= 3600):
        add("error", "audio.duration", "must be -1 (backend default) or a finite number from 1 to 3600 seconds")
    bpm = audio.get("bpm")
    if bpm is not None and (
        isinstance(bpm, bool) or not isinstance(bpm, int) or not 20 <= bpm <= 300
    ):
        add("error", "audio.bpm", "must be null or an integer from 20 to 300")
    visual = data.get("visual")
    if not isinstance(visual, dict):
        add("error", "visual", "must be an object")
        visual = {}
    prompt = visual.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        add("error", "visual.prompt", "must be a non-empty image prompt")
    elif "minimalistic sky" not in prompt.casefold():
        add("warning", "visual.prompt", "does not use the recommended 'minimalistic sky' motif")
    for key in ("width", "height"):
        value = visual.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 64 <= value <= 8192:
            add("error", f"visual.{key}", "must be an integer from 64 to 8192")
    visual_seed = visual.get("seed")
    if isinstance(visual_seed, bool) or not isinstance(visual_seed, int) or visual_seed < 0:
        add("error", "visual.seed", "must be a non-negative integer")
    for section in ("render", "youtube", "rights", "alignment", "separation", "review"):
        if not isinstance(data.get(section), dict):
            add("error", section, "must be an object")
    render = data.get("render", {})
    render = render if isinstance(render, dict) else {}
    for key in ("width", "height"):
        value = render.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 64 <= value <= 8192:
            add("error", f"render.{key}", "must be an integer from 64 to 8192")
    fps = render.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 120:
        add("error", "render.fps", "must be an integer from 1 to 120")
    lyrics_style = render.get("lyrics_style") if isinstance(render, dict) else None
    if lyrics_style is None:
        add("warning", "render.lyrics_style", "is absent; the edo-sky-fade-v1 defaults will be used")
        lyrics_style = {}
    elif not isinstance(lyrics_style, dict):
        add("error", "render.lyrics_style", "must be an object")
        lyrics_style = {}
    style = resolved_lyrics_style(lyrics_style)
    if not isinstance(style.get("font_name"), str) or not str(style["font_name"]).strip():
        add("error", "render.lyrics_style.font_name", "must be a non-empty string")
    elif "," in str(style["font_name"]):
        add("error", "render.lyrics_style.font_name", "must not contain a comma")
    font_file = style.get("font_file")
    if font_file is not None and (not isinstance(font_file, str) or not font_file.strip()):
        add("error", "render.lyrics_style.font_file", "must be null or a non-empty path string")
    elif isinstance(font_file, str) and font_file.startswith("@bundled/") and (
        Path(font_file.removeprefix("@bundled/")).name != font_file.removeprefix("@bundled/")
    ):
        add("error", "render.lyrics_style.font_file", "bundled font paths must contain one file name")
    elif isinstance(font_file, str) and font_file.startswith("@bundled/") and not (
        BUNDLED_FONTS_DIR / font_file.removeprefix("@bundled/")
    ).is_file():
        add("error", "render.lyrics_style.font_file", "does not resolve to a packaged font")
    elif isinstance(font_file, str) and Path(font_file).suffix.casefold() not in {".ttf", ".otf", ".ttc"}:
        add("error", "render.lyrics_style.font_file", "must point to a .ttf, .otf, or .ttc font")
    elif font_file is None and str(style.get("font_name")).casefold() == "edo sz":
        add(
            "warning",
            "render.lyrics_style.font_file",
            "is not set; install a licensed Edo SZ font on the render host or provide its path",
        )
    numeric_ranges = {
        "font_size_ratio": (0.02, 0.2),
        "outline": (0.0, 10.0),
        "shadow": (0.0, 10.0),
        "margin_vertical_ratio": (0.0, 0.5),
    }
    for key, (minimum, maximum) in numeric_ranges.items():
        value = style.get(key)
        if not _finite_number(value) or not minimum <= value <= maximum:
            add("error", f"render.lyrics_style.{key}", f"must be between {minimum:g} and {maximum:g}")
    for key in ("fade_in_ms", "fade_out_ms"):
        value = style.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5000:
            add("error", f"render.lyrics_style.{key}", "must be an integer from 0 to 5000")
    alignment = style.get("alignment")
    if isinstance(alignment, bool) or not isinstance(alignment, int) or not 1 <= alignment <= 9:
        add("error", "render.lyrics_style.alignment", "must be an ASS alignment integer from 1 to 9")
    alignment_state = data.get("alignment") if isinstance(data.get("alignment"), dict) else {}
    alignment_device = alignment_state.get("device")
    if not isinstance(alignment_device, str) or alignment_device not in {"auto", "cuda", "cpu"}:
        add("error", "alignment.device", "must be auto, cuda, or cpu")
    minimum_confidence = alignment_state.get("minimum_confidence")
    if (
        isinstance(minimum_confidence, bool)
        or not isinstance(minimum_confidence, (int, float))
        or not 0 <= minimum_confidence <= 1
    ):
        add("error", "alignment.minimum_confidence", "must be a number from 0 to 1")
    if not isinstance(alignment_state.get("approved"), bool):
        add("error", "alignment.approved", "must be true or false")
    if not isinstance(alignment_state.get("approved_digest"), str):
        add("error", "alignment.approved_digest", "must be a string")
    elif alignment_state.get("approved") is True and not re.fullmatch(
        r"[0-9a-f]{64}", alignment_state.get("approved_digest", "")
    ):
        add("error", "alignment.approved_digest", "must be the 64-character digest of reviewed timed_lyrics.json")
    separation = data.get("separation") if isinstance(data.get("separation"), dict) else {}
    if separation.get("model") != "htdemucs-ft":
        add("error", "separation.model", "must equal the provisioned 'htdemucs-ft' model")
    separation_device = separation.get("device")
    if not isinstance(separation_device, str) or separation_device not in {"auto", "cuda", "cpu"}:
        add("error", "separation.device", "must be auto, cuda, or cpu")
    review = data.get("review") if isinstance(data.get("review"), dict) else {}
    if not isinstance(review.get("video_approved"), bool):
        add("error", "review.video_approved", "must be true or false")
    if not isinstance(review.get("approved_video_sha256"), str):
        add("error", "review.approved_video_sha256", "must be a string")
    elif review.get("video_approved") is True and not re.fullmatch(
        r"[0-9a-f]{64}", review.get("approved_video_sha256", "")
    ):
        add("error", "review.approved_video_sha256", "must be the SHA-256 of the reviewed video")
    youtube_value = data.get("youtube")
    youtube = youtube_value if isinstance(youtube_value, dict) else {}
    profile = youtube.get("profile", "default") if isinstance(youtube_value, dict) else None
    if not isinstance(profile, str):
        add("error", "youtube.profile", "must be a safe profile name string")
    else:
        try:
            validate_profile(profile)
        except ValueError as exc:
            add("error", "youtube.profile", str(exc))
    privacy = youtube.get("privacy", "private")
    if not isinstance(privacy, str) or privacy not in {"private", "unlisted", "public"}:
        add("error", "youtube.privacy", "must be private, unlisted, or public")
    for key in ("title", "description"):
        value = youtube.get(key)
        if not isinstance(value, str):
            add("error", f"youtube.{key}", "must be a string")
        elif key == "title" and not value.strip():
            add("error", "youtube.title", "must be a non-empty string")
    tags = youtube.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        add("error", "youtube.tags", "must be an array of non-empty strings")
    rights_value = data.get("rights")
    rights = rights_value if isinstance(rights_value, dict) else {}
    right_keys = (
        "lyrics_rights_confirmed", "audio_rights_confirmed", "voice_consent_confirmed",
        "synthetic_media_disclosure_acknowledged",
    )
    for key in right_keys:
        if not isinstance(rights.get(key), bool):
            add("error", f"rights.{key}", "must be true or false")
    if not isinstance(rights.get("provenance_notes"), str):
        add("error", "rights.provenance_notes", "must be a string")
    missing_rights = [key for key in right_keys if rights.get(key) is not True]
    if for_publish and missing_rights:
        add("error", "rights", "publishing requires true confirmations: " + ", ".join(missing_rights))
    elif missing_rights:
        add("warning", "rights", "complete confirmations before publishing: " + ", ".join(missing_rights))
    if for_publish and alignment_state.get("approved") is not True:
        add("error", "alignment.approved", "must be true after reviewing canonical lyric timing")
    if for_publish and review.get("video_approved") is not True:
        add("error", "review.video_approved", "must be true after reviewing the rendered video")
    return problems


def _resolve_source(root: Path, source: str | None) -> Path:
    if not source:
        return root / "audio" / "song.wav"
    path = Path(source).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _run_streaming(argv: list[str], *, accepted_codes: tuple[int, ...] = (0,)) -> dict:
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
    if rc not in accepted_codes:
        raise RuntimeError(f"command failed with exit code {rc}: {subprocess.list2cmdline(argv)}")
    result["exit_code"] = rc
    return result


def _ass_filter_path(path: Path) -> str:
    value = path.resolve().as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{value}'"


def _resolve_font_file(root: Path, value: object) -> Path | None:
    if not value:
        return None
    if isinstance(value, str) and value.startswith("@bundled/"):
        name = value.removeprefix("@bundled/")
        if not name or Path(name).name != name:
            raise ValueError(f"invalid bundled font path: {value}")
        return (BUNDLED_FONTS_DIR / name).resolve()
    path = Path(str(value)).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _alignment_data(path: Path, canonical_lyrics: str) -> tuple[dict | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None, [f"timed lyric JSON is missing or invalid: {path}"]
    if not isinstance(value, dict) or not isinstance(value.get("lines"), list) or not value["lines"]:
        return None, ["timed lyric JSON must contain a non-empty lines array"]
    if value.get("schema_version") != 1:
        return None, ["timed lyric JSON schema_version must equal 1"]
    expected_text = [line for line, _tokens in lyric_lines(canonical_lyrics)]
    actual_text: list[str] = []
    problems: list[str] = []
    previous_start = -1.0
    for index, line in enumerate(value["lines"]):
        if not isinstance(line, dict):
            problems.append(f"line {index + 1} must be an object")
            continue
        text = line.get("text")
        start, end = line.get("start"), line.get("end")
        line_number = line.get("index")
        if isinstance(line_number, bool) or not isinstance(line_number, int) or line_number != index + 1:
            problems.append(f"line {index + 1} index must equal {index + 1}")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"line {index + 1} text must be non-empty")
        else:
            actual_text.append(text.strip())
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or start < 0
            or end <= start
        ):
            problems.append(f"line {index + 1} must have numeric 0 <= start < end")
        elif start < previous_start:
            problems.append(f"line {index + 1} starts before the previous lyric line")
        else:
            previous_start = float(start)
    if actual_text != expected_text:
        problems.append(
            "timed lyric text must exactly match the canonical manifest lyrics; "
            "edit song.json first when correcting words"
        )
    return value, problems


def alignment_approval_problems(data: dict, root: Path, vocals_path: Path) -> tuple[dict | None, list[str]]:
    timed_path = root / "alignment" / "timed_lyrics.json"
    alignment, problems = _alignment_data(timed_path, data["lyrics"])
    vocals_sha256 = _sha256_file(vocals_path) if vocals_path.is_file() else None
    audio_path = _resolve_source(root, data.get("audio", {}).get("source"))
    audio_sha256 = _sha256_file(audio_path) if audio_path.is_file() else None
    separation_state = _load_state(root / "stems" / "separation_state.json")
    if (
        audio_sha256 is None
        or vocals_sha256 is None
        or separation_state.get("audio_sha256") != audio_sha256
        or separation_state.get("vocals_sha256") != vocals_sha256
    ):
        problems.append("audio or vocal stem changed; run the separation and alignment stages again")
    if not data.get("audio", {}).get("source"):
        generation_state = _load_state(root / "audio" / "generation_state.json")
        if (
            generation_state.get("contract_digest") != _generation_contract(data)
            or generation_state.get("audio_sha256") != audio_sha256
        ):
            problems.append("generated song does not match its current prompt/lyrics contract; run generation again")
    expected_contract = _alignment_contract(data, vocals_sha256)
    state = _load_state(root / "alignment" / "alignment_state.json")
    if state.get("contract_digest") != expected_contract:
        problems.append("alignment inputs changed; run the align stage again")
    digest = _alignment_artifact_digest(timed_path)
    manifest_alignment = data.get("alignment", {})
    if manifest_alignment.get("approved") is not True:
        problems.append("alignment.approved must be true after timing review")
    elif manifest_alignment.get("approved_digest") != digest:
        problems.append(f"alignment.approved_digest must equal current digest {digest or '<regenerate>'}")
    return alignment, problems


def _video_input_digest(
    data: dict,
    audio: Path,
    background: Path,
    alignment_digest: str,
    font_file: Path | None,
) -> str:
    return _stable_digest({
        "audio_sha256": _sha256_file(audio),
        "background_sha256": _sha256_file(background),
        "alignment_digest": alignment_digest,
        "render": data.get("render", {}),
        "font_sha256": _sha256_file(font_file) if font_file and font_file.is_file() else None,
    })


def approved_video(data: dict, root: Path, expected_video: Path, expected_input_digest: str) -> list[str]:
    state = _load_state(root / "review" / "video_generation.json")
    if not expected_video.is_file():
        return [f"rendered video is missing: {expected_video}"]
    actual_sha256 = _sha256_file(expected_video)
    problems: list[str] = []
    try:
        recorded_video = Path(str(state.get("video", ""))).resolve()
    except (OSError, ValueError):
        recorded_video = Path()
    if (
        recorded_video != expected_video.resolve()
        or state.get("sha256") != actual_sha256
        or state.get("input_digest") != expected_input_digest
    ):
        problems.append("video generation record is stale or the rendered video changed; render and review again")
    review = data.get("review", {})
    if review.get("video_approved") is not True:
        problems.append("review.video_approved must be true after watching the complete video")
    elif review.get("approved_video_sha256") != actual_sha256:
        problems.append(f"review.approved_video_sha256 must equal {actual_sha256}")
    return problems


def render_video(background: Path, audio: Path, subtitles: Path, output: Path,
                 width: int, height: int, fps: int, overwrite: bool,
                 font_file: Path | None = None) -> None:
    if output.exists():
        if not overwrite:
            print(f"[render] up to date, skipping: {output}")
            return
        archived = archive_before_overwrite(output)
        print(f"[render] archived previous output: {archived}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if font_file is not None and not font_file.is_file():
        raise FileNotFoundError(f"configured lyric font file not found: {font_file}")
    encoder = choose_h264_encoder("auto")
    ass_filter = f"ass=filename={_ass_filter_path(subtitles)}"
    if font_file is not None:
        ass_filter += f":fontsdir={_ass_filter_path(font_file.parent)}"
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},format=yuv420p,{ass_filter}"
    )
    command = [
        "ffmpeg", "-hide_banner", "-y", "-loop", "1", "-framerate", str(fps),
        "-i", str(background), "-i", str(audio), "-vf", video_filter,
        "-map", "0:v:0", "-map", "1:a:0", *h264_encoder_args(encoder, "p4", 18),
        "-c:a", "aac", "-b:a", "192k", "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        "-shortest", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True, **popen_kwargs())


def init_main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} song-init")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--lyrics")
    parser.add_argument("--lyrics-file", type=Path)
    parser.add_argument("--music-prompt")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    lyrics = _exclusive_text(args.lyrics, args.lyrics_file, parser)
    root = args.project_root.resolve()
    manifest = root / MANIFEST_NAME
    if manifest.exists() and not args.force:
        print(f"[error] manifest already exists: {manifest} (pass --force to replace)")
        return 1
    root.mkdir(parents=True, exist_ok=True)
    data = new_manifest(args.title.strip(), lyrics, args.music_prompt,
                        str(args.audio.resolve()) if args.audio else None)
    if not atomic_write_json(manifest, data):
        return 1
    payload = {"ok": True, "manifest": str(manifest),
               "next": f"Review rights/settings, then run: {CLI_NAME} song-check --manifest \"{manifest}\" --json"}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Created: {manifest}\n{payload['next']}")
    return 0


def check_main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} song-check")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--project-root", type=Path)
    parser.add_argument("--for-publish", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    path = (args.manifest or args.project_root / MANIFEST_NAME).resolve()
    data = load_manifest(path)
    problems = validate_manifest(data, for_publish=args.for_publish)
    current_alignment_digest = _alignment_artifact_digest(path.parent / "alignment" / "timed_lyrics.json")
    current_video_sha256 = None
    if args.for_publish and not any(problem["severity"] == "error" for problem in problems):
        root = path.parent
        audio_path = _resolve_source(root, data.get("audio", {}).get("source"))
        vocals_path = root / "stems" / "vocals.wav"
        background = root / "visual" / "background.png"
        video = root / "output" / f"{_slug(str(data.get('title', 'song')))}_lyrics.mp4"
        _alignment, artifact_problems = alignment_approval_problems(data, root, vocals_path)
        for message in artifact_problems:
            problems.append({"severity": "error", "path": "artifacts.alignment", "message": message})
        if not artifact_problems and current_alignment_digest and audio_path.is_file() and background.is_file():
            style = resolved_lyrics_style(data.get("render", {}).get("lyrics_style"))
            font_file = _resolve_font_file(root, style.get("font_file"))
            input_digest = _video_input_digest(
                data, audio_path, background, current_alignment_digest, font_file,
            )
            for message in approved_video(data, root, video, input_digest):
                problems.append({"severity": "error", "path": "artifacts.video", "message": message})
            current_video_sha256 = _sha256_file(video) if video.is_file() else None
    errors = [problem for problem in problems if problem["severity"] == "error"]
    report = {
        "ok": not errors,
        "manifest": str(path),
        "alignment_digest": current_alignment_digest,
        "video_sha256": current_video_sha256,
        "problems": problems,
    }
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("Song manifest: " + ("OK" if report["ok"] else f"{len(errors)} error(s)"))
        for problem in problems:
            print(f"  [{problem['severity']}] {problem['path']}: {problem['message']}")
    return 0 if report["ok"] else 1


def build_main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} song-build")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--project-root", type=Path)
    parser.add_argument("--stage", choices=("prepare", "generate", "separate", "align", "visual", "render", "publish", "all"), default="all")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--privacy", choices=("private", "unlisted", "public"))
    args = parser.parse_args()
    manifest = (args.manifest or args.project_root / MANIFEST_NAME).resolve()
    root = manifest.parent
    data = load_manifest(manifest)
    for_publish = args.stage == "publish"
    problems = validate_manifest(data, for_publish=for_publish)
    errors = [problem for problem in problems if problem["severity"] == "error"]
    if errors:
        print(json.dumps({"ok": False, "manifest": str(manifest), "problems": problems}, ensure_ascii=False))
        return 1
    # "all" deliberately ends at a reviewed local artifact. Publishing is a
    # separate, rights-gated external mutation.
    stages = {args.stage} if args.stage != "all" else {
        "prepare", "generate", "separate", "align", "visual", "render",
    }
    lyrics_path = root / "lyrics.txt"
    audio_path = _resolve_source(root, data["audio"].get("source"))
    stems_dir = root / "stems"
    vocals_path = stems_dir / "vocals.wav"
    alignment_dir = root / "alignment"
    timed_path = alignment_dir / "timed_lyrics.json"
    alignment_state_path = alignment_dir / "alignment_state.json"
    background = root / "visual" / "background.png"
    render = data["render"]
    lyrics_style = resolved_lyrics_style(render.get("lyrics_style"))
    font_file = _resolve_font_file(root, lyrics_style.get("font_file"))
    video = root / "output" / f"{_slug(data['title'])}_lyrics.mp4"

    generation_state_path = root / "audio" / "generation_state.json"
    separation_state_path = stems_dir / "separation_state.json"
    visual_state_path = root / "visual" / "visual_state.json"
    generated_audio = not data["audio"].get("source")
    generation_contract = _generation_contract(data)
    generation_state = _load_state(generation_state_path)
    generation_needed = bool(
        "generate" in stages
        and generated_audio
        and (
            args.overwrite
            or not audio_path.is_file()
            or generation_state.get("contract_digest") != generation_contract
            or generation_state.get("audio_sha256")
            != (_sha256_file(audio_path) if audio_path.is_file() else None)
        )
    )
    current_audio_sha256 = _sha256_file(audio_path) if audio_path.is_file() else None
    current_vocals_sha256 = _sha256_file(vocals_path) if vocals_path.is_file() else None
    separation_state = _load_state(separation_state_path)
    separation_needed = bool(
        "separate" in stages
        and (
            args.overwrite
            or generation_needed
            or current_audio_sha256 is None
            or current_vocals_sha256 is None
            or separation_state.get("audio_sha256") != current_audio_sha256
            or separation_state.get("vocals_sha256") != current_vocals_sha256
        )
    )
    alignment_state = _load_state(alignment_state_path)
    alignment_contract = _alignment_contract(data, current_vocals_sha256)
    alignment_needed = bool(
        "align" in stages
        and (
            args.overwrite
            or separation_needed
            or not timed_path.is_file()
            or alignment_state.get("contract_digest") != alignment_contract
        )
    )
    visual_contract = _visual_contract(data)
    visual_state = _load_state(visual_state_path)
    visual_needed = bool(
        "visual" in stages
        and (
            args.overwrite
            or not background.is_file()
            or visual_state.get("contract_digest") != visual_contract
            or visual_state.get("background_sha256")
            != (_sha256_file(background) if background.is_file() else None)
        )
    )

    planned: list[tuple[str, list[str]]] = []
    if generation_needed:
        command = cli_command(
            "ace-step", "--prompt", data["audio"]["generation_prompt"],
            "--lyrics-file", str(lyrics_path), "--output", str(audio_path),
            "--seed", str(data["audio"]["seed"]), "--duration", str(data["audio"].get("duration", -1)),
            "--language", data.get("language", "en"),
        )
        if data["audio"].get("bpm") is not None:
            command += ["--bpm", str(data["audio"]["bpm"])]
        planned.append(("generate", command))
    if separation_needed:
        planned.append(("separate", cli_command(
            "demucs", "--audio", str(audio_path), "--output-dir", str(stems_dir),
            "--device", data["separation"].get("device", "auto"),
        )))
    if alignment_needed:
        planned.append(("align", cli_command(
            "whisperx", "--audio", str(vocals_path), "--lyrics-file", str(lyrics_path),
            "--output-dir", str(alignment_dir), "--language", data.get("language", "en"),
            "--device", data["alignment"].get("device", "auto"),
            "--minimum-confidence", str(data["alignment"]["minimum_confidence"]),
            "--width", str(render["width"]), "--height", str(render["height"]),
            "--font-name", str(lyrics_style["font_name"]),
            "--font-size-ratio", str(lyrics_style["font_size_ratio"]),
            "--outline", str(lyrics_style["outline"]),
            "--shadow", str(lyrics_style["shadow"]),
            "--fade-in-ms", str(lyrics_style["fade_in_ms"]),
            "--fade-out-ms", str(lyrics_style["fade_out_ms"]),
            "--alignment", str(lyrics_style["alignment"]),
            "--margin-vertical-ratio", str(lyrics_style["margin_vertical_ratio"]),
        )))
    if visual_needed:
        planned.append(("visual", cli_command(
            "zimage", "--prompt", data["visual"]["prompt"], "--output", str(background),
            "--width", str(data["visual"]["width"]), "--height", str(data["visual"]["height"]),
            "--seed", str(data["visual"]["seed"]),
        )))

    if args.dry_run and "publish" not in stages:
        print(json.dumps({
            "ok": True, "dry_run": True, "manifest": str(manifest),
            "commands": [command for _stage, command in planned], "render": str(video),
            "render_requested": "render" in stages,
            "font_file": str(font_file) if font_file else None,
            "publish": "explicit --stage publish only",
        }, ensure_ascii=False))
        return 0

    if stages - {"publish"}:
        lyrics_path.parent.mkdir(parents=True, exist_ok=True)
        lyrics_path.write_text(data["lyrics"].strip() + "\n", encoding="utf-8")
    results: list[dict] = []
    for planned_stage, command in planned:
        if planned_stage in {"generate", "separate", "align"}:
            _invalidate_alignment(data, manifest)
        elif planned_stage == "visual":
            _invalidate_video(data, manifest)
        if planned_stage == "generate" and audio_path.exists():
            archive_before_overwrite(audio_path)
        if planned_stage == "visual" and background.exists():
            archive_before_overwrite(background)
        accepted = (0, 3) if planned_stage == "align" else (0,)
        result = _run_streaming(command, accepted_codes=accepted)
        results.append(result)
        if planned_stage == "generate":
            if not atomic_write_json(generation_state_path, {
                "schema_version": 1,
                "contract_digest": generation_contract,
                "audio_sha256": _sha256_file(audio_path),
            }):
                raise OSError(f"could not write generation state: {generation_state_path}")
        elif planned_stage == "separate":
            if not atomic_write_json(separation_state_path, {
                "schema_version": 1,
                "audio_sha256": _sha256_file(audio_path),
                "vocals_sha256": _sha256_file(vocals_path),
            }):
                raise OSError(f"could not write separation state: {separation_state_path}")
        elif planned_stage == "align":
            current_vocals_sha256 = _sha256_file(vocals_path)
            alignment_contract = _alignment_contract(data, current_vocals_sha256)
            if not atomic_write_json(alignment_state_path, {
                "schema_version": 1,
                "contract_digest": alignment_contract,
                "vocals_sha256": current_vocals_sha256,
            }):
                raise OSError(f"could not write alignment state: {alignment_state_path}")
            digest = _alignment_artifact_digest(timed_path)
            print(
                "[review required] Inspect raw transcript, unmatched words, confidence, and every repeated chorus; "
                "correct timings in alignment/timed_lyrics.json while keeping text equal to song.json, then set "
                f"alignment.approved=true and alignment.approved_digest='{digest or '<regenerate>'}'."
            )
            return 3
        elif planned_stage == "visual":
            if not atomic_write_json(visual_state_path, {
                "schema_version": 1,
                "contract_digest": visual_contract,
                "background_sha256": _sha256_file(background),
            }):
                raise OSError(f"could not write visual state: {visual_state_path}")

    if "render" in stages or "publish" in stages:
        alignment, alignment_problems = alignment_approval_problems(data, root, vocals_path)
        if alignment is None or alignment_problems:
            print(json.dumps({
                "ok": False,
                "gate": "alignment-review",
                "manifest": str(manifest),
                "problems": alignment_problems,
                "current_digest": _alignment_artifact_digest(timed_path),
            }, ensure_ascii=False))
            return 3 if "render" in stages else 1
        visual_state = _load_state(visual_state_path)
        actual_background_sha256 = _sha256_file(background) if background.is_file() else None
        if (
            actual_background_sha256 is None
            or visual_state.get("contract_digest") != _visual_contract(data)
            or visual_state.get("background_sha256") != actual_background_sha256
        ):
            print(json.dumps({
                "ok": False,
                "gate": "visual-provenance",
                "problems": ["background is missing or stale; run the visual stage again"],
            }, ensure_ascii=False))
            return 1
        alignment_dir.mkdir(parents=True, exist_ok=True)
        write_srt(alignment, alignment_dir / "lyrics.srt")
        write_ass(alignment, alignment_dir / "lyrics.ass", render["width"], render["height"], lyrics_style)
        if not audio_path.is_file() or not background.is_file():
            missing = [str(path) for path in (audio_path, background) if not path.is_file()]
            print(json.dumps({"ok": False, "gate": "render-inputs", "missing": missing}, ensure_ascii=False))
            return 1
        alignment_digest = _alignment_artifact_digest(timed_path)
        assert alignment_digest is not None
        video_input_digest = _video_input_digest(data, audio_path, background, alignment_digest, font_file)
    else:
        video_input_digest = ""

    if "render" in stages:
        video_state_path = root / "review" / "video_generation.json"
        video_state = _load_state(video_state_path)
        render_needed = bool(
            args.overwrite
            or not video.is_file()
            or video_state.get("input_digest") != video_input_digest
            or video_state.get("sha256") != (_sha256_file(video) if video.is_file() else None)
        )
        if render_needed:
            _invalidate_video(data, manifest)
            render_video(
                background, audio_path, alignment_dir / "lyrics.ass", video,
                render["width"], render["height"], render["fps"], video.exists(), font_file,
            )
            video_sha256 = _sha256_file(video)
            if not atomic_write_json(video_state_path, {
                "schema_version": 1,
                "video": str(video.resolve()),
                "sha256": video_sha256,
                "input_digest": video_input_digest,
            }):
                raise OSError(f"could not write video generation state: {video_state_path}")
            print(
                "[review required] Watch the complete lyrics video, then set "
                f"review.video_approved=true and review.approved_video_sha256='{video_sha256}'."
            )
            return 3
        video_problems = approved_video(data, root, video, video_input_digest)
        if video_problems:
            print(json.dumps({
                "ok": False,
                "gate": "video-review",
                "problems": video_problems,
                "current_sha256": _sha256_file(video) if video.is_file() else None,
            }, ensure_ascii=False))
            return 3

    if "publish" in stages:
        state_path = root / "publish.json"
        if state_path.exists():
            print(f"[error] this project is already recorded as published: {state_path}")
            return 1
        video_problems = approved_video(data, root, video, video_input_digest)
        if video_problems:
            print(json.dumps({"ok": False, "gate": "publish-artifact-review", "problems": video_problems},
                             ensure_ascii=False))
            return 1
        youtube = data["youtube"]
        upload_command = cli_command(
            "youtube-upload", "--profile", youtube.get("profile", "default"),
            "--video", str(video), "--title", youtube.get("title") or data["title"],
            "--description", youtube.get("description", ""), "--tags", ",".join(youtube.get("tags", [])),
            "--privacy", args.privacy or youtube.get("privacy", "private"),
            "--thumbnail", str(background), "--contains-synthetic-media", "--json",
        )
        if args.dry_run:
            print(json.dumps({
                "ok": True, "dry_run": True, "manifest": str(manifest),
                "commands": [upload_command], "video": str(video),
                "profile": youtube.get("profile", "default"),
            }, ensure_ascii=False))
            return 0
        result = _run_streaming(upload_command)
        if not atomic_write_json(
            state_path,
            {"schema_version": 1, "video": str(video), "youtube": result},
        ):
            raise OSError(
                f"upload succeeded but idempotency state could not be saved: {state_path}; "
                "do not retry until the published video is reconciled"
            )
        results.append(result)
    emit_result(
        manifest=manifest,
        audio=audio_path if audio_path.exists() else None,
        video=video if video.exists() else None,
        commands_run=len(results),
        results=results,
    )
    return 0
