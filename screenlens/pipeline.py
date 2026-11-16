from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field

import mss
import numpy as np
from PySide6.QtCore import QObject, Signal

from .config import Settings
from .ocr import OcrEngine, image_changed, thumbnail
from .translation import Cancelled, Translator, TranslationError
from .tracking import SubtitleTracker
from .live import CaptionSelector, LiveStyleLock


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int
    dpi_scale: float = field(default=1.0, compare=False)

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
    """Bounded frame mailbox, independently scheduled OCR, and latest-only translation."""
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
        self.latest_frame = None
        self.latest_frame_epoch = -1
        self.frame_number = 0
        self.visual_appearance = None
        self.visual_mask = None
        self.visual_source_visible = False
        self.ignored_lines = 0
        self.threads = [
            threading.Thread(target=self._capture_loop, daemon=True, name="capture"),
            threading.Thread(target=self._ocr_loop, daemon=True, name="ocr"),
            threading.Thread(target=self._translation_loop, daemon=True, name="translation"),
        ]

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
            self.latest_frame = None
            self.latest_frame_epoch = -1
            self.visual_appearance = None
            self.visual_mask = None
            self.visual_source_visible = False
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

    def latest_visual(self):
        """The UI polls one slot instead of accumulating frame signals."""
        with self.condition:
            if self.closed or self.latest_frame_epoch != self.epoch or not self.visual_source_visible or self.visual_appearance is None:
                return None
            return self.epoch, self.sequence, {
                "frame": self.latest_frame, "appearance": self.visual_appearance,
                "mask": self.visual_mask, "revision": self.frame_number, "ignored": self.ignored_lines,
            }

    def _capture_loop(self):
        capture = None
        failures = 0
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or (self.region is not None and (self.running or self.once)))
                    if self.closed:
                        return
                    epoch, region = self.epoch, self.region
                start = time.monotonic()
                try:
                    if capture is None:
                        capture = mss.MSS()
                    frame = np.asarray(capture.grab(region.as_dict()))[:, :, :3].copy()
                    failures = 0
                    with self.condition:
                        if self.closed or self.epoch != epoch:
                            continue
                        self.latest_frame = frame
                        self.latest_frame_epoch = epoch
                        self.frame_number += 1
                        self.condition.notify_all()
                except Exception:
                    failures += 1
                    if failures == 3 and self._valid(epoch):
                        self.events.error.emit(epoch, 0, "屏幕捕获连续失败，请重新框选可见屏幕区域。")
                with self.condition:
                    self.condition.wait(timeout=max(.01, .065-(time.monotonic()-start)) if failures == 0 else .35)
        finally:
            if capture is not None:
                capture.close()

    def _ocr_loop(self):
        engine, profile = None, None
        last_epoch = -1
        processed = None
        last_text = None
        last_ocr_at = last_analyzed_at = 0.0
        failures = 0
        tracker = None
        selector = None
        style_lock = None
        while True:
            with self.condition:
                if self.closed:
                    return
                epoch, settings, region = self.epoch, self.settings, self.region
                if epoch != last_epoch:
                    last_epoch = epoch
                    processed = None
                    last_text = None
                    last_ocr_at = last_analyzed_at = 0.0
                    tracker = SubtitleTracker(settings.subtitle_missing_ms)
                    selector = CaptionSelector(settings)
                    style_lock = LiveStyleLock()
                active = region is not None and (self.running or self.once)
            if profile != settings.ocr_profile:
                self.events.state.emit(epoch, "正在加载本地识别模型…")
                try:
                    engine = OcrEngine(settings.ocr_profile)
                    profile = settings.ocr_profile
                    self.events.state.emit(epoch, "OCR 已就绪 · 请框选屏幕" if region is None else "字幕识别已就绪")
                except Exception:
                    self.events.error.emit(epoch, 0, "识别模型加载失败，额外语种首次使用需要联网下载。")
                    with self.condition:
                        self.condition.wait(timeout=3)
                    continue
            with self.condition:
                if not active or self.latest_frame_epoch != epoch or self.latest_frame is None:
                    self.condition.wait(timeout=.15)
                    continue
                frame = self.latest_frame
            if not self._valid(epoch):
                continue
            now = time.monotonic()
            delay = min(settings.interval_ms, max(80, settings.stable_ms))/1000 if tracker.needs_confirmation else settings.interval_ms/1000
            if now-last_ocr_at < delay:
                with self.condition:
                    self.condition.wait(timeout=min(.1, delay-(now-last_ocr_at)))
                continue
            last_ocr_at = now
            try:
                small = thumbnail(frame)
                if not tracker.needs_confirmation and not image_changed(processed, small) and now-last_analyzed_at < 1:
                    continue
                start = time.perf_counter()
                result = engine.analyze(frame)
                last_analyzed_at = time.monotonic()
                selected = selector.select(result.lines, frame.shape)
                text = "\n".join(line.text for line in selected)
                elapsed = (time.perf_counter()-start)*1000
                observed = tracker.observe(text, now*1000)
                failures = 0
                if not self._valid(epoch):
                    continue
                processed = small
                if observed is not None and observed != last_text:
                    appearance = mask = None
                    if observed and selected:
                        appearance, mask = style_lock.apply(frame, selected, settings, region.dpi_scale)
                        selector.commit(selected)
                    with self.condition:
                        if self.closed or self.epoch != epoch:
                            continue
                        last_text = observed
                        self.visual_source_visible = bool(observed)
                        self.visual_appearance, self.visual_mask = appearance, mask
                        self.ignored_lines = selector.ignored
                        if observed:
                            self.sequence += 1
                            self.cancel.set()
                            self.cancel = threading.Event()
                            self.pending_job = (epoch, self.sequence, observed, copy.copy(settings), self.cancel)
                        sequence = self.sequence
                        self.events.source.emit(epoch, sequence, observed, elapsed)
                        self.condition.notify_all()
                    visual = self.latest_visual()
                    if visual is not None:
                        self.events.frame.emit(*visual)
                with self.condition:
                    if not self.running and self.epoch == epoch:
                        self.once = False
            except Exception:
                failures += 1
                if failures == 3 and self._valid(epoch):
                    self.events.error.emit(epoch, 0, "字幕识别连续失败，请重新框选清晰的字幕区域。")

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
                        first_ms = (time.perf_counter()-start)*1000
                    self.events.chunk.emit(epoch, sequence, value)

                try:
                    result = translator.translate(text, settings, cancel, on_chunk)
                    if self._valid(epoch) and not cancel.is_set():
                        self.events.done.emit(epoch, sequence, (time.perf_counter()-start)*1000, first_ms, result.cached)
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
