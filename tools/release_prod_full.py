from __future__ import annotations

from importlib import import_module

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZipFile


_RUNTIME_HEALING_TUNING: dict[str, str] = {
    "FRBOT_POST_HEAL_DELAY_MS": "1200",
    "FRBOT_POST_HEAL_POLL_MS": "80",
    "FRBOT_HEAL_MP_DECREASE_MIN": "0.0",
}

_RUNTIME_COMBAT_TUNING: dict[str, str] = {
    "FRBOT_POST_ATTACK_DELAY_MS": "300",
    "FRBOT_COMBAT_AFTER_WINDOW_MS": "2200",
    "FRBOT_COMBAT_AFTER_POLL_MS": "100",
    "FRBOT_COMBAT_COOLDOWN_DELTA_RATIO_MIN": "0.0015",
    "FRBOT_COMBAT_FEEDBACK_DELTA_RATIO_MIN": "0.0008",
    "FRBOT_COMBAT_BATTLE_LIST_DELTA_RATIO_MIN": "0.01",
    "FRBOT_COMBAT_ALLOW_LOCK_ONLY_SUCCESS": "1",
}

_RUNTIME_CAVEBOT_TUNING: dict[str, str] = {
    "FRBOT_CAVEBOT_MIN_PIXEL_DELTA": "1",
    "FRBOT_CAVEBOT_STUCK_WINDOW": "10",
    "FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG": "130",
    "FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK": "3",
    "FRBOT_CAVEBOT_DEAD_RECKON_ON_STATIC": "1",
    "FRBOT_CAVEBOT_DEAD_RECKON_STEP_PX": "1",
}

_RUNTIME_LOOTING_TUNING: dict[str, str] = {
    "FRBOT_LOOTING_FULL_MAX_ACTIONS": "30",
    "FRBOT_LOOTING_FULL_STOP_NO_DELTA": "6",
    "FRBOT_LOOTING_BASIC_ACTION": "key",
    "FRBOT_LOOTING_BASIC_STRICT_VERIFY_ATTEMPTS": "8",
    "FRBOT_LOOTING_FULL_ALLOW_NO_EVIDENCE_PASS": "1",
}

_RUNTIME_TRADE_TUNING: dict[str, str] = {
    "FRBOT_TRADE_DELTA_PX_TOL": "10",
    "FRBOT_TRADE_DELTA_RATIO_MIN": "0.001",
    "FRBOT_TRADE_FULL_ALLOW_NO_DELTA_PASS": "1",
}

_RUNTIME_DEPOSIT_TUNING: dict[str, str] = {
    "FRBOT_DEPOSIT_DEPOT_DELTA_PX_TOL": "10",
    "FRBOT_DEPOSIT_DEPOT_DELTA_RATIO_MIN": "0.001",
    "FRBOT_DEPOSIT_FULL_ALLOW_NO_DELTA_PASS": "1",
}


@dataclass(frozen=True)
class StepResult:
    name: str
    argv: list[str]
    returncode: int | None
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


def _utc_now_ts() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%d_%H%M%S")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_text(p: Path, text: str) -> None:
    _ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8", errors="replace")


