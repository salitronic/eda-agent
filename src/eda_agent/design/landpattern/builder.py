# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Shared assembly of a footprint: pads in, finished model out.

Each family function (``smd.py``, ``tht.py``) places lands and states the
body, the courtyard excess and where a pin 1 mark may go. This module
does the rest the same way for every family: land shapes, clearance
trimming, silkscreen, pin 1 mark, assembly outline, courtyard, 3D body,
the description and the record of the calculation.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from eda_agent.design.landpattern import ipc7351 as ipc
from eda_agent.design.landpattern.drafting import (
    DEFAULT_RULES,
    Box,
    DraftingRules,
    assembly_outline,
    box_gap,
    clip_segment,
    copper_boxes,
    corner_ratio_for,
    courtyard_polygon,
    courtyard_rect,
    enforce_pad_gaps,
    pick_pin1_dot,
    ref_height,
    silk_clear_of_lands,
    silk_outline,
)
from eda_agent.design.landpattern.model import (
    Body3D,
    Circle,
    FootprintModel,
    Line,
    Pad,
    Polygon,
    Rect,
    Text,
)
from eda_agent.design.landpattern.spec import PackageSpec, SpecError

__all__ = ["Builder", "build_footprint"]

_FAMILY_TITLES = {
    "chip": "Chip", "melf": "MELF", "molded": "Molded body", "sod": "SOD",
    "sot23": "SOT-23", "sot89": "SOT-89", "sot223": "SOT-223",
    "dpak": "DPAK/D2PAK", "soic": "Small outline (SOIC/SOP/TSSOP/MSOP)",
    "son": "Small outline no-lead (SON/DFN)", "qfn": "Quad flat no-lead",
    "qfp": "Quad flat package", "plcc": "PLCC (J-lead)", "bga": "BGA",
    "lga": "LGA", "crystal2": "Crystal, 2 pad", "crystal4": "Crystal, 4 pad",
    "dip": "DIP", "header": "Pin header", "radial": "Radial",
    "axial": "Axial", "to220": "TO-220", "to92": "TO-92",
    "connector": "Connector", "custom": "Custom",
}


