"""Estimate subtitle style, repair only glyph pixels, and render replacement text."""
from __future__ import annotations

import functools
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .ocr import TextLine


FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
FONT_CANDIDATES = ("msyh.ttc", "msyhbd.ttc", "simsun.ttc", "simhei.ttf", "arial.ttf",
                   "arialbd.ttf", "ariali.ttf", "segoeui.ttf", "YuGothM.ttc", "msgothic.ttc", "times.ttf")


@functools.lru_cache(maxsize=64)
def resolve_font(family: str = "自动匹配") -> str:
    if family and Path(family).is_file():
        return family
    normalized = family.lower().replace(" ", "")
    aliases = {"微软雅黑": "msyh.ttc", "宋体": "simsun.ttc", "黑体": "simhei.ttf"}
    if family in aliases:
        return str(FONT_DIR / aliases[family])
    if family != "自动匹配":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, index)
                    if normalized in name.lower().replace(" ", ""):
                        path = Path(value)
                        path = path if path.is_absolute() else FONT_DIR / path
                        if path.is_file():
                            return str(path)
        except OSError:
            pass
    return str(FONT_DIR / "msyh.ttc")


@dataclass
class Appearance:
    boxes: list[tuple[int, int, int, int]]
    color: tuple[int, int, int] = (255, 255, 255)  # RGB
    outline: tuple[int, int, int] = (0, 0, 0)
    font_path: str = ""
    font_family: str = "Microsoft YaHei"
    font_px: int = 32
    stroke: int = 2

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        result = cls(**value)
        if not Path(result.font_path).is_file():
            result.font_path = resolve_font(result.font_family)
        return result


def clip_box(box, shape, margin=0):
    x, y, w, h = box
    x1, y1 = max(0, x-margin), max(0, y-margin)
    x2, y2 = min(shape[1], x+w+margin), min(shape[0], y+h+margin)
    return int(x1), int(y1), max(1, int(x2-x1)), max(1, int(y2-y1))


def union_box(boxes):
    x = min(b[0] for b in boxes)
    y = min(b[1] for b in boxes)
    right = max(b[0]+b[2] for b in boxes)
    bottom = max(b[1]+b[3] for b in boxes)
    return x, y, right-x, bottom-y


def _foreground_color(frame, boxes):
    values = []
    for box in boxes:
        x, y, w, h = clip_box(box, frame.shape)
        crop = cv2.cvtColor(frame[y:y+h, x:x+w], cv2.COLOR_BGR2RGB)
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        eligible = (hsv[:, :, 2] > 175) | ((hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 125))
        # Bright backgrounds can have dark subtitle letters.
        if float(np.mean(hsv[:, :, 2] > 220)) > 0.65:
            eligible = hsv[:, :, 2] < 95
        if eligible.any():
            values.append(crop[eligible])
    if not values:
        return (255, 255, 255)
    pixels = np.concatenate(values)
    quantized = (pixels // 24).astype(np.int32)
    keys = quantized[:, 0]*121 + quantized[:, 1]*11 + quantized[:, 2]
    most = int(np.bincount(keys).argmax())
    return tuple(int(v) for v in np.median(pixels[keys == most], axis=0))


def glyph_mask(frame: np.ndarray, appearance: Appearance) -> np.ndarray:
    """Remove glyph fill + thin outline, never an entire subtitle rectangle."""
    mask = np.zeros(frame.shape[:2], np.uint8)
    color = np.array(appearance.color[::-1], dtype=np.float32)
    for box in appearance.boxes:
        x, y, w, h = clip_box(box, frame.shape, 1)
        crop = frame[y:y+h, x:x+w]
        distance = np.linalg.norm(crop.astype(np.float32)-color, axis=2)
        ink = np.uint8(distance < 72)*255
        count, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
        selected = np.zeros_like(ink)
        for label in range(1, count):
            sx, sy, sw, sh, area = stats[label]
            if 2 <= area < w*h*0.55 and sh <= h and sw < w*0.9:
                selected[labels == label] = 255
        mask[y:y+h, x:x+w] |= selected
    radius = max(2, min(8, appearance.stroke+3))
    return cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1)))


def _font_match(frame, line: TextLine, color):
    x, y, w, h = clip_box(line.box, frame.shape)
    crop = frame[y:y+h, x:x+w]
    target = (np.linalg.norm(crop.astype(float)-np.array(color[::-1]), axis=2) < 65).astype(np.uint8)*255
    points = cv2.findNonZero(target)
    if points is not None:
        tx, ty, tw, th = cv2.boundingRect(points)
        target = target[ty:ty+th, tx:tx+tw]
    target = cv2.resize(target, (240, 40), interpolation=cv2.INTER_AREA) / 255.0
    best = (float("inf"), resolve_font(), "Microsoft YaHei", max(10, round(h*1.1)))
    for name in FONT_CANDIDATES:
        path = FONT_DIR / name
        if not path.is_file():
            continue
        try:
            font = ImageFont.truetype(str(path), max(10, round(h*1.1)))
            bounds = font.getbbox(line.text)
            image = Image.new("L", (max(1, bounds[2]-bounds[0]), max(1, bounds[3]-bounds[1])))
            ImageDraw.Draw(image).text((-bounds[0], -bounds[1]), line.text, font=font, fill=255)
            rendered = cv2.resize(np.asarray(image), (240, 40), interpolation=cv2.INTER_AREA) / 255.0
            score = float(np.mean(np.abs(rendered-target)))
            if score < best[0]:
                best = (score, str(path), font.getname()[0], max(10, round(h*1.1)))
        except (OSError, ValueError):
            continue
    return best[1:]


