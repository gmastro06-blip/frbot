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
    def __init__(self) -> None:
        self.regions: list[dict[str, int]] = []
        # mss convention: monitors[0] is the virtual screen.
        self.monitors = [{"left": 0, "top": 0, "width": 3840, "height": 2160}]

    def grab(self, region: dict[str, int]) -> _FakeGrabImage:
        # record the region used so tests can assert per-grab rect derivation
        self.regions.append(dict(region))
        w = max(1, int(region.get('width', 1)))
        h = max(1, int(region.get('height', 1)))
        # Non-black buffer so grab() doesn't hard-stop.
        buf = (b"\x00\x00\x01" * (w * h))[: w * h * 3]
        return _FakeGrabImage(width=w, height=h, rgb=buf)


class _FakeBinding(WindowBindingAdapter):
    name = 'fake-binding'

    def __init__(self, rect: WindowRect) -> None:
        self._rect = rect

    def set_rect(self, rect: WindowRect) -> None:
        self._rect = rect

    def verify(self) -> VerificationResult:  # pragma: no cover
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

    # Stub win32 checks used by grab(). Keep invariant: foreground == expected.
    import adapters.capture.mss_bound_window_real as cap_mod

    monkeypatch.setattr(cap_mod.w32, 'is_window', lambda hwnd: True)
    monkeypatch.setattr(cap_mod.w32, 'get_foreground_window', lambda: 123)
    monkeypatch.setattr(cap_mod.w32, 'is_window_visible', lambda hwnd: True)
    monkeypatch.setattr(cap_mod.w32, 'is_window_minimized', lambda hwnd: False)
    monkeypatch.setattr(cap_mod.w32, 'get_window_process_id', lambda hwnd: 999)
    monkeypatch.setattr(cap_mod.w32, 'can_query_process', lambda pid: (True, None))
    monkeypatch.setattr(cap_mod.w32, 'get_dpi_awareness_status', lambda: {'attempted': True, 'mode': 'test', 'ok': True, 'error': None})
    monkeypatch.setattr(cap_mod.w32, 'get_client_rect_in_screen', lambda hwnd: binding._rect)
    monkeypatch.setattr(cap_mod.w32, 'get_window_rect_in_screen', lambda hwnd: binding._rect)

    cap.grab()

    binding.set_rect(WindowRect(left=30, top=40, right=130, bottom=240))
    cap.grab()

    assert fake_sct.regions[0] == {'left': 10, 'top': 20, 'width': 100, 'height': 200}
    assert fake_sct.regions[1] == {'left': 30, 'top': 40, 'width': 100, 'height': 200}
