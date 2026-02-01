from __future__ import annotations

import argparse
import os

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair
from runtime.preflight import preflight


def _ctx(backend: str, config_path: str) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode=str(backend).strip().lower(),
        config_path=str(config_path or '').strip(),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
    )
    return RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description='Capture an idle BEFORE/AFTER frame pair (no input).')
    ap.add_argument('--backend', default=os.environ.get('FRBOT_CAVEBOT_BACKEND', os.environ.get('FRBOT_MODE', 'real')))
    ap.add_argument('--config', default=os.environ.get('FRBOT_CONFIG_PATH', ''))
    ap.add_argument('--gate', default=os.environ.get('FRBOT_MODE', 'cavebot'))
    args = ap.parse_args()

    backend = str(args.backend or 'real').strip().lower()
    gate = str(args.gate or 'cavebot').strip().lower()
    config_path = str(args.config or '').strip()

    ctx = _ctx(backend=backend, config_path=config_path)

    try:
        capture, _input, binding = preflight(ctx)

        try:
            binding.assert_bound()
        except Exception:
            raise PreflightFailed('window_binding_lost')

        before = capture.grab()
        after = capture.grab()

        dump_pair(gate=gate, before=before, after=after, reason='idle_baseline')
        return 0

    except PreflightFailed as exc:
        write_fatal(f'idle_capture_abort gate={gate} abort_reason={exc}', exc)
        return 2
    except Exception as exc:
        write_fatal(f'idle_capture_crash gate={gate}', exc)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
