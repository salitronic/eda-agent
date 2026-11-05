# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The package footprint tools on all three backends, offline.

Altium is driven through a fake bridge that records every command and
answers the way the handlers do: it parses the batch payloads with the
Python mirror of GetBatchField that test_cross_validate holds to the
real Pascal, and reads the pads back from what was sent. EasyEDA is
driven through a fake ``_call``. KiCad writes a real file, which is
parsed back by two independent readers.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.design import _landpattern_cases as cases
from tests.test_cross_validate import get_batch_field, next_batch_op


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _ops(payload: str) -> list[str]:
    out, rest = [], payload
    while rest:
        op, rest = next_batch_op(rest)
        if op:
            out.append(op)
    return out


_SHAPE_READ = {"roundrect": "roundrectangle", "rectangular": "rectangular",
               "round": "round", "octagonal": "octagonal"}


class FakeAltium:
    """Records commands; answers like the Library.pas handlers."""

    def __init__(self, mech=None, existing=(), pad_reply=None, shift=0.0):
        self.calls: list[tuple[str, dict]] = []
        self.mech = mech if mech is not None else [
            {"layer": "Mechanical13", "number": 13, "name": "Top Assembly",
             "kind": "Assembly Top", "kind_id": 1},
            {"layer": "Mechanical15", "number": 15, "name": "Top Courtyard",
             "kind": "Courtyard Top", "kind_id": 11},
        ]
        self.existing = list(existing)
        self.pad_reply = pad_reply
        self.shift = shift
        self.pads: list[dict] = []

    def commands(self) -> list[str]:
        return [c for c, _ in self.calls]

    async def send_command_async(self, command, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((command, params))
        if command == "library.get_mech_layers":
            if self.mech == "unknown":
                from eda_agent.bridge.exceptions import AltiumCommandError
                raise AltiumCommandError("Unknown library action",
                                         code="UNKNOWN_ACTION")
            return {"library_path": "scratch.PcbLib", "count": len(self.mech),
                    "layers": self.mech}
        if command == "library.get_footprints":
            return {"library_path": params.get("library_path"),
                    "count": len(self.existing),
                    "footprints": [{"name": n, "description": ""}
                                   for n in self.existing]}
        if command == "library.create_footprint":
            return {"success": True, "name": params["name"]}
        if command == "library.add_footprint_pads":
            ops = _ops(params["pads"])
            for op in ops:
                self.pads.append({k: get_batch_field(op, k) for k in (
                    "designator", "x", "y", "x_size", "y_size", "hole_size",
                    "shape", "corner_radius", "rotation", "layer",
                    "hole_length", "plated", "paste_expansion",
                    "mask_expansion")})
            if self.pad_reply is not None:
                return self.pad_reply
            return {"added": len(ops), "failed": 0, "unknown_layers": "",
                    "slots": 0, "unplated": 0, "expansions": 0,
                    "property_failures": 0, "total": len(ops)}
        if command in ("library.add_footprint_tracks",
                       "library.add_footprint_regions"):
            key = "tracks" if command.endswith("tracks") else "regions"
            n = len(_ops(params[key]))
            return {"added": n, "failed": 0, "unknown_layers": "", "total": n}
        if command in ("library.add_footprint_arc",
                       "library.add_footprint_text"):
            return {"success": True}
        if command == "library.set_footprint_height":
            return {"name": params["footprint_name"],
                    "new_height_mm": float(params["height_mm"])}
        if command == "library.get_pad_geometry":
            return {"name": params["footprint_name"], "pads": [
                self._read(p, i) for i, p in enumerate(self.pads)]}
        if command == "library.link_3d_model":
            return {"success": True}
        if command == "library.add_footprint_bodies":
            n = len(_ops(params["bodies"]))
            return {"added": n, "failed": 0, "total": n, "bodies": []}
        raise AssertionError(f"unexpected command {command}")

    def _read(self, p: dict, i: int) -> dict:
        hole = float(p["hole_size"] or 0)
        slot = float(p["hole_length"] or 0)
        return {
            "name": p["designator"],
            "x_mm": float(p["x"]) + (self.shift if i == 0 else 0.0),
            "y_mm": float(p["y"]), "w_mm": float(p["x_size"]),
            "h_mm": float(p["y_size"]), "shape": _SHAPE_READ[p["shape"]],
            "corner_pct": int(p["corner_radius"]) if p["shape"] == "roundrect" else 0,
            "rotation": float(p["rotation"]), "hole_mm": hole,
            "hole_w_mm": slot, "hole_type": "slot" if slot > hole > 0 else "round",
            "plated": p["plated"] != "false",
            "layer": "multi" if hole > 0 else "top",
            "paste_expansion_mm": float(p["paste_expansion"] or 0),
            "paste_expansion_source": "manual" if p["paste_expansion"] else "rule",
            "mask_expansion_mm": float(p["mask_expansion"] or 0),
            "mask_expansion_source": "manual" if p["mask_expansion"] else "rule",
        }


@pytest.fixture
def altium(monkeypatch):
    from eda_agent.tools import library as lib

    box = {"bridge": FakeAltium()}
    monkeypatch.setattr(lib, "get_bridge", lambda: box["bridge"])
    mcp = _Mcp()
    lib.register_library_tools(mcp)
    tool = mcp.tools["lib_create_footprint_from_package"]

    def run(bridge=None, **kw):
        if bridge is not None:
            box["bridge"] = bridge
        return asyncio.run(tool(**kw))

    return run, box


#: The last call a footprint build makes, by the 3D body it got.
_BODY_COMMAND = {"step": "library.link_3d_model",
                 "extruded": "library.add_footprint_bodies"}


def test_soic8_is_created_written_in_mm_and_read_back(altium):
    run, box = altium
    out = run(package=cases.soic8().to_dict())
    bridge = box["bridge"]
    assert out["ok"], out.get("reason") or out["readback"]
    assert bridge.commands() == [
        "library.get_mech_layers", "library.get_footprints",
        "library.create_footprint", "library.add_footprint_pads",
        "library.add_footprint_tracks", "library.add_footprint_arc",
        "library.add_footprint_text", "library.set_footprint_height",
        "library.get_pad_geometry", _BODY_COMMAND[out["model_3d"]["kind"]]]
    name = out["footprint"]
    assert name == "SOIC-8 (D)"
    for command, params in bridge.calls[3:-2]:
        target = params.get("footprint_name") or params.get("component_name")
        assert target == name, f"{command} did not name the footprint"
        if command != "library.set_footprint_height":
            assert params.get("units") == "mm", command
    # The reads are aimed at the library the writes went to.
    assert bridge.calls[1][1]["library_path"] == "scratch.PcbLib"
    assert bridge.calls[-2][1]["library_path"] == "scratch.PcbLib"
    assert out["layers"]["assembly"]["layer"] == "Mechanical13"
    assert out["layers"]["courtyard"]["layer"] == "Mechanical15"
    text = bridge.calls[6][1]
    assert text["text"] == ".Designator" and text["layer"] == "Mechanical13"
    assert bridge.calls[7][1]["height_mm"] == "1.75"
    assert out["readback"]["pads_read"] == 8
    # Unrounded: the land is 1.95 mm, not 77 mil.
    assert {p["x_size"] for p in bridge.pads} == {"1.95"}


def test_qfn16_exposed_pad_gets_closed_paste_and_windows(altium):
    run, box = altium
    out = run(package=cases.qfn16().to_dict())
    assert out["ok"], out.get("reason") or out["readback"]
    bridge = box["bridge"]
    assert "library.add_footprint_regions" in bridge.commands()
    regions = next(p for c, p in bridge.calls
                   if c == "library.add_footprint_regions")
    ops = _ops(regions["regions"])
    assert len(ops) == 4
    assert {get_batch_field(op, "layer") for op in ops} == {"TopPaste"}
    ep = next(p for p in bridge.pads if p["paste_expansion"])
    assert float(ep["paste_expansion"]) == -max(float(ep["x_size"]),
                                               float(ep["y_size"]))
    assert out["created"]["regions"] == 4


def test_a_name_already_in_the_library_is_refused(altium):
    run, box = altium
    out = run(bridge=FakeAltium(existing=["SOIC-8 (D)"]),
              package=cases.soic8().to_dict())
    assert not out["ok"] and out["created"] is False
    assert "already in the library" in out["reason"]
    assert "library.create_footprint" not in box["bridge"].commands()


def test_a_name_taken_in_another_case_is_refused_too(altium):
    run, box = altium
    out = run(bridge=FakeAltium(existing=["soic-8 (d)"]),
              package=cases.soic8().to_dict())
    assert not out["ok"] and "already in the library" in out["reason"]
    assert "library.create_footprint" not in box["bridge"].commands()


def test_no_role_layers_refuses_before_writing(altium):
    run, box = altium
    out = run(bridge=FakeAltium(mech=[{"layer": "Mechanical1", "name":
                                       "Mechanical 1", "kind": "Not Set"}]),
              package=cases.soic8().to_dict())
    assert not out["ok"] and "assembly_layer" in out["reason"]
    assert box["bridge"].commands() == ["library.get_mech_layers"]


def test_named_layers_work_without_the_layer_read(altium):
    run, box = altium
    out = run(bridge=FakeAltium(mech="unknown"),
              package=cases.soic8().to_dict(),
              assembly_layer="Mechanical2", courtyard_layer="Mechanical3")
    assert out["ok"], out.get("reason")
    tracks = next(p for c, p in box["bridge"].calls
                  if c == "library.add_footprint_tracks")
    layers = {get_batch_field(op, "layer") for op in _ops(tracks["tracks"])}
    assert layers == {"TopOverlay", "Mechanical2", "Mechanical3"}


def test_a_bad_package_sends_nothing(altium):
    run, box = altium
    out = run(package={"family": "soic", "pins": 8})
    assert not out["ok"] and "needs" in out["reason"]
    assert box["bridge"].commands() == []


def test_a_readback_off_by_two_units_is_a_failure(altium):
    run, box = altium
    out = run(bridge=FakeAltium(shift=2 * 0.0254 / 10000),
              package=cases.soic8().to_dict())
    assert not out["ok"]
    assert out["readback"]["mismatches"][0]["check"] == "x"


def test_a_refused_pad_property_stops_the_run(altium):
    run, box = altium
    out = run(bridge=FakeAltium(pad_reply={
        "added": 17, "failed": 0, "unknown_layers": "", "slots": 0,
        "unplated": 0, "expansions": 0, "property_failures": 1,
        "total": 17}), package=cases.qfn16().to_dict())
    assert not out["ok"] and out["created"] is True
    assert "refused 1 pad property" in out["reason"]
    assert "incomplete" in out["note"]
    assert "library.add_footprint_tracks" not in box["bridge"].commands()


def test_slots_and_unplated_holes_reach_the_payload(altium):
    run, box = altium
    out = run(package={
        "family": "custom", "name": "SLOT_TEST", "body_x": 6, "body_y": 4,
        "custom_pads": [
            {"number": "1", "x": -1.5, "y": 0, "width": 2.4, "height": 1.4,
             "shape": "oval", "hole": 0.8, "hole_length": 1.6},
            {"number": "MH", "x": 1.5, "y": 0, "width": 1.0, "height": 1.0,
             "shape": "circle", "hole": 1.0, "plated": False}]})
    assert out["ok"], out.get("reason") or out["readback"]
    slot, mh = box["bridge"].pads
    assert slot["hole_length"] == "1.6" and slot["plated"] == ""
    assert mh["plated"] == "false" and mh["hole_length"] == ""


def test_the_region_tool_refuses_a_region_with_no_layer(monkeypatch):
    from eda_agent.tools import library as lib

    bridge = FakeAltium()
    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    lib.register_library_tools(mcp)
    tool = mcp.tools["lib_add_footprint_regions"]
    out = asyncio.run(tool(regions=[{"points": [[0, 0], [1, 0], [1, 1]]}]))
    assert out["added"] == 0 and "no layer" in out["refused"][0]
    assert bridge.calls == []
    out = asyncio.run(tool(regions=[
        {"layer": "TopPaste", "points": [[0, 0], [1.25, 0], [1.25, 1], [0, 0]]},
        {"layer": "TopPaste", "points": [[0, 0], [1, 1]]}], units="mm"))
    command, params = bridge.calls[-1]
    assert command == "library.add_footprint_regions" and params["units"] == "mm"
    (op,) = _ops(params["regions"])
    assert get_batch_field(op, "points") == "0,0/1.25,0/1.25,1"
    assert "at least three" in out["refused"][0]


def _pascal_routine(name: str) -> str:
    import re
    from pathlib import Path

    code = (Path(__file__).resolve().parents[1] / "scripts" / "altium"
            / "Library.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\(", code)
    assert start, name
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_every_optional_pad_field_python_sends_is_read_by_the_handler():
    """A key the payload carries and the handler never reads is a pad
    that silently lacks it: a slot drilled round, a thermal pad with full
    paste. Source guard, since the handler cannot run here."""
    from eda_agent.tools import library as lib

    body = _pascal_routine("Lib_AddFootprintPads")
    for key in (*lib._OPTIONAL_PAD_LENGTHS, "plated"):
        assert f"GetBatchField(Op, '{key}')" in body, key
    # Each optional write is guarded and counted, so a refusal reaches the
    # reply as property_failures rather than vanishing.
    assert body.count("Inc(PropFailed)") == 3
    assert "Pad.SetState_Cache := Cache;" in body
    assert '"property_failures":' in body
    regions = _pascal_routine("Lib_AddFootprintRegions")
    for key in ("layer", "points"):
        assert f"GetBatchField(Op, '{key}')" in regions, key
    assert "PcbLib.Board.AddPCBObject(Region);" in regions


# --- EasyEDA -------------------------------------------------------------------


@pytest.fixture
def easyeda(monkeypatch):
    import eda_agent.tools.easyeda as ez

    calls: list[tuple[str, dict]] = []
    sent_pads: list[dict] = []

    def fake_call(command, params=None, timeout=None):
        params = dict(params or {})
        calls.append((command, params))
        if command == "lib.create_footprint":
            return {"ok": True, "uuid": "fp-1"}
        if command == "pcb.add_pads":
            sent_pads.extend(params["pads"])
            return {"ok": True, "placed": len(params["pads"]),
                    "of": len(params["pads"]), "stopped": False}
        if command == "pcb.pads":
            # The measured shape: padNumber, a [shape, w, h(, r)] tuple,
            # a [type, d] hole tuple, rotation.
            names = {"RECTANGLE": "RECT", "ELLIPSE": "ELLIPSE",
                     "OBLONG": "OVAL"}
            out = []
            for p in sent_pads:
                shape = [names[p["shape"]], p["width"], p["height"]]
                if p["shape"] == "RECTANGLE":
                    shape.append(p["corner_radius"])
                out.append({"padNumber": p["pad_number"], "x": p["x"],
                            "y": p["y"], "rotation": p["rotation"],
                            "pad": shape,
                            "hole": (["ROUND", p["hole_diameter"]]
                                     if p["hole_diameter"] else None)})
            return {"ok": True, "pads": out}
        return {"ok": True, "created": {"id": len(calls)}}

    monkeypatch.setattr(ez, "_call", fake_call)
    mcp = _Mcp()
    ez.register_easyeda_tools(mcp)
    return mcp.tools["easyeda_create_footprint_from_package"], calls


def test_easyeda_qfn16_is_drawn_and_read_back(easyeda):
    tool, calls = easyeda
    out = asyncio.run(tool(library_uuid="lib-1",
                           package=cases.qfn16().to_dict()))
    assert out["ok"], out
    commands = [c for c, _ in calls]
    assert commands[:3] == ["lib.create_footprint", "lib.open_footprint",
                            "pcb.add_pads"]
    assert commands[-1] == "pcb.pads"
    fills = [p for c, p in calls if c == "pcb.add_fill"]
    assert sum(1 for f in fills if f["layer"] == "TOP_PASTE_MASK") == 4
    texts = [p for c, p in calls if c == "pcb.add_text"]
    assert texts[-1]["layer"] == "TOP_ASSEMBLY" and texts[-1]["align"] == "CENTER"
    lines = {p["layer"] for c, p in calls if c == "pcb.add_polyline"}
    assert lines == {"TOP_SILKSCREEN", "TOP_ASSEMBLY", "DOCUMENT"}
    assert out["readback"]["ok"] and out["pad_count"] == 17
    assert any("paste" in n for n in out["notes"])


def test_easyeda_refuses_a_non_plated_hole_before_creating(easyeda):
    tool, calls = easyeda
    out = asyncio.run(tool(library_uuid="lib-1", package={
        "family": "custom", "name": "NPTH", "body_x": 4, "body_y": 4,
        "custom_pads": [{"number": "MH", "x": 0, "y": 0, "width": 1.0,
                         "height": 1.0, "shape": "circle", "hole": 1.0,
                         "plated": False}]}))
    assert not out["ok"] and "non-plated" in out["reason"]
    assert calls == []


def test_easyeda_needs_a_library_and_a_good_package(easyeda):
    tool, calls = easyeda
    assert not asyncio.run(tool(package=cases.qfn16().to_dict()))["ok"]
    out = asyncio.run(tool(library_uuid="lib-1", package={"family": "qfn"}))
    assert not out["ok"] and "needs" in out["reason"]
    assert calls == []


# --- KiCad ---------------------------------------------------------------------


@pytest.fixture
def kicad():
    from eda_agent.tools import kicad as k

    mcp = _Mcp()
    k.register_kicad_tools(mcp)
    return mcp.tools["kicad_create_footprint_from_package"]


def test_kicad_qfn16_file_parses_back_with_both_readers(kicad, tmp_path):
    from eda_agent.core.kicad_footprint import footprint_pads_mm
    from eda_agent.design.landpattern.emit import model_from_package
    from eda_agent.libimport.kicad.reader import read_kicad_footprint
    from eda_agent.libimport.kicad.sexpr import loads

    lib = str(tmp_path / "test.pretty")
    out = asyncio.run(kicad(library_path=lib, package=cases.qfn16().to_dict()))
    assert out["ok"], out["readback"]
    assert out["pad_count"] == 17 and out["paste_windows"] == 4
    text = open(out["path"], encoding="utf-8").read()
    root = loads(text)
    assert root[0] == "footprint" and root[1] == "VQFN-16 (RGT)"
    layers = {c[1] for node in root[1:] if isinstance(node, list)
              for c in node if isinstance(c, list) and c and c[0] == "layer"}
    assert {"F.SilkS", "F.Fab", "F.CrtYd"} <= layers
    assert '"${REFERENCE}"' in text and "(attr smd)" in text
    model = model_from_package(cases.qfn16().to_dict())
    pads = [p for p in footprint_pads_mm(text) if p["number"]]
    for pad, got in zip(model.pads, pads):
        assert (got["x"], got["y"]) == pytest.approx((pad.x, pad.y))
        assert got["roundrect_rratio"] == pytest.approx(pad.corner_ratio)
    ep = next(p for p in pads if p["number"] == "17")
    assert ep["layers"] == ["F.Cu", "F.Mask"], "the EP keeps its own paste out"
    # An independent reader, the KiCad importer, turns the file back into
    # Y-up mils: its pad centres must land on the model's, which also
    # proves the Y flip went the right way.
    from eda_agent.libimport.easyeda.shapes import EePad
    from eda_agent.units import MILS_PER_MM

    fp = read_kicad_footprint(text)
    imported = [s for s in fp.footprint.shapes
                if isinstance(s, EePad) and s.number]
    assert len(imported) == 17
    for pad, got in zip(model.pads, imported):
        assert got.number == pad.number
        assert (got.cx, got.cy) == pytest.approx(
            (pad.x * MILS_PER_MM, pad.y * MILS_PER_MM), abs=1e-6)
        assert got.width == pytest.approx(pad.width * MILS_PER_MM, abs=1e-6)


def test_kicad_through_hole_slot_and_npth(kicad, tmp_path):
    from eda_agent.core.kicad_footprint import footprint_pads_mm

    lib = str(tmp_path / "th.pretty")
    out = asyncio.run(kicad(library_path=lib, package={
        "family": "custom", "name": "SLOT_TEST", "body_x": 6, "body_y": 4,
        "custom_pads": [
            {"number": "1", "x": -1.5, "y": 0.5, "width": 2.4, "height": 1.4,
             "shape": "oval", "hole": 0.8, "hole_length": 1.6},
            {"number": "MH", "x": 1.5, "y": 0, "width": 1.0, "height": 1.0,
             "shape": "circle", "hole": 1.0, "plated": False}]}))
    assert out["ok"], out["readback"]
    text = open(out["path"], encoding="utf-8").read()
    slot, mh = footprint_pads_mm(text)
    assert slot["type"] == "thru_hole" and slot["shape"] == "oval"
    assert (slot["slot"], slot["drill"]) == (1.6, 0.8)
    assert slot["y"] == 0.5 and "(at -1.5 -0.5)" in text, "Y flips"
    assert mh["type"] == "np_thru_hole" and mh["drill"] == 1.0
    assert "(attr through_hole)" in text


def test_kicad_refuses_to_overwrite_unless_asked(kicad, tmp_path):
    lib = str(tmp_path / "o.pretty")
    first = asyncio.run(kicad(library_path=lib, package=cases.soic8().to_dict()))
    assert first["ok"]
    again = asyncio.run(kicad(library_path=lib, package=cases.soic8().to_dict()))
    assert not again["ok"] and "already exists" in again["reason"]
    forced = asyncio.run(kicad(library_path=lib, package=cases.soic8().to_dict(),
                               overwrite=True))
    assert forced["ok"] and os.path.exists(forced["path"])


def test_kicad_refuses_a_bad_package_and_a_path_name(kicad, tmp_path):
    lib = str(tmp_path / "x.pretty")
    out = asyncio.run(kicad(library_path=lib, package={"family": "soic"}))
    assert not out["ok"]
    out = asyncio.run(kicad(library_path=lib, package=cases.soic8().to_dict(),
                            name="..\\escape"))
    assert not out["ok"] and "file name" in out["reason"]
    assert not os.path.exists(lib)


def test_the_3d_body_is_a_step_model_from_the_drawing_first(altium, monkeypatch):
    import pytest
    from eda_agent.design import models3d
    if "cadquery" not in models3d.available_writers():
        pytest.skip("CadQuery is not installed")
    monkeypatch.setattr(models3d, "available_writers", lambda: ["cadquery"])
    run, box = altium
    out = run(package=cases.soic8().to_dict())
    assert out["model_3d"]["kind"] == "step", out["model_3d"]
    command, params = box["bridge"].calls[-1]
    assert command == "library.link_3d_model"
    assert params["component_name"] == out["footprint"]
    assert params["model_path"].endswith(".step")
    assert out["model_3d"]["sources"]["lead_span_x"]


def test_with_no_step_writer_the_body_falls_back_to_an_extruded_box(altium, monkeypatch):
    from eda_agent.design import models3d
    monkeypatch.setattr(models3d, "available_writers", lambda: [])
    run, box = altium
    out = run(package=cases.soic8().to_dict())
    assert out["model_3d"]["kind"] == "extruded"
    assert "fallback" in out["model_3d"]["note"] and "CadQuery" in out["model_3d"]["note"]
    command, params = box["bridge"].calls[-1]
    assert command == "library.add_footprint_bodies"
    assert "overall_height=1.75" in params["bodies"]


def test_no_3d_body_when_asked_for_none(altium):
    run, box = altium
    out = run(package=cases.soic8().to_dict(), model_3d="none")
    assert out["model_3d"]["kind"] == "none"
    assert box["bridge"].commands()[-1] == "library.get_pad_geometry"
