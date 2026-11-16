from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from .translation import Cancelled

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def asset_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets"


def media_tool(name):
    bundled = asset_root() / "ffmpeg" / (name + (".exe" if sys.platform == "win32" else ""))
    result = str(bundled) if bundled.exists() else shutil.which(name)
    if not result:
        raise RuntimeError(f"没有找到 {name}，请使用包含视频组件的完整发行包。")
    return result


def run_media(args, cancel=None, timeout=None):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError("视频处理失败：" + stderr.decode("utf-8", errors="replace")[-1500:])
        return stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@dataclass
class MediaInfo:
    path: str
    width: int
    height: int
    duration: float
    fps: float
    audio: bool
    subtitles: list


def probe(path) -> MediaInfo:
    raw = json.loads(run_media([media_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    video = next((s for s in raw["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ValueError("文件没有可解码的视频轨道。")
    width, height = video["width"], video["height"]
    rotation = next((float(s.get("rotation", 0)) for s in video.get("side_data_list", []) if "rotation" in s), float(video.get("tags", {}).get("rotate", 0)))
    if round(rotation) % 180:
        width, height = height, width
    try:
        fps = float(Fraction(video.get("avg_frame_rate", "0/1"))) or float(Fraction(video.get("r_frame_rate", "30/1")))
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    duration = float(raw.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ValueError("无法读取视频时长。")
    return MediaInfo(str(Path(path).resolve()), width, height, duration, min(120, max(1, fps)),
                     any(s["codec_type"] == "audio" for s in raw["streams"]),
                     [s for s in raw["streams"] if s["codec_type"] == "subtitle"])


class FrameReader:
    """FFmpeg normalizes timestamps to a known frame rate for scans and export."""
    def __init__(self, info: MediaInfo, fps: float, cancel: threading.Event):
        self.info, self.fps, self.cancel = info, fps, cancel
        self.error_log = tempfile.TemporaryFile()
        self.process = subprocess.Popen([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", info.path,
            "-an", "-sn", "-vf", f"fps={fps:.8f}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
            stdout=subprocess.PIPE, stderr=self.error_log, creationflags=CREATE_FLAGS)
        self.stop_watch = threading.Event()
        def watch():
            while not self.stop_watch.wait(0.1):
                if self.cancel.is_set() and self.process.poll() is None:
                    self.process.kill()
                    return
        self.watch = threading.Thread(target=watch, daemon=True)
        self.watch.start()

    def __iter__(self):
        size = self.info.width*self.info.height*3
        index = 0
        while True:
            if self.cancel.is_set():
                raise Cancelled()
            data = bytearray()
            while len(data) < size:
                chunk = self.process.stdout.read(size-len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if not data:
                break
            if len(data) != size:
                raise RuntimeError("视频帧数据不完整。")
            yield index/self.fps, np.frombuffer(data, np.uint8).reshape(self.info.height, self.info.width, 3).copy()
            index += 1
        code = self.process.wait()
        if self.cancel.is_set():
            raise Cancelled()
        if code:
            self.error_log.seek(0)
            raise RuntimeError("解码失败："+self.error_log.read().decode("utf-8", errors="replace")[-1000:])

    def close(self):
        self.stop_watch.set()
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.process.stdout.close()
        self.watch.join(timeout=0.5)
        self.error_log.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def preview_frame(info: MediaInfo, seconds=0):
    data = run_media([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-ss", str(seconds), "-i", info.path,
                      "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
    expected = info.width*info.height*3
    if len(data) != expected:
        raise RuntimeError("无法读取视频预览帧。")
    return np.frombuffer(data, np.uint8).reshape(info.height, info.width, 3).copy()
