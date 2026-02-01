from __future__ import annotations

import types

import pytest

from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from contracts.window import WindowBindingStatus, WindowRect


class _FakeGrabImage:
    def __init__(self, *, width: int, height: int, rgb: bytes) -> None:
        self.width = int(width)
        self.height = int(height)
        self.rgb = rgb


class _FakeMSS:
    def __init__(self) -> None:
        self.regions: list[dict[str, int]] = []

    def grab(self, region: dict[str, int]) -> _FakeGrabImage:
        # record the region used so tests can assert per-grab rect derivation
        self.regions.append(dict(region))
        # Always return a valid buffer for sha256; dims can be arbitrary.
        return _FakeGrabImage(width=max(1, int(region.get('width', 1))), height=max(1, int(region.get('height', 1))), rgb=b"\x00\x00\x00")


class _FakeBinding:
    name = 'fake-binding'

    def __init__(self, rect: WindowRect) -> None:
        self._rect = rect

    def set_rect(self, rect: WindowRect) -> None:
        self._rect = rect

    def verify(self):  # pragma: no cover
        raise NotImplementedError

    def assert_bound(self) -> None:  # pragma: no cover
        return None

    def snapshot(self) -> WindowBindingStatus:
        return WindowBindingStatus(backend='fake', verified=True, hwnd=123, rect=self._rect)


def test_hwnd_bound_capture_uses_latest_binding_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sct = _FakeMSS()

    fake_mss_mod = types.SimpleNamespace(mss=lambda: fake_sct)
    monkeypatch.setitem(__import__('sys').modules, 'mss', fake_mss_mod)

    binding = _FakeBinding(WindowRect(left=10, top=20, right=110, bottom=220))
    cap = MssBoundWindowRealCapture(binding=binding)

    cap.grab()

    binding.set_rect(WindowRect(left=30, top=40, right=130, bottom=240))
    cap.grab()

    assert fake_sct.regions[0] == {'left': 10, 'top': 20, 'width': 100, 'height': 200}
    assert fake_sct.regions[1] == {'left': 30, 'top': 40, 'width': 100, 'height': 200}
