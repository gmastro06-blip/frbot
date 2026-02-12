from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Mapping


STATUS_REPO_JSON_REL: Final[str] = "diagnostics/status_repo.json"
WINDOW_DIAGNOSTICS_JSON_REL: Final[str] = "diagnostics/window_diagnostics.json"

EXIT_READY: Final[int] = 0
EXIT_NOT_READY: Final[int] = 2
EXIT_NOT_OPERATIONAL: Final[int] = 3


@dataclass(frozen=True)
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        out = (self.stdout or "").rstrip()
        err = (self.stderr or "").rstrip()
        if not err:
            return out
        if not out:
            return err
        return out + "\n[stderr]\n" + err


@dataclass(frozen=True)
class VisibleWindow:
    hwnd: int
    title: str
    minimized: bool
    rect_left: int
    rect_top: int
    rect_right: int
    rect_bottom: int
    z_order: int

    @property
    def area(self) -> int:
        w = max(0, int(self.rect_right) - int(self.rect_left))
        h = max(0, int(self.rect_bottom) - int(self.rect_top))
        return int(w * h)


def _rects_overlap(a: VisibleWindow, b: VisibleWindow) -> bool:
    left = max(int(a.rect_left), int(b.rect_left))
    top = max(int(a.rect_top), int(b.rect_top))
    right = min(int(a.rect_right), int(b.rect_right))
    bottom = min(int(a.rect_bottom), int(b.rect_bottom))
    return (right > left) and (bottom > top)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _tail_lines(text: str, *, max_lines: int = 60) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines).rstrip()
    return "\n".join(lines[-max_lines:]).rstrip()


def _normalize_output(text: str) -> str:
    # Keep reports stable-ish across runs by removing volatile durations.
    # Example: "119 passed in 10.87s" -> "119 passed in <time>s"
    return re.sub(r"\bin\s+\d+(?:\.\d+)?s\b", "in <time>s", text or "")


def _run_cmd(
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout_s: int = 0,
) -> CmdResult:
    p = subprocess.run(
        argv,
        cwd=str(cwd),
        env=(dict(os.environ) | dict(env)) if env is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=(timeout_s if timeout_s and timeout_s > 0 else None),
    )
    return CmdResult(argv=list(argv), returncode=int(p.returncode), stdout=str(p.stdout or ""), stderr=str(p.stderr or ""))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", errors="replace")
    tmp.replace(path)


def _parse_final_decision(output: str) -> str:
    # Must be robust against substring collisions like OPERATIONAL_REAL in NOT_OPERATIONAL_REAL.
    # We only accept an explicit FINAL DECISION line.
    decision = ""
    for line in (output or "").splitlines():
        m = re.match(r"^\s*FINAL\s+DECISION:\s*(\S+)\s*$", line)
        if m:
            decision = str(m.group(1)).strip()
    return decision


def _parse_int_hwnd(raw: str) -> int:
    s = (raw or "").strip()
    if not s:
        return 0
    # Common placeholders.
    if s.lower().startswith("0x") and len(s) > 2 and set(s[2:].lower()) == {"x"}:
        return 0
    if s.strip().lower() in {"0xyourhwnd", "yourhwnd", "0x<yourhwnd>"}:
        return 0
    try:
        v = int(s, 0)
    except Exception:
        return -1
    return int(v)


def _default_frames_dir(repo_root: Path, env: Mapping[str, str]) -> Path:
    raw = str(env.get("FRBOT_REAL_FRAMES_DIR", "") or "").strip()
    if raw:
        return Path(raw)
    prof = str(env.get("FRBOT_PROFILE", "") or "").strip().lower()
    if prof == "prod_full":
        return repo_root / "diagnostics" / "frames_full"
    if prof == "prod_emergency":
        return repo_root / "diagnostics" / "frames_emergency"
    return repo_root / "diagnostics" / "frames"


def _default_config_path(repo_root: Path, env: Mapping[str, str]) -> Path:
    raw = str(env.get("FRBOT_CONFIG_PATH", "") or "").strip()
    if raw:
        return Path(raw)
    return repo_root / "config" / "rois_prod_full.json"


