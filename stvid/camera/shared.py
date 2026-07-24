"""Shared-memory frame buffers for STVID acquisition processes.

This module provides a small abstraction around ``multiprocessing.Array`` and
``multiprocessing.Value`` objects used to exchange image cubes and per-frame
metadata between camera acquisition and FITS-writing processes.

The shared buffer preserves:

- image data;
- host timestamps in nanoseconds;
- raw camera timestamp ticks;
- camera frame IDs;
- validity flags for camera timestamps and frame IDs;
- the camera timestamp-counter frequency;
- a short code describing the timestamp-frequency source.

The image array layout follows the existing STVID convention:

    (height, width, frame_count)

The classes in this module do not depend on a particular camera backend.
"""

from __future__ import annotations

import ctypes
import multiprocessing
from dataclasses import dataclass
from typing import Any

import numpy as np


# Maximum number of UTF-8 bytes retained for the frequency-source description.
_TIMESTAMP_SOURCE_LENGTH = 128


@dataclass
class SharedFrameBufferAllocation:
    """Own the multiprocessing objects for one shared frame buffer.

    Instances of this class are passed to child processes. Use :meth:`map` in
    each process to obtain NumPy views onto the underlying shared memory.
    """

    image_base: Any
    host_timestamps_ns_base: Any
    camera_timestamps_base: Any
    frame_ids_base: Any
    camera_timestamp_valid_base: Any
    frame_id_valid_base: Any

    camera_timestamp_frequency_hz_base: Any
    camera_timestamp_frequency_valid_base: Any
    camera_timestamp_source_base: Any

    width: int
    height: int
    frame_count: int
    dtype_name: str

    @property
    def dtype(self) -> np.dtype:
        """Return the NumPy dtype used by the image cube."""

        return np.dtype(self.dtype_name)

    def map(self) -> "SharedFrameBufferView":
        """Create NumPy views onto this allocation."""

        return map_frame_buffer(self)


