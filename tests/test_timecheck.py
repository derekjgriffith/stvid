import json
import struct

import timecheck


def test_load_settings_accepts_time_section_capitalization(tmp_path):
    for heading in ("Time", "TIME"):
        path = tmp_path / f"{heading}.ini"
        path.write_text(
            f"[{heading}]\nservers = ntp.example.test\nsamples = 7\n",
            encoding="utf-8",
        )
        settings = timecheck.load_settings(path)
        assert settings["servers"] == ("ntp.example.test",)
        assert settings["samples"] == 7


def test_assess_thresholds():
    settings = {"max_offset_ms": 100, "critical_offset_ms": 1000,
                "max_delay_ms": 500}
    samples = [{"ok": True, "offset_ms": 10.0, "delay_ms": 20.0}]
    assert timecheck.assess(samples, settings) == ("OK", [])
    samples[0]["offset_ms"] = 200.0
    assert timecheck.assess(samples, settings)[0] == "WARNING"
    samples[0]["offset_ms"] = 2000.0
    assert timecheck.assess(samples, settings)[0] == "CRITICAL"


def test_no_valid_samples_is_critical():
    status, reasons = timecheck.assess(
        [{"ok": False}], {"max_offset_ms": 100,
                          "critical_offset_ms": 1000, "max_delay_ms": 500})
    assert status == "CRITICAL"
    assert reasons


def test_append_log_is_json_lines(tmp_path):
    path = tmp_path / "timecheck.log"
    timecheck.append_log({"status": "OK", "value": 1}, path)
    timecheck.append_log({"status": "WARNING", "value": 2}, path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["value"] for record in records] == [1, 2]


def test_ntp_timestamp_conversion():
    unix_time = 1_700_000_000.25
    ntp_time = unix_time + timecheck.NTP_EPOCH_DELTA
    data = bytearray(48)
    struct.pack_into("!II", data, 32, int(ntp_time), int((ntp_time % 1) * 2**32))
    assert timecheck._ntp_seconds(data, 32) == unix_time
