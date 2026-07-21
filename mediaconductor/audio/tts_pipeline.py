#!/usr/bin/env python3
"""mediaconductor.audio.tts_pipeline — batch IndexTTS2 audio generation for the video pipeline.

IMPORTANT: Must run inside the external index-tts uv environment.
Invoked automatically by mediaconductor video-audio-indextts.
"""

import argparse
import os
import sys
import traceback
import wave
from pathlib import Path

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent
_INDEX_TTS_DIR = Path(
    os.environ.get("INDEX_TTS_ROOT")
    or os.environ.get("INDEX_TTS_DIR")
    or (_PROJECT_ROOT.parent / "index-tts")
).resolve()

# _INDEX_TTS_DIR at front: gives `indextts` package source priority.
# _PROJECT_ROOT appended: mediaconductor imports resolve, but index-tts's own
# numpy/torch are not shadowed by whichever Python version built the outer venv.
if str(_INDEX_TTS_DIR) not in sys.path:
    sys.path.insert(0, str(_INDEX_TTS_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))

from mediaconductor.config import HF_CACHE_DIR
from mediaconductor.utils import LazyArchiveRunDir, archive_into_run
from mediaconductor.audio.emotion import DEFAULT_EMO_ALPHA, indextts_kwargs, narration_emotion
from mediaconductor.video_pipeline.item_assets import load_narration, validate_calm_narration
from mediaconductor.video_pipeline.common import (
    item_dirs,
    merge_item_selection,
    project_name,
    prune_recent_audio_for_resume,
)

# setdefault, NOT assignment: when this runs inside the index-tts tool env
# the parent already force-pinned HF_HOME under <data>/.mangaeasy/ via
# tool_env() — overriding it here scattered model downloads into a second
# cache at <cwd>/.hf_cache. The fallback only matters for bare standalone runs.
os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
os.environ.setdefault("HF_HUB_OFFLINE", "0")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")

CHECKPOINTS_DIR = _INDEX_TTS_DIR / "checkpoints"

import torch


def _patch_torchaudio_save_win32() -> None:
    """Fall back to stdlib WAV writing when torchaudio asks for torchcodec.

    Recent torchaudio releases route saves through torchcodec. TorchCodec does
    not ship usable Windows wheels for this app's IndexTTS environment, so
    IndexTTS can synthesize audio successfully and then fail at the final
    `torchaudio.save()` call. Patch that exact failure path before IndexTTS is
    imported so its internal save call lands here.
    """
    if sys.platform != "win32":
        return
    try:
        import numpy as np
        import torchaudio
    except ImportError:
        return

    original_save = torchaudio.save

    def save_compat(uri, src, sample_rate, channels_first: bool = True, **kwargs):
        try:
            return original_save(uri, src, sample_rate, channels_first=channels_first, **kwargs)
        except (ImportError, RuntimeError) as exc:
            text = str(exc).lower()
            if "torchcodec" not in text and "save_with_torchcodec" not in text:
                raise

            path = Path(uri)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = src.detach()
            if getattr(data, "is_cuda", False):
                data = data.cpu()
            if channels_first and data.ndim > 1:
                data = data.transpose(0, 1)
            data = data.contiguous()
            pcm = data.numpy()
            if pcm.ndim == 1:
                pcm = pcm.reshape(-1, 1)
            if np.issubdtype(pcm.dtype, np.floating):
                pcm = np.clip(pcm, -1.0, 1.0)
                pcm = (pcm * 32767).astype(np.int16)
            elif pcm.dtype != np.int16:
                pcm = pcm.astype(np.int16)

            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(int(pcm.shape[1]))
                wav.setsampwidth(2)
                wav.setframerate(int(sample_rate))
                wav.writeframes(pcm.tobytes())
            return None

    torchaudio.save = save_compat


_patch_torchaudio_save_win32()

try:
    from indextts.infer_v2 import IndexTTS2
except Exception as exc:
    print(f"[FATAL] Could not import IndexTTS2: {exc}")
    print("  Make sure this runs via: mediaconductor video-audio-indextts")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch IndexTTS2 audio generation.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--audio-root", type=Path, default=Path("audio"))
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--speaker-wav", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Delete the most recently generated audio file plus the previous 5 before "
                             "generating, in case the last run was interrupted mid-write, then continue "
                             "with anything still missing.")
    parser.add_argument("--emo-alpha", type=float, default=DEFAULT_EMO_ALPHA,
                        help="How strongly a narration entry's optional 'emotion' field colors the "
                             "voice (0 disables, ~0.6 keeps the cloned voice recognizable).")
    parser.add_argument("--no-emotion", action="store_true",
                        help="Synthesize in a plain neutral delivery. The calm-policy preflight "
                             "still rejects invalid emotion fields.")
    return parser.parse_args()


