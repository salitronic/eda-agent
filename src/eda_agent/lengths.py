# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Lengths in mils or millimetres, the same rule on every tool.

Each Claude session works in one unit, ``mil`` or ``mm``. It starts in the
unit chosen on the Config tab (``length_units`` in preferences.json),
changes it with the ``length_units`` tool, and any one call can use
another by giving ``length_units`` beside its arguments. The setting is
the session's, not the server's: one Claude session runs several
eda-agent servers, and several sessions share one Altium, so it is kept
in the workspace under the session tag every server of that session
shares.

The rule, for every tool on every backend:

* A length whose name carries no unit (``x``, ``width``, ``hole_size``)
  is given and returned in the session's unit.
* A name that carries its unit always means that unit: ``thickness_mils``
  is mils whatever the session's unit, and ``thickness_mm`` may be given
  in its place. In a reply such a key is renamed to the session's unit
  (``thickness_mils`` comes back as ``thickness_mm`` in a mm session).
* Every reply that is an object says ``"length_units"``.

The conversion happens here, at the boundary between the client and the
tools: the tools themselves keep working in their own unit (mils, or mm
on the KiCad tools), so nothing inside the server changes meaning.

Which plain names are lengths is declared, never guessed from a name
alone: ``width`` is mils on a track and pixels on a rendered picture,
``offset`` is a page offset on a listing. ``LENGTH_KEYS`` is the
vocabulary; ``NOT_LENGTHS`` and ``ADDED`` correct it tool by tool, and a
test fails any numeric argument no list accounts for.

