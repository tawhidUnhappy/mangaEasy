from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


Runner = Callable[[list[str]], object]
Logger = Callable[[str], None]


@dataclass(frozen=True)
class BlurBackgroundOptions:
    sigma: float = 28.0
    downscale: int = 4
    brightness: float = -0.06
    saturation: float = 1.08
    kernel_size: int = 19
    backend: str = "auto"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "BlurBackgroundOptions":
        values = values or {}
        return cls(
            sigma=_as_float(values.get("blur_sigma"), cls.sigma),
            downscale=_as_int(values.get("blur_downscale"), cls.downscale),
            brightness=_as_float(values.get("background_brightness"), cls.brightness),
            saturation=_as_float(values.get("background_saturation"), cls.saturation),
            kernel_size=_as_int(values.get("blur_kernel_size"), cls.kernel_size),
            backend=str(values.get("blur_backend") or cls.backend).lower(),
        )


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalized_downscale(value: int) -> int:
    return max(1, min(16, int(value or 1)))


def blur_work_size(width: int, height: int, options: BlurBackgroundOptions) -> tuple[int, int]:
    downscale = normalized_downscale(options.downscale)
    return max(16, width // downscale), max(16, height // downscale)


def scaled_blur_sigma(options: BlurBackgroundOptions) -> float:
    return max(0.01, float(options.sigma) / normalized_downscale(options.downscale))


def vulkan_kernel_size(options: BlurBackgroundOptions) -> int:
    size = max(1, min(127, int(options.kernel_size or 19)))
    return size if size % 2 else size + 1


def ffmpeg_cpu_blur_filter(width: int, height: int, options: BlurBackgroundOptions) -> str:
    small_w, small_h = blur_work_size(width, height, options)
    sigma = scaled_blur_sigma(options)
    return (
        "[0:v]format=rgba,split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,scale={small_w}:{small_h},"
        f"gblur=sigma={sigma:.3f}:steps=1,scale={width}:{height},"
        f"eq=brightness={options.brightness}:saturation={options.saturation}[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        "setsar=1,format=rgba[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=rgb24,setsar=1[v]"
    )


def ffmpeg_vulkan_blur_filter(width: int, height: int, options: BlurBackgroundOptions) -> str:
    small_w, small_h = blur_work_size(width, height, options)
    sigma = scaled_blur_sigma(options)
    kernel = vulkan_kernel_size(options)
    return (
        "[0:v]format=rgba,split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=rgba,hwupload,"
        f"scale_vulkan=w={small_w}:h={small_h},"
        f"gblur_vulkan=sigma={sigma:.3f}:size={kernel},"
        f"scale_vulkan=w={width}:h={height},hwdownload,format=rgba,"
        f"eq=brightness={options.brightness}:saturation={options.saturation}[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        "setsar=1,format=rgba[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=rgb24,setsar=1[v]"
    )


def render_blurred_panel_ffmpeg(
    image_path: Path,
    output_path: Path,
    width: int,
    height: int,
    options: BlurBackgroundOptions,
    runner: Runner,
    *,
    log: Logger | None = None,
) -> str:
    """Render one complete panel frame; returns the backend used."""
    backend = options.backend if options.backend in {"auto", "vulkan", "cpu"} else "auto"
    if backend in {"auto", "vulkan"}:
        try:
            runner(
                [
                    "ffmpeg", "-hide_banner", "-y",
                    "-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk",
                    "-i", str(image_path),
                    "-filter_complex", ffmpeg_vulkan_blur_filter(width, height, options),
                    "-map", "[v]", "-frames:v", "1", str(output_path),
                ]
            )
            return "vulkan"
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            if backend == "vulkan":
                raise
            if log is not None:
                log(f"[blur] Vulkan blur failed for {image_path.name}; using CPU fallback ({_short_error(exc)})")

    runner(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(image_path),
            "-filter_complex", ffmpeg_cpu_blur_filter(width, height, options),
            "-map", "[v]", "-frames:v", "1", str(output_path),
        ]
    )
    return "cpu"


def compose_blurred_panel_pil(
    image_path: Path,
    output_path: Path,
    width: int,
    height: int,
    options: BlurBackgroundOptions,
) -> None:
    """Last-resort compositor used only if ffmpeg is unavailable."""
    small_w, small_h = blur_work_size(width, height, options)
    sigma = scaled_blur_sigma(options)
    with Image.open(image_path).convert("RGBA") as source:
        bg = ImageOps.fit(source.convert("RGB"), (width, height), Image.LANCZOS)
        bg = bg.resize((small_w, small_h), Image.BILINEAR)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=sigma))
        bg = bg.resize((width, height), Image.BILINEAR)
        if options.brightness:
            bg = ImageEnhance.Brightness(bg).enhance(max(0.0, 1.0 + options.brightness))
        if options.saturation != 1.0:
            bg = ImageEnhance.Color(bg).enhance(max(0.0, options.saturation))

        fg = ImageOps.contain(source, (width, height), Image.LANCZOS)
        frame = bg.convert("RGBA")
        frame.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2), fg)
        frame.convert("RGB").save(output_path, "PNG", optimize=True)


def _short_error(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        text = (exc.stderr or exc.stdout or str(exc)).strip().splitlines()
        return text[-1] if text else str(exc)
    return str(exc)