def _list_visible_windows_real() -> tuple[list[VisibleWindow], dict[str, Any]]:
    """List visible windows (Windows-only).

    Returns (windows, diag_meta). Never raises.
    """

    if not _is_windows():
        return [], {"ok": False, "reason": "unsupported_platform", "platform": str(sys.platform)}

    try:
        from adapters.windows.win32 import list_visible_windows_diagnostic, monitor_info_to_dict, window_diag_to_dict
    except Exception as exc:
        return [], {"ok": False, "reason": "win32_import_failed", "error": f"{type(exc).__name__}: {exc}"}

    try:
        win_diags, monitors = list_visible_windows_diagnostic()
        windows: list[VisibleWindow] = []
        for w in win_diags:
            d = window_diag_to_dict(w)
            r_obj = d.get("rect")
            r: dict[str, Any] = r_obj if isinstance(r_obj, dict) else {}
            windows.append(
                VisibleWindow(
                    hwnd=int(w.hwnd),
                    title=str(w.title),
                    minimized=bool(w.minimized),
                    rect_left=int(r.get("left", 0)),
                    rect_top=int(r.get("top", 0)),
                    rect_right=int(r.get("right", 0)),
                    rect_bottom=int(r.get("bottom", 0)),
                    z_order=int(getattr(w, "z_order", 0)),
                )
            )
        meta = {
            "ok": True,
            "monitors": [monitor_info_to_dict(m) for m in monitors],
            "windows": [window_diag_to_dict(w) for w in win_diags],
        }
        return windows, meta
    except Exception as exc:
        return [], {"ok": False, "reason": "win32_enumeration_failed", "error": f"{type(exc).__name__}: {exc}"}


def _choose_best_window(candidates: list[VisibleWindow]) -> VisibleWindow | None:
    if not candidates:
        return None
    # Prefer largest area; tie-break by z-order (smaller is closer to top), then hwnd.
    return sorted(candidates, key=lambda w: (-int(w.area), int(w.z_order), int(w.hwnd)))[0]


