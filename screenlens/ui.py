from __future__ import annotations

import copy
import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSpinBox, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget, QScrollArea,
)

from .config import CONFIG_PATH, OCR_PROFILES, PRESETS, Settings, TARGETS
from .native import exclude_from_capture, monitor_rect
from .pipeline import Pipeline
from .selection import Selector
from .subtitle import SubtitleOverlay
from .translation import Translator


STYLE = """
QWidget { background: #0e1520; color: #e9eff6; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QMainWindow, QDialog { background: #0e1520; }
QLabel { background: transparent; }
QLabel#brand { font-size: 24px; font-weight: 700; }
QLabel#muted { color: #8394aa; font-size: 12px; }
QLabel#eyebrow { color: #6ee6c4; font-size: 11px; font-weight: 600; }
QLabel#status { color: #a8bacf; font-size: 12px; }
QFrame#card { background: #151f2e; border: 1px solid #253348; border-radius: 12px; }
QPushButton { background: #1b293b; border: 1px solid #304158; border-radius: 8px; padding: 9px 13px; }
QPushButton:hover { background: #263b51; border-color: #536d8a; }
QPushButton:pressed { background: #314b63; }
QPushButton:disabled { color: #617187; background: #17212e; }
QPushButton#primary { background: #67e0bd; border-color: #67e0bd; color: #09231e; font-weight: 700; }
QPushButton#primary:hover { background: #93f0d3; }
QPushButton#small { padding: 5px 10px; font-size: 12px; }
QTextEdit { background: transparent; border: none; selection-background-color: #326b66; padding: 0; }
QComboBox, QLineEdit, QSpinBox { background: #172335; border: 1px solid #33445d; border-radius: 6px; padding: 7px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #182638; selection-background-color: #30536b; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; }
QToolTip { background: #26374c; color: white; border: none; padding: 6px; }
QMenu { background: #182638; padding: 5px; }
QMenu::item { padding: 7px 18px; }
QMenu::item:selected { background: #30536b; }
"""


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#67e0bd"))
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    painter.setPen(QPen(QColor("#103d35"), 3.5))
    for x, y, dx, dy in [(16, 16, 10, 10), (48, 16, -10, 10), (16, 48, 10, -10), (48, 48, -10, -10)]:
        painter.drawLine(x, y, x + dx, y)
        painter.drawLine(x, y, x, y + dy)
    painter.setFont(QFont("Segoe UI", 21, QFont.Weight.Bold))
    painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "A")
    painter.end()
    return QIcon(pixmap)


