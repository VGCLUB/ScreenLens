from __future__ import annotations

import os
import sys

from screenlens.native import enable_dpi_awareness


def main():
    enable_dpi_awareness()
    # Capture native DLL diagnostics safely in windowless builds.
    if sys.stdout is None or sys.stderr is None:
        null = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or null
        sys.stderr = sys.stderr or null
    if len(sys.argv) == 3 and sys.argv[1] == "--overlay-self-test":
        from screenlens.selfcheck import run_overlay_check
        return run_overlay_check(sys.argv[2])
    if len(sys.argv) == 3 and sys.argv[1] == "--video-self-test":
        from screenlens.video_selfcheck import run_video_check
        return run_video_check(sys.argv[2])
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        import json
        import traceback
        from pathlib import Path
        report = {}
        try:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            import cv2
            import numpy as np
            from PySide6.QtWidgets import QApplication
            from screenlens.config import Settings, protect_secret
            from screenlens.native import monitor_rect
            from screenlens.ocr import OcrEngine
            from screenlens.ui import SettingsDialog
            app = QApplication([])
            dialog = SettingsDialog(Settings())
            assert dialog.collect().model == "deepseek-flash"
            report["qt_settings_dialog"] = "passed"
            assert protect_secret(protect_secret("self-check"), decrypt=True) == "self-check"
            report["windows_key_encryption"] = "passed"
            report["monitors"] = [monitor_rect(s.name(), s.geometry(), s.devicePixelRatio()) for s in app.screens()]
            engine = OcrEngine("中 / 英 / 日（自动）")
            sample = np.full((100, 620, 3), 255, dtype=np.uint8)
            cv2.putText(sample, "Hello ScreenLens 123", (15, 65), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 2)
            text = engine.recognize(sample)
            assert "Hello ScreenLens 123" in text
            report["bundled_ocr"] = text
            # Exercise the packaged async HTTP backend with an isolated local
            # server. No cloud service, user text or real API key is involved.
            import threading
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from screenlens.translation import Translator
            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    payload = ('data: ' + json.dumps({"choices": [{"delta": {"content": "自检通过"}, "finish_reason": "stop"}]}) + '\n\ndata: [DONE]\n\n').encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server_thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
            server_thread.start()
            translator = Translator(trust_env=False)
            try:
                settings = Settings(provider="自定义兼容 API", base_url=f"http://127.0.0.1:{server.server_port}/v1", model="self-test")
                result = translator.translate("self-test", settings, threading.Event(), lambda _: None)
                assert result.text == "自检通过"
                report["async_http_stream_loopback"] = "passed (simulated local response)"
            finally:
                translator.close()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=1)
            report["ok"] = True
            dialog.close()
        except Exception:
            report["ok"] = False
            report["error"] = traceback.format_exc()
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if report["ok"] else 1
    if len(sys.argv) == 3 and sys.argv[1] == "--ocr-image":
        import json
        import time
        import cv2
        import numpy as np
        from screenlens.ocr import OcrEngine
        engine = OcrEngine("中 / 英 / 日（自动）")
        image = cv2.imdecode(np.fromfile(sys.argv[2], dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("无法读取图片")
        start = time.perf_counter()
        text = engine.recognize(image)
        print(json.dumps({"text": text, "ocr_ms": round((time.perf_counter() - start) * 1000)}, ensure_ascii=False))
        return 0
    from screenlens.ui import run_app
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
