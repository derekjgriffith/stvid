#!/usr/bin/env python3
"""Plot trends from the JSON Lines log produced by timecheck.py."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


STATUS_COLOURS = {"OK": "tab:green", "WARNING": "tab:orange", "CRITICAL": "tab:red"}


def parse_timestamp(value: str) -> dt.datetime:
    timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp.astimezone(dt.timezone.utc)


def load_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read valid timecheck records, returning non-fatal input warnings."""
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                record["_timestamp"] = parse_timestamp(record["timestamp_utc"])
                if not isinstance(record.get("summary"), dict):
                    raise ValueError("missing summary object")
                records.append(record)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                warnings.append(f"line {line_number}: {exc}")
    records.sort(key=lambda item: item["_timestamp"])
    return records, warnings


def select_records(
    records: Iterable[dict[str, Any]], host: str | None, days: float | None,
) -> list[dict[str, Any]]:
    selected = [record for record in records if host is None or record.get("host") == host]
    if days is not None and selected:
        cutoff = selected[-1]["_timestamp"] - dt.timedelta(days=days)
        selected = [record for record in selected if record["_timestamp"] >= cutoff]
    return selected


def numeric(records: Iterable[dict[str, Any]], key: str) -> tuple[list[dt.datetime], list[float]]:
    times: list[dt.datetime] = []
    values: list[float] = []
    for record in records:
        value = record["summary"].get(key)
        if isinstance(value, (int, float)):
            times.append(record["_timestamp"])
            values.append(float(value))
    return times, values


def configure_matplotlib(show: bool) -> Any:
    import matplotlib

    if not show or (os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")):
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def format_time_axis(axis: Any) -> None:
    import matplotlib.dates as mdates

    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axis.grid(True, alpha=0.3)


def plot_offsets(records: list[dict[str, Any]], output: Path, plt: Any) -> None:
    fig, axis = plt.subplots(figsize=(11, 5.5), constrained_layout=True)
    times, offsets = numeric(records, "median_offset_ms")
    axis.plot(times, offsets, marker=".", linewidth=1, label="Median signed offset")
    times_abs, maxima = numeric(records, "max_absolute_offset_ms")
    axis.plot(times_abs, maxima, marker=".", linewidth=1, alpha=0.7,
              label="Maximum absolute offset")

    warning_values = [record.get("thresholds", {}).get("max_offset_ms") for record in records]
    critical_values = [record.get("thresholds", {}).get("critical_offset_ms") for record in records]
    warning = next((float(value) for value in reversed(warning_values) if isinstance(value, (int, float))), None)
    critical = next((float(value) for value in reversed(critical_values) if isinstance(value, (int, float))), None)
    if warning is not None:
        axis.axhline(warning, color="tab:orange", linestyle="--", alpha=0.7, label="Warning threshold")
        axis.axhline(-warning, color="tab:orange", linestyle="--", alpha=0.7)
    if critical is not None:
        axis.axhline(critical, color="tab:red", linestyle=":", alpha=0.7, label="Critical threshold")
        axis.axhline(-critical, color="tab:red", linestyle=":", alpha=0.7)
    axis.axhline(0, color="black", linewidth=0.7)
    axis.set(title="System clock offset", ylabel="Offset (ms)")
    axis.legend(loc="best")
    format_time_axis(axis)
    fig.savefig(output, dpi=160)


def plot_quality(records: list[dict[str, Any]], output: Path, plt: Any) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, constrained_layout=True)
    times, delays = numeric(records, "median_delay_ms")
    axes[0].plot(times, delays, marker=".", linewidth=1, label="Median network delay")
    times, jitter = numeric(records, "offset_jitter_ms")
    axes[0].plot(times, jitter, marker=".", linewidth=1, label="Offset jitter")
    axes[0].set(title="NTP measurement quality", ylabel="Milliseconds")
    axes[0].legend(loc="best")
    axes[0].grid(True, alpha=0.3)

    success_times: list[dt.datetime] = []
    success_percent: list[float] = []
    for record in records:
        summary = record["summary"]
        attempted = summary.get("attempted_samples")
        successful = summary.get("successful_samples")
        if isinstance(attempted, (int, float)) and attempted > 0 and isinstance(successful, (int, float)):
            success_times.append(record["_timestamp"])
            success_percent.append(100.0 * successful / attempted)
    axes[1].plot(success_times, success_percent, marker=".", linewidth=1, color="tab:green")
    axes[1].set(ylabel="Successful samples (%)", xlabel="UTC", ylim=(-5, 105))
    format_time_axis(axes[1])
    fig.savefig(output, dpi=160)