class Builder:
    """Collects what a family function decides, then finishes the model."""

    def __init__(self, spec: PackageSpec, density: str, rules: DraftingRules,
                 warnings: Sequence[str] = ()):
        self.spec = spec
        self.d = density
        self.rules = rules
        self.pads: list[Pad] = []
        self.calcs: dict[str, dict] = {}
        self.tables: dict[str, dict] = {}
        self.notes: list[str] = []
        self.warnings: list[str] = list(warnings)
        self.body: Optional[Box] = None
        self.excess: float = ipc.goal_table("gullwing").at(density).courtyard
        self.silk_path: Optional[list[tuple[float, float]]] = None
        self.silk_closed = True
        self.extra_silk_paths: list[tuple[list[tuple[float, float]], bool]] = []
        self.pin1_candidates: list[tuple[float, float]] = []
        self.marker = True
        self.chamfer = True
        self.assembly_shape: Optional[Polygon] = None
        self.extra_assembly: list[Any] = []
        #: Copper and solder-mask outlines of lands that are not rectangles.
        self.land_outlines: list[Polygon] = []
        self.offset = (0.0, 0.0)
        self.pin1_name = "1"
        self.extra_ipc: dict[str, Any] = {}
        self.F = ipc.FAB_TOL
        self.P = ipc.PLACE_TOL

    # -- helpers for family functions ---------------------------------------

    def pad(self, number: str, x: float, y: float, w: float, h: float, *,
            shape: str = "roundrect", role: str = "signal", group: str = "",
            kind: str = "smd", hole: float = 0.0, hole_length: float = 0.0,
            plated: bool = True, rotation: float = 0.0,
            paste_apertures: Optional[list[Rect]] = None) -> Pad:
        if w <= 0 or h <= 0:
            raise SpecError(
                f"land {number} came out {w:.3f} x {h:.3f} mm; check the "
                "terminal dimensions")
        p = Pad(number=str(number), x=round(x, 6), y=round(y, 6),
                width=round(w, 6), height=round(h, 6), shape=shape,
                rotation=rotation, kind=kind, hole=hole,
                hole_length=hole_length, plated=plated, role=role,
                group=group, paste_apertures=list(paste_apertures or []))
        if kind != "smd":
            p.paste = False
        self.pads.append(p)
        return p

    def record(self, name: str, calc: Any, table_key: Optional[str] = None,
               **extra: Any) -> None:
        d = calc.to_dict() if hasattr(calc, "to_dict") else dict(calc)
        if table_key:
            t = ipc.goal_table(table_key)
            d["table"] = table_key
            self.tables[table_key] = {"title": t.title, "source": t.source}
        d.update(extra)
        self.calcs[name] = d

    def set_body(self, bx: Optional[float], by: Optional[float],
                 cx: float = 0.0, cy: float = 0.0) -> None:
        if bx and by:
            self.body = (round(cx - bx / 2, 6), round(cy - by / 2, 6),
                         round(cx + bx / 2, 6), round(cy + by / 2, 6))

    def dot_radius(self) -> float:
        return self.rules.pin1_dot_diameter / 2.0

    def cands_row_end_then_toe(self, p: Pad, side: str) -> list[tuple[float, float]]:
        """Pin 1 dot positions next to land ``p`` on ``side``: first past the
        end of its row (the corner), then beyond its toe."""
        r = self.dot_radius()
        c = self.rules.silk_clearance
        x0, y0, x1, y1 = p.bbox()
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if side == "left":
            return [(mx, y1 + c + r), (x0 - c - r, my)]
        if side == "right":
            return [(mx, y1 + c + r), (x1 + c + r, my)]
        if side == "top":
            return [(x0 - c - r, my), (mx, y1 + c + r)]
        if side == "bottom":
            return [(x0 - c - r, my), (mx, y0 - c - r)]
        return [(x0 - c - r, my)]

    def add_ep(self, default_number: str) -> Optional[Pad]:
        """The exposed (thermal) pad, with its paste windows."""
        s = self.spec
        if s.ep_x is None or s.ep_y is None:
            return None
        mode = self.rules.ep_land
        w = s.ep_x.max if mode == "max" else s.ep_x.nom
        h = s.ep_y.max if mode == "max" else s.ep_y.nom
        cx, cy = s.ep_offset
        from eda_agent.design.landpattern.drafting import ep_paste_windows
        windows, coverage = ep_paste_windows(cx, cy, w, h, self.rules)
        num = s.ep_number or default_number
        p = self.pad(num, cx, cy, w, h, role="thermal", group="ep",
                     paste_apertures=windows)
        self.extra_ipc["exposed_pad"] = {
            "number": num, "land": [w, h], "sized_from": mode,
            "source": ("Hausherr CADlib p. 30, 1:1 with the maximum lead"
                       if mode == "max" else "TB389 p. 1, 1:1 with the die pad"),
            "paste_windows": len(windows),
            "paste_coverage_target": self.rules.ep_paste_coverage,
            "paste_coverage": coverage,
            "paste_source": "TB389 rev. 7 p. 3: 50 to 80 % coverage",
        }
        return p

    # -- finishing ---------------------------------------------------------------

    def finish(self) -> FootprintModel:
        r = self.rules
        s = self.spec
        ox, oy = self.offset
        if ox or oy:
            for p in self.pads:
                p.x = round(p.x + ox, 6)
                p.y = round(p.y + oy, 6)
                for a in p.paste_apertures:
                    a.x = round(a.x + ox, 6)
                    a.y = round(a.y + oy, 6)
            if self.body:
                b = self.body
                self.body = (b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy)
            self.pin1_candidates = [(x + ox, y + oy) for x, y in self.pin1_candidates]

        for p in self.pads:
            if p.kind == "smd":
                p.paste_expansion = r.paste_expansion
            p.mask_expansion = r.mask_expansion
        trims = enforce_pad_gaps(self.pads, r.min_pad_gap)
        self.notes.extend(trims)
        for p in self.pads:
            if p.shape == "roundrect":
                p.corner_ratio = corner_ratio_for(p.width, p.height, r)
        self._check_overlaps()

        # silkscreen
        silk: list[Any] = []
        path = self.silk_path
        if path is None and self.body is not None:
            x0, y0, x1, y1 = self.body
            path = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
        copper = copper_boxes(self.land_outlines)
        if path:
            outline = silk_outline(path, self.pads, r, closed=self.silk_closed,
                                   copper=copper)
            if (not outline and self.silk_path is None and self.body is not None
                    and s.family in ("custom", "bga", "lga")):
                # Lands at the body edge all round cut the whole body outline
                # away: draw it just clear of the lands instead. A chip's
                # lands cover its ends and it carries no outline, by design.
                outline = silk_outline(
                    silk_clear_of_lands(self.body, self.pads, r, copper),
                    self.pads, r, copper=copper)
                self.notes.append("silkscreen drawn outside the lands: they "
                                  "reach the body edge all round")
            silk.extend(outline)
        for pts, closed in self.extra_silk_paths:
            silk.extend(silk_outline(pts, self.pads, r, closed=closed,
                                     copper=copper))
        ipc_court, _ = courtyard_rect(self.pads, self.body, self.excess, r,
                                     copper=copper)
        dot = None
        if self.marker and self.pin1_candidates:
            dot = pick_pin1_dot(self.pin1_candidates, self.pads, r, inside=ipc_court)
            if dot is None:
                self.warnings.append("no room for a pin 1 mark clear of the lands")
            else:
                # Keep outline silk off the dot so the mark reads as a dot and
                # not as a blob on a corner line (QFN corner marks).
                m = dot.r + r.silk_width
                keep = [(dot.x - m, dot.y - m, dot.x + m, dot.y + m)]
                clipped: list[Any] = []
                for g in silk:
                    if isinstance(g, Line):
                        # No length floor here: an arc is many short lines,
                        # and only the pieces under the dot should go.
                        for seg in clip_segment(g.x1, g.y1, g.x2, g.y2, keep,
                                                1e-6):
                            clipped.append(Line("silk", *seg, width=g.width))
                    else:
                        clipped.append(g)
                silk = clipped + [dot]
        extra_boxes = []
        for g in silk:
            if isinstance(g, Line):
                hw = g.width / 2
                extra_boxes.append((min(g.x1, g.x2) - hw, min(g.y1, g.y2) - hw,
                                    max(g.x1, g.x2) + hw, max(g.y1, g.y2) + hw))
            elif isinstance(g, Circle):
                rr = g.r + g.width / 2
                extra_boxes.append((g.x - rr, g.y - rr, g.x + rr, g.y + rr))
        court_box, court_info = courtyard_rect(self.pads, self.body, self.excess,
                                               r, extra=extra_boxes, copper=copper)
        courtyard = courtyard_polygon(court_box, r)

        # assembly
        assembly: list[Any] = []
        body = self.body or _pads_box(self.pads)
        if self.assembly_shape is not None:
            assembly.append(self.assembly_shape)
        else:
            px, py = self._pin1()
            bcx, bcy = (body[0] + body[2]) / 2, (body[1] + body[3]) / 2
            corner = (1 if px > bcx + 1e-6 else -1, -1 if py < bcy - 1e-6 else 1)
            assembly.append(assembly_outline(body, r, chamfer=self.chamfer,
                                             corner=corner))
        assembly.extend(self.extra_assembly)
        h = ref_height(body, r)
        cx = (body[0] + body[2]) / 2
        cyy = (body[1] + body[3]) / 2
        ref = Text("assembly", "REF", round(cx, 4), round(cyy, 4), h,
                   round(0.15 * h, 4))

        z0 = s.standoff.min if s.standoff is not None else 0.0
        z1 = s.height.max if s.height is not None else 0.0
        if not z1:
            self.warnings.append("no package height given; the 3D body is flat")
        body3d = Body3D(round(body[0], 4), round(body[1], 4), round(body[2], 4),
                        round(body[3], 4), round(z0, 4), round(z1, 4))

        pin1 = self._pin1()
        name = s.name or self._default_name()
        desc = self._description()
        model = FootprintModel(
            name=name, description=desc, family=s.family, density=self.d,
            pads=self.pads, graphics=silk + assembly + self.land_outlines,
            courtyard=courtyard,
            ref=ref, body=body3d, pin1=pin1,
            source=s.source.to_dict() if s.source else {},
            ipc=self._ipc_record(court_info), warnings=self.warnings)
        return model

    def _check_overlaps(self) -> None:
        for i, a in enumerate(self.pads):
            for b in self.pads[i + 1:]:
                if a.number == b.number:
                    continue
                if box_gap(a.bbox(), b.bbox()) <= 0:
                    raise SpecError(
                        f"lands {a.number} and {b.number} overlap; the package "
                        "dimensions leave no room between them")

    def _pin1(self) -> tuple[float, float]:
        for p in self.pads:
            if p.number == self.pin1_name:
                return (p.x, p.y)
        p = self.pads[0]
        return (p.x, p.y)

    def _default_name(self) -> str:
        s = self.spec
        parts = [s.family.upper()]
        if s.pins:
            parts.append(f"{s.pins}")
        if s.pitch is not None:
            parts.append(f"P{s.pitch.nom:g}")
        return "-".join(parts) + self.d

    def _description(self) -> str:
        s = self.spec
        bits = [_FAMILY_TITLES.get(s.family, s.family)]
        n = len({p.number for p in self.pads if p.number})
        if n:
            bits.append(f"{n} pads")
        if s.pitch is not None:
            bits.append(f"{s.pitch.nom:g} mm pitch")
        head = ", ".join(bits)
        if s.family in ("dip", "header", "radial", "axial", "to220", "to92",
                        "connector"):
            std = "IPC-2221/IPC-2222 padstacks"
        elif s.family == "custom":
            std = "custom lands"
        else:
            std = "IPC-7351B land pattern"
        level = {"L": "least", "N": "nominal", "M": "most"}[self.d]
        out = f"{head}; {std}, density {self.d} ({level})"
        if s.part_number:
            out += f"; {s.part_number}"
        if s.source is not None and s.source.document:
            out += f"; from {s.source.short()}"
        if s.description:
            out += f"; {s.description}"
        return out

    def _ipc_record(self, court_info: dict) -> dict:
        # Through JSON once so tuples become lists and the record compares
        # equal after a to_dict / from_dict round trip.
        return json.loads(json.dumps(self._ipc_raw(court_info)))

    def _ipc_raw(self, court_info: dict) -> dict:
        return {
            "standard": "IPC-7351B",
            "density": self.d,
            "density_level": ipc.DENSITY_LEVEL[self.d],
            "F": self.F,
            "P": self.P,
            "tolerance_source": "IPC-7351:2005 Table 3-1 and p. 10 example; "
                                "Hausherr CADlib p. 51",
            "rounding": {
                "land_size": ipc.LAND_SIZE_ROUND,
                "land_span": ipc.LAND_SPAN_ROUND,
                "small_chip_size": ipc.SMALL_LAND_SIZE_ROUND,
                "small_chip_span": ipc.SMALL_LAND_SPAN_ROUND,
                "courtyard": self.rules.courtyard_round,
            },
            "tables": self.tables,
            "calcs": self.calcs,
            "courtyard": court_info,
            "notes": self.notes,
            **self.extra_ipc,
        }


