from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from adapters.capture.obs_source_real import ObsSourceRealCapture, list_obs_input_names
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from importlib import import_module
from runtime.config_loader import load_rois
from runtime.minimap_semantics import detect_player_marker, marker_config_from_env

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _build_ctx(config_path: str) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode="real",
        tick_hz=20.0,
        config_path=str(config_path or "").strip(),
        bot_config_path=str(_env_str("FRBOT_BOT_CONFIG_PATH", "") or ""),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        minimap_roi=str(_env_str("FRBOT_MINIMAP_ROI", "minimap") or "minimap"),
        player_marker_rgb=str(_env_str("FRBOT_PLAYER_MARKER_RGB", "255,0,255") or "255,0,255"),
        player_marker_tol=int(_env_str("FRBOT_PLAYER_MARKER_TOL", "30") or "30"),
        player_marker_min_pixels=int(_env_str("FRBOT_PLAYER_MARKER_MIN_PIXELS", "5") or "5"),
        player_marker_max_pixels=int(_env_str("FRBOT_PLAYER_MARKER_MAX_PIXELS", "0") or "0"),
        player_marker_min_fill_ratio=float(_env_str("FRBOT_PLAYER_MARKER_MIN_FILL_RATIO", "0.15") or "0.15"),
        player_marker_max_aspect_ratio=float(_env_str("FRBOT_PLAYER_MARKER_MAX_ASPECT_RATIO", "4.0") or "4.0"),
        window_hwnd=0,
        window_title_substring="",
    )
    return RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())


def _candidate_config_paths(raw: str) -> list[Path]:
    preferred = Path(raw).expanduser() if raw else Path()
    candidates = [
        preferred,
        Path("rois_prod_emergency.json"),
        Path("config/rois_prod_full.json"),
        Path("rois_prod_full.json"),
        Path("rois_prod.json"),
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        if str(p).strip() == "":
            continue
        rp = p if p.is_absolute() else (Path.cwd() / p).resolve()
        key = str(rp).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(rp)
    return out


def _top_minimap_colors(rgb: bytes, *, top_n: int = 8) -> list[dict[str, Any]]:
    if not rgb:
        return []
    counts: dict[tuple[int, int, int], int] = {}
    for i in range(0, len(rgb), 3):
        r = int(rgb[i])
        g = int(rgb[i + 1])
        b = int(rgb[i + 2])
        q = (r // 8 * 8, g // 8 * 8, b // 8 * 8)
        counts[q] = int(counts.get(q, 0)) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(top_n))]
    out: list[dict[str, Any]] = []
    for (r, g, b), c in ranked:
        out.append({"rgb": f"{int(r)},{int(g)},{int(b)}", "count": int(c)})
    return out


