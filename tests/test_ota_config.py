import json
import types

import ota_config as oc


class Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self.ok = status < 400
        self._data = data

    def json(self):
        return self._data


def make_config(**kw):
    c = types.SimpleNamespace(POWER_SAVE_TIME="00000000", GDRIVE_UPLOAD_RETRY_ENABLED=True,
                              GDRIVE_RETRY_DELAY_SEC=3.0, OTA_CHECK_INTERVAL=3600,
                              OTA_MANIFEST_BASE_URL="https://raw.example/manifest",
                              OTA_DEVICE_ID="157")
    c.__dict__.update(kw)
    return c


def checker(cfg, path, pages, logs):
    def get(url, timeout, headers):
        return pages.get(url, Resp(404))
    return oc.OTAConfigChecker(cfg, logs.append, path=str(path), get=get)


BASE = "https://raw.example/manifest"


def test_device_manifest_applies_and_persists(tmp_path):
    cfg, logs = make_config(), []
    path = tmp_path / "override.json"
    pages = {f"{BASE}/default.json": Resp(200, {"config_override": {"power_save_time": "00000000"}}),
             f"{BASE}/devices/157.json": Resp(200, {"config_override": {"power_save_time": "17500400"}})}
    changed = checker(cfg, path, pages, logs).check_once()
    assert changed == {"power_save_time": "17500400"}
    assert cfg.POWER_SAVE_TIME == "17500400"
    assert json.loads(path.read_text()) == {"power_save_time": "17500400"}
    assert any("OTA setting changed: power_save_time" in l for l in logs)

    # 「再起動」: 新しい config に保存値を読み込む
    cfg2 = make_config()
    oc.apply_saved(cfg2, logs.append, path=str(path))
    assert cfg2.POWER_SAVE_TIME == "17500400"

    # 同じ内容なら何も変わらない
    assert checker(cfg, path, pages, logs).check_once() == {}


def test_invalid_value_rejected_keeps_previous(tmp_path):
    cfg, logs = make_config(), []
    path = tmp_path / "override.json"
    oc.save({"power_save_time": "23000600"}, str(path))
    oc.apply_saved(cfg, logs.append, path=str(path))
    for bad in ["25000600", 17500400, "1750", "12001200"]:
        pages = {f"{BASE}/devices/157.json": Resp(200, {"config_override": {"power_save_time": bad}})}
        assert checker(cfg, path, pages, logs).check_once() == {}
        assert cfg.POWER_SAVE_TIME == "23000600"
    assert json.loads(path.read_text()) == {"power_save_time": "23000600"}
    assert any("OTA rejected" in l for l in logs)


def test_secret_keys_ignored(tmp_path):
    cfg, logs = make_config(GAS_URL="orig"), []
    pages = {f"{BASE}/devices/157.json": Resp(200, {"config_override": {"GAS_URL": "evil", "HOST": "x"}})}
    assert checker(cfg, tmp_path / "o.json", pages, logs).check_once() == {}
    assert cfg.GAS_URL == "orig"


def test_upper_case_keys_and_other_settings(tmp_path):
    cfg, logs = make_config(), []
    pages = {f"{BASE}/default.json": Resp(200, {"config_override": {
        "POWER_SAVE_TIME": "12001300", "gdrive_retry_delay_sec": 5,
        "gdrive_upload_retry_enabled": False, "ota_check_interval": 600}})}
    checker(cfg, tmp_path / "o.json", pages, logs).check_once()
    assert (cfg.POWER_SAVE_TIME, cfg.GDRIVE_RETRY_DELAY_SEC,
            cfg.GDRIVE_UPLOAD_RETRY_ENABLED, cfg.OTA_CHECK_INTERVAL) == ("12001300", 5.0, False, 600)


def test_network_failure_changes_nothing(tmp_path):
    cfg, logs = make_config(POWER_SAVE_TIME="17500400"), []

    def get(url, timeout, headers):
        raise OSError("network down")
    c = oc.OTAConfigChecker(cfg, logs.append, path=str(tmp_path / "o.json"), get=get)
    assert c.check_once() == {}
    assert cfg.POWER_SAVE_TIME == "17500400"


def test_corrupt_saved_file_is_ignored(tmp_path):
    path = tmp_path / "o.json"
    path.write_text("{broken")
    cfg = make_config()
    oc.apply_saved(cfg, lambda m: None, path=str(path))
    assert cfg.POWER_SAVE_TIME == "00000000"


def test_disabled_without_url():
    logs = []
    oc.OTAConfigChecker(make_config(OTA_MANIFEST_BASE_URL=""), logs.append).start()
    assert any("OTA disabled" in l for l in logs)


def test_all_404_is_logged(tmp_path):
    cfg, logs = make_config(), []
    assert checker(cfg, tmp_path / "o.json", {}, logs).check_once() == {}
    assert any("could not fetch any manifest" in l for l in logs)
