from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass

import mss
import numpy as np
from PySide6.QtCore import QObject, Signal

from .config import Settings
from .ocr import OcrEngine, image_changed, thumbnail
from .translation import Cancelled, Translator, TranslationError
from .appearance import detect_appearance
from .tracking import SubtitleTracker, same_text


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def as_dict(self):
        return dict(left=self.left, top=self.top, width=self.width, height=self.height)


class Events(QObject):
    state = Signal(int, str)
    source = Signal(int, int, str, float)
    chunk = Signal(int, int, str)
    done = Signal(int, int, float, float, bool)
    error = Signal(int, int, str)
    frame = Signal(int, int, object)


class Pipeline:
    """One OCR worker + one translation worker, with a single latest-job mailbox."""
    def __init__(self, settings: Settings):
        self.events = Events()
        self.condition = threading.Condition()
        self.settings = copy.copy(settings)
        self.region: Region | None = None
        self.running = False
        self.once = False
        self.epoch = 0
        self.sequence = 0
        self.closed = False
        self.pending_job = None
        self.cancel = threading.Event()
        self.threads = [threading.Thread(target=self._capture_loop, daemon=True, name="ocr"),
                        threading.Thread(target=self._translation_loop, daemon=True, name="translation")]

    def start_workers(self):
        for thread in self.threads:
            thread.start()

    def configure(self, settings=None, region=None, running=None, once=False):
        with self.condition:
            self.epoch += 1
            self.cancel.set()
            self.pending_job = None
            if settings is not None:
                self.settings = copy.copy(settings)
            if region is not None:
                self.region = region
            if running is not None:
                self.running = running
            self.once = once
            self.condition.notify_all()
            return self.epoch

    def close(self):
        with self.condition:
            self.closed = True
            self.cancel.set()
            self.condition.notify_all()

    def _valid(self, epoch):
        with self.condition:
            return not self.closed and self.epoch == epoch

    def _capture_loop(self):
        engine, profile = None, None
        previous, processed = None, None
        last_epoch, last_text = -1, None
        pending_since = 0.0
        stable_since = 0.0
        delay = 0.01
        tracker = SubtitleTracker()
        appearance = None
        with mss.MSS() as capture:
            while True:
                with self.condition:
                    if self.closed:
                        return
                    epoch, settings, region = self.epoch, self.settings, self.region
                    active = self.running or self.once
                    if epoch != last_epoch:
                        previous = processed = None
                        last_text = None
                        pending_since = stable_since = 0.0
                        last_epoch = epoch
                        tracker = SubtitleTracker(settings.subtitle_missing_ms)
                        appearance = None
                    if profile == settings.ocr_profile and (not active or region is None):
                        self.condition.wait(timeout=0.5)
                        continue
                if profile != settings.ocr_profile:
                    self.events.state.emit(epoch, "正在加载本地识别模型…")
                    try:
                        new_engine = OcrEngine(settings.ocr_profile)
                        engine, profile = new_engine, settings.ocr_profile
                        self.events.state.emit(epoch, "OCR 已就绪 · 请框选屏幕" if region is None else "识别模型已就绪")
                    except Exception:
                        self.events.error.emit(epoch, 0, "识别模型加载失败。额外语种首次使用需联网下载，请检查网络后重新选择语言。")
                        with self.condition:
                            self.condition.wait(timeout=3.0)
                        continue
                if not active or region is None or not self._valid(epoch):
                    continue
                try:
                    frame = np.asarray(capture.grab(region.as_dict()))[:, :, :3].copy()
                    small = thumbnail(frame)
                    now = time.monotonic()
                    if image_changed(previous, small):
                        stable_since = now
                        if not pending_since:
                            pending_since = now
                    previous = small
                    changed = image_changed(processed, small)
                    stable = (now - stable_since) * 1000 >= settings.stable_ms
                    overdue = pending_since and now - pending_since >= 0.8
                    if (changed or tracker.needs_confirmation) and (stable or overdue):
                        start = time.perf_counter()
                        result = engine.analyze(frame)
                        text = result.text
                        elapsed = (time.perf_counter() - start) * 1000
                        if self._valid(epoch):
                            processed = small
                            pending_since = 0
                            observed = tracker.observe(text, now*1000)
                            if observed is not None and observed != last_text:
                                with self.condition:
                                    if self.epoch != epoch or self.closed:
                                        continue
                                    text = observed
                                    last_text = text
                                    appearance = detect_appearance(frame, result.lines, settings.subtitle_font) if text and result.lines else None
                                    if text:
                                        self.sequence += 1
                                        self.cancel.set()
                                        self.cancel = threading.Event()
                                        self.pending_job = (epoch, self.sequence, text, copy.copy(settings), self.cancel)
                                    sequence = self.sequence
                                    # A subtitle gap may happen before the request completes.
                                    # Let that request finish; a new nonempty subtitle still cancels it.
                                    self.events.source.emit(epoch, sequence, text, elapsed)
                                    self.condition.notify_all()
                            if not self.running:
                                with self.condition:
                                    if self.epoch == epoch:
                                        self.once = False
                    elif not changed:
                        pending_since = 0
                    if self._valid(epoch) and tracker.current:
                        self.events.frame.emit(epoch, self.sequence, {"frame": frame, "appearance": appearance})
                    if tracker.needs_confirmation:
                        pending_since = pending_since or now
                    delay = min(settings.interval_ms, max(40, settings.stable_ms)) / 1000 if pending_since else settings.interval_ms / 1000
                except Exception:
                    if self._valid(epoch):
                        self.events.error.emit(epoch, 0, "屏幕捕获或识别失败，请重新框选可见屏幕区域。")
                    delay = 2.0
                with self.condition:
                    self.condition.wait(timeout=delay)

    def _translation_loop(self):
        translator = Translator()
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending_job is not None)
                    if self.closed:
                        return
                    epoch, sequence, text, settings, cancel = self.pending_job
                    self.pending_job = None
                if cancel.is_set() or not self._valid(epoch):
                    continue
                start = time.perf_counter()
                first_ms = 0.0

                def on_chunk(value):
                    nonlocal first_ms
                    if cancel.is_set() or not self._valid(epoch):
                        raise Cancelled()
                    if first_ms == 0:
                        first_ms = (time.perf_counter() - start) * 1000
                    self.events.chunk.emit(epoch, sequence, value)

                try:
                    result = translator.translate(text, settings, cancel, on_chunk)
                    if self._valid(epoch) and not cancel.is_set():
                        self.events.done.emit(epoch, sequence, (time.perf_counter() - start) * 1000, first_ms, result.cached)
                except Cancelled:
                    pass
                except (TranslationError, ValueError) as exc:
                    if self._valid(epoch) and not cancel.is_set():
                        self.events.error.emit(epoch, sequence, str(exc))
                except Exception:
                    if self._valid(epoch) and not cancel.is_set():
                        self.events.error.emit(epoch, sequence, "翻译失败，请检查 API 配置后按 Alt+S 重试。")
        finally:
            translator.close()
