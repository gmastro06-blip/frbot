import time
import hashlib
import numpy as np
from dataclasses import dataclass
from typing import Any, Optional

from runtime.pacing import sleep_ms


# =========================
# Data contracts
# =========================

@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    timestamp_ms: int
    digest_hex: str
    width: int
    height: int


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str


# =========================
# HDMI Capture Adapter
# =========================

class HdmiCaptureReal:
    """
    Real HDMI capture via VideoCapture.
    NO fallback. If this fails → abort.
    """

    def __init__(self, device_index: int = 0, expected_min_fps: int = 5):
        self.device_index = device_index
        self.expected_min_fps = expected_min_fps
        # NOTE: cv2 is imported lazily to keep this module import-safe in CI/Linux.
        self.cap: Optional[Any] = None
        self._last_frame_ts: Optional[int] = None

    def _cv2(self) -> Any:
        try:
            import cv2  # type: ignore

            return cv2
        except Exception as exc:
            # Common in headless Linux CI: opencv wheel can exist but fail to import
            # due to missing libGL/libgtk dependencies.
            raise RuntimeError(f"opencv_unavailable: {type(exc).__name__}: {exc}") from exc

    # ---------- Verification ----------

    def verify(self) -> VerificationResult:
        cv2 = self._cv2()
        cap = cv2.VideoCapture(self.device_index, cv2.CAP_ANY)

        if not cap.isOpened():
            return VerificationResult(
                ok=False,
                reason=f"HDMI device index {self.device_index} not openable"
            )

        # Try to read several frames to validate stability
        frames = []
        timestamps = []

        for _ in range(10):
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                return VerificationResult(
                    ok=False,
                    reason="Failed to read frame from HDMI capture"
                )

            ts = int(time.time() * 1000)
            frames.append(frame)
            timestamps.append(ts)
            sleep_ms(50.0)

        cap.release()

        # Basic sanity checks
        h, w = frames[0].shape[:2]
        if w < 320 or h < 240:
            return VerificationResult(
                ok=False,
                reason=f"Invalid resolution {w}x{h}"
            )

        # FPS estimation
        duration_ms = timestamps[-1] - timestamps[0]
        fps = len(frames) / (duration_ms / 1000.0) if duration_ms > 0 else 0

        if fps < self.expected_min_fps:
            return VerificationResult(
                ok=False,
                reason=f"FPS too low: {fps:.2f}"
            )

        # Check entropy (black screen detection)
        gray = cv2.cvtColor(frames[-1], cv2.COLOR_BGR2GRAY)
        gray_f = np.asarray(gray, dtype=np.float32)
        std = float(np.std(gray_f))

        if std < 5.0:
            return VerificationResult(
                ok=False,
                reason="Frame variance too low (black/frozen screen)"
            )

        return VerificationResult(ok=True, reason="HDMI capture verified")

    # ---------- Runtime ----------

    def open(self) -> None:
        cv2 = self._cv2()
        self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_ANY)
        if not self.cap.isOpened():
            raise RuntimeError("Failed to open HDMI capture device")

    def grab(self) -> Frame:
        if self.cap is None:
            raise RuntimeError("Capture device not opened")

        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError("Failed to grab HDMI frame")

        ts = int(time.time() * 1000)

        # Detect stale frames
        if self._last_frame_ts is not None:
            if ts - self._last_frame_ts > 2000:
                raise RuntimeError("HDMI capture stalled (>2s gap)")

        self._last_frame_ts = ts

        h, w = frame.shape[:2]

        digest = hashlib.sha256(frame.tobytes()).hexdigest()

        return Frame(
            image=frame,
            timestamp_ms=ts,
            digest_hex=digest,
            width=w,
            height=h
        )

    def close(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None