def _pads_box(pads: Sequence[Pad]) -> Box:
    boxes = [p.bbox() for p in pads]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def build_footprint(spec: PackageSpec, density: Optional[str] = None,
                    rules: Optional[DraftingRules] = None, *,
                    fab_tol: Optional[float] = None,
                    place_tol: Optional[float] = None) -> FootprintModel:
    """Validate ``spec`` and build its land pattern.

    ``density`` overrides ``spec.density`` (``L``, ``N`` or ``M``).
    ``fab_tol`` and ``place_tol`` override F and P (see ``ipc7351``); they
    can also come from ``spec.options['fab_tol' / 'place_tol']``.
    """
    from eda_agent.design.landpattern import smd, tht

    warnings = spec.validate()
    d = ipc.normalise_density(density or spec.density)
    b = Builder(spec, d, rules or DEFAULT_RULES, warnings)
    opts = spec.options or {}
    for attr, arg, key in (("F", fab_tol, "fab_tol"), ("P", place_tol, "place_tol")):
        val = arg if arg is not None else opts.get(key)
        if val is not None:
            if not (0 <= float(val) < 1.0):
                raise SpecError(f"{key} must be between 0 and 1 mm, got {val}")
            setattr(b, attr, float(val))
    fn = smd.FAMILY_BUILDERS.get(spec.family) or tht.FAMILY_BUILDERS.get(spec.family)
    if fn is None:
        raise SpecError(f"no builder for family {spec.family!r}")
    fn(b)
    if not b.pads:
        raise SpecError("the family produced no lands")
    return b.finish()
