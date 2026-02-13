from __future__ import annotations

from typing import Any

from adapters.window.win32 import Win32WindowBinding, _Bound
from contracts.window import WindowRect


def test_assert_bound_recovers_when_unbound(monkeypatch: Any) -> None:
    b = Win32WindowBinding(hwnd=123, title_substring="Tibia")

    monkeypatch.setattr("adapters.window.win32.is_window", lambda _hwnd: True)
    monkeypatch.setattr("adapters.window.win32.get_window_text", lambda _hwnd: "Tibia - Onniwabanshu")
    monkeypatch.setattr(
        "adapters.window.win32.get_client_rect_in_screen",
        lambda _hwnd: WindowRect(left=0, top=0, right=100, bottom=100),
    )
    monkeypatch.setattr("adapters.window.win32.find_window_by_title_substring", lambda _s: None)  # noqa: ARG005

    b.assert_bound()
    assert b._bound is not None
    assert int(b._bound.hwnd) == 123


def test_assert_bound_recovers_transient_is_window_false(monkeypatch: Any) -> None:
    b = Win32WindowBinding(hwnd=0, title_substring="Tibia")
    b._bound = _Bound(hwnd=111, title_substring="Tibia")

    calls = {"is_window": 0}

    class _Match:
        hwnd = 222

    def fake_is_window(hwnd: int) -> bool:
        calls["is_window"] += 1
        if int(hwnd) == 111:
            return False
        return int(hwnd) == 222

    monkeypatch.setenv("FRBOT_WINDOW_BINDING_RETRY_MS", "2000")
    monkeypatch.setattr("adapters.window.win32.is_window", fake_is_window)
    monkeypatch.setattr("adapters.window.win32.find_window_by_title_substring", lambda _s: _Match())  # noqa: ARG005
    monkeypatch.setattr("adapters.window.win32.get_window_text", lambda _hwnd: "Tibia - Onniwabanshu")  # noqa: ARG005
    monkeypatch.setattr(
        "adapters.window.win32.get_client_rect_in_screen",
        lambda _hwnd: WindowRect(left=0, top=0, right=100, bottom=100),  # noqa: ARG005
    )

    b.assert_bound()
    assert b._bound is not None
    assert int(b._bound.hwnd) == 222
    assert calls["is_window"] >= 2
