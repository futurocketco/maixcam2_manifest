"""
OTA 設定更新（rdk_x3_thermal_pexai の manifest 方式を設定専用に移植）

GitHub 上の manifest を定期的に取得し、config_override の許可キーだけを検証して
機器に保存・即時反映する（アプリの再起動は不要）。モデルの OTA 更新は扱わない。

manifest:
  <OTA_MANIFEST_BASE_URL>/default.json          全機共通
  <OTA_MANIFEST_BASE_URL>/devices/<ID>.json     機器個別（default にディープマージ）

  {"config_override": {"power_save_time": "17500400"}}

保存先 (OVERRIDE_FILE) は再起動後も読み込まれる。不正な値は拒否し、前の有効値を保つ。
認証トークン・URL などの秘密の値は manifest からは変更できない。

このモジュールは maix に依存しない（PC でテストできる）。
"""

import copy
import json
import os
import threading
import time

import requests

import power_save

OVERRIDE_FILE = "/root/maixcam2_custom/config_override.json"
TOKEN_FILE    = "/root/maixcam2_custom/github_token"   # private リポジトリ用（任意）


def _v_power_save(v):
    if not isinstance(v, str):
        raise ValueError("must be a string like \"17500400\" (quote it to keep leading zeros)")
    power_save.parse(v)
    return v


def _v_bool(v):
    if not isinstance(v, bool):
        raise ValueError("must be true or false")
    return v


def _v_retry_delay(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 60:
        raise ValueError("must be a number between 0 and 60")
    return float(v)


def _v_ota_interval(v):
    if isinstance(v, bool) or not isinstance(v, int) or not 300 <= v <= 86400:
        raise ValueError("must be an integer between 300 and 86400")
    return v


# manifest のキー → (config の属性名, 検証関数)
ALLOWED = {
    "power_save_time"            : ("POWER_SAVE_TIME", _v_power_save),
    "gdrive_upload_retry_enabled": ("GDRIVE_UPLOAD_RETRY_ENABLED", _v_bool),
    "gdrive_retry_delay_sec"     : ("GDRIVE_RETRY_DELAY_SEC", _v_retry_delay),
    "ota_check_interval"         : ("OTA_CHECK_INTERVAL", _v_ota_interval),
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(result.get(k), dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def validate(override: dict, log_fn) -> dict:
    """許可キーだけを検証して返す（キーは小文字の manifest 名、不正値は捨てる）"""
    valid = {}
    for key, value in (override or {}).items():
        name = key.lower()
        if name not in ALLOWED:
            log_fn(f"[ WARN ] OTA ignored key: {key}")
            continue
        try:
            valid[name] = ALLOWED[name][1](value)
        except ValueError as e:
            log_fn(f"[ ERROR ] OTA rejected {key}={value!r}: {e}")
    return valid


def load_saved(path: str = OVERRIDE_FILE) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(values: dict, path: str = OVERRIDE_FILE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(values, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply_to_config(config, values: dict) -> None:
    for name, value in values.items():
        setattr(config, ALLOWED[name][0], value)


def apply_saved(config, log_fn, path: str = OVERRIDE_FILE) -> None:
    """起動時: 保存済みの設定を検証し直して config に反映する"""
    saved = validate(load_saved(path), log_fn)
    if saved:
        apply_to_config(config, saved)
        log_fn(f"[ INFO ] OTA saved settings loaded: {saved}")


class OTAConfigChecker:
    """バックグラウンドで manifest を定期確認する（メインループを止めない）"""

    def __init__(self, config, log_fn, path: str = OVERRIDE_FILE, get=None):
        self._config = config
        self._log    = log_fn
        self._path   = path
        self._get    = get or requests.get
        self._wake   = threading.Event()
        self._paused = threading.Event()
        self._lock   = threading.Lock()

    # ── メインループから呼ぶ ──

    def start(self) -> None:
        if not getattr(self._config, "OTA_MANIFEST_BASE_URL", ""):
            self._log("[ INFO ] OTA disabled (OTA_MANIFEST_BASE_URL is empty)")
            return
        threading.Thread(target=self._run, daemon=True).start()

    def pause(self) -> None:
        """省電力中は確認しない"""
        self._paused.set()

    def resume(self) -> None:
        """省電力から戻ったらすぐ 1 回確認する"""
        self._paused.clear()
        self._wake.set()

    # ── 取得と反映 ──

    def _headers(self) -> dict:
        try:
            with open(TOKEN_FILE) as f:
                token = f.read().strip()
            return {"Authorization": f"token {token}"} if token else {}
        except OSError:
            return {}

    def _fetch(self, url: str):
        try:
            r = self._get(url, timeout=10, headers=self._headers())
            if r.ok:
                return r.json()
            if r.status_code != 404:
                self._log(f"[ ERROR ] OTA fetch failed: {r.status_code} {url}")
        except Exception as e:
            self._log(f"[ ERROR ] OTA fetch exception: {e}")
        return None

    def check_once(self) -> dict:
        """manifest を取得して反映する。変わった項目を返す。"""
        base_url = self._config.OTA_MANIFEST_BASE_URL.rstrip("/")
        default  = self._fetch(f"{base_url}/default.json")
        device   = None
        dev_id   = str(getattr(self._config, "OTA_DEVICE_ID", "") or "")
        if dev_id:
            device = self._fetch(f"{base_url}/devices/{dev_id}.json")
        if default is None and device is None:
            # private リポジトリでトークンが無い／間違っている場合も 404 になる
            self._log(f"[ ERROR ] OTA could not fetch any manifest from {base_url} "
                      "(check network and /root/maixcam2_custom/github_token)")
            return {}
        manifest = _deep_merge(default if isinstance(default, dict) else {},
                               device if isinstance(device, dict) else {})
        override = manifest.get("config_override") or {}
        if not isinstance(override, dict):
            self._log("[ ERROR ] OTA config_override is not an object")
            return {}

        with self._lock:
            new = validate(override, self._log)
            saved = validate(load_saved(self._path), lambda m: None)
            merged = dict(saved)
            merged.update(new)
            changed = {k: v for k, v in merged.items() if saved.get(k) != v}
            if not changed:
                return {}
            save(merged, self._path)
            apply_to_config(self._config, merged)
        for k, v in changed.items():
            self._log(f"[ INFO ] OTA setting changed: {k} {saved.get(k)!r} -> {v!r}")
        return changed

    def _run(self) -> None:
        while True:
            if not self._paused.is_set():
                try:
                    self.check_once()
                except Exception as e:
                    self._log(f"[ ERROR ] OTA check exception: {e}")
            self._wake.wait(getattr(self._config, "OTA_CHECK_INTERVAL", 3600))
            self._wake.clear()
