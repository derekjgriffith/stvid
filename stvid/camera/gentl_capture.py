"""STVID acquisition loop for cameras accessed through GenTL."""

from __future__ import annotations

import configparser
import logging
import os
import sys
import time

import cv2
import numpy as np

from .gentl import GenTLCamera
from .models import CameraConfig
from .shared import SharedFrameBufferAllocation


def capture_gentl(
    image_queue,
    buffer_1: SharedFrameBufferAllocation,
    buffer_2: SharedFrameBufferAllocation,
    tend: float,
    live: bool,
    conf_file,
) -> None:
    """Capture GenTL frames into STVID's shared double buffer."""

    logger = _setup_logging(os.getcwd())

    cfg = configparser.ConfigParser(
        inline_comment_prefixes=("#", ";")
    )
    cfg.read(conf_file)

    section = "GENTL"
    timeout_s = cfg.getfloat(section, "timeout_s", fallback=2.0)

    timestamp_frequency_hz = _optional_float(
        cfg,
        section,
        "timestamp_frequency_hz",
    )

    serial_number = cfg.get(
        section,
        "serial_number",
        fallback="",
    ).strip() or None

    camera = GenTLCamera(
        cfg.get(section, "cti_file"),
        device_index=cfg.getint(section, "device_id", fallback=0),
        serial_number=serial_number,
        timestamp_frequency_hz=timestamp_frequency_hz,
    )

    views = (buffer_1.map(), buffer_2.map())
    active_index = 0
    reason = "Session complete"

    try:
        camera.open()
        camera.configure(_camera_config_from_ini(cfg, section))

        frequency_hz = camera.camera_timestamp_frequency_hz
        frequency_source = getattr(
            camera,
            "camera_timestamp_frequency_source",
            None,
        )

        for view in views:
            view.set_camera_timestamp_frequency(
                frequency_hz,
                frequency_source,
            )

        info = camera.get_info()
        logger.info(
            "Using GenTL camera %s %s, serial %s",
            info.vendor,
            info.model,
            info.serial_number,
        )

        if frequency_hz is None:
            logger.warning(
                "Camera timestamp frequency is unavailable"
            )
        else:
            logger.info(
                "Camera timestamp frequency: %.7f Hz (%s)",
                frequency_hz,
                frequency_source or "unspecified source",
            )

        camera.start()

        while time.time() < tend:
            _wait_for_output_capacity(image_queue, logger)

            view = views[active_index]
            view.reset()

            for frame_index in range(view.image.shape[2]):
                frame = camera.get_frame(timeout_s=timeout_s)

                if not frame.complete:
                    raise RuntimeError(
                        "Received an incomplete GenTL frame"
                    )

                image = frame.image

                if image.dtype != np.uint8:
                    raise ValueError(
                        "STVID GenTL acquisition currently requires "
                        f"Mono8/uint8 data, not {image.dtype}"
                    )

                host_wall_timestamp_ns = getattr(
                    frame,
                    "host_wall_timestamp_ns",
                    None,
                )

                if host_wall_timestamp_ns is None:
                    host_wall_timestamp_ns = time.time_ns()

                view.store_frame(
                    frame_index,
                    image,
                    host_timestamp_ns=host_wall_timestamp_ns,
                    camera_timestamp=frame.camera_timestamp,
                    frame_id=frame.frame_id,
                )

                if live:
                    cv2.imshow("Capture", image)
                    cv2.waitKey(1)

            view.validate_complete()

            buffer_number = active_index + 1
            image_queue.put(buffer_number)
            logger.debug(
                "Captured GenTL buffer %d (%dx%dx%d)",
                buffer_number,
                view.image.shape[1],
                view.image.shape[0],
                view.image.shape[2],
            )

            active_index = 1 - active_index

    except KeyboardInterrupt:
        reason = "Keyboard interrupt"
    except Exception as exc:
        reason = str(exc)
        logger.exception("GenTL capture failed: %s", exc)
        raise
    finally:
        if camera.is_acquiring:
            camera.stop()
        camera.close()
        logger.info("Capture: %s - Exiting", reason)



def _setup_logging(path: str) -> logging.Logger:
    formatter = logging.Formatter(
        "%(asctime)s [%(processName)-12.12s] "
        "[%(levelname)-5.5s] %(message)s"
    )

    logger = logging.getLogger()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(
        os.path.join(path, "acquire.log")
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

def _camera_config_from_ini(
    cfg: configparser.ConfigParser,
    section: str,
) -> CameraConfig:
    """Build a generic camera configuration from the GENTL section."""

    return CameraConfig(
        exposure_us=_optional_float(cfg, section, "exposure_us"),
        frame_rate_hz=_optional_float(cfg, section, "framerate"),
        gain=_optional_float(cfg, section, "gain"),
        pixel_format=_optional_text(cfg, section, "pixel_format"),
        width=_optional_int(cfg, section, "nx"),
        height=_optional_int(cfg, section, "ny"),
        offset_x=_optional_int(cfg, section, "offset_x"),
        offset_y=_optional_int(cfg, section, "offset_y"),
        binning_horizontal=_optional_int(
            cfg,
            section,
            "binning_horizontal",
        ),
        binning_vertical=_optional_int(
            cfg,
            section,
            "binning_vertical",
        ),
        acquisition_mode=_optional_text(
            cfg,
            section,
            "acquisition_mode",
        ),
        trigger_mode=_optional_text(
            cfg,
            section,
            "trigger_mode",
        ),
        trigger_source=_optional_text(
            cfg,
            section,
            "trigger_source",
        ),
    )


def _wait_for_output_capacity(image_queue, logger) -> None:
    warned = False

    while image_queue.qsize() > 1:
        if not warned:
            logger.warning(
                "Acquiring data faster than the CPU can process"
            )
            warned = True
        time.sleep(0.1)


def _optional_text(cfg, section: str, option: str) -> str | None:
    value = cfg.get(section, option, fallback="").strip()
    return value or None


def _optional_float(cfg, section: str, option: str) -> float | None:
    value = _optional_text(cfg, section, option)

    if value is None or value.casefold() == "auto":
        return None

    return float(value)


def _optional_int(cfg, section: str, option: str) -> int | None:
    value = _optional_text(cfg, section, option)

    if value is None:
        return None

    return int(value)
