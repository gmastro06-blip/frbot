from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
from typing import Any, Optional

from contracts.capture import Frame
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_frame_ppm
from models import Script, Waypoint, WaypointType, now_iso
from runtime.minimap_semantics import MarkerConfig, SemanticTracker, detect_player_marker
from runtime.pacing import wait_until_ns


def _sign(v: int) -> int:
    if int(v) > 0:
        return 1
    if int(v) < 0:
        return -1
    return 0


@dataclass(slots=True)
class RouteRecordingSession:
    script_name: str = 'recorded_route'
    default_z: int = 7
    simplify_straight_every: int = 3
    enabled: bool = True

    waypoints: list[Waypoint] = field(default_factory=list)

    _last_tile: Optional[tuple[int, int, int]] = None
    _last_dir: Optional[tuple[int, int]] = None
    _same_dir_steps: int = 0

    def _add_waypoint(self, *, waypoint_type: str, x: int, y: int, z: int, options: Optional[dict[str, Any]] = None) -> Waypoint:
        wp = Waypoint(
            type=str(waypoint_type),
            x=int(x),
            y=int(y),
            z=int(z),
            options=dict(options or {}),
            enabled=bool(self.enabled),
            created_at=now_iso(),
        )
        self.waypoints.append(wp)
        return wp

    def record_tile(self, x: int, y: int, z: Optional[int] = None) -> bool:
        z_val = int(self.default_z if z is None else z)
        cur = (int(x), int(y), int(z_val))

        if self._last_tile is None:
            self._add_waypoint(waypoint_type=WaypointType.WALK.value, x=cur[0], y=cur[1], z=cur[2])
            self._last_tile = cur
            self._last_dir = None
            self._same_dir_steps = 0
            return True

        if cur == self._last_tile:
            return False

        dx = int(cur[0]) - int(self._last_tile[0])
        dy = int(cur[1]) - int(self._last_tile[1])
        step_dir = (_sign(dx), _sign(dy))
        step_len = max(abs(int(dx)), abs(int(dy)))

        should_add = False
        if self._last_dir is None or step_dir != self._last_dir:
            should_add = True
            self._same_dir_steps = int(step_len)
        else:
            self._same_dir_steps += int(step_len)
            if int(self._same_dir_steps) >= max(1, int(self.simplify_straight_every)):
                should_add = True
                self._same_dir_steps = 0

        self._last_dir = step_dir
        self._last_tile = cur

        if not bool(should_add):
            return False

        self._add_waypoint(waypoint_type=WaypointType.WALK.value, x=cur[0], y=cur[1], z=cur[2])
        return True

    def mark_action(self, action: str) -> Waypoint:
        if self._last_tile is None:
            raise ValueError('route_recorder_no_position')

        x, y, z = self._last_tile
        a = str(action).strip().lower()
        if a in {'ladder', 'stairs_up', 'stairs_down'}:
            return self._add_waypoint(
                waypoint_type=WaypointType.USE_LADDER.value,
                x=x,
                y=y,
                z=z,
                options={'action_kind': a},
            )
        if a == 'rope':
            return self._add_waypoint(
                waypoint_type=WaypointType.ROPE.value,
                x=x,
                y=y,
                z=z,
                options={'action_kind': a},
            )
        if a in {'open_hole', 'shovel', 'pick'}:
            return self._add_waypoint(
                waypoint_type=WaypointType.USE_RIGHT_CLICK.value,
                x=x,
                y=y,
                z=z,
                options={'interaction': 'open_hole', 'action_kind': a},
            )
        raise ValueError(f'route_recorder_unknown_action:{a}')

    def build_script(self) -> Script:
        return Script(
            name=str(self.script_name),
            enabled=True,
            run_to_target=False,
            waypoints=list(self.waypoints),
            metadata={
                'recorded_at': now_iso(),
                'recorder': {
                    'simplify_straight_every': int(self.simplify_straight_every),
                    'default_z': int(self.default_z),
                },
            },
        )


