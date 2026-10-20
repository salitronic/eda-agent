# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library management tools for Altium Designer MCP Server."""

import asyncio
import math
import re
from pathlib import Path
from typing import Any, Optional
from ..bridge import get_bridge
from ..bridge.payload import payload_safe
from ..fileio import pcblib_models
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


def _step_solid_count(path: str) -> Optional[int]:
    """Solids in a STEP file, or None when it cannot be read."""
    try:
        text = Path(path).read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return None
    return (text.count("MANIFOLD_SOLID_BREP(")
            + text.count("BREP_WITH_VOIDS("))


async def _remove_old_bodies(bridge, footprint: str) -> dict:
    """Take a footprint's 3D bodies off before a new model is linked.

    Linking adds a body; it never replaced one. A follow-up take rebuilt
    every footprint's model and each kept its old plain box beside the
    new one, removed afterwards by hand.
    """
    if not footprint.strip():
        return {"skipped": "no footprint name, so the current one's bodies "
                           "were left as they are"}
    try:
        out = await bridge.send_command_async(
            "library.delete_footprint_primitives",
            {"footprint_name": footprint, "object_type": "component_body",
             "confirm": "true"})
    except Exception as exc:                    # noqa: BLE001
        return {"error": str(exc)}
    if isinstance(out, dict):
        return {"removed": out.get("removed", 0), "leftover": out.get("leftover", 0)}
    return {"removed": 0}


async def _save_library(bridge, library_path) -> tuple[bool, str]:
    """Write ONE library to disk: (saved, reason).

    These passes used application.save_all, which writes every open
    document, so fixing one library's designators rewrote sheets and
    boards in other projects (issue #94). save_document measures the save
    by the file getting newer, and file times tick every two seconds, so
    a save right after the previous one is tried once more.
    """
    params = {"file_path": library_path} if library_path else {}
    reply: dict = {}
    for attempt in range(2):
        reply = await bridge.send_command_async(
            "application.save_document", params, timeout=_GEOMETRY_TIMEOUT)
        reply = reply if isinstance(reply, dict) else {}
        if reply.get("saved"):
            return True, ""
        if attempt == 0:
            await asyncio.sleep(2.1)
    return False, str(reply.get("reason") or "the library did not get newer on disk")


async def _open_document(bridge, path: str) -> str:
    """Make sure Altium has ``path`` open as a document; '' or the problem.

    Focused when it is open already; opened (WorkspaceManager:OpenObject)
    and focused when it is not.
    """
    from ..bridge.exceptions import AltiumError

    async def focus() -> str:
        try:
            await bridge.send_command_async(
                "application.set_active_document", {"file_path": path})
            return ""
        except AltiumError as exc:
            return str(exc)

    problem = await focus()
    if problem and "not loaded" in problem.lower():
        try:
            await bridge.send_command_async("application.run_process", {
                "process_name": "WorkspaceManager:OpenObject",
                "parameters": f"ObjectKind=Document|FileName={path}"})
        except AltiumError as exc:
            return f"could not open {path}: {exc}"
        problem = await focus()
    return f"could not open {path}: {problem}" if problem else ""


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


def corner_pct_from_radius(radius: float, x_size: float, y_size: float) -> int:
    """Altium's roundrect percentage for an absolute corner radius.

    Altium has no setter for the absolute radius: it stores a percentage
    (0..100) of half the pad's shortest side, so a pad resized later keeps
    the percentage and its radius changes with it."""
    half = min(float(x_size), float(y_size)) / 2.0
    if half <= 0:
        return 0
    return max(0, min(100, round(100.0 * float(radius) / half)))


def _pad_corner_pct(p: dict[str, Any], units: str) -> int:
    """A pad dict's corner percentage; corner_radius_length wins when given."""
    if p.get("corner_radius_length") is not None:
        return corner_pct_from_radius(
            p["corner_radius_length"], p.get("x_size", length_in(units, 60)),
            p.get("y_size", length_in(units, 60)))
    return round(p.get("corner_radius", 25))


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
        # A non-plated mounting hole carries no designator in a land pattern.
        if not desig and not (p.get("hole_size") and _unplated(p.get("plated"))):
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
            f"corner_radius={_pad_corner_pct(p, units)}",
            f"rotation={_payload_safe(p.get('rotation', 0))}",
            f"layer={_payload_safe(p.get('layer', 'TopLayer'))}",
        ]
        # Optional, and only sent when the pad carries them, so every
        # payload without them is byte-identical to what it always was.
        for key in _OPTIONAL_PAD_LENGTHS:
            if p.get(key) is not None:
                fields.append(f"{key}={format_length(p[key])}")
        turn = _slot_rotation(p, units)
        if turn is not None:
            fields.append(f"hole_rotation={format_length(turn)}")
        if p.get("plated") is not None:
            fields.append(f"plated={'false' if _unplated(p['plated']) else 'true'}")
        op_strs.append(";".join(fields))
    return "~~".join(op_strs), skipped_invalid


def _unplated(plated: Any) -> bool:
    if plated is None:
        return False
    if isinstance(plated, str):
        return plated.strip().lower() in ("false", "0", "no")
    return not plated


def _slot_rotation(p: dict[str, Any], units: str) -> Optional[float]:
    """The slot's turn from the pad's X axis, or None for no slot.

    Altium runs a slot along the pad's X. A slot given without
    ``hole_rotation`` runs along the pad's longer side, so a pad taller
    than it is wide has its slot turned 90 degrees. A USB-C receptacle's
    shell pads (1.1 x 1.9 mm with a 1.4 mm slot, 1.1 x 1.6 mm with a
    1.1 mm one) came out with their slots across the pads.
    """
    try:
        hole = float(p.get("hole_size") or 0)
        slot = float(p.get("hole_length") or 0)
    except (TypeError, ValueError):
        return None
    if not (hole > 0 and slot > hole):
        return None
    if p.get("hole_rotation") is not None:
        return float(p["hole_rotation"])
    xs = float(p.get("x_size", length_in(units, 60)))
    ys = float(p.get("y_size", length_in(units, 60)))
    return 90.0 if ys > xs else 0.0


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
    out as ``x,y`` pairs joined by ``/``, unrounded. A point given as
    ``[x, y, sweep]`` makes the edge from it to the next point a true arc
    of ``sweep`` degrees, counterclockwise when positive; those edges go
    out in an ``arcs`` field as ``index,cx,cy,radius,angle1,angle2``, the
    index 1-based and the angles Altium's, which always run
    counterclockwise from angle1 to angle2. Returns ``(payload,
    problems)``: a region with no layer, fewer than three numeric points
    or an arc that cannot be drawn is named in ``problems`` and left out,
    never sent with a guessed layer or a point read as zero.
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
            verts = [(float(p[0]), float(p[1]),
                      float(p[2]) if len(p) > 2 else 0.0) for p in pts]
        except (TypeError, ValueError, IndexError, KeyError):
            problems.append(f"region {i} points must be [x, y] or "
                            "[x, y, sweep] numbers")
            continue
        if len(verts) > 3 and verts[0][:2] == verts[-1][:2]:
            verts = verts[:-1]
        if len(verts) < 3:
            problems.append(f"region {i} needs at least three points")
            continue
        arcs: list[str] = []
        bad_arc = ""
        for k, (x, y, sweep) in enumerate(verts):
            if not sweep:
                continue
            nx, ny, _ = verts[(k + 1) % len(verts)]
            arc = _edge_arc(x, y, nx, ny, sweep)
            if isinstance(arc, str):
                bad_arc = f"region {i} point {k}: {arc}"
                break
            cx, cy, r, a1, a2 = arc
            arcs.append(f"{k + 1},{format_length(cx)},{format_length(cy)},"
                        f"{format_length(r)},{_angle_text(a1)},{_angle_text(a2)}")
        if bad_arc:
            problems.append(bad_arc)
            continue
        points = "/".join(f"{format_length(x)},{format_length(y)}"
                          for x, y, _ in verts)
        op = f"layer={_payload_safe(layer)};points={points}"
        if arcs:
            op += ";arcs=" + "/".join(arcs)
        ops.append(op)
    return "~~".join(ops), problems


def _edge_arc(x1: float, y1: float, x2: float, y2: float,
              sweep: float) -> tuple[float, float, float, float, float] | str:
    """The circle of an edge drawn as an arc from (x1, y1) to (x2, y2)
    that sweeps ``sweep`` degrees, counterclockwise when positive.

    Returns ``(cx, cy, radius, angle1, angle2)`` with the angles in
    Altium's sense, counterclockwise from angle1 to angle2 and each in
    [0, 360), so a clockwise edge has its start at angle2. Returns the
    reason as a string when there is no such arc.
    """
    if not math.isfinite(sweep) or abs(sweep) >= 360:
        return f"sweep {sweep:g} must be between -360 and 360 degrees"
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord == 0:
        return "an arc needs its end point apart from its start"
    half = math.radians(abs(sweep)) / 2
    radius = chord / (2 * math.sin(half))
    # The centre sits on the chord's perpendicular bisector, on the left
    # of the travel for a counterclockwise arc under 180 degrees and on
    # the right past it; cos(half) changes sign at 180 and does both.
    offset = radius * math.cos(half) * (1 if sweep > 0 else -1)
    cx = (x1 + x2) / 2 - dy / chord * offset
    cy = (y1 + y2) / 2 + dx / chord * offset
    start = math.degrees(math.atan2(y1 - cy, x1 - cx)) % 360
    end = math.degrees(math.atan2(y2 - cy, x2 - cx)) % 360
    if sweep > 0:
        return cx, cy, radius, start, end
    return cx, cy, radius, end, start


def _angle_text(deg: float) -> str:
    """An angle for the wire: ten decimals, trailing zeros dropped, and a
    value that rounds to 360 sent as 0."""
    text = f"{round(deg, 10) % 360:.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


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


def _vias_payload(vias: list[dict[str, Any]], size: float,
                  hole_size: float) -> tuple[str, list[str]]:
    """The ``vias`` batch payload for library.add_footprint_vias.

    Each via is ``{"x": .., "y": ..}`` with an optional ``size`` and
    ``hole_size`` (the call's defaults otherwise). A via that is not
    numeric, or whose hole is not inside its diameter, is named in the
    problems and left out.
    """
    ops: list[str] = []
    problems: list[str] = []
    for i, via in enumerate(vias or []):
        if not isinstance(via, dict):
            problems.append(f"via {i} is not an object")
            continue
        try:
            x, y = float(via["x"]), float(via["y"])
            d = float(via.get("size", size))
            h = float(via.get("hole_size", hole_size))
        except (TypeError, ValueError, KeyError):
            problems.append(f"via {i} needs numeric x and y")
            continue
        if not 0 < h < d:
            problems.append(f"via {i} needs 0 < hole_size < size")
            continue
        ops.append(f"x={format_length(x)};y={format_length(y)};"
                   f"size={format_length(d)};hole_size={format_length(h)}")
    return "~~".join(ops), problems


