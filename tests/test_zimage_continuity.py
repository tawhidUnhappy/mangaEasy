from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from mangaeasy.assets.tools.generate_zimage import load_pipeline, normalize_batch_entries


def _args():
    return SimpleNamespace(width=1024, height=576, steps=9)


def test_generic_zimage_batch_remains_text_to_image():
    entries = normalize_batch_entries(
        [{"prompt": "A quiet blue sky", "output": "frame.png", "seed": 7}],
        _args(),
        lambda: 99,
    )

    assert entries[0]["generation_mode"] == "text-to-image"
    assert entries[0]["seed"] == 7
    assert "init_image" not in entries[0]


def test_zimage_continuation_entry_is_explicit_and_bounded(tmp_path):
    init_image = tmp_path / "scene_001.png"
    output = tmp_path / "scene_002.png"
    entries = normalize_batch_entries(
        [{
            "prompt": "The same traveler takes one more step",
            "output": str(output),
            "generation_mode": "continue-previous",
            "init_image": str(init_image),
            "strength": 0.45,
        }],
        _args(),
        lambda: 99,
    )

    assert entries[0]["generation_mode"] == "continue-previous"
    assert entries[0]["init_image"] == init_image
    assert entries[0]["strength"] == 0.45

    with pytest.raises(ValueError, match="between 0.35 and 0.65"):
        normalize_batch_entries(
            [{
                "prompt": "Unsafe strength",
                "output": str(output),
                "generation_mode": "continue-previous",
                "init_image": str(init_image),
                "strength": 0.9,
            }],
            _args(),
            lambda: 99,
        )


def test_zimage_text_mode_rejects_silent_init_image(tmp_path):
    with pytest.raises(ValueError, match="require generation_mode"):
        normalize_batch_entries(
            [{
                "prompt": "Ambiguous mode",
                "output": str(tmp_path / "out.png"),
                "init_image": str(tmp_path / "init.png"),
            }],
            _args(),
            lambda: 99,
        )


def test_zimage_img2img_pipeline_reuses_loaded_components(monkeypatch):
    fake_diffusers = ModuleType("diffusers")

    class TextPipeline:
        @classmethod
        def from_pretrained(cls, model, **kwargs):
            instance = cls()
            instance.model = model
            return instance

    class Img2ImgPipeline:
        @classmethod
        def from_pipe(cls, source):
            instance = cls()
            instance.source = source
            return instance

    fake_diffusers.ZImagePipeline = TextPipeline
    fake_diffusers.ZImageImg2ImgPipeline = Img2ImgPipeline
    monkeypatch.setitem(sys.modules, "diffusers", fake_diffusers)

    text_pipe, img2img_pipe = load_pipeline(
        "local-model",
        "cpu",
        SimpleNamespace(float32="float32"),
        with_img2img=True,
    )

    assert img2img_pipe.source is text_pipe
