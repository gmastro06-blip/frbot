import json
import time
from pathlib import Path
from typing import Any, cast
from PySide6.QtWidgets import QApplication
from ui_main import MainWindow

app = QApplication.instance() or QApplication([])
w = MainWindow()
result: dict[str, Any] = {"ts": int(time.time()), "steps": [], "errors": []}
try:
    def _capture_error(title: str, msg: str) -> None:
        result["errors"].append(f"ui_error:{title}:{msg}")
    cast(Any, w)._show_error = _capture_error
    result["steps"].append("route_start")
    w._on_route_start_clicked()
    app.processEvents()
    for _ in range(6):
        w._on_route_tick()
        app.processEvents()
    result["route_recording_active_after_start"] = bool(w._route_recording_active)

    result["steps"].append("route_mark_actions")
    if int(len(getattr(getattr(w, "_route_session", None), "waypoints", []) or [])) > 0:
        for action in ["rope", "shovel", "pick", "stairs_up", "stairs_down"]:
            try:
                w._on_route_mark_action(action)
                app.processEvents()
            except Exception as exc:
                result["errors"].append(f"mark_{action}:{type(exc).__name__}:{exc}")
    else:
        result["errors"].append("route_actions_skipped:no_position")

    result["steps"].append("route_apply_export")
    try:
        w._on_route_apply_clicked()
        app.processEvents()
    except Exception as exc:
        result["errors"].append(f"apply:{type(exc).__name__}:{exc}")

    try:
        w._on_route_export_clicked()
        app.processEvents()
    except Exception as exc:
        result["errors"].append(f"export:{type(exc).__name__}:{exc}")

    result["route_status"] = str(w.lbl_route_status.text())
    result["route_counts"] = str(w.lbl_route_counts.text())
    result["waypoints_in_script"] = int(len(getattr(w._script, "waypoints", []) or []))

    result["steps"].append("cavebot_start_poll_stop")
    try:
        w._on_cavebot_start_clicked()
        app.processEvents()
        for _ in range(8):
            w._on_cavebot_tick()
            app.processEvents()
            time.sleep(0.2)
        result["cavebot_status_after_poll"] = str(w.lbl_cavebot_status.text())
        result["cavebot_runtime_after_poll"] = str(w.lbl_cavebot_runtime.text())
    except Exception as exc:
        result["errors"].append(f"cavebot_start_poll:{type(exc).__name__}:{exc}")

    try:
        w._on_cavebot_stop_clicked()
        app.processEvents()
        result["cavebot_status_after_stop"] = str(w.lbl_cavebot_status.text())
    except Exception as exc:
        result["errors"].append(f"cavebot_stop:{type(exc).__name__}:{exc}")
finally:
    try:
        w.close()
        app.processEvents()
    except Exception:
        pass

out_dir = Path("diagnostics") / f"ui_guided_run_{int(time.time())}"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "ui_guided_result.json"
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"UI_GUIDED_RESULT={out_path}")
print(json.dumps(result, ensure_ascii=False))
