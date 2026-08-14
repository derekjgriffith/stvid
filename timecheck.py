#!/usr/bin/env python3
"""Report system time-synchronization state and independently sample NTP.

The terminal report is intended for an operator.  One compact JSON object is
appended to the configured log for each check, making the log JSON Lines
(JSONL), which is suitable for later trend analysis.
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
import platform
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


NTP_EPOCH_DELTA = 2_208_988_800
DEFAULT_SERVERS = ("pool.ntp.org",)


def run_command(command: list[str], timeout_s: float) -> dict[str, Any]:
    """Run an optional clock-service command without invoking a shell."""
    executable = shutil.which(command[0])
    if executable is None:
        return {"command": command, "available": False}
    try:
        result = subprocess.run(
            [executable, *command[1:]], capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        return {
            "command": command, "available": True,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(), "stderr": result.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "available": True, "error": str(exc)}


def service_diagnostics(timeout_s: float) -> list[dict[str, Any]]:
    """Collect diagnostics from installed time synchronization clients."""
    if os.name == "nt":
        commands = [
            ["w32tm", "/query", "/status", "/verbose"],
            ["w32tm", "/query", "/peers"],
            ["w32tm", "/query", "/configuration"],
        ]
    else:
        commands = [
            ["timedatectl", "show", "--property=NTPSynchronized",
             "--property=NTP", "--property=Timezone"],
            ["timedatectl", "timesync-status"],
            ["chronyc", "tracking", "-n"],
            ["chronyc", "sources", "-n"],
            ["ntpq", "-pn"],
        ]
    return [run_command(command, timeout_s) for command in commands]


def _ntp_seconds(data: bytes, offset: int) -> float:
    seconds, fraction = struct.unpack_from("!II", data, offset)
    return seconds - NTP_EPOCH_DELTA + fraction / 2**32


def query_ntp(server: str, timeout_s: float) -> dict[str, Any]:
    """Perform one RFC 5905-style SNTP exchange and estimate clock offset."""
    packet = bytearray(48)
    packet[0] = 0x23  # leap=0, version=4, client mode=3
    t1 = time.time()
    ntp_t1 = t1 + NTP_EPOCH_DELTA
    struct.pack_into("!II", packet, 40, int(ntp_t1),
                     int((ntp_t1 % 1) * 2**32))
    try:
        addresses = socket.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)
        family, socktype, proto, _, address = addresses[0]
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout_s)
            sock.sendto(packet, address)
            data, peer = sock.recvfrom(512)
        t4 = time.time()
        if len(data) < 48:
            raise ValueError(f"short NTP response ({len(data)} bytes)")
        leap = data[0] >> 6
        version = (data[0] >> 3) & 7
        mode = data[0] & 7
        stratum = data[1]
        t2 = _ntp_seconds(data, 32)
        t3 = _ntp_seconds(data, 40)
        offset_ms = ((t2 - t1) + (t3 - t4)) * 500.0
        delay_ms = ((t4 - t1) - (t3 - t2)) * 1000.0
        return {
            "server": server, "peer": str(peer[0]), "ok": True,
            "offset_ms": offset_ms, "delay_ms": delay_ms,
            "stratum": stratum, "leap": leap, "version": version,
            "mode": mode,
        }
    except (OSError, ValueError, IndexError) as exc:
        return {"server": server, "ok": False, "error": str(exc)}


def load_settings(path: Path) -> dict[str, Any]:
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if path.exists():
        cfg.read(path)
    # ConfigParser option names are case-insensitive, but section names are
    # not.  Resolve this section explicitly so both [Time] and [TIME] work.
    section = next(
        (name for name in cfg.sections() if name.casefold() == "time"),
        "Time",
    )
    raw_servers = cfg.get(section, "servers", fallback=",".join(DEFAULT_SERVERS))
    servers = tuple(s.strip() for s in raw_servers.split(",") if s.strip())
    return {
        "servers": servers or DEFAULT_SERVERS,
        "samples": cfg.getint(section, "samples", fallback=3),
        "timeout_s": cfg.getfloat(section, "timeout_s", fallback=2.0),
        "max_offset_ms": cfg.getfloat(section, "max_offset_ms", fallback=100.0),
        "critical_offset_ms": cfg.getfloat(
            section, "critical_offset_ms", fallback=1000.0),
        "max_delay_ms": cfg.getfloat(section, "max_delay_ms", fallback=500.0),
        "log_file": cfg.get(section, "log_file", fallback="timecheck.log"),
    }


def assess(samples: list[dict[str, Any]], settings: dict[str, Any]) -> tuple[str, list[str]]:
    valid = [sample for sample in samples if sample.get("ok")]
    reasons: list[str] = []
    if not valid:
        return "CRITICAL", ["no NTP server returned a usable sample"]
    offsets = [abs(float(sample["offset_ms"])) for sample in valid]
    delays = [float(sample["delay_ms"]) for sample in valid]
    median_offset = statistics.median(offsets)
    median_delay = statistics.median(delays)
    if median_offset > settings["critical_offset_ms"]:
        reasons.append("median absolute clock offset exceeds critical threshold")
        status = "CRITICAL"
    elif median_offset > settings["max_offset_ms"]:
        reasons.append("median absolute clock offset exceeds warning threshold")
        status = "WARNING"
    else:
        status = "OK"
    if median_delay > settings["max_delay_ms"]:
        reasons.append("median network delay exceeds threshold")
        if status == "OK":
            status = "WARNING"
    if len(valid) < len(samples):
        reasons.append(f"{len(samples) - len(valid)} NTP queries failed")
        if status == "OK":
            status = "WARNING"
    return status, reasons


def build_report(settings: dict[str, Any]) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    diagnostics = service_diagnostics(settings["timeout_s"])
    samples = [
        query_ntp(server, settings["timeout_s"])
        for server in settings["servers"]
        for _ in range(settings["samples"])
    ]
    status, reasons = assess(samples, settings)
    valid = [sample for sample in samples if sample.get("ok")]
    offsets = [float(sample["offset_ms"]) for sample in valid]
    delays = [float(sample["delay_ms"]) for sample in valid]
    summary = {
        "successful_samples": len(valid), "attempted_samples": len(samples),
        "median_offset_ms": statistics.median(offsets) if offsets else None,
        "max_absolute_offset_ms": max(map(abs, offsets)) if offsets else None,
        "offset_jitter_ms": statistics.pstdev(offsets) if len(offsets) > 1 else 0.0 if offsets else None,
        "median_delay_ms": statistics.median(delays) if delays else None,
    }
    return {
        "schema_version": 1, "timestamp_utc": started.isoformat(),
        "host": socket.gethostname(), "platform": platform.platform(),
        "status": status, "reasons": reasons, "thresholds": {
            key: settings[key] for key in
            ("max_offset_ms", "critical_offset_ms", "max_delay_ms")
        },
        "summary": summary, "ntp_samples": samples,
        "clock_services": diagnostics,
        "duration_s": time.time() - started.timestamp(),
    }


def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(f"STVID system clock report: {report['status']}")
    print(f"UTC check time : {report['timestamp_utc']}")
    print(f"Host           : {report['host']} ({report['platform']})")
    print(f"NTP samples    : {summary['successful_samples']}/{summary['attempted_samples']} successful")
    if summary["median_offset_ms"] is not None:
        print(f"Median offset  : {summary['median_offset_ms']:+.3f} ms")
        print(f"Maximum offset : {summary['max_absolute_offset_ms']:.3f} ms absolute")
        print(f"Offset jitter  : {summary['offset_jitter_ms']:.3f} ms")
        print(f"Median delay   : {summary['median_delay_ms']:.3f} ms")
    for reason in report["reasons"]:
        print(f"Attention      : {reason}")
    print("\nNTP servers:")
    for sample in report["ntp_samples"]:
        if sample.get("ok"):
            print(f"  {sample['server']} ({sample['peer']}): "
                  f"offset {sample['offset_ms']:+.3f} ms, "
                  f"delay {sample['delay_ms']:.3f} ms, stratum {sample['stratum']}")
        else:
            print(f"  {sample['server']}: FAILED ({sample['error']})")
    print("\nLocal synchronization services:")
    shown = False
    for diagnostic in report["clock_services"]:
        if diagnostic.get("available"):
            shown = True
            name = " ".join(diagnostic["command"])
            output = diagnostic.get("stdout") or diagnostic.get("stderr") or diagnostic.get("error", "no output")
            print(f"  [{name}]\n    " + output.replace("\n", "\n    "))
    if not shown:
        print("  No supported clock-service command was found.")


def append_log(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(report, separators=(",", ":"), sort_keys=True))
        stream.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="configuration.ini",
                        help="configuration file (default: configuration.ini)")
    parser.add_argument("--log-file", help="override [Time] log_file")
    parser.add_argument("--server", action="append", dest="servers",
                        help="override NTP servers; may be repeated")
    parser.add_argument("--samples", type=int, help="samples per NTP server")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(Path(args.config))
    if args.log_file:
        settings["log_file"] = args.log_file
    if args.servers:
        settings["servers"] = tuple(args.servers)
    if args.samples is not None:
        settings["samples"] = args.samples
    if settings["samples"] < 1 or settings["timeout_s"] <= 0:
        raise SystemExit("samples must be >= 1 and timeout_s must be > 0")
    report = build_report(settings)
    print_report(report)
    log_path = Path(settings["log_file"]).expanduser()
    append_log(report, log_path)
    print(f"\nMachine-readable report appended to {log_path}")
    return {"OK": 0, "WARNING": 1, "CRITICAL": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
