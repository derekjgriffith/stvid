"""Backend-neutral camera interface used by STVID acquisition code.

This module intentionally contains no operating-system, GenTL, Harvesters,
OpenCV, Raspberry Pi, or vendor-specific imports. Platform-specific resource
management belongs in the individual camera backend modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Optional, Type

from .models import (
    CameraCapabilities,
    CameraConfig,
    CameraInfo,
    Frame,
)


class CameraError(RuntimeError):
    """Base exception for all camera-related failures."""


class CameraStateError(CameraError):
    """Raised when an operation is invalid in the camera's current state."""


class CameraTimeoutError(CameraError):
    """Raised when no frame arrives before the requested timeout."""


class CameraConfigurationError(CameraError):
    """Raised when requested camera configuration cannot be applied."""


class CameraFrameError(CameraError):
    """Raised when a received frame or image payload is invalid."""


class Camera(ABC):
    """Abstract interface implemented by all STVID camera backends.

    The lifecycle is:

        open() -> configure() -> start() -> get_frame() ... -> stop() -> close()

    ``open()``, ``start()``, ``stop()``, and ``close()`` should be idempotent
    where practical. A backend must return detached frame data from
    :meth:`get_frame`; callers must not depend on a transport buffer remaining
    valid after the method returns.
    """

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return ``True`` when the camera and its transport are open."""

    @property
    @abstractmethod
    def is_acquiring(self) -> bool:
        """Return ``True`` while image acquisition is active."""

    @property
    def image_shape(self) -> Optional[tuple[int, int]]:
        """Return the active image shape as ``(height, width)`` if known.

        Backends may override this property after configuration. ``None`` is
        permitted before the camera is open or when the backend cannot report
        the active dimensions.
        """

        return None

    @property
    def image_dtype(self) -> Optional[str]:
        """Return the NumPy dtype name expected from frames, if known.

        Typical values are ``"uint8"`` for Mono8 and ``"uint16"`` for
        unpacked 10-, 12-, or 16-bit monochrome data. Backends may override
        this property; ``None`` means that the dtype has not yet been resolved.
        """

        return None

    @abstractmethod
    def open(self) -> None:
        """Open the selected camera and allocate backend resources."""

    @abstractmethod
    def close(self) -> None:
        """Stop acquisition if necessary and release all backend resources."""

    @abstractmethod
    def get_info(self) -> CameraInfo:
        """Return identifying information for the open camera."""

    @abstractmethod
    def get_capabilities(self) -> CameraCapabilities:
        """Return generic capabilities reported by the camera."""

    @abstractmethod
    def configure(self, config: CameraConfig) -> None:
        """Apply camera configuration while acquisition is stopped."""

    @abstractmethod
    def start(self) -> None:
        """Start continuous or triggered image acquisition."""

    @abstractmethod
    def stop(self) -> None:
        """Stop image acquisition without closing the camera."""

    @abstractmethod
    def get_frame(self, timeout_s: float = 1.0) -> Frame:
        """Fetch and return one detached frame.

        Parameters
        ----------
        timeout_s
            Maximum time to wait for a frame, in seconds. Backends should
            reject non-positive values and translate transport-specific timeout
            exceptions to :class:`CameraTimeoutError`.

        Returns
        -------
        Frame
            Image data and all metadata available for that frame. Missing
            camera frame IDs or camera timestamps should be represented by
            ``None`` rather than synthesized inside the backend.
        """

    def __enter__(self) -> "Camera":
        """Open the camera for use in a context manager."""

        self.open()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> bool:
        """Close the camera when leaving a context manager.

        Returning ``False`` ensures that an exception raised inside the
        ``with`` block is not suppressed.
        """

        self.close()
        return False
