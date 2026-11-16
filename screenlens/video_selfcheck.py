"""End-to-end local video checks; translations are simulated by a local server."""
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def run_video_check(report_path):
    from .config import Settings
    from .media import preview_frame, probe
    from .translation import Translator
    from .video import VideoOptions, VideoProcessor
    import cv2
    import numpy as np
    report_path = Path(report_path)
    fixtures = report_path.parent / "video-fixtures"
    outputs = report_path.parent / "video-output"
    outputs.mkdir(exist_ok=True)
    report = {"translation_service": "simulated local HTTP service; not a cloud translation quality test", "cases": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            inputs = json.loads(request["messages"][-1]["content"])
            values = []
            for item in inputs:
                source = item["text"].upper()
                value = "你好，视译" if "SCREENLENS" in source else "早上好" if source.strip() == "GOOD MORNING" else "你好，这是一次字幕翻译测试。"
                values.append({"id": item["id"], "text": value})
            content = json.dumps(values, ensure_ascii=False)
            body = ("data: "+json.dumps({"choices":[{"delta":{"content":content},"finish_reason":"stop"}]},ensure_ascii=False)+"\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True)
    thread.start()
    settings = Settings(provider="自定义兼容 API",base_url=f"http://127.0.0.1:{server.server_port}/v1",model="local-self-test")
    try:
        for case, expected in (("embedded","embedded"),("hard","ocr"),("speech","speech")):
            path = fixtures/f"{case}.mp4"
            if not path.exists():
                raise FileNotFoundError("Generate the synthetic QA fixtures before video self-test")
            info = probe(path)
            result = VideoProcessor(VideoOptions(str(path),str(outputs/f"{case}-translated.mp4")),settings,
                                    translator_factory=lambda:Translator(trust_env=False)).process()
            output_info = probe(result["output"])
            assert output_info.audio and (output_info.width,output_info.height)==(info.width,info.height)
            assert abs(output_info.duration-info.duration)<.5
            assert result["source"]==expected,result
            project = json.loads(Path(result["output"]).with_suffix(".project.json").read_text(encoding="utf-8"))
            if case=="hard":
                assert len(project["cues"])==2,project
                assert abs(project["cues"][0]["start"]-.5)<=.25
                assert abs(project["cues"][0]["end"]-2.5)<=.25
                original = preview_frame(info,1.4)
                translated = preview_frame(output_info,1.4)
                # Pixel differences outside the subtitle strip are encoding-only.
                outside = float(np.abs(original[:250].astype(float)-translated[:250].astype(float)).mean())
                assert outside<5,outside
                cv2.imencode(".png",translated)[1].tofile(str(report_path.with_name("video-translated-preview.png")))
            if case=="speech":
                text = " ".join(c["source"] for c in project["cues"])
                assert "test" in text.lower(),text
            report["cases"].append({"case":case,**result,"output_has_audio":output_info.audio,"ok":True})
        report["ok"] = True
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if report["ok"] else 1
