"""main.py をダミーの maix モジュールで動かし、時計を進めて
通常 → 省電力 → 復帰 の流れを確認する（機器・ネットワークは使わない）。"""

import datetime
import os
import runpy
import sys
import time
import types

import pytest

import ota_config
import power_save

_real_sleep = time.sleep

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = datetime.timezone(datetime.timedelta(hours=9))


class Sim:
    def __init__(self, start, frame_step, sleep_step, max_frames, bear_frames=()):
        self.now = start
        self.frame_step = frame_step
        self.sleep_step = sleep_step
        self.max_frames = max_frames
        self.bear_frames = set(bear_frames)
        self.frames = 0
        self.events = []        # (時刻, イベント)
        self.posts = []         # (時刻, url, json)

    def ev(self, name):
        self.events.append((self.now, name))


def fake_maix(sim):
    class Img:
        def width(self): return 320
        def height(self): return 320
        def draw_rect(self, *a, **k): pass
        def draw_string(self, *a, **k): pass
        def save(self, path):
            with open(path, "wb") as f:
                f.write(b"\xff\xd8jpeg")

    class Camera:
        def __init__(self, *a):
            sim.ev("camera_open")
        def read(self):
            sim.frames += 1
            sim.now += sim.frame_step
            return Img()
        def close(self):
            sim.ev("camera_close")

    class Obj:
        x = y = 10; w = h = 50; class_id = 21; score = 0.9   # bear

    class YOLO:
        def __init__(self, model, dual_buff):
            sim.ev("model_load")
        def input_width(self): return 320
        def input_height(self): return 320
        def input_format(self): return 0
        def detect(self, img, conf_th):
            sim.ev("detect")
            return [Obj()] if sim.frames in sim.bear_frames else []

    class Display:
        def __init__(self):
            self.on = True
        def set_backlight_off(self, ms=500, wait=False):
            # 実機では消灯済みのときに呼ぶと戻らなくなることがある
            assert self.on, "set_backlight_off() called while already off"
            self.on = False
            sim.ev("backlight_off")
        def set_backlight_on(self, ms=500, wait=False):
            self.on = True
        def show(self, img): pass
        def width(self): return 320
        def height(self): return 320

    class Touch:
        def read(self): return 0, 0, False

    maix = types.ModuleType("maix")
    maix.camera = types.SimpleNamespace(Camera=Camera)
    maix.display = types.SimpleNamespace(Display=Display)
    maix.image = types.SimpleNamespace(Color=types.SimpleNamespace(from_rgb=lambda *a: a),
                                       Image=lambda *a: Img())
    maix.nn = types.SimpleNamespace(YOLO11=YOLO, YOLOv8=YOLO)
    maix.app = types.SimpleNamespace(need_exit=lambda: sim.frames >= sim.max_frames,
                                     set_exit_flag=lambda v: None)
    maix.touchscreen = types.SimpleNamespace(TouchScreen=Touch)
    return maix


def run_main(monkeypatch, tmp_path, sim, power_save_time):
    import config
    import requests
    monkeypatch.setattr(config, "POWER_SAVE_TIME", power_save_time, raising=False)
    monkeypatch.setattr(config, "OTA_MANIFEST_BASE_URL", "", raising=False)
    monkeypatch.setattr(config, "GAS_URL", "https://gas.example/exec")
    monkeypatch.setattr(config, "HOST", "https://count.example/count")
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "log"))
    monkeypatch.setattr(config, "INTERVAL", 60)
    monkeypatch.setitem(sys.modules, "maix", fake_maix(sim))
    monkeypatch.setattr(ota_config, "OVERRIDE_FILE", str(tmp_path / "override.json"))

    class Resp:
        ok = True; status_code = 200; text = "image was successfully uploaded"

    def post(url, **kw):
        sim.posts.append((sim.now, url, kw.get("json")))
        return Resp()

    def sleep(sec):
        sim.now += sim.sleep_step

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(power_save, "now_jst", lambda: sim.now)
    monkeypatch.setattr(power_save, "cpu_low", lambda log: sim.ev("cpu_low"))
    monkeypatch.setattr(power_save, "cpu_normal", lambda log: sim.ev("cpu_normal"))
    monkeypatch.setattr(time, "time", lambda: sim.now.timestamp())
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(os, "system", lambda cmd: sim.ev(f"system:{cmd}"))
    monkeypatch.setattr(sys, "path", [ROOT] + sys.path)

    ns = runpy.run_path(os.path.join(ROOT, "main.py"), run_name="__main__")
    q = ns["uploader"]._q
    for _ in range(500):                 # 送信スレッドの完了を実時間で待つ
        if q.unfinished_tasks == 0:
            break
        _real_sleep(0.01)
    assert q.unfinished_tasks == 0
    with open(config.LOG_FILE) as f:
        return f.read()


