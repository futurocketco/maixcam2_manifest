# TODO

2026-11-04 時点。今回の実証は現状（リポジトリ public、省電力なし）で臨み、以下は後日対応する。

## リポジトリ・OTA

- [ ] リポジトリを private に戻す
  - 機器に読み取り専用トークン（fine-grained、対象 `maixcam2_manifest` のみ、Contents: Read-only）を
    `/root/maixcam2_custom/github_token` に置いてから private にする（先に private にすると manifest を取れなくなる）
  - 確認: 機器のログに `OTA could not fetch any manifest` が出ないこと
- [ ] public の間は、`config.example.py` に奥多摩の device_id（142〜144）が残っている点を整理する（空欄化など）
- [ ] コードと manifest を別リポジトリに分けるか決める（現状は両方とも `maixcam2_manifest`）
- [ ] 台数が増えたら機器ごとの `manifest/devices/<ID>.json` を追加する（ID は person の device_id）

## 省電力（power_save_time）

- [ ] 実際の時間帯（例 `17500400`）で一晩通して動かし、04:00 の自己再起動と重なっても正常に復帰することを確認する
      （確認済みは 4 分間の試験 2 回のみ: 2026-11-03 23:54–23:58、2026-11-04 00:47–00:51 の OTA 経由）
- [ ] 画面を点けたまま省電力に入るケースを実機で確認する（`set_backlight_off(0)` の経路は未検証）
- [ ] 電流計で消費電力を実測する（通常時 / 省電力中。CPU は通常 約145%・系全体 91% → 省電力中 約7%・18% まで下がることは確認済み）
- [ ] ハードウェアスリープを使う場合は、RTC（BM8563）の INT ピンを SoC に配線し、デバイスツリーに割り込みを定義する必要がある
      （現状は `rtcwake -m mem` が `rtc0 not enabled for wakeup events` で不可）

## 既存コードの不具合・改善

- [ ] `main.py` の例外時の処理が存在しない `time.sleep_ms` を呼んでいる（エラー画面を出した直後にアプリが終了する）。元からの不具合で未修正
- [ ] ログファイル `log` が無制限に増える（奥多摩の 3 か月で約 36MB）。ローテーションを検討する
- [ ] 数値送信（manacam.net）は再送なし（今回の仕様どおり従来の動作を維持）。必要なら再送を検討する

## 現場運用

- [ ] 画面から Wi-Fi を切り替えると Custom Detector が終了する（KEY_OK）。切り替え後はアイコンをタップするか電源を入れ直す。手順書に記載する
