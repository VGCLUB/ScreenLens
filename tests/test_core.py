import json

import numpy as np
import pytest
from PySide6.QtCore import QRect, QSize

from screenlens.config import Settings, protect_secret
from screenlens.ocr import image_changed, normalize_text
from screenlens.selection import to_native_region


def test_settings_dpapi_roundtrip(tmp_path):
    settings = Settings(api_key="test-key-not-real-123")
    path = tmp_path / "settings.json"
    settings.save(path)
    content = path.read_text(encoding="utf-8")
    assert settings.api_key not in content
    assert "api_key" not in json.loads(content)
    assert Settings.load(path).api_key == settings.api_key
    assert protect_secret(protect_secret("日本語+English"), decrypt=True) == "日本語+English"


@pytest.mark.parametrize("url", ["http://example.com", "https://key:password@example.com", "file:///tmp/key", "https://example.com?key=abc"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        Settings(base_url=url, api_key="test").validate()


def test_blank_key_still_allows_local_service():
    Settings(base_url="http://127.0.0.1:11434/v1", api_key="").validate()
    with pytest.raises(ValueError):
        Settings(api_key="").validate()


def test_mixed_dpi_and_negative_monitor_origin():
    region = to_native_region(QRect(100, 60, 500, 120), QSize(1920, 1080), (-3840, 0, 3840, 2160))
    assert region.as_dict() == {"left": -3640, "top": 120, "width": 1000, "height": 240}
    reverse = QRect(600, 180, -500, -120).normalized()
    assert to_native_region(reverse, QSize(1920, 1080), (0, 0, 3840, 2160)).width == 1000


def test_change_gate_notices_small_word_and_ignores_identical_image():
    image = np.zeros((96, 192), dtype=np.uint8)
    assert image_changed(None, image)
    assert not image_changed(image, image.copy())
    changed = image.copy()
    changed[10:20, 10:20] = 255
    assert image_changed(image, changed)


def test_ocr_normalization_keeps_languages_and_line_breaks():
    assert normalize_text("  Hello   world  \n\n  今日はいい天気。  ") == "Hello world\n今日はいい天気。"
