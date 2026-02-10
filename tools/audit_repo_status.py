from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final


_REQUIRED_GATES: Final[tuple[str, ...]] = (
    "targeting_full",
    "healing_full",
    "combat_full",
    "cavebot_full",
    "looting_full",
    "deposit_full",
    "trade_full",
)


@dataclass(frozen=True, slots=True)
class _CmdResult:
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _normalize_output(s: str) -> str:
    # Keep reports stable-ish across runs by removing volatile durations.
    # Example: "119 passed in 10.87s" -> "119 passed in <time>s"
    s = re.sub(r"\bin\s+\d+(?:\.\d+)?s\b", "in <time>s", s)
    return s


def _run_cmd(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout_s: int = 0) -> _CmdResult:
    p = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=None if timeout_s <= 0 else timeout_s,
    )
    return _CmdResult(argv=list(argv), returncode=int(p.returncode), stdout=p.stdout or "", stderr=p.stderr or "")


def _which_or_missing(name: str) -> str | None:
    return shutil.which(name)


def _default_config_path(repo_root: Path) -> Path:
    # Align with scripts/run_prod_full_real_obs_source.ps1 behavior.
    p = repo_root / "config" / "rois_prod_full.json"
    if p.exists():
        return p
    legacy = repo_root / "rois_prod_full.json"
    return legacy


