"""STVID acquisition loop for Raspberry Pi cameras using Picamera2."""

from __future__ import annotations

import configparser
import logging
import os
import sys
import time
from typing import Optional

import cv2
import numpy as np

from .base import CameraTimeoutError
from .models import CameraConfig
from .pi2 import PiCamera2Camera
from .shared import SharedFrameBufferAllocation


def capture_pi2(
    image_queue,
    free_buffer_queue,
    buffer_1: SharedFrameBufferAllocation,
    buffer_2: SharedFrameBufferAllocation,
    tend: float,
    live: bool,
    conf_file,
) -> None:
    """Capture Picamera2 frames into STVID's shared double buffer."""

    logger = _setup_logging(os.getcwd())

    cfg = configparser.ConfigParser(
        inline_comment_prefixes=("#", ";")
    )
    cfg.read(conf_file)

    section = "PI2"
    timeout_s = cfg.getfloat(section, "timeout_s", fallback=2.0)
    timeout_retries = cfg.getint(
        section,
        "timeout_retries",
        fallback=2,
    )

    camera = PiCamera2Camera(
        device_index=cfg.getint(section, "device_id", fallback=0),
        buffer_count=cfg.getint(section, "buffer_count", fallback=4),
    )

    views = (buffer_1.map(), buffer_2.map())
    reason = "Session complete"

    try:
        camera.open()
        camera.configure(_camera_config_from_ini(cfg, section))

        frequency_hz = camera.camera_timestamp_frequency_hz
        frequency_source = camera.camera_timestamp_frequency_source

        for view in views:
            view.set_camera_timestamp_frequency(
                frequency_hz,
                frequency_source,
            )

        info = camera.get_info()
        logger.info(
            "Using PI2 camera %s %s (%s)",
            info.vendor,
            info.model,
            info.device_id,
        )
        logger.info(
            "Camera timestamp frequency: %.1f Hz (%s)",
            frequency_hz,
            frequency_source,
        )

        camera.start()

        consecutive_timeouts = 0

        while time.time() < tend:
            buffer_number = free_buffer_queue.get()
            view = views[buffer_number - 1]
            view.reset()

            try:
                for frame_index in range(view.image.shape[2]):
                    while True:
                        try:
                            frame = camera.get_frame(
                                timeout_s=timeout_s
                            )
                            consecutive_timeouts = 0
                            break
                        except CameraTimeoutError:
                            consecutive_timeouts += 1
                            logger.warning(
                                "Camera timeout %d/%d",
                                consecutive_timeouts,
                                timeout_retries + 1,
                            )

                            if consecutive_timeouts > timeout_retries:
                                raise

                    if not frame.complete:
                        raise RuntimeError(
                            "Received an incomplete PI2 frame"
                        )

                    image = frame.image

                    if image.dtype != np.uint8:
                        raise ValueError(
                            "STVID PI2 acquisition requires uint8 "
                            f"luminance data, not {image.dtype}"
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
                image_queue.put(buffer_number)
                logger.debug(
                    "Captured PI2 buffer %d (%dx%dx%d)",
                    buffer_number,
                    view.image.shape[1],
                    view.image.shape[0],
                    view.image.shape[2],
                )
            except Exception:
                free_buffer_queue.put(buffer_number)
                raise

    except KeyboardInterrupt:
        reason = "Keyboard interrupt"
    except Exception as exc:
        reason = str(exc)
        logger.exception("PI2 capture failed: %s", exc)
        raise
    finally:
        try:
            if camera.is_acquiring:
                camera.stop()
        except Exception:
            logger.exception(
                "Could not stop PI2 acquisition cleanly"
            )

        try:
            camera.close()
        except Exception:
            logger.exception(
                "Could not close PI2 camera cleanly"
            )

        image_queue.put(None)
        logger.info("Capture: %s - Exiting", reason)

        if live:
            cv2.destroyAllWindows()


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
    """Build a generic camera configuration from the PI2 section."""

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


def _optional_text(
    cfg: configparser.ConfigParser,
    section: str,
    option: str,
) -> Optional[str]:
    value = cfg.get(section, option, fallback="").strip()
    return value or None


def _optional_float(
    cfg: configparser.ConfigParser,
    section: str,
    option: str,
) -> Optional[float]:
    value = _optional_text(cfg, section, option)

    if value is None or value.casefold() == "auto":
        return None

    return float(value)


def _optional_int(
    cfg: configparser.ConfigParser,
    section: str,
    option: str,
) -> Optional[int]:
    value = _optional_text(cfg, section, option)

    if value is None:
        return None

    return int(value)
