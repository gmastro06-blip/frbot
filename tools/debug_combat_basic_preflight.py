from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _ensure_repo_root_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_root_on_syspath()

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.config_loader import load_rois
from runtime.combat_basic_preflight import run as combat_basic_preflight
from runtime.env import parse_window_hwnd_env
from runtime.capture_source import capture_source


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def main() -> int:
    cfg = RuntimeConfig(
        mode="real",
        tick_hz=float(_env_str("FRBOT_TICK_HZ", "20.0") or "20.0"),
        config_path=str(_env_str("FRBOT_CONFIG_PATH", "") or ""),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=True,
        minimap_roi=_env_str("FRBOT_MINIMAP_ROI", "minimap") or "minimap",
        window_hwnd=parse_window_hwnd_env("FRBOT_WINDOW_HWND"),
        window_title_substring=_env_str("FRBOT_WINDOW_TITLE", ""),
        target_frame_roi=_env_str("FRBOT_TARGET_FRAME_ROI", "target_frame") or "target_frame",
        target_hp_bar_roi=_env_str("FRBOT_TARGET_HP_BAR_ROI", "target_hp_bar") or "target_hp_bar",
        combat_cooldown_roi=_env_str("FRBOT_COMBAT_COOLDOWN_ROI", "combat_cooldown") or "combat_cooldown",
        combat_feedback_roi=_env_str("FRBOT_COMBAT_FEEDBACK_ROI", "combat_feedback") or "combat_feedback",
        attack_key=_env_str("FRBOT_ATTACK_KEY", "SPACE") or "SPACE",
        combat_target_hp_decrease_min=float(_env_str("FRBOT_COMBAT_BASIC_TARGET_HP_DECREASE_MIN", "0.02") or "0.02"),
        player_marker_rgb=_env_str("FRBOT_PLAYER_MARKER_RGB", "255,255,0"),
        player_marker_tol=int(_env_str("FRBOT_PLAYER_MARKER_TOL", "10") or "10"),
        player_marker_min_pixels=int(_env_str("FRBOT_PLAYER_MARKER_MIN_PIXELS", "3") or "3"),
    )

    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    loaded = load_rois(ctx)
    print(
        json.dumps(
            {
                "capture_source": capture_source(),
                "config_path": cfg.config_path,
                "loaded_frame": {"width": loaded.frame_width, "height": loaded.frame_height},
                "roi_keys": sorted(list(loaded.rois.keys())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    try:
        cap, inp, binding = combat_basic_preflight(ctx)
        print(json.dumps({"ok": True, "capture": cap.name, "input": inp.name}, ensure_ascii=False, indent=2))
        return 0
    except PreflightFailed as exc:
        details = getattr(exc, "details", None)
        print(json.dumps({"ok": False, "reason": str(exc), "details": details}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