A tool that takes a length as a whole number of mils (an ``int``
argument) is handed the converted value rounded to the nearest mil:
1 mm reaches it as 39 mils, where 39.37 would be refused as not a whole
number.
"""

from __future__ import annotations

import contextvars
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .units import MILS_PER_MM, MM_PER_MIL, format_length, normalise_units

#: The argument a call gives to use another unit for itself alone.
OVERRIDE = "length_units"

#: Plain names that are lengths wherever they appear, in arguments and in
#: replies, at any depth.
LENGTH_KEYS = frozenset({
    # Positions and points.
    "x", "y", "x1", "y1", "x2", "y2", "x3", "y3", "cx", "cy", "dx", "dy",
    "x_center", "y_center", "center_x", "center_y", "centre_x", "centre_y",
    "start_x", "start_y", "end_x", "end_y", "mid_x", "mid_y",
    "from_x", "from_y", "to_x", "to_y", "source_x", "source_y",
    "reference_x", "reference_y", "location_x", "location_y",
    "directive_x", "directive_y", "origin_x", "origin_y",
    "offset_x", "offset_y", "offset_z",
    "xmin", "xmax", "ymin", "ymax", "min_x", "max_x", "min_y", "max_y",
    "left_x", "right_x", "top_y", "bottom_y",
    # Sizes.
    "width", "height", "w", "h", "radius", "min_radius", "max_radius", "diameter",
    "x_size", "y_size", "bottom_x_size", "bottom_y_size", "mid_x_size",
    "mid_y_size", "top_x_size", "top_y_size",
    "hole_size", "hole_width", "hole_length", "hole_diameter", "dia_bottom",
    "dia_mid", "dia_top", "diameter_top", "diameter_mid", "diameter_bottom",
    "pad_w", "pad_h", "pad_width", "pad_height", "body_w", "body_h",
    "body_width", "body_height", "tab_w", "tab_h", "tab_width", "tab_height",
    "row_span", "courtyard", "pitch", "pin_pitch", "pin_length", "entry_pitch",
    "length", "add_length", "amplitude", "thickness",
    "clearance", "gap", "spacing", "minimum_ring", "max_uncoupled_length",
    "margin_width", "custom_margin_width", "custom_width", "custom_height",
    "frame_width", "frame_height", "text_height", "stroke",
    "standoff", "standoff_height", "overall_height", "distance_from_top",
    "expansion", "mask_expansion", "paste_expansion", "mask_bottom_expansion",
    "grid", "grid_size", "snap_grid", "hotspot_grid", "snap_x", "snap_y",
    "step_x", "step_y", "radial_step", "symbol_size", "cutter_fixed_length",
    "pin_name_margin", "pin_number_margin", "ignore_below",
    # A package, as a datasheet draws it (design/landpattern/spec.py).
    "pitch_y", "row_pitch", "body_x", "body_y", "lead_span_x", "lead_span_y",
    "lead_length", "lead_width", "lead_diameter", "lead_thickness",
    "ep_x", "ep_y", "ep_offset", "exposed_pad", "ball_diameter",
    "pad_length", "span", "span_y", "land_diameter", "hole",
})

#: Names under which every number is a coordinate or a size: a list of
#: points, a box as a list or a dict of its edges.
POINT_KEYS = frozenset({
    "points", "vertices", "path", "outline", "polygon", "corners",
    "bounds", "bbox", "box", "rect", "extent", "board_bounds",
    "location", "position", "center", "centre", "origin", "at", "pos", "xy",
})

#: A unit in a name: what it says, and the stem before it.
_SUFFIX = re.compile(r"^(?P<stem>.+)_(?P<unit>mils|mil|mm)$")

#: A name with a unit in it that is not a length, or not a linear one: an
#: area scales with the square, and is left as its name says.
_NOT_LINEAR = re.compile(r"area|_sq|square|per_")

#: Inside a container of lengths (a point list, a box, a package), the
#: numbers that are not lengths, by their names.
_NOT_IN_LENGTHS = re.compile(
    r"angle|rotat|deg|sweep|bulge|count|index|^n$|_n$|^id$|_id$|kind|type|"
    r"layer|net|ratio|coverage|^pins|^rows$|^cols$|page|number|density|"
    r"percent|pct|factor|scale|weight|segments|sides")

#: Arguments that are documents of lengths in a unit of their own, mm on
#: every tool: a package as a datasheet draws it, the drafting rules, the
#: drawing's totals. Given as an object or as its JSON text.
LENGTH_DOCUMENTS: dict[str, str] = {
    "package": "mm", "drafting": "mm", "drawing": "mm",
    # A land pattern transcribed from a datasheet; a 3D body's solids.
    "spec_json": "mm", "solids": "mm",
}

#: Tools that take or give no lengths: screen pixels, pictures, the tool
#: surface itself. Their arguments and replies pass through as they are.
NO_LENGTHS_PREFIXES = ("app_", "tool_", "server_", "datasheet_", "sim_")
NO_LENGTHS = frozenset({
    "length_units", "design_visual_review", "proj_export_image",
    "pcb_render_3d", "pcb_render_png", "pcb_render_svg", "sch_render_svg",
    "lib_render_footprint", "footprint_render_package", "design_live_view",
    "design_live_snapshot", "easyeda_render_image", "kicad_get_board_view",
})

#: Plain names in LENGTH_KEYS that are not lengths in one tool, with what
#: they are there.
NOT_LENGTHS: dict[str, frozenset[str]] = {
    # Degrees covered.
    "pcb_place_ring": frozenset({"span"}),
    # Line width as a size code, 0 to 3 (a line's own, in ``lines``).
    "lib_add_symbol_arc": frozenset({"width"}),
    "lib_add_symbol_lines": frozenset({"width"}),
    # Font size in points.
    "obj_get_font_id": frozenset({"size"}),
    # Percent.
    "pcb_impedance_profile": frozenset({"tolerance"}),
}

#: Names outside LENGTH_KEYS that are lengths in one tool.
ADDED: dict[str, frozenset[str]] = {
    "lib_add_footprint_text": frozenset({"size"}),
    "lib_add_footprint_via": frozenset({"size"}),
    "lib_add_footprint_vias": frozenset({"size"}),
    "lib_set_via_properties": frozenset({"size"}),
    "pcb_place_via": frozenset({"size"}),
    "pcb_place_via_array": frozenset({"size"}),
    "pcb_place_vias": frozenset({"size"}),
    "pcb_place_text": frozenset({"margin"}),
    "sch_place_text_frame": frozenset({"text_margin"}),
    "pcb_set_grids": frozenset({"track", "via", "visible", "visible_big",
                                "component_x", "component_y"}),
    "easyeda_add_text": frozenset({"font_size"}),
    "easyeda_mark_findings": frozenset({"line_width"}),
    "easyeda_audit_dangling_track_ends": frozenset({"tolerance"}),
    "easyeda_add_schematic_rectangle": frozenset({"corner_radius"}),
    "easyeda_create_standard_footprint": frozenset({"corner_radius"}),
    "easyeda_add_pad": frozenset({"corner_radius"}),
    "pcb_view": frozenset({"left", "bottom", "right", "top"}),
    # An arc's centre and radius, a text's height.
    "lib_get_footprint_primitives": frozenset({"xc", "yc", "r", "size"}),
    # Dimensions: the line / callout distance, a center's size, the
    # callout place and a baseline's line positions.
    "pcb_create_dimension": frozenset({"offset", "size", "line_width"}),
    "pcb_edit_dimension": frozenset({"offset", "size", "line_width"}),
    "pcb_get_dimensions": frozenset({"text_x", "text_y", "size", "line_width",
                                     "text_locations"}),
    # Containers of lengths keyed by name: pin, net, constraint.
    "easyeda_emit_template_connections": frozenset({"pin_positions"}),
    "pcb_calc_length_match": frozenset({"lengths"}),
    "kicad_set_design_rules": frozenset({"constraints"}),
}

#: Lists of vertices ``[x, y]`` or ``[x, y, sweep]``, by tool and name (a
#: name with a unit by its stem): x and y are lengths, the sweep is
#: degrees. A vertex given as a dict says which is which by its keys.
ARC_VERTICES: dict[str, frozenset[str]] = {
    "pcb_place_region": frozenset({"points", "shape"}),
    "lib_get_footprint_primitives": frozenset({"shape"}),
}

#: Tools that take their list inline or as a JSON ``file``, by the name of
#: the list. The file is read before the lengths are converted.
FILE_LISTS: dict[str, str] = {
    "pcb_place_tracks": "tracks",
    "pcb_place_vias": "vias",
}

#: Arguments that are lengths written as text, numbers joined by commas
#: (``"x1,y1,x2,y2"``), by tool.
LENGTH_STRINGS: dict[str, frozenset[str]] = {
    "lib_add_symbol_polygon": frozenset({"vertices"}),
    "pcb_apply_geometry_map": frozenset({"bounds"}),
}

#: Names with a unit that a tool takes inside its lists, in the one
#: spelling it takes them: the same length given in the other unit is
#: renamed and converted to it.
NESTED_NAMES: dict[str, frozenset[str]] = {
    # The guard a lib_get_pad_geometry read gives (x_mils in a mil session).
    "lib_set_pad_geometry": frozenset({"x_mm", "y_mm"}),
    "pcb_place_vias": frozenset({"size_mm", "hole_size_mm"}),
}

#: Tools whose ``value`` arguments are a length for some kinds only.
_RULE_VALUES = ("value", "max_value", "favored_value")
_RULES_NOT_LENGTHS = ("signal_impedance", "flight_time_rising",
                      "flight_time_falling", "vias_under_smd",
                      "polygon_connect", "plane_connect")


def _rule_value_lengths(args: dict) -> frozenset[str]:
    kind = str(args.get("rule_type", "")).strip().lower()
    return frozenset() if kind in _RULES_NOT_LENGTHS else frozenset(_RULE_VALUES)


#: Names that are lengths depending on the call's other arguments.
CONDITIONAL: dict[str, Callable[[dict], frozenset[str]]] = {
    "pcb_create_design_rule": _rule_value_lengths,
}

#: Tools that take a ``units`` argument of their own and convert their
#: lengths themselves. They are handed the session's unit and their plain
#: arguments are left alone.
SELF_CONVERTING = frozenset({
    "lib_add_footprint_arc", "lib_add_footprint_bodies", "lib_add_footprint_fills",
    "lib_add_footprint_pad", "lib_add_footprint_pads", "lib_add_footprint_regions",
    "lib_add_footprint_text", "lib_add_footprint_track", "lib_add_footprint_tracks",
    "lib_add_footprint_via", "lib_add_footprint_vias", "lib_redraw_assembly",
    "lib_set_3d_body", "lib_set_pad_geometry", "lib_set_pad_properties",
    "lib_set_via_properties", "pcb_place_pad", "pcb_place_pads",
})

#: The tools whose own unit is the millimetre; every other tool's is the
#: mil. KiCad's tools work in its unit; the land-pattern tools in the
#: datasheet's, on every backend.
MM_NATIVE_PREFIXES = ("kicad_", "footprint_")
MM_NATIVE = frozenset({
    "lib_create_footprint_from_package", "lib_audit_footprint_vs_datasheet",
    "lib_audit_footprint_vs_package", "lib_create_3d_model",
    "lib_export_kicad_symbol", "lib_export_kicad_footprint",
    "lib_get_footprint_primitives", "lib_select_footprint_primitives",
    "easyeda_create_footprint_from_package", "easyeda_audit_footprint_vs_datasheet",
    "easyeda_audit_footprint_vs_package",
})


def native_unit(tool: str) -> str:
    """The unit ``tool`` works in itself."""
    return "mm" if tool in MM_NATIVE or tool.startswith(MM_NATIVE_PREFIXES) else "mil"


def converts(tool: str) -> bool:
    """Whether ``tool``'s arguments and replies carry lengths at all."""
    return tool not in NO_LENGTHS and not tool.startswith(NO_LENGTHS_PREFIXES)


