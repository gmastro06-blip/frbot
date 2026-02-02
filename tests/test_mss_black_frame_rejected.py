from __future__ import annotations

import types

import pytest

from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from contracts.window import VerificationResult, WindowBindingAdapter, WindowBindingStatus, WindowRect


class _FakeGrabImage:
    def __init__(self, *, width: int, height: int, rgb: bytes) -> None:
        self.width = int(width)
        self.height = int(height)
        self.rgb = rgb


class _FakeMSS:
    def __init__(self, *, rgb: bytes) -> None:
        self._rgb = rgb
        # mss convention: monitors[0] is the virtual screen.
        self.monitors = [{"left": 0, "top": 0, "width": 3840, "height": 2160}]

    def grab(self, region: dict[str, int]) -> _FakeGrabImage:
        w = max(1, int(region.get("width", 1)))
        h = max(1, int(region.get("height", 1)))
        # Return exactly w*h*3 bytes. Use the provided pattern repeated.
        if not self._rgb:
            buf = b"\x00" * (w * h * 3)
        else:
            pat = self._rgb
            need = w * h * 3
            buf = (pat * ((need // len(pat)) + 1))[:need]
        return _FakeGrabImage(width=w, height=h, rgb=buf)


class _FakeBinding(WindowBindingAdapter):
    name = "fake-binding"

    def __init__(self, rect: WindowRect) -> None:
        self._rect = rect

    def verify(self) -> VerificationResult:  # pragma: no cover
        raise NotImplementedError

    def assert_bound(self) -> None:  # pragma: no cover
        return None

    def snapshot(self) -> WindowBindingStatus:
        return WindowBindingStatus(backend="fake", verified=True, hwnd=123, rect=self._rect)


def test_verify_rejects_black_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sct = _FakeMSS(rgb=b"\x00")
    fake_mss_mod = types.SimpleNamespace(mss=lambda: fake_sct)
    monkeypatch.setitem(__import__("sys").modules, "mss", fake_mss_mod)

    binding = _FakeBinding(WindowRect(left=10, top=20, right=110, bottom=220))
    cap = MssBoundWindowRealCapture(binding=binding)

    v = cap.verify()
    assert not v.ok
    assert v.reason == "captured_frame_black"


def test_verify_allows_non_black_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    # Non-zero byte pattern.
    fake_sct = _FakeMSS(rgb=b"\x00\x00\x01")
    fake_mss_mod = types.SimpleNamespace(mss=lambda: fake_sct)
    monkeypatch.setitem(__import__("sys").modules, "mss", fake_mss_mod)

    binding = _FakeBinding(WindowRect(left=10, top=20, right=110, bottom=220))
    cap = MssBoundWindowRealCapture(binding=binding)

    v = cap.verify()
    assert v.ok
