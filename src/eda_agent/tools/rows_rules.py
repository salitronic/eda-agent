# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The request checks of a rows write, made BEFORE the request is sent.

rows.py is the only way into the rows commands, so the checks that need
nothing from Altium live here, where they cost microseconds a row instead of
milliseconds in Altium's script interpreter (measured: the parse and row
checks of a 1000-pad write were ~2.4 s of ~4.5 s). The bridge keeps only a
backstop (every number a canonical 32-bit integer, never raising) and the
checks that need the document: guards, ids, layer names, a via's ring
against its present size, special strings.

Every check here reads the WIRE values (the scaled integers rows.py has
made: lengths in 1/10000 mil, angles in 1/10000 degree), so a range is
checked exactly, whatever unit the caller wrote in. The messages say "row
<id>: ..." and end "; nothing was changed", as the bridge's do. The
contract is docs/ROWS_CONTRACT.md.
"""

from __future__ import annotations

from typing import Any

#: The 32-bit range every integer on the wire keeps to.
INT_MIN, INT_MAX = -2147483647, 2147483647

#: Pin electrical types in every spelling the bridge's writer takes.
_ELECTRICAL = {
    "input": "input", "in": "input", "output": "output", "out": "output",
    "io": "io", "bidir": "io", "bidirectional": "io", "power": "power",
    "opencollector": "open_collector", "oc": "open_collector",
    "openemitter": "open_emitter", "oe": "open_emitter",
    "hiz": "hiz", "tri": "hiz", "tristate": "hiz", "passive": "passive",
}


def pin_electrical(value: str) -> str:
    """A pin electrical type by the name the bridge writes, '' for a word it
    does not know (that one would silently fall back to passive)."""
    s = str(value).strip().lower()
    if s.startswith("eelectric"):
        s = s[len("eelectric"):]
    return _ELECTRICAL.get(s.replace("_", ""), "")


def func_list(value: str) -> str:
    """Pin functions in their "/" form: items trimmed, empty ones dropped."""
    return "/".join(p.strip() for p in str(value).replace(";", "/").split("/") if p.strip())


def canonical_int(text: str) -> bool:
    """Whether text is a canonical 32-bit integer, as the bridge requires."""
    s = str(text)
    if not s or s != s.strip():
        return False
    try:
        n = int(s)
    except ValueError:
        return False
    return str(n) == s and INT_MIN <= n <= INT_MAX


# --- value checks per kind: (field, wire value) -> problem or "" -------------

def _num(v: str) -> int:
    return int(v) if canonical_int(v) else 0


def _one_of(v: str, words: str) -> bool:
    return bool(v.strip()) and v.strip().lower() in words.split("|")


_SIZES = ("size", "size_mid", "size_bottom", "x_size", "y_size", "bottom_x_size",
          "bottom_y_size", "hole_width")


def _pad_via_value(is_via: bool, field: str, v: str) -> str:
    n = _num(v)
    if field == "hole":
        if is_via and n <= 0:
            return "hole must be more than 0"
        if n < 0:
            return "hole cannot be negative"
    elif field in _SIZES:
        if n <= 0:
            return f"{field} must be more than 0"
    elif field == "stack_mode":
        if not _one_of(v, "simple|top-middle-bottom"):
            return "stack_mode is simple or top-middle-bottom (a full stack is set in Altium)"
    elif field == "shape":
        if not _one_of(v, "round|rectangular|octagonal|roundrect"):
            return "shape is round, rectangular, octagonal or roundrect"
    elif field == "hole_type":
        if not _one_of(v, "round|square|slot"):
            return "hole_type is round, square or slot"
    elif field == "corner_radius_pct":
        if not 0 <= n <= 100:
            return "corner_radius_pct is 0 to 100, a percentage of half the shorter side"
    elif field == "name":
        if not v.strip():
            return "a pad name cannot be empty"
    return ""


def _track_value(field: str, v: str) -> str:
    if field == "width" and _num(v) <= 0:
        return "width must be more than 0"
    return ""


def _arc_value(field: str, v: str) -> str:
    n = _num(v)
    if field in ("width", "radius") and n <= 0:
        return f"{field} must be more than 0"
    if field in ("start_angle", "end_angle") and not 0 <= n <= 3600000:
        return f"{field} must be from 0 to 3600000 (1/10000 degree)"
    return ""


def _text_value(field: str, v: str) -> str:
    n = _num(v)
    if field in ("height", "stroke_width") and n <= 0:
        return f"{field} must be more than 0"
    if field == "rotation" and not 0 <= n < 3600000:
        return "rotation must be from 0 to less than 3600000 (1/10000 degree)"
    if field == "text" and not v.strip():
        return "text cannot be empty"
    return ""


def _pin_value(field: str, v: str) -> str:
    n = _num(v)
    if field == "designator" and not v.strip():
        return "designator may not be empty"
    if field == "length" and n < 0:
        return "length may not be below 0"
    if field == "orientation" and (not canonical_int(v.strip()) or n % 90):
        return f'orientation takes 0, 90, 180 or 270, not "{v}"'
    if field == "electrical" and not pin_electrical(v):
        return ("electrical takes input, output, io, power, open_collector, "
                f'open_emitter, hiz or passive, not "{v}"')
    whole = canonical_int(v.strip())
    if field in ("name_font_mode", "designator_font_mode") and (not whole or n < 0):
        return f"{field} takes a whole number, 0 or more"
    if field in ("name_font_id", "designator_font_id") and (not whole or n < 1):
        return f"{field} takes a font id (a whole number, 1 or more)"
    if field in ("name_color", "designator_color", "color") and (
            not whole or not 0 <= n <= 16777215):
        return f"{field} takes a colour, 0 to 16777215"
    if field == "functions" and not func_list(v):
        return "functions cannot be cleared (Altium has no route for it)"
    return ""


_SHAPES = "none|arrow|solid arrow|tail|solid tail|circle|square"


def _sch_value(field: str, v: str) -> str:
    """schlib / sch line, polyline and arc."""
    n = _num(v)
    whole = canonical_int(v.strip())
    if field in ("width", "style", "shape_size") and (not whole or not 0 <= n <= 3):
        return f"{field} takes 0, 1, 2 or 3, not \"{v}\""
    if field == "color" and (not whole or not 0 <= n <= 16777215):
        return f"{field} takes a colour, 0 to 16777215"
    if field in ("start_shape", "end_shape") and not _one_of(v, _SHAPES):
        return (f"{field} takes None, Arrow, Solid Arrow, Tail, Solid Tail, Circle "
                f"or Square, not \"{v}\"")
    if field == "radius" and n <= 0:
        return "radius must be more than 0"
    if field in ("start_angle", "end_angle") and not 0 <= n <= 3600000:
        return f"{field} must be from 0 to 3600000 (1/10000 degree)"
    return ""


def _value_check(doc: str, kind: str):
    if doc in ("schlib", "sch") and kind in ("line", "polyline", "arc"):
        return _sch_value
    if kind in ("via", "pad"):
        return lambda f, v: _pad_via_value(kind == "via", f, v)
    if kind == "track":
        return _track_value
    if kind == "arc":
        return _arc_value
    if kind == "text":
        return _text_value
    if kind == "pin":
        return _pin_value
    return lambda f, v: ""


# --- a row taken together ------------------------------------------------------

def _pad_row(w: dict[str, str]) -> str:
    hole_type = w.get("hole_type", "").lower()
    shape = w.get("shape", "").lower()
    if hole_type in ("round", "square") and "hole_width" in w:
        return "hole_width makes a slot; leave it out for a round or square hole"
    if "corner_radius_pct" in w and shape and shape != "roundrect":
        return "corner_radius_pct needs shape roundrect"
    if w.get("stack_mode", "").lower() == "simple" and (
            "bottom_x_size" in w or "bottom_y_size" in w):
        return "a simple pad has one size: a bottom size needs top-middle-bottom"
    if w.get("mask_manual", "").lower() == "false" and ("mask_top" in w or "mask_bottom" in w):
        return ("mask_manual=false hands the mask to the design rule, so a mask value "
                "with it would be ignored")
    if w.get("paste_manual", "").lower() == "false" and "paste" in w:
        return ("paste_manual=false hands the paste to the design rule, so a paste value "
                "with it would be ignored")
    return ""


def _via_row(w: dict[str, str]) -> str:
    man = w.get("mask_manual", "").lower()
    if man == "false" and ("mask_top" in w or "mask_bottom" in w):
        return ("mask_manual=false hands the mask to the design rule, so a mask value "
                "with it would be ignored")
    if man == "false" and ("tenting_top" in w or "tenting_bottom" in w):
        return "tenting holds only on a manual mask; mask_manual=false with it would undo it"
    if w.get("stack_mode", "").lower() == "simple" and ("size_mid" in w or "size_bottom" in w):
        return "a simple via has one size: size_mid and size_bottom need top-middle-bottom"
    hole, size = w.get("hole", ""), w.get("size", "")
    if canonical_int(hole) and canonical_int(size) and int(hole) >= int(size):
        return f"hole {hole} must be smaller than size {size} (no annular ring left)"
    return ""


def _row_check(kind: str):
    return {"pad": _pad_row, "via": _via_row}.get(kind, lambda w: "")


# --- the whole write -----------------------------------------------------------

def _type_problem(t: str, name: str, v: str) -> str:
    if t in ("l", "a", "i") and not canonical_int(v.strip()):
        what = {"l": "a length in 1/10000 mil", "a": "an angle in 1/10000 degree"}.get(
            t, "an integer")
        return (f"{name} takes a whole number ({what}; no decimals, no leading zeros), "
                f'not "{v}"')
    if t == "b" and v.strip().lower() not in ("true", "false"):
        return f'{name} takes true or false, not "{v}"'
    if t == "v":
        pts = [p.split(",") for p in v.split(";")]
        if len(pts) < 2 or any(len(p) != 2 or not all(canonical_int(c) for c in p)
                               for p in pts):
            return (f"{name} takes two or more points x,y;x,y (whole numbers, a "
                    f'length in 1/10000 mil), not "{v}"')
    return ""


def check_rows(doc: str, kind: str, rows: list[dict[str, str]],
               entry: dict[str, Any]) -> tuple[str, str]:
    """Every check of a rows write that needs nothing from Altium.

    rows: one dict per row, keys as sent ("id", "?guard", field), values as
    the WIRE carries them (strings, numbers already scaled). entry: the
    kind's rows.list_kinds entry (fields with type / writable, and
    container_guard). Returns ("", "") when all rows are fine, else
    (error code, message) -- the codes the bridge would have answered.
    """
    fields = {f["name"]: f for f in entry.get("fields") or []}
    names = ", ".join(fields)
    guard = str(entry.get("container_guard") or "")
    value = _value_check(doc, kind)
    row = _row_check(kind)
    if not rows:
        return "MISSING_PARAMS", "rows is empty"
    seen: set[str] = set()
    for raw in rows:
        r = {str(k).strip().lower(): v for k, v in raw.items()}
        rid = str(r.get("id", "")).strip()
        if not rid or rid in seen:
            return ("BAD_ROWS", f'every row needs an id of its own (missing or repeated: '
                                f'"{rid}"); nothing was changed')
        seen.add(rid)
        writes: dict[str, str] = {}
        for key, v in r.items():
            if key == "id":
                continue
            name = key[1:] if key.startswith("?") else key
            spec = fields.get(name)
            if spec is None:
                return ("BAD_FIELD", f'row {rid}: unknown field "{name}"; this kind has: '
                                     f"{names}; nothing was changed")
            if not key.startswith("?"):
                if not spec.get("writable"):
                    return ("READ_ONLY_FIELD", f"row {rid}: {name} is read-only (use ?{name}= "
                                               f"to guard on it); nothing was changed")
                writes[name] = str(v)
            t = spec.get("type", "")
            if str(v) != "" or t != "s":
                p = _type_problem(t, name, str(v))
                if p:
                    return "BAD_VALUE", f"row {rid}: {p}; nothing was changed"
        if not writes:
            return "BAD_ROWS", f"row {rid} writes nothing; nothing was changed"
        if guard and not str(r.get("?" + guard, "")).strip():
            return ("BAD_ROWS", f"row {rid} needs ?{guard}= (where the object is); "
                                "nothing was changed")
        for name, v in writes.items():
            p = value(name, v)
            if p:
                return "BAD_VALUE", f"row {rid}: {p}; nothing was changed"
        p = row(writes)
        if p:
            return "BAD_VALUE", f"row {rid}: {p}; nothing was changed"
    return "", ""