def plot_status(records: list[dict[str, Any]], output: Path, plt: Any) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, constrained_layout=True)
    levels = {"OK": 0, "WARNING": 1, "CRITICAL": 2}
    for status, level in levels.items():
        times = [r["_timestamp"] for r in records if r.get("status") == status]
        axes[0].scatter(times, [level] * len(times), color=STATUS_COLOURS[status], s=22, label=status)
    axes[0].set(title="Time-check status and runtime", yticks=list(levels.values()),
                yticklabels=list(levels), ylim=(-0.5, 2.5))
    axes[0].legend(loc="best", ncol=3)
    axes[0].grid(True, alpha=0.3)
    duration_times: list[dt.datetime] = []
    durations: list[float] = []
    for record in records:
        value = record.get("duration_s")
        if isinstance(value, (int, float)):
            duration_times.append(record["_timestamp"])
            durations.append(float(value))
    axes[1].plot(duration_times, durations, marker=".", linewidth=1, color="tab:purple")
    axes[1].set(ylabel="Check duration (s)", xlabel="UTC")
    format_time_axis(axes[1])
    fig.savefig(output, dpi=160)


def print_summary(records: list[dict[str, Any]], warnings: list[str], output_dir: Path) -> None:
    counts = Counter(str(record.get("status", "UNKNOWN")) for record in records)
    hosts = sorted({str(record.get("host", "unknown")) for record in records})
    print(f"Read {len(records)} records for {', '.join(hosts)}")
    print(f"Period: {records[0]['_timestamp'].isoformat()} to {records[-1]['_timestamp'].isoformat()}")
    print("Status: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    print(f"Plots written to {output_dir}")
    if warnings:
        print(f"Skipped {len(warnings)} malformed log line(s):", file=sys.stderr)
        for warning in warnings[:10]:
            print(f"  {warning}", file=sys.stderr)
        if len(warnings) > 10:
            print(f"  ... and {len(warnings) - 10} more", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", nargs="?", default="timecheck.log",
                        help="input JSONL log (default: timecheck.log)")
    parser.add_argument("-o", "--output-dir", default="graphics/timecheck",
                        help="plot directory (default: graphics/timecheck)")
    parser.add_argument("--host", help="include only records from this host")
    parser.add_argument("--days", type=float, help="include only the latest DAYS")
    parser.add_argument("--show", action="store_true",
                        help="also display plots when a graphical backend is available")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.days is not None and args.days <= 0:
        raise SystemExit("--days must be greater than zero")
    log_path = Path(args.log_file)
    try:
        records, warnings = load_records(log_path)
    except OSError as exc:
        print(f"Could not read {log_path}: {exc}", file=sys.stderr)
        return 2
    records = select_records(records, args.host, args.days)
    if not records:
        print("No matching valid timecheck records were found.", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plt = configure_matplotlib(args.show)
    plot_offsets(records, output_dir / "clock-offset.png", plt)
    plot_quality(records, output_dir / "ntp-quality.png", plt)
    plot_status(records, output_dir / "check-status.png", plt)
    print_summary(records, warnings, output_dir)
    if args.show:
        try:
            plt.show()
        except Exception as exc:
            print(f"Plots were saved, but could not be displayed: {exc}", file=sys.stderr)
    else:
        plt.close("all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
