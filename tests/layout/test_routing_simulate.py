# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Simulations a routing finding asks for, built from the board's copper:
ngspice decks from the parasitics measured on it, and an openEMS model of
its geometry on the board's stack."""

from __future__ import annotations

import json
import math

import pytest

from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Region, Rule, Track
from eda_agent.layout.route import brief as B
from eda_agent.layout.route import simulate as S
from eda_agent.sim.ngspice import find_ngspice

SMD = [PadCopper("TopLayer", "rect", 20, 20)]
STACK = [{"kind": "copper", "thickness": 1.4}, {"kind": "dielectric", "thickness": 4.0, "er": 4.2},
         {"kind": "copper", "thickness": 1.4}, {"kind": "dielectric", "thickness": 50.0, "er": 4.5},
         {"kind": "copper", "thickness": 1.4}]
needs_ngspice = pytest.mark.skipif(find_ngspice() is None, reason="ngspice not installed")


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    from eda_agent import config
    monkeypatch.setattr(config.get_config(), "workspace_dir", tmp_path)


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (800, 0), (800, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("Mid", "plane", 1, "GND"),
                            Layer("BottomLayer", "signal", 2)], rules=rules)
    b.meta["stack"] = STACK
    b.pads = [Pad("C1", "1", 100, 200, net="VIN", copper=list(SMD)),
              Pad("C1", "2", 100, 140, net="GND", copper=list(SMD)),
              Pad("U1", "1", 300, 200, net="VIN", copper=list(SMD)),
              Pad("U1", "3", 300, 140, net="GND", copper=list(SMD))]
    b.tracks = [Track("TopLayer", 100, 200, 300, 200, 10, net="VIN"),
                Track("TopLayer", 100, 140, 300, 140, 10, net="GND")]
    return b


HOT = {"name": "input", "kind": "hot_loop", "members": ["C1.1", "U1.1", "U1.3", "C1.2"],
       "max_area": 5000, "source": "regulator datasheet"}


def test_the_parasitic_formulas_match_their_sources():
    # H. Johnson's worked via: 0.063 in long, 0.016 in drill, about 1.2 nH.
    assert S.via_nh(63, 16) == pytest.approx(1.20, abs=0.02)
    # 1 inch of 1 oz copper 10 mil wide: about 49 milliohm.
    assert S.track_ohm(1000, 10, 1.4) == pytest.approx(0.0484, rel=0.02)
    assert S.track_nh(1000, 10, 4) > S.track_nh(1000, 10, 2) > 0


@needs_ngspice
def test_a_loop_rings_to_its_inductance_times_the_edge():
    b = _board()
    brief = B.Brief.from_list([], loops=[HOT])
    prep = S.loop_deck(b, brief, "input", {"i": 2.0, "rise_ns": 5.0, "source": "datasheet"})
    L, R = prep["inputs"]["inductance_nh"], prep["inputs"]["resistance_ohm"]
    assert prep["inputs"]["area_sq_mils"] == pytest.approx(12000, rel=0.01) and L > 0
    res = S.run_ngspice(prep, b, "loop", "input")
    assert res["ok"], res
    # L di/dt on the rising edge, plus the copper's IR.
    assert res["peak_v"] == pytest.approx(L * 1e-9 * 2.0 / 5e-9 + R * 2.0, rel=0.05)
    assert json.loads(open(res["result_file"]).read())["peak_v"] == res["peak_v"]


@needs_ngspice
def test_a_power_pin_sees_its_capacitor_behind_the_mounting():
    b = _board()
    brief = B.Brief.from_list([], pdn=[{"name": "U1", "part": "U1", "rails": ["VIN"],
                                        "max_distance": 300, "source": "datasheet"}])
    models = {"C1": {"c_uf": 0.1, "esr_ohm": 0.02, "esl_nh": 0.5, "source": "C1's datasheet"}}
    prep = S.pdn_deck(b, brief, "U1", models, {"f_start": 1e5, "f_stop": 1e9})
    mount = prep["inputs"]["C1:VIN"]
    assert mount["mount_track_mils"] == 200.0 and mount["along_copper"]
    res = S.run_ngspice(prep, b, "pdn", "U1", target=0.1)
    assert res["ok"], res
    z = res["rails"]["VIN"]
    # The series resonance bottoms out at the ESR; at 1 GHz it is the
    # mounting and the ESL.
    assert z["min_ohm"] == pytest.approx(0.02, rel=0.1)
    assert z["max_ohm"] == pytest.approx(2 * math.pi * 1e9 * mount["total_nh"] * 1e-9, rel=0.05)
    assert z["over_target"]["to_hz"] == pytest.approx(1e9, rel=0.01)
    assert "give each capacitor" in S.pdn_deck(b, brief, "U1", {}, {})["error"]


def test_an_openems_model_is_the_nets_copper_on_the_stack(monkeypatch):
    b = _board()
    b.regions = [Region("BottomLayer", [(0, 0), (800, 0), (800, 400), (0, 400)], net="GND",
                        kind="pour")]
    brief = B.Brief.from_list([{"nets": ["VIN"], "reference": "GND", "impedance": 50,
                                "source": "x"}])
    model = S.openems_model(b, brief, "impedance", "VIN")
    assert [p["pad"] for p in model["ports"]] in (["C1.1", "U1.1"], ["U1.1", "C1.1"])
    # The plane layer and the bottom pour, at the top of their copper.
    assert sorted(model["sheets"]) == pytest.approx([1.4, 52.8])
    assert model["polygons"][0]["z"] == pytest.approx(58.2)
    assert [d["er"] for d in model["dielectrics"]] == [4.2, 4.5]
    monkeypatch.setattr(S, "openems_available", lambda: None)
    res = S.run_openems(b, model)
    assert not res["ok"] and "install_offer" in res
    script = open(res["script"], encoding="utf-8").read()
    compile(script, res["script"], "exec")
    assert "AddLumpedPort" in script and "AddPolygon" in script
    assert json.loads(open(res["model"]).read())["net"] == "VIN"
    xt = S.openems_model(b, brief, "crosstalk", "VIN", "GND")
    assert [p["net"] for p in xt["ports"]] == ["VIN", "VIN", "GND", "GND"]
    assert "no routed track" in S.openems_model(b, brief, "impedance", "NOPE")["error"]