def _is_placeholder_path(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    # Common README-style placeholder: C:\...\file.json
    if "..." in t:
        return True
    return False


def _parse_hwnd(s: str) -> int | None:
    t = (s or "").strip()
    if not t:
        return None
    # Ignore common placeholder values like 0xXXXXXXXX.
    if re.fullmatch(r"(?i)0xX+", t):
        return None
    try:
        base = 16 if t.lower().startswith("0x") else 10
        v = int(t, base)
    except Exception:
        return None
    return v if v > 0 else None


def _is_within_diagnostics(repo_root: Path, path: Path) -> bool:
    try:
        diag = (repo_root / "diagnostics").resolve()
        rp = path.resolve()
        return diag == rp or diag in rp.parents
    except Exception:
        return False


def _select_effective_frames_dir(frames_dir: Path) -> Path:
    if (frames_dir / "evidence_manifest.json").exists():
        return frames_dir

    if frames_dir.exists() and frames_dir.is_dir():
        candidates: list[Path] = []
        for p in frames_dir.iterdir():
            if not p.is_dir():
                continue
            if p.name.startswith("evidence_") and (p / "evidence_manifest.json").exists():
                candidates.append(p)
        if candidates:
            # evidence_YYYYMMDD-HHmmss naming is lexicographically sortable.
            return sorted(candidates, key=lambda x: x.name)[-1]

    return frames_dir


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def parse_gate_last_result(*, frames_dir: Path, gate: str) -> dict[str, Any]:
    meta_path = frames_dir / f"{gate}_last_result.json"
    entry: dict[str, Any] = {
        "gate_name": gate,
        "ok": False,
        "outcome_kind": "",
        "reason": "missing_last_result" if not meta_path.exists() else "last_result_unreadable",
        "evidence_files": [],
        "before_ppm": None,
        "after_ppm": None,
        "next_action": "run prod_full to generate evidence",
    }

    if not meta_path.exists():
        return entry

    data = _load_json_object(meta_path)
    if data is None:
        entry["reason"] = "last_result_unreadable"
        entry["next_action"] = "re-run gate to regenerate last_result"
        return entry

    ok = bool(data.get("ok"))
    outcome_kind = str(data.get("outcome_kind") or "").strip()
    reason = str(data.get("reason") or "").strip()
    before_ppm = data.get("before_ppm")
    after_ppm = data.get("after_ppm")

    entry["ok"] = ok
    entry["outcome_kind"] = outcome_kind
    entry["reason"] = reason or ("ok" if ok else "gate_not_ok")

    if isinstance(before_ppm, str) and before_ppm.strip():
        entry["before_ppm"] = before_ppm
    if isinstance(after_ppm, str) and after_ppm.strip():
        entry["after_ppm"] = after_ppm

    evidence: list[str] = []
    for k in ("before_ppm", "after_ppm"):
        v = entry.get(k)
        if isinstance(v, str) and v:
            if (frames_dir / v).exists():
                evidence.append(v)
    entry["evidence_files"] = evidence

    if ok:
        entry["next_action"] = ""
    else:
        if not entry.get("before_ppm") or not entry.get("after_ppm"):
            entry["next_action"] = "re-run gate with dumping enabled"
        elif not evidence:
            entry["next_action"] = "re-run gate to regenerate evidence frames"
        else:
            entry["next_action"] = "inspect before/after evidence in frames dir"

    return entry


def _parse_pytest_counts(output: str) -> dict[str, int]:
    # Typical -q footer: "119 passed, 2 skipped in 10.87s"
    counts = {"passed": 0, "skipped": 0, "failed": 0}
    m = re.search(r"(?P<body>\d+\s+passed(?:,\s+\d+\s+skipped)?(?:,\s+\d+\s+failed)?)", output)
    if not m:
        # Try a broader parse (failed first).
        m2 = re.search(r"\b(\d+)\s+failed\b", output)
        if m2:
            counts["failed"] = int(m2.group(1))
        m3 = re.search(r"\b(\d+)\s+passed\b", output)
        if m3:
            counts["passed"] = int(m3.group(1))
        m4 = re.search(r"\b(\d+)\s+skipped\b", output)
        if m4:
            counts["skipped"] = int(m4.group(1))
        return counts

    body = m.group("body")
    for key in ("passed", "skipped", "failed"):
        mk = re.search(rf"\b(\d+)\s+{key}\b", body)
        if mk:
            counts[key] = int(mk.group(1))
    return counts


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def new_status_report() -> dict[str, Any]:
    return {
        "timestamp": _utc_now_iso(),
        "platform": platform.platform(),
        "git_clean": False,
        "last_commit": "",
        "tests": {
            "tests_ok": False,
            "passed": 0,
            "skipped": 0,
            "failed": 0,
            "output": "",
        },
        "audit_mock": {
            "audit_mock_ok": False,
            "returncode": None,
            "output": "",
        },
        "audit_prod_full": {
            "audit_prod_full_ok": False,
            "returncode": None,
            "output": "",
            "frames_dir": "",
            "config_path": "",
        },
        "gates": [],
        "root_blockers": [],
        "preconditions": {
            "ok": False,
            "reasons": [],
        },
    }


def main() -> int:
    repo_root = _repo_root()

    report: dict[str, Any] = new_status_report()

    root_blockers: list[str] = []

    if not _is_windows():
        root_blockers.append("unsupported_platform")
        report["root_blockers"] = list(root_blockers)
        # Still emit report to diagnostics.
        diag = repo_root / "diagnostics"
        _ensure_dir(diag)
        (diag / "status_repo.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"FINAL_DECISION: NOT_OPERATIONAL_REAL + ROOT_BLOCKERS: {root_blockers}")
        return 2

    missing_dep: list[str] = []
    for dep in ("git", "poetry"):
        if _which_or_missing(dep) is None:
            missing_dep.append(dep)
    if missing_dep:
        root_blockers.extend([f"missing_dependency:{d}" for d in missing_dep])
        report["root_blockers"] = list(root_blockers)
        diag = repo_root / "diagnostics"
        _ensure_dir(diag)
        (diag / "status_repo.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"FINAL_DECISION: NOT_OPERATIONAL_REAL + ROOT_BLOCKERS: {root_blockers}")
        return 2

    # 1) git status + last commit
    env_base = dict(os.environ)
    env_base["PYTHONIOENCODING"] = "utf-8"

    git_status = _run_cmd(["git", "status", "--porcelain"], cwd=repo_root, env=env_base)
    git_log = _run_cmd(["git", "log", "-1", "--oneline"], cwd=repo_root, env=env_base)

    porcelain = (git_status.stdout or "").strip("\r\n")
    git_clean = porcelain.strip() == ""
    report["git_clean"] = bool(git_clean)
    report["last_commit"] = (git_log.stdout or "").strip()
    report["git_status_porcelain"] = porcelain

    if not git_clean:
        root_blockers.append("git_dirty")

    # 2) tests
    tests_res = _run_cmd(["poetry", "run", "pytest", "-q"], cwd=repo_root, env=env_base)
    combined_tests = _normalize_output(tests_res.combined)
    counts = _parse_pytest_counts(combined_tests)
    tests_ok = tests_res.returncode == 0 and counts.get("failed", 0) == 0

    report["tests"]["tests_ok"] = bool(tests_ok)
    report["tests"]["passed"] = int(counts.get("passed", 0))
    report["tests"]["skipped"] = int(counts.get("skipped", 0))
    report["tests"]["failed"] = int(counts.get("failed", 0))
    report["tests"]["output"] = combined_tests

    if not tests_ok:
        root_blockers.append("tests_failed")

    # 3) mock audit
    env_mock = dict(env_base)
    env_mock["FRBOT_MODE"] = "mock"
    mock_res = _run_cmd(["poetry", "run", "python", str(repo_root / "tools" / "audit_all.py")], cwd=repo_root, env=env_mock)
    combined_mock = _normalize_output(mock_res.combined)
    report["audit_mock"]["returncode"] = int(mock_res.returncode)
    report["audit_mock"]["output"] = combined_mock
    report["audit_mock"]["audit_mock_ok"] = bool(mock_res.returncode == 0)
    if mock_res.returncode != 0:
        root_blockers.append("audit_mock_failed")

    # Preconditions for REAL prod_full audit
    pre_reasons: list[str] = []

    capture_source = (_env_str("FRBOT_CAPTURE_SOURCE", "") or "").strip().lower()
    if capture_source != "obs_source":
        pre_reasons.append("obs_source_missing")

    obs_name = _env_str("FRBOT_OBS_SOURCE_NAME", "")
    if not obs_name:
        pre_reasons.append("obs_source_name_missing")

    config_raw = _env_str("FRBOT_CONFIG_PATH", "")
    if config_raw and _is_placeholder_path(config_raw):
        config_raw = ""
    config_path = Path(config_raw) if config_raw else _default_config_path(repo_root)
    if not config_path.is_absolute():
        config_path = (repo_root / config_path).resolve()
    if config_raw and not config_path.exists():
        # Env var points to a non-existent path; fall back to repo default.
        config_raw = ""
        config_path = _default_config_path(repo_root)
        if not config_path.is_absolute():
            config_path = (repo_root / config_path).resolve()

    if not config_path.exists():
        pre_reasons.append("config_missing")

    frames_raw = _env_str("FRBOT_REAL_FRAMES_DIR", "")
    frames_dir = Path(frames_raw) if frames_raw else (repo_root / "diagnostics" / "frames_full")
    if not frames_dir.is_absolute():
        frames_dir = (repo_root / frames_dir).resolve()

    if not frames_dir.exists():
        # Create only if it's within diagnostics/**
        if _is_within_diagnostics(repo_root, frames_dir):
            _ensure_dir(frames_dir)
        else:
            pre_reasons.append("frames_dir_missing")

    effective_frames_dir = _select_effective_frames_dir(frames_dir)

    # Require a manifest in the effective directory for audit_prod_full.
    if not (effective_frames_dir / "evidence_manifest.json").exists():
        pre_reasons.append("evidence_manifest_missing")

    hwnd_raw = _env_str("FRBOT_WINDOW_HWND", "")
    title_raw = _env_str("FRBOT_WINDOW_TITLE", "")
    hwnd = _parse_hwnd(hwnd_raw)
    title = title_raw.strip()

    if hwnd is None and not title:
        pre_reasons.append("window_selector_missing")
    if hwnd_raw and hwnd is None and not title:
        # If user attempted HWND but it's invalid/placeholder.
        pre_reasons.append("window_hwnd_invalid")

    report["audit_prod_full"]["frames_dir"] = str(effective_frames_dir)
    report["audit_prod_full"]["config_path"] = str(config_path)

    # Parse gates if we have any evidence directory.
    gates_out: list[dict[str, Any]] = []
    if effective_frames_dir.exists() and effective_frames_dir.is_dir():
        for gate in _REQUIRED_GATES:
            gates_out.append(parse_gate_last_result(frames_dir=effective_frames_dir, gate=gate))
    report["gates"] = gates_out

    preconditions_ok = not pre_reasons
    report["preconditions"]["ok"] = bool(preconditions_ok)
    report["preconditions"]["reasons"] = list(pre_reasons)
    if not preconditions_ok:
        root_blockers.extend(pre_reasons)

    # 4) prod_full REAL audit (only if preconditions OK)
    real_rc: int | None = None
    real_out: str = ""
    if preconditions_ok:
        env_real = dict(env_base)
        env_real["FRBOT_PROFILE"] = "prod_full"
        env_real["FRBOT_REAL_FRAMES_DIR"] = str(effective_frames_dir)
        env_real["FRBOT_CONFIG_PATH"] = str(config_path)

        # Keep the contract explicit for strict OBS v5 source identity.
        env_real["FRBOT_CAPTURE_SOURCE"] = "obs_source"
        env_real["FRBOT_OBS_SOURCE_NAME"] = obs_name

        # Preserve any provided selector variables as-is.
        if hwnd_raw:
            env_real["FRBOT_WINDOW_HWND"] = hwnd_raw
        if title_raw:
            env_real["FRBOT_WINDOW_TITLE"] = title_raw

        real_res = _run_cmd(["poetry", "run", "python", str(repo_root / "tools" / "audit_prod_full.py")], cwd=repo_root, env=env_real)
        real_rc = int(real_res.returncode)
        real_out = _normalize_output(real_res.combined)

        report["audit_prod_full"]["returncode"] = real_rc
        report["audit_prod_full"]["output"] = real_out
        report["audit_prod_full"]["audit_prod_full_ok"] = bool(real_rc == 0)

        if real_rc != 0:
            root_blockers.append("audit_prod_full_failed")
    else:
        report["audit_prod_full"]["returncode"] = None
        report["audit_prod_full"]["output"] = ""
        report["audit_prod_full"]["audit_prod_full_ok"] = False

    # Persist report.
    report["root_blockers"] = list(root_blockers)
    diag = repo_root / "diagnostics"
    _ensure_dir(diag)
    (diag / "status_repo.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    operational = not root_blockers and bool(report["audit_prod_full"]["audit_prod_full_ok"]) and bool(report["tests"]["tests_ok"]) and bool(report["git_clean"])

    if operational:
        print("FINAL_DECISION: OPERATIONAL_REAL")
        return 0

    print(f"FINAL_DECISION: NOT_OPERATIONAL_REAL + ROOT_BLOCKERS: {root_blockers}")

    # Exit code contract
    if pre_reasons:
        return 2
    if not tests_ok:
        return 4
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
