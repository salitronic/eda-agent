# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Engineering calculators, each with where its answer comes from.

Every result carries ``status``:

- ``exact``: a definition or identity (reactance, resonance, wavelength,
  target impedance, the AWG diameter law). Wrong only if the inputs are.
- ``published``: a named author's closed form, cited, and tested here
  against that author's own worked number or measurement, not against
  this code's output.
- ``fab``: a fab house's published figure (fab_library), quoted.

and ``source``. A rule of thumb is never returned looking like either.

Left out on purpose, both for want of a source that can be checked:
conductor spacing from voltage (IPC-2221 Table 6-1 is not freely
available and its reproductions disagree) and via capacitance (Johnson
himself calls his book's formula a crude approximation).

Lengths are MILS at the API, as everywhere here; electrical values are SI
(henry, farad, hertz, volt, ampere) so a value is never off by a prefix.
Pure offline.
"""
from __future__ import annotations

import math
from typing import Optional

from ..units import MM_PER_MIL as _MM_PER_MIL
from ..units import OZ_TO_MILS
from . import fab_library

_C = 299_792_458.0                  # m/s, exact by the SI definition
_MU0 = 4e-7 * math.pi               # H/m (the pre-2019 exact value; the
#                                     revised SI differs in the 10th digit)


def _out(status: str, source: str, **values) -> dict:
    return {"ok": True, "status": status, "source": source, **values}


def _bad(reason: str) -> dict:
    return {"ok": False, "reason": reason}


# --- exact -----------------------------------------------------------------

def reactance(frequency_hz: float, inductance_h: float = 0.0,
              capacitance_f: float = 0.0) -> dict:
    """X_L = 2 pi f L and X_C = 1 / (2 pi f C), for whichever is given."""
    if frequency_hz <= 0 or (inductance_h <= 0 and capacitance_f <= 0):
        return _bad("give a positive frequency_hz and inductance_h or capacitance_f")
    w = 2 * math.pi * frequency_hz
    out = {}
    if inductance_h > 0:
        out["inductive_ohms"] = w * inductance_h
    if capacitance_f > 0:
        out["capacitive_ohms"] = 1.0 / (w * capacitance_f)
    return _out("exact", "X_L = 2 pi f L, X_C = 1/(2 pi f C)", **out)


def resonance(inductance_h: float, capacitance_f: float) -> dict:
    """f0 = 1 / (2 pi sqrt(LC)) and the characteristic impedance sqrt(L/C)."""
    if inductance_h <= 0 or capacitance_f <= 0:
        return _bad("inductance_h and capacitance_f must be positive")
    return _out("exact", "f0 = 1/(2 pi sqrt(LC)), Z0 = sqrt(L/C)",
                frequency_hz=1.0 / (2 * math.pi * math.sqrt(inductance_h * capacitance_f)),
                characteristic_ohms=math.sqrt(inductance_h / capacitance_f))


def wavelength(frequency_hz: float, er_eff: float = 1.0,
               length_mils: float = 0.0) -> dict:
    """lambda = c / (f sqrt(er_eff)), and a length's electrical length.

    ``er_eff`` is the effective permittivity the wave sees: the
    laminate's Dk for stripline (fully embedded); a microstrip's is lower
    and depends on its geometry, so it is the caller's to give."""
    if frequency_hz <= 0 or er_eff < 1:
        return _bad("frequency_hz must be positive and er_eff at least 1")
    lam_m = _C / (frequency_hz * math.sqrt(er_eff))
    lam_mils = lam_m * 1000 / _MM_PER_MIL
    out = {"wavelength_mm": lam_m * 1000, "wavelength_mils": lam_mils,
           "quarter_wave_mils": lam_mils / 4}
    if length_mils > 0:
        out["electrical_length_deg"] = 360.0 * length_mils / lam_mils
    return _out("exact", "lambda = c/(f sqrt(er_eff)), c = 299 792 458 m/s", **out)


def pdn_target_impedance(supply_v: float, ripple_pct: float,
                         transient_a: float) -> dict:
    """Z_target = allowed ripple voltage / transient current step.

    The definition of the target, nothing more: what share of the load
    current steps at once is the caller's to know from the device's
    datasheet, not assumed here."""
    if supply_v <= 0 or not 0 < ripple_pct < 100 or transient_a <= 0:
        return _bad("supply_v and transient_a must be positive, ripple_pct in (0, 100)")
    ripple_v = supply_v * ripple_pct / 100.0
    return _out("exact", "Z_target = (V x ripple) / delta_I",
                target_ohms=ripple_v / transient_a, ripple_v=ripple_v)


def wire_gauge(awg: Optional[int] = None, diameter_mils: float = 0.0) -> dict:
    """AWG n: d = 0.005 in x 92^((36 - n) / 39); 0000 is n = -3. With a
    diameter instead, the nearest gauge and its size."""
    if awg is None and diameter_mils <= 0:
        return _bad("give awg (-3 for 0000 to 40) or diameter_mils")
    if awg is None:
        n = round(36 - 39 * math.log(diameter_mils / 5.0) / math.log(92))
    else:
        n = int(awg)
    if not -3 <= n <= 40:
        return _bad("AWG runs from 0000 (-3) to 40 here")
    d_mils = 5.0 * 92 ** ((36 - n) / 39)
    d_mm = d_mils * _MM_PER_MIL
    name = "0" * (1 - n) if n <= 0 else str(n)
    return _out("exact", "ASTM B258 AWG law, d = 0.005 in x 92^((36-n)/39)",
                awg=n, gauge=name, diameter_mils=d_mils, diameter_mm=d_mm,
                area_mm2=math.pi * d_mm ** 2 / 4, area_kcmil=d_mils ** 2 / 1000)


# --- published -------------------------------------------------------------

def fusing_current(seconds: float, area_sq_mils: float = 0.0,
                   width_mils: float = 0.0, copper_oz: float = 1.0,
                   ambient_c: float = 20.0, melt_c: float = 1083.0) -> dict:
    """Onderdonk's current to bring copper from ambient to melting in
    ``seconds``, in the form Brooks and Adam give (2015, Eq. 5):

        t = (1 / 33.5) log10((Tm - Ta) / (234 + Ta) + 1) (A / I)^2,
        A in circular mils.

    Adiabatic: no heat leaves the copper, so it is a LOWER bound for a
    trace on a board. Brooks and Adam's simulations of traces on FR-4
    carry 2 to 6 times this (their Table 1), and they warn that cooling
    matters after one or two seconds."""
    if seconds <= 0:
        return _bad("seconds must be positive")
    if area_sq_mils <= 0:
        if width_mils <= 0:
            return _bad("give area_sq_mils, or width_mils and copper_oz")
        area_sq_mils = width_mils * copper_oz * OZ_TO_MILS
    a_cmil = area_sq_mils * 4.0 / math.pi
    k = math.log10((melt_c - ambient_c) / (234.0 + ambient_c) + 1.0) / 33.5
    amps = a_cmil * math.sqrt(k / seconds)
    notes = ["adiabatic lower bound: a trace on a board, losing heat to it, "
             "carries more (Brooks and Adam, Table 1: 2 to 6 times)"]
    if seconds > 1:
        notes.append("past 1 to 2 s cooling matters; beyond about 10 s the "
                     "equation does not apply at all")
    return _out("published",
                "Onderdonk, as Brooks & Adam, 'Fusing Currents in Traces', 2015, Eq. 5",
                amps=amps, area_sq_mils=area_sq_mils, valid=seconds <= 10, notes=notes)


def via_inductance(plane_spacing_mils: float, via_diameter_mils: float,
                   return_spacing_mils: float) -> dict:
    """The loop inductance a via adds crossing between two planes, with
    its return (a ground via, or a plane-to-plane capacitor) at
    ``return_spacing_mils`` centre to centre (Johnson, 'Via Inductance
    II', Signal Consulting newsletter v6-08):

        L = (mu / 2 pi) 2 h ln(s / r)

    Only the part inside the interplane space; pads and traces add their
    own. Johnson's measurements matched to within about 10 %."""
    for value in (plane_spacing_mils, via_diameter_mils, return_spacing_mils):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            return _bad("plane spacing, diameter and return spacing are numbers")
    h, s, r = plane_spacing_mils, return_spacing_mils, via_diameter_mils / 2.0
    if h <= 0 or r <= 0 or s <= r:
        return _bad("plane spacing and diameter must be positive, and the "
                    "return further away than the via's radius")
    henry = _MU0 / (2 * math.pi) * 2 * (h * _MM_PER_MIL / 1000) * math.log(s / r)
    return _out("published", "Johnson, 'Via Inductance II' (sigcon.com, v6-08)",
                inductance_nh=henry * 1e9)


#: Mohan, Hershenson, Boyd and Lee, "Simple Accurate Expressions for
#: Planar Spiral Inductances", IEEE JSSC 34(10), 1999, Tables I and II.
_WHEELER = {"square": (2.34, 2.75), "hexagonal": (2.33, 3.82),
            "octagonal": (2.25, 3.55)}
_SHEET = {"square": (1.27, 2.07, 0.18, 0.13), "hexagonal": (1.09, 2.23, 0.00, 0.17),
          "octagonal": (1.07, 2.29, 0.00, 0.19), "circle": (1.00, 2.46, 0.00, 0.20)}


def spiral_inductance(turns: float, outer_mils: float, width_mils: float = 0.0,
                      spacing_mils: float = 0.0, inner_mils: float = 0.0,
                      shape: str = "square") -> dict:
    """DC inductance of a planar spiral (Mohan et al. 1999): the modified
    Wheeler expression (1) and the current-sheet expression (2), each
    with the paper's coefficients.

    The inner diameter is given, or worked out as each full turn stepping
    in by width + spacing on every side: d_in = d_out - 2 n w - 2 (n-1) s.
    The paper's own accuracy: about 2 to 3 % typical, and current sheet
    to within 8 % for spacing up to three widths."""
    shape = shape.strip().lower()
    if shape not in _SHEET:
        return _bad(f"shape must be one of {sorted(_SHEET)}")
    if turns <= 0 or outer_mils <= 0:
        return _bad("turns and outer_mils must be positive")
    din = inner_mils or (outer_mils - 2 * turns * width_mils
                         - 2 * (turns - 1) * spacing_mils)
    if not 0 < din < outer_mils:
        return _bad(f"the inner diameter comes out at {din:g} mil; give "
                    f"inner_mils or check turns, width and spacing")
    davg = (outer_mils + din) / 2 * _MM_PER_MIL / 1000          # metres
    rho = (outer_mils - din) / (outer_mils + din)
    c1, c2, c3, c4 = _SHEET[shape]
    sheet = _MU0 * turns ** 2 * davg * c1 / 2 * (math.log(c2 / rho) + c3 * rho + c4 * rho ** 2)
    out = {"inner_mils": din, "fill_ratio": rho,
           "current_sheet_nh": sheet * 1e9}
    if shape in _WHEELER:
        k1, k2 = _WHEELER[shape]
        out["modified_wheeler_nh"] = k1 * _MU0 * turns ** 2 * davg / (1 + k2 * rho) * 1e9
    if spacing_mils > 3 * width_mils > 0:
        out["note"] = "spacing over three widths: the current-sheet form's 8 % bound no longer holds"
    return _out("published", "Mohan, Hershenson, Boyd, Lee, IEEE JSSC 34(10) 1999, (1) and (2)", **out)


# --- fab -------------------------------------------------------------------

def padstack(vendor: str, copper_layers: int, drill_mils: float,
             kind: str = "via", outer_oz: float = 1.0, tier: str = "standard") -> dict:
    """The smallest pad and inner-layer clearance a fab will build around
    a hole: pad = drill + 2 x the fab's ring for that kind of hole, and
    clearance = drill + 2 x its inner hole-to-copper. Each from the fab's
    page with its quote; one the fab does not publish is said so."""
    kind = kind.strip().lower()
    if kind not in ("via", "pth"):
        return _bad("kind is 'via' or 'pth'")
    if drill_mils <= 0:
        return _bad("drill_mils must be positive")
    ring_key = "min_annular_ring" if kind == "via" else "pth_annular_ring"
    clear_key = f"inner_{kind}_hole_to_copper"
    ring = fab_library.figure(vendor, ring_key, copper_layers=copper_layers,
                              outer_oz=outer_oz, tier=tier)
    out: dict = {"drill_mils": drill_mils, "kind": kind}
    if ring["ok"]:
        out["min_pad_mils"] = drill_mils + 2 * ring["mils"]
        out["ring"] = ring["figure"]
        rec = ring["figure"].get("recommended_mm")
        if rec:
            out["recommended_pad_mils"] = drill_mils + 2 * rec / _MM_PER_MIL
    else:
        out["pad_missing"] = ring["reason"]
    if copper_layers > 2:
        clear = fab_library.figure(vendor, clear_key, copper_layers=copper_layers,
                                   outer_oz=outer_oz, tier=tier)
        if clear["ok"]:
            out["min_inner_clearance_mils"] = drill_mils + 2 * clear["mils"]
            out["clearance"] = clear["figure"]
        else:
            out["clearance_missing"] = clear["reason"]
    drill = fab_library.figure(vendor, "min_drill", copper_layers=copper_layers,
                               outer_oz=outer_oz, tier=tier)
    if drill["ok"] and drill_mils < drill["mils"]:
        out["below_min_drill"] = drill["figure"]
    return _out("fab", f"{vendor} {tier} capability page", **out)


#: Name -> function, for the tools and the CLI.
CALCULATORS = {
    "reactance": reactance,
    "resonance": resonance,
    "wavelength": wavelength,
    "pdn-target-impedance": pdn_target_impedance,
    "wire-gauge": wire_gauge,
    "fusing-current": fusing_current,
    "via-inductance": via_inductance,
    "spiral-inductance": spiral_inductance,
    "padstack": padstack,
}

__all__ = ["CALCULATORS", *[f.__name__ for f in CALCULATORS.values()]]
