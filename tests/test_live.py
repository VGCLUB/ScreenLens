"""Live-caption regressions: interference, typography, and independent capture."""
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import Qt

from screenlens.appearance import Appearance, caption_patch
from screenlens.config import Settings
from screenlens.live import CaptionSelector, LiveStyleLock, is_windows_watermark
from screenlens.ocr import OcrResult, TextLine


@pytest.mark.parametrize("text", ["激活 Windows", "转到设置以激活 Windows。", "Activate Windows",
                                      "Go to Settings to activate Windows", "激活 Windos", "Windows のライセンス認証"])
def test_full_watermark_lines_are_filtered(text):
    assert is_windows_watermark(text)
    assert CaptionSelector(Settings()).select([TextLine(text, (700, 115, 240, 20))], (180, 1000, 3)) == []


def test_activation_dialogue_is_not_a_watermark():
    assert not is_windows_watermark("You should activate Windows before continuing.")
    assert not is_windows_watermark("我想知道如何激活 Windows，请告诉我。")


def test_primary_two_line_caption_survives_watermarks_hud_and_ocr_jitter():
    selector = CaptionSelector(Settings())
    main = [TextLine("The first subtitle line", (250, 55, 500, 30)),
            TextLine("The second subtitle line", (270, 92, 460, 31))]
    noise = [TextLine("Activate Windows", (810, 130, 170, 18)),
             TextLine("Go to Settings to activate Windows", (700, 151, 280, 16)),
             TextLine("FPS 144", (5, 10, 70, 12))]
    assert selector.select(main + noise, (180, 1000, 3)) == main
    assert selector.ignored == 3
    selector.commit(main)
    assert selector.select(noise + [TextLine("GAME MENU", (400, 0, 200, 15))], (180, 1000, 3)) == []
    next_line = TextLine("Next sentence", (380, 91, 240, 29))
    assert selector.select(noise + [next_line], (180, 1000, 3)) == [next_line]


def test_small_corner_text_alone_does_not_become_a_subtitle():
    selector = CaptionSelector(Settings())
    assert selector.select([TextLine("HUD READY", (5, 40, 110, 20))], (120, 1000, 3)) == []


def test_user_ignore_rule_and_full_region_mode():
    lines = [TextLine("Custom broadcast logo", (0, 0, 200, 20)), TextLine("Hello", (300, 50, 200, 30))]
    selector = CaptionSelector(Settings(subtitle_ignore_text="broadcast", subtitle_focus_main=False))
    assert selector.select(lines, (120, 1000, 3)) == lines[1:]
    assert CaptionSelector(Settings(subtitle_ignore_watermarks=False, subtitle_focus_main=False)).select(
        [TextLine("Activate Windows", (0, 0, 200, 20))], (120, 1000, 3))


@pytest.mark.skipif(not Path("C:/Windows/Fonts/msyh.ttc").exists(), reason="Optional Windows CJK font fixture")
def test_style_lock_keeps_font_and_baseline_across_ocr_box_jitter():
    image = Image.new("RGB", (700, 180), (35, 60, 90))
    ImageDraw.Draw(image).text((140, 85), "HELLO SCREENLENS", font=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 32), fill="white")
    frame = np.asarray(image)[:, :, ::-1].copy()
    lock = LiveStyleLock()
    first, mask = lock.apply(frame, [TextLine("HELLO SCREENLENS", (140, 90, 350, 32))], Settings(subtitle_style_mode="glyph"), 1.5)
    second, _ = lock.apply(frame, [TextLine("New source", (230, 87, 220, 38))], Settings(subtitle_style_mode="glyph"), 1.5)
    assert first.font_px == second.font_px
    assert first.font_path == second.font_path and first.stroke == second.stroke
    assert first.target_box == second.target_box and first.fixed_font and second.fixed_font
    assert mask.shape == frame.shape[:2] and mask.any()
    sizes = []
    for text in ("你好", "这是一个明显更长的译文，但是字号和基线仍然保持一致。", "长句" * 300):
        box, rgba, metrics = caption_patch(frame.shape, second, text)
        sizes.append(metrics["font_px"])
        assert rgba[:, :, 3].any()
    assert sizes == [first.font_px]*3
    assert metrics["overflow"]


def test_capture_continues_during_slow_ocr_and_translation(monkeypatch):
    from screenlens import pipeline
    from screenlens.pipeline import Pipeline, Region
    ocr_started, release_ocr, translating, release_translation = [threading.Event() for _ in range(4)]
    requests, errors = [], []

    class Capture:
        def grab(self, region):
            return np.zeros((100, 800, 4), np.uint8)
        def close(self):
            pass

    class Engine:
        def __init__(self, profile):
            pass
        def analyze(self, frame):
            ocr_started.set()
            assert release_ocr.wait(3)
            lines = [TextLine("MAIN SUBTITLE", (250, 30, 300, 30)), TextLine("Activate Windows", (600, 70, 190, 18))]
            return OcrResult("unused full OCR result", lines)

    class Style:
        def apply(self, frame, lines, settings, scale):
            return Appearance([line.box for line in lines]), np.zeros(frame.shape[:2], np.uint8)

    class Translator:
        def translate(self, text, settings, cancel, callback):
            requests.append(text)
            translating.set()
            assert release_translation.wait(3)
            callback("翻译完成")
            return SimpleNamespace(cached=False)
        def close(self):
            pass

    monkeypatch.setattr(pipeline.mss, "MSS", Capture)
    monkeypatch.setattr(pipeline, "OcrEngine", Engine)
    monkeypatch.setattr(pipeline, "LiveStyleLock", Style)
    monkeypatch.setattr(pipeline, "Translator", Translator)
    worker = Pipeline(Settings(interval_ms=150))
    worker.events.error.connect(lambda *args: errors.append(args), Qt.ConnectionType.DirectConnection)
    worker.configure(region=Region(0, 0, 800, 100), running=True)
    worker.start_workers()
    try:
        assert ocr_started.wait(2)
        with worker.condition:
            initial = worker.frame_number
            assert worker.condition.wait_for(lambda: worker.frame_number >= initial+3, timeout=2)
        assert not requests
        release_ocr.set()
        assert translating.wait(2)
        assert requests == ["MAIN SUBTITLE"]
        before = worker.latest_visual()
        with worker.condition:
            initial = worker.frame_number
            assert worker.condition.wait_for(lambda: worker.frame_number >= initial+3, timeout=2)
        after = worker.latest_visual()
        assert after[2]["revision"] > before[2]["revision"]
        assert after[2]["frame"] is not before[2]["frame"]
        assert after[2]["ignored"] == 1 and not errors
    finally:
        release_ocr.set()
        release_translation.set()
        worker.close()
        for thread in worker.threads:
            thread.join(3)
            assert not thread.is_alive()
