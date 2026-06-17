from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection, project_name
from mangaeasy.video_pipeline.item_assets import (
    IMAGE_EXTENSIONS,
    PanelAsset,
    item_narration_path,
    collect_panel_assets,
)
from mangaeasy.video_pipeline.ffmpeg_tools import (
    choose_h264_encoder,
    ffconcat_path,
    h264_encoder_args,
    run,
    validate_video_stream,
)
from mangaeasy.video.blur_background import (
    BlurBackgroundOptions,
    blur_work_size,
    render_blurred_panel_ffmpeg,
    scaled_blur_sigma,
)


@dataclass(frozen=True)
class VideoBuildConfig:
    project_root: Path
    audio_root: Path
    output_root: Path
    work_dir: Path
    project_name_override: str | None = None
    output_dir: Path | None = None
    items: list[str] | None = None
    item_range: str | None = None
    overwrite: bool = False
    width: int = 1920
    height: int = 1080
    fps: int = 15
    encoder: str = "auto"
    preset: str = "p1"
    cq: int = 18
    audio_bitrate: str = "128k"
    background_style: str = "blur"
    background_image: Path | None = None
    blur_sigma: float = 28.0
    blur_downscale: int = 4
    blur_backend: str = "auto"
    background_brightness: float = -0.06
    background_saturation: float = 1.08
    keep_work: bool = False
    render_mode: str = "segments"
    workers: int = 1


def item_output_dir(config: VideoBuildConfig) -> Path:
    if config.output_dir is not None:
        return config.output_dir.resolve()
    return (config.output_root.resolve() / project_name(config.project_root, config.project_name_override) / "items").resolve()


def project_work_dir(config: VideoBuildConfig) -> Path:
    return config.work_dir.resolve() / project_name(config.project_root, config.project_name_override)


def selected_item_dirs(config: VideoBuildConfig) -> list[Path]:
    return item_dirs(
        config.project_root.resolve(),
        merge_item_selection(config.items, config.item_range),
    )


def blur_options(config: VideoBuildConfig) -> BlurBackgroundOptions:
    return BlurBackgroundOptions(
        sigma=config.blur_sigma,
        downscale=config.blur_downscale,
        backend=config.blur_backend,
        brightness=config.background_brightness,
        saturation=config.background_saturation,
    )


def video_filter(config: VideoBuildConfig) -> str:
    width = config.width
    height = config.height
    fps = config.fps
    if config.background_style == "black":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"scale={width}:{height}:out_range=tv,"
            f"fps={fps},format=yuv420p"
        )
    options = blur_options(config)
    small_w, small_h = blur_work_size(width, height, options)
    sigma = scaled_blur_sigma(options)
    return (
        "split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,scale={small_w}:{small_h},"
        f"gblur=sigma={sigma:.3f}:steps=1,scale={width}:{height},"
        f"eq=brightness={config.background_brightness}:saturation={config.background_saturation}[bg];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"scale={width}:{height}:out_range=tv,fps={fps},format=yuv420p"
    )


def background_image_filter(config: VideoBuildConfig) -> str:
    width = config.width
    height = config.height
    fps = config.fps
    return (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=rgba[bg];"
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"setsar=1,format=rgba[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"scale={width}:{height}:out_range=tv,fps={fps},format=yuv420p[v]"
    )


def composed_blur_frame_path(segment_path: Path) -> Path:
    return segment_path.with_name(f"{segment_path.stem}_frame.png")


def render_blurred_panel_frame(image_path: Path, frame_path: Path, config: VideoBuildConfig) -> None:
    if frame_path.exists() and not config.overwrite:
        return
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = frame_path.with_name(f"{frame_path.stem}.tmp{frame_path.suffix}")
    try:
        backend = render_blurred_panel_ffmpeg(
            image_path,
            tmp_path,
            config.width,
            config.height,
            blur_options(config),
            run,
            log=print,
        )
        tmp_path.replace(frame_path)
        print(f"    blur background: {backend} one-frame composite", flush=True)
    finally:
        tmp_path.unlink(missing_ok=True)


def write_video_concat_file(paths: list[Path], work_dir: Path, chapter: str) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    concat_path = work_dir / f"{chapter}_segments.ffconcat"
    with concat_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("ffconcat version 1.0\n")
        for path in paths:
            f.write(f"file '{ffconcat_path(path)}'\n")
    return concat_path