def _resolve_effective_hwnd(
    *,
    env: Mapping[str, str],
    windows: list[VisibleWindow],
    can_validate_hwnd: bool,
) -> tuple[int | None, dict[str, Any], list[str]]:
    blockers: list[str] = []

    hwnd_raw = str(env.get("FRBOT_WINDOW_HWND", "") or "").strip()
    title_raw = str(env.get("FRBOT_WINDOW_TITLE", "") or "").strip()

    selection: dict[str, Any] = {
        "requested_hwnd_raw": hwnd_raw,
        "requested_title": title_raw,
        "effective_hwnd": None,
        "resolved_title": "",
        "match_kind": "",
        "reason": "",
    }

    hwnd_parsed = _parse_int_hwnd(hwnd_raw)
    if hwnd_parsed < 0:
        # Invalid parse is treated as unset; title-based self-heal may still resolve.
        hwnd_parsed = 0
        selection["reason"] = "FRBOT_WINDOW_HWND inválido (se intentará resolver por título)"

    # Always require a visible + not-minimized hwnd.
    def _is_hwnd_ok(hwnd: int) -> bool:
        for w in windows:
            if int(w.hwnd) == int(hwnd):
                return bool(not w.minimized)
        return False

    # 1) Explicit HWND wins.
    # If we cannot validate via enumeration, accept the provided HWND to let downstream
    # auditors (prod_full) do the real validation.
    if int(hwnd_parsed) > 0 and not bool(can_validate_hwnd):
        selection.update(
            {
                "effective_hwnd": int(hwnd_parsed),
                "match_kind": "explicit_hwnd_unverified",
                "reason": "HWND provisto (sin enumeración de ventanas)",
            }
        )
        blockers = [b for b in blockers if b != "window_hwnd_invalid"]
        return int(hwnd_parsed), selection, blockers

    if int(hwnd_parsed) > 0 and _is_hwnd_ok(int(hwnd_parsed)):
        selection.update({"effective_hwnd": int(hwnd_parsed), "match_kind": "explicit_hwnd", "reason": "HWND válido y visible"})
        return int(hwnd_parsed), selection, blockers

    # 2) If explicit HWND is set but not valid, attempt self-heal by title once.
    if int(hwnd_parsed) > 0 and not _is_hwnd_ok(int(hwnd_parsed)):
        selection["reason"] = "FRBOT_WINDOW_HWND no visible/minimizado; se intentará resolver por título"

    if not title_raw:
        if int(hwnd_parsed) <= 0:
            blockers.append("window_selector_missing")
            selection["reason"] = selection.get("reason") or "Falta FRBOT_WINDOW_HWND o FRBOT_WINDOW_TITLE"
        else:
            blockers.append("window_invalid_state")
            selection["reason"] = selection.get("reason") or "HWND no visible / minimizado"
        return None, selection, blockers

    # 3) Resolve by exact title match.
    title_norm = title_raw.strip()
    exact = [w for w in windows if (not w.minimized) and w.title == title_norm]
    best = _choose_best_window(exact)
    if best is not None:
        selection.update(
            {
                "effective_hwnd": int(best.hwnd),
                "resolved_title": str(best.title),
                "match_kind": "title_exact",
                "reason": "Auto-resolución por título exacto",
            }
        )
        # Self-heal clears selector blockers.
        blockers = [b for b in blockers if b not in {"window_selector_missing", "window_invalid_state", "window_hwnd_invalid"}]
        return int(best.hwnd), selection, blockers

    # 4) Resolve by substring match.
    needle = title_norm.lower()
    subs = [w for w in windows if (not w.minimized) and (needle in w.title.lower())]
    best2 = _choose_best_window(subs)
    if best2 is not None:
        selection.update(
            {
                "effective_hwnd": int(best2.hwnd),
                "resolved_title": str(best2.title),
                "match_kind": "title_substring",
                "reason": "Auto-resolución por título (substring)",
            }
        )
        blockers = [b for b in blockers if b not in {"window_selector_missing", "window_invalid_state", "window_hwnd_invalid"}]
        return int(best2.hwnd), selection, blockers

    # Distinguish between "title exists but window state invalid" and "no match".
    exact_any = [w for w in windows if w.title == title_norm]
    subs_any = [w for w in windows if (needle in w.title.lower())]
    if any(bool(w.minimized) for w in (exact_any or subs_any)):
        blockers.append("window_invalid_state")
        selection["reason"] = "La ventana encontrada está minimizada"
    else:
        blockers.append("window_invalid_state")
        selection["reason"] = "No se encontró una ventana visible que coincida con FRBOT_WINDOW_TITLE"
    return None, selection, blockers


def evaluate_go_no_go(
    *,
    root_blockers: list[str],
    prod_full_final_decision: str,
    repo_dirty: bool,
    pytest_ok: bool,
) -> str:
    """Reglas puras para el veredicto global.

    Este auditor se limita a validar *readiness* del entorno REAL
    (variables/selector/config/capture). No ejecuta gates ni tests.

    Nota: se mantienen los parámetros `prod_full_final_decision/repo_dirty/pytest_ok`
    por compatibilidad con versiones anteriores, pero no afectan la decisión.
    """

    _ = (prod_full_final_decision, repo_dirty, pytest_ok)

    missing_or_invalid = {
        "unsupported_platform",
        "profile_not_prod_full",
        "obs_source_missing",
        "obs_source_mismatch",
        "obs_source_name_missing",
        "window_selector_missing",
        "window_invalid_state",
        "window_not_foreground",
        "window_hwnd_invalid",
        "config_missing",
        "frames_dir_missing",
        "internal_error",
    }

    if any(b in missing_or_invalid for b in root_blockers):
        return "NOT_READY"

    return "READY"


