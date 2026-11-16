"""Reproduce the synthetic English/Japanese OCR latency benchmark."""
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screenlens.ocr import OcrEngine

root = Path(__file__).resolve().parent
image = cv2.imdecode(np.fromfile(str(root / "ocr-sample.png"), dtype=np.uint8), cv2.IMREAD_COLOR)
engine = OcrEngine("中 / 英 / 日（自动）")
runs = []
for _ in range(5):
    start = time.perf_counter()
    text = engine.recognize(image)
    runs.append((time.perf_counter() - start) * 1000)
report = {
    "fixture": "qa/ocr-sample.png", "image_size": [image.shape[1], image.shape[0]],
    "runs_ms": [round(value, 1) for value in runs],
    "median_ms": round(statistics.median(runs), 1), "text": text,
    "cloud_api_tested": False,
}
(root / "ocr-benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