@dataclass
class SharedFrameBufferView:
    """NumPy views and scalar helpers for one shared frame buffer."""

    image: np.ndarray
    host_timestamps_ns: np.ndarray
    camera_timestamps: np.ndarray
    frame_ids: np.ndarray
    camera_timestamp_valid: np.ndarray
    frame_id_valid: np.ndarray

    camera_timestamp_frequency_hz_base: Any
    camera_timestamp_frequency_valid_base: Any
    camera_timestamp_source_base: Any

    def reset(self, *, clear_image: bool = False) -> None:
        """Reset per-frame metadata and optional image content.

        Parameters
        ----------
        clear_image
            Also zero the image cube. This is normally unnecessary because
            acquisition overwrites every frame before the buffer is queued.
        """

        if clear_image:
            self.image.fill(0)

        self.host_timestamps_ns.fill(0)
        self.camera_timestamps.fill(0)
        self.frame_ids.fill(0)
        self.camera_timestamp_valid.fill(False)
        self.frame_id_valid.fill(False)

    def set_camera_timestamp_frequency(
        self,
        frequency_hz: float | None,
        source: str | None = None,
    ) -> None:
        """Store timestamp-counter frequency and source metadata."""

        if frequency_hz is None:
            self.camera_timestamp_frequency_hz_base.value = 0.0
            self.camera_timestamp_frequency_valid_base.value = False
            self.camera_timestamp_source = ""
            return

        frequency_hz = float(frequency_hz)

        if frequency_hz <= 0:
            raise ValueError(
                "camera timestamp frequency must be greater than zero"
            )

        self.camera_timestamp_frequency_hz_base.value = frequency_hz
        self.camera_timestamp_frequency_valid_base.value = True
        self.camera_timestamp_source = source or ""

    @property
    def camera_timestamp_frequency_hz(self) -> float | None:
        """Return the stored timestamp-counter frequency, if valid."""

        if not self.camera_timestamp_frequency_valid_base.value:
            return None

        return float(
            self.camera_timestamp_frequency_hz_base.value
        )

    @property
    def camera_timestamp_source(self) -> str:
        """Return the stored timestamp-frequency source description."""

        raw_value = bytes(self.camera_timestamp_source_base[:])
        raw_value = raw_value.split(b"\0", 1)[0]

        return raw_value.decode(
            "utf-8",
            errors="replace",
        )

    @camera_timestamp_source.setter
    def camera_timestamp_source(self, value: str) -> None:
        encoded = value.encode(
            "utf-8",
            errors="replace",
        )

        maximum_payload = len(self.camera_timestamp_source_base) - 1
        encoded = encoded[:maximum_payload]

        self.camera_timestamp_source_base[:] = (
            b"\0" * len(self.camera_timestamp_source_base)
        )

        if encoded:
            self.camera_timestamp_source_base[:len(encoded)] = encoded

    def store_frame_metadata(
        self,
        index: int,
        *,
        host_timestamp_ns: int,
        camera_timestamp: int | None,
        frame_id: int | None,
    ) -> None:
        """Store timing and frame-identification metadata for one frame."""

        self._validate_index(index)

        host_timestamp_ns = int(host_timestamp_ns)

        if host_timestamp_ns < 0:
            raise ValueError(
                "host_timestamp_ns must not be negative"
            )

        self.host_timestamps_ns[index] = host_timestamp_ns

        if camera_timestamp is None:
            self.camera_timestamps[index] = 0
            self.camera_timestamp_valid[index] = False
        else:
            camera_timestamp = int(camera_timestamp)

            if camera_timestamp < 0:
                raise ValueError(
                    "camera_timestamp must not be negative"
                )

            self.camera_timestamps[index] = camera_timestamp
            self.camera_timestamp_valid[index] = True

        if frame_id is None:
            self.frame_ids[index] = 0
            self.frame_id_valid[index] = False
        else:
            frame_id = int(frame_id)

            if frame_id < 0:
                raise ValueError(
                    "frame_id must not be negative"
                )

            self.frame_ids[index] = frame_id
            self.frame_id_valid[index] = True

    def store_frame(
        self,
        index: int,
        image: np.ndarray,
        *,
        host_timestamp_ns: int,
        camera_timestamp: int | None,
        frame_id: int | None,
    ) -> None:
        """Copy an image and its metadata into one frame slot."""

        self._validate_index(index)

        expected_shape = self.image.shape[:2]

        if image.shape != expected_shape:
            raise ValueError(
                f"image shape {image.shape} does not match "
                f"shared-buffer frame shape {expected_shape}"
            )

        if image.dtype != self.image.dtype:
            raise ValueError(
                f"image dtype {image.dtype} does not match "
                f"shared-buffer dtype {self.image.dtype}"
            )

        self.image[:, :, index] = image

        self.store_frame_metadata(
            index,
            host_timestamp_ns=host_timestamp_ns,
            camera_timestamp=camera_timestamp,
            frame_id=frame_id,
        )

    def validate_complete(self) -> None:
        """Check that every frame slot has a host timestamp."""

        missing = np.flatnonzero(self.host_timestamps_ns == 0)

        if missing.size:
            preview = ", ".join(
                str(int(index))
                for index in missing[:10]
            )

            if missing.size > 10:
                preview += ", ..."

            raise RuntimeError(
                "shared frame buffer is incomplete; "
                f"missing host timestamps at indices {preview}"
            )

    def _validate_index(self, index: int) -> None:
        if index < 0 or index >= self.image.shape[2]:
            raise IndexError(
                f"frame index {index} is outside the valid range "
                f"0 to {self.image.shape[2] - 1}"
            )


def allocate_frame_buffer(
    width: int,
    height: int,
    frame_count: int,
    dtype: np.dtype | type[np.generic] | str,
    *,
    lock: bool = False,
) -> SharedFrameBufferAllocation:
    """Allocate one shared image cube and its per-frame metadata.

    Parameters
    ----------
    width
        Image width in pixels.
    height
        Image height in pixels.
    frame_count
        Number of frames stored in the cube.
    dtype
        NumPy image dtype. Integer dtypes are supported.
    lock
        Whether each ``multiprocessing.Array`` should carry its own lock.
        STVID's producer/consumer double-buffer design normally uses
        ``False`` because only one process owns a buffer at a time.
    """

    width = _validate_positive_integer(width, "width")
    height = _validate_positive_integer(height, "height")
    frame_count = _validate_positive_integer(
        frame_count,
        "frame_count",
    )

    image_dtype = np.dtype(dtype)
    ctype = _dtype_to_ctype(image_dtype)

    pixel_count = width * height * frame_count

    allocation = SharedFrameBufferAllocation(
        image_base=multiprocessing.Array(
            ctype,
            pixel_count,
            lock=lock,
        ),
        host_timestamps_ns_base=multiprocessing.Array(
            ctypes.c_uint64,
            frame_count,
            lock=lock,
        ),
        camera_timestamps_base=multiprocessing.Array(
            ctypes.c_uint64,
            frame_count,
            lock=lock,
        ),
        frame_ids_base=multiprocessing.Array(
            ctypes.c_uint64,
            frame_count,
            lock=lock,
        ),
        camera_timestamp_valid_base=multiprocessing.Array(
            ctypes.c_bool,
            frame_count,
            lock=lock,
        ),
        frame_id_valid_base=multiprocessing.Array(
            ctypes.c_bool,
            frame_count,
            lock=lock,
        ),
        camera_timestamp_frequency_hz_base=multiprocessing.Value(
            ctypes.c_double,
            0.0,
            lock=lock,
        ),
        camera_timestamp_frequency_valid_base=multiprocessing.Value(
            ctypes.c_bool,
            False,
            lock=lock,
        ),
        camera_timestamp_source_base=multiprocessing.Array(
            ctypes.c_char,
            _TIMESTAMP_SOURCE_LENGTH,
            lock=lock,
        ),
        width=width,
        height=height,
        frame_count=frame_count,
        dtype_name=image_dtype.str,
    )

    allocation.map().reset(clear_image=True)

    return allocation


