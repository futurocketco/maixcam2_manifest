"""
指定時間帯の省電力モード

power_save_time = "HHMMHHMM"（前半4桁 = 省電力開始、後半4桁 = 通常動作再開、日本時間）
  "17500400" → 17:50〜翌04:00
  "00000000" → 無効（24時間稼働）

MaixCAM2 では RTC (BM8563) の割り込みが SoC に配線されておらず (デバイスツリーに
interrupts 定義なし、rtcwake は "rtc0 not enabled for wakeup events")、時刻指定の
自動復帰ができないため、ハードウェアスリープは使わない。
省電力中は YOLO・カメラ・送信を止め、CPU を 1 コア・低クロックにして待機する。

このモジュールは maix に依存しない（PC でテストできる）。
"""

import datetime
import os
import re

DISABLED = "00000000"

_TZ_JST = datetime.timezone(datetime.timedelta(hours=9), "JST")

# この年より前の時刻は「時刻が取れていない」とみなす（RTC 電池切れ等で 1970 年になる）
_MIN_VALID_YEAR = 2025

_CPU_DIR      = "/sys/devices/system/cpu"
_CPU0_FREQ    = _CPU_DIR + "/cpu0/cpufreq"
_FREQ_NORMAL  = 1200000   # kHz（起動時の値）
_FREQ_SAVE    = 208000    # kHz（SSH が実用的に使える最低段。24000 は未検証）


def parse(value):
    """'HHMMHHMM' を ((開始時, 分), (終了時, 分)) に変換する。
    無効 ('00000000') は None。不正な値は ValueError。"""
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"power_save_time must be an 8-digit string: {value!r}")
    if value == DISABLED:
        return None
    sh, sm, eh, em = int(value[0:2]), int(value[2:4]), int(value[4:6]), int(value[6:8])
    for h, m in ((sh, sm), (eh, em)):
        if h > 23 or m > 59:
            raise ValueError(f"power_save_time has an invalid time: {value!r}")
    if (sh, sm) == (eh, em):
        raise ValueError(f"power_save_time start and end are the same: {value!r}")
    return (sh, sm), (eh, em)


def is_valid(value) -> bool:
    try:
        parse(value)
        return True
    except ValueError:
        return False


def now_jst():
    return datetime.datetime.now(_TZ_JST)


def time_is_valid(now) -> bool:
    return now.year >= _MIN_VALID_YEAR


def in_window(value, now) -> bool:
    """now（JST の datetime）が省電力時間帯に入っていれば True。
    無効設定・不正な設定・時刻不明のときは False（通常動作を続ける）。"""
    try:
        window = parse(value)
    except ValueError:
        return False
    if window is None or not time_is_valid(now):
        return False
    start = window[0][0] * 60 + window[0][1]
    end   = window[1][0] * 60 + window[1][1]
    cur   = now.hour * 60 + now.minute
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end   # 日付をまたぐ


def starts_within(value, now, seconds: float) -> bool:
    """いまは時間帯の外だが、seconds 秒以内に省電力が始まる場合 True"""
    return (not in_window(value, now)
            and in_window(value, now + datetime.timedelta(seconds=seconds)))


# ── CPU の省電力制御（失敗しても動作は止めない）──────────────

def _write(path: str, value: str) -> bool:
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError:
        return False


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _cpu_state():
    return _read(_CPU_DIR + "/cpu1/online"), _read(_CPU0_FREQ + "/scaling_setspeed")


def _set_cpu(low: bool, log_fn, quiet: bool = False) -> bool:
    """CPU を省電力（1 コア・低クロック）/ 通常に設定する。変更したら True。
    コアのオン/オフで周波数が変わることがあるので、コアを先に切り替えてから周波数を書く。"""
    if not os.path.isdir(_CPU0_FREQ):
        return False
    want = ("0", str(_FREQ_SAVE)) if low else ("1", str(_FREQ_NORMAL))
    if _cpu_state() == want:
        return False
    _write(_CPU_DIR + "/cpu1/online", want[0])
    _write(_CPU0_FREQ + "/scaling_setspeed", want[1])
    if _cpu_state() != want and not quiet:
        log_fn(f"[ WARN ] power save: cpu control failed (low={low}, state={_cpu_state()})")
    return True


def cpu_low(log_fn) -> None:
    _set_cpu(True, log_fn)


def cpu_low_keep(log_fn) -> bool:
    """省電力中に定期的に呼ぶ。MaixPy（NPU の解放など）が後から周波数を戻すことがあるため、
    ずれていたら設定し直す。設定し直したら True。"""
    return _set_cpu(True, log_fn, quiet=True)


def cpu_normal(log_fn) -> None:
    _set_cpu(False, log_fn)
