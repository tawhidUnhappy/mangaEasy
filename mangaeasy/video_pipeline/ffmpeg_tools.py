from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path


def run(
    command: list[str],
    *,
    capture: bool = False,
    quiet_ffmpeg: bool = True,
    print_command: bool = True,
) -> subprocess.CompletedProcess[str]:
    if quiet_ffmpeg and command and Path(command[0]).name.lower() == "ffmpeg" and "-loglevel" not in command:
        insert_at = 2 if len(command) > 1 and command[1] == "-hide_banner" else 1
        command = command[:insert_at] + ["-loglevel", "error"] + command[insert_at:]
    if print_command:
        print(" ".join(shlex.quote(part) for part in command), flush=True)
    return subprocess.run(
        command,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def ffconcat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def write_concat_file(paths: list[Path], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as f:
        f.write("ffconcat version 1.0\n")
        for path in paths:
            f.write(f"file '{ffconcat_path(path)}'\n")
    return output


def probe_json(path: Path, entries: str) -> dict:
    result = run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture=True,
        quiet_ffmpeg=False,
        print_command=False,
    )
    return json.loads(result.stdout or "{}")


def probe_duration(path: Path) -> float:
    data = probe_json(path, "format=duration")
    value = data.get("format", {}).get("duration")
    if value is None:
        raise ValueError(f"Could not read duration: {path}")
    return max(0.08, float(value))


def first_stream(path: Path, codec_type: str, entries: str) -> dict[str, str]:
    data = probe_json(path, entries)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return {key: str(value) for key, value in stream.items()}
    raise ValueError(f"No {codec_type} stream found in {path}")


def video_stream(path: Path) -> dict[str, str]:
    return first_stream(
        path,
        "video",
        "stream=codec_type,pix_fmt,width,height,duration,nb_frames,avg_frame_rate",
    )


def validate_video_stream(path: Path, *, width: int | None = None, height: int | None = None) -> None:
    stream = video_stream(path)
    pix_fmt = stream.get("pix_fmt", "")
    if not pix_fmt or pix_fmt == "unknown":
        raise ValueError(f"Rendered video has invalid pixel format: {path}")
    if width is not None and stream.get("width") != str(width):
        print(f"WARNING: unexpected video width for {path}: {stream}", flush=True)
    if height is not None and stream.get("height") != str(height):
        print(f"WARNING: unexpected video height for {path}: {stream}", flush=True)


def available_encoders() -> set[str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return set()
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return encoders


def choose_h264_encoder(requested: str) -> str:
    if requested != "auto":
        return requested
    encoders = available_encoders()
    for candidate in ("h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox"):
        if candidate in encoders:
            print(f"Auto video encoder: {candidate}", flush=True)
            return candidate
    print("Auto video encoder: libx264", flush=True)
    return "libx264"


def h264_encoder_args(encoder: str, preset: str, cq: int) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "medium" if preset == "p1" else preset, "-crf", str(cq)]
    if encoder == "h264_videotoolbox":
        return ["-c:v", encoder, "-q:v", str(max(1, min(100, cq)))]
    if encoder in {"h264_amf", "h264_qsv"}:
        return ["-c:v", encoder, "-global_quality", str(cq)]
    return ["-c:v", encoder, "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
