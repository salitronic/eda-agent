# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The request checks of a rows write, made by rows.py before it sends.

rows.py is the only way into the rows commands (2026-11-11): the
checks that need nothing from Altium moved here from the Pascal writers,
where they cost milliseconds a row in the script interpreter. The cases
below are the Free Pascal checks those Pascal routines had, unchanged.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import rows as rows_tools
from eda_agent.tools import rows_rules as R

#: (check, arguments, whether a problem is expected), from the Pascal tests.
CASES = [
    ('_pad_row', ({'hole_type': 'round', 'hole_width': '5000'},), True),
    ('_pad_row', ({'hole_type': 'slot', 'hole_width': '5000'},), False),
    ('_pad_row', ({'shape': 'round', 'corner_radius_pct': '25'},), True),
    ('_pad_row', ({'shape': 'roundrect', 'corner_radius_pct': '25'},), False),
    ('_pad_row', ({'corner_radius_pct': '25'},), False),
    ('_pad_row', ({'stack_mode': 'simple', 'bottom_x_size': '5000'},), True),
    ('_pad_row', ({'stack_mode': 'top-middle-bottom', 'bottom_x_size': '5000'},), False),
    ('_pad_row', ({'mask_manual': 'false', 'mask_top': '500'},), True),
    ('_pad_row', ({'mask_manual': 'False', 'mask_bottom': '500'},), True),
    ('_pad_row', ({'paste_manual': 'false', 'paste': '-500'},), True),
    ('_pad_row', ({'mask_manual': 'true', 'mask_top': '500', 'paste': '-500'},), False),
    ('_pad_via_value', (True, 'hole', '0'), True),
    ('_pad_via_value', (False, 'hole', '0'), False),
    ('_pad_via_value', (False, 'hole', '-1'), True),
    ('_pad_via_value', (True, 'size', '0'), True),
    ('_pad_via_value', (False, 'y_size', '-1'), True),
    ('_pad_via_value', (True, 'size', '8000'), False),
    ('_pad_via_value', (True, 'stack_mode', 'full-stack'), True),
    ('_pad_via_value', (True, 'stack_mode', 'Top-Middle-Bottom'), False),
    ('_pad_via_value', (False, 'shape', 'roundrect'), False),
    ('_pad_via_value', (False, 'shape', 'roundrectangle'), True),
    ('_pad_via_value', (False, 'hole_type', 'slot'), False),
    ('_pad_via_value', (False, 'hole_type', 'oval'), True),
    ('_pad_via_value', (False, 'corner_radius_pct', '101'), True),
    ('_pad_via_value', (False, 'corner_radius_pct', '50'), False),
    ('_pad_via_value', (False, 'name', ' '), True),
    ('_pad_via_value', (True, 'mask_top', '-1000'), False),
    ('_via_row', ({'mask_manual': 'false', 'mask_top': '1000'},), True),
    ('_via_row', ({'mask_manual': 'False', 'mask_bottom': '1000'},), True),
    ('_via_row', ({'mask_manual': 'false', 'tenting_top': 'true'},), True),
    ('_via_row', ({'stack_mode': 'simple', 'size_mid': '5000'},), True),
    ('_via_row', ({'stack_mode': 'simple', 'size_bottom': '5000'},), True),
    ('_via_row', ({'hole': '5000', 'size': '4000'},), True),
    ('_via_row', ({'hole': '5000', 'size': '5000'},), True),
    ('_via_row', ({'hole': '3000', 'size': '6000'},), False),
    ('_via_row', ({'hole': '9000', 'size': '10000'},), False),
    ('_via_row', ({'mask_manual': 'true', 'mask_top': '1000', 'tenting_top': 'true'},), False),
    ('_via_row', ({'stack_mode': 'top-middle-bottom', 'size_mid': '5000'},), False),
    ('_track_value', ('width', '0'), True),
    ('_track_value', ('width', '1000'), False),
    ('_arc_value', ('radius', '-1'), True),
    ('_arc_value', ('cx', '-1'), False),
    ('_arc_value', ('width', '0'), True),
    ('_arc_value', ('width', '1200'), False),
    ('_arc_value', ('radius', '-1'), True),
    ('_arc_value', ('start_angle', '3600000'), False),
    ('_arc_value', ('start_angle', '0'), False),
    ('_arc_value', ('end_angle', '3600001'), True),
    ('_arc_value', ('end_angle', '-5'), True),
    ('_arc_value', ('x', '-5'), False),
    ('_text_value', ('height', '0'), True),
    ('_text_value', ('height', '12000'), False),
    ('_text_value', ('stroke_width', '0'), True),
    ('_text_value', ('rotation', '3600000'), True),
    ('_text_value', ('rotation', '2700000'), False),
    ('_text_value', ('text', '  '), True),
    ('_text_value', ('y', '-3'), False),
    ('_pin_value', ('orientation', '45'), True),
    ('_pin_value', ('orientation', '90.5'), True),
    ('_pin_value', ('orientation', '270'), False),
    ('_pin_value', ('orientation', '-90'), False),
    ('_pin_value', ('electrical', 'analog'), True),
    ('_pin_value', ('electrical', 'bidir'), False),
    ('_pin_value', ('color', '16777216'), True),
    ('_pin_value', ('color', '16777215'), False),
    ('_pin_value', ('name_color', '-1'), True),
    ('_pin_value', ('name_font_id', '0'), True),
    ('_pin_value', ('name_font_id', '3'), False),
    ('_pin_value', ('designator_font_mode', '1.5'), True),
    ('_pin_value', ('designator', ' '), True),
    ('_pin_value', ('name', ''), False),
    ('_pin_value', ('functions', ''), True),
    ('_pin_value', ('functions', ' / '), True),
    ('_pin_value', ('functions', 'GPIO1/RX1'), False),
    ('_pin_value', ('length', '-1'), True),
    ('_pin_value', ('length', '0'), False),
]


