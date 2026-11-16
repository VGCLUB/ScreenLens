import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from screenlens.config import Settings
from screenlens.pipeline import Region
from screenlens import subtitle


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "segoeui.ttf", "YuGothM.ttc"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)
    return application


def test_overlay_inverse_coordinates_and_negative_monitor_origin():
    region = Region(-3640, 120, 1000, 240)
    assert subtitle.logical_rect(region, QRect(-3840, 0, 1920, 1080), (-3840, 0, 3840, 2160)) == QRect(-3740, 60, 500, 120)


@pytest.mark.parametrize("text", ["原位字幕覆盖，自动换行。" * 3, "A longer translated subtitle that should fit inside the selected rectangle.", "今日はいい天気ですね。\n画面の文字を翻訳します。"])
def test_unicode_layout_fits_without_covering_outside_region(app, text):
    layout, height, size, lines = subtitle.fit_layout(text, 320, 64, 32)
    assert height <= 64 and size <= 32 and lines >= 1
    for index in range(layout.lineCount()):
        line = layout.lineAt(index)
        assert line.naturalTextWidth() <= 320.1


def test_mask_covers_original_and_opacity_is_adjustable(app):
    overlay = subtitle.SubtitleOverlay(Settings(subtitle_style_mode="box", subtitle_opacity=100))
    overlay.resize(600, 100)
    overlay.set_text("译文覆盖原字幕的位置")
    image = overlay.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 255
    assert image.pixelColor(0, 0).red() == 0
    Path("qa").mkdir(exist_ok=True)
    assert image.save("qa/subtitle-render-preview.png")
    settings = Settings(subtitle_style_mode="box", subtitle_opacity=40)
    overlay.apply_settings(settings)
    image = overlay.grab().toImage()
    assert 100 <= image.pixelColor(0, 0).alpha() <= 103
    overlay.set_text("")
    assert not overlay.isVisible()
    overlay.close()


def test_overlay_is_clickthrough_and_offset_does_not_move_ocr_region(app):
    overlay = subtitle.SubtitleOverlay(Settings(subtitle_offset_x=30, subtitle_offset_y=-20))
    region = Region(100, 200, 600, 100)
    overlay.set_region(region)
    assert overlay.region == region
    assert overlay.display_region() == Region(130, 180, 600, 100)
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    overlay.close()


def test_old_settings_keep_key_and_get_overlay_defaults(tmp_path):
    settings = Settings(api_key="test-key-only")
    path = tmp_path / "settings.json"
    settings.save(path)
    import json
    content = json.loads(path.read_text(encoding="utf-8"))
    for name in list(content):
        if name.startswith("subtitle_"):
            content.pop(name)
    path.write_text(json.dumps(content), encoding="utf-8")
    restored = Settings.load(path)
    assert restored.api_key == "test-key-only"
    assert restored.subtitle_cover is True and restored.subtitle_opacity == 0
    assert restored.subtitle_style_mode == "glyph"


def test_default_has_no_rectangle_and_minimum_reading_time_prevents_flash(app):
    overlay = subtitle.SubtitleOverlay(Settings())
    overlay.resize(600,100)
    overlay.set_text("这句翻译会保留最短阅读时间")
    assert overlay.grab().toImage().pixelColor(0,0).alpha() == 0
    overlay.clear_after_reading()
    assert overlay.text and overlay.clear_timer.isActive()
    overlay.set_text("新的字幕")
    assert not overlay.clear_timer.isActive()
    overlay.close()
