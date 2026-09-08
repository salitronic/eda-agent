# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A frozen, scorer-independent ruler for comparing schematic canvases.

``quality.score_canvas`` is the OBJECTIVE the layout engine optimises, and
it changes whenever the engine's priorities do. That makes it the wrong
instrument for asking whether a change made the drawing better: a session
that rewrote 567 lines of it also compared scores across the two versions
and reported an improvement that was an artifact of the new weights.

This module is the ruler instead. Every measure here is a raw count or
distance with no weights, computed by one function applied identically to
an engine canvas and to a human one, and the module is VERSIONED: a change
to any measure bumps ``RULER_VERSION`` and invalidates stored baselines.

The measures are the ones on which human sheets and engine output were found
to differ most, measured over 176 public sheets (engine vs human median):
axis alignment 36% vs 85%, wire length 2.5x, parts per row 1.57 vs 2.5,
density 0.49x, upright shunt parts 32% vs 60%.
"""
from __future__ import annotations

import statistics
from typing import Any

RULER_VERSION = 1

_KINDS = ("R", "C", "L", "D", "U", "J", "Y", "Q")


def _kind(refdes: str) -> str:
    letters = "".join(c for c in refdes if c.isalpha())
    return letters.upper() or "?"


def _pin_world_vertical(inst) -> bool | None:
    eps = list(inst.all_pin_endpoints())
    if len(eps) != 2:
        return None
    a, b = eps
    return abs(a.y - b.y) > abs(a.x - b.x)


def measure(canvas, plan=None) -> dict[str, Any]:
    """Every measure, on one canvas. Identical code for human and engine.

    ``plan`` is accepted for signature stability and is not needed by any
    current measure; keeping it here means a future net-aware measure does
    not change every caller.
    """
    inst = list(canvas.instances)
    if not inst:
        return {}
    xs = [i.x for i in inst]
    ys = [i.y for i in inst]
    out: dict[str, Any] = {"ruler_version": RULER_VERSION}

    # --- banding: parts per horizontal band and per vertical column ---
    bands: list[list[int]] = []
    for y in sorted(ys):
        if not bands or y - bands[-1][-1] > 200:
            bands.append([y])
        else:
            bands[-1].append(y)
    out["parts_per_row"] = len(inst) / len(bands)
    cols: list[list[int]] = []
    for x in sorted(xs):
        if not cols or x - cols[-1][-1] > 200:
            cols.append([x])
        else:
            cols[-1].append(x)
    out["parts_per_col"] = len(inst) / len(cols)

    # --- alignment: a part shares an exact axis with some other part ---
    aligned = sum(
        1 for a in inst
        if any(a is not b and (a.x == b.x or a.y == b.y) for b in inst))
    out["pct_axis_aligned"] = 100.0 * aligned / len(inst)

    # --- world orientation of 2-pin parts, overall and per refdes kind ---
    verts = [(_kind(i.refdes), _pin_world_vertical(i)) for i in inst]
    two_pin = [(k, v) for k, v in verts if v is not None]
    if two_pin:
        out["pct_two_pin_upright"] = 100.0 * sum(
            1 for _, v in two_pin if v) / len(two_pin)
    for k in _KINDS:
        vs = [v for kk, v in two_pin if kk == k]
        if vs:
            out[f"pct_upright_{k}"] = 100.0 * sum(1 for v in vs if v) / len(vs)

    # --- wires ---
    ws = list(canvas.wires)
    out["wire_segments"] = len(ws)
    out["wire_length"] = sum(abs(w.x2 - w.x1) + abs(w.y2 - w.y1) for w in ws)
    n = 0
    for i, a in enumerate(ws):
        av = a.x1 == a.x2
        for b in ws[i + 1:]:
            if av == (b.x1 == b.x2) or a.net == b.net:
                continue
            v, h = (a, b) if av else (b, a)
            if (min(h.x1, h.x2) < v.x1 < max(h.x1, h.x2)
                    and min(v.y1, v.y2) < h.y1 < max(v.y1, v.y2)):
                n += 1
    out["crossings"] = n
    out["long_wire_fraction"] = (
        100.0 * sum(1 for w in ws
                    if abs(w.x2 - w.x1) + abs(w.y2 - w.y1) > 1000)
        / max(1, len(ws)))

    # --- how connectivity is expressed ---
    pins = sum(len(list(i.all_pin_endpoints())) for i in inst)
    out["labels"] = len(canvas.labels)
    out["power_ports"] = len(canvas.power_ports)
    out["labels_per_pin"] = len(canvas.labels) / max(1, pins)
    out["wire_len_per_pin"] = out["wire_length"] / max(1, pins)

    # --- extent ---
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    out["width"] = w
    out["height"] = h
    out["aspect"] = w / max(1, h)
    out["density"] = len(inst) / max(1.0, (w * h) / 1e6)

    # --- connectors at the sheet edge ---
    conns = [i for i in inst if _kind(i.refdes).startswith("J")]
    if conns and len(inst) > 2 and w > 0:
        out["conn_edge_bias"] = 100.0 * statistics.mean(
            max(abs(c.x - min(xs)), abs(max(xs) - c.x)) / w for c in conns)
    return out


def compare(engine: dict[str, Any], human: dict[str, Any]) -> dict[str, float]:
    """Per-measure engine/human ratio for one sheet; inf where human is 0."""
    out: dict[str, float] = {}
    for k, hv in human.items():
        if k == "ruler_version" or k not in engine:
            continue
        ev = engine[k]
        out[k] = (ev / hv) if hv else (float("inf") if ev else 1.0)
    return out
