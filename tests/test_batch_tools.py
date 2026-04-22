# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for the new batch tools (batch_create, batch_delete, place_wires,
place_sch_components_from_library, sch_attach_spice_primitives,
lib_add_pins).

The Python side builds pipe-separated '~~' batch strings; verify the
formatting and parameter wiring against a fake bridge.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.bulk_hints import BulkHintTracker


@pytest.fixture(autouse=True)
def _reset_tracker():
    BulkHintTracker.reset()
    yield
    BulkHintTracker.reset()


class _Sent:
    def __init__(self):
        self.command = None
        self.params = None
        self.timeout = None


def _install_fake_bridge(monkeypatch, module_path: str) -> _Sent:
    sent = _Sent()

    class FakeBridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.command = command
            sent.params = params
            sent.timeout = timeout
            return {"ok": True}

    monkeypatch.setattr(f"{module_path}.get_bridge", lambda: FakeBridge())
    return sent


def _capture(module, register_fn_name: str):
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    getattr(module, register_fn_name)(DummyMcp())
    return captured


class TestBulkHintEquivalents:
    def test_every_new_singular_has_a_bulk_nudge(self):
        # The 6 new bulk tools each need their singular wired into the tracker.
        assert "create_object" in BulkHintTracker.BULK_EQUIVALENTS
        assert "delete_objects" in BulkHintTracker.BULK_EQUIVALENTS
        assert "place_wire" in BulkHintTracker.BULK_EQUIVALENTS
        assert (
            "place_sch_component_from_library"
            in BulkHintTracker.BULK_EQUIVALENTS
        )
        assert (
            "sch_attach_spice_primitive" in BulkHintTracker.BULK_EQUIVALENTS
        )
        assert "lib_add_pin" in BulkHintTracker.BULK_EQUIVALENTS

    def test_bulk_nudge_targets_are_valid(self):
        # Every nudged tool must point at a bulk equivalent whose name
        # shares at least one stem with the singular, so the nudge text
        # makes sense to a reader. Self-references are allowed for tools
        # whose nudge is a usage-pattern change (e.g. get_nets: "call it
        # unfiltered once instead of looping").
        for singular, (bulk, _) in BulkHintTracker.BULK_EQUIVALENTS.items():
            stem = singular.split("_")
            assert any(tok in bulk for tok in stem), (
                f"{singular} -> {bulk} doesn't share a stem"
            )


class TestBatchCreate:
    @pytest.mark.asyncio
    async def test_format_uses_double_tilde_separator(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        await tools["batch_create"](operations=[
            {"object_type": "eNetLabel",
             "properties": "Text=VCC|Location.X=100|Location.Y=200"},
            {"object_type": "eNetLabel",
             "properties": "Text=GND|Location.X=100|Location.Y=400"},
        ])
        assert sent.command == "generic.batch_create"
        ops = sent.params["operations"]
        assert "~~" in ops
        assert ops.count("~~") == 1  # 2 ops -> 1 separator
        assert "object_type=eNetLabel" in ops
        assert "Text=VCC|Location.X=100|Location.Y=200" in ops

    @pytest.mark.asyncio
    async def test_empty_operations_returns_error(self, monkeypatch):
        _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        result = await tools["batch_create"](operations=[])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_object_type_is_skipped(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        await tools["batch_create"](operations=[
            {"object_type": "eJunction", "properties": "Location.X=1"},
            {"properties": "Text=orphan"},  # no object_type → drop
        ])
        # Exactly one op survived → no separator in the final string.
        assert "~~" not in sent.params["operations"]


class TestBatchDelete:
    @pytest.mark.asyncio
    async def test_each_op_carries_scope_type_filter(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        await tools["batch_delete"](operations=[
            {"scope": "active_doc", "object_type": "eNoERC", "filter": ""},
            {"scope": "project", "object_type": "eJunction", "filter": ""},
        ])
        ops = sent.params["operations"].split("~~")
        assert len(ops) == 2
        assert "scope=active_doc" in ops[0]
        assert "object_type=eNoERC" in ops[0]
        assert "scope=project" in ops[1]


class TestPlaceWires:
    @pytest.mark.asyncio
    async def test_wires_get_coordinate_fields(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        await tools["place_wires"](wires=[
            {"x1": 100, "y1": 200, "x2": 300, "y2": 200},
            {"x1": 300, "y1": 200, "x2": 300, "y2": 400},
            {"x1": 300, "y1": 400, "x2": 600, "y2": 400},
        ])
        assert sent.command == "generic.place_wires"
        ops = sent.params["wires"].split("~~")
        assert len(ops) == 3
        assert ops[0] == "x1=100;y1=200;x2=300;y2=200"


class TestPlaceSchComponentsFromLibrary:
    @pytest.mark.asyncio
    async def test_placements_skip_entries_missing_lib_ref(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        await tools["place_sch_components_from_library"](placements=[
            {"lib_reference": "Res1", "x": 1000, "y": 2000,
             "designator": "R1"},
            {"x": 0, "y": 0},  # no lib_reference → drop
            {"lib_reference": "Cap", "x": 1500, "y": 2000,
             "designator": "C1", "rotation": 90,
             "library_path": "C:\\Lib\\Cap.SchLib"},
        ])
        ops = sent.params["placements"].split("~~")
        assert len(ops) == 2
        assert "lib_reference=Res1" in ops[0]
        assert "rotation=90" in ops[1]
        assert "library_path=C:\\Lib\\Cap.SchLib" in ops[1]


class TestSchAttachSpicePrimitivesBulk:
    @pytest.mark.asyncio
    async def test_bulk_attach_skips_missing_fields(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.generic")
        from eda_agent.tools import generic as g
        tools = _capture(g, "register_generic_tools")
        await tools["sch_attach_spice_primitives"](attachments=[
            {"designator": "R1", "primitive": "R", "value": "10k"},
            {"designator": "C1", "primitive": "C"},
            {"designator": "", "primitive": "R"},  # no designator → drop
            {"designator": "D1"},                   # no primitive → drop
        ])
        assert sent.command == "generic.attach_spice_primitives"
        ops = sent.params["attachments"].split("~~")
        assert len(ops) == 2
        assert "designator=R1" in ops[0]
        assert "value=10k" in ops[0]
        # C1 has no value → omit the field entirely.
        assert "value=" not in ops[1]


class TestLibAddPins:
    @pytest.mark.asyncio
    async def test_bulk_pin_packing(self, monkeypatch):
        sent = _install_fake_bridge(monkeypatch, "eda_agent.tools.library")
        from eda_agent.tools import library as lib
        tools = _capture(lib, "register_library_tools")
        await tools["lib_add_pins"](pins=[
            {"designator": "1", "name": "OUT1", "x": 0, "y": 0,
             "rotation": 180, "electrical_type": "output"},
            {"designator": "2", "name": "IN1-", "x": 0, "y": 100,
             "rotation": 180, "electrical_type": "input"},
            {"designator": "3", "name": "IN1+", "x": 0, "y": 200,
             "rotation": 180, "electrical_type": "input"},
            {"designator": "4", "name": "GND",  "x": 0, "y": 300,
             "electrical_type": "power", "hidden": True},
        ])
        assert sent.command == "library.add_pins"
        ops = sent.params["pins"].split("~~")
        assert len(ops) == 4
        assert "designator=1;name=OUT1" in ops[0]
        assert "electrical_type=output" in ops[0]
        assert "hidden=true" in ops[3]
        # Default length/rotation propagate.
        assert "length=200" in ops[3]
        assert "rotation=0" in ops[3]
