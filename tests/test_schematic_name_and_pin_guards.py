# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic property guards and pin settings (issues #74-#78, #87, #89).

Reading Name on a type that has none raises an undeclared-identifier modal
that Try/Except cannot contain and that stops the bridge, so Name is behind an
allowlist. The allowlist must not drop pins: renaming a pin is the documented
obj_modify example.
"""

from __future__ import annotations

import asyncio
import re

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src


def _gen(func: str) -> str:
    return _decommented(_body(_src("Generic.pas"), func))


def _allowlisted_ids(func: str) -> set[str]:
    return set(re.findall(r"Obj\.ObjectId = (e\w+)", _gen(func)))


def test_pins_keep_their_name():
    assert "ePin" in _allowlisted_ids("SchObjectHasName")


def test_free_text_and_net_labels_never_reach_name():
    ids = _allowlisted_ids("SchObjectHasName")
    assert not ids & {"eLabel", "eNetLabel", "eNote", "eTextFrame"}


def test_name_reads_and_writes_both_check_the_allowlist():
    get = _gen("GetSchProperty")
    branch = get[get.index("PropName = 'Name'"):][:300]
    assert "SchObjectHasName(Obj)" in branch
    text = _decommented(_src("Generic.pas"))
    writes = [m.start() for m in re.finditer(r"PropName = 'Name'\s+Then", text)]
    assert len(writes) >= 2
    for at in writes:
        assert "SchObjectHasName(Obj)" in text[at:at + 300]


def test_a_pin_description_is_the_pins_own_field():
    text = _decommented(_src("Generic.pas"))
    at = text.index("(PropName = 'ComponentDescription') Or (PropName = 'Description')")
    branch = text[at:at + 600]
    component = branch.index("Obj.ComponentDescription := Value")
    assert branch.index("Obj.ObjectId = eSchComponent") < component
    assert "WriteSchPinProp(Obj, 'Description', Value)" in branch
    assert "Obj.ObjectId = ePin" in branch
    assert "P.Description := Value" in _gen("WriteSchPinProp")
    assert "P.Description" in _gen("ReadSchPinProp")


def test_bare_and_altium_ellipse_names_both_resolve():
    body = _gen("ObjectTypeFromString")
    assert "Raw = 'ellipse'" in body and "Raw = 'ellipticalarc'" in body
    assert "N = 'ellipse'" in body and "N = 'ellipticalarc'" in body


def test_the_schlib_toggle_prefers_the_symbol_on_screen():
    body = _gen("Gen_TogglePinVisibility")
    lib = body[body.index("If IsLib Then"):]
    assert lib.index("CurrentSchComponent") < lib.index("RememberedLibComponent(SchDoc)")


def test_a_schlib_component_query_uses_the_library_iterator():
    body = _decommented(_src("Generic.pas"))
    assert "SchLibDoc.SchLibIterator_Create" in body


def test_set_grid_sends_the_switches_only_when_given(monkeypatch):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params, **kw):
            sent.append(dict(params))
            return {"ok": True}

    import eda_agent.tools.generic as generic
    monkeypatch.setattr(generic, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(Mcp())
    asyncio.run(tools["obj_set_grid"](snap_grid=50))
    assert sent[-1] == {"snap_grid": "50"}
    asyncio.run(tools["obj_set_grid"](visible_grid_on=False))
    assert sent[-1] == {"visible_grid_on": "false"}
