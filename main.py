"""
MaixCam 2 カスタム検知アプリ

maixIII_custom を MaixCam 2（Axera AX630C / MaixPy 4.x）向けに移植。
config.py の MODE を "coco" / "custom" で切り替えてA/Bテスト可能。
"""

import config
import datetime
import time
import requests
import base64
import os
from collections import deque

from maix import camera, display, image, nn, app, touchscreen


# ── ロギング ──────────────────────────────────────────────

def log(msg: str):
    with open(config.LOG_FILE, 'a') as f:
        f.write(datetime.datetime.now().strftime("[ %Y-%m-%d %H:%M:%S ] "))
        f.write(msg + '\n')
    print(msg)


# ── HTTP 送信 ─────────────────────────────────────────────

def post_count(device_id: int, token: str, count: int):
    headers = {
        'Authorization': token,
        'Content-Type' : 'application/json',
        'User-Agent'   : 'MaixCam2Custom',
        'Connection'   : 'close',
        'Cache-Control': 'no-cache',
    }
    body = {'device_id': device_id, 'count': count}
    try:
        resp = requests.post(config.HOST, headers=headers, json=body, timeout=10)
        if not resp.ok:
            log(f"[ ERROR ] post failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log(f"[ ERROR ] post exception: {e}")


# ── Google Drive アップロード ─────────────────────────────

def last_detection_path(name: str) -> str:
    return f"/tmp/maixcam2_custom_last_{name}.jpg"


def save_detection_snapshot(img, name: str):
    """検知が発生したフレームをtmpfsへ保存しておき、送信時にこれを使う"""
    try:
        img.save(last_detection_path(name))
    except Exception as e:
        log(f"[ ERROR ] snapshot save failed ({name}): {e}")


def _post_image_bytes(data: bytes, label_name: str, ts: datetime.datetime):
    if not config.GAS_URL:
        return
    try:
        payload = {
            "image"   : base64.b64encode(data).decode(),
            "label"   : label_name,
            "date"    : ts.strftime("%Y-%m-%d"),
            "filename": ts.strftime("%Y%m%d_%H%M%S") + ".jpg",
        }
        resp = requests.post(config.GAS_URL, json=payload, timeout=30)
        if not resp.ok:
            log(f"[ ERROR ] gdrive upload failed ({label_name}): {resp.status_code}")
        else:
            log(f"[ INFO ] gdrive uploaded: {label_name}/{ts.strftime('%Y-%m-%d')}/{payload['filename']}")
    except Exception as e:
        log(f"[ ERROR ] gdrive upload exception ({label_name}): {e}")


def upload_to_gdrive(img, label_name: str, ts: datetime.datetime):
    """現在のフレームをそのままアップロード（no_detection、およびフォールバック用）"""
    if not config.GAS_URL:
        return
    tmp = "/tmp/gdrive_snap.jpg"
    try:
        img.save(tmp)
        with open(tmp, 'rb') as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    _post_image_bytes(data, label_name, ts)


def upload_last_detection(label_name: str, ts: datetime.datetime) -> bool:
    """インターバル内で実際に検知された最後のフレームをアップロードする。
    スナップショットが存在しない場合は False を返す（呼び出し側でフォールバック）。"""
    if not config.GAS_URL:
        return True   # アップロード不要（正常扱い、フォールバックさせない）
    path = last_detection_path(label_name)
    if not os.path.exists(path):
        return False
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError as e:
        log(f"[ ERROR ] snapshot read failed ({label_name}): {e}")
        return False
    _post_image_bytes(data, label_name, ts)
    return True


# ── モデルロード ───────────────────────────────────────────

def load_model(model_path: str, model_class: str):
    if model_class == "YOLOv8":
        return nn.YOLOv8(model=model_path, dual_buff=True)
    else:
        return nn.YOLO11(model=model_path, dual_buff=True)


# ── ターゲット判定 ─────────────────────────────────────────

def match_targets(obj):
    """obj.class_id/obj.score にマッチする count_targets のエントリ一覧を返す"""
    matched = []
    for t in config.COUNT_TARGETS:
        th = t["classes"].get(obj.class_id)
        if th is not None and obj.score >= th:
            matched.append(t)
    return matched


# ── メイン処理 ─────────────────────────────────────────────

def main(disp):
    log(f"[ INFO ] MODE={config.MODE}  model={config.MODEL_PATH}")

    yolo = load_model(config.MODEL_PATH, config.MODEL_CLASS)
    cam  = camera.Camera(yolo.input_width(), yolo.input_height(), yolo.input_format())
    ts   = touchscreen.TouchScreen()

    max_counts      = {t["name"]: 0 for t in config.COUNT_TARGETS}
    confirm_windows = {t["name"]: deque() for t in config.COUNT_TARGETS}
    next_post_time  = time.time() + config.INTERVAL
    interval_labels = set()   # インターバル内で確定検知されたラベル名
    restart_triggered = False

    r, g, b = config.BOX_COLOR_RGB
    box_color = image.Color.from_rgb(r, g, b)
    r, g, b = config.LABEL_COLOR_RGB
    label_color = image.Color.from_rgb(r, g, b)

    screen_on    = False
    prev_pressed = False
    disp.set_backlight_off()

    log("[ INFO ] main loop started")

    while not app.need_exit():
        img = cam.read()
        if img is None:
            continue

        w, h = img.width(), img.height()
        now_ts = time.time()

        # 推論（THRESHOLDは各ターゲットの個別しきい値の最小値。個別フィルタは下で行う）
        objs = yolo.detect(img, conf_th=config.THRESHOLD)

        # フレーム内カウント＋ヒット判定
        frame_counts = {t["name"]: 0 for t in config.COUNT_TARGETS}
        frame_hit    = {t["name"]: False for t in config.COUNT_TARGETS}

        for obj in objs:
            for t in match_targets(obj):
                name = t["name"]
                frame_counts[name] += 1
                frame_hit[name] = True
                orig_name = (config.CLASS_NAMES[obj.class_id]
                             if obj.class_id < len(config.CLASS_NAMES)
                             else str(obj.class_id))
                if orig_name != name:
                    log(f"[ {name.upper()} ] proxy hit via '{orig_name}' score={obj.score:.2f}")

        # 複数フレーム確認（confirm設定があるターゲットのみ）＋フレーム最大値の更新
        for t in config.COUNT_TARGETS:
            name = t["name"]
            confirm = t.get("confirm")
            if confirm:
                window = confirm_windows[name]
                if frame_hit[name]:
                    window.append(now_ts)
                while window and now_ts - window[0] > confirm["window_sec"]:
                    window.popleft()
                confirmed = len(window) >= confirm["frames"]
            else:
                confirmed = frame_hit[name]

            if confirmed:
                interval_labels.add(name)
                if frame_counts[name] > max_counts[name]:
                    log(f"[ {name.upper()} ] new max: {frame_counts[name]}")
                    max_counts[name] = frame_counts[name]

        # 描画（常に実行 → アップロード画像にもboxが入る）
        for obj in objs:
            label_name = (config.CLASS_NAMES[obj.class_id]
                          if obj.class_id < len(config.CLASS_NAMES)
                          else str(obj.class_id))
            matched = match_targets(obj)
            is_target = bool(matched)
            color = box_color if is_target else image.Color.from_rgb(128, 128, 128)
            img.draw_rect(obj.x, obj.y, obj.w, obj.h, color, 2)
            if is_target and matched[0]["name"] != label_name:
                disp_text = f"{matched[0]['name']} ({label_name}) {obj.score:.2f}"
            else:
                disp_text = f"{label_name} {obj.score:.2f}"
            img.draw_string(obj.x, obj.y - 16, disp_text,
                            label_color if is_target else image.Color.from_rgb(180, 180, 180))

        for i, t in enumerate(config.COUNT_TARGETS):
            img.draw_string(4, 4 + i * 24,
                            f"{t['name']}: {max_counts[t['name']]}",
                            image.Color.from_rgb(0, 255, 0), scale=1.2)

        img.draw_string(4, h - 20,
                        f"MODE: {config.MODE}",
                        image.Color.from_rgb(200, 200, 200))

        img.draw_rect(w - 36, h - 36, 32, 32, image.Color.from_rgb(60, 60, 60), 2)
        img.draw_string(w - 30, h - 28, "<<", image.Color.from_rgb(255, 255, 255))

        # 検知があったターゲットは、このフレーム（検知ボックス描画済み）を
        # 「最後に検知された画像」として保存しておく（送信時にはこちらを使う）
        for t in config.COUNT_TARGETS:
            name = t["name"]
            if frame_hit[name]:
                save_detection_snapshot(img, name)

        if screen_on:
            disp.show(img)

        # タッチ処理（立ち上がりエッジ検出でON/OFFトグル）
        tx, ty, pressed = ts.read()
        touch_start = pressed and not prev_pressed
        prev_pressed = pressed

        if touch_start:
            if not screen_on:
                disp.set_backlight_on()
                screen_on = True
                log("[ INFO ] screen on")
            elif tx >= w - 36 and ty >= h - 36:
                log("[ INFO ] back button tapped")
                app.set_exit_flag(True)
            else:
                disp.set_backlight_off()
                screen_on = False
                log("[ INFO ] screen off")

        # 毎日 04:00 に自己再起動（起動直後はスキップしてリブートループを防ぐ）
        now_dt = datetime.datetime.now()
        with open('/proc/uptime') as _f:
            _uptime = float(_f.read().split()[0])
        if now_dt.hour == 4 and now_dt.minute == 0 and not restart_triggered and _uptime > 300:
            restart_triggered = True
            log("[ INFO ] 04:00 scheduled reboot")
            os.system("reboot")

        # 定期処理（HTTP送信 + Google Drive アップロード）
        if time.time() >= next_post_time:
            now            = datetime.datetime.now()
            next_post_time = time.time() + config.INTERVAL

            for t in config.COUNT_TARGETS:
                cnt = max_counts[t["name"]]
                log(f"[ INFO ] sending {t['name']}={cnt}")
                post_count(t["device_id"], t["token"], cnt)
            max_counts = {t["name"]: 0 for t in config.COUNT_TARGETS}

            # 検知ラベルごとのフォルダへアップロード（未検知は "no_detection"）。
            # 検知ラベルは、送信直前の現在フレームではなく、インターバル内で
            # 実際に検知された最後のフレームを使う（スナップショットが無ければ現在フレームにフォールバック）。
            if interval_labels:
                for label in interval_labels:
                    if not upload_last_detection(label, now):
                        upload_to_gdrive(img, label, now)
            else:
                upload_to_gdrive(img, "no_detection", now)
            interval_labels = set()


# ── 起動（エラーは画面に表示）────────────────────────────

disp = display.Display()   # display を最初に初期化
try:
    main(disp)
except Exception:
    import traceback
    msg = traceback.format_exc()
    print(msg)
    img = image.Image(disp.width(), disp.height())
    img.draw_string(0, 0, msg, image.Color.from_rgb(255, 255, 255))
    disp.show(img)
    while not app.need_exit():
        time.sleep_ms(300)
