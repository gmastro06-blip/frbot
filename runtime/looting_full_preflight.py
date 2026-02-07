from __future__ import annotations

from typing import TypeAlias

from runtime.looting_basic_preflight import (
    CaptureAdapter,
    InputAdapter,
    WindowBindingAdapter,
    looting_basic_preflight,
)
from contracts.runtime import RuntimeContext


def looting_full_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Preflight for looting_full.

    Contract: identical to looting_basic preflight in prod_emergency.
    """

    return looting_basic_preflight(ctx)


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return looting_full_preflight(ctx)
