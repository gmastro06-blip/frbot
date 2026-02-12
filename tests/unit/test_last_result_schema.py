from __future__ import annotations

import json
from pathlib import Path

from tools import audit_prod_full


def test_last_result_schema_requires_after_ppm(tmp_path: Path) -> None:
    frames_dir = tmp_path
    (frames_dir / "sample.ppm").write_text("P3\n1 1\n255\n0 0 0\n", encoding="utf-8")

    gate = "targeting_full"
    payload = {
        "ok": True,
        "reason": "ok",
        "evidence_kind": "unit_test",
        "inputs_sent": 0,
        "before_ppm": "sample.ppm",
        # after_ppm intentionally missing
    }
    (frames_dir / f"{gate}_last_result.json").write_text(json.dumps(payload), encoding="utf-8")

    reasons = audit_prod_full._check_gate_last_result(frames_dir, gate)
    assert f"missing_after_ppm:{gate}" in reasons
