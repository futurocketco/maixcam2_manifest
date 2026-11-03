# maixcam2_custom（Custom Detector / クマ・シカ検知）

MaixCAM2 上で COCO YOLO11n を動かし、bear / person / deer（鹿候補）の検知数を
manacam.net に送り、検知画像を Google Drive（GAS 経由）に保存するアプリ。
機器では `/maixapp/apps/maixcam2_custom` にインストールされ、`/maixapp/auto_start.txt` で自動起動する。

| ファイル | 内容 |
|---|---|
| `main.py` | メインループ（推論・数値送信・画像送信・04:00 再起動・省電力） |
| `config.py` | 機器ごとの設定（**トークン・GAS URL を含むのでコミットしない**。雛形は `config.example.py`） |
| `image_uploader.py` | 画像送信（別スレッド、失敗時 1 回だけ再送） |
| `power_save.py` | 省電力時間帯の判定、CPU の省電力制御 |
| `ota_config.py` | OTA 設定更新（GitHub manifest の `config_override`） |
| `manifest/` | OTA 用 manifest（`default.json` と `devices/<ID>.json`） |
| `tests/` | PC 用テスト（`maix` はダミーで代用） |

PC でのテスト:

```bash
python3 -m venv .venv && .venv/bin/pip install pytest requests==2.32.5
.venv/bin/python -m pytest -q
```

## 画像送信の再送

送信に失敗したとき（HTTP エラー、タイムアウト、接続エラー、GAS が `error...` を返したとき）だけ、
同じ画像データで **1 回だけ** 再送する。2 回とも失敗したらログに残して次へ進む。

- 送信は 1 本のワーカースレッドで順番に行う。再送の待ち時間や HTTP の待ちで推論・数値送信は止まらない。
- 送信待ちは最大 6 件。通信断で溢れたら古いものから捨てる（ログに `gdrive queue full, dropped`）。
- 省電力中・省電力が始まる直前（再送待ちの間に始まる場合）は再送しない。
- 数値送信（manacam.net）は従来どおり再送しない。

| 設定 (`config.py`) | 既定値 | 内容 |
|---|---|---|
| `GDRIVE_UPLOAD_RETRY_ENABLED` | `True` | 再送の ON/OFF |
| `GDRIVE_RETRY_DELAY_SEC` | `3.0` | 再送までの待機（秒） |

## 省電力時間帯

```python
POWER_SAVE_TIME: str = "17500400"   # HHMMHHMM = 開始 + 通常動作の再開（日本時間）
```

- `"17500400"` → 17:50〜翌 04:00、`"12001300"` → 12:00〜13:00、`"00000000"` → 無効（24 時間稼働）。
- 必ず文字列で書く（数値にすると先頭の 0 が消える）。開始と終了が同じ値・24 時以上などの不正な値は拒否する。
- 時間帯の中では次を止める: YOLO 推論、カメラ（`close()`）、数値送信、画像送信、OTA 確認。
  さらに CPU を 1 コア（cpu1 オフライン）・208 MHz に下げ、画面のバックライトを消す。
- 止めないもの: アプリ本体（1 秒ごとに時刻を確認するだけ）、Wi-Fi、SSH、04:00 の再起動。
- 時間帯に入る直前のインターバルで検知があった場合だけ、止める前にその分を送る。
- 時間帯の中で起動（再起動）した場合は、カメラ・モデルを開かずにそのまま省電力に入る。
- システム時刻が 2025 年より前（時刻が取れていない）なら、省電力に入らず通常動作を続ける（ログは 1 回だけ）。
- 終了時刻になると CPU を戻し、モデルとカメラを開き直して検知・送信を再開する。

### ハードウェアスリープを使わない理由

MaixCAM2 はカーネルとしては deep suspend（`/sys/power/mem_sleep` = `[deep]`）に対応しているが、
RTC（BM8563 / PCF8563 互換、I2C 1-0051）の割り込みがデバイスツリーに定義されておらず
（`interrupts` プロパティなし）、RTC のアラームで SoC を起こせない。
実機で `rtcwake -m mem -s 60` を実行すると `rtc0 not enabled for wakeup events` で拒否された（2026-11-03）。
時刻指定で確実に自動復帰できないため、サスペンドとシャットダウンは使わず、アプリ内の低負荷待機にしている。

## OTA 設定更新

[rdk_x3_thermal_pexai](https://github.com/futurocketco/rdk_x3_thermal_pexai) と同じ manifest 方式。
`OTA_CHECK_INTERVAL` 秒ごと（既定 1 時間）と起動直後・省電力からの復帰直後に、別スレッドで manifest を取得する。

```
<OTA_MANIFEST_BASE_URL>/default.json         全機共通
<OTA_MANIFEST_BASE_URL>/devices/<ID>.json    機器個別（default に上書き）
```

```json
{
  "config_override": {
    "power_save_time": "17500400"
  }
}
```

| `config_override` のキー | 内容 |
|---|---|
| `power_save_time` | 省電力時間帯（8 桁の文字列） |
| `gdrive_upload_retry_enabled` | 画像の再送 ON/OFF（true / false） |
| `gdrive_retry_delay_sec` | 再送までの待機（0〜60 秒） |
| `ota_check_interval` | OTA 確認間隔（300〜86400 秒） |

- 検証に通った値だけを `/root/maixcam2_custom/config_override.json` に保存し、**再起動せずに反映**する。
  保存した値は再起動後も読み込まれ、`config.py` の値より優先される。
- 不正な値はログ（`OTA rejected ...`）に残して捨て、前の有効な値を使い続ける。上表以外のキー（トークン・URL など）は無視する。
- manifest からキーを消しても保存済みの値は残る。無効に戻すときは `"power_save_time": "00000000"` を書く。
- 省電力中は確認しない。復帰後すぐに確認して反映する。

機器側の設定（`config.py`）:

| 設定 | 内容 |
|---|---|
| `OTA_MANIFEST_BASE_URL` | 例 `https://raw.githubusercontent.com/futurocketco/<repo>/main/manifest`。空なら OTA しない |
| `OTA_DEVICE_ID` | `devices/<ID>.json` の ID（person の device_id を使う） |
| `OTA_CHECK_INTERVAL` | 確認間隔（秒） |

private リポジトリの場合は、機器の `/root/maixcam2_custom/github_token` にトークンを置く（リポジトリには入れない）。

## ログ

アプリのログは `log`（アプリのフォルダ）。主なメッセージ:

- `gdrive upload failed (...) -> retry in 3s` / `gdrive uploaded (retry ok)` / `gdrive retry failed`
- `power save start (power_save_time=...)` / `power save end, resuming detection`
- `OTA setting changed: power_save_time '...' -> '...'` / `OTA rejected ...`
- `power save: system time looks invalid ...`
