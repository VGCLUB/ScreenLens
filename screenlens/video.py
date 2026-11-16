from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import tempfile
import threading
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .appearance import Appearance, apply_replacement, detect_appearance, resolve_font
from .config import APP_DIR, Settings
from .media import CREATE_FLAGS, FrameReader, asset_root, media_tool, probe, run_media
from .ocr import OcrEngine, TextLine
from .tracking import same_text
from .translation import Cancelled, Translator


@dataclass
class Cue:
    start: float
    end: float
    source: str
    translated: str = ""
    appearance: Appearance | None = None
    origin: str = "ocr"


@dataclass
class VideoOptions:
    input_path: str
    output_path: str
    mode: str = "auto"  # auto / ocr / speech / embedded
    sample_fps: float = 5.0
    region: tuple[int, int, int, int] | None = None
    asr_model: str = "base"
    source_language: str = "auto"
    crf: int = 18


def srt_time(seconds):
    value = max(0, round(seconds*1000))
    return f"{value//3600000:02d}:{value//60000%60:02d}:{value//1000%60:02d},{value%1000:03d}"


def write_srt(cues, path, translated=True):
    blocks = [f"{index}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n{cue.translated if translated else cue.source}\n"
              for index, cue in enumerate(cues, 1)]
    Path(path).write_text("\n".join(blocks), encoding="utf-8-sig")


def parse_srt(text):
    cues = []
    pattern = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)
    for match in pattern.finditer(text.replace("\r", "")):
        numbers = [int(v) for v in match.groups()[:8]]
        start = numbers[0]*3600+numbers[1]*60+numbers[2]+numbers[3]/1000
        end = numbers[4]*3600+numbers[5]*60+numbers[6]+numbers[7]/1000
        content = re.sub(r"<[^>]+>|\{[^}]*\}", "", match.group(9)).strip()
        if content and end > start:
            cues.append(Cue(start, end, content, origin="embedded"))
    return cues


def choose_output(path, input_path):
    path = Path(path).with_suffix(".mp4")
    if path.resolve() == Path(input_path).resolve():
        raise ValueError("输出文件不能是原视频，请选择新的文件名。")
    root, suffix = path.stem, path.suffix
    index = 1
    while any(candidate.exists() for candidate in (path, path.with_suffix(".srt"), path.with_suffix(".original.srt"), path.with_suffix(".project.json"))):
        path = path.with_name(f"{root}_{index:03d}{suffix}")
        index += 1
    return path


def default_appearance(info, settings):
    width = round(info.width*.88)
    height = max(32, round(info.height*.12))
    return Appearance([(round((info.width-width)/2), round(info.height*.82), width, height)],
                      font_path=resolve_font(settings.subtitle_font), font_px=max(20, round(info.height*.045)),
                      stroke=max(1, round(info.height*.002)))


def embedded_appearance(ass_text, info, settings):
    """Reuse exported subtitle style metadata when the source track exposes it."""
    res_y = re.search(r"PlayResY:\s*(\d+)", ass_text)
    scale = info.height / int(res_y.group(1)) if res_y else 1
    fields = None
    for line in ass_text.splitlines():
        if line.startswith("Format:") and "Fontname" in line:
            fields = [value.strip() for value in line[7:].split(",")]
        elif line.startswith("Style:") and fields:
            data = dict(zip(fields, [value.strip() for value in line[6:].split(",")]))
            try:
                family = data["Fontname"]
                color_value = int(data.get("PrimaryColour", "&HFFFFFF").replace("&H", "").replace("&", ""), 16)
                color = (color_value&255, color_value>>8&255, color_value>>16&255)
                size = max(8, round(float(data["Fontsize"])*scale))
                margin = round(float(data.get("MarginV", "10"))*scale)
                height = min(round(info.height*.2), max(size+10, round(size*1.5)))
                return Appearance([(round(info.width*.05), max(0, info.height-margin-height), round(info.width*.9), height)],
                                  color=color, font_path=resolve_font(family if settings.subtitle_font == "自动匹配" else settings.subtitle_font),
                                  font_family=family, font_px=size, stroke=max(1, round(float(data.get("Outline", "1"))*scale)))
            except (ValueError, KeyError):
                break
    return default_appearance(info, settings)


