# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EasyEDA component document model.

One level above :mod:`shapes`: takes the JSON an EasyEDA/LCSC component
response carries and produces a normalized :class:`EasyEdaComponent`
with symbol geometry, footprint geometry, and part metadata, all in
MILS relative to each element's own origin and with the Y axis already
flipped to Y-up.

Why normalize here rather than in each emitter: EasyEDA is Y-down on an
absolute canvas, KiCad symbols are Y-up, KiCad footprints are Y-down,
and Altium is Y-up. Converting once to a single neutral convention
(Y-up, mils, origin-relative) means each emitter applies at most one
further flip, instead of every emitter re-deriving the same arithmetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from eda_agent.libimport.easyeda.shapes import (
    EASYEDA_UNIT_MIL,
    EeShape,
    parse_footprint_shapes,
    parse_symbol_shapes,
)

__all__ = [
    "EasyEdaComponent",
    "EasyEdaFootprint",
    "EasyEdaSymbol",
    "parse_component",
]


def _origin(head: dict[str, Any]) -> tuple[float, float]:
    """The element's origin in canvas units."""
    try:
        return (float(head.get("x", 0) or 0), float(head.get("y", 0) or 0))
    except (TypeError, ValueError):
        return (0.0, 0.0)


@dataclass
class EasyEdaSymbol:
    """Schematic symbol: shapes in mils, Y-up, relative to the origin."""

    name: str = ""
    prefix: str = "U"
    shapes: list[EeShape] = field(default_factory=list)


@dataclass
class EasyEdaFootprint:
    """PCB footprint: shapes in mils, Y-up, relative to the origin."""

    name: str = ""
    shapes: list[EeShape] = field(default_factory=list)
    model_3d_uuid: Optional[str] = None
    model_3d_name: Optional[str] = None
    #: Reference to a 3D model FILE, as the source recorded it. KiCad
    #: writes "${KICAD10_3DMODEL_DIR}/Lib.3dshapes/Name.step", which
    #: resolves to a real STEP file, and Altium's linker wants STEP. The
    #: EasyEDA path has no equivalent: its model arrives as OBJ, which
    #: Altium cannot load, so this stays empty there.
    model_3d_ref: str = ""
    #: The same reference resolved to a path on this machine, when it
    #: could be. Blank means it was not found, never a guess.
    model_3d_path: str = ""


@dataclass
class EasyEdaComponent:
    """A whole part: metadata plus its symbol and footprint."""

    lcsc_id: str = ""
    #: "Basic Part" or "Extended Part" at JLCPCB assembly, as the source
    #: states it; blank when it does not say.
    jlc_part_class: str = ""
    #: LCSC stock when the source reports it, else None (not zero).
    lcsc_stock: Optional[int] = None
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    datasheet: str = ""
    description: str = ""
    #: The footprint the SOURCE says belongs to this symbol, in whatever
    #: form it records ("Library:Name" for KiCad). Distinct from
    #: ``package``, which is a human package name: this one is a pointer
    #: that can be resolved to a real file, and resolving it is what
    #: turns a symbol-only hit into a whole part.
    footprint_ref: str = ""
    #: How many sub-parts the SOURCE part has. A quad gate reads as 4.
    #: Every unit is normally read at once and each pin tagged via
    #: ``EePin.unit``, so the emitter builds ONE multi-part component;
    #: this is here so a caller can see the shape of the part without
    #: walking the pins. Carried as data rather than only as a warning
    #: string, since acting on it should not mean parsing prose.
    unit_count: int = 1
    #: The single sub-part this component holds, when only one was
    #: requested. Meaningless when all units were read (the pins carry
    #: their own), and left at 1 by sources with no sub-part concept.
    unit: int = 1
    symbol: Optional[EasyEdaSymbol] = None
    footprint: Optional[EasyEdaFootprint] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lcsc_id": self.lcsc_id,
            "jlc_part_class": self.jlc_part_class,
            "lcsc_stock": self.lcsc_stock,
            "mpn": self.mpn,
            "manufacturer": self.manufacturer,
            "package": self.package,
            "datasheet": self.datasheet,
            "description": self.description,
            "footprint_ref": self.footprint_ref,
            "unit_count": self.unit_count,
            "unit": self.unit,
            "symbol": {
                "name": self.symbol.name,
                "prefix": self.symbol.prefix,
                "shape_count": len(self.symbol.shapes),
                "pin_count": sum(
                    1 for s in self.symbol.shapes if s.kind == "pin"),
            } if self.symbol else None,
            "footprint": {
                "name": self.footprint.name,
                "shape_count": len(self.footprint.shapes),
                "pad_count": sum(
                    1 for s in self.footprint.shapes if s.kind == "pad"),
                "model_3d_uuid": self.footprint.model_3d_uuid,
            } if self.footprint else None,
            "warnings": list(self.warnings),
        }


