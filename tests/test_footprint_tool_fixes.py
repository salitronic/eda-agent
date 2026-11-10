# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Defects in the existing footprint tools, each pinned by its behaviour.

Every test here fails on the code before the fix:

* lib_create_standard_footprint rounded silkscreen and courtyard track
  coordinates to whole mils;
* easyeda_create_standard_footprint refused every tab and bga, and could
  not pass an exposed pad, a courtyard, or a body through;
* easyeda_audit_footprint_vs_datasheet read holes as 0, never matched a
  shape name, ignored pad rotation, read a polygon's side count as its
  height, and never read paste expansion;
* lib_audit_footprint_vs_datasheet turned "footprint not found" into a
  pad count failure, or raised;
* kicad_generate_pcb and kicad_generate_project used placeholder boxes
  without saying so.
"""

from __future__ import annotations

import asyncio

import pytest

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


# --- lib_create_standard_footprint ------------------------------------------


class _Recorder:
    def __init__(self, reply=None, raises=None):
        self.calls = []
        self.reply = reply
        self.raises = raises

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if self.raises is not None:
            raise self.raises
        if self.reply is not None:
            return self.reply
        return {"added": 1, "failed": 0, "total": 1}


def _library_tools(monkeypatch, bridge):
    from eda_agent.tools import library as lib

    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    lib.register_library_tools(mcp)
    return mcp.tools


def test_standard_footprint_tracks_keep_fractional_mils(monkeypatch):
    from eda_agent.design.footprint_gen import generate_footprint

    bridge = _Recorder()
    tools = _library_tools(monkeypatch, bridge)
    kw = dict(pitch=59.055, pad_w=31.496, pad_h=39.37, body_w=78.74,
              body_h=49.213, courtyard=9.843)
    asyncio.run(tools["lib_create_standard_footprint"](
        name="R_1206", family="chip", pin_count=2, **kw))
    tracks = next(p for c, p in bridge.calls
                  if c == "library.add_footprint_tracks")["tracks"]
    sent = [{k: float(get_batch_field(op, k))
             for k in ("x1", "y1", "x2", "y2", "width")}
            for op in _ops(tracks)]
    geom = generate_footprint("chip", 2, **kw)
    want = [{k: float(t[k]) for k in ("x1", "y1", "x2", "y2", "width")}
            for t in geom.all_tracks()]
    assert sent == want
    assert any(v["x1"] != round(v["x1"]) for v in sent), (
        "every edge was rounded to a whole mil")


# --- easyeda_create_standard_footprint ---------------------------------------


@pytest.fixture
def easyeda(monkeypatch):
    import eda_agent.tools.easyeda as ez

    calls = []

    def fake_call(command, params=None, timeout=None):
        calls.append((command, dict(params or {})))
        if command == "lib.create_footprint":
            return {"ok": True, "uuid": "fp-1"}
        if command == "pcb.add_pads":
            n = len(params["pads"])
            return {"ok": True, "placed": n, "of": n}
        if command == "pcb.pads":
            return {"ok": True, "pads": calls_pads.pop(0)}
        return {"ok": True, "created": {"id": 1}}

    calls_pads: list = []
    monkeypatch.setattr(ez, "_call", fake_call)
    mcp = _Mcp()
    ez.register_easyeda_tools(mcp)
    return mcp.tools, calls, calls_pads


def _sent_pads(calls):
    return next(p for c, p in calls if c == "pcb.add_pads")["pads"]


def test_easyeda_tab_family_is_built(easyeda):
    tools, calls, _ = easyeda
    out = asyncio.run(tools["easyeda_create_standard_footprint"](
        library_uuid="lib", name="SOT223", family="tab", pin_count=3,
        pitch=90.55, pad_width=27.56, pad_height=59.06, row_span=248,
        tab_width=129.92, tab_height=59.06))
    assert out["ok"], out
    pads = _sent_pads(calls)
    assert [p["pad_number"] for p in pads] == ["1", "2", "3", "4"]
    assert pads[-1]["width"] == pytest.approx(129.92)


def test_easyeda_bga_is_built_without_a_pin_count(easyeda):
    tools, calls, _ = easyeda
    out = asyncio.run(tools["easyeda_create_standard_footprint"](
        library_uuid="lib", name="BGA9", family="bga", rows=3, cols=3,
        pitch=31.5, pad_width=15.7, skip=["B2"]))
    assert out["ok"], out
    pads = _sent_pads(calls)
    assert len(pads) == 8 and "B2" not in {p["pad_number"] for p in pads}
    assert {p["shape"] for p in pads} == {"ELLIPSE"}


def test_easyeda_quad_takes_an_exposed_pad_and_a_courtyard(easyeda):
    tools, calls, _ = easyeda
    out = asyncio.run(tools["easyeda_create_standard_footprint"](
        library_uuid="lib", name="QFN16", family="quad", pin_count=16,
        pitch=19.69, pad_width=9.45, pad_height=31.5, row_span=110.2,
        exposed_pad=66.1, courtyard=0.0))
    assert out["ok"], out
    pads = _sent_pads(calls)
    assert pads[-1]["pad_number"] == "17"
    assert pads[-1]["width"] == pytest.approx(66.1)
    assert not [p for c, p in calls
                if c == "pcb.add_polyline" and p["layer"] == "DOCUMENT"], (
        "courtyard=0 must leave the courtyard out")


def test_easyeda_unchanged_call_still_draws_the_courtyard(easyeda):
    tools, calls, _ = easyeda
    out = asyncio.run(tools["easyeda_create_standard_footprint"](
        library_uuid="lib", name="SOIC8", family="dual", pin_count=8,
        pitch=50, pad_width=60, pad_height=24, row_span=213))
    assert out["ok"]
    court = [p for c, p in calls
             if c == "pcb.add_polyline" and p["layer"] == "DOCUMENT"]
    assert len(court) == 4


# --- easyeda_audit_footprint_vs_datasheet ---------------------------------------

_SRC = {"datasheet_url": "https://example.invalid/ds.pdf",
        "reference": "Fig. 1, p. 2", "part_number": "TEST"}
MIL = 1 / 0.0254


def _audit(easyeda, spec, pads):
    tools, _calls, queue = easyeda
    queue.append(pads)
    return asyncio.run(tools["easyeda_audit_footprint_vs_datasheet"](
        spec_json=spec))


def _checks(report):
    return {f["check"] for f in report["findings"]}


def test_easyeda_audit_reads_the_hole_from_its_tuple(easyeda):
    spec = {"source": _SRC, "pads": [
        {"name": "1", "x": -1.27, "y": 0, "w": 1.6, "h": 1.6, "hole": 0.8},
        {"name": "2", "x": 1.27, "y": 0, "w": 1.6, "h": 1.6, "hole": 0.8}]}
    pads = [{"padNumber": n, "x": x * MIL, "y": 0, "rotation": 0,
             "pad": ["ELLIPSE", 1.6 * MIL, 1.6 * MIL],
             "hole": ["ROUND", 0.8 * MIL]} for n, x in (("1", -1.27), ("2", 1.27))]
    report = _audit(easyeda, spec, pads)
    assert "pad_hole" not in _checks(report), report["findings"]
    assert report["ok"]


def test_easyeda_audit_matches_shape_names(easyeda):
    spec = {"source": _SRC, "pads": [
        {"name": "1", "x": -1, "y": 0, "w": 1.0, "h": 0.6,
         "shape": "rectangular"},
        {"name": "2", "x": 1, "y": 0, "w": 1.0, "h": 0.6,
         "shape": "roundrectangle"}]}
    pads = [{"padNumber": "1", "x": -MIL, "y": 0, "rotation": 0,
             "pad": ["RECT", MIL, 0.6 * MIL, 0], "hole": None},
            {"padNumber": "2", "x": MIL, "y": 0, "rotation": 0,
             "pad": ["RECT", MIL, 0.6 * MIL, 3.0], "hole": None}]
    report = _audit(easyeda, spec, pads)
    assert "pad_shape" not in _checks(report), report["findings"]


def test_easyeda_audit_turns_a_rotated_pad(easyeda):
    spec = {"source": _SRC, "pads": [
        {"name": "1", "x": -1, "y": 0, "w": 1.5, "h": 0.6},
        {"name": "2", "x": 1, "y": 0, "w": 1.5, "h": 0.6}]}
    pads = [{"padNumber": n, "x": x * MIL, "y": 0, "rotation": 90,
             "pad": ["RECT", 0.6 * MIL, 1.5 * MIL, 0], "hole": None}
            for n, x in (("1", -1), ("2", 1))]
    report = _audit(easyeda, spec, pads)
    assert "pad_size" not in _checks(report), report["findings"]


def test_easyeda_audit_reads_a_polygon_diameter_not_its_sides(easyeda):
    spec = {"source": _SRC, "pads": [
        {"name": "1", "x": -1, "y": 0, "w": 1.2, "h": 1.2,
         "shape": "octagonal"},
        {"name": "2", "x": 1, "y": 0, "w": 1.2, "h": 1.2,
         "shape": "octagonal"}]}
    pads = [{"padNumber": n, "x": x * MIL, "y": 0, "rotation": 0,
             "pad": ["NGON", 1.2 * MIL, 8], "hole": None}
            for n, x in (("1", -1), ("2", 1))]
    report = _audit(easyeda, spec, pads)
    assert not {"pad_size", "pad_shape"} & _checks(report), report["findings"]


def test_easyeda_audit_sees_a_windowed_thermal_pad(easyeda):
    spec = {"source": _SRC, "pads": [
        {"name": "1", "x": -1.5, "y": 0, "w": 0.8, "h": 0.3},
        {"name": "2", "x": 1.5, "y": 0, "w": 0.8, "h": 0.3}],
        "thermal_pad": {"name": "3", "x": 0, "y": 0, "w": 1.7, "h": 1.7},
        "thermal_paste": "windowed"}
    pads = [{"padNumber": "1", "x": -1.5 * MIL, "y": 0, "rotation": 0,
             "pad": ["RECT", 0.8 * MIL, 0.3 * MIL, 0], "hole": None},
            {"padNumber": "2", "x": 1.5 * MIL, "y": 0, "rotation": 0,
             "pad": ["RECT", 0.8 * MIL, 0.3 * MIL, 0], "hole": None},
            {"padNumber": "3", "x": 0, "y": 0, "rotation": 0,
             "pad": ["RECT", 1.7 * MIL, 1.7 * MIL, 0], "hole": None,
             "solderMaskAndPasteMaskExpansion": {"topPasteMask": -1.7 * MIL}}]
    report = _audit(easyeda, spec, pads)
    assert "thermal_paste" not in _checks(report), report["findings"]


# --- lib_audit_footprint_vs_datasheet ------------------------------------------

_SPEC = {"source": _SRC, "dual_row": {"count": 8, "pitch": 1.27, "span": 5.4,
                                      "pad_w": 1.55, "pad_h": 0.6}}


def test_altium_audit_of_a_missing_footprint_is_not_a_pad_count(monkeypatch):
    from eda_agent.bridge.exceptions import PreconditionError

    bridge = _Recorder(raises=PreconditionError(
        "Footprint not found: NOPE", code="NO_FOOTPRINT"))
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_audit_footprint_vs_datasheet"](
        footprint_name="NOPE", spec_json=_SPEC))
    assert out["ok"] is False and "could not read footprint" in out["reason"]
    assert out["findings"] == [] and out["error_code"] == "NO_FOOTPRINT"


def test_altium_audit_of_a_refusal_reply_is_not_a_pad_count(monkeypatch):
    bridge = _Recorder(reply={"success": False, "error": {
        "code": "NO_FOOTPRINT", "message": "Footprint not found: NOPE"}})
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_audit_footprint_vs_datasheet"](
        footprint_name="NOPE", spec_json=_SPEC))
    assert out["ok"] is False and "not found" in out["reason"]
    assert not any(f.get("check") == "pad_count" for f in out["findings"])


def test_altium_audit_of_a_real_empty_footprint_still_reports_it(monkeypatch):
    bridge = _Recorder(reply={"name": "EMPTY", "pad_count": 0, "pads": []})
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_audit_footprint_vs_datasheet"](
        footprint_name="EMPTY", spec_json=_SPEC))
    assert out["ok"] is False
    assert any(f["check"] == "pad_count" for f in out["findings"])


# --- kicad_generate_pcb / kicad_generate_project -------------------------------

_PLAN = {"spec": "t", "summary": "t", "sheets": [{"name": "main"}],
         "zones": [], "parts": [
             {"refdes": "R1", "lib_ref": "R", "value": "10k",
              "footprint": "NoSuchLib:R_0603", "status": "existing"},
             {"refdes": "R2", "lib_ref": "R", "value": "1k",
              "status": "existing"}],
         "nets": [{"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                                         {"refdes": "R2", "pin": "1"}]}],
         "bom": [], "design_rules": [], "open_questions": []}


@pytest.fixture
def kicad_tools(monkeypatch):
    from eda_agent.tools import kicad as k

    class _NoKiCad:
        def kicad_cli_path(self):
            raise RuntimeError("no kicad-cli here")

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _NoKiCad())
    mcp = _Mcp()
    k.register_kicad_tools(mcp)
    return mcp.tools


def test_generate_pcb_names_its_placeholder_footprints(kicad_tools, tmp_path):
    out = asyncio.run(kicad_tools["kicad_generate_pcb"](
        plan_json=_PLAN, output_path=str(tmp_path / "b.kicad_pcb")))
    assert out["ok"] is True
    assert out["placeholder_footprints"] == ["R1", "R2"]
    assert "placeholder" in out["warning"] and "R1, R2" in out["warning"]


def test_generate_project_names_them_too(kicad_tools, tmp_path):
    out = asyncio.run(kicad_tools["kicad_generate_project"](
        plan_json=_PLAN, output_dir=str(tmp_path / "proj")))
    assert out["ok"] is True
    assert out["placeholder_footprints"] == ["R1", "R2"]


def test_build_pcb_reports_only_the_boxes():
    from eda_agent.core.kicad_footprint import build_footprint
    from eda_agent.core.kicad_pcb import build_pcb

    mod = build_footprint("R", [
        {"number": "1", "x_mm": -0.8, "y_mm": 0, "w_mm": 0.9, "h_mm": 0.95},
        {"number": "2", "x_mm": 0.8, "y_mm": 0, "w_mm": 0.9, "h_mm": 0.95}])
    boxes: list[str] = []
    build_pcb([{"refdes": "R1"}, {"refdes": "R2"}],
              [{"name": "A", "nodes": [{"refdes": "R1", "pin": "1"},
                                       {"refdes": "R2", "pin": "1"}]}],
              mod_texts={"R1": mod}, placeholders=boxes)
    assert boxes == ["R2"]


def test_standard_footprint_courtyard_goes_on_the_courtyard_layer(monkeypatch):
    # #302: the courtyard went on Mechanical1 as plain tracks and the
    # renderer found no layer of the Courtyard kind.
    class Bridge(_Recorder):
        async def send_command_async(self, command, params=None, timeout=None):
            if command == "library.get_mech_layers":
                self.calls.append((command, dict(params or {})))
                return {"layers": [{"layer": "Mechanical15", "name": "Courtyard",
                                    "kind": "Courtyard Top"}]}
            return await super().send_command_async(command, params, timeout)

    bridge = Bridge()
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_create_standard_footprint"](
        name="SON", family="dual", pin_count=8, pitch=25.6, pad_w=30,
        pad_h=12, row_span=110, body_w=118, body_h=118))
    tracks = next(p for c, p in bridge.calls
                  if c == "library.add_footprint_tracks")["tracks"]
    layers = [get_batch_field(op, "layer") for op in _ops(tracks)]
    assert layers.count("Mechanical15") == 4 and "Mechanical1" not in layers
    assert out["courtyard_layer"] == "Mechanical15"
