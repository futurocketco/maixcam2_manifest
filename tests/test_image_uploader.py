import datetime
import threading

import requests

import image_uploader as iu

TS = datetime.datetime(2026, 11, 3, 12, 0, 0)


class Resp:
    def __init__(self, status=200, text="image was successfully uploaded"):
        self.status_code = status
        self.ok = status < 400
        self.text = text


def make(results, **kw):
    """results: 呼び出しごとに返す Resp または投げる例外"""
    calls, logs = [], []

    def post(url, json, timeout):
        calls.append(json)
        r = results[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    up = iu.ImageUploader(url_fn=lambda: "https://gas.example/exec", log_fn=logs.append,
                          retry_delay_fn=lambda: 0.01, post=post, **kw)
    return up, calls, logs


def run(up, n=1):
    for _ in range(n):
        up.submit(b"jpegdata", "bear", TS)
    assert up.wait_idle()


def test_success_sends_once():
    up, calls, logs = make([Resp()])
    run(up)
    assert len(calls) == 1
    assert any("gdrive uploaded: bear/2026-11-03/20261103_120000.jpg" in l for l in logs)


def test_retry_once_then_success_no_more_sends():
    up, calls, logs = make([Resp(500), Resp(), Resp()])
    run(up)
    assert len(calls) == 2
    assert calls[0] == calls[1]          # 同じ画像データで再送
    assert any("retry in" in l for l in logs)
    assert any("retry ok" in l for l in logs)


def test_two_failures_then_gives_up_and_keeps_working():
    up, calls, logs = make([requests.exceptions.Timeout("t"),
                            requests.exceptions.ConnectionError("c"),
                            Resp()])
    run(up)
    assert len(calls) == 2
    assert any("retry failed" in l and "connection error" in l for l in logs)
    run(up)                              # 次の画像は普通に送れる
    assert len(calls) == 3


def test_gas_error_body_counts_as_failure():
    up, calls, logs = make([Resp(200, "error: image is empty"), Resp()])
    run(up)
    assert len(calls) == 2


def test_retry_disabled():
    up, calls, logs = make([Resp(500)], retry_enabled_fn=lambda: False)
    run(up)
    assert len(calls) == 1


def test_retry_skipped_before_power_save():
    up, calls, logs = make([Resp(500)], retry_allowed_fn=lambda: False)
    run(up)
    assert len(calls) == 1
    assert any("retry skipped: power save" in l for l in logs)


def test_clear_cancels_retry_wait():
    started = threading.Event()
    calls, logs = [], []

    def post(url, json, timeout):
        calls.append(json)
        started.set()
        return Resp(500)

    up = iu.ImageUploader(url_fn=lambda: "u", log_fn=logs.append,
                          retry_delay_fn=lambda: 30, post=post)
    up.submit(b"x", "bear", TS)
    assert started.wait(2)
    up.clear()
    assert up.wait_idle(2)               # 30 秒待たずに終わる
    assert len(calls) == 1
    assert any("retry cancelled" in l for l in logs)


def test_queue_is_bounded():
    gate = threading.Event()
    calls, logs = [], []

    def post(url, json, timeout):
        gate.wait(5)
        calls.append(json)
        return Resp()

    up = iu.ImageUploader(url_fn=lambda: "u", log_fn=logs.append, post=post)
    for _ in range(iu.MAX_QUEUE + 5):
        up.submit(b"x", "bear", TS)
    gate.set()
    assert up.wait_idle()
    assert len(calls) <= iu.MAX_QUEUE + 1
    assert any("queue full" in l for l in logs)


def test_empty_url_skips():
    calls = []
    up = iu.ImageUploader(url_fn=lambda: "", log_fn=lambda m: None,
                          post=lambda *a, **k: calls.append(1))
    up.submit(b"x", "bear", TS)
    assert up.wait_idle()
    assert calls == []
