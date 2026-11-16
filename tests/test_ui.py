"""Headless application checks; no desktop input, screenshots, or external API calls."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

from screenlens.config import Settings
from screenlens.pipeline import Pipeline
from screenlens import ui


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    # The offscreen platform has no Windows font discovery of its own.
    for font in ("msyh.ttc", "segoeui.ttf", "YuGothM.ttc"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    application.setStyle("Fusion")
    application.setStyleSheet(ui.STYLE)
    return application


def test_settings_dialog_preserves_and_clears_provider_key(app):
    dialog = ui.SettingsDialog(Settings(api_key="fake-test-key"))
    assert dialog.collect().api_key == "fake-test-key"
    assert dialog.collect().model == "deepseek-flash"
    dialog.provider.setCurrentText("自定义兼容 API")
    assert dialog.collect().api_key == ""
    assert dialog.model.text() == ""
    dialog.close()


def test_ui_ignores_old_results_and_renders(app, monkeypatch):
    class FakeHotkeys:
        registered = [1, 2, 3, 4]
        def __init__(self, window):
            pass
        def close(self):
            pass

    monkeypatch.setattr(ui, "Hotkeys", FakeHotkeys)
    monkeypatch.setattr(Pipeline, "start_workers", lambda _: None)
    monkeypatch.setattr(ui, "exclude_from_capture", lambda _: True)
    window = ui.MainWindow(Settings())
    window.epoch = 10
    window.running = True
    window.on_source(10, 20, "今日はいい天気ですね。", 117.0)
    window.on_chunk(10, 20, "今天天气真好。")
    window.on_chunk(9, 20, "outdated epoch")
    window.on_chunk(10, 19, "outdated sequence")
    assert window.translated_text.toPlainText() == "今天天气真好。"
    assert window.subtitle.text == "今天天气真好。"
    window.on_done(10, 20, 950, 260, False)
    assert "0.26" in window.metrics.text()
    window.show()
    app.processEvents()
    Path("qa").mkdir(exist_ok=True)
    assert window.grab().save("qa/ui-preview.png")
    window.toggle_compact()
    app.processEvents()
    assert not window.source_card.isVisible()
    assert window.grab().save("qa/ui-compact-preview.png")
    window.on_source(10, 21, "Next subtitle", 60.0)
    assert window.subtitle.text == ""
    window.on_chunk(10, 20, "outdated caption")
    assert window.subtitle.text == ""
    window.on_chunk(10, 21, "下一句字幕")
    assert window.subtitle.text == "下一句字幕"
    window.on_source(10, 21, "", 60.0)
    assert window.subtitle.text == "下一句字幕"
    assert not window.subtitle.clear_timer.isActive()
    window.on_done(10, 21, 1800, 200, False)
    assert window.subtitle.text == "下一句字幕" and window.subtitle.clear_timer.isActive()
    window.close()


def test_english_japanese_real_ocr():
    import cv2
    import numpy as np
    from screenlens.ocr import OcrEngine
    fixture = Path("qa/ocr-sample.png")
    if not fixture.exists():
        pytest.skip("Optional OCR benchmark fixture not generated")
    image = cv2.imdecode(np.fromfile(str(fixture), dtype=np.uint8), cv2.IMREAD_COLOR)
    text = OcrEngine("中 / 英 / 日（自动）").recognize(image)
    assert "Please select the text on your screen." in text
    assert "今日はいい天気ですね。画面の文字を翻訳します。" in text
    assert "The translation updates automatically." in text


def test_video_dialog_can_close_without_shadowing_qdialog_done(app):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog
    from screenlens.video_ui import VideoDialog
    dialog = VideoDialog(Settings())
    QTimer.singleShot(0, dialog.reject)
    assert dialog.exec() == QDialog.DialogCode.Rejected