def render_panel_segment(image_path: Path, frame_count: int, segment_path: Path, config: VideoBuildConfig) -> Path | None:
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    if segment_path.exists() and not config.overwrite:
        return None
    encoder = choose_h264_encoder(config.encoder)
    if config.background_style == "blur":
        frame_path = composed_blur_frame_path(segment_path)
        render_blurred_panel_frame(image_path, frame_path, config)
        run(
            [
                "ffmpeg", "-hide_banner", "-y", "-loop", "1", "-framerate", str(config.fps),
                "-i", str(frame_path),
                "-vf", f"scale={config.width}:{config.height}:out_range=tv,fps={config.fps},format=yuv420p",
                "-map", "0:v:0", *h264_encoder_args(encoder, config.preset, config.cq),
                "-frames:v", str(frame_count),
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                "-an", "-movflags", "+faststart", str(segment_path),
            ]
        )
        return frame_path
    if config.background_style == "image":
        if config.background_image is None:
            raise ValueError("--background-image is required when --background-style image is used.")
        run(
            [
                "ffmpeg", "-hide_banner", "-y",
                "-loop", "1", "-framerate", str(config.fps), "-i", str(config.background_image),
                "-loop", "1", "-framerate", str(config.fps), "-i", str(image_path),
                "-filter_complex", background_image_filter(config),
                "-map", "[v]", *h264_encoder_args(encoder, config.preset, config.cq),
                "-frames:v", str(frame_count),
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                "-an", "-movflags", "+faststart", str(segment_path),
            ]
        )
        return None
    run(
        [
            "ffmpeg", "-hide_banner", "-y", "-loop", "1", "-framerate", str(config.fps),
            "-i", str(image_path), "-vf", video_filter(config), "-map", "0:v:0",
            *h264_encoder_args(encoder, config.preset, config.cq), "-frames:v", str(frame_count),
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-an", "-movflags", "+faststart", str(segment_path),
        ]
    )
    return None


def build_item_narration_wav(
    item_dir: Path,
    assets: list[PanelAsset],
    work_dir: Path,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for idx, asset in enumerate(assets):
        inputs += ["-guess_layout_max", "0", "-i", str(asset.audio_path)]
        label = f"a{idx}"
        filter_parts.append(
            f"[{idx}:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=mono,"
            f"apad,atrim=duration={asset.visual_duration:.6f},asetpts=N/SR/TB[{label}]"
        )
        concat_inputs.append(f"[{label}]")
    filter_parts.append(
        "".join(concat_inputs)
        + f"concat=n={len(assets)}:v=0:a=1,aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=mono[a]"
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-y", *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[a]", "-c:a", "pcm_s16le", str(output_path),
        ]
    )


