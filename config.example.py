##################################################
#   A/B テスト切替
#   "coco"   : COCO 80クラスモデルで検知（プリインストール済み）
#   "custom" : カスタム学習済みモデルで検知
##################################################

MODE = "coco"

##################################################
#   モードごとの設定
##################################################

_COCO = {
    "model_path"   : "/root/models/yolo11n.mud",
    "model_class"  : "YOLO11",   # nn.YOLO11 を使用
    "class_names"  : [
        "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
        "truck", "boat", "traffic light", "fire hydrant", "stop sign",
        "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
        "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
        "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
        "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
        "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
        "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv",
        "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
        "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
        "scissors", "teddy bear", "hair drier", "toothbrush"
    ],
    "count_targets": [
        # name   : 表示名 / 送信識別名 / Google Driveアップロード先フォルダ名
        # classes: {COCOクラス番号: 個別しきい値} -- 複数クラスを1ターゲットに束ねられる
        # confirm: 直近 window_sec 秒間に frames 回以上検出された場合のみ確定（省略時は即時確定）
        {
            "name": "bear", "device_id": 158,
            "token": "",
            "classes": {21: 0.40},   # bear
        },
        {
            "name": "person", "device_id": 157,
            "token": "",
            "classes": {0: 0.40},   # person
        },
        {
            # 鹿を直接検知できるCOCOクラスは存在しないため、比較的足の細い四足歩行動物の
            # クラスを「鹿候補」として扱う暫定対応（実証用）。誤検知しやすい dog は
            # 閾値を高めに設定。元のCOCOクラスは main.py 側でログに残す。
            "name": "deer", "device_id": 159,
            "token": "",
            "classes": {
                16: 0.50,  # dog
                17: 0.35,  # horse
                18: 0.40,  # sheep
                19: 0.40,  # cow
                22: 0.35,  # zebra
                23: 0.35,  # giraffe
            },
            "confirm": {"frames": 5, "window_sec": 2.0},
        },
    ],
}

_CUSTOM = {
    "model_path"   : "/root/models/custom.mud",   # カスタムモデルのパス
    "model_class"  : "YOLO11",    # YOLO11モデルなら "YOLO11"、YOLOv8なら "YOLOv8"
    "class_names"  : ["Kuma"],    # 学習時の data.yaml の names と同じ順序で
    "count_targets": [
        {"name": "Kuma", "device_id": 1, "token": "", "classes": {0: 0.40}},
    ],
}

_CONF         = _COCO if MODE == "coco" else _CUSTOM
MODEL_PATH    = _CONF["model_path"]
MODEL_CLASS   = _CONF["model_class"]
CLASS_NAMES   = _CONF["class_names"]
COUNT_TARGETS = _CONF["count_targets"]

##################################################
#   検知閾値
##################################################

# yolo.detect() に渡す全体しきい値（全ターゲットの個別しきい値の最小値）。
# ターゲットごとの個別しきい値によるフィルタは main.py 側で行う。
THRESHOLD: float = min(th for t in COUNT_TARGETS for th in t["classes"].values())

##################################################
#   HTTP 送信設定
##################################################

HOST    : str = "http://manacam.net/count"
INTERVAL: int = 60   # 送信間隔（秒）

##################################################
#   表示設定
##################################################

BOX_COLOR_RGB   = (0, 255, 0)   # 検知ボックスの色
LABEL_COLOR_RGB = (255, 255, 0) # ラベルテキストの色

##################################################
#   Google Drive アップロード設定
#
#   GAS スクリプトをデプロイして得た URL を設定する。
#   空文字列のままにするとアップロードはスキップされる。
#
#   フォルダ構成（GAS側で自動作成）:
#     <GDRIVE_ROOT_FOLDER>/
#       <label>/
#         <YYYY-MM-DD>/
#           <YYYYmmdd_HHMMSS>.jpg
##################################################

GAS_URL           : str = ""
GDRIVE_ROOT_FOLDER: str = "MaixCam2_Detections"

##################################################
#   画像送信の再送
#   送信に失敗したときだけ、同じ画像で 1 回だけ再送する
##################################################

GDRIVE_UPLOAD_RETRY_ENABLED: bool  = True
GDRIVE_RETRY_DELAY_SEC     : float = 3.0   # 再送までの待機（秒）

##################################################
#   省電力時間帯（日本時間）
#   "HHMMHHMM" = 開始時刻 + 通常動作の再開時刻。"00000000" で無効（24時間稼働）
#   例: "17500400" → 17:50〜翌04:00 は推論・カメラ・送信を止める
#   OTA で変更した値（/root/maixcam2_custom/config_override.json）がこの値より優先される
##################################################

POWER_SAVE_TIME: str = "00000000"

##################################################
#   OTA 設定更新（GitHub manifest）
#   空文字なら OTA しない。manifest の書き方は README.md を参照
##################################################

OTA_MANIFEST_BASE_URL: str = "https://raw.githubusercontent.com/futurocketco/maixcam2_manifest/main/manifest"
OTA_DEVICE_ID        : str = "157"   # manifest/devices/<ID>.json（person の device_id を使う）
OTA_CHECK_INTERVAL   : int = 3600    # 確認間隔（秒）

##################################################
#   ログ
##################################################

LOG_FILE: str = "./log"
