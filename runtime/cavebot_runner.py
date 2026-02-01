from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import CaptureAdapter, Frame
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import RuntimeContext, Waypoint
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from runtime.cavebot import CavebotTickInput, tick
from runtime.cavebot_semantics import ProgressResult, compute_progress, detect_player_marker, is_progress_valid


@dataclass(frozen=True, slots=True)
class CavebotTickEvidence:
    marker_before: Optional[object]
    marker_after: Optional[object]
    progress: Optional[ProgressResult]
    status: str


@dataclass(frozen=True, slots=True)
class CavebotTickOutcome:
    reached_waypoint: bool
    evidence: CavebotTickEvidence
    abort_reason: Optional[str]


def _waypoint_reached(marker: Optional[object], waypoint: Waypoint) -> bool:
    # Reached when marker is within min_pixel_delta of waypoint target.
    if marker is None:
        return False
    # marker is MinimapMarker but keep this helper robust.
    mx = int(getattr(marker, 'x_px', -1))
    my = int(getattr(marker, 'y_px', -1))
    dx = abs(mx - int(waypoint.x))
    dy = abs(my - int(waypoint.y))
    return (dx + dy) <= int(waypoint.min_pixel_delta)


def _progress_from_frames(ctx: RuntimeContext, before_f: Frame, after_f: Frame, waypoint: Waypoint) -> tuple[Optional[ProgressResult], str]:
    marker_before = detect_player_marker(
        before_f,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker_before is None:
        return None, 'cavebot_marker_not_found'

    marker_after = detect_player_marker(
        after_f,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker_after is None:
        return None, 'cavebot_marker_not_found'

    progress = compute_progress(marker_before, marker_after, waypoint)

    # Aborts are decided in the runner (evidence-or-abort).
    if int(progress.delta_mag_px) == 0:
        # No movement evidence. Retryable within attempt/tick guardrails.
        return progress, 'cavebot_no_progress_retryable'

    if int(progress.delta_mag_px) < int(waypoint.min_pixel_delta):
        # Jitter/noise moved the marker but not enough to count as progress.
        # This is treated as fatal ambiguity/noise.
        return progress, 'cavebot_no_progress'

    if not bool(progress.in_expected_direction) or not bool(progress.moved_toward_waypoint):
        return progress, 'cavebot_wrong_direction'

    if not is_progress_valid(progress, waypoint):
        return progress, 'cavebot_no_progress'

    return progress, 'ok'


def _parse_rgb(raw: str) -> tuple[int, int, int]:
    s = (raw or '').strip()
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) != 3:
        return (255, 0, 255)
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return (255, 0, 255)


def execute_cavebot_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    waypoint: Waypoint,
    tick_index: int,
) -> CavebotTickOutcome:
    """Execute exactly one cavebot tick.

    Returns True only when the final waypoint is reached with valid progress.
    """

    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    before = capture.grab()
    record_before('cavebot', before)

    marker_before = detect_player_marker(
        before,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker_before is None:
        return CavebotTickOutcome(
            reached_waypoint=False,
            evidence=CavebotTickEvidence(marker_before=None, marker_after=None, progress=None, status='cavebot_marker_not_found'),
            abort_reason='cavebot_marker_not_found',
        )

    # Rule engine emits at most one input.
    intent, abort = tick(
        CavebotTickInput(
            waypoint=waypoint,
            ticks_in_waypoint=int(ctx.cavebot.gate_ticks_in_waypoint),
            attempts_used=int(ctx.cavebot.gate_attempts_used),
            max_attempts_per_waypoint=int(ctx.config.cavebot_max_attempts_per_waypoint),
            max_ticks_per_waypoint=min(
                int(ctx.config.cavebot_max_ticks_per_waypoint),
                int(waypoint.max_ticks_without_progress),
            ),
        )
    )
    if abort is not None:
        return CavebotTickOutcome(
            reached_waypoint=False,
            evidence=CavebotTickEvidence(marker_before=marker_before, marker_after=None, progress=None, status=str(abort.reason)),
            abort_reason=str(abort.reason),
        )
    if intent is None:
        return CavebotTickOutcome(
            reached_waypoint=False,
            evidence=CavebotTickEvidence(marker_before=marker_before, marker_after=None, progress=None, status='cavebot_no_progress'),
            abort_reason='cavebot_no_progress',
        )

    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    input_.press_key(str(intent.key))
    ctx.cavebot.gate_inputs_sent += 1

    after = capture.grab()
    record_after('cavebot', after)

    progress, status = _progress_from_frames(ctx, before, after, waypoint)

    marker_after = detect_player_marker(
        after,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )

    evidence = CavebotTickEvidence(
        marker_before=marker_before,
        marker_after=marker_after,
        progress=progress,
        status=str(status),
    )

    if status == 'cavebot_marker_not_found':
        return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_marker_not_found')

    if status == 'cavebot_wrong_direction':
        return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_wrong_direction')

    if status == 'cavebot_no_progress':
        # Fatal: movement happened but is not valid progress.
        return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_no_progress')

    if status == 'cavebot_no_progress_retryable':
        # Retryable failure: consume attempt.
        ctx.cavebot.gate_ticks_in_waypoint += 1
        ctx.cavebot.gate_attempts_used += 1
        if ctx.cavebot.gate_attempts_used >= int(ctx.config.cavebot_max_attempts_per_waypoint):
            return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_waypoint_stuck')
        if ctx.cavebot.gate_ticks_in_waypoint >= min(
            int(ctx.config.cavebot_max_ticks_per_waypoint),
            int(waypoint.max_ticks_without_progress),
        ):
            return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_waypoint_stuck')
        return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason=None)

    # status == ok
    ctx.cavebot.gate_attempts_used = 0
    ctx.cavebot.gate_ticks_in_waypoint = 0

    return CavebotTickOutcome(
        reached_waypoint=_waypoint_reached(marker_after, waypoint),
        evidence=evidence,
        abort_reason=None,
    )
