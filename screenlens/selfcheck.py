"""Native overlay check against a synthetic window owned by this application."""
from __future__ import annotations

import ctypes
import json
import os
import time
import traceback
from ctypes import wintypes
from pathlib import Path


def run_overlay_check(output_path: str) -> int:
    # This is intentionally separate from the default headless self-test.
    # No input is injected; only the program's own synthetic window is captured.
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ.pop("SCREENLENS_QA", None)
    import mss
    import numpy as np
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QFont, QPainter
    from PySide6.QtWidgets import QApplication, QWidget
    from .config import Settings
    from .pipeline import Region
    from .subtitle import SubtitleOverlay

    report = {"scope": "synthetic windows owned by ScreenLens; no desktop input or cloud API"}
    app = QApplication([])
    fixture = overlay = None

    def settle(milliseconds=180):
        deadline = time.perf_counter() + milliseconds / 1000
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
        ctypes.windll.dwmapi.DwmFlush()

    def native_bounds(widget):
        rect = wintypes.RECT()
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        assert user32.GetWindowRect(int(widget.winId()), ctypes.byref(rect))
        return Region(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    class SourceWindow(QWidget):
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#1c3348"))
            painter.setPen(QColor("#8eaac1"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(QRect(20, 15, self.width() - 40, 50), Qt.AlignmentFlag.AlignCenter,
                             "SCREENLENS / SYNTHETIC SUBTITLE TEST")
            painter.setPen(Qt.GlobalColor.white)
            painter.setFont(QFont("Segoe UI", 21, QFont.Weight.Bold))
            painter.drawText(QRect(0, 110, self.width(), 90),
                             Qt.AlignmentFlag.AlignCenter, "SUBTITLE SOURCE 123")
            painter.setPen(QColor("#8093a0"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(QRect(0, 185, self.width()-20, 25), Qt.AlignmentFlag.AlignRight, "Activate Windows")
            painter.drawText(QRect(0, 210, self.width()-20, 25), Qt.AlignmentFlag.AlignRight, "Go to Settings to activate Windows")

    try:
        screen = app.primaryScreen()
        available = screen.availableGeometry()
        fixture = SourceWindow(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        fixture.setWindowTitle("ScreenLens synthetic subtitle check")
        fixture.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        fixture.setGeometry(available.center().x() - 320, available.center().y() - 130, 640, 260)
        fixture.show()
        settle()
        bounds = native_bounds(fixture)
        scale = fixture.devicePixelRatioF()
        region = Region(bounds.left + round(20*scale), bounds.top + round(65*scale),
                        bounds.width - round(40*scale), bounds.height - round(75*scale), scale)
        with mss.MSS() as capture:
            baseline = np.asarray(capture.grab(region.as_dict()))[:, :, :3].copy()
            assert float(baseline.std()) > 10, "Synthetic source window is not visible"
            from .ocr import OcrEngine
            from .live import CaptionSelector, LiveStyleLock
            engine = OcrEngine("中 / 英 / 日（自动）")
            source_result = engine.analyze(baseline)
            selector = CaptionSelector(Settings())
            selected = selector.select(source_result.lines, baseline.shape)
            selected_text = "\n".join(line.text for line in selected)
            assert "SUBTITLE SOURCE 123" in selected_text, source_result.text
            assert "activate" not in selected_text.casefold(), selected_text
            assert "Activate Windows" in source_result.text, source_result.text
            report["watermark_ocr"] = source_result.text
            report["primary_subtitle_after_filter"] = selected_text
            appearance, mask = LiveStyleLock().apply(baseline, selected, Settings(), scale)
            assert appearance is None and mask is None, "Box mode must not estimate or erase original glyphs"
            report["original_pixel_repair"] = "disabled: original subtitle stays behind the translucent box"
            overlay = SubtitleOverlay(Settings())
            overlay.set_region(region)
            overlay.set_visual(baseline, appearance, mask)
            overlay.set_text("译文直接覆盖在原字幕的位置")
            settle()
            assert overlay.isVisible(), overlay.failure_reason
            actual = native_bounds(overlay)
            assert actual == region, (actual, region)
            covered = np.asarray(capture.grab(region.as_dict()))[:, :, :3].copy()
            difference = float(np.abs(baseline.astype(float) - covered.astype(float)).mean())
            assert difference < 0.1, f"Overlay leaked into the OCR capture: mean pixel difference {difference}"
            report["overlay_physical_bounds"] = actual.as_dict()
            report["source_capture_mean_pixel_difference"] = round(difference, 6)
            report["capture_exclusion"] = "passed: OCR capture still sees the original subtitle"
            get_style = ctypes.windll.user32.GetWindowLongPtrW
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            style = get_style(int(overlay.winId()), -20)
            assert style & 0x20 and style & 0x08000000
            report["mouse_passthrough_and_no_activation_styles"] = "passed"
            sizes = []
            for caption in ("短句", "这是较长的完整译文，显示时仍然保持首句的字号和位置。", "译文直接覆盖在原字幕的位置"):
                overlay.set_text(caption)
                settle(70)
                sizes.append(overlay.caption_metrics["font_px"])
                assert overlay.caption_metrics["lines"] == 1
                assert overlay._page_layout.lineCount() == 1
                assert overlay._page_layout.font().pixelSize() == overlay.settings.subtitle_font_size
            assert len(set(sizes)) == 1, sizes
            report["short_and_long_caption_font_pixels"] = sizes
            cached_caption = overlay.grab().toImage()
            overlay.set_visual(None, appearance)
            settle(70)
            assert overlay.grab().toImage() == cached_caption and overlay.isVisible()
            report["ocr_gap_keeps_caption_layer"] = "passed: same rendered text, font and position"
            overlay.set_visual(baseline, appearance, mask)
            caption_image = overlay.grab().toImage()
            alpha = caption_image.pixelColor(round(5*scale), caption_image.height()//2).alpha()
            assert 139 <= alpha <= 141, alpha
            assert caption_image.pixelColor(round(5*scale), 0).alpha() == 0
            report["background_opacity_percent"] = overlay.settings.subtitle_opacity
            # Exercise the real Qt timer rather than manually changing the page.
            from .line_layout import page_read_ms
            overlay.set_text("这是较长的完整译文，字幕每次只显示一行，分段切换时也始终保持同样的字体大小。")
            assert len(overlay.pages) > 1, overlay.pages
            pages = list(overlay.pages)
            overlay.mark_complete()
            overlay.clear_after_reading()
            settle(page_read_ms(pages[0], overlay.settings.subtitle_min_read_ms)+120)
            assert overlay.page_index == 1 and overlay.isVisible()
            assert overlay.caption_metrics["lines"] == 1 and overlay.caption_metrics["font_px"] == sizes[0]
            report["automatic_single_line_pages"] = {"total":len(pages), "shown":overlay.page_index+1, "font_px":sizes[0]}
            overlay.set_text("固定字号 · 单行字幕 · 半透明底框")
            # Composite the program's own rendered components for a local preview.
            preview = fixture.grab()
            preview.setDevicePixelRatio(1)
            caption = overlay.grab()
            caption.setDevicePixelRatio(1)
            painter = QPainter(preview)
            painter.drawPixmap(QPoint(region.left - bounds.left, region.top - bounds.top), caption)
            painter.end()
            preview_path = Path(output_path).with_name("subtitle-preview.png")
            assert preview.save(str(preview_path))
            report["preview"] = preview_path.name
            overlay.suspend(True)
            settle(80)
            assert not overlay.isVisible()
            report["pause_reveals_original"] = "passed"
            overlay.close()
            fixture.close()
            app.processEvents()
            # Original OCR verifies the same capture, without uploading any image.
            text = engine.recognize(covered)
            assert "SUBTITLE SOURCE 123" in text, text
            report["ocr_with_overlay_visible"] = text
        report["ok"] = True
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    finally:
        if overlay is not None:
            overlay.close()
        if fixture is not None:
            fixture.close()
        app.processEvents()
        Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
