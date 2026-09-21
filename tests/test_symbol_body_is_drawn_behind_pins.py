# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A solid symbol body must be added before the pins it sits behind.

Altium exposes no z-order on schematic primitives.  Drawing order is
insertion order, and there is no send-to-back to correct it, so the only
control is the sequence of calls.

``lib_create_ic_symbol`` passes a ``fill_color``, which sets ``IsSolid``,
and used to add that rectangle AFTER the pins.  The body was therefore
painted over the pin names on every IC symbol this server generated, and
the only remedy is rebuilding the symbol from scratch.  Reported from the
field 2026-09-21 after exactly that rebuild.
"""

from __future__ import annotations

import inspect
import re

from eda_agent.tools import library as library_tools


def _ic_symbol_source() -> str:
    src = inspect.getsource(library_tools)
    m = re.search(r"async def lib_create_ic_symbol\(.*?(?=\n    @mcp\.tool|\n    async def )",
                  src, re.DOTALL)
    assert m, "lib_create_ic_symbol is gone; this guard must follow it"
    return m.group(0)


def test_the_body_is_added_before_the_pins():
    body = _ic_symbol_source()
    rect = body.find("library.add_symbol_rectangle")
    pins = body.find("library.add_pins")
    assert rect != -1, "no rectangle is added; the symbol has no body"
    assert pins != -1, "no pins are added"
    assert rect < pins, (
        "lib_create_ic_symbol adds the solid body AFTER the pins, so it "
        "paints over the pin names. Altium has no z-order on schematic "
        "primitives, so insertion order is the only control and there is "
        "no way to fix the symbol afterwards except rebuilding it.")


def test_the_body_is_actually_solid():
    """The ordering only matters because the fill makes it opaque.

    If the fill were ever dropped, this guard would still pass while
    meaning nothing, so pin the premise too.
    """
    assert "fill_color" in _ic_symbol_source(), (
        "the body no longer sets a fill; if that is deliberate the "
        "ordering guard above is moot and should be revisited")


def test_the_ordering_rule_is_documented_where_callers_meet_it():
    """A caller building a symbol by hand hits the same trap."""
    doc = library_tools.__dict__.get("__doc__") or ""
    src = inspect.getsource(library_tools)
    m = re.search(r"async def lib_add_symbol_rectangle\(.*?\"\"\"(.*?)\"\"\"",
                  src, re.DOTALL)
    assert m, "lib_add_symbol_rectangle docstring is gone"
    text = m.group(1).lower()
    assert "before the pins" in text, (
        "lib_add_symbol_rectangle must tell callers to add the body "
        "before the pins; without it they build the same broken symbol "
        "and cannot fix it except by rebuilding")