class TestEvents(QObject):
    result = Signal(bool, str)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = copy.copy(settings)
        self.setWindowTitle("视译 · 设置")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        title = QLabel("翻译服务与识别")
        title.setObjectName("brand")
        layout.addWidget(title)
        hint = QLabel("本地识别屏幕，只将识别后的文字发送至你选择的翻译服务。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(12)
        form = QFormLayout()
        form.setSpacing(12)
        self.provider = QComboBox()
        self.provider.addItems(PRESETS)
        self.provider.setCurrentText(settings.provider)
        self.url = QLineEdit(settings.base_url)
        self.url.setPlaceholderText("https://api.deepseek.com")
        self.model = QLineEdit(settings.model)
        self.key = QLineEdit(settings.api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("填写你自己的 API Key，本机服务可留空")
        self.show_key = QCheckBox("显示密钥")
        self.show_key.toggled.connect(lambda checked: self.key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        self.ocr = QComboBox()
        self.ocr.addItems(OCR_PROFILES)
        self.ocr.setCurrentText(settings.ocr_profile)
        self.interval = QSpinBox()
        self.interval.setRange(150, 3000)
        self.interval.setSingleStep(50)
        self.interval.setSuffix(" ms")
        self.interval.setValue(settings.interval_ms)
        self.stable = QSpinBox()
        self.stable.setRange(0, 1000)
        self.stable.setSingleStep(50)
        self.stable.setSuffix(" ms")
        self.stable.setValue(settings.stable_ms)
        self.font = QSpinBox()
        self.font.setRange(12, 32)
        self.font.setSuffix(" px")
        self.font.setValue(settings.font_size)
        form.addRow("翻译服务", self.provider)
        form.addRow("API 基础地址", self.url)
        form.addRow("模型名称", self.model)
        form.addRow("API Key", self.key)
        form.addRow("", self.show_key)
        form.addRow("原文识别语言", self.ocr)
        form.addRow("检测间隔", self.interval)
        form.addRow("字幕变化确认间隔", self.stable)
        form.addRow("译文字号", self.font)
        self.subtitle_cover = QCheckBox("把译文覆盖到原字幕位置")
        self.subtitle_cover.setChecked(settings.subtitle_cover)
        self.subtitle_style = QComboBox()
        for title, key in (("局部文字修复（无底框）", "glyph"), ("透明文字（不擦除原文）", "text"), ("黑底遮罩（兼容）", "box")):
            self.subtitle_style.addItem(title, key)
        self.subtitle_style.setCurrentIndex(max(0, self.subtitle_style.findData(settings.subtitle_style_mode)))
        self.subtitle_family = QComboBox()
        self.subtitle_family.setEditable(True)
        self.subtitle_family.addItems(["自动匹配", "微软雅黑", "宋体", "黑体", "Arial", "Segoe UI", "Yu Gothic"])
        self.subtitle_family.setCurrentText(settings.subtitle_font)
        self.missing_delay = QSpinBox()
        self.missing_delay.setRange(200, 3000)
        self.missing_delay.setSuffix(" ms")
        self.missing_delay.setValue(settings.subtitle_missing_ms)
        self.min_read = QSpinBox()
        self.min_read.setRange(0, 5000)
        self.min_read.setSuffix(" ms")
        self.min_read.setValue(settings.subtitle_min_read_ms)
        self.subtitle_font = QSpinBox()
        self.subtitle_font.setRange(12, 72)
        self.subtitle_font.setSuffix(" px")
        self.subtitle_font.setValue(settings.subtitle_font_size)
        self.subtitle_opacity = QSpinBox()
        self.subtitle_opacity.setRange(0, 100)
        self.subtitle_opacity.setSuffix(" %")
        self.subtitle_opacity.setValue(settings.subtitle_opacity)
        self.subtitle_opacity.setToolTip("100% 黑底完全遮住原字幕；降低后会透出原画面。")
        self.subtitle_x = QSpinBox()
        self.subtitle_y = QSpinBox()
        for control, value in ((self.subtitle_x, settings.subtitle_offset_x), (self.subtitle_y, settings.subtitle_offset_y)):
            control.setRange(-2000, 2000)
            control.setSuffix(" px")
            control.setValue(value)
            control.setToolTip("只移动译文覆盖层，识别区域保持在原字幕位置。")
        form.addRow("原位字幕", self.subtitle_cover)
        form.addRow("字幕显示方式", self.subtitle_style)
        form.addRow("字幕字体", self.subtitle_family)
        form.addRow("字幕最大字号", self.subtitle_font)
        form.addRow("遮罩不透明度", self.subtitle_opacity)
        form.addRow("字幕水平偏移", self.subtitle_x)
        form.addRow("字幕垂直偏移", self.subtitle_y)
        form.addRow("漏识别容忍时间", self.missing_delay)
        form.addRow("译文最短阅读时间", self.min_read)
        self.lock_style = QCheckBox("锁定首句字幕的字号、字体与位置")
        self.lock_style.setChecked(settings.subtitle_lock_style)
        self.complete_only = QCheckBox("整句翻译完成后再更新覆盖字幕")
        self.complete_only.setChecked(settings.subtitle_complete_only)
        self.focus_main = QCheckBox("锁定主字幕，忽略角落文字与其他文字块")
        self.focus_main.setChecked(settings.subtitle_focus_main)
        self.ignore_watermarks = QCheckBox("过滤 Windows 激活水印")
        self.ignore_watermarks.setChecked(settings.subtitle_ignore_watermarks)
        self.ignore_text = QTextEdit()
        self.ignore_text.setAcceptRichText(False)
        self.ignore_text.setPlainText(settings.subtitle_ignore_text)
        self.ignore_text.setPlaceholderText("每行填写一段需要忽略的固定文字，如游戏提示或台标")
        self.ignore_text.setMaximumHeight(80)
        self.ignore_text.setStyleSheet("background: #172335; border: 1px solid #33445d; padding: 6px;")
        form.addRow("稳定显示", self.lock_style)
        form.addRow("整句更新", self.complete_only)
        form.addRow("字幕筛选", self.focus_main)
        form.addRow("水印过滤", self.ignore_watermarks)
        form.addRow("额外忽略文字", self.ignore_text)
        form_holder = QWidget()
        form_holder.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_holder)
        scroll.setMinimumHeight(300)
        scroll.setMaximumHeight(570)
        layout.addWidget(scroll, 1)
        self.resize(620, 850)
        details = QLabel("速度优先：选择非思考模型，检测间隔 150–350 ms。\n中英日模型已内置；额外识别语种首次使用需下载模型。\nAPI Key 使用 Windows 当前账户加密保存；翻译按服务商规则计费。")
        details.setObjectName("muted")
        details.setWordWrap(True)
        layout.addWidget(details)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        layout.addWidget(self.test_label)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self.test_connection)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.test_button)
        row.addStretch()
        row.addWidget(buttons)
        layout.addLayout(row)
        self.provider.currentTextChanged.connect(self.preset_changed)
        self.test_events = TestEvents(self)
        self.test_events.result.connect(self.test_done)
        self.test_cancel = threading.Event()
        self.finished.connect(lambda _: self.test_cancel.set())

    def preset_changed(self, name):
        self.url.setText(PRESETS[name][0])
        self.model.setText(PRESETS[name][1])
        # Do not reuse an existing provider's key with a different provider.
        self.key.clear()

    def collect(self):
        result = copy.copy(self.settings)
        result.provider = self.provider.currentText()
        result.base_url = self.url.text().strip()
        result.model = self.model.text().strip()
        result.api_key = self.key.text().strip()
        result.ocr_profile = self.ocr.currentText()
        result.interval_ms = self.interval.value()
        result.stable_ms = self.stable.value()
        result.font_size = self.font.value()
        result.subtitle_cover = self.subtitle_cover.isChecked()
        result.subtitle_font_size = self.subtitle_font.value()
        result.subtitle_opacity = self.subtitle_opacity.value()
        result.subtitle_offset_x = self.subtitle_x.value()
        result.subtitle_offset_y = self.subtitle_y.value()
        result.subtitle_font = self.subtitle_family.currentText().strip() or "自动匹配"
        result.subtitle_style_mode = self.subtitle_style.currentData()
        result.subtitle_missing_ms = self.missing_delay.value()
        result.subtitle_min_read_ms = self.min_read.value()
        result.subtitle_lock_style = self.lock_style.isChecked()
        result.subtitle_complete_only = self.complete_only.isChecked()
        result.subtitle_focus_main = self.focus_main.isChecked()
        result.subtitle_ignore_watermarks = self.ignore_watermarks.isChecked()
        result.subtitle_ignore_text = self.ignore_text.toPlainText().strip()
        return result

    def accept(self):
        try:
            result = self.collect()
            result.validate(require_key=False)
            result.save()
            self.settings = result
            super().accept()
        except (ValueError, OSError) as exc:
            self.test_label.setText(str(exc))

    def test_connection(self):
        settings = self.collect()
        try:
            settings.validate()
        except ValueError as exc:
            self.test_label.setText(str(exc))
            return
        self.test_button.setEnabled(False)
        self.test_label.setText("正在发送一条简短测试文本…")
        self.test_cancel = threading.Event()
        cancel, events = self.test_cancel, self.test_events

        def run():
            import time
            translator = Translator()
            try:
                start = time.perf_counter()
                result = translator.translate("Hello, world!", settings, cancel, lambda _: None)
                events.result.emit(True, f"连接成功 · {(time.perf_counter() - start):.2f} 秒 · {result.text}")
            except Exception as exc:
                if not cancel.is_set():
                    events.result.emit(False, str(exc))
            finally:
                translator.close()
        threading.Thread(target=run, daemon=True).start()

    def test_done(self, success, text):
        self.test_label.setText(text)
        self.test_label.setStyleSheet("color: #78e7c5;" if success else "color: #ffae9f;")
        self.test_button.setEnabled(True)


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.registered = []
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        for ident, key in [(1, ord("Q")), (2, ord("W")), (3, ord("S")), (4, ord("T"))]:
            if user32.RegisterHotKey(None, ident, 0x4001, key):
                self.registered.append(ident)
        QApplication.instance().installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0312:
            action = {1: self.window.begin_select, 2: self.window.toggle_running, 3: self.window.once, 4: self.window.toggle_subtitle}
            if msg.wParam in action:
                QTimer.singleShot(0, action[msg.wParam])
                return True, 0
        return False, 0

    def close(self):
        for ident in self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, ident)
        QApplication.instance().removeNativeEventFilter(self)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.pipeline = Pipeline(settings)
        self.subtitle = SubtitleOverlay(settings)
        self.subtitle.unavailable.connect(self.subtitle_unavailable)
        self.epoch = self.pipeline.epoch
        self.sequence = 0
        self.region = None
        self.running = False
        self.selecting = False
        self.compact = False
        self.was_running = False
        self.last_ocr_ms = 0.0
        self._translation_pending = False
        self._pending_epoch = -1
        self._source_visible = False
        self._visual_revision = None
        self.setWindowTitle("视译 ScreenLens")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(430, 360)
        self.resize(520, 720)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, settings.always_on_top)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("SCREENLENS  /  LIVE TRANSLATION")
        eyebrow.setObjectName("eyebrow")
        brand = QLabel("视译")
        brand.setObjectName("brand")
        titles.addWidget(eyebrow)
        titles.addWidget(brand)
        header.addLayout(titles)
        header.addStretch()
        self.compact_button = QPushButton("迷你")
        self.compact_button.setObjectName("small")
        self.compact_button.clicked.connect(self.toggle_compact)
        settings_button = QPushButton("设置")
        settings_button.setObjectName("small")
        settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.compact_button)
        header.addWidget(settings_button)
        layout.addLayout(header)
        description = QLabel("原字幕样式匹配 · 同步显示 · 支持 MP4 自动译制")
        description.setObjectName("muted")
        layout.addWidget(description)
        self.description = description
        actions = QHBoxLayout()
        self.select_button = QPushButton("＋  框选屏幕   Alt+Q")
        self.select_button.setObjectName("primary")
        self.select_button.clicked.connect(self.begin_select)
        self.pause_button = QPushButton("开始")
        self.pause_button.clicked.connect(self.toggle_running)
        self.pause_button.setToolTip("开始 / 暂停持续翻译 · Alt+W")
        self.once_button = QPushButton("重译")
        self.once_button.clicked.connect(self.once)
        self.once_button.setToolTip("立即识别一次并翻译 · Alt+S")
        actions.addWidget(self.select_button, 1)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.once_button)
        layout.addLayout(actions)
        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("翻译为"))
        self.target = QComboBox()
        self.target.addItems(TARGETS)
        self.target.setCurrentText(settings.target)
        self.target.currentTextChanged.connect(self.target_changed)
        language_row.addWidget(self.target, 1)
        self.top_check = QCheckBox("置顶")
        self.top_check.setChecked(settings.always_on_top)
        self.top_check.toggled.connect(self.toggle_top)
        language_row.addWidget(self.top_check)
        layout.addLayout(language_row)
        cover_row = QHBoxLayout()
        self.cover_check = QCheckBox("覆盖原字幕")
        self.cover_check.setChecked(settings.subtitle_cover)
        self.cover_check.toggled.connect(self.cover_changed)
        cover_row.addWidget(self.cover_check)
        cover_hint = QLabel("鼠标穿透  ·  Alt+T 显示 / 隐藏")
        cover_hint.setObjectName("muted")
        cover_row.addWidget(cover_hint)
        cover_row.addStretch()
        layout.addLayout(cover_row)
        self.video_button = QPushButton("导入 MP4 · 字幕识别 / 语音识别与译制")
        self.video_button.clicked.connect(self.open_video)
        layout.addWidget(self.video_button)
        self.region_label = QLabel("尚未选择区域  ·  支持任意显示器")
        self.region_label.setObjectName("muted")
        layout.addWidget(self.region_label)
        self.source_card, self.source_text = self.text_card("ORIGINAL  /  原文", "识别出的原文会出现在这里。", False)
        self.source_card.setMaximumHeight(190)
        layout.addWidget(self.source_card, 2)
        translation_card, self.translated_text = self.text_card("TRANSLATION  /  译文", "点击「框选屏幕」开始。\n\n首次使用，请在设置中填写 API Key。", True)
        layout.addWidget(translation_card, 3)
        self.status = QLabel("●  正在准备本地识别引擎…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.metrics = QLabel("本地 OCR  ·  文字变化触发  ·  流式翻译")
        self.metrics.setObjectName("muted")
        layout.addWidget(self.metrics)
        self.selector = Selector(self.selected, self.selection_cancelled)
        events = self.pipeline.events
        events.state.connect(self.on_state)
        events.source.connect(self.on_source)
        events.chunk.connect(self.on_chunk)
        events.done.connect(self.on_done)
        events.error.connect(self.on_error)
        events.frame.connect(self.on_frame)
        self.visual_timer = QTimer(self)
        self.visual_timer.setInterval(65)
        self.visual_timer.timeout.connect(self.refresh_visual)
        self.visual_timer.start()
        self.hotkeys = Hotkeys(self)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("视译 ScreenLens · Alt+Q 框选")
        menu = QMenu()
        for title, fn in [("显示窗口", self.restore), ("框选屏幕  Alt+Q", self.begin_select),
                          ("开始 / 暂停  Alt+W", self.toggle_running),
                          ("显示 / 隐藏原位字幕  Alt+T", self.toggle_subtitle), ("退出", self.close)]:
            action = menu.addAction(title)
            action.triggered.connect(fn)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self.pipeline.start_workers()
        QTimer.singleShot(100, self.after_show)

    def after_show(self):
        exclude_from_capture(int(self.winId()))
        if len(self.hotkeys.registered) != 4:
            self.status.setText("部分全局快捷键被其他程序占用，可使用窗口按钮操作。")

    def text_card(self, title, placeholder, translation):
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 13, 16, 12)
        row = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("eyebrow" if translation else "muted")
        row.addWidget(label)
        row.addStretch()
        copy_button = QPushButton("复制")
        copy_button.setObjectName("small")
        row.addWidget(copy_button)
        box.addLayout(row)
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setAcceptRichText(False)
        edit.setPlaceholderText(placeholder)
        edit.setStyleSheet(f"font-size: {self.settings.font_size if translation else 14}px; color: {'#f0f8ff' if translation else '#b5c5d8'};")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(edit.toPlainText()))
        box.addWidget(edit)
        return card, edit

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def begin_select(self):
        if self.selecting or QApplication.activeModalWidget():
            return
        self.selecting = True
        self.was_running = self.running
        self.subtitle.suspend(True)
        self.epoch = self.pipeline.configure(running=False)
        self.hide()
        QTimer.singleShot(160, self.show_selector)

    def show_selector(self):
        try:
            self.selector.start()
        except Exception as exc:
            self.selector.cleanup()
            self.selection_cancelled()
            self.status.setText(str(exc))

    def selected(self, region):
        self.selecting = False
        self.region = region
        self.subtitle.set_text("")
        self.subtitle.set_region(region)
        self.subtitle.suspend(False)
        self.source_text.clear()
        self.translated_text.clear()
        self.region_label.setText(f"区域  {region.width} × {region.height}  ·  ({region.left}, {region.top})")
        self.move_outside_region()
        self.restore()
        exclude_from_capture(int(self.winId()))
        self.running = True
        self.pause_button.setText("暂停")
        self.status.setText("●  持续监测中…")
        self.epoch = self.pipeline.configure(region=region, running=True)

    def move_outside_region(self):
        for screen in QApplication.screens():
            native = monitor_rect(screen.name(), screen.geometry(), screen.devicePixelRatio())
            if not native:
                continue
            nx, ny, nw, nh = native
            if not QRect(nx, ny, nw, nh).contains(QPoint(self.region.left, self.region.top)):
                continue
            geometry = screen.geometry()
            sx, sy = geometry.width() / nw, geometry.height() / nh
            selected = QRect(round(geometry.x() + (self.region.left - nx) * sx),
                             round(geometry.y() + (self.region.top - ny) * sy),
                             round(self.region.width * sx), round(self.region.height * sy))
            available = screen.availableGeometry().adjusted(12, 12, -12, -12)
            fw, fh = self.frameGeometry().width(), self.frameGeometry().height()
            candidates = [(selected.right() + 16, available.top()),
                          (selected.left() - fw - 16, available.top()),
                          (available.left(), selected.bottom() + 16),
                          (available.left(), selected.top() - fh - 16)]
            for x, y in candidates:
                if available.contains(QRect(x, y, fw, fh)):
                    self.move(x, y)
                    return
            # A different display is preferable when the selected display is full.
            for other in QApplication.screens():
                if other != screen:
                    self.move(other.availableGeometry().topLeft() + QPoint(20, 20))
                    return
            return

    def selection_cancelled(self):
        self.selecting = False
        self.restore()
        self.running = self.was_running
        self.subtitle.suspend(not self.running)
        self.epoch = self.pipeline.configure(running=self.running)
        self.pause_button.setText("暂停" if self.running else "开始")
        self.status.setText("●  持续监测中…" if self.running else "已取消框选")

    def toggle_running(self):
        if self.selecting:
            return
        if not self.region:
            self.begin_select()
            return
        self.running = not self.running
        self.subtitle.suspend(not self.running)
        self.epoch = self.pipeline.configure(running=self.running)
        self.pause_button.setText("暂停" if self.running else "继续")
        self.status.setText("●  持续监测中…" if self.running else "Ⅱ  已暂停 · Alt+W 继续")

    def once(self):
        if self.selecting:
            return
        if not self.region:
            self.begin_select()
            return
        self.epoch = self.pipeline.configure(once=True)
        self.subtitle.suspend(False)
        self.status.setText("正在重新识别…")

    def open_settings(self):
        was_running = self.running
        self.subtitle.suspend(True)
        self.epoch = self.pipeline.configure(running=False)
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.settings
            self.translated_text.setStyleSheet(f"font-size: {self.settings.font_size}px; color: #f0f8ff;")
        self.subtitle.set_text("")
        self.subtitle.apply_settings(self.settings)
        self.cover_check.blockSignals(True)
        self.cover_check.setChecked(self.settings.subtitle_cover)
        self.cover_check.blockSignals(False)
        self.subtitle.suspend(not was_running)
        self.epoch = self.pipeline.configure(settings=self.settings, running=was_running)
        self.status.setText("●  持续监测中…" if was_running else "设置已就绪 · Alt+Q 框选屏幕")

    def open_video(self):
        if self.selecting or QApplication.activeModalWidget():
            return
        from .video_ui import VideoDialog
        was_running = self.running
        self.subtitle.suspend(True)
        self.epoch = self.pipeline.configure(running=False)
        dialog = VideoDialog(self.settings, self)
        dialog.exec()
        self.epoch = self.pipeline.configure(running=was_running)
        self.subtitle.suspend(not was_running)

    def target_changed(self, value):
        self.settings.target = value
        try:
            self.settings.save()
        except OSError:
            self.status.setText("设置保存失败，请检查本地目录权限。")
        self.epoch = self.pipeline.configure(settings=self.settings)
        self.translated_text.clear()
        self.subtitle.set_text("")

    def toggle_subtitle(self):
        self.cover_check.setChecked(not self.cover_check.isChecked())

    def cover_changed(self, value):
        self.settings.subtitle_cover = value
        self.subtitle.apply_settings(self.settings)
        try:
            self.settings.save()
        except OSError:
            self.status.setText("设置保存失败，请检查本地目录权限。")

    def subtitle_unavailable(self, text):
        self.status.setText(text)
        self.status.setStyleSheet("color: #ffae9f;")

    def toggle_top(self, value):
        self.settings.always_on_top = value
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, value)
        self.show()
        exclude_from_capture(int(self.winId()))
        try:
            self.settings.save()
        except OSError:
            pass

    def toggle_compact(self):
        self.compact = not self.compact
        for widget in (self.source_card, self.description, self.region_label):
            widget.setVisible(not self.compact)
        self.compact_button.setText("展开" if self.compact else "迷你")
        self.resize(460, 400) if self.compact else self.resize(520, 720)

    def on_state(self, epoch, text):
        if epoch == self.epoch:
            self.status.setStyleSheet("")
            self.status.setText("●  " + text)

    def on_source(self, epoch, sequence, text, elapsed):
        if epoch != self.epoch:
            return
        self.sequence = sequence
        self.status.setStyleSheet("")
        self.last_ocr_ms = elapsed
        self._source_visible = bool(text)
        self.source_text.setPlainText(text)
        if text:
            self._translation_pending = True
            self._pending_epoch = epoch
            self.translated_text.clear()
            self.subtitle.begin_update()
        else:
            if self._pending_epoch != epoch:
                self._translation_pending = False
            self.subtitle.set_visual(None, self.subtitle.appearance)
            if not self._translation_pending:
                self.subtitle.clear_after_reading()
        self.status.setText("●  正在翻译…" if text else "区域内暂未检测到文字")
        self.metrics.setText(f"OCR {elapsed:.0f} ms  ·  {len(text)} 字符")

    def on_frame(self, epoch, sequence, payload):
        if epoch == self.epoch and sequence == self.sequence:
            revision = (epoch, sequence, payload.get("revision", id(payload["frame"])))
            if revision != self._visual_revision:
                self.subtitle.set_visual(payload["frame"], payload["appearance"], payload.get("mask"))
                self._visual_revision = revision

    def refresh_visual(self):
        visual = self.pipeline.latest_visual()
        if visual is not None:
            self.on_frame(*visual)

    def on_chunk(self, epoch, sequence, text):
        if epoch == self.epoch and sequence == self.sequence:
            scrollbar = self.translated_text.verticalScrollBar()
            pos = scrollbar.value()
            self.translated_text.setPlainText(text)
            if not self.settings.subtitle_complete_only:
                self.subtitle.set_text(text)
            scrollbar.setValue(pos)
            self.status.setText(self.subtitle.failure_reason if self.settings.subtitle_cover and self.subtitle.failure_reason else "●  译文接收中…")

    def on_done(self, epoch, sequence, total, first, cached):
        if epoch == self.epoch and sequence == self.sequence:
            self._translation_pending = False
            self.subtitle.set_text(self.translated_text.toPlainText())
            self.subtitle.mark_complete()
            if not self._source_visible:
                self.subtitle.clear_after_reading()
            self.status.setStyleSheet("")
            self.status.setText("●  持续监测中 · 等待文字变化" if self.running else "✓  翻译完成")
            if self.settings.subtitle_cover and self.subtitle.failure_reason:
                self.subtitle_unavailable(self.subtitle.failure_reason)
            if cached:
                self.metrics.setText(f"OCR {self.last_ocr_ms:.0f} ms  ·  译文来自本次运行缓存")
            else:
                self.metrics.setText(f"OCR {self.last_ocr_ms:.0f} ms  ·  首字 {first / 1000:.2f} s  ·  翻译 {total / 1000:.2f} s")
            if self.subtitle.caption_metrics.get("overflow"):
                self.metrics.setText(self.metrics.text() + "  ·  字幕过长，完整译文见窗口；可扩大框选或调小字号")

    def on_error(self, epoch, sequence, text):
        if epoch == self.epoch and (sequence == 0 or sequence == self.sequence):
            self._translation_pending = False
            self.subtitle.set_text("")
            self.status.setText(text)
            self.status.setStyleSheet("color: #ffae9f;")

    def closeEvent(self, event):
        self.visual_timer.stop()
        self.selector.cleanup()
        self.pipeline.close()
        self.hotkeys.close()
        self.tray.hide()
        self.subtitle.close()
        event.accept()


def run_app():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ScreenLens")
    app.setOrganizationName("ScreenLens")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    window = MainWindow(Settings.load())
    window.show()
    return app.exec()
