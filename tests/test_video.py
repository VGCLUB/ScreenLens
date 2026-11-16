import json
import threading
from pathlib import Path

import httpx
import pytest

from screenlens.config import Settings
from screenlens.translation import Translator
from screenlens.video import Cue, choose_output, parse_srt, write_srt


def test_srt_roundtrip_keeps_timing_and_text(tmp_path):
    cues = [Cue(.5, 2.5, "Hello", "你好"), Cue(3, 6.5, "Good morning", "早上好")]
    path = tmp_path/"translated.srt"
    write_srt(cues, path)
    restored = parse_srt(path.read_text(encoding="utf-8-sig"))
    assert [(c.start,c.end,c.source) for c in restored] == [(.5,2.5,"你好"),(3,6.5,"早上好")]


def test_output_does_not_overwrite_input_or_existing_video(tmp_path):
    source = tmp_path/"source.mp4"
    source.touch()
    with pytest.raises(ValueError):
        choose_output(source, source)
    target = tmp_path/"translated.mp4"
    target.touch()
    assert choose_output(target, source).name == "translated_001.mp4"
    sidecar = tmp_path/"existing-subtitle.srt"
    sidecar.write_text("user-authored subtitles",encoding="utf-8")
    assert choose_output(sidecar.with_suffix(".mp4"),source).name == "existing-subtitle_001.mp4"


def test_batch_translation_preserves_ids_even_when_response_is_reordered():
    result = json.dumps([{"id":1,"text":"早上好"},{"id":0,"text":"你好"}],ensure_ascii=False)
    transport = httpx.MockTransport(lambda request:httpx.Response(200,json={"choices":[{"message":{"content":result}}]}))
    translator = Translator(transport)
    try:
        assert translator.translate_batch(["Hello","Good morning"],Settings(api_key="fake"),threading.Event()) == ["你好","早上好"]
    finally:
        translator.close()
