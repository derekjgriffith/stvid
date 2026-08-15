#!/usr/bin/env python3
"""Patch observer metadata in captured FITS files from an STVID config file."""

from __future__ import annotations

import argparse
import configparser
import sys
from dataclasses import dataclass
from pathlib import Path

from astropy.io import fits


@dataclass(frozen=True)
class Settings:
    observations_path: Path
    cospar: int
    observer: str
    latitude: float
    longitude: float
    elevation: float


def read_settings(config_path: Path) -> Settings:
    """Read and validate the paths and observer values used by the patcher."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not cfg.read(config_path):
        raise FileNotFoundError(f"Could not read configuration file: {config_path}")

    for section in ("Observer", "Setup"):
        if not cfg.has_section(section):
            raise ValueError(f"Configuration file has no [{section}] section")

    observations_value = cfg.get("Setup", "observations_path").strip()
    if not observations_value:
        raise ValueError("[Setup] observations_path is empty")

    observations_path = Path(observations_value).expanduser()
    if not observations_path.is_absolute():
        observations_path = config_path.resolve().parent / observations_path

    observer = cfg.get("Observer", "name").strip()
    if not observer:
        raise ValueError("[Observer] name is empty")

    return Settings(
        observations_path=observations_path.resolve(),
        cospar=cfg.getint("Observer", "cospar"),
        observer=observer,
        latitude=cfg.getfloat("Observer", "latitude"),
        longitude=cfg.getfloat("Observer", "longitude"),
        elevation=cfg.getfloat("Observer", "height"),
    )


def scan_root(observations_path: Path, sub_folder: str | None) -> Path:
    """Resolve the requested scan root and keep it inside observations_path."""
    if sub_folder is None:
        root = observations_path
    else:
        candidate = Path(sub_folder)
        if candidate.is_absolute():
            raise ValueError("-p/--path must be relative to observations_path")
        root = (observations_path / candidate).resolve()
        try:
            root.relative_to(observations_path)
        except ValueError as exc:
            raise ValueError(
                "-p/--path must identify a folder inside observations_path"
            ) from exc

    if not root.is_dir():
        raise FileNotFoundError(f"Observation folder does not exist: {root}")
    return root


def fits_files(root: Path) -> list[Path]:
    """Return all FITS files below root in deterministic order."""
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".fits", ".fit", ".fts"}
    )


def patch_file(path: Path, settings: Settings) -> None:
    """Update the primary HDU's observer header cards in place."""
    with fits.open(path, mode="update") as hdul:
        header = hdul[0].header
        header["COSPAR"] = settings.cospar
        header["OBSERVER"] = settings.observer
        header["SITELAT"] = settings.latitude
        header["SITELONG"] = settings.longitude
        header["ELEVATIO"] = settings.elevation
        hdul.flush()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c", "--config", default="configuration.ini",
        help="configuration file (default: configuration.ini)",
    )
    parser.add_argument(
        "-p", "--path", metavar="SUB_FOLDER",
        help="only patch this sub-folder below observations_path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = read_settings(Path(args.config))
        root = scan_root(settings.observations_path, args.path)
    except (configparser.Error, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    files = fits_files(root)
    if not files:
        print(f"No FITS files found under {root}")
        return 0

    patched = 0
    failed = 0
    for path in files:
        try:
            patch_file(path, settings)
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"Failed:  {path}: {exc}", file=sys.stderr)
        else:
            patched += 1
            print(f"Patched: {path}")

    print(f"Completed: {patched} patched, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
