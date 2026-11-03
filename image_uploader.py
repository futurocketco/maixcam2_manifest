"""
画像アップロード（Google Drive / GAS）を別スレッドで送る。

- 送信に失敗したときだけ、同じ画像データで 1 回だけ再送する（待機 RETRY_DELAY_SEC 秒）。
- 再送の待機や HTTP の待ち時間でメインループ（推論・数値送信）を止めないよう、
  送信は 1 本のワーカースレッドで順番に行う。
- 通信断で送信待ちが溜まり続けないよう、キューは上限付き（溢れたら古い方を捨てる）。
- 省電力モードへの移行時は cancel_retry() で再送待ちを打ち切る（送信待ちは 1 回ずつ送る）。
  retry_allowed_fn が False の間（省電力中・開始直前）は再送しない。

このモジュールは maix に依存しない（PC でテストできる）。
"""

import base64
import queue
import threading
import time

import requests

MAX_QUEUE = 6


def _check(resp) -> str:
    """成功なら ''、失敗なら理由を返す"""
    if not resp.ok:
        return f"HTTP {resp.status_code}"
    # GAS はスクリプトのエラーでも HTTP 200 を返すことがある（本文で判定）
    text = (resp.text or "")[:200]
    if text.startswith("error"):
        return f"GAS {text}"
    return ""


class ImageUploader:
    def __init__(self, url_fn, log_fn, retry_enabled_fn=lambda: True,
                 retry_delay_fn=lambda: 3.0, retry_allowed_fn=lambda: True,
                 timeout: float = 30, post=None):
        self._url_fn           = url_fn            # 送信先 (config.GAS_URL) を返す
        self._log              = log_fn
        self._retry_enabled_fn = retry_enabled_fn
        self._retry_delay_fn   = retry_delay_fn
        self._retry_allowed_fn = retry_allowed_fn  # False なら再送しない（省電力直前など）
        self._timeout          = timeout
        self._post             = post or requests.post
        self._q                = queue.Queue(maxsize=MAX_QUEUE)
        self._cancel           = threading.Event()
        self._thread           = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ── メインループから呼ぶ ──

    def submit(self, data: bytes, label: str, ts) -> None:
        payload = {
            "image"   : base64.b64encode(data).decode(),
            "label"   : label,
            "date"    : ts.strftime("%Y-%m-%d"),
            "filename": ts.strftime("%Y%m%d_%H%M%S") + ".jpg",
        }
        try:
            self._q.put_nowait(payload)
        except queue.Full:
            try:
                old = self._q.get_nowait()
                self._q.task_done()
                self._log(f"[ ERROR ] gdrive queue full, dropped: {old['label']}/{old['filename']}")
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(payload)
            except queue.Full:
                pass

    def cancel_retry(self) -> None:
        """実行中の再送待機を打ち切る（送信待ちは残す）"""
        self._cancel.set()

    def clear(self) -> None:
        """送信待ちを捨て、実行中の再送待機を打ち切る"""
        self._cancel.set()
        dropped = 0
        while True:
            try:
                self._q.get_nowait()
                self._q.task_done()
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            self._log(f"[ INFO ] gdrive pending uploads dropped: {dropped}")

    def wait_idle(self, timeout: float = 10) -> bool:
        """テスト用: キューが空になるまで待つ"""
        end = time.time() + timeout
        while time.time() < end:
            if self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False

    # ── ワーカー ──

    def _send_once(self, url: str, payload: dict) -> str:
        try:
            return _check(self._post(url, json=payload, timeout=self._timeout))
        except requests.exceptions.Timeout as e:
            return f"timeout: {e}"
        except requests.exceptions.ConnectionError as e:
            return f"connection error: {e}"
        except Exception as e:
            return f"exception: {e}"

    def _upload(self, payload: dict) -> None:
        url = self._url_fn()
        if not url:
            return
        name = f"{payload['label']}/{payload['date']}/{payload['filename']}"
        err = self._send_once(url, payload)
        if not err:
            self._log(f"[ INFO ] gdrive uploaded: {name}")
            return
        if not self._retry_enabled_fn():
            self._log(f"[ ERROR ] gdrive upload failed ({payload['label']}): {err}")
            return
        if not self._retry_allowed_fn():
            self._log(f"[ ERROR ] gdrive upload failed ({payload['label']}): {err} (retry skipped: power save)")
            return
        delay = self._retry_delay_fn()
        self._log(f"[ WARN ] gdrive upload failed ({payload['label']}): {err} -> retry in {delay:g}s")
        if self._cancel.wait(delay) or not self._retry_allowed_fn():
            self._log(f"[ ERROR ] gdrive retry cancelled ({payload['label']}): power save")
            return
        err = self._send_once(url, payload)
        if not err:
            self._log(f"[ INFO ] gdrive uploaded (retry ok): {name}")
        else:
            self._log(f"[ ERROR ] gdrive retry failed ({payload['label']}): {err}")

    def _run(self) -> None:
        while True:
            payload = self._q.get()
            self._cancel.clear()
            try:
                self._upload(payload)
            except Exception as e:
                self._log(f"[ ERROR ] gdrive upload exception ({payload.get('label')}): {e}")
            finally:
                self._q.task_done()
