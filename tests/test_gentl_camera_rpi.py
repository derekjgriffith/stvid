#!/usr/bin/env python3
"""Raspberry Pi smoke test for the STVID GenTL camera backend.

Run from the STVID repository root:

    python tests/test_gentl_camera_rpi.py
    python tests/test_gentl_camera_rpi.py -c configuration.ini
    python tests/test_gentl_camera_rpi.py -c configuration.ini --frames 100
    python tests/test_gentl_camera_rpi.py -c configuration.ini --save first_frame.npy

This is intentionally a hardware integration test rather than a pytest unit
test. It checks discovery, configuration, acquisition, frame integrity,
timing diagnostics, and whether image data remains valid after the GenTL
buffer has been returned.
"""

from __future__ import annotations

import argparse
import configparser
import sys
from pathlib import Path
from statistics import mean, pstdev
from time import perf_counter

import numpy as np

from stvid.camera.gentl import DEFAULT_CTI, GenTLCamera
from stvid.camera.models import CameraConfig


SECTION = "GENTL"


def optional_text(
    cfg: configparser.ConfigParser,
    section: str,
    option: str,
) -> str | None:
    value = cfg.get(section, option, fallback="").strip()
    return value or None


def optional_int(
    cfg: configparser.ConfigParser,
    section: str,
    option: str,
) -> int | None:
    text = optional_text(cfg, section, option)
    return None if text is None else int(text)


def optional_float(
    cfg: configparser.ConfigParser,
    section: str,
    option: str,
) -> float | None:
    text = optional_text(cfg, section, option)
    return None if text is None else float(text)


def read_configuration(path: Path) -> tuple[GenTLCamera, CameraConfig, int, float]:
    cfg = configparser.ConfigParser(
        inline_comment_prefixes=("#", ";")
    )

    loaded = cfg.read(path)
    if not loaded:
        raise FileNotFoundError(f"Could not read configuration file: {path}")

    if not cfg.has_section(SECTION):
        raise ValueError(
            f"Configuration file has no [{SECTION}] section."
        )

    cti_file = Path(
        cfg.get(SECTION, "cti_file", fallback=str(DEFAULT_CTI))
    ).expanduser()

    device_index = cfg.getint(SECTION, "device_id", fallback=0)
    serial_number = optional_text(cfg, SECTION, "serial_number")

    # Prefer STVID's existing nx/ny naming. Width/height are accepted as
    # aliases to make the generic camera meaning clearer.
    width = optional_int(cfg, SECTION, "nx")
    if width is None:
        width = optional_int(cfg, SECTION, "width")

    height = optional_int(cfg, SECTION, "ny")
    if height is None:
        height = optional_int(cfg, SECTION, "height")

    config = CameraConfig(
        exposure_us=optional_float(cfg, SECTION, "exposure_us"),
        frame_rate_hz=optional_float(cfg, SECTION, "framerate"),
        gain=optional_float(cfg, SECTION, "gain"),
        pixel_format=optional_text(cfg, SECTION, "pixel_format"),
        width=width,
        height=height,
        offset_x=optional_int(cfg, SECTION, "offset_x"),
        offset_y=optional_int(cfg, SECTION, "offset_y"),
        binning_horizontal=optional_int(
            cfg, SECTION, "binning_horizontal"
        ),
        binning_vertical=optional_int(
            cfg, SECTION, "binning_vertical"
        ),
        acquisition_mode=optional_text(
            cfg, SECTION, "acquisition_mode"
        ),
        trigger_mode=optional_text(cfg, SECTION, "trigger_mode"),
        trigger_source=optional_text(
            cfg, SECTION, "trigger_source"
        ),
    )

    frame_count = cfg.getint(SECTION, "test_frames", fallback=20)
    timeout_s = cfg.getfloat(SECTION, "timeout_s", fallback=2.0)

    camera = GenTLCamera(
        cti_file=cti_file,
        device_index=device_index,
        serial_number=serial_number,
    )

    return camera, config, frame_count, timeout_s