def detect_appearance(frame: np.ndarray, lines: list[TextLine], font_override="自动匹配") -> Appearance:
    if not lines:
        raise ValueError("No subtitle text boxes")
    boxes = [clip_box(line.box, frame.shape) for line in lines]
    color = _foreground_color(frame, boxes)
    representative = max(lines, key=lambda line: len(line.text))
    path, family, size = _font_match(frame, representative, color)
    if font_override != "自动匹配":
        path = resolve_font(font_override)
        family = font_override
    return Appearance(boxes, color, (0, 0, 0), path, family, size, max(1, min(5, round(size*.055))))


def prepare_repair(frame, appearance):
    box = clip_box(union_box(appearance.boxes), frame.shape, max(8, appearance.stroke+6))
    x, y, w, h = box
    crop = frame[y:y+h, x:x+w].copy()
    local = Appearance([(a-x, b-y, c, d) for a, b, c, d in appearance.boxes],
                       appearance.color, appearance.outline, appearance.font_path,
                       appearance.font_family, appearance.font_px, appearance.stroke)
    mask = glyph_mask(crop, local)
    repaired = cv2.inpaint(crop, mask, 3, cv2.INPAINT_TELEA) if mask.any() else crop
    return box, repaired, mask, local


@functools.lru_cache(maxsize=128)
def _font(path, size):
    return ImageFont.truetype(path if Path(path).is_file() else resolve_font(), max(8, size))


def draw_translation(image: Image.Image, text: str, appearance: Appearance, box=None, font_override="自动匹配"):
    draw = ImageDraw.Draw(image)
    x, y, width, height = box or union_box(appearance.boxes)
    path = resolve_font(font_override) if font_override != "自动匹配" else appearance.font_path
    if any("\u3000" <= char <= "\u9fff" for char in text) and Path(path).name.lower() in ("arial.ttf", "arialbd.ttf", "ariali.ttf", "segoeui.ttf", "times.ttf"):
        path = str(FONT_DIR / ("simsun.ttc" if "times" in Path(path).name.lower() else "msyh.ttc"))
    if any("\uac00" <= char <= "\ud7af" for char in text) and (FONT_DIR / "malgun.ttf").exists():
        path = str(FONT_DIR / "malgun.ttf")
    lines = text.strip().splitlines() or [text]
    preferred = max(8, appearance.font_px)
    for size in range(preferred, 7, -1):
        font = _font(path, size)
        spacing = max(1, round(size*.1))
        value = "\n".join(lines)
        bounds = draw.multiline_textbbox((0, 0), value, font=font, spacing=spacing, stroke_width=appearance.stroke)
        if (bounds[2]-bounds[0] <= width and bounds[3]-bounds[1] <= height+3) or size == 8:
            break
    tx = x + (width-(bounds[2]-bounds[0]))/2 - bounds[0]
    ty = y + (height-(bounds[3]-bounds[1]))/2 - bounds[1]
    draw.multiline_text((round(tx), round(ty)), value, font=font, fill=appearance.color+(255,),
                        stroke_width=appearance.stroke, stroke_fill=appearance.outline+(255,), spacing=spacing, align="center")


def replacement_patch(frame, appearance, text, font_override="自动匹配"):
    box, repaired, mask, local = prepare_repair(frame, appearance)
    rgba = np.dstack((cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB), mask))
    image = Image.fromarray(rgba, "RGBA")
    draw_translation(image, text, local, font_override=font_override)
    return box, np.asarray(image).copy()


def apply_replacement(frame, appearance, text, font_override="自动匹配", erase=True):
    output = frame.copy()
    if erase:
        box, rgba = replacement_patch(frame, appearance, text, font_override)
    else:
        x, y, w, h = clip_box(union_box(appearance.boxes), frame.shape, 5)
        box = (x, y, w, h)
        image = Image.new("RGBA", (w, h))
        local = Appearance([(a-x, b-y, c, d) for a,b,c,d in appearance.boxes], appearance.color, appearance.outline,
                           appearance.font_path, appearance.font_family, appearance.font_px, appearance.stroke)
        draw_translation(image, text, local, font_override=font_override)
        rgba = np.asarray(image)
    x, y, w, h = box
    alpha = rgba[:, :, 3:4].astype(np.float32)/255
    output[y:y+h, x:x+w] = np.uint8(rgba[:, :, :3][:, :, ::-1]*alpha + output[y:y+h, x:x+w]*(1-alpha))
    return output