def build_from_segments(chapter_dir: Path, assets: list[PanelAsset], work_dir: Path, output_path: Path, config: VideoBuildConfig) -> None:
    segment_dir = work_dir / "segments"
    segments: list[Path] = []
    composed_frames: list[Path] = []
    for idx, asset in enumerate(assets, start=1):
        segment_path = segment_dir / f"{idx:04d}_{asset.image_path.stem}.mp4"
        print(
            f"  segment {idx:03d}/{len(assets):03d}: {asset.image_path.name} "
            f"audio={asset.audio_duration:.3f}s visual={asset.visual_duration:.3f}s",
            flush=True,
        )
        composed_frame = render_panel_segment(asset.image_path, asset.frame_count, segment_path, config)
        if composed_frame is not None:
            composed_frames.append(composed_frame)
        segments.append(segment_path)

    concat_path = write_video_concat_file(segments, work_dir, chapter_dir.name)
    video_only_path = work_dir / f"{chapter_dir.name}_video.mp4"
    run(
        [
            "ffmpeg", "-hide_banner", "-y", "-fflags", "+genpts",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-map", "0:v:0", "-c:v", "copy", "-an",
            "-movflags", "+faststart", str(video_only_path),
        ]
    )
    chapter_audio = item_narration_path(config.audio_root, config.project_root, config.project_name_override, chapter_dir)
    build_item_narration_wav(chapter_dir, assets, work_dir, chapter_audio)
    run(
        [
            "ffmpeg", "-hide_banner", "-y", "-i", str(video_only_path),
            "-guess_layout_max", "0", "-i", str(chapter_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-af", "aformat=channel_layouts=stereo,aresample=48000:async=1:first_pts=0",
            "-movflags", "+faststart", str(output_path),
        ]
    )

    if not config.keep_work:
        for path in segments:
            path.unlink(missing_ok=True)
        for path in composed_frames:
            path.unlink(missing_ok=True)
        video_only_path.unlink(missing_ok=True)
        concat_path.unlink(missing_ok=True)
        try:
            segment_dir.rmdir()
            work_dir.rmdir()
        except OSError:
            pass


def write_image_concat_file(chapter: str, assets: list[PanelAsset], work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    image_list = work_dir / f"{chapter}_images.ffconcat"
    with image_list.open("w", encoding="utf-8", newline="\n") as f:
        f.write("ffconcat version 1.0\n")
        for asset in assets:
            f.write(f"file '{ffconcat_path(asset.image_path)}'\n")
            f.write(f"duration {asset.visual_duration:.6f}\n")
        f.write(f"file '{ffconcat_path(assets[-1].image_path)}'\n")
    return image_list


def build_from_image_concat(chapter_dir: Path, assets: list[PanelAsset], work_dir: Path, output_path: Path, config: VideoBuildConfig) -> None:
    image_list = write_image_concat_file(chapter_dir.name, assets, work_dir)
    chapter_audio = item_narration_path(config.audio_root, config.project_root, config.project_name_override, chapter_dir)
    build_item_narration_wav(chapter_dir, assets, work_dir, chapter_audio)
    encoder = choose_h264_encoder(config.encoder)
    run(
        [
            "ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(image_list),
            "-i", str(chapter_audio), "-vf", video_filter(config),
            "-map", "0:v:0", "-map", "1:a:0",
            *h264_encoder_args(encoder, config.preset, config.cq), "-c:a", "aac", "-b:a", config.audio_bitrate,
            "-af", "aformat=channel_layouts=stereo,aresample=48000:async=1:first_pts=0",
            "-movflags", "+faststart", str(output_path),
        ]
    )
    if not config.keep_work:
        image_list.unlink(missing_ok=True)


def build_one_chapter(chapter_dir: Path, config: VideoBuildConfig) -> None:
    output_dir = item_output_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = project_work_dir(config) / chapter_dir.name
    output_path = output_dir / f"item_{chapter_dir.name}.mp4"
    if output_path.exists() and not config.overwrite:
        print(f"[{chapter_dir.name}] exists, skipping: {output_path}", flush=True)
        return
    assets = collect_panel_assets(
        chapter_dir,
        project_root=config.project_root,
        audio_root=config.audio_root,
        project_name_override=config.project_name_override,
        fps=config.fps,
    )
    audio_minutes = sum(asset.audio_duration for asset in assets) / 60
    visual_minutes = sum(asset.visual_duration for asset in assets) / 60
    print(
        f"\n[{chapter_dir.name}] {len(assets)} panels, audio={audio_minutes:.2f} minutes, "
        f"frame-aligned visual={visual_minutes:.2f} minutes",
        flush=True,
    )
    if config.render_mode == "segments":
        build_from_segments(chapter_dir, assets, work_dir, output_path, config)
    else:
        build_from_image_concat(chapter_dir, assets, work_dir, output_path, config)
    validate_video_stream(output_path, width=config.width, height=config.height)


def validate_config(config: VideoBuildConfig) -> None:
    if config.width <= 0 or config.height <= 0 or config.fps <= 0:
        raise ValueError("Width, height, and fps must be positive.")
    if config.cq < 0:
        raise ValueError("CQ must be non-negative.")
    if config.workers < 1:
        raise ValueError("--workers must be at least 1.")
    if config.blur_downscale < 1:
        raise ValueError("--blur-downscale must be at least 1.")
    if config.blur_backend not in {"auto", "vulkan", "cpu"}:
        raise ValueError("--blur-backend must be auto, vulkan, or cpu.")
    if config.background_style == "image":
        if config.render_mode != "segments":
            raise ValueError("--background-style image requires --render-mode segments.")
        if config.background_image is None:
            raise ValueError("--background-image is required when --background-style image is used.")
        if not config.background_image.exists():
            raise FileNotFoundError(f"Background image not found: {config.background_image}")
        if config.background_image.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported background image type: {config.background_image}")


def build_item_videos(config: VideoBuildConfig) -> Path:
    validate_config(config)
    items = selected_item_dirs(config)
    if not items:
        raise FileNotFoundError(f"No item folders selected under {config.project_root.resolve()}")
    if config.workers == 1 or len(items) <= 1:
        for item_dir in items:
            build_one_chapter(item_dir, config)
    else:
        print(f"Rendering with {config.workers} item worker(s).", flush=True)
        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            list(executor.map(lambda item_dir: build_one_chapter(item_dir, config), items))
    output_dir = item_output_dir(config)
    print(f"\nVideos written to: {output_dir}", flush=True)
    return output_dir
