# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Turn a :class:`FootprintModel` into what each backend's calls take.

Pure data, no bridge. The package footprint tools in ``tools/library.py``
and ``tools/easyeda.py`` send what these functions return, and
``core/kicad_footprint.py`` writes the KiCad file from the same model, so
every coordinate can be checked offline before anything reaches an
editor.

* :func:`model_from_package` reads the caller's package description and
  builds the model, refusing a bad description with the spec's own text.
* :func:`resolve_altium_layers` picks the library's assembly and
  courtyard layers by ROLE, the way ``lib_audit_footprint_policies``
  classifies them: the mechanical layer KIND first, then the layer name.
* :func:`altium_plan` / :func:`verify_altium_readback` are the Altium
  emitter and the check of what ``library.get_pad_geometry`` reads back.
* :func:`easyeda_plan` is the EasyEDA emitter, in mils.

Lengths stay in millimetres for Altium and KiCad. Altium takes them with
``units="mm"`` on the existing library calls, through the same
``format_length`` path the units work uses, so nothing is rounded to a
mil on the way.
"""

from __future__ import annotations

import json
import math
from dataclasses import fields as dc_fields
from typing import Any, Iterable, Optional

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.drafting import DEFAULT_RULES, DraftingRules
from eda_agent.design.landpattern.model import (
    Arc,
    Circle,
    FootprintModel,
    Line,
    Pad,
    Polygon,
    Text,
)
from eda_agent.design.landpattern.spec import PackageSpec, SpecError
from eda_agent.units import MILS_PER_MM, MM_PER_MIL

__all__ = [
    "ALTIUM_UNIT_MM",
    "LayerRoleError",
    "altium_plan",
    "easyeda_plan",
    "model_from_package",
    "package_spec",
    "resolve_altium_layers",
    "verify_altium_readback",
]

#: One Altium internal unit in mm: 1/10000 mil (Utils.pas MilsToCoordF).
ALTIUM_UNIT_MM = MM_PER_MIL / 10000.0


class LayerRoleError(ValueError):
    """No layer, or more than one, can be named for a graphic role."""


# --- the request -----------------------------------------------------------


def _drafting_rules(drafting: Optional[dict]) -> DraftingRules:
    if not drafting:
        return DEFAULT_RULES
    if not isinstance(drafting, dict):
        raise SpecError(f"drafting must be an object of rule overrides, got "
                        f"{type(drafting).__name__}")
    known = {f.name: f for f in dc_fields(DraftingRules)}
    unknown = sorted(set(drafting) - set(known))
    if unknown:
        raise SpecError(f"unknown drafting rules: {', '.join(unknown)}; known: "
                        f"{', '.join(sorted(known))}")
    kw: dict[str, Any] = {}
    for key, value in drafting.items():
        if key == "ep_land":
            if value not in ("max", "nom"):
                raise SpecError("drafting ep_land must be 'max' or 'nom'")
            kw[key] = value
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise SpecError(f"drafting {key} must be a number, got {value!r}")
        if not math.isfinite(number):
            raise SpecError(f"drafting {key} must be finite, got {value!r}")
        kw[key] = number
    base = {f: getattr(DEFAULT_RULES, f) for f in known}
    base.update(kw)
    return DraftingRules(**base)


def package_spec(package: Any) -> PackageSpec:
    """Read a package description: a :class:`PackageSpec`, its ``to_dict``
    form, or that form as JSON text. Raises :class:`SpecError`."""
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except json.JSONDecodeError as exc:
            raise SpecError(f"package is not valid JSON ({exc}); pass the "
                            "PackageSpec as an object or its JSON text")
    if isinstance(package, PackageSpec):
        spec = PackageSpec.from_dict(package.to_dict())
    elif isinstance(package, dict):
        if not package:
            raise SpecError("package is empty; give at least the family and "
                            "the dimensions that family needs")
        try:
            spec = PackageSpec.from_dict(package)
        except SpecError:
            raise
        except (TypeError, ValueError) as exc:
            raise SpecError(f"package could not be read: {exc}")
    else:
        raise SpecError(f"package must be an object (a PackageSpec as JSON), "
                        f"got {type(package).__name__}")
    return spec


def model_from_package(package: Any, *, density: str = "", name: str = "",
                       description: str = "",
                       drafting: Optional[dict] = None) -> FootprintModel:
    """Build the land pattern a package description asks for.

    ``package`` is a :class:`PackageSpec`, its ``to_dict`` form, or that
    form as JSON text. ``density`` (L, N or M) overrides the package's own;
    empty keeps it (N unless the package says otherwise). ``name`` and
    ``description`` override the package's. ``drafting`` overrides
    :class:`DraftingRules` fields (silk width, paste coverage and so on).

    Raises :class:`SpecError` with the spec's own text for anything the
    engine cannot use.
    """
    spec = package_spec(package)
    if name:
        spec.name = str(name)
    if description:
        spec.description = str(description)
    rules = _drafting_rules(drafting)
    try:
        return build_footprint(spec, density or None, rules)
    except SpecError:
        raise
    except ValueError as exc:
        # normalise_density and the dimension readers raise ValueError.
        raise SpecError(str(exc))


# --- layers by role ----------------------------------------------------------


def _role_hints():
    # The same substrings lib_audit_footprint_policies classifies by, so a
    # footprint this tool writes is attributed to the role it was written for.
    from eda_agent.design.footprint_policy import (
        _ASSEMBLY_HINTS,
        _COURTYARD_HINTS,
    )
    return {"assembly": _ASSEMBLY_HINTS, "courtyard": _COURTYARD_HINTS}


_ROLE_KIND = {"assembly": "assembly top", "courtyard": "courtyard top"}
_UNSET_KINDS = {"", "not set", "unknown"}


def _is_bottom(name: str) -> bool:
    words = name.lower().replace("_", " ").replace("-", " ").split()
    return "bottom" in name.lower() or "bot" in words


def resolve_altium_layers(mech_layers: Iterable[dict], *,
                          assembly_layer: str = "",
                          courtyard_layer: str = "") -> dict[str, dict]:
    """The layer for each graphic role, and the rule that chose it.

    Silkscreen is always Altium's top overlay and paste windows go on the
    top paste layer; those are fixed layers. The assembly and courtyard
    roles are mechanical layers whose numbers differ by house style, so
    each is chosen in this order:

    1. the layer the caller named;
    2. the enabled mechanical layer whose KIND is "Assembly Top" or
       "Courtyard Top", the signal ``lib_audit_footprint_policies`` treats
       as reliable;
    3. the enabled mechanical layer whose NAME carries the same hints that
       audit classifies by (``assembly``/``assy``/``fab``,
       ``courtyard``/``court``/``place bound``/``placement``), skipping
       bottom-side names and layers whose kind says they are something
       else; with several, the one naming "top".

    Anything else is refused with :class:`LayerRoleError`, listing what
    the library has, rather than guessing a mechanical number that means
    something else in this library.
    """
    layers = [dict(m) for m in mech_layers or [] if isinstance(m, dict)]
    hints = _role_hints()
    given = {"assembly": str(assembly_layer or "").strip(),
             "courtyard": str(courtyard_layer or "").strip()}
    out: dict[str, dict] = {
        "silk": {"layer": "TopOverlay", "rule": "fixed: Altium's top overlay"},
        "paste": {"layer": "TopPaste", "rule": "fixed: Altium's top paste"},
        "copper": {"layer": "TopLayer", "rule": "fixed: a land's outline"},
        "mask": {"layer": "TopSolder", "rule": "fixed: a land's outline"},
    }
    problems: list[str] = []
    for role in ("assembly", "courtyard"):
        if given[role]:
            out[role] = {"layer": given[role], "rule": "named by the caller"}
            continue
        by_kind = [m for m in layers
                   if str(m.get("kind") or "").strip().lower() == _ROLE_KIND[role]]
        if by_kind:
            m = by_kind[0]
            out[role] = {"layer": str(m.get("layer")),
                         "rule": f"mechanical layer kind {m.get('kind')!r}",
                         "name": m.get("name", "")}
            continue
        candidates = []
        for m in layers:
            name = str(m.get("name") or "")
            kind = str(m.get("kind") or "").strip().lower()
            if kind not in _UNSET_KINDS:
                continue
            if _is_bottom(name):
                continue
            if any(h in name.lower() for h in hints[role]):
                candidates.append(m)
        if len(candidates) > 1:
            top = [m for m in candidates if "top" in str(m.get("name", "")).lower()]
            if len(top) == 1:
                candidates = top
        if len(candidates) == 1:
            m = candidates[0]
            out[role] = {"layer": str(m.get("layer")),
                         "rule": f"mechanical layer name {m.get('name')!r}",
                         "name": m.get("name", "")}
            continue
        if candidates:
            problems.append(
                f"{role}: several layers could be it ("
                + ", ".join(f"{m.get('layer')} {m.get('name')!r}"
                            for m in candidates)
                + f"); pass {role}_layer")
        else:
            problems.append(
                f"{role}: no enabled mechanical layer has the kind "
                f"{_ROLE_KIND[role].title()!r} or a name containing "
                f"{'/'.join(hints[role])}")
    if not problems and out["assembly"]["layer"].lower() == \
            out["courtyard"]["layer"].lower():
        problems.append(
            f"assembly and courtyard both resolved to "
            f"{out['assembly']['layer']}; they are separate roles and need "
            "separate layers")
    if problems:
        seen = ", ".join(f"{m.get('layer')} {m.get('name')!r} kind "
                         f"{m.get('kind')!r}" for m in layers) or "none"
        raise LayerRoleError(
            "cannot choose the footprint layers by role: " + "; ".join(problems)
            + f". Enabled mechanical layers in this library: {seen}. Pass "
            "assembly_layer and courtyard_layer (for example Mechanical13 and "
            "Mechanical15), or give the layers a kind first with "
            "lib_set_mech_layers (kind \"Assembly Top\" / \"Courtyard Top\").")
    return out


# --- Altium ------------------------------------------------------------------

#: Model pad shape -> the shape word lib_add_footprint_pads takes. An oval
#: is Altium's round shape with unequal sizes.
_ALTIUM_SHAPE = {
    "roundrect": "roundrect", "rect": "rectangular",
    "rectangle": "rectangular", "rectangular": "rectangular",
    "circle": "round", "round": "round", "oval": "round",
    "ellipse": "round", "octagon": "octagonal", "octagonal": "octagonal",
}


def _altium_corner_pct(ratio: float) -> int:
    # The importer's conversion, so the two cannot drift: Altium's percent
    # is of HALF the short side, the model's ratio of the whole side.
    from eda_agent.libimport.easyeda.altium import _corner_radius_pct
    return _corner_radius_pct(ratio)


def _suppress_paste(p: Pad) -> bool:
    return p.kind == "smd" and (bool(p.paste_apertures) or not p.paste)


def _altium_pad(p: Pad) -> dict:
    shape_key = str(p.shape or "").strip().lower()
    shape = _ALTIUM_SHAPE.get(shape_key)
    if shape is None:
        raise SpecError(f"pad {p.number} has shape {p.shape!r}, which Altium "
                        f"cannot draw; use one of {', '.join(sorted(_ALTIUM_SHAPE))}")
    pct = 0
    if shape == "roundrect":
        pct = _altium_corner_pct(p.corner_ratio)
        if pct <= 0:
            shape = "rectangular"
    out: dict[str, Any] = {
        "designator": p.number,
        "x": p.x, "y": p.y,
        "x_size": p.width, "y_size": p.height,
        "hole_size": p.hole if p.kind != "smd" else 0,
        "shape": shape,
        "corner_radius": pct if shape == "roundrect" else 25,
        "rotation": p.rotation,
        "layer": "TopLayer" if p.kind == "smd" else "MultiLayer",
    }
    if p.kind != "smd" and p.hole_length > p.hole > 0:
        out["hole_length"] = p.hole_length
    if p.kind == "npth" or (p.kind != "smd" and not p.plated):
        out["plated"] = "false"
    if _suppress_paste(p):
        # Minus the larger side closes the opening whatever the shape, the
        # value PCB_MakePasteGrid writes before laying its windows.
        out["paste_expansion"] = -max(p.width, p.height)
    elif p.kind == "smd" and p.paste_expansion:
        out["paste_expansion"] = p.paste_expansion
    if p.mask_expansion:
        out["mask_expansion"] = p.mask_expansion
    return out


def _close(points: list) -> list:
    pts = [tuple(map(float, pt)) for pt in points]
    if len(pts) > 2 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _segments(points: list, closed: bool) -> list[tuple]:
    pts = _close(points) if closed else [tuple(map(float, pt)) for pt in points]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])
            if (a[0], a[1]) != (b[0], b[1])]


def _arc_angles(start: float, sweep: float) -> tuple[float, float]:
    """Altium arcs run counter-clockwise from start to end angle."""
    a0, a1 = (start, start + sweep) if sweep >= 0 else (start + sweep, start)
    a0 %= 360.0
    a1 = a0 + abs(sweep)
    if abs(sweep) < 360.0:
        a1 %= 360.0
    return round(a0, 6), round(a1, 6)


def altium_plan(model: FootprintModel, layers: dict[str, dict]) -> dict:
    """Everything the Altium library calls take, in mm.

    ``layers`` is :func:`resolve_altium_layers` output. Returns ``pads``
    (dicts for the pad payload), ``tracks``, ``regions`` (paste windows
    and any filled polygon), ``arcs``, ``texts``, ``height_mm`` and the
    ``notes`` a caller should read.
    """
    lay = {role: info["layer"] for role, info in layers.items()}
    pads = [_altium_pad(p) for p in model.pads]
    tracks: list[dict] = []
    regions: list[dict] = []
    arcs: list[dict] = []
    texts: list[dict] = []
    notes: list[str] = []

    def layer_of(role: str) -> str:
        if role not in lay:
            raise SpecError(f"no layer resolved for the {role!r} role")
        return lay[role]

    def add_polygon(g: Polygon, layer: str) -> None:
        if g.filled:
            regions.append({"layer": layer,
                            "points": [tuple(map(float, p)) for p in g.points]})
            return
        for x1, y1, x2, y2 in _segments(g.points, g.closed):
            tracks.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                           "width": g.width, "layer": layer})

    for g in list(model.graphics) + [model.courtyard]:
        layer = layer_of(g.layer)
        if isinstance(g, Line):
            tracks.append({"x1": g.x1, "y1": g.y1, "x2": g.x2, "y2": g.y2,
                           "width": g.width, "layer": layer})
        elif isinstance(g, Polygon):
            add_polygon(g, layer)
        elif isinstance(g, Circle):
            if g.filled:
                # A filled disc as an arc: radius r/2 drawn r wide covers
                # the centre out to r, the way a dot is drawn in Altium.
                width = max(g.r, g.width)
                arcs.append({"x_center": g.x, "y_center": g.y,
                             "radius": g.r / 2.0, "start_angle": 0,
                             "end_angle": 360, "width": width,
                             "layer": layer})
            else:
                arcs.append({"x_center": g.x, "y_center": g.y, "radius": g.r,
                             "start_angle": 0, "end_angle": 360,
                             "width": g.width, "layer": layer})
        elif isinstance(g, Arc):
            a0, a1 = _arc_angles(g.start, g.sweep)
            arcs.append({"x_center": g.x, "y_center": g.y, "radius": g.r,
                         "start_angle": a0, "end_angle": a1,
                         "width": g.width, "layer": layer})
        elif isinstance(g, Text):
            texts.append(_altium_text(g, g.text, layer))

    for p in model.pads:
        for a in p.paste_apertures:
            hw, hh = a.width / 2.0, a.height / 2.0
            regions.append({"layer": layer_of("paste"), "points": [
                (round(a.x - hw, 6), round(a.y - hh, 6)),
                (round(a.x + hw, 6), round(a.y - hh, 6)),
                (round(a.x + hw, 6), round(a.y + hh, 6)),
                (round(a.x - hw, 6), round(a.y + hh, 6))]})

    # The reference text: Altium's .Designator special string, which shows
    # the placed part's designator, on the assembly layer.
    ref = _altium_ref_text(model.ref, layer_of(model.ref.layer),
                           model.body.x1 - model.body.x0)
    texts.append(ref)
    notes.append(
        f"assembly designator: the '.Designator' placeholder centred on the "
        f"body at {ref['size']:g} mm, small enough for its width to fit the "
        f"body (not below {_REF_MIN_MM:g} mm); to change it, pass drafting "
        f"ref_height_max (and ref_height_min) in mm")

    suppressed = [p.number for p in model.pads if _suppress_paste(p)]
    if suppressed:
        notes.append(
            "paste: pad(s) " + ", ".join(suppressed) + " carry a manual paste "
            "expansion of minus their larger side, closing their own opening, "
            "and get their paste from the windows on " + layer_of("paste"))
    if not any("mask_expansion" in d for d in pads):
        notes.append(
            "solder mask: the model asks for 1:1 openings (IPC-7351B default), "
            "so no manual expansion is written and the library's design rule "
            "decides; set mask_expansion in drafting to write one")
    return {"pads": pads, "tracks": tracks, "regions": regions, "arcs": arcs,
            "texts": texts, "height_mm": model.body.z1, "notes": notes}


#: Advance of one character of Altium's default stroke font, per unit of
#: text height: '.Designator' at 0.9 mm measured 9.27 mm including a
#: 0.135 mm stroke, so 11 characters advance 10.15 heights.
_STROKE_ADVANCE = 10.15 / 11
_REF_MIN_MM = 0.30


def _altium_ref_text(t: Text, layer: str, body_width: float) -> dict:
    """The ``.Designator`` placeholder centred on ``(t.x, t.y)``: Altium
    anchors stroke text at its lower left, and the placeholder is eleven
    characters wide, so a body-sized height ran far past a small body."""
    string = ".Designator"
    h = float(t.height)
    fit = 0.9 * body_width / (_STROKE_ADVANCE * len(string)) if body_width > 0 else h
    h = max(_REF_MIN_MM, math.floor(min(h, fit) * 1000) / 1000)
    width = _STROKE_ADVANCE * h * len(string)
    return {"text": string, "x": round(t.x - width / 2.0, 6),
            "y": round(t.y - h / 2.0, 6), "size": h,
            "width": round(0.15 * h, 4), "layer": layer}


def _altium_text(t: Text, string: str, layer: str) -> dict:
    h = float(t.height)
    return {"text": string, "x": round(t.x - h / 2.0, 6),
            "y": round(t.y - h / 2.0, 6), "size": h,
            "width": float(t.thickness), "layer": layer}


def _expected_shape(sent: dict) -> str:
    return {"roundrect": "roundrectangle", "rectangular": "rectangular",
            "round": "round", "octagonal": "octagonal"}[sent["shape"]]


def verify_altium_readback(plan: dict, readback: Any, *,
                           tol: float = ALTIUM_UNIT_MM) -> dict:
    """Compare what ``library.get_pad_geometry`` read with what was sent.

    Pad count, and per pad the centre, size, rotation, shape, corner
    percentage, hole, slot length, plating and any manual paste or mask
    expansion that was written. Centres and sizes must agree within one
    Altium internal unit (``tol``, mm). Any difference is a mismatch and
    makes ``ok`` false: a footprint that read back differently is not the
    footprint that was asked for.
    """
    sent = list(plan.get("pads") or [])
    if not isinstance(readback, dict) or not isinstance(readback.get("pads"), list):
        return {"ok": False, "reason": "the readback carried no pad list",
                "expected_pads": len(sent)}
    read = [p for p in readback["pads"] if isinstance(p, dict)]
    mismatches: list[dict] = []
    if len(read) != len(sent):
        mismatches.append({"check": "pad_count", "expected": len(sent),
                           "read": len(read)})
    unused = list(range(len(read)))

    def num(d: dict, key: str) -> float:
        try:
            return float(d.get(key) or 0.0)
        except (TypeError, ValueError):
            return float("nan")

    for s in sent:
        cands = [i for i in unused if str(read[i].get("name")) == str(s["designator"])]
        if not cands:
            mismatches.append({"check": "pad_missing", "pad": s["designator"],
                               "expected": [s["x"], s["y"]]})
            continue
        i = min(cands, key=lambda k: math.hypot(num(read[k], "x_mm") - s["x"],
                                                num(read[k], "y_mm") - s["y"]))
        unused.remove(i)
        r = read[i]
        pad = s["designator"]

        def differ(check: str, want: float, got: float, limit: float = tol):
            if not (abs(want - got) <= limit):
                mismatches.append({"check": check, "pad": pad,
                                   "expected": want, "read": got})

        differ("x", s["x"], num(r, "x_mm"))
        differ("y", s["y"], num(r, "y_mm"))
        differ("width", s["x_size"], num(r, "w_mm"))
        differ("height", s["y_size"], num(r, "h_mm"))
        rot_d = (num(r, "rotation") - float(s["rotation"])) % 360.0
        if min(rot_d, 360.0 - rot_d) > 1e-6:
            mismatches.append({"check": "rotation", "pad": pad,
                               "expected": s["rotation"],
                               "read": r.get("rotation")})
        want_shape = _expected_shape(s)
        if str(r.get("shape")) != want_shape:
            mismatches.append({"check": "shape", "pad": pad,
                               "expected": want_shape, "read": r.get("shape")})
        if want_shape == "roundrectangle" and \
                int(num(r, "corner_pct")) != int(s["corner_radius"]):
            mismatches.append({"check": "corner_pct", "pad": pad,
                               "expected": s["corner_radius"],
                               "read": r.get("corner_pct")})
        differ("hole", float(s["hole_size"]), num(r, "hole_mm"))
        if "hole_length" in s:
            if str(r.get("hole_type")) != "slot":
                mismatches.append({"check": "hole_type", "pad": pad,
                                   "expected": "slot", "read": r.get("hole_type")})
            differ("slot_length", float(s["hole_length"]), num(r, "hole_w_mm"))
        want_layer = "top" if s["layer"] == "TopLayer" else "multi"
        if str(r.get("layer")) != want_layer:
            mismatches.append({"check": "layer", "pad": pad,
                               "expected": want_layer, "read": r.get("layer")})
        want_plated = s.get("plated") != "false"
        if float(s["hole_size"]) > 0 and bool(r.get("plated", True)) != want_plated:
            mismatches.append({"check": "plated", "pad": pad,
                               "expected": want_plated, "read": r.get("plated")})
        for key, label in (("paste_expansion", "paste"),
                           ("mask_expansion", "mask")):
            if key not in s:
                continue
            if str(r.get(f"{key}_source")) != "manual":
                mismatches.append({"check": f"{label}_expansion_source",
                                   "pad": pad, "expected": "manual",
                                   "read": r.get(f"{key}_source")})
            differ(f"{label}_expansion", float(s[key]), num(r, f"{key}_mm"))
    for i in unused:
        mismatches.append({"check": "pad_extra", "pad": read[i].get("name"),
                           "read": [read[i].get("x_mm"), read[i].get("y_mm")]})
    return {"ok": not mismatches, "pads_expected": len(sent),
            "pads_read": len(read), "tolerance_mm": tol,
            "mismatches": mismatches}


# --- EasyEDA -------------------------------------------------------------------

#: Model pad shape -> EasyEDA's pad shape names (the extension's padShape).
_EASYEDA_SHAPE = {
    "roundrect": "RECTANGLE", "rect": "RECTANGLE", "rectangle": "RECTANGLE",
    "rectangular": "RECTANGLE", "circle": "ELLIPSE", "round": "ELLIPSE",
    "ellipse": "ELLIPSE", "oval": "OBLONG",
}

#: Graphic roles -> EasyEDA layer names. EasyEDA has fixed assembly and
#: silkscreen layers; the courtyard defaults to DOCUMENT, where
#: easyeda_create_standard_footprint already puts it.
EASYEDA_LAYERS = {"silk": "TOP_SILKSCREEN", "assembly": "TOP_ASSEMBLY",
                  "courtyard": "DOCUMENT", "paste": "TOP_PASTE_MASK"}


def _mil(v: float) -> float:
    return round(float(v) * MILS_PER_MM, 6)


def _circle_points(x: float, y: float, r: float, n: int = 24) -> list[list[float]]:
    return [[_mil(x + r * math.cos(2 * math.pi * k / n)),
             _mil(y + r * math.sin(2 * math.pi * k / n))] for k in range(n)]


def _chain_lines(lines: list[Line]) -> list[tuple[list[list[float]], float]]:
    """Consecutive touching silk lines of one width as one polyline each."""
    runs: list[tuple[list[list[float]], float]] = []
    for g in lines:
        a, b = [_mil(g.x1), _mil(g.y1)], [_mil(g.x2), _mil(g.y2)]
        w = _mil(g.width)
        if runs and runs[-1][1] == w and runs[-1][0][-1] == a:
            runs[-1][0].append(b)
        else:
            runs.append(([a, b], w))
    return runs


def easyeda_plan(model: FootprintModel, *, courtyard_layer: str = "") -> dict:
    """Everything the EasyEDA calls take, in mils (the PCB canvas unit).

    Returns ``pads`` (for ``easyeda_add_pads``), ``polylines``, ``fills``
    (paste windows and the pin 1 dot), ``arcs``, ``texts``, ``unsupported``
    (model features the extension cannot express, which the tool refuses
    on) and ``notes``.
    """
    lay = dict(EASYEDA_LAYERS)
    if courtyard_layer:
        lay["courtyard"] = str(courtyard_layer).strip().upper()
    pads: list[dict] = []
    unsupported: list[str] = []
    notes: list[str] = []
    for p in model.pads:
        shape = _EASYEDA_SHAPE.get(str(p.shape or "").strip().lower())
        if shape is None:
            unsupported.append(f"pad {p.number}: shape {p.shape!r}")
            continue
        if p.kind == "npth" or (p.kind != "smd" and not p.plated):
            unsupported.append(
                f"pad {p.number}: a non-plated hole; the extension's pad call "
                "passes no plating flag, so it would come out plated")
        entry = {
            "pad_number": p.number, "x": _mil(p.x), "y": _mil(p.y),
            "width": _mil(p.width), "height": _mil(p.height),
            "shape": shape, "rotation": float(p.rotation),
            "layer": "TOP" if p.kind == "smd" else "MULTI",
            "hole_diameter": _mil(p.hole) if p.kind != "smd" else 0.0,
            "hole_length": (_mil(p.hole_length)
                            if p.kind != "smd" and p.hole_length > p.hole else 0.0),
            "corner_radius": _mil(p.corner_radius) if shape == "RECTANGLE" else 0.0,
        }
        pads.append(entry)
        if p.mask_expansion or (p.kind == "smd" and p.paste_expansion):
            unsupported.append(
                f"pad {p.number}: a manual paste or mask expansion; the "
                "extension's pad call passes none")
    polylines: list[dict] = []
    fills: list[dict] = []
    arcs: list[dict] = []
    texts: list[dict] = []
    silk_lines = [g for g in model.graphics if isinstance(g, Line)]
    by_layer: dict[str, list[Line]] = {}
    for g in silk_lines:
        by_layer.setdefault(g.layer, []).append(g)
    for role, group in by_layer.items():
        for pts, w in _chain_lines(group):
            polylines.append({"points": pts, "layer": lay[role], "width": w})

    def ring(points: list, layer: str, width: float, closed: bool) -> None:
        pts = [[_mil(x), _mil(y)] for x, y in points]
        polylines.append({"points": pts, "layer": layer, "width": _mil(width)})
        if closed and len(pts) > 2:
            # The extension drops a repeated closing point, so the closing
            # edge goes as its own two-point run.
            polylines.append({"points": [pts[-1], pts[0]], "layer": layer,
                              "width": _mil(width)})

    for g in list(model.graphics) + [model.courtyard]:
        layer = lay[g.layer]
        if isinstance(g, Polygon):
            if g.filled:
                fills.append({"points": [[_mil(x), _mil(y)] for x, y in g.points],
                              "layer": layer})
            else:
                ring(g.points, layer, g.width, g.closed)
        elif isinstance(g, Circle):
            if g.filled:
                fills.append({"points": _circle_points(g.x, g.y, g.r),
                              "layer": layer})
            else:
                for a in (0.0, 180.0):
                    s = math.radians(a)
                    e = math.radians(a + 180.0)
                    arcs.append({
                        "start_x": _mil(g.x + g.r * math.cos(s)),
                        "start_y": _mil(g.y + g.r * math.sin(s)),
                        "end_x": _mil(g.x + g.r * math.cos(e)),
                        "end_y": _mil(g.y + g.r * math.sin(e)),
                        "angle": 180.0, "layer": layer, "width": _mil(g.width)})
        elif isinstance(g, Arc):
            s = math.radians(g.start)
            e = math.radians(g.start + g.sweep)
            arcs.append({"start_x": _mil(g.x + g.r * math.cos(s)),
                         "start_y": _mil(g.y + g.r * math.sin(s)),
                         "end_x": _mil(g.x + g.r * math.cos(e)),
                         "end_y": _mil(g.y + g.r * math.sin(e)),
                         "angle": float(g.sweep), "layer": layer,
                         "width": _mil(g.width)})
        elif isinstance(g, Text):
            texts.append({"text": g.text, "x": _mil(g.x), "y": _mil(g.y),
                          "font_size": _mil(g.height),
                          "width": _mil(g.thickness), "layer": layer,
                          "align": "CENTER"})
    for p in model.pads:
        for a in p.paste_apertures:
            hw, hh = a.width / 2.0, a.height / 2.0
            fills.append({"layer": lay["paste"], "points": [
                [_mil(a.x - hw), _mil(a.y - hh)], [_mil(a.x + hw), _mil(a.y - hh)],
                [_mil(a.x + hw), _mil(a.y + hh)], [_mil(a.x - hw), _mil(a.y + hh)]]})
    r = model.ref
    texts.append({"text": r.text, "x": _mil(r.x), "y": _mil(r.y),
                  "font_size": _mil(r.height), "width": _mil(r.thickness),
                  "layer": lay[r.layer], "align": "CENTER"})
    windowed = [p.number for p in model.pads if p.paste_apertures]
    if windowed:
        notes.append(
            "paste: pad(s) " + ", ".join(windowed) + " get paste windows on "
            f"{lay['paste']}, but the extension's pad call cannot close the "
            "pad's own paste opening, so until that pad's paste expansion is "
            "set to minus its larger side in the editor the windows sit "
            "inside a full opening")
    notes.append(
        "reference text: the literal model text " + repr(r.text) + " on "
        f"{lay[r.layer]}; EasyEDA's designator placeholder for footprint text "
        "is unmeasured")
    notes.append(
        "coordinates: mils, Y up as the model is, with no flip; whether the "
        "footprint canvas is Y up has not been measured live")
    notes.append("3D body: not drawn; the extension has no body or height call")
    return {"pads": pads, "polylines": polylines, "fills": fills,
            "arcs": arcs, "texts": texts, "layers": lay,
            "unsupported": unsupported, "notes": notes}
