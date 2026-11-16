import asyncio
import json
import threading
import time

import httpx
import pytest

from screenlens.config import Settings
from screenlens.translation import Cancelled, Translator, TranslationError


def sse(content="你好", reason="stop"):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": None}]})
            + "\n\ndata: " + json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]})
            + "\n\ndata: [DONE]\n\n").encode()


def test_stream_cache_target_and_request():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse())

    translator = Translator(httpx.MockTransport(handler))
    try:
        settings = Settings(api_key="fake-test-only")
        chunks = []
        first = translator.translate("Hello", settings, threading.Event(), chunks.append)
        cached = translator.translate("Hello", settings, threading.Event(), chunks.append)
        assert first.text == "你好" and not first.cached and cached.cached
        assert len(requests) == 1 and chunks == ["你好", "你好"]
        payload = json.loads(requests[0].content)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["stream"] is True
        assert str(requests[0].url) == "https://api.deepseek.com/chat/completions"
        settings.target = "日本語"
        translator.translate("Hello", settings, threading.Event(), lambda _: None)
        assert len(requests) == 2
    finally:
        translator.close()


@pytest.mark.parametrize("status,match", [(401, "API Key"), (402, "余额"), (404, "不存在"), (429, "频繁"), (503, "503")])
def test_errors_do_not_expose_response_or_secret(status, match):
    translator = Translator(httpx.MockTransport(lambda _: httpx.Response(status, text="sensitive-server-body")))
    try:
        with pytest.raises(TranslationError, match=match) as caught:
            translator.translate("Hello", Settings(api_key="fake-secret"), threading.Event(), lambda _: None)
        assert "sensitive" not in str(caught.value) and "fake-secret" not in str(caught.value)
        assert not translator.cache
    finally:
        translator.close()


def test_cancel_while_waiting_for_first_token():
    async def slow_handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, content=sse())

    translator = Translator(httpx.MockTransport(slow_handler))
    cancel = threading.Event()
    timer = threading.Timer(0.08, cancel.set)
    timer.start()
    start = time.perf_counter()
    try:
        with pytest.raises(Cancelled):
            translator.translate("old subtitle", Settings(api_key="fake"), cancel, lambda _: pytest.fail("stale text"))
        assert time.perf_counter() - start < 0.5
        assert not translator.cache
    finally:
        timer.join()
        translator.close()


@pytest.mark.parametrize("reason", ["length", None])
def test_truncated_stream_not_cached(reason):
    payload = sse(reason=reason) if reason else b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
    translator = Translator(httpx.MockTransport(lambda _: httpx.Response(200, content=payload)))
    try:
        with pytest.raises(TranslationError):
            translator.translate("text", Settings(api_key="fake"), threading.Event(), lambda _: None)
        assert not translator.cache
    finally:
        translator.close()


def test_nonstream_compatible_server():
    translator = Translator(httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "こんにちは"}}]})))
    try:
        assert translator.translate("Hello", Settings(api_key="fake"), threading.Event(), lambda _: None).text == "こんにちは"
    finally:
        translator.close()


def test_custom_provider_does_not_receive_deepseek_extension():
    def handler(request):
        assert "thinking" not in json.loads(request.content)
        assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
        return httpx.Response(200, content=sse())

    translator = Translator(httpx.MockTransport(handler))
    try:
        translator.translate("Hello", Settings(provider="自定义兼容 API", base_url="http://127.0.0.1:11434/v1"), threading.Event(), lambda _: None)
    finally:
        translator.close()
