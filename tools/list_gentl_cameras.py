#!/usr/bin/env python3
"""Enumerate cameras exposed by the GenTL producer in configuration.ini."""

from __future__ import annotations

import argparse
import configparser
import json
import sys
from pathlib import Path
from typing import Any, Callable


DEVICE_ATTRIBUTES = (
    ("display_name", "Display name"),
    ("vendor", "Vendor"),
    ("model", "Model"),
    ("serial_number", "Serial number"),
    ("id_", "Device ID"),
    ("user_defined_name", "User name"),
    ("tl_type", "Transport"),
    ("version", "Version"),
)


def find_section(cfg: configparser.ConfigParser, requested: str) -> str:
    """Find a configuration section without requiring exact capitalization."""
    return next(
        (name for name in cfg.sections() if name.casefold() == requested.casefold()),
        requested,
    )


def read_configuration(path: Path) -> dict[str, Any]:
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    loaded = cfg.read(path)
    if not loaded:
        raise FileNotFoundError(f"Could not read configuration file: {path}")
    section = find_section(cfg, "GENTL")
    if not cfg.has_section(section):
        raise ValueError(f"Configuration file has no [GENTL] section: {path}")
    if not cfg.has_option(section, "cti_file"):
        raise ValueError(f"Configuration section [{section}] has no cti_file")
    cti_file = Path(cfg.get(section, "cti_file")).expanduser()
    serial = cfg.get(section, "serial_number", fallback="").strip() or None
    return {
        "config_file": path,
        "section": section,
        "cti_file": cti_file,
        "device_index": cfg.getint(section, "device_id", fallback=0),
        "serial_number": serial,
    }


def optional_attribute(obj: Any, name: str) -> str | None:
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def describe_device(info: Any, index: int) -> dict[str, Any]:
    device: dict[str, Any] = {"index": index}
    for attribute, _ in DEVICE_ATTRIBUTES:
        device[attribute] = optional_attribute(info, attribute)
    return device


def enumerate_devices(
    cti_file: Path,
    harvester_factory: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """Load one CTI producer and return its discovered device descriptors."""
    if not cti_file.is_file():
        raise FileNotFoundError(f"GenTL producer not found: {cti_file}")
    if harvester_factory is None:
        try:
            from harvesters.core import Harvester
        except ImportError as exc:
            raise RuntimeError(
                "Harvesters is not installed; install STVID with the gentl extra"
            ) from exc
        harvester_factory = Harvester

    harvester = harvester_factory()
    try:
        harvester.add_file(str(cti_file))
        harvester.update()
        return [
            describe_device(info, index)
            for index, info in enumerate(harvester.device_info_list)
        ]
    except Exception as exc:
        raise RuntimeError(
            f"Could not enumerate devices with producer {cti_file}: {exc}"
        ) from exc
    finally:
        try:
            harvester.reset()
        except Exception:
            # Discovery is complete (or has already failed), and no camera was
            # opened. Do not hide the useful result/error with a cleanup error.
            pass


def selected_index(devices: list[dict[str, Any]], settings: dict[str, Any]) -> int | None:
    serial = settings["serial_number"]
    if serial is not None:
        return next(
            (device["index"] for device in devices if device["serial_number"] == serial),
            None,
        )
    index = settings["device_index"]
    return index if 0 <= index < len(devices) else None


def print_devices(devices: list[dict[str, Any]], settings: dict[str, Any]) -> None:
    chosen = selected_index(devices, settings)
    print(f"Configuration : {settings['config_file']}")
    print(f"CTI producer  : {settings['cti_file']}")
    print(f"Devices found : {len(devices)}")
    if settings["serial_number"] is not None:
        print(f"Configured    : serial_number = {settings['serial_number']}")
    else:
        print(f"Configured    : device_id = {settings['device_index']}")

    for device in devices:
        marker = "*" if device["index"] == chosen else " "
        print(f"\n{marker} Camera {device['index']}")
        for attribute, label in DEVICE_ATTRIBUTES:
            value = device.get(attribute)
            if value is not None:
                print(f"    {label:<14}: {value}")

    if devices and chosen is None:
        print("\nWARNING: the camera selected in [GENTL] was not discovered.", file=sys.stderr)
    elif chosen is not None:
        print("\n* Camera selected by the current [GENTL] configuration")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c", "--config", default="configuration.ini",
        help="configuration file (default: configuration.ini)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="write machine-readable JSON instead of the formatted report",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = read_configuration(Path(args.config))
        devices = enumerate_devices(settings["cti_file"])
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"GenTL discovery failed: {exc}", file=sys.stderr)
        return 2

    chosen = selected_index(devices, settings)
    if args.json:
        output = {
            "configuration": str(settings["config_file"]),
            "cti_file": str(settings["cti_file"]),
            "configured_device_index": settings["device_index"],
            "configured_serial_number": settings["serial_number"],
            "selected_index": chosen,
            "device_count": len(devices),
            "devices": devices,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print_devices(devices, settings)

    if not devices:
        print("No cameras were discovered.", file=sys.stderr)
        return 1
    return 0 if chosen is not None else 1


if __name__ == "__main__":
    sys.exit(main())