def describe_dataclass(label: str, value: object) -> None:
    print(f"\n{label}")
    print("-" * len(label))

    for name in value.__dataclass_fields__:  # type: ignore[attr-defined]
        print(f"{name:34s}: {getattr(value, name)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test the STVID GenTL camera backend on Raspberry Pi."
    )
    parser.add_argument(
        "-c",
        "--conf-file",
        type=Path,
        default=Path("configuration.ini"),
        help="STVID configuration file (default: configuration.ini)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Override [GENTL] test_frames.",
    )
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Save the first detached frame as a NumPy .npy file.",
    )
    args = parser.parse_args()

    try:
        camera, requested_config, configured_frames, timeout_s = (
            read_configuration(args.conf_file)
        )
    except Exception as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2

    frame_count = (
        args.frames if args.frames is not None else configured_frames
    )
    if frame_count < 2:
        print("At least two frames are required.", file=sys.stderr)
        return 2

    print(f"Configuration : {args.conf_file}")
    print(f"CTI producer  : {camera.cti_file}")
    print(f"Frames        : {frame_count}")
    print(f"Timeout       : {timeout_s:.3f} s")

    first_image: np.ndarray | None = None
    host_midpoints: list[int] = []
    fetch_durations: list[int] = []
    frame_means: list[float] = []
    frame_ids: list[int] = []
    camera_timestamps: list[int] = []

    started_at = perf_counter()

    try:
        camera.open()
        describe_dataclass("Camera information", camera.get_info())
        describe_dataclass(
            "Capabilities before acquisition",
            camera.get_capabilities(),
        )

        print("\nRequested configuration")
        print("-----------------------")
        print(requested_config)

        camera.configure(requested_config)

        print(f"\nActive shape : {camera.image_shape}")
        print(f"Expected dtype: {camera.image_dtype}")

        camera.start()

        for index in range(frame_count):
            frame = camera.get_frame(timeout_s=timeout_s)

            if not frame.complete:
                raise RuntimeError(
                    f"Frame {index} was reported as incomplete."
                )

            if frame.image.ndim != 2:
                raise RuntimeError(
                    f"Frame {index} is not two-dimensional: "
                    f"shape={frame.image.shape}"
                )

            if first_image is None:
                # Keep the original returned array to prove that it remains
                # valid after subsequent GenTL buffers have been returned.
                first_image = frame.image
                first_checksum = int(first_image.sum(dtype=np.uint64))

            frame_means.append(float(frame.image.mean()))

            if frame.host_fetch_midpoint_monotonic_ns is not None:
                host_midpoints.append(
                    frame.host_fetch_midpoint_monotonic_ns
                )

            if frame.host_fetch_duration_ns is not None:
                fetch_durations.append(frame.host_fetch_duration_ns)

            if frame.frame_id is not None:
                frame_ids.append(frame.frame_id)

            if frame.camera_timestamp is not None:
                camera_timestamps.append(frame.camera_timestamp)

            print(
                f"{index:04d} "
                f"shape={frame.image.shape} "
                f"dtype={frame.dtype_name} "
                f"min={int(frame.image.min())} "
                f"max={int(frame.image.max())} "
                f"mean={frame_means[-1]:.2f} "
                f"frame_id={frame.frame_id} "
                f"camera_ts={frame.camera_timestamp} "
                f"fetch_us="
                f"{(frame.host_fetch_duration_ns or 0) / 1000:.1f}"
            )

        camera.stop()

        assert first_image is not None
        checksum_after = int(first_image.sum(dtype=np.uint64))
        if checksum_after != first_checksum:
            raise RuntimeError(
                "The first image changed after its GenTL buffer was "
                "returned. Frame data may not be detached correctly."
            )

        describe_dataclass(
            "Capabilities after acquisition",
            camera.get_capabilities(),
        )

    except Exception as exc:
        print(f"\nTEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            camera.close()
        except Exception as exc:
            print(
                f"WARNING: camera cleanup reported: {exc}",
                file=sys.stderr,
            )

    elapsed_s = perf_counter() - started_at

    print("\nSummary")
    print("-------")
    print(f"Frames captured             : {frame_count}")
    print(f"Total elapsed               : {elapsed_s:.3f} s")
    print(f"Overall acquisition rate    : {frame_count / elapsed_s:.3f} fps")
    print(f"Mean image level            : {mean(frame_means):.3f} DN")
    print(f"Image-level standard dev.   : {pstdev(frame_means):.3f} DN")
    print("Detached-buffer check       : PASS")

    if len(host_midpoints) > 1:
        intervals_ms = [
            (b - a) / 1e6
            for a, b in zip(host_midpoints, host_midpoints[1:])
        ]
        print(f"Mean host frame interval    : {mean(intervals_ms):.3f} ms")
        print(f"Host interval std. dev.     : {pstdev(intervals_ms):.3f} ms")
        print(f"Host-derived frame rate     : {1000 / mean(intervals_ms):.3f} fps")

    if fetch_durations:
        fetch_us = [value / 1000 for value in fetch_durations]
        print(f"Mean fetch duration         : {mean(fetch_us):.1f} us")
        print(f"Maximum fetch duration      : {max(fetch_us):.1f} us")

    print(f"Usable frame IDs            : {len(frame_ids)}/{frame_count}")
    print(
        f"Usable camera timestamps    : "
        f"{len(camera_timestamps)}/{frame_count}"
    )

    if frame_ids and len(frame_ids) > 1:
        discontinuities = [
            (a, b)
            for a, b in zip(frame_ids, frame_ids[1:])
            if b != a + 1
        ]
        print(f"Frame-ID discontinuities    : {len(discontinuities)}")

    if args.save is not None:
        assert first_image is not None
        args.save.parent.mkdir(parents=True, exist_ok=True)
        np.save(args.save, first_image)
        print(f"Saved first frame           : {args.save}")

    print("\nTEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
