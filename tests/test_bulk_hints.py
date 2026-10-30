# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for BulkHintTracker, the response-time nudge that steers
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
        assert BulkHintTracker.record_and_hint("obj_create") is None

    def test_two_calls_still_return_no_hint(self):
        BulkHintTracker.record_and_hint("obj_create")
        assert BulkHintTracker.record_and_hint("obj_create") is None

    def test_third_rapid_call_trips_hint(self):
        BulkHintTracker.record_and_hint("obj_create")
        BulkHintTracker.record_and_hint("obj_create")
        hint = BulkHintTracker.record_and_hint("obj_create")
        assert hint is not None
        assert hint["bulk_tool"] == "obj_create"
        assert "items=" in hint["hint"]
        assert "obj_create" in hint["hint"]

    def test_hint_fires_once_per_window(self):
        for _ in range(3):
            BulkHintTracker.record_and_hint("obj_create")
        assert BulkHintTracker.record_and_hint("obj_create") is None

    def test_unknown_tool_returns_no_hint(self):
        for _ in range(10):
            assert BulkHintTracker.record_and_hint("some_other_tool") is None

    def test_a_tool_that_takes_items_is_pointed_at_them(self):
        for _ in range(2):
            assert BulkHintTracker.record_and_hint("pcb_place_arc",
                                                   takes_items=True) is None
        hint = BulkHintTracker.record_and_hint("pcb_place_arc", takes_items=True)
        assert hint["bulk_tool"] == "pcb_place_arc"
        assert "pcb_place_arc items=[" in hint["hint"]

    def test_a_repeated_single_call_through_the_wrapper_carries_the_hint(self):
        import asyncio

        from eda_agent.tools.bulk import with_items

        async def place_thing(x: float) -> dict:
            return {"placed": x}
        tool = with_items(place_thing, "pcb_place_thing")
        replies = [asyncio.run(tool(x=i)) for i in range(3)]
        assert "_hint_bulk" not in replies[0] and "_hint_bulk" not in replies[1]
        assert "items=" in replies[2]["_hint_bulk"]["hint"]

    def test_library_footprint_pad_nudges_to_bulk(self):
        # looping the singular footprint-pad tool steers to the batch tool
        hint = None
        for _ in range(8):
            hint = BulkHintTracker.record_and_hint("lib_add_footprint_pad")
            if hint:
                break
        assert hint is not None
        assert hint["bulk_tool"] == "lib_add_footprint_pads"

    def test_library_footprint_track_nudges_to_bulk(self):
        hint = None
        for _ in range(8):
            hint = BulkHintTracker.record_and_hint("lib_add_footprint_track")
            if hint:
                break
        assert hint is not None
        assert hint["bulk_tool"] == "lib_add_footprint_tracks"

    def test_design_authoring_tools_nudge_to_compose(self):
        # looping add_part / add_block / connect_bus steers to compose_netlist
        for tool in ("design_add_part", "design_add_circuit_block",
                     "design_connect_bus"):
            BulkHintTracker.reset()
            hint = None
            for _ in range(5):
                hint = BulkHintTracker.record_and_hint(tool)
                if hint:
                    break
            assert hint is not None, tool
            assert hint["bulk_tool"] == "design_compose_netlist", tool

    def test_compose_netlist_never_self_nudges(self):
        # the bulk tool itself must not be registered as a singular source
        for _ in range(10):
            assert BulkHintTracker.record_and_hint(
                "design_compose_netlist") is None

    def test_distinct_tools_tracked_separately(self):
        for _ in range(2):
            BulkHintTracker.record_and_hint("obj_create")
            BulkHintTracker.record_and_hint("obj_delete")
        assert BulkHintTracker.record_and_hint("obj_create")["bulk_tool"] == "obj_create"
        assert BulkHintTracker.record_and_hint("obj_delete")["bulk_tool"] == "obj_delete"

    def test_slow_calls_do_not_trip_hint(self, monkeypatch):
        """Calls spaced farther apart than the window should not trip the hint."""
        fake_now = [0.0]
        monkeypatch.setattr(
            "eda_agent.tools.bulk_hints.time.monotonic",
            lambda: fake_now[0],
        )
        for _ in range(5):
            BulkHintTracker.record_and_hint("obj_create")
            fake_now[0] += BulkHintTracker._WINDOW_SEC + 1.0
        assert len(BulkHintTracker._windows["obj_create"]) == 1

    def test_modify_objects_nudges_to_its_items(self):
        for _ in range(2):
            BulkHintTracker.record_and_hint("obj_modify")
        hint = BulkHintTracker.record_and_hint("obj_modify")
        assert hint is not None
        assert hint["bulk_tool"] == "obj_modify"
        assert "items=" in hint["hint"]
