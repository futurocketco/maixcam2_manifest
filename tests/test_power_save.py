import datetime

import pytest

import power_save as ps

JST = datetime.timezone(datetime.timedelta(hours=9))


def at(h, m, day=3):
    return datetime.datetime(2026, 11, day, h, m, tzinfo=JST)


def test_parse():
    assert ps.parse("17500400") == ((17, 50), (4, 0))
    assert ps.parse("00000000") is None


@pytest.mark.parametrize("bad", ["", "1750040", "175004000", "2400060a", "24000600",
                                 "17600400", "12001200", 17500400, None, " 7500400"])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        ps.parse(bad)
    assert not ps.in_window(bad, at(18, 0))   # 不正な値では止めない


@pytest.mark.parametrize("h,m,expected", [
    (17, 49, False), (17, 50, True), (23, 59, True), (0, 0, True),
    (3, 59, True), (4, 0, False), (12, 0, False),
])
def test_overnight_17500400(h, m, expected):
    assert ps.in_window("17500400", at(h, m)) is expected


@pytest.mark.parametrize("h,m,expected", [
    (11, 59, False), (12, 0, True), (12, 59, True), (13, 0, False), (0, 0, False),
])
def test_daytime_12001300(h, m, expected):
    assert ps.in_window("12001300", at(h, m)) is expected


def test_23000600_crosses_midnight_and_month():
    assert ps.in_window("23000600", datetime.datetime(2026, 11, 30, 23, 30, tzinfo=JST))
    assert ps.in_window("23000600", datetime.datetime(2026, 12, 1, 5, 59, tzinfo=JST))
    assert not ps.in_window("23000600", datetime.datetime(2026, 12, 1, 6, 0, tzinfo=JST))


def test_disabled_is_always_on():
    for h in range(24):
        assert not ps.in_window("00000000", at(h, 0))


def test_invalid_clock_keeps_normal_mode():
    epoch = datetime.datetime(1970, 1, 1, 18, 0, tzinfo=JST)
    assert not ps.time_is_valid(epoch)
    assert not ps.in_window("17500400", epoch)


def test_starts_within():
    assert ps.starts_within("17500400", at(17, 49, ) .replace(second=58), 4)
    assert not ps.starts_within("17500400", at(17, 40), 4)
    assert not ps.starts_within("17500400", at(18, 0), 4)   # 既に時間帯の中


def test_cpu_control_writes(tmp_path, monkeypatch):
    cpu = tmp_path / "cpu"
    (cpu / "cpu0" / "cpufreq").mkdir(parents=True)
    (cpu / "cpu1").mkdir()
    monkeypatch.setattr(ps, "_CPU_DIR", str(cpu))
    monkeypatch.setattr(ps, "_CPU0_FREQ", str(cpu / "cpu0" / "cpufreq"))
    logs = []
    ps.cpu_low(logs.append)
    assert (cpu / "cpu1" / "online").read_text() == "0"
    assert (cpu / "cpu0" / "cpufreq" / "scaling_setspeed").read_text() == "208000"
    ps.cpu_normal(logs.append)
    assert (cpu / "cpu1" / "online").read_text() == "1"
    assert (cpu / "cpu0" / "cpufreq" / "scaling_setspeed").read_text() == "1200000"
    assert logs == []


def test_cpu_low_keep_reapplies_when_reset(tmp_path, monkeypatch):
    cpu = tmp_path / "cpu"
    (cpu / "cpu0" / "cpufreq").mkdir(parents=True)
    (cpu / "cpu1").mkdir()
    monkeypatch.setattr(ps, "_CPU_DIR", str(cpu))
    monkeypatch.setattr(ps, "_CPU0_FREQ", str(cpu / "cpu0" / "cpufreq"))
    ps.cpu_low(print)
    assert ps.cpu_low_keep(print) is False                 # そのままなら何もしない
    (cpu / "cpu0" / "cpufreq" / "scaling_setspeed").write_text("1200000")   # 誰かが戻した
    assert ps.cpu_low_keep(print) is True
    assert (cpu / "cpu0" / "cpufreq" / "scaling_setspeed").read_text() == "208000"
