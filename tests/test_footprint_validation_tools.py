# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The footprint validation tools, offline.

``footprint_check_package``, ``footprint_render_package`` and
``datasheet_fetch`` need no editor. ``lib_audit_footprint_vs_package`` is
driven through a fake bridge answering the three reads it sends from a
footprint the package tool would write; ``easyeda_audit_footprint_vs_package``
through a fake ``_call``. The new Pascal read is held to its contract by
reading its source, since it cannot run here.
"""

from __future__ import annotations

import asyncio
import copy
import re
from pathlib import Path

import pytest

from eda_agent.design.landpattern import build_footprint
from tests.design import _landpattern_cases as cases
from tests.design.test_landpattern_audit import MECH, readback


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def tools(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    from eda_agent.tools.footprint import register_footprint_tools

    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path)
    mcp = _Mcp()
    register_footprint_tools(mcp)
    return mcp.tools


def run(coro):
    return asyncio.run(coro)


# --- footprint_check_package ------------------------------------------------------


def test_check_a_good_package_with_its_vendor_pattern(tools):
    out = run(tools["footprint_check_package"](package=cases.soic8().to_dict()))
    assert out["ok"] and out["errors"] == 0 and out["warnings"] == 0
    assert out["cross_check"]["ran"] and out["cross_check"]["ok"]
    assert out["summary"].endswith("cross-check agrees")


def test_check_a_misread_package_skips_the_cross_check(tools):
    d = cases.soic8().to_dict()
    d["pitch"] = 4.9
    out = run(tools["footprint_check_package"](package=d))
    assert not out["ok"] and out["errors"] >= 1
    assert out["cross_check"]["ran"] is False
    assert "sanity errors" in out["cross_check"]["reason"]


def test_check_a_vendor_typo_fails_on_the_cross_check(tools):
    """TI RGT0016C example layout: 2.8 centre to centre, read as 3.8. The
    vendor pattern is still consistent with itself, so only the cross-check
    sees it."""
    d = cases.qfn16().to_dict()
    d["vendor"]["span"] = 3.8
    out = run(tools["footprint_check_package"](package=d))
    assert out["errors"] == 0
    assert not out["ok"] and out["summary"].endswith("disagrees on span")


def test_check_accepts_json_text_and_reports_bad_json(tools):
    import json

    out = run(tools["footprint_check_package"](
        package=json.dumps(cases.sod123().to_dict())))
    assert out["ok"]
    out = run(tools["footprint_check_package"](package="{nope"))
    assert not out["ok"] and out["findings"][0]["check"] == "readable"


# --- footprint_render_package -------------------------------------------------------


def test_render_writes_an_svg_with_the_terminals(tools, tmp_path):
    target = tmp_path / "out" / "soic.svg"
    out = run(tools["footprint_render_package"](
        package=cases.soic8().to_dict(), path=str(target), png=False))
    assert out["ok"] and out["svg_path"] == str(target)
    svg = target.read_text(encoding="utf-8")
    assert '<g id="terminals">' in svg and svg.count("stroke-dasharray=\"0.08") == 8
    assert "IPC land pattern, mm: pitch 1.27, span 4.9 c-c, land 1.95 x 0.6" in svg
    assert out["terminals_drawn"] == 8 and out["png_path"] is None


def test_render_defaults_into_the_workspace_and_rasterizes_when_it_can(
        tools, tmp_path, monkeypatch):
    import eda_agent.render as render

    seen = {}

    def fake_raster(svg_path, png_path=None, width=1600, height=None, **kw):
        seen["svg"] = Path(svg_path).read_text(encoding="utf-8")
        Path(png_path).write_bytes(b"\x89PNG")
        return {"ok": True, "png_path": png_path}

    monkeypatch.setattr(render, "rasterize_svg", fake_raster)
    out = run(tools["footprint_render_package"](
        package=cases.qfn16().to_dict(), density="M"))
    assert out["ok"] and out["density"] == "M"
    assert Path(out["svg_path"]).parent == tmp_path / "renders"
    assert out["png_path"].endswith(".png") and Path(out["png_path"]).exists()
    assert 'width="1400"' in seen["svg"]
    assert not list((tmp_path / "renders").glob("*.raster.svg"))


def test_render_without_a_browser_still_gives_the_svg(tools, monkeypatch):
    import eda_agent.render as render

    monkeypatch.setattr(render, "rasterize_svg", lambda *a, **k: {
        "ok": False, "reason": "no Edge/Chromium browser found", "png_path": None})
    out = run(tools["footprint_render_package"](package=cases.bga80().to_dict()))
    assert out["ok"] and out["png_path"] is None
    assert "browser" in out["rasterize_note"]
    assert out["next_step"].startswith(f"Read {out['svg_path']}")


def test_render_refuses_a_package_the_engine_cannot_draw(tools):
    out = run(tools["footprint_render_package"](
        package={"family": "soic", "pins": 8}, png=False))
    assert not out["ok"] and "needs" in out["reason"]


# --- datasheet_fetch ----------------------------------------------------------------


def test_the_fetch_tool_caches_into_the_workspace(tools, tmp_path, monkeypatch):
    from eda_agent.design import datasheet_fetch as dsf
    from tests.test_datasheet_fetch import PDF, FakeNet

    fake = FakeNet()
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    url = "https://www.example.com/ds/x.pdf"
    first = run(tools["datasheet_fetch"](url=url, part_number="X1"))
    assert first["ok"] and first["cache"] == "miss"
    assert Path(first["path"]).parent == tmp_path / "datasheets"
    assert Path(first["path"]).read_bytes() == PDF
    again = run(tools["datasheet_fetch"](url=url))
    assert again["cache"] == "hit" and len(fake.calls) == 1
    refused = run(tools["datasheet_fetch"](url="file:///etc/passwd"))
    assert not refused["ok"] and len(fake.calls) == 1


# --- lib_audit_footprint_vs_package ---------------------------------------------------


class FakeLibrary:
    """Answers the three reads the audit sends, from a written footprint."""

    def __init__(self, pads, prims, mech=MECH, lib="scratch.PcbLib",
                 prims_error=None, pads_error=None, mech_lib=None):
        self.pads, self.prims, self.mech = pads, prims, mech
        self.lib, self.mech_lib = lib, mech_lib or lib
        self.prims_error, self.pads_error = prims_error, pads_error
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        from eda_agent.bridge.exceptions import AltiumCommandError

        params = dict(params or {})
        self.calls.append((command, params))
        if command == "library.get_pad_geometry":
            if self.pads_error:
                raise AltiumCommandError(self.pads_error, code="NO_FOOTPRINT")
            return {"name": params["footprint_name"], "library_path": self.lib,
                    "pad_count": len(self.pads), "pads": self.pads}
        if command == "library.get_footprint_primitives":
            if self.prims_error:
                raise AltiumCommandError(self.prims_error, code="UNKNOWN_ACTION")
            return dict(self.prims, name=params["footprint_name"],
                        library_path=self.lib)
        if command == "library.get_mech_layers":
            return {"library_path": self.mech_lib, "count": len(self.mech),
                    "layers": self.mech}
        raise AssertionError(f"the audit sent {command}, which is not a read")


@pytest.fixture
def audit(monkeypatch):
    from eda_agent.tools import library as lib

    box = {}
    monkeypatch.setattr(lib, "get_bridge", lambda: box["bridge"])
    mcp = _Mcp()
    lib.register_library_tools(mcp)
    tool = mcp.tools["lib_audit_footprint_vs_package"]

    def call(bridge, **kw):
        box["bridge"] = bridge
        return run(tool(**kw))

    return call


def _soic_footprint():
    pads, prims = readback(build_footprint(cases.soic8()))
    return pads, prims


def test_audit_a_correct_footprint(audit):
    pads, prims = _soic_footprint()
    bridge = FakeLibrary(pads, prims)
    out = audit(bridge, footprint_name="SOIC-8 (D)",
                package=cases.soic8().to_dict())
    assert out["ok"] and out["findings"] == [], out["findings"]
    assert [c for c, _ in bridge.calls] == [
        "library.get_pad_geometry", "library.get_footprint_primitives",
        "library.get_mech_layers"]
    # The primitives read is aimed at the library the pads came from.
    assert bridge.calls[1][1]["library_path"] == "scratch.PcbLib"
    assert out["library_path"] == "scratch.PcbLib"
    assert "courtyard" in out["examined"] and out["package_check"]["ok"]
    assert out["primitives_read"] == {"tracks": 11, "arcs": 1, "regions": 0,
                                      "fills": 0, "texts": 0, "height_mm": 1.75}


def test_audit_finds_silk_on_copper_and_a_missing_courtyard(audit):
    pads, prims = _soic_footprint()
    prims = copy.deepcopy(prims)
    prims["tracks"] = [t for t in prims["tracks"] if t["layer"] != "Mechanical15"]
    prims["tracks"].append({"layer": "TopOverlay", "x1": -3.0, "y1": 1.905,
                            "x2": -1.5, "y2": 1.905, "width": 0.2})
    out = audit(FakeLibrary(pads, prims), footprint_name="SOIC-8 (D)",
                package=cases.soic8().to_dict())
    assert not out["ok"]
    assert {f["check"] for f in out["findings"]} == {"silk", "courtyard"}


def test_an_old_script_without_the_primitives_read_still_audits_the_pads(audit):
    pads, prims = _soic_footprint()
    out = audit(FakeLibrary(pads, prims, prims_error="Unknown library action"),
                footprint_name="SOIC-8 (D)", package=cases.soic8().to_dict())
    assert out["ok"]
    reasons = {s["check"]: s["reason"] for s in out["not_examined"]}
    assert "get_footprint_primitives" in reasons["courtyard"]


def test_mech_kinds_from_another_library_are_not_trusted(audit):
    pads, prims = _soic_footprint()
    out = audit(FakeLibrary(pads, prims, mech_lib="other.PcbLib"),
                footprint_name="SOIC-8 (D)", package=cases.soic8().to_dict())
    assert any("other.PcbLib" in n for n in out["notes"])
    assert any(f["check"] == "courtyard" for f in out["findings"])
    out = audit(FakeLibrary(pads, prims, mech_lib="other.PcbLib"),
                footprint_name="SOIC-8 (D)", package=cases.soic8().to_dict(),
                courtyard_layer="Mechanical15", assembly_layer="Mechanical13")
    assert out["ok"], out["findings"]


def test_audit_of_a_footprint_that_is_not_there(audit):
    out = audit(FakeLibrary([], {}, pads_error="Footprint not found: X"),
                footprint_name="X", package=cases.soic8().to_dict())
    assert not out["ok"] and "could not read footprint 'X'" in out["reason"]
    assert out["findings"] == []


def test_audit_refuses_a_bad_package_before_reading(audit):
    bridge = FakeLibrary([], {})
    out = audit(bridge, footprint_name="X", package={"family": "qfn"})
    assert not out["ok"] and bridge.calls == []


def test_audit_says_when_the_package_itself_is_wrong(audit):
    d = cases.soic8().to_dict()
    d["lead_width"] = [0.31, 0.51, 0.71]
    d["body_x"], d["body_y"] = d["body_y"], d["body_x"]
    pads, prims = _soic_footprint()
    out = audit(FakeLibrary(pads, prims), footprint_name="SOIC-8 (D)", package=d)
    assert out["package_check"]["ok"] is False
    assert any("cannot be right" in n for n in out["notes"])


# --- easyeda_audit_footprint_vs_package ---------------------------------------------


def test_easyeda_audit_reads_pads_and_lists_what_it_cannot(monkeypatch):
    import eda_agent.tools.easyeda as ez
    from eda_agent.units import MILS_PER_MM

    model = build_footprint(cases.qfn16())
    raw = []
    for p in model.pads:
        raw.append({"padNumber": p.number, "x": p.x * MILS_PER_MM,
                    "y": p.y * MILS_PER_MM, "rotation": 0,
                    "pad": ["RECT", p.width * MILS_PER_MM,
                            p.height * MILS_PER_MM, 1.0],
                    "hole": None,
                    "solderMaskAndPasteMaskExpansion": (
                        {"topPasteMask": -max(p.width, p.height) * MILS_PER_MM}
                        if p.paste_apertures else None)})
    calls = []

    def fake_call(command, params=None, timeout=None):
        calls.append(command)
        assert command == "pcb.pads"
        return {"ok": True, "pads": raw}

    monkeypatch.setattr(ez, "_call", fake_call)
    mcp = _Mcp()
    ez.register_easyeda_tools(mcp)
    out = run(mcp.tools["easyeda_audit_footprint_vs_package"](
        package=cases.qfn16().to_dict()))
    assert out["ok"], out["findings"]
    assert calls == ["pcb.pads"] and out["pad_count_read"] == 17
    skipped = {s["check"] for s in out["not_examined"]}
    assert {"courtyard", "silk", "assembly", "height"} <= skipped
    assert any("Y direction" in n for n in out["notes"])


def test_easyeda_audit_needs_an_open_footprint(monkeypatch):
    import eda_agent.tools.easyeda as ez

    monkeypatch.setattr(ez, "_call", lambda c, p=None, timeout=None: {
        "ok": True, "pads": []})
    mcp = _Mcp()
    ez.register_easyeda_tools(mcp)
    out = run(mcp.tools["easyeda_audit_footprint_vs_package"](
        package=cases.qfn16().to_dict()))
    assert not out["ok"] and "easyeda_open_footprint" in out["reason"]


# --- the Pascal read ---------------------------------------------------------------


def _library_pas() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts" / "altium"
            / "Library.pas").read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?m)^Function {name}\(", code)
    assert start, name
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_primitives_read_is_dispatched_and_restores_focus():
    code = _library_pas()
    assert re.search(r"'get_footprint_primitives':\s*Result := "
                     r"Lib_GetFootprintPrimitives\(Params, RequestId\);", code)
    ro = _routine(code, "LibActionIsReadOnly")
    assert "(Action = 'get_footprint_primitives')" in ro
    body = _routine(code, "Lib_GetFootprintPrimitives")
    for key in ("footprint_name", "library_path"):
        assert f"ExtractJsonValue(Params, '{key}')" in body, key


def test_the_primitives_read_writes_nothing():
    """A read must not edit the library: no object property is assigned,
    nothing is added or removed, the document is not marked dirty."""
    body = _routine(_library_pas(), "Lib_GetFootprintPrimitives")
    for banned in ("AddPCBObject", "RemovePCBObject", "PreProcess",
                   "PostProcess", "PcbBeginEdit", "PcbEndEdit",
                   "MarkDocDirty", "MarkLibDirty",
                   "SendMessageToRobots"):
        assert banned not in body, banned
    writes = re.findall(r"(?m)^\s*([A-Za-z_][\w]*(?:\.[\w\[\]]+)+)\s*:=", body)
    assert writes == [], writes


def test_the_primitives_read_answers_in_the_shape_the_audit_reads():
    body = _routine(_library_pas(), "Lib_GetFootprintPrimitives")
    for key in ('"height_mm"', '"tracks"', '"arcs"', '"regions"', '"fills"',
                '"texts"', '"layer"', '"x1"', '"y1"', '"x2"', '"y2"',
                '"width"', '"xc"', '"yc"', '"r"', '"start"', '"end"',
                '"area"', '"text"', '"size"', '"rotation"'):
        assert key in body, key
    # Only typed loop variables touch subtype members.
    assert not re.search(r"\bPrim\.(X1|Y1|X2|Y2|Width|XCenter|Radius)\b", body)
