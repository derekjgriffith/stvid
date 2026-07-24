"""GenTL camera backend implemented with Harvesters.

The backend is intended for Linux/Raspberry Pi operation with the SVS Vistek
GenTL producer, while remaining usable with other GenTL/GenICam cameras.

Harvesters and GenICam are optional STVID dependencies. Importing this module
therefore requires those packages, but the rest of the camera abstraction does
not.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from genicam.gentl import TimeoutException
from harvesters.core import Harvester

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


DEFAULT_CTI = Path(
    "/opt/SVS/SVCamKit/SDK/Linux64_x64/cti/"
    "libsv_gev_tl_x64.cti"
)


class GenTLCamera(Camera):
    """Camera accessed through a GenTL producer.

    Parameters
    ----------
    cti_file
        Path to the GenTL producer ``.cti`` file. The SVS Vistek Linux path
        used on the Raspberry Pi is the default.
    device_index
        Zero-based device index used when ``serial_number`` is not supplied.
    serial_number
        Optional camera serial number. When supplied, it takes precedence over
        ``device_index``.
    """

    def __init__(
        self,
        cti_file: str | Path = DEFAULT_CTI,
        *,
        device_index: int = 0,
        serial_number: str | None = None,
    ) -> None:
        if isinstance(device_index, bool) or not isinstance(device_index, int):
            raise TypeError("device_index must be an integer.")

        if device_index < 0:
            raise ValueError("device_index must not be negative.")

        self._cti_file = Path(cti_file).expanduser()
        self._device_index = device_index
        self._serial_number = (
            str(serial_number) if serial_number is not None else None
        )

        self._harvester: Harvester | None = None
        self._image_acquirer: Any | None = None
        self._selected_device_index: int | None = None

        self._is_open = False
        self._is_acquiring = False

        self._camera_timestamp_frequency_hz: float | None = None
        self._observed_frame_id = False
        self._observed_camera_timestamp = False

    @property
    def is_open(self) -> bool:
        """Return whether the camera and transport are open."""

        return self._is_open

    @property
    def is_acquiring(self) -> bool:
        """Return whether image acquisition is active."""

        return self._is_acquiring

    @property
    def image_shape(self) -> tuple[int, int] | None:
        """Return active image shape as ``(height, width)``, if readable."""

        if not self._is_open:
            return None

        width = self._read_optional_feature("Width")
        height = self._read_optional_feature("Height")

        try:
            width_int = int(width)
            height_int = int(height)
        except (TypeError, ValueError):
            return None

        if width_int <= 0 or height_int <= 0:
            return None

        return height_int, width_int

    @property
    def image_dtype(self) -> str | None:
        """Return the expected NumPy dtype name for the active pixel format."""

        if not self._is_open:
            return None

        pixel_format = self._read_optional_feature("PixelFormat")

        if pixel_format is None:
            return None

        name = str(pixel_format).casefold()

        # Harvesters exposes unpacked Mono10/12/16 data as uint16 arrays.
        if "mono8" in name:
            return "uint8"

        if any(token in name for token in ("mono10", "mono12", "mono14", "mono16")):
            return "uint16"

        return None

    @property
    def node_map(self) -> Any:
        """Return the remote GenICam node map for advanced access."""

        self._require_open()
        assert self._image_acquirer is not None
        return self._image_acquirer.remote_device.node_map

    @property
    def cti_file(self) -> Path:
        """Return the configured GenTL producer path."""

        return self._cti_file

    def open(self) -> None:
        """Load the producer, discover cameras, and open the selected device."""

        if self._is_open:
            return

        if not self._cti_file.is_file():
            raise CameraError(
                f"GenTL producer not found: {self._cti_file}"
            )

        harvester = Harvester()
        image_acquirer: Any | None = None

        try:
            harvester.add_file(str(self._cti_file))
            harvester.update()

            if not harvester.device_info_list:
                raise CameraError(
                    "No GenTL cameras were found. Check camera power, "
                    "network configuration, and the selected CTI producer."
                )

            selected_index = self._select_device_index(harvester)
            image_acquirer = harvester.create(selected_index)

            self._harvester = harvester
            self._image_acquirer = image_acquirer
            self._selected_device_index = selected_index
            self._is_open = True

            self._camera_timestamp_frequency_hz = (
                self._discover_timestamp_frequency()
            )

        except CameraError:
            self._destroy_resources(image_acquirer, harvester)
            raise
        except Exception as exc:
            self._destroy_resources(image_acquirer, harvester)
            raise CameraError(
                f"Could not open GenTL camera: {exc}"
            ) from exc

    def close(self) -> None:
        """Stop acquisition and release all Harvesters/GenTL resources."""

        stop_error: Exception | None = None

        if self._is_acquiring:
            try:
                self.stop()
            except Exception as exc:
                stop_error = exc

        image_acquirer = self._image_acquirer
        harvester = self._harvester

        self._image_acquirer = None
        self._harvester = None
        self._selected_device_index = None
        self._is_open = False
        self._is_acquiring = False
        self._camera_timestamp_frequency_hz = None

        cleanup_error = self._destroy_resources(
            image_acquirer,
            harvester,
        )

        if stop_error is not None:
            raise CameraError(
                f"Camera acquisition could not be stopped cleanly: "
                f"{stop_error}"
            ) from stop_error

        if cleanup_error is not None:
            raise CameraError(
                f"GenTL resources could not be released cleanly: "
                f"{cleanup_error}"
            ) from cleanup_error

    def get_info(self) -> CameraInfo:
        """Return identifying information for the selected camera."""

        self._require_open()

        assert self._harvester is not None
        assert self._selected_device_index is not None

        info = self._harvester.device_info_list[
            self._selected_device_index
        ]

        return CameraInfo(
            vendor=self._string_attribute(info, "vendor"),
            model=self._string_attribute(info, "model"),
            serial_number=self._string_attribute(info, "serial_number"),
            device_id=self._string_attribute(info, "id_"),
            transport_type=self._optional_string_attribute(info, "tl_type"),
            display_name=self._optional_string_attribute(
                info,
                "display_name",
            ),
            firmware_version=self._first_optional_feature_as_string(
                "DeviceFirmwareVersion",
                "DeviceVersion",
            ),
        )

    def get_capabilities(self) -> CameraCapabilities:
        """Inspect common GenICam nodes and return generic capabilities."""

        self._require_open()

        return CameraCapabilities(
            pixel_formats=tuple(
                self._get_enum_entries_by_name("PixelFormat")
            ),
            binning_horizontal_factors=tuple(
                self._discover_binning_factors("BinningHorizontal")
            ),
            binning_vertical_factors=tuple(
                self._discover_binning_factors("BinningVertical")
            ),
            supports_roi=all(
                self._feature_exists(name)
                for name in ("Width", "Height", "OffsetX", "OffsetY")
            ),
            supports_frame_rate_control=self._any_feature_exists(
                "AcquisitionFrameRate",
                "AcquisitionFrameRateAbs",
            ),
            supports_gain_control=self._any_feature_exists(
                "Gain",
                "GainRaw",
            ),
            supports_trigger=self._feature_exists("TriggerMode"),
            # These transport-buffer properties are unimplemented by the
            # tested SVS producer. Report support only after a real value has
            # actually been observed.
            supports_frame_id=self._observed_frame_id,
            supports_camera_timestamp=self._observed_camera_timestamp,
        )

    def configure(self, config: CameraConfig) -> None:
        """Apply generic camera configuration while acquisition is stopped."""

        self._require_open()

        if self._is_acquiring:
            raise CameraStateError(
                "Stop acquisition before changing camera configuration."
            )

        # Binning can change the allowed ROI dimensions and increments.
        if config.binning_horizontal is not None:
            self._write_binning_factor(
                "BinningHorizontal",
                config.binning_horizontal,
            )

        if config.binning_vertical is not None:
            self._write_binning_factor(
                "BinningVertical",
                config.binning_vertical,
            )

        if config.pixel_format is not None:
            self._write_feature_with_aliases(
                "PixelFormat",
                config.pixel_format,
            )

        # Zero offsets are commonly required before increasing Width/Height.
        if config.offset_x == 0:
            self._write_feature_with_aliases("OffsetX", 0)

        if config.offset_y == 0:
            self._write_feature_with_aliases("OffsetY", 0)

        if config.width is not None:
            self._write_feature_with_aliases("Width", config.width)

        if config.height is not None:
            self._write_feature_with_aliases("Height", config.height)

        if config.offset_x is not None and config.offset_x != 0:
            self._write_feature_with_aliases("OffsetX", config.offset_x)

        if config.offset_y is not None and config.offset_y != 0:
            self._write_feature_with_aliases("OffsetY", config.offset_y)

        scalar_settings = (
            ("ExposureTime", config.exposure_us),
            ("AcquisitionFrameRate", config.frame_rate_hz),
            ("Gain", config.gain),
            ("AcquisitionMode", config.acquisition_mode),
            ("TriggerMode", config.trigger_mode),
            ("TriggerSource", config.trigger_source),
        )

        for feature_name, value in scalar_settings:
            if value is not None:
                self._write_feature_with_aliases(feature_name, value)

    def start(self) -> None:
        """Start continuous or triggered acquisition."""

        self._require_open()

        if self._is_acquiring:
            return

        assert self._image_acquirer is not None

        try:
            self._image_acquirer.start()
        except Exception as exc:
            raise CameraError(
                f"Could not start acquisition: {exc}"
            ) from exc

        self._is_acquiring = True

    def stop(self) -> None:
        """Stop acquisition without closing the camera."""

        if not self._is_acquiring:
            return

        if self._image_acquirer is None:
            self._is_acquiring = False
            return

        try:
            self._image_acquirer.stop()
        except Exception as exc:
            raise CameraError(
                f"Could not stop acquisition: {exc}"
            ) from exc
        finally:
            self._is_acquiring = False

    def get_frame(self, timeout_s: float = 1.0) -> Frame:
        """Fetch one frame and return a detached two-dimensional NumPy array."""

        self._require_open()

        if timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero.")

        if not self._is_acquiring:
            raise CameraStateError(
                "Acquisition has not been started."
            )

        assert self._image_acquirer is not None

        fetch_start_monotonic_ns = time.monotonic_ns()

        try:
            with self._image_acquirer.fetch(
                timeout=float(timeout_s)
            ) as buffer:
                fetch_end_monotonic_ns = time.monotonic_ns()
                host_wall_timestamp_ns = time.time_ns()

                complete = self._buffer_is_complete(buffer)
                image, pixel_format = self._copy_image_component(buffer)

                frame_id = self._read_buffer_integer(
                    buffer,
                    "frame_id",
                )

                # Prefer timestamp_ns when implemented. The SVS producer tested
                # on the Raspberry Pi raises NotImplementedException for these
                # properties, which is handled as an unavailable value.
                camera_timestamp = self._read_first_buffer_integer(
                    buffer,
                    "timestamp_ns",
                    "timestamp",
                )

                if frame_id is not None:
                    self._observed_frame_id = True

                if camera_timestamp is not None:
                    self._observed_camera_timestamp = True

                host_timestamp_ns = (
                    fetch_start_monotonic_ns
                    + fetch_end_monotonic_ns
                ) // 2

                return Frame(
                    image=image,
                    frame_id=frame_id,
                    camera_timestamp=camera_timestamp,
                    camera_timestamp_frequency_hz=(
                        self._camera_timestamp_frequency_hz
                    ),
                    host_timestamp_ns=host_timestamp_ns,
                    complete=complete,
                    pixel_format=pixel_format,
                    host_fetch_start_monotonic_ns=(
                        fetch_start_monotonic_ns
                    ),
                    host_fetch_end_monotonic_ns=(
                        fetch_end_monotonic_ns
                    ),
                    host_wall_timestamp_ns=host_wall_timestamp_ns,
                )

        except TimeoutException as exc:
            raise CameraTimeoutError(
                f"No frame received within {timeout_s:.3f} s."
            ) from exc
        except CameraError:
            raise
        except Exception as exc:
            # Some Harvesters versions may surface a built-in TimeoutError.
            if isinstance(exc, TimeoutError):
                raise CameraTimeoutError(
                    f"No frame received within {timeout_s:.3f} s."
                ) from exc

            raise CameraError(
                f"Frame acquisition failed: {exc}"
            ) from exc

    def _copy_image_component(
        self,
        buffer: Any,
    ) -> tuple[np.ndarray, str | None]:
        """Copy the first image component while the buffer is owned."""

        try:
            components = buffer.payload.components
        except Exception as exc:
            raise CameraFrameError(
                f"Could not inspect buffer payload: {exc}"
            ) from exc

        if not components:
            raise CameraFrameError(
                "Received buffer contains no image components."
            )

        if len(components) != 1:
            raise CameraFrameError(
                "The current GenTL backend supports exactly one image "
                f"component; received {len(components)}."
            )

        component = components[0]

        try:
            width = int(component.width)
            height = int(component.height)
        except Exception as exc:
            raise CameraFrameError(
                f"Could not read image dimensions: {exc}"
            ) from exc

        if width <= 0 or height <= 0:
            raise CameraFrameError(
                f"Invalid image dimensions received: {width} x {height}."
            )

        try:
            data = np.asarray(component.data)
        except Exception as exc:
            raise CameraFrameError(
                f"Could not access image component data: {exc}"
            ) from exc

        expected_pixels = width * height

        if data.size != expected_pixels:
            raise CameraFrameError(
                "The current GenTL backend supports single-component "
                "two-dimensional images only. "
                f"Received {data.size} elements for an expected "
                f"{expected_pixels}-pixel image ({width} x {height}), "
                f"format {getattr(component, 'data_format', None)!s}."
            )

        try:
            image = data.reshape(height, width).copy()
        except Exception as exc:
            raise CameraFrameError(
                f"Could not reshape and detach image data: {exc}"
            ) from exc

        pixel_format = self._safe_optional_string(
            self._get_optional_attribute(component, "data_format")
        )

        return image, pixel_format

    def _select_device_index(self, harvester: Harvester) -> int:
        """Select a camera by serial number or device index."""

        devices = harvester.device_info_list

        if self._serial_number is None:
            if self._device_index >= len(devices):
                raise CameraError(
                    f"Camera index {self._device_index} does not exist; "
                    f"{len(devices)} device(s) were discovered."
                )

            return self._device_index

        for index, info in enumerate(devices):
            candidate = self._optional_string_attribute(
                info,
                "serial_number",
            )

            if candidate == self._serial_number:
                return index

        discovered = [
            self._optional_string_attribute(info, "serial_number")
            or "<unknown>"
            for info in devices
        ]

        raise CameraError(
            f"No camera found with serial number "
            f"{self._serial_number!r}. Discovered serial numbers: "
            + ", ".join(discovered)
        )

    def _write_feature_with_aliases(
        self,
        name: str,
        value: Any,
    ) -> None:
        """Write a feature, trying common older GenICam aliases."""

        aliases = {
            "ExposureTime": (
                "ExposureTime",
                "ExposureTimeAbs",
            ),
            "AcquisitionFrameRate": (
                "AcquisitionFrameRate",
                "AcquisitionFrameRateAbs",
            ),
            "Gain": (
                "Gain",
                "GainRaw",
            ),
        }

        candidates = aliases.get(name, (name,))
        errors: list[str] = []

        for candidate in candidates:
            try:
                node = getattr(self.node_map, candidate)
                node.value = value
                return
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        raise CameraConfigurationError(
            f"Could not set {name}={value!r}. "
            + " | ".join(errors)
        )

    def _write_binning_factor(
        self,
        feature_name: str,
        factor: int,
    ) -> None:
        """Set an integer or enumeration binning node."""

        if isinstance(factor, bool) or not isinstance(factor, int):
            raise CameraConfigurationError(
                f"{feature_name} must be an integer factor."
            )

        if factor < 1:
            raise CameraConfigurationError(
                f"{feature_name} must be at least 1."
            )

        try:
            node = getattr(self.node_map, feature_name)
        except Exception as exc:
            raise CameraConfigurationError(
                f"Camera does not expose {feature_name}."
            ) from exc

        try:
            node.value = factor
            return
        except Exception:
            pass

        entries = self._get_enum_entries(node)

        if not entries:
            raise CameraConfigurationError(
                f"Could not set {feature_name} to factor {factor}; "
                "the node is neither writable as an integer nor exposes "
                "usable enumeration entries."
            )

        lookup = {entry.casefold(): entry for entry in entries}

        for candidate in self._binning_enum_candidates(factor):
            exact = lookup.get(candidate.casefold())

            if exact is None:
                continue

            try:
                node.value = exact
                return
            except Exception:
                continue

        raise CameraConfigurationError(
            f"Could not set {feature_name} to factor {factor}. "
            f"Available values: {', '.join(entries)}"
        )

    @staticmethod
    def _binning_enum_candidates(factor: int) -> tuple[str, ...]:
        if factor == 1:
            return (
                "Off",
                "Disabled",
                "Disable",
                "False",
                "X1",
                "1x",
                "Binning1",
                "Bin1",
                "One",
            )

        general = (
            f"X{factor}",
            f"{factor}x",
            f"Binning{factor}",
            f"Bin{factor}",
            f"Factor{factor}",
        )

        if factor == 2:
            return (
                "On",
                "Enabled",
                "Enable",
                "True",
                *general,
            )

        return general

    def _discover_binning_factors(
        self,
        feature_name: str,
    ) -> list[int]:
        """Discover likely supported factors from a GenICam node."""

        try:
            node = getattr(self.node_map, feature_name)
        except Exception:
            return []

        factors: set[int] = set()

        try:
            minimum = int(node.min)
            maximum = int(node.max)
            increment = int(getattr(node, "inc", 1))

            if increment <= 0:
                increment = 1

            factors.update(range(minimum, maximum + 1, increment))
        except Exception:
            pass

        for entry in self._get_enum_entries(node):
            factor = self._binning_factor_from_symbol(entry)

            if factor is not None:
                factors.add(factor)

        return sorted(factor for factor in factors if factor >= 1)

    @staticmethod
    def _binning_factor_from_symbol(symbol: str) -> int | None:
        normalized = symbol.strip().casefold()

        if normalized in {
            "off",
            "disabled",
            "disable",
            "false",
            "one",
        }:
            return 1

        if normalized in {
            "on",
            "enabled",
            "enable",
            "true",
        }:
            return 2

        patterns = (
            r"^x(\d+)$",
            r"^(\d+)x$",
            r"^binning(\d+)$",
            r"^bin(\d+)$",
            r"^factor(\d+)$",
        )

        for pattern in patterns:
            match = re.match(pattern, normalized)

            if match:
                return int(match.group(1))

        return None

    def _get_enum_entries_by_name(
        self,
        feature_name: str,
    ) -> list[str]:
        try:
            node = getattr(self.node_map, feature_name)
        except Exception:
            return []

        return self._get_enum_entries(node)

    @staticmethod
    def _get_enum_entries(node: Any) -> list[str]:
        entries: list[str] = []

        try:
            node_entries: Iterable[Any] = node.entries
        except Exception:
            return entries

        for entry in node_entries:
            try:
                if not entry.is_available:
                    continue
            except Exception:
                pass

            try:
                entries.append(str(entry.symbolic))
            except Exception:
                continue

        return entries

    def _discover_timestamp_frequency(self) -> float | None:
        """Read a usable camera timestamp frequency, when exposed."""

        for name in (
            "GevTimestampTickFrequency",
            "TimestampTickFrequency",
        ):
            value = self._read_optional_feature(name)

            if value is None:
                continue

            try:
                frequency = float(value)
            except (TypeError, ValueError):
                continue

            if frequency > 0:
                return frequency

        return None

    def _require_open(self) -> None:
        if not self._is_open or self._image_acquirer is None:
            raise CameraStateError("Camera is not open.")

    def _feature_exists(self, name: str) -> bool:
        try:
            getattr(self.node_map, name)
            return True
        except Exception:
            return False

    def _any_feature_exists(self, *names: str) -> bool:
        return any(self._feature_exists(name) for name in names)

    def _read_optional_feature(self, name: str) -> Any | None:
        try:
            return getattr(self.node_map, name).value
        except Exception:
            return None

    def _first_optional_feature_as_string(
        self,
        *names: str,
    ) -> str | None:
        for name in names:
            value = self._read_optional_feature(name)

            if value is not None:
                return str(value)

        return None

    @staticmethod
    def _buffer_is_complete(buffer: Any) -> bool:
        try:
            value = buffer.is_complete
            return bool(value() if callable(value) else value)
        except Exception:
            return False

    @classmethod
    def _read_first_buffer_integer(
        cls,
        buffer: Any,
        *names: str,
    ) -> int | None:
        for name in names:
            value = cls._read_buffer_integer(buffer, name)

            if value is not None:
                return value

        return None

    @classmethod
    def _read_buffer_integer(
        cls,
        buffer: Any,
        name: str,
    ) -> int | None:
        value = cls._get_optional_attribute(buffer, name)

        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _get_optional_attribute(
        obj: Any,
        name: str,
    ) -> Any | None:
        try:
            value = getattr(obj, name)
            return value() if callable(value) else value
        except Exception:
            return None

    @staticmethod
    def _string_attribute(obj: Any, name: str) -> str:
        value = getattr(obj, name, "")
        return "" if value is None else str(value)

    @classmethod
    def _optional_string_attribute(
        cls,
        obj: Any,
        name: str,
    ) -> str | None:
        return cls._safe_optional_string(
            cls._get_optional_attribute(obj, name)
        )

    @staticmethod
    def _safe_optional_string(value: Any) -> str | None:
        if value is None:
            return None

        text = str(value)
        return text if text else None

    @staticmethod
    def _destroy_resources(
        image_acquirer: Any | None,
        harvester: Harvester | None,
    ) -> Exception | None:
        """Best-effort destruction, returning the first cleanup error."""

        first_error: Exception | None = None

        if image_acquirer is not None:
            try:
                image_acquirer.destroy()
            except Exception as exc:
                first_error = exc

        if harvester is not None:
            try:
                harvester.reset()
            except Exception as exc:
                if first_error is None:
                    first_error = exc

        return first_error
