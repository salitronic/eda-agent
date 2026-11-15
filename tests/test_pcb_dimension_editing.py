# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_get_dimensions / pcb_dimension_bindings / pcb_create_dimension /
pcb_edit_dimension: dimensions that measure, made and changed by script.

The existing placers write X1Location / Size / TextX on a new dimension and
add no references, so what they draw measures nothing. A dimension's ends
are its references, and DelphiScript's reference record has no point field:
an empty reference lands at the board origin and travels with the dimension
when its X / Y are written. These handlers place each point by moving the
dimension by -P, adding the reference and moving back by +P, measured from
the X / Y read just before (an angular recomputes its vertex on every add;
setting X / Y back to the old value put its points as much as 17 mil off).
Measured on AD 25.8.1 for linear, angular, leader, datum, baseline, center
and original dimensions, each then edited by hand, saved and reopened.

A script cannot bind a reference to a board object (Primitive written back
reads back until the next rebuild, and is gone after it and after a save),
so references made here are free and do not follow a pad that moves; a
bound one is refused unless the caller asks to unbind it.
"""
from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

from eda_agent.layout import dimensions
from eda_agent.tools import pcb

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _pcb():
    return load(_ALTIUM / "PCB.pas", minimum=20)


# --- the Pascal --------------------------------------------------------------

def test_references_count_is_never_written():
    # Writing References_Count raised an access violation in Altium's
    # script engine on AD 25: references are only added and deleted.
    for name, body in _pcb().items():
        assert not re.search(r"References_Count\s*:=", body, re.I), name


def test_text_locations_are_asked_of_a_baseline_only():
    # On any other kind "Undeclared identifier: TextLocationsCount" stops
    # the script.
    funcs = _pcb()
    body = funcs["DimSetTextLocations"]
    first_use = body.find("Dim.TextLocations")
    assert re.search(r"If\s+Dim\.DimensionKind\s*<>\s*6\s+Then\s+Exit", body[:first_use], re.I)
    json = funcs["DimJson"]
    loop = json.lower().find("textlocationscount")
    assert re.search(r"If\s+K\s*=\s*6\s+Then", json[:loop], re.I)


def test_a_point_is_placed_by_moving_from_the_fresh_x_and_y():
    funcs = _pcb()
    add = funcs["DimAddRefAt"]
    assert add.index("DimMoveBy(Dim, -PX, -PY)") < add.index("References_Add") \
        < add.index("DimMoveBy(Dim, PX, PY)")
    move = funcs["DimMoveBy"]
    # relative to where the dimension is now, never to a value kept from before
    assert "CoordToMilsF(Dim.X) + DX" in move and "CoordToMilsF(Dim.Y) + DY" in move


def test_a_bound_dimension_is_refused_unless_unbinding_is_asked():
    body = _pcb()["PCB_EditDimension"]
    assert re.search(r"Geometry\s+And\s+\(Bound\s*>\s*0\)\s+And\s+KeepBindings", body)
    assert "'BOUND|'" in body
    assert re.search(r"\(Bound\s*>\s*0\)\s+And\s+Not\s+KeepBindings\s+Then\s+Begin\s+DimUnbindAll", body)
    assert "'UNBIND_FAILED|'" in body


def test_an_arc_bound_kind_is_never_unbound_or_created():
    funcs = _pcb()
    edit = funcs["PCB_EditDimension"]
    assert re.search(r"\(K = 3\) Or \(K = 9\) Or \(K = 10\)", edit)
    assert "'BOUND_TO_ARC|" in edit
    create = funcs["PCB_CreateDimension"]
    assert re.search(r"\(K = 3\) Or \(K = 9\) Or \(K = 10\)", create)
    assert "'KIND_NOT_SUPPORTED'" in create


def test_a_reference_is_bound_only_by_an_anchor_above_zero():
    # A free reference reads anchor -1 once rebuilt and 0 when just added;
    # its Primitive is not Nil then, and touching it stops the script.
    bound = _pcb()["DimRefBound"]
    assert "Result := R.Anchor > 0;" in bound
    assert "Primitive" not in bound
    for name in ("DimBoundCount", "DimUnbindAll", "DimJson"):
        assert "DimRefBound(Dim, I)" in _pcb()[name]
        assert "Anchor <> -1" not in _pcb()[name]


def test_a_create_or_edit_is_one_board_edit():
    create = _pcb()["PCB_CreateDimension"]
    assert create.index("PcbBeginEdit(0);") < create.index("DimApplyGeometry(Dim, Params);") \
        < create.index("PcbEndEdit(0);")
    edit = _pcb()["PCB_EditDimension"]
    assert edit.index("Board.BoardIterator_Destroy(Iter);") < edit.index("PcbBeginEdit(0);") \
        < edit.index("DimApplyGeometry(Dim, Params);") < edit.index("PcbEndEdit(0);")


def test_a_stale_index_is_refused():
    body = _pcb()["PCB_EditDimension"]
    assert re.search(r"Abs\(CoordToMilsF\(Dim\.X\) - GX\) > 0\.01", body)
    assert "'NOT_THIS_DIMENSION|" in body


def test_the_new_dimension_is_found_by_its_address_not_its_place():
    # Altium lists a new dimension first, not last.
    body = _pcb()["PCB_CreateDimension"]
    assert "Obj.I_ObjectAddress = Dim.I_ObjectAddress" in body


def test_kind_values_are_written_only_on_kinds_that_have_them():
    body = _pcb()["DimApplyValues"]
    assert re.search(r"'angle'\);\s+If \(V <> ''\) And \(\(K = 1\) Or \(K = 5\) Or \(K = 6\) Or \(K = 7\) Or \(K = 9\)\)", body)
    assert re.search(r"'radius'\);\s+If \(V <> ''\) And \(K = 2\)", body)
    assert re.search(r"If K = 4 Then\s+Begin\s+If Pos\('\"label\"'", body)


# --- the geometry ------------------------------------------------------------

def test_an_angular_gets_altium_s_reference_order():
    p = dimensions.params_for("angular", [[0, 0], [100, 200], [-100, 200]])
    assert p["points"] == "50.0000,100.0000/100.0000,200.0000/-50.0000,100.0000/-100.0000,200.0000"
    assert float(p["radius"]) == pytest.approx(0.6 * math.hypot(100, 200))


def test_a_linear_line_sits_off_the_points_by_the_offset():
    p = dimensions.params_for("linear", [[100, 50], [700, 50]], offset=300)
    assert (p["line_x"], p["line_y"]) == ("100.0000", "350.0000")
    turned = dimensions.params_for("linear", [[0, 0], [0, 500]], angle=90, offset=200)
    assert float(turned["line_x"]) == pytest.approx(-200)
    assert float(turned["line_y"]) == pytest.approx(0, abs=1e-9)
    assert "line_x" not in dimensions.params_for("linear", [[0, 0], [9, 0]], offset=None)


def test_a_baseline_gets_one_stacked_line_per_point():
    p = dimensions.params_for("baseline", [[0, 0], [400, 0], [700, 0]], offset=300, spacing=250)
    assert p["text_locations"] == "500.0000,300.0000/800.0000,550.0000"


def test_a_datum_callout_row_and_the_points_kept():
    p = dimensions.params_for("datum", [[10, 20], [400, 20]], offset=-300)
    assert p["points"] == "10.0000,20.0000/400.0000,20.0000"
    assert (p["text_x"], p["text_y"]) == ("10.0000", "-280.0000")


@pytest.mark.parametrize("kind", ["linear_diameter", "radial_diameter"])
def test_arc_bound_kinds_are_refused(kind):
    with pytest.raises(ValueError, match="bound"):
        dimensions.params_for(kind, [[0, 0], [1, 1]])


def test_a_radius_is_drawn_as_a_leader_from_the_arc_labelled_with_it():
    # Tip on the arc, a bend out along the radius, the label end beside it.
    p = dimensions.params_for("radial", [[1000, 1000], [1250, 1000]])
    assert p["kind"] == "leader" and p["label"] == "R 250.00"
    assert p["points"] == "1250.0000,1000.0000/1475.0000,1000.0000/1675.0000,1000.0000"
    # From a centre and radius alone the leader leaves at 45 degrees.
    p = dimensions.params_for("radial", [[0, 0]], radius=100)
    tip = p["points"].split("/")[0].split(",")
    assert tip == ["70.7107", "70.7107"]
    # The label follows the unit and precision asked for.
    p = dimensions.params_for("radial", [[0, 0]], radius=100, label_unit="mm",
                              label_precision=3)
    assert p["label"] == "R 2.540"
    with pytest.raises(ValueError, match="radius"):
        dimensions.params_for("radial", [[0, 0]])


def test_too_few_points_are_refused():
    with pytest.raises(ValueError, match="angular needs at least 3"):
        dimensions.params_for("angular", [[0, 0], [1, 1]])


# --- the tools ---------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def reg(fn):
            self.tools[fn.__name__] = fn
            return fn
        return reg


class _Bridge:
    def __init__(self, dims):
        self.sent = []
        self.dims = dims

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, dict(params)))
        if command == "pcb.get_dimensions":
            return {"count": len(self.dims), "dimensions": list(self.dims)}
        return {"ok": True}


def _tool(monkeypatch, name, dims=(), **kwargs):
    bridge = _Bridge(dims)
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools[name](**kwargs)), bridge.sent


def test_bindings_count_the_bound_ends(monkeypatch):
    dims = [{"index": 0, "kind": "linear", "x": 1.0, "y": 2.0,
             "references": [{"anchor": 1, "bound": True}, {"anchor": -1, "bound": False}]}]
    reply, sent = _tool(monkeypatch, "pcb_dimension_bindings", dims=dims)
    assert sent == [("pcb.get_dimensions", {"index": "-1", "bound_only": "true"})]
    assert reply["bound"] == [{"index": 0, "kind": "linear", "x": 1.0, "y": 2.0,
                               "bound_references": 1}]


def test_edit_keeps_bindings_unless_told(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_edit_dimension", index=2, x=10, y=20, dx=5)
    assert "keep_bindings" not in sent[0][1]
    _, sent = _tool(monkeypatch, "pcb_edit_dimension", index=2, x=10, y=20,
                    keep_bindings=False)
    assert sent == [("pcb.edit_dimension", {"index": "2", "x": "10", "y": "20",
                                            "keep_bindings": "false"})]


def test_edit_points_take_the_kind_from_the_board(monkeypatch):
    dims = [{"index": 0, "kind": "baseline", "x": 0.0, "y": 0.0, "angle": 0}]
    _, sent = _tool(monkeypatch, "pcb_edit_dimension", dims=dims, index=0, x=0, y=0,
                    points=[[0, 0], [400, 0]])
    command, params = sent[-1]
    assert command == "pcb.edit_dimension"
    assert params["points"] == "0.0000,0.0000/400.0000,0.0000"
    assert params["text_locations"] == "500.0000,300.0000"


def test_create_sends_a_radial_as_a_leader_and_refuses_a_diameter(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_create_dimension", kind="radial",
                    points=[[0, 0], [10, 0]])
    assert sent[0][0] == "pcb.create_dimension"
    assert sent[0][1]["kind"] == "leader" and sent[0][1]["label"] == "R 10.00"
    reply, sent = _tool(monkeypatch, "pcb_create_dimension", kind="radial_diameter",
                        points=[[0, 0], [10, 0]])
    assert reply["ok"] is False and sent == []


def test_create_sends_the_leader_label_and_frame(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_create_dimension", kind="leader",
                    points=[[0, 0], [200, 200], [500, 200]], label="PAD 20", shape="round")
    params = sent[0][1]
    assert sent[0][0] == "pcb.create_dimension"
    assert (params["kind"], params["label"], params["shape"]) == ("leader", "PAD 20", "1")
    assert "sector" not in params and "size" not in params


def test_a_datum_baseline_leader_or_center_keeps_its_first_reference():
    # Rebuilt from no reference at all, a baseline kept one line position too
    # few (measured on AD 25.8.1); the first reference sits on X / Y on these
    # kinds, so it is kept and moved with them.
    funcs = _pcb()
    assert re.search(r"DimSetPoints\(Dim, Pts, DimAnchorFor\(K, Pts, Params, AX, AY\), AX, AY,\s+"
                     r"\(K = 4\) Or \(K = 5\) Or \(K = 6\) Or \(K = 7\)\)", funcs["DimApplyGeometry"])
    body = funcs["DimSetPoints"]
    assert re.search(r"If KeepFirst And \(Dim\.References_Count > 0\) Then\s+Begin\s+"
                     r"While Dim\.References_Count > 1 Do DimDropLastRef", body)
