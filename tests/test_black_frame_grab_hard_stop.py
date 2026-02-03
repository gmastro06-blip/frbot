from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from contracts.errors import PreflightFailed
from contracts.window import VerificationResult, WindowBindingAdapter, WindowBindingStatus, WindowRect


class _FakeGrabImage:
    def __init__(self, *, width: int, height: int, rgb: bytes) -> None:
        self.width = int(width)
        self.height = int(height)
        self.rgb = rgb


class _FakeMSS:
    def __init__(self, *, rgb: bytes) -> None:
        self._rgb = rgb
        self.monitors = [{"left": 0, "top": 0, "width": 3840, "height": 2160}]

    def grab(self, region: dict[str, int]) -> _FakeGrabImage:
        w = max(1, int(region.get('width', 1)))
        h = max(1, int(region.get('height', 1)))
        need = w * h * 3
        buf = (self._rgb * ((need // len(self._rgb)) + 1))[:need]
        return _FakeGrabImage(width=w, height=h, rgb=buf)


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


def test_grab_hard_stops_on_black_frame_and_writes_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    # Frame dumping is opt-in now.
    monkeypatch.setenv('FRBOT_DUMP_FRAMES', '1')

    fake_sct = _FakeMSS(rgb=b'\x00')
    fake_mss_mod = types.SimpleNamespace(mss=lambda: fake_sct)
    monkeypatch.setitem(__import__('sys').modules, 'mss', fake_mss_mod)

    binding = _FakeBinding(WindowRect(left=10, top=20, right=110, bottom=220))
    cap = MssBoundWindowRealCapture(binding=binding)

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

    with pytest.raises(PreflightFailed) as ei:
        cap.grab()

    assert str(ei.value) == 'capture_invalid'

    fatal = tmp_path / 'diagnostics' / 'fatal.log'
    assert fatal.exists()
    payload = json.loads(fatal.read_text(encoding='utf-8'))
    assert payload.get('reason') == 'capture_invalid'
    details = payload.get('details')
    assert isinstance(details, dict)
    assert details.get('hwnd') == 123
    assert details.get('foreground_hwnd') == 123
    assert 'region' in details
    # Luma stats are emitted for black/blocked capture diagnosis.
    assert 'luma_mean' in details
    assert 'luma_std' in details
    assert ('all_zero' in details) or ('rgb_all_zero' in details)

    # Mandatory frame dump evidence.
    frames_dir = tmp_path / 'diagnostics' / 'frames'
    assert frames_dir.exists()
    ppm_files = list(frames_dir.glob('capture_*_*.ppm'))
    assert ppm_files
