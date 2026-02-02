from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from adapters.windows import win32 as w32
from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair

# Reuse the exact same luma stats logic used by MELD diagnostics.
from adapters.capture.meld_real import _sample_luma_stats


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    try:
        return int(s)
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {"", "0", "false", "no", "off"}


def _hard_stop(reason: str, *, details: dict[str, object]) -> None:
    exc = PreflightFailed(reason)
    setattr(exc, "details", details)
    write_fatal(reason, exc, details=details)
    raise exc


def _crop_rgb(rgb: bytes, width: int, height: int, roi: Roi) -> bytes:
    if width <= 0 or height <= 0:
        return b""
    if roi.width <= 0 or roi.height <= 0:
        return b""
    if roi.x < 0 or roi.y < 0:
        return b""
    if (roi.x + roi.width) > width or (roi.y + roi.height) > height:
        return b""

    row_stride = width * 3
    out = bytearray(roi.width * roi.height * 3)
    out_row_stride = roi.width * 3
    for row in range(roi.height):
        src_start = ((roi.y + row) * row_stride) + (roi.x * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = rgb[src_start:src_end]
    return bytes(out)


class CamMinimapRealCapture(CaptureAdapter):
    """REAL capture from a camera-like device (DirectShow).

    Intended uses:
    - OBS Virtual Camera (use OBS "Game Capture" to hook Tibia)
    - HDMI capture dongle/card

    Security model:
    - Still enforces the foreground HWND invariant (Tibia must be foreground).
    - Additionally requires minimap evidence + player marker (handled by preflight).

    Notes:
    - ROI coordinates are interpreted in the camera frame's pixel space.
      For best results, configure OBS to output ONLY the Tibia client area
      (no borders/letterboxing) so ROIs match your existing config.
    """

    name = "cam"

    def __init__(self, minimap_roi: Roi, *, binding: WindowBindingAdapter) -> None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError("missing dependency: opencv-python") from exc

        self._cv2 = cv2
        self._np = np
        self._cap: Any | None = None

        self._binding = binding
        self._minimap_roi = minimap_roi

        # Device selection.
        self._device_index = int(_env_int("FRBOT_CAM_DEVICE_INDEX", 0) or 0)
        # Use DirectShow on Windows by default.
        self._backend = str(os.environ.get("FRBOT_CAM_BACKEND", "dshow") or "dshow").strip().lower()

        # Optional tuning.
        self._req_w = _env_int("FRBOT_CAM_WIDTH")
        self._req_h = _env_int("FRBOT_CAM_HEIGHT")
        self._req_fps = _env_int("FRBOT_CAM_FPS")

    def _open(self) -> Any:
        if self._cap is not None:
            return self._cap

        api = self._cv2.CAP_ANY
        if self._backend in {"dshow", "directshow"}:
            api = self._cv2.CAP_DSHOW
        elif self._backend in {"msmf"}:
            api = self._cv2.CAP_MSMF

        cap = self._cv2.VideoCapture(int(self._device_index), int(api))
        if not cap or not cap.isOpened():
            raise RuntimeError(f"camera device {self._device_index} not openable")

        # Best-effort properties (may be ignored by device/driver).
        if self._req_w is not None:
            cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, float(self._req_w))
        if self._req_h is not None:
            cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, float(self._req_h))
        if self._req_fps is not None:
            cap.set(self._cv2.CAP_PROP_FPS, float(self._req_fps))

        self._cap = cap
        return self._cap

    def _close(self) -> None:
        try:
            if self._cap is not None:
                self._cap.release()
        finally:
            self._cap = None

    def _read_bgr(self) -> Any:
        cap = self._open()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("failed to read camera frame")
        return frame

    def _bgr_to_rgb_bytes(self, bgr: Any) -> tuple[bytes, int, int]:
        # bgr is a numpy ndarray (H, W, 3) BGR.
        h, w = bgr.shape[:2]
        if int(w) <= 0 or int(h) <= 0:
            return b"", 0, 0
        # Convert BGR -> RGB. Copy to ensure contiguous bytes.
        rgb = bgr[:, :, ::-1]
        try:
            rgb_c = self._np.ascontiguousarray(rgb)
        except Exception:
            rgb_c = rgb
        return bytes(rgb_c.tobytes()), int(w), int(h)

    def _assert_hwnd_foreground(self) -> tuple[int, int]:
        snap = self._binding.snapshot()
        hwnd = int(getattr(snap, "hwnd", 0) or 0)
        fg = 0
        try:
            fg = int(w32.get_foreground_window())
        except Exception:
            fg = 0

        if hwnd <= 0 or not w32.is_window(hwnd):
            _hard_stop(
                "window_hwnd_invalid",
                details={"hwnd": hwnd, "foreground_hwnd": fg, "dpi_awareness": w32.get_dpi_awareness_status()},
            )
        if fg != hwnd:
            _hard_stop("window_not_foreground", details={"hwnd": hwnd, "foreground_hwnd": fg})
        if not w32.is_window_visible(hwnd):
            _hard_stop("window_not_visible", details={"hwnd": hwnd, "foreground_hwnd": fg})
        if w32.is_window_minimized(hwnd):
            _hard_stop("window_minimized", details={"hwnd": hwnd, "foreground_hwnd": fg})

        return hwnd, fg

    def verify(self) -> VerificationResult:
        # Must not throw; return ok=False.
        try:
            self._assert_hwnd_foreground()

            # Read a few frames and check variance.
            cap = self._open()
            _ = cap  # keep for clarity
            last_rgb = b""
            w = 0
            h = 0
            for _i in range(5):
                bgr = self._read_bgr()
                rgb, w, h = self._bgr_to_rgb_bytes(bgr)
                last_rgb = rgb
                time.sleep(0.03)

            if not last_rgb or w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason="frame_empty")

            _mean, std, all_zero = _sample_luma_stats(last_rgb, width=int(w), height=int(h))
            if all_zero or std <= 5.0:
                return VerificationResult(ok=False, reason="capture_black_or_unavailable")

            # Minimapa ROI must be within frame bounds.
            mm = _crop_rgb(last_rgb, int(w), int(h), self._minimap_roi)
            if not mm:
                return VerificationResult(ok=False, reason="minimap_not_detected")

            return VerificationResult(ok=True)
        except PreflightFailed:
            # Propagate as a failed verification reason.
            return VerificationResult(ok=False, reason="capture_black_or_unavailable")
        except Exception as exc:
            return VerificationResult(ok=False, reason=f"capture verify failed: {type(exc).__name__}: {exc}")
        finally:
            # Keep the device open for runtime; don't close here.
            pass

    def grab(self) -> Frame:
        hwnd, fg = self._assert_hwnd_foreground()

        ts_ns = int(time.monotonic_ns())
        bgr = self._read_bgr()
        rgb, w, h = self._bgr_to_rgb_bytes(bgr)
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ""

        if not rgb or w <= 0 or h <= 0:
            _hard_stop(
                "frame_empty",
                details={"hwnd": hwnd, "foreground_hwnd": fg, "device_index": int(self._device_index)},
            )

        _mean, std, all_zero = _sample_luma_stats(rgb, width=int(w), height=int(h))
        if all_zero or std <= 2.0:
            failing = Frame(width=int(w), height=int(h), monotonic_ts_ns=ts_ns, digest_hex=str(digest), rgb=rgb)
            # Re-grab for before/after evidence.
            try:
                bgr2 = self._read_bgr()
                rgb2, w2, h2 = self._bgr_to_rgb_bytes(bgr2)
                ts2 = int(time.monotonic_ns())
                dig2 = hashlib.sha256(rgb2).hexdigest() if rgb2 else ""
                after = Frame(width=int(w2), height=int(h2), monotonic_ts_ns=ts2, digest_hex=str(dig2), rgb=rgb2)
            except Exception:
                after = None

            reason = "black_frame_capture"
            dump_pair(gate="capture", before=failing, after=after, reason=reason)
            _hard_stop(
                reason,
                details={
                    "hwnd": hwnd,
                    "foreground_hwnd": fg,
                    "device_index": int(self._device_index),
                    "frame_size": [int(w), int(h)],
                    "std_luma": float(std),
                    "all_zero": bool(all_zero),
                },
            )

        minimap_rgb = _crop_rgb(rgb, int(w), int(h), self._minimap_roi)
        minimap_detected = bool(minimap_rgb)
        minimap_digest = hashlib.sha256(minimap_rgb).hexdigest() if minimap_rgb else ""

        return Frame(
            width=int(w),
            height=int(h),
            monotonic_ts_ns=int(ts_ns),
            digest_hex=str(digest),
            rgb=rgb,
            minimap_detected=bool(minimap_detected),
            minimap_rgb=minimap_rgb,
            minimap_width=int(self._minimap_roi.width),
            minimap_height=int(self._minimap_roi.height),
            minimap_digest_hex=str(minimap_digest),
        )
