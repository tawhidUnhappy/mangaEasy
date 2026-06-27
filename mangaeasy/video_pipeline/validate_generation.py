from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    item_dirs,
    merge_item_selection,
    project_name,
)
from mangaeasy.video_pipeline.item_assets import load_narration


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".wav"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate generated narration video outputs.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*", help="Item names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--require-long", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--long-video", type=Path, default=None, help="Override the long video path to validate.")
    parser.add_argument("--duration-tolerance", type=float, default=3.0)
    return parser.parse_args()


def files_by_stem(folder: Path, extensions: set[str]) -> dict[str, Path]:
    if not folder.exists():
        return {}
    return {
        path.stem: path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    }


def ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,pix_fmt,width,height,sample_rate,channel_layout,start_time,duration",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout or "{}")


def duration(path: Path) -> float:
    data = ffprobe(path)
    value = data.get("format", {}).get("duration")
    if value is None:
        raise ValueError(f"Could not read duration: {path}")
    return float(value)


def stream(data: dict, codec_type: str) -> dict | None:
    for item in data.get("streams", []):
        if item.get("codec_type") == codec_type:
            return item
    return None


def approx(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def check_video_file(path: Path, args: argparse.Namespace, errors: list[str]) -> float:
    if not path.exists():
        errors.append(f"Missing video: {path}")
        return 0.0
    data = ffprobe(path)
    video = stream(data, "video")
    audio = stream(data, "audio")
    if video is None:
        errors.append(f"No video stream: {path}")
    else:
        if str(video.get("width")) != str(args.width) or str(video.get("height")) != str(args.height):
            errors.append(f"Wrong video size for {path}: {video.get('width')}x{video.get('height')}")
        if video.get("pix_fmt") != "yuv420p":
            errors.append(f"Video pixel format should be yuv420p for YouTube: {path} has {video.get('pix_fmt')}")
    if audio is None:
        errors.append(f"No audio stream: {path}")
    else:
        if audio.get("codec_name") != "aac":
            errors.append(f"Audio codec should be AAC for {path}: {audio.get('codec_name')}")
        if str(audio.get("sample_rate")) != "48000":
            errors.append(f"Audio sample rate should be 48000 for {path}: {audio.get('sample_rate')}")
        layout = (audio.get("channel_layout") or "").lower()
        if layout != "stereo":
            errors.append(f"Audio channel layout should be stereo for {path}: {audio.get('channel_layout')}")
    if video is not None and audio is not None:
        video_start = float(video.get("start_time") or 0.0)
        audio_start = float(audio.get("start_time") or 0.0)
        if abs(video_start - audio_start) > 0.05:
            errors.append(
                f"Video/audio stream start mismatch for {path}: "
                f"video_start={video_start:.6f}s audio_start={audio_start:.6f}s"
            )
    return float(data.get("format", {}).get("duration") or 0.0)


def check_item(item_dir: Path, args: argparse.Namespace, totals: dict[str, int], errors: list[str]) -> float:
    item_name = item_dir.name
    name = project_name(args.project_root, args.project_name)
    audio_dir = args.audio_root.resolve() / name / item_name
    item_wav = args.audio_root.resolve() / name / "_items" / f"item_{item_name}_narration.wav"
    legacy_item_wav = args.audio_root.resolve() / name / "_chapters" / f"chapter_{item_name}_narration.wav"
    if not item_wav.exists() and legacy_item_wav.exists():
        item_wav = legacy_item_wav
    item_video = args.output_root.resolve() / name / "items" / f"item_{item_name}.mp4"
    legacy_item_video = args.output_root.resolve() / name / "chapters" / f"chapter_{item_name}.mp4"
    if not item_video.exists() and legacy_item_video.exists():
        item_video = legacy_item_video

    narration_path = item_dir / "narration.json"
    panels_dir = item_dir / "panels"

    if not narration_path.exists():
        errors.append(f"[{item_name}] Missing narration.json")
        return 0.0
    narration = load_narration(item_dir)
    narration_stems = [Path(item.get("image", "")).stem for item in narration if isinstance(item, dict) and item.get("image")]
    panels = files_by_stem(panels_dir, IMAGE_EXTENSIONS)
    audios = files_by_stem(audio_dir, AUDIO_EXTENSIONS)

    totals["narration"] += len(narration_stems)
    totals["panels"] += len(panels)
    totals["panel_audio"] += len(audios)

    if len(narration_stems) != len(narration):
        errors.append(f"[{item_name}] Some narration entries are missing image keys.")
    if len(set(narration_stems)) != len(narration_stems):
        errors.append(f"[{item_name}] Duplicate narration image stems.")
    if set(narration_stems) != set(panels):
        errors.append(f"[{item_name}] Narration and panel names do not match.")
    if set(narration_stems) != set(audios):
        errors.append(f"[{item_name}] Narration and panel-audio names do not match.")

    panel_audio_duration = 0.0
    for stem in narration_stems:
        audio_path = audios.get(stem)
        if audio_path:
            panel_audio_duration += duration(audio_path)

    if not item_wav.exists():
        errors.append(f"[{item_name}] Missing item narration WAV: {item_wav}")
        item_wav_duration = 0.0
    else:
        totals["item_wavs"] += 1
        wav_probe = ffprobe(item_wav)
        wav_audio = stream(wav_probe, "audio")
        if wav_audio is None:
            errors.append(f"[{item_name}] Item WAV has no audio stream: {item_wav}")
        elif str(wav_audio.get("sample_rate")) != "48000":
            errors.append(f"[{item_name}] Item WAV sample rate should be 48000: {wav_audio.get('sample_rate')}")
        item_wav_duration = float(wav_probe.get("format", {}).get("duration") or 0.0)
        if not approx(item_wav_duration, panel_audio_duration, args.duration_tolerance):
            errors.append(
                f"[{item_name}] Item WAV duration mismatch: wav={item_wav_duration:.2f}s "
                f"panel_audio_sum={panel_audio_duration:.2f}s"
            )

    item_video_duration = check_video_file(item_video, args, errors)
    if item_video.exists():
        totals["item_videos"] += 1
        if item_wav_duration and not approx(item_video_duration, item_wav_duration, args.duration_tolerance):
            errors.append(
                f"[{item_name}] Item video duration mismatch: video={item_video_duration:.2f}s "
                f"item_wav={item_wav_duration:.2f}s"
            )

    print(
        f"[{item_name}] panels={len(panels)} narration={len(narration_stems)} "
        f"audio={len(audios)} item_wav={'yes' if item_wav.exists() else 'no'} "
        f"item_video={'yes' if item_video.exists() else 'no'}"
    )
    return item_wav_duration


def main() -> int:
    args = parse_args()
    name = project_name(args.project_root, args.project_name)
    items = item_dirs(
        args.project_root.resolve(),
        merge_item_selection(args.items, args.item_range),
    )
    if not items:
        raise FileNotFoundError("No item folders selected.")

    totals = {"panels": 0, "narration": 0, "panel_audio": 0, "item_wavs": 0, "item_videos": 0}
    errors: list[str] = []
    long_expected_duration = 0.0

    for item_dir in items:
        long_expected_duration += check_item(item_dir, args, totals, errors)

    expected_items = len(items)
    output_items_dir = args.output_root.resolve() / name / "items"
    legacy_output_items_dir = args.output_root.resolve() / name / "chapters"
    actual_item_videos = []
    if output_items_dir.exists():
        actual_item_videos.extend(sorted(output_items_dir.glob("item_*.mp4")))
    if legacy_output_items_dir.exists() and not actual_item_videos:
        actual_item_videos.extend(sorted(legacy_output_items_dir.glob("chapter_*.mp4")))
    selected_names = {f"item_{item.name}.mp4" for item in items}
    legacy_selected_names = {f"chapter_{item.name}.mp4" for item in items}
    actual_selected = [path for path in actual_item_videos if path.name in selected_names or path.name in legacy_selected_names]
    if len(actual_selected) != expected_items:
        errors.append(f"Video item count mismatch: expected={expected_items} actual={len(actual_selected)}")
    extra_videos = sorted(
        path.name
        for path in actual_item_videos
        if path.name not in selected_names and path.name not in legacy_selected_names
    )
    if extra_videos:
        errors.append("Unexpected extra item videos: " + ", ".join(extra_videos[:20]))

    if args.require_long:
        long_video = (args.long_video or (args.output_root.resolve() / name / f"{name}_full.mp4")).resolve()
        long_duration = check_video_file(long_video, args, errors)
        if long_video.exists() and long_expected_duration:
            long_tolerance = max(args.duration_tolerance, expected_items * 0.25)
            if not approx(long_duration, long_expected_duration, long_tolerance):
                errors.append(
                    f"Long video duration mismatch: video={long_duration:.2f}s "
                    f"item_wav_sum={long_expected_duration:.2f}s"
                )

    print("\nValidation totals:")
    print(f"  items:          {expected_items}")
    print(f"  panels:         {totals['panels']}")
    print(f"  narration:      {totals['narration']}")
    print(f"  panel audio:    {totals['panel_audio']}")
    print(f"  item WAVs:      {totals['item_wavs']}")
    print(f"  item videos:    {totals['item_videos']}")

    if errors:
        print("\nVALIDATION FAILED")
        for error in errors:
            print(f"  ERROR: {error}")
        return 1

    print("\nVALIDATION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
