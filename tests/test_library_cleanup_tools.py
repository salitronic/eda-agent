# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_move_bodies_to_layer and lib_redraw_assembly.

Measured on AD 26.10 on a scratch library: bodies moved on the footprint
on screen and on one off screen both kept their layer through save and a
restart of Altium; three redraws in a row left one outline and one
.Designator per footprint, with the 3D body on the same layer untouched.
The first redraw drew its outline a whole footprint offset away, because
an off-screen footprint's primitives read relative to its origin; the
outline is now measured again once the footprint is on screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.tools import library

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body(name: str) -> str:
    text = LIBRARY.read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(") if f"Function {name}(" in text \
        else text.index(f"Procedure {name}(")
    return text[start:text.index("\nEnd;\n", start)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    library.register_library_tools(DummyMcp())
    return captured, bridge


@pytest.mark.asyncio
async def test_bodies_go_to_the_named_layer_for_the_named_footprints(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    await tools["lib_move_bodies_to_layer"](
        "Mechanical13", footprint_names=["SOIC-8", " ", "R0603"])
    assert bridge.sent == [("library.move_bodies_to_layer", {
        "layer": "Mechanical13", "footprint_names": "SOIC-8\tR0603"})]


@pytest.mark.asyncio
async def test_no_names_means_every_footprint(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    await tools["lib_move_bodies_to_layer"]("Mechanical13")
    assert bridge.sent == [("library.move_bodies_to_layer", {"layer": "Mechanical13"})]


@pytest.mark.asyncio
async def test_redraw_sends_its_choices(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    await tools["lib_redraw_assembly"](
        "Mechanical15", footprint_names=["R0603"], outline_from="Courtyard",
        courtyard_layer="Mechanical13", width=0.1, text_height=0.5,
        confirm=True, units="mm")
    assert bridge.sent == [("library.redraw_assembly", {
        "layer": "Mechanical15", "outline_from": "courtyard", "width": "0.1",
        "designator": "true", "replace": "true", "footprint_names": "R0603",
        "courtyard_layer": "Mechanical13", "text_height": "0.5",
        "confirm": "true", "units": "mm"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"layer": ""},
    {"layer": "Mechanical15", "outline_from": "silk"},
    {"layer": "Mechanical15", "outline_from": "courtyard"},
    {"layer": "Mechanical15", "width": 0},
    {"layer": "Mechanical15", "units": "inch"},
])
async def test_redraw_sends_nothing_it_cannot_do(monkeypatch, kwargs):
    tools, bridge = _tools(monkeypatch)
    result = await tools["lib_redraw_assembly"](**kwargs)
    assert bridge.sent == []
    assert result.get("ok") is False or "error" in str(result).lower()


def test_the_outline_is_measured_again_once_the_footprint_is_on_screen():
    body = _body("Lib_RedrawAssembly")
    shown = body.index("PcbLib.SetState_CurrentComponent(Footprint);")
    measured = body.index("FootprintExtent(Footprint, Used, CourtLayer", shown)
    assert shown < measured < body.index("AddOutlineTrack(", shown)


def test_old_drawing_is_cleared_with_another_footprint_on_screen():
    body = _body("Lib_RedrawAssembly")
    clear = body.index("ClearDrawingOnLayer(Footprint, Layer)")
    away = body.rindex("PcbLib.SetState_CurrentComponent(Other)", 0, clear)
    assert body.rindex("IsCurrentFootprint(PcbLib, Footprint)", 0, away) < away


def test_clearing_never_takes_bodies_or_pads():
    body = _body("ClearDrawingOnLayer")
    kinds = body[body.index("AddFilter_ObjectSet("):body.index(");", body.index("AddFilter_ObjectSet("))]
    assert "eComponentBodyObject" not in kinds and "ePadObject" not in kinds
    assert "Footprint.RemovePCBObject(Prim)" in body
    assert body.index("PCBServer.DestroyPCBObject(Prim)") < body.index(
        "Footprint.GroupIterator_Destroy(GrpIter)")


def test_a_courtyard_on_the_assembly_layer_is_not_cleared():
    body = _body("Lib_RedrawAssembly")
    assert "If Replace And (CourtLayer = Layer) Then" in body


def test_both_warn_about_a_layer_the_library_has_not_enabled():
    for name in ("Lib_MoveBodiesToLayer", "Lib_RedrawAssembly"):
        assert "MechLayerEnabledJson(PcbLib.Board" in _body(name), name
    assert "MechanicalLayerEnabled" in _body("LibMechLayerEnabled")


def test_a_body_listed_twice_is_moved_once():
    body = _body("Lib_MoveBodiesToLayer")
    assert "If Pos(#9 + Addr + #9, Seen) = 0 Then" in body


def _saved(monkeypatch, bodies):
    monkeypatch.setattr(library.pcblib_models, "saved_body_models",
                        lambda path, name: bodies)


@pytest.mark.asyncio
async def test_set_3d_body_sends_heights_in_mils(monkeypatch, tmp_path):
    lib = tmp_path / "Caps.PcbLib"
    lib.write_bytes(b"")
    _saved(monkeypatch, None)
    tools, bridge = _tools(monkeypatch)
    await tools["lib_set_3d_body"](
        "C1206", library_path=str(lib), standoff_height=0.65,
        opacity=0.5, layer="Mechanical5", body_index=1, units="mm")
    assert bridge.sent == [("library.set_3d_body", {
        "footprint_name": "C1206", "standoff_height": "25.5906",
        "opacity": "0.5", "body_index": "1", "layer": "Mechanical5",
        "library_path": str(lib)})]


@pytest.mark.asyncio
async def test_a_step_standoff_carries_the_saved_rotations(monkeypatch, tmp_path):
    # SetState writes the rotations with the Z offset, and no getter reads
    # them, so the saved file's must go along or the model is turned flat.
    lib = tmp_path / "Ics.PcbLib"
    lib.write_bytes(b"")
    _saved(monkeypatch, [
        {"model_file": "SOT23.step", "model_type": 1, "rotation_deg": [90.0, 0.0, 180.0],
         "model_z_offset_mils": 0.0},
        {"model_file": "", "model_type": 0, "rotation_deg": [0.0, 0.0, 0.0],
         "model_z_offset_mils": 0.0}])
    tools, bridge = _tools(monkeypatch)
    result = await tools["lib_set_3d_body"]("SOT23", library_path=str(lib),
                                            standoff_height=12)
    sent = bridge.sent[-1][1]
    assert sent["saved_count"] == "2"
    assert sent["saved_models"] == "SOT23.step|1|90.0|0.0|180.0||0|0.0|0.0|0.0"
    assert [b["index"] for b in result["saved_bodies"]] == [1, 2]


@pytest.mark.asyncio
async def test_a_library_path_that_is_not_a_file_is_refused(monkeypatch, tmp_path):
    # Measured on AD 26.11: Altium opens a new, empty library there.
    tools, bridge = _tools(monkeypatch)
    result = await tools["lib_set_3d_body"](
        "C1206", library_path=str(tmp_path / "Missing.PcbLib"), opacity=0.5)
    assert bridge.sent == []
    assert result["ok"] is False and "not a file" in result["reason"]


@pytest.mark.asyncio
async def test_set_3d_body_with_only_a_name_reads(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    await tools["lib_set_3d_body"]("C1206")
    assert bridge.sent == [("library.set_3d_body", {"footprint_name": "C1206"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"footprint_name": " "},
    {"footprint_name": "C1206", "opacity": 50},
    {"footprint_name": "C1206", "body_index": 0},
    {"footprint_name": "C1206", "units": "inch"},
    {"footprint_name": "C1206", "standoff_height": float("nan")},
])
async def test_set_3d_body_sends_nothing_it_cannot_do(monkeypatch, kwargs):
    tools, bridge = _tools(monkeypatch)
    result = await tools["lib_set_3d_body"](**kwargs)
    assert bridge.sent == []
    assert result.get("ok") is False


def test_set_3d_body_touches_only_proven_body_members():
    body = _body("Lib_Set3DBody")
    for member in ("Body.StandoffHeight :=", "Body.OverallHeight :=",
                   "Body.BodyOpacity3D :=", "Body.Layer :="):
        assert member in body, member
    # SetState(RotX, RotY, RotZ, Dz) is measured on AD 26.11, and only the
    # reassignment after it saves. No 3D rotation getter is known, and an
    # undeclared identifier takes the polling loop down, so none is read.
    assert body.count("Model.SetState(") == 1
    assert body.index("Model.SetState(") < body.index("Body.Model := Model;")
    assert "Model.Rotation" not in body and "RotationX" not in body
    # A STEP body is matched to its saved record before it is written.
    assert "If SavedCount <> Total Then SavedStr := '';" in body
    assert "UpperCase(SavedFile) <> UpperCase(ModelFile)" in body
    # A skipped body is not counted as changed (read changed 1 live before).
    assert "If Wrote Then Changed := Changed + 1;" in body
    assert "Changed := Changed + 1;" not in body.replace(
        "If Wrote Then Changed := Changed + 1;", "")
    assert "If Pos(#9 + Addr + #9, Seen) = 0 Then" in body
    # Refuses rather than editing whichever library happens to be in front.
    assert "WRONG_DOCUMENT_FOCUSED" in body
