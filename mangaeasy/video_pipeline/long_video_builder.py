from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mangaeasy.utils import archive_before_overwrite
from mangaeasy.video_pipeline.check_items import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, files_by_stem, load_narration
from mangaeasy.video_pipeline.common import item_dirs, item_number, item_value, merge_item_selection, project_name
from mangaeasy.video_pipeline.ffmpeg_tools import (
    choose_h264_encoder,
    h264_encoder_args,
    run,
    validate_video_stream,
    write_concat_file,
)


ITEM_VIDEO_RE = re.compile(r"^(?:item|chapter)_(\d+(?:\.\d+)?)\.mp4$", re.IGNORECASE)


@dataclass(frozen=True)
class LongVideoConfig:
    project_root: Path
    output_root: Path
    work_dir: Path
    project_name_override: str | None = None
    input_dir: Path | None = None
    output: Path | None = None
    start: str = "01"
    end: str | None = None
    items: list[str] | None = None
    item_range: str | None = None
    overwrite: bool = False
    reencode: bool = False
    copy_all: bool = False
    encoder: str = "auto"
    preset: str = "p1"
    cq: int = 18
    audio_bitrate: str = "128k"
    audio_root: Path | None = None
    narration_dir: Path | None = None
    background_music: Path | None = None
    music_volume_db: float = -25.0
    narration_volume: float = 1.0
    # Off by default: a missing item video is normally a failed render and
    # must stop the build. Turn on only when a chapter genuinely does not
    # exist (e.g. a scanlation gap on the source) so the join stitches the
    # chapters that DO exist, in order, and skips the hole with a warning.
    allow_gaps: bool = False


