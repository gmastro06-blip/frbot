from __future__ import annotations

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


def _extract_ts_from_frames_dir(frames_dir: Path) -> str | None:
    # Expect diagnostics/frames_full/<timestamp>
    tail = frames_dir.name
    if re.match(r"^\d{8}_\d{6}$", tail):
        return tail
    return None


def _snapshot_existing_frames(*, repo_root: Path, dest_frames_dir: Path, runtime_log: Path) -> None:
    """Best-effort snapshot from canonical diagnostics/frames_full into the release frames dir.

    This keeps release packaging deterministic even if the evidence was generated earlier.
    """

    canonical = repo_root / "diagnostics" / "frames_full"
    if not canonical.exists() or not canonical.is_dir():
        return

    # Only snapshot if destination is empty.
    if any(dest_frames_dir.iterdir()):
        return

    must_copy = [
        canonical / "evidence_manifest.json",
        canonical / "cavebot_trace.jsonl",
    ]
    if not (canonical / "evidence_manifest.json").exists():
        return

    copied = 0
    for p in must_copy:
        if p.exists() and p.is_file():
            (dest_frames_dir / p.name).write_bytes(p.read_bytes())
            copied += 1

    for p in canonical.glob("*_last_result.json"):
        if p.is_file():
            (dest_frames_dir / p.name).write_bytes(p.read_bytes())
            copied += 1

    for p in canonical.glob("*.ppm"):
        if p.is_file():
            (dest_frames_dir / p.name).write_bytes(p.read_bytes())
            copied += 1

    _write_text(runtime_log, _read_text(runtime_log) + f"\n[snapshot] copied_files={copied}\n")


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


def _collect_release_files(*, repo_root: Path, frames_dir: Path, runtime_log: Path, fatal_log: Path, release_last_result: Path) -> list[Path]:
    files: list[Path] = []

    for p in (
        repo_root / "diagnostics" / "status_repo.json",
        repo_root / "diagnostics" / "window_diagnostics.json",
        runtime_log,
        fatal_log,
        release_last_result,
    ):
        if p.exists() and p.is_file():
            files.append(p)

    # *_last_result.json
    if frames_dir.exists():
        for p in frames_dir.glob("*_last_result.json"):
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

    # Determine timestamp + frames_dir.
    env_frames_raw = str(os.environ.get("FRBOT_REAL_FRAMES_DIR", "") or "").strip()
    env_frames_dir = Path(env_frames_raw) if env_frames_raw else None

    ts = None
    if env_frames_dir is not None:
        ts = _extract_ts_from_frames_dir(env_frames_dir)

    if ts is None:
        ts = _utc_now_ts()

    frames_dir = env_frames_dir if env_frames_dir is not None else (repo_root / "diagnostics" / "frames_full" / ts)
    releases_dir = repo_root / "diagnostics" / "releases"
    release_work_dir = releases_dir / ts
    runtime_log = release_work_dir / "runtime.log"
    fatal_log = release_work_dir / "fatal.log"
    release_last_result = release_work_dir / "release_last_result.json"
    zip_path = releases_dir / f"{ts}.zip"

    _ensure_dir(frames_dir)
    _ensure_dir(release_work_dir)

    blockers: list[str] = []
    if not obs_source_name:
        blockers.append("missing_FRBOT_OBS_SOURCE_NAME")
    if not (window_hwnd or window_title):
        blockers.append("missing_FRBOT_WINDOW_SELECTOR")

    # Prepare env for subprocess.
    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_full"
    env["FRBOT_MODE"] = "prod_full"
    env["FRBOT_CAPTURE_SOURCE"] = "obs_source"
    env["FRBOT_DUMP_FRAMES"] = "1"
    env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
    if obs_source_name:
        env["FRBOT_OBS_SOURCE_NAME"] = obs_source_name
    if window_title:
        env["FRBOT_WINDOW_TITLE"] = window_title
    if window_hwnd:
        env["FRBOT_WINDOW_HWND"] = window_hwnd

    steps: list[tuple[str, list[str]]] = [
        ("pytest", ["poetry", "run", "pytest", "-q"]),
        ("audit_prod_full", ["poetry", "run", "python", "tools/audit_prod_full.py"]),
        ("audit_repo_status", ["poetry", "run", "python", "tools/audit_repo_status.py"]),
    ]

    results: list[StepResult] = []
    ok = True

    # If evidence already exists in canonical frames_full, snapshot it into the release frames dir.
    # This keeps the new timestamped directory usable for the subsequent audit.
    _snapshot_existing_frames(repo_root=repo_root, dest_frames_dir=frames_dir, runtime_log=runtime_log)

    if blockers:
        ok = False
        _append_runtime(runtime_log, "preflight", "\n".join(blockers))
        _write_text(fatal_log, "preflight_failed\n" + "\n".join(blockers) + "\n")
    else:
        for name, cmd in steps:
            r = _run(cmd, cwd=repo_root, env=env)
            results.append(r)
            _append_runtime(runtime_log, name, r.combined)
            if r.returncode != 0:
                ok = False
                _write_text(fatal_log, f"step_failed:{name}\n" + r.combined + "\n")
                break

    if ok and not fatal_log.exists():
        _write_text(fatal_log, "")

    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "frames_dir": str(frames_dir),
        "zip_path": str(zip_path),
        "ok": bool(ok),
        "blockers": blockers,
        "steps": [
            {
                "name": s.name,
                "argv": s.argv,
                "returncode": s.returncode,
            }
            for s in results
        ],
    }
    _write_json(release_last_result, payload)

    # Always attempt to zip what we have.
    files = _collect_release_files(
        repo_root=repo_root,
        frames_dir=frames_dir,
        runtime_log=runtime_log,
        fatal_log=fatal_log,
        release_last_result=release_last_result,
    )
    _zip_files(zip_path, files)

    # Final output must be ONLY one line.
    if ok:
        print("RELEASE_GO")
        return 0

    print("RELEASE_NO_GO")
    return 1


def main() -> int:
    return int(run_release(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
