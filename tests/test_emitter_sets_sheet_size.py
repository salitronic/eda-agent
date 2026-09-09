# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The plan's paper has to reach the document.

sheet_bounds spreads a layout across the sheet the plan declares, so a
plan asking for A3 is laid out to A3. The emitter created the document
and never resized it, so those parts were drawn past the edge of an A4
border. Nothing failed: every part is present, the netlist is right, and
the frame is simply the wrong size around them.
"""
from __future__ import annotations

from eda_agent.design.canvas import SchematicCanvas, Sheet, SymbolInstance
from eda_agent.design.emitter import EmitResult, _emit_sheet_size
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


class _Bridge:
    def __init__(self, fail_on: str = ""):
        self.calls: list[tuple] = []
        self.fail_on = fail_on

    def send_command(self, command, params=None, **kw):
        self.calls.append((command, params or {}))
        if self.fail_on and command == self.fail_on:
            raise RuntimeError("no schematic document is active")
        return {"success": True}

    def sizes(self):
        return [p.get("style") for c, p in self.calls
                if c == "generic.set_sheet_size"]


def _canvas(size: str, name: str = "main") -> SchematicCanvas:
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name=name, size=size))
    return canvas


def test_the_declared_paper_is_sent():
    bridge = _Bridge()
    _emit_sheet_size(_canvas("A3"), "main", bridge, EmitResult())
    assert bridge.sizes() == ["A3"]


def test_a4_costs_no_call():
    """It is what a new document already is."""
    bridge = _Bridge()
    _emit_sheet_size(_canvas("A4"), "main", bridge, EmitResult())
    assert bridge.sizes() == []


def test_the_size_is_set_before_anything_is_drawn():
    """It is a document property: changing it after placement moves the
    frame under parts already positioned against it."""
    import inspect

    from eda_agent.design import emitter

    body = inspect.getsource(emitter._emit_sheet)
    assert "_emit_sheet_size(" in body
    assert body.index("_emit_sheet_size(") < body.index("_emit_placements("), (
        "the paper is resized after the parts are placed")


def test_each_sheet_gets_its_own_paper():
    """A plan can mix sizes: a dense MCU sheet on A3, a power sheet on A4."""
    canvas = _canvas("A3", "mcu")
    canvas.add_sheet(Sheet(name="power", size="A2"))
    bridge = _Bridge()
    _emit_sheet_size(canvas, "power", bridge, EmitResult())
    assert bridge.sizes() == ["A2"]


def test_an_unknown_sheet_name_asks_for_nothing():
    bridge = _Bridge()
    _emit_sheet_size(_canvas("A3"), "nosuch", bridge, EmitResult())
    assert bridge.sizes() == []


def test_a_refused_resize_is_reported_and_does_not_abort():
    """The sheet is still worth drawing; only its border is wrong, and
    the note has to say so or the layout looks inexplicably oversized."""
    bridge = _Bridge(fail_on="generic.set_sheet_size")
    result = EmitResult()
    _emit_sheet_size(_canvas("A3"), "main", bridge, result)
    assert result.ok is True
    joined = " ".join(result.notes)
    assert "A3" in joined and "could not set size" in joined
