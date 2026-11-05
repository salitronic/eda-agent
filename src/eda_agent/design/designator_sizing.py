# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Designator texts sized to their parts, for an assembly drawing.

The convention of Altium's AdjustDesignators.pas example script: a stroke
font centred on the part, running along its longer side, the characters
sharing that side's length but no taller than the part is wide, kept
between a smallest and a largest height. The stroke is a share of the
height with a ceiling. Bottom parts read mirrored; an upright part turns
its text 90 degrees on the top and 270 on the bottom, so both read from
the same board edge.

The board talks in mils; the plans here are in millimetres.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..units import MILS_PER_MM


@dataclass(frozen=True)
class DesignatorLimits:
    """Bounds a resize keeps to, and where the texts go."""
    smallest_mm: float = 0.1
    largest_mm: float = 1.0
    stroke_share: float = 0.25
    stroke_cap_mm: float = 0.254
    top_layer: str = ""
    bottom_layer: str = ""

    def problems(self) -> list[str]:
        out = []
        if not 0 < self.smallest_mm <= self.largest_mm:
            out.append("smallest_mm must be above zero and no more than largest_mm")
        if self.stroke_share <= 0 or self.stroke_cap_mm <= 0:
            out.append("stroke_share and stroke_cap_mm must be above zero")
        return out


def _span_mm(part: dict[str, Any]) -> tuple[float, float]:
    left, bottom, right, top = (float(v) for v in part["box_mils"])
    return abs(right - left) / MILS_PER_MM, abs(top - bottom) / MILS_PER_MM


def size_designator(part: dict[str, Any], limits: DesignatorLimits) -> dict[str, Any]:
    """The text one part's designator should get, from its outline box."""
    name = str(part.get("designator", ""))
    across, up = _span_mm(part)
    per_char = max(across, up) / max(len(name), 1)
    room = min(across, up)
    natural = min(per_char, room)
    height = max(limits.smallest_mm, min(natural, limits.largest_mm))
    bottom = bool(part.get("bottom"))
    angle = (270 if bottom else 90) if up > across else 0
    plan: dict[str, Any] = {
        "designator": name, "bottom": bottom,
        "move_to": limits.bottom_layer if bottom else limits.top_layer,
        "part_mm": [round(across, 4), round(up, 4)],
        "height_mm": round(height, 4),
        "stroke_mm": round(min(height * limits.stroke_share, limits.stroke_cap_mm), 4),
        "angle": angle, "mirrored": bottom}
    if per_char > room:
        plan["limited_by_width"] = True
    if round(height, 4) != round(natural, 4):
        plan["natural_mm"] = round(natural, 4)
    return plan


def batch_op(plan: dict[str, Any]) -> str:
    """The plan as one op for the board's batch format, sizes in mils."""
    fields = [("designator", plan["designator"]),
              ("height", f"{plan['height_mm'] * MILS_PER_MM:.4f}"),
              ("stroke", f"{plan['stroke_mm'] * MILS_PER_MM:.4f}"),
              ("angle", str(plan["angle"])),
              ("mirror", "true" if plan["mirrored"] else "false")]
    if plan["move_to"]:
        fields.append(("layer", plan["move_to"]))
    return ";".join(f"{k}={v}" for k, v in fields)


def shortfalls(plan: dict[str, Any], part: dict[str, Any], label: dict[str, Any],
               off_centre_mm: float = 0.25) -> list[str]:
    """How the text read back after the write differs from the plan."""
    found = []

    def norm(layer: str) -> str:
        return layer.replace(" ", "").upper()

    if plan["move_to"] and norm(str(label.get("layer", ""))) != norm(plan["move_to"]):
        found.append(f"on {label.get('layer')}, not {plan['move_to']}")
    for key, wanted in (("height_mils", "height_mm"), ("stroke_mils", "stroke_mm")):
        got_mm = float(label.get(key, 0)) / MILS_PER_MM
        if abs(got_mm - plan[wanted]) > 0.002:
            found.append(f"{wanted[:-3]} {got_mm:.3f} mm")
    if round(float(label.get("angle", 0))) % 360 != plan["angle"] % 360:
        found.append(f"angle {label.get('angle')}")
    if bool(label.get("mirrored")) != plan["mirrored"]:
        found.append("mirrored" if label.get("mirrored") else "not mirrored")
    if not label.get("stroke_font", True):
        found.append("TrueType font kept")
    if not label.get("visible", True):
        found.append("hidden")
    middle = label.get("middle_mils")
    if middle:
        left, bottom, right, top = (float(v) for v in part["box_mils"])
        dx = float(middle[0]) - (left + right) / 2
        dy = float(middle[1]) - (bottom + top) / 2
        gap = (dx * dx + dy * dy) ** 0.5 / MILS_PER_MM
        if gap > off_centre_mm:
            found.append(f"{gap:.2f} mm off the part's centre")
    return found
