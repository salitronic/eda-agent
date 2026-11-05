# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each calculator against its author's own number, never its own output.

A test that only recomputes the formula passes just as well with the
formula wrong from the first commit. These pin each one to a value
published independently of this code:

- fusing current: Brooks and Adam's worked example (2015): 103 sq mil
  for 1 s is about 19 A, and their Eq. 6 coefficient 0.0346 at 20 C;
- via inductance: Johnson's predicted and measured values ('Via
  Inductance II'): h 0.040 in, r 0.005 in, s 0.017 and 0.12 in;
- spiral inductance: Mohan et al. 1999, Table IV, where the paper
  lists each measured inductor and its own error for each expression;
- wire gauge: the AWG table (Wikipedia's, from the ASTM law);
- the exact ones: identities with round numbers.
"""
from __future__ import annotations

import math

import pytest

from eda_agent import cli
from eda_agent.design import calculators as c


def test_fusing_current_matches_brooks_and_adams_worked_example():
    got = c.fusing_current(1.0, area_sq_mils=103)
    assert got["amps"] == pytest.approx(19, abs=0.3)
    assert got["status"] == "published" and "lower bound" in got["notes"][0]


def test_fusing_current_reduces_to_their_eq_6_at_20_c():
    """t = 0.0346 (A / I)^2, A in square mils, Ta 20 C, Tm 1083 C."""
    got = c.fusing_current(1.0, area_sq_mils=100.0)
    assert 1.0 * (got["amps"] / 100.0) ** 2 == pytest.approx(0.0346, abs=0.0001)


def test_fusing_current_says_when_it_stops_applying():
    assert c.fusing_current(30, area_sq_mils=100)["valid"] is False
    assert len(c.fusing_current(3, area_sq_mils=100)["notes"]) == 2


@pytest.mark.parametrize("s_in, predicted, measured", [(0.017, 0.49, 0.46), (0.12, 1.29, 1.41)])
def test_via_inductance_matches_johnsons_table(s_in, predicted, measured):
    got = c.via_inductance(40, 10, s_in * 1000)["inductance_nh"]
    assert got == pytest.approx(predicted, abs=0.01)
    assert got == pytest.approx(measured, rel=0.10)


#: Mohan et al. 1999, Table IV: n, d_out (um), w, s, L measured (nH),
#: the paper's error for the modified Wheeler and the current-sheet forms.
_TABLE_IV = [
    (2.75, 344, 29.7, 1.9, 3.20, 5.2, 6.4),
    (3.75, 292, 13.0, 1.9, 6.00, -1.2, -0.7),
    (6.50, 217, 5.4, 1.9, 12.50, 1.4, 2.3),
    (2.75, 279, 18.3, 1.9, 3.10, 2.1, 2.8),
    (4.75, 206, 7.8, 1.9, 6.10, -0.7, 0.3),
    (7.50, 166, 3.2, 1.9, 12.40, 2.2, 3.2),
    (12.00, 180, 3.2, 2.1, 20.50, -1.0, -0.4),
    (7.00, 300, 13.0, 7.0, 8.00, 5.6, 4.0),
]


@pytest.mark.parametrize("n, dout, w, s, l_meas, e_whe, e_gmd", _TABLE_IV)
def test_spiral_inductance_errs_as_the_paper_says(n, dout, w, s, l_meas, e_whe, e_gmd):
    """Our error against the measurement is the paper's own error, to
    within the rounding of its table and its d_in for part turns."""
    mil = 1 / 25.4
    got = c.spiral_inductance(n, dout * mil, w * mil, s * mil)
    whe = (l_meas - got["modified_wheeler_nh"]) / got["modified_wheeler_nh"] * 100
    gmd = (l_meas - got["current_sheet_nh"]) / got["current_sheet_nh"] * 100
    assert whe == pytest.approx(e_whe, abs=0.7)
    assert gmd == pytest.approx(e_gmd, abs=0.7)


@pytest.mark.parametrize("awg, mm", [(24, 0.511), (-3, 11.684), (40, 0.0799), (10, 2.588), (0, 8.251)])
def test_wire_gauge_matches_the_awg_table(awg, mm):
    assert c.wire_gauge(awg)["diameter_mm"] == pytest.approx(mm, abs=0.001)


def test_wire_gauge_names_and_finds_the_nearest():
    assert c.wire_gauge(-3)["gauge"] == "0000" and c.wire_gauge(0)["gauge"] == "0"
    assert c.wire_gauge(diameter_mils=20.1)["awg"] == 24
    assert c.wire_gauge(24)["area_mm2"] == pytest.approx(0.205, abs=0.001)


def test_the_exact_ones():
    assert c.reactance(1e9, inductance_h=1e-9)["inductive_ohms"] == pytest.approx(2 * math.pi)
    assert c.reactance(1e6, capacitance_f=1e-9)["capacitive_ohms"] == pytest.approx(159.155, abs=0.001)
    assert c.resonance(1e-6, 1e-6)["frequency_hz"] == pytest.approx(159154.9, abs=0.1)
    assert c.wavelength(1e9)["wavelength_mm"] == pytest.approx(299.792458)
    assert c.wavelength(1e9, er_eff=4)["wavelength_mm"] == pytest.approx(299.792458 / 2)
    assert c.wavelength(1e9, length_mils=0.0)  # optional length
    assert c.pdn_target_impedance(1.0, 5.0, 10.0)["target_ohms"] == pytest.approx(0.005)
    for got in (c.reactance(1e6, 1e-9), c.wavelength(1e9)):
        assert got["status"] == "exact" and got["source"]


def test_padstack_uses_the_fabs_quoted_figures():
    got = c.padstack("JLCPCB", 4, 8)
    assert got["min_pad_mils"] == pytest.approx(8 + 2 * 0.05 / 0.0254, abs=1e-3)
    assert got["min_inner_clearance_mils"] == pytest.approx(8 + 2 * 0.2 / 0.0254, abs=1e-3)
    assert "Inner layer via hole to copper" in got["clearance"]["quote"]
    pth = c.padstack("JLCPCB", 4, 40, kind="pth")
    assert pth["min_pad_mils"] == pytest.approx(40 + 2 * 0.15 / 0.0254, abs=1e-3)
    assert pth["recommended_pad_mils"] == pytest.approx(40 + 2 * 0.20 / 0.0254, abs=1e-3)


def test_padstack_says_what_the_fab_does_not_publish():
    got = c.padstack("PCBWay", 4, 12, kind="pth")
    assert "pad_missing" in got and "min_pad_mils" not in got
    tiered = c.padstack("PCBWay", 6, 12, tier="advanced_normal")
    assert tiered["min_inner_clearance_mils"] == 12 + 2 * 8      # '6L >=8MIL'


def test_padstack_flags_a_hole_below_the_fabs_minimum():
    assert c.padstack("JLCPCB", 4, 4)["below_min_drill"]["mm"] == 0.15


def test_the_cli_reads_inputs_against_the_signature(capsys):
    assert cli.cmd_calc("fusing-current", ["seconds=1", "area_sq_mils=103"]) == 0
    assert "amps: 19.15" in capsys.readouterr().out
    assert cli.cmd_calc("wire-gauge", ["gauge=24"]) == 2
    assert "awg, diameter_mils" in capsys.readouterr().out
    assert cli.cmd_calc("wire-gauge", ["awg=x"]) == 2
    assert cli.cmd_calc("nonsense", []) == 2


def test_every_calculator_is_on_every_backend():
    """Left in register_calc_tools they would be missing on Altium."""
    import asyncio

    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    names = {"pcb_calc_" + k.replace("-", "_") for k in c.CALCULATORS}
    for backend in ("altium", "kicad", "easyeda"):
        registry = ToolRegistry()
        register_backend(registry, backend, "full")
        have = set(registry.hidden)
        assert not names - have, (backend, names - have)
        assert "pcb_calc" in registry