def _to_mils_yup(shapes: list[EeShape], ox: float, oy: float) -> None:
    """Convert every shape in place: units -> mils, canvas -> origin, Y up.

    EasyEDA's canvas grows downward, so a Y coordinate becomes
    ``(origin_y - value) * unit``; X is a plain ``(value - origin_x)``.
    """
    k = EASYEDA_UNIT_MIL

    def cx(v: float) -> float:
        return (v - ox) * k

    def cy(v: float) -> float:
        return (oy - v) * k

    for s in shapes:
        kind = s.kind
        if kind == "pad":
            s.cx, s.cy = cx(s.cx), cy(s.cy)
            s.width *= k
            s.height *= k
            # EasyEDA stores a hole RADIUS; keep a diameter downstream.
            s.hole_radius *= k
            s.hole_length *= k
            s.points = [(cx(px), cy(py)) for (px, py) in s.points]
            # A Y mirror negates rotation. Rect/oval pads happen to be
            # 180-symmetric so this is invisible for them, but relying on
            # that would break the moment a non-symmetric pad shows up.
            s.rotation = (-s.rotation) % 360.0
        elif kind == "pin":
            s.x, s.y = cx(s.x), cy(s.y)
            s.length *= k
            # Two corrections collapse into one formula.
            #
            # 1. EasyEDA's rotation is 180 degrees off the KiCad/Altium
            #    convention. Verified against a real API payload: a body
            #    spanning x=370..430 has its LEFT pins at x=360 drawn
            #    inward (M360,310h10) carrying rot=180, and its RIGHT
            #    pins at x=440 drawn inward carrying rot=0. KiCad and
            #    Altium both call "extends +X" angle 0.
            # 2. Flipping Y mirrors direction, negating the angle.
            #
            # canvas direction  d = (-cos t, -sin t)   [Y-down]
            # neutral direction d = (-cos t, +sin t)   [Y-up]
            #                     = (cos(180 - t), sin(180 - t))
            s.rotation = (180.0 - s.rotation) % 360.0
        elif kind in ("rect",):
            # Rect y is the TOP edge on a Y-down canvas; after flipping
            # it becomes the BOTTOM edge, which is what Y-up consumers
            # expect from (x, y, w, h).
            s.x = cx(s.x)
            s.y = cy(s.y + s.height)
            s.width *= k
            s.height *= k
            s.stroke_width *= k
        elif kind in ("circle", "ellipse"):
            s.cx, s.cy = cx(s.cx), cy(s.cy)
            s.radius *= k
            if s.ry is not None:
                s.ry *= k
            s.stroke_width *= k
        elif kind in ("polyline", "polygon", "track", "solid_region"):
            s.points = [(cx(px), cy(py)) for (px, py) in s.points]
            s.stroke_width *= k
        elif kind == "arc":
            s.stroke_width *= k
            s.x1, s.y1 = cx(s.x1), cy(s.y1)
            s.x2, s.y2 = cx(s.x2), cy(s.y2)
            # Radii are lengths: they scale, but are never translated
            # or flipped. Flipping Y mirrors the curve, which reverses
            # the sweep direction.
            s.rx *= k
            s.ry *= k
            s.sweep = 0 if s.sweep else 1
        elif kind == "text":
            s.x, s.y = cx(s.x), cy(s.y)
            s.font_size *= k
            s.stroke_width *= k
            s.rotation = (-s.rotation) % 360.0
        elif kind == "hole":
            s.cx, s.cy = cx(s.cx), cy(s.cy)
            s.diameter *= k


def _attr(attrs: dict[str, Any], *names: str, default: str = "") -> str:
    for n in names:
        v = attrs.get(n)
        if v:
            return str(v).strip()
    return default


