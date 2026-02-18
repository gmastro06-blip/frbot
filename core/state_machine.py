from __future__ import annotations

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext, RuntimeState


def require_ready(ctx: RuntimeContext) -> None:
    """Pure rule: runtime may only run when READY."""
    if ctx.status.state != RuntimeState.READY:
        raise PreflightFailed(f"context not READY (state={ctx.status.state})")


def mark_running(ctx: RuntimeContext) -> None:
    ctx.status.state = RuntimeState.RUNNING