def default_output_name(name: str) -> str:
    """Each join gets its own timestamped filename instead of one fixed
    name -- re-running the join (e.g. after fixing a chapter) never
    overwrites a previous long video; both stay on disk side by side. See
    common.find_latest_long_video for how later steps locate the newest one."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{name}_full_{timestamp}.mp4"


def output_path(config: LongVideoConfig) -> Path:
    name = project_name(config.project_root, config.project_name_override)
    return (config.output or (config.output_root / name / default_output_name(name))).resolve()


def project_work_dir(config: LongVideoConfig) -> Path:
    return config.work_dir.resolve() / project_name(config.project_root, config.project_name_override)


def input_dir(config: LongVideoConfig) -> Path:
    name = project_name(config.project_root, config.project_name_override)
    if config.input_dir is not None:
        return config.input_dir.resolve()
    current = (config.output_root / name / "items").resolve()
    legacy = (config.output_root / name / "chapters").resolve()
    return legacy if legacy.exists() and not current.exists() else current


def discover_chapters(folder: Path) -> dict[str, Path]:
    """Rendered item videos keyed by their item NAME ("01", "9.5", ...).

    Keys are names, not parsed integers: split/extra chapters (2.1, 9.5) are
    real rendered items and used to vanish here silently — an integer-only
    regex skipped them and the join shipped without them."""
    chapters: dict[str, Path] = {}
    for path in list(folder.glob("item_*.mp4")) + list(folder.glob("chapter_*.mp4")):
        match = ITEM_VIDEO_RE.match(path.name)
        if match:
            chapters[match.group(1)] = path
    return chapters


def selected_range(config: LongVideoConfig, chapters: dict[str, Path]) -> tuple[int, int]:
    selected = merge_item_selection(config.items, config.item_range)
    if selected:
        return min(item_number(chapter) for chapter in selected), max(item_number(chapter) for chapter in selected)
    if config.end:
        return item_number(config.start), item_number(config.end)
    return item_number(config.start), int(max(item_value(name) for name in chapters))


def included_chapters(chapters: dict[str, Path], start: int, end: int, allow_gaps: bool) -> tuple[list[str], list[int]]:
    """Item names to join across ``[start, end]``, plus the integer gaps.

    Every item video whose numeric VALUE falls inside the range is included,
    in value order — so decimal chapters (2.1, 9.5) ride along automatically.
    Contiguity is checked on integers only: a missing 9.5 can't be known
    about, but a missing 09 is either a failed render (fatal by default) or a
    genuine source gap (``allow_gaps`` skips it with a warning)."""
    values = {name: item_value(name) for name in chapters}
    covered = {int(v) for v in values.values() if v == int(v)}
    gaps = [n for n in range(start, end + 1) if n not in covered]
    included = sorted((name for name, v in values.items() if start <= v <= end),
                      key=lambda name: (values[name], name))
    return included, gaps


def chapter_narration_files(narration_dir: Path, names: list[str]) -> list[Path]:
    files: list[Path] = []
    for name in names:
        path = narration_dir / f"item_{name}_narration.wav"
        if not path.exists():
            legacy = narration_dir / f"chapter_{name}_narration.wav"
            path = legacy if legacy.exists() else path
        if not path.exists():
            raise FileNotFoundError(f"Missing item narration WAV: {path}")
        files.append(path)
    return files


def build_full_narration_wav(paths: list[Path], work_dir: Path, output_name: str) -> Path:
    output = work_dir / f"{Path(output_name).stem}_narration.wav"
    audio_list = write_concat_file(paths, work_dir / f"{Path(output_name).stem}_audio.ffconcat")
    run(
        [
            "ffmpeg", "-hide_banner", "-y", "-guess_layout_max", "0",
            "-f", "concat", "-safe", "0", "-i", str(audio_list),
            "-af", "aformat=channel_layouts=mono,aresample=48000",
            "-c:a", "pcm_s16le", str(output),
        ]
    )
    return output


def build_video_only_copy(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(input_path),
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            "-movflags", "+faststart",
            str(output_path),
        ]
    )
    return output_path


def video_only_chapter_files(paths: list[Path], work_dir: Path) -> list[Path]:
    clean_dir = work_dir / "long_video_only_items"
    clean_paths: list[Path] = []
    for path in paths:
        clean_path = clean_dir / path.name
        clean_paths.append(build_video_only_copy(path, clean_path))
    return clean_paths


def mixed_audio_filter(
    narration_volume: float, music_volume_db: float, narration_input: int, music_input: int
) -> str:
    return (
        f"[{narration_input}:a]volume={narration_volume},"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[narr];"
        f"[{music_input}:a]volume={music_volume_db}dB,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[music];"
        "[narr][music]amix=inputs=2:duration=first:dropout_transition=3,"
        "alimiter=limit=0.95,aresample=async=1:first_pts=0[a]"
    )


def narration_only_filter(narration_volume: float) -> str:
    return (
        f"[1:a]volume={narration_volume},"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "alimiter=limit=0.95,aresample=async=1:first_pts=0[a]"
    )


def video_codec_args(config: LongVideoConfig) -> list[str]:
    if not config.reencode:
        return ["-c:v", "copy"]
    return h264_encoder_args(choose_h264_encoder(config.encoder), config.preset, config.cq)


def validate_config(config: LongVideoConfig) -> None:
    if config.background_music is not None and not config.background_music.exists():
        raise FileNotFoundError(f"Background music does not exist: {config.background_music}")
    if config.narration_volume < 0:
        raise ValueError("Narration volume must be non-negative.")


def validate_items_strict(config: LongVideoConfig, chapters: dict[str, Path], names: list[str]) -> None:
    """Check every item's panels, narration entries, audio, and rendered video.

    The long video cannot be missing or mismatched content for any item it
    joins, so any problem stops the build instead of just warning. ``numbers``
    is the exact set being joined (already gap-filtered by the caller when
    ``--allow-gaps`` is on), so genuinely-absent chapters are never checked
    here. Panels are checked before narration text because a missing panel
    image breaks rendering outright, while a bad narration entry only affects
    that one audio line.
    """
    name = project_name(config.project_root, config.project_name_override)
    audio_root = config.audio_root.resolve() if config.audio_root else None
    problems: list[str] = []

    for name in names:
        label = f"item {name}"
        found = item_dirs(config.project_root, [name])
        if not found:
            problems.append(f"{label}: no project item folder found")
            continue
        item_dir = found[0]

        narration_path = item_dir / "narration.json"
        if not narration_path.exists():
            problems.append(f"{label}: missing narration.json")
            continue
        try:
            narration = load_narration(item_dir)
        except Exception as exc:
            problems.append(f"{label}: could not read narration.json: {exc}")
            continue

        panels = files_by_stem(item_dir / "panels", IMAGE_EXTENSIONS)
        narration_images = [entry.get("image", "") for entry in narration if isinstance(entry, dict)]
        narration_stems = [Path(image).stem for image in narration_images if image]

        missing_panels = sorted(set(narration_stems) - set(panels))
        if missing_panels:
            problems.append(f"{label}: missing panel image(s) for {', '.join(missing_panels[:10])}")

        missing_text = [
            Path(entry.get("image", "")).stem or f"#{idx}"
            for idx, entry in enumerate(narration, start=1)
            if isinstance(entry, dict) and not (entry.get("narration") or entry.get("text") or "").strip()
        ]
        if missing_text:
            problems.append(f"{label}: empty narration text for {', '.join(missing_text[:10])}")

        if audio_root is not None:
            audios = files_by_stem(audio_root / name / item_dir.name, AUDIO_EXTENSIONS)
            missing_audio = sorted(set(narration_stems) - set(audios))
            if missing_audio:
                problems.append(f"{label}: missing audio for {', '.join(missing_audio[:10])}")

        if name not in chapters:
            problems.append(f"{label}: missing rendered item video")

    if problems:
        raise FileNotFoundError(
            "Long video build stopped: it cannot be missing any panel, narration entry, "
            "audio file, or item video. Problems found:\n  " + "\n  ".join(problems)
        )


def build_long_video(config: LongVideoConfig) -> Path:
    validate_config(config)
    out_path = output_path(config)
    work_dir = project_work_dir(config)
    if out_path.exists():
        if not config.overwrite:
            raise FileExistsError(f"Output exists. Use --overwrite: {out_path}")
        archived = archive_before_overwrite(out_path)
        if archived is not None:
            print(f"Archived previous long video to: {archived}", flush=True)

    chapters = discover_chapters(input_dir(config))
    if not chapters:
        raise FileNotFoundError(f"No item_*.mp4 files found in {input_dir(config)}")
    start, end = selected_range(config, chapters)
    names, gaps = included_chapters(chapters, start, end, config.allow_gaps)
    if gaps and not config.allow_gaps:
        raise FileNotFoundError(
            "Missing item videos: " + ", ".join(f"{n:02d}" for n in gaps)
            + "\nRe-render them, or if these chapters genuinely do not exist "
            "(e.g. a scanlation gap on the source) pass --allow-gaps to join "
            "the chapters that do exist."
        )
    if gaps:
        print(
            "[long-video] --allow-gaps: joining "
            + ", ".join(names)
            + "; skipping absent chapter(s) "
            + ", ".join(f"{n:02d}" for n in gaps),
            flush=True,
        )
    validate_items_strict(config, chapters, names)

    selected = [chapters[n] for n in names]
    for path in selected:
        validate_video_stream(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    base = ["ffmpeg", "-hide_banner", "-y" if config.overwrite else "-n"]
    narration_input: list[str] = []
    music_input: list[str] = []
    full_narration = None
    if config.narration_dir is not None:
        full_narration = build_full_narration_wav(
            chapter_narration_files(config.narration_dir.resolve(), names),
            work_dir,
            out_path.name,
        )
        narration_input = ["-guess_layout_max", "0", "-i", str(full_narration)]

    video_inputs = video_only_chapter_files(selected, work_dir) if full_narration is not None else selected
    concat_path = write_concat_file(video_inputs, work_dir / f"{out_path.stem}.ffconcat")
    print(f"Joining {len(selected)} item video(s): {start:02d} through {end:02d}", flush=True)
    inputs = ["-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", str(concat_path)]

    if config.background_music is not None and full_narration is not None:
        music_input = ["-guess_layout_max", "0", "-stream_loop", "-1", "-i", str(config.background_music.resolve())]
        output_args = [
            "-filter_complex", mixed_audio_filter(config.narration_volume, config.music_volume_db, 1, 2),
            "-map", "0:v:0", "-map", "[a]", *video_codec_args(config),
            "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-movflags", "+faststart", str(out_path),
        ]
    elif full_narration is not None:
        output_args = [
            "-filter_complex", narration_only_filter(config.narration_volume),
            "-map", "0:v:0", "-map", "[a]", *video_codec_args(config),
            "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-movflags", "+faststart", str(out_path),
        ]
    elif config.background_music is not None:
        music_input = ["-guess_layout_max", "0", "-stream_loop", "-1", "-i", str(config.background_music.resolve())]
        output_args = [
            "-filter_complex", mixed_audio_filter(config.narration_volume, config.music_volume_db, 0, 1),
            "-map", "0:v:0", "-map", "[a]", *video_codec_args(config),
            "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-movflags", "+faststart", str(out_path),
        ]
    elif config.reencode:
        output_args = [
            *video_codec_args(config),
            "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-movflags", "+faststart", str(out_path),
        ]
    elif config.copy_all:
        output_args = ["-c", "copy", "-movflags", "+faststart", str(out_path)]
    else:
        output_args = [
            "-fflags", "+genpts", "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
            "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", str(out_path),
        ]

    run(base + inputs + narration_input + music_input + output_args)
    print(f"\nLong video written to: {out_path}", flush=True)
    return out_path
