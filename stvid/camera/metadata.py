"""FITS metadata helpers for STVID camera frame timing.

This module writes camera-originated frame metadata into an Astropy FITS
header without depending on a particular camera backend or acquisition
process.

The existing STVID host-derived timing keywords can remain unchanged. This
module adds the exact raw camera values alongside them:

    CTFREQ   Camera timestamp-counter frequency in hertz
    CTUNIT   Unit of the CTnnnn values
    CTSOURCE Source of the timestamp-counter frequency
    CT0000   Raw camera timestamp for frame 0
    CT0001   Raw camera timestamp for frame 1
    ...
    CF0000   Camera frame ID for frame 0
    CF0001   Camera frame ID for frame 1
    ...

Validity arrays are used so that a legitimate zero timestamp or frame ID is
not confused with missing metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from astropy.io.fits import Header


_MAX_INDEXED_KEYWORDS = 10_000


def add_camera_frame_metadata(
    header: Header,
    *,
    camera_timestamps: Sequence[int] | np.ndarray,
    camera_timestamp_valid: Sequence[bool] | np.ndarray,
    frame_ids: Sequence[int] | np.ndarray,
    frame_id_valid: Sequence[bool] | np.ndarray,
    camera_timestamp_frequency_hz: float | None,
    camera_timestamp_source: str | None = None,
) -> None:
    """Add raw camera timing and frame-ID metadata to a FITS header.

    Parameters
    ----------
    header
        FITS header to modify in place.
    camera_timestamps
        Raw camera timestamp ticks, one value per frame.
    camera_timestamp_valid
        Boolean validity flags corresponding to ``camera_timestamps``.
    frame_ids
        Camera frame identifiers, one value per frame.
    frame_id_valid
        Boolean validity flags corresponding to ``frame_ids``.
    camera_timestamp_frequency_hz
        Frequency of the camera timestamp counter in hertz. Use ``None`` when
        unavailable.
    camera_timestamp_source
        Optional description of where the frequency came from, for example
        ``"configuration override"`` or
        ``"GenICam node GevTimestampTickFrequency"``.

    Notes
    -----
    This function deliberately stores raw integer ticks and frame IDs. It does
    not replace or reinterpret STVID's existing DATE-OBS, MJD-OBS, or DTnnnn
    host-timing keywords.
    """

    if not isinstance(header, Header):
        raise TypeError(
            "header must be an astropy.io.fits.Header"
        )

    camera_timestamps_array = _as_one_dimensional_array(
        camera_timestamps,
        "camera_timestamps",
    )
    camera_timestamp_valid_array = _as_boolean_array(
        camera_timestamp_valid,
        "camera_timestamp_valid",
    )
    frame_ids_array = _as_one_dimensional_array(
        frame_ids,
        "frame_ids",
    )
    frame_id_valid_array = _as_boolean_array(
        frame_id_valid,
        "frame_id_valid",
    )

    frame_count = len(camera_timestamps_array)

    _require_matching_length(
        camera_timestamp_valid_array,
        frame_count,
        "camera_timestamp_valid",
    )
    _require_matching_length(
        frame_ids_array,
        frame_count,
        "frame_ids",
    )
    _require_matching_length(
        frame_id_valid_array,
        frame_count,
        "frame_id_valid",
    )

    if frame_count > _MAX_INDEXED_KEYWORDS:
        raise ValueError(
            "camera metadata FITS keyword format supports at most "
            f"{_MAX_INDEXED_KEYWORDS} frames per file, not {frame_count}"
        )

    frequency_hz = _validate_optional_frequency(
        camera_timestamp_frequency_hz
    )

    if frequency_hz is not None:
        header["CTFREQ"] = (
            frequency_hz,
            "Camera timestamp frequency [Hz]",
        )
        header["CTUNIT"] = (
            "tick",
            "Unit of CTnnnn values",
        )

        source = _normalise_optional_text(
            camera_timestamp_source
        )

        if source:
            header["CTSOURCE"] = (
                source,
                "Camera timestamp frequency source",
            )

    valid_timestamp_count = int(
        np.count_nonzero(camera_timestamp_valid_array)
    )
    valid_frame_id_count = int(
        np.count_nonzero(frame_id_valid_array)
    )

    header["NCTVALID"] = (
        valid_timestamp_count,
        "Frames with valid camera timestamps",
    )
    header["NFIDVAL"] = (
        valid_frame_id_count,
        "Frames with valid camera frame IDs",
    )

    for index in range(frame_count):
        if camera_timestamp_valid_array[index]:
            timestamp = _validate_nonnegative_integer(
                camera_timestamps_array[index],
                f"camera_timestamps[{index}]",
            )

            header[f"CT{index:04d}"] = (
                timestamp,
                "Raw camera timestamp [tick]",
            )

        if frame_id_valid_array[index]:
            frame_id = _validate_nonnegative_integer(
                frame_ids_array[index],
                f"frame_ids[{index}]",
            )

            header[f"CF{index:04d}"] = (
                frame_id,
                "Camera frame ID",
            )


def camera_timestamp_seconds(
    camera_timestamps: Sequence[int] | np.ndarray,
    *,
    camera_timestamp_frequency_hz: float,
    reference_index: int = 0,
) -> np.ndarray:
    """Convert raw camera ticks to relative seconds.

    The returned array is relative to ``reference_index``. Raw integer
    subtraction is performed before conversion to floating point, preserving
    precision for large absolute timestamp counters.
    """

    timestamps = _as_one_dimensional_array(
        camera_timestamps,
        "camera_timestamps",
    )

    frequency_hz = _validate_optional_frequency(
        camera_timestamp_frequency_hz
    )

    if frequency_hz is None:
        raise ValueError(
            "camera_timestamp_frequency_hz is required"
        )

    if len(timestamps) == 0:
        return np.asarray([], dtype=np.float64)

    if reference_index < 0 or reference_index >= len(timestamps):
        raise IndexError(
            f"reference_index {reference_index} is outside the valid "
            f"range 0 to {len(timestamps) - 1}"
        )

    timestamps_uint64 = np.asarray(
        timestamps,
        dtype=np.uint64,
    )

    reference = timestamps_uint64[reference_index]

    # Use signed arithmetic after removing the large common counter value.
    relative_ticks = (
        timestamps_uint64.astype(object)
        - int(reference)
    )

    return np.asarray(
        relative_ticks,
        dtype=np.float64,
    ) / frequency_hz


def _as_one_dimensional_array(
    values: Sequence[Any] | np.ndarray,
    name: str,
) -> np.ndarray:
    array = np.asarray(values)

    if array.ndim != 1:
        raise ValueError(
            f"{name} must be one-dimensional, not shape {array.shape}"
        )

    return array


def _as_boolean_array(
    values: Sequence[bool] | np.ndarray,
    name: str,
) -> np.ndarray:
    array = _as_one_dimensional_array(values, name)

    return np.asarray(
        array,
        dtype=bool,
    )


def _require_matching_length(
    values: Sequence[Any] | np.ndarray,
    expected_length: int,
    name: str,
) -> None:
    if len(values) != expected_length:
        raise ValueError(
            f"{name} has length {len(values)}, "
            f"expected {expected_length}"
        )


def _validate_optional_frequency(
    frequency_hz: float | None,
) -> float | None:
    if frequency_hz is None:
        return None

    frequency_hz = float(frequency_hz)

    if not np.isfinite(frequency_hz):
        raise ValueError(
            "camera timestamp frequency must be finite"
        )

    if frequency_hz <= 0:
        raise ValueError(
            "camera timestamp frequency must be greater than zero"
        )

    return frequency_hz


def _validate_nonnegative_integer(
    value: Any,
    name: str,
) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(
            f"{name} must be an integer, not bool"
        )

    try:
        integer_value = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(
            f"{name} must be an integer"
        ) from exc

    try:
        if integer_value != value:
            raise ValueError(
                f"{name} must contain an exact integer value"
            )
    except TypeError:
        pass

    if integer_value < 0:
        raise ValueError(
            f"{name} must not be negative"
        )

    if integer_value > np.iinfo(np.int64).max:
        raise OverflowError(
            f"{name} exceeds the signed 64-bit FITS integer range"
        )

    return integer_value


def _normalise_optional_text(
    value: str | None,
) -> str:
    if value is None:
        return ""

    return " ".join(str(value).split())
