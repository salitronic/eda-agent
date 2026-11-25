# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Backend selection, the neutral EDA-agnostic tools (fed a fake backend), the
Altium snapshot adapter, and the KiCad connection check. No live EDA required."""

from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.core.snapshot import DesignSnapshot


def _payload(result):
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


def _call(mcp, name, **kwargs):
    return _payload(asyncio.run(mcp.call_tool(name, kwargs)))


# -- backend dispatch -------------------------------------------------------

def _surface(backend):
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend
    m = FastMCP("test")
    used = register_backend(m, backend)
    names = {t.name for t in asyncio.run(m.list_tools())}
    return used, names

_NEUTRAL = {"review_design", "get_board_info", "list_components", "list_nets",
            "run_drc", "run_erc"}


def test_altium_backend_has_neutral_tools_but_no_kicad_native():
    used, names = _surface("altium")
    assert used == "altium"
    assert _NEUTRAL <= names
    assert not any(n.startswith("kicad_") for n in names)
    assert "proj_get_bom" in names


def test_kicad_backend_has_neutral_and_native_no_altium():
    used, names = _surface("kicad")
    assert used == "kicad"
    assert _NEUTRAL <= names
    # KiCad-native reads and exports are present...
    assert {"kicad_ping", "kicad_list_tracks", "kicad_export_gerbers",
            "kicad_export_step", "kicad_export_bom"} <= names
    # ...and no Altium live tools leak in.
    assert "proj_get_bom" not in names
    assert not any(n.startswith("proj_") or n.startswith("audit_")
                   for n in names)


def test_both_backend_is_the_union():
    _, alt = _surface("altium")
    _, kic = _surface("kicad")
    used, both = _surface("both")
    assert used == "both"
    assert both == alt | kic


def test_unknown_backend_falls_back_to_default():
    used, names = _surface("nonsense")
    assert used == "altium"
    assert _NEUTRAL <= names


# -- neutral tools over a fake backend --------------------------------------

class _FakeBackend:
    name = "fake"

    def __init__(self, snap):
        self._snap = snap

    async def snapshot(self):
        return self._snap


@pytest.fixture
def eda_mcp():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_eda_tools
    m = FastMCP("test")
    register_eda_tools(m)
    return m


def _install(monkeypatch, snap):
    import eda_agent.tools.eda as e
    monkeypatch.setattr(e, "resolve_backend", lambda name=None: _FakeBackend(snap))


def _demo_snapshot():
    parts = [{"refdes": "U1", "value": "MCU"}, {"refdes": "C1", "value": "100n"}]
    pins = [{"refdes": "U1", "pin": "1", "net": "3V3"},
            {"refdes": "C1", "pin": "1", "net": "3V3"},
            {"refdes": "U1", "pin": "2", "net": "GND"},
            {"refdes": "C1", "pin": "2", "net": "GND"}]
    return DesignSnapshot.build("fake", parts, pins, board_name="demo")


def test_review_design_runs_shared_engine(monkeypatch, eda_mcp):
    _install(monkeypatch, _demo_snapshot())
    res = _call(eda_mcp, "review_design")
    assert res["ok"] is True
    assert res["source"] == "fake"
    assert set(res["summary"]) == {"error", "warning", "info"}


def test_list_components_and_nets(monkeypatch, eda_mcp):
    _install(monkeypatch, _demo_snapshot())
    comps = _call(eda_mcp, "list_components")
    assert comps["count"] == 2
    assert {c["reference"] for c in comps["components"]} == {"U1", "C1"}
    nets = _call(eda_mcp, "list_nets")
    assert nets["count"] == 2
    assert any(n["is_power"] for n in nets["nets"])
    assert any(n["is_ground"] for n in nets["nets"])


def test_get_board_info(monkeypatch, eda_mcp):
    _install(monkeypatch, _demo_snapshot())
    info = _call(eda_mcp, "get_board_info")
    assert info["ok"] is True
    assert info["stats"]["part_count"] == 2
    assert "net_classes" in info


def test_run_drc_dispatches_to_backend(monkeypatch, eda_mcp):
    import eda_agent.tools.eda as e

    class _DrcBackend:
        name = "fake"
        async def run_drc(self):
            return {"ok": True, "source": "fake", "violation_count": 2,
                    "summary": {"error": 1, "warning": 1}}

    monkeypatch.setattr(e, "resolve_backend", lambda name=None: _DrcBackend())
    res = _call(eda_mcp, "run_drc")
    assert res["ok"] is True and res["violation_count"] == 2


def test_kicad_drc_normalizer():
    from eda_agent.core.kicad_drc import _normalize
    report = {
        "coordinate_units": "mm",
        "violations": [
            {"type": "clearance", "severity": "error",
             "description": "Clearance violation",
             "items": [{"description": "pad"}, {"description": "track"}]},
            {"type": "silk_over_copper", "severity": "warning",
             "description": "Silk on pad", "items": [{"description": "x"}]},
        ],
        "unconnected_items": [{"type": "unconnected"}],
        "schematic_parity": [],
    }
    out = _normalize(report, "/tmp/Board.kicad_pcb")
    assert out["ok"] is True and out["source"] == "kicad"
    assert out["violation_count"] == 2
    assert out["unconnected_count"] == 1
    assert out["summary"] == {"error": 1, "warning": 1}
    assert out["violations"][0]["item_count"] == 2
    assert out["board"] == "Board.kicad_pcb"


def test_neutral_tool_reports_reason_when_backend_unavailable(monkeypatch, eda_mcp):
    import eda_agent.tools.eda as e
    from eda_agent.core.backends import BackendUnavailableError

    class _Dead:
        name = "dead"
        async def snapshot(self):
            raise BackendUnavailableError("no board open")

    monkeypatch.setattr(e, "resolve_backend", lambda name=None: _Dead())
    res = _call(eda_mcp, "review_design")
    assert res["ok"] is False
    assert "no board open" in res["reason"]


# -- Altium snapshot adapter (BOM shape, no live Altium) --------------------

def test_altium_adapter_builds_snapshot_from_bom(monkeypatch):
    from eda_agent.core import backends

    bom = {"components": [
        {"designator": "R1", "comment": "10k", "footprint": "0402",
         "lib_ref": "RES", "pins": [
             {"pin": "1", "name": "1", "net": "VIN"},
             {"pin": "2", "name": "2", "net": "FB"}]},
        {"designator": "U1", "comment": "REG", "footprint": "SOT23",
         "lib_ref": "LDO", "pins": [
             {"pin": "1", "name": "IN", "net": "VIN"},
             {"pin": "2", "name": "GND", "net": "GND"},
             {"pin": "3", "name": "OUT", "net": ""}]},
    ]}

    class _FakeBridge:
        async def send_command_async(self, cmd, params=None, timeout=None):
            assert cmd == "project.get_bom"
            return bom

    async def fake_bridge(self):
        from eda_agent.bridge.exceptions import AltiumError
        return _FakeBridge(), AltiumError

    monkeypatch.setattr(backends.AltiumBackend, "_bridge", fake_bridge)
    snap = asyncio.run(backends.AltiumBackend().snapshot())
    assert snap.source == "altium"
    assert {p.refdes for p in snap.parts} == {"R1", "U1"}
    assert {n.name for n in snap.nets} == {"VIN", "FB", "GND"}
    assert snap.unconnected_pad_count == 1   # U1.3 has no net


# -- KiCad-native reads and exports -----------------------------------------

@pytest.fixture
def kicad_native_mcp():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_kicad_tools
    m = FastMCP("test")
    register_kicad_tools(m)
    return m


class _ReadBridge:
    def tracks(self):
        return [{"net": "GND", "layer": "F.Cu", "width_mm": 0.2,
                 "length_mm": 5.0},
                {"net": "GND", "layer": "F.Cu", "width_mm": 0.2,
                 "length_mm": 3.0},
                {"net": "VCC", "layer": "F.Cu", "width_mm": 0.3,
                 "length_mm": 2.0}]
    def stackup(self):
        return [{"name": "F.Cu", "type": 1}]
    def nets(self):
        return [{"name": "USB_D+"}, {"name": "USB_D-"}, {"name": "CLK_P"},
                {"name": "CLK_N"}, {"name": "GND"}, {"name": "VCC"}]
    def vias(self):
        return [{"diameter_mm": 0.6, "drill_mm": 0.3},
                {"diameter_mm": 0.6, "drill_mm": 0.3},
                {"diameter_mm": 0.8, "drill_mm": 0.4}]
    def board_outline(self):
        return {"bbox_mm": {"x": 0.0, "y": 0.0, "w": 50.0, "h": 40.0},
                "edge_shape_count": 4}
    def board_stats(self):
        return {"name": "b", "footprints": 3, "nets": 5, "pads": 6,
                "tracks": 3, "vias": 3, "zones": 1, "stackup_layers": 2}
    def net_classes(self):
        return {"by_net": {"GND": "Default"},
                "classes": {"Default": {"clearance_mm": 0.2}}}
    def pads(self):
        return [{"number": "1", "net": "GND", "x_mm": 1.0, "y_mm": 2.0,
                 "pad_type": 1}]
    def footprints(self):
        return [{"reference": "R1", "value": "10k", "x_mm": 1.0, "y_mm": 2.0,
                 "layer": 0, "locked": False},
                {"reference": "C1", "value": "100n"},
                {"reference": "R2", "value": "10k"}]
    def component_pins(self):
        parts = [{"refdes": "R1"}, {"refdes": "C1"}, {"refdes": "R2"}]
        pins = [{"refdes": "R1", "pin": "1", "net": "VIN"},
                {"refdes": "R1", "pin": "2", "net": "GND"},
                {"refdes": "C1", "pin": "1", "net": ""}]  # unconnected
        return parts, pins, 1
    def open_documents(self):
        return [{"type": "pcb", "filename": "Demo.kicad_pcb",
                 "project": "Demo"},
                {"type": "schematic", "filename": "Demo.kicad_sch",
                 "project": "Demo"}]
    def project_info(self):
        return {"name": "Demo", "path": "/tmp/demo", "net_classes": ["Default"]}
    def text_variables(self):
        return {"REV": "B"}
    def texts(self):
        return [{"value": "REV B", "layer": "F.Silkscreen", "x_mm": 1.0,
                 "y_mm": 2.0}]
    def groups(self):
        return [{"name": "grp", "item_count": 3}]
    def dimensions(self):
        return [{"layer": "User.Drawings", "override_text": "",
                 "height_mm": 10.0}]
    def shapes(self):
        return [{"type": "segment", "layer": "Edge.Cuts", "net": "",
                 "start_mm": [0.0, 0.0], "end_mm": [10.0, 0.0]}]
    def selection(self):
        return [{"type": "FootprintInstance", "reference": "U1"},
                {"type": "Track"}]
    def kicad_cli_path(self):
        return "kicad-cli"
    def board_file_path(self):
        return "/tmp/Board.kicad_pcb"


def test_kicad_read_tool_wraps_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    res = _call(kicad_native_mcp, "kicad_list_tracks")
    assert res["ok"] is True and res["count"] == 3
    assert res["tracks"][0]["net"] == "GND"


def test_kicad_layer_usage(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_layer_usage")
    assert r["ok"] is True
    fcu = next(l for l in r["layers"] if l["layer"] == "F.Cu")
    assert fcu["track_count"] == 3 and fcu["total_length_mm"] == 10.0


def test_kicad_trace_lengths(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_trace_lengths")
    assert r["ok"] is True and r["net_count"] == 2
    gnd = next(x for x in r["lengths"] if x["net"] == "GND")
    assert gnd["length_mm"] == 8.0          # 5.0 + 3.0
    # Sorted longest-first: GND (8) before VCC (2).
    assert r["lengths"][0]["net"] == "GND"


def test_kicad_net_classes_and_pads_reads(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    nc = _call(kicad_native_mcp, "kicad_get_net_classes")
    assert nc["ok"] is True
    assert nc["net_classes"]["by_net"]["GND"] == "Default"
    pads = _call(kicad_native_mcp, "kicad_list_pads")
    assert pads["ok"] is True and pads["count"] == 1
    assert pads["pads"][0]["net"] == "GND"


def test_kicad_text_groups_dimensions_reads(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    tx = _call(kicad_native_mcp, "kicad_list_text")
    assert tx["ok"] is True and tx["text"][0]["value"] == "REV B"
    gr = _call(kicad_native_mcp, "kicad_list_groups")
    assert gr["ok"] is True and gr["groups"][0]["item_count"] == 3
    dm = _call(kicad_native_mcp, "kicad_list_dimensions")
    assert dm["ok"] is True and dm["dimensions"][0]["height_mm"] == 10.0
    sh = _call(kicad_native_mcp, "kicad_list_shapes")
    assert sh["ok"] is True and sh["shapes"][0]["type"] == "segment"
    sel = _call(kicad_native_mcp, "kicad_get_selection")
    assert sel["ok"] is True and sel["count"] == 2
    assert sel["selection"][0]["reference"] == "U1"


def test_kicad_get_unconnected_pins(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_unconnected_pins")
    assert r["ok"] is True and r["count"] == 1
    assert r["pins"][0]["reference"] == "C1"


def test_kicad_board_summary(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_board_summary")
    assert r["ok"] is True
    assert r["board_size_mm"] == {"w": 50.0, "h": 40.0}
    assert r["board_bbox_area_mm2"] == 2000.0
    assert r["distinct_via_sizes"] == 2 and r["footprints"] == 3


def test_kicad_via_summary(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_via_summary")
    assert r["ok"] is True and r["via_count"] == 3
    assert len(r["via_types"]) == 2         # two distinct sizes
    assert r["via_types"][0]["count"] == 2  # the 0.6/0.3 pair, most common


def test_kicad_get_diff_pairs(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_diff_pairs")
    assert r["ok"] is True and r["count"] == 2
    bases = {p["base"] for p in r["pairs"]}
    assert bases == {"USB_D", "CLK"}
    usb = next(p for p in r["pairs"] if p["base"] == "USB_D")
    assert usb["positive"] == "USB_D+" and usb["negative"] == "USB_D-"


def test_kicad_get_net(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_net", net_name="GND")
    assert r["ok"] is True and r["pad_count"] == 1
    assert r["pads"][0]["reference"] == "R1" and r["pads"][0]["pin"] == "2"
    miss = _call(kicad_native_mcp, "kicad_get_net", net_name="NOPE")
    assert miss["ok"] is False


def test_kicad_get_component_details(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_get_component_details", reference="R1")
    assert r["ok"] is True and r["value"] == "10k" and r["x_mm"] == 1.0
    assert r["pin_count"] == 2
    assert {p["net"] for p in r["pins"]} == {"VIN", "GND"}
    miss = _call(kicad_native_mcp, "kicad_get_component_details",
                 reference="ZZ9")
    assert miss["ok"] is False and "ZZ9" in miss["reason"]


def test_kicad_find_component(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_find_component", query="10k")
    assert r["ok"] is True and r["count"] == 2
    assert {c["reference"] for c in r["components"]} == {"R1", "R2"}
    r2 = _call(kicad_native_mcp, "kicad_find_component", query="C1")
    assert r2["count"] == 1 and r2["components"][0]["reference"] == "C1"


def test_kicad_list_documents(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    r = _call(kicad_native_mcp, "kicad_list_documents")
    assert r["ok"] is True and r["count"] == 2
    types = {d["type"] for d in r["documents"]}
    assert types == {"pcb", "schematic"}


def test_kicad_project_info_and_text_variables(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    pi = _call(kicad_native_mcp, "kicad_get_project_info")
    assert pi["ok"] is True and pi["project"]["name"] == "Demo"
    assert pi["project"]["net_classes"] == ["Default"]
    tv = _call(kicad_native_mcp, "kicad_get_text_variables")
    assert tv["ok"] is True and tv["text_variables"]["REV"] == "B"


def test_kicad_read_tool_reports_reason(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    from eda_agent.bridge.kicad_bridge import KiCadNotReachableError
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: (_ for _ in ()).throw(
        KiCadNotReachableError("no board open")))
    res = _call(kicad_native_mcp, "kicad_get_stackup")
    assert res["ok"] is False and "no board open" in res["reason"]


def test_kicad_export_builds_args_and_summarizes(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    captured = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        captured["cli"] = cli
        captured["args"] = args
        return {"returncode": 0, "stdout": "done", "stderr": ""}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    res = _call(kicad_native_mcp, "kicad_export_step")
    assert res["ok"] is True
    assert captured["args"][:3] == ["pcb", "export", "step"]
    assert "--output" in captured["args"]
    assert res["output"].endswith("Board.step")


def test_kicad_generic_pcb_export_file_and_dir(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    seen = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        seen["args"] = args
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    # File format -> single .cad file output.
    r = _call(kicad_native_mcp, "kicad_export_pcb", fmt="gencad")
    assert r["ok"] is True and seen["args"][:3] == ["pcb", "export", "gencad"]
    assert seen["args"][-1].endswith(".kicad_pcb")
    assert any(a.endswith("Board.cad") for a in seen["args"])
    # Dir format -> --output is the directory.
    r = _call(kicad_native_mcp, "kicad_export_pcb", fmt="odb")
    assert r["ok"] is True and "--force" not in seen["args"]


def test_kicad_run_spice_no_ngspice(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = _call(kicad_native_mcp, "kicad_run_spice")
    assert r["ok"] is False and "ngspice" in r["reason"]


def test_kicad_run_spice_runs(monkeypatch, kicad_native_mcp, tmp_path):
    import os
    import shutil

    import eda_agent.tools.kicad as k
    calls = []

    async def fake_run_cli(cli, args, timeout=600.0, cwd=None):
        calls.append((cli, args, cwd))
        if cli.endswith("ngspice.exe"):
            # ngspice starts in the folder that holds the netlist, and that
            # folder holds nothing else, so no .spiceinit was waiting there.
            assert cwd and os.path.dirname(args[-1]) == cwd
            assert os.listdir(cwd) == ["deck.cir"]
        else:
            out = args[args.index("--output") + 1]
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("t\nR1 1 0 1k\nV1 1 0 1\n.op\n.end\n")
        return {"returncode": 0, "stdout": "sim ok", "stderr": ""}

    class _SchBridge(_ReadBridge):
        def sch_file_path(self):
            f = tmp_path / "D.kicad_sch"
            f.write_text("(kicad_sch)")
            return str(f)

    monkeypatch.setattr(shutil, "which", lambda name: "C:/ngspice/ngspice.exe")
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _SchBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    r = _call(kicad_native_mcp, "kicad_run_spice")
    assert r["ok"] is True and "sim ok" in r["output"]
    # Two runs: export the spice netlist, then ngspice batch.
    assert any("spice" in a for _, a, _ in calls)
    sim = [cwd for c, _, cwd in calls if c.endswith("ngspice.exe")]
    assert sim and not os.path.exists(sim[0]), "the run folder outlived the run"
    _call(kicad_native_mcp, "kicad_run_spice")
    again = [cwd for c, _, cwd in calls if c.endswith("ngspice.exe")]
    assert again[0] != again[1], "two runs shared a folder"


def test_kicad_cli_escape_hatch(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    seen = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        seen["args"] = args
        return {"returncode": 0, "stdout": "9.0.0", "stderr": ""}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    r = _call(kicad_native_mcp, "kicad_cli",
              args=["version", "--format", "json"])
    assert r["ok"] is True and r["returncode"] == 0
    assert seen["args"] == ["version", "--format", "json"]


def test_kicad_run_jobset(monkeypatch, kicad_native_mcp, tmp_path):
    import eda_agent.tools.kicad as k
    seen = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        seen["args"] = args
        return {"returncode": 0, "stdout": "ran 3 jobs", "stderr": ""}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    jobset = tmp_path / "outputs.kicad_jobset"
    jobset.write_text("{}")
    r = _call(kicad_native_mcp, "kicad_run_jobset", jobset_path=str(jobset))
    assert r["ok"] is True and "3 jobs" in r["log"]
    assert seen["args"][:2] == ["jobset", "run"]
    assert seen["args"][-1].endswith(".kicad_pro")
    miss = _call(kicad_native_mcp, "kicad_run_jobset",
                 jobset_path="C:/nope.kicad_jobset")
    assert miss["ok"] is False and "not found" in miss["reason"]


def test_kicad_generic_sch_export(monkeypatch, kicad_native_mcp, tmp_path):
    import eda_agent.tools.kicad as k
    seen = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        seen["args"] = args
        return {"returncode": 0, "stdout": "", "stderr": ""}

    class _SchBridge(_ReadBridge):
        def sch_file_path(self):
            f = tmp_path / "Design.kicad_sch"
            f.write_text("(kicad_sch)")
            return str(f)

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _SchBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    r = _call(kicad_native_mcp, "kicad_export_sch", fmt="python-bom")
    assert r["ok"] is True and seen["args"][:3] == ["sch", "export", "python-bom"]
    assert any(a.endswith("Design.xml") for a in seen["args"])


def test_kicad_upgrade_board_builds_args(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    seen = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        seen["args"] = args
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    r = _call(kicad_native_mcp, "kicad_upgrade_board")
    assert r["ok"] is True
    assert seen["args"][:2] == ["pcb", "upgrade"]
    assert "--force" in seen["args"]
    assert seen["args"][-1].endswith(".kicad_pcb")


def test_kicad_export_reports_failure(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k

    async def fake_run_cli(cli, args, timeout=600.0):
        return {"returncode": 1, "stdout": "", "stderr": "boom"}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    res = _call(kicad_native_mcp, "kicad_export_gerbers")
    assert res["ok"] is False and "boom" in res["reason"]


def test_find_footprint_file(tmp_path):
    from eda_agent.core.kicad_footprint import (find_footprint_file,
                                                standard_footprint_dirs)
    pretty = tmp_path / "Resistor_SMD.pretty"
    pretty.mkdir()
    (pretty / "R_0402.kicad_mod").write_text("(footprint)")
    dirs = [str(tmp_path)]
    assert find_footprint_file("Resistor_SMD:R_0402", dirs) is not None
    assert find_footprint_file("R_0402", dirs) is not None       # bare name
    assert find_footprint_file("Resistor_SMD:Nope", dirs) is None
    # env-var discovery
    import os
    monkey = os.environ.get("KICAD_FOOTPRINT_DIR")
    os.environ["KICAD_FOOTPRINT_DIR"] = str(tmp_path)
    try:
        assert str(tmp_path) in standard_footprint_dirs()
    finally:
        if monkey is None:
            del os.environ["KICAD_FOOTPRINT_DIR"]
        else:
            os.environ["KICAD_FOOTPRINT_DIR"] = monkey


def test_build_footprint_sexpr():
    from eda_agent.core.kicad_footprint import build_footprint
    pads = [{"number": "1", "x_mm": -0.5, "y_mm": 0, "w_mm": 0.6, "h_mm": 0.6,
             "shape": "roundrect", "type": "smd"},
            {"number": "2", "x_mm": 0.5, "y_mm": 0, "w_mm": 0.6, "h_mm": 0.6,
             "type": "thru_hole", "shape": "circle", "drill_mm": 0.3}]
    sexpr = build_footprint("TEST_R", pads, descr="test", tags="resistor")
    assert sexpr.startswith('(footprint "TEST_R"')
    assert sexpr.rstrip().endswith(")")
    assert '(pad "1" smd roundrect' in sexpr
    assert '(pad "2" thru_hole circle' in sexpr
    assert '(drill 0.3)' in sexpr
    assert sexpr.count("(pad ") == 2


def test_kicad_create_footprint_writes_file(monkeypatch, kicad_native_mcp, tmp_path):
    import eda_agent.tools.kicad as k
    lib = str(tmp_path / "test.pretty")
    pads = [{"number": "1", "x_mm": 0, "y_mm": 0, "w_mm": 1, "h_mm": 1}]
    # Dict form so the tool's own "name" arg doesn't clash with _call's.
    r = _payload(asyncio.run(kicad_native_mcp.call_tool(
        "kicad_create_footprint",
        {"library_path": lib, "name": "MyFP", "pads": pads})))
    assert r["ok"] is True and r["pad_count"] == 1
    import os
    assert os.path.exists(os.path.join(lib, "MyFP.kicad_mod"))
    bad = _payload(asyncio.run(kicad_native_mcp.call_tool(
        "kicad_create_footprint",
        {"library_path": lib, "name": "", "pads": []})))
    assert bad["ok"] is False


def test_build_schematic_structure():
    from eda_agent.core.kicad_schematic import build_schematic
    parts = [{"refdes": "R1", "value": "10k"}, {"refdes": "R2", "value": "4k7"},
             {"refdes": "U1", "value": "MCU"}]
    nets = [{"name": "VIN", "nodes": [{"refdes": "R1", "pin": "1"},
                                      {"refdes": "U1", "pin": "1"}]},
            {"name": "GND", "nodes": [{"refdes": "R2", "pin": "2"},
                                      {"refdes": "U1", "pin": "2"}]}]
    sch = build_schematic(parts, nets)
    assert sch.startswith("(kicad_sch")
    assert sch.rstrip().endswith(")")
    assert "(lib_symbols" in sch and "(sheet_instances" in sch
    # A placed instance per part, a global label per net.
    assert sch.count("(lib_id ") == 3
    assert '(global_label "VIN"' in sch and '(global_label "GND"' in sch
    assert '(property "Reference" "R1"' in sch
    # Sub-symbols must drop the library prefix (the bug this guards against).
    assert '"eda:BOX' in sch and '(symbol "BOX' in sch


def test_kicad_generate_schematic_writes_file(monkeypatch, kicad_native_mcp, tmp_path):
    out = str(tmp_path / "gen.kicad_sch")
    plan = {"spec": "t", "summary": "t", "sheets": [{"name": "main"}],
            "zones": [], "parts": [
                {"refdes": "R1", "lib_ref": "R", "value": "10k",
                 "status": "existing"},
                {"refdes": "R2", "lib_ref": "R", "value": "1k",
                 "status": "existing"}],
            "nets": [{"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                                            {"refdes": "R2", "pin": "1"}]},
                     {"name": "B", "pins": [{"refdes": "R1", "pin": "2"},
                                            {"refdes": "R2", "pin": "2"}]}],
            "bom": [], "design_rules": [], "open_questions": []}
    r = _call(kicad_native_mcp, "kicad_generate_schematic",
              plan_json=plan, output_path=out)
    assert r["ok"] is True and r["part_count"] == 2 and r["net_count"] == 2
    import os
    assert os.path.exists(out)
    with open(out) as fh:
        assert fh.read().startswith("(kicad_sch")


def test_order_by_connectivity():
    from eda_agent.core.placement_order import order_by_connectivity
    parts = [{"refdes": "R1"}, {"refdes": "C1"}, {"refdes": "R2"},
             {"refdes": "U1"}]
    # U1 and R2 share a net; they should end up adjacent in the ordering.
    nets = [{"name": "N", "nodes": [{"refdes": "U1", "pin": "1"},
                                    {"refdes": "R2", "pin": "1"}]}]
    order = [p["refdes"] for p in order_by_connectivity(parts, nets)]
    assert set(order) == {"R1", "C1", "R2", "U1"}   # all parts kept
    assert abs(order.index("U1") - order.index("R2")) == 1


def test_build_pcb_structure():
    from eda_agent.core.kicad_pcb import build_pcb
    parts = [{"refdes": "R1", "value": "10k"}, {"refdes": "R2", "value": "1k"}]
    nets = [{"name": "A", "nodes": [{"refdes": "R1", "pin": "1"},
                                    {"refdes": "R2", "pin": "1"}]},
            {"name": "B", "nodes": [{"refdes": "R1", "pin": "2"},
                                    {"refdes": "R2", "pin": "2"}]}]
    pcb = build_pcb(parts, nets)
    assert pcb.startswith("(kicad_pcb")
    assert pcb.rstrip().endswith(")")
    assert '(layers' in pcb and '(net 0 "")' in pcb
    assert '(net 1 "A")' in pcb and '(net 2 "B")' in pcb
    assert pcb.count("(footprint ") == 2
    assert '(property "Reference" "R1"' in pcb
    assert '(net 1 "A")' in pcb  # pad assigned to net


def test_build_pcb_uses_real_footprint_with_box_fallback():
    from eda_agent.core.kicad_pcb import build_pcb
    from eda_agent.core.kicad_footprint import build_footprint
    real = build_footprint("R_TEST", [
        {"number": "1", "x_mm": -0.5, "y_mm": 0, "w_mm": 0.6, "h_mm": 0.6},
        {"number": "2", "x_mm": 0.5, "y_mm": 0, "w_mm": 0.6, "h_mm": 0.6}])
    parts = [{"refdes": "R1", "value": "10k"}, {"refdes": "U1", "value": "IC"}]
    nets = [{"name": "VIN", "nodes": [{"refdes": "R1", "pin": "1"},
                                      {"refdes": "U1", "pin": "1"}]},
            {"name": "GND", "nodes": [{"refdes": "R1", "pin": "2"},
                                      {"refdes": "U1", "pin": "2"}]}]
    pcb = build_pcb(parts, nets, mod_texts={"R1": real})
    assert '"R_TEST"' in pcb                    # R1 uses the real footprint
    assert "eda:BOX" in pcb                     # U1 falls back to a box
    assert '(net 1 "VIN")' in pcb               # pad net injected
    assert '(property "Reference" "R1"' in pcb


def test_kicad_generate_pcb_writes_file(monkeypatch, kicad_native_mcp, tmp_path):
    out = str(tmp_path / "gen.kicad_pcb")
    plan = {"spec": "t", "summary": "t", "sheets": [{"name": "main"}],
            "zones": [], "parts": [
                {"refdes": "R1", "lib_ref": "R", "value": "10k",
                 "status": "existing"},
                {"refdes": "R2", "lib_ref": "R", "value": "1k",
                 "status": "existing"}],
            "nets": [{"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                                            {"refdes": "R2", "pin": "1"}]},
                     {"name": "B", "pins": [{"refdes": "R1", "pin": "2"},
                                            {"refdes": "R2", "pin": "2"}]}],
            "bom": [], "design_rules": [], "open_questions": []}
    r = _call(kicad_native_mcp, "kicad_generate_pcb",
              plan_json=plan, output_path=out)
    assert r["ok"] is True and r["part_count"] == 2
    import os
    assert os.path.exists(out)
    with open(out) as fh:
        assert fh.read().startswith("(kicad_pcb")


def test_build_schematic_uses_real_symbol_with_box_fallback():
    from eda_agent.core.kicad_schematic import build_schematic
    from eda_agent.core.kicad_symbol import build_symbol
    real = build_symbol("Device:R", [
        {"number": "1", "name": "~", "x_mm": 0, "y_mm": 3.81, "angle": 270},
        {"number": "2", "name": "~", "x_mm": 0, "y_mm": -3.81, "angle": 90}])
    parts = [{"refdes": "R1", "value": "10k", "lib_ref": "Device:R"},
             {"refdes": "U1", "value": "IC", "lib_ref": "MCU"}]
    nets = [{"name": "A", "nodes": [{"refdes": "R1", "pin": "1"},
                                    {"refdes": "U1", "pin": "1"}]}]
    sch = build_schematic(parts, nets, symbols={"Device:R": real},
                          part_symbol={"R1": "Device:R"})
    assert '(lib_id "Device:R")' in sch        # R1 uses the real symbol
    assert "eda:BOX" in sch                     # U1 falls back to a box
    assert '(property "Reference" "R1"' in sch


def test_build_symbol_and_insert():
    from eda_agent.core.kicad_symbol import build_symbol_lib, insert_symbol
    pins = [{"number": "1", "name": "IN", "x_mm": -7.62, "y_mm": 0,
             "angle": 0, "type": "input"},
            {"number": "2", "name": "OUT", "x_mm": 7.62, "y_mm": 0,
             "angle": 180, "type": "output"}]
    lib = build_symbol_lib("MY_IC", pins, reference="U")
    assert lib.startswith("(kicad_symbol_lib")
    assert '(symbol "MY_IC"' in lib and '(symbol "MY_IC_1_1"' in lib
    assert lib.count("(pin ") == 2
    assert '(pin input line' in lib and '(pin output line' in lib
    assert lib.rstrip().endswith(")")
    # Insert a second symbol into the existing library.
    lib2 = insert_symbol(lib, "SECOND", pins)
    assert '(symbol "MY_IC"' in lib2 and '(symbol "SECOND"' in lib2
    assert lib2.rstrip().endswith(")")


def test_kicad_create_symbol_writes_file(monkeypatch, kicad_native_mcp, tmp_path):
    lib = str(tmp_path / "mylib.kicad_sym")
    pins = [{"number": "1", "name": "A", "x_mm": -5, "y_mm": 0, "type": "passive"}]
    r = _payload(asyncio.run(kicad_native_mcp.call_tool(
        "kicad_create_symbol",
        {"library_path": lib, "name": "S1", "pins": pins})))
    assert r["ok"] is True and r["pin_count"] == 1
    import os
    assert os.path.exists(lib)
    # Second call inserts into the same file.
    r2 = _payload(asyncio.run(kicad_native_mcp.call_tool(
        "kicad_create_symbol",
        {"library_path": lib, "name": "S2", "pins": pins})))
    assert r2["ok"] is True
    with open(lib) as fh:
        content = fh.read()
    assert '(symbol "S1"' in content and '(symbol "S2"' in content


def test_kicad_library_export_builds_args(monkeypatch, kicad_native_mcp, tmp_path):
    import eda_agent.tools.kicad as k
    captured = {}

    async def fake_run_cli(cli, args, timeout=600.0):
        captured["args"] = args
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _ReadBridge())
    monkeypatch.setattr(k, "run_cli", fake_run_cli)
    lib = tmp_path / "Lib.pretty"
    lib.mkdir()
    res = _call(kicad_native_mcp, "kicad_export_footprint_svg",
                library_path=str(lib), footprint="R_0402")
    assert res["ok"] is True
    assert captured["args"][:3] == ["fp", "export", "svg"]
    assert "--footprint" in captured["args"] and str(lib) in captured["args"]


def test_kicad_library_export_missing_lib(kicad_native_mcp):
    res = _call(kicad_native_mcp, "kicad_export_footprint_svg",
                library_path="C:/definitely_not_here.pretty")
    assert res["ok"] is False and "not found" in res["reason"]


def test_kicadxml_netlist_parser(tmp_path):
    from eda_agent.core.kicad_netlist import parse_kicadxml_netlist
    xml = tmp_path / "net.xml"
    xml.write_text(
        "<export><components>"
        "<comp ref='R1'><value>10k</value><footprint>R_0402</footprint></comp>"
        "<comp ref='C1'><value>100n</value></comp>"
        "</components><nets>"
        "<net code='1' name='GND' class='Default'>"
        "<node ref='R1' pin='2'/><node ref='C1' pin='2'/></net>"
        "<net code='2' name='VCC' class='Power'>"
        "<node ref='R1' pin='1'/></net>"
        "</nets></export>")
    data = parse_kicadxml_netlist(str(xml))
    assert len(data["components"]) == 2
    r1 = next(c for c in data["components"] if c["reference"] == "R1")
    assert r1["value"] == "10k" and r1["footprint"] == "R_0402"
    gnd = next(n for n in data["nets"] if n["name"] == "GND")
    assert gnd["class"] == "Default" and len(gnd["nodes"]) == 2
    assert {n["reference"] for n in gnd["nodes"]} == {"R1", "C1"}


def test_compare_schematic_to_pcb():
    from eda_agent.core.kicad_netlist import compare_schematic_to_pcb
    sch = {"components": [{"reference": "R1"}, {"reference": "R2"},
                          {"reference": "U1"}],
           "nets": [{"name": "GND"}, {"name": "VCC"}, {"name": "SIG"}]}
    pcb_refs = {"R1", "R2", "C9"}          # C9 extra on PCB, U1 missing
    pcb_nets = {"GND", "VCC"}              # SIG missing on PCB
    out = compare_schematic_to_pcb(sch, pcb_refs, pcb_nets)
    assert out["in_sync"] is False
    assert out["components_only_in_schematic"] == ["U1"]
    assert out["components_only_in_pcb"] == ["C9"]
    assert out["nets_only_in_schematic"] == ["SIG"]
    codes = {(f["dimension"], f["severity"]) for f in out["findings"]}
    assert ("components", "warning") in codes


def test_bom_from_netlist_consolidates():
    from eda_agent.core.kicad_netlist import bom_from_netlist
    sch = {"components": [
        {"reference": "R1", "value": "10k", "footprint": "R_0402"},
        {"reference": "R2", "value": "10k", "footprint": "R_0402"},
        {"reference": "C1", "value": "100n", "footprint": "C_0402"},
    ]}
    bom = bom_from_netlist(sch)
    assert len(bom) == 2
    r = next(l for l in bom if l["value"] == "10k")
    assert r["quantity"] == 2 and r["references"] == ["R1", "R2"]


def test_snapshot_from_netlist_feeds_review():
    from eda_agent.core.kicad_netlist import snapshot_from_netlist
    from eda_agent.core.review_engine import review_snapshot
    sch = {"components": [{"reference": "R1", "value": "10k"},
                          {"reference": "R1", "value": "10k"}],  # dup ref
           "nets": [{"name": "A", "nodes": [{"reference": "R1", "pin": "1"}]}]}
    snap = snapshot_from_netlist(sch)
    assert snap.source == "kicad-sch"
    res = review_snapshot(snap)
    codes = {f["code"] for f in res["findings"]}
    assert "duplicate_reference" in codes       # engine runs on sch snapshot


def test_compare_in_sync():
    from eda_agent.core.kicad_netlist import compare_schematic_to_pcb
    sch = {"components": [{"reference": "R1"}], "nets": [{"name": "GND"}]}
    out = compare_schematic_to_pcb(sch, {"R1"}, {"GND"})
    assert out["in_sync"] is True and out["finding_count"] == 0


def test_erc_normalizer_flattens_sheets():
    from eda_agent.core.kicad_drc import _normalize_erc
    report = {"sheets": [
        {"path": "/", "violations": [
            {"type": "pin_not_connected", "severity": "error",
             "description": "Pin not connected", "items": [{}]}]},
        {"path": "/sub", "violations": [
            {"type": "label_dangling", "severity": "warning",
             "description": "Dangling label", "items": [{}, {}]}]},
    ]}
    out = _normalize_erc(report, "/tmp/Design.kicad_sch")
    assert out["violation_count"] == 2
    assert out["sheet_count"] == 2
    assert out["summary"] == {"error": 1, "warning": 1}
    assert out["schematic"] == "Design.kicad_sch"


def test_run_erc_dispatches(monkeypatch, eda_mcp):
    import eda_agent.tools.eda as e

    class _ErcBackend:
        name = "fake"
        async def run_erc(self):
            return {"ok": True, "source": "fake", "violation_count": 3}

    monkeypatch.setattr(e, "resolve_backend", lambda name=None: _ErcBackend())
    res = _call(eda_mcp, "run_erc")
    assert res["ok"] is True and res["violation_count"] == 3


# -- KiCad authoring (writes), mocked bridge --------------------------------

class _WriteBridge:
    def __init__(self):
        self.calls = []
    def move_component(self, ref, x, y, save=False):
        self.calls.append(("move", ref, x, y, save))
        return {"reference": ref, "x_mm": x, "y_mm": y, "saved": save}
    def rotate_component(self, ref, deg, save=False):
        self.calls.append(("rotate", ref, deg, save))
        return {"reference": ref, "orientation_deg": deg, "saved": save}
    def set_component_locked(self, ref, locked, save=False):
        self.calls.append(("lock", ref, locked, save))
        return {"reference": ref, "locked": locked, "saved": save}
    def delete_component(self, ref, save=False):
        self.calls.append(("delete", ref, save))
        return {"reference": ref, "deleted": True, "saved": save}
    def create_track(self, net, layer, x1, y1, x2, y2, w, save=False):
        self.calls.append(("track", net, layer, x1, y1, x2, y2, w, save))
        return {"net": net, "layer": layer, "width_mm": w, "created": 1,
                "saved": save}
    def create_via(self, net, x, y, dia, drill, save=False):
        self.calls.append(("via", net, x, y, dia, drill, save))
        return {"net": net, "x_mm": x, "y_mm": y, "diameter_mm": dia,
                "drill_mm": drill, "created": 1, "saved": save}
    def run_action(self, action):
        self.calls.append(("action", action))
        return {"action": action, "status": "AS_OK"}
    def create_text(self, text, x, y, layer="F.Silkscreen", save=False):
        self.calls.append(("text", text, x, y, layer, save))
        return {"text": text, "x_mm": x, "y_mm": y, "layer": layer,
                "created": 1, "saved": save}
    def create_line(self, x1, y1, x2, y2, layer="F.Silkscreen", w=0.15, save=False):
        self.calls.append(("line", x1, y1, x2, y2, layer, w, save))
        return {"layer": layer, "width_mm": w, "created": 1, "saved": save}
    def create_circle(self, cx, cy, rad, layer="F.Silkscreen", w=0.15, save=False):
        self.calls.append(("circle", cx, cy, rad, layer, w, save))
        return {"layer": layer, "radius_mm": rad, "created": 1, "saved": save}
    def create_rectangle(self, x1, y1, x2, y2, layer="F.Silkscreen", w=0.15, save=False):
        self.calls.append(("rect", x1, y1, x2, y2, layer, w, save))
        return {"layer": layer, "created": 1, "saved": save}
    def create_arc(self, x1, y1, mx, my, x2, y2, layer="F.Silkscreen", w=0.15, save=False):
        self.calls.append(("arc", x1, y1, mx, my, x2, y2, layer, w, save))
        return {"layer": layer, "created": 1, "saved": save}
    def create_zone(self, net, points, layer="F.Cu", name="", priority=0, save=False):
        self.calls.append(("zone", net, len(points), layer, name, priority, save))
        return {"net": net, "layer": layer, "name": name,
                "point_count": len(points), "created": 1, "saved": save}
    def set_text_variable(self, key, value, save=False):
        self.calls.append(("textvar", key, value, save))
        return {"key": key, "value": value, "saved": save}


def test_kicad_authoring_tools_call_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)

    r = _call(kicad_native_mcp, "kicad_move_component",
              reference="R4", x_mm=10.0, y_mm=20.0)
    assert r["ok"] is True and r["x_mm"] == 10.0 and r["saved"] is False
    r = _call(kicad_native_mcp, "kicad_rotate_component",
              reference="U1", degrees=90.0, save=True)
    assert r["ok"] is True and r["orientation_deg"] == 90.0 and r["saved"] is True
    r = _call(kicad_native_mcp, "kicad_delete_component", reference="C9")
    assert r["ok"] is True and r["deleted"] is True
    assert ("move", "R4", 10.0, 20.0, False) in bridge.calls
    assert ("rotate", "U1", 90.0, True) in bridge.calls
    assert ("delete", "C9", False) in bridge.calls


def test_kicad_add_track_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_add_track", net="GND", layer="F.Cu",
              x1_mm=0.0, y1_mm=0.0, x2_mm=5.0, y2_mm=0.0, width_mm=0.25)
    assert r["ok"] is True and r["created"] == 1 and r["net"] == "GND"
    assert ("track", "GND", "F.Cu", 0.0, 0.0, 5.0, 0.0, 0.25, False) in bridge.calls


def test_kicad_add_via_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_add_via", net="GND", x_mm=10.0,
              y_mm=10.0, diameter_mm=0.6, drill_mm=0.3)
    assert r["ok"] is True and r["created"] == 1
    assert ("via", "GND", 10.0, 10.0, 0.6, 0.3, False) in bridge.calls


def test_kicad_add_text_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_add_text", text="REV B", x_mm=5.0,
              y_mm=5.0)
    assert r["ok"] is True and r["created"] == 1 and r["layer"] == "F.Silkscreen"
    assert ("text", "REV B", 5.0, 5.0, "F.Silkscreen", False) in bridge.calls


def test_kicad_add_line_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_add_line", x1_mm=0.0, y1_mm=0.0,
              x2_mm=10.0, y2_mm=0.0, layer="Edge.Cuts", width_mm=0.1)
    assert r["ok"] is True and r["created"] == 1
    assert ("line", 0.0, 0.0, 10.0, 0.0, "Edge.Cuts", 0.1, False) in bridge.calls


def test_kicad_add_circle_and_rectangle(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    c = _call(kicad_native_mcp, "kicad_add_circle", cx_mm=10.0, cy_mm=10.0,
              radius_mm=3.0)
    assert c["ok"] is True and c["radius_mm"] == 3.0
    r = _call(kicad_native_mcp, "kicad_add_rectangle", x1_mm=0.0, y1_mm=0.0,
              x2_mm=20.0, y2_mm=10.0, layer="Edge.Cuts")
    assert r["ok"] is True
    assert ("circle", 10.0, 10.0, 3.0, "F.Silkscreen", 0.15, False) in bridge.calls
    assert ("rect", 0.0, 0.0, 20.0, 10.0, "Edge.Cuts", 0.15, False) in bridge.calls


def test_kicad_add_arc_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_add_arc", x1_mm=0.0, y1_mm=0.0,
              mid_x_mm=5.0, mid_y_mm=5.0, x2_mm=10.0, y2_mm=0.0)
    assert r["ok"] is True and r["created"] == 1
    assert ("arc", 0.0, 0.0, 5.0, 5.0, 10.0, 0.0, "F.Silkscreen", 0.15,
            False) in bridge.calls


def test_kicad_add_zone_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
    # Call with a dict so the zone's own "name" arg doesn't clash with _call's.
    r = _payload(asyncio.run(kicad_native_mcp.call_tool(
        "kicad_add_zone", {"net": "GND", "points": pts, "layer": "F.Cu",
                           "name": "GNDpour"})))
    assert r["ok"] is True and r["point_count"] == 4 and r["created"] == 1
    assert ("zone", "GND", 4, "F.Cu", "GNDpour", 0, False) in bridge.calls


def test_kicad_set_text_variable_calls_bridge(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_set_text_variable", key="REV",
              value="B")
    assert r["ok"] is True and r["key"] == "REV" and r["value"] == "B"
    assert ("textvar", "REV", "B", False) in bridge.calls


def test_kicad_run_action(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    bridge = _WriteBridge()
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: bridge)
    r = _call(kicad_native_mcp, "kicad_run_action",
              action="pcbnew.Control.zoomFitScreen")
    assert r["ok"] is True and r["status"] == "AS_OK"
    assert ("action", "pcbnew.Control.zoomFitScreen") in bridge.calls


def test_kicad_authoring_reports_reason(monkeypatch, kicad_native_mcp):
    import eda_agent.tools.kicad as k
    from eda_agent.bridge.kicad_bridge import KiCadNotReachableError

    class _Bad:
        def move_component(self, *a, **k):
            raise KiCadNotReachableError("no footprint with reference 'ZZ9'")
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: _Bad())
    r = _call(kicad_native_mcp, "kicad_move_component",
              reference="ZZ9", x_mm=0.0, y_mm=0.0)
    assert r["ok"] is False and "ZZ9" in r["reason"]


# -- KiCad connection check -------------------------------------------------

def test_kicad_ping_reports_reason_when_unreachable(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_kicad_tools
    import eda_agent.tools.kicad as k
    from eda_agent.bridge.kicad_bridge import KiCadNotReachableError

    m = FastMCP("test")
    register_kicad_tools(m)
    monkeypatch.setattr(k, "get_kicad_bridge", lambda: (_ for _ in ()).throw(
        KiCadNotReachableError("API server is off")))
    res = _call(m, "kicad_ping")
    assert res["ok"] is False
    assert "API server is off" in res["reason"]