def _bodies_payload(bodies: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The ``bodies`` batch payload for library.add_footprint_bodies.

    Each body is ``{"points": [[x, y], ...], "overall_height": ..}`` with
    optional ``standoff_height``, ``layer``, ``identifier``, ``color``
    (an Altium TColor integer) and ``opacity`` (0 to 1). A body with
    fewer than three numeric points, or heights that do not satisfy
    0 <= standoff < overall, is named in the problems and left out.
    """
    ops: list[str] = []
    problems: list[str] = []
    for i, body in enumerate(bodies or []):
        if not isinstance(body, dict):
            problems.append(f"body {i} is not an object")
            continue
        try:
            pts = [(float(p["x"]), float(p["y"])) if isinstance(p, dict)
                   else (float(p[0]), float(p[1]))
                   for p in body.get("points") or []]
            overall = float(body["overall_height"])
            standoff = float(body.get("standoff_height") or 0)
        except (TypeError, ValueError, KeyError, IndexError):
            problems.append(f"body {i} needs numeric points and overall_height")
            continue
        if len(pts) < 3:
            problems.append(f"body {i} needs three or more points")
            continue
        if not 0 <= standoff < overall:
            problems.append(f"body {i} needs 0 <= standoff_height < overall_height")
            continue
        op = ("points=" + "/".join(f"{format_length(x)},{format_length(y)}"
                                   for x, y in pts)
              + f";overall_height={format_length(overall)}"
              + f";standoff_height={format_length(standoff)}")
        for key in ("layer", "identifier"):
            if body.get(key):
                op += f";{key}={_payload_safe(str(body[key]))}"
        if body.get("color") is not None:
            op += f";color={int(body['color'])}"
        if body.get("opacity") is not None:
            op += f";opacity={min(max(float(body['opacity']), 0.0), 1.0)}"
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


def _standard_symbol_warnings(components: list[dict]) -> list[dict]:
    """Amplifiers whose body is a rectangle with no triangle (polygon)."""
    from eda_agent.design.symbol_gen import classify_amplifier_pins

    out = []
    for comp in components:
        body = comp.get("body") or {}
        if not body or body.get("polygons", 0) or not body.get("rectangles", 0):
            continue
        groups = classify_amplifier_pins(comp.get("pins") or [])
        if groups is None:
            continue
        out.append({
            "name": comp.get("name"),
            "description": comp.get("description", ""),
            "units": len(groups["units"]),
            "reason": "the pins form an amplifier (IN+, IN-, OUT) but the "
                      "body is a rectangle; the standard symbol is a "
                      "triangle",
        })
    return out


async def _emit_amplifier_symbol(name: str, amp: Any, prefix: str,
                                 description: str) -> dict[str, Any]:
    """Create a triangle amplifier symbol from AmplifierSymbol geometry.

    Per part: the body (triangle or block) FIRST, then the pins, then the
    +/- marks, because Altium draws in insertion order and a solid body
    added later paints over the pins (see lib_create_ic_symbol)."""
    bridge = get_bridge()
    created = await bridge.send_command_async(
        "library.create_symbol",
        {"name": name, "designator_prefix": prefix,
         "description": description, "part_count": str(amp.part_count)},
    )
    steps: list[dict[str, Any]] = []
    total_pins = 0
    for part in amp.parts:
        res: dict[str, Any] = {"part": part["part"]}
        for poly in part["polygons"]:
            verts = ",".join(str(v) for pt in poly["points"] for v in pt)
            res["triangle"] = await bridge.send_command_async(
                "library.add_symbol_polygon",
                {"vertices": verts, "fill_color": poly["fill_color"],
                 "owner_part_id": poly["owner_part_id"]})
        for r in part["rectangles"]:
            res["block"] = await bridge.send_command_async(
                "library.add_symbol_rectangle",
                {"x1": r["x1"], "y1": r["y1"], "x2": r["x2"], "y2": r["y2"],
                 "fill_color": r["fill_color"], "border_color": 0,
                 "owner_part_id": r["owner_part_id"]})
        payload, _ = _pins_payload(part["pins"])
        res["pins"] = await bridge.send_command_async(
            "library.add_pins", {"pins": payload})
        total_pins += len(part["pins"])
        if part["texts"]:
            ops = [";".join([f"text={t['text']}", f"x={t['x']}",
                             f"y={t['y']}", f"font_size={t['font_size']}",
                             f"owner_part_id={t['owner_part_id']}"])
                   for t in part["texts"]]
            res["marks"] = await bridge.send_command_async(
                "library.add_symbol_text", {"texts": "~~".join(ops)})
        steps.append(res)
    return {"symbol": name, "body": "amplifier", "parts": amp.part_count,
            "pins": total_pins, "create": created, "steps": steps}


#: Line-size code for symbol glyph lines: Altium's TSize, 0 smallest,
#: 1 small (its default), 2 medium, 3 large. Not mils.
SYMBOL_LINE_WIDTH = 1

#: Outline colour of a discrete glyph's body (resistor, fuse, crystal), as
#: Altium's BGR integer: the same blue its symbol lines default to, so a
#: resistor box does not come out black beside blue capacitor plates.
SYMBOL_LINE_COLOR = 0xFF0000


#: Every key lib_set_label_formats encodes into an op.
_LABEL_OP_KEYS = frozenset({
    "target", "font_id", "color", "is_hidden", "orientation",
    "justification", "x", "y", "text",
})


async def _footprint_3d_body(bridge: Any, step: Any, spec: Any, model: Any,
                             fp_name: str, mode: str) -> dict[str, Any]:
    """The 3D body of a footprint built from a package drawing.

    First choice a STEP model designed from the same drawing
    (design.models3d), linked to the footprint; the fallback an extruded
    body of the package outline from its standoff to its height. Returns
    ``{kind, note, ...}`` where kind is step, extruded or none.
    """
    from ..design import models3d

    if mode == "none":
        return {"kind": "none", "note": "3D body: none, as asked"}
    why_not_step = ""
    if mode in ("auto", "step"):
        import logging
        import time as _time
        log = logging.getLogger(__name__)
        t0 = _time.monotonic()
        plan = models3d.plan_model(spec, model)
        if plan is None:
            why_not_step = (f"the {spec.family} family is not modelled yet "
                            f"(modelled: {', '.join(models3d.MODELLED_FAMILIES)})"
                            if spec.family not in models3d.MODELLED_FAMILIES
                            else "the package gives too few dimensions for its shape")
        else:
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                           for ch in fp_name)
            path = get_config().workspace_dir / "models" / f"{safe}.step"
            t1 = _time.monotonic()
            wrote = models3d.write_step(plan, path, name=fp_name)
            # The first model in a server once took 146 s here and none
            # outside it could be made slow; the log says which step.
            log.info("3D model %s: plan %.1f s, write %.1f s (%s)", fp_name,
                     t1 - t0, _time.monotonic() - t1, wrote.get("writer") or "none")
            if wrote["ok"]:
                linked = await step("model_3d", bridge.send_command_async(
                    "library.link_3d_model",
                    {"component_name": fp_name, "model_path": wrote["path"],
                     "offset_x": 0, "offset_y": 0, "offset_z": 0,
                     "rotation_x": 0, "rotation_y": 0, "rotation_z": 0}))
                if linked is not None:
                    note = (f"3D body: STEP model from the drawing, written with "
                            f"{wrote['writer']} to {wrote['path']} and linked")
                    if plan.assumed:
                        note += "; not in the drawing, so assumed: " + "; ".join(plan.assumed)
                    return {"kind": "step", "path": wrote["path"],
                            "writer": wrote["writer"], "assumed": plan.assumed,
                            "sources": plan.sources, "note": note}
                why_not_step = "the STEP model was written but Altium did not link it"
            else:
                why_not_step = wrote.get("reason") or "no STEP writer worked"
        if mode == "step":
            return {"kind": "none", "note": f"3D body: none, {why_not_step}"}

    b = model.body
    if b.z1 <= b.z0 or b.x1 <= b.x0 or b.y1 <= b.y0:
        return {"kind": "none", "note": "3D body: none, the package gives no "
                "body size and height" + (f" ({why_not_step})" if why_not_step else "")}
    payload = (f"points={b.x0},{b.y0}/{b.x1},{b.y0}/{b.x1},{b.y1}/{b.x0},{b.y1}"
               f";overall_height={b.z1};standoff_height={max(b.z0, 0.0)}"
               f";identifier={payload_safe(fp_name)}")
    reply = await step("model_3d", bridge.send_command_async(
        "library.add_footprint_bodies", _with_units(
            {"bodies": payload, "footprint_name": fp_name}, "mm")))
    ok = isinstance(reply, dict) and int(reply.get("added") or 0) == 1
    reason = f" because {why_not_step}" if why_not_step else ""
    if not ok:
        return {"kind": "none", "note": "3D body: none, the extruded fallback "
                f"was not added{reason}"}
    return {"kind": "extruded", "note": "3D body: an extruded box of the package "
            f"outline and height, the fallback{reason}; lib_create_3d_model "
            "replaces it with a STEP model built from the drawing"}


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
        body: str = "auto",
        invert_on_top: bool = False,
    ) -> dict[str, Any]:
        """Create a complete IC symbol (body plus every pin) in one call.

        Amplifiers get the standard triangle: with ``body="auto"`` it is
        chosen when the pin names form IN+/IN-/OUT units (+INA/-INA/OUTA
        and IN1+/IN1-/OUT1 count), and a dual or quad becomes one
        triangle per part plus a power part. Copying a standard part
        from the user's library with lib_copy_component is an
        alternative.

        Otherwise a rectangle: you group the pins (inputs, power and
        control LEFT; outputs and status RIGHT, discipline rule 18) and
        this lays them on grid with the top-left pin's wire end at the
        origin (rule 14).

        Args:
            name: Component name.
            left_pins: Pins on the LEFT side, top-to-bottom. Each a dict
                with `designator`, `name`, and optional `electrical_type`
                (input/output/power/passive/...).
            right_pins: Pins on the RIGHT side, top-to-bottom, same shape.
                For an amplifier the sides are ignored: the triangle puts
                every pin where the convention does.
            designator_prefix: e.g. "U".
            description: Component description.
            body: "auto" (triangle for amplifiers, else rectangle),
                "amplifier" or "rectangle".
            invert_on_top: triangle only; put IN- above IN+.

        Returns:
            Dict with the symbol name, pin count, ``body`` drawn, body
            extent, and the per-step bridge results.
        """
        from eda_agent.design.symbol_gen import (
            generate_amplifier_symbol, generate_ic_symbol,
            looks_like_amplifier)

        wanted = (body or "auto").strip().lower()
        if wanted not in ("auto", "amplifier", "rectangle"):
            return {"ok": False, "reason":
                    "body must be 'auto', 'amplifier' or 'rectangle'"}
        all_pins = [*left_pins, *right_pins]
        if wanted == "amplifier" or (
                wanted == "auto" and looks_like_amplifier(all_pins,
                                                          description)):
            try:
                amp = generate_amplifier_symbol(all_pins,
                                                invert_on_top=invert_on_top)
            except ValueError as e:
                if wanted == "amplifier":
                    return {"ok": False, "reason": str(e)}
                amp = None
            if amp is not None:
                return await _emit_amplifier_symbol(
                    name, amp, designator_prefix, description)

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
        pin_map: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Create a standard DISCRETE symbol (the conventional glyph) in ONE call.

        Use this, not a rectangle, for any discrete part. Draws resistor,
        capacitor, polarized_capacitor, inductor (arcs), diode, led,
        crystal, fuse, zener, schottky, tvs, tvs_bidirectional, switch,
        push_button, and the three-pin nmos, pmos, npn and pnp. Two-pin
        parts have pin 1 on the left and pin 2 on the right; a transistor
        has its gate/base on the left and the other two leaving the top
        and bottom. Glyph detail is emitted in raw mils, so it keeps its
        sub-100-mil shape while every pin stays on the 100-mil grid.

        Args:
            name: Component name (e.g. "R_0402").
            kind: one of the kinds above (aliases such as r/c/cp/l/d/xtal/
                f/z/sd/tvs_bi/sw/button/nfet/pfet/bjt_npn are accepted).
            designator_prefix: default by kind (R/C/L/D/Y/F/S/Q) if omitted.
            description: Component description.
            pin_map: transistors only, role -> pin number, e.g.
                {"G": "1", "S": "2", "D": "3"}. Defaults are the common
                SOT-23 assignment (MOSFET 1=G 2=S 3=D, BJT 1=B 2=E 3=C);
                check the datasheet.

        Returns:
            Dict with the symbol name and per-step bridge results.
        """
        from eda_agent.design.symbol_gen import (
            TRANSISTOR_DEFAULT_PINS, generate_passive_symbol,
            generate_transistor_symbol)

        try:
            if kind.strip().lower() in ("nmos", "pmos", "npn", "pnp",
                                        "nfet", "pfet", "n_mosfet",
                                        "p_mosfet", "mosfet_n", "mosfet_p",
                                        "bjt_npn", "bjt_pnp"):
                geom = generate_transistor_symbol(kind, pin_map)
            elif pin_map:
                return {"ok": False, "reason":
                        "pin_map applies to transistors only "
                        f"({sorted(TRANSISTOR_DEFAULT_PINS)})"}
            else:
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
            "zener": "D", "z": "D", "schottky": "D", "sd": "D",
            "tvs": "D", "tvs_uni": "D", "tvs_bidirectional": "D",
            "tvs_bi": "D", "bidirectional_tvs": "D",
            "switch": "S", "spst": "S", "sw": "S", "push_button": "S",
            "pushbutton": "S", "button": "S", "tactile": "S",
            "nmos": "Q", "pmos": "Q", "npn": "Q", "pnp": "Q", "nfet": "Q",
            "pfet": "Q", "n_mosfet": "Q", "p_mosfet": "Q", "mosfet_n": "Q",
            "mosfet_p": "Q", "bjt_npn": "Q", "bjt_pnp": "Q",
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
                 "fill_color": -1, "border_color": SYMBOL_LINE_COLOR})
        if geom.lines:
            # width is Altium's line-size code (0 smallest .. 3 large), not
            # mils: 10 drew every plate and lead as a fat bar, so a
            # capacitor read as a blue blob.
            line_ops = [
                ";".join([f"x1={l['x1']}", f"y1={l['y1']}",
                          f"x2={l['x2']}", f"y2={l['y2']}",
                          f"width={SYMBOL_LINE_WIDTH}"])
                for l in geom.lines
            ]
            steps["lines"] = await bridge.send_command_async(
                "library.add_symbol_lines", {"lines": "~~".join(line_ops)})
        for i, poly in enumerate(geom.polygons):
            verts = ",".join(
                str(v) for pt in poly["points"] for v in pt)
            steps[f"poly{i}"] = await bridge.send_command_async(
                "library.add_symbol_polygon", {"vertices": verts})
        for i, arc in enumerate(geom.arcs):
            steps[f"arc{i}"] = await bridge.send_command_async(
                "library.add_symbol_arc",
                {"x_center": arc["x"], "y_center": arc["y"],
                 "radius": arc["radius"], "start_angle": arc["start"],
                 "end_angle": arc["end"], "width": 1})

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

        Call it before ``obj_modify`` / ``obj_batch_modify`` with scope
        "active_doc" on a symbol's pins, rectangles or parameters: those
        act on the current component, which reading one with
        ``lib_get_component_details`` does not change. (Scope
        "lib_component:NAME" selects the symbol for you.)

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
        """Add MANY pins to the current symbol in ONE call (there is no
        single-pin variant).

        Args:
            pins: List of pin dicts, each with:
                - designator, name (required)
                - x, y: where the pin meets the BODY (Altium's
                  Location), mils. The wire connects at the other end,
                  ``length`` away in the rotation's direction.
                - length: mils (default 200)
                - rotation: the way the pin points out of the body:
                  0 right, 90 up, 180 left, 270 down (default 0). A
                  left-side pin at the body edge x=0 is rotation 180 and
                  connects at x=-length; a top pin at y=0 is 90.
                - electrical_type: input, output, bidirectional, passive
                  (default), open_collector, open_emitter, power, hiz, io
                - hidden: hide the pin (default False)
                - owner_part_id: sub-part of a multi-part symbol; 0 shares
                  the pin across all parts (power pins).
                - symbol_outer_edge / symbol_inner_edge: IEEE decoration,
                  e.g. "dot" (active low) outside, "clock" inside, or any
                  TIeeeSymbol name.
                - show_name / show_designator: draw the pin's name or
                  number (a resistor shows neither).

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
        line_width: Optional[int] = None,
        is_solid: Optional[bool] = None,
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
            line_width: Border width, 0 (smallest) to 3 (large); omit
                for Altium's default.
            is_solid: Fill (True) or outline only (False); omit and a
                fill colour makes it solid.

        Returns:
            Dictionary confirming rectangle addition, with the style it
            now holds read back: ``color``, ``area_color``,
            ``line_width``, ``is_solid``.
        """
        if line_width is not None and line_width not in (0, 1, 2, 3):
            return {"success": False, "error": "line_width is 0 to 3"}
        params: dict[str, Any] = {
            "x1": _snap(int(x1)),
            "y1": _snap(int(y1)),
            "x2": _snap(int(x2)),
            "y2": _snap(int(y2)),
            "fill_color": fill_color,
            "border_color": border_color,
        }
        if line_width is not None:
            params["line_width"] = int(line_width)
        if is_solid is not None:
            params["is_solid"] = "true" if is_solid else "false"
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_rectangle", params)
        return result

    @mcp.tool()
    async def lib_add_symbol_text(
        texts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add MANY body-text items to the current symbol in ONE call.

        Free text such as polarity marks, pin-group headings and "NC".

        Args:
            texts: List of text dicts, each with:
                - text     (str, required; empty is refused)
                - x, y     (int, mils)
                - rotation (int, default 0), 0/90/180/270
                - font_size (int, default 10): Altium's font size, not
                  mils and not converted
                - font_name (str, default "Arial")
                - bold, italic (bool, default False)
                - owner_part_id (int, optional): as in ``lib_add_pins``,
                  1..part_count picks a sub-part, 0 shares it

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
                default 1) and an optional ``color`` (Altium colour
                integer).
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
            if line.get("color") is not None:
                fields.append(f"color={int(line['color'])}")
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

        A name the library already has (ignoring case) is refused with
        NAME_EXISTS: a second footprint of the same name would be
        unreachable, every lookup by name finding the first.

        Args:
            name: Footprint name
            description: Footprint description

        Returns:
            Dictionary with created footprint information
        """
        if not name.strip():
            return {"success": False, "error": "name is required"}
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
        """Create a COMPLETE standard footprint in ONE call from the land
        dimensions you give (pads, silk outline, courtyard). For a package
        drawing with tolerances prefer lib_create_footprint_from_package,
        which sizes lands by IPC-7351B.

        Args:
            name: Footprint name (e.g. "SOIC-8_3.9x4.9").
            family: "chip" | "sip" | "dual" (SOIC/SOP/SON/SOT/DIP; SOT-23
                is dual with 3 pins) | "header" (2-row, odd/even) | "tab"
                (SOT-223, DPAK, TO-220; give tab_w, tab_h) | "quad"
                (QFP/QFN) | "bga" (give rows, cols). hole > 0 makes any
                family through-hole.
            pin_count: Total pads (2 chip, even header, signal pins for
                tab, multiple of 4 quad; ignored for bga).
            rows, cols: BGA grid.
            exposed_pad: QFN/QFP centre pad size, mils (0 = none); it is
                pin_count+1.
            tab_w, tab_h: Tab pad size, mils; it is pin_count+1.
            skip: BGA balls to leave out, e.g. ["A1"].
            pitch: Pad pitch within a row, mils.
            pad_w, pad_h: Pad size, mils. pad_w is the X size (across the
                row for chip and dual, along it for sip, header and tab);
                for quad it runs along each row.
            row_span: Centre-to-centre between rows (dual, quad), mils.
            shape: Pad shape (default "roundrect", corner_radius in %).
            silk: Draw a TopOverlay body outline.
            courtyard: Courtyard margin, mils (0 = none; Mechanical1).
            body_w, body_h: Body size for the silk (default pad extent).
            hole: Drill, mils; > 0 gives through-hole pads, pin 1 square.
            description: Footprint description.

        Returns:
            Footprint name, pad and track counts, extent, step results.
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
        model_3d: str = "auto",
    ) -> dict[str, Any]:
        """Create a COMPLETE footprint in the open PcbLib from a package drawing.

        Give the package as the datasheet draws it, not a pad list: lands
        come from IPC-7351B for the family and density. Writes pads,
        exposed-pad paste windows, silkscreen with a pin 1 dot, assembly
        outline and .Designator, courtyard and height; origin at the
        centroid, all lengths in mm. The pads are then read back and any
        difference is a failure.

        DATASHEET DISCIPLINE: transcribe each dimension as min/nom/max
        from the manufacturer's drawing fetched in this conversation, and
        name it in ``source``; nominal-only dimensions give worse lands
        and a warning.

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
            name: Footprint name; default the package's, else generated
                (e.g. SOIC-8-P1.27N).
            density: L, N or M (default the package's, else N).
            description: Appended to the generated description.
            assembly_layer, courtyard_layer: Mechanical layers. Empty picks
                them by kind or name ("Assembly Top", "Courtyard Top");
                with neither, it refuses and lists the layers.
            drafting: Overrides, e.g. {"silk_width": 0.15,
                "ep_paste_coverage": 0.6, "mask_expansion": 0.05}.
            set_height: Write the height (package A max).
            model_3d: The 3D body. "auto" (default): a STEP model designed
                from the same drawing (body at its standoff and height,
                each lead or terminal at its land, exposed pad, balls),
                written with CadQuery or FreeCAD and linked to the
                footprint; when the family is not modelled or no writer is
                installed, an extruded body of the package outline and
                height instead, and ``notes`` says which and why. "step":
                the STEP model or nothing. "extruded": the extruded body.
                "none": no body. A STEP model is refused when a dimension
                it needs names no drawing.

        Returns:
            {ok, footprint, library_path, created, layers, readback, pads,
            courtyard_mm, warnings, notes, ipc, steps}. ``ok`` only when
            every step succeeded and the readback matched; a later
            failure leaves the footprint created but incomplete.
        """
        from ..bridge.exceptions import AltiumError
        from ..design.landpattern.emit import (
            LayerRoleError,
            altium_plan,
            model_from_package,
            resolve_altium_layers,
            verify_altium_readback,
        )
        from ..design.landpattern.emit import package_spec
        from ..design.landpattern.spec import SpecError

        if model_3d not in ("auto", "step", "extruded", "none"):
            return {"ok": False, "created": False,
                    "reason": "model_3d is auto, step, extruded or none"}
        try:
            model = model_from_package(package, density=density, name=name,
                                       description=description,
                                       drafting=drafting)
            spec = package_spec(package)
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
        names = {str(f.get("name")).upper() for f in rows if isinstance(f, dict)}
        if fp_name.upper() in names:
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
        body = await _footprint_3d_body(bridge, step, spec, model, fp_name,
                                        model_3d)
        notes.append(body["note"])
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
            "model_3d": body,
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
        corner_radius_length: Optional[float] = None,
    ) -> dict[str, Any]:
        """Add a pad to a footprint: the named one, or the current one.

        Args:
            designator: Pad designator (e.g., "1", "2")
            x, y: Pad position.
            x_size, y_size: Pad size (default 60 mil).
            hole_size: Drill size; 0 for SMD. A drilled pad is
                through-hole (MultiLayer); a hole-less one is SMD on
                ``layer``.
            shape: "round", "rectangular", "octagonal" or "roundrect".
                An oval is "round" with x_size != y_size.
            layer: SMD pad layer ("TopLayer"/"BottomLayer").
            rotation: Pad rotation in degrees
            corner_radius: roundrect corner radius, % (Altium's unit).
            corner_radius_length: or the radius as a length, converted to
                that % of half the shortest side (so resizing the pad
                later changes it).
            footprint_name: footprint to write into; empty uses the
                current one. A name is made current first, refusing if
                it cannot.
            units: "mil" (default) or "mm", for every length and
                coordinate here; mm values are kept exact.

        Returns:
            Dictionary confirming pad addition
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        bridge = get_bridge()
        params_x = length_in(units, 60) if x_size is None else x_size
        params_y = length_in(units, 60) if y_size is None else y_size
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
            "corner_radius": _pad_corner_pct(
                {"corner_radius": corner_radius,
                 "corner_radius_length": corner_radius_length,
                 "x_size": params_x, "y_size": params_y}, units),
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
    async def lib_add_footprint_via(
        x: float,
        y: float,
        size: float = 40,
        hole_size: float = 20,
        footprint_name: str = "",
        units: str = "mil",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Add a through-stack via to a footprint: the named one, or the current one.

        The via spans top to bottom. Its position is relative to the
        footprint origin, like a pad's. It goes into the focused PCB
        library; give ``library_path`` to refuse when that is not the one
        you mean.

        Args:
            x, y: Via centre, relative to the footprint origin.
            size: Via diameter (default 40 mil).
            hole_size: Drill diameter (default 20 mil). Must be below size.
            footprint_name: footprint to write into; empty uses the
                current one.
            units: "mil" (default) or "mm", for every length here.
            library_path: Full path to the .PcbLib. When given, the call
                refuses unless that library is the focused one.

        Returns:
            Dictionary confirming the via was added
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x": format_length(x),
            "y": format_length(y),
            "size": format_length(size),
            "hole_size": format_length(hole_size),
        }
        if footprint_name:
            params["footprint_name"] = footprint_name
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.add_footprint_via", _with_units(params, units))
        return result

    @mcp.tool()
    async def lib_add_footprint_vias(
        vias: list[dict[str, Any]],
        size: float = 40,
        hole_size: float = 20,
        footprint_name: str = "",
        units: str = "mil",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Add many through-stack vias to a footprint in ONE call.

        A via grid under an exposed pad is 9 to 50 vias: one call places
        them all, settles the footprint once, and replies with what was
        placed and what was not. Positions are relative to the footprint
        origin. Same rules as lib_add_footprint_via.

        Args:
            vias: list of ``{"x": .., "y": ..}``, each with an optional
                ``size`` and ``hole_size`` overriding the defaults below.
            size: Via diameter for vias that give none (default 40 mil).
            hole_size: Drill for vias that give none (default 20 mil).
            footprint_name: footprint to write into; empty uses the
                current one.
            units: "mil" (default) or "mm", for every length here.
            library_path: Full path to the .PcbLib. When given, the call
                refuses unless that library is the focused one.

        Returns:
            Dict with added, failed, bad_size (hole not inside the
            diameter), bad_number, total; a via refused here before
            sending is listed in ``refused`` with the reason.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, problems = _vias_payload(vias, size, hole_size)
        if not payload:
            return {"error": "No valid vias", "added": 0,
                    "total": len(vias or []), "refused": problems}
        params: dict[str, Any] = {"vias": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_vias", _with_units(params, units),
            timeout=60.0)
        if isinstance(result, dict) and problems:
            result["refused"] = problems
        return result

    @mcp.tool()
    async def lib_create_3d_model(
        footprint_name: str,
        solids: list[dict[str, Any]],
        source: str,
        output_dir: str = "",
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Build a STEP model from the datasheet drawing and link it to a
        footprint in the ACTIVE .PcbLib.

        The footprint's existing 3D bodies are removed first
        (``replace_existing``, reported as ``replaced_bodies``), so a
        rebuilt model does not sit beside the old box.

        For a part no package family covers (a connector, a LED with a
        lens, a header, a switch): read each feature off the mechanical
        drawing and give it as a primitive. lib_create_footprint_from_package
        already models the standard families; an extruded body
        (lib_add_footprint_bodies) is only the fallback.

        Args:
            footprint_name: the footprint to link the model to.
            solids: primitives in mm, origin at the footprint origin, Z up
                from the board (a top-side part sits on Z=0). Each has
                ``kind`` and ``role`` (colour: body, metal, ceramic, ball,
                white, lens, gold, pin1) and:
                box ``x0 y0 z0 x1 y1 z1``; cylinder ``r`` with ``cx cy
                z0 z1`` (vertical) or ``axis: "x"``, ``cy cz x0 x1``;
                sphere ``cx cy cz r``; prism ``profile`` [[u, z], ...]
                in the XZ plane swept from ``y0`` to ``y1`` (``axis``
                "y"), or in the YZ plane swept from ``x0`` to ``x1``
                (``axis`` "x").
            source: the drawing the dimensions come from (document,
                revision, page or figure). Required: a model is not built
                from guesses.
            output_dir: folder for the .step; empty puts it in a ``3D``
                folder beside the active library.
            replace_existing: remove the footprint's 3D bodies before
                linking (default True); False adds this one beside them.

        Returns:
            ``ok``, ``path``, ``writer``, ``height_mm``, ``linked`` (the
            link reply) and ``replaced_bodies``, or ``ok: false`` with
            ``problems`` or ``reason``.
        """
        from ..design import models3d

        plan, problems = models3d.plan_from_solids(solids, source)
        if plan is None:
            return {"ok": False, "problems": problems}
        bridge = get_bridge()
        folder = Path(output_dir) if output_dir else None
        if folder is None:
            active = await bridge.send_command_async(
                "application.get_active_document", {}) or {}
            doc = str(active.get("file_path") or "") if isinstance(active, dict) else ""
            folder = (Path(doc).parent / "3D" if doc.lower().endswith(".pcblib")
                      else get_config().workspace_dir / "models")
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                       for ch in footprint_name) or "model"
        wrote = await asyncio.to_thread(
            models3d.write_step, plan, folder / f"{safe}.step",
            name=footprint_name or safe)
        if not wrote["ok"]:
            return {"ok": False, "reason": wrote.get("reason"),
                    "tried": wrote.get("tried")}
        replaced = (await _remove_old_bodies(bridge, footprint_name)
                    if replace_existing else None)
        linked = await bridge.send_command_async(
            "library.link_3d_model",
            {"component_name": footprint_name, "model_path": wrote["path"],
             "offset_x": 0, "offset_y": 0, "offset_z": 0,
             "rotation_x": 0, "rotation_y": 0, "rotation_z": 0})
        ok = isinstance(linked, dict) and linked.get("success") is not False
        return {"ok": ok, "path": wrote["path"], "writer": wrote["writer"],
                "height_mm": plan.height, "solids": len(plan.solids),
                "source": source, "linked": linked,
                "replaced_bodies": replaced}

    @mcp.tool()
    async def lib_add_footprint_bodies(
        bodies: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add extruded 3D bodies to a PcbLib footprint in ONE call.

        THE FALLBACK BODY. A part's 3D body should be its STEP model:
        the vendor's, linked with lib_link_3d_model, or one generated from
        the package description (lib_create_footprint_from_package does
        that first). Use this for a part with neither, or for a clearance
        or keep-in volume: an outline lifted from standoff_height to
        overall_height.

        Args:
            bodies: list of ``{"points": [[x, y], ...], "overall_height":
                ..}``, the outline relative to the footprint origin, with
                optional ``standoff_height`` (default 0), ``layer``
                (default TopLayer, the side the body projects from; a
                mechanical layer such as Mechanical13 is common),
                ``identifier`` (a name to find the body again by),
                ``color`` (Altium TColor integer) and ``opacity`` (0-1).
            footprint_name: footprint to write into; empty uses the
                current one.
            units: "mil" (default) or "mm", for outline and heights.

        Returns:
            Dict with added, failed, bad_points, bad_height,
            unknown_layers, total and ``bodies``, each read back
            (outline, heights, identifier, layer) with which optional
            properties were applied.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, problems = _bodies_payload(bodies)
        if not payload:
            return {"error": "No valid bodies", "added": 0,
                    "total": len(bodies or []), "refused": problems}
        params: dict[str, Any] = {"bodies": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_bodies", _with_units(params, units))
        if isinstance(result, dict) and problems:
            result["refused"] = problems
        return result

    @mcp.tool()
    async def lib_set_via_properties(
        x: float,
        y: float,
        library_path: str = "",
        footprint_name: str = "",
        units: str = "mil",
        hole_size: Optional[float] = None,
        size: Optional[float] = None,
        dia_mid: Optional[float] = None,
        dia_bottom: Optional[float] = None,
        stack_mode: Optional[str] = None,
        tenting_top: Optional[bool] = None,
        tenting_bottom: Optional[bool] = None,
        mask_expansion: Optional[float] = None,
        mask_bottom_expansion: Optional[float] = None,
        mask_separate: Optional[bool] = None,
        mask_manual: Optional[bool] = None,
        mask_from_hole_edge: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Modify one via inside a footprint of an open PCB library.

        The via is found by its position relative to the footprint origin
        (within 1 mil, whatever ``units``). No via there is an error, and so
        is more than one: nothing is changed. Only the fields given are
        written, and each is read back: one that did not take sets
        ``all_writes_ran`` false. A middle or bottom diameter raises the via
        to a local-stack via in the same call. The mask expansions are set
        through the pad cache.

        Tenting only persists while the via's mask expansion is manual
        (measured on Altium 26.11). A via whose mask follows the design
        rules is switched to a manual expansion at the value it already
        has before tenting is written, and ``mask_made_manual`` says so.

        Args:
            x, y: The via centre, relative to the footprint origin.
            library_path: Full path to the open .PcbLib; it must be the
                focused library, or the call refuses. Empty: the focused
                library.
            footprint_name: footprint to change; empty uses the current one.
            units: "mil" (default) or "mm".
            hole_size: Drill diameter.
            size: Top-layer diameter.
            dia_mid: Middle-layer diameter (makes a local-stack via).
            dia_bottom: Bottom-layer diameter (makes a local-stack via).
            stack_mode: "simple" (one diameter on every layer) or
                "top-middle-bottom", written before the sizes.
            tenting_top, tenting_bottom: Solder mask tenting on each side.
            mask_expansion: Top solder mask expansion (manual).
            mask_bottom_expansion: Bottom solder mask expansion (manual).
            mask_separate: Separate (True) or linked (False) mask expansions.
            mask_manual: True keeps the mask as written; False hands it
                back to the design rule (not with a mask value).
            mask_from_hole_edge: Measure the mask from the drill edge.

        Returns:
            Dictionary with ``all_writes_ran``, the via's drill and size,
            its tenting and mask expansions read back, ``mask_source``
            (manual or rule), ``mask_from_hole_edge``, ``stack_mode``,
            ``mid_unverified`` (a middle diameter that reads back 0 on
            this build, so the write could not be checked) and
            ``mask_made_manual``. A field this build cannot read comes back
            null.
        """
        if stack_mode is not None and stack_mode not in ("simple",
                                                         "top-middle-bottom"):
            return {"success": False,
                    "error": "stack_mode must be simple or top-middle-bottom"}
        if stack_mode == "simple" and (dia_mid is not None
                                       or dia_bottom is not None):
            return {"success": False,
                    "error": "a simple via has one size; dia_mid and "
                             "dia_bottom need top-middle-bottom"}
        if mask_manual is False and (mask_expansion is not None
                                     or mask_bottom_expansion is not None):
            return {"success": False,
                    "error": "mask_manual=False hands the mask to the rule; "
                             "a mask value with it would be ignored"}
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        params: dict[str, Any] = {
            "x": format_length(x),
            "y": format_length(y),
        }
        if library_path:
            params["library_path"] = library_path
        if footprint_name:
            params["footprint_name"] = footprint_name
        if stack_mode is not None:
            params["stack_mode"] = stack_mode
        for key, value in (("hole_size", hole_size), ("size", size),
                           ("dia_mid", dia_mid), ("dia_bottom", dia_bottom)):
            if value is not None:
                params[key] = format_length(value)
        for key, value in (("tenting_top", tenting_top),
                           ("tenting_bottom", tenting_bottom),
                           ("mask_separate", mask_separate),
                           ("mask_manual", mask_manual),
                           ("mask_from_hole_edge", mask_from_hole_edge)):
            if value is not None:
                params[key] = "true" if value else "false"
        for key, value in (("mask_expansion", mask_expansion),
                           ("mask_bottom_expansion", mask_bottom_expansion)):
            if value is not None:
                params[key] = format_length(value)
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_via_properties", _with_units(params, units))

    @mcp.tool()
    async def lib_count_layer_objects(library_path: str = "") -> dict[str, Any]:
        """Count what is on each layer across every footprint of a PCB library.

        Read only. Answers "is this layer empty in every footprint?" before a
        layer is disabled or a layer scheme is tidied, and names the
        footprints that use each layer, so a layer that is not empty says
        where to look. Covers tracks, arcs, text, regions, fills, 3D bodies,
        pads, vias, polygons, dimensions and coordinate markers. Mechanical
        layers past 16 are named by number (``Mechanical17``...).

        Args:
            library_path: Full path to the open .PcbLib; it must be the
                focused library, or the call refuses. Empty: the focused
                library.

        Returns:
            Dictionary with ``layers`` (``{layer, count, footprints}`` for
            each layer that holds anything), ``footprints`` and ``objects``
        """
        bridge = get_bridge()
        params = {"library_path": library_path} if library_path else {}
        return await bridge.send_command_async(
            "library.count_layer_objects", params, timeout=60.0)

    @mcp.tool()
    async def lib_get_library_origin(library_path: str) -> dict[str, Any]:
        """Report where a PCB library's origin marker sits.

        Read from the library's backing board, the same one footprint
        coordinates are written against. The library must already be open
        and focused in Altium.

        Args:
            library_path: Full path to the open .PcbLib. It must be the
                focused library, or the call refuses.

        Returns:
            Dictionary with origin_x_mil, origin_y_mil and library_path
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_library_origin", {"library_path": library_path})

    @mcp.tool()
    async def lib_add_footprint_pads(
        pads: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add MANY pads to the current footprint in ONE call. PREFER THIS
        over looping lib_add_footprint_pad. Coordinates are not snapped.

        Args:
            pads: List of pad dicts, each with:
                - designator (required), e.g. "1", "A1"
                - x, y: pad centre
                - x_size, y_size (default 60 mil)
                - hole_size (default 0 = SMD; drilled pads are MultiLayer)
                - shape: round, rectangular (default), octagonal or
                  roundrect; oval is round with unequal sizes
                - corner_radius: roundrect corner in % (default 25), or
                  corner_radius_length as a length
                - rotation: degrees
                - layer: SMD side, "TopLayer" (default) or "BottomLayer"
                - optional: hole_length (slot), hole_rotation (the
                  slot's turn from the pad's X axis; left out, the slot
                  runs along the pad's longer side),
                  plated (false = NPTH, which may have no designator),
                  paste_expansion, mask_expansion (minus the larger side
                  closes the paste); refused writes show in
                  ``property_failures``
            footprint_name: Footprint to write into; empty uses the
                current one, a name makes it current or refuses.
            units: "mil" (default) or "mm" for every pad, kept exact.

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

        PREFER THIS over looping `lib_add_footprint_track`: one call,
        one undo step and one save for a whole outline.

        Args:
            tracks: ``{x1, y1, x2, y2, width (default 10), layer
                (default "TopOverlay")}`` each; any layer name, e.g.
                "Mechanical13" for an assembly outline.

        ``footprint_name`` picks the footprint; empty uses the current
        one, and a name that cannot be made current is refused.
        ``units`` is "mil" (default) or "mm", kept exact.

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

        For example, paste windows over an exposed pad (on TopPaste, with
        the pad's own paste closed by a negative paste_expansion in
        lib_add_footprint_pads), or a filled shape on any layer.

        Args:
            regions: list of ``{"layer": ..., "points": [[x, y], ...]}``,
                three or more points each, relative to the footprint
                origin. The layer is REQUIRED: there is no default,
                because a solid shape defaulted onto copper is a short.
                A point ``[x, y, sweep]`` makes the edge to the next point
                a true arc of ``sweep`` degrees, counterclockwise when
                positive (a slot's round end is 180 or -180); other edges
                are straight.
            footprint_name: footprint to write into; empty uses the
                current one, and a name that cannot be made current is
                refused.
            units: "mil" (default) or "mm", for every point, kept exact.

        Returns:
            Dict with added, failed, bad_points, unknown_layers, total,
            and arc_edges: the arc edges the created regions read back
            as holding. A region refused here before sending is listed
            in ``refused`` with the reason.
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

        Args:
            text: The string to place. Required.
            x, y: Position relative to the footprint's origin.
            size: Text height (default 50 mil).
            width: Stroke width (default 8 mil); scale with ``size``.
            rotation: Degrees.
            layer: Layer name (``TopOverlay``, ``BottomOverlay``,
                ``Mechanical1`` ...).
            use_ttfont: TrueType instead of the stroke font.
            mirror: Mirror the text; set it for a BOTTOM layer.
            library_path: .PcbLib to focus first; default the active one.
            component_name: Footprint to switch to first; default the
                current one.
            units: "mil" (default) or "mm", kept exact.

        Returns:
            ``success``, ``footprint``, ``text``, ``layer``, ``x``, ``y``.
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

        Runs Extract Sources and reports which files appeared (in a folder
        named after the IntLib, or beside it). Pass the reported paths as
        ``library_path`` to the other library tools.

        Args:
            intlib_path: Absolute path to the .IntLib file.

        Returns:
            Dict with ``intlib_path``, ``extract_dir``, ``sch_lib_path``,
            ``sch_lib_found``, ``pcb_lib_path``, ``pcb_lib_found``. A
            ``_found`` flag of False means that source did not appear (the
            IntLib lacks it, the folder was not writable, or this build
            names the command differently).
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

        Each convention is the library's most common one, so any house
        style works; ``policy`` pins one instead, e.g.
        ``{"silk_layer": "Top Overlay", "courtyard": true}``. Each finding
        gives footprint, dimension, expected and actual. Designators must
        sit on the average pad centre, within
        ``policy={"designator_center_tol": <mils>}``. One library pass.

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
                saved, why = await _save_library(bridge, resolved)
                if not saved:
                    res.setdefault("errors", []).append(f"save failed: {why}")
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

        The conventions are inferred from this library (see
        ``lib_audit_footprint_policies``); ``policy`` overrides them.
        "Centred" means on the average pad centre. A dry run by default.
        Applying repeats apply, save, reload until nothing is left; a few
        designators may not persist and come back in ``unrepairable``
        with ``converged: false``. ``applied`` counts only writes
        confirmed by read-back.

        Args:
            library_path: .PcbLib to fix; defaults to the focused library.
            dry_run: report the plan without writing (default True).
            fix_layer: move designators that are not on the convention
                layer onto it.
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
                saved, why = await _save_library(bridge, resolved_path)
            except Exception as e:
                saved, why = False, str(e)
            if not saved:
                failed.append({"footprint": "*", "error": f"save failed: {why}"})
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
        """Set every footprint's height in the active PcbLib from its
        tallest 3D body, for the height-clearance DRC (a height of 0
        makes that rule pass silently).

        Args:
            mode: ``raise`` (default) only increases a height, so a
                hand-set one survives. ``match`` also lowers it to the
                model; use it when heights are too tall. For one
                footprint, or one with no model, use
                ``lib_set_footprint_height``.

        A footprint with no 3D body is never set to 0; it is listed in
        ``without_model``. The library is not saved; review ``items``
        and save in Altium.

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

        With replace=True (default) existing footprint models are removed
        first; False adds another.

        Args:
            component_name: Name of the schematic component, made current
                first. Empty uses the current component.
            footprint_name: Name of the footprint to link
            footprint_library: NOT APPLIED (kept for old callers). The
                footprint is bound by name with an empty location, since
                a .PcbLib path there wedges AD26: install the library and
                keep footprint names unambiguous.
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
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Link a 3D STEP model to a footprint in the ACTIVE .PcbLib, as a
        component body (drives the 3D view and height DRC).

        The footprint's existing 3D bodies are removed first
        (``replace_existing``, reported as ``replaced_bodies``); a link
        used to add the new body beside the old one.

        Args:
            component_name: Footprint name (empty: the current footprint).
            model_path: Existing .step/.stp file.
            offset_x: X offset in mils from the footprint's origin (the
                body is moved onto that origin first).
            offset_y: Y offset in mils.
            offset_z: Standoff height in mils (lifts the body).
            rotation_x: NOT APPLIED; rotate in the library editor.
            rotation_y: NOT APPLIED.
            rotation_z: NOT APPLIED (no scriptable rotation on the body;
                assigning one halted the script).
            replace_existing: remove the footprint's 3D bodies before
                linking (default True); False adds this one beside them,
                for a part built from several models.

        Returns:
            ``success``, ``footprint``, ``model``,
            ``footprint_origin_mils``, ``moved_to_footprint_origin``
            (false: the body may sit away from the footprint) and
            ``applied`` {standoff_height, rotation_z (always false),
            offset_xy}; false means not written, including a zero left at
            its default.
        """
        bridge = get_bridge()
        replaced = (await _remove_old_bodies(bridge, component_name)
                    if replace_existing else None)
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
        if isinstance(result, dict) and replaced is not None:
            result["replaced_bodies"] = replaced
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
        """List the components in a library: name, alias_name, part_count,
        description, fast. ``part_count`` here can read one high; use
        lib_get_component_details when it matters.

        Args:
            library_path: Library path (default: the active library).
            with_parameters: Add each component's parameters (slow on
                large libraries; for one symbol use
                lib_get_component_details).
            with_designator: Add each default designator ("U?"); also
                loads every symbol, so slow on large libraries.

        Returns:
            ``count`` and ``components`` [{index, name, alias_name,
            part_count, description, parameters?, designator?}]. Pass
            ``index`` as component_index to the details, delete and
            rename tools when a name has characters you cannot type.
            The list is the SAVED file. When the library is open, two more
            keys compare it with the symbols in memory:
            ``unsaved_symbols`` (in the open library, not yet in the file,
            for example renamed or new) and
            ``saved_names_not_in_open_library`` (in the file, gone from the
            open library). Name a symbol as the open library has it.
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

        Searched: every .SchLib open (in a project or standalone) and every
        installed DbLib, case-insensitive substring. NOT searched: IntLibs,
        query/SVN libraries, workspace components, closed SchLibs, so 0
        hits is not "no such part"; ``not_searched`` and ``coverage_note``
        say what was skipped. It reads files ON DISK: a symbol created and
        not yet saved is not found (use lib_get_component_details).
        DbLib rows match on every column (MPN, value) and are marked
        ``source: "dblib"``; lib_dblib_search narrows to a table.

        DATASHEET DISCIPLINE: fetch a part's datasheet before recommending
        it; do not go on symbol metadata alone.

        Args:
            query: Substring to match.
            search_type: "all" (name, alias, description; default),
                "name", "description", or "parameters" (slow).
            library_path: Restrict to one .SchLib or .DbLib.
            limit: Cap on matches, SchLib and DbLib together (default 100).
            include_dblibs: Also search installed DbLibs (default True;
                slower on large databases).

        Returns:
            ``query``, ``search_type``, ``count``, ``limit``,
            ``truncated``, ``results`` [{name, alias_name, description,
            library_path, part_count, source}; DbLib rows add table,
            key_field, symbol_ref, symbol_library, footprints,
            matched_field], ``_datasheet_guidance``,
            ``libraries_searched``, ``not_searched`` [{library_path,
            library_type, reason}], ``dblib_searches``, ``coverage_note``.
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

        Call it first on an unfamiliar DbLib: per table it names the key
        column and the symbol and footprint columns that
        ``lib_dblib_get_record`` and ``sch_place_dblib_component`` take.
        The password is never returned (``redacted``, ``has_password``).
        ``key_field_source`` says how the key was found; ``first_column``
        is a guess.

        Args:
            library_path: Full path of the .DbLib (or .SVNDbLib);
                lib_get_installed_libraries lists installed ones.
            with_fields: Connect read-only to list each table's columns
                (default True). False reads the file only.

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

        Case-insensitive substring match over every row of every enabled
        table (lib_search folds these in already; this narrows to a table
        or columns). The query never reaches the database as SQL: rows
        are read and matched inside Altium, bounded by ``max_rows``
        (``scan_capped`` says the bound was hit).

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
            "query", "count", "limit", "truncated", "rows_scanned",
            "scan_capped", "libraries" (per library: searched, an error
            code, tables searched and failed, unknown_fields), and
            "results" (library_path, table, key, key_field, symbol_ref,
            symbol_library, footprints, description, matched_field,
            matched_value). No connection string is ever returned.
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

        Altium builds the part from the row (symbol, footprints,
        parameters and database link) and drops it at (x, y), with no
        cursor or click. The DbLib must be installed (lib_install_library)
        or in the project, or the call is refused. ``database_linked``
        says whether the placed part kept its link. For a whole sheet use
        sch_draw_template instead.

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
            "placed", "library_path", "table", "key", "lib_reference",
            "database_table", "database_linked", "x", "y", "rotation",
            "part_id" (read back), "part_count", "part_id_applied",
            "designator".
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
        """Everything about one library symbol in one call: metadata, pins,
        parameters, models, and the style (font_id, color, is_hidden, x,
        y, orientation, justification) of its designator, comment and
        parameters. obj_get_font_spec expands a font_id. Naming
        library_path opens (focuses) that library; nothing is saved.

        DATASHEET DISCIPLINE: pins, parameters and MPN here are hints;
        fetch the datasheet and cite it before stating a pin or rating.

        Args:
            component_name: LibRef in the .SchLib.
            library_path: .SchLib path (default: the focused library).
            component_index: ``index`` from lib_get_components, for a name
                with characters you cannot reproduce. Give exactly one of
                name or index.

        Returns:
            name, library_path, description, alias_name, part_count (the
            file reader's), part_count_live (the open symbol's),
            parts_with_pins (highest part that has pins) and a
            part_count_note when they disagree, pin_count;
            ``designator`` and ``comment`` (text plus style);
            ``pins`` [{designator, name, electrical_type, x, y,
            orientation, hidden, label_hidden}]; ``parameters`` (name ->
            value); ``parameter_styles``; ``models`` [{model_name,
            model_type, is_current, datafile_links}];
            ``_datasheet_guidance``.
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
        check_standard_symbols: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Audit label styles across every symbol of a library in one call.

        Reports each symbol's designator style; comment, parameter styles
        and pins are opt-in. With an ``expect_designator_*`` filter only
        the symbols that do NOT match come back.
        ``check_standard_symbols`` adds ``standard_symbol_warnings``:
        amplifiers drawn as a rectangle instead of a triangle (warns only).

        Args:
            library_path: .SchLib (default: the focused one).
            with_comment: Add the comment style.
            with_parameters: Add parameter styles (slower).
            with_pins: Add pins, ``body`` (rectangle and polygon counts)
                and description.
            check_standard_symbols: Flag rectangular amplifiers (implies
                with_pins).
            expect_designator_font_id: Keep only symbols whose designator
                font differs.
            expect_designator_color: Keep only symbols whose designator
                colour differs (BGR int, e.g. 8388608 for navy).
            limit: Cap on symbols returned (default 5000).
            timeout: Poll timeout in seconds.
            refresh: SAVE the library and read it back in first. Symbols
                are listed from the saved file, so ones created or changed
                since the last save are otherwise missing or stale.

        Returns:
            library_path, count, mismatch_count, limit, truncated,
            filter_applied, refreshed, ``components`` [{name, designator,
            mismatched, comment?, pins?, parameter_styles?}].
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if library_path:
            params["library_path"] = library_path
        if with_comment:
            params["with_comment"] = "true"
        if with_parameters:
            params["with_parameters"] = "true"
        if with_pins or check_standard_symbols:
            params["with_pins"] = "true"
        if expect_designator_font_id is not None:
            params["expect_designator_font_id"] = str(expect_designator_font_id)
        if expect_designator_color is not None:
            params["expect_designator_color"] = str(expect_designator_color)
        if refresh:
            params["refresh"] = "true"
        result = await bridge.send_command_async(
            "library.audit_styles", params, timeout=timeout,
        )
        result = result or {}
        if check_standard_symbols and isinstance(result, dict):
            result["standard_symbol_warnings"] = _standard_symbol_warnings(
                result.get("components") or [])
        return result

    @mcp.tool()
    async def lib_set_label_formats(
        ops: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        only_mismatched: bool = True,
        limit: int = 5000,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Set label styles on SchLib symbols, several targets in ONE call
        (no single-target variant), across one symbol or the whole library.

        Each op writes only the fields it gives (at least one):
            {"target": "designator" | "comment" | "parameter:<Name>",
             "font_id", "color" (BGR int), "is_hidden", "orientation"
             (0/90/180/270), "justification", "x", "y" (mils, symbol
             frame), "text" (e.g. "=Partnumber" on the comment)}
        Resolve font_id with ``obj_get_font_id`` / ``obj_get_font_spec``.

        Args:
            ops: Non-empty list of ops; targets cannot contain ';' or
                '~~'.
            component_name: One symbol only; omit for the whole library.
            library_path: .SchLib (default: the focused one).
            only_mismatched: Skip labels already matching (default True).
            limit: Cap on components in a library walk.
            timeout: Poll timeout override.

        Returns:
            library_path, scope ("single"|"bulk"), total, limit,
            truncated, and ``ops`` [{target, modified, already_compliant,
            missing_target, failed}].
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
            unknown = sorted(set(op) - _LABEL_OP_KEYS)
            if unknown:
                # A key this does not encode would be dropped on the way to
                # Altium while the reply still said "modified". That is how
                # designator x/y moves reported success and never moved.
                return {"ok": False, "reason":
                        f"ops[{i}] has keys this tool does not set: "
                        f"{unknown}. Known keys: "
                        f"{sorted(_LABEL_OP_KEYS)}"}
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
            for axis in ("x", "y"):
                if op.get(axis) is not None:
                    parts.append(f"{axis}={int(round(float(op[axis])))}")
                    style_set = True
            if op.get("text") is not None:
                text = str(op["text"])
                if not text or ";" in text or "~~" in text:
                    return {"ok": False, "reason":
                            f"ops[{i}].text must be non-empty and contain "
                            "neither ';' nor '~~'"}
                parts.append(f"text={text}")
                style_set = True
            if not style_set:
                return {"ok": False, "reason":
                        f"ops[{i}] must set at least one of font_id / "
                        "color / is_hidden / orientation / justification "
                        "/ x / y / text"}
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
        """Name, enable and set the kind of a library's mechanical layers.

        Takes the library by PATH (unlike the pcb_* layer tools, which act
        on whatever is focused) and refuses unless that library is the one
        focused. Every change is read back and failures are reported per
        layer. A Top/Bottom kind ("Courtyard Top") belongs to a layer PAIR,
        so give the partner kind to another layer in the same call. Use
        ``lib_run_across`` to apply one scheme to many libraries.

        Args:
            layers: [{layer (e.g. "Mechanical13"), name?, enabled?,
                kind?}].
            library_path: Library to edit (default: the focused one; only
                safe for a single library).
            tidy_pairs: Also remove layer pairs whose two layers are not a
                matching Top/Bottom kind pair (default off: it removes
                pairs).
            timeout: Seconds.

        Returns:
            ``library``, ``layers`` [{layer, changed, problem}],
            ``changed``, ``failed``, ``kinds_displaced``,
            ``pairs_removed``, ``pairs_tidied``, ``pairs_scanned_to``.
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
        """Run one library command against several libraries in one call
        (the loop runs inside Altium; each library is still opened).

        Works for commands that accept a ``library_path`` (most reads and
        batch edits). Footprint editing commands (``create_footprint``,
        ``add_footprint_pad``, ``link_3d_model``...) act on the open
        library and cannot be swept.

        Failure is per library: one that will not open does not stop the
        rest, and each is reported separately.

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

        Each assignment updates or creates one parameter. Three names set
        properties instead: ``"Description"``, ``"Designator"`` (the
        default, e.g. ``"U?"``) and ``"Comment"`` (e.g. ``"=Partnumber"``).
        A created parameter is hidden; ``lib_set_label_formats`` shows,
        hides or moves it.

        Args:
            assignments: List of dicts with keys:
                - component_name: Name of the component in the library
                - param_name: Parameter name (e.g., "Partnumber", "Manufacturer"),
                  or the special "Description" / "Designator" / "Comment" keys
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
        color: Optional[int] = None,
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
            color: line colour (Altium colour integer); omit for the
                default.

        Returns:
            Dictionary confirming arc addition, with ``color`` and
            ``line_width`` read back
        """
        params: dict[str, Any] = {
            "x_center": _snap_grid(x_center, grid),
            "y_center": _snap_grid(y_center, grid),
            "radius": _snap_grid(radius, grid),
            "start_angle": start_angle,
            "end_angle": end_angle,
            "width": width,
        }
        if color is not None:
            params["color"] = int(color)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_arc", params)
        return result

    @mcp.tool()
    async def lib_add_symbol_polygon(
        vertices: str,
        grid: int = 25,
        fill_color: Optional[int] = None,
        owner_part_id: Optional[int] = None,
        color: Optional[int] = None,
        line_width: Optional[int] = None,
        is_solid: Optional[bool] = None,
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
            fill_color: omit for a solid glyph in the line colour (a diode
                triangle); a colour such as 8454143, Altium's block yellow,
                makes it a filled BODY (an amplifier triangle); -1 draws
                an outline only.
            owner_part_id: the part of a multi-part symbol to draw on;
                omit for the current part.
            color: border colour (Altium colour integer).
            line_width: border width, 0 (smallest) to 3 (large).
            is_solid: fill (True) or outline only (False), over what
                ``fill_color`` implies.

        Returns:
            Dictionary confirming polygon addition with vertex count, and
            the style it now holds read back: ``color``, ``area_color``,
            ``line_width``, ``is_solid``
        """
        if line_width is not None and line_width not in (0, 1, 2, 3):
            return {"success": False, "error": "line_width is 0 to 3"}
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
        params: dict[str, Any] = {"vertices": vertices}
        if fill_color is not None:
            params["fill_color"] = int(fill_color)
        if owner_part_id is not None:
            params["owner_part_id"] = int(owner_part_id)
        if color is not None:
            params["color"] = int(color)
        if line_width is not None:
            params["line_width"] = int(line_width)
        if is_solid is not None:
            params["is_solid"] = "true" if is_solid else "false"
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_polygon", params,
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
        library_path: str = "",
    ) -> dict[str, Any]:
        """Get all pins of a library component.

        Name the component: without ``component_name`` this reads
        whatever the SchLib editor has selected. Pin names and types in
        a symbol can be wrong; check them against the datasheet before
        relying on them.

        Args:
            component_name: library reference of the symbol to read.
                Empty falls back to the editor's current component.
            output_path: write the full pin array here as JSON and
                return a summary instead.
            inline_limit: above this many pins the array goes to a file
                (``pins_path``); 0 always returns inline.
            library_path: the .SchLib to read; it is brought to the front
                (opened if need be) first, so a sheet in front does not
                stop the read. Empty reads the library in front.

        Returns:
            "count", "component", and "pins", each with designator,
            name, electrical_type, x, y, orientation, hidden,
            ``owner_part_id`` and ``display_mode`` (0 normal, 1+ an
            alternate mode, not a duplicate). Spilled: "pins_path" and
            "summary" instead of "pins". Plus `_datasheet_guidance`.
        """
        bridge = get_bridge()
        if library_path:
            problem = await _open_document(bridge, library_path)
            if problem:
                return {"success": False, "error": problem}
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

        A KiCad 6+ symbol: a body rectangle round the pins and every pin
        with number, name, type and position, in mm. Naming a component
        leaves it current in the library (``current_component``), so a
        later call without a name, such as ``lib_add_pins``, acts on it.

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

        result = {
            "success": True,
            "output_path": str(out),
            "footprint": name,
            "pad_count": len(norm),
        }
        custom = [p["name"] for p in norm
                  if str(p["shape"]).strip().lower() == "custom"]
        if custom:
            result["custom_pads"] = custom
            result["warning"] = (
                "Custom-shaped pads were written as rectangles of their "
                "bounding box, not their outline: " + ", ".join(custom))
        return result

    @mcp.tool()
    async def lib_copy_component(
        source_name: str,
        new_name: Optional[str] = None,
        source_library: Optional[str] = None,
        dest_library: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Copy a component WITHIN or BETWEEN schematic libraries.

        Copies pins, graphics and parameters. The destination ends
        focused with the copy selected; save with ``app_save_all``.

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

        Bulk lib_copy_component: copies each match whole, then removes it
        from the source unless ``delete_from_source`` is False. One already
        in the destination is skipped unless ``overwrite``. A failed copy
        is never deleted from the source.

        Args:
            source_schlib: source .SchLib path.
            dest_schlib: destination .SchLib path.
            names: explicit list of component LibReferences to move.
            name_regex: regex matched against source component names when names
                is not given.
            delete_from_source: remove moved components from source (default True).
            overwrite: replace a same-named component already in dest.
            dry_run: report the selection without moving anything. Use it
                with name_regex, which is a ``search``, not a full match
                (``R`` selects every name containing an R).

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N}, or
            in dry-run {"dry_run", "resolved", "count", ...}.
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
    async def lib_rotate_footprint(
        library_path: str,
        footprint: str,
        angle: int,
        dry_run: bool = False,
        save: bool = True,
        show: bool = False,
    ) -> dict[str, Any]:
        """Turn a footprint in a .PcbLib about its own origin by 90, 180 or
        270 degrees, counter-clockwise (-90 is 270), every primitive with it:
        pads (position and pad rotation), tracks, arcs, fills, regions, vias,
        texts (.Designator / .Comment included) and 3D bodies, whose model
        turns with the body as it does under Altium's own Rotate Selection
        (the model's own rotation fields are never written). The library is
        opened by path; it need not be focused, and the user's selection is
        not touched.

        Every pad is read back: its centre must land where the turn puts it
        and its rotation must move by the angle. Any that did not are
        listed in ``pad_mismatches`` and counted in ``mismatches``; the
        library is then NOT saved, so it can be inspected (or the footprint
        turned back by the opposite angle). Polygons and dimensions in a
        footprint are counted in ``not_turned``. Look at one turned
        footprint in 3D before a batch (``show``), since the model's
        orientation cannot be read back.

        Args:
            library_path: the .PcbLib.
            footprint: footprint name (case-insensitive).
            angle: 90, 180, 270 or -90.
            dry_run: count what would be turned, change nothing.
            save: save the library afterwards when nothing mismatched.
            show: make the footprint the library's current one afterwards,
                to look at it (its 3D model shows only there). Changes what
                the library editor has on screen.

        Returns:
            ``kinds`` (count per primitive kind), ``turned``,
            ``not_turned``, ``mismatches``, ``pad_mismatches``,
            ``bodies_with_models`` and ``saved``.
        """
        if angle not in (90, 180, 270, -90):
            return {"success": False,
                    "error": "angle must be 90, 180, 270 or -90 (counter-clockwise)"}
        bridge = get_bridge()
        if not dry_run:
            # A library Altium has not opened as a document is loaded off
            # screen by the turn, where nothing can save it: the edit would
            # be lost. Open it first, the way the template tools open a sheet.
            opened = await _open_document(bridge, library_path)
            if opened:
                return {"success": False, "saved": False, "error": opened}
        result = await bridge.send_command_async(
            "library.rotate_footprint",
            {"library_path": library_path, "footprint": footprint,
             "angle": str(angle), "dry_run": "true" if dry_run else "false",
             "show": "true" if show else "false"},
            timeout=60.0,
        )
        if not isinstance(result, dict) or dry_run:
            return result
        out = dict(result)
        out["saved"] = False
        if not save:
            out["next_step"] = "Not saved (save=False). app_save_document on the library writes it."
        elif out.get("mismatches"):
            out["next_step"] = ("Not saved: something did not turn as expected. Inspect it, "
                                "or turn the footprint back with the opposite angle.")
        else:
            out["saved"], reason = await _save_library(bridge, library_path)
            if not out["saved"]:
                out["save_problem"] = reason
        return out

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

        Copies each footprint whole, then removes it from the source
        unless delete_from_source is False; with lib_move_components it
        splits a library. Give names or name_regex. A footprint already in
        dest is skipped unless overwrite=True.

        Args:
            source_pcblib: source .PcbLib path.
            dest_pcblib: destination .PcbLib path.
            names: explicit list of footprint names to move.
            name_regex: regex matched against source footprint names when names
                is not given.
            delete_from_source: remove moved footprints from source (default True).
            overwrite: replace a same-named footprint already in dest.
            dry_run: report the selection and move nothing. Use it with
                name_regex: the pattern matches anywhere in a name, so a
                short one selects far more, and the default deletes them
                from the source.

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

        Reads the environment's own list (IntLib, SchLib, PcbLib,
        database and query libraries), open or not; `lib_search` sees
        only open SchLibs. Installed means switched on; the available
        total comes beside it, so a library present but switched off
        shows as a gap between the two numbers.

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

        Also repairs a name a caller cannot type (a quote or control
        character from a broken import): address it by
        ``component_index``. A name another component has is NAME_EXISTS.
        Save with app_save_all.

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

        The only library-scoped primitive delete: ``obj_delete`` and
        ``pcb_delete_object`` act on a BOARD, and aimed at a library they
        can delete from some other open PcbDoc. The library, footprint
        and type or layer must all match. Pads are kept unless
        ``include_pads``. A footprint on screen is edited with another
        made current, then shown again (``was_current``).

        Args:
            footprint_name: The footprint to edit. Required.
            object_type: e.g. "track", "arc", "text", "region", "fill".
                Give this or ``layer``.
            layer: One layer, e.g. "TopOverlay" or a stack name such as
                "Top Assembly"; an unknown name is refused.
            library_path: The .PcbLib; default the focused document.
            include_pads: Allow pads to be removed.
            confirm: Required True; check first with
                ``lib_probe_footprint``.

        Returns:
            ``library``, ``footprint``, ``matched``, ``removed``,
            ``leftover`` (matching primitives still there after the
            delete, counted again), ``hint`` (why, when something matched
            and stayed or nothing matched), ``examined``, ``layers``,
            ``pads_included``, ``was_current``.
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
        keep_layers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Put the 3D bodies of the focused PCB library on one mechanical
        layer.

        For libraries merged from sources that used different layers.
        Bodies already on the layer are counted and left; nothing is added
        or removed.

        Args:
            layer: The mechanical layer, by number ("Mechanical13") or by
                the name the library's stack gives it. Anything else is
                refused, as Altium's body dialog only offers mechanical
                layers.
            footprint_names: Only these footprints. Empty means every
                footprint in the library; names no footprint has come
                back in ``not_found``.
            keep_layers: Layers whose bodies stay where they are, by the
                same names as ``layer``. A body already on one of them is
                counted as already on the layer. Every other body moves.
                An unknown name refuses the whole call, and nothing moves.
                Empty keeps the old behaviour: every body moves.

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
        keep = [k.strip() for k in (keep_layers or []) if k.strip()]
        if keep:
            params["keep_layers"] = ",".join(keep)
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_bodies_to_layer", params, timeout=60.0)

    @mcp.tool()
    async def lib_set_3d_body(
        footprint_name: str,
        library_path: str = "",
        standoff_height: Optional[float] = None,
        overall_height: Optional[float] = None,
        opacity: Optional[float] = None,
        layer: str = "",
        body_index: Optional[int] = None,
        units: str = "mil",
    ) -> dict[str, Any]:
        """Read or change the 3D bodies already in a PcbLib footprint.

        ``lib_link_3d_model`` sets a body's standoff only when it creates
        it; this edits the body in place, so a wrong standoff no longer
        means deleting the body and linking it again. Call it with only
        ``footprint_name`` to read every body's current values.

        Writes the body's OverallHeight, BodyOpacity3D and Layer, and its
        standoff: what the Properties panel calls Standoff Height.

        ON A STEP BODY THAT IS THE MODEL'S Z OFFSET, not the body's own
        StandoffHeight, which Altium ignores there (measured on AD 26.11:
        the body field saved and the panel still read 0). It is written
        with IPCB_Model.SetState, which also writes the model's three
        rotations. No getter reaches those, so they are read from the
        SAVED library file and passed back unchanged: a rotation changed
        in the editor and not saved yet is reverted to the saved one. A
        STEP body with no saved record to match (an unsaved footprint, or
        the saved one names another model file) is listed in ``skipped``
        and left alone.

        Args:
            footprint_name: The footprint whose bodies to edit. Required.
            library_path: The .PcbLib. Defaults to the focused document;
                the edit is refused if that library cannot be focused,
                and a path that is not a file is refused before Altium
                sees it, since Altium opens a new, empty library there.
            standoff_height: Height of the body's base above the board.
            overall_height: Height of the body's top above the board.
            opacity: 0 (clear) to 1 (solid), not a percentage.
            layer: Move the body to this mechanical layer, by number
                ("Mechanical5") or the name the library's stack gives it.
            body_index: 1-based, in the order ``before`` lists them.
                Empty edits every body of the footprint.
            units: "mil" (default) or "mm" for the two heights.

        Does NOT save the library.

        Returns:
            Dict with ``library``, ``footprint``, ``bodies``, ``changed``,
            and ``before`` / ``after``: one ``{index, layer, step,
            standoff_height_mils, overall_height_mils, opacity}`` per body
            touched, read back from Altium (for a STEP body
            ``standoff_height_mils`` is the unused body field).
            ``saved_bodies`` is each body as the saved file has it, with
            ``model_z_offset_mils`` and ``rotation_deg``.
            ``model_z_offset_written`` lists the STEP bodies whose Z
            offset was written; save the library, then read again, to see
            it in ``saved_bodies``. ``skipped``: bodies left alone, why.
        """
        if not footprint_name.strip():
            return {"ok": False, "reason": "footprint_name is required"}
        unit = units.strip().lower()
        if unit not in ("mil", "mm"):
            return {"ok": False, "reason": f"units is 'mil' or 'mm', got {units!r}"}
        scale = 1000.0 / 25.4 if unit == "mm" else 1.0
        params: dict[str, Any] = {"footprint_name": footprint_name.strip()}
        for name, value in (("standoff_height", standoff_height),
                            ("overall_height", overall_height)):
            if value is None:
                continue
            if value != value:                  # NaN
                return {"ok": False, "reason": f"{name} must be a number"}
            params[name] = str(round(value * scale, 4))
        if opacity is not None:
            if not 0 <= opacity <= 1:
                return {"ok": False,
                        "reason": "opacity is 0 (clear) to 1 (solid), "
                                  "not a percentage"}
            params["opacity"] = str(opacity)
        if body_index is not None:
            if body_index < 1:
                return {"ok": False, "reason": "body_index starts at 1"}
            params["body_index"] = str(body_index)
        if layer.strip():
            params["layer"] = layer.strip()
        path = library_path.strip()
        if path:
            if not Path(path).is_file():
                return {"ok": False,
                        "reason": f"{path} is not a file. Altium would open a "
                                  "new, empty library under that name."}
            params["library_path"] = path
        bridge = get_bridge()

        def saved_bodies(lib: str) -> Optional[list[dict[str, Any]]]:
            if not lib or not Path(lib).is_file():
                return None
            try:
                return pcblib_models.saved_body_models(lib, params["footprint_name"])
            except (OSError, ValueError):
                return None

        saved = None
        if "standoff_height" in params:
            if not path:
                # The rotations come from the saved file: find which is in front.
                read = await bridge.send_command_async(
                    "library.set_3d_body", {"footprint_name": params["footprint_name"]},
                    timeout=60.0)
                path = read.get("library", "") if isinstance(read, dict) else ""
            saved = saved_bodies(path)
            if saved is not None:
                params["saved_count"] = str(len(saved))
                params["saved_models"] = "|".join(
                    f"{b['model_file']}|{b['model_type']}|{b['rotation_deg'][0]}"
                    f"|{b['rotation_deg'][1]}|{b['rotation_deg'][2]}" for b in saved)
        reply = await bridge.send_command_async(
            "library.set_3d_body", params, timeout=60.0)
        if isinstance(reply, dict):
            if saved is None and "standoff_height" not in params:
                saved = saved_bodies(path or reply.get("library", ""))
            if saved is not None:
                reply["saved_bodies"] = [dict(b, index=i) for i, b in enumerate(saved, 1)]
        return reply

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

        With ``replace`` (the default) the tracks, arcs, texts, fills and
        regions on ``layer`` are cleared first; bodies and pads are never
        touched. Does NOT save. A layer the library has not enabled is
        accepted with ``layer_enabled`` false and a ``warning``.

        Args:
            layer: The mechanical layer for the drawing.
            footprint_names: Only these footprints. Empty means all.
            outline_from: ``body``, ``pads``, ``courtyard`` (drawn on
                ``courtyard_layer``) or ``auto`` (that order of
                preference). A footprint with none is in ``skipped``.
            courtyard_layer: The courtyard's layer; not ``layer`` when
                replacing.
            width: Line width (default 4 mil).
            designator: Place a centred ``.Designator``, upright in a
                tall box.
            text_height: 0 sizes it to the box (0.2 to 1 mm).
            replace: Clear the layer first; False draws beside it.
            confirm: Required with ``replace``.
            units: "mil" (default) or "mm" for ``width`` and
                ``text_height``.

        Returns:
            ``layer``, ``layer_enabled``, ``redrawn``, ``cleared``,
            ``skipped``, ``not_found``, ``items`` (``{name,
            outline_from, cleared, width_mm, height_mm}``).
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

        With an empty Location a footprint resolves by name in the editor
        but is missing from a compiled library, with no compile error.
        Targets footprint models named model_name, or all of them. Copy
        the exact source_library from a model that embeds correctly
        (``lib_get_component_details`` shows each model's location); it
        is written as given.

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
        """Pad geometry of one PcbLib footprint as floats in mm, relative
        to the footprint origin (no integer-mil rounding, for datasheet
        tolerance checks).

        Args:
            footprint_name: the footprint name in the library.
            library_path: optional .PcbLib path; defaults to the
                focused library.

        Returns:
            {name, description, library_path, pad_count, pads: [{
             name, x_mm, y_mm, copper_x_mm, copper_y_mm, w_mm, h_mm,
             shape, corner_pct, rotation, hole_rotation, hole_mm,
             hole_w_mm, hole_type, slot_aligned (slots only), plated,
             layer, selected, mode, paste_expansion_mm,
             paste_expansion_source, paste_expansion_state,
             mask_expansion_mm, mask_bottom_expansion_mm, mask_separate,
             mask_expansion_source, mask_expansion_state}]}.
            selected is whether the pad is selected in the editor (what
            lib_set_pad_properties selected=True acts on); mode is the
            stack mode, 0 simple, 1 top-middle-bottom, 2 full stack.
            x/y is the pad origin, copper_x/y the copper centre (differs
            on a custom pad). hole_rotation is relative to the pad.
            mask_expansion_mm is the top side when mask_separate, else
            both; mask_bottom_expansion_mm is the stored bottom value.
            *_source is 'manual' (pad override) or 'rule' (value 0; the
            rule holds it); *_state is the cache's manual/valid/invalid.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_set_pad_properties(
        footprint_name: str,
        pad_name: str = "",
        selected: bool = False,
        library_path: str = "",
        units: str = "mil",
        x_size: Optional[float] = None,
        y_size: Optional[float] = None,
        bottom_x_size: Optional[float] = None,
        bottom_y_size: Optional[float] = None,
        shape: Optional[str] = None,
        corner_radius_pct: Optional[float] = None,
        stack_mode: Optional[str] = None,
        rotation: Optional[float] = None,
        hole_type: Optional[str] = None,
        hole_size: Optional[float] = None,
        hole_width: Optional[float] = None,
        hole_rotation: Optional[float] = None,
        plated: Optional[bool] = None,
        paste_expansion: Optional[float] = None,
        paste_manual: Optional[bool] = None,
        mask_expansion: Optional[float] = None,
        mask_bottom_expansion: Optional[float] = None,
        mask_separate: Optional[bool] = None,
        mask_manual: Optional[bool] = None,
        mask_from_hole_edge: Optional[bool] = None,
        paste_top_enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Change existing pads in a PcbLib footprint: every pad named
        ``pad_name``, or with ``selected=True`` every pad selected in the
        editor, only the fields given, each read back. A written
        expansion is made manual, without which Altium ignores it;
        ``mask_manual`` / ``paste_manual`` False hands one back to the
        design rule. On a per-layer pad stack ``x_size`` / ``y_size`` are
        the top size (``note``); ``stack_mode`` and the bottom size
        change the rest.

        Args:
            footprint_name: The footprint holding the pad.
            pad_name: The pad's designator. Names need not be unique (an
                SMD pad "1" beside a thru-hole "1"): use ``selected``
                to say which.
            selected: Change the pads selected in the editor instead of
                those named ``pad_name``.
            library_path: Optional .PcbLib path; defaults to the focused
                library.
            units: "mil" (default) or "mm", for every length here.
            x_size, y_size: Copper size (the top size on a stacked pad).
            bottom_x_size, bottom_y_size: Bottom copper size; makes a
                simple pad top-middle-bottom.
            shape: "round", "rectangular", "octagonal" or "roundrect".
            corner_radius_pct: Rounded-rectangle corner, 0 to 100, as a
                percentage of half the shorter side (how Altium stores
                it); 25 when ``shape`` is roundrect and this is not given.
            stack_mode: "simple" (the top size serves every layer) or
                "top-middle-bottom", written before the sizes.
            rotation: Pad rotation in degrees.
            hole_type: "round", "square" or "slot", written before the
                hole sizes, so a slot can be made round again.
            hole_size: Hole diameter, 0 for a surface-mount pad.
            hole_width: Slot length; makes the hole a slot (so it is
                refused beside ``hole_type`` round or square).
            hole_rotation: Slot rotation relative to the pad, degrees.
            plated: Whether the hole is plated.
            paste_expansion: Paste expansion.
            paste_manual: True keeps the paste value as written; False
                hands the paste back to the design rule.
            mask_expansion: Mask expansion, both sides unless
                ``mask_bottom_expansion`` is also given.
            mask_bottom_expansion: Bottom mask expansion; unlinks sides.
            mask_separate: Link (False) or unlink (True) the mask sides
                (the bottom value takes effect once unlinked).
            mask_manual: As ``paste_manual``, for the solder mask.
            mask_from_hole_edge: Measure the mask from the drill edge,
                not the copper (mechanical holes and slots).
            paste_top_enabled: Switch the top paste opening on or off
                (not its size). Altium 26.11 keeps this only while the
                paste expansion is manual, so a rule-driven one is made
                manual at its current value first, and ``note`` says so.
                Before Altium Designer 25 the flag does not exist: the
                write is refused per pad and reads give null.

        Returns:
            ``pads_changed``, ``pads_with_a_failed_write``, ``note``,
            ``pads`` as read back (top and bottom size, shape,
            ``corner_radius_pct``, ``stack_mode``, rotation, hole, slot,
            plating, expansions and their state, ``mask_separate``,
            ``mask_from_hole_edge``, ``paste_top_enabled``,
            ``all_writes_ran``).
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        if not pad_name and not selected:
            return {"success": False,
                    "error": "give pad_name, or selected=True for the "
                             "pads selected in the editor"}
        if hole_type is not None and hole_type not in ("round", "square", "slot"):
            return {"success": False,
                    "error": "hole_type must be round, square or slot"}
        if hole_type in ("round", "square") and hole_width is not None:
            return {"success": False,
                    "error": "hole_width makes a slot; leave it out for a "
                             "round or square hole"}
        if stack_mode is not None and stack_mode not in ("simple",
                                                         "top-middle-bottom"):
            return {"success": False,
                    "error": "stack_mode must be simple or top-middle-bottom"}
        if corner_radius_pct is not None and not 0 <= corner_radius_pct <= 100:
            return {"success": False,
                    "error": "corner_radius_pct is 0 to 100"}
        if mask_manual is False and (mask_expansion is not None
                                     or mask_bottom_expansion is not None):
            return {"success": False,
                    "error": "mask_manual=False hands the mask to the rule; "
                             "a mask value with it would be ignored"}
        if paste_manual is False and paste_expansion is not None:
            return {"success": False,
                    "error": "paste_manual=False hands the paste to the rule; "
                             "a paste value with it would be ignored"}
        params: dict[str, Any] = {"footprint_name": footprint_name,
                                  "pad_name": pad_name,
                                  "library_path": library_path}
        if selected:
            params["selected"] = "true"
        base = len(params)
        if hole_type is not None:
            params["hole_type"] = hole_type
        for key, value in (("x_size", x_size), ("y_size", y_size),
                           ("bottom_x_size", bottom_x_size),
                           ("bottom_y_size", bottom_y_size),
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
        if corner_radius_pct is not None:
            params["corner_radius_pct"] = float(corner_radius_pct)
        if stack_mode is not None:
            params["stack_mode"] = stack_mode
        if plated is not None:
            params["plated"] = bool(plated)
        for key, value in (("mask_separate", mask_separate),
                           ("mask_manual", mask_manual),
                           ("paste_manual", paste_manual),
                           ("mask_from_hole_edge", mask_from_hole_edge)):
            if value is not None:
                params[key] = "true" if value else "false"
        if paste_top_enabled is not None:
            # The top paste opening itself, not its expansion (issue #80).
            params["paste_top_enabled"] = "true" if paste_top_enabled else "false"
        if len(params) == base:
            return {"success": False,
                    "error": "give at least one property to change"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_pad_properties", _with_units(params, units))

    @mcp.tool()
    async def lib_get_footprint_primitives(
        footprint_name: str,
        library_path: str = "",
        kinds: str = "",
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
            kinds: Optional ``|`` list of what to read, from track, arc,
                region, fill, text, via and body (``"via|body"``); empty
                reads all. Each kind is its own filtered walk.

        Every primitive carries ``selected``. Each via also carries its
        mask (top, bottom, separate, ``mask_expansion_source`` manual or
        rule, ``mask_from_hole_edge``), tenting, the four testpoint flags
        (read only), ``stack_mode`` and its top / mid / bottom diameters;
        each body its outline box (x1, y1, x2, y2).

        Returns:
            {name, library_path, height_mm, editor_origin_x_mm /
            editor_origin_y_mm (the editor's origin marker in this
            footprint's frame), kinds, bodies (a count),
            body_layers (the layer of each body), body_models [{layer,
            identifier, has_model, model_file (the file the model was
            embedded from), model_type (Altium's T3DModelType; 1 for a
            STEP model), step_solids (solids in that file when it is
            still there, else null: a STEP model is ONE body whatever its
            detail, so read this, not ``bodies``), rotation,
            overall_height_mm, standoff_mm}], tracks
            [{layer, layer_id, x1, y1, x2, y2, width}], arcs [{layer,
            layer_id, xc, yc, r, start, end, width}], regions and fills
            [{layer, layer_id, x1, y1, x2, y2, area and arc_edges
            (regions)}], texts
            [{text, layer, layer_id, x, y, size, rotation, x1, y1, x2,
            y2}], vias [{x, y, diameter, hole}]}. A text's box is
            Altium's cached one and can lag an edit made this session.
        """
        params: dict[str, Any] = {"footprint_name": footprint_name,
                                  "library_path": library_path}
        if kinds:
            params["kinds"] = kinds
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.get_footprint_primitives", params)
        # ONE BODY IS NOT ONE BLOCK. A STEP model is a single body however
        # detailed it is; a take read "bodies": 1 on every footprint as
        # "single plain blocks" and rebuilt eleven models that already had
        # their terminations and leads. The file says how many solids.
        for model in (result.get("body_models") or []
                      if isinstance(result, dict) else []):
            if isinstance(model, dict) and model.get("model_file"):
                model["step_solids"] = await asyncio.to_thread(
                    _step_solid_count, str(model["model_file"]))
        return result

    @mcp.tool()
    async def lib_select_footprint_primitives(
        footprint_name: str,
        objects: list[dict[str, Any]],
        clear: bool = True,
    ) -> dict[str, Any]:
        """Select primitives of one PcbLib footprint in the editor, for an
        editor command that acts on the selection (obj_run_process with
        PCB:CustomPadShape to merge a pad and a region into a custom pad,
        PCB:Clear, ...). obj_select acts on a board, not a footprint.

        Several sets select together in one call; with ``clear=False`` a
        later call adds to the selection. Pad names are not unique (an
        SMD "12" and a thru-hole "12" can share a footprint), so narrow by
        layer or box and check the reply before running the command.

        Args:
            footprint_name: The footprint, made current if it is not.
            objects: One or more filter sets; an object matching ANY set is
                selected. Each set has any of ``object_type`` ("pad",
                "region", "track", ...), ``layer`` ("TopLayer",
                "MultiLayer", ...), ``name`` (a pad's designator or a
                region's Name) and ``box`` ([x1, y1, x2, y2] in mm from the
                footprint origin, the box lib_get_footprint_primitives
                gives a region, fill or text; matched within 0.001 mm).
            clear: Deselect everything else first (default True).

        Returns:
            {selected (how many matched), layers (theirs), selected_total
            (everything the editor now holds selected)}
        """
        sets: list[str] = []
        for i, spec in enumerate(objects or []):
            if not isinstance(spec, dict):
                return {"ok": False, "reason": f"set {i} is not an object"}
            fields: list[str] = []
            for key in ("object_type", "layer", "name"):
                value = str(spec.get(key) or "").strip()
                if value:
                    fields.append(f"{key}={payload_safe(value)}")
            box = spec.get("box")
            if box is not None:
                try:
                    x1, y1, x2, y2 = (float(v) for v in box)
                except (TypeError, ValueError):
                    return {"ok": False,
                            "reason": f"set {i}: box is [x1, y1, x2, y2] in mm"}
                fields.append("box=" + ",".join(
                    format_length(v) for v in (x1, y1, x2, y2)))
            if not fields:
                return {"ok": False, "reason": f"set {i} has no filter; it "
                        "would select the whole footprint"}
            sets.append(";".join(fields))
        if not sets:
            return {"ok": False, "reason": "objects needs at least one set"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.select_footprint_primitives",
            {"footprint_name": footprint_name, "objects": "~~".join(sets),
             "clear": "true" if clear else "false"})

    @mcp.tool()
    async def lib_extract_embedded_models(
        library_path: str,
        footprint_name: str = "",
        model_names: Optional[list] = None,
        out_dir: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Get back the ORIGINAL 3D model files (STEP) embedded in a .PcbLib.

        Byte for byte the file Altium took in: each is written only when
        its checksum equals the one Altium stored when it embedded it.
        No Altium API saves an embedded model, so this reads the saved
        library file directly, read only (it never writes the library).
        A model added in Altium and not saved yet is not in the file.

        With ``footprint_name``, Altium is asked which model files that
        footprint's bodies use and only those are extracted; with
        ``model_names``, those; otherwise every embedded model.

        Args:
            library_path: The .PcbLib on disk.
            footprint_name: Extract only this footprint's models (asks
                Altium, so the library must be reachable over the bridge).
            model_names: Model file names, e.g. ["NE555DR.stp"].
            out_dir: Folder to write to (default: the workspace's
                models/<library name>).
            overwrite: Replace files already there.

        Returns:
            ``written`` [{name, path, bytes, sha256, checksum_verified,
            step_header, rotation_deg}], ``refused`` [{name, reason}],
            ``not_in_saved_file`` (asked for, not in the saved file).
        """
        path = Path(library_path)
        if not path.is_file():
            return {"ok": False, "reason": f"no such library file: {library_path}"}
        names = list(model_names or [])
        asked_altium = None
        if footprint_name:
            prims = await get_bridge().send_command_async(
                "library.get_footprint_primitives",
                {"footprint_name": footprint_name, "library_path": str(path)})
            if not isinstance(prims, dict) or "body_models" not in prims:
                return {"ok": False, "reason": "Altium could not list the "
                        "footprint's 3D bodies", "altium": prims}
            from_altium = [b.get("model_file", "") for b in prims["body_models"]
                           if b.get("has_model") and b.get("model_file")]
            asked_altium = from_altium
            if not from_altium:
                return {"ok": False, "reason": f"{footprint_name} has no 3D "
                        f"model, only simple bodies", "body_models": prims["body_models"]}
            # Altium's names are Windows paths whatever runs this.
            names += [re.split(r"[\\/]", n)[-1] for n in from_altium]
        target = Path(out_dir) if out_dir else (
            Path(get_bridge().config.workspace_dir) / "models" / path.stem)
        try:
            got = pcblib_models.extract_embedded_models(
                path, target, names=names or None, overwrite=overwrite)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if asked_altium is not None:
            got["footprint"] = footprint_name
            got["altium_model_files"] = asked_altium
        return got

    async def _live_footprint(footprint_name: str, library_path: str,
                              courtyard_layer: str, assembly_layer: str):
        """(model, notes) for one footprint, read from the library live:
        pads, every other primitive, and the library's layer kinds."""
        from ..bridge.exceptions import AltiumCommandError
        from ..design.landpattern.live import model_from_live

        bridge = get_bridge()
        args = {"footprint_name": footprint_name, "library_path": library_path}
        pads = await bridge.send_command_async("library.get_pad_geometry", args)
        prims = await bridge.send_command_async(
            "library.get_footprint_primitives", args)
        notes: list[str] = []
        mech = None
        want = str((prims or {}).get("library_path") or library_path)
        try:
            reply = await bridge.send_command_async(
                "library.get_mech_layers", {"library_path": want})
            got = str((reply or {}).get("library_path") or "")
            if got and want and got.lower() != want.lower():
                notes.append(f"layer kinds were read from {got}, not {want}; "
                             f"layers are matched by the names given only")
            else:
                mech = (reply or {}).get("layers") or []
        except AltiumCommandError as e:
            notes.append(f"layer kinds not read ({e.code or e}); courtyard "
                         f"and assembly come from the names given only")
        model = model_from_live(pads, prims, mech,
                                courtyard_layer=courtyard_layer,
                                assembly_layer=assembly_layer)
        if not model.pads and not model.graphics:
            notes.append(f"{footprint_name} is empty: no pads and no "
                         f"primitives")
        elif not any(g.layer == "courtyard" for g in model.graphics):
            notes.append("no courtyard found: no mechanical layer has the "
                         "Courtyard kind and courtyard_layer was not given")
        return model, notes

    @mcp.tool()
    async def lib_render_footprint(
        footprint_name: str,
        library_path: str = "",
        path: str = "",
        png: bool = True,
        courtyard_layer: str = "",
        assembly_layer: str = "",
    ) -> dict[str, Any]:
        """Draw a footprint as it is in its library, to look at.

        Read live (pads, every primitive, the library's layer kinds) and
        drawn the way ``footprint_render_package`` draws a planned one:
        copper with pad numbers and holes, paste, silkscreen, assembly
        dashed blue, courtyard magenta, anything else dashed grey. Which
        layer is the courtyard comes from Altium's layer KIND; a library
        whose layers have no kind set needs ``courtyard_layer`` /
        ``assembly_layer`` (e.g. "Mechanical15").

        Args:
            footprint_name: the footprint in the library.
            library_path: the .PcbLib; empty = the focused library.
            path: SVG to write; default <workspace>/renders/<name>_live.svg.
            png: also write a PNG beside it when a headless browser is
                installed.
            courtyard_layer / assembly_layer: layer names to use when the
                layer kinds do not say.

        Returns:
            {ok, svg_path, png_path, pads, roles (per role: count and
            extent in mm), height_mm, silk_over_copper (silkscreen lines
            that touch a pad, by pad), notes}.
        """
        from ..design.landpattern.live import role_summary, silk_over_copper
        from ..design.landpattern.svg import render_svg

        model, notes = await _live_footprint(
            footprint_name, library_path, courtyard_layer, assembly_layer)
        on_copper = silk_over_copper(model)
        if on_copper:
            notes.insert(0, f"silkscreen over copper on pad(s) "
                            f"{', '.join(sorted({h['pad'] for h in on_copper}))}")
        svg = render_svg(model, notes=[f"{len(model.pads)} pads, read live",
                                       *notes[:2]])
        if path:
            target = Path(path).with_suffix(".svg")
        else:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model.name).strip("._") \
                or "footprint"
            target = get_config().workspace_dir / "renders" / f"{safe}_live.svg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(svg, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "reason": f"could not write {target}: {exc}"}
        out: dict[str, Any] = {
            "ok": True, "svg_path": str(target), "png_path": None,
            "name": model.name, "pads": len(model.pads),
            "roles": role_summary(model), "height_mm": model.body.z1,
            "silk_over_copper": on_copper, "notes": notes,
            "legend": {"copper": "pads", "dark": "paste", "black": "silkscreen",
                       "blue dashed": "assembly", "magenta": "courtyard",
                       "grey dashed": "other layers"}}
        if png:
            from ..render import rasterize_svg, size_svg_for_raster

            sized, w_px, h_px = size_svg_for_raster(svg, target_width=1400)
            raster = target.with_suffix(".raster.svg")
            try:
                raster.write_text(sized, encoding="utf-8")
                rr = await asyncio.to_thread(
                    rasterize_svg, str(raster),
                    str(target.with_suffix(".png")), w_px, h_px)
            finally:
                try:
                    raster.unlink()
                except OSError:
                    pass
            if rr.get("ok"):
                out["png_path"] = rr["png_path"]
            else:
                out["rasterize_note"] = rr.get("reason")
        return out

    @mcp.tool()
    async def lib_compare_footprints(
        footprint_a: str,
        footprint_b: str,
        library_a: str = "",
        library_b: str = "",
        tolerance_mm: float = 0.001,
        courtyard_layer: str = "",
        assembly_layer: str = "",
    ) -> dict[str, Any]:
        """What differs between two footprints, pad by pad and layer by layer.

        Both read live, from one library or two (a vendor's footprint
        against yours, an old revision against the new). Pads are matched
        by number: position, size, shape, corner radius, rotation, hole,
        plating, paste and mask expansion are compared. Silkscreen,
        assembly, courtyard and the other layers are compared by count and
        extent. ``same`` is true only when nothing differs over the
        tolerance.

        Args:
            footprint_a / footprint_b: footprint names.
            library_a / library_b: their .PcbLib files; empty = focused
                library (library_b defaults to library_a).
            tolerance_mm: smallest difference reported.
            courtyard_layer / assembly_layer: layer names to use when the
                layer kinds do not say.
        """
        from ..design.landpattern.live import compare_live

        model_a, notes_a = await _live_footprint(
            footprint_a, library_a, courtyard_layer, assembly_layer)
        model_b, notes_b = await _live_footprint(
            footprint_b, library_b or library_a, courtyard_layer,
            assembly_layer)
        out = compare_live(model_a, model_b, tol=max(0.0, tolerance_mm))
        out.update({"a": {"footprint": footprint_a,
                          "library": model_a.source.get("library_path")},
                    "b": {"footprint": footprint_b,
                          "library": model_b.source.get("library_path")},
                    "notes": sorted(set(notes_a + notes_b))})
        return out

    @mcp.tool()
    async def lib_build_integrated_library(
        package_path: str,
        source_libraries: list[str],
        install: bool = False,
    ) -> dict[str, Any]:
        """Package libraries into a LibPkg and compile it to an IntLib.

        Through Altium, end to end: the .LibPkg is created when it does
        not exist, each source library (.SchLib, .PcbLib, ...) is added to
        it, and the package is compiled. The IntLib Altium writes to
        "Project Outputs for <package>" is then looked for, and
        ``built`` is true only when a NEW file is there, so a compile
        that produced nothing is not reported as done. ``install`` adds
        the IntLib to the installed libraries.

        Args:
            package_path: the .LibPkg to create or update (full path).
            source_libraries: full paths of the libraries to package.
            install: install the compiled IntLib.
        """
        import os
        import time as _time

        if Path(package_path).suffix.lower() != ".libpkg":
            return {"success": False, "error": "BAD_PARAM",
                    "reason": "package_path must be a .LibPkg"}
        sources = [str(s) for s in (source_libraries or []) if str(s).strip()]
        missing = [s for s in sources if not os.path.isfile(s)]
        if not sources or missing:
            return {"success": False, "error": "MISSING_SOURCES",
                    "reason": ("no source libraries given" if not sources
                               else "not found: " + ", ".join(missing))}
        bridge = get_bridge()
        steps: dict[str, Any] = {}
        created = not os.path.isfile(package_path)
        if created:
            os.makedirs(os.path.dirname(package_path) or ".", exist_ok=True)
            steps["create"] = await bridge.send_command_async(
                "project.create", {"project_path": package_path,
                                   "project_type": "IntegratedLibrary"})
        else:
            steps["open"] = await bridge.send_command_async(
                "project.open", {"project_path": package_path})
        added = []
        for src in sources:
            reply = await bridge.send_command_async(
                "project.add_document",
                {"document_path": src, "project_path": package_path})
            added.append({"library": src,
                          "ok": bool(isinstance(reply, dict)
                                     and reply.get("success", True))})
        out_dir = Path(package_path).parent / \
            f"Project Outputs for {Path(package_path).stem}"
        intlib = out_dir / f"{Path(package_path).stem}.IntLib"
        before = intlib.stat().st_mtime if intlib.is_file() else None
        started = _time.time()
        steps["compile"] = await bridge.send_command_async(
            "project.compile", {"project_path": package_path}, timeout=300.0)
        after = intlib.stat().st_mtime if intlib.is_file() else None
        built = after is not None and (before is None or after > before)
        out: dict[str, Any] = {
            "success": built, "built": built, "created": created,
            "package_path": package_path, "added": added,
            "intlib": str(intlib) if intlib.is_file() else None,
            "intlib_bytes": intlib.stat().st_size if intlib.is_file() else None,
            "compile_seconds": round(_time.time() - started, 1)}
        if not built:
            out["reason"] = (f"no new IntLib at {intlib} after the compile; "
                             f"read Altium's Messages panel for the errors")
        elif install:
            out["install"] = await bridge.send_command_async(
                "library.install_library", {"library_path": str(intlib)})
        return out

    @mcp.tool()
    async def lib_audit_footprint_vs_datasheet(
        footprint_name: str,
        spec_json: Any,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Audit a footprint against the manufacturer's land pattern.

        Compares the live pads (count, position, size, shape, drill,
        numbering, thermal pad and its paste) with a spec transcribed
        from the manufacturer datasheet fetched in this conversation and
        cited in ``spec.source``. Origin and 90-degree rotation are
        aligned out; a mirrored pattern is reported, not compensated.

        The spec (JSON object or string):
          source: {datasheet_url, reference, part_number}   REQUIRED
          pads: [{name, x, y, w, h, shape?, hole?}]         explicit, mm
          dual_row: {count, pitch, span, pad_w, pad_h, shape?}
          quad: {count, pitch, span_x, span_y, pad_w, pad_h, shape?}
          thermal_pad: {name, x, y, w, h, shape?}
          thermal_paste: 'full' | 'windowed'
          position_tol / size_tol: mm, default 0.05

        dual_row numbers like SOIC and quad like QFN (pin 1 top-left,
        CCW); otherwise give ``pads``. pad_w runs ACROSS the row in
        dual_row but ALONG it in quad. span / span_x / span_y are pad
        CENTRE to pad CENTRE (C on a vendor land pattern), never the
        outer extent Z or the package's lead span.

        Whatever the spec says, pads of different names whose copper
        overlaps are an error and a gap under 0.1 mm a warning
        (check ``pad_clearance``): a footprint that faithfully matches a
        misread spec still shorts.

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

        Takes the PackageSpec (as lib_create_footprint_from_package does).
        Reads only. Checks pad count, numbering and pin 1 (names a
        mirror-image footprint), pitch, lands against IPC-7351B within
        vendor tolerances, holes, exposed-pad paste, solder mask, origin,
        courtyard, silkscreen, assembly outline and height. Unread items
        are in ``not_examined``. Run footprint_check_package on the
        package first.

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

        EXPECT THIS TO FAIL, and not for lack of credentials: the
        service's search routes no longer answer (checked live), and no
        login would restore them. Search LCSC in a browser instead and
        import by id, which still works:
        lib_easyeda_import(lcsc_id="C1234", ...). When it does answer it
        gives candidates only. HTTPS with a host allowlist;
        EASYEDA_API_BASE overrides the endpoint.

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

        target="altium" returns an ORDERED PLAN of this server's library
        tools, like lib_easyeda_import; nothing is written until you run
        the steps. target="inspect" only parses. A multi-unit symbol
        becomes one Altium multi-part symbol; hidden pins stay hidden;
        derived symbols take their parent's geometry. Audit a converted
        footprint with ``lib_audit_footprint_vs_datasheet`` before
        trusting it.

        Args:
            symbol_path, footprint_path: the KiCad files; give either or
                both.
            symbol_name: one symbol of a multi-symbol library; default
                the first.
            target: "altium" (default) or "inspect".
            schlib_path, pcblib_path: destination libraries, required
                for target="altium".
            include_body_art: also draw the symbol body; False gives
                pins only.
            unit: convert only this sub-part (0 = all).
            model_3d_path: a resolved 3D model file to link (for example
                from part_fetch).

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
        """Convert an EasyEDA / LCSC part to KiCad files or an Altium plan
        (own converter, from EasyEDA's published format).

        Args:
            lcsc_id: fetch online, e.g. "C7420".
            payload_path: or read a saved component JSON (offline).
            target: "inspect" parses only (use first on an unfamiliar
                part); "kicad" writes ``.kicad_sym`` and ``.kicad_mod``
                into ``output_dir``; "altium" returns an ordered plan of
                this server's lib_* tools that recreates the part in
                ``schlib_path`` / ``pcblib_path``. Nothing is written to
                Altium; review the plan, then run the steps
                (tool_invoke).
            schlib_path, pcblib_path: required for target="altium".
            output_dir: required for target="kicad".
            symbol_name, footprint_name: override the part's own names.
            include_body_art: also draw the symbol body; False gives
                pins only.
            fetch_3d: kicad only; fetch the 3D model as a ``.wrl`` the
                footprint references (Altium needs STEP, not VRML).

        An imported footprint is a vendor drawing, not ground truth:
        audit it with ``lib_audit_footprint_vs_datasheet``. Polygon pads
        and slotted holes are reported in ``warnings``.

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
        copied from another library keep SourceLibraryName,
        TargetFileName and DesignItemId pointing at it, and placements
        then land in <Not Found>. This clears the first, sets the second
        to '*' and the third to LibReference: the provenance part of
        lib_normalize_implementations, alone. Save with app_save_all.

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

        Per component: clears SourceLibraryName and TargetFileName; keeps
        ONE model per (type, name) in place, with its parameters, pin map
        and datafile link; applies ``rename_map``; and repairs footprints
        with no datafile link. Nothing is rebuilt, so nothing is lost.
        When renaming footprints, run lib_rename_footprint on the PcbLib
        first and pass the same map here.

        Args:
            library_path: optional .SchLib path; defaults to the focused lib.
            rename_map: optional {old_model_name: new_model_name} applied to
                the kept model's name and matching datafile entity.
            dedupe_only: skip renames; only collapse duplicates + clear source.
            source_library: optional; written into every PCBLIB model's
                datafile Location so footprints embed on compile (never
                blanked). Copy it from a known-good model (see
                lib_set_model_source).
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
