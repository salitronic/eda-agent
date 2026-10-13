# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library management tools for Altium Designer MCP Server."""

import asyncio
import re
from pathlib import Path
from typing import Any, Optional
from ..bridge import get_bridge
from ..bridge.payload import payload_safe
from ..libimport import extract_cse_zip, inspect_cse_zip
from ..library_db import (
    dblib_path_refusal, is_dblib_path, redact_connection_text, redact_reply,
    search_hit_as_result,
)
from .bulk_hints import BulkHintTracker
from .datasheet_hints import tag_response
from ..config import get_config
from ..units import format_length, length_in, normalise_units
from ..atomicfile import replace_with_retry


def _encode_layer_ops(layers) -> "str | dict":
    """Layer dicts to the batch string, or a refusal dict.

    Shared by ``lib_set_mech_layers`` and ``lib_run_across``. It lived
    inside the first of those, so a sweep passing the same list through
    the second sent a JSON array where the handler expected the encoded
    string. The handler parsed no operations from it, changed nothing,
    and reported success, which across twenty two libraries read as
    twenty two successes and no work done.
    """
    if not isinstance(layers, (list, tuple)):
        return {"success": False,
                "error": (f"layers must be a list of dicts, got "
                          f"{type(layers).__name__}")}
    ops: list[str] = []
    for entry in layers:
        if not isinstance(entry, dict):
            return {"success": False,
                    "error": f"each layer must be a dict, got {entry!r}"}
        name = str(entry.get("layer") or "").strip()
        if not name:
            return {"success": False,
                    "error": "every layer entry needs a 'layer' key"}
        fields = [f"layer={name}"]
        if entry.get("name") is not None:
            fields.append(f"name={entry['name']}")
        if entry.get("enabled") is not None:
            fields.append(
                f"enabled={'true' if entry['enabled'] else 'false'}")
        if entry.get("kind") is not None:
            fields.append(f"kind={entry['kind']}")
        joined = ";".join(str(f) for f in fields)
        # ';' separates fields and '~~' separates operations, so a value
        # carrying either would silently split into something else.
        if "~~" in joined:
            return {"success": False,
                    "error": (f"a value contains the '~~' separator and "
                              f"cannot be encoded: {joined}")}
        ops.append(joined)

    if not ops:
        return {"success": False,
                "error": ("layers is empty, so nothing would change. An "
                          "empty request that reports success is how a "
                          "library gets skipped unnoticed")}
    return "~~".join(ops)


# Schematic symbol grid is 100 mils. Every pin Location and every
# rectangle/line corner authored via the lib_add_* tools is rounded to
# this grid before the bridge call. Off-grid pins break wire routing
# in placed instances; off-grid rectangle corners produce blurry-looking
# bodies and prevent the Altium snap mechanism from aligning them.
# This is a hard invariant, not a hint -- every coord goes through here.
_SCHEMATIC_GRID_MILS = 100

# Footprint-policy sweep: footprints per bulk-geometry page, and the per-page
# timeout. The first page also opens the PcbLib, which on a large library is
# seconds; the default command timeout is far too short for it.
_GEOMETRY_PAGE = 250
_GEOMETRY_TIMEOUT = 120.0

# A repaired designator further than this from its pads did not land on the
# part. The biggest footprints here span a couple of thousand mils; a designator
# dropped at the board origin is ~50000 away.
_STRAY_MILS = 5000

# Apply/reload passes before giving up. Each pass fixes what the previous one's
# geometry change revealed (a resized text has a new bounding box). Convergence
# is fast -- ~90% in the first pass -- so this is a runaway guard, not a budget.
_MAX_PASSES = 6


def _batch_encoding() -> str:
    """The encoding the Pascal batch reader actually decodes.

    The batch handlers read with classic AssignFile/ReadLn, which hands
    Altium bytes in the SYSTEM ANSI codepage; Python's name for that is
    "mbcs" (Windows only). The previous latin-1 choice was wrong twice
    on a CP1252 machine: 0x80-0x9F characters (trademark sign, curly
    quotes, en/em dashes) crashed the tool with UnicodeEncodeError, and
    any latin-1/ANSI divergence would have reached Altium as the wrong
    character. Off Windows there is no "active ANSI codepage", so fall
    back to cp1252, the codepage of the Windows machine the workspace
    is shared with in every real deployment.
    """
    try:
        "".encode("mbcs")
        return "mbcs"
    except LookupError:
        return "cp1252"


def _first_unencodable(value: str, encoding: str) -> str:
    """The first character of ``value`` the codepage cannot represent.

    Not taken from UnicodeEncodeError.start/end: the native mbcs codec
    reports those in UTF-16 code units, so slicing the code-point
    string with them lands past an astral character and names ''.
    """
    for ch in value:
        try:
            ch.encode(encoding)
        except UnicodeEncodeError:
            return ch
    return ""


def _edit_line(act: dict) -> str:
    """One tab-separated edit row: name, layer_id, x, y, height, create.

    An empty field tells the script to leave that property alone.
    """
    def _f(key):
        v = act.get(key)
        return "" if v is None else str(v)

    return "\t".join((
        str(act["footprint"]), _f("layer_id"), _f("x"), _f("y"), _f("height"),
        "1" if act.get("create") else "0",
    ))