def plain_lengths(tool: str, args: Optional[dict] = None) -> frozenset[str]:
    """The plain names that are lengths in ``tool`` (for this call)."""
    keys = (LENGTH_KEYS - NOT_LENGTHS.get(tool, frozenset())) | ADDED.get(tool, frozenset())
    rule = CONDITIONAL.get(tool)
    if rule is not None:
        keys = keys | rule(args or {})
    return keys


def _factor(src: str, dst: str) -> float:
    if src == dst:
        return 1.0
    return MM_PER_MIL if src == "mil" else MILS_PER_MM


def _unit_of(suffix: str) -> str:
    return "mm" if suffix == "mm" else "mil"


def _numbers(value: Any) -> bool:
    """Whether ``value`` is a number or a list made only of numbers."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, list) and bool(value) and all(_numbers(v) for v in value)


def _all_numbers(value: Any) -> bool:
    """Numbers, or a list or dict whose every leaf is a number."""
    if isinstance(value, dict):
        return bool(value) and all(_all_numbers(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and all(_all_numbers(v) for v in value)
    return _numbers(value)


def _scale(value: Any, k: float) -> Any:
    """``value`` times ``k``: a number, or a list or dict of numbers."""
    if k == 1.0 or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _tidy(value * k)
    if isinstance(value, list):
        return [_scale(v, k) for v in value]
    if isinstance(value, dict):
        return {key: _scale(v, k) for key, v in value.items()}
    return value


def _tidy(v: float) -> float:
    """A converted number without float noise: 25.4 mm is 1000 mils, not
    999.9999999999999. Altium's own resolution is 1e-4 mil, so six
    decimals of a mil, or of a millimetre, lose nothing."""
    r = round(v, 6)
    return int(r) if r == int(r) and abs(r) < 1e15 else r


def _suffix_for(unit: str, spelled: str = "mils") -> str:
    """The suffix that names ``unit``, in the spelling a name used."""
    if unit == "mm":
        return "mm"
    return "mil" if spelled == "mil" else "mils"


#: Inside a point or a box, the edges are lengths too.
_EDGES = frozenset({"left", "right", "top", "bottom", "l", "r", "t", "b"})


def _vertices(value: Any, k: float, walk: Callable[[dict], Any]) -> Any:
    """A list of vertices, ``[x, y]`` or ``[x, y, sweep]``, scaled by
    ``k``: the sweep is degrees and stays as it is. A list of such lists
    (holes) is taken the same way; a vertex given as a dict goes to
    ``walk``, which knows its sweep by name."""
    if not isinstance(value, list):
        return value
    out = []
    for v in value:
        if isinstance(v, list) and v and all(
                isinstance(n, (int, float)) and not isinstance(n, bool) for n in v):
            out.append(_scale(v[:2], k) + v[2:])
        elif isinstance(v, list):
            out.append(_vertices(v, k, walk))
        elif isinstance(v, dict):
            out.append(walk(v))
        else:
            out.append(v)
    return out


def _walk(obj: Any, keys: frozenset[str], k: float, name_for: Callable[[str, "re.Match"], str],
          in_points: bool = False, arcs: frozenset[str] = frozenset()) -> Any:
    """``obj`` with its lengths scaled by ``k`` and its names with a unit
    renamed by ``name_for`` (converted to the unit the new name says).
    ``arcs`` names the lists of vertices that may carry a sweep."""
    if isinstance(obj, list):
        if in_points and _numbers(obj):
            return _scale(obj, k)
        return [_walk(v, keys, k, name_for, in_points, arcs) for v in obj]
    if not isinstance(obj, dict):
        return obj
    here = keys | _EDGES if in_points else keys
    out: dict[str, Any] = {}
    renamed: dict[str, Any] = {}

    def vertex(v: dict) -> Any:
        return _walk(v, keys, k, name_for, True, arcs)

    for key, value in obj.items():
        m = _SUFFIX.match(key) if isinstance(key, str) else None
        if m and not _NOT_LINEAR.search(m.group("stem")):
            new = name_for(key, m)
            if new != key and new not in obj and _all_numbers(value):
                f = _factor(_unit_of(m.group("unit")), _unit_of(new.rsplit("_", 1)[1]))
                renamed[new] = _vertices(value, f, vertex) \
                    if m.group("stem") in arcs else _scale(value, f)
                continue
            # The name says the unit of all it holds.
            out[key] = value
        elif key in arcs and isinstance(value, list):
            out[key] = _vertices(value, k, vertex)
        elif key in POINT_KEYS and isinstance(value, (list, dict)):
            # A list or a box; a bare number under such a name is not a
            # point (``points`` is a sample count on a simulation).
            out[key] = _scale(value, k) if _numbers(value) \
                else _walk(value, keys, k, name_for, True, arcs)
        elif key in here and _numbers(value):
            out[key] = _scale(value, k)
        elif key in here and isinstance(value, (list, dict)):
            # A range ({min, nom, max}), a pair, or lengths keyed by name.
            out[key] = _walk(value, keys, k, name_for, True, arcs)
        elif in_points and not (isinstance(key, str) and _NOT_IN_LENGTHS.search(key)):
            out[key] = _scale(value, k) if _numbers(value) \
                else _walk(value, keys, k, name_for, True, arcs)
        else:
            out[key] = _walk(value, keys, k, name_for, False, arcs)
    out.update(renamed)
    return out


def _length_text(text: str, k: float) -> str:
    """Lengths written as numbers joined by commas, scaled by ``k``; text
    that is not that is left as it is, for the tool to refuse."""
    if k == 1.0:
        return text
    try:
        return ",".join(format_length(_tidy(float(part) * k))
                        for part in text.split(","))
    except ValueError:
        return text


def _whole_mils(converted: dict, given: dict, whole: frozenset[str]) -> dict:
    """``converted`` with each whole-number length that the conversion
    made fractional rounded to the nearest whole unit; a fraction the
    call gave itself is left for the tool to refuse."""
    for key in whole:
        value = converted.get(key)
        if isinstance(value, float) and given.get(key) != value:
            converted[key] = int(round(value))
    return converted


def _named(target: str) -> Callable[[str, "re.Match"], str]:
    """Rename a name with a unit to ``target``'s, in its own spelling."""
    def name_for(key: str, m: "re.Match") -> str:
        if _unit_of(m.group("unit")) == target:
            return key
        return f"{m.group('stem')}_{_suffix_for(target, m.group('unit'))}"
    return name_for


