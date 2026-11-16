from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget)

from .config import Settings, TARGETS
from .media import preview_frame, probe
from .translation import Cancelled
from .video import VideoOptions, VideoProcessor


class VideoCanvas(QWidget):
    region_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(200)
        self.setMaximumHeight(350)
        self.frame = None
        self.pixmap = None
        self.region = None
        self.origin = None
        self.cursor_point = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_frame(self, frame):
        self.frame = frame
        h, w = frame.shape[:2]
        image = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self.pixmap = QPixmap.fromImage(image)
        self.update()

    def image_rect(self):
        if self.pixmap is None:
            return QRect()
        size = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width()-size.width())//2, (self.height()-size.height())//2, size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#050a12"))
        if self.pixmap is None:
            painter.setPen(QColor("#899caf"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入 MP4 后显示预览，可拖动框选字幕区域")
            return
        rect = self.image_rect()
        painter.drawPixmap(rect, self.pixmap)
        selected = None
        if self.origin is not None and self.cursor_point is not None:
            selected = QRect(self.origin, self.cursor_point).normalized().intersected(rect)
        elif self.region:
            x, y, w, h = self.region
            sx, sy = rect.width()/self.frame.shape[1], rect.height()/self.frame.shape[0]
            selected = QRect(round(rect.x()+x*sx), round(rect.y()+y*sy), round(w*sx), round(h*sy))
        if selected:
            painter.setPen(QPen(QColor("#67e0bd"), 2))
            painter.drawRect(selected)

    def mousePressEvent(self, event):
        if self.frame is not None and self.image_rect().contains(event.position().toPoint()):
            self.origin = self.cursor_point = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.cursor_point = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.origin is None:
            return
        rect = self.image_rect()
        selected = QRect(self.origin, event.position().toPoint()).normalized().intersected(rect)
        self.origin = self.cursor_point = None
        if selected.width() > 5 and selected.height() > 5:
            sx, sy = self.frame.shape[1]/rect.width(), self.frame.shape[0]/rect.height()
            x, y = round((selected.x()-rect.x())*sx), round((selected.y()-rect.y())*sy)
            self.region = (x, y, min(self.frame.shape[1]-x, round(selected.width()*sx)), min(self.frame.shape[0]-y, round(selected.height()*sy)))
            self.region_changed.emit(self.region)
        self.update()


class VideoWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, options, settings, parent=None):
        super().__init__(parent)
        import threading
        self.cancel = threading.Event()
        self.options, self.settings = options, settings

    def run(self):
        try:
            result = VideoProcessor(self.options, self.settings, self.cancel, self.progress.emit).process()
            self.completed.emit(result)
        except Cancelled:
            self.failed.emit("处理已取消。已完成的字幕文件会保留，原视频不受影响。")
        except Exception as exc:
            self.failed.emit(str(exc))


class VideoDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = copy.copy(settings)
        self.info = None
        self.worker = None
        self.pending_close = False
        self.setWindowTitle("视译 · MP4 视频译制")
        self.resize(820, 850)
        layout = QVBoxLayout(self)
        title = QLabel("MP4 自动字幕译制")
        title.setObjectName("brand")
        layout.addWidget(title)
        info = QLabel("内嵌字幕 → 画面字幕 OCR → 无字幕时语音识别。保留原音轨，输出译制 MP4 与可编辑 SRT。")
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setReadOnly(True)
        self.input.setPlaceholderText("选择要翻译的 MP4 文件")
        self.import_button = QPushButton("导入 MP4")
        self.import_button.clicked.connect(self.import_file)
        row.addWidget(self.input, 1)
        row.addWidget(self.import_button)
        layout.addLayout(row)
        self.canvas = VideoCanvas()
        self.canvas.region_changed.connect(lambda r: self.region_hint.setText(f"指定字幕区域：{r} · 仅此区域参与 OCR"))
        layout.addWidget(self.canvas, 1)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(self.seek_preview)
        layout.addWidget(self.seek)
        region_row = QHBoxLayout()
        self.region_hint = QLabel("默认自动扫描画面下方字幕；可拖动预览框选其他位置")
        self.region_hint.setObjectName("muted")
        self.region_hint.setWordWrap(True)
        region_row.addWidget(self.region_hint, 1)
        reset = QPushButton("恢复自动区域")
        reset.clicked.connect(self.reset_region)
        region_row.addWidget(reset)
        layout.addLayout(region_row)
        form = QFormLayout()
        self.mode = QComboBox()
        for title, key in (("自动：字幕优先，无字幕用语音", "auto"), ("只识别画面字幕", "ocr"), ("只用语音识别", "speech"), ("只读取内嵌字幕轨道", "embedded")):
            self.mode.addItem(title, key)
        self.target = QComboBox()
        self.target.addItems(TARGETS)
        self.target.setCurrentText(settings.target)
        self.rate = QComboBox()
        for title, value in (("5 帧/秒（推荐）", 5), ("2 帧/秒（较快）", 2), ("10 帧/秒（短字幕更细）", 10)):
            self.rate.addItem(title, value)
        self.asr = QComboBox()
        for title, key in (("base 多语言（已内置）", "base"), ("small 多语言（更准确，首次下载）", "small")):
            self.asr.addItem(title, key)
        self.language = QComboBox()
        for title, code in (("自动检测", "auto"), ("日语", "ja"), ("英语", "en"), ("中文", "zh"), ("韩语", "ko"), ("法语", "fr"), ("德语", "de"), ("西班牙语", "es")):
            self.language.addItem(title, code)
        self.output = QLineEdit()
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        choose = QPushButton("选择位置")
        choose.clicked.connect(self.choose_output)
        output_row.addWidget(choose)
        form.addRow("识别方式", self.mode)
        form.addRow("翻译为", self.target)
        form.addRow("画面采样", self.rate)
        form.addRow("语音模型", self.asr)
        form.addRow("语音原语言", self.language)
        form.addRow("输出 MP4", output_row)
        layout.addLayout(form)
        note = QLabel("画面硬字幕仅修补文字笔画附近，复杂纹理可能留下修补痕迹；自动字体匹配为近似。\n采样决定时间精度；5 帧/秒约 0.2 秒。无字幕语音识别在本地运行，翻译文本发送到已配置 API。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = QLabel("导入视频后即可开始。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.start_button = QPushButton("开始译制")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("取消处理")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.open_button = QPushButton("打开输出目录")
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output.text()).parent))))
        controls.addWidget(self.start_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        controls.addWidget(self.open_button)
        layout.addLayout(controls)

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 MP4", "", "MP4 视频 (*.mp4);;视频文件 (*.mp4 *.mkv *.mov *.m4v)")
        if not path:
            return
        try:
            self.info = probe(path)
            self.canvas.region = None
            self.canvas.set_frame(preview_frame(self.info, min(3, self.info.duration*.2)))
            self.input.setText(path)
            self.output.setText(str(Path(path).with_name(Path(path).stem+"_translated.mp4")))
            self.status.setText(f"{self.info.width} × {self.info.height} · {self.info.fps:.2f} fps · {self.info.duration:.1f} 秒 · 音轨 {'有' if self.info.audio else '无'}")
        except Exception as exc:
            self.status.setText(str(exc))

    def seek_preview(self):
        if self.info and not (self.worker and self.worker.isRunning()):
            try:
                self.canvas.set_frame(preview_frame(self.info, min(self.info.duration-.05, self.seek.value()/1000*self.info.duration)))
            except Exception as exc:
                self.status.setText(str(exc))

    def reset_region(self):
        self.canvas.region = None
        self.canvas.update()
        self.region_hint.setText("自动扫描画面下方字幕")

    def choose_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择译制视频位置", self.output.text(), "MP4 (*.mp4)")
        if path:
            self.output.setText(path)

    def start(self):
        try:
            self.settings.target = self.target.currentText()
            self.settings.validate()
            if self.info is None or not self.output.text().strip():
                raise ValueError("请导入视频并选择输出文件。")
            options = VideoOptions(self.info.path, self.output.text().strip(), self.mode.currentData(), self.rate.currentData(),
                                   self.canvas.region, self.asr.currentData(), self.language.currentData())
            self.worker = VideoWorker(options, copy.copy(self.settings), self)
            self.worker.progress.connect(self.update_progress)
            self.worker.completed.connect(self.on_completed)
            self.worker.failed.connect(self.failed)
            self.worker.finished.connect(self.finished_work)
            self.start_button.setEnabled(False)
            self.import_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.progress.setValue(0)
            self.worker.start()
        except Exception as exc:
            self.status.setText(str(exc))

    def update_progress(self, value, message):
        self.progress.setValue(value)
        self.status.setText(message)

    def on_completed(self, result):
        self.output.setText(result["output"])
        self.status.setText(f"已完成：{result['cues']} 句字幕\n{result['output']}\n同时保存 SRT、原文字幕和项目时间轴。")

    def failed(self, message):
        self.status.setText(message)

    def finished_work(self):
        self.start_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self.pending_close:
            self.close()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText("正在取消；语音识别会在当前段完成后停止…")

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.pending_close = True
            self.cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.pending_close = True
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)