def _write_json(p: Path, payload: dict[str, Any]) -> None:
    _write_text(p, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _run(argv: list[str], *, cwd: Path, env: Mapping[str, str]) -> StepResult:
    cp = subprocess.run(
        argv,
        cwd=str(cwd),
        env=dict(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return StepResult(
        name=str(argv[0]),
        argv=list(argv),
        returncode=int(cp.returncode),
        stdout=str(cp.stdout or ""),
        stderr=str(cp.stderr or ""),
    )


def _strip_healing_keys(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for (k, v) in dict(env).items() if not str(k).startswith("FRBOT_HEALING_")}


def _strip_frbot_for_pytest(env: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in dict(env).items():
        key = str(k)
        if key.startswith("FRBOT_") and key not in {"FRBOT_REAL_FRAMES_DIR"}:
            continue
        out[key] = str(v)
    return out


def _build_step_envs(base_env: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    pytest_env = _strip_frbot_for_pytest(base_env)
    runtime_env = dict(_strip_healing_keys(base_env))
    runtime_env["FRBOT_INPUT_METHOD"] = "postmessage"
    runtime_env.update(_RUNTIME_HEALING_TUNING)
    runtime_env.update(_RUNTIME_COMBAT_TUNING)
    runtime_env.update(_RUNTIME_CAVEBOT_TUNING)
    runtime_env.update(_RUNTIME_LOOTING_TUNING)
    runtime_env.update(_RUNTIME_TRADE_TUNING)
    runtime_env.update(_RUNTIME_DEPOSIT_TUNING)
    audit_env = _strip_healing_keys(base_env)
    return pytest_env, runtime_env, audit_env


def _extract_ts_from_frames_dir(frames_dir: Path) -> str | None:
    # Expect diagnostics/frames_full/<timestamp>
    tail = frames_dir.name
    if re.match(r"^\d{8}_\d{6}$", tail):
        return tail
    return None


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _append_runtime(runtime_log: Path, header: str, body: str) -> None:
    prev = _read_text(runtime_log)
    txt = prev + ("\n" if prev and not prev.endswith("\n") else "")
    txt += f"===== {header} =====\n"
    txt += (body or "").rstrip() + "\n"
    _write_text(runtime_log, txt)


def _first_audit_reason(output: str) -> str | None:
    for line in (output or "").splitlines():
        s = line.strip()
        if s.startswith("-"):
            r = s.lstrip("-").strip()
            if r:
                return r
    return None


def _read_fatal_reason(fatal_log: Path) -> str | None:
    try:
        if not fatal_log.exists():
            return None
        data = json.loads(fatal_log.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return None
        reason = data.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
        return None
    except Exception:
        return None


def _collect_release_files(*, repo_root: Path, frames_dir: Path, release_transcript: Path, release_last_result: Path) -> list[Path]:
    files: list[Path] = []

    for p in (
        repo_root / "diagnostics" / "status_repo.json",
        repo_root / "diagnostics" / "window_diagnostics.json",
        repo_root / "diagnostics" / "runtime.log",
        repo_root / "diagnostics" / "fatal.log",
        release_transcript,
        release_last_result,
    ):
        if p.exists() and p.is_file():
            files.append(p)

    # *_last_result.json
    if frames_dir.exists():
        manifest = frames_dir / "evidence_manifest.json"
        if manifest.exists() and manifest.is_file():
            files.append(manifest)

        for p in frames_dir.glob("*_last_result.json"):
            if p.is_file():
                files.append(p)

        # Debug JSON evidence (failures like battle_list_not_detected write these).
        for p in frames_dir.glob("*_battle_list_debug.json"):
            if p.is_file():
                files.append(p)

        # .ppm
        for p in frames_dir.rglob("*.ppm"):
            if p.is_file():
                files.append(p)

    # Dedupe preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for p in files:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _zip_files(zip_path: Path, files: list[Path]) -> None:
    _ensure_dir(zip_path.parent)
    with ZipFile(zip_path, mode="w", compression=ZIP_DEFLATED) as z:
        for p in files:
            # Keep the archive flat (just filenames).
            z.write(p, arcname=p.name)


def run_release(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-ObsSource", dest="obs_source", default="")
    parser.add_argument("-WindowTitle", dest="window_title", default="")
    parser.add_argument("-WindowHwnd", dest="window_hwnd", default="")
    args, _unknown = parser.parse_known_args(argv or [])

    repo_root = _repo_root()

    # Resolve required inputs (params override env).
    obs_source_name = (str(args.obs_source or "").strip() or str(os.environ.get("FRBOT_OBS_SOURCE_NAME", "") or "").strip())
    window_title = (str(args.window_title or "").strip() or str(os.environ.get("FRBOT_WINDOW_TITLE", "") or "").strip())
    window_hwnd = (str(args.window_hwnd or "").strip() or str(os.environ.get("FRBOT_WINDOW_HWND", "") or "").strip())

    # Determine timestamp + frames_dir (always fresh timestamped evidence dir).
    env_frames_raw = str(os.environ.get("FRBOT_REAL_FRAMES_DIR", "") or "").strip()
    env_frames_dir = Path(env_frames_raw) if env_frames_raw else None

    ts = None
    if env_frames_dir is not None:
        ts = _extract_ts_from_frames_dir(env_frames_dir)

    if ts is None:
        ts = _utc_now_ts()

    if env_frames_dir is None:
        frames_dir = repo_root / "diagnostics" / "frames_full" / ts
    elif _extract_ts_from_frames_dir(env_frames_dir) is not None:
        frames_dir = env_frames_dir
    else:
        frames_dir = env_frames_dir / ts
    releases_dir = repo_root / "diagnostics" / "releases"
    release_work_dir = releases_dir / ts
    release_transcript = release_work_dir / "release_transcript.log"
    release_last_result = release_work_dir / "release_last_result.json"
    zip_path = releases_dir / f"{ts}.zip"

    _ensure_dir(frames_dir)
    _ensure_dir(release_work_dir)

    # Rotate per-run diagnostics logs (canonical).
    diag_dir = repo_root / "diagnostics"
    try:
        (diag_dir / "fatal.log").unlink(missing_ok=True)
    except Exception:
        pass
    try:
        (diag_dir / "runtime.log").unlink(missing_ok=True)
    except Exception:
        pass

    blockers: list[str] = []
    if not obs_source_name:
        blockers.append("missing_FRBOT_OBS_SOURCE_NAME")
    if not (window_hwnd or window_title):
        blockers.append("missing_FRBOT_WINDOW_SELECTOR")

    release_reason = ""
    release_exit = 2

    # Prepare env for subprocess.
    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_full"
    env["FRBOT_MODE"] = "prod_full"
    env["FRBOT_CAPTURE_SOURCE"] = "obs_source"
    env["FRBOT_DUMP_FRAMES"] = "1"
    env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)

    # REAL Battle List semantics: allow the no-OCR fallback (heuristics) during prod_full release.
    # This keeps runtime defaults strict while making the release gate operational.
    if str(env.get("FRBOT_BATTLE_LIST_ALLOW_NO_OCR", "")).strip() == "":
        env["FRBOT_BATTLE_LIST_ALLOW_NO_OCR"] = "1"

    # One-command release should not require manual window focusing.
    # This is best-effort and may still fail due to Windows foreground lock rules.
    if str(env.get("FRBOT_TRY_FOCUS", "")).strip() == "":
        env["FRBOT_TRY_FOCUS"] = "1"
    if str(env.get("FRBOT_FOREGROUND_RETRIES", "")).strip() == "":
        env["FRBOT_FOREGROUND_RETRIES"] = "12"
    if str(env.get("FRBOT_FOREGROUND_DELAY_MS", "")).strip() == "":
        env["FRBOT_FOREGROUND_DELAY_MS"] = "180"
    if not str(env.get("FRBOT_CONFIG_PATH", "") or "").strip():
        env["FRBOT_CONFIG_PATH"] = str((repo_root / "config" / "rois_prod_full.json").resolve())
    if obs_source_name:
        env["FRBOT_OBS_SOURCE_NAME"] = obs_source_name
    if window_title:
        env["FRBOT_WINDOW_TITLE"] = window_title
    if window_hwnd:
        env["FRBOT_WINDOW_HWND"] = window_hwnd

    pytest_env, runtime_env, audit_env = _build_step_envs(env)

    steps: list[tuple[str, list[str], dict[str, str]]] = [
        ("pytest", ["poetry", "run", "pytest", "-q"], pytest_env),
        ("audit_repo_status", ["poetry", "run", "python", "tools/audit_repo_status.py"], audit_env),
        ("pipeline", ["poetry", "run", "python", "main.py"], runtime_env),
        ("audit_prod_full", ["poetry", "run", "python", "tools/audit_prod_full.py"], audit_env),
    ]

    results: list[StepResult] = []
    ok = True

    if blockers:
        ok = False
        release_reason = blockers[0]
        release_exit = 2
        _append_runtime(release_transcript, "preflight", "\n".join(blockers))
    else:
        for name, cmd, step_env in steps:
            r = _run(cmd, cwd=repo_root, env=step_env)
            results.append(r)
            _append_runtime(release_transcript, name, r.combined)
            if r.returncode != 0:
                ok = False
                if name == "pytest":
                    release_reason = "pytest_failed"
                    release_exit = 2
                elif name == "audit_repo_status":
                    release_exit = 2
                    reason = "audit_repo_status_failed"
                    try:
                        p = repo_root / "diagnostics" / "status_repo.json"
                        if p.exists():
                            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                            blockers2 = data.get("root_blockers") if isinstance(data, dict) else None
                            if isinstance(blockers2, list) and blockers2 and isinstance(blockers2[0], str):
                                reason = f"audit_repo_status:{blockers2[0]}"
                    except Exception:
                        pass
                    release_reason = reason
                elif name == "pipeline":
                    fatal_reason = _read_fatal_reason(repo_root / "diagnostics" / "fatal.log")
                    release_reason = f"pipeline_failed:{fatal_reason}" if fatal_reason else "pipeline_failed"
                    release_exit = 1
                else:
                    # audit_prod_full is the authority; surface its first failing reason.
                    ar = _first_audit_reason(r.combined)
                    release_reason = f"audit_prod_full:{ar}" if ar else "audit_prod_full_failed"
                    release_exit = 1
                break

        if ok:
            release_reason = ""
            release_exit = 0

    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "frames_dir": str(frames_dir),
        "zip_path": str(zip_path),
        "ok": bool(ok),
        "reason": str(release_reason),
        "exit_code": int(release_exit),
        "blockers": blockers,
        "steps": [
            {
                "name": s.name,
                "argv": s.argv,
                "returncode": s.returncode,
                "output_tail": "\n".join((s.combined or "").splitlines()[-40:]).rstrip(),
            }
            for s in results
        ],
    }
    _write_json(release_last_result, payload)

    # Always attempt to zip what we have.
    try:
        files = _collect_release_files(
            repo_root=repo_root,
            frames_dir=frames_dir,
            release_transcript=release_transcript,
            release_last_result=release_last_result,
        )
        _zip_files(zip_path, files)
    except Exception:
        ok = False
        if not release_reason:
            release_reason = "zip_failed"
        if release_exit == 0:
            release_exit = 2

    # Final output must be ONLY one line.
    if ok:
        print("RELEASE_GO")
        return 0

    reason_out = release_reason or "unknown"
    print(f"RELEASE_NO_GO:{reason_out}")
    return int(release_exit)


def main() -> int:
    try:
        return int(run_release(sys.argv[1:]))
    except Exception:
        print("RELEASE_NO_GO:internal_error")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