def _taken(params: set, native: str) -> Callable[[str, "re.Match"], str]:
    """Rename a name with a unit to the spelling the tool takes; below
    the top level, to the tool's own unit."""
    below = _named(native)

    def name_for(key: str, m: "re.Match") -> str:
        if key in params:
            return key
        for spelled in ("mils", "mil", "mm"):
            if f"{m.group('stem')}_{spelled}" in params:
                return f"{m.group('stem')}_{spelled}"
        return below(key, m)
    return name_for


def args_in(tool: str, args: dict, unit: str, params: Iterable[str],
            _item: bool = False, whole: Iterable[str] = ()) -> dict:
    """A call's arguments, given in ``unit``, as ``tool`` takes them.
    ``items`` are each converted as a call of their own. ``whole`` names
    the arguments the tool takes as whole numbers."""
    if not converts(tool) or not isinstance(args, dict):
        return args
    names = set(params)
    whole = frozenset(whole)
    native = native_unit(tool)
    taken = _taken(names | NESTED_NAMES.get(tool, frozenset()), native)
    out = {}
    for key, value in args.items():
        if key == "items" and isinstance(value, list) and not _item:
            out[key] = [args_in(tool, v, unit, names, _item=True, whole=whole)
                        if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    if tool in SELF_CONVERTING:
        # Given beside the items, the unit applies to every one of them.
        if "units" in names and "units" not in out and not _item:
            out["units"] = unit
        return _whole_mils(_walk(out, frozenset(), 1.0, taken), out, whole)
    items = out.pop("items", None)
    documents = {key: _document(out.pop(key), unit, LENGTH_DOCUMENTS[key])
                 for key in list(out) if key in LENGTH_DOCUMENTS}
    k = _factor(unit, native)
    converted = _walk(out, plain_lengths(tool, out), k, taken,
                      arcs=ARC_VERTICES.get(tool, frozenset()))
    for key in LENGTH_STRINGS.get(tool, frozenset()):
        if isinstance(out.get(key), str):
            converted[key] = _length_text(out[key], k)
    converted = _whole_mils(converted, out, whole)
    converted.update(documents)
    if items is not None:
        converted["items"] = items
    return converted


def _document(value: Any, unit: str, own: str) -> Any:
    """A document of lengths, given in ``unit``, in its own unit; JSON
    text stays text."""
    text = isinstance(value, str)
    if text:
        try:
            body = json.loads(value)
        except ValueError:
            return value
    else:
        body = value
    if not isinstance(body, (dict, list)):
        return value
    out = _walk(body, frozenset(), _factor(unit, own), _named(own), in_points=True)
    return json.dumps(out) if text else out


def reply_in(tool: str, reply: Any, unit: str, args: Optional[dict] = None) -> Any:
    """``tool``'s reply, in its own unit, given in ``unit``."""
    if not converts(tool):
        return reply
    source = native_unit(tool)
    if tool in SELF_CONVERTING:
        # They work in the unit they were given, replies included.
        source = normalise_units((args or {}).get("units", "")) or source
    out = _walk(reply, plain_lengths(tool, args), _factor(source, unit), _named(unit),
                arcs=ARC_VERTICES.get(tool, frozenset()))
    if isinstance(out, dict):
        out = {**out, OVERRIDE: unit}
    return out


def unknown_unit_names(args: dict, params: Iterable[str]) -> list[str]:
    """Names with a unit that the tool takes in no spelling, which the
    server would otherwise drop without a word."""
    names = set(params)
    bad = []
    for key in args:
        m = _SUFFIX.match(key) if isinstance(key, str) else None
        if m and key not in names and not any(
                f"{m.group('stem')}_{s}" in names for s in ("mils", "mil", "mm")):
            bad.append(key)
    return bad


# ------------------------------------------------------------- Altium's reach

#: Altium holds a coordinate as a signed 32-bit number, 10,000 to the mil,
#: so a place past this many mils wraps round to the other side of the
#: origin. MEASURED on AD 25.8.1: a track at 214,000 mil was kept as given,
#: one at 220,000 came back at -209,497 (a No ERC marker given as
#: 5,600 mm landed at -209,024 mil).
WRAP_MILS = (2 ** 31 - 1) / 10000

#: A board's editor shows 0 to 99,999 mil on each axis, absolute (the
#: script engine's kMinCoord and kMaxCoord). MEASURED on AD 25.8.1: a track
#: outside that box is kept as given, but the view cannot be moved to it
#: (Zoom All shows exactly 0 to 99,999), so it can be neither seen nor
#: picked. A board tool takes a place either absolute or from the board
#: origin, which lies inside the box, so a place further than this from
#: zero is outside it either way. A sheet has no such edge short of the
#: wrap: markers at -500 and at 133,858 mil were both zoomed to.
BOARD_REACH_MILS = 99999

#: Plain names whose numbers are places, or moves from one place to
#: another. A size puts nothing anywhere and is not checked.
POSITION_KEYS = frozenset({
    "x", "y", "x1", "y1", "x2", "y2", "x3", "y3", "cx", "cy", "dx", "dy",
    "x_center", "y_center", "center_x", "center_y", "centre_x", "centre_y",
    "start_x", "start_y", "end_x", "end_y", "mid_x", "mid_y",
    "from_x", "from_y", "to_x", "to_y", "source_x", "source_y",
    "reference_x", "reference_y", "location_x", "location_y",
    "directive_x", "directive_y", "origin_x", "origin_y",
    "offset_x", "offset_y",
})

#: Names under which every number, or every vertex of a list, is a place.
#: A box (bounds, bbox, box, rect, extent) is left out: it is mostly an
#: area to search, and one larger than the board is a fair way to say all.
PLACE_KEYS = frozenset({
    "points", "vertices", "outline", "polygon", "corners",
    "location", "position", "center", "centre", "origin", "at", "pos", "xy",
})

#: The tools that put or find something in an Altium document.
_ALTIUM_PREFIXES = ("pcb_", "sch_", "lib_", "route_", "obj_")


def _on_board(tool: str) -> bool:
    """Whether ``tool`` acts on a board or a footprint (a symbol is drawn
    on a sheet's plane, which has no edge short of the wrap)."""
    return tool.startswith(("pcb_", "route_")) or (
        tool.startswith("lib_") and re.search(r"footprint|pad|via", tool) is not None)


def _places(obj: Any, path: str, unit: str, own_units: bool, in_place: bool,
            found: list) -> None:
    """Every place in ``obj`` as ``(path, number, unit)`` onto ``found``.
    ``own_units``: a dict's ``units`` says the unit of its plain lengths
    (the tools that convert their lengths themselves). A name that carries
    its unit (``x_mils``) is in that unit whatever it sits in."""
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            at = f"{path}[{i}]"
            if in_place and _numbers(v) and not isinstance(v, list):
                found.append((at, v, unit))
            else:
                _places(v, at, unit, own_units, in_place, found)
        return
    if not isinstance(obj, dict):
        return
    if own_units and isinstance(obj.get("units"), str):
        unit = normalise_units(obj["units"]) or unit
    for key, value in obj.items():
        if not isinstance(key, str):
            continue
        at = f"{path}.{key}" if path else key
        name, here = key, unit
        m = _SUFFIX.match(key)
        if m:
            name, here = m.group("stem"), _unit_of(m.group("unit"))
        if name in POSITION_KEYS and _numbers(value):
            for i, v in enumerate(value if isinstance(value, list) else [value]):
                found.append((f"{at}[{i}]" if isinstance(value, list) else at, v, here))
        elif name in PLACE_KEYS and isinstance(value, (list, dict)):
            _places(value, at, here, own_units, True, found)
        elif in_place and _numbers(value) and not _NOT_IN_LENGTHS.search(key):
            for v in (value if isinstance(value, list) else [value]):
                found.append((at, v, here))
        elif isinstance(value, (list, dict)):
            _places(value, at, here, own_units, in_place and isinstance(value, list), found)


def out_of_reach(tool: str, args: dict, unit: str) -> str:
    """Why ``tool`` must not be sent ``args`` (as the tool takes them),
    or ''. A place past Altium's 32-bit coordinate wraps to the other side
    of the origin, and on a board a place outside the editor's box can be
    neither seen nor picked; both are refused before anything is sent.
    ``unit`` is the call's, to give the value back as the caller wrote it."""
    if not tool.startswith(_ALTIUM_PREFIXES) or not converts(tool) \
            or not isinstance(args, dict):
        return ""
    own = tool in SELF_CONVERTING
    found: list = []
    _places(args, "", native_unit(tool), own, False, found)
    board = _on_board(tool)
    limit = BOARD_REACH_MILS if board else WRAP_MILS
    far = []
    for at, value, here in found:
        mils = value * (MILS_PER_MM if here == "mm" else 1.0)
        if abs(mils) > limit + 1e-6:
            # As the caller wrote it: a plain length the server converted
            # was given in the call's unit; any other is in its own.
            named = _SUFFIX.match(at.rsplit(".", 1)[-1].split("[", 1)[0])
            shown = unit if not own and not named else here
            given = mils * (MM_PER_MIL if shown == "mm" else 1.0)
            far.append(f"{at} = {_tidy(given):g} {shown}")
    if not far:
        return ""
    more = f" and {len(far) - 3} more" if len(far) > 3 else ""
    if board:
        why = (f"outside the board area Altium can show, 0 to {BOARD_REACH_MILS:,} mil "
               f"({BOARD_REACH_MILS * MM_PER_MIL:,.2f} mm) on each axis; there it "
               "can be neither seen nor picked")
    else:
        why = (f"past Altium's coordinate range, +/-{WRAP_MILS:,.0f} mil "
               f"({WRAP_MILS * MM_PER_MIL:,.1f} mm), where it wraps round to "
               "the other side of the origin")
    return (f"{tool}: {', '.join(far[:3])}{more} is {why}. Nothing was sent; "
            f"check the unit (this call is in {unit}).")


# ---------------------------------------------------------------- the setting

#: Where each session's unit is kept, in the workspace.
SESSIONS_DIR = "length_units"

#: A session's file is cleared this long after it was last written.
STALE_SECONDS = 14 * 24 * 3600


def _workspace() -> Path:
    from .config import get_config
    return Path(get_config().workspace_dir)


def _session_tag() -> str:
    from .bridge.altium_bridge import _SESSION_TAG
    return _SESSION_TAG


def _session_file(workspace: Optional[Path] = None) -> Path:
    return (workspace or _workspace()) / SESSIONS_DIR / f"{_session_tag()}.json"


def default_unit(workspace: Optional[Path] = None) -> str:
    """The unit chosen on the Config tab for new sessions."""
    from .preferences import preferences
    return preferences(workspace)["length_units"]


def session_unit(workspace: Optional[Path] = None) -> str:
    """This session's unit: its own choice, else the Config tab's."""
    try:
        stored = json.loads(_session_file(workspace).read_text(encoding="utf-8"))
        kept = stored.get("unit") if isinstance(stored, dict) else None
        unit = normalise_units(kept) if isinstance(kept, str) and kept.strip() else None
    except (OSError, ValueError):
        unit = None
    return unit or default_unit(workspace)


def set_session_unit(unit: str, workspace: Optional[Path] = None) -> str:
    """Keep ``unit`` as this session's; answers the unit kept."""
    u = normalise_units(unit)
    if u is None:
        raise ValueError(f"length_units is 'mil' or 'mm', not {unit!r}")
    path = _session_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    from .atomicfile import replace_with_retry
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"unit": u}), encoding="utf-8")
    replace_with_retry(tmp, path)
    _clear_stale(path.parent)
    return u


def _clear_stale(folder: Path) -> None:
    now = time.time()
    for old in folder.glob("*.json"):
        try:
            if now - old.stat().st_mtime > STALE_SECONDS:
                old.unlink()
        except OSError:
            pass


# ------------------------------------------------------------- the boundary

#: Set while a call is being answered, so a tool called from inside
#: another (tool_invoke, a family tool) is converted once, by the outer.
_INSIDE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "eda_agent_lengths_inside", default=False)


def inside() -> bool:
    return _INSIDE.get()


def enter() -> contextvars.Token:
    return _INSIDE.set(True)


def leave(token: contextvars.Token) -> None:
    _INSIDE.reset(token)


def call_unit(arguments: dict) -> tuple[dict, str]:
    """The arguments without a per-call ``length_units``, and the unit
    the call is in."""
    if not isinstance(arguments, dict) or OVERRIDE not in arguments:
        return arguments, session_unit()
    rest = {k: v for k, v in arguments.items() if k != OVERRIDE}
    asked = arguments[OVERRIDE]
    unit = normalise_units(asked) if asked is not None else None
    if asked is not None and unit is None:
        raise ValueError(f"length_units is 'mil' or 'mm', not {asked!r}")
    return rest, unit or session_unit()