def run_repo_status_audit(
    *,
    repo_root: Path,
    env: Mapping[str, str],
    run_cmd: Callable[..., CmdResult] = _run_cmd,
    list_windows: Callable[[], tuple[list[VisibleWindow], dict[str, Any]]] = _list_visible_windows_real,
    now_iso: Callable[[], str] = _utc_now_iso,
    write_json: Callable[[Path, dict[str, Any]], None] = _write_json_atomic,
) -> tuple[int, dict[str, Any]]:
    """Ejecuta auditoría del repo y del entorno REAL en una sola pasada.

    Siempre escribe:
    - diagnostics/status_repo.json
    - diagnostics/window_diagnostics.json
    """

    status_path = repo_root / STATUS_REPO_JSON_REL
    window_diag_path = repo_root / WINDOW_DIAGNOSTICS_JSON_REL

    timestamp = now_iso()
    root_blockers: list[str] = []
    warnings: list[str] = []

    env_snapshot = {
        k: str(env.get(k, "") or "")
        for k in (
            "FRBOT_PROFILE",
            "FRBOT_CAPTURE_SOURCE",
            "FRBOT_OBS_SOURCE_NAME",
            "FRBOT_WINDOW_HWND",
            "FRBOT_WINDOW_TITLE",
            "FRBOT_CONFIG_PATH",
            "FRBOT_REAL_FRAMES_DIR",
        )
    }

    windows: list[VisibleWindow] = []
    windows_meta: dict[str, Any] = {}
    if _is_windows():
        windows, windows_meta = list_windows()
    else:
        windows, windows_meta = [], {"ok": False, "reason": "unsupported_platform", "platform": str(sys.platform)}
        root_blockers.append("unsupported_platform")

    windows_meta_ok = bool(isinstance(windows_meta, dict) and windows_meta.get("ok") is True)
    if _is_windows() and (not windows_meta_ok):
        # Informational: window enumeration failed. Do not block by itself.
        warnings.append("window_enumeration_failed")

    effective_hwnd, selection, window_blockers = _resolve_effective_hwnd(
        env=env_snapshot,
        windows=windows,
        can_validate_hwnd=bool(windows_meta_ok and len(windows) > 0),
    )
    root_blockers.extend(window_blockers)

    # Window state/foreground validation after selection resolution.
    selected_window = None
    if effective_hwnd is not None:
        for w in windows:
            if int(w.hwnd) == int(effective_hwnd):
                selected_window = w
                break
    if effective_hwnd is not None and selected_window is None and bool(windows_meta_ok):
        root_blockers.append("window_invalid_state")
    if selected_window is not None and bool(selected_window.minimized):
        root_blockers.append("window_invalid_state")
    if selected_window is not None and bool(windows_meta_ok):
        target_z = int(getattr(selected_window, "z_order", 1))
        if target_z != 0:
            overlapped_by_front_window = any(
                (not bool(w.minimized))
                and int(getattr(w, "z_order", 999999)) < target_z
                and _rects_overlap(selected_window, w)
                for w in windows
            )
            if overlapped_by_front_window:
                root_blockers.append("window_not_foreground")

    # Window diagnostics payload (persist at the end so pytest/tools can't overwrite it).
    window_diag_payload: dict[str, Any] = {
        "timestamp_utc": timestamp,
        "selection": selection,
        "meta": windows_meta,
    }

    # Validate REAL env for strict OBS-source identity.
    profile = str(env_snapshot.get("FRBOT_PROFILE", "") or "").strip().lower()
    if profile != "prod_full":
        root_blockers.append("profile_not_prod_full")

    capture_source = str(env_snapshot.get("FRBOT_CAPTURE_SOURCE", "") or "").strip().lower()
    if not capture_source:
        root_blockers.append("obs_source_missing")
    elif capture_source != "obs_source":
        root_blockers.append("obs_source_mismatch")

    obs_source_name = str(env_snapshot.get("FRBOT_OBS_SOURCE_NAME", "") or "").strip()
    if not obs_source_name:
        root_blockers.append("obs_source_name_missing")

    frames_dir = _default_frames_dir(repo_root, env_snapshot)
    config_path = _default_config_path(repo_root, env_snapshot)

    # Ensure frames dir exists (requirement: create if missing).
    try:
        frames_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        root_blockers.append("frames_dir_missing")

    if (not frames_dir.exists()) or (not frames_dir.is_dir()):
        root_blockers.append("frames_dir_missing")

    if (not config_path.exists()) or (not config_path.is_file()):
        root_blockers.append("config_missing")

    # Repo metadata.
    git_dirty = False
    git_branch = ""
    git_commit = ""
    git_status_out = ""
    untracked_count = 0
    try:
        r1 = run_cmd(["git", "status", "--porcelain"], cwd=repo_root)
        git_status_out = r1.stdout
        lines = (r1.stdout or "").splitlines()
        git_dirty = bool(any(l.strip() for l in lines))
        untracked_count = sum(1 for l in lines if l.startswith("??"))

        r2 = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        git_branch = (r2.stdout or "").strip()

        r3 = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_root)
        git_commit = (r3.stdout or "").strip()
    except Exception:
        # Don't block readiness solely for git probing.
        pass

    # This tool is intentionally NOT running pytest nor prod_full auditors.
    # Those are executed by the release wrapper (fail-fast, step-by-step).
    pytest_ok = False
    pytest_rc: int | None = None
    pytest_tail = "skipped_by_design"

    prod_ok = False
    prod_rc: int | None = None
    prod_tail = "skipped_by_design"
    prod_final = ""

    # Decide.
    # Dedupe blockers while preserving order.
    seen: set[str] = set()
    deduped_blockers: list[str] = []
    for b in root_blockers:
        if b not in seen:
            seen.add(b)
            deduped_blockers.append(b)

    final_decision = ""
    exit_code = EXIT_NOT_READY
    try:
        final_decision = evaluate_go_no_go(
            root_blockers=deduped_blockers,
            prod_full_final_decision=prod_final,
            repo_dirty=git_dirty,
            pytest_ok=pytest_ok,
        )
        if final_decision == "READY":
            exit_code = EXIT_READY
        else:
            exit_code = EXIT_NOT_READY
    except Exception:
        deduped_blockers.append("internal_error")
        final_decision = "NOT_READY"
        exit_code = EXIT_NOT_READY

    report: dict[str, Any] = {
        "timestamp_utc": timestamp,
        "final_decision": final_decision,
        "exit_code": int(exit_code),
        "root_blockers": deduped_blockers,
        "warnings": warnings,
        "git_branch": git_branch,
        "git_commit": git_commit,
        "is_dirty": bool(git_dirty),
        "untracked_count": int(untracked_count),
        "env_snapshot": env_snapshot,
        "paths": {
            "status_repo_json": STATUS_REPO_JSON_REL,
            "window_diagnostics_json": WINDOW_DIAGNOSTICS_JSON_REL,
            "frames_dir": str(frames_dir),
            "config_path": str(config_path),
        },
        "window": {
            "effective_hwnd": (int(effective_hwnd) if effective_hwnd is not None else None),
            "selection": selection,
            "found_windows": [
                {
                    "hwnd": int(w.hwnd),
                    "title": str(w.title),
                    "minimized": bool(w.minimized),
                    "z_order": int(w.z_order),
                    "rect": {
                        "left": int(w.rect_left),
                        "top": int(w.rect_top),
                        "right": int(w.rect_right),
                        "bottom": int(w.rect_bottom),
                    },
                }
                for w in windows
            ],
        },
        "git": {
            "dirty": bool(git_dirty),
            "branch": git_branch,
            "commit": git_commit,
            "is_dirty": bool(git_dirty),
            "untracked_count": int(untracked_count),
            "status_porcelain": _tail_lines(git_status_out, max_lines=30),
        },
        "pytest": {
            "ok": bool(pytest_ok),
            "returncode": pytest_rc,
            "output_tail": pytest_tail,
        },
        "audit_prod_full": {
            "ok": bool(prod_ok),
            "returncode": prod_rc,
            "final_decision": prod_final,
            "output_tail": _tail_lines(prod_tail, max_lines=80),
        },
    }

    # Persist window diagnostics last (best-effort).
    try:
        write_json(window_diag_path, window_diag_payload)
    except Exception:
        pass

    # Always write a fresh status JSON.
    try:
        write_json(status_path, report)
    except Exception:
        # If we can't write the report, reflect it in the process exit.
        return EXIT_NOT_OPERATIONAL, report

    return int(exit_code), report


def main(argv: list[str] | None = None) -> int:
    _ = argv  # reserved for future flags; keep deterministic for now.

    repo_root = _repo_root()
    # Ensure imports work when running from any CWD.
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    exit_code, report = run_repo_status_audit(repo_root=repo_root, env=dict(os.environ))
    # Single, stable final line (ASCII).
    print(f"FINAL DECISION: {report.get('final_decision', '')}")
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