def ordered_audio_paths(audio_root: Path, name: str, selected: list[Path]) -> list[Path]:
    """Every expected audio file path, in narration sequence order, across selected items."""
    paths: list[Path] = []
    for item_dir in selected:
        item_audio_dir = audio_root / name / item_dir.name
        try:
            narration = load_narration(item_dir)
        except Exception:
            continue
        for item in narration:
            image_name = item.get("image") if isinstance(item, dict) else None
            if not image_name:
                continue
            paths.append(item_audio_dir / f"{Path(image_name).stem}.wav")
    return paths


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    audio_root = args.audio_root.resolve()
    speaker_wav = args.speaker_wav.resolve()
    name = project_name(project_root, args.project_name)

    if not project_root.exists():
        print(f"[FATAL] Project root not found: {project_root}")
        return 1
    if not speaker_wav.is_file():
        print(f"[FATAL] Speaker WAV not found: {speaker_wav}")
        return 1

    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found in {project_root}")
        return 1

    # Fail before resume archives or model loading. This also protects direct
    # video-audio-indextts calls that did not run work-qa first.
    for item_dir in selected:
        narration = load_narration(item_dir)
        validate_calm_narration(narration, item_dir)

    total_chapters = len(selected)
    print(f"MEDIACONDUCTOR_PROGRESS 0/{total_chapters} Generating audio", flush=True)

    archive_run_dir = LazyArchiveRunDir(audio_root / name / "old")

    if args.resume:
        removed = prune_recent_audio_for_resume(ordered_audio_paths(audio_root, name, selected), archive_run_dir)
        if removed:
            print(
                f"Resume: archived {len(removed)} most recent audio file(s) to re-verify: "
                + ", ".join(p.name for p in removed),
                flush=True,
            )

    per_chapter: list[list[tuple[str, str | None, Path]]] = []
    for item_dir in selected:
        item_audio_dir = audio_root / name / item_dir.name
        item_audio_dir.mkdir(parents=True, exist_ok=True)
        narrations = load_narration(item_dir)
        print(f"\n[{item_dir.name}] {len(narrations)} narration item(s) -> {item_audio_dir}", flush=True)
        jobs_for_item: list[tuple[str, str | None, Path]] = []
        for item in narrations:
            image_name = item.get("image")
            text = (item.get("narration") or item.get("text") or "").strip()
            if not image_name or not text:
                continue
            dst = item_audio_dir / f"{Path(image_name).stem}.wav"
            if dst.exists():
                if not args.overwrite:
                    continue
                archive_into_run(dst, archive_run_dir.dir, subdir=item_dir.name)
            emotion = None if args.no_emotion else narration_emotion(item)
            jobs_for_item.append((text, emotion, dst))
        per_chapter.append(jobs_for_item)

    if archive_run_dir.allocated is not None:
        print(f"Archived previously-generated audio that was overwritten to: {archive_run_dir.allocated}", flush=True)

    to_generate = [job for jobs_for_item in per_chapter for job in jobs_for_item]
    if not to_generate:
        print(f"MEDIACONDUCTOR_PROGRESS {total_chapters}/{total_chapters} Audio already generated", flush=True)
        print("\n[INFO] All audio already generated.")
        return 0

    device = "cuda" if torch.cuda.is_available() else "cpu"
    use_cuda = device == "cuda"
    print(f"\n[INFO] Loading IndexTTS2 (device: {device}, speaker: {speaker_wav.name})...", flush=True)
    tts = IndexTTS2(
        cfg_path=str(CHECKPOINTS_DIR / "config.yaml"),
        model_dir=str(CHECKPOINTS_DIR),
        use_fp16=use_cuda,
        use_cuda_kernel=use_cuda,
        use_deepspeed=False,
    )
    print(f"[INFO] Generating {len(to_generate)} audio files...", flush=True)

    generated = 0
    failures: list[Path] = []
    i = 0
    for chapter_idx, (item_dir, jobs_for_item) in enumerate(zip(selected, per_chapter, strict=False), start=1):
        for text, emotion, dst in jobs_for_item:
            i += 1
            tag = f"  (emotion: {emotion})" if emotion else ""
            print(f"  [{i}/{len(to_generate)}] {dst.parent.name}/{dst.name}{tag}", flush=True)
            try:
                extra = indextts_kwargs(emotion, args.emo_alpha) if args.emo_alpha > 0 else {}
                try:
                    tts.infer(
                        spk_audio_prompt=str(speaker_wav),
                        text=text,
                        output_path=str(dst),
                        verbose=False,
                        **extra,
                    )
                except TypeError:
                    # Older IndexTTS2 builds without emo_text support: emotion
                    # degrades to neutral delivery instead of failing the run.
                    if not extra:
                        raise
                    print("[warn] this IndexTTS2 build has no emo_text support - generating without emotion", flush=True)
                    tts.infer(
                        spk_audio_prompt=str(speaker_wav),
                        text=text,
                        output_path=str(dst),
                        verbose=False,
                    )
                generated += 1
            except Exception as exc:
                print(f"[ERROR] {dst.name}: {exc}")
                traceback.print_exc()
                failures.append(dst)
        print(f"MEDIACONDUCTOR_PROGRESS {chapter_idx}/{total_chapters} Generated audio for {item_dir.name}", flush=True)

    print(f"\n[INFO] Done. Generated {generated} file(s).")
    if failures:
        print(f"[FATAL] Failed to generate {len(failures)} audio file(s):", flush=True)
        for path in failures[:20]:
            print(f"  {path}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