def parse_component(payload: dict[str, Any]) -> EasyEdaComponent:
    """Build a component from an EasyEDA/LCSC component JSON payload.

    Accepts either the raw API envelope (``{"success":..,"result":{..}}``)
    or the inner result object, so a saved fixture works either way.
    """
    result = payload.get("result", payload) or {}
    comp = EasyEdaComponent()

    data_str = result.get("dataStr") or {}
    head = data_str.get("head") or {}
    attrs = head.get("c_para") or {}

    # The live service keys the part number as lcsc.number (and repeats
    # it as the "Supplier Part" attribute); "code" is an older spelling.
    lcsc = result.get("lcsc") or {}
    szlcsc = result.get("szlcsc") or {}
    comp.lcsc_id = str(
        lcsc.get("number") or szlcsc.get("number") or szlcsc.get("code")
        or result.get("code") or _attr(attrs, "Supplier Part") or "").strip()
    comp.jlc_part_class = _attr(attrs, "JLCPCB Part Class")
    stock = lcsc.get("stock")
    comp.lcsc_stock = stock if isinstance(stock, int) else None

    comp.mpn = _attr(attrs, "Manufacturer Part", "name")
    comp.manufacturer = _attr(attrs, "Manufacturer")
    comp.package = _attr(attrs, "package", "Package")
    comp.datasheet = str(
        result.get("lcsc", {}).get("url")
        or result.get("szlcsc", {}).get("url") or "").strip()
    comp.description = str(result.get("description") or "").strip()

    # ---- symbol -------------------------------------------------------
    sym_shapes_raw = data_str.get("shape") or []
    if isinstance(sym_shapes_raw, list):
        blob = "#@$".join(str(s) for s in sym_shapes_raw)
    else:
        blob = str(sym_shapes_raw)
    if blob.strip():
        sym = EasyEdaSymbol(
            name=comp.mpn or comp.lcsc_id or "SYMBOL",
            prefix=(_attr(attrs, "pre", "Prefix", default="U?")
                    .replace("?", "") or "U"),
            shapes=parse_symbol_shapes(blob),
        )
        ox, oy = _origin(head)
        _to_mils_yup(sym.shapes, ox, oy)
        comp.symbol = sym

    # ---- footprint ----------------------------------------------------
    pkg = result.get("packageDetail") or {}
    pkg_data = pkg.get("dataStr") or {}
    pkg_head = pkg_data.get("head") or {}
    fp_shapes_raw = pkg_data.get("shape") or []
    if isinstance(fp_shapes_raw, list):
        fp_blob = "#@$".join(str(s) for s in fp_shapes_raw)
    else:
        fp_blob = str(fp_shapes_raw)

    if fp_blob.strip():
        fp = EasyEdaFootprint(
            name=(pkg.get("title") or comp.package or "FOOTPRINT").strip(),
            shapes=parse_footprint_shapes(fp_blob),
        )
        pox, poy = _origin(pkg_head)
        _to_mils_yup(fp.shapes, pox, poy)
        _attach_3d(fp, pkg_data)
        comp.footprint = fp

    if comp.symbol is None:
        comp.warnings.append("payload carries no symbol geometry")
    if comp.footprint is None:
        comp.warnings.append("payload carries no footprint geometry")
    _warn_unsupported(comp)
    return comp


def _attach_3d(fp: EasyEdaFootprint, pkg_data: dict[str, Any]) -> None:
    """Find the 3D model reference, which rides as an SVGNODE shape."""
    for raw in pkg_data.get("shape") or []:
        s = str(raw)
        if not s.startswith("SVGNODE"):
            continue
        try:
            node = json.loads(s.split("~", 1)[1])
        except (ValueError, IndexError):
            continue
        attrs = node.get("attrs") or {}
        uuid = attrs.get("uuid")
        if uuid:
            fp.model_3d_uuid = str(uuid)
            fp.model_3d_name = str(attrs.get("title") or "").strip() or None
            return


def _warn_unsupported(comp: EasyEdaComponent) -> None:
    """Flag geometry no target CAD can reproduce faithfully.

    Silence here would be the dangerous outcome: a polygon pad quietly
    approximated by a rectangle changes the land pattern.
    """
    if comp.footprint:
        polys = [s for s in comp.footprint.shapes
                 if s.kind == "pad" and s.shape == "POLYGON"]
        if polys:
            nums = ", ".join(sorted(p.number for p in polys if p.number))
            comp.warnings.append(
                f"{len(polys)} polygon pad(s) ({nums}) have no native "
                f"equivalent in KiCad or Altium; they are emitted as their "
                f"bounding rectangle. Verify against the datasheet land "
                f"pattern before use.")
        slots = [s for s in comp.footprint.shapes
                 if s.kind == "pad" and s.is_slot]
        if slots:
            comp.warnings.append(
                f"{len(slots)} slotted hole(s) emitted with approximate "
                f"slot geometry; verify drill sizes.")
