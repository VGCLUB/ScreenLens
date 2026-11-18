"""Single-line subtitles keep their font, show complete pages, and never inpaint."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
import pytest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from screenlens import subtitle, live
from screenlens.appearance import Appearance
from screenlens.config import Settings
from screenlens.line_layout import paginate_line, single_line_layout, subtitle_font, page_read_ms
from screenlens.pipeline import Region


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "segoeui.ttf", "YuGothM.ttc"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/"+name)
    return application


@pytest.mark.parametrize("text", [
    "这是较长的中文翻译，所有内容都要完整显示。\n但始终只能有一行。"*3,
    "A long subtitle must keep the same font size and display every word without wrapping. "*3,
    "今日はいい天気ですね。\n画面の文字を翻訳します。"*4,
    "هذا نص طويل لترجمة الشاشة بحجم ثابت وسطر واحد فقط. "*4,
    "A 👨‍👩‍👧‍👦 family with e\u0301 and café 🎉 is on screen. "*5,
])
def test_pages_preserve_all_unicode_at_one_fixed_size(app, text):
    font = subtitle_font(28)
    pages = paginate_line(text, 300, font)
    assert len(pages) > 1
    assert "".join("".join(pages).split()) == "".join(text.split())
    for page in pages:
        layout = single_line_layout(page, 300, font)
        assert layout.lineCount() == 1 and layout.font().pixelSize() == 28
        assert layout.lineAt(0).naturalTextWidth() <= 300.1
        assert "\n" not in page and "\r" not in page


def test_box_ignores_original_pixels_style_and_never_calls_repair(app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Single-line box must not estimate fonts or repair original pixels")
    monkeypatch.setattr(subtitle, "repair_patch", forbidden)
    monkeypatch.setattr(subtitle, "caption_patch", forbidden)
    monkeypatch.setattr(live, "detect_appearance", forbidden)
    settings = Settings(subtitle_font_size=30, subtitle_lock_style=False)
    frame = np.full((120, 600, 3), 255, np.uint8)
    assert live.LiveStyleLock().apply(frame, [], settings) == (None, None)
    overlay = subtitle.SubtitleOverlay(settings)
    overlay.resize(600, 120)
    overlay.set_text("只加半透明底框，不修改原字幕")
    before = overlay.grab().toImage()
    overlay.set_visual(frame, Appearance([(0, 0, 500, 80)], color=(255, 0, 0), font_px=72))
    after = overlay.grab().toImage()
    assert before == after and np.all(frame == 255)
    assert overlay.caption_metrics["logical_font_px"] == 30 and overlay.caption_metrics["lines"] == 1
    assert 139 <= after.pixelColor(5, 60).alpha() <= 141
    assert after.pixelColor(5, 0).alpha() == 0
    overlay.close()


def test_paging_keeps_geometry_and_font_and_new_cue_replaces_old_pages(app, monkeypatch):
    moves = []
    monkeypatch.setattr(subtitle, "place_native_window", lambda *args: moves.append(args))
    overlay = subtitle.SubtitleOverlay(Settings())
    overlay._native_ready = True
    overlay.set_region(Region(100, 200, 600, 120))
    text = "这是一句很长的字幕，必须按单行分段显示，每次换段都保持相同字号，完整展示所有文字。"*3
    overlay.set_text(text)
    overlay.mark_complete()
    pages = list(overlay.pages)
    geometry = overlay.geometry()
    assert len(pages) > 1 and overlay.page_timer.isActive()
    overlay.clear_after_reading()
    assert overlay.clear_timer.remainingTime() > page_read_ms(pages[0], 1200)
    for index, page in enumerate(pages):
        assert overlay.pages[overlay.page_index] == page
        assert overlay._page_layout.lineCount() == 1
        assert overlay.caption_metrics["logical_font_px"] == 26
        assert overlay.caption_metrics["lines"] == 1 and overlay.geometry() == geometry
        if index+1 < len(pages):
            overlay.advance_page()
    assert not overlay.page_timer.isActive()
    overlay.advance_page()
    assert overlay.page_index == len(pages)-1  # Do not loop or replay old pages.
    overlay.set_text("新的一句")
    assert overlay.pages == ["新的一句"] and overlay.page_index == 0
    assert not overlay.clear_timer.isActive() and not overlay.page_timer.isActive()
    assert len(moves) == 1
    overlay.close()


def test_short_and_long_captions_use_identical_background_band(app):
    overlay = subtitle.SubtitleOverlay(Settings())
    overlay.resize(600, 180)
    overlay.set_text("短句")
    first = overlay.grab().toImage()
    overlay.set_text("一段很长的译文\n"*30)
    second = overlay.grab().toImage()
    assert [first.pixelColor(5, y).alpha() for y in range(180)] == [second.pixelColor(5, y).alpha() for y in range(180)]
    assert overlay.caption_metrics["logical_font_px"] == 26 and overlay.caption_metrics["lines"] == 1
    assert len(overlay.pages) > 1
    overlay.close()


def test_narrow_selection_grows_viewport_without_changing_ocr_region_or_font(app):
    overlay = subtitle.SubtitleOverlay(Settings(subtitle_font_size=32))
    selected = Region(100, 200, 40, 20, 2)
    overlay.set_region(selected)
    displayed = overlay.display_region()
    assert overlay.region == selected
    assert displayed.height >= overlay._band_height()*2 and displayed.width >= (32*2+24)*2
    assert abs((displayed.top+displayed.height/2)-(selected.top+selected.height/2)) <= 1
    overlay.close()


def test_legacy_settings_migrate_once_without_changing_encrypted_key(tmp_path):
    path = tmp_path/"settings.json"
    Settings(api_key="fake-key-not-real", subtitle_style_mode="glyph", subtitle_opacity=0).save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    encrypted_key = raw["encrypted_key"]
    raw.pop("subtitle_box_revision")
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = Settings.load(path)
    assert restored.subtitle_style_mode == "box" and restored.subtitle_opacity == 55
    assert restored.api_key == "fake-key-not-real" and restored.subtitle_font_size == 26
    assert json.loads(path.read_text(encoding="utf-8"))["encrypted_key"] == encrypted_key
    restored.subtitle_opacity = 37
    restored.save(path)
    assert Settings.load(path).subtitle_opacity == 37


def test_zero_reading_time_can_clear_pages_immediately(app):
    overlay = subtitle.SubtitleOverlay(Settings(subtitle_min_read_ms=0))
    overlay.resize(300, 100)
    overlay.set_text("很长的字幕用于验证立即跟随原文消失。"*10)
    assert len(overlay.pages) > 1
    overlay.clear_after_reading()
    assert not overlay.text and not overlay.page_timer.isActive()
    overlay.close()
