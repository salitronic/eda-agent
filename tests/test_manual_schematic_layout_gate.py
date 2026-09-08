# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Drawing a schematic by hand through sch_place_components is refused.

An LLM asked to draw a schematic ignores the layout engine and places
every part itself, choosing each coordinate. The result is netlist
correct and reads like nothing a person would draw, and steering it with
docstrings is persuasion. This is the enforcing half.

The refusal is deliberately narrow: BULK placement onto an EMPTY sheet.
Adding parts to a sheet with work on it is ordinary editing, one or two
parts is never a layout, and an explicit ``allow_manual_layout`` still
gets through.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools.generic import (
    _MANUAL_LAYOUT_BULK,
    _MANUAL_LAYOUT_EMPTY,
    _refuse_manual_sheet_layout,
)


class _Bridge:
    """Counts what the gate asks for and answers with a fixed component count."""

    def __init__(self, count, raises=False):
        self._count = count
        self._raises = raises
        self.calls: list[tuple] = []

    async def send_command_async(self, command, params=None, **kw):
        self.calls.append((command, params))
        if self._raises:
            raise RuntimeError("bridge is down")
        return {"count": self._count}


def _run(bridge, n, allow=False, document_path=None):
    return asyncio.run(_refuse_manual_sheet_layout(
        bridge, n, document_path, allow))


def test_bulk_placement_onto_an_empty_sheet_is_refused():
    bridge = _Bridge(0)
    out = _run(bridge, _MANUAL_LAYOUT_BULK)
    assert out is not None and out["refused"] is True
    assert out["placed"] == 0
    assert out["use_instead"] == "design_execute_plan"
    # The refusal has to say what to do instead, or it is just a wall.
    assert "design_execute_plan" in out["error"]
    assert "allow_manual_layout" in out["error"]


def test_a_sheet_with_work_on_it_is_ordinary_editing():
    bridge = _Bridge(_MANUAL_LAYOUT_EMPTY + 1)
    assert _run(bridge, 50) is None


def test_a_couple_of_parts_is_never_a_layout():
    bridge = _Bridge(0)
    assert _run(bridge, _MANUAL_LAYOUT_BULK - 1) is None
    # And it must not have paid for the count round trip to find out.
    assert bridge.calls == []


def test_the_caller_can_say_it_means_it():
    bridge = _Bridge(0)
    assert _run(bridge, 99, allow=True) is None
    assert bridge.calls == []


def test_it_fails_open_when_the_count_cannot_be_read():
    # A gate that blocks real work because an unrelated bridge call failed
    # would be worse than the drawing it exists to prevent.
    assert _run(_Bridge(0, raises=True), 99) is None
    assert _run(_Bridge(None), 99) is None


def test_the_count_is_scoped_to_the_target_sheet():
    bridge = _Bridge(0)
    _run(bridge, 99, document_path="C:/x/Sheet.SchDoc")
    assert bridge.calls, "the gate must ask for a count"
    command, params = bridge.calls[0]
    assert command == "generic.get_object_count"
    assert params["object_type"] == "eSchComponent"
    assert "Sheet.SchDoc" in str(params["scope"]), (
        "counting the ACTIVE document would read the wrong sheet when the "
        "caller named one")


def test_the_gate_cannot_block_the_engine_it_points_at():
    """design_execute_plan must not route through the gated tool.

    The refusal names design_execute_plan, so if the emitter placed parts
    by calling this tool the gate would cut off the only path it offers.
    It does not: the emitter sends the bridge command directly. Asserted
    here because that is a property someone could quietly change.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "eda_agent"
    emitter = (src / "design" / "emitter.py").read_text(encoding="utf-8")
    assert "place_sch_components_from_library" in emitter, (
        "the emitter no longer sends the placement command directly; if it "
        "now goes through sch_place_components, the manual-layout gate will "
        "refuse the engine's own placements")
    assert "sch_place_components(" not in emitter
