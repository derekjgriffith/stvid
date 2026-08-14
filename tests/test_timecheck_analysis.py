import json

from tools import timecheck_analysis


def test_load_records_skips_bad_lines_and_sorts(tmp_path):
    path = tmp_path / "timecheck.log"
    records = [
        {"timestamp_utc": "2026-01-02T00:00:00+00:00", "summary": {}},
        {"timestamp_utc": "2026-01-01T00:00:00+00:00", "summary": {}},
    ]
    path.write_text(
        json.dumps(records[0]) + "\nnot JSON\n" + json.dumps(records[1]) + "\n",
        encoding="utf-8",
    )
    loaded, warnings = timecheck_analysis.load_records(path)
    assert [record["timestamp_utc"] for record in loaded] == [
        "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
    ]
    assert len(warnings) == 1


def test_select_records_by_host_and_recent_days():
    records = [
        {"host": "pi", "_timestamp": timecheck_analysis.parse_timestamp("2026-01-01T00:00:00Z")},
        {"host": "pi", "_timestamp": timecheck_analysis.parse_timestamp("2026-01-10T00:00:00Z")},
        {"host": "win", "_timestamp": timecheck_analysis.parse_timestamp("2026-01-10T00:00:00Z")},
    ]
    selected = timecheck_analysis.select_records(records, "pi", 2)
    assert len(selected) == 1
    assert selected[0]["host"] == "pi"
