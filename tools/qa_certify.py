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
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_REQUIRED_GATES: tuple[str, ...] = (
    "targeting_full",
    "healing_full",
    "combat_full",
    "cavebot_full",
    "looting_full",
    "deposit_full",
    "trade_full",
)

_FLAKY_GATES: tuple[str, ...] = (
    "targeting_full",
    "healing_full",
    "cavebot_full",
)


@dataclass(frozen=True)
class StepRecord:
    name: str
    status: str
    reason: str
    returncode: int
    command: list[str]
    evidence_paths: list[str]
    stdout_tail: str
    stderr_tail: str
    duration_s: float


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ts_slug() -> str:
    return _utc_now().strftime("%Y%m%d_%H%M%S")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _diag_dir(repo_root: Path) -> Path:
    return repo_root / "diagnostics"


def _qa_dir(repo_root: Path, ts: str) -> Path:
    return _diag_dir(repo_root) / "qa" / ts


def _tail(text: str, max_lines: int = 60) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _env_with(base: Mapping[str, str], updates: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    for k, v in updates.items():
        env[str(k)] = str(v)
    return env


def _run_cmd(*, cwd: Path, argv: list[str], env: Mapping[str, str], timeout_s: int = 0) -> CmdResult:
    start = time.perf_counter()
    try:
        cp = subprocess.run(
            argv,
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=(timeout_s if timeout_s > 0 else None),
        )
        end = time.perf_counter()
        return CmdResult(
            returncode=int(cp.returncode),
            stdout=str(cp.stdout or ""),
            stderr=str(cp.stderr or ""),
            duration_s=max(0.0, float(end - start)),
        )
    except subprocess.TimeoutExpired as exc:
        end = time.perf_counter()
        out = ""
        err = f"timeout_after_s:{int(timeout_s)}"
        if isinstance(exc.stdout, str):
            out = exc.stdout
        elif isinstance(exc.stdout, bytes):
            out = exc.stdout.decode("utf-8", errors="replace")
        if isinstance(exc.stderr, str):
            err = f"{err}\n{exc.stderr}".strip()
        elif isinstance(exc.stderr, bytes):
            err = f"{err}\n{exc.stderr.decode('utf-8', errors='replace')}".strip()
        return CmdResult(
            returncode=124,
            stdout=str(out),
            stderr=str(err),
            duration_s=max(0.0, float(end - start)),
        )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_release_line(text: str) -> str:
    out = ""
    for line in (text or "").splitlines():
        s = str(line).strip()
        if s.startswith("RELEASE_GO") or s.startswith("RELEASE_NO_GO:"):
            out = s
    return out


def _parse_final_decision(text: str) -> str:
    out = ""
    for line in (text or "").splitlines():
        s = str(line).strip()
        if s.startswith("FINAL DECISION:"):
            out = s.split(":", 1)[1].strip()
    return out


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("invalid_json_shape")
    return data


def _is_ppm_readable(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(2)
        return head in {b"P3", b"P6"}
    except Exception:
        return False


def _validate_runtime_log(runtime_log: Path) -> tuple[bool, str]:
    if not runtime_log.exists() or not runtime_log.is_file():
        return False, "runtime_log_missing"

    try:
        lines = runtime_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return False, "runtime_log_unreadable"

    if not lines:
        return False, "runtime_log_empty"

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            return False, f"runtime_log_invalid_jsonl:L{idx}"
        if not isinstance(obj, dict):
            return False, f"runtime_log_invalid_jsonl_shape:L{idx}"
    return True, "ok"


def _validate_fatal_log(fatal_log: Path) -> tuple[bool, str]:
    if not fatal_log.exists() or not fatal_log.is_file():
        return True, "fatal_log_absent"

    try:
        raw = fatal_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False, "fatal_log_unreadable"

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) != 1:
        return False, "fatal_log_not_one_line"

    try:
        obj = json.loads(lines[0])
    except Exception:
        return False, "fatal_log_invalid_json"

    if not isinstance(obj, dict):
        return False, "fatal_log_invalid_shape"

    reason = obj.get("reason")
    tb = obj.get("traceback")
    if not isinstance(reason, str) or not reason.strip():
        return False, "fatal_log_missing_reason"
    if not isinstance(tb, list):
        return False, "fatal_log_missing_traceback"

    return True, "ok"


def _required_field(data: dict[str, Any], key: str, expected_type: type) -> tuple[bool, str]:
    if key not in data:
        return False, f"missing_field:{key}"
    if not isinstance(data[key], expected_type):
        return False, f"invalid_type:{key}"
    if expected_type is str and not str(data[key]).strip():
        return False, f"empty_field:{key}"
    return True, "ok"


def _validate_last_result(frames_dir: Path, gate: str) -> tuple[bool, str, dict[str, Any] | None]:
    p = frames_dir / f"{gate}_last_result.json"
    if not p.exists() or not p.is_file():
        return False, f"missing_last_result:{gate}", None

    try:
        data = _load_json(p)
    except Exception as exc:
        return False, f"invalid_last_result_json:{gate}:{type(exc).__name__}", None

    checks = (
        ("ok", bool),
        ("reason", str),
        ("evidence_kind", str),
        ("inputs_sent", int),
        ("before_ppm", str),
        ("after_ppm", str),
    )
    for key, typ in checks:
        ok, reason = _required_field(data, key, typ)
        if not ok:
            return False, f"{reason}:{gate}", data

    before_name = str(data["before_ppm"]).strip()
    after_name = str(data["after_ppm"]).strip()
    before_path = frames_dir / before_name
    after_path = frames_dir / after_name

    if not before_path.exists() or not before_path.is_file():
        return False, f"before_ppm_missing:{gate}:{before_name}", data
    if not after_path.exists() or not after_path.is_file():
        return False, f"after_ppm_missing:{gate}:{after_name}", data
    if not _is_ppm_readable(before_path):
        return False, f"before_ppm_unreadable:{gate}:{before_name}", data
    if not _is_ppm_readable(after_path):
        return False, f"after_ppm_unreadable:{gate}:{after_name}", data

    return True, "ok", data


def _validate_evidence_schema(*, repo_root: Path, frames_dir: Path) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    details: dict[str, Any] = {
        "frames_dir": str(frames_dir),
        "gates": {},
        "runtime_log": "",
        "fatal_log": "",
    }

    runtime_ok, runtime_reason = _validate_runtime_log(_diag_dir(repo_root) / "runtime.log")
    details["runtime_log"] = runtime_reason
    if not runtime_ok:
        reasons.append(runtime_reason)

    fatal_ok, fatal_reason = _validate_fatal_log(_diag_dir(repo_root) / "fatal.log")
    details["fatal_log"] = fatal_reason
    if not fatal_ok:
        reasons.append(fatal_reason)

    for gate in _REQUIRED_GATES:
        ok, reason, data = _validate_last_result(frames_dir, gate)
        details["gates"][gate] = {
            "ok": bool(ok),
            "reason": str(reason),
            "path": str(frames_dir / f"{gate}_last_result.json"),
            "data": data,
        }
        if not ok:
            reasons.append(reason)

    return (len(reasons) == 0), reasons, details


def _precheck_inventory(repo_root: Path) -> dict[str, Any]:
    paths = {
        "tools/audit_repo_status.py": (repo_root / "tools" / "audit_repo_status.py").exists(),
        "tools/audit_prod_full.py": (repo_root / "tools" / "audit_prod_full.py").exists(),
        "run_release_prod_full.ps1": (repo_root / "run_release_prod_full.ps1").exists(),
        "tools/release_prod_full.py": (repo_root / "tools" / "release_prod_full.py").exists(),
        "main.py": (repo_root / "main.py").exists(),
        "targeting_full_entrypoint.py": (repo_root / "targeting_full_entrypoint.py").exists(),
        "healing_full_entrypoint.py": (repo_root / "healing_full_entrypoint.py").exists(),
        "combat_full_entrypoint.py": (repo_root / "combat_full_entrypoint.py").exists(),
        "cavebot_full_entrypoint.py": (repo_root / "cavebot_full_entrypoint.py").exists(),
        "looting_full_entrypoint.py": (repo_root / "looting_full_entrypoint.py").exists(),
        "deposit_full_entrypoint.py": (repo_root / "deposit_full_entrypoint.py").exists(),
        "trade_full_entrypoint.py": (repo_root / "trade_full_entrypoint.py").exists(),
    }

    windows_only = sys.platform == "win32"
    profile_targets = ["prod_full", "prod_emergency"]

    return {
        "repo_root": str(repo_root),
        "artifacts": paths,
        "windows_platform": windows_only,
        "target_profiles": profile_targets,
    }


def _status_repo_root_blockers(repo_root: Path) -> list[str]:
    p = _diag_dir(repo_root) / "status_repo.json"
    if not p.exists() or not p.is_file():
        return ["status_repo_missing"]
    try:
        data = _load_json(p)
    except Exception as exc:
        return [f"status_repo_invalid_json:{type(exc).__name__}"]

    blockers = data.get("root_blockers")
    if not isinstance(blockers, list):
        return ["status_repo_root_blockers_invalid"]

    out: list[str] = []
    for b in blockers:
        if isinstance(b, str) and b.strip():
            out.append(b.strip())
    return out


def _latest_release_result(repo_root: Path) -> Path | None:
    base = _diag_dir(repo_root) / "releases"
    if not base.exists() or not base.is_dir():
        return None
    cands = sorted(base.glob("*/release_last_result.json"), key=lambda p: p.stat().st_mtime)
    if not cands:
        return None
    return cands[-1]


def _ensure_base_real_env(base_env: Mapping[str, str], frames_dir: Path) -> dict[str, str]:
    env = dict(base_env)
    env["FRBOT_PROFILE"] = "prod_full"
    env["FRBOT_CAPTURE_SOURCE"] = "obs_source"
    env["FRBOT_MODE"] = "real"
    env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
    if not str(env.get("FRBOT_TRY_FOCUS", "") or "").strip():
        env["FRBOT_TRY_FOCUS"] = "1"
    if not str(env.get("FRBOT_FOREGROUND_RETRIES", "") or "").strip():
        env["FRBOT_FOREGROUND_RETRIES"] = "12"
    if not str(env.get("FRBOT_FOREGROUND_DELAY_MS", "") or "").strip():
        env["FRBOT_FOREGROUND_DELAY_MS"] = "180"
    return env


def _pytest_env(base_env: Mapping[str, str]) -> dict[str, str]:
    # Keep unit tests hermetic: do not leak runtime/release FRBOT vars into pytest.
    env: dict[str, str] = {}
    for k, v in dict(base_env).items():
        key = str(k)
        if key.startswith("FRBOT_"):
            continue
        env[key] = str(v)
    return env


def _run_step(*, name: str, cmd: list[str], cwd: Path, env: Mapping[str, str], evidence_paths: list[str], timeout_s: int = 0) -> StepRecord:
    res = _run_cmd(cwd=cwd, argv=cmd, env=env, timeout_s=timeout_s)
    status = "PASS" if res.returncode == 0 else "FAIL"
    reason = "ok" if status == "PASS" else f"{name}_failed"
    return StepRecord(
        name=name,
        status=status,
        reason=reason,
        returncode=int(res.returncode),
        command=list(cmd),
        evidence_paths=list(evidence_paths),
        stdout_tail=_tail(res.stdout),
        stderr_tail=_tail(res.stderr),
        duration_s=float(res.duration_s),
    )


def _failure(*, reason: str, repro: str, evidence_paths: list[str], fix: str) -> dict[str, Any]:
    return {
        "reason": str(reason),
        "repro": str(repro),
        "evidence_paths": [str(p) for p in evidence_paths],
        "fix": str(fix),
    }


def _real_env_missing(env: Mapping[str, str]) -> list[str]:
    required = [
        "FRBOT_CAPTURE_SOURCE",
        "FRBOT_OBS_SOURCE_NAME",
        "FRBOT_CONFIG_PATH",
        "FRBOT_REAL_FRAMES_DIR",
    ]
    missing: list[str] = []

    for name in required:
        if not str(env.get(name, "") or "").strip():
            missing.append(name)

    has_title = bool(str(env.get("FRBOT_WINDOW_TITLE", "") or "").strip())
    has_hwnd = bool(str(env.get("FRBOT_WINDOW_HWND", "") or "").strip())
    if not (has_title or has_hwnd):
        missing.append("FRBOT_WINDOW_TITLE|FRBOT_WINDOW_HWND")

    if str(env.get("FRBOT_CAPTURE_SOURCE", "") or "").strip().lower() != "obs_source":
        missing.append("FRBOT_CAPTURE_SOURCE=obs_source")

    wrapper_missing = str(env.get("FRBOT_QA_REQUIRED_ENV_MISSING", "") or "").strip()
    if wrapper_missing:
        for item in wrapper_missing.split(","):
            token = str(item).strip()
            if token and token not in missing:
                missing.append(token)

    return missing


def _collect_gate_reason(frames_dir: Path, gate: str) -> str:
    p = frames_dir / f"{gate}_last_result.json"
    if not p.exists():
        return "missing_last_result"
    try:
        data = _load_json(p)
    except Exception as exc:
        return f"invalid_last_result:{type(exc).__name__}"
    r = data.get("reason")
    if isinstance(r, str) and r.strip():
        return r.strip()
    rk = data.get("outcome_kind")
    if isinstance(rk, str) and rk.strip():
        return rk.strip()
    return "unknown"


def _run_real_isolated_gates(*, repo_root: Path, env: Mapping[str, str], frames_dir: Path) -> tuple[list[StepRecord], list[str]]:
    steps: list[StepRecord] = []
    reasons: list[str] = []

    for gate in _REQUIRED_GATES:
        gate_env = _env_with(env, {"FRBOT_MODE": gate})
        cmd = ["poetry", "run", "python", "main.py"]
        step = _run_step(
            name=f"gate:{gate}",
            cmd=cmd,
            cwd=repo_root,
            env=gate_env,
            evidence_paths=[str(frames_dir / f"{gate}_last_result.json")],
            timeout_s=420,
        )
        reason = _collect_gate_reason(frames_dir, gate)
        if step.status == "FAIL":
            step = StepRecord(
                name=step.name,
                status=step.status,
                reason=reason,
                returncode=step.returncode,
                command=step.command,
                evidence_paths=step.evidence_paths,
                stdout_tail=step.stdout_tail,
                stderr_tail=step.stderr_tail,
                duration_s=step.duration_s,
            )
            reasons.append(f"{gate}:{reason}")
        steps.append(step)

    return steps, reasons


def _run_flaky_checks(*, repo_root: Path, env: Mapping[str, str], qa_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    out: list[dict[str, Any]] = []
    for gate in _FLAKY_GATES:
        attempt_reasons: list[str] = []
        attempt_rows: list[dict[str, Any]] = []
        for idx in range(1, 4):
            attempt_dir = qa_dir / "flaky" / gate / f"attempt_{idx}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            attempt_env = _env_with(
                env,
                {
                    "FRBOT_MODE": gate,
                    "FRBOT_REAL_FRAMES_DIR": str(attempt_dir),
                },
            )
            res = _run_cmd(cwd=repo_root, argv=["poetry", "run", "python", "main.py"], env=attempt_env, timeout_s=420)
            reason = _collect_gate_reason(attempt_dir, gate)
            attempt_reasons.append(reason)
            attempt_rows.append(
                {
                    "attempt": idx,
                    "returncode": int(res.returncode),
                    "reason": reason,
                    "frames_dir": str(attempt_dir),
                    "last_result": str(attempt_dir / f"{gate}_last_result.json"),
                }
            )

        uniq = sorted(set(attempt_reasons))
        out.append(
            {
                "gate": gate,
                "attempts": attempt_rows,
                "reasons": uniq,
                "flaky": len(uniq) > 1,
            }
        )
        if len(uniq) > 1:
            return out, "flaky_real"

    return out, None


def _build_md_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# QA Certificación")
    lines.append("")
    lines.append(f"- Timestamp UTC: {payload.get('timestamp_utc', '')}")
    lines.append(f"- Resultado final: {payload.get('final_line', '')}")
    lines.append(f"- Reason canónico: {payload.get('reason', '')}")
    lines.append("")

    lines.append("## Precheck")
    pre = payload.get("precheck", {})
    inv = pre.get("artifacts", {}) if isinstance(pre, dict) else {}
    for k in sorted(inv.keys()):
        lines.append(f"- {k}: {'PASS' if inv[k] else 'FAIL'}")
    lines.append("")

    lines.append("## Steps")
    for row in payload.get("steps", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('name', '')}: {row.get('status', '')} | reason={row.get('reason', '')} | rc={row.get('returncode', '')}"
        )
        evs = row.get("evidence_paths", [])
        if isinstance(evs, list):
            for ev in evs:
                lines.append(f"  - evidence: {ev}")
    lines.append("")

    lines.append("## Gates")
    gate_summary = payload.get("gate_summary", {})
    if isinstance(gate_summary, dict) and gate_summary:
        for gate in _REQUIRED_GATES:
            row = gate_summary.get(gate, {}) if isinstance(gate_summary.get(gate, {}), dict) else {}
            lines.append(
                f"- {gate}: {row.get('status', 'SKIPPED')} | reason={row.get('reason', 'not_run')}"
            )
            for ev in row.get("evidence_paths", []) if isinstance(row.get("evidence_paths", []), list) else []:
                lines.append(f"  - evidence: {ev}")
    else:
        lines.append("- none")
    lines.append("")

    val = payload.get("evidence_validation", {})
    lines.append("## Validación evidencia")
    lines.append(f"- status: {'PASS' if val.get('ok') else 'FAIL'}")
    for r in val.get("reasons", []) if isinstance(val, dict) else []:
        lines.append(f"- reason: {r}")
    lines.append("")

    fl = payload.get("flaky_checks", [])
    if isinstance(fl, list) and fl:
        lines.append("## Anti-flakiness")
        for row in fl:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('gate', '')}: {'FLAKY' if row.get('flaky') else 'STABLE'} reasons={row.get('reasons', [])}")
        lines.append("")

    lines.append("## Fallos")
    failures = payload.get("failures", [])
    if isinstance(failures, list) and failures:
        for f in failures:
            if not isinstance(f, dict):
                continue
            lines.append(f"- reason: {f.get('reason', '')}")
            lines.append(f"  - repro: {f.get('repro', '')}")
            lines.append(f"  - fix: {f.get('fix', '')}")
            for ev in f.get("evidence_paths", []) if isinstance(f.get("evidence_paths", []), list) else []:
                lines.append(f"  - evidence: {ev}")
    else:
        lines.append("- none")

    blockers = payload.get("root_blockers", [])
    lines.append("")
    lines.append("## Root blockers")
    if isinstance(blockers, list) and blockers:
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Evidencia")
    for p in payload.get("artifact_paths", []):
        lines.append(f"- {p}")

    return "\n".join(lines).rstrip() + "\n"