def write_designator_edits(workspace_dir, actions) -> tuple:
    """Write the edit file the bulk script reads. Returns (path, written,
    rejected).

    A footprint whose name contains a tab or newline cannot be expressed in
    this format; it is rejected rather than silently corrupting the row that
    follows it. Written atomically (temp + replace) so the script can never
    observe a half-written file.
    """
    import os
    import tempfile
    from pathlib import Path

    written, rejected = [], []
    for act in actions:
        name = str(act.get("footprint", ""))
        if not name or "\t" in name or "\n" in name or "\r" in name:
            rejected.append({"footprint": name,
                             "reason": "name contains a tab or newline"})
            continue
        written.append(act)

    path = Path(workspace_dir) / "designator_edits.tsv"
    body = "".join(_edit_line(a) + "\n" for a in written)
    fd, tmp = tempfile.mkstemp(dir=str(workspace_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path, written, rejected


def _same_plan(a, b) -> bool:
    """Two plans that ask for exactly the same edits. If a pass applied and the
    next plan is identical, nothing landed and repeating will not help."""
    def key(acts):
        return sorted((x["footprint"], x.get("x"), x.get("y"),
                       x.get("layer_id"), x.get("height"), x.get("create"))
                      for x in acts)
    return bool(a) and key(a) == key(b)


async def _reload_library(bridge, library_path) -> bool:
    """Close and reopen the PcbLib so Altium rebuilds its caches from disk.

    ``IPCB_Text.BoundingRectangle`` is populated at load and is NOT refreshed by
    assigning XLocation or Size. Without a reload, everything read after a write
    reports the text's OLD extent, and a centring pass computed from that moves
    the text by a wrong delta. Save before calling this.
    """
    if not library_path:
        return False
    try:
        res = await bridge.send_command_async(
            "library.reload_library", {"library_path": library_path},
            timeout=_GEOMETRY_TIMEOUT)
        return bool(isinstance(res, dict) and res.get("reloaded"))
    except Exception:
        return False


async def _apply_edits(bridge, actions, resolved_path, failed):
    """Write one batch of designator edits. Returns (applied, created)."""
    edits_path, written, rejected = write_designator_edits(
        bridge.config.workspace_dir, actions)
    failed.extend(rejected)
    if not written:
        return 0, 0

    params = {"edits_path": str(edits_path)}
    if resolved_path:
        params["library_path"] = resolved_path
    try:
        res = await bridge.send_command_async(
            "library.set_designators", params, timeout=_GEOMETRY_TIMEOUT)
        res = res if isinstance(res, dict) else {}
    except Exception as e:
        failed.append({"footprint": "*", "error": str(e)})
        return 0, 0

    if res.get("failed"):
        failed.append({"footprint": "*",
                       "error": f"{res['failed']} edits failed"})
    if res.get("refused"):
        # The script refused to create a designator on a footprint that already
        # had one. Never silent: that guard firing means reader and writer
        # disagree about what exists.
        failed.append({"footprint": "*",
                       "error": f"{res['refused']} creates refused "
                                f"(footprint already had a .Designator)"})
    for name in res.get("missing", []):
        failed.append({"footprint": name, "error": "not found in library"})
    for name in res.get("immovable", []):
        # The script assigned XLocation, read it back, and it had not changed.
        # Silently counting these as applied hid failed repairs behind a
        # success total for an entire session.
        failed.append({"footprint": name,
                       "error": "designator did not move (XLocation refused "
                                "the assignment)"})
    return res.get("applied", 0), res.get("created", 0)


async def _read_library_geometry(bridge, library_path):
    """Page the whole library's policy geometry. Returns (footprints, path)."""
    base: dict[str, Any] = {}
    if library_path:
        base["library_path"] = library_path
    footprints: list[dict[str, Any]] = []
    resolved: Optional[str] = None
    offset, total = 0, None
    while total is None or offset < total:
        page = await bridge.send_command_async(
            "library.get_library_geometry",
            dict(base, offset=offset, limit=_GEOMETRY_PAGE),
            timeout=_GEOMETRY_TIMEOUT,
        )
        if not isinstance(page, dict):
            break
        resolved = resolved or page.get("library_path")
        total = page.get("total") or 0
        batch = page.get("footprints") or []
        footprints.extend(batch)
        if not batch:  # never spin if the script reports a short page
            break
        offset += len(batch)
    return footprints, (resolved or library_path)


def _snap(value: int) -> int:
    """Round a mil coordinate to the schematic 100-mil grid.

    Banker's-style: nearest-50 rounds away from zero. The placement
    pipelines all snap downward (// 100 * 100), but the symbol author
    tools accept user-supplied coords that may be off by a few mils
    (e.g., a hand-typed 503) -- rounding is more forgiving than
    truncating in that case.
    """
    if value >= 0:
        return ((value + _SCHEMATIC_GRID_MILS // 2)
                // _SCHEMATIC_GRID_MILS) * _SCHEMATIC_GRID_MILS
    return -(((-value + _SCHEMATIC_GRID_MILS // 2)
              // _SCHEMATIC_GRID_MILS) * _SCHEMATIC_GRID_MILS)


def _snap_grid(value: int, grid: int) -> int:
    """Round a mil coordinate to an arbitrary grid (<=1 disables snapping).

    For glyph primitives (lines / arcs / polygons), which -- unlike pins
    and rectangle corners (discipline rule 15) -- do NOT need the 100-mil
    grid, so finer detail (cap plates, diode triangles, resistor zigzags)
    can be drawn.
    """
    value = int(value)
    if grid <= 1:
        return value
    if value >= 0:
        return ((value + grid // 2) // grid) * grid
    return -(((-value + grid // 2) // grid) * grid)



#: Only two sequences actually delimit the batch payload, confirmed
#: against the Pascal parser (Main.pas GetBatchField):
#:   Pos(';', ...)  -> fields split on the FIRST ";"
#:   Pos('=', ...)  -> key/value split on the FIRST "=", so a value may
#:                     contain further "=" safely
#: and ops are separated by "~~". A single "~" is NOT a delimiter, and
#: must survive: it is standard overbar notation in pin names (~RESET).
_MULTI_TILDE = re.compile(r"~{2,}")


def _payload_safe(value: object) -> str:
    """Delegates to the shared sanitiser; see bridge/payload.py.

    Kept as a module-local name because a lot of call sites use it,
    but the RULES live in one place now: generic.py had a near-copy
    that collapsed tildes differently, so identical input produced
    different output depending on which tool sent it.
    """
    return payload_safe(value)


def _units_refusal(units: str) -> dict[str, Any] | None:
    """The reply for a unit the authoring tools do not take, or None."""
    if normalise_units(units) is None:
        return {"error": f"units must be 'mil' or 'mm', not {units!r}"}
    return None


def _with_units(params: dict[str, Any], units: str) -> dict[str, Any]:
    """Name millimetres on the wire; mils, the default, are left implicit
    so a call that never mentions units sends what it always sent."""
    if normalise_units(units) == "mm":
        params["units"] = "mm"
    return params


def _pads_payload(pads: list[dict[str, Any]],
                  units: str = "mil") -> tuple[str, int]:
    """Build the ``pads`` batch payload, and count what was dropped.

    Sibling of :func:`_pins_payload`, extracted for the same reason: the
    exact string Pascal parses can be produced without a bridge, so the
    cross-validation suite runs it through the real ``GetBatchField``
    compiled by FPC. Land patterns are where a payload mistake becomes a
    board that cannot be assembled, so this is the payload most worth
    checking against the real parser rather than a reading of it.

    Returns ``(payload, skipped_invalid)``. A pad with a blank
    designator is dropped -- the Pascal requires one -- and counted so
    the loss is reported instead of silently absorbed.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for p in pads:
        # Free-text fields are neutralised for the same reason as pins: a
        # designator or layer carrying ";", "=" or "~~" reshapes the
        # payload and lands the pad somewhere else.
        desig = _payload_safe(str(p.get("designator", "")).strip())
        if not desig:
            skipped_invalid += 1
            continue
        fields = [
            f"designator={desig}",
            # Lengths unrounded, in ``units``: round() here quantised every
            # millimetre land pattern to whole mils before it left Python.
            f"x={format_length(p.get('x', 0))}",
            f"y={format_length(p.get('y', 0))}",
            f"x_size={format_length(p.get('x_size', length_in(units, 60)))}",
            f"y_size={format_length(p.get('y_size', length_in(units, 60)))}",
            f"hole_size={format_length(p.get('hole_size', 0))}",
            f"shape={_payload_safe(p.get('shape', 'rectangular'))}",
            f"corner_radius={round(p.get('corner_radius', 25))}",
            f"rotation={_payload_safe(p.get('rotation', 0))}",
            f"layer={_payload_safe(p.get('layer', 'TopLayer'))}",
        ]
        # Optional, and only sent when the pad carries them, so every
        # payload without them is byte-identical to what it always was.
        for key in _OPTIONAL_PAD_LENGTHS:
            if p.get(key) is not None:
                fields.append(f"{key}={format_length(p[key])}")
        if p.get("plated") is not None:
            plated = p["plated"]
            if isinstance(plated, str):
                plated = plated.strip().lower() not in ("false", "0", "no")
            fields.append(f"plated={'true' if plated else 'false'}")
        op_strs.append(";".join(fields))
    return "~~".join(op_strs), skipped_invalid


def _tracks_payload(tracks: list[dict[str, Any]],
                    units: str = "mil") -> tuple[str, int]:
    """The ``tracks`` batch payload, and how many entries were dropped.

    Shared by lib_add_footprint_tracks and the package footprint tool so
    the two cannot format a track differently. A track missing an
    endpoint is dropped and counted.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for t in tracks:
        if not all(k in t for k in ("x1", "y1", "x2", "y2")):
            skipped_invalid += 1
            continue
        fields = [
            f"x1={format_length(t.get('x1', 0))}",
            f"y1={format_length(t.get('y1', 0))}",
            f"x2={format_length(t.get('x2', 0))}",
            f"y2={format_length(t.get('y2', 0))}",
            f"width={format_length(t.get('width', length_in(units, 10)))}",
            f"layer={_payload_safe(t.get('layer', 'TopOverlay'))}",
        ]
        op_strs.append(";".join(fields))
    return "~~".join(op_strs), skipped_invalid


def _pad_geometry_problem(reply: Any) -> str:
    """Why a library.get_pad_geometry reply is not a footprint, or ''.

    A footprint with no pads reads as an empty list and is a real
    answer; a refusal or a reply with no list at all is not.
    """
    if not isinstance(reply, dict):
        return f"the read returned {type(reply).__name__}, not a footprint"
    if reply.get("success") is False or (
            reply.get("error") and not isinstance(reply.get("pads"), list)):
        err = reply.get("error")
        if isinstance(err, dict):
            err = err.get("message") or err.get("code")
        return f"the read was refused: {err or 'no reason given'}"
    if not isinstance(reply.get("pads"), list):
        return "the read carried no pad list"
    return ""


def _unread_footprint(name: str, reason: str, code: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "reason": (f"could not read footprint {name!r}: {reason}. Nothing "
                   "was audited; check the name with lib_get_footprints"),
        "findings": [],
    }
    if code:
        out["error_code"] = code
    return out


#: Pad fields the batch handler writes only when present, all lengths in
#: the call's units: a slot length (above hole_size it makes a slotted
#: hole), and manual paste and solder mask expansions.
_OPTIONAL_PAD_LENGTHS = ("hole_length", "paste_expansion", "mask_expansion")


def _regions_payload(regions: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The ``regions`` batch payload for library.add_footprint_regions.

    Each region is ``{"layer": ..., "points": [[x, y], ...]}``; points go
    out as ``x,y`` pairs joined by ``/``, unrounded. Returns
    ``(payload, problems)``: a region with no layer or fewer than three
    numeric points is named in ``problems`` and left out, never sent with
    a guessed layer or a point read as zero.
    """
    ops: list[str] = []
    problems: list[str] = []
    for i, region in enumerate(regions or []):
        if not isinstance(region, dict):
            problems.append(f"region {i} is not an object")
            continue
        layer = str(region.get("layer") or "").strip()
        if not layer:
            problems.append(f"region {i} has no layer")
            continue
        pts = region.get("points")
        try:
            pairs = [(float(p[0]), float(p[1])) for p in pts]
        except (TypeError, ValueError, IndexError, KeyError):
            problems.append(f"region {i} points must be [x, y] number pairs")
            continue
        if len(pairs) > 3 and pairs[0] == pairs[-1]:
            pairs = pairs[:-1]
        if len(pairs) < 3:
            problems.append(f"region {i} needs at least three points")
            continue
        points = "/".join(f"{format_length(x)},{format_length(y)}"
                          for x, y in pairs)
        ops.append(f"layer={_payload_safe(layer)};points={points}")
    return "~~".join(ops), problems


def _fills_payload(fills: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The ``fills`` batch payload for library.add_footprint_fills.

    Each fill is ``{"layer": ..., "x1": .., "y1": .., "x2": .., "y2": ..}``
    with an optional ``rotation`` in degrees, corners unrounded. Returns
    ``(payload, problems)``: a fill with no layer, a corner that is not a
    number, or no area is named in ``problems`` and left out, never sent
    with a guessed layer or a corner read as zero.
    """
    ops: list[str] = []
    problems: list[str] = []
    for i, fill in enumerate(fills or []):
        if not isinstance(fill, dict):
            problems.append(f"fill {i} is not an object")
            continue
        layer = str(fill.get("layer") or "").strip()
        if not layer:
            problems.append(f"fill {i} has no layer")
            continue
        try:
            x1, y1, x2, y2 = (float(fill[k]) for k in ("x1", "y1", "x2", "y2"))
            rotation = float(fill.get("rotation") or 0)
        except (TypeError, ValueError, KeyError):
            problems.append(f"fill {i} needs numeric x1, y1, x2, y2")
            continue
        if x1 == x2 or y1 == y2:
            problems.append(f"fill {i} has no area")
            continue
        op = (f"layer={_payload_safe(layer)};x1={format_length(x1)};"
              f"y1={format_length(y1)};x2={format_length(x2)};"
              f"y2={format_length(y2)}")
        if rotation:
            op += f";rotation={format_length(rotation)}"
        ops.append(op)
    return "~~".join(ops), problems


def _pins_payload(pins: list[dict[str, Any]]) -> tuple[str, int]:
    """Build the ``pins`` batch payload, and count what was dropped.

    Extracted so the exact string Pascal will parse can be produced
    without a bridge. The grammar is unforgiving -- operations split on
    "~~", fields on ";", key and value on the FIRST "=" -- and the
    cross-validation suite now runs this output through the real
    ``GetBatchField`` compiled by FPC, which is the only way to know the
    sanitiser and the parser agree rather than merely look like they do.

    Returns ``(payload, skipped_invalid)``. lib_add_footprint_pads drops
    blank designators the same way, so the count is reported, never
    silently absorbed.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for p in pins:
        desig = _payload_safe(str(p.get("designator", "")).strip())
        name = _payload_safe(str(p.get("name", "")).strip())
        if not desig:
            skipped_invalid += 1
            continue
        fields = [
            f"designator={desig}",
            f"name={name}",
            f"x={_snap(round(p.get('x', 0)))}",
            f"y={_snap(round(p.get('y', 0)))}",
            f"length={_snap(round(p.get('length', 200)))}",
            f"rotation={round(p.get('rotation', 0))}",
            f"electrical_type="
            f"{_payload_safe(p.get('electrical_type', 'passive'))}",
            f"hidden={'true' if p.get('hidden') else 'false'}",
        ]
        if "owner_part_id" in p and p["owner_part_id"] is not None:
            fields.append(f"owner_part_id={int(p['owner_part_id'])}")
        # IEEE edge decorations. Omitted entirely when unset, because a
        # fresh pin already carries eNoSymbol and the Pascal only writes
        # the property when the field is present, which keeps every
        # existing caller's payload byte-identical.
        for key in ("symbol_outer_edge", "symbol_inner_edge"):
            value = p.get(key)
            if value not in (None, ""):
                fields.append(f"{key}={_payload_safe(value)}")
        # Label visibility is tri-state on the wire: absent leaves
        # Altium's default alone, so False has to survive as the string
        # "false" rather than being folded into "not set".
        for key in ("show_name", "show_designator"):
            if p.get(key) is not None:
                fields.append(f"{key}={'true' if p[key] else 'false'}")
        op_strs.append(";".join(fields))
    return "~~".join(op_strs), skipped_invalid


def _safe_filename(name: str, fallback: str = "part") -> str:
    """Thin alias for the shared importer helper.

    Kept as a name so existing call sites and their tests do not move;
    the implementation lives in libimport._names so the providers and the
    EasyEDA importer cannot drift apart on which characters are illegal.
    """
    from ..libimport._names import safe_filename

    return safe_filename(name, fallback)


def _spill_pin_list(result, pins, comp, output_path):
    """Write the full pin array to JSON and summarise what is left.

    A pin list that overflows the conversation is not merely awkward: the
    client decides what survives, so the answer becomes environment
    dependent. Writing the whole thing and returning a summary makes the
    outcome the same everywhere, and the file is the more useful artefact
    for the job people actually do with it, which is a field-by-field
    comparison against the datasheet pin table.

    The summary carries the two distributions worth eyeballing: pins per
    part, and electrical type. Both are the checks that catch a symbol
    built from a mis-transcribed table.
    """
    import json
    from collections import Counter
    from pathlib import Path

    per_part = Counter()
    per_type = Counter()
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        per_part[str(pin.get("owner_part_id", ""))] += 1
        per_type[str(pin.get("electrical_type", ""))] += 1

    if output_path:
        target = Path(output_path)
    else:
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in (comp or "symbol"))
        target = Path(get_bridge().config.workspace_dir) / f"pins_{safe}.json"

    written = ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pins, indent=1), encoding="utf-8")
        written = str(target)
    except OSError as exc:
        # Never lose the data because the file could not be written.
        # Returning it inline is worse than a file and better than
        # nothing, and the reason has to travel with it.
        out = dict(result)
        out["spill_error"] = f"could not write {target}: {exc}"
        return out

    out = {k: v for k, v in result.items() if k != "pins"}
    out["pins_path"] = written
    out["summary"] = {
        "pins": len(pins),
        "per_part": dict(sorted(per_part.items())),
        "per_electrical_type": dict(sorted(per_type.items())),
    }
    out["note"] = (
        f"{len(pins)} pins written to {written}. Read or diff that file "
        f"directly; it is not summarised further here."
    )
    return out


#: Installed library kinds lib_search cannot read, and why, as
#: library.get_installed_libraries names them.
_UNSEARCHED_KINDS = {
    "database": "a database library (DbLib) keeps its parts as rows in a "
                "database; lib_search reads it only with include_dblibs on, "
                "and lib_dblib_search reads it directly",
    "query": "a query library (SVN DbLib) keeps its parts as rows in a "
             "database, which this search does not read",
    "integrated": "an integrated library (IntLib) is a compiled package; this "
                  "search reads only open .SchLib files",
    "design_items": "workspace components are not in a file this search reads",
}


async def _search_coverage(bridge, library_path) -> dict[str, Any]:
    """What a lib_search looked through, and what it could not.

    An empty search used to read as "no such part" whatever was installed:
    a user with a database library got 0 hits and no hint that the DbLib
    was never opened. The search walks the open .SchLib documents, which
    the open-documents list names exactly; the installed list names the
    rest. Either read failing leaves its field None rather than failing
    the search.
    """
    if library_path:
        return {"libraries_searched": [library_path], "libraries_searched_count": 1}
    out: dict[str, Any] = {}
    searched: list[str] = []
    try:
        docs = await bridge.send_command_async("application.get_open_documents")
        rows = docs if isinstance(docs, list) else (
            (docs or {}).get("result") or (docs or {}).get("documents") or [])
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = str(row.get("file_path") or "")
            kind = str(row.get("document_kind") or "").upper()
            if (kind == "SCHLIB" or path.upper().endswith(".SCHLIB")) and path.lower() not in seen:
                seen.add(path.lower())
                searched.append(path)
        out["libraries_searched"] = searched
        out["libraries_searched_count"] = len(searched)
    except Exception:  # noqa: BLE001 - coverage is a report, never a failure
        out["libraries_searched"] = None
    try:
        inst = await bridge.send_command_async(
            "library.get_installed_libraries", {"with_counts": "false"})
        libs = (inst.get("libraries") or []) if isinstance(inst, dict) else []
        open_paths = {p.lower() for p in searched}
        skipped = []
        for lib in libs:
            if not isinstance(lib, dict):
                continue
            path = str(lib.get("library_path") or "")
            kind = str(lib.get("library_type") or "unknown")
            if kind == "unknown" and is_dblib_path(path):
                # Installed but missing from the available list, which is
                # how the type gets lost; the file is still a DbLib.
                kind = "database"
            reason = _UNSEARCHED_KINDS.get(kind)
            if reason is None and path.upper().endswith(".SCHLIB") and path.lower() not in open_paths:
                reason = ("an installed .SchLib that is not open; open it, or pass "
                          "library_path, to search it")
            if reason:
                skipped.append({"library_path": path, "library_type": kind, "reason": reason})
        out["not_searched"] = skipped
        out["not_searched_count"] = len(skipped)
        note = _coverage_note(len(searched), 0, skipped)
        if note:
            out["coverage_note"] = note
    except Exception:  # noqa: BLE001
        out["not_searched"] = None
    return out


def _coverage_note(schlib_count: int, dblib_count: int, skipped: list) -> "str | None":
    """The sentence that stops an empty search reading as "no such part"."""
    if not skipped:
        return None
    kinds = sorted({str(s.get("library_type")) for s in skipped})
    what = f"{schlib_count} open .SchLib file(s)"
    if dblib_count:
        what += (f" and {dblib_count} database "
                 f"librar{'y' if dblib_count == 1 else 'ies'}")
    else:
        what += " only"
    n = len(skipped)
    return (f"Searched {what}. {n} installed librar{'y' if n == 1 else 'ies'} "
            f"({', '.join(kinds)}) were not searched, so an empty result is not "
            "evidence that a part is absent from them.")


#: One DbLib search reads table rows over ADO. A network database or a
#: large table takes far longer than the file reads the other tools wait
#: on, so it gets its own, longer, bound.
_DBLIB_TIMEOUT = 120.0


async def _query_one_dblib(bridge, library_path: str, query: str,
                           limit: int) -> dict[str, Any]:
    """One database library searched by library.query_dblib, redacted."""
    reply = await bridge.send_command_async(
        "library.query_dblib",
        {"library_path": library_path, "query": query, "limit": str(limit)},
        timeout=_DBLIB_TIMEOUT)
    return redact_reply(reply) if isinstance(reply, dict) else {}


def _dblib_summary(path: str, reply: dict[str, Any]) -> dict[str, Any]:
    """What one DbLib search covered, for ``dblib_searches``."""
    libs = reply.get("libraries") or []
    lib = libs[0] if libs and isinstance(libs[0], dict) else {}
    return {
        "library_path": path,
        "searched": bool(lib.get("searched")),
        "error": lib.get("error"),
        "count": reply.get("count", 0),
        "truncated": bool(reply.get("truncated")),
        "rows_scanned": reply.get("rows_scanned"),
        "scan_capped": bool(reply.get("scan_capped")),
        "tables_searched": lib.get("tables_searched") or [],
        "tables_failed": lib.get("tables_failed") or [],
    }


async def _search_dblibs(bridge, result: dict[str, Any], query: str,
                         limit: int, include_dblibs: bool) -> None:
    """Fold the installed database libraries into a lib_search reply.

    Each DbLib that _search_coverage listed under ``not_searched`` is
    searched in turn with what is left of ``limit``. Its rows join
    ``results`` marked ``source: "dblib"`` and its path moves to
    ``libraries_searched``. A DbLib that is switched off, crowded out by
    the limit, or that fails stays under ``not_searched`` with that
    reason: a DbLib never fails the search, and an unread one is never
    reported as read.
    """
    skipped = result.get("not_searched")
    if not isinstance(skipped, list):
        return
    results = result.setdefault("results", [])
    searched = result.get("libraries_searched")
    schlib_count = len(searched) if isinstance(searched, list) else 0
    truncated = bool(result.get("truncated"))
    remaining: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    dblibs_read = 0
    for entry in skipped:
        if not isinstance(entry, dict) or entry.get("library_type") != "database":
            remaining.append(entry)
            continue
        path = str(entry.get("library_path") or "")
        if not include_dblibs:
            remaining.append({**entry, "reason": (
                "a database library (DbLib), skipped because include_dblibs "
                "is off; turn it on, or use lib_dblib_search")})
            continue
        room = limit - len(results)
        if room <= 0:
            truncated = True
            remaining.append({**entry, "reason": (
                "a database library (DbLib) left unsearched because the result "
                "limit was reached first; raise limit or use lib_dblib_search")})
            continue
        try:
            reply = await _query_one_dblib(bridge, path, query, room)
        except Exception as exc:  # noqa: BLE001 - one DbLib never fails the search
            remaining.append({**entry, "reason": (
                "a database library (DbLib) whose search failed: "
                + redact_connection_text(str(exc))[:300])})
            continue
        summary = _dblib_summary(path, reply)
        summaries.append(summary)
        if not summary["searched"]:
            remaining.append({**entry, "reason": (
                "a database library (DbLib) that could not be searched ("
                + str(summary["error"] or "no reply") + "); lib_dblib_info "
                "reports its connection and tables")})
            continue
        hits = [search_hit_as_result(h) for h in (reply.get("results") or [])
                if isinstance(h, dict)]
        results.extend(hits[:room])
        if summary["truncated"] or summary["scan_capped"] or len(hits) > room:
            truncated = True
        if isinstance(searched, list):
            searched.append(path)
        dblibs_read += 1
    result["not_searched"] = remaining
    result["not_searched_count"] = len(remaining)
    if isinstance(searched, list):
        result["libraries_searched_count"] = len(searched)
    if summaries:
        result["dblib_searches"] = summaries
    note = _coverage_note(schlib_count, dblibs_read, remaining)
    if note:
        result["coverage_note"] = note
    else:
        result.pop("coverage_note", None)
    result["count"] = len(results)
    result["truncated"] = truncated or len(results) >= limit


async def _search_named_dblib(bridge, library_path: str, query: str,
                              search_type: str, limit: int) -> dict[str, Any]:
    """lib_search with library_path naming a DbLib: that DbLib alone, in
    lib_search's own reply shape."""
    out: dict[str, Any] = {"query": query, "search_type": search_type,
                           "limit": limit, "results": []}
    try:
        reply = await _query_one_dblib(bridge, library_path, query, limit)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": redact_connection_text(str(exc))}
    summary = _dblib_summary(library_path, reply)
    out["results"] = [search_hit_as_result(h) for h in (reply.get("results") or [])
                      if isinstance(h, dict)]
    out["count"] = len(out["results"])
    out["truncated"] = summary["truncated"] or summary["scan_capped"]
    out["dblib_searches"] = [summary]
    if summary["searched"]:
        out["libraries_searched"] = [library_path]
        out["libraries_searched_count"] = 1
    else:
        out["libraries_searched"] = []
        out["libraries_searched_count"] = 0
        out["not_searched"] = [{
            "library_path": library_path, "library_type": "database",
            "reason": ("a database library (DbLib) that could not be searched ("
                       + str(summary["error"] or "no reply") + ")")}]
        out["not_searched_count"] = 1
    return out


def register_library_tools(mcp):
    """Register library tools with the MCP server."""

    # =========================================================================
    # Symbol Creation
    # =========================================================================

    @mcp.tool()
    async def lib_create_symbol(
        name: str,
        designator_prefix: str = "U",
        description: str = "",
        part_count: int = 1,
    ) -> dict[str, Any]:
        """Create a new schematic symbol in the active library.

        Args:
            name: Component name
            designator_prefix: Default designator prefix (e.g., "U", "R", "C")
            description: Component description
            part_count: For multi-part components (quad op-amp, dual gate,
                etc), the number of sub-parts. Pins added via lib_add_pins
                with ``owner_part_id`` set to 1..part_count are assigned
                to that sub-part; ``owner_part_id=0`` shares the pin
                across all sub-parts (the usual power-pin pattern on a
                quad op-amp).

        Returns:
            Dictionary with created symbol information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.create_symbol",
            {
                "name": name,
                "designator_prefix": designator_prefix,
                "description": description,
                "part_count": str(max(1, int(part_count))),
            },
        )
        return result

    @mcp.tool()
    async def lib_create_ic_symbol(
        name: str,
        left_pins: list[dict[str, Any]],
        right_pins: list[dict[str, Any]],
        designator_prefix: str = "U",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a COMPLETE, discipline-compliant IC symbol in ONE call.

        You decide the functional grouping (inputs/power/control on the
        LEFT, outputs/status on the RIGHT -- discipline rule 18); this
        lays them out cleanly and emits the whole symbol: the body
        rectangle (Altium-yellow fill) plus every pin, grid-aligned with
        the top-left pin's wire-end at the origin (rule 14), in one
        create + one bulk add_pins + one rectangle.

        Args:
            name: Component name.
            left_pins: Pins on the LEFT side, top-to-bottom. Each a dict
                with `designator`, `name`, and optional `electrical_type`
                (input/output/power/passive/...).
            right_pins: Pins on the RIGHT side, top-to-bottom, same shape.
            designator_prefix: e.g. "U".
            description: Component description.

        Returns:
            Dict with the symbol name, pin count, body extent, and the
            per-step bridge results.
        """
        from eda_agent.design.symbol_gen import generate_ic_symbol

        # The generator is a pure function and raises on bad geometry;
        # at the tool boundary that becomes a refusal, not a stack trace.
        try:
            geom = generate_ic_symbol(left_pins, right_pins)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}

        bridge = get_bridge()
        created = await bridge.send_command_async(
            "library.create_symbol",
            {
                "name": name,
                "designator_prefix": designator_prefix,
                "description": description,
                "part_count": "1",
            },
        )

        # Built by the shared payload helper rather than inline. The
        # inline copy interpolated designator, name and electrical_type
        # straight into the string, so any of them carrying ";" or "~~"
        # reshaped the payload -- a pin name lifted from a datasheet
        # table is enough to do it by accident.
        # BODY FIRST, THEN PINS, AND THE ORDER IS NOT COSMETIC.
        # Altium exposes no z-order on schematic primitives: drawing order
        # IS insertion order, and there is no send-to-back to undo it. This
        # rectangle is solid (fill_color sets IsSolid), so adding it after
        # the pins paints it straight over the pin names, and the only
        # remedy is rebuilding the whole symbol. Reported from the field
        # 2026-09-21 after exactly that rebuild.
        body = geom.body
        rect_res = await bridge.send_command_async(
            "library.add_symbol_rectangle",
            {
                "x1": body["x1"], "y1": body["y1"],
                "x2": body["x2"], "y2": body["y2"],
                "fill_color": 8454143,
                "border_color": 0,
            },
        )

        pins_payload, _ = _pins_payload(geom.pins)
        pins_res = await bridge.send_command_async(
            "library.add_pins", {"pins": pins_payload})

        return {
            "symbol": name,
            "pins": len(geom.pins),
            "left": len(left_pins),
            "right": len(right_pins),
            "width_mils": geom.width_mils,
            "height_mils": geom.height_mils,
            "create": created,
            "pins_result": pins_res,
            "rectangle_result": rect_res,
        }

    @mcp.tool()
    async def lib_create_passive_symbol(
        name: str,
        kind: str,
        designator_prefix: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a standard 2-pin PASSIVE symbol in ONE call.

        Draws the recognizable glyph for a resistor / capacitor /
        polarized_capacitor / inductor / diode / led / crystal / fuse,
        with pin 1 on the left and pin 2 on the right. Composes
        `lib_create_symbol` + `lib_add_pins` + the
        glyph primitives (emitted as raw mils, so the LED's emission
        arrows and the crystal's resonator keep their sub-100-mil detail).

        Args:
            name: Component name (e.g. "R_0402").
            kind: "resistor" | "capacitor" | "polarized_capacitor" |
                "inductor" | "diode" | "led" | "crystal" | "fuse"
                (aliases r/c/cap_pol/electrolytic/cp/l/d/xtal/f accepted).
            designator_prefix: default by kind (R/C/L/D/Y/F) if omitted.
            description: Component description.

        Returns:
            Dict with the symbol name and per-step bridge results.
        """
        from eda_agent.design.symbol_gen import generate_passive_symbol

        try:
            geom = generate_passive_symbol(kind)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}
        prefix = designator_prefix or {
            "resistor": "R", "r": "R", "res": "R",
            "capacitor": "C", "c": "C", "cap": "C",
            "polarized_capacitor": "C", "cap_pol": "C",
            "electrolytic": "C", "cp": "C",
            "inductor": "L", "l": "L", "ind": "L",
            "diode": "D", "d": "D",
            "led": "D", "diode_led": "D",
            "crystal": "Y", "xtal": "Y", "y": "Y",
            "fuse": "F", "f": "F",
        }.get(kind.strip().lower(), "U")

        bridge = get_bridge()
        created = await bridge.send_command_async(
            "library.create_symbol",
            {"name": name, "designator_prefix": prefix,
             "description": description, "part_count": "1"},
        )

        # Third copy of this format, now the last: all three built the
        # payload inline and interpolated designator, name and
        # electrical_type raw, so a value carrying ";" or "~~" reshaped
        # it. One builder means one place to get the grammar right.
        pins_payload, _ = _pins_payload(geom.pins)
        pins_res = await bridge.send_command_async(
            "library.add_pins", {"pins": pins_payload})

        steps: dict[str, Any] = {}
        for i, r in enumerate(geom.rectangles):
            steps[f"rect{i}"] = await bridge.send_command_async(
                "library.add_symbol_rectangle",
                {"x1": r["x1"], "y1": r["y1"], "x2": r["x2"], "y2": r["y2"],
                 "fill_color": -1, "border_color": 0})
        if geom.lines:
            line_ops = [
                ";".join([f"x1={l['x1']}", f"y1={l['y1']}",
                          f"x2={l['x2']}", f"y2={l['y2']}", "width=10"])
                for l in geom.lines
            ]
            steps["lines"] = await bridge.send_command_async(
                "library.add_symbol_lines", {"lines": "~~".join(line_ops)})
        for i, poly in enumerate(geom.polygons):
            verts = ",".join(
                str(v) for pt in poly["points"] for v in pt)
            steps[f"poly{i}"] = await bridge.send_command_async(
                "library.add_symbol_polygon", {"vertices": verts})

        return {
            "symbol": name, "kind": kind, "designator_prefix": prefix,
            "pins": len(geom.pins), "create": created,
            "pins_result": pins_res, "glyph": steps,
        }

    @mcp.tool()
    async def lib_set_current_component(
        component_name: str,
    ) -> dict[str, Any]:
        """Make a named component the editor's current selection in the
        active SchLib.

        Required before bulk-editing a specific component's pins,
        rectangle, or parameters via ``obj_modify`` / ``obj_batch_modify``
        on a SchLib. The asymmetry it fixes: ``lib_get_component_details``
        is a read-only fetch and does NOT update the editor's current
        component, so subsequent ``obj_modify`` on the SchLib's
        ePin / eRectangle / eParameter iterators silently hits whatever
        component was last UI-selected -- usually NOT the one you just
        read.

        Use this between switching components:
            lib_set_current_component("MyIC")
            modify_objects("ePin", scope="active_doc",
                           filter="Location.X=200", set="Orientation=2")
            lib_set_current_component("MyOtherPart")
            modify_objects("ePin", scope="active_doc",
                           filter="Location.X=200", set="Orientation=2")

        Args:
            component_name: Component name (LibRef) in the active SchLib.

        Returns:
            Dict with ``success`` + ``name``, or an error if no SchLib
            is active or the component name isn't found in it.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_current_component",
            {"name": component_name},
        )

    @mcp.tool()
    async def lib_add_pins(
        pins: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add MANY pins to the current symbol in ONE call.

        PREFER THIS over adding pins one at a time (there is no singular
        variant). A 48-pin IC symbol built one pin at a time would be 48
        LLM turns; with this tool it's one turn + one
        PreProcess/PostProcess + one save.

        Args:
            pins: List of pin dicts, each with:
                - designator (str, required)
                - name       (str, required)
                - x, y       (int, mils), pin endpoint
                - length     (int, mils, default 200)
                - rotation   (int, default 0), 0/90/180/270
                - electrical_type (str, default "passive"), one of
                  input/output/bidirectional/passive/open_collector/
                  open_emitter/power/hiz/io
                - hidden     (bool, default False)
                - owner_part_id (int, optional). For multi-part
                  components (e.g. quad op-amp). 1..part_count picks a
                  specific sub-part; 0 shares the pin across all parts
                  (typical for V+ / V- power pins on a quad). Omit for
                  single-part symbols.
                - symbol_outer_edge (str, optional). IEEE decoration on
                  the pin's outer edge. "dot" draws the inversion bubble
                  of an active-low pin. Omit for none.
                - symbol_inner_edge (str, optional). Same, inner edge.
                  "clock" draws the clock wedge. The two are
                  independent, so an inverted clock sets both.
                  Either field also accepts any other TIeeeSymbol name
                  ("schmitt", "open_collector", "active_low_input", ...)
                  or a bare ordinal.
                - show_name / show_designator (bool, optional). Whether
                  the pin's name and number are DRAWN. Different from
                  `hidden`, which hides the pin itself: a resistor shows
                  both pins and neither label. Omit to leave Altium's
                  default.

        Example, one stage of a dual op-amp (sub-part 1) with shared
        power pins (sub-part 0):
            lib_add_pins(pins=[
                {"designator": "1", "name": "OUT1",  "x": 0,   "y": 0,
                 "rotation": 180, "electrical_type": "output",
                 "owner_part_id": 1},
                {"designator": "2", "name": "IN1-",  "x": 0,   "y": 100,
                 "rotation": 180, "electrical_type": "input",
                 "owner_part_id": 1},
                {"designator": "3", "name": "IN1+",  "x": 0,   "y": 200,
                 "rotation": 180, "electrical_type": "input",
                 "owner_part_id": 1},
                {"designator": "8", "name": "V+",    "x": 0,   "y": 400,
                 "rotation": 180, "electrical_type": "power",
                 "owner_part_id": 0},
            ])

        Returns:
            Dict with added, failed, total counts.
        """
        payload, skipped_invalid = _pins_payload(pins)

        if not payload:
            return {
                "error": "No valid pins (every entry was missing a designator)",
                "added": 0,
                "total": len(pins),
                "skipped_invalid": skipped_invalid,
            }

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_pins",
            {"pins": payload},
        )
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_symbol_rectangle(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        fill_color: int = -1,
        border_color: int = 0,
    ) -> dict[str, Any]:
        """Add a rectangle to the current symbol body.

        ADD THE BODY BEFORE THE PINS. Altium exposes no z-order on
        schematic primitives, so drawing order is insertion order and
        there is no send-to-back to correct it afterwards. A rectangle
        given a `fill_color` is SOLID, and one added after the pins paints
        over the pin names; the only fix is rebuilding the symbol. Build
        order is body, then pins, then anything drawn on top.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            fill_color: Fill color index (-1 = no fill)
            border_color: Border color index

        Returns:
            Dictionary confirming rectangle addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_rectangle",
            {
                "x1": _snap(int(x1)),
                "y1": _snap(int(y1)),
                "x2": _snap(int(x2)),
                "y2": _snap(int(y2)),
                "fill_color": fill_color,
                "border_color": border_color,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_text(
        texts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add MANY body-text items to the current symbol in ONE call.

        PREFER THIS over adding text one at a time (there is no singular
        variant). Free text on a symbol is an Altium ISch_Label: the
        polarity marks, pin-group headings and "NC" annotations that
        carry meaning the geometry alone does not.

        Args:
            texts: List of text dicts, each with:
                - text     (str, required). Empty strings are refused,
                  not placed: an empty label is invisible and
                  unselectable, and only turns up later as a stray
                  object.
                - x, y     (int, mils)
                - rotation (int, default 0), 0/90/180/270
                - font_size (int, default 10). Altium's own font size,
                  the number its font manager takes. NOT mils, and not
                  converted from mils: the relationship between the two
                  is not documented anywhere this project can verify, so
                  a conversion here would silently resize every item.
                - font_name (str, default "Arial")
                - bold, italic (bool, default False)
                - owner_part_id (int, optional). Same meaning as in
                  ``lib_add_pins``: 1..part_count picks a sub-part, 0
                  shares the text across all of them. Omit for
                  single-part symbols.

        Returns:
            Dict with ``added``, ``failed``, ``total`` counts.
        """
        op_strs: list[str] = []
        for item in texts:
            content = _payload_safe(str(item.get("text", "")).strip())
            if not content:
                continue
            fields = [
                f"text={content}",
                f"x={_snap(round(item.get('x', 0)))}",
                f"y={_snap(round(item.get('y', 0)))}",
                f"rotation={round(item.get('rotation', 0))}",
                f"font_size={int(item.get('font_size', 10))}",
                f"font_name={_payload_safe(item.get('font_name', 'Arial'))}",
                f"bold={'true' if item.get('bold') else 'false'}",
                f"italic={'true' if item.get('italic') else 'false'}",
            ]
            if item.get("owner_part_id") is not None:
                fields.append(f"owner_part_id={int(item['owner_part_id'])}")
            op_strs.append(";".join(fields))
        if not op_strs:
            return {"error": "No texts provided", "added": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.add_symbol_text",
            {"texts": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def lib_add_symbol_lines(
        lines: list[dict[str, Any]],
        grid: int = 25,
    ) -> dict[str, Any]:
        """Add MANY lines to the current symbol body in ONE call.

        PREFER THIS over adding lines one at a time (there is no singular
        variant). A 12-line LED diode glyph drawn line-by-line would be 12
        LLM turns + 12 IPC round-trips + 12 redraw passes; this tool does
        it in one turn
        with a single PreProcess/PostProcess pair and one editor
        redraw at the end.

        Args:
            lines: list of dicts, each with keys ``x1``, ``y1``,
                ``x2``, ``y2`` (mils, int), ``width`` (int 0-3,
                default 1).
            grid: snap grid (mils) for the line endpoints. Glyph lines do
                NOT need the 100-mil pin grid (rule 15), so the default is
                a finer 25 mils for clean glyph detail; pass 100 for the
                coarse grid or 0/1 to disable snapping.

        Returns:
            Dict with ``added``, ``failed``, ``total`` counts.
        """
        op_strs: list[str] = []
        for line in lines:
            fields = [
                f"x1={_snap_grid(line.get('x1', 0), grid)}",
                f"y1={_snap_grid(line.get('y1', 0), grid)}",
                f"x2={_snap_grid(line.get('x2', 0), grid)}",
                f"y2={_snap_grid(line.get('y2', 0), grid)}",
                f"width={int(line.get('width', 1))}",
            ]
            op_strs.append(";".join(fields))
        if not op_strs:
            return {"error": "No lines provided", "added": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.add_symbol_lines",
            {"lines": "~~".join(op_strs)},
        )

    # =========================================================================
    # Footprint Creation
    # =========================================================================

    @mcp.tool()
    async def lib_create_footprint(
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new PCB footprint in the active library.

        Args:
            name: Footprint name
            description: Footprint description

        Returns:
            Dictionary with created footprint information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.create_footprint",
            {"name": name, "description": description},
        )
        return result

    @mcp.tool()
    async def lib_create_standard_footprint(
        name: str,
        family: str,
        pin_count: int,
        pitch: float = 50.0,
        pad_w: float = 24.0,
        pad_h: float = 30.0,
        row_span: float = 0.0,
        shape: str = "roundrect",
        corner_radius: int = 25,
        silk: bool = True,
        courtyard: float = 10.0,
        body_w: float = 0.0,
        body_h: float = 0.0,
        hole: float = 0.0,
        rows: int = 0,
        cols: int = 0,
        exposed_pad: float = 0.0,
        skip: list[str] = None,
        tab_w: float = 0.0,
        tab_h: float = 0.0,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a COMPLETE standard footprint in ONE call.

        Composes parametric geometry with the bulk authoring tools: makes
        the footprint, then emits every pad + the silkscreen outline +
        the courtyard rectangle in two batched round-trips. Supply the
        package's recommended land-pattern dimensions from the datasheet
        (this carries no part library -- the numbers you pass ARE the
        footprint).

        Args:
            name: Footprint name (e.g. "SOIC-8_3.9x4.9").
            family: "chip" (2-pin passive) | "sip" (single-row header) |
                "dual" (SOIC/SOP/SON/SOT/DIP) | "header" (2-row pin/box
                header, IDC, SWD/JTAG -- column-major odd/even numbering) |
                "tab" (power/tab package: SOT-223, DPAK/D2PAK, TO-220 --
                signal pins one side + a large tab pad opposite, pass
                tab_w & tab_h) | "quad" (QFP/QFN) | "bga" (ball grid --
                pass rows & cols). SOT-23 is "dual" with pin_count=3.
                Through-hole (DIP/header/TO-220) = any family with hole > 0.
            pin_count: total pads (2 for chip; even for header; n signal
                pins for tab; divisible by 4 for quad; ignored for bga).
            rows, cols: BGA ball grid dimensions (bga family only).
            exposed_pad: centre thermal/exposed pad size (mils) for QFN/QFP
                (quad family); 0 = none. Gets designator pin_count+1.
            tab_w, tab_h: tab pad size (mils) for the "tab" family; the tab
                gets designator pin_count+1 (tie it to a signal pin in the
                schematic).
            skip: BGA ball designators to omit (depopulation), e.g.
                ["A1","E5"] (bga family only).
            pitch: pad centre-to-centre within a row (mils).
            pad_w, pad_h: pad size (mils). TWO MEANINGS, kept for the
                callers that rely on them: pad_w is the pad's X size in
                every family but quad, so in dual (rows left and right)
                and chip it runs ACROSS the row, along the span, while
                in sip, header and tab it runs along the row; in quad
                each side turns the pad and pad_w runs ALONG the row.
                For a footprint from a package drawing use
                lib_create_footprint_from_package, which sizes the lands
                by IPC-7351B from the terminal's lead_length (along the
                span) and lead_width (across it).
            row_span: centre-to-centre between the two rows / opposite
                sides (mils). Required for dual and quad.
            shape: pad shape (default "roundrect"); corner_radius is its %.
            silk: draw a silkscreen body outline (TopOverlay).
            courtyard: clearance (mils) past the extent for the courtyard
                rectangle (Mechanical1); 0 disables it.
            body_w, body_h: package body for the silk outline; default to
                the pad extent.
            hole: drill size (mils). >0 makes through-hole pads (DIP /
                header / axial): pin 1 rectangular, the rest round.

        Returns:
            Dict with the footprint name, pad/track counts, extent, and the
            per-step bridge results.
        """
        from eda_agent.design.footprint_gen import generate_footprint

        try:
            geom = generate_footprint(
                family, pin_count, pitch=pitch, pad_w=pad_w, pad_h=pad_h,
                row_span=row_span, shape=shape, silk=silk,
                courtyard=courtyard, body_w=body_w, body_h=body_h,
                hole=hole, rows=rows, cols=cols, exposed_pad=exposed_pad,
                skip=skip, tab_w=tab_w, tab_h=tab_h)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}

        bridge = get_bridge()
        created = await bridge.send_command_async(
            "library.create_footprint",
            {"name": name, "description": description},
        )

        # Shared helper, same reasoning as the pins above: designator
        # and shape were interpolated raw here.
        pads_payload, _ = _pads_payload([
            {**pad, "shape": pad.get("shape", shape),
             "corner_radius": corner_radius, "rotation": 0,
             "layer": "TopLayer"}
            for pad in geom.pads
        ])
        pads_res = await bridge.send_command_async(
            "library.add_footprint_pads", {"pads": pads_payload})

        tracks = geom.all_tracks()
        tracks_res: dict[str, Any] = {}
        if tracks:
            # Unrounded, like the pads: round() moved every fractional mil,
            # so a 1 mm body (39.37 mil) came out 39 mil wide.
            track_ops = [
                ";".join([
                    f"x1={format_length(t['x1'])}", f"y1={format_length(t['y1'])}",
                    f"x2={format_length(t['x2'])}", f"y2={format_length(t['y2'])}",
                    f"width={format_length(t['width'])}",
                    f"layer={_payload_safe(t['layer'])}",
                ])
                for t in tracks
            ]
            tracks_res = await bridge.send_command_async(
                "library.add_footprint_tracks", {"tracks": "~~".join(track_ops)})

        return {
            "footprint": name,
            "family": family,
            "pin_count": pin_count,
            "pads": len(geom.pads),
            "tracks": len(tracks),
            "width_mils": geom.width_mils,
            "height_mils": geom.height_mils,
            "create": created,
            "pads_result": pads_res,
            "tracks_result": tracks_res,
        }

    @mcp.tool()
    async def lib_create_footprint_from_package(
        package: Any,
        name: str = "",
        density: str = "",
        description: str = "",
        assembly_layer: str = "",
        courtyard_layer: str = "",
        drafting: Optional[dict[str, Any]] = None,
        set_height: bool = True,
    ) -> dict[str, Any]:
        """Create a COMPLETE footprint in the open PcbLib from a package drawing.

        Takes the package as the datasheet draws it, not a pad list: the
        lands come from IPC-7351B (toe, heel and side fillets with the
        tolerance stack-up) for the family and density asked for. Writes
        the pads (rounded rectangles with their corner ratio, rotation,
        holes, slots, non-plated holes), paste windows over an exposed
        pad with the pad's own paste closed, silkscreen kept clear of the
        copper with a pin 1 dot, the body outline and a .Designator on
        the assembly layer, the courtyard, and the footprint height from
        the package's maximum A. Origin at the package centroid. Every
        length goes in millimetres, unrounded.

        It then READS THE PADS BACK (lib_get_pad_geometry) and compares
        count, centres, sizes, rotation, shape, holes and any manual paste
        or mask expansion with what was sent, within one internal unit.
        A difference is a failure, not a warning.

        DATASHEET DISCIPLINE: transcribe the package from the
        manufacturer's drawing fetched in this conversation, each
        dimension as min/nom/max with its page and figure, and name the
        datasheet in ``source``. The engine sizes lands from the
        tolerances, so a nominal-only dimension gives a worse pattern and
        says so in ``warnings``.

        Args:
            package: the PackageSpec as an object (or its JSON text):
                ``family`` (chip, melf, molded, sod, sot23, sot89, sot223,
                dpak, soic, son, qfn, qfp, plcc, bga, lga, crystal2,
                crystal4, dip, header, radial, axial, to220, to92,
                connector, custom) and the dimensions it needs, each a
                number, ``[min, max]``, ``[min, nom, max]`` or
                ``{min, nom, max, cite}``. Axes: two-row parts have their
                rows LEFT and RIGHT, so ``lead_span_x`` is the tip to tip
                span across them (JEDEC E) and ``body_y`` the body along
                them (D); ``lead_length`` is the terminal along the span
                (L), ``lead_width`` across it (b). Exposed pad:
                ``ep_x``/``ep_y``. ``source``: {document, revision, page,
                ref, url}.
            name: footprint name; default the package's ``name``, else a
                generated one (e.g. SOIC-8-P1.27N).
            density: L, N or M (least, nominal, most). Default: the
                package's own, N unless it says otherwise.
            description: appended to the generated description, which
                already names the family, the standard, the density and
                the source.
            assembly_layer, courtyard_layer: mechanical layers for those
                roles. Empty picks them BY ROLE from the library: the
                layer whose kind is "Assembly Top" / "Courtyard Top",
                else the one whose name says so (the same reading
                lib_audit_footprint_policies uses). With neither, the
                call refuses and lists the library's layers rather than
                guessing a number.
            drafting: overrides of the drafting rules, e.g.
                ``{"silk_width": 0.15, "ep_paste_coverage": 0.6,
                "mask_expansion": 0.05}``. A non-zero mask expansion is
                written to every pad; at the default 1:1 the library's
                design rule decides.
            set_height: write the footprint height (package A max) with
                the existing height setter.

        Returns:
            ``{ok, footprint, library_path, created, layers, readback,
            pads, courtyard_mm, warnings, notes, ipc, steps}``. ``ok`` is
            true only when every step succeeded and the readback matched.
            A failure after the footprint was created says so: the
            footprint exists and is incomplete.
        """
        from ..bridge.exceptions import AltiumError
        from ..design.landpattern.emit import (
            LayerRoleError,
            altium_plan,
            model_from_package,
            resolve_altium_layers,
            verify_altium_readback,
        )
        from ..design.landpattern.spec import SpecError

        try:
            model = model_from_package(package, density=density, name=name,
                                       description=description,
                                       drafting=drafting)
        except SpecError as e:
            return {"ok": False, "reason": str(e), "created": False}
        fp_name = model.name
        if any(ord(ch) < 32 for ch in fp_name) or not fp_name.strip():
            return {"ok": False, "created": False,
                    "reason": f"footprint name {fp_name!r} is empty or has a "
                              "control character"}

        bridge = get_bridge()
        steps: dict[str, Any] = {}

        async def step(label: str, call) -> Any:
            try:
                reply = await call
            except AltiumError as e:
                steps[label] = {"error": str(e), "code": e.code}
                return None
            steps[label] = reply
            return reply

        def failed(label: str, why: str, created: bool) -> dict[str, Any]:
            out: dict[str, Any] = {
                "ok": False, "footprint": fp_name, "created": created,
                "reason": f"{label}: {why}", "steps": steps,
                "warnings": model.warnings}
            if created:
                out["note"] = (f"footprint {fp_name!r} exists and is "
                               "incomplete; remove it with lib_delete_footprint "
                               "before trying again")
            return out

        # The library the writes go to: GetCurrentPCBLibrary, the same one
        # create_footprint uses. Its path keeps the reads on that library.
        mech = await step("mech_layers", bridge.send_command_async(
            "library.get_mech_layers", {}))
        if mech is None and not (assembly_layer and courtyard_layer):
            return failed("mech_layers", "could not read the library's "
                          "mechanical layers; pass assembly_layer and "
                          "courtyard_layer", False)
        lib_path = str((mech or {}).get("library_path") or "")
        try:
            layers = resolve_altium_layers(
                (mech or {}).get("layers") or [], assembly_layer=assembly_layer,
                courtyard_layer=courtyard_layer)
            plan = altium_plan(model, layers)
        except (LayerRoleError, SpecError) as e:
            return {"ok": False, "created": False, "reason": str(e),
                    "steps": steps}

        listing = await step("existing", bridge.send_command_async(
            "library.get_footprints", {"library_path": lib_path}))
        if listing is None:
            return failed("existing", "could not list the library, so a "
                          "footprint of the same name cannot be ruled out", False)
        rows = listing.get("footprints") if isinstance(listing, dict) else None
        if not isinstance(rows, list):
            return failed("existing", "the library listing carried no "
                          "footprint list", False)
        names = {str(f.get("name")) for f in rows if isinstance(f, dict)}
        if fp_name in names:
            return failed("existing", f"a footprint named {fp_name!r} is "
                          "already in the library; pass another name", False)

        created = await step("create", bridge.send_command_async(
            "library.create_footprint",
            {"name": fp_name, "description": model.description}))
        if created is None:
            return failed("create", "the footprint was not created", False)

        def batch_ok(reply: Any, want: int) -> str:
            if not isinstance(reply, dict):
                return "no reply"
            if int(reply.get("added") or 0) != want or int(
                    reply.get("failed") or 0):
                return (f"added {reply.get('added')} of {want}, failed "
                        f"{reply.get('failed')}, unknown layers "
                        f"{reply.get('unknown_layers')!r}")
            if int(reply.get("property_failures") or 0):
                return (f"Altium refused {reply.get('property_failures')} "
                        "pad property writes (slot, plating or expansion)")
            return ""

        pads_payload, _ = _pads_payload(plan["pads"], "mm")
        reply = await step("pads", bridge.send_command_async(
            "library.add_footprint_pads", _with_units(
                {"pads": pads_payload, "footprint_name": fp_name}, "mm")))
        problem = "the call failed" if reply is None else batch_ok(
            reply, len(plan["pads"]))
        if problem:
            return failed("pads", problem, True)

        tracks_payload, _ = _tracks_payload(plan["tracks"], "mm")
        if tracks_payload:
            reply = await step("tracks", bridge.send_command_async(
                "library.add_footprint_tracks", _with_units(
                    {"tracks": tracks_payload, "footprint_name": fp_name},
                    "mm")))
            problem = "the call failed" if reply is None else batch_ok(
                reply, len(plan["tracks"]))
            if problem:
                return failed("tracks", problem, True)

        if plan["regions"]:
            regions_payload, refused = _regions_payload(plan["regions"])
            if refused:
                return failed("regions", "; ".join(refused), True)
            reply = await step("regions", bridge.send_command_async(
                "library.add_footprint_regions", _with_units(
                    {"regions": regions_payload, "footprint_name": fp_name},
                    "mm")))
            problem = "the call failed" if reply is None else batch_ok(
                reply, len(plan["regions"]))
            if problem:
                return failed("regions", problem, True)

        for i, arc in enumerate(plan["arcs"]):
            reply = await step(f"arc_{i}", bridge.send_command_async(
                "library.add_footprint_arc", _with_units({
                    "x_center": format_length(arc["x_center"]),
                    "y_center": format_length(arc["y_center"]),
                    "radius": format_length(arc["radius"]),
                    "start_angle": arc["start_angle"],
                    "end_angle": arc["end_angle"],
                    "width": format_length(arc["width"]),
                    "layer": arc["layer"], "footprint_name": fp_name}, "mm")))
            if reply is None:
                return failed(f"arc_{i}", "the call failed", True)

        for i, text in enumerate(plan["texts"]):
            reply = await step(f"text_{i}", bridge.send_command_async(
                "library.add_footprint_text", _with_units({
                    "text": text["text"],
                    "x": format_length(text["x"]),
                    "y": format_length(text["y"]),
                    "size": format_length(text["size"]),
                    "width": format_length(text["width"]),
                    "rotation": 0, "layer": text["layer"],
                    "component_name": fp_name}, "mm")))
            if reply is None:
                return failed(f"text_{i}", "the call failed", True)

        height = float(plan["height_mm"] or 0.0)
        if set_height and height > 0:
            reply = await step("height", bridge.send_command_async(
                "library.set_footprint_height",
                {"footprint_name": fp_name,
                 "height_mm": format_length(height)}))
            if reply is None:
                return failed("height", "the call failed", True)

        readback = await step("readback", bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": fp_name, "library_path": lib_path}))
        verdict = verify_altium_readback(plan, readback)
        notes = list(plan["notes"])
        if not (set_height and height > 0):
            notes.append("height: not written (set_height false, or the "
                         "package gives no height)")
        notes.append("3D body: the footprint height is set; no extruded body "
                     "is drawn, which would need a body-outline write this "
                     "bridge has not run live")
        return {
            "ok": bool(verdict.get("ok")),
            "footprint": fp_name,
            "library_path": lib_path,
            "family": model.family,
            "density": model.density,
            "description": model.description,
            "created": {"pads": len(plan["pads"]),
                        "tracks": len(plan["tracks"]),
                        "regions": len(plan["regions"]),
                        "arcs": len(plan["arcs"]),
                        "texts": len(plan["texts"]),
                        "height_mm": height if set_height else 0.0},
            "layers": layers,
            "readback": verdict,
            "pads": [{"number": p.number, "x": p.x, "y": p.y,
                      "width": p.width, "height": p.height, "shape": p.shape,
                      "rotation": p.rotation, "hole": p.hole,
                      "kind": p.kind} for p in model.pads],
            "courtyard_mm": list(model.courtyard_bbox()),
            "pin1": list(model.pin1),
            "warnings": model.warnings,
            "notes": notes,
            "ipc": model.ipc.get("calcs", {}),
            "steps": steps,
        }

    @mcp.tool()
    async def lib_add_footprint_pad(
        designator: str,
        x: float,
        y: float,
        x_size: Optional[float] = None,
        y_size: Optional[float] = None,
        hole_size: float = 0,
        shape: str = "rectangular",
        layer: str = "TopLayer",
        rotation: int = 0,
        corner_radius: int = 25,
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add a pad to a footprint: the named one, or the current one.

        Args:
            designator: Pad designator (e.g., "1", "2")
            x: X coordinate in mils
            y: Y coordinate in mils
            x_size: Pad X size (default 60 mil)
            y_size: Pad Y size (default 60 mil)
            hole_size: Drill hole size (0 for SMD). A drilled pad
                is forced through-hole (MultiLayer); a hole-less pad is
                SMD on `layer`.
            shape: Pad shape -- "round", "rectangular", "octagonal", or
                "roundrect" (rounded-rectangle, the modern IPC SMD
                default). An oval/obround is "round" with x_size != y_size.
            layer: Layer for an SMD pad ("TopLayer"/"BottomLayer"); ignored
                for through-hole pads (always MultiLayer).
            rotation: Pad rotation in degrees
            corner_radius: Corner radius percentage for shape="roundrect"
                (Altium stores RR radius as a %; 25 is typical).
            footprint_name: footprint to write into. Empty uses the
                editor's current footprint; a name makes that footprint
                current first and refuses if it cannot, since otherwise
                the primitives land in whichever footprint another call
                left current.
            units: "mil" (default) or "mm", for every length and
                coordinate here. Millimetres are kept exact: 1.625 mm
                used to arrive as 64 mil and read back as 1.6256 mm.

        Returns:
            Dictionary confirming pad addition
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        bridge = get_bridge()
        params: dict[str, Any] = {
            "designator": designator,
            "x": format_length(x),
            "y": format_length(y),
            "x_size": format_length(
                length_in(units, 60) if x_size is None else x_size),
            "y_size": format_length(
                length_in(units, 60) if y_size is None else y_size),
            "hole_size": format_length(hole_size),
            "shape": shape,
            "layer": layer,
            "rotation": rotation,
            "corner_radius": corner_radius,
        }
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_pad", _with_units(params, units))
        hint = BulkHintTracker.record_and_hint("lib_add_footprint_pad")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def lib_add_footprint_pads(
        pads: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add MANY pads to the current footprint in ONE call.

        PREFER THIS over looping `lib_add_footprint_pad`. A 64-pad QFP
        built one pad at a time is 64 LLM turns and 64
        PreProcess/PostProcess+save cycles; with this tool it's one turn,
        one PreProcess/PostProcess, one save. Pad coords are NOT snapped
        to a grid (footprints use fine pitches like 50 mils / 0.5 mm).

        Args:
            pads: List of pad dicts, each with:
                - designator (str, required) -- e.g. "1", "2", "A1"
                - x, y       (int, mils), pad centre
                - x_size, y_size (int, mils, default 60)
                - hole_size  (int, mils, default 0 = SMD)
                - shape      (str, default "rectangular"), one of
                  round / rectangular / octagonal / roundrect (modern IPC
                  rounded-rectangle SMD). Oval = round with x_size!=y_size.
                - corner_radius (int %, default 25) for shape="roundrect"
                - rotation   (float deg, default 0)
                - layer      (str, default "TopLayer"). Only used for SMD
                  pads (hole_size=0); a drilled pad is forced through-hole
                  (MultiLayer). Use "BottomLayer" for bottom-side SMD.
                Optional, written only when given:
                - hole_length (slot length, above hole_size: a slotted hole
                  hole_size wide, along the pad's X before rotation)
                - plated (false: a non-plated hole)
                - paste_expansion, mask_expansion (a manual expansion that
                  overrides the design rule; minus the larger pad side
                  closes the paste opening)
                The reply counts them (slots, unplated, expansions) and
                any write this Altium build refused (property_failures).

        Example, a 4-pad 0402 + corner pad in one call:
            lib_add_footprint_pads(pads=[
                {"designator": "1", "x": -25, "y": 0, "x_size": 30,
                 "y_size": 40},
                {"designator": "2", "x":  25, "y": 0, "x_size": 30,
                 "y_size": 40},
            ])

        ``footprint_name`` picks the footprint to write into. Empty uses
        the editor's current footprint; a name makes that footprint
        current first and refuses if it cannot, since otherwise the
        primitives land in whichever footprint another call left current.

        ``units`` is "mil" (default) or "mm" for every pad in the call:
        one land pattern, one unit, so half a footprint cannot be drawn
        in the wrong one. Millimetres are kept exact.

        Returns:
            Dict with added, failed, total counts.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, skipped_invalid = _pads_payload(pads, units)

        if not payload:
            return {
                "error": "No valid pads (every entry was missing a designator)",
                "added": 0,
                "total": len(pads),
                "skipped_invalid": skipped_invalid,
            }

        bridge = get_bridge()
        params: dict[str, Any] = {"pads": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_pads", _with_units(params, units))
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_footprint_track(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: Optional[float] = None,
        layer: str = "TopOverlay",
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add a track to the current footprint (for silkscreen/courtyard).

        Args:
            x1: Start X in mils
            y1: Start Y in mils
            x2: End X in mils
            y2: End Y in mils
            width: Track width in mils
            layer: Layer name. Default "TopOverlay" (silkscreen). Any
                Altium layer is accepted, e.g. "BottomOverlay" or
                "Mechanical1".."Mechanical16" for courtyard / assembly
                outlines.
            footprint_name: footprint to write into. Empty uses the
                editor's current footprint; a name makes that footprint
                current first and refuses if it cannot.
            units: "mil" (default) or "mm", for every length and
                coordinate here, kept exact rather than rounded to
                whole mils.

        Returns:
            Dictionary confirming track addition
        """
        bridge = get_bridge()
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        params: dict[str, Any] = {
            "x1": format_length(x1), "y1": format_length(y1),
            "x2": format_length(x2), "y2": format_length(y2),
            "width": format_length(
                length_in(units, 10) if width is None else width),
            "layer": layer}
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_track", _with_units(params, units))
        hint = BulkHintTracker.record_and_hint("lib_add_footprint_track")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def lib_add_footprint_tracks(
        tracks: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add MANY tracks to the current footprint in ONE call.

        PREFER THIS over looping `lib_add_footprint_track`. A silkscreen
        outline + assembly outline is 8+ segments; built one at a time
        that's 8+ LLM turns and 8+ PreProcess/PostProcess+save cycles.
        Batched it's one turn, one PreProcess/PostProcess, one save.

        Args:
            tracks: List of track dicts, each with:
                - x1, y1, x2, y2 (int, mils) -- endpoints
                - width (int, mils, default 10)
                - layer (str, default "TopOverlay" silkscreen). Any
                  Altium layer name, e.g. "BottomOverlay" or
                  "Mechanical1".."Mechanical16" for courtyard/assembly.

        Example, a 100x60 mil silkscreen rectangle in one call:
            lib_add_footprint_tracks(tracks=[
                {"x1": -50, "y1": -30, "x2":  50, "y2": -30},
                {"x1":  50, "y1": -30, "x2":  50, "y2":  30},
                {"x1":  50, "y1":  30, "x2": -50, "y2":  30},
                {"x1": -50, "y1":  30, "x2": -50, "y2": -30},
            ])

        ``footprint_name`` picks the footprint to write into. Empty uses
        the editor's current footprint; a name makes that footprint
        current first and refuses if it cannot, since otherwise the
        primitives land in whichever footprint another call left current.

        ``units`` is "mil" (default) or "mm" for every track in the
        call, kept exact rather than rounded to whole mils.

        Returns:
            Dict with added, failed, total counts.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, skipped_invalid = _tracks_payload(tracks, units)
        op_strs = [op for op in payload.split("~~") if op]

        if not op_strs:
            return {
                "error": "No valid tracks (each needs x1,y1,x2,y2)",
                "added": 0,
                "total": len(tracks),
                "skipped_invalid": skipped_invalid,
            }

        bridge = get_bridge()
        params: dict[str, Any] = {"tracks": "~~".join(op_strs)}
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_tracks", _with_units(params, units))
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_footprint_regions(
        regions: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add solid polygon regions to a PcbLib footprint in ONE call.

        What a footprint needs beyond pads and lines: paste windows over
        an exposed pad (on TopPaste, with the pad's own paste closed by a
        negative paste_expansion in lib_add_footprint_pads), or any filled
        shape on a mechanical or overlay layer.

        Args:
            regions: list of ``{"layer": ..., "points": [[x, y], ...]}``,
                three or more points each, relative to the footprint
                origin. The layer is REQUIRED: there is no default,
                because a solid shape defaulted onto copper is a short.
            footprint_name: footprint to write into. Empty uses the
                editor's current footprint; a name makes that footprint
                current first and refuses if it cannot.
            units: "mil" (default) or "mm", for every point, kept exact.

        Returns:
            Dict with added, failed, bad_points, unknown_layers, total. A
            region refused here before sending is listed in
            ``refused`` with the reason.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, problems = _regions_payload(regions)
        if not payload:
            return {"error": "No valid regions", "added": 0,
                    "total": len(regions or []), "refused": problems}
        bridge = get_bridge()
        params: dict[str, Any] = {"regions": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_regions", _with_units(params, units))
        if isinstance(result, dict) and problems:
            result["refused"] = problems
        return result

    @mcp.tool()
    async def lib_add_footprint_fills(
        fills: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add rectangular fills to a PcbLib footprint in ONE call.

        A fill stays an Altium fill, not a four-point region, so it reads
        back and edits as one. Use it for a grid of paste windows over an
        exposed pad, or a solid block on a mechanical or overlay layer.
        For any other outline use lib_add_footprint_regions.

        Args:
            fills: list of ``{"layer": ..., "x1": .., "y1": .., "x2": ..,
                "y2": ..}``, opposite corners relative to the footprint
                origin, with an optional ``"rotation"`` in degrees about
                the fill's centre. The layer is REQUIRED: there is no
                default, because a solid shape defaulted onto copper is a
                short. A fill with no area is refused.
            footprint_name: footprint to write into. Empty uses the
                editor's current footprint; a name makes that footprint
                current first and refuses if it cannot.
            units: "mil" (default) or "mm", for every corner, kept exact.

        Returns:
            Dict with added, failed, bad_extent, unknown_layers, total. A
            fill refused here before sending is listed in ``refused`` with
            the reason.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, problems = _fills_payload(fills)
        if not payload:
            return {"error": "No valid fills", "added": 0,
                    "total": len(fills or []), "refused": problems}
        bridge = get_bridge()
        params: dict[str, Any] = {"fills": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_fills", _with_units(params, units))
        if isinstance(result, dict) and problems:
            result["refused"] = problems
        return result

    @mcp.tool()
    async def lib_add_footprint_arc(
        x_center: float,
        y_center: float,
        radius: float,
        start_angle: float = 0,
        end_angle: float = 360,
        width: Optional[float] = None,
        layer: str = "TopOverlay",
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add an arc to the current footprint.

        Args:
            x_center: Center X in mils
            y_center: Center Y in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            width: Line width in mils
            layer: Layer name. Default "TopOverlay" (silkscreen). Any
                Altium layer accepted, e.g. "Mechanical1".."Mechanical16"
                for pin-1 / assembly markers.
            footprint_name: footprint to write into. Empty uses the
                editor's current footprint; a name makes that footprint
                current first and refuses if it cannot.
            units: "mil" (default) or "mm", for every length and
                coordinate here, kept exact rather than rounded to
                whole mils.

        Returns:
            Dictionary confirming arc addition
        """
        bridge = get_bridge()
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        params: dict[str, Any] = {
            "x_center": format_length(x_center),
            "y_center": format_length(y_center),
            "radius": format_length(radius),
            "start_angle": start_angle,
            "end_angle": end_angle,
            "width": format_length(
                length_in(units, 10) if width is None else width),
            "layer": layer,
        }
        if footprint_name:
            params["footprint_name"] = footprint_name
        result = await bridge.send_command_async(
            "library.add_footprint_arc", _with_units(params, units))
        return result

    @mcp.tool()
    async def lib_add_footprint_text(
        text: str,
        x: float = 0,
        y: float = 0,
        size: Optional[float] = None,
        width: Optional[float] = None,
        rotation: int = 0,
        layer: str = "TopOverlay",
        use_ttfont: bool = False,
        mirror: bool = False,
        library_path: Optional[str] = None,
        component_name: Optional[str] = None,
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add a text primitive to a PcbLib footprint.

        Trap baked into this handler: in a PcbLib, adding a primitive
        only to the footprint does NOT register it with the placement
        editor -- the text shows up only after a save+reload. This
        tool adds the text to BOTH the footprint and the underlying
        board, then broadcasts ``PCBM_BoardRegisteration`` to both, which
        is the pattern that registers the primitive reliably.

        Args:
            text: The string to place. Required.
            x, y: Coordinates in mils, relative to the FOOTPRINT's origin,
                which is what the handler has always actually done
                (``Footprint.X + MilsToCoord(x)``). The old wording said
                "board origin" and that was wrong: a PcbLib footprint sits
                at Altium's library origin, 50000 mils out, so the two
                readings differ by more than a metre. The pad, track and
                arc tools wrote absolute board coordinates and produced
                footprints whose geometry was nowhere near them; they now
                match this one.
            size: Text height in mils. 50 is a common silkscreen size;
                drop to 30-40 for tight footprints.
            width: Stroke width in mils. 8 reads cleanly at 50 mil
                size; scale with ``size``.
            rotation: Rotation in degrees (0, 90, 180, 270 typical).
            layer: Layer name resolved by GetLayerFromString
                (``TopOverlay``, ``BottomOverlay``, ``TopSolder``,
                ``BottomSolder``, ``Mechanical1`` ... ``Mechanical32``).
            use_ttfont: ``True`` for TrueType; default ``False`` is the
                vector stroke font that fab houses prefer.
            mirror: Mirror the text. Set this for anything on a BOTTOM
                layer, or it reads backwards on the finished board;
                ``audit_find_mirrored_pcb_text`` reports both halves of
                the mistake (bottom text unmirrored, top text mirrored).
            library_path: Optional .PcbLib to focus before adding.
                Defaults to the active document.
            component_name: Optional footprint name to switch to before
                adding. Defaults to the currently active footprint.
            units: "mil" (default) or "mm", for every length and
                coordinate here, kept exact rather than rounded to
                whole mils.

        Returns:
            Dict with ``success``, ``footprint``, ``text``, ``layer``,
            ``x``, ``y``.
        """
        if not text:
            return {"ok": False,
                    "reason": "text is required: pass the string to place"}
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        bridge = get_bridge()
        params: dict[str, Any] = _with_units({
            "text": text,
            "x": format_length(x), "y": format_length(y),
            "size": format_length(length_in(units, 50) if size is None else size),
            "width": format_length(length_in(units, 8) if width is None else width),
            "rotation": rotation, "layer": layer,
        }, units)
        if use_ttfont:
            params["use_ttfont"] = "true"
        if mirror:
            params["mirror"] = "true"
        if library_path:
            params["library_path"] = library_path
        if component_name:
            params["component_name"] = component_name
        result = await bridge.send_command_async(
            "library.add_footprint_text", params,
        )
        return result

    @mcp.tool()
    async def lib_extract_intlib(
        intlib_path: str,
    ) -> dict[str, Any]:
        """Extract .SchLib + .PcbLib sources from an .IntLib.

        Opens the integrated library in Altium, runs the editor's
        ``Extract Sources`` command, then probes Altium's conventional
        output locations (a sibling folder named after the IntLib base
        name, falling back to the IntLib's own directory) to report
        which source files actually appeared on disk.

        After this returns with ``sch_lib_found`` and / or
        ``pcb_lib_found`` true, the produced files can be opened with
        the rest of the library toolset -- ``lib_get_components``,
        ``lib_get_footprints``, ``lib_copy_component``, etc. -- by
        passing the reported ``sch_lib_path`` / ``pcb_lib_path`` as
        the ``library_path`` argument.

        Args:
            intlib_path: Absolute path to the .IntLib file.

        Returns:
            Dict with ``intlib_path``, ``extract_dir``, ``sch_lib_path``,
            ``sch_lib_found``, ``pcb_lib_path``, ``pcb_lib_found``. A
            ``_found`` flag of False means Altium did not produce that
            source -- either the extract command name needs adjusting
            for the local Altium build, the IntLib does not contain that
            kind of source, or write permissions prevented the dump.
        """
        if not intlib_path:
            return {"ok": False, "reason":
                    "intlib_path is required: absolute path to the .IntLib"}
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.extract_intlib",
            {"intlib_path": intlib_path},
            timeout=60.0,
        )
        return result or {}

    @mcp.tool()
    async def lib_get_footprints(
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Enumerate every footprint in a PcbLib.

        PcbLib counterpart to ``lib_get_components`` for SchLibs. Walks
        the PCB library with ``IPCB_Library.LibraryIterator_Create``
        and returns one entry per footprint with its name and
        description.

        Args:
            library_path: Optional .PcbLib path to focus first.
                Defaults to the focused document.

        Returns:
            Dict with ``library_path``, ``count`` and ``footprints``
            (a list of ``{name, description}``).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.get_footprints", params,
        )
        return result

    @mcp.tool()
    async def lib_audit_footprint_policies(
        library_path: Optional[str] = None,
        policy: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Sweep a PcbLib and flag footprints that break the library's own
        conventions: inconsistent layer usage, pad rules, pin-1 markings,
        courtyard, silkscreen, 3D models, designator style.

        For each policy dimension it learns the library's DOMINANT convention
        across all footprints and reports every footprint that deviates (so it
        works on any house style without hard-coded rules). Pass ``policy`` to
        pin a dimension to a required value instead, e.g.
        ``{"silk_layer": "Top Overlay", "courtyard": true}``.

        Each finding names the footprint, the dimension, and the expected vs
        actual value: enough to drive a fix, not just a report.

        Designators are checked for presence, layer, height, and for sitting on
        the footprint's AVERAGE PAD CENTRE rather than the library origin (which
        is arbitrary). Tune the centring window with
        ``policy={"designator_center_tol": <mils>}``.

        Geometry is read with the bulk ``library.get_library_geometry``
        command, which walks the library once and pages the result. A library
        of ~1000 footprints is one pass and a handful of round trips rather
        than one round trip (and one full library re-scan) per footprint.

        Args:
            library_path: Optional .PcbLib to focus first; defaults to the
                focused library.
            policy: Optional explicit conventions to enforce; omitted
                dimensions are inferred from the library.

        Returns ``{library_path, footprint_count, conventions, findings,
        summary}``.
        """
        from ..design.footprint_policy import (
            audit_footprint_library,
            plan_footprint_fixes,
        )

        bridge = get_bridge()
        footprints, resolved_path = await _read_library_geometry(
            bridge, library_path)
        report = audit_footprint_library(footprints, policy=policy)
        report["fixes"] = plan_footprint_fixes(report)
        report["library_path"] = resolved_path
        return report

    @mcp.tool()
    async def lib_convert_designators_to_stroke(
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Convert every TrueType ``.Designator`` in a PcbLib to a stroke font.

        A TrueType PCB text will not persist a position change made through
        ``XLocation``: the write reads back changed, then Altium recomputes the
        position from the TrueType layout on reload and it reverts. That is why a
        handful of designators per library refuse to centre. ``Bold``/``Italic``
        are TrueType-only attributes (a stroke text cannot be bold), so a bold or
        italic designator is the reliable TrueType tell.

        This clears ``UseTTFonts``, ``Bold`` and ``Italic`` on each such
        designator, reads back to confirm, saves, and reloads. Afterwards those
        designators are stroke and ``lib_fix_designators`` can centre them.

        Returns ``{library_path, designators, converted, names, saved}``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        res = await bridge.send_command_async(
            "library.convert_designators_to_stroke", params,
            timeout=_GEOMETRY_TIMEOUT)
        res = res if isinstance(res, dict) else {}
        saved = False
        if res.get("converted"):
            resolved = res.get("library_path") or library_path
            try:
                await bridge.send_command_async(
                    "application.save_all", {}, timeout=_GEOMETRY_TIMEOUT)
                saved = True
            except Exception as e:
                res.setdefault("errors", []).append(f"save failed: {e}")
            await _reload_library(bridge, resolved)
        res["saved"] = saved
        return res

    @mcp.tool()
    async def lib_reload_library(library_path: str) -> dict[str, Any]:
        """Close and reopen a PcbLib so Altium rebuilds its caches from disk.

        ``IPCB_Text.BoundingRectangle`` is populated when the document loads and
        is NOT refreshed when a text moves or is resized, not even by
        ``GraphicallyInvalidate``. Anything that reads a text's extent after
        writing to it therefore gets the OLD box. Reload between a write and the
        next read.

        SAVE FIRST: this does not save, and closing a dirty document loses the
        edits or raises a prompt. ``lib_fix_designators`` already does this.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.reload_library", {"library_path": library_path},
            timeout=_GEOMETRY_TIMEOUT)

    @mcp.tool()
    async def lib_probe_designator(
        footprint_name: str,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Diagnostic: dump one footprint's raw designator geometry.

        Read-only. Returns the footprint origin and bounding rectangle, the pad
        extents, and the ``.Designator`` anchor, bounding rectangle, size and
        width: all in native TCoord. Use it to establish what
        ``IPCB_Text.BoundingRectangle`` actually measures before trusting it to
        centre anything.

        Returns ``{footprint, fp_x, fp_y, fp_rect, pad_count, pad_rect,
        desig_anchor, desig_rect, desig_size, desig_width, desig_text}``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"footprint_name": footprint_name}
        if library_path:
            params["library_path"] = library_path
        return await bridge.send_command_async(
            "library.probe_designator", params, timeout=_GEOMETRY_TIMEOUT)

    @mcp.tool()
    async def lib_fix_designators(
        library_path: Optional[str] = None,
        dry_run: bool = True,
        fix_layer: bool = True,
        fix_center: bool = True,
        fix_height: bool = False,
        add_missing: bool = False,
        policy: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Bring every footprint's ``.Designator`` onto the library's OWN
        convention: the layer, height and centring the majority already use.

        Nothing is hard-coded. The target layer, its raw ordinal, the norm
        height and the centring tolerance are all inferred from this library
        (see ``lib_audit_footprint_policies``); ``policy`` overrides any of
        them. "Centred" means on the AVERAGE PAD CENTRE, not the library
        origin.

        DEFAULTS TO A DRY RUN. With ``dry_run=True`` (the default) it reports
        exactly which footprints it would touch, on which layer, and at which
        coordinates, and writes nothing. Pass ``dry_run=False`` to apply.

        Applying RUNS TO CONVERGENCE: apply, save, reload the PcbLib, re-plan,
        repeat until nothing is left. The reload is mandatory, not cosmetic:
        Altium caches each text's bounding rectangle at load and never refreshes
        it when the text moves or is resized, so a second pass without a reload
        would centre against stale geometry and shove designators off the part.
        Resizing a text also changes its box, so height fixes and centring
        settle over successive passes rather than one.

        PARTIAL SUCCESS IS EXPECTED AND REPORTED, NOT HIDDEN. A small number of
        designators (a handful per library) do not persist a position change
        through Altium's save for reasons not yet diagnosed: the write reads
        back changed but the value reverts on reload. Rather than loop forever or
        claim success, the tool stops when a pass makes no progress and returns
        ``converged: false`` with the offending footprints in ``unrepairable``
        for a human to centre by hand. Everything else is applied. ``applied``
        counts only writes confirmed by read-back, never attempts.

        Args:
            library_path: .PcbLib to fix; defaults to the focused library.
            dry_run: report the plan without writing (default True).
            fix_layer: move designators off the convention layer onto it.
            fix_center: re-centre designators beyond the library's tolerance.
            fix_height: also normalise text height to the library norm.
            add_missing: create a ``.Designator`` on footprints that lack one.
            policy: explicit conventions, e.g.
                ``{"designator_center_tol": 50, "designator_height": 20}``.

        Returns ``{library_path, dry_run, conventions, planned, applied,
        created, passes, converged, failed, skipped, actions}``.
        """
        from ..design.footprint_policy import plan_designator_repairs

        bridge = get_bridge()
        footprints, resolved_path = await _read_library_geometry(
            bridge, library_path)
        if not footprints:
            return {"success": False,
                    "error": "no footprints (open the PcbLib)"}

        plan = plan_designator_repairs(
            footprints, fix_layer=fix_layer, fix_center=fix_center,
            fix_height=fix_height, add_missing=add_missing, policy=policy)
        actions = plan["actions"]

        result: dict[str, Any] = {
            "library_path": resolved_path,
            "dry_run": dry_run,
            "conventions": plan["conventions"],
            "planned": len(actions),
            "skipped": plan["skipped"],
            "actions": actions,
        }
        if dry_run or not actions:
            result["applied"] = 0
            result["failed"] = []
            return result

        # Baseline BEFORE any write: the loop rebinds `footprints` each pass,
        # and blaming a write for duplicates that were already there makes the
        # guard cry wolf and stop being believed.
        from ..design.footprint_policy import _designator_count
        before_dupes = {fp.get("name") for fp in footprints
                        if _designator_count(fp) > 1}

        # Apply, save, RELOAD, re-plan -- until nothing is left to do.
        #
        # One pass is never enough. Altium caches each text's BoundingRectangle
        # at load and does not refresh it when XLocation or Size changes, so
        # after a write every rectangle in memory is stale. Centring is computed
        # from that rectangle, so a second pass in the same session would move
        # designators by a wrong delta. Reloading the document drops the caches;
        # the next read gets true geometry. Resizing a text also changes its
        # box, so height fixes and centring genuinely need separate passes.
        failed: list[dict] = []
        applied, created, saved, passes = 0, 0, False, 0
        touched: set = set()

        while actions and passes < _MAX_PASSES:
            passes += 1
            touched.update(a["footprint"] for a in actions)
            a, c = await _apply_edits(bridge, actions, resolved_path, failed)
            applied += a
            created += c
            if not a:
                break

            try:
                await bridge.send_command_async(
                    "application.save_all", {}, timeout=_GEOMETRY_TIMEOUT)
                saved = True
            except Exception as e:
                failed.append({"footprint": "*", "error": f"save failed: {e}"})
                break

            if not await _reload_library(bridge, resolved_path):
                failed.append({
                    "footprint": "*",
                    "error": "library reload failed; stopping after one pass "
                             "(further passes would read a stale bounding-box "
                             "cache and misplace designators)"})
                break

            footprints, _ = await _read_library_geometry(bridge, resolved_path)
            next_actions = plan_designator_repairs(
                footprints, fix_layer=fix_layer, fix_center=fix_center,
                fix_height=fix_height, add_missing=add_missing,
                policy=policy)["actions"]

            # A pass that changes nothing will never change anything. Repeating
            # it burns passes and, worse, reports a growing `applied` count for
            # edits that are not landing.
            if _same_plan(actions, next_actions):
                stuck = [a["footprint"] for a in next_actions]
                failed.append({
                    "footprint": "*",
                    "error": f"{len(stuck)} designators would not move; "
                             f"stopping (no progress): {', '.join(stuck[:10])}"})
                actions = next_actions
                break
            actions = next_actions

        result["passes"] = passes
        result["converged"] = not actions
        if actions:
            result["remaining"] = len(actions)
            result["unrepairable"] = [a["footprint"] for a in actions][:25]

        # Read the library BACK and confirm no footprint gained a second
        # .Designator. A create that lands on the wrong component is invisible
        # to the writer, so the only honest check is to re-measure.
        if applied:
            from ..design.footprint_policy import _designator_offsets

            after, _ = await _read_library_geometry(bridge, resolved_path)
            dupes = [fp.get("name") for fp in after
                     if _designator_count(fp) > 1
                     and fp.get("name") not in before_dupes]
            result["verified_no_duplicates"] = not dupes
            result["preexisting_duplicates"] = sorted(before_dupes)
            if dupes:
                result["duplicates_created"] = dupes[:25]
                failed.append({
                    "footprint": "*",
                    "error": f"{len(dupes)} footprints now have duplicate "
                             f".Designator strings: RESTORE FROM BACKUP"})

            # A designator left at the board origin instead of on its part sits
            # tens of thousands of mils away. Nothing legitimate does that.
            stray = [fp.get("name") for fp, off, _, _ in
                     _designator_offsets(after)
                     if fp.get("name") in touched and off > _STRAY_MILS]
            result["verified_no_stray_designators"] = not stray
            if stray:
                result["strays_created"] = stray[:25]
                failed.append({
                    "footprint": "*",
                    "error": f"{len(stray)} designators landed >{_STRAY_MILS} "
                             f"mils from their pads: RESTORE FROM BACKUP"})

        result["applied"] = applied
        result["created"] = created
        result["failed"] = failed
        result["saved"] = saved
        return result

    # =========================================================================
    # Component Linking
    # =========================================================================

    @mcp.tool()
    async def lib_update_footprint_heights_from_3d(
        mode: str = "raise",
    ) -> dict[str, Any]:
        """Sweep the active PCB Library: for every footprint, find the
        tallest 3D body and write its ``OverallHeight`` to
        ``Footprint.Height``.

        Footprint.Height is what Altium's placement-collision DRC
        uses to enforce height-clearance rules (don't place a tall
        electrolytic under a low overhang; don't sandwich an LCD
        connector under another PCB in a multi-board stack-up).
        Libraries shipped from vendors without explicit heights default
        to 0 which makes the DRC silently no-op -- a real production
        risk caught only at first-article assembly.

        Args:
            mode: ``raise`` (default) only ever increases a height, so a
                hand-set "I know this part is 5mm despite the model
                being 3mm" survives. ``match`` also LOWERS one to the
                model.

        Reach for ``match`` when heights are too TALL, which raising
        cannot fix and which is the more damaging fault: a footprint
        claiming 50mm when the part is 3mm fails placement-collision
        DRC against everything near it and blocks placements that are
        fine, where a too-low height merely fails to catch a real
        collision. To correct one footprint, or one with no model at
        all, use ``lib_set_footprint_height``.

        Safety:
          - NEITHER MODE WRITES ZERO. A footprint with no 3D body
            yields no measurement, and writing the 0 that implies would
            silently disable the very DRC rule this arms. Those come
            back as ``without_model`` with their names.
          - Does NOT save the library; review the ``items[]`` diff and
            save in Altium.

        Returns:
            Dict with ``mode``, ``inspected``, ``updated``, ``lowered``,
            ``without_model``, ``without_model_names``, and ``items``
            (``{name, old_height_mm, new_height_mm}`` per footprint).
        """
        mode = (mode or "raise").strip().lower()
        if mode not in ("raise", "match"):
            return {
                "ok": False,
                "reason": (
                    f"mode must be 'raise' (only increase, the default) or "
                    f"'match' (also lower to the model). Got {mode!r}."),
            }
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.update_footprint_heights_from_3d", {"mode": mode},
            timeout=60.0,
        )

    @mcp.tool()
    async def lib_set_footprint_height(
        height_mm: float,
        footprint_name: str = "",
    ) -> dict[str, Any]:
        """Set one footprint's Height directly, up or down.

        The sweep can only derive a height from a 3D body, which leaves
        two cases it cannot serve: a part with no model, and a part
        whose model is wrong. There was no setter at all before this, so
        a footprint carrying an absurd height could be read and not
        corrected.

        WHY TOO TALL IS WORSE THAN TOO SHORT. Footprint.Height drives
        placement-collision DRC. A footprint claiming 50mm when the part
        is 3mm fails against everything near it and blocks placements
        that are fine. A too-low height only fails to catch a real
        collision. The first floods the report and gets the rule
        switched off; the second is quiet.

        ZERO IS ACCEPTED AND IS NOT NEUTRAL. It disables the rule for
        that footprint rather than relaxing it, so nothing is ever
        flagged against it however tall the real part is. The reply says
        so when zero is written.

        Args:
            height_mm: the height in MILLIMETRES, not mils. Must be
                non-negative.
            footprint_name: which footprint. Empty uses the library's
                current component.

        Returns:
            ``{name, old_height_mm, new_height_mm, changed, saved,
            save_note, note}``. ``saved`` is always false: the library
            is modified in memory and left for you to review and save.
        """
        try:
            height = float(height_mm)
        except (TypeError, ValueError):
            return {"ok": False,
                    "reason": f"height_mm must be a number, got {height_mm!r}"}
        if height < 0 or height != height:      # NaN fails both comparisons
            return {"ok": False,
                    "reason": (f"height_mm must be a non-negative number of "
                               f"millimetres, got {height_mm!r}")}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_footprint_height",
            {"height_mm": height, "footprint_name": footprint_name},
        )

    @mcp.tool()
    async def lib_link_footprint(
        component_name: str,
        footprint_name: str,
        footprint_library: str = "",
        replace: bool = True,
    ) -> dict[str, Any]:
        """Link a footprint to a schematic component.

        With replace=True (the default) any existing footprint (PCBLIB) model
        on the component is removed first, so re-linking replaces rather than
        appends. Pass replace=False to keep prior footprint models and add
        another (the old append behaviour, which bloats a component with
        duplicate models).

        Args:
            component_name: Name of the schematic component. Resolved by
                library reference and made current before linking, so the
                model lands on the symbol you name rather than on whatever
                was last created. Leave empty to target the current
                component.
            footprint_name: Name of the footprint to link
            footprint_library: NOT APPLIED, accepted only so existing
                callers keep working. The footprint is bound by NAME via
                the implementation's datafile link, with a deliberately
                EMPTY location: passing a .PcbLib path there wedges
                AD26 (see Lib_LinkFootprint in Library.pas). Install the
                library and keep footprint names unambiguous instead.
            replace: remove existing footprint models before adding (default True)

        Returns:
            Dictionary confirming link
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.link_footprint",
            {
                "component_name": component_name,
                "footprint_name": footprint_name,
                # footprint_library is deliberately NOT sent: nothing on
                # the Pascal side reads it, and the only place it could
                # go is the datafile link location, which wedges AD26.
                "replace": "true" if replace else "false",
            },
        )
        return result

    @mcp.tool()
    async def lib_link_3d_model(
        component_name: str,
        model_path: str,
        offset_x: float = 0,
        offset_y: float = 0,
        offset_z: float = 0,
        rotation_x: float = 0,
        rotation_y: float = 0,
        rotation_z: float = 0,
    ) -> dict[str, Any]:
        """Link a 3D STEP model to a PcbLib footprint.

        Loads the STEP geometry into the footprint as an IPCB_ComponentBody
        (the real 3D body that drives the 3D view and the height/placement
        DRC), so the **active document must be the .PcbLib** that contains the
        footprint. ``component_name`` selects the footprint by name (empty uses
        the library's current footprint).

        Args:
            component_name: Name of the footprint in the active PcbLib
            model_path: Path to the 3D model file (.step, .stp); must exist
            offset_x: X offset in mils from the FOOTPRINT's origin. The
                body is always moved onto that origin first: it arrives
                at the board origin, and a footprint open in the editor
                sits at Altium's library origin, about 50000,50000 mil,
                so an offset of 50000 used to be needed to bring it back.
                Do not add that any more.
            offset_y: Y offset in mils, applied with offset_x
            offset_z: Z offset in mils, sets the body's StandoffHeight.
                This is the common adjustment: lifting a connector body
                off the board so it sits on its pads rather than through
                them.
            rotation_x: NOT APPLIED, accepted for signature stability.
                Set it in the library editor after linking.
            rotation_y: NOT APPLIED, same reason as rotation_x.
            rotation_z: NOT APPLIED. This one used to assign
                ``Body.Rotation``, and IPCB_ComponentBody has no such
                property on AD26 26.9.1.9. MEASURED: it raised
                "Undeclared identifier: Rotation", which DelphiScript
                cannot catch, so the Try around it never fired and the
                resulting modal took the whole polling loop down with
                it. Passing a rotation used to cost you the bridge.
                The rotation lives on the MODEL, not the body, and has
                to be set before the model is attached
                (``Model.SetState`` in AutoSTEPplacer.pas). Its four
                arguments are undocumented, so they are not guessed at
                here. Rotate in the library editor for now.

        Returns:
            Dict with ``success``, ``footprint``, ``model``,
            ``footprint_origin_mils`` and ``moved_to_footprint_origin``
            (false means the body may sit at the board origin, away
            from the footprint), and
            ``applied`` -- which adjustments were actually written to
            the body (``standoff_height``, ``rotation_z``,
            ``offset_xy``). Check it rather than assuming: each
            assignment is individually guarded, so one failing does not
            fail the call. ``rotation_z`` is now ALWAYS false, see
            above. ``standoff_height`` is confirmed live;
            ``offset_xy`` is not yet.

            A ``false`` means the adjustment did not happen, which
            covers both a rejected assignment and an argument left at
            its default of 0: the handler skips a zero rather than
            writing it. ``offset_xy`` covers ``offset_x`` and
            ``offset_y`` together and is true if either is non-zero.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.link_3d_model",
            {
                "component_name": component_name,
                "model_path": model_path,
                "offset_x": offset_x,
                "offset_y": offset_y,
                "offset_z": offset_z,
                "rotation_x": rotation_x,
                "rotation_y": rotation_y,
                "rotation_z": rotation_z,
            },
        )
        return result

    @mcp.tool()
    async def lib_auto_link_3d_models(
        model_dir: str,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch-link STEP models to footprints by matching file names.

        Enumerates every footprint in the PcbLib, scans `model_dir` for
        .step/.stp files, and links each footprint whose name matches a model
        file's stem (case-insensitive). Builds on lib_link_3d_model so the
        same caveat applies: offsets/rotations are not set, adjust in the
        library if a model needs repositioning.

        Args:
            model_dir: Folder containing .step/.stp model files.
            library_path: Optional .PcbLib to focus first; uses the focused
                document otherwise.

        Returns:
            {"linked": [{footprint, model}], "unmatched_footprints": [...],
             "unused_models": [...]}.
        """
        from pathlib import Path

        d = Path(model_dir)
        if not d.is_dir():
            return {"success": False, "error": f"not a directory: {model_dir}"}

        models: dict[str, str] = {}
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in (".step", ".stp"):
                models[f.stem.lower()] = str(f)

        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        fp_data = await bridge.send_command_async("library.get_footprints", params)
        footprints = fp_data.get("footprints", []) if isinstance(fp_data, dict) else []

        linked: list[dict[str, str]] = []
        unmatched: list[str] = []
        used: set[str] = set()
        for fp in footprints:
            fname = str(fp.get("name", "")).strip()
            key = fname.lower()
            if key in models:
                await bridge.send_command_async(
                    "library.link_3d_model",
                    {
                        "component_name": fname,
                        "model_path": models[key],
                        "offset_x": 0, "offset_y": 0, "offset_z": 0,
                        "rotation_x": 0, "rotation_y": 0, "rotation_z": 0,
                    },
                )
                linked.append({"footprint": fname, "model": models[key]})
                used.add(key)
            else:
                unmatched.append(fname)

        return {
            "success": True,
            "linked": linked,
            "unmatched_footprints": unmatched,
            "unused_models": [models[k] for k in models if k not in used],
        }

    # =========================================================================
    # Library Search and Information
    # =========================================================================

    @mcp.tool()
    async def lib_get_components(
        library_path: Optional[str] = None,
        with_parameters: bool = False,
        with_designator: bool = False,
    ) -> dict[str, Any]:
        """Get all components in a library.

        Default fast path returns only the metadata that the
        ``ILibCompInfoReader`` exposes directly: name, alias_name,
        part_count, description. That path scales linearly with file IO
        and finishes in well under a second on typical libraries.

        Setting ``with_parameters=True`` adds each component's full
        parameter dict (Manufacturer, Value, Footprint, etc.) to the
        result. That branch calls ``GetState_SchComponentByLibRef`` once
        per symbol and iterates parameters, which is O(N) in the live
        SchLib document and is what makes the call slow on libraries
        with many hundreds of components. Use it when you need the
        parameters; for a single symbol's parameters, prefer
        ``lib_get_component_details``.

        Setting ``with_designator=True`` adds each component's DEFAULT
        designator (``Component.Designator.Text``, e.g. ``"U?"`` / ``"R?"``
        / ``"IC?"``). The CompInfoReader fast path does NOT expose the
        designator, so this also loads each live symbol (same cost driver
        as with_parameters) but skips parameter iteration, keeping the
        payload small. Intended for library-wide designator-consistency
        audits.

        Args:
            library_path: Path to library (uses active library if not specified)
            with_parameters: If True, include each component's parameter
                dict (slow on large libraries). Default False.
            with_designator: If True, include each component's default
                designator string (slow on large libraries; smaller
                payload than with_parameters). Default False.

        ``part_count`` HERE IS NOT TRUSTWORTHY. It comes from the
        CompInfoReader, and MEASURED on AD26 it reported 2 for a symbol
        created single-part whose every pin carries OwnerPartId 1, while
        ``lib_get_component_details`` reported 1 for an identically
        created symbol. The two readers disagree and only the
        discrepancy is established, not a conversion between them, so
        no correction is applied here rather than guess one. Use
        ``lib_get_component_details`` when the part count matters.

        Returns:
            Dictionary with ``count`` and ``components`` list. Each
            component carries index, name, alias_name, part_count,
            description, (only when with_parameters is True) parameters,
            and (only when with_designator is True) designator.

            ``index`` is a stable addressing key (position in library
            order). Pass it as ``component_index`` to lib_get_component_details
            / lib_delete_component / lib_rename_component to reach a
            component whose ``name`` contains bytes you cannot reproduce
            (an embedded double-quote, or a control char from a broken
            import), the case where matching by name fails.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        if with_parameters:
            params["with_parameters"] = "true"
        if with_designator:
            params["with_designator"] = "true"
        result = await bridge.send_command_async("library.get_components", params)
        if isinstance(result, dict):
            return tag_response(
                result, components=result, context="lib_get_components"
            )
        return result or {}

    @mcp.tool()
    async def lib_search(
        query: str,
        search_type: str = "all",
        library_path: Optional[str] = None,
        limit: int = 100,
        include_dblibs: bool = True,
    ) -> dict[str, Any]:
        """Search open SchLib documents and installed database libraries.

        OPEN .SchLib FILES AND INSTALLED DATABASE LIBRARIES (DbLib) ARE
        SEARCHED. Query libraries (SVN DbLib), integrated libraries
        (IntLib), workspace components and installed .SchLib files that
        are not open are not, so 0 hits is not "no such part". The reply
        says what was searched (``libraries_searched``, DbLibs included)
        and lists every installed library it could not read
        (``not_searched``, with the reason), with a ``coverage_note`` when
        anything was skipped.

        DbLib rows join ``results`` after the SchLib matches, with what
        is left of ``limit``, each marked ``source: "dblib"`` and carrying
        its table, key, symbol and footprints; SchLib matches are marked
        ``source: "schlib"``. Every column of a row is matched, so a
        manufacturer part number or a value finds the part. A DbLib that
        cannot be connected stays under ``not_searched`` with the reason,
        and ``dblib_searches`` says what each DbLib search covered. Use
        ``lib_dblib_search`` to restrict to one table or a few columns,
        and ``lib_dblib_get_record`` for every column of one row.

        Case-insensitive substring match. Walks every .SchLib that is
        a member of any open project, plus every standalone .SchLib in
        the workspace's free-documents area. Each library is read via
        ``CreateLibCompInfoReader`` so the search is fast even with
        many libraries open: it only loads symbols when ``search_type``
        is ``"parameters"``.

        IT READS THE FILE ON DISK, NOT THE EDITOR. A component created
        in this session and not yet saved WILL NOT BE FOUND, and the
        reply is an ordinary empty result with nothing to say why.
        MEASURED: a symbol that ``lib_get_component_details`` returned
        in full was absent here until a save. Save first, or use
        ``lib_get_component_details`` / ``lib_get_pin_list``, which read
        the live document.

        DATASHEET DISCIPLINE: Matches carry `_datasheet_guidance`.
        Before recommending any matched part as a replacement or
        answer, fetch its datasheet (WebSearch + WebFetch). Do not
        recommend based on symbol metadata alone.

        Args:
            query: Substring to match against component name / alias /
                description (case-insensitive).
            search_type: ``"all"`` (default, matches name / alias /
                description), ``"name"``, ``"description"``, or
                ``"parameters"`` (slow, also walks each candidate's
                parameter dict via the live symbol).
            library_path: Optional path to a single .SchLib, or to a
                single .DbLib, to restrict the search to. When omitted,
                searches every open .SchLib and every installed DbLib.
            limit: Cap on returned matches (default 100), SchLib and
                DbLib together.
            include_dblibs: also search the installed database libraries
                (default True). Their rows are read through the connection
                each DbLib declares, every row of every enabled table up to
                lib_dblib_search's row bound, so a large database makes the
                search slower; False keeps it to open .SchLib files and
                lists the DbLibs under ``not_searched``. A ``library_path``
                naming a .DbLib searches that one either way.

        Returns:
            Dict with ``query``, ``search_type``, ``count``, ``limit``,
            ``truncated`` (True when count == limit, or when a DbLib
            search stopped early), and ``results``, a list of {name,
            alias_name, description, library_path, part_count, source}
            per match (a DbLib row adds table, key_field, symbol_ref,
            symbol_library, footprints, matched_field, and its name is
            the row's key), plus `_datasheet_guidance` +
            `_datasheet_parts`; and ``libraries_searched``,
            ``not_searched`` (each ``{library_path, library_type,
            reason}``), ``dblib_searches`` and ``coverage_note`` as
            above. Either list is None when it could not be read.
        """
        bridge = get_bridge()
        if library_path and is_dblib_path(library_path):
            result = await _search_named_dblib(
                bridge, library_path, query, search_type, limit)
            if result.get("success") is False:
                return result
            synthetic = {"components": result.get("results") or []}
            return tag_response(
                result, components=synthetic, context="lib_search"
            )
        params: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "limit": str(limit),
        }
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async("library.search", params)
        if isinstance(result, list):
            result = {"results": result}
        if isinstance(result, dict) and "error" not in result:
            for row in result.get("results") or []:
                if isinstance(row, dict):
                    row.setdefault("source", "schlib")
            result.update(await _search_coverage(bridge, library_path))
            if not library_path:
                await _search_dblibs(bridge, result, query, limit, include_dblibs)
        if isinstance(result, dict):
            synthetic = {"components": (
                result.get("results") or result.get("components") or []
            )}
            return tag_response(
                result, components=synthetic, context="lib_search"
            )
        return result

    # =========================================================================
    # Database libraries (DbLib)
    # =========================================================================

    @mcp.tool()
    async def lib_dblib_info(
        library_path: str,
        with_fields: bool = True,
    ) -> dict[str, Any]:
        """What a database library (.DbLib) declares, and how it connects.

        A DbLib holds no symbols. It names a database (Access, Excel, SQL
        Server, any ODBC or OLE DB source) and lists the tables whose rows
        are the components; each row names a symbol in a .SchLib and
        footprints in .PcbLibs. This reads the .DbLib file itself, inside
        Altium, and with ``with_fields`` opens the database read-only to
        list each table's columns. Call it first on an unfamiliar DbLib:
        it says which column is each table's key, which carries the
        symbol and which the footprints, which is what
        ``lib_dblib_get_record`` and ``sch_place_dblib_component`` take.

        THE PASSWORD IS NEVER RETURNED. ``connection.redacted`` is the
        connection string with every Password / Pwd value replaced by
        ``***``, and ``has_password`` says whether there was one.

        HOW EACH COLUMN WAS FOUND IS STATED. ``key_field_source`` is
        ``key_setting`` or ``where_clause`` when the DbLib names it,
        ``part_number_column`` when a column is called Part Number
        (Altium's default), or ``first_column`` as a last resort; treat
        the last as a guess. ``settings`` is the table's section of the
        file as written, for anything the parsed fields do not cover.

        Args:
            library_path: Full path of the .DbLib (or .SVNDbLib) file.
                lib_get_installed_libraries lists installed ones with
                library_type "database".
            with_fields: True (default) connects to the database to list
                every table's columns. False reads the file only and
                opens no connection: use it when the database is slow or
                unreachable, or to read the connection settings alone.

        Returns:
            {"library_path", "connection": {"provider", "kind" (access /
            excel / sql_server / odbc / oracle / other), "data_source",
            "has_password", "redacted", "read_only"}, "left_quote",
            "right_quote", "search_path", "table_count", "tables": [{"name",
            "enabled", "schema", "key_field", "key_field_source",
            "key_field_found", "symbol_ref_field", "symbol_library_field",
            "description_field", "footprint_fields": [{"ref_field",
            "library_field"}], "fields" (null when not read), "field_count",
            "error", "settings"}], "sections", "fields_included",
            "connected" (null when no connection was attempted)}.
        """
        refusal = dblib_path_refusal(library_path)
        if refusal:
            return refusal
        bridge = get_bridge()
        params: dict[str, Any] = {"library_path": library_path}
        if not with_fields:
            params["with_fields"] = "false"
        result = await bridge.send_command_async(
            "library.get_dblib_info", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def lib_dblib_search(
        query: str,
        library_path: Optional[str] = None,
        table: Optional[str] = None,
        fields: Optional[list[str]] = None,
        limit: int = 50,
        max_rows: int = 20000,
    ) -> dict[str, Any]:
        """Search database libraries (.DbLib) for components, read-only.

        Case-insensitive substring match against the columns of every
        row of every enabled table, in one DbLib or in every installed
        one. lib_search already folds DbLib rows into its own results;
        this is the direct form, for restricting to one table or a few
        columns, or for raising the row bound on a large database.

        THE QUERY NEVER REACHES THE DATABASE. Each table is read with a
        plain SELECT of the DbLib's own declared table and every row is
        matched inside Altium, so a query holding quotes, wildcards or
        SQL is matched as the text it is. The cost is that every row is
        read: ``max_rows`` bounds the total, ``rows_scanned`` reports it,
        and ``scan_capped`` says the bound cut the search short.

        Args:
            query: Text to find (case-insensitive substring).
            library_path: One .DbLib to search. Omitted, every installed
                database library is searched.
            table: One table the DbLib declares (as lib_dblib_info names
                it). Omitted, every enabled table.
            fields: Column names to match. Omitted, every column. A name
                a table lacks is reported under ``unknown_fields``.
            limit: Cap on returned rows (1 to 1000, default 50).
            max_rows: Cap on rows read in total (default 20000).

        Returns:
            {"query", "count", "limit", "truncated", "rows_scanned",
            "scan_capped", "libraries": [{"library_path", "searched",
            "error", "tables_searched", "tables_failed", "unknown_fields"}],
            "results": [{"library_path", "table", "key", "key_field",
            "symbol_ref", "symbol_library", "footprints": [{"ref",
            "library"}], "description", "matched_field", "matched_value"}]}.
            A library's ``error`` is a code (READ_FAILED, TABLE_UNKNOWN,
            NO_TABLES, NO_CONNECTION_STRING, CONNECT_FAILED,
            LIMIT_REACHED); the reason text never carries the connection
            string.
        """
        if not isinstance(query, str) or not query.strip():
            return {"success": False, "error": "query is required"}
        if library_path:
            refusal = dblib_path_refusal(library_path)
            if refusal:
                return refusal
        if not 1 <= int(limit) <= 1000:
            return {"success": False, "error": "limit must be between 1 and 1000"}
        if int(max_rows) < 1:
            return {"success": False, "error": "max_rows must be at least 1"}
        names = [str(f).strip() for f in (fields or [])]
        if any(not n or "|" in n for n in names):
            return {"success": False,
                    "error": ("each entry in fields must be a non-empty column "
                              "name without '|', which separates them on the "
                              "way to Altium")}
        params: dict[str, Any] = {
            "query": query,
            "limit": str(int(limit)),
            "max_rows": str(int(max_rows)),
        }
        if library_path:
            params["library_path"] = library_path
        if table:
            params["table"] = table
        if names:
            params["fields"] = "|".join(names)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.query_dblib", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def lib_dblib_get_record(
        library_path: str,
        table: str,
        key: str,
        key_field: Optional[str] = None,
    ) -> dict[str, Any]:
        """Every column of one row of a database library table.

        The row is found by its key, the value in the table's key column
        (``key`` in a ``lib_dblib_search`` result; the column is
        ``key_field`` in ``lib_dblib_info``). The key is passed to the
        database as a parameter, never as SQL text. ``lookup`` says how
        the row was found: ``parameter`` when the database matched it,
        ``scan`` when the parameterised query could not run and the
        table was read and compared instead.

        Args:
            library_path: Full path of the .DbLib file.
            table: A table the DbLib declares.
            key: The row's key value.
            key_field: Optional column to match instead of the DbLib's
                own key column; it must be a column of the table.

        Returns:
            {"library_path", "table", "key", "key_field",
            "key_field_source", "lookup", "found", "match_count",
            "rows_scanned", "scan_capped", "symbol_ref", "symbol_library",
            "footprints": [{"ref", "library"}], "description",
            "fields": {column: value}}. ``found`` False is an answer, not
            an error: the key is not in that table. ``match_count`` above
            1 means the key is not unique and the first row is shown.
        """
        refusal = dblib_path_refusal(library_path)
        if refusal:
            return refusal
        if not isinstance(table, str) or not table.strip():
            return {"success": False, "error": "table is required"}
        if not isinstance(key, str) or not key.strip():
            return {"success": False, "error": "key is required"}
        params: dict[str, Any] = {
            "library_path": library_path, "table": table, "key": key,
        }
        if key_field:
            params["key_field"] = key_field
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.get_dblib_record", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def sch_place_dblib_component(
        library_path: str,
        table: str,
        key: str,
        x: int,
        y: int,
        rotation: int = 0,
        designator: Optional[str] = None,
        sheet_path: Optional[str] = None,
        part_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Place one database library (DbLib) part on a schematic sheet.

        Acts on a .SchDoc: the active sheet, or ``sheet_path``. The part
        is built by Altium from the database row (symbol, footprints and
        parameters, with its database link) and dropped at (x, y). NOT
        INTERACTIVE: nothing is attached to the cursor and nothing waits
        for a click.

        THE DbLib MUST BE AVAILABLE TO ALTIUM. Altium finds a DbLib part
        by the library's file name among its available libraries, so the
        DbLib has to be installed (lib_install_library) or in the
        project. The call refuses before touching the sheet when it is
        not, or when ``table`` is not one the DbLib declares.

        ``database_linked`` reads the placed part's DatabaseTableName
        back: True means the part carries its database link, False that
        Altium built it without one. ``lib_reference`` is the symbol it
        used.

        Do not draw a schematic part by part with this. A sheet comes from
        a DesignPlan through design_execute_plan; this is for adding a
        part a person picked from their company library.

        Args:
            library_path: Full path of the .DbLib file.
            table: A table the DbLib declares.
            key: The row's key value (``key`` in a lib_dblib_search
                result).
            x, y: Location in mils.
            rotation: 0, 90, 180 or 270.
            designator: Optional designator to stamp on the part.
            sheet_path: Optional .SchDoc path; default the active sheet.
            part_id: Which part of a multi-part component to place, from
                1 (A). Left out, Altium's default part is placed.

        Returns:
            {"placed", "library_path", "table", "key", "lib_reference",
            "database_table", "database_linked", "x", "y", "rotation",
            "part_id", "part_count", "part_id_applied", "designator"}.
            ``part_id`` is read back off the placed part; a part_id
            above ``part_count`` is not applied and
            ``part_id_applied`` says so.
        """
        refusal = dblib_path_refusal(library_path)
        if refusal:
            return refusal
        if not isinstance(table, str) or not table.strip():
            return {"success": False, "error": "table is required"}
        if not isinstance(key, str) or not key.strip():
            return {"success": False, "error": "key is required"}
        if int(rotation) not in (0, 90, 180, 270):
            return {"success": False, "error": "rotation must be 0, 90, 180 or 270"}
        if part_id is not None and int(part_id) < 1:
            return {"success": False, "error": "part_id must be 1 or more"}
        params: dict[str, Any] = {
            "library_path": library_path, "table": table, "key": key,
            "x": str(int(x)), "y": str(int(y)), "rotation": str(int(rotation)),
        }
        if designator:
            params["designator"] = designator
        if sheet_path:
            params["sheet_path"] = sheet_path
        if part_id is not None:
            params["part_id"] = str(int(part_id))
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.place_dblib_component", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def lib_get_component_details(
        component_name: str = "",
        library_path: Optional[str] = None,
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Get full inspection of one library component in a single call.

        Returns metadata, every pin, every parameter (as a flat dict),
        AND visual-style records for the designator, comment, pins,
        and each parameter (font_id, color, is_hidden, x, y,
        orientation, justification). The integer ``font_id`` can be
        expanded to {name, size, bold, italic} via ``obj_get_font_spec``
        when style detail is needed; the round-trip default keeps it
        compact.

        If ``library_path`` is provided and isn't already focused, the
        library is opened (focus changes), so the next ``lib_*`` call
        operates on it without an explicit open. Saves are deferred,
        opening doesn't write anything to disk.

        DATASHEET DISCIPLINE: This response is the highest-density
        device-fact surface in the library API (pins, parameters,
        Manufacturer/MPN). Treat every value as a hint to find the
        manufacturer datasheet, not as ground truth. The response
        carries `_datasheet_guidance` and `_datasheet_parts`, fetch
        the PDF and cite a section/page before stating any pin or
        rating.

        Args:
            component_name: Component LibRef as it appears in the .SchLib.
            library_path: Optional .SchLib full path. When omitted the
                currently focused library is used.
            component_index: Alternative to component_name. The integer
                `index` from lib_get_components. Use this to reach a
                component whose LibReference carries bytes you cannot
                reproduce (an embedded double-quote, or a control char
                left by a broken import) -- the name never has to round
                trip through the caller. Provide exactly one of
                component_name / component_index.

        Returns:
            Dict with:
              - name, library_path, description, alias_name,
                part_count, pin_count
              - designator: {text, font_id, color, is_hidden, x, y,
                orientation, justification} - the on-canvas designator
                label (NOT just the prefix string).
              - comment: {text, font_id, color, is_hidden, x, y,
                orientation, justification} - the on-canvas comment /
                value label.
              - pins: list of {designator, name, electrical_type, x, y,
                orientation, hidden, label_hidden}. Pin font / color
                are not exposed by the Altium SDK on ISch_Pin and
                therefore not surfaced here.
              - parameters: flat dict of name -> value (cheap lookups).
              - parameter_styles: list of {name, value, style:{font_id,
                color, is_hidden, x, y, orientation, justification}}
                in the same order parameters appear on the symbol.
              - models: list of the component's implementations (footprint
                / SPICE / 3D links), each {model_name, model_type,
                is_current, datafile_links:[{entity_name, file_kind,
                location}]}. datafile_links is the model's source (the
                library it resolves from); empty when the footprint binds
                by name only. Empty list when the part carries no models.
              - `_datasheet_guidance` + `_datasheet_parts`.
        """
        if not component_name and component_index is None:
            return {"ok": False, "reason":
                    "provide component_name or component_index to pick "
                    "the part"}
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if component_name:
            params["component_name"] = component_name
        if component_index is not None:
            params["component_index"] = component_index
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.get_component_details", params,
        )
        if isinstance(result, dict):
            mfr = ""
            mpn = ""
            params = result.get("parameters") or {}
            if isinstance(params, dict):
                mfr = str(
                    params.get("Manufacturer")
                    or params.get("manufacturer")
                    or ""
                ).strip()
                mpn = str(
                    params.get("ManufacturerPartNumber")
                    or params.get("Manufacturer Part Number")
                    or params.get("Partnumber")
                    or params.get("PartNumber")
                    or params.get("Comment")
                    or ""
                ).strip()
            if not mpn:
                mpn = str(result.get("name") or component_name or "").strip()
            explicit = (
                [{"manufacturer": mfr, "part_number": mpn, "designators": ""}]
                if mpn
                else []
            )
            return tag_response(
                result,
                explicit_parts=explicit,
                context="lib_get_component_details",
            )
        return result

    @mcp.tool()
    async def lib_audit_styles(
        library_path: Optional[str] = None,
        with_comment: bool = False,
        with_parameters: bool = False,
        with_pins: bool = False,
        expect_designator_font_id: Optional[int] = None,
        expect_designator_color: Optional[int] = None,
        limit: int = 5000,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Bulk visual-style audit across every component in a library.

        Walks the focused .SchLib (or one specified by ``library_path``)
        component-by-component and emits each component's designator
        style record. Comment / parameter_styles / pins are opt-in via
        the ``with_*`` flags so the default response stays compact:
        designator alone is ~120 bytes per component, so a 2000-symbol
        library is ~240 KB without filters.

        Filter mode: pass ``expect_designator_font_id`` and/or
        ``expect_designator_color`` and the response only contains
        components whose designator does NOT match the expected style.
        That makes the audit case (find every symbol that doesn't use
        Times New Roman 10pt navy) a single round-trip with bounded
        output.

        ``timeout`` overrides the bridge default. A 2000-symbol audit
        with no opt-in flags finishes well under the 10s default; pass
        a larger value if you flip on ``with_parameters`` and the lib
        has heavy parameter dicts.

        Args:
            library_path: .SchLib path. Defaults to focused doc.
            with_comment: Include comment style record per component.
            with_parameters: Include parameter_styles array per component.
            with_pins: Include pins array per component.
            expect_designator_font_id: Filter; trim components where
                designator.font_id equals this value.
            expect_designator_color: Filter; trim components where
                designator.color equals this BGR int (e.g. 8388608 for
                navy / 0x000080 in BGR-packed form).
            limit: Cap on emitted entries. Default 5000.
            timeout: Per-call bridge poll timeout override (seconds).

        Returns:
            Dict with library_path, count (emitted), mismatch_count
            (subset that failed the filter), limit, truncated,
            filter_applied, and components: list of
            {name, designator:{...}, mismatched, comment?:{...},
             pins?:[...], parameter_styles?:[...]}.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if library_path:
            params["library_path"] = library_path
        if with_comment:
            params["with_comment"] = "true"
        if with_parameters:
            params["with_parameters"] = "true"
        if with_pins:
            params["with_pins"] = "true"
        if expect_designator_font_id is not None:
            params["expect_designator_font_id"] = str(expect_designator_font_id)
        if expect_designator_color is not None:
            params["expect_designator_color"] = str(expect_designator_color)
        result = await bridge.send_command_async(
            "library.audit_styles", params, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_set_label_formats(
        ops: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        only_mismatched: bool = True,
        limit: int = 5000,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Multi-target label-style writer for SchLib symbols.

        Applies SEVERAL label target/style ops to SchLib symbols in
        one IPC round-trip (there is no singular variant). The library
        is opened once and walked once; every op is applied to each
        component in turn. Five sequential per-target writes would
        collapse into one trip plus one library walk -- which is
        the dominant cost on large libraries (each individual call
        runs an IPC, a workspace lookup, a doc-focus check and a
        CompInfoReader walk).

        Each ``op`` is a dict describing one label target -- no field
        has a built-in default, only the ones you supply get written:

            {"target": "designator",         # required
             "font_id": <int>,               # optional
             "color":   <int>,               # optional, BGR-packed
             "is_hidden": False,             # optional
             "orientation": <int>,           # optional, 0/90/180/270
             "justification": <int>}         # optional

        At least one style field must be set per op. Targets are
        ``"designator"``, ``"comment"``, or ``"parameter:<Name>"``.
        Use ``obj_get_font_id`` / ``obj_get_font_spec`` to resolve the font_id
        for {name, size, bold, italic} from the active library's font
        table; that keeps the call neutral to any particular library's
        style choices.

        Args:
            ops: List of per-target style ops. Must be non-empty.
                Targets may not contain the wire separators ``;``
                or ``~~``.
            component_name: When set, applies only to that one
                component. Omit for bulk-walk across the library.
            library_path: .SchLib path. Defaults to focused doc.
            only_mismatched: When True (default) skip labels
                already matching the target style. Applies
                globally to every op.
            limit: Cap on processed components in bulk mode.
            timeout: Per-call bridge poll timeout override.

        Returns:
            Dict with library_path, scope ("single"|"bulk"),
            total, limit, truncated, and an ``ops`` array each
            with target, modified, already_compliant,
            missing_target, failed.
        """
        if not ops:
            return {"ok": False,
                    "reason": "ops must be a non-empty list of style ops"}
        encoded_ops: list[str] = []
        for i, op in enumerate(ops):
            if not isinstance(op, dict):
                return {"ok": False,
                        "reason": f"ops[{i}] must be a dict of style keys"}
            target = op.get("target", "designator")
            if (not isinstance(target, str) or ";" in target
                    or "~~" in target):
                return {"ok": False, "reason":
                        f"ops[{i}].target must be a string without "
                        "';' or '~~'"}
            parts = [f"target={target}"]
            style_set = False
            if op.get("font_id") is not None:
                parts.append(f"font_id={int(op['font_id'])}")
                style_set = True
            if op.get("color") is not None:
                parts.append(f"color={int(op['color'])}")
                style_set = True
            if op.get("is_hidden") is not None:
                parts.append(
                    "is_hidden="
                    + ("true" if op["is_hidden"] else "false")
                )
                style_set = True
            if op.get("orientation") is not None:
                parts.append(f"orientation={int(op['orientation'])}")
                style_set = True
            if op.get("justification") is not None:
                parts.append(f"justification={int(op['justification'])}")
                style_set = True
            if not style_set:
                return {"ok": False, "reason":
                        f"ops[{i}] must set at least one of font_id / "
                        "color / is_hidden / orientation / justification"}
            encoded_ops.append(";".join(parts))
        bridge = get_bridge()
        params: dict[str, Any] = {
            "ops": "~~".join(encoded_ops),
            "limit": str(limit),
        }
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        if not only_mismatched:
            params["only_mismatched"] = "false"
        result = await bridge.send_command_async(
            "library.set_label_formats", params, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_set_mech_layers(
        layers: list[dict[str, Any]],
        library_path: Optional[str] = None,
        tidy_pairs: bool = False,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Name, enable and kind the mechanical layers of one library.

        WHY THIS EXISTS RATHER THAN THE pcb_* LAYER TOOLS. Those act on
        whichever board is current, and pointing them at a particular
        library depends on the focus actually moving. When it does not
        they operate on the previously focused library while reporting
        success: a sweep over twenty one libraries returned twenty one
        identical answers because every call had re-read the same file.

        This takes the library by PATH and refuses unless the document
        that ended up focused is the one asked for. Acting on the wrong
        library is worse than not acting, because it looks like it
        worked.

        Every change is READ BACK. A layer whose name, enable or kind did
        not take is reported against that layer rather than folded into
        an overall pass.

        PAIRED KINDS. Any kind ending in Top or Bottom is held by the
        layer PAIR rather than by either layer, under a shorter name with
        the side dropped, so "Courtyard Top" is stored as the pair kind
        "Courtyard". Assigning one therefore needs BOTH sides in the same
        call: give the partner kind to another layer in ``layers`` and
        the two are joined and the pair given the kind. Single kinds such
        as "Fab Notes" need no partner.

        Pair with ``lib_run_across`` to apply the same layer scheme to
        many libraries in a single call.

        Args:
            layers: One dict per layer. Keys: ``layer`` (required, e.g.
                "Mechanical13"), and any of ``name``, ``enabled``
                (bool), ``kind`` (e.g. "Courtyard Top"). A layer with
                none of the three is reported as nothing asked for.
            library_path: The library to edit. Defaults to the focused
                document, which is only safe for a single library.
            tidy_pairs: Remove layer pairs no kind justifies. Altium
                never drops a pair when a kind moves to other layers, so
                a library reworked more than once accumulates pairs the
                Layer Stack Manager still shows. A pair survives the
                tidy only when its two layers carry kinds that are each
                other's opposite side. Off by default, because it
                REMOVES pairs and a caller renaming one layer should not
                have the stack rearranged underneath.
            timeout: Seconds.

        Returns:
            Dict with ``library``, ``layers`` (each: layer, changed,
            problem), ``changed``, ``failed``, ``kinds_displaced``,
            ``pairs_removed``, ``pairs_tidied`` and ``pairs_scanned_to``.
        """
        encoded = _encode_layer_ops(layers)
        if isinstance(encoded, dict):
            return encoded

        params: dict[str, Any] = {"layers": encoded}
        if library_path:
            params["library_path"] = library_path
        if tidy_pairs:
            params["tidy_pairs"] = "true"

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.set_mech_layers", params, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_run_across(
        action: str,
        libraries: list[str],
        params: Optional[dict[str, Any]] = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Run one library command against several libraries in one call.

        Sweeping libraries one at a time costs a round trip and a turn of
        orchestration per library. This sends the whole sweep as a single
        request and loops inside Altium.

        WHAT THIS DOES NOT SAVE. Opening and saving each library, which is
        the larger cost and is unavoidable. The saving is the round trips
        and the waiting between them, which is what makes a twenty
        library sweep tedious rather than slow.

        WHICH ACTIONS WORK. Those that accept a ``library_path``, which is
        most of the read and batch-edit commands. The footprint EDITING
        commands (``create_footprint``, ``add_footprint_pad``,
        ``link_3d_model`` and the rest) act on whichever library is open
        and cannot be swept, because focusing the document is the work.

        FAILURE IS PER LIBRARY. One library that will not open does not
        abandon the rest, and the reply reports each library separately
        rather than as a single flag. "17 of 20 worked" collapses into
        either a false clean or a false failure the moment it becomes one
        boolean, and the caller cannot tell which library to fix.

        Args:
            action: Library command without its namespace, e.g.
                "get_components", "batch_rename", "audit_styles".
            libraries: Full paths. Passed to Altium separated by "|",
                which cannot occur in a Windows path.
            params: Parameters shared by every library. Any
                ``library_path`` here is overridden per library.
            timeout: Seconds for the whole sweep, not per library.

        Returns:
            Dict with ``results`` (each: library, success, data, error),
            ``succeeded``, ``failed`` and ``libraries``.
        """
        paths = [str(p).strip() for p in (libraries or []) if str(p).strip()]
        if not paths:
            return {"success": False,
                    "error": ("libraries is empty, so nothing would run. A "
                              "sweep over no libraries would report a clean "
                              "pass having done nothing")}
        if not str(action or "").strip():
            return {"success": False, "error": "action is required"}
        bad = [p for p in paths if "|" in p]
        if bad:
            return {"success": False,
                    "error": (f"a library path contains the '|' separator, "
                              f"so the list cannot be split safely: {bad}")}

        merged: dict[str, Any] = dict(params or {})
        merged.pop("library_path", None)

        # A `layers` LIST has to be encoded the same way
        # lib_set_mech_layers encodes it. Passed through raw it arrives
        # as a JSON array, the handler parses no operations from it, and
        # the sweep reports a success per library having changed
        # nothing. Anything already a string is left alone, so a caller
        # who encoded it themselves is not re-encoded.
        if isinstance(merged.get("layers"), (list, tuple)):
            encoded = _encode_layer_ops(merged["layers"])
            if isinstance(encoded, dict):
                return encoded
            merged["layers"] = encoded

        # Every value crossing the bridge is a JSON string field. A
        # nested object or list in any other parameter would arrive the
        # same way `layers` did, so it is refused rather than silently
        # flattened into something the handler cannot read.
        for key, value in merged.items():
            if isinstance(value, (list, tuple, dict)):
                return {"success": False,
                        "error": (f"params[{key!r}] is a "
                                  f"{type(value).__name__}, which the "
                                  f"handler receives as raw JSON and cannot "
                                  f"parse. Pass it in the encoded form that "
                                  f"action expects.")}

        merged["action"] = str(action).strip()
        merged["libraries"] = "|".join(paths)

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.run_across", merged, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_batch_set_params(
        assignments: list[dict[str, str]],
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch set parameters on library components.

        Each assignment sets one parameter on one component.
        If the parameter exists it is updated; if not it is created.

        Two ``param_name`` values are SPECIAL-CASED to component
        properties rather than parameters:
          - ``"Description"`` sets ``Component.ComponentDescription``.
          - ``"Designator"`` sets the component's DEFAULT designator
            (``Component.Designator.Text``, e.g. ``"U?"`` / ``"R?"``).
            Use this to normalize library designator defaults in bulk
            (pairs with ``lib_get_components(with_designator=True)``).

        Args:
            assignments: List of dicts with keys:
                - component_name: Name of the component in the library
                - param_name: Parameter name (e.g., "Partnumber", "Manufacturer"),
                  or the special "Description" / "Designator" property keys
                - param_value: Value to set
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with counts of updated, created, and failed assignments
        """
        if not assignments:
            return {"ok": False, "reason":
                    "assignments must be a non-empty list of "
                    "component_name/param_name/param_value dicts"}
        # Validate keys and values BEFORE touching the workspace, so a
        # bad call leaves no half-written batch file behind.
        required_keys = {"component_name", "param_name", "param_value"}
        encoding = _batch_encoding()
        for i, a in enumerate(assignments):
            missing = required_keys - set(a.keys())
            if missing:
                return {"ok": False, "reason":
                        f"assignment {i} is missing required keys: "
                        f"{', '.join(sorted(missing))}"}
            for key in required_keys:
                if "|" in str(a[key]):
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' value contains the "
                            "pipe character '|' which would corrupt the "
                            "batch file"}
                try:
                    str(a[key]).encode(encoding)
                except UnicodeEncodeError:
                    bad = _first_unencodable(str(a[key]), encoding)
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' contains "
                            f"{bad!r}, which the Altium-side batch "
                            f"reader's codepage ({encoding}) cannot "
                            "represent; use a plain-text equivalent"}

        config = get_config()
        config.ensure_workspace()
        batch_path = config.workspace_dir / "batch_params.txt"

        # Windows would translate the newline into CR LF here. The
        # Pascal reader splits on the newline and leaves the carriage
        # return attached to the LAST field on the line, so a rename
        # wrote a LibReference ending in CR and a parameter got a CR
        # in its value. The Pascal side trims as well; both, because
        # either alone leaves the other half of the contract unstated.
        with open(batch_path, "w", encoding=encoding, newline="") as f:
            for a in assignments:
                f.write(f"{a['component_name']}|{a['param_name']}|{a['param_value']}\n")

        bridge = get_bridge()
        params = {"batch_file": str(batch_path)}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.batch_set_params", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def lib_batch_rename(
        assignments: list[dict[str, str]],
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch rename components in a schematic library.

        Each assignment renames one component from old_name to new_name.

        Args:
            assignments: List of dicts with keys:
                - old_name: Current name of the component in the library
                - new_name: New name for the component
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with counts of renamed and failed assignments
        """
        if not assignments:
            return {"ok": False, "reason":
                    "assignments must be a non-empty list of "
                    "old_name/new_name dicts"}
        # Validate keys and values BEFORE touching the workspace, so a
        # bad call leaves no half-written batch file behind.
        required_keys = {"old_name", "new_name"}
        encoding = _batch_encoding()
        for i, a in enumerate(assignments):
            missing = required_keys - set(a.keys())
            if missing:
                return {"ok": False, "reason":
                        f"assignment {i} is missing required keys: "
                        f"{', '.join(sorted(missing))}"}
            for key in required_keys:
                if "|" in str(a[key]):
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' value contains the "
                            "pipe character '|' which would corrupt the "
                            "batch file"}
                try:
                    str(a[key]).encode(encoding)
                except UnicodeEncodeError:
                    bad = _first_unencodable(str(a[key]), encoding)
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' contains "
                            f"{bad!r}, which the Altium-side batch "
                            f"reader's codepage ({encoding}) cannot "
                            "represent; use a plain-text equivalent"}

        config = get_config()
        config.ensure_workspace()
        batch_path = config.workspace_dir / "batch_rename.txt"

        # Windows would translate the newline into CR LF here. The
        # Pascal reader splits on the newline and leaves the carriage
        # return attached to the LAST field on the line, so a rename
        # wrote a LibReference ending in CR and a parameter got a CR
        # in its value. The Pascal side trims as well; both, because
        # either alone leaves the other half of the contract unstated.
        with open(batch_path, "w", encoding=encoding, newline="") as f:
            for a in assignments:
                f.write(f"{a['old_name']}|{a['new_name']}\n")

        bridge = get_bridge()
        params = {"batch_file": str(batch_path)}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.batch_rename", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def lib_diff_libraries(
        library_a: str,
        library_b: str,
    ) -> dict[str, Any]:
        """Compare two schematic libraries and report differences.

        Returns which components are only in library A, only in B, or shared.

        Args:
            library_a: Full path to the first SchLib file
            library_b: Full path to the second SchLib file

        Returns:
            Dictionary with only_in_a, only_in_b, common arrays,
            and count_a, count_b, only_a, only_b, shared counts
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.diff_libraries",
            {"library_a": library_a, "library_b": library_b},
            timeout=60.0,
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_arc(
        x_center: int,
        y_center: int,
        radius: int,
        start_angle: float = 0,
        end_angle: float = 360,
        width: int = 1,
        grid: int = 25,
    ) -> dict[str, Any]:
        """Add an arc to the current library symbol.

        Args:
            x_center: Center X coordinate in mils
            y_center: Center Y coordinate in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees (0 = right, 90 = up)
            end_angle: End angle in degrees
            width: Line width (0=zero, 1=small, 2=medium, 3=large)
            grid: snap grid (mils); arcs are glyph detail and don't need
                the 100-mil pin grid (rule 15), so the default is a finer
                25 (e.g. inductor coils). Pass 100 for coarse, 0/1 for none.

        Returns:
            Dictionary confirming arc addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_arc",
            {
                "x_center": _snap_grid(x_center, grid),
                "y_center": _snap_grid(y_center, grid),
                "radius": _snap_grid(radius, grid),
                "start_angle": start_angle,
                "end_angle": end_angle,
                "width": width,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_polygon(
        vertices: str,
        grid: int = 25,
    ) -> dict[str, Any]:
        """Add a polygon (filled shape) to the current library symbol.

        Args:
            vertices: Comma-separated x,y coordinate pairs in mils.
                Example: "0,0,100,0,100,100,0,100" creates a square with
                vertices at (0,0), (100,0), (100,100), (0,100).
                Minimum 3 vertices (6 values) required.
            grid: snap grid (mils) for the vertices. A filled polygon is
                glyph detail (e.g. a diode triangle) and doesn't need the
                100-mil pin grid (rule 15), so the default is a finer 25;
                pass 100 for coarse or 0/1 to disable snapping.

        Returns:
            Dictionary confirming polygon addition with vertex count
        """
        # Snap each (x, y) vertex pair to the chosen glyph grid (default 25;
        # finer than the 100-mil pin grid per rule 15) so triangles/chevrons
        # keep their shape instead of collapsing.
        try:
            coords = [int(v.strip()) for v in vertices.split(",") if v.strip()]
            if len(coords) >= 6 and len(coords) % 2 == 0:
                snapped = [_snap_grid(c, grid) for c in coords]
                vertices = ",".join(str(c) for c in snapped)
        except (ValueError, AttributeError):
            # Pass through; bridge will reject malformed vertices itself.
            pass
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_polygon",
            {"vertices": vertices},
        )
        return result

    @mcp.tool()
    async def lib_set_component_description(
        component_name: str,
        description: str,
    ) -> dict[str, Any]:
        """Set the description field on a library component.

        Args:
            component_name: Name of the component in the active library
            description: New description text

        Returns:
            Dictionary confirming the description was set
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.set_component_description",
            {"component_name": component_name, "description": description},
        )
        return result

    @mcp.tool()
    async def lib_get_pin_list(
        component_name: str = "",
        output_path: str = "",
        inline_limit: int = 150,
    ) -> dict[str, Any]:
        """Get all pins of a library component.

        NAME THE COMPONENT. Without ``component_name`` this reads
        whatever the SchLib editor currently has selected, so the answer
        depends on editor state a caller cannot see, and any tool that
        moves the selection between calls changes what this returns.
        Passing the name also avoids disturbing the selection, which is
        what made exporting one symbol affect the next call.

        DATASHEET DISCIPLINE: Pin name + electrical_type from the
        symbol can be wrong, especially on libraries that have been
        edited by hand or imported from third-party sources. Before
        relying on a pin's function for any decision, fetch the
        manufacturer datasheet and verify against its pin-description
        table. The response carries `_datasheet_guidance` +
        `_datasheet_parts`.

        BIG SYMBOLS SPILL TO A FILE ON PURPOSE. A 699-pin module returns
        more than a conversation can hold, and which half survives is
        then decided by whichever client happens to be reading. Above
        `inline_limit` pins the full array is written as JSON and the
        reply carries `pins_path` and a summary instead. That is the
        better artefact anyway: the file can be diffed against the
        datasheet's pin table by script, field by field, without any of
        it passing through the conversation.

        Args:
            component_name: library reference of the symbol to read.
                Empty falls back to the editor's current component.
            output_path: write the full pin array here as JSON and
                return the summary. Forces the file path regardless of
                size.
            inline_limit: pin count above which the array is written to
                a file instead of returned inline. 0 always returns
                inline, which is what you want only for a small symbol.

        Returns:
            Dictionary with "count", "component" name, and "pins" array.
            Each pin has: designator, name, electrical_type, x, y,
            orientation, hidden. Plus `_datasheet_guidance` +
            `_datasheet_parts`.

            When spilled: "pins_path" plus "summary" giving pins per
            part and the electrical-type distribution, and no "pins".
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if str(component_name).strip():
            params["component_name"] = component_name
        result = await bridge.send_command_async(
            "library.get_pin_list", params
        )
        if isinstance(result, dict):
            comp = str(result.get("component") or "").strip()
            pins = result.get("pins")
            if isinstance(pins, list) and (
                output_path or (inline_limit and len(pins) > inline_limit)
            ):
                result = _spill_pin_list(result, pins, comp, output_path)
            explicit = (
                [{"manufacturer": "", "part_number": comp, "designators": ""}]
                if comp
                else []
            )
            return tag_response(
                result, explicit_parts=explicit, context="lib_get_pin_list"
            )
        return result

    @mcp.tool()
    async def lib_set_pin_owner_part(
        pin_designators: str,
        owner_part_id: int,
        component_name: str = "",
    ) -> dict[str, Any]:
        """Reassign pins of a multi-part symbol to a sub-part, or to Part Zero.

        ``owner_part_id=0`` is Altium's Part Zero: the pin belongs to
        the package as a whole instead of to one sub-part. That is the
        documented placement for a multi-part component's supply pins.
        A sub-part-owned supply pin is redrawn at every instance origin,
        and where the gate pitch is only twice the pin length those
        copies land on each other and the netlist merges the two rails.

        Changes the library only. Placed instances pick it up on the
        next Update From Libraries, which produces an ECO.

        Args:
            pin_designators: comma-separated pin numbers, e.g. "3,12".
            owner_part_id: 0 for Part Zero, or 1..part_count.
            component_name: library reference of the symbol to edit.
                Empty falls back to the editor's current component.

        Returns:
            Dictionary with "component", "owner_part_id", the
            "pins_changed" list and its "count".
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "pin_designators": pin_designators,
            "owner_part_id": int(owner_part_id),
        }
        if str(component_name).strip():
            params["component_name"] = str(component_name).strip()
        return await bridge.send_command_async(
            "library.set_pin_owner_part", params
        )

    @mcp.tool()
    async def lib_export_kicad_symbol(
        component_name: str = "",
        output_path: str = "",
        reference: str = "U",
    ) -> dict[str, Any]:
        """Export a SchLib symbol to a KiCad .kicad_sym file.

        Reads the symbol's pins from the active schematic library and writes a
        KiCad 6+ S-expression symbol: a body rectangle sized to the pin roots
        plus one pin per Altium pin (designator -> number, name, electrical
        type, position, orientation). Coordinates convert mils -> mm. This
        covers the symbol only; footprint export is separate.

        Naming a component SELECTS it in the library, and that outlives
        the call: exporting is a read, but it leaves the library on the
        component it exported. Tools called afterwards without a
        component name act on whatever is current, so a following
        ``lib_add_pins`` would add pins here. The reply reports it in
        ``current_component`` when a name was given.

        Args:
            component_name: LibRef to export. If empty, exports whatever
                component is currently selected in the SchLib.
            output_path: Destination .kicad_sym file. Defaults to
                workspace/<name>.kicad_sym.
            reference: Reference designator prefix for the symbol (default
                "U").

        Returns:
            {"output_path", "symbol", "pin_count"} or an error.
        """
        from pathlib import Path

        bridge = get_bridge()
        if component_name:
            await bridge.send_command_async(
                "library.set_current_component", {"name": component_name}
            )
        pin_data = await bridge.send_command_async("library.get_pin_list", {})
        if not isinstance(pin_data, dict):
            return {"success": False, "error": "could not read pin list"}
        name = component_name or str(pin_data.get("component") or "").strip()
        if not name:
            return {"success": False, "error": "no component selected and none named"}
        pins = pin_data.get("pins", []) or []

        from eda_agent.units import MM_PER_MIL as MM  # mils -> mm

        def esc(s: str) -> str:
            return str(s).replace("\\", "\\\\").replace('"', '\\"')

        # Altium electrical type -> KiCad pin electrical type.
        etype_map = {
            "input": "input", "output": "output", "io": "bidirectional",
            "bidirectional": "bidirectional", "opencollector": "open_collector",
            "open_collector": "open_collector", "openemitter": "open_emitter",
            "open_emitter": "open_emitter", "power": "power_in",
            "power_in": "power_in", "power_out": "power_out",
            "hiz": "tri_state", "tristate": "tri_state", "tri_state": "tri_state",
            "passive": "passive",
        }
        # Altium orientation 0/1/2/3 (right/up/left/down) -> KiCad pin angle.
        angle_map = {0: 0, 1: 90, 2: 180, 3: 270}
        # Unit direction of each orientation, to find each pin's body root.
        dir_map = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
        PIN_LEN_MILS = 100

        roots_x: list[float] = []
        roots_y: list[float] = []
        pin_lines: list[str] = []
        for p in pins:
            try:
                px = float(p.get("x", 0))
                py = float(p.get("y", 0))
            except (TypeError, ValueError):
                continue
            orient = int(p.get("orientation", 0)) % 4
            dx, dy = dir_map[orient]
            roots_x.append((px - dx * PIN_LEN_MILS) * MM)
            roots_y.append((py - dy * PIN_LEN_MILS) * MM)
            ktype = etype_map.get(
                str(p.get("electrical_type", "")).strip().lower().replace(" ", ""),
                "unspecified",
            )
            number = esc(p.get("designator", ""))
            pname = esc(p.get("name", "") or "~")
            pin_lines.append(
                f'        (pin {ktype} line (at {px * MM:.4f} {py * MM:.4f} '
                f'{angle_map[orient]}) (length {PIN_LEN_MILS * MM:.4f})\n'
                f'          (name "{pname}" (effects (font (size 1.27 1.27))))\n'
                f'          (number "{number}" (effects (font (size 1.27 1.27)))))'
            )

        if roots_x and roots_y:
            minx, maxx = min(roots_x), max(roots_x)
            miny, maxy = min(roots_y), max(roots_y)
            if maxx - minx < 2.54:
                maxx = minx + 2.54
            if maxy - miny < 2.54:
                maxy = miny + 2.54
        else:
            minx, miny, maxx, maxy = -2.54, -2.54, 2.54, 2.54

        nm = esc(name)
        body = (
            f'(kicad_symbol_lib\n'
            f'  (version 20211014)\n'
            f'  (generator eda_agent)\n'
            f'  (symbol "{nm}"\n'
            f'    (in_bom yes)\n'
            f'    (on_board yes)\n'
            f'    (property "Reference" "{esc(reference)}" (at 0 {maxy + 2.54:.4f} 0)\n'
            f'      (effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{nm}" (at 0 {miny - 2.54:.4f} 0)\n'
            f'      (effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "" (at 0 0 0)\n'
            f'      (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Datasheet" "" (at 0 0 0)\n'
            f'      (effects (font (size 1.27 1.27)) hide))\n'
            f'    (symbol "{nm}_0_1"\n'
            f'      (rectangle (start {minx:.4f} {maxy:.4f}) (end {maxx:.4f} {miny:.4f})\n'
            f'        (stroke (width 0.254) (type default))\n'
            f'        (fill (type background)))\n'
            f'    )\n'
            f'    (symbol "{nm}_1_1"\n'
            + "\n".join(pin_lines) + ("\n" if pin_lines else "")
            + f'    )\n'
            f'  )\n'
            f')\n'
        )

        if output_path:
            out = Path(output_path)
        else:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            out = get_config().workspace_dir / f"{safe}.kicad_sym"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")

        result = {
            "success": True,
            "output_path": str(out),
            "symbol": name,
            "pin_count": len(pin_lines),
        }
        # Naming a component SELECTS it, and the selection outlives this
        # call. Library tools that take no component name act on whatever
        # is current, so a later lib_add_pins would land here rather than
        # on whatever the caller was editing. Reported instead of
        # restored: the caller asked about this component, so leaving it
        # selected is reasonable, but leaving it silent is not.
        if component_name:
            result["current_component"] = name
            result["note"] = (
                f"the library is now on '{name}'; tools that take no "
                "component name will act on it")
        return result

    @mcp.tool()
    async def lib_export_kicad_footprint(
        footprint_name: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a PcbLib footprint to a KiCad ``.kicad_mod`` file.

        Reads the footprint's pads from the active (or named) PCB library and
        writes a KiCad 6+ S-expression footprint: one pad each
        (designator -> number, position, size, shape, drill, layer set).
        Through-hole pads (hole > 0) get a drill and the all-copper layer set;
        SMD pads get the side's Cu/Paste/Mask set. Altium mils convert to mm
        and the y axis is flipped (Altium y-up -> KiCad y-down). This is the
        footprint counterpart to ``lib_export_kicad_symbol``.

        Args:
            footprint_name: Footprint to export. If empty, exports the
                library's current footprint.
            output_path: Destination .kicad_mod file. Defaults to
                workspace/<name>.kicad_mod.

        Returns:
            {"output_path", "footprint", "pad_count"} or an error.
        """
        from pathlib import Path

        from ..export.kicad_footprint import format_kicad_footprint

        bridge = get_bridge()
        args: dict[str, Any] = {}
        if footprint_name:
            args["footprint_name"] = footprint_name
        data = await bridge.send_command_async(
            "library.get_footprint_pads", args
        )
        if not isinstance(data, dict) or "pads" not in data:
            return {"success": False,
                    "error": "could not read footprint pads (open the PcbLib)"}
        name = footprint_name or str(data.get("name") or "").strip()
        if not name:
            return {"success": False,
                    "error": "no footprint selected and none named"}
        pads = data.get("pads", []) or []
        # Pascal returns x/y/size_x/size_y/hole in mils; map to the writer's
        # mil-suffixed keys.
        norm = [
            {
                "name": p.get("name", ""),
                "x_mils": p.get("x", 0),
                "y_mils": p.get("y", 0),
                "size_x_mils": p.get("size_x", 0),
                "size_y_mils": p.get("size_y", 0),
                "shape": p.get("shape", "round"),
                "layer": p.get("layer", "top"),
                "hole_mils": p.get("hole", 0),
                "rotation": p.get("rotation", 0),
            }
            for p in pads
        ]
        body = format_kicad_footprint(name, norm)

        if output_path:
            out = Path(output_path)
        else:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            out = get_config().workspace_dir / f"{safe}.kicad_mod"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")

        return {
            "success": True,
            "output_path": str(out),
            "footprint": name,
            "pad_count": len(norm),
        }

    @mcp.tool()
    async def lib_copy_component(
        source_name: str,
        new_name: Optional[str] = None,
        source_library: Optional[str] = None,
        dest_library: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Copy a component WITHIN or BETWEEN schematic libraries.

        Replicates the source component (all pins, graphics, parameters)
        and adds it to the destination library. When ``dest_library`` is
        omitted or equals ``source_library`` this behaves as the original
        same-library duplicate. When ``dest_library`` differs, the
        component is copied across libraries -- the source library is
        focused for the replicate, then the destination is focused and
        the clone is added there. The destination ends focused with the
        new component selected. Save is deferred (call ``app_save_all`` to
        flush).

        Args:
            source_name: lib_ref of the component to copy.
            new_name: lib_ref for the clone. Defaults to ``source_name``
                (natural choice for cross-library copies that should
                keep their identity).
            source_library: .SchLib path to read from. Defaults to the
                currently focused document.
            dest_library: .SchLib path to write to. Omit (or pass the
                same path as ``source_library``) for a same-library
                duplicate.
            overwrite: When True, a component already named ``new_name``
                in the destination is removed first. Default False
                returns ``NAME_EXISTS`` and changes nothing.

        Returns:
            Dictionary with ``success``, ``source``, ``new_name``,
            ``source_library``, ``dest_library``, ``same_library`` and
            ``overwrote`` flags.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"source_name": source_name}
        if new_name:
            params["new_name"] = new_name
        if source_library:
            params["source_library"] = source_library
        if dest_library:
            params["dest_library"] = dest_library
        if overwrite:
            params["overwrite"] = "true"
        result = await bridge.send_command_async(
            "library.copy_component", params,
        )
        return result

    @mcp.tool()
    async def lib_copy_footprint(
        source_name: str,
        new_name: str = "",
        source_library: str = "",
        dest_library: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Copy one footprint (all pads/primitives) into a PcbLib, optionally renaming.

        Footprint analog of lib_copy_component: full-copies the footprint with
        no delete. source_library defaults to the focused PcbLib; dest_library
        defaults to the source. A same-library copy requires a different
        new_name. Errors if new_name already exists in dest unless overwrite.

        Args:
            source_name: the footprint to copy.
            new_name: name for the copy (default source_name).
            source_library: source .PcbLib path (default the focused lib).
            dest_library: destination .PcbLib path (default source).
            overwrite: replace a same-named footprint already in dest.

        Returns:
            {"success": true, "source": "...", "new_name": "...",
             "same_library": bool}.
        """
        params: dict[str, Any] = {"source_name": source_name}
        if new_name:
            params["new_name"] = new_name
        if source_library:
            params["source_library"] = source_library
        if dest_library:
            params["dest_library"] = dest_library
        if overwrite:
            params["overwrite"] = "true"
        bridge = get_bridge()
        return await bridge.send_command_async("library.copy_footprint", params)

    @mcp.tool()
    async def lib_move_components(
        source_schlib: str,
        dest_schlib: str,
        names: Optional[list[str]] = None,
        name_regex: str = "",
        delete_from_source: bool = True,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move matching components (symbol + params + models) between SchLibs.

        Bulk analog of lib_copy_component: copies every matching component from
        source_schlib to dest_schlib (Replicate carries the whole symbol, its
        parameters and its models) in one pass, then removes them from source
        unless delete_from_source is False. This is the piece that makes a full
        library split a single call.

        Provide either names (explicit LibReferences) or name_regex (matched
        against the source's component names). With names it is one IPC call;
        with name_regex the source component list is fetched first to resolve
        the matches, then the move runs. A component already present in dest is
        skipped unless overwrite=True.

        Args:
            source_schlib: source .SchLib path.
            dest_schlib: destination .SchLib path.
            names: explicit list of component LibReferences to move.
            name_regex: regex matched against source component names when names
                is not given.
            delete_from_source: remove moved components from source (default True).
            overwrite: replace a same-named component already in dest.
            dry_run: resolve the selection and report it WITHOUT moving
                anything. Worth doing whenever name_regex is used: the
                pattern is applied with ``search``, not a full match, so
                ``R`` selects every name containing an R, and the default
                is to delete them from the source afterwards.

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N}, or
            in dry-run {"dry_run", "resolved", "count", ...} with nothing
            sent to the bridge.

        A failed copy does not delete: the handler counts it in ``failed``
        and moves on, so the source keeps it. What is NOT recoverable is
        ``overwrite=True`` replacing a different component of the same
        name in the destination, which is why dry_run reports both flags.
        """
        resolved: list[str] = list(names or [])
        if not resolved and name_regex:
            import re

            listing = await get_bridge().send_command_async(
                "library.get_components", {"library_path": source_schlib}
            )
            comps = (listing or {}).get("components") or []
            pat = re.compile(name_regex)
            for c in comps:
                nm = c.get("name") or c.get("lib_ref") or ""
                if nm and pat.search(nm):
                    resolved.append(nm)
        if not resolved:
            return {
                "error": "no components to move: provide names[] or a "
                "name_regex that matches at least one source component"
            }
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "source_schlib": source_schlib,
                "dest_schlib": dest_schlib,
                "resolved": resolved,
                "count": len(resolved),
                "delete_from_source": delete_from_source,
                "overwrite": overwrite,
                "note": "nothing was moved; re-run with dry_run=False",
            }
        # Names ride a '~~'-separated field, and the default is to
        # DELETE the moved parts from the source: a name carrying the
        # separator splits into fragments that can match real parts.
        # Refuse rather than strip.
        bad = [n for n in resolved if "~~" in n]
        if bad:
            return {"ok": False, "reason":
                    f"component names {bad} contain '~~', which is the "
                    "wire-format separator; fragments could match other "
                    "parts, so the call is refused"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_components",
            {
                "source_library": source_schlib,
                "dest_library": dest_schlib,
                "names": "~~".join(resolved),
                "delete_from_source": "true" if delete_from_source else "false",
                "overwrite": "true" if overwrite else "false",
            },
        )

    @mcp.tool()
    async def lib_move_footprints(
        source_pcblib: str,
        dest_pcblib: str,
        names: Optional[list[str]] = None,
        name_regex: str = "",
        delete_from_source: bool = True,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move matching footprints between PcbLibs (the PcbLib analog of move).

        Copies every matching footprint (all pads and primitives, via a full
        copy) from source_pcblib to dest_pcblib, then removes them from source
        unless delete_from_source is False. The footprint counterpart of
        lib_move_components; together they split a full library.

        Provide either names (explicit footprint names) or name_regex (matched
        against the source's footprint names). With names it is one IPC call;
        with name_regex the source footprint list is fetched first. A footprint
        already present in dest is skipped unless overwrite=True.

        Args:
            source_pcblib: source .PcbLib path.
            dest_pcblib: destination .PcbLib path.
            names: explicit list of footprint names to move.
            name_regex: regex matched against source footprint names when names
                is not given.
            delete_from_source: remove moved footprints from source (default True).
            overwrite: replace a same-named footprint already in dest.
            dry_run: resolve the selection and report it WITHOUT moving
                anything. Worth doing whenever name_regex is used: the
                pattern is applied with ``search``, not a full match, so a
                short pattern selects far more than intended, and the
                default is to delete them from the source afterwards.

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N}, or
            in dry-run {"dry_run", "resolved", "count", ...} with nothing
            sent to the bridge.
        """
        resolved: list[str] = list(names or [])
        if not resolved and name_regex:
            import re

            listing = await get_bridge().send_command_async(
                "library.get_footprints", {"library_path": source_pcblib}
            )
            fps = (listing or {}).get("footprints") or []
            pat = re.compile(name_regex)
            for f in fps:
                nm = f.get("name") or ""
                if nm and pat.search(nm):
                    resolved.append(nm)
        if not resolved:
            return {
                "error": "no footprints to move: provide names[] or a "
                "name_regex that matches at least one source footprint"
            }
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "source_pcblib": source_pcblib,
                "dest_pcblib": dest_pcblib,
                "resolved": resolved,
                "count": len(resolved),
                "delete_from_source": delete_from_source,
                "overwrite": overwrite,
                "note": "nothing was moved; re-run with dry_run=False",
            }
        # Same wire format and same default-delete as
        # lib_move_components; same refusal.
        bad = [n for n in resolved if "~~" in n]
        if bad:
            return {"ok": False, "reason":
                    f"footprint names {bad} contain '~~', which is the "
                    "wire-format separator; fragments could match other "
                    "footprints, so the call is refused"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_footprints",
            {
                "source_library": source_pcblib,
                "dest_library": dest_pcblib,
                "names": "~~".join(resolved),
                "delete_from_source": "true" if delete_from_source else "false",
                "overwrite": "true" if overwrite else "false",
            },
        )

    @mcp.tool()
    async def lib_split_pin_functions() -> dict[str, Any]:
        """Split slash-delimited pin names into pin function lists.

        For the current library symbol, parses each pin whose name is
        `PRIMARY/ALT1/ALT2/...` into the pin's function list (the alternate-
        function popup), leaving `PRIMARY` as the visible name. Pins without a
        `/` are unchanged.

        Returns:
            {"pins_processed": N}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("library.split_pin_functions", {})

    @mcp.tool()
    async def lib_get_installed_libraries(
        with_counts: bool = True,
    ) -> dict[str, Any]:
        """List the libraries installed in the Altium environment.

        The answer to "what libraries does this installation have?", which
        no other tool gives: `lib_search` walks only the SchLibs already
        open in the workspace, and `design_snapshot_inventory` has to be
        handed explicit .SchLib paths. This reads the environment's own
        list, so it covers .IntLib, .SchLib, .PcbLib, database and query
        libraries whether or not anything is open.

        INSTALLED IS NOT AVAILABLE. Installed libraries are the ones
        switched on for this environment; available ones are every library
        it knows about. This returns the installed list and reports the
        available total beside it, so a library that is present but not
        switched on shows up as a gap between the two numbers rather than
        as an absence.

        Args:
            with_counts: True (default) also reports how many components
                each library holds. That opens every library to count
                them, so pass False for a fast listing; the count then
                comes back as -1, meaning not asked rather than empty.

        Returns:
            {"libraries": [{"library_path", "file_name", "library_type",
            "library_type_ordinal", "component_count"}], "installed_count",
            "available_count", "counts_included"}. ``library_type`` is one
            of integrated / source / datafile / database / none / query /
            design_items, or unknown; it reads "unknown" with an ordinal of
            -1 when a library is installed but missing from the available
            list, which is a real state and not an error.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if not with_counts:
            params["with_counts"] = "false"
        result = await bridge.send_command_async(
            "library.get_installed_libraries", params
        )
        # Altium's component count is not a database library's row count:
        # MEASURED on AD26, a DbLib of five rows read 0 while its connection
        # was unreadable and 1 once it worked. Reported as unknown, with
        # where the real answer is, rather than as a number.
        if isinstance(result, dict) and with_counts:
            for lib in result.get("libraries") or []:
                if isinstance(lib, dict) and lib.get("library_type") in ("database", "query"):
                    lib["component_count"] = None
                    lib["count_note"] = ("Altium's count does not reflect a database "
                                         "library's rows; lib_dblib_info and "
                                         "lib_dblib_search read the database.")
        return result

    @mcp.tool()
    async def lib_install_library(library_path: str) -> dict[str, Any]:
        """Register a library with the environment's Available Libraries.

        Installs an .IntLib / .SchLib / .PcbLib so its parts resolve by
        lib-ref across the workspace (the step after authoring a library).

        Args:
            library_path: absolute path to the library file.

        Returns:
            {"installed": bool, "library_path": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.install_library", {"library_path": library_path}
        )

    @mcp.tool()
    async def lib_get_vault_name() -> dict[str, Any]:
        """The Workspace name the managed tools would use, read off the design.

        Altium writes the Workspace name onto every managed part it
        places. This reads it from the active sheet, then from the other
        sheets that are already open; it never opens one. A Workspace has
        been seen under two names on one design, so a part addressed by
        kind 4 (VaultName) is preferred and ``other_kind`` says when only
        another kind was found.

        Returns:
            ``found``, ``vault``, ``other_kind`` and ``seen``, every
            ``kind:name`` met, separated by ``|``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("library.get_vault_name", {})

    @mcp.tool()
    async def lib_find_vault_components(name: str) -> dict[str, Any]:
        """Find the Design Item ID of a managed part by its exact name.

        Looks through the managed (Workspace) parts already placed on the
        sheets Altium has open, and matches ``name`` exactly, case
        ignored, against each part's comment, library reference and
        Design Item ID. Every match comes back, one per Design Item ID,
        with the lifecycle state the library manager reports, so two
        parts sharing a name are both shown rather than the first one
        being picked.

        It does not search the whole Workspace: that needs Altium's EDMS
        interfaces, whose methods no reference documents, and a call this
        Altium lacks would stop the bridge. A part not yet used in the
        design has to be looked up in Altium's Components panel.

        Args:
            name: The comment, library reference or Design Item ID.

        Returns:
            ``name``, ``count``, ``sheets_searched`` and ``matches``, each
            with design_item_id, comment, lib_reference, vault,
            lib_identifier_kind, revision_guid, lifecycle_state and sheet.
        """
        if not str(name or "").strip():
            return {"success": False, "error": "name is required"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.find_vault_components", {"name": str(name).strip()})

    @mcp.tool()
    async def lib_get_vault_component(
        design_item_id: str,
        vault: str = "",
        lib_identifier_kind: Optional[int] = None,
    ) -> dict[str, Any]:
        """Check one managed (Workspace) component can be placed, without placing it.

        Loads the item the way ``sch_place_vault_components`` would, reads
        its identity, and throws it away again; nothing reaches a sheet.
        The kind asked for is tried first, then the others. The library
        manager's lookups (library path, lifecycle state, revisions,
        placement parameters) are reported alongside, as they come back.
        A part number is not looked up here, so bring the Design Item ID.

        Args:
            design_item_id: The component's Design Item ID.
            vault: The Workspace name. Usually left out; see
                ``lib_get_vault_name``.
            lib_identifier_kind: Altium's TLibIdentifierKind, tried first:
                0 Any, 1 NameNoType, 2 NameWithType, 3 FullPath, 4 VaultName
                (the default). A managed part Altium placed shows the kind
                and identifier that work, through ``obj_query`` on
                ``eSchComponent``.

        Returns:
            ``placeable``, ``loader_callable``, lib_identifier_kind (the
            kind that loaded, -1 if none), ``load_probe`` (``kind:result``
            for each try), the loaded ``loaded_design_item_id``,
            ``vault_guid``, ``item_guid`` and ``revision_guid``, then
            ``library_path``, ``display_path``, ``lifecycle_state``,
            ``has_revisions``, ``revisions``, ``placement_parameters``,
            ``note`` and ``diag`` (library manager calls that raised).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"design_item_id": design_item_id}
        if vault:
            params["vault"] = vault
        if lib_identifier_kind is not None:
            params["lib_identifier_kind"] = int(lib_identifier_kind)
        return await bridge.send_command_async(
            "library.get_vault_component", params)

    @mcp.tool()
    async def lib_uninstall_library(library_path: str) -> dict[str, Any]:
        """Unregister a library from the environment's Available Libraries.

        Args:
            library_path: absolute path to the library file.

        Returns:
            {"uninstalled": bool, "library_path": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.uninstall_library", {"library_path": library_path}
        )

    @mcp.tool()
    async def lib_delete_component(
        component_name: str = "",
        library_path: str = "",
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Delete one symbol from a schematic library (.SchLib).

        Removes the component whose LibReference is ``component_name`` and
        marks the library dirty for deferred save (flushed by app_save_all).
        Deletes a single named part; if the name is not found the call
        errors (COMPONENT_NOT_FOUND) rather than silently doing nothing.
        There is no wildcard mass-delete.

        Args:
            component_name: the component's LibReference (its library name).
            library_path: optional absolute .SchLib path to target; defaults
                to the currently focused library document.
            component_index: alternative to component_name; the integer
                `index` from lib_get_components. Use it to delete a part
                whose name carries unreproducible bytes (embedded quote or
                control char). Provide exactly one of the two.

        Returns:
            {"success": true, "library_path": "...", "deleted": "..."}.
        """
        if not component_name and component_index is None:
            return {"ok": False, "reason":
                    "provide component_name or component_index to pick "
                    "the part"}
        bridge = get_bridge()
        params: dict[str, Any] = {"library_path": library_path}
        if component_name:
            params["component_name"] = component_name
        if component_index is not None:
            params["component_index"] = component_index
        return await bridge.send_command_async(
            "library.delete_component", params,
        )

    @mcp.tool()
    async def lib_rename_component(
        new_name: str,
        component_name: str = "",
        library_path: str = "",
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Rename one symbol's LibReference in a schematic library (.SchLib).

        This is the repair path for a component whose current name carries
        bytes a caller cannot reproduce (an embedded double-quote, or a
        control char left by a broken import). Address it by
        ``component_index`` from lib_get_components, give it a clean
        ``new_name``, and every name-based tool can reach it again.

        The component is removed and re-added under the new name so the
        library's internal by-name index is rebuilt. Errors with
        NAME_EXISTS if a different component already uses ``new_name``.
        Marks the library dirty for deferred save.

        Args:
            new_name: the clean LibReference to write.
            component_name: current LibReference of the target. Provide this
                or component_index.
            library_path: optional absolute .SchLib path; defaults to the
                focused library.
            component_index: the integer `index` from lib_get_components,
                the name-free way to pick the target. Provide this or
                component_name.

        Returns:
            {"success": true, "library_path": "...", "old_name": "...",
             "new_name": "..."}.
        """
        if not component_name and component_index is None:
            return {"ok": False, "reason":
                    "provide component_name or component_index to pick "
                    "the part"}
        bridge = get_bridge()
        params: dict[str, Any] = {
            "new_name": new_name,
            "library_path": library_path,
        }
        if component_name:
            params["component_name"] = component_name
        if component_index is not None:
            params["component_index"] = component_index
        return await bridge.send_command_async(
            "library.rename_component", params,
        )

    @mcp.tool()
    async def lib_delete_footprint_primitives(
        footprint_name: str,
        object_type: str = "",
        layer: str = "",
        library_path: str = "",
        include_pads: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete primitives inside ONE footprint of a PCB library.

        THE ONLY LIBRARY-SCOPED PRIMITIVE DELETE. ``obj_delete`` and
        ``pcb_delete_object`` both resolve a BOARD, and when no board is
        focused that lookup opens the first PcbDoc any open project
        holds. Aimed at a library they do not fail; they remove
        primitives from a board you never named, and the reply does not
        say which one. This is the tool for editing a footprint.

        SCOPED THREE WAYS, all of which must agree before anything is
        removed: the library by path, the footprint by name, and the
        object type or layer. The library is verified AFTER it is
        brought to the front, so a delete aimed at one that never
        focused is refused rather than landing in whichever did.

        PADS ARE EXCLUDED unless ``include_pads``. Deleting a pad changes
        the part's connectivity rather than its drawing, and clearing
        graphics off a layer should not quietly cost you the pinout.

        THE FOOTPRINT ON SCREEN IS EDITED FROM ANOTHER. The footprint the
        editor shows keeps its primitives on the library board, which is
        written back into it at the next switch, so removing from it in
        place came undone. When the target is on screen, another
        footprint is made current for the delete (a temporary one in a
        single-footprint library, removed afterwards) and the target is
        shown again at the end; ``was_current`` says this happened.

        Args:
            footprint_name: The footprint to edit. Required: deleting
                from whichever footprint the editor happens to show is
                the mistake this avoids.
            object_type: PCB object type, e.g. "track", "arc", "text",
                "region", "fill". Give this or ``layer``.
            layer: Restrict to one layer, e.g. "TopOverlay", or a name
                the library's stack gives it, such as "Top Assembly". A
                name the stack does not have is refused, with the names
                it does have.
            library_path: The .PcbLib. Defaults to the focused document.
            include_pads: Allow pads to be removed. Off by default.
            confirm: Required. Read the footprint first with
                ``lib_probe_footprint`` and pass True once the filters
                are what you mean.

        Returns:
            Dict with ``library``, ``footprint``, ``removed``,
            ``examined``, ``layers``, ``pads_included`` and
            ``was_current``.
        """
        if not footprint_name.strip():
            return {"ok": False, "reason": "footprint_name is required"}
        if not object_type.strip() and not layer.strip():
            return {"ok": False, "reason": (
                "give object_type or layer. Emptying a whole footprint is "
                "not something to reach by leaving both filters off")}

        params: dict[str, Any] = {"footprint_name": footprint_name}
        if object_type.strip():
            params["object_type"] = object_type.strip()
        if layer.strip():
            params["layer"] = layer.strip()
        if library_path.strip():
            params["library_path"] = library_path.strip()
        if include_pads:
            params["include_pads"] = "true"
        if confirm:
            params["confirm"] = "true"

        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.delete_footprint_primitives", params)

    @mcp.tool()
    async def lib_move_bodies_to_layer(
        layer: str,
        footprint_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Put the 3D bodies of the focused PCB library on one mechanical
        layer.

        Libraries put together from several sources carry their bodies
        on whatever layer each source used, so one footprint's model
        shows with a layer turned on and the next one's never does.
        Every footprint, or only the named ones, has each body moved; a
        body already on the layer is counted and left alone. Nothing is
        added or removed.

        Args:
            layer: The mechanical layer, by number ("Mechanical13") or by
                the name the library's stack gives it. Anything else is
                refused, as Altium's body dialog only offers mechanical
                layers.
            footprint_names: Only these footprints. Empty means every
                footprint in the library; names no footprint has come
                back in ``not_found``.

        Does NOT save the library. A mechanical layer the library has not
        enabled is accepted, with ``layer_enabled`` false and a
        ``warning``, since Altium does not draw what is on it.

        Returns:
            Dict with ``layer``, ``layer_enabled``, ``footprints``,
            ``bodies``, ``moved``,
            ``already_on_layer``, ``footprints_without_bodies``,
            ``not_found``, and ``items`` (``{name, moved, from}`` for
            each footprint that changed).
        """
        if not layer.strip():
            return {"ok": False, "reason": "layer is required"}
        params: dict[str, Any] = {"layer": layer.strip()}
        names = [n.strip() for n in (footprint_names or []) if n.strip()]
        if names:
            params["footprint_names"] = "\t".join(names)
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_bodies_to_layer", params, timeout=60.0)

    @mcp.tool()
    async def lib_redraw_assembly(
        layer: str,
        footprint_names: Optional[list[str]] = None,
        outline_from: str = "auto",
        courtyard_layer: str = "",
        width: Optional[float] = None,
        designator: bool = True,
        text_height: float = 0,
        replace: bool = True,
        confirm: bool = False,
        units: str = "mil",
    ) -> dict[str, Any]:
        """Draw the assembly layer of the focused PCB library again from
        each footprint's own geometry: a rectangle round the part and a
        ``.Designator`` in its middle.

        Libraries collected over the years carry the assembly drawing on
        the wrong layer, from another package, or not at all. With
        ``replace`` (the default) each footprint's tracks, arcs, texts,
        fills and regions on ``layer`` are cleared first; 3D bodies and
        pads on that layer are never touched.

        Args:
            layer: The mechanical layer the assembly drawing goes on.
            footprint_names: Only these footprints. Empty means all.
            outline_from: What the rectangle goes round. ``body`` (the 3D
                bodies), ``pads`` (their copper, without mask expansion),
                ``courtyard`` (whatever is drawn on ``courtyard_layer``),
                or ``auto`` (bodies, then the courtyard when
                ``courtyard_layer`` is given, then pads). A footprint
                with none of them is skipped and named in ``skipped``.
            courtyard_layer: The layer the courtyard is drawn on. It
                cannot be ``layer`` itself with ``replace``, which would
                clear the courtyard.
            width: Line width. Default 4 mil (0.1 mm).
            designator: Place a ``.Designator`` text in the middle,
                turned upright in a box taller than wide.
            text_height: Text height. 0 sizes it to a third of the
                box's short side, kept short enough for a four-character
                designator to fit along the long side, between 0.2 and
                1 mm.
            replace: Clear what is on the layer first. With False the new
                drawing goes beside what is there.
            confirm: Required with ``replace``, since it clears that
                layer in every footprint it redraws.
            units: "mil" (default) or "mm" for ``width`` and
                ``text_height``.

        The footprint on screen is shown again at the end. Does NOT save
        the library. A mechanical layer the library has not enabled is
        accepted, with ``layer_enabled`` false and a ``warning``, since
        Altium does not draw what is on it.

        Returns:
            Dict with ``layer``, ``layer_enabled``, ``redrawn``,
            ``cleared``, ``skipped``,
            ``not_found``, and ``items`` (``{name, outline_from,
            cleared, width_mm, height_mm}`` per footprint drawn).
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        if not layer.strip():
            return {"ok": False, "reason": "layer is required"}
        source = (outline_from or "auto").strip().lower()
        if source not in ("auto", "body", "pads", "courtyard"):
            return {"ok": False, "reason": (
                f"outline_from must be auto, body, pads or courtyard; "
                f"got {outline_from!r}")}
        if source == "courtyard" and not courtyard_layer.strip():
            return {"ok": False,
                    "reason": "outline_from courtyard needs courtyard_layer"}
        if text_height < 0 or (width is not None and width <= 0):
            return {"ok": False,
                    "reason": "width must be above 0 and text_height 0 or more"}
        params: dict[str, Any] = {
            "layer": layer.strip(),
            "outline_from": source,
            "width": format_length(length_in(units, 4) if width is None else width),
            "designator": "true" if designator else "false",
            "replace": "true" if replace else "false",
        }
        names = [n.strip() for n in (footprint_names or []) if n.strip()]
        if names:
            params["footprint_names"] = "\t".join(names)
        if courtyard_layer.strip():
            params["courtyard_layer"] = courtyard_layer.strip()
        if text_height:
            params["text_height"] = format_length(text_height)
        if confirm:
            params["confirm"] = "true"
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.redraw_assembly", _with_units(params, units), timeout=120.0)

    @mcp.tool()
    async def lib_delete_footprint(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Delete one footprint from a PCB library (.PcbLib).

        Finds the footprint by name, removes and deregisters it, and MARKS
        the .PcbLib dirty. It does NOT write to disk: this bridge defers
        saves, and `app_save_all` (or `proj_save` on the LibPkg) is what
        flushes them. Reported: this said it saved, the footprint was still
        in the file and the timestamp unchanged until an explicit save.

        Deletes a single named footprint; if the name is not found the call
        errors (FOOTPRINT_NOT_FOUND). No wildcard mass-delete.

        Args:
            footprint_name: the footprint's name in the library.
            library_path: optional absolute .PcbLib path to target; defaults
                to the currently focused library document.

        Returns:
            {"success": true, "library_path": "...", "deleted": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.delete_footprint",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_set_model_name(
        component_name: str,
        new_model_name: str,
        model_name: str = "",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Set a model's model_name (footprint reference) on a SchLib symbol.

        Rewrites the ModelName of one of the component's implementations.
        If model_name is given, the model whose current ModelName matches is
        targeted; otherwise the current (or first) model is used.

        Args:
            component_name: the component's LibReference.
            new_model_name: the new model_name to write.
            model_name: optional current model_name to target a specific one.
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"success": true, "component": "...", "new_model_name": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_model_name",
            {
                "component_name": component_name,
                "new_model_name": new_model_name,
                "model_name": model_name,
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_set_model_source(
        component_name: str,
        source_library: str,
        model_name: str = "",
        use_component_library: Optional[bool] = None,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Write the datafile-link Location (source library) on a footprint model.

        A footprint whose datafile link has an empty Location resolves in the
        SchLib editor by name but cannot embed into a self-contained compiled
        library, so the footprint goes missing in the used/compiled output
        even though the package's own compile reports no error. This writes
        Location so the model is embeddable. Location is set in place (never
        through AddDataFileLink with a path, which wedges AD26). Targets PCBLIB
        models matching model_name, or all PCBLIB models when it is empty.

        IMPORTANT: get the exact source_library string from a known-good model
        that already embeds correctly. Read it via lib_get_component_details,
        whose models array now shows each model's location and
        use_component_library. This tool writes exactly what you pass; it does
        not infer the format.

        Args:
            component_name: the component's LibReference.
            source_library: the source-library reference written into Location.
            model_name: optional footprint model name to target one model.
            use_component_library: optional; sets the embed-vs-search flag.
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"success": true, "component": "...", "source_library": "...",
             "models_updated": N}.
        """
        params: dict[str, Any] = {
            "component_name": component_name,
            "source_library": source_library,
            "model_name": model_name,
            "library_path": library_path,
        }
        if use_component_library is not None:
            params["use_component_library"] = (
                "true" if use_component_library else "false"
            )
        bridge = get_bridge()
        return await bridge.send_command_async("library.set_model_source", params)

    @mcp.tool()
    async def lib_remove_model(
        component_name: str,
        model_name: str,
        keep_one: bool = False,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Remove models (implementations) from a SchLib component by name.

        Removes every implementation whose ModelName equals model_name. With
        keep_one=True it keeps the first match and removes the rest, which
        deletes duplicate model links while preserving one. Errors if the
        component is not found; returns removed=0 if no model matches.

        Args:
            component_name: the component's LibReference.
            model_name: the ModelName of the model(s) to remove.
            keep_one: keep the first match and remove the rest (dedup).
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"success": true, "model_name": "...", "removed": N,
             "kept_one": bool}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.remove_model",
            {
                "component_name": component_name,
                "model_name": model_name,
                "keep_one": "true" if keep_one else "false",
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_rename_footprint(
        footprint_name: str,
        new_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Rename a footprint in a PCB library (.PcbLib).

        Renames the footprint whose name is footprint_name to new_name.
        Errors if footprint_name is not found or new_name already exists in
        the library.

        MARKS the .PcbLib dirty; it does not write. Call `app_save_all` to
        flush, and check what it reports actually reached disk.

        Args:
            footprint_name: the current footprint name.
            new_name: the new footprint name.
            library_path: optional .PcbLib path; defaults to the focused lib.

        Returns:
            {"success": true, "footprint": "...", "new_name": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.rename_footprint",
            {
                "footprint_name": footprint_name,
                "new_name": new_name,
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_probe_footprint(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Read-only dump of a PcbLib footprint's name-bearing fields.

        Use this to locate where an old name persists after a rename. On a
        PcbLib footprint, Name and Pattern are the SAME property (renaming Name
        writes both), and the footprint's only metadata is Name, Description,
        Height. So a leftover old name lives in a child primitive: this returns
        every text primitive's object_id and text, plus name/description/
        height_mils/primitive_count. Nothing is written.

        Args:
            footprint_name: the footprint name.
            library_path: optional .PcbLib path; defaults to the focused lib.

        Returns:
            {"success": true, "footprint": "...", "description": "...",
             "height_mils": N, "primitive_count": N,
             "texts": [{"object_id": N, "text": "..."}]}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.probe_footprint",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_get_pad_geometry(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Full-precision pad geometry of one PcbLib footprint, in mm.

        The measurement half of the datasheet land-pattern audit. Unlike
        the integer-mil dump behind lib_audit_footprint_policies, every
        dimension here is a float in millimetres (a 0.65 mm pitch loses
        10 um to integer-mil rounding, which a tolerance comparison
        against the datasheet cannot afford). Coordinates are relative
        to the footprint's own origin.

        Args:
            footprint_name: the footprint name in the library.
            library_path: optional .PcbLib path; defaults to the
                focused library.

        Returns:
            {name, description, library_path, pad_count, pads: [{
             name, x_mm, y_mm, copper_x_mm, copper_y_mm, w_mm, h_mm,
             shape, corner_pct, rotation, hole_rotation, hole_mm,
             hole_w_mm, hole_type, slot_aligned (slots only), plated,
             layer, paste_expansion_mm, paste_expansion_source,
             paste_expansion_state, mask_expansion_mm,
             mask_bottom_expansion_mm, mask_separate,
             mask_expansion_source, mask_expansion_state}]}.
            mask_expansion_mm is the TOP side when mask_separate is true
            and both sides when it is false; mask_bottom_expansion_mm is
            the stored bottom value, which takes effect once the sides are
            unlinked even while it is not in use.
            x_mm/y_mm are the pad's origin; copper_x_mm/copper_y_mm are
            the centre of its copper, which differs on a custom pad.
            hole_rotation is relative to the pad, so a slot lines up when
            the copper's and the hole's long axes agree in the pad's
            frame, which slot_aligned reports. *_expansion_source is
            'manual' when the pad overrides the design rule, 'rule'
            otherwise (value 0 in that case: the rule's value lives in
            the design rules, not on the pad); *_expansion_state is the
            cache's own manual, valid or invalid.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_set_pad_properties(
        footprint_name: str,
        pad_name: str,
        library_path: str = "",
        units: str = "mil",
        x_size: Optional[float] = None,
        y_size: Optional[float] = None,
        shape: Optional[str] = None,
        rotation: Optional[float] = None,
        hole_size: Optional[float] = None,
        hole_width: Optional[float] = None,
        hole_rotation: Optional[float] = None,
        plated: Optional[bool] = None,
        paste_expansion: Optional[float] = None,
        mask_expansion: Optional[float] = None,
        mask_bottom_expansion: Optional[float] = None,
        mask_separate: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Change pads that already exist in a PcbLib footprint.

        Every pad named ``pad_name`` in the footprint is changed, and
        only the fields you pass are written, so setting plating leaves
        the shape alone. Each pad is read back afterwards and the reply
        reports what it holds, not what was asked.

        A mask or paste expansion is written with the pad's manual flag
        set: without it Altium ignores the value and follows the board's
        rule, and a library has no rules. ``hole_width`` makes the hole a
        slot; ``hole_rotation`` is relative to the pad, so a slot lines up
        when its long axis matches the copper's in the pad's frame (see
        ``slot_aligned`` in ``lib_get_pad_geometry``). On a pad with a
        per-layer stack only the top size is written, and ``note`` says so.

        THE MASK HAS TWO SIDES. A pad keeps a top and a bottom solder
        mask expansion; with the sides linked the top value serves both,
        but the bottom value is still stored and takes effect the moment
        the sides are unlinked. So ``mask_expansion`` writes both sides
        unless ``mask_bottom_expansion`` is given too, which writes the
        bottom and unlinks the sides. ``mask_separate`` links or unlinks
        them explicitly. The reply reads back all three.

        Args:
            footprint_name: The footprint holding the pad.
            pad_name: The pad's designator.
            library_path: Optional .PcbLib path; defaults to the focused
                library.
            units: "mil" (default) or "mm", for every length here.
            x_size, y_size: Copper size.
            shape: "round", "rectangular" or "octagonal".
            rotation: Pad rotation in degrees.
            hole_size: Hole diameter, 0 for a surface-mount pad.
            hole_width: Slot length; makes the hole a slot.
            hole_rotation: Slot rotation relative to the pad, in degrees.
            plated: Whether the hole is plated.
            paste_expansion, mask_expansion: Expansions, set as manual.
                ``mask_expansion`` is the top side, and the bottom too
                unless ``mask_bottom_expansion`` is given.
            mask_bottom_expansion: The bottom side's mask expansion; the
                sides are unlinked.
            mask_separate: Link (False) or unlink (True) the two sides.

        Returns:
            ``pads_changed``, ``pads_with_a_failed_write``, ``note`` and
            ``pads``, each as read back (position, size, rotation, hole,
            slot, plating, expansion states, ``mask_expansion_mm``,
            ``mask_bottom_expansion_mm``, ``mask_separate``,
            ``all_writes_ran``).
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        params: dict[str, Any] = {"footprint_name": footprint_name,
                                  "pad_name": pad_name,
                                  "library_path": library_path}
        for key, value in (("x_size", x_size), ("y_size", y_size),
                           ("hole_size", hole_size), ("hole_width", hole_width),
                           ("paste_expansion", paste_expansion),
                           ("mask_expansion", mask_expansion),
                           ("mask_bottom_expansion", mask_bottom_expansion)):
            if value is not None:
                params[key] = format_length(value)
        for key, value in (("rotation", rotation),
                           ("hole_rotation", hole_rotation)):
            if value is not None:
                params[key] = float(value)
        if shape is not None:
            params["shape"] = shape
        if plated is not None:
            params["plated"] = bool(plated)
        if mask_separate is not None:
            params["mask_separate"] = "true" if mask_separate else "false"
        if len(params) == 3:
            return {"success": False,
                    "error": "give at least one property to change"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_pad_properties", _with_units(params, units))

    @mcp.tool()
    async def lib_get_footprint_primitives(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Every non-pad primitive of one PcbLib footprint, with where it is.

        Tracks, arcs, regions, fills, texts and vias, each with its layer
        (the name the library's stack gives it, plus ``layer_id``), in mm
        from the footprint's origin: the frame the ``lib_add_footprint_*``
        writers take. Pads are in ``lib_get_pad_geometry``. Read-only.
        Each primitive is listed once. Altium's own iterator yields one
        just added to the footprint on screen twice until that footprint
        is shown again; the footprint writers here show it again after
        each write, and this read skips a repeat either way.

        Args:
            footprint_name: The footprint name in the library.
            library_path: Optional .PcbLib path; defaults to the focused
                library.

        Returns:
            {name, library_path, height_mm, bodies (a count),
            body_layers (the layer of each body), tracks
            [{layer, layer_id, x1, y1, x2, y2, width}], arcs [{layer,
            layer_id, xc, yc, r, start, end, width}], regions and fills
            [{layer, layer_id, x1, y1, x2, y2, area (regions)}], texts
            [{text, layer, layer_id, x, y, size, rotation, x1, y1, x2,
            y2}], vias [{x, y, diameter, hole}]}. A text's box is
            Altium's cached one and can lag an edit made this session.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_footprint_primitives",
            {"footprint_name": footprint_name, "library_path": library_path})

    @mcp.tool()
    async def lib_audit_footprint_vs_datasheet(
        footprint_name: str,
        spec_json: Any,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Audit a footprint against the manufacturer's land pattern.

        Deterministic comparison of the REAL pad geometry (read live
        from the .PcbLib) against a land-pattern spec transcribed from
        the manufacturer datasheet: pad count, every pad's position /
        size / shape / drill, the numbering sequence, pitch (implied by
        positions), the thermal pad, and its paste policy. Alignment is
        automatic (centroid + best of the four 90-degree rotations), so
        library origin and rotation conventions do not produce false
        errors; a MIRRORED pattern is deliberately not compensated and
        reports as position/sequence errors.

        DATASHEET DISCIPLINE: the spec must be transcribed from the
        manufacturer datasheet fetched in this conversation, and
        ``spec.source`` must cite it (url + figure/page + part number).
        Symbol metadata, distributor drawings, or memory of a package
        are not acceptable sources. There are no built-in package
        tables here, and that is intentional.

        The spec (JSON object or string):
          source: {datasheet_url, reference, part_number}   REQUIRED
          pads: [{name, x, y, w, h, shape?, hole?}]         explicit, mm
          dual_row: {count, pitch, span, pad_w, pad_h, shape?}
          quad: {count, pitch, span_x, span_y, pad_w, pad_h, shape?}
          thermal_pad: {name, x, y, w, h, shape?}
          thermal_paste: 'full' | 'windowed'
          position_tol / size_tol: mm, default 0.05

        dual_row expands with the SOIC/SOP convention (pin 1 top-left,
        CCW); quad with the QFP/QFN convention (pin 1 top of left side,
        CCW). For any other numbering give explicit ``pads``. pad_w has
        two meanings, kept for existing callers: in dual_row it is the
        pad's X size, ACROSS the row; in quad it runs ALONG the row.

        Returns:
            {ok, rotation_applied_deg, findings: [{check, severity,
             message, pad?, expected?, actual?}]} plus the footprint
            geometry under 'footprint' for reference. ok is true only
            with zero error-severity findings.
        """
        import json as _json

        from eda_agent.design.footprint_audit import (
            LandPatternSpec,
            audit_footprint_against_spec,
        )

        if isinstance(spec_json, str):
            try:
                spec_json = _json.loads(spec_json)
            except _json.JSONDecodeError as e:
                return {"ok": False, "reason":
                        f"spec_json is not valid JSON ({e}); pass the "
                        "land-pattern spec object or its JSON text"}
        try:
            spec = LandPatternSpec.model_validate(spec_json)
        except Exception as e:                         # pydantic ValidationError
            return {"ok": False, "reason":
                    f"spec_json does not match the land-pattern spec "
                    f"schema: {e}"}

        from ..bridge.exceptions import AltiumCommandError

        bridge = get_bridge()
        try:
            footprint = await bridge.send_command_async(
                "library.get_pad_geometry",
                {"footprint_name": footprint_name, "library_path": library_path},
            )
        except AltiumCommandError as e:
            return _unread_footprint(footprint_name, str(e), e.code)
        problem = _pad_geometry_problem(footprint)
        if problem:
            # A footprint that could not be read is not one with the wrong
            # pad count. Auditing the refusal reported "pad_count: expected
            # 8, actual 0" for a name that simply was not in the library.
            out = _unread_footprint(footprint_name, problem, "")
            out["footprint"] = footprint
            return out
        report = audit_footprint_against_spec(spec, footprint)
        report["footprint"] = footprint
        report["spec_source"] = spec.source.model_dump()
        return report

    @mcp.tool()
    async def lib_audit_footprint_vs_package(
        footprint_name: str,
        package: Any,
        density: str = "N",
        library_path: str = "",
        assembly_layer: str = "",
        courtyard_layer: str = "",
    ) -> dict[str, Any]:
        """Audit an existing PcbLib footprint against its package drawing.

        Where lib_audit_footprint_vs_datasheet compares pads with a land
        pattern typed in, this takes the PACKAGE (the same PackageSpec as
        lib_create_footprint_from_package) and checks the footprint is
        right for it. Reads only.

        Checked: pad count, numbering and pin 1 (a mirror-image footprint,
        the bottom-view mistake, is named as such); pitch exactly; row
        span, land sizes and land centres against the IPC-7351B pattern
        within the deviations measured between real vendor patterns and
        IPC (a footprint drawn to the vendor's own pattern passes); every
        land covering the centre of its terminal; holes against the leads;
        paste on an exposed pad (closed opening plus windows covering 50
        to 80 %) and on every land; manual solder mask settings; origin at
        the package centroid; and, from the footprint's other primitives,
        a courtyard that encloses the lands and the body, silkscreen off
        the copper, an assembly outline that follows the body, and the
        footprint height against the package's maximum. A turned footprint
        is compared after turning it back. What could not be read is
        listed under ``not_examined``.

        Run footprint_check_package on the package first: an audit against
        a misread package passes a footprint built from the same misread.

        Args:
            footprint_name: the footprint in the library.
            package: the PackageSpec as an object or JSON text.
            density: L, N or M, the IPC pattern compared against.
            library_path: optional .PcbLib path; default the focused one.
            assembly_layer, courtyard_layer: the mechanical layers for
                those roles; empty picks them by the library's layer kind
                ("Assembly Top" / "Courtyard Top"), then by name.

        Returns:
            ``{ok, errors, warnings, findings: [{check, severity, message,
            pad?, expected?, actual?}], examined, not_examined, alignment,
            metrics, notes, package_check}``. ``ok`` is true with no
            error-severity finding.
        """
        from ..bridge.exceptions import AltiumCommandError
        from ..design.landpattern.audit import audit_footprint_vs_package
        from ..design.landpattern.check import check_package, read_package
        from ..design.landpattern.emit import model_from_package
        from ..design.landpattern.spec import SpecError

        try:
            spec = read_package(package)
            model = model_from_package(spec, density=density or "N")
        except SpecError as e:
            return {"ok": False, "reason": str(e), "findings": []}

        bridge = get_bridge()
        try:
            footprint = await bridge.send_command_async(
                "library.get_pad_geometry",
                {"footprint_name": footprint_name, "library_path": library_path},
            )
        except AltiumCommandError as e:
            return _unread_footprint(footprint_name, str(e), e.code)
        problem = _pad_geometry_problem(footprint)
        if problem:
            out = _unread_footprint(footprint_name, problem, "")
            out["footprint"] = footprint
            return out
        lib_path = str(footprint.get("library_path") or library_path or "")

        prims: Optional[dict[str, Any]] = None
        prims_reason = ""
        try:
            reply = await bridge.send_command_async(
                "library.get_footprint_primitives",
                {"footprint_name": footprint_name, "library_path": lib_path})
            if isinstance(reply, dict) and isinstance(reply.get("tracks"), list):
                prims = reply
            else:
                prims_reason = "the primitives read carried no track list"
        except AltiumCommandError as e:
            prims_reason = (f"the primitives read failed ({e.code or e}); a "
                            "deployed script older than this tool has no "
                            "get_footprint_primitives handler")

        mech: Optional[list[dict[str, Any]]] = None
        notes: list[str] = []
        try:
            reply = await bridge.send_command_async("library.get_mech_layers", {})
            mpath = str((reply or {}).get("library_path") or "")
            if mpath and lib_path and mpath.lower() != lib_path.lower():
                notes.append(f"the mechanical layer kinds come from {mpath}, "
                             f"not {lib_path}; layers were matched by name only")
            else:
                mech = list((reply or {}).get("layers") or [])
        except AltiumCommandError as e:
            notes.append(f"mechanical layer kinds not read ({e.code or e}); "
                         "layers were matched by name only")

        report = audit_footprint_vs_package(
            spec, footprint["pads"], model=model, primitives=prims,
            mech_layers=mech, courtyard_layer=courtyard_layer,
            assembly_layer=assembly_layer, primitives_reason=prims_reason)
        report["notes"] = notes + list(report.get("notes") or [])
        if prims is not None:
            report["primitives_read"] = {
                k: len(prims.get(k) or [])
                for k in ("tracks", "arcs", "regions", "fills", "texts")}
            report["primitives_read"]["height_mm"] = prims.get("height_mm")
        sanity = check_package(spec)
        report["package_check"] = {
            "ok": sanity["ok"], "errors": sanity["errors"],
            "warnings": sanity["warnings"],
            "findings": [f for f in sanity["findings"]
                         if f["severity"] == "error"]}
        if not sanity["ok"]:
            report["notes"].append(
                "the package itself fails its sanity checks "
                "(package_check); the footprint was audited against a "
                "package that cannot be right")
        report["footprint"] = footprint_name
        report["library_path"] = lib_path
        return report

    @mcp.tool()
    async def lib_easyeda_search(query: str, limit: int = 20) -> dict[str, Any]:
        """Search LCSC / EasyEDA for parts by MPN or description.

        EXPECT THIS TO FAIL, and NOT because of credentials. Checked
        against the live service: the search route answers 404/403 with
        an HTML error page and no auth challenge, component fetch on the
        SAME host still works with no credentials, LCSC's own search API
        returns an error body, and the LCSC results page is rendered
        client-side (fetching it yields zero part numbers). There is no
        endpoint left to log in to, so no cookie, token or account will
        restore this.

        Search is therefore a BROWSER task, which also keeps it per-user:
        each person searches LCSC in their own signed-in browser, and no
        shared credential is ever stored by this server. Take the part
        number from there and import by id, which is unaffected:
        lib_easyeda_import(lcsc_id="C1234", ...).

        Online. Returns candidates only, no geometry: pick an lcsc_id
        and pass it to lib_easyeda_import. HTTPS-only with a host
        allowlist; override endpoints with EASYEDA_API_BASE.

        Args:
            query: MPN, keyword, or description fragment.
            limit: max candidates (default 20).

        Returns:
            {"ok": true, "count": N, "results": [{lcsc_id, mpn,
             manufacturer, package, description}]}.
        """
        from eda_agent.libimport.easyeda.fetch import (
            EasyEdaFetchError, search_components,
        )
        try:
            rows = await asyncio.to_thread(search_components, query, limit)
        except EasyEdaFetchError as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "count": len(rows), "results": rows}

    @mcp.tool()
    async def lib_kicad_import(
        symbol_path: str = "",
        footprint_path: str = "",
        symbol_name: str = "",
        target: str = "altium",
        schlib_path: str = "",
        pcblib_path: str = "",
        include_body_art: bool = True,
        unit: int = 0,
        model_3d_path: str = "",
    ) -> dict[str, Any]:
        """Convert a KiCad .kicad_sym / .kicad_mod into an Altium plan.

        Closes the gap that made KiCad-format part sources useless on
        the Altium side: this server could export Altium to KiCad but
        had no path back, so a hit from a KiCad registry or from the
        local KiCad libraries (see ``part_search``) was a dead end.

        Parses into the SAME neutral model the EasyEDA importer uses, so
        the Altium plan is produced by one shared emitter rather than a
        second copy that would drift from it.

        Give ``symbol_path``, ``footprint_path``, or both. Use
        ``symbol_name`` to pick one symbol out of a multi-symbol library
        (the first is used otherwise).

        MULTI-PART COMPONENTS: a quad gate or dual op-amp converts in
        ONE call to a real Altium multi-part symbol (``part_count`` plus
        per-pin ``owner_part_id``), not to N symbols to merge by hand.
        Set ``unit`` to a positive number to convert only that sub-part
        instead. Units are never merged into a flat symbol: they share
        coordinates by design, so a merged symbol has every unit's pins
        stacked on the same points and still looks converted.

        HIDDEN PINS: kept, and kept hidden. A hidden pin is
        electrically real (this is how symbols carry supply rails and
        no-connects), so dropping it would lose a pin while showing it
        would clutter the symbol.

        DERIVED SYMBOLS: over half of KiCad's standard entries carry no
        geometry of their own and inherit it from a parent. That link is
        followed, and ``warnings`` says where the geometry came from.

        target="altium": returns an ORDERED PLAN of this server's own
        library tools, exactly like lib_easyeda_import. Nothing is
        written to Altium; review the plan, then execute the steps.

        target="inspect": parse only, return a summary and warnings.
        Use this first on an unfamiliar library.

        DATASHEET DISCIPLINE: a converted footprint is somebody else's
        drawing, not a verified land pattern. Audit it against the
        manufacturer document with ``lib_audit_footprint_vs_datasheet``
        before trusting it.

        Returns:
            ``{"ok": ..., "component": {...}, "warnings": [...]}`` plus
            "steps"/"summary" for target="altium".
        """
        from ..libimport.easyeda.altium import build_altium_plan
        from ..libimport.kicad.reader import read_kicad_files

        if not symbol_path and not footprint_path:
            return {"ok": False,
                    "reason": "give symbol_path, footprint_path, or both"}

        for label, path in (("symbol_path", symbol_path),
                            ("footprint_path", footprint_path)):
            if path and not Path(path).is_file():
                return {"ok": False, "reason": f"{label} not found: {path}"}

        try:
            comp = read_kicad_files(
                symbol_path or None, footprint_path or None,
                symbol_name or None,
                unit=(int(unit) if int(unit or 0) > 0 else None))
        except (TypeError, ValueError, OSError) as exc:
            return {"ok": False, "reason": f"cannot read KiCad files: {exc}"}

        # A 3D body is only linked from a path the caller resolved, since
        # lib_link_3d_model loads the file: a guess would either fail at
        # execution or attach the wrong shape. part_fetch returns one in
        # model_3d_path when the installed KiCad ships it.
        if model_3d_path and comp.footprint is not None:
            if not Path(model_3d_path).is_file():
                return {"ok": False,
                        "reason": f"model_3d_path not found: "
                                  f"{model_3d_path}"}
            comp.footprint.model_3d_path = model_3d_path

        base: dict[str, Any] = {
            "ok": True,
            "component": comp.to_dict(),
            "warnings": list(comp.warnings),
        }

        mode = (target or "altium").strip().lower()
        if mode == "inspect":
            return base

        if mode != "altium":
            return {"ok": False,
                    "reason": f"unknown target {target!r}; use altium or "
                              f"inspect"}

        if not schlib_path or not pcblib_path:
            return {"ok": False,
                    "reason": "schlib_path and pcblib_path are required "
                              "for target=altium"}

        plan = build_altium_plan(
            comp, schlib_path, pcblib_path,
            include_body_art=include_body_art)
        if not plan["steps"]:
            return {"ok": False,
                    "reason": "the KiCad files carried no usable geometry",
                    "component": base["component"],
                    "warnings": plan["warnings"]}
        base["steps"] = plan["steps"]
        base["summary"] = plan["summary"]
        base["warnings"] = plan["warnings"]
        return base

    @mcp.tool()
    async def lib_easyeda_import(
        lcsc_id: str = "",
        payload_path: str = "",
        target: str = "altium",
        schlib_path: str = "",
        pcblib_path: str = "",
        output_dir: str = "",
        symbol_name: str = "",
        footprint_name: str = "",
        include_body_art: bool = True,
        fetch_3d: bool = True,
    ) -> dict[str, Any]:
        """Convert an EasyEDA / LCSC part to KiCad files or an Altium plan.

        Independent implementation from EasyEDA's published format
        spec, so symbol and footprint geometry, pads, drills, silkscreen
        and metadata all convert without any third-party converter.

        Source: ``lcsc_id`` fetches online (e.g. "C7420"), or
        ``payload_path`` reads a saved component JSON, which keeps the
        whole conversion offline and reproducible.

        target="kicad": writes ``<name>.kicad_sym`` and
        ``<name>.kicad_mod`` into ``output_dir`` and returns their paths.
        With ``fetch_3d`` (default on) the part's 3D model is fetched and
        converted to ``<name>.wrl``, which the footprint then references.
        Altium needs STEP and cannot use VRML, so this is KiCad only.

        target="altium": returns an ORDERED PLAN of this server's own
        library tools (lib_create_symbol, lib_add_pins,
        lib_create_footprint, lib_add_footprint_pads, lib_link_footprint,
        ...) that recreates the part in ``schlib_path`` / ``pcblib_path``.
        Nothing is written to Altium: review the plan, then execute the
        steps (tool_invoke drives them directly). Altium's binary library
        formats are never synthesized, the bridge authoring API is used
        instead.

        target="inspect": parse only, return the component summary and
        warnings. Use this first on an unfamiliar part.

        DATASHEET DISCIPLINE: an imported footprint is a vendor drawing,
        not ground truth. Audit it with
        ``lib_audit_footprint_vs_datasheet`` against the manufacturer
        land pattern before trusting it. Polygon pads and slotted holes
        have no faithful equivalent and are reported in ``warnings``.

        Returns:
            {"ok": ..., "component": {...}, "warnings": [...]} plus
            "files" (kicad) or "steps"/"summary" (altium).
        """
        import json as _json

        from eda_agent.libimport.easyeda import (
            build_altium_plan, footprint_to_kicad_mod, parse_component,
            symbol_to_kicad_sym,
        )
        from eda_agent.libimport.easyeda.fetch import (
            EasyEdaFetchError, fetch_component_json,
        )

        if not lcsc_id and not payload_path:
            return {"ok": False,
                    "reason": "give lcsc_id (online) or payload_path (offline)"}
        try:
            if payload_path:
                payload = _json.loads(
                    Path(payload_path).read_text(encoding="utf-8"))
            else:
                payload = await asyncio.to_thread(
                    fetch_component_json, lcsc_id)
        except EasyEdaFetchError as exc:
            return {"ok": False, "reason": str(exc)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "reason": f"cannot read payload: {exc}"}

        comp = parse_component(payload)
        base = {"ok": True, "component": comp.to_dict(),
                "warnings": comp.warnings}

        mode = (target or "altium").strip().lower()
        if mode == "inspect":
            return base

        if mode == "kicad":
            if not output_dir:
                return {"ok": False, "reason": "output_dir is required "
                                               "for target=kicad"}
            out = Path(output_dir)
            try:
                out.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"ok": False, "reason": f"cannot create "
                                               f"{output_dir}: {exc}"}
            stem = _safe_filename(
                symbol_name or comp.mpn or comp.lcsc_id or "easyeda_part",
                "easyeda_part")
            files: dict[str, str] = {}
            if comp.symbol is not None:
                p = out / f"{stem}.kicad_sym"
                p.write_text(symbol_to_kicad_sym(comp), encoding="utf-8")
                files["symbol"] = str(p)
            if comp.footprint is not None:
                model_ref = None
                if fetch_3d and comp.footprint.model_3d_uuid:
                    # EasyEDA serves an OBJ-family payload with the
                    # material library inlined; model3d converts it to
                    # the VRML KiCad renders natively.
                    try:
                        from eda_agent.libimport.easyeda.fetch import (
                            fetch_3d_model,
                        )
                        from eda_agent.libimport.easyeda.model3d import (
                            obj_to_wrl, parse_easyeda_obj,
                        )
                        raw3d = fetch_3d_model(
                            comp.footprint.model_3d_uuid)
                        mdl = parse_easyeda_obj(
                            raw3d.decode("utf-8", "replace"))
                        wrl_name = _safe_filename(
                            comp.footprint.model_3d_name or stem, stem
                        ) + ".wrl"
                        wp = out / wrl_name
                        wp.write_text(obj_to_wrl(
                            mdl, name=comp.footprint.name), encoding="utf-8")
                        files["model_3d"] = str(wp)
                        model_ref = f"${{KIPRJMOD}}/{wrl_name}"
                        comp.warnings.extend(mdl.warnings)
                    except (EasyEdaFetchError, ValueError, OSError) as exc:
                        # A missing 3D model must not lose the symbol and
                        # footprint that already converted cleanly.
                        comp.warnings.append(
                            f"3D model could not be converted: {exc}")
                fp_stem = _safe_filename(
                    footprint_name or comp.footprint.name, stem)
                p = out / f"{fp_stem}.kicad_mod"
                p.write_text(footprint_to_kicad_mod(
                    comp, model_path=model_ref), encoding="utf-8")
                files["footprint"] = str(p)
            if not files:
                return {"ok": False, "reason": "payload had no geometry",
                        "warnings": comp.warnings}
            base["files"] = files
            return base

        if mode == "altium":
            if not schlib_path or not pcblib_path:
                return {"ok": False,
                        "reason": "schlib_path and pcblib_path are required "
                                  "for target=altium"}
            plan = build_altium_plan(
                comp, schlib_path, pcblib_path,
                symbol_name=symbol_name or None,
                footprint_name=footprint_name or None,
                include_body_art=include_body_art,
            )
            if not plan["steps"]:
                # An empty plan with ok=True reads as success, and the
                # caller then executes nothing and believes the part was
                # imported. The kicad branch already refuses this; keep
                # both targets honest about a payload with no geometry.
                return {"ok": False,
                        "reason": "payload had no symbol or footprint "
                                  "geometry, so there is nothing to build",
                        "component": base.get("component"),
                        "warnings": plan["warnings"]}
            base["steps"] = plan["steps"]
            base["summary"] = plan["summary"]
            # build_altium_plan already appends a warning for any text
            # the bridge will flatten, so both importers get it and
            # neither can drift.
            base["warnings"] = plan["warnings"]
            return base

        return {"ok": False,
                "reason": f"unknown target {target!r}; "
                          f"use kicad, altium, or inspect"}

    @mcp.tool()
    async def lib_clear_source_library(
        library_path: str = "",
        component_names: Optional[list[str]] = None,
        clear_target_file_name: bool = True,
        sync_design_item_id: bool = True,
    ) -> dict[str, Any]:
        """Unpin a SchLib's symbols from their source-library provenance.

        Library-side sibling of ``sch_clear_source_library``. Symbols
        copied in from another library (a vendor pack, a stock library
        like the built-in miscellaneous-devices set) carry three
        provenance fields pointing at their ORIGIN: SourceLibraryName,
        TargetFileName, and DesignItemId. Left stale, every placement
        from your library re-links against a library that no longer
        exists on the machine and lands in the <Not Found> state.

        Per matching component: clears SourceLibraryName, resets
        TargetFileName to '*', and syncs DesignItemId to the
        LibReference (each independently switchable). This is the
        minimal fast path of what lib_normalize_implementations does
        inside its full model sweep, when you only need the provenance
        cleaned, use this. Deferred save: flush with app_save_all.

        Args:
            library_path: absolute .SchLib path; empty = focused library.
            component_names: restrict to these LibReferences; None/empty
                = every component in the library.
            clear_target_file_name: reset TargetFileName to '*'.
            sync_design_item_id: set DesignItemId = LibReference where
                they differ.

        Returns:
            {"library_path": ..., "total": inspected,
             "cleared_source_library": n, "cleared_target_file_name": n,
             "synced_design_item_id": n}.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "library_path": library_path,
            "clear_target_file_name":
                "true" if clear_target_file_name else "false",
            "sync_design_item_id":
                "true" if sync_design_item_id else "false",
        }
        if component_names:
            # Comma-separated field; a LibReference containing a comma
            # arrives as two names and a fragment can match a real
            # component whose provenance this tool then rewrites.
            # Refuse rather than strip.
            bad = [n for n in component_names if "," in str(n)]
            if bad:
                return {"ok": False, "reason":
                        f"component names {bad} contain a comma, which "
                        "is the wire-format separator; fragments could "
                        "match other components, so the call is refused"}
            params["component_names"] = ",".join(component_names)
        return await bridge.send_command_async(
            "library.clear_source_library", params,
        )

    @mcp.tool()
    async def lib_normalize_implementations(
        library_path: str = "",
        rename_map: Optional[dict[str, str]] = None,
        dedupe_only: bool = False,
        source_library: str = "",
        use_component_library: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Clean up every component's models in a SchLib for a self-contained package.

        Whole-library sweep. Per component it: clears the component-level
        provenance (SourceLibraryName, TargetFileName); collapses duplicate
        models, keeping ONE per (model_type, model_name) IN PLACE so the kept
        model's parameters, MapAsString pin-map, and datafile link are all
        preserved; re-flags the survivor current if any duplicate was;
        optionally renames a model's name and its datafile entity via
        rename_map; and repairs any footprint (PCBLIB) left with no datafile
        link. It does NOT destroy and rebuild implementations, so nothing on a
        model is lost.

        With source_library set, it also writes that value into the Location
        of every PCBLIB model's datafile link, turning a name-only library
        (footprints resolve but do not embed) into an embeddable one in the
        same pass. Existing Locations are only overwritten when source_library
        is given; they are never blanked. Get the exact string from a
        known-good model first (see lib_set_model_source).

        Note: it preserves what is present; it cannot resurrect data a prior
        broken run already dropped. If a library was damaged by an earlier
        version, restore the backup and run this on it.

        If you are also renaming footprint entities, run lib_rename_footprint
        on the PcbLib first, then pass the same {old: new} as rename_map so
        symbol and footprint stay matched.

        Args:
            library_path: optional .SchLib path; defaults to the focused lib.
            rename_map: optional {old_model_name: new_model_name} applied to
                the kept model's name and matching datafile entity.
            dedupe_only: skip renames; only collapse duplicates + clear source.
            source_library: optional; when set, written into every PCBLIB
                model's datafile Location so footprints embed on compile.
            use_component_library: optional; sets the embed-vs-search flag on
                the models whose Location is written.

        Returns:
            {"success": true, "components_touched": N, "duplicates_removed": N,
             "sources_cleared": N, "links_repaired": N, "sources_set": N}.
        """
        pairs = ";".join(f"{k}={v}" for k, v in (rename_map or {}).items())
        params: dict[str, Any] = {
            "library_path": library_path,
            "rename_map": pairs,
            "dedupe_only": "true" if dedupe_only else "false",
            "source_library": source_library,
        }
        if use_component_library is not None:
            params["use_component_library"] = (
                "true" if use_component_library else "false"
            )
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.normalize_implementations", params
        )

    @mcp.tool()
    async def lib_inspect_cse_zip(zip_path: str) -> dict[str, Any]:
        """Identify the library members of a Component Search Engine zip.

        Stage 5 of the autonomous flow (library readiness,
        reference/autonomy-roadmap.md): a SamacSys / Component Search
        Engine download carries the symbol, footprint and 3D model for an
        MPN in one zip. This tool reads the archive WITHOUT extracting
        anything: which member is the .SchLib, which the .PcbLib, which
        the STEP model, the best-effort MPN, and any members attempting
        path traversal (``suspicious``). Inspect first, then stage the
        files with ``lib_extract_cse_zip``. Pure Python, no Altium.

        Args:
            zip_path: Absolute path to the downloaded zip.

        Returns:
            ``{"ok": True, "mpn", "schlib", "pcblib", "step", "extras",
            "suspicious"}`` (member names; None when absent) or
            ``{"ok": False, "reason": "..."}`` when the file is missing,
            not a zip, or contains no .SchLib/.PcbLib.
        """
        return inspect_cse_zip(zip_path)

    @mcp.tool()
    async def lib_extract_cse_zip(
        zip_path: str,
        dest_dir: str = "",
    ) -> dict[str, Any]:
        """Extract a Component Search Engine zip and build its install plan.

        Stage 5 of the autonomous flow (library readiness,
        reference/autonomy-roadmap.md): stages the recognized members
        (.SchLib / .PcbLib / STEP) flattened into ``dest_dir`` and returns
        an ordered ``install_plan`` whose steps are exact
        ``lib_install_library`` / ``lib_link_footprint`` /
        ``lib_link_3d_model`` parameter dicts -- dispatch them in order to
        make the part placeable. Any archive member with an absolute path,
        drive letter, or ``..`` segment rejects the WHOLE archive before
        anything is written. Extraction is pure Python; only the install
        plan touches Altium when dispatched.

        Args:
            zip_path: Absolute path to the downloaded zip.
            dest_dir: Where to stage the files. Default:
                ``<workspace>/cse_imports/<zip stem>``.

        Returns:
            ``{"ok": True, "mpn", "files": [abs paths], "extracted":
            {"schlib"/"pcblib"/"step": abs path or None}, "install_plan":
            [{"tool", "params"}]}`` or ``{"ok": False, "reason": "..."}``.
        """
        dest = (
            Path(dest_dir)
            if dest_dir
            else get_config().workspace_dir / "cse_imports"
            / Path(zip_path).stem
        )
        return extract_cse_zip(zip_path, dest)
