# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Impedance on the board's own stack, for the brief and the review.

The AI gives a net its target impedance (single-ended, or differential with
the pair's gap); the brief check tells it the width each layer needs, and
the review measures every routed piece at its width on its layer. The
numbers are IPC-2141 closed forms (design/impedance_sizing.py), good to
about ten percent: a field solver, or the fab's, has the last word.

An outer copper layer is a microstrip over the dielectric beneath it; an
inner one a stripline between the two next to it (its plane spacing the two
dielectrics and the copper, its constant theirs weighted by thickness).
Copper layers of the stack are matched to the board's in order, top down.
"""

from __future__ import annotations

from typing import Optional

from ...design import impedance_sizing as zs
from ...units import OZ_TO_MILS
from ..model import LayoutBoard


def geometry(board: LayoutBoard) -> dict[str, dict]:
    """Copper layer name -> {geometry, h, er, oz}; a layer whose dielectric
    the stack does not give is left out."""
    stack = board.meta.get("stack") or []
    copper = [i for i, l in enumerate(stack) if l["kind"] == "copper"]
    names = board.copper_layers()
    if not copper or len(copper) != len(names):
        return {}
    out: dict[str, dict] = {}
    for k, (i, name) in enumerate(zip(copper, names)):
        oz = stack[i]["thickness"] / OZ_TO_MILS if stack[i]["thickness"] > 0 else 1.0
        above = stack[i - 1] if i > 0 and stack[i - 1]["kind"] == "dielectric" else None
        below = stack[i + 1] if i + 1 < len(stack) and stack[i + 1]["kind"] == "dielectric" else None
        outer = k == 0 or k == len(copper) - 1
        if outer:
            d = below if k == 0 else above
            if d and d["thickness"] > 0 and d["er"] > 0:
                out[name] = {"geometry": "microstrip", "h": d["thickness"], "er": d["er"],
                             "oz": oz}
        elif above and below and min(above["thickness"], below["thickness"]) > 0 \
                and min(above["er"], below["er"]) > 0:
            b = above["thickness"] + below["thickness"] + stack[i]["thickness"]
            er = ((above["er"] * above["thickness"] + below["er"] * below["thickness"])
                  / (above["thickness"] + below["thickness"]))
            out[name] = {"geometry": "stripline", "h": b, "er": er, "oz": oz}
    return out


def z0(geo: dict, width: float, gap: Optional[float] = None) -> Optional[float]:
    """The impedance of a ``width`` track on the layer ``geo`` describes:
    single-ended, or differential with ``gap``. None where the closed form
    gives no physical answer."""
    if width <= 0:
        return None
    f = zs.z0_microstrip if geo["geometry"] == "microstrip" else zs.z0_stripline
    se = f(width, geo["h"], geo["er"], geo["oz"])
    if se <= 0.5:
        return None
    if gap:
        return 2.0 * se * zs.diff_coupling_factor(geo["geometry"] + "_diff", gap, geo["h"])
    return se


def width_for(geo: dict, target: float, gap: Optional[float] = None) -> Optional[float]:
    """The width that gives ``target`` ohms on the layer, or None if none can."""
    g = geo["geometry"] + ("_diff" if gap else "")
    r = zs.trace_width_for_impedance(target, g, geo["h"], dielectric_constant=geo["er"],
                                     copper_oz=geo["oz"], spacing_mils=gap or 0.0)
    return round(r.width_mils, 2) if r.feasible else None


__all__ = ["geometry", "z0", "width_for"]
