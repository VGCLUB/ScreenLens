"""Click-through subtitles that cover the selected source subtitle region."""
from __future__ import annotations

import copy
import sys
import time

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QTextLayout, QTextOption, QImage
from PySide6.QtWidgets import QApplication, QWidget

from .config import Settings
from .native import exclude_from_capture, monitor_rect, place_native_window
from .pipeline import Region
from .appearance import replacement_patch


def logical_rect(region: Region, geometry: QRect, native_bounds) -> QRect:
    """Inverse of selection.to_native_region; Windows screen origins stay native."""
    left, top, width, height = native_bounds
    sx, sy = geometry.width() / width, geometry.height() / height
    return QRect(round(geometry.x() + (region.left - left) * sx),
                 round(geometry.y() + (region.top - top) * sy),
                 max(1, round(region.width * sx)), max(1, round(region.height * sy)))


def fit_layout(text: str, width: int, height: int, preferred_size: int, family="Microsoft YaHei UI"):
    """Fit Unicode text into the mask. Never let translated lines escape it."""
    width, height = max(1, width), max(1, height)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\u2028")
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    for size in range(preferred_size, 7, -1):
        font = QFont(family)
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.DemiBold)
        layout = QTextLayout(text, font)
        layout.setTextOption(option)
        layout.beginLayout()
        y = 0.0
        lines = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, y))
            y += line.height()
            lines += 1
        layout.endLayout()
        if y <= height or size == 8:
            return layout, y, size, lines


class SubtitleOverlay(QWidget):
    unavailable = Signal(str)

    def __init__(self, settings: Settings):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setWindowTitle("视译 · 原位字幕")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")
        self.settings = copy.copy(settings)
        self.region: Region | None = None
        self.text = ""
        self.suspended = False
        self._layout_key = None
        self._layout = None
        self._native_ready = False
        self._warned = False
        self.failure_reason = ""
        self.source_frame = None
        self.appearance = None
        self._patch_key = None
        self._patch = None
        self.first_shown_at = 0.0
        self.clear_timer = QTimer(self)
        self.clear_timer.setSingleShot(True)
        self.clear_timer.timeout.connect(lambda: self.set_text(""))

    def apply_settings(self, settings: Settings):
        self.settings = copy.copy(settings)
        self._layout_key = None
        self._position()
        self._sync_visibility()
        self.update()

    def set_region(self, region: Region | None):
        self.region = region
        self._position()
        self._sync_visibility()

    def set_text(self, text: str):
        self.clear_timer.stop()
        if text and not self.text:
            self.first_shown_at = time.monotonic()
        self.text = text
        self._layout_key = None
        self._sync_visibility()
        self.update()

    def set_visual(self, frame, appearance):
        self.source_frame, self.appearance = frame, appearance
        self._patch_key = None
        self.update()

    def clear_after_reading(self):
        elapsed = (time.monotonic()-self.first_shown_at)*1000
        remaining = max(0, self.settings.subtitle_min_read_ms-elapsed)
        if self.text and remaining > 0:
            self.source_frame = None
            self.clear_timer.start(round(remaining))
        else:
            self.set_text("")

    def mark_complete(self):
        if self.text:
            self.first_shown_at = time.monotonic()

    def suspend(self, value: bool):
        self.suspended = value
        self._sync_visibility()

    def display_region(self):
        if self.region is None:
            return None
        return Region(self.region.left + self.settings.subtitle_offset_x,
                      self.region.top + self.settings.subtitle_offset_y,
                      self.region.width, self.region.height)

    def _position(self):
        region = self.display_region()
        if region is None:
            return
        screens = []
        for screen in QApplication.screens():
            bounds = monitor_rect(screen.name(), screen.geometry(), screen.devicePixelRatio())
            if bounds:
                screens.append((screen, bounds))
        if screens:
            screen, bounds = next(((s, b) for s, b in screens
                                   if QRect(*b).contains(QPoint(region.left, region.top))), screens[0])
            self.setGeometry(logical_rect(region, screen.geometry(), bounds))
        if self._native_ready:
            place_native_window(int(self.winId()), region.left, region.top, region.width, region.height)

    def _sync_visibility(self):
        wanted = self.settings.subtitle_cover and self.region is not None and bool(self.text.strip()) and not self.suspended
        if not wanted:
            self.hide()
            return
        if not self._native_ready:
            supported = sys.platform == "win32" and sys.getwindowsversion().build >= 19041
            if not supported or not exclude_from_capture(int(self.winId())):
                self.hide()
                self.failure_reason = "当前系统无法安全排除字幕覆盖层，已保留窗口译文。原位覆盖需要 Windows 10 2004 或更新版本。"
                if not self._warned:
                    self._warned = True
                    self.unavailable.emit(self.failure_reason)
                return
            self._native_ready = True
            self.failure_reason = ""
        self.show()
        self._position()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not self.text.strip():
            return
        if self.settings.subtitle_style_mode == "glyph" and self.source_frame is not None and self.appearance is not None:
            key = (id(self.source_frame), self.text, self.settings.subtitle_font)
            if self._patch_key != key:
                self._patch = replacement_patch(self.source_frame, self.appearance, self.text, self.settings.subtitle_font)
                self._patch_key = key
            (x, y, w, h), rgba = self._patch
            image = QImage(rgba.data, w, h, rgba.strides[0], QImage.Format.Format_RGBA8888).copy()
            sx, sy = self.width()/self.source_frame.shape[1], self.height()/self.source_frame.shape[0]
            painter.drawImage(QRect(round(x*sx), round(y*sy), max(1, round(w*sx)), max(1, round(h*sy))), image)
            return
        if self.settings.subtitle_style_mode == "box":
            painter.fillRect(self.rect(), QColor(0, 0, 0, round(self.settings.subtitle_opacity * 2.55)))
        margin_x = min(10, max(1, self.width() // 20))
        margin_y = min(5, max(1, self.height() // 12))
        area = self.rect().adjusted(margin_x, margin_y, -margin_x, -margin_y)
        family = self.settings.subtitle_font if self.settings.subtitle_font != "自动匹配" else (self.appearance.font_family if self.appearance else "Microsoft YaHei UI")
        key = (self.text, area.width(), area.height(), self.settings.subtitle_font_size, family)
        if key != self._layout_key:
            self._layout = fit_layout(self.text, area.width(), area.height(), self.settings.subtitle_font_size, family)
            self._layout_key = key
        layout, height, _, _ = self._layout
        origin = QPointF(area.left(), area.top() + max(0, (area.height() - height) / 2))
        painter.setClipRect(area)
        painter.setPen(QColor(0, 0, 0, 230))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            layout.draw(painter, origin + QPointF(dx, dy))
        painter.setPen(QColor(*self.appearance.color) if self.appearance else QColor("#ffffff"))
        layout.draw(painter, origin)
