#!/usr/bin/env python3
"""List observer metadata for FITS files in an STVID observations tree."""

from __future__ import annotations

import argparse
import configparser
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from astropy.io import fits


OBSERVER_KEYS = ("COSPAR", "OBSERVER", "SITELAT", "SITELONG", "ELEVATIO")
BASE_COLUMNS = ("file_name", "sub_folder", "relative_path")


def observations_path(config_path: Path) -> Path:
    """Return observations_path from the requested STVID configuration."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not cfg.read(config_path):
        raise FileNotFoundError(f"Could not read configuration file: {config_path}")
    if not cfg.has_section("Setup"):
        raise ValueError("Configuration file has no [Setup] section")

    value = cfg.get("Setup", "observations_path").strip()
    if not value:
        raise ValueError("[Setup] observations_path is empty")

    root = Path(value).expanduser()
    if not root.is_absolute():
        root = config_path.resolve().parent / root
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Observations folder does not exist: {root}")
    return root


def find_fits_files(root: Path) -> list[Path]:
    """Find FITS files recursively in deterministic path order."""
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".fits", ".fit", ".fts"}
    )


def header_values(path: Path) -> dict[str, Any]:
    """Read every primary-header card, retaining repeated cards in one value."""
    values: dict[str, list[Any]] = defaultdict(list)
    with fits.open(path, memmap=True) as hdul:
        for card in hdul[0].header.cards:
            keyword = card.keyword.strip()
            if keyword:
                values[keyword].append(card.value)

    result: dict[str, Any] = {}
    for keyword, items in values.items():
        result[keyword] = items[0] if len(items) == 1 else " | ".join(
            str(item) for item in items
        )
    return result


def make_record(path: Path, root: Path, header: dict[str, Any]) -> dict[str, Any]:
    relative = path.relative_to(root)
    parent = relative.parent
    return {
        "file_name": path.name,
        "sub_folder": "" if parent == Path(".") else str(parent),
        "relative_path": str(relative),
        **header,
    }


def display_value(value: Any) -> str:
    return "" if value is None else str(value)


def print_records(records: list[dict[str, Any]]) -> None:
    columns = (*BASE_COLUMNS[:2], *OBSERVER_KEYS)
    rows = [[display_value(record.get(column, "")) for column in columns]
            for record in records]
    widths = [len(column) for column in columns]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]

    print("  ".join(column.ljust(width) for column, width in zip(columns, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))


def output_columns(records: list[dict[str, Any]]) -> list[str]:
    discovered = {key for record in records for key in record}
    leading = [*BASE_COLUMNS, *OBSERVER_KEYS]
    return leading + sorted(discovered.difference(leading))


def write_csv(
    destination: Path, records: list[dict[str, Any]], columns: list[str]
) -> None:
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_xlsx(
    destination: Path, records: list[dict[str, Any]], columns: list[str]
) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError(
            "Writing .xlsx files requires openpyxl; install it or choose .csv"
        ) from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FITS headers"
    sheet.append(columns)
    for record in records:
        sheet.append([display_value(record.get(column, "")) for column in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    workbook.save(destination)


def export_records(output: str, root: Path, records: list[dict[str, Any]]) -> Path:
    requested = Path(output)
    if requested.name != output:
        raise ValueError("-o/--output must be a file name, not a path")
    destination = root / requested.name
    columns = output_columns(records)
    suffix = destination.suffix.casefold()
    if suffix == ".csv":
        write_csv(destination, records, columns)
    elif suffix == ".xlsx":
        write_xlsx(destination, records, columns)
    else:
        raise ValueError("output file must end in .csv or .xlsx")
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c", "--config", default="configuration.ini",
        help="configuration file (default: configuration.ini)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="export all primary-header tags to FILE.csv or FILE.xlsx",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = observations_path(Path(args.config))
    except (configparser.Error, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    failures = 0
    for path in find_fits_files(root):
        try:
            records.append(make_record(path, root, header_values(path)))
        except (OSError, ValueError, IndexError) as exc:
            failures += 1
            print(f"Failed: {path}: {exc}", file=sys.stderr)

    if records:
        print_records(records)
    else:
        print(f"No readable FITS files found under {root}")

    if args.output:
        try:
            destination = export_records(args.output, root, records)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(f"\nWrote {destination}")

    print(f"\nFound {len(records)} FITS file(s); {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
