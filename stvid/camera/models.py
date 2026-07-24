"""Backend-neutral data models for STVID camera acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray


ImageArray: TypeAlias = NDArray[np.generic]


@dataclass(frozen=True, slots=True)
class CameraInfo:
    """Basic identifying information for one camera."""

    vendor: str
    model: str
    serial_number: str
    device_id: str
    transport_type: str | None = None
    display_name: str | None = None
    firmware_version: str | None = None


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Generic camera configuration requested by STVID.

    A field set to ``None`` is left unchanged by the backend.

    Binning is expressed as an integer factor. Horizontal and vertical
    binning are independent:

        1 = no binning
        2 = two-pixel binning
        4 = four-pixel binning

    The backend translates these factors to the representation exposed by
    the camera, such as integer values or enumeration symbols.
    """

    exposure_us: float | None = None
    frame_rate_hz: float | None = None
    gain: float | None = None
    pixel_format: str | None = None

    width: int | None = None
    height: int | None = None
    offset_x: int | None = None
    offset_y: int | None = None

    binning_horizontal: int | None = None
    binning_vertical: int | None = None

    acquisition_mode: str | None = None
    trigger_mode: str | None = None
    trigger_source: str | None = None

    def __post_init__(self) -> None:
        """Reject configuration values that are invalid for every backend."""

        if self.exposure_us is not None and self.exposure_us <= 0:
            raise ValueError("exposure_us must be greater than zero.")

        if self.frame_rate_hz is not None and self.frame_rate_hz <= 0:
            raise ValueError("frame_rate_hz must be greater than zero.")

        for name, value in (
            ("width", self.width),
            ("height", self.height),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero.")

        for name, value in (
            ("offset_x", self.offset_x),
            ("offset_y", self.offset_y),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative.")

        for name, value in (
            ("binning_horizontal", self.binning_horizontal),
            ("binning_vertical", self.binning_vertical),
        ):
            if value is None:
                continue

            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer factor.")

            if value < 1:
                raise ValueError(f"{name} must be at least 1.")


@dataclass(frozen=True, slots=True)
class CameraCapabilities:
    """Generic capabilities discovered by a camera backend.

    Capability flags describe whether the backend can use a feature, not merely
    whether a similarly named node exists in a vendor node map.
    """

    pixel_formats: tuple[str, ...] = ()

    binning_horizontal_factors: tuple[int, ...] = ()
    binning_vertical_factors: tuple[int, ...] = ()

    supports_roi: bool = False
    supports_frame_rate_control: bool = False
    supports_gain_control: bool = False
    supports_trigger: bool = False
    supports_frame_id: bool = False
    supports_camera_timestamp: bool = False


@dataclass(frozen=True, slots=True)
class Frame:
    """One detached image and the metadata associated with its acquisition.

    ``host_timestamp_ns`` is retained for compatibility with the first GenTL
    backend. It should represent the best host-side estimate of frame time
    available to that backend. Future timing work can additionally populate
    the explicit fetch-boundary fields.

    Camera timestamps and frame IDs must remain ``None`` when the transport
    producer does not implement them. They must not be synthesized by a
    backend and presented as camera metadata.

    Packed transport formats may be exposed by Harvesters as unpacked NumPy
    arrays. Consequently, ``image.nbytes`` need not equal the transport
    payload size.
    """

    image: ImageArray

    frame_id: int | None
    camera_timestamp: int | None
    camera_timestamp_frequency_hz: float | None

    host_timestamp_ns: int
    complete: bool

    pixel_format: str | None = None

    # Optional timing diagnostics for a later timing-policy layer.
    host_fetch_start_monotonic_ns: int | None = None
    host_fetch_end_monotonic_ns: int | None = None
    host_wall_timestamp_ns: int | None = None

    @property
    def shape(self) -> tuple[int, ...]:
        """Return the NumPy image shape."""

        return self.image.shape

    @property
    def height(self) -> int:
        """Return image height in pixels."""

        self._require_two_dimensions()
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        """Return image width in pixels."""

        self._require_two_dimensions()
        return int(self.image.shape[1])

    @property
    def dtype(self) -> np.dtype:
        """Return the NumPy image dtype."""

        return self.image.dtype

    @property
    def dtype_name(self) -> str:
        """Return the canonical NumPy dtype name."""

        return self.image.dtype.name

    @property
    def size(self) -> int:
        """Return the number of array elements."""

        return int(self.image.size)

    @property
    def nbytes(self) -> int:
        """Return the in-memory size of the image array."""

        return int(self.image.nbytes)

    @property
    def camera_timestamp_seconds(self) -> float | None:
        """Convert the raw camera timestamp to seconds when possible."""

        if (
            self.camera_timestamp is None
            or self.camera_timestamp_frequency_hz is None
            or self.camera_timestamp_frequency_hz <= 0
        ):
            return None

        return (
            float(self.camera_timestamp)
            / self.camera_timestamp_frequency_hz
        )

    @property
    def host_fetch_midpoint_monotonic_ns(self) -> int | None:
        """Return the midpoint of the measured host fetch interval."""

        if (
            self.host_fetch_start_monotonic_ns is None
            or self.host_fetch_end_monotonic_ns is None
        ):
            return None

        return (
            self.host_fetch_start_monotonic_ns
            + self.host_fetch_end_monotonic_ns
        ) // 2

    @property
    def host_fetch_duration_ns(self) -> int | None:
        """Return measured host fetch duration, if both bounds are present."""

        if (
            self.host_fetch_start_monotonic_ns is None
            or self.host_fetch_end_monotonic_ns is None
        ):
            return None

        return (
            self.host_fetch_end_monotonic_ns
            - self.host_fetch_start_monotonic_ns
        )

    def _require_two_dimensions(self) -> None:
        if self.image.ndim < 2:
            raise ValueError(
                "Image array does not have at least two dimensions."
            )
