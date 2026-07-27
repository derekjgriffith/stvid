"""Raspberry Pi camera backend implemented with Picamera2.

This backend targets the current Raspberry Pi libcamera stack. Picamera2 is an
optional STVID dependency and is imported only when this module is selected.
The backend returns detached two-dimensional ``uint8`` luminance images to the
backend-neutral :class:`Camera` interface.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import numpy as np
from picamera2 import Picamera2

from .base import (
    Camera,
    CameraConfigurationError,
    CameraError,
    CameraFrameError,
    CameraStateError,
    CameraTimeoutError,
)
from .models import (
    CameraCapabilities,
    CameraConfig,
    CameraInfo,
    Frame,
)


CAMERA_TIMESTAMP_FREQUENCY_HZ = 1_000_000_000.0
SUPPORTED_PIXEL_FORMATS = ("YUV420",)


class PiCamera2Camera(Camera):
    """Camera accessed through Raspberry Pi's Picamera2/libcamera stack.

    Parameters
    ----------
    device_index
        Zero-based camera index as reported by Picamera2.
    buffer_count
        Number of libcamera buffers used by the continuous video stream.
    """

    def __init__(
        self,
        *,
        device_index: int = 0,
        buffer_count: int = 4,
    ) -> None:
        if isinstance(device_index, bool) or not isinstance(device_index, int):
            raise TypeError("device_index must be an integer.")

        if device_index < 0:
            raise ValueError("device_index must not be negative.")

        if isinstance(buffer_count, bool) or not isinstance(buffer_count, int):
            raise TypeError("buffer_count must be an integer.")

        if buffer_count < 2:
            raise ValueError("buffer_count must be at least 2.")

        self._device_index = device_index
        self._buffer_count = buffer_count
        self._camera: Optional[Picamera2] = None
        self._is_open = False
        self._is_acquiring = False
        self._image_shape: Optional[tuple[int, int]] = None
        self._pixel_format: Optional[str] = None
        self._camera_info: Optional[dict[str, Any]] = None

    @property
    def is_open(self) -> bool:
        """Return whether the Picamera2 camera is open."""

        return self._is_open

    @property
    def is_acquiring(self) -> bool:
        """Return whether continuous acquisition is active."""

        return self._is_acquiring

    @property
    def image_shape(self) -> Optional[tuple[int, int]]:
        """Return configured image shape as ``(height, width)``."""

        return self._image_shape

    @property
    def image_dtype(self) -> Optional[str]:
        """Return the dtype used for STVID luminance frames."""

        if self._image_shape is None:
            return None

        return "uint8"

    @property
    def camera_timestamp_frequency_hz(self) -> float:
        """Return the Picamera2 sensor timestamp frequency in hertz."""

        return CAMERA_TIMESTAMP_FREQUENCY_HZ

    @property
    def camera_timestamp_frequency_source(self) -> str:
        """Return the source used for timestamp interpretation."""

        return "Picamera2 SensorTimestamp metadata (nanoseconds)"

    def open(self) -> None:
        """Open the selected Raspberry Pi camera."""

        if self._is_open:
            return

        camera: Optional[Picamera2] = None

        try:
            cameras = Picamera2.global_camera_info()

            if not cameras:
                raise CameraError("No Raspberry Pi cameras were found.")

            if self._device_index >= len(cameras):
                raise CameraError(
                    f"Camera index {self._device_index} does not exist; "
                    f"{len(cameras)} device(s) were discovered."
                )

            camera_info = dict(cameras[self._device_index])
            camera = Picamera2(camera_num=self._device_index)

            self._camera = camera
            self._camera_info = camera_info
            self._is_open = True

        except CameraError:
            if camera is not None:
                camera.close()
            raise
        except Exception as exc:
            if camera is not None:
                camera.close()
            raise CameraError(
                f"Could not open Raspberry Pi camera: {exc}"
            ) from exc

    def close(self) -> None:
        """Stop acquisition and release Picamera2 resources."""

        stop_error: Optional[Exception] = None

        if self._is_acquiring:
            try:
                self.stop()
            except Exception as exc:
                stop_error = exc

        camera = self._camera

        self._camera = None
        self._camera_info = None
        self._is_open = False
        self._is_acquiring = False
        self._image_shape = None
        self._pixel_format = None

        close_error: Optional[Exception] = None

        if camera is not None:
            try:
                camera.close()
            except Exception as exc:
                close_error = exc

        if stop_error is not None:
            raise CameraError(
                "Raspberry Pi camera acquisition could not be stopped "
                f"cleanly: {stop_error}"
            ) from stop_error

        if close_error is not None:
            raise CameraError(
                f"Raspberry Pi camera could not be closed cleanly: "
                f"{close_error}"
            ) from close_error

    def get_info(self) -> CameraInfo:
        """Return identifying information for the selected camera."""

        self._require_open()

        info = self._camera_info or {}
        model = str(info.get("Model", ""))
        device_id = str(info.get("Id", self._device_index))

        return CameraInfo(
            vendor="Raspberry Pi",
            model=model,
            serial_number="",
            device_id=device_id,
            transport_type="libcamera",
            display_name=model or f"Raspberry Pi camera {self._device_index}",
            firmware_version=None,
        )

    def get_capabilities(self) -> CameraCapabilities:
        """Return the generic capabilities exposed by this backend."""

        self._require_open()

        return CameraCapabilities(
            pixel_formats=SUPPORTED_PIXEL_FORMATS,
            binning_horizontal_factors=(1,),
            binning_vertical_factors=(1,),
            supports_roi=False,
            supports_frame_rate_control=True,
            supports_gain_control=True,
            supports_trigger=False,
            supports_frame_id=False,
            supports_camera_timestamp=True,
        )

    def configure(self, config: CameraConfig) -> None:
        """Configure a continuous 8-bit luminance stream."""

        self._require_open()

        if self._is_acquiring:
            raise CameraStateError(
                "Stop acquisition before changing camera configuration."
            )

        self._validate_config(config)

        assert self._camera is not None

        width = config.width
        height = config.height

        if width is None or height is None:
            sensor_width, sensor_height = self._camera.sensor_resolution
            width = sensor_width if width is None else width
            height = sensor_height if height is None else height

        pixel_format = config.pixel_format or "YUV420"
        controls = self._build_controls(config)

        try:
            camera_config = self._camera.create_video_configuration(
                main={
                    "size": (int(width), int(height)),
                    "format": pixel_format,
                    "preserve_ar": False,
                },
                buffer_count=self._buffer_count,
                controls=controls,
                display=None,
                encode=None,
                queue=True,
            )
            self._camera.configure(camera_config)
        except Exception as exc:
            raise CameraConfigurationError(
                f"Could not configure Raspberry Pi camera: {exc}"
            ) from exc

        stream = self._camera.camera_configuration()["main"]
        actual_width, actual_height = stream["size"]

        self._image_shape = (int(actual_height), int(actual_width))
        self._pixel_format = str(stream["format"])

        if self._image_shape != (int(height), int(width)):
            raise CameraConfigurationError(
                "Picamera2 adjusted the requested dimensions from "
                f"{width} x {height} to {actual_width} x {actual_height}. "
                "Use dimensions accepted by the selected sensor mode."
            )

    def start(self) -> None:
        """Start continuous camera acquisition."""

        self._require_open()

        if self._is_acquiring:
            return

        if self._image_shape is None:
            raise CameraStateError(
                "Configure the camera before starting acquisition."
            )

        assert self._camera is not None

        try:
            self._camera.start()
        except Exception as exc:
            raise CameraError(
                f"Could not start Raspberry Pi camera acquisition: {exc}"
            ) from exc

        self._is_acquiring = True

    def stop(self) -> None:
        """Stop acquisition without closing the camera."""

        if not self._is_acquiring:
            return

        camera = self._camera

        if camera is None:
            self._is_acquiring = False
            return

        try:
            camera.stop()
        except Exception as exc:
            raise CameraError(
                f"Could not stop Raspberry Pi camera acquisition: {exc}"
            ) from exc
        finally:
            self._is_acquiring = False

    def get_frame(self, timeout_s: float = 1.0) -> Frame:
        """Fetch one detached luminance image and its capture metadata."""

        self._require_open()

        if timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero.")

        if not self._is_acquiring:
            raise CameraStateError(
                "Acquisition has not been started."
            )

        assert self._camera is not None
        assert self._image_shape is not None

        fetch_start_monotonic_ns = time.monotonic_ns()
        request = None

        try:
            request = self._camera.capture_request(wait=float(timeout_s))
            fetch_end_monotonic_ns = time.monotonic_ns()
            host_wall_timestamp_ns = time.time_ns()

            metadata = request.get_metadata()
            image = self._copy_luminance_image(request)

            sensor_timestamp = self._optional_int(
                metadata.get("SensorTimestamp")
            )

            host_timestamp_ns = (
                fetch_start_monotonic_ns
                + fetch_end_monotonic_ns
            ) // 2

            return Frame(
                image=image,
                frame_id=None,
                camera_timestamp=sensor_timestamp,
                camera_timestamp_frequency_hz=(
                    CAMERA_TIMESTAMP_FREQUENCY_HZ
                    if sensor_timestamp is not None
                    else None
                ),
                host_timestamp_ns=host_timestamp_ns,
                complete=True,
                pixel_format=self._pixel_format,
                host_fetch_start_monotonic_ns=(
                    fetch_start_monotonic_ns
                ),
                host_fetch_end_monotonic_ns=(
                    fetch_end_monotonic_ns
                ),
                host_wall_timestamp_ns=host_wall_timestamp_ns,
            )

        except TimeoutError as exc:
            raise CameraTimeoutError(
                f"No frame received within {timeout_s:.3f} s."
            ) from exc
        except CameraError:
            raise
        except Exception as exc:
            if exc.__class__.__name__ == "TimeoutError":
                raise CameraTimeoutError(
                    f"No frame received within {timeout_s:.3f} s."
                ) from exc

            raise CameraError(
                f"Raspberry Pi frame acquisition failed: {exc}"
            ) from exc
        finally:
            if request is not None:
                try:
                    request.release()
                except Exception:
                    pass

    def _copy_luminance_image(self, request: Any) -> np.ndarray:
        """Copy the Y plane from a Picamera2 YUV420 request."""

        assert self._image_shape is not None

        try:
            array = np.asarray(request.make_array("main"))
        except Exception as exc:
            raise CameraFrameError(
                f"Could not access Picamera2 image data: {exc}"
            ) from exc

        height, width = self._image_shape

        if array.ndim != 2:
            raise CameraFrameError(
                "Picamera2 YUV420 stream did not produce a two-dimensional "
                f"array; received shape {array.shape}."
            )

        if array.shape[0] < height or array.shape[1] < width:
            raise CameraFrameError(
                "Picamera2 frame is smaller than the configured image: "
                f"received {array.shape}, expected at least "
                f"({height}, {width})."
            )

        image = array[:height, :width].copy()

        if image.dtype != np.uint8:
            raise CameraFrameError(
                "Picamera2 luminance stream must be uint8, not "
                f"{image.dtype}."
            )

        return image

    def _build_controls(self, config: CameraConfig) -> dict[str, Any]:
        """Translate generic camera settings into libcamera controls."""

        controls: dict[str, Any] = {
            "AwbEnable": False,
        }

        if config.exposure_us is not None or config.gain is not None:
            controls["AeEnable"] = False

        if config.exposure_us is not None:
            controls["ExposureTime"] = int(round(config.exposure_us))

        if config.gain is not None:
            controls["AnalogueGain"] = float(config.gain)

        if config.frame_rate_hz is not None:
            frame_duration_us = int(
                round(1_000_000.0 / config.frame_rate_hz)
            )

            if config.exposure_us is not None:
                frame_duration_us = max(
                    frame_duration_us,
                    int(round(config.exposure_us)),
                )

            controls["FrameDurationLimits"] = (
                frame_duration_us,
                frame_duration_us,
            )

        return controls

    @staticmethod
    def _validate_config(config: CameraConfig) -> None:
        """Reject generic settings unsupported by the Picamera2 backend."""

        if config.width is not None and config.width <= 0:
            raise CameraConfigurationError(
                "width must be greater than zero."
            )

        if config.height is not None and config.height <= 0:
            raise CameraConfigurationError(
                "height must be greater than zero."
            )

        if config.exposure_us is not None and config.exposure_us <= 0:
            raise CameraConfigurationError(
                "exposure_us must be greater than zero."
            )

        if config.frame_rate_hz is not None and config.frame_rate_hz <= 0:
            raise CameraConfigurationError(
                "frame_rate_hz must be greater than zero."
            )

        if config.gain is not None and config.gain <= 0:
            raise CameraConfigurationError(
                "gain must be greater than zero."
            )

        pixel_format = config.pixel_format or "YUV420"

        if pixel_format not in SUPPORTED_PIXEL_FORMATS:
            raise CameraConfigurationError(
                f"Unsupported PI2 pixel format {pixel_format!r}. "
                f"Supported values: {', '.join(SUPPORTED_PIXEL_FORMATS)}"
            )

        if config.offset_x not in (None, 0):
            raise CameraConfigurationError(
                "PI2 offset_x ROI control is not implemented."
            )

        if config.offset_y not in (None, 0):
            raise CameraConfigurationError(
                "PI2 offset_y ROI control is not implemented."
            )

        if config.binning_horizontal not in (None, 1):
            raise CameraConfigurationError(
                "PI2 hardware binning control is not implemented."
            )

        if config.binning_vertical not in (None, 1):
            raise CameraConfigurationError(
                "PI2 hardware binning control is not implemented."
            )

        if config.acquisition_mode not in (None, "Continuous"):
            raise CameraConfigurationError(
                "PI2 supports only Continuous acquisition."
            )

        if config.trigger_mode not in (None, "Off"):
            raise CameraConfigurationError(
                "PI2 trigger operation is not implemented."
            )

        if config.trigger_source is not None:
            raise CameraConfigurationError(
                "PI2 trigger_source is not implemented."
            )

    def _require_open(self) -> None:
        if not self._is_open or self._camera is None:
            raise CameraStateError("Camera is not open.")

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None