def allocate_double_frame_buffer(
    width: int,
    height: int,
    frame_count: int,
    dtype: np.dtype | type[np.generic] | str,
    *,
    lock: bool = False,
) -> tuple[
    SharedFrameBufferAllocation,
    SharedFrameBufferAllocation,
]:
    """Allocate the two frame buffers used by STVID acquisition."""

    return (
        allocate_frame_buffer(
            width,
            height,
            frame_count,
            dtype,
            lock=lock,
        ),
        allocate_frame_buffer(
            width,
            height,
            frame_count,
            dtype,
            lock=lock,
        ),
    )


def map_frame_buffer(
    allocation: SharedFrameBufferAllocation,
) -> SharedFrameBufferView:
    """Map one allocation to process-local NumPy views."""

    image_dtype = allocation.dtype

    image = np.ctypeslib.as_array(
        _raw_shared_object(allocation.image_base)
    ).reshape(
        allocation.height,
        allocation.width,
        allocation.frame_count,
    )

    if image.dtype != image_dtype:
        image = image.view(image_dtype)

    host_timestamps_ns = np.ctypeslib.as_array(
        _raw_shared_object(
            allocation.host_timestamps_ns_base
        )
    )

    camera_timestamps = np.ctypeslib.as_array(
        _raw_shared_object(
            allocation.camera_timestamps_base
        )
    )

    frame_ids = np.ctypeslib.as_array(
        _raw_shared_object(allocation.frame_ids_base)
    )

    camera_timestamp_valid = np.ctypeslib.as_array(
        _raw_shared_object(
            allocation.camera_timestamp_valid_base
        )
    )

    frame_id_valid = np.ctypeslib.as_array(
        _raw_shared_object(
            allocation.frame_id_valid_base
        )
    )

    return SharedFrameBufferView(
        image=image,
        host_timestamps_ns=host_timestamps_ns,
        camera_timestamps=camera_timestamps,
        frame_ids=frame_ids,
        camera_timestamp_valid=camera_timestamp_valid,
        frame_id_valid=frame_id_valid,
        camera_timestamp_frequency_hz_base=(
            allocation.camera_timestamp_frequency_hz_base
        ),
        camera_timestamp_frequency_valid_base=(
            allocation.camera_timestamp_frequency_valid_base
        ),
        camera_timestamp_source_base=(
            allocation.camera_timestamp_source_base
        ),
    )


def _raw_shared_object(value: Any) -> Any:
    """Return the raw ctypes object from locked or lock-free wrappers."""

    get_obj = getattr(value, "get_obj", None)

    if callable(get_obj):
        return get_obj()

    return value


def _dtype_to_ctype(dtype: np.dtype) -> type[ctypes._SimpleCData]:
    """Map a supported NumPy dtype to its ctypes scalar type."""

    mapping = {
        np.dtype(np.uint8): ctypes.c_uint8,
        np.dtype(np.int8): ctypes.c_int8,
        np.dtype(np.uint16): ctypes.c_uint16,
        np.dtype(np.int16): ctypes.c_int16,
        np.dtype(np.uint32): ctypes.c_uint32,
        np.dtype(np.int32): ctypes.c_int32,
        np.dtype(np.uint64): ctypes.c_uint64,
        np.dtype(np.int64): ctypes.c_int64,
    }

    try:
        return mapping[dtype]
    except KeyError as exc:
        supported = ", ".join(
            str(item)
            for item in mapping
        )

        raise TypeError(
            f"unsupported shared image dtype {dtype}; "
            f"supported dtypes are {supported}"
        ) from exc


def _validate_positive_integer(
    value: int,
    name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be an integer"
        )

    if value <= 0:
        raise ValueError(
            f"{name} must be greater than zero"
        )

    return value
