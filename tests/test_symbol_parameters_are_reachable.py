# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A symbol's parameters can be read, moved and shown or hidden.

Parameters belong to the symbol, not to the library document, so the
library's own iterator never reaches them. MEASURED: obj_query for
eParameter in a lib_component scope matched 0 while the same scope
found the symbol's pins, and lib_batch_set_params created every new
parameter visible on the symbol origin with no way to move it.
"""

from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
GENERIC = load(SCRIPTS / "Generic.pas")
LIBRARY = load(SCRIPTS / "Library.pas")


def test_parameter_query_walks_the_scoped_symbol() -> None:
    body = GENERIC["ProcessSchDocObjects"]
    assert "LibComp.SchIterator_Create" in body, (
        "parameter queries in a library iterate the document, which "
        "holds none of the symbol's parameters")
    assert "LibComp.SchIterator_Destroy" in body


def test_label_ops_carry_position_and_text() -> None:
    body = LIBRARY["Lib_SetLabelFormats"]
    for key in ("'x'", "'y'", "'text'"):
        assert f"GetBatchField(OpStr, {key})" in body, key
    assert "ApplyLabelPlacement(" in LIBRARY["ApplyOpToComponent"]


def test_a_created_parameter_starts_hidden() -> None:
    body = LIBRARY["Lib_BatchSetParams"]
    assert "NewParam.IsHidden := True" in body


def test_a_moved_label_leaves_auto_position() -> None:
    """MEASURED: a designator moved without this read back at the new
    place and was saved at the old one, because Altium re-positions an
    auto-positioned designator."""
    assert "Autoposition := False" in LIBRARY["ApplyLabelPlacement"]


def test_a_single_keyed_batch_op_uses_the_keyed_framing() -> None:
    """A one-op batch has no '~~'. Choosing the framing by '~~' alone sent
    it down the legacy '|' split, which tore 'set=A|B' apart."""
    body = GENERIC["Gen_BatchModify"]
    assert "Copy(Operations, 1, 6) = 'scope='" in body


def test_label_ops_refuse_a_key_they_would_drop() -> None:
    """MEASURED: a server started before x/y existed encoded is_hidden,
    dropped x and y, and the reply still said modified:1, so designator
    moves reported success five times and moved nothing."""
    import asyncio

    from eda_agent.tools.library import register_library_tools

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    register_library_tools(_Mcp())
    out = asyncio.run(tools["lib_set_label_formats"](
        ops=[{"target": "designator", "dx": 5, "is_hidden": False}]))
    assert out.get("ok") is False and "dx" in out["reason"]


def test_every_batch_handler_selects_each_ops_symbol() -> None:
    """MEASURED: one obj_batch_delete naming two symbols deleted the first
    symbol's 17 targets from the CURRENT symbol, and the second symbol's
    ops matched nothing, because the per-op lib_component scope was never
    applied."""
    for handler in ("Gen_BatchModify", "Gen_BatchDelete"):
        assert "ApplyLibComponentScope(" in GENERIC[handler], handler


def test_a_pin_designator_write_reaches_the_pin() -> None:
    """MEASURED: obj_modify ePin set Designator=SH returned matched:1 and
    the pin still read MH1. The read side had a pin branch and the write
    side sent it to the component-text helper, a no-op for a pin."""
    body = next(b for n, b in GENERIC.items()
                if "SetSchComponentSubText(Obj, 'Designator'" in b)
    assert "Obj.Designator := Value" in body


def test_a_copy_is_verified_by_its_pins_not_its_name() -> None:
    """MEASURED: the first copy after a library opened arrived with no pins
    and reported verified:true, because only the name was checked."""
    body = LIBRARY["Lib_CopyComponent"]
    assert body.count("CountComponentPins(") >= 3
    assert "copy_pins" in body and "SelectLibComponentPart(SourceName" in body


def test_placed_component_labels_can_be_shown_and_moved() -> None:
    """MEASURED: obj_modify eSchComponent set Designator.IsHidden=false
    reported the name as unknown, and a library template that hid the
    designator left placed parts without one."""
    set_body = GENERIC["SetSchProperty"]
    get_body = GENERIC["GetSchProperty"]
    # One parser answers every label name, sheet symbols' too; it is run by
    # fpc on these names in test_labels_are_autopositioned.
    assert "SchLabelPropWhich(PropName) <> ''" in set_body
    assert "SchLabelPropWhich(PropName) <> ''" in get_body
    parser = GENERIC["SchLabelPropWhich"]
    for field in ("IsHidden", "Location.X", "Location.Y", "Autoposition"):
        assert f"'{field}'" in parser, field
    assert "Autoposition := False" in GENERIC["SetSchComponentLabelProp"]
