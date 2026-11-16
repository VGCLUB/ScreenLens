from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .native import monitor_rect
from .pipeline import Region


def to_native_region(rect: QRect, logical_size, native_bounds) -> Region:
    left, top, width, height = native_bounds
    sx, sy = width / logical_size.width(), height / logical_size.height()
    x1, y1 = round(rect.left() * sx), round(rect.top() * sy)
    x2, y2 = round((rect.left() + rect.width()) * sx), round((rect.top() + rect.height()) * sy)
    return Region(left + x1, top + y1, x2 - x1, y2 - y1)


class SelectionOverlay(QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, screen):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.screen = screen
        self.background = screen.grabWindow(0)
        self.bounds = monitor_rect(screen.name(), screen.geometry(), screen.devicePixelRatio())
        if self.bounds is None:
            raise RuntimeError("无法定位显示器，请检查显示设置。")
        self.setGeometry(screen.geometry())
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.origin: QPoint | None = None
        self.current = QPoint()
        self.setWindowTitle("ScreenLens 框选区域")

    def selection(self):
        return QRect(self.origin, self.current).normalized().intersected(self.rect()) if self.origin is not None else QRect()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.background)
        painter.fillRect(self.rect(), QColor(5, 12, 22, 145))
        rect = self.selection()
        if not rect.isEmpty():
            painter.save()
            painter.setClipRect(rect)
            painter.drawPixmap(self.rect(), self.background)
            painter.restore()
            painter.setPen(QPen(QColor("#55e0be"), 2))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            region = to_native_region(rect, self.size(), self.bounds)
            label_rect = QRect(rect.left(), max(8, rect.top() - 36), 220, 28)
            painter.fillRect(label_rect, QColor("#132b30"))
            painter.setFont(QFont("Microsoft YaHei UI", 10))
            painter.setPen(QColor("#bff9eb"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, f"{region.width} × {region.height} px")
        hint = QRect((self.width() - 520) // 2, 40, 520, 48)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#101d2b"))
        painter.drawRoundedRect(hint, 12, 12)
        painter.setFont(QFont("Microsoft YaHei UI", 11))
        painter.setPen(QColor("#edf6fc"))
        painter.drawText(hint, Qt.AlignmentFlag.AlignCenter, "拖动框选要翻译的位置  ·  松开确认  ·  Esc / 右键取消")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.current = self.origin
            self.update()

    def mouseMoveEvent(self, event):
        self.current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.origin is None:
            return
        self.current = event.position().toPoint()
        rect = self.selection()
        if rect.width() >= 12 and rect.height() >= 12:
            self.selected.emit(to_native_region(rect, self.size(), self.bounds))
        else:
            self.origin = None
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()


class Selector:
    def __init__(self, on_selected, on_cancelled):
        self.overlays = []
        self.on_selected = on_selected
        self.on_cancelled = on_cancelled

    def start(self):
        # Capture each display before showing any overlay.
        self.overlays = [SelectionOverlay(screen) for screen in QApplication.screens()]
        for overlay in self.overlays:
            overlay.selected.connect(self.finish)
            overlay.cancelled.connect(self.cancel)
        for overlay in self.overlays:
            overlay.show()
        screen = QApplication.screenAt(QCursor.pos())
        for overlay in self.overlays:
            if overlay.screen == screen:
                overlay.activateWindow()
                overlay.setFocus()

    def cleanup(self):
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()

    def finish(self, region):
        self.cleanup()
        self.on_selected(region)

    def cancel(self):
        self.cleanup()
        self.on_cancelled()
