from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from contracts.capture import Frame
from runtime.minimap_semantics import MarkerConfig, marker_config_from_env
from runtime.route_recorder import WaypointRecorder


@dataclass
class _Snap:
    hwnd: int = 123


class _Binding:
    def assert_bound(self) -> None:
        return

    def snapshot(self) -> _Snap:
        return _Snap(hwnd=123)


class _Input:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def assert_bound(self, hwnd: int | None = None) -> None:
        return

    def press_key(self, key: str) -> None:
        self.pressed.append(str(key))

    def click(self, x: int, y: int) -> None:
        self.pressed.append(f"click:{int(x)},{int(y)}")


def _mk_frame(*, marker_x: int, marker_y: int, digest: str) -> Frame:
    w = 16
    h = 16
    rgb = bytearray(w * h * 3)
    mm = bytearray(w * h * 3)

    for y in range(h):
        for x in range(w):
            idx = (y * w + x) * 3
            rgb[idx] = 24
            rgb[idx + 1] = 24
            rgb[idx + 2] = 24
            mm[idx] = 0
            mm[idx + 1] = 0
            mm[idx + 2] = 0

    for y in range(max(0, marker_y - 1), min(h, marker_y + 2)):
        for x in range(max(0, marker_x - 1), min(w, marker_x + 2)):
            idx = (y * w + x) * 3
            mm[idx] = 255
            mm[idx + 1] = 0
            mm[idx + 2] = 255

    return Frame(
        width=w,
        height=h,
        monotonic_ts_ns=1,
        digest_hex=str(digest),
        rgb=bytes(rgb),
        minimap_detected=True,
        minimap_rgb=bytes(mm),
        minimap_width=w,
        minimap_height=h,
        minimap_digest_hex=str(digest),
    )


class _Capture:
    def __init__(self, frames: list[Frame]) -> None:
        self._frames = list(frames)
        self._idx = 0

    def grab(self) -> Frame:
        if self._idx >= len(self._frames):
            return self._frames[-1]
        out = self._frames[self._idx]
        self._idx += 1
        return out


@pytest.fixture()
def _cfg() -> MarkerConfig:
    return marker_config_from_env("255,0,255", "20", "3", "0", "0.10", "4.0")


def test_start_stop_creates_json_and_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _cfg: MarkerConfig) -> None:
    monkeypatch.chdir(tmp_path)
    capture = _Capture([_mk_frame(marker_x=6, marker_y=6, digest="a"), _mk_frame(marker_x=8, marker_y=6, digest="b")])
    rec = WaypointRecorder(capture=capture, input_adapter=_Input(), binding=_Binding(), marker_cfg=_cfg, out_dir=tmp_path / "diagnostics" / "waypoints")

    rec.start({"source": "test"})
    rec.record_move("d")
    out = rec.stop(save=True)

    assert out is not None
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["steps"]) == 1
    assert data["steps"][0]["before_ppm"].endswith("before.ppm")
    assert data["steps"][0]["after_ppm"].endswith("after.ppm")
    assert rec.jsonl_path.exists()


def test_record_move_sets_inputs_sent_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _cfg: MarkerConfig) -> None:
    monkeypatch.chdir(tmp_path)
    inp = _Input()
    capture = _Capture([_mk_frame(marker_x=4, marker_y=4, digest="a"), _mk_frame(marker_x=6, marker_y=4, digest="b")])
    rec = WaypointRecorder(capture=capture, input_adapter=inp, binding=_Binding(), marker_cfg=_cfg, out_dir=tmp_path / "diagnostics" / "waypoints")

    rec.start({})
    step = rec.record_move("d")

    assert step.inputs_sent == 1
    assert step.action_kind == "move"
    assert inp.pressed == ["d"]


def test_record_actions_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _cfg: MarkerConfig) -> None:
    monkeypatch.chdir(tmp_path)
    capture = _Capture([
        _mk_frame(marker_x=4, marker_y=4, digest="a"),
        _mk_frame(marker_x=5, marker_y=4, digest="b"),
        _mk_frame(marker_x=5, marker_y=4, digest="c"),
        _mk_frame(marker_x=6, marker_y=4, digest="d"),
        _mk_frame(marker_x=6, marker_y=4, digest="e"),
        _mk_frame(marker_x=7, marker_y=4, digest="f"),
    ])
    rec = WaypointRecorder(capture=capture, input_adapter=_Input(), binding=_Binding(), marker_cfg=_cfg, out_dir=tmp_path / "diagnostics" / "waypoints")

    rec.start({})
    s1 = rec.record_action("rope", "F8")
    s2 = rec.record_action("shovel", "F9")
    s3 = rec.record_action("pick", "F10")

    assert s1.action_kind == "rope"
    assert s2.action_kind == "shovel"
    assert s3.action_kind == "pick"


def test_failure_writes_fatal_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _cfg: MarkerConfig) -> None:
    monkeypatch.chdir(tmp_path)
    before = _mk_frame(marker_x=4, marker_y=4, digest="same")
    after = _mk_frame(marker_x=4, marker_y=4, digest="same")
    capture = _Capture([before, after, after, after, after, after, after, after, after, after, after, after, after])

    rec = WaypointRecorder(capture=capture, input_adapter=_Input(), binding=_Binding(), marker_cfg=_cfg, out_dir=tmp_path / "diagnostics" / "waypoints")
    rec.start({})

    with pytest.raises(RuntimeError):
        rec.record_move("d")

    fatal = tmp_path / "diagnostics" / "fatal.log"
    assert fatal.exists()
    payload = json.loads(fatal.read_text(encoding="utf-8"))
    assert payload.get("reason") in {"no_progress", "waypoint_record_failed", "marker_not_found"}