def count_posts(sim):
    return [(t, p) for t, u, p in sim.posts if "count.example" in u]


def image_posts(sim):
    return [(t, p) for t, u, p in sim.posts if "gas.example" in u]


def test_overnight_cycle(monkeypatch, tmp_path):
    # 17:45 開始、1 フレーム 30 秒。省電力中は sleep 1 回 = 10 分
    sim = Sim(datetime.datetime(2026, 11, 3, 17, 45, tzinfo=JST),
              frame_step=datetime.timedelta(seconds=30),
              sleep_step=datetime.timedelta(minutes=10),
              max_frames=30, bear_frames={3})
    log = run_main(monkeypatch, tmp_path, sim, "17500400")
    names = [e for _, e in sim.events]

    start = datetime.datetime(2026, 11, 3, 17, 50, tzinfo=JST)
    end   = datetime.datetime(2026, 11, 4, 4, 0, tzinfo=JST)

    # 省電力への移行と復帰
    assert "power save start (power_save_time=17500400)" in log
    assert "power save end, resuming detection" in log
    assert names.count("camera_open") == 2 and names.count("model_load") == 2
    assert names.count("camera_close") == 1 and names.count("cpu_low") == 1

    # 時間帯の中では推論も送信もしない
    for t, e in sim.events:
        if e == "detect":
            assert not (start <= t < end), t
    for t, _ in count_posts(sim):        # 画像はワーカースレッドが送るので時刻が前後する
        assert not (start <= t < end), t

    # 復帰後に推論と送信が再開している
    assert any(e == "detect" and t >= end for t, e in sim.events)
    assert any(t >= end for t, _ in count_posts(sim))
    assert any(t >= end for t, _ in image_posts(sim))

    # 移行前の bear 検知は 17:50 前に送られている
    bear = [p for t, p in count_posts(sim) if p["device_id"] == 158 and p["count"] > 0]
    assert bear


def test_boot_inside_window_skips_camera(monkeypatch, tmp_path):
    sim = Sim(datetime.datetime(2026, 11, 3, 12, 30, tzinfo=JST),
              frame_step=datetime.timedelta(seconds=30),
              sleep_step=datetime.timedelta(minutes=5),
              max_frames=6)
    log = run_main(monkeypatch, tmp_path, sim, "12001300")
    first_open = next(t for t, e in sim.events if e == "camera_open")
    assert first_open >= datetime.datetime(2026, 11, 3, 13, 0, tzinfo=JST)
    assert "power save start" in log and "power save end" in log


def test_disabled_runs_continuously(monkeypatch, tmp_path):
    sim = Sim(datetime.datetime(2026, 11, 3, 17, 45, tzinfo=JST),
              frame_step=datetime.timedelta(minutes=1),
              sleep_step=datetime.timedelta(minutes=10),
              max_frames=20)
    log = run_main(monkeypatch, tmp_path, sim, "00000000")
    assert "power save start" not in log
    assert [e for _, e in sim.events].count("camera_open") == 1
    assert len(count_posts(sim)) >= 3 * 15


def test_invalid_clock_keeps_running(monkeypatch, tmp_path):
    sim = Sim(datetime.datetime(1970, 1, 1, 18, 0, tzinfo=JST),
              frame_step=datetime.timedelta(minutes=1),
              sleep_step=datetime.timedelta(minutes=10),
              max_frames=5)
    log = run_main(monkeypatch, tmp_path, sim, "17500400")
    assert "power save start" not in log
    assert log.count("system time looks invalid") == 1
    assert [e for _, e in sim.events].count("detect") == 5


def test_detection_just_before_window_is_sent(monkeypatch, tmp_path):
    # 17:49:30 のフレームでクマ検知 → 次の定期送信 (17:50 過ぎ) の前に省電力へ入る
    sim = Sim(datetime.datetime(2026, 11, 3, 17, 47, 0, tzinfo=JST),
              frame_step=datetime.timedelta(seconds=30),
              sleep_step=datetime.timedelta(minutes=30),
              max_frames=8, bear_frames={5})
    log = run_main(monkeypatch, tmp_path, sim, "17500400")
    assert "power save: sending detections of the last interval" in log
    bear = [p for t, p in count_posts(sim) if p["device_id"] == 158 and p["count"] == 1]
    assert len(bear) == 1
    assert any(p["label"] == "bear" for t, p in image_posts(sim))
    # 省電力中の数値送信はこの 1 回分だけ（それ以降は 04:00 まで送らない）。
    # 画像はワーカースレッドが送るので記録時刻が前後する → 数値送信だけで確認する
    in_window = [t for t, _ in count_posts(sim)
                 if datetime.datetime(2026, 11, 3, 17, 50, 1, tzinfo=JST) <= t
                 < datetime.datetime(2026, 11, 4, 4, 0, tzinfo=JST)]
    assert in_window == []