@pytest.mark.parametrize("fn,args,bad", CASES)
def test_the_checks_the_pascal_writers_made(fn, args, bad):
    assert bool(getattr(R, fn)(*args)) is bad


def test_a_whole_number_type_is_checked_before_any_rule():
    # the Pascal check refused '1.5' for a font mode by its integer test
    assert R._type_problem("i", "designator_font_mode", "1.5")
    assert R._type_problem("l", "x", "007") and R._type_problem("l", "x", "+5")
    assert R._type_problem("l", "x", "99999999999")
    assert not R._type_problem("l", "x", "-2147483647")
    assert R._type_problem("b", "plated", "maybe") and not R._type_problem("b", "plated", "TRUE")


@pytest.mark.parametrize("text,ok", [
    ("0", True), ("-5", True), ("2147483647", True), ("-2147483647", True),
    ("2147483648", False), ("-2147483648", False), ("007", False), ("+5", False),
    ("-0", False), (" 5", False), ("5 ", False), ("1.5", False), ("", False),
    ("5e3", False), ("$1F", False), ("0x10", False), ("1_000", False), ("\uff15", False)])
def test_canonical_integers_as_the_bridge_requires(text, ok):
    assert R.canonical_int(text) is ok


TRACK = {"doc": "pcblib", "kind": "track", "container_guard": "footprint", "fields": [
    {"name": "id", "type": "s", "writable": False},
    {"name": "footprint", "type": "s", "writable": False},
    {"name": "layer", "type": "s", "writable": True},
    {"name": "width", "type": "l", "writable": True},
    {"name": "keepout", "type": "b", "writable": False}]}


@pytest.mark.parametrize("rows,code", [
    ([{"id": "1", "?footprint": "A", "width": "1000"}], ""),
    ([{"id": "1", "?FOOTPRINT": "A", "Width": "1000"}], ""),        # names ignore case
    ([], "MISSING_PARAMS"),
    ([{"?footprint": "A", "width": "1000"}], "BAD_ROWS"),             # no id
    ([{"id": "1", "?footprint": "A", "width": "1000"},
      {"id": "1", "?footprint": "A", "width": "2000"}], "BAD_ROWS"),  # id twice
    ([{"id": "1", "?footprint": "A", "colour": "1"}], "BAD_FIELD"),
    ([{"id": "1", "?footprint": "A", "?colour": "1", "width": "1"}], "BAD_FIELD"),
    ([{"id": "1", "?footprint": "A", "footprint": "B"}], "READ_ONLY_FIELD"),
    ([{"id": "1", "?footprint": "A", "width": "wide"}], "BAD_VALUE"),
    ([{"id": "1", "?footprint": "A", "?keepout": "perhaps", "width": "1"}], "BAD_VALUE"),
    ([{"id": "1", "?footprint": "A", "?width": "1000"}], "BAD_ROWS"),  # writes nothing
    ([{"id": "1", "width": "1000"}], "BAD_ROWS"),                     # no container guard
    ([{"id": "1", "?footprint": " ", "width": "1000"}], "BAD_ROWS"),
    ([{"id": "1", "?footprint": "A", "width": "0"}], "BAD_VALUE"),    # the kind's own rule
])
def test_a_write_is_checked_whole_before_it_is_sent(rows, code):
    got, message = R.check_rows("pcblib", "track", rows, TRACK)
    assert got == code, message
    if code and code != "MISSING_PARAMS":
        assert message.endswith("nothing was changed")


VIA = {"doc": "pcblib", "kind": "via", "container_guard": "footprint", "fields": [
    {"name": "id", "type": "s"}, {"name": "footprint", "type": "s"},
    {"name": "hole", "type": "l", "writable": True},
    {"name": "size", "type": "l", "writable": True}]}


def test_a_row_rule_refuses_the_write():
    rows = [{"id": "1", "?footprint": "A", "hole": "5000", "size": "4000"}]
    code, message = R.check_rows("pcblib", "via", rows, VIA)
    assert code == "BAD_VALUE" and "annular ring" in message
    rows[0]["size"] = "6000"
    assert R.check_rows("pcblib", "via", rows, VIA) == ("", "")


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, *a, **k):
        if command == "rows.list_kinds":
            return {"success": True, "data": {"kinds": [TRACK]}}
        self.sent.append((command, params))
        return {"success": True}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_a_refused_write_never_reaches_the_bridge(monkeypatch):
    mcp, bridge = _Mcp(), _Bridge()
    monkeypatch.setattr(rows_tools, "get_bridge", lambda: bridge)
    rows_tools._KINDS.clear()
    rows_tools.register_rows_tools(mcp)
    out = asyncio.run(mcp.tools["rows_write"](doc="pcblib", kind="track", rows=[
        {"id": "7", "?footprint": "X", "width": 0}]))
    assert out["success"] is False and out["error"].startswith("BAD_VALUE")
    out = asyncio.run(mcp.tools["rows_write"](doc="pcblib", kind="nope", rows=[
        {"id": "7", "?footprint": "X", "width": 0.2}]))
    assert out["success"] is False and "BAD_KIND" in out["error"]
    assert bridge.sent == []
    asyncio.run(mcp.tools["rows_write"](doc="pcblib", kind="track", rows=[
        {"id": "7", "?footprint": "X", "width": 0.2}]))
    assert bridge.sent and bridge.sent[0][0] == "rows.write_rows"