@dataclass(slots=True)
class MinimapRouteSampler:
    marker_cfg: MarkerConfig
    pixels_per_tile: float = 1.0
    z: int = 7
    tracker: SemanticTracker | None = None

    def __post_init__(self) -> None:
        if self.tracker is None:
            self.tracker = SemanticTracker(pixels_per_tile=float(self.pixels_per_tile), z=int(self.z))

    def sample_tile(self, frame: Frame) -> Optional[tuple[int, int, int]]:
        det = detect_player_marker(frame, self.marker_cfg)
        if det is None or self.tracker is None:
            return None
        tile = self.tracker.observe_tile(det.pos)
        return (int(tile.x), int(tile.y), int(tile.z))


@dataclass(slots=True)
class WaypointRecorderStep:
    step_index: int
    action_kind: str
    key_or_click: str
    before_ppm: str
    after_ppm: str
    ts: str
    window_hwnd: int
    capture_source: str
    frame_size: dict[str, int]
    metrics: dict[str, Any]
    inputs_sent: int = 1


class WaypointRecorder:
    def __init__(
        self,
        *,
        capture: Any,
        input_adapter: Any,
        binding: Any,
        marker_cfg: MarkerConfig,
        out_dir: str | Path = "diagnostics/waypoints",
        max_steps: int = 500,
        after_poll_attempts: int = 12,
        after_poll_interval_ms: int = 80,
        max_marker_miss_streak: int = 6,
    ) -> None:
        self._capture = capture
        self._input = input_adapter
        self._binding = binding
        self._marker_cfg = marker_cfg
        self._out_dir = Path(out_dir)
        self._max_steps = max(1, int(max_steps))
        self._after_poll_attempts = max(1, int(after_poll_attempts))
        self._after_poll_interval_ms = max(1, int(after_poll_interval_ms))
        self._max_marker_miss_streak = max(1, int(max_marker_miss_streak))

        self._started = False
        self._paused = False
        self._session_meta: dict[str, Any] = {}
        self._steps: list[WaypointRecorderStep] = []
        self._marker_miss_streak = 0

        self._session_id = ""
        self._session_dir = Path(".")
        self._jsonl_path = Path(".")
        self._json_path = Path(".")

    @property
    def steps(self) -> list[WaypointRecorderStep]:
        return list(self._steps)

    @property
    def jsonl_path(self) -> Path:
        return self._jsonl_path

    @property
    def json_path(self) -> Path:
        return self._json_path

    def start(self, session_meta: dict[str, Any] | None = None) -> None:
        if self._started:
            raise RuntimeError("waypoint_recorder_already_started")

        self._session_id = now_iso().replace("-", "").replace(":", "").replace("+", "_").replace("T", "_")
        self._session_meta = dict(session_meta or {})
        self._steps = []
        self._marker_miss_streak = 0
        self._paused = False

        self._session_dir = self._out_dir / f"waypoints_{self._session_id}"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl_path = self._session_dir / f"waypoints_{self._session_id}.jsonl"
        self._json_path = self._session_dir / f"waypoints_{self._session_id}.json"

        self._append_jsonl(
            {
                "event": "session_start",
                "ts": now_iso(),
                "session_id": self._session_id,
                "session_meta": dict(self._session_meta),
            }
        )
        self._started = True

    def pause(self) -> None:
        self._ensure_started()
        self._paused = True
        self._append_jsonl({"event": "session_pause", "ts": now_iso(), "session_id": self._session_id})

    def resume(self) -> None:
        self._ensure_started()
        self._paused = False
        self._append_jsonl({"event": "session_resume", "ts": now_iso(), "session_id": self._session_id})

    def stop(self, save: bool = True) -> Path | None:
        self._ensure_started()
        self._append_jsonl({"event": "session_stop", "ts": now_iso(), "session_id": self._session_id, "steps": len(self._steps)})
        out_path: Path | None = None
        if save:
            payload = {
                "schema_version": 1,
                "session_id": self._session_id,
                "ts": now_iso(),
                "session_meta": dict(self._session_meta),
                "steps": [self._step_to_dict(s) for s in self._steps],
            }
            out_path = self._atomic_write_json(self._json_path, payload)
        self._started = False
        self._paused = False
        return out_path

    def record_move(self, direction_key: str) -> WaypointRecorderStep:
        key = str(direction_key or "").strip()
        direction = self._direction_from_key(key)
        return self._record(action_kind="move", key_or_click=key, expected_direction=direction)

    def record_action(self, action_kind: str, key_or_click: str) -> WaypointRecorderStep:
        a = str(action_kind or "").strip().lower()
        if a not in {"rope", "shovel", "pick", "stairs_up", "stairs_down"}:
            raise RuntimeError(f"waypoint_action_invalid:{a}")
        return self._record(action_kind=a, key_or_click=str(key_or_click or "").strip(), expected_direction=None)

    def _record(self, *, action_kind: str, key_or_click: str, expected_direction: str | None) -> WaypointRecorderStep:
        self._ensure_started()
        if self._paused:
            raise RuntimeError("waypoint_recorder_paused")
        if len(self._steps) >= int(self._max_steps):
            raise RuntimeError("waypoint_max_steps_reached")

        before_ppm = ""
        after_ppm = ""

        try:
            self._binding.assert_bound()
            before = self._capture.grab()
            before_det = detect_player_marker(before, self._marker_cfg)
            if before_det is None:
                self._marker_miss_streak += 1
                if self._marker_miss_streak >= int(self._max_marker_miss_streak):
                    raise RuntimeError("marker_not_found")
            else:
                self._marker_miss_streak = 0

            step_index = len(self._steps)
            before_ppm = self._frame_path(step_index=step_index, suffix="before")
            dump_frame_ppm(before, Path(before_ppm))

            self._input.assert_bound(None)
            self._send_one_input(key_or_click)

            after: Frame | None = None
            after_det = None
            for attempt in range(int(self._after_poll_attempts)):
                after_frame: Frame = self._capture.grab()
                after = after_frame
                after_det = detect_player_marker(after_frame, self._marker_cfg)
                if self._evidence_ok(action_kind=action_kind, expected_direction=expected_direction, before=before, after=after_frame, before_det=before_det, after_det=after_det):
                    break
                if attempt + 1 < int(self._after_poll_attempts):
                    wait_until_ns(int(before.monotonic_ts_ns + (attempt + 1) * int(self._after_poll_interval_ms) * 1_000_000))

            if after is None:
                raise RuntimeError("waypoint_record_failed")

            after_ppm = self._frame_path(step_index=step_index, suffix="after")
            dump_frame_ppm(after, Path(after_ppm))

            if not self._evidence_ok(action_kind=action_kind, expected_direction=expected_direction, before=before, after=after, before_det=before_det, after_det=after_det):
                raise RuntimeError("no_progress")

            metrics = self._metrics(before=before, after=after, before_det=before_det, after_det=after_det)
            window_hwnd = self._window_hwnd_or_zero()
            step = WaypointRecorderStep(
                step_index=int(step_index),
                action_kind=str(action_kind),
                key_or_click=str(key_or_click),
                before_ppm=str(Path(before_ppm).name),
                after_ppm=str(Path(after_ppm).name),
                ts=now_iso(),
                window_hwnd=int(window_hwnd),
                capture_source=str(os.environ.get("FRBOT_CAPTURE_SOURCE", "") or ""),
                frame_size={"width": int(after.width), "height": int(after.height)},
                metrics=metrics,
                inputs_sent=1,
            )
            self._steps.append(step)
            self._append_jsonl({"event": "step", **self._step_to_dict(step)})
            return step
        except Exception as exc:
            details: dict[str, Any] = {
                "reason": "waypoint_record_failed",
                "action_kind": str(action_kind),
                "key_or_click": str(key_or_click),
                "step_index": len(self._steps),
                "before_ppm": str(before_ppm),
                "after_ppm": str(after_ppm),
                "session_id": str(self._session_id),
                "jsonl_path": str(self._jsonl_path),
            }
            write_fatal(str(exc) if str(exc) else "waypoint_record_failed", exc, details=details)
            raise

    def _direction_from_key(self, key: str) -> str:
        k = str(key or "").strip().lower()
        mapping = {
            "up": "up",
            "w": "up",
            "down": "down",
            "s": "down",
            "left": "left",
            "a": "left",
            "right": "right",
            "d": "right",
        }
        if k not in mapping:
            raise RuntimeError(f"move_key_invalid:{k}")
        return str(mapping[k])

    def _send_one_input(self, key_or_click: str) -> None:
        payload = str(key_or_click or "").strip()
        if not payload:
            raise RuntimeError("input_missing")
        if payload.startswith("click:"):
            raw = payload.split(":", 1)[1]
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) != 2:
                raise RuntimeError("input_invalid")
            self._input.click(int(parts[0]), int(parts[1]))
            return
        self._input.press_key(payload)

    def _evidence_ok(self, *, action_kind: str, expected_direction: str | None, before: Frame, after: Frame, before_det: Any, after_det: Any) -> bool:
        if before_det is None or after_det is None:
            return False

        dx = float(after_det.pos.px) - float(before_det.pos.px)
        dy = float(after_det.pos.py) - float(before_det.pos.py)
        distance = math.hypot(dx, dy)
        floor_change = bool(int(before.height) == int(after.height) and str(before.minimap_digest_hex) != str(after.minimap_digest_hex))

        if action_kind == "move":
            if distance <= 0.5:
                return False
            if expected_direction == "left":
                return dx < -0.25
            if expected_direction == "right":
                return dx > 0.25
            if expected_direction == "up":
                return dy < -0.25
            if expected_direction == "down":
                return dy > 0.25
            return True

        if action_kind in {"stairs_up", "stairs_down"}:
            return floor_change

        return distance > 0.5 or floor_change

    def _metrics(self, *, before: Frame, after: Frame, before_det: Any, after_det: Any) -> dict[str, Any]:
        dx = None
        dy = None
        distance = None
        if before_det is not None and after_det is not None:
            dx = float(after_det.pos.px) - float(before_det.pos.px)
            dy = float(after_det.pos.py) - float(before_det.pos.py)
            distance = float(math.hypot(float(dx), float(dy)))
        floor_change = bool(str(before.minimap_digest_hex) != str(after.minimap_digest_hex))
        return {
            "marker_delta_px": {
                "dx": dx,
                "dy": dy,
                "distance": distance,
            },
            "floor_change_flag": bool(floor_change),
            "before_minimap_digest": str(before.minimap_digest_hex),
            "after_minimap_digest": str(after.minimap_digest_hex),
        }

    def _window_hwnd_or_zero(self) -> int:
        try:
            snap = self._binding.snapshot()
            return int(getattr(snap, "hwnd", 0) or 0)
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    try:
                        from runtime.error_policy import should_reraise

                        if should_reraise():
                            raise
                    except Exception:
                        pass
            return 0

    def _frame_path(self, *, step_index: int, suffix: str) -> str:
        name = f"step_{int(step_index):05d}_{str(suffix)}.ppm"
        return str(self._session_dir / name)

    def _append_jsonl(self, payload: dict[str, Any]) -> None:
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def _step_to_dict(self, step: WaypointRecorderStep) -> dict[str, Any]:
        return {
            "step_index": int(step.step_index),
            "action_kind": str(step.action_kind),
            "key_or_click": str(step.key_or_click),
            "before_ppm": str(step.before_ppm),
            "after_ppm": str(step.after_ppm),
            "ts": str(step.ts),
            "window_hwnd": int(step.window_hwnd),
            "capture_source": str(step.capture_source),
            "frame_size": dict(step.frame_size),
            "metrics": dict(step.metrics),
            "inputs_sent": int(step.inputs_sent),
        }

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("waypoint_recorder_not_started")
