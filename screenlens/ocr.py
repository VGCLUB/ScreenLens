from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import APP_DIR, OCR_PROFILES


@dataclass
class TextLine:
    text: str
    box: tuple[int, int, int, int]
    score: float = 1.0


@dataclass
class OcrResult:
    text: str
    lines: list[TextLine]


def normalize_text(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip()).strip()


def thumbnail(image: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (192, 96), interpolation=cv2.INTER_AREA)


def image_changed(first: np.ndarray | None, second: np.ndarray) -> bool:
    if first is None:
        return True
    diff = cv2.absdiff(first, second)
    # A small changed word must still be noticed in a large capture area.
    return float(diff.mean()) > 0.8 or float(np.mean(diff > 24)) > 0.003


class OcrEngine:
    def __init__(self, profile: str):
        import rapidocr
        from rapidocr import RapidOCR, OCRVersion, ModelType, LangRec

        models = Path(rapidocr.__file__).parent / "models"
        params = {
            "Global.log_level": "error", "Global.use_cls": False,
            "Global.text_score": 0.45,
            "Det.model_path": str(models / "PP-OCRv6_det_small.onnx"),
            "Det.limit_side_len": 960, "Det.limit_type": "max",
            "EngineConfig.onnxruntime.intra_op_num_threads": min(4, os.cpu_count() or 2),
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
        lang = OCR_PROFILES[profile]
        if lang == "multi":
            params["Rec.model_path"] = str(models / "PP-OCRv6_rec_small.onnx")
        else:
            params.update({
                "Global.model_root_dir": str(APP_DIR / "models"),
                "Rec.lang_type": LangRec(lang),
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            })
        self.engine = RapidOCR(params=params)
        # Force lazy detector + recognizer loading before the first capture.
        sample = np.full((70, 400, 3), 255, dtype=np.uint8)
        cv2.putText(sample, "Ready 123", (8, 45), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        self.engine(sample, use_cls=False)

    def recognize(self, image: np.ndarray) -> str:
        return self.analyze(image).text

    def analyze(self, image: np.ndarray) -> OcrResult:
        # Small UI fonts benefit from 2x enlargement; keep full-screen work bounded.
        scale = 1
        if image.shape[0] < 140 and image.shape[1] < 1500:
            image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            scale = 2
        output = self.engine(image, use_cls=False)
        lines = []
        if output.txts is not None and output.boxes is not None:
            for index, (text, points) in enumerate(zip(output.txts, output.boxes)):
                points = np.asarray(points) / scale
                x, y = np.maximum(0, np.floor(points.min(axis=0))).astype(int)
                right, bottom = np.ceil(points.max(axis=0)).astype(int)
                lines.append(TextLine(normalize_text(text), (int(x), int(y), max(1, int(right-x)), max(1, int(bottom-y))),
                                      float(output.scores[index]) if output.scores is not None else 1.0))
        return OcrResult(normalize_text("\n".join(line.text for line in lines)), lines)
