"""Tests for _dialog_phrase_from_waypoint in cavebot_runner.

Covers both the new call_npc format (waypoint_type='call_npc', call/payload)
and the legacy format (action_kind='call', legacy_call/legacy_payload).
"""
from __future__ import annotations

import os
import pytest

from contracts.runtime import Waypoint
from runtime.cavebot_runner import _dialog_phrase_from_waypoint


def _make_wp(
    *,
    waypoint_type: str = "walk",
    options: dict | None = None,
) -> Waypoint:
    return Waypoint(
        waypoint_id="test",
        x=64,
        y=38,
        z=7,
        radius_px=2,
        max_ticks=30,
        waypoint_type=waypoint_type,
        options=dict(options or {}),
    )


# ---------------------------------------------------------------------------
# New call_npc format (waypoint_type='call_npc')
# ---------------------------------------------------------------------------


class TestCallNpcFormat:
    def test_talk_npc_returns_default_greeting(self):
        wp = _make_wp(
            waypoint_type="call_npc",
            options={"action_kind": "call_npc", "call": "talk_npc", "payload": ""},
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "hi"

    def test_talk_npc_respects_env_greeting(self, monkeypatch):
        monkeypatch.setenv("FRBOT_CAVEBOT_NPC_GREETING", "hello")
        wp = _make_wp(
            waypoint_type="call_npc",
            options={"action_kind": "call_npc", "call": "talk_npc", "payload": ""},
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "hello"

    def test_say_with_sentence_json_payload(self):
        wp = _make_wp(
            waypoint_type="call_npc",
            options={
                "action_kind": "call_npc",
                "call": "say",
                "payload": '{"sentence":"trade"}',
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "trade"

    def test_say_with_plain_text_payload(self):
        wp = _make_wp(
            waypoint_type="call_npc",
            options={"action_kind": "call_npc", "call": "say", "payload": "yes"},
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "yes"

    def test_say_with_empty_payload_returns_empty(self):
        wp = _make_wp(
            waypoint_type="call_npc",
            options={"action_kind": "call_npc", "call": "say", "payload": ""},
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == ""

    def test_unknown_call_returns_empty(self):
        wp = _make_wp(
            waypoint_type="call_npc",
            options={"action_kind": "call_npc", "call": "unknown_call", "payload": ""},
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == ""

    def test_action_kind_call_npc_without_wp_type(self):
        """action_kind='call_npc' on a walk waypoint should still trigger the new path."""
        wp = _make_wp(
            waypoint_type="walk",
            options={"action_kind": "call_npc", "call": "talk_npc", "payload": ""},
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "hi"

    def test_say_payload_sentence_case_insensitive(self):
        wp = _make_wp(
            waypoint_type="call_npc",
            options={
                "action_kind": "call_npc",
                "call": "say",
                "payload": '{"Sentence":"Hello"}',
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "hello"


# ---------------------------------------------------------------------------
# Legacy format (action_kind='call', legacy_call/legacy_payload)
# ---------------------------------------------------------------------------


class TestLegacyCallFormat:
    def test_legacy_talk_npc_returns_default_greeting(self):
        wp = _make_wp(
            waypoint_type="walk_ignore",
            options={
                "action_kind": "call",
                "legacy_call": "talk_npc",
                "legacy_payload": "",
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "hi"

    def test_legacy_talk_npc_respects_env_greeting(self, monkeypatch):
        monkeypatch.setenv("FRBOT_CAVEBOT_NPC_GREETING", "greetings")
        wp = _make_wp(
            waypoint_type="walk_ignore",
            options={
                "action_kind": "call",
                "legacy_call": "talk_npc",
                "legacy_payload": "",
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "greetings"

    def test_legacy_say_with_sentence_json(self):
        wp = _make_wp(
            waypoint_type="walk_ignore",
            options={
                "action_kind": "call",
                "legacy_call": "say",
                "legacy_payload": '{"sentence":"deposit"}',
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "deposit"

    def test_legacy_say_plain_text(self):
        wp = _make_wp(
            waypoint_type="walk_ignore",
            options={
                "action_kind": "call",
                "legacy_call": "say",
                "legacy_payload": "bank",
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == "bank"

    def test_legacy_unknown_call_returns_empty(self):
        wp = _make_wp(
            waypoint_type="walk_ignore",
            options={
                "action_kind": "call",
                "legacy_call": "unknown_legacy",
                "legacy_payload": "",
            },
        )
        result = _dialog_phrase_from_waypoint(wp)
        assert result == ""


# ---------------------------------------------------------------------------
# Non-dialog waypoints return empty
# ---------------------------------------------------------------------------


class TestNonDialogWaypoints:
    def test_walk_waypoint_returns_empty(self):
        wp = _make_wp(waypoint_type="walk")
        assert _dialog_phrase_from_waypoint(wp) == ""

    def test_rope_waypoint_returns_empty(self):
        wp = _make_wp(waypoint_type="rope")
        assert _dialog_phrase_from_waypoint(wp) == ""

    def test_no_options_returns_empty(self):
        wp = _make_wp(waypoint_type="walk", options={})
        assert _dialog_phrase_from_waypoint(wp) == ""

    def test_non_dict_options_returns_empty(self):
        # options field that cannot be iterated as a dict
        wp = Waypoint(
            waypoint_id="t",
            x=0,
            y=0,
            z=7,
            radius_px=2,
            max_ticks=30,
            waypoint_type="walk",
            options={},
        )
        # Patch options to a non-dict value via object attribute mutation workaround
        # contracts.runtime.Waypoint is frozen; test the branch via subclass sim.
        class _FakeWP:
            options = "not-a-dict"
            waypoint_type = "walk"

        result = _dialog_phrase_from_waypoint(_FakeWP())  # type: ignore[arg-type]
        assert result == ""
