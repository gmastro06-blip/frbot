from __future__ import annotations

from typing import Optional

from contracts.capture import Frame


def try_dump_window_frame(*, gate: str, reason: str) -> bool:
    """Best-effort capture dump when normal BEFORE/AFTER frames are missing.

    This must never create runtime.log; it only writes frame files if possible.
    """

    try:
        from diagnostics.frame_dump import dump_pair
        from runtime.preflight import preflight
        from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
        from contracts.errors import PreflightFailed

        # Try a minimal preflight using current environment settings.
        import os

        from pathlib import Path

        def _evidence_dir() -> str:
            raw = str(os.environ.get('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
            if raw:
                return raw
            prof = str(os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
            if prof == 'prod_emergency':
                return str((Path('diagnostics') / 'frames_emergency').as_posix())
            if prof == 'prod_full':
                return str((Path('diagnostics') / 'frames_full').as_posix())
            return str((Path('diagnostics') / 'frames').as_posix())

        backend = (os.environ.get(f'FRBOT_{str(gate).strip().upper()}_BACKEND') or os.environ.get('FRBOT_MODE') or 'real').strip().lower()
        ctx = RuntimeContext(
            config=RuntimeConfig(mode=backend, config_path=os.environ.get('FRBOT_CONFIG_PATH', '')),
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        try:
            capture, _input, binding = preflight(ctx)
        except PreflightFailed:
            return False

        try:
            binding.assert_bound()
        except Exception:
            return False

        frame: Optional[Frame] = None
        try:
            frame = capture.grab()
        except Exception:
            frame = None

        if frame is None:
            return False

        dump_pair(
            gate=str(gate),
            before=frame,
            after=None,
            reason=f'preflight_abort_{reason}',
            out_dir=_evidence_dir(),
        )
        return True
    except Exception:
        return False


def try_dump_window_frame_pair(*, gate: str, reason: str) -> bool:
    """Best-effort BEFORE+AFTER dump when normal BEFORE/AFTER frames are missing.

    This must never create runtime.log; it only writes frame files if possible.
    """

    try:
        from diagnostics.frame_dump import dump_pair
        from runtime.preflight import preflight
        from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
        from contracts.errors import PreflightFailed

        # Try a minimal preflight using current environment settings.
        import os

        from pathlib import Path

        def _evidence_dir() -> str:
            raw = str(os.environ.get('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
            if raw:
                return raw
            prof = str(os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
            if prof == 'prod_emergency':
                return str((Path('diagnostics') / 'frames_emergency').as_posix())
            if prof == 'prod_full':
                return str((Path('diagnostics') / 'frames_full').as_posix())
            return str((Path('diagnostics') / 'frames').as_posix())

        backend = (os.environ.get(f'FRBOT_{str(gate).strip().upper()}_BACKEND') or os.environ.get('FRBOT_MODE') or 'real').strip().lower()
        ctx = RuntimeContext(
            config=RuntimeConfig(mode=backend, config_path=os.environ.get('FRBOT_CONFIG_PATH', '')),
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        try:
            capture, _input, binding = preflight(ctx)
        except PreflightFailed:
            return False

        try:
            binding.assert_bound()
        except Exception:
            return False

        try:
            before = capture.grab()
            after = capture.grab()
        except Exception:
            return False

        dump_pair(
            gate=str(gate),
            before=before,
            after=after,
            reason=f'preflight_abort_{reason}',
            out_dir=_evidence_dir(),
        )
        return True
    except Exception:
        return False
