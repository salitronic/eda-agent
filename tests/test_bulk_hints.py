# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for BulkHintTracker — the response-time nudge that steers
callers toward bulk tools when the same singular tool is hit repeatedly.
"""

from __future__ import annotations

import time

import pytest

from eda_agent.tools.bulk_hints import BulkHintTracker


@pytest.fixture(autouse=True)
def _reset_tracker():
    BulkHintTracker.reset()
    yield
    BulkHintTracker.reset()


class TestBulkHintTracker:
    def test_single_call_returns_no_hint(self):
        assert BulkHintTracker.record_and_hint("pcb_move_component") is None

    def test_two_calls_still_return_no_hint(self):
        BulkHintTracker.record_and_hint("pcb_move_component")
        assert BulkHintTracker.record_and_hint("pcb_move_component") is None

    def test_third_rapid_call_trips_hint(self):
        BulkHintTracker.record_and_hint("pcb_move_component")
        BulkHintTracker.record_and_hint("pcb_move_component")
        hint = BulkHintTracker.record_and_hint("pcb_move_component")
        assert hint is not None
        assert hint["bulk_tool"] == "pcb_move_components"
        assert "pcb_move_components" in hint["hint"]
        assert "pcb_move_component" in hint["hint"]

    def test_hint_fires_once_per_window(self):
        for _ in range(3):
            BulkHintTracker.record_and_hint("pcb_move_component")
        assert BulkHintTracker.record_and_hint("pcb_move_component") is None

    def test_unknown_tool_returns_no_hint(self):
        for _ in range(10):
            assert BulkHintTracker.record_and_hint("some_other_tool") is None

    def test_distinct_tools_tracked_separately(self):
        for _ in range(2):
            BulkHintTracker.record_and_hint("pcb_move_component")
            BulkHintTracker.record_and_hint("pcb_place_track")
        assert BulkHintTracker.record_and_hint("pcb_move_component")["bulk_tool"] == "pcb_move_components"
        assert BulkHintTracker.record_and_hint("pcb_place_track")["bulk_tool"] == "pcb_place_tracks"

    def test_slow_calls_do_not_trip_hint(self, monkeypatch):
        """Calls spaced farther apart than the window should not trip the hint."""
        fake_now = [0.0]
        monkeypatch.setattr(
            "eda_agent.tools.bulk_hints.time.monotonic",
            lambda: fake_now[0],
        )
        for _ in range(5):
            BulkHintTracker.record_and_hint("pcb_move_component")
            fake_now[0] += BulkHintTracker._WINDOW_SEC + 1.0
        assert len(BulkHintTracker._windows["pcb_move_component"]) == 1

    def test_modify_objects_nudges_to_batch_modify(self):
        for _ in range(2):
            BulkHintTracker.record_and_hint("modify_objects")
        hint = BulkHintTracker.record_and_hint("modify_objects")
        assert hint is not None
        assert hint["bulk_tool"] == "batch_modify"
