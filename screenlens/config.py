from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ScreenLens"
CONFIG_PATH = APP_DIR / "settings.json"

TARGETS = {
    "简体中文": "Simplified Chinese", "繁體中文": "Traditional Chinese",
    "English": "English", "日本語": "Japanese", "한국어": "Korean",
    "Français": "French", "Deutsch": "German", "Español": "Spanish",
    "Русский": "Russian", "Português": "Portuguese", "العربية": "Arabic",
}
OCR_PROFILES = {
    "中 / 英 / 日（自动）": "multi", "韩语": "korean", "拉丁语系（法德西等）": "latin",
    "俄语 / 西里尔语系": "cyrillic", "阿拉伯语": "arabic", "天城文（印地语等）": "devanagari",
}
PRESETS = {
    "DeepSeek（推荐）": ("https://api.deepseek.com", "deepseek-flash"),
    "自定义兼容 API": ("https://api.example.com/v1", ""),
    "Ollama（本地）": ("http://127.0.0.1:11434/v1", "qwen3:8b"),
}


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect_secret(value: str, decrypt: bool = False) -> str:
    """Windows DPAPI, scoped to the current Windows user. Never fall back to plaintext."""
    if not value:
        return ""
    raw = base64.b64decode(value, validate=True) if decrypt else value.encode("utf-8")
    buf = ctypes.create_string_buffer(raw)
    source = _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("无法使用 Windows 账户加密保存密钥")
    try:
        result = ctypes.string_at(output.data, output.size)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel32.LocalFree(output.data)


@dataclass
class Settings:
    provider: str = "DeepSeek（推荐）"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key: str = ""
    target: str = "简体中文"
    ocr_profile: str = "中 / 英 / 日（自动）"
    interval_ms: int = 350
    stable_ms: int = 150
    timeout_s: int = 20
    font_size: int = 17
    always_on_top: bool = True
    subtitle_cover: bool = True
    subtitle_font_size: int = 26
    subtitle_opacity: int = 0
    subtitle_offset_x: int = 0
    subtitle_offset_y: int = 0
    subtitle_font: str = "自动匹配"
    subtitle_style_mode: str = "glyph"
    subtitle_missing_ms: int = 800
    subtitle_min_read_ms: int = 1200
    subtitle_lock_style: bool = True
    subtitle_complete_only: bool = True
    subtitle_focus_main: bool = True
    subtitle_ignore_watermarks: bool = True
    subtitle_ignore_text: str = ""

    def validate(self, require_key: bool = True) -> None:
        parsed = urlsplit(self.base_url.strip())
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("请输入有效的 API 地址，例如 https://api.deepseek.com")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("远程 API 请使用 HTTPS；HTTP 仅限本机服务。")
        if parsed.query or parsed.fragment:
            raise ValueError("API 基础地址中不能带查询参数或 #。")
        if not self.model.strip():
            raise ValueError("请填写模型名称。")
        if require_key and not self.api_key.strip() and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("请在设置中填写 API Key，然后再开始翻译。")
        if self.target not in TARGETS or self.ocr_profile not in OCR_PROFILES:
            raise ValueError("请选择有效的语言。")
        if not 150 <= self.interval_ms <= 3000 or not 0 <= self.stable_ms <= 1000:
            raise ValueError("检测间隔或字幕确认间隔超出范围。")
        if not 12 <= self.subtitle_font_size <= 72 or not 0 <= self.subtitle_opacity <= 100:
            raise ValueError("字幕字号或遮罩不透明度超出范围。")
        if not -2000 <= self.subtitle_offset_x <= 2000 or not -2000 <= self.subtitle_offset_y <= 2000:
            raise ValueError("字幕位置偏移超出范围。")
        if self.subtitle_style_mode not in ("glyph", "text", "box") or not 200 <= self.subtitle_missing_ms <= 3000 or not 0 <= self.subtitle_min_read_ms <= 5000:
            raise ValueError("字幕显示方式或同步时间超出范围。")
        if not isinstance(self.subtitle_ignore_text, str):
            raise ValueError("忽略文字应为每行一条的文本。")

    def save(self, path: Path = CONFIG_PATH) -> None:
        data = asdict(self)
        data.pop("api_key")
        data["encrypted_key"] = protect_secret(self.api_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Settings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "subtitle_style_mode" not in data:
                data["subtitle_opacity"] = 0
            key = protect_secret(data.pop("encrypted_key", ""), decrypt=True)
            data.pop("api_key", None)
            result = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__}, api_key=key)
            result.validate(require_key=False)
            return result
        except (ValueError, OSError, TypeError):
            return cls()
