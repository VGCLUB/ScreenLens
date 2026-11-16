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
            painter.drawText(QRect(0, self.height() // 2, self.width(), self.height() // 2),
                             Qt.AlignmentFlag.AlignCenter, "SUBTITLE SOURCE 123")

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
        region = Region(bounds.left + 20, bounds.top + bounds.height // 2,
                        bounds.width - 40, bounds.height // 2)
        with mss.MSS() as capture:
            baseline = np.asarray(capture.grab(region.as_dict()))[:, :, :3].copy()
            assert float(baseline.std()) > 10, "Synthetic source window is not visible"
            from .ocr import OcrEngine
            from .appearance import detect_appearance
            engine = OcrEngine("中 / 英 / 日（自动）")
            source_result = engine.analyze(baseline)
            overlay = SubtitleOverlay(Settings())
            overlay.set_region(region)
            overlay.set_visual(baseline, detect_appearance(baseline, source_result.lines))
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