def run_diagnosis(explicit_config_path: str = "") -> dict[str, Any]:
    report: dict[str, Any] = {
        "ts": _now_ts(),
        "ok": False,
        "env": {
            "FRBOT_CAPTURE_SOURCE": _env_str("FRBOT_CAPTURE_SOURCE", ""),
            "FRBOT_OBS_SOURCE_NAME": _env_str("FRBOT_OBS_SOURCE_NAME", ""),
            "FRBOT_OBS_WS_HOST": _env_str("FRBOT_OBS_WS_HOST", "127.0.0.1"),
            "FRBOT_OBS_WS_PORT": _env_str("FRBOT_OBS_WS_PORT", "4455"),
            "FRBOT_OBS_WS_PASSWORD_set": bool(_env_str("FRBOT_OBS_WS_PASSWORD", "")),
            "FRBOT_OBS_WS_TIMEOUT_S": _env_str("FRBOT_OBS_WS_TIMEOUT_S", "2.0"),
            "FRBOT_CONFIG_PATH": _env_str("FRBOT_CONFIG_PATH", ""),
            "FRBOT_PROFILE": _env_str("FRBOT_PROFILE", ""),
            "FRBOT_MINIMAP_ROI": _env_str("FRBOT_MINIMAP_ROI", "minimap"),
            "FRBOT_PLAYER_MARKER_RGB": _env_str("FRBOT_PLAYER_MARKER_RGB", "255,0,255"),
        },
        "checks": [],
    }

    def add_check(name: str, ok: bool, **extra: Any) -> None:
        item: dict[str, Any] = {"name": name, "ok": bool(ok)}
        item.update(extra)
        report["checks"].append(item)

    try:
        inputs = list_obs_input_names()
        add_check("obs_ws_inputs", bool(inputs), count=len(inputs), inputs=inputs)
    except Exception as exc:
        add_check("obs_ws_inputs", False, error=f"{type(exc).__name__}:{exc}")
        inputs = []

    configured_source = _env_str("FRBOT_OBS_SOURCE_NAME", "")
    selected_source = configured_source
    if not selected_source and inputs:
        selected_source = str(inputs[0])
    add_check(
        "obs_source_selected",
        bool(selected_source),
        configured=configured_source,
        selected=selected_source,
        source_in_inputs=(selected_source in inputs if selected_source else False),
    )

    config_path_used = ""
    loaded = None
    load_error = ""
    chosen_raw = str(explicit_config_path or _env_str("FRBOT_CONFIG_PATH", "")).strip()
    for candidate in _candidate_config_paths(chosen_raw):
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            ctx = _build_ctx(str(candidate))
            loaded = load_rois(ctx)
            config_path_used = str(candidate)
            add_check(
                "roi_config_load",
                True,
                config_path=config_path_used,
                roi_count=len(loaded.rois),
                frame_width=loaded.frame_width,
                frame_height=loaded.frame_height,
                computed_sha=loaded.computed_sha,
            )
            break
        except Exception as exc:
            load_error = f"{type(exc).__name__}:{exc}"
            continue

    if loaded is None:
        add_check(
            "roi_config_load",
            False,
            selected_raw=chosen_raw,
            error=(load_error or "config_invalid_schema_or_missing"),
        )
        report["summary"] = {
            "reason": "roi_config_load_failed",
            "action": "Asegura FRBOT_CONFIG_PATH absoluto o usa config/rois_prod_full.json con schema valido.",
        }
        return report

    if not selected_source:
        report["summary"] = {
            "reason": "obs_source_not_found",
            "action": "Configura FRBOT_OBS_SOURCE_NAME con una fuente de video existente en OBS.",
        }
        return report

    try:
        capture = ObsSourceRealCapture(
            obs_source_name=str(selected_source),
            expected_width=int(loaded.frame_width or 0),
            expected_height=int(loaded.frame_height or 0),
            rois=dict(loaded.rois),
            minimap_roi_name=str(_env_str("FRBOT_MINIMAP_ROI", "minimap") or "minimap"),
        )
    except Exception as exc:
        add_check("capture_init", False, error=f"{type(exc).__name__}:{exc}")
        report["summary"] = {
            "reason": "capture_init_failed",
            "action": "Revisa frame width/height en config y nombre de fuente OBS.",
        }
        return report

    add_check("capture_init", True, source=selected_source)

    try:
        vr = capture.verify()
        add_check("capture_verify", bool(vr.ok), reason=str(vr.reason or ""))
        if not bool(vr.ok):
            report["summary"] = {
                "reason": str(vr.reason or "capture_verify_failed"),
                "action": "OBS responde pero el screenshot no cumple validaciones (contenido/ROI/resolucion).",
            }
            return report
    except Exception as exc:
        detail = getattr(exc, "details", None)
        add_check("capture_verify", False, error=f"{type(exc).__name__}:{exc}", details=detail)
        report["summary"] = {
            "reason": str(exc),
            "action": "Fallo en OBS WS o contenido de fuente; valida fuente visible y tamaño esperado.",
        }
        return report

    try:
        frame = capture.grab()
    except Exception as exc:
        detail = getattr(exc, "details", None)
        add_check("capture_grab", False, error=f"{type(exc).__name__}:{exc}", details=detail)
        report["summary"] = {
            "reason": str(exc),
            "action": "La fuente existe pero la imagen no pasa validacion (luma/ROI/resolucion).",
        }
        return report

    add_check(
        "capture_grab",
        True,
        frame_width=int(frame.width),
        frame_height=int(frame.height),
        minimap_detected=bool(frame.minimap_detected),
        minimap_width=int(frame.minimap_width),
        minimap_height=int(frame.minimap_height),
        luma_std=float(getattr(capture, "last_luma_std", 0.0) or 0.0),
        all_zero=bool(getattr(capture, "last_all_zero", False)),
    )

    cfg = marker_config_from_env(
        str(_env_str("FRBOT_PLAYER_MARKER_RGB", "255,0,255") or "255,0,255"),
        str(_env_str("FRBOT_PLAYER_MARKER_TOL", "30") or "30"),
        str(_env_str("FRBOT_PLAYER_MARKER_MIN_PIXELS", "5") or "5"),
        str(_env_str("FRBOT_PLAYER_MARKER_MAX_PIXELS", "0") or "0"),
        str(_env_str("FRBOT_PLAYER_MARKER_MIN_FILL_RATIO", "0.15") or "0.15"),
        str(_env_str("FRBOT_PLAYER_MARKER_MAX_ASPECT_RATIO", "4.0") or "4.0"),
    )
    det = detect_player_marker(frame, cfg)
    marker_ok = det is not None
    add_check(
        "minimap_marker_detect",
        marker_ok,
        marker_rgb=str(_env_str("FRBOT_PLAYER_MARKER_RGB", "255,0,255")),
        detected=(None if det is None else {"x": int(det.pos.px), "y": int(det.pos.py), "pixels": int(det.pixel_count)}),
    )

    if not marker_ok:
        hint_colors = _top_minimap_colors(frame.minimap_rgb, top_n=10)
        add_check("minimap_marker_hint", True, candidate_rgb=hint_colors)
        report["summary"] = {
            "reason": "minimap_player_not_found",
            "action": "OBS captura bien, pero el marcador no coincide. Recalibra FRBOT_PLAYER_MARKER_RGB/TOL o usa scripts/calibrate_minimap_marker.py.",
            "source": selected_source,
            "config_path": config_path_used,
        }
        return report

    report["ok"] = True
    report["summary"] = {
        "reason": "ok",
        "action": "OBS source y captura validados. Si route recorder no mueve, calibrar marcador minimap.",
        "source": selected_source,
        "config_path": config_path_used,
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnostica pipeline OBS source -> captura -> minimap -> marcador.")
    ap.add_argument("--config", default="", help="Path de config ROI; por defecto usa FRBOT_CONFIG_PATH y fallbacks conocidos.")
    ap.add_argument("--out", default="", help="Path de salida JSON (opcional).")
    args = ap.parse_args()

    report = run_diagnosis(explicit_config_path=str(args.config or "").strip())

    out_path = Path(args.out).expanduser() if str(args.out or "").strip() else Path("diagnostics") / f"obs_source_diagnosis_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(out_path))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if bool(report.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
