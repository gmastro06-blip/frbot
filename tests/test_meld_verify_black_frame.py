from __future__ import annotations

import types

import pytest

from adapters.capture.meld_real import MeldBoundMinimapRealCapture
from contracts.window import VerificationResult, WindowBindingAdapter, WindowBindingStatus, WindowRect
from contracts.evidence import Roi


class _FakeFrame:
    def __init__(self, w: int, h: int, c: int, b: bytes) -> None:
        self.shape = (h, w, c)
        self._b = b

    def tobytes(self) -> bytes:
        return self._b


class _FakeCam:
    def __init__(self, frame: _FakeFrame) -> None:
        self._frame = frame

    def grab(self, region: object | None = None) -> _FakeFrame:
        return self._frame


class _FakeBinding(WindowBindingAdapter):
    name = 'fake-binding'

    def __init__(self, rect: WindowRect) -> None:
        self._rect = rect

    def verify(self) -> VerificationResult:  # pragma: no cover
        raise NotImplementedError

    def assert_bound(self) -> None:  # pragma: no cover
        return None

    def snapshot(self) -> WindowBindingStatus:
        return WindowBindingStatus(backend='fake', verified=True, hwnd=123, rect=self._rect)


def test_meld_verify_fails_on_black_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    # 10x10 RGB all zeros.
    raw = b'\x00' * (10 * 10 * 3)
    frame = _FakeFrame(10, 10, 3, raw)
    cam = _FakeCam(frame)

    fake_dxcam = types.SimpleNamespace(create=lambda output_idx=0: cam)
    monkeypatch.setitem(__import__('sys').modules, 'dxcam', fake_dxcam)

    import adapters.capture.meld_real as meld_mod

    monkeypatch.setattr(meld_mod.w32, 'get_foreground_window', lambda: 123)
    monkeypatch.setattr(meld_mod.w32, 'get_client_rect_in_screen', lambda hwnd: WindowRect(left=0, top=0, right=10, bottom=10))

    binding = _FakeBinding(WindowRect(left=0, top=0, right=10, bottom=10))
    cap = MeldBoundMinimapRealCapture(minimap_roi=Roi(name='minimap', x=0, y=0, width=1, height=1), binding=binding)

    v = cap.verify()
    assert not v.ok
    assert v.reason == 'capture_black_or_unavailable'