class VideoProcessor:
    def __init__(self, options: VideoOptions, settings: Settings, cancel=None, progress=None, translator_factory=None):
        self.options, self.settings = options, copy.copy(settings)
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda value, message: None)
        self.translator_factory = translator_factory or Translator

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def embedded_cues(self, info, workspace):
        for index, stream in enumerate(info.subtitles):
            self.check()
            if stream.get("codec_name") not in ("mov_text", "subrip", "ass", "ssa", "webvtt", "text"):
                continue
            path = workspace / f"embedded-{index}.srt"
            try:
                run_media([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", info.path,
                           "-map", f"0:s:{index}", str(path)], self.cancel)
                cues = parse_srt(path.read_text(encoding="utf-8-sig"))
                if cues:
                    ass = workspace/f"embedded-{index}.ass"
                    try:
                        run_media([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", info.path,
                                   "-map", f"0:s:{index}", str(ass)], self.cancel)
                        style = embedded_appearance(ass.read_text(encoding="utf-8-sig"), info, self.settings)
                    except (RuntimeError, OSError):
                        style = default_appearance(info,self.settings)
                    for cue in cues:
                        cue.appearance = style
                    return cues
            except RuntimeError:
                continue
        return []

    def ocr_cues(self, info):
        engine = OcrEngine(self.settings.ocr_profile)
        region = self.options.region or (round(info.width*.05), round(info.height*.56), round(info.width*.9), round(info.height*.41))
        rx, ry, rw, rh = region
        step = 1/self.options.sample_fps
        cues = []
        current = None
        misses = 0
        with FrameReader(info, self.options.sample_fps, self.cancel) as reader:
            for timestamp, frame in reader:
                self.check()
                crop = frame[ry:ry+rh, rx:rx+rw]
                result = engine.analyze(crop)
                lines = [line for line in result.lines if len(line.text.strip()) >= 2 and line.score >= .55]
                if self.options.region is None:
                    lines = [line for line in lines if .12*info.width < rx+line.box[0]+line.box[2]/2 < .88*info.width]
                    if lines:
                        # Prefer the lowest group, avoiding titles above the actual subtitles.
                        lowest = max(line.box[1]+line.box[3] for line in lines)
                        lines = [line for line in lines if lowest-(line.box[1]+line.box[3]) < max(55, info.height*.10)]
                text = "\n".join(line.text for line in lines)
                if text and current and same_text(current.source, text, .88):
                    current.end = min(info.duration, timestamp+step*.5)
                    misses = 0
                elif text:
                    full_lines = [TextLine(line.text, (rx+line.box[0], ry+line.box[1], line.box[2], line.box[3]), line.score) for line in lines]
                    appearance = detect_appearance(frame, full_lines, self.settings.subtitle_font)
                    current = Cue(max(0, timestamp-step*.5), min(info.duration, timestamp+step*.5), text, appearance=appearance)
                    if cues and current.start < cues[-1].end:
                        boundary = (current.start+cues[-1].end)/2
                        cues[-1].end = current.start = boundary
                    cues.append(current)
                    misses = 0
                else:
                    misses += 1
                    if misses >= 2:
                        current = None
                self.progress(round(5+45*timestamp/info.duration), f"识别画面字幕 {timestamp:.1f} / {info.duration:.1f} 秒 · {len(cues)} 句")
        return [cue for cue in cues if cue.end-cue.start >= min(.25, step*.9)]

    def speech_cues(self, info, workspace):
        if not info.audio:
            raise ValueError("没有识别到字幕，视频也没有音轨，无法进行语音识别。")
        self.progress(8, "提取音轨，准备本地语音识别…")
        wav = workspace / "speech.wav"
        run_media([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", info.path,
                   "-vn", "-sn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], self.cancel)
        from faster_whisper import WhisperModel
        bundled = asset_root() / "asr-base"
        model = str(bundled) if self.options.asr_model == "base" and (bundled/"model.bin").is_file() else self.options.asr_model
        self.progress(12, "本地语音识别中；其他模型首次使用需下载，取消会在当前识别段完成后生效…")
        engine = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=min(6, os.cpu_count() or 2),
                              download_root=str(APP_DIR / "speech-models"))
        self.check()
        with wave.open(str(wav), "rb") as audio:
            waveform = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32)/32768.0
        segments, language = engine.transcribe(waveform, language=None if self.options.source_language == "auto" else self.options.source_language,
                                             beam_size=3, vad_filter=True, condition_on_previous_text=False)
        cues = []
        for segment in segments:
            self.check()
            text = segment.text.strip()
            if text and segment.end > segment.start:
                cues.append(Cue(float(segment.start), min(info.duration, float(segment.end)), text,
                                appearance=default_appearance(info, self.settings), origin="speech"))
            self.progress(round(12+38*min(segment.end, info.duration)/info.duration), f"语音识别 · {language.language} · {segment.end:.1f}/{info.duration:.1f} 秒")
        return cues

    def translate_cues(self, cues):
        translator = self.translator_factory()
        try:
            unique = list(dict.fromkeys(cue.source for cue in cues))
            translations = {}
            for start in range(0, len(unique), 12):
                self.check()
                batch = unique[start:start+12]
                values = translator.translate_batch(batch, self.settings, self.cancel)
                translations.update(zip(batch, values))
                self.progress(round(50+20*min(start+12, len(unique))/len(unique)), f"翻译字幕 {min(start+12,len(unique))}/{len(unique)} 句")
            for cue in cues:
                cue.translated = translations[cue.source]
        finally:
            translator.close()

    def export(self, info, cues, output, workspace):
        partial = workspace / "encoded.mp4"
        stderr = tempfile.TemporaryFile()
        encoder = subprocess.Popen([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{info.width}x{info.height}", "-r", f"{info.fps:.8f}", "-i", "pipe:0",
            "-i", info.path, "-map", "0:v:0", "-map", "1:a?", "-sn", "-c:v", "libx264", "-preset", "fast", "-crf", str(self.options.crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(partial)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr, creationflags=CREATE_FLAGS)
        position = 0
        frame_count = 0
        try:
            with FrameReader(info, info.fps, self.cancel) as reader:
                for timestamp, frame in reader:
                    self.check()
                    while position < len(cues) and cues[position].end <= timestamp:
                        position += 1
                    if position < len(cues) and cues[position].start <= timestamp < cues[position].end:
                        cue = cues[position]
                        appearance = cue.appearance or default_appearance(info, self.settings)
                        frame = apply_replacement(frame, appearance, cue.translated, self.settings.subtitle_font, erase=cue.origin == "ocr")
                    encoder.stdin.write(frame.tobytes())
                    frame_count += 1
                    if frame_count % max(1, round(info.fps/2)) == 0:
                        self.progress(round(70+29*timestamp/info.duration), f"输出译制视频 {timestamp:.1f}/{info.duration:.1f} 秒")
            encoder.stdin.close()
            while encoder.poll() is None:
                self.check()
                self.cancel.wait(.1)
            if encoder.returncode:
                stderr.seek(0)
                raise RuntimeError("编码失败："+stderr.read().decode("utf-8", errors="replace")[-1200:])
            output.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            with partial.open("rb") as source, output.open("xb") as destination:
                shutil.copyfileobj(source, destination)
        except BrokenPipeError as exc:
            stderr.seek(0)
            raise RuntimeError("视频编码中断："+stderr.read().decode("utf-8", errors="replace")[-1200:]) from exc
        finally:
            if encoder.poll() is None:
                encoder.kill()
            encoder.wait()
            stderr.close()

    def process(self):
        self.settings.validate()
        self.check()
        info = probe(self.options.input_path)
        output = choose_output(self.options.output_path, info.path)
        output.parent.mkdir(parents=True, exist_ok=True)
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.progress(1, "检查视频轨道…")
        with tempfile.TemporaryDirectory(prefix="screenlens-video-", dir=str(APP_DIR)) as temp:
            workspace = Path(temp)
            cues = []
            if self.options.mode in ("auto", "embedded"):
                cues = self.embedded_cues(info, workspace)
            if not cues and self.options.mode in ("auto", "ocr"):
                self.progress(5, "扫描视频画面字幕…")
                cues = self.ocr_cues(info)
            if not cues and self.options.mode in ("auto", "speech"):
                self.progress(5, "没有检测到画面字幕，切换为语音识别…")
                cues = self.speech_cues(info, workspace)
            if not cues:
                raise ValueError("未识别到可翻译的字幕或语音。可指定字幕区域、语言，或选择语音识别模式重试。")
            self.translate_cues(cues)
            self.check()
            write_srt(cues, output.with_suffix(".original.srt"), translated=False)
            write_srt(cues, output.with_suffix(".srt"))
            payload = {"input": info.path, "output": str(output), "mode": self.options.mode,
                       "sample_fps": self.options.sample_fps, "target": self.settings.target,
                       "cues": [{**{k:v for k,v in asdict(cue).items() if k != "appearance"},
                                 "appearance": cue.appearance.to_dict() if cue.appearance else None} for cue in cues]}
            output.with_suffix(".project.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.export(info, cues, output, workspace)
        self.progress(100, "译制完成")
        return {"output": str(output), "subtitle": str(output.with_suffix(".srt")), "cues": len(cues),
                "source": cues[0].origin, "duration": info.duration}