def run_qa(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QA certificación repo")
    ap.add_argument("--real", action="store_true", help="Ejecuta ruta REAL con gates aislados y anti-flakiness")
    args = ap.parse_args(argv or [])

    repo_root = _repo_root()
    ts = _ts_slug()
    qa_dir = _qa_dir(repo_root, ts)
    qa_dir.mkdir(parents=True, exist_ok=True)

    # Determine frames dir for this QA run.
    base_env = dict(os.environ)
    raw_frames = str(base_env.get("FRBOT_REAL_FRAMES_DIR", "") or "").strip()
    if raw_frames:
        base_frames = Path(raw_frames)
    else:
        base_frames = _diag_dir(repo_root) / "frames_full"
    frames_dir = base_frames / ts

    precheck = _precheck_inventory(repo_root)

    report: dict[str, Any] = {
        "timestamp_utc": _utc_now().replace(microsecond=0).isoformat(),
        "repo_root": str(repo_root),
        "qa_dir": str(qa_dir),
        "mode": "real" if args.real else "standard",
        "precheck": precheck,
        "steps": [],
        "gate_summary": {
            gate: {
                "status": "SKIPPED",
                "reason": "not_run",
                "evidence_paths": [str(frames_dir / f"{gate}_last_result.json")],
            }
            for gate in _REQUIRED_GATES
        },
        "root_blockers": [],
        "failures": [],
        "evidence_validation": {},
        "flaky_checks": [],
        "artifact_paths": [],
        "reason": "",
        "final_line": "",
    }

    final_reason = "ok"
    final_exit = 0

    run_env = _ensure_base_real_env(base_env, frames_dir)
    pytest_env = _pytest_env(base_env)

    if args.real:
        missing = _real_env_missing(base_env)
        if missing:
            final_reason = "real_env_missing"
            final_exit = 1
            report["root_blockers"] = [f"env_missing:{m}" for m in missing]
            report["failures"] = [
                _failure(
                    reason="real_env_missing",
                    repro="Ejecutar qa_certify.ps1 -Real sin todas las variables FRBOT requeridas.",
                    evidence_paths=[str(qa_dir)],
                    fix="Definir FRBOT_CAPTURE_SOURCE=obs_source, FRBOT_OBS_SOURCE_NAME, FRBOT_WINDOW_TITLE o FRBOT_WINDOW_HWND, FRBOT_CONFIG_PATH y FRBOT_REAL_FRAMES_DIR.",
                )
            ]

    if final_exit == 0:
        run_env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
        run_env["FRBOT_PROFILE"] = "prod_full"
        run_env["FRBOT_CAPTURE_SOURCE"] = "obs_source"

        steps: list[StepRecord] = []

        pytest_step = _run_step(
            name="pytest",
            cmd=["poetry", "run", "pytest", "-q"],
            cwd=repo_root,
            env=pytest_env,
            evidence_paths=[],
            timeout_s=900,
        )
        if pytest_step.status == "FAIL":
            pytest_step = StepRecord(**{**asdict(pytest_step), "reason": "pytest_failed"})
            final_reason = "pytest_failed"
            final_exit = 2
            report["failures"].append(
                _failure(
                    reason="pytest_failed",
                    repro="Ejecutar poetry run pytest -q.",
                    evidence_paths=[str(qa_dir / "qa_report.json")],
                    fix="Corregir tests en fallo y volver a ejecutar la suite completa.",
                )
            )
        steps.append(pytest_step)

        audit_repo_step = _run_step(
            name="audit_repo_status",
            cmd=["poetry", "run", "python", "tools/audit_repo_status.py"],
            cwd=repo_root,
            env=run_env,
            evidence_paths=[str(_diag_dir(repo_root) / "status_repo.json")],
            timeout_s=300,
        )
        blockers = _status_repo_root_blockers(repo_root)
        if blockers and not report["root_blockers"]:
            report["root_blockers"] = list(blockers)
        if audit_repo_step.status == "PASS":
            if blockers:
                audit_repo_step = StepRecord(**{**asdict(audit_repo_step), "status": "FAIL", "reason": f"root_blockers:{blockers[0]}"})
                if final_exit == 0:
                    final_reason = f"root_blockers:{blockers[0]}"
                    final_exit = 2
                report["failures"].append(
                    _failure(
                        reason=f"root_blockers:{blockers[0]}",
                        repro="Ejecutar poetry run python tools/audit_repo_status.py.",
                        evidence_paths=[str(_diag_dir(repo_root) / "status_repo.json")],
                        fix="Resolver root_blockers del status_repo.json y reintentar.",
                    )
                )
        else:
            audit_reason = f"root_blockers:{blockers[0]}" if blockers else "audit_repo_status_failed"
            audit_repo_step = StepRecord(**{**asdict(audit_repo_step), "reason": audit_reason})
            if final_exit == 0:
                final_reason = audit_reason
                final_exit = 2
            report["failures"].append(
                _failure(
                    reason=audit_reason,
                    repro="Ejecutar poetry run python tools/audit_repo_status.py.",
                    evidence_paths=[str(_diag_dir(repo_root) / "status_repo.json")],
                    fix="Revisar salida del auditor y corregir precondiciones/root blockers.",
                )
            )
        steps.append(audit_repo_step)

        if args.real and final_exit == 0:
            isolated_steps, isolated_reasons = _run_real_isolated_gates(repo_root=repo_root, env=run_env, frames_dir=frames_dir)
            steps.extend(isolated_steps)
            for s in isolated_steps:
                gate_name = str(s.name).replace("gate:", "", 1).strip()
                if gate_name in report["gate_summary"]:
                    report["gate_summary"][gate_name] = {
                        "status": "PASS" if s.status == "PASS" else "FAIL",
                        "reason": str(s.reason),
                        "evidence_paths": [str(frames_dir / f"{gate_name}_last_result.json")],
                    }
            if isolated_reasons:
                report["root_blockers"].extend(isolated_reasons)
                final_reason = f"gate_failed:{isolated_reasons[0]}"
                final_exit = 1
                report["failures"].append(
                    _failure(
                        reason=f"gate_failed:{isolated_reasons[0]}",
                        repro="Ejecutar gates individuales con FRBOT_MODE=targeting_full|healing_full|combat_full|cavebot_full|looting_full|deposit_full|trade_full.",
                        evidence_paths=[str(frames_dir)],
                        fix="Revisar *_last_result.json del gate fallido y corregir binding/ROI/evidencia según reason.",
                    )
                )

            flaky_report, flaky_reason = _run_flaky_checks(repo_root=repo_root, env=run_env, qa_dir=qa_dir)
            report["flaky_checks"] = flaky_report
            if flaky_reason is not None and final_exit == 0:
                final_reason = flaky_reason
                final_exit = 1
                report["root_blockers"].append(flaky_reason)
                report["failures"].append(
                    _failure(
                        reason="flaky_real",
                        repro="Re-ejecutar 3 veces targeting_full, healing_full, cavebot_full con mismo setup REAL.",
                        evidence_paths=[str(qa_dir / "flaky")],
                        fix="Eliminar fuentes de no determinismo (binding/foreground/ROI/config) hasta que reason sea estable.",
                    )
                )

        release_step = _run_step(
            name="release_prod_full",
            cmd=["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "run_release_prod_full.ps1"],
            cwd=repo_root,
            env=run_env,
            evidence_paths=[str(repo_root / "run_release_prod_full.ps1")],
            timeout_s=600,
        )
        release_line = _parse_release_line(release_step.stdout_tail + "\n" + release_step.stderr_tail)
        if release_step.status == "FAIL" or release_line != "RELEASE_GO":
            reason = "release_no_go"
            if release_line.startswith("RELEASE_NO_GO:"):
                reason = release_line.split(":", 1)[1].strip() or "release_no_go"
            release_step = StepRecord(**{**asdict(release_step), "status": "FAIL", "reason": reason})
            if final_exit == 0:
                final_reason = reason
                final_exit = 1
            report["failures"].append(
                _failure(
                    reason=str(reason),
                    repro="Ejecutar powershell -File run_release_prod_full.ps1 y verificar línea final RELEASE_GO.",
                    evidence_paths=[str(repo_root / "run_release_prod_full.ps1"), str(_diag_dir(repo_root) / "releases")],
                    fix="Corregir bloqueador reportado por RELEASE_NO_GO y repetir release.",
                )
            )
        steps.append(release_step)

        audit_prod_step = _run_step(
            name="audit_prod_full",
            cmd=["poetry", "run", "python", "tools/audit_prod_full.py"],
            cwd=repo_root,
            env=run_env,
            evidence_paths=[str(frames_dir)],
            timeout_s=300,
        )
        final_decision = _parse_final_decision(audit_prod_step.stdout_tail + "\n" + audit_prod_step.stderr_tail)
        if audit_prod_step.status == "FAIL" or final_decision != "OPERATIONAL_REAL":
            reason = "audit_prod_full_not_operational"
            if final_decision and final_decision != "OPERATIONAL_REAL":
                reason = f"audit_prod_full:{final_decision}"
            audit_prod_step = StepRecord(**{**asdict(audit_prod_step), "status": "FAIL", "reason": reason})
            if final_exit == 0:
                final_reason = reason
                final_exit = 1
            report["failures"].append(
                _failure(
                    reason=str(reason),
                    repro="Ejecutar poetry run python tools/audit_prod_full.py y exigir FINAL DECISION: OPERATIONAL_REAL.",
                    evidence_paths=[str(frames_dir)],
                    fix="Completar evidencia por gate y corregir reasons reportados por audit_prod_full.",
                )
            )
        steps.append(audit_prod_step)

        # Resolve frames dir using release artifact when available.
        release_last = _latest_release_result(repo_root)
        if release_last is not None:
            try:
                release_data = _load_json(release_last)
                rel_frames = str(release_data.get("frames_dir", "") or "").strip()
                if rel_frames:
                    frames_dir = Path(rel_frames)
            except Exception:
                pass

        evidence_ok, evidence_reasons, evidence_details = _validate_evidence_schema(
            repo_root=repo_root,
            frames_dir=frames_dir,
        )
        report["evidence_validation"] = {
            "ok": bool(evidence_ok),
            "reasons": list(evidence_reasons),
            "details": evidence_details,
        }
        gates_detail = evidence_details.get("gates", {}) if isinstance(evidence_details, dict) else {}
        if isinstance(gates_detail, dict):
            for gate in _REQUIRED_GATES:
                row = gates_detail.get(gate, {}) if isinstance(gates_detail.get(gate, {}), dict) else {}
                status = "PASS" if bool(row.get("ok")) else "FAIL"
                reason = str(row.get("reason", "unknown"))
                path = str(row.get("path", frames_dir / f"{gate}_last_result.json"))
                report["gate_summary"][gate] = {
                    "status": status,
                    "reason": reason,
                    "evidence_paths": [path],
                }
        if not evidence_ok:
            final_reason = "evidence_schema_invalid"
            final_exit = 1
            report["root_blockers"].extend(evidence_reasons)
            report["failures"].append(
                _failure(
                    reason="evidence_schema_invalid",
                    repro="Validar runtime.log JSONL, fatal.log JSON y schema mínimo de *_last_result.json + PPMs referenciados.",
                    evidence_paths=[str(_diag_dir(repo_root) / "runtime.log"), str(_diag_dir(repo_root) / "fatal.log"), str(frames_dir)],
                    fix="Asegurar campos obligatorios (ok, reason, evidence_kind, inputs_sent, before_ppm, after_ppm) y artefactos PPM válidos por gate.",
                )
            )

        report["steps"] = [asdict(s) for s in steps]

    # Always emit report artifacts.
    report["artifact_paths"] = [
        str(qa_dir / "qa_report.json"),
        str(qa_dir / "qa_report.md"),
        str(_diag_dir(repo_root) / "runtime.log"),
        str(_diag_dir(repo_root) / "fatal.log"),
        str(frames_dir),
    ]

    if final_exit == 0:
        report["reason"] = "ok"
        report["final_line"] = "QA_GO"
    else:
        report["reason"] = str(final_reason or "unknown")
        report["final_line"] = f"QA_NO_GO:{report['reason']}"

    _write_json(qa_dir / "qa_report.json", report)
    _write_text(qa_dir / "qa_report.md", _build_md_report(report))

    print(str(report["final_line"]))
    return int(final_exit)


def main() -> int:
    try:
        return int(run_qa(sys.argv[1:]))
    except Exception:
        repo_root = _repo_root()
        ts = _ts_slug()
        qa_dir = _qa_dir(repo_root, ts)
        qa_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_utc": _utc_now().replace(microsecond=0).isoformat(),
            "reason": "internal_error",
            "final_line": "QA_NO_GO:internal_error",
        }
        _write_json(qa_dir / "qa_report.json", payload)
        _write_text(qa_dir / "qa_report.md", "# QA Certificación\n\n- Resultado final: QA_NO_GO:internal_error\n")
        print("QA_NO_GO:internal_error")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
