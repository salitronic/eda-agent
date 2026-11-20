# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EasyEDA Pro tools, reached through the editor extension.

Every tool here is a thin wrapper over one command the extension
answers, which is the same shape the KiCad tools take over their bridge.
The thinness is deliberate: the editor owns the operation, and anything
this layer computed itself would be a second opinion competing with the
tool the user is actually looking at.

FAILURE SHAPE. These follow the bridge-backed convention already in use:
a call that cannot reach the editor comes back as
``{"ok": False, "unavailable": ..., "reason": ...}`` rather than
raising, so a caller can tell "start the extension" from "the editor
refused that". ``reason`` carries the same text, because every other
refusal on this backend uses that key and a caller should not have to
know which kind of failure it is before it can read why.

MATURITY. None of these has run against a live EasyEDA Pro. They are
registered so the backend is usable and so the command contract is
checked, and every response carries ``verified_live`` from the bridge
rather than implying a working link. That flag flips when someone runs
it against the real editor, not before.
"""

from __future__ import annotations

import json
from typing import Any, Optional

__all__ = ["register_easyeda_tools"]

#: EasyEDA's schematic canvas counts in units of 0.01 inch, so one unit
#: is TEN mils, while its PCB canvas counts in mils, one to one. Their
#: own guidance calls mixing the two the most common mistake made
#: against this API, and it is the quiet kind: a schematic laid out in
#: mils and sent unconverted lands ten times too far out, which looks
#: like a layout bug rather than a unit bug.
#:
#: Every coordinate in this module is in MILS, matching the rest of this
#: project, and the conversion happens here at the boundary rather than
#: in each tool. One constant, one place to correct if EasyEDA ever
#: changes it.
MILS_PER_SCHEMATIC_UNIT = 10.0


def _sch(value: float) -> float:
    """Convert one mil measurement to EasyEDA schematic units."""
    return value / MILS_PER_SCHEMATIC_UNIT


def _point_to_segment(px: float, py: float,
                      x1: float, y1: float,
                      x2: float, y2: float) -> float:
    """Distance from a point to a line SEGMENT, not to its infinite line.

    The clamp is the whole point. Projecting onto the unclamped line
    makes a point beyond an endpoint look closer to that edge than it
    is, which invents violations off the board and can mask the corner
    it is really near.

    Module-level so the audits share one copy. Two audits measuring
    distance to a board edge slightly differently would disagree about
    the same board, and the disagreement would look like a real finding.
    """
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    nx, ny = x1 + t * dx, y1 + t * dy
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def _edges_from(segments: list) -> list[tuple[float, float, float, float]]:
    """Outline segments as (x1, y1, x2, y2), skipping unusable ones."""
    edges = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        try:
            edges.append((float(seg["startX"]), float(seg["startY"]),
                          float(seg["endX"]), float(seg["endY"])))
        except (KeyError, TypeError, ValueError):
            continue
    return edges


#: Set to a dict for the duration of ONE aggregated review, so the
#: eighteen audits share their reads instead of each fetching the same
#: board. Four of them read pcb.lines alone; on a live board that is
#: 813 segments fetched four times.
#:
#: None outside a review, which is the important half: a cache that
#: outlived its review would answer a later call with an older board,
#: and an audit reporting yesterday's geometry is worse than a slow
#: one. Only parameterless reads are cached, so nothing has to reason
#: about argument equality, and only successful replies, so a transient
#: refusal is not remembered as the answer.
_READ_CACHE: "dict | None" = None


def _call(command: str, params: dict | None = None,
          timeout: float = 30.0) -> dict[str, Any]:
    """Run one editor command, reporting unreachability as data.

    Raising would be wrong here: "no editor is connected" is an ordinary
    state on a machine where EasyEDA simply is not open, and an agent
    reading the result should be able to act on it rather than catch it.
    """
    from ..bridge.easyeda_bridge import (
        EasyEdaNotReachableError,
        get_easyeda_bridge,
    )

    cacheable = _READ_CACHE is not None and not params
    if cacheable and command in _READ_CACHE:
        return _READ_CACHE[command]

    bridge = get_easyeda_bridge()
    try:
        reply = bridge.send_editor_command(
            command, params or {}, timeout=timeout)
    except EasyEdaNotReachableError as exc:
        # Both keys, on purpose.
        #
        # `unavailable` is the older convention and carries a real
        # distinction this module is built on: never reached the editor
        # versus the editor said no. `reason` is what every other
        # refusal on this backend uses, and what the envelope contract
        # asserts is present whenever ok is false.
        #
        # They contradicted each other for 107 tools, and the contract
        # test could not see it because it drives a bridge that answers
        # everything, so the unreachable path never ran. A caller
        # reading `reason` got None from those tools and a sentence
        # from the other 32, which is the one thing a uniform envelope
        # exists to prevent. Carrying both keeps the distinction and
        # makes the explanation readable the same way everywhere.
        return {"ok": False, "unavailable": str(exc), "reason": str(exc),
                "command": command}

    if "error" in reply:
        # The command reached the editor and the editor said no. A
        # different answer from never arriving, and kept distinct.
        return {"ok": False, "reason": reply["error"], "command": command}

    result = reply.get("result")
    out: dict[str, Any] = {"ok": True, "command": command}
    if isinstance(result, dict):
        out.update(result)
    else:
        out["result"] = result

    # A CREATE THAT CREATED NOTHING IS A FAILURE.
    #
    # Twenty one handlers end `return { created: primitive || null }`,
    # which is honest: null means the editor's create call handed back
    # nothing. But the envelope was still ok, so a caller placing a via
    # or a wire got `{"ok": true, "created": null}` and every one of
    # those tools reported success for having drawn nothing.
    #
    # Checked here rather than at twenty two call sites, because this
    # is a property of the reply envelope, the same as verified_live.
    # Only when the key is PRESENT: a reply that never mentions
    # `created` is a read, and absence must not be read as failure.
    if "created" in out and out["created"] is None:
        return {
            "ok": False,
            "reason": (
                f"{command} reported nothing created. The editor accepted "
                f"the call and returned no object, so nothing was drawn. "
                f"This is not a partial result to retry blindly: check "
                f"the arguments, and whether the target document is the "
                f"one you meant."),
            "command": command,
            "created": None,
        }

    # THE SAME RULE FOR THE OTHER STATUS FIELDS.
    #
    # A handler that answers `saved: false` or `opened: false` has told
    # the truth, and the envelope then wrapped it in ok: true. A save
    # the editor declined was reported as a save.
    #
    # Named explicitly rather than matched on "any false boolean",
    # because two fields nearby are DATA and not verdicts: `ready` sits
    # beside `opened` and distinguishes opened-but-not-yet-readable,
    # which is a state worth reporting rather than a failure, and
    # `reachable` in system.capabilities describes each probed class.
    #
    # Handlers that set their own `ok` need nothing here: result is
    # merged over the envelope, so their false already wins.
    for field in ("saved", "opened", "activated", "closed"):
        if out.get(field) is False:
            return {
                "ok": False,
                "reason": str(out.get("failed")
                              or f"{command} reported {field}: false, so "
                                 f"the operation did not take effect"),
                "command": command,
                field: False,
            }
    # Per COMMAND where the bridge can say, because after one good
    # session the global flag is true forever and would launder the
    # forty-five unverified commands with the twenty verified ones. The
    # attribute fallback keeps the test fakes working: they declare a
    # plain verified_live and know nothing about the record.
    per_command = getattr(bridge, "verified_live_for", None)
    out["verified_live"] = (per_command(command) if callable(per_command)
                            else bridge.verified_live)
    if cacheable:
        _READ_CACHE[command] = out
    return out


#: Method-name prefixes that mean existing content stops existing.
#:
#: The extension carries the same list, and this is the second copy on
#: purpose. The extension's guard only protects editors running a build
#: that has it, and an older one is exactly the situation where a
#: caller reaches for the generic shim. Refusing here means the
#: protection does not depend on when the user last re-imported.
_DESTRUCTIVE_PREFIX = ("delete", "remove", "clear", "destroy", "reset",
                       "overwrite")

#: Methods that replace content wholesale without a name that says so.
#:
#: Named individually rather than by widening the prefixes: `set` and
#: `import` cover dozens of harmless calls, and a guard that fires on
#: setVisible teaches a caller to pass confirm reflexively, which is
#: worse than no guard. Found by enumerating all 675 methods the
#: runtime exposes and reading the ones that imply replacement.
_DESTRUCTIVE_EXACT = frozenset({
    "setNetlist",                 # replaces the whole connectivity
    "importAutoRouteSesFile",     # replaces all routing
    "importAutoRouteJsonFile",
    "importAutoLayoutJsonFile",   # replaces all PLACEMENT
    # The automatic engines. Each discards existing work and replaces
    # it wholesale, and none carries a destructive-sounding verb.
    #
    # pcb_Document.autoRouting was guarded ONLY at the tool level, by
    # easyeda_auto_route. The generic shim reaches the same method and
    # did not check it, so the documented guarantee that auto-routing
    # requires confirmation was true of one route in and not the other.
    "autoRouting",                # discards routing, re-routes the board
    "autoLayout",                 # rearranges a whole schematic or board
    "restoreDefault",             # resets the editor's own settings
    # Repoint or rename an existing constraint. These do not read as
    # destructive from the name, which is why the prefix list misses
    # them, but the effect is: moving a differential pair onto another
    # net silently re-scopes whatever is already routed under it, and a
    # rename orphans every rule that referenced the old name. Verified
    # present in reference/easyeda-api-skill PCB_Drc.md before listing,
    # since a guard on a method that does not exist protects nothing.
    "modifyDifferentialPairPositiveNet",
    "modifyDifferentialPairNegativeNet",
    "modifyDifferentialPairName",
    "modifyNetClassName",
    "modifyEqualLengthNetGroupName",
})


def _as_list(value, field: str):
    """A list, or a refusal. Never a string treated as a sequence.

    ``list("[1,2]")`` is ``['[', '1', ',', '2', ']']``, and a caller that
    sends JSON as a string is common enough that this is not a hypothetical:
    passing ``args='["0402"]'`` to easyeda_invoke handed the editor EIGHT
    single-character arguments. It did not fail cleanly either. The extra
    arguments hit the never-answering overload of the method and HUNG the
    connection for the whole timeout, so the symptom was a dead editor
    rather than a bad argument.

    Returns the list, or a dict describing the refusal.
    """
    import json as _json

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = _json.loads(text)
        except ValueError:
            return {"ok": False, "reason": (
                f"{field} was a string that is not JSON: {value!r}. Pass a "
                f"list, or a JSON array as text")}
        if not isinstance(parsed, list):
            return {"ok": False, "reason": (
                f"{field} parsed as {type(parsed).__name__}, not a list. "
                f"Pass a list, or a JSON array as text")}
        return parsed
    return {"ok": False, "reason": (
        f"{field} must be a list, and was {type(value).__name__}")}


def _points(value, field: str, minimum: int, allow_segments: bool = False):
    """A list of numeric ``[x, y]`` pairs, or a refusal.

    The single-shape writers took the caller's points straight into
    ``for p in points`` with nothing checked, which fails three ways and
    none of them cleanly:

    * a JSON STRING iterates as characters, so ``p[0]`` is the character
      and ``p[1]`` raises IndexError, surfacing as a traceback rather
      than a reason;
    * an EMPTY list draws a wire with no path, which the editor accepts;
    * a wire whose points are all the same is zero length, invisible,
      and connects nothing while still counting as a wire.

    The bulk variants already validated their input. Only the
    one-at-a-time versions did not, which is the reverse of what one
    would guess.
    """
    items = _as_list(value, field)
    if isinstance(items, dict):
        return items
    # AN ENTRY IS A WHOLE POLYLINE, not necessarily one segment. The
    # official WIRE format documents `dots` as a list of polylines, each
    # a continuous flat run X1 Y1 X2 Y2 X3 Y3 ..., and its own example
    # carries a six-number entry:
    #
    #   "dots": [[310, 550, 400, 550, 400, 460], [480, 460, 400, 460]]
    #
    # Accepting only 2 and 4 rejected any wire with a bend expressed as
    # one entry, including geometry read straight back out of a live
    # sheet. The earlier measurement that entries are four numbers was
    # correct and was the two-point case, not the rule.
    #
    # So: pairs stay exactly two, and a segment run must be even and at
    # least four. An odd count is still refused, since a dangling
    # coordinate means the caller has lost track of the shape.
    out = []
    for index, point in enumerate(items):
        if isinstance(point, str) or not isinstance(point, (list, tuple)):
            return {"ok": False, "reason": (
                f"{field}[{index}] must be an [x, y] pair, and is a "
                f"{type(point).__name__}")}
        width = len(point)
        ok_width = (width == 2) or (
            allow_segments and width >= 4 and width % 2 == 0)
        if not ok_width:
            expected = ("an [x, y] pair, or a flat run of coordinates "
                        "x1 y1 x2 y2 ... with an even count of at least 4"
                        if allow_segments else "an [x, y] pair")
            return {"ok": False, "reason": (
                f"{field}[{index}] has {width} value(s); {expected}")}
        try:
            out.append([float(n) for n in point])
        except (TypeError, ValueError):
            return {"ok": False, "reason": (
                f"{field}[{index}] is not numeric: {point!r}")}

    # Pair form and run form cannot be mixed, but two runs of DIFFERENT
    # lengths are perfectly normal: one bent wire and one straight one.
    # Comparing raw widths would have rejected that.
    pair_form = all(len(p) == 2 for p in out)
    run_form = all(len(p) >= 4 for p in out)
    if not pair_form and not run_form:
        return {"ok": False, "reason": (
            f"{field} mixes [x, y] pairs and flat coordinate runs; "
            f"use one form or the other")}

    # The minimum counts POINTS, and one run already carries at least
    # two of them. Applying the pair-form minimum to runs would reject a
    # single-segment wire, which is the commonest wire there is.
    needed = 1 if run_form else minimum
    if len(out) < needed:
        noun = "polylines" if run_form else "points"
        return {"ok": False, "reason": (
            f"{field} needs at least {needed} {noun} and got {len(out)}")}

    # Zero length: every pair identical, or every segment's two ends
    # identical. Both draw something invisible that connects nothing.
    if pair_form and len(set(map(tuple, out))) == 1:
        return {"ok": False, "reason": (
            f"every point in {field} is the same, so this has zero length "
            f"and would draw something invisible that connects nothing")}
    # A run is degenerate when every coordinate pair within it is the
    # same point, checked across the whole run rather than only its two
    # ends: a three-point run could otherwise start and finish
    # elsewhere while collapsing in the middle.
    def _degenerate(run):
        pts = {(run[i], run[i + 1]) for i in range(0, len(run), 2)}
        return len(pts) == 1
    if run_form and all(_degenerate(p) for p in out):
        return {"ok": False, "reason": (
            f"every polyline in {field} collapses to a single point, so "
            f"this has zero length and connects nothing")}
    return out


def _search_params(query: str, library_uuid: str,
                   page: int = 1, items_of_page: int = 0) -> dict:
    """Parameters for a library search, omitting what was not asked for.

    ``library_uuid`` is the editor's second search argument, measured:
    the system uuid returns the same matches and the personal, project
    and favorite uuids return none for a term only the system library
    holds. Sending an empty one would scope the search to a library
    that does not exist, so it is left out entirely rather than passed
    through blank.
    """
    params: dict = {"query": str(query or "")}
    if str(library_uuid or "").strip():
        params["library_uuid"] = str(library_uuid).strip()
    # Paging is sent only when asked for, so the editor's own default
    # page size stays the default rather than being pinned here.
    if int(page or 1) > 1:
        params["page"] = int(page)
    if int(items_of_page or 0) > 0:
        params["items_of_page"] = int(items_of_page)
    return params


def _destructive_refusal(class_name, method, confirm) -> "dict | None":
    """Refuse a destructive call that was not confirmed, or None."""
    name = str(method or "")
    lowered = name.lower()
    risky = (any(lowered.startswith(p) for p in _DESTRUCTIVE_PREFIX)
             or name in _DESTRUCTIVE_EXACT)
    if not risky or confirm:
        return None
    return {
        "ok": False,
        "reason": (
            f"{class_name}.{name} looks destructive and confirm was not "
            f"given. Pass confirm=true if that is intended. Checked here "
            f"as well as in the extension, because an editor running an "
            f"older build has no guard of its own."),
        "class_name": class_name,
        "method": name,
    }


_LOCAL_BUILD: "list[str | None]" = []


def _local_extension_build() -> "str | None":
    """The build id this tree's extension source would stamp.

    Computed with the BUILDER's own hash rather than a second copy of
    it: two implementations of one hash drift, and the failure is
    invisible because every install then looks stale. Cached because
    it reads a file and cannot change within a process.
    """
    if _LOCAL_BUILD:
        return _LOCAL_BUILD[0]

    import pathlib
    import sys

    result = None
    try:
        root = pathlib.Path(__file__).resolve().parents[3]
        ext = root / "extensions" / "easyeda"
        source = ext / "main.js"
        if source.exists():
            sys.path.insert(0, str(ext))
            from build import build_id            # type: ignore[import]

            result = build_id(source.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        # An installed wheel has no extensions/ directory. Not knowing
        # is reported as None, never as a mismatch.
        result = None
    _LOCAL_BUILD.append(result)
    return result


def _schematic_parts() -> dict[str, Any]:
    """Every schematic part with its PARAMETERS, best source first.

    Two sources carry component parameters and they are not equal.
    ``sch.netlist`` returns a dict keyed by unique id whose ``props``
    hold RESOLVED values: a live board reported ``Designator`` as
    ``U1``, ``Manufacturer`` as ``TDK`` and ``ComponentLink1URL`` as a
    real manufacturer PDF. ``sch.components`` returned, for the one
    part sampled, unresolved templates: a designator of ``*2`` and a
    link of ``=HelpURL``.

    An audit reading templates reports documented parts as
    undocumented, so the netlist is preferred and the component list
    is the fallback. Which one answered is reported, because a result
    means different things depending on the source and a reader should
    not have to guess.

    Returns {ok, source, parts: [{designator, params}]} or the failing
    reply from whichever source was tried.
    """
    netlist = _call("sch.netlist", timeout=60.0)
    if netlist.get("ok"):
        raw = netlist.get("netlist")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        if isinstance(raw, dict) and raw:
            parts = []
            for entry in raw.values():
                if not isinstance(entry, dict):
                    continue
                props = entry.get("props")
                if not isinstance(props, dict):
                    continue
                parts.append({
                    "designator": str(props.get("Designator") or "").strip(),
                    "params": props,
                    # The library DEVICE, which is what "the same part"
                    # means when grouping. Measured in the netlist's
                    # props alongside Symbol and Footprint.
                    "library_uuid": str(props.get("Device") or "").strip(),
                })
            if parts:
                return {"ok": True, "source": "sch.netlist",
                        "verified_live": netlist.get("verified_live"),
                        "parts": parts}

    components = _call("sch.components", timeout=60.0)
    if not components.get("ok"):
        return components
    parts = []
    for item in components.get("components") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("componentType") or "").strip().lower()
        if kind and kind != "part":
            continue          # a sheet is not a component
        props = item.get("otherProperty")
        component = item.get("component")
        parts.append({
            "designator": str(item.get("designator") or "").strip(),
            "params": props if isinstance(props, dict) else {},
            "library_uuid": str(
                (component or {}).get("uuid") or "").strip(),
        })
    return {"ok": True, "source": "sch.components",
            "verified_live": components.get("verified_live"),
            "parts": parts}


def _is_ground_net(net_name: str) -> bool:
    """Ground specifically, not the whole power class.

    ``_is_power_or_ground_net`` answers a different question and would
    put a rail and a ground in the same bucket, which is exactly the
    distinction the orientation check turns on: grounds point one way
    and rails the other.
    """
    if not net_name:
        return False
    upper = net_name.upper()
    return upper in ("GND", "GROUND", "VSS", "AGND", "DGND", "PGND",
                     "EGND", "SGND", "EARTH") or "GND" in upper


def _is_power_or_ground_net(net_name: str) -> bool:
    """Mirror of IsPowerOrGroundNetName in Audit.pas, kept in step so
    the signal-via audit flags the same boards on both backends.

    Heuristic only: plain ground vocabulary, any name containing GND,
    and the V/+/- rail prefixes where the second character is a digit
    or one of C/D/E/B/S (VCC, VDD, VEE, VBAT, VSS, +3V3, -12V). VOUT
    and VIDEO are deliberately signal nets: O and I are outside the
    rail class.
    """
    if not net_name:
        return False
    upper = net_name.upper()
    if upper in ("GND", "GROUND", "VSS", "AGND", "DGND", "PGND",
                 "EGND", "SGND"):
        return True
    if "GND" in upper:
        return True
    if (len(upper) >= 2 and upper[0] in "V+-"
            and (upper[1].isdigit() or upper[1] in "CDEBS")):
        return True
    return False


#: EasyEDA pad shape (the runtime value in a pad's ``pad`` tuple, or the
#: enum member name the extension accepts) -> the shape words the
#: land-pattern audit compares, which are Altium's. An oval is Altium's
#: round with unequal sizes; a regular polygon of eight sides is the
#: octagon. Values from EPCB_PrimitivePadShapeType in the official
#: reference: ELLIPSE, OVAL (member OBLONG), RECT (RECTANGLE), NGON
#: (REGULAR_POLYGON), POLYGON (POLYLINE_COMPLEX_POLYGON).
_PAD_SHAPE_AS_AUDIT = {
    "ELLIPSE": "round", "OVAL": "round", "OBLONG": "round",
    "RECT": "rectangular", "RECTANGLE": "rectangular",
}


def _polygon_extent(source) -> tuple[float, float]:
    """Width and height of a complex-polygon pad's source array.

    The source is flat: numbers and command letters (``x, y, "L", x, y``),
    possibly nested one level for several rings. Arc commands carry an
    angle among the numbers, so this is an estimate from every number
    pair after each letter, good for the axis-aligned outlines pads use.
    """
    flat: list = []

    def walk(v):
        if isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        else:
            flat.append(v)

    walk(source)
    nums: list[float] = []
    for v in flat:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            nums.append(float(v))
    xs, ys = nums[0::2], nums[1::2]
    if not xs or not ys:
        return 0.0, 0.0
    return max(xs) - min(xs), max(ys) - min(ys)


def _easyeda_pad_rows(raw: list) -> list[dict[str, Any]]:
    """Pads read from the editor, as the land-pattern audit's mm rows.

    The measured shape of a pad (``extensions/easyeda/verified.json``,
    ``pcb.pads``): ``padNumber``, ``x``, ``y``, ``rotation``, ``pad`` as a
    tuple ``[shape, w, h(, corner radius)]``, ``hole`` as ``[type, d]`` or
    ``[SLOT, d, length]`` or null, and ``solderMaskAndPasteMaskExpansion``.
    Lengths are mils, the PCB canvas unit, converted here; the expansions
    are read in the same unit, which has not been measured separately.

    Four things this used to get wrong, each of which made a correct
    footprint read as wrong or a wrong one as right: the hole tuple was
    passed whole to a number conversion and read as 0; the shape names
    (RECT, ELLIPSE) never matched the audit's words; the rotation was
    dropped, so a turned pad compared its sizes the wrong way round; and
    a regular polygon's SIDE COUNT was read as its height. Paste and mask
    expansion were not read at all, so the thermal paste check could not
    see a windowed pad.
    """
    from eda_agent.units import MM_PER_MIL

    def mm(value) -> float:
        try:
            return float(value) * MM_PER_MIL
        except (TypeError, ValueError):
            return 0.0

    rows: list[dict[str, Any]] = []
    for pad in raw or []:
        if not isinstance(pad, dict):
            continue
        spec = pad.get("pad")
        shape_raw, width, height, radius = "", 0.0, 0.0, 0.0
        if isinstance(spec, (list, tuple)) and spec:
            shape_raw = str(spec[0] or "").upper()
            if shape_raw in ("NGON", "REGULAR_POLYGON"):
                # [NGON, diameter, sides]: the second number is a count.
                width = height = spec[1] if len(spec) > 1 else 0.0
            elif shape_raw in ("POLYGON", "POLYLINE_COMPLEX_POLYGON"):
                width, height = _polygon_extent(spec[1:])
            elif len(spec) > 2:
                width, height = spec[1], spec[2]
                if len(spec) > 3:
                    radius = spec[3]
            elif len(spec) == 2:
                width = height = spec[1]
        if shape_raw in ("NGON", "REGULAR_POLYGON"):
            sides = spec[2] if isinstance(spec, (list, tuple)) and len(spec) > 2 else 0
            shape = "octagonal" if sides == 8 else f"polygon{sides}"
        elif shape_raw in ("RECT", "RECTANGLE"):
            try:
                rounded = float(radius or 0.0) > 0
            except (TypeError, ValueError):
                rounded = False
            shape = "roundrectangle" if rounded else "rectangular"
        else:
            shape = _PAD_SHAPE_AS_AUDIT.get(shape_raw, shape_raw.lower())

        hole = pad.get("hole")
        hole_d, hole_len, hole_type = 0.0, 0.0, "round"
        if isinstance(hole, (list, tuple)) and len(hole) > 1:
            hole_d = mm(hole[1])
            if str(hole[0]).upper() == "SLOT" and len(hole) > 2:
                hole_type = "slot"
                hole_len = mm(hole[2])
        elif isinstance(hole, (int, float)) and not isinstance(hole, bool):
            hole_d = mm(hole)

        exp = pad.get("solderMaskAndPasteMaskExpansion")
        paste_src, paste_mm, mask_src, mask_mm = "rule", 0.0, "rule", 0.0
        if isinstance(exp, dict):
            if exp.get("topPasteMask") is not None:
                paste_src, paste_mm = "manual", mm(exp.get("topPasteMask"))
            if exp.get("topSolderMask") is not None:
                mask_src, mask_mm = "manual", mm(exp.get("topSolderMask"))

        try:
            rotation = float(pad.get("rotation") or 0.0)
        except (TypeError, ValueError):
            rotation = 0.0
        rows.append({
            "name": str(pad.get("padNumber") or pad.get("number") or ""),
            "x_mm": mm(pad.get("x")),
            "y_mm": mm(pad.get("y")),
            "w_mm": mm(width),
            "h_mm": mm(height),
            "shape": shape,
            "rotation": rotation,
            "hole_mm": hole_d,
            "hole_w_mm": hole_len,
            "hole_type": hole_type,
            "plated": pad.get("metallization", True) is not False,
            "paste_expansion_mm": paste_mm,
            "paste_expansion_source": paste_src,
            "mask_expansion_mm": mask_mm,
            "mask_expansion_source": mask_src,
        })
    return rows


def register_easyeda_tools(mcp):
    """Register the EasyEDA-native tools."""

    @mcp.tool()
    async def easyeda_ping() -> dict[str, Any]:
        """Is an EasyEDA editor connected, and to which document?

        The editor dials out to this server rather than being driven by
        it, so a failure here usually means the extension is not running
        rather than that EasyEDA is closed. The reply names the current
        document kind, because a PCB command aimed at a schematic tab is
        meaningless and finding that out from a confusing error is worse
        than being told.

        It also reports whether the editor is running THIS tree's build
        of the extension. That is not housekeeping: an extension that
        is installed, enabled and months old is indistinguishable from
        a current one in EasyEDA's Extensions Manager, same name, same
        uuid, same size. A whole session was once spent reading "the
        export fix is broken" off an editor that was simply running a
        build from before the fix.

        Returns:
            The editor's reply plus ``build_matches`` (True, False, or
            None when either side cannot say) and, when it does not
            match, ``build_warning`` naming both builds and the remedy.
        """
        reply = _call("system.ping", timeout=5.0)
        if not reply.get("ok"):
            return reply

        # Names the expected version and the file to import, and
        # repeats the permission the editor needs before it will open a
        # connection at all.
        from ..bridge.easyeda_expected import check as _version_check

        reply.update(_version_check(reply.get("build")))

        expected = _local_extension_build()
        reported = reply.get("build")
        if not expected or not reported:
            # Never guess from silence: an extension predating the
            # stamp reports no build, and calling that stale would cry
            # wolf on every one of them.
            reply["build_matches"] = None
        elif reported == expected:
            reply["build_matches"] = True
        else:
            reply["build_matches"] = False
            reply["build_warning"] = (
                f"the editor is running extension build {reported}, but "
                f"this server's tree builds {expected}. Anything you "
                f"test is the OLDER code. Rebuild with "
                f"extensions/easyeda/build.py and re-import the .eext; "
                f"re-importing the same version number is a silent "
                f"no-op, so bump the version in extension.json first.")

        # Which runtimes are reachable, not just which one answered.
        # EasyEDA gives each document type its own extension host, so
        # "connected" was never the question a caller actually has:
        # with only a PCB tab open every sch_* call fails, and the
        # reply here said `document: "pcb"` and left the reader to
        # infer the rest. Now it says what is missing and why.
        try:
            from ..bridge import easyeda_bridge as _eb

            status = _eb.get_easyeda_bridge().status()
        except Exception:                              # noqa: BLE001
            return reply

        contexts = status.get("editor_contexts") or []
        reply["editors_connected"] = status.get("editors_connected")
        reply["editor_contexts"] = contexts
        if status.get("editors_connected") == 1:
            reply["single_runtime_note"] = (
                "one editor runtime is connected, so only the commands "
                "that document type provides will work. Open the other "
                "document (a schematic alongside a PCB, or the reverse) "
                "and connect it too if a whole sch-to-PCB flow is "
                "wanted; the bridge keeps both and routes by namespace.")
        return reply

    @mcp.tool()
    async def easyeda_get_capabilities() -> dict[str, Any]:
        """Which parts of the EasyEDA API exist in the CURRENT context.

        EasyEDA loads its API per document type: its own pro-api
        manifest declares separate services for default, sch, symbol,
        pcb and panel. On the start page only the reduced default
        surface is present, so every pcb_* and sch_* class is undefined
        and every read fails with "Cannot read properties of
        undefined". Those failures look like defects in this project
        and are not.

        Read this FIRST when commands fail in a way that makes no
        sense. One call reports which classes the editor actually
        injected and what each can do, which sixty-four failing probes
        can only hint at.

        The answer depends on the open document, so it is a measurement
        of this moment rather than a property of the editor.
        """
        return _call("system.capabilities", timeout=30.0)

    @mcp.tool()
    async def easyeda_get_components() -> dict[str, Any]:
        """Every component placed on the current PCB.

        Designator, footprint, layer, position and rotation as the
        editor reports them.
        """
        return _call("pcb.components")

    @mcp.tool()
    async def easyeda_get_schematic_components() -> dict[str, Any]:
        """Every component on the current schematic.

        Separate from the PCB list on purpose: the two disagree while a
        design is mid-edit, and that disagreement is a finding rather
        than something to paper over by merging them.

        COUNTED BY KIND, because the raw number misleads on a
        hierarchical design. A live top page returned five rows for a
        111-part design: one page FRAME (reported as componentType
        "sheet", which is not a child sheet), two ordinary parts, and
        two block symbols standing in for child schematics. A reviewer
        reading "5 components" against a netlist of 111 has no way to
        tell a hierarchy from a broken read.

        Every row is still returned. The blocks and the frame are real
        objects a caller may want; what changes is that the counts now
        say which is which.
        """
        reply = _call("sch.components")
        if not reply.get("ok"):
            return reply

        rows = [c for c in (reply.get("components") or [])
                if isinstance(c, dict)]
        kinds: dict[str, int] = {}
        for row in rows:
            kind = str(row.get("componentType") or "unknown").strip().lower()
            kinds[kind] = kinds.get(kind, 0) + 1

        reply["count_by_type"] = kinds
        reply["part_count"] = kinds.get("part", 0)
        reply["block_count"] = kinds.get("block_symbol", 0)
        if kinds.get("block_symbol"):
            reply["hierarchy_note"] = (
                f"this page carries {kinds['block_symbol']} hierarchical "
                f"block(s), so most parts live in child schematics and "
                f"are NOT in this list. The netlist is flattened and "
                f"has them; easyeda_get_schematic_hierarchy names the "
                f"blocks")
        return reply

    @mcp.tool()
    async def easyeda_get_nets() -> dict[str, Any]:
        """Nets in the design, with the pins on each.

        The board read is tried first, then the schematic netlist.

        THE SCHEMATIC PATH EXISTS BECAUSE THE OBVIOUS ONE RETURNS
        NOTHING. Measured on a schematic with 332 connected
        pins: sch_Net.getAllNets, getAllNetsName and
        getCurrentProjectAllNets each returned an EMPTY LIST. Not an
        error, not a refusal, just zero, which reads as a design with
        no nets and is the quietest way to be wrong.

        The netlist has them: every part carries pin number to net
        name, so the nets and their members can be rebuilt exactly.
        That also gives something the raw net list would not, the pins
        on each net, which is what a fanout or single-pin check needs.
        """
        board = _call("pcb.nets", timeout=60.0)
        if board.get("ok"):
            nets = board.get("nets")
            if isinstance(nets, list) and nets:
                board["source"] = "pcb.nets"
                board["net_count"] = len(nets)
                return board

        reply = _call("sch.netlist", timeout=60.0)
        if not reply.get("ok"):
            # Neither source answered. Return the BOARD failure, since
            # that is the one a caller on a PCB was asking about.
            return board if not board.get("ok") else reply

        raw = reply.get("netlist")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        if not isinstance(raw, dict):
            return board

        members: dict[str, list] = {}
        for entry in raw.values():
            if not isinstance(entry, dict):
                continue
            props = entry.get("props") if isinstance(entry.get("props"),
                                                     dict) else {}
            designator = str(props.get("Designator") or "").strip()
            pins = entry.get("pins")
            if not isinstance(pins, dict):
                continue
            for number, net in pins.items():
                name = str(net or "").strip()
                if not name:
                    continue
                members.setdefault(name, []).append(
                    f"{designator or '?'}.{number}")

        nets = [{"name": name, "pin_count": len(pins),
                 "pins": sorted(pins)}
                for name, pins in sorted(members.items())]
        single = [n["name"] for n in nets if n["pin_count"] < 2]
        out = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "source": "sch.netlist",
            "nets": nets,
            "net_count": len(nets),
            "single_pin_nets": single,
            "note": ("rebuilt from the netlist because the schematic's "
                     "own net reads return an empty list"),
        }
        out.update(_schematic_scope())
        return out

    @mcp.tool()
    async def easyeda_get_net_length(net: str = "") -> dict[str, Any]:
        """Routed length of one net, as the editor measures it.

        Read from the editor rather than computed here: a length this
        project calculated could disagree with what the user sees, and
        two numbers for one net is worse than one.

        A reply carrying no length is a FAILURE here, not a success
        with a missing field. Measured on a schematic tab, where net
        reads answer from either document: the editor returned the net
        name and nothing else, and passing that through as ``ok`` left
        a caller reading ``length`` as absent. Absent is one careless
        line away from being treated as zero, and zero length is the
        definition of an unrouted net.

        Args:
            net: exact net name. Required; an empty name is refused
                rather than defaulting to some net.
        """
        if not str(net).strip():
            return {"ok": False, "reason": "net is required"}
        reply = _call("pcb.net_length", {"net": net})
        if not reply.get("ok"):
            return reply
        value = reply.get("length")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return {
                "ok": False,
                "reason": (
                    f"the editor returned no length for {net!r}. That is "
                    f"not a length of zero: it means the net was not "
                    f"measured. A schematic tab answers net reads without "
                    f"a board to measure, so open the PCB"),
                "net": net,
                "command": "pcb.net_length",
            }
        return reply

    @mcp.tool()
    async def easyeda_highlight_net(net: str = "") -> dict[str, Any]:
        """Highlight one net in the editor, to show a human where it is.

        Args:
            net: exact net name.
        """
        if not str(net).strip():
            return {"ok": False, "reason": "net is required"}
        return _call("pcb.highlight_net", {"net": net})

    @mcp.tool()
    async def easyeda_get_net_classes() -> dict[str, Any]:
        """Net classes defined on the current PCB."""
        return _call("pcb.net_classes")

    @mcp.tool()
    async def easyeda_get_differential_pairs() -> dict[str, Any]:
        """Differential pairs the editor knows about.

        The editor's own list, not this project's naming-based
        inference, so it reflects what the router will actually treat
        as a pair.
        """
        return _call("pcb.differential_pairs")

    @mcp.tool()
    async def easyeda_invoke(
        class_name: str,
        method: str,
        args: Optional[list[Any]] = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Call any EasyEDA API method directly.

        The escape hatch, and the reason new capability no longer costs
        an extension re-import. Everything else here is a named tool
        over one or two of these calls; this reaches the rest of the 92
        classes without new extension code.

        WHAT IT IS FOR: measuring. A method whose arguments or reply
        shape nobody has seen can be tried here, once, and what it
        answers decides whether a proper tool is worth writing. Several
        features currently rest on a guessed reply shape precisely
        because there was no way to ask.

        WHAT IT IS NOT FOR: routine work. A named tool validates its
        arguments, reports what the editor answered rather than
        assuming, and carries the accumulated knowledge of what goes
        wrong. This has none of that, so anything worth doing twice is
        worth a tool.

        DESTRUCTIVE METHODS NEED confirm. Any method starting delete,
        remove, clear, destroy or reset is refused without it, on both
        sides. Otherwise this would be a hole straight through every
        confirm guard in the surface.

        The value comes back exactly as the editor gave it, including
        null and false. Those are how this API declines, and reading a
        falsey answer as success is the single most common bug this
        backend has produced.

        Args:
            class_name: an API class, e.g. "pcb_Layer". Call
                easyeda_get_capabilities for what exists in the current
                document.
            method: the method on it, e.g. "getAllLayers".
            args: positional arguments, in order.
            confirm: required for a destructive-looking method.
        """
        if not str(class_name or "").strip():
            return {"ok": False, "reason": "class_name is required"}
        if not str(method or "").strip():
            return {"ok": False, "reason": "method is required"}
        refusal = _destructive_refusal(class_name, method, confirm)
        if refusal:
            return refusal
        arg_list = _as_list(args, "args")
        if isinstance(arg_list, dict):
            return arg_list
        params: dict[str, Any] = {
            "class_name": class_name, "method": method,
            "args": arg_list,
        }
        if confirm:
            params["confirm"] = True
        return _call("system.invoke", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_invoke_batch(
        calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Several easyeda_invoke calls in one round trip.

        What makes the generic path usable for real work. A per-item
        loop over a hundred primitives is a hundred round trips; this
        is one, and the editor runs them in order.

        Each result is reported individually. A failure in the middle
        does not lose the answers either side of it, because a partial
        result naming what failed is usable and an exception is not.

        Args:
            calls: a list of ``{"class_name", "method", "args",
                "confirm"}`` dicts, same fields as easyeda_invoke.
        """
        call_list = _as_list(calls, "calls")
        if isinstance(call_list, dict):
            return call_list
        if not call_list:
            return {"ok": False, "reason": "calls must not be empty"}
        cleaned = []
        for index, call in enumerate(call_list):
            if not isinstance(call, dict):
                # Reached only for a genuine non-object entry now. A JSON
                # STRING used to land here as its first character, and
                # "call 0 is not an object" then pointed at the caller's
                # data instead of at the type of the argument.
                return {"ok": False,
                        "reason": f"call {index} is not an object, it is a "
                                  f"{type(call).__name__}"}
            if not str(call.get("class_name") or "").strip():
                return {"ok": False,
                        "reason": f"call {index} has no class_name"}
            if not str(call.get("method") or "").strip():
                return {"ok": False,
                        "reason": f"call {index} has no method"}
            refusal = _destructive_refusal(
                call["class_name"], call["method"],
                call.get("confirm") is True)
            if refusal:
                # Refuse the WHOLE batch rather than running the calls
                # around it. The editor executes a batch in order, so a
                # partial run would leave the design half changed with
                # no record of where it stopped.
                refusal["reason"] = (f"call {index}: {refusal['reason']}")
                return refusal
            entry_args = _as_list(call.get("args"), f"call {index} args")
            if isinstance(entry_args, dict):
                return entry_args
            entry = {"class_name": call["class_name"],
                     "method": call["method"],
                     "args": entry_args}
            if call.get("confirm") is True:
                entry["confirm"] = True
            cleaned.append(entry)
        return _call("system.batch", {"calls": cleaned}, timeout=180.0)

    @mcp.tool()
    async def easyeda_get_layers() -> dict[str, Any]:
        """Layers on the current PCB."""
        return _call("pcb.layers")

    @mcp.tool()
    async def easyeda_list_boards() -> dict[str, Any]:
        """Every PCB in the open project, with the current one marked."""
        return _call("pcb.list_boards")

    @mcp.tool()
    async def easyeda_get_netlist() -> dict[str, Any]:
        """The schematic netlist, parsed.

        The editor returns this as a JSON STRING, measured on a live
        schematic, not as an object. Handing that through unchanged
        meant every caller received one long string where it expected a
        mapping: no iteration, no lookup, and no error either, because
        a string is perfectly valid JSON to whatever received it.

        The parsed shape, measured: keyed by a component's uniqueId,
        each entry carrying ``props`` (Designator, Device, Footprint,
        Datasheet and the rest) and ``pins`` as pin number to net name.

        ``netlist_raw`` keeps the original string, since the export
        tools deal in file content and a caller writing it out should
        not have to re-serialise what the editor already formatted.
        """
        reply = _call("sch.netlist", timeout=60.0)
        if not reply.get("ok"):
            return reply

        raw = reply.get("netlist")
        if not isinstance(raw, str):
            # Already an object, or absent. Either way there is nothing
            # to parse and the reply passes through as it arrived.
            return reply

        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            return dict(reply, ok=False, reason=(
                f"the netlist did not parse as JSON: {exc}"),
                netlist_raw=raw)

        out = dict(reply)
        out["netlist"] = parsed
        out["netlist_raw"] = raw
        if isinstance(parsed, dict):
            out["part_count"] = len(parsed)
        return out

    def _checker_result(reply: dict, what: str,
                        cls: str) -> dict[str, Any]:
        """A check that did not produce a report is a FAILURE, not a pass.

        Two layers, deliberately.

        The extension answers ``ran: false`` with a reason when the
        checker hands it something it cannot enumerate. Passing that
        through as ``ok`` put the worst sentence in a review in front of
        a reader: zero violations, from a check that reported none.

        The second layer is here because the first only exists in
        extension builds that carry the fix, and an editor running an
        older one still says ``ran: true``. So a reported CLEAN result
        is confirmed by asking the checker again through the reflective
        shim and looking at its raw answer.

        The measurement behind it was that the schematic checker returns
        the boolean ``false``, which is a status and not an empty
        violation list. That was true, and the cause was the call rather
        than the editor: ``check`` is overloaded on its third argument,
        and the zero-argument form asks for the boolean. Both the
        handler and this re-check now request the array form, so a
        boolean arriving here means an editor genuinely answering with
        a status, which is still not a clean bill of health.

        Only a clean result is re-checked. Violations that came back
        populated are self-evidently a real report.
        """
        if not reply.get("ok"):
            return reply
        if reply.get("ran") is not True:
            return {
                "ok": False,
                "reason": str(reply.get("failed")
                              or f"the {what} check did not run"),
                "ran": False,
                "verified_live": reply.get("verified_live"),
            }
        if reply.get("violation_count"):
            return reply

        # THE SAME ARGUMENTS THE HANDLER USES, or this proves nothing.
        # check() is overloaded on its third argument: false returns a
        # boolean, true returns an array. Sending [] selects the boolean
        # overload, so this re-check would ALWAYS see a boolean and
        # always overturn a genuinely clean board. That was masked while
        # the handler also sent no arguments, because the run never got
        # this far; correcting the handler exposed it.
        raw = _call("system.invoke",
                    {"class_name": cls, "method": "check",
                     "args": [True, False, True]},
                    timeout=120.0)
        if not raw.get("ok"):
            # The shim could not be reached, so the clean result stands
            # unconfirmed rather than being overturned on no evidence.
            reply["confirmation"] = (
                f"could not re-read the {what} result to confirm it: "
                f"{raw.get('reason')}")
            return reply
        value = raw.get("value")
        if isinstance(value, bool):
            return {
                "ok": False,
                "reason": (
                    f"the {what} checker answered with the boolean "
                    f"{str(value).lower()} rather than a report, so no "
                    f"violation list exists. This is NOT a clean result: "
                    f"nothing was enumerated. Run the check from the "
                    f"editor's own interface to see its findings."),
                "ran": False,
                "raw_answer": value,
                "verified_live": reply.get("verified_live"),
            }
        reply["confirmed_report_type"] = type(value).__name__
        return reply

    @mcp.tool()
    async def easyeda_run_drc() -> dict[str, Any]:
        """Run the editor's own design rule check and read the result.

        The editor's checker, never a reimplementation. A second opinion
        that disagreed with the tool the user is looking at would be
        worse than no opinion.

        A check that produced no report is reported as a FAILURE. Zero
        violations is only good news when something was actually
        examined.
        """
        return _checker_result(
            _call("design.run_drc", timeout=120.0),
            "DRC", "pcb_Drc")

    @mcp.tool()
    async def easyeda_run_erc() -> dict[str, Any]:
        """Run the editor's own electrical rule check and read it back.

        A check that produced no report is reported as a FAILURE rather
        than as a clean schematic. Measured on a live editor: the
        schematic checker answers with the boolean ``false``, which is
        not a violation list, and treating it as an empty one announced
        a clean bill of health nothing had established.
        """
        return _checker_result(
            _call("design.run_erc", timeout=120.0),
            "ERC", "sch_Drc")

    @mcp.tool()
    async def easyeda_get_vias() -> dict[str, Any]:
        """Vias on the current PCB."""
        return _call("pcb.vias")

    @mcp.tool()
    async def easyeda_get_lines() -> dict[str, Any]:
        """Line primitives (tracks and graphics) on the current PCB."""
        return _call("pcb.lines")

    @mcp.tool()
    async def easyeda_get_pads() -> dict[str, Any]:
        """Pads on the current PCB."""
        return _call("pcb.pads")

    @mcp.tool()
    async def easyeda_save() -> dict[str, Any]:
        """Save the current PCB document."""
        return _call("pcb.save", timeout=60.0)

    @mcp.tool()
    async def easyeda_clear_routing(confirm: bool = False) -> dict[str, Any]:
        """Remove existing routing from the current PCB.

        DESTRUCTIVE, and not undoable through this channel. Refused
        unless ``confirm`` is true, the same guard the Altium side puts
        on a delete-everything call: an agent that can erase every track
        by accident eventually will.

        Args:
            confirm: must be true for anything to happen.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "clear_routing removes existing routing and is not "
                "undoable from here. Pass confirm=True if that is "
                "intended.")}
        return _call("pcb.clear_routing", {"confirm": True}, timeout=120.0)

    @mcp.tool()
    async def easyeda_get_primitives_in_region(
        x1: float = 0.0, y1: float = 0.0,
        x2: float = 0.0, y2: float = 0.0,
    ) -> dict[str, Any]:
        """Everything inside a rectangle on the current PCB.

        The way to inspect one area without pulling the whole board
        across the link. A zero-area rectangle is refused rather than
        returning an empty list, because "nothing is there" and "you
        asked about nothing" are different answers.

        Args:
            x1, y1: one corner.
            x2, y2: the opposite corner.
        """
        if x1 == x2 or y1 == y2:
            return {"ok": False, "reason": (
                "the region has zero width or height; an empty result "
                "would be indistinguishable from an empty area")}
        return _call("pcb.primitives_in_region",
                     {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    @mcp.tool()
    async def easyeda_navigate(x: float = 0.0, y: float = 0.0) -> dict[str, Any]:
        """Scroll the editor to a coordinate, to show a human where.

        Pairs with a violation or a net query: report the finding, then
        put it on screen.

        Args:
            x: x to centre on, in mils. A PCB coordinate, so it takes no
                unit conversion, unlike everything schematic here.
            y: y to centre on, in mils.
        """
        return _call("pcb.navigate", {"x": x, "y": y})

    @mcp.tool()
    async def easyeda_auto_route(confirm: bool = False) -> dict[str, Any]:
        """Run the editor's autorouter over the current PCB.

        Replaces existing routing decisions across the board, so it is
        refused unless ``confirm`` is true. Not destructive the way
        clearing routing is, but it overwrites human work wholesale and
        an agent should have to mean it.

        KNOWN NOT TO WORK on EasyEDA Pro with pro-api 0.2.29:
        ``pcb_Document.autoRouting`` is declared in the published types
        and absent at runtime, measured and matching
        upstream pro-api-sdk issue #28 (the method is @alpha and
        unimplemented). The call fails as "is not a function". Kept
        rather than removed so an editor update that implements it
        starts working here without a change; the capability guard in
        tests flags the day that happens.

        Args:
            confirm: must be true for anything to happen.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "auto_route replaces existing routing decisions across "
                "the board. Pass confirm=True if that is intended.")}
        return _call("pcb.auto_route", {"confirm": True}, timeout=600.0)

    @mcp.tool()
    async def easyeda_create_net_port(
        name: str, x: float, y: float,
        direction: str = "BI", rotation: float = 0.0, mirror: bool = False,
    ) -> dict[str, Any]:
        """Place a net port on the schematic, at a point.

        A sheet-level connector. For a power or ground rail glyph use
        ``easyeda_create_net_flag`` instead: EasyEDA treats those as a
        different object, and a port standing in for a rail symbol
        connects correctly while reading wrong to anyone used to the
        convention, so nothing catches it.

        Args:
            name: the net the port carries.
            x: placement x, in mils.
            y: placement y, in mils.
            direction: IN, OUT or BI.
            rotation: degrees.
            mirror: mirror the glyph.
        """
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}
        return _call("sch.create_net_port", {
            "name": name, "x": _sch(x), "y": _sch(y),
            "direction": direction, "rotation": rotation, "mirror": mirror,
        })

    @mcp.tool()
    async def easyeda_create_net_flag(
        name: str, x: float, y: float, kind: str = "Power",
        rotation: float = 0.0, mirror: bool = False,
    ) -> dict[str, Any]:
        """Place a power or ground rail glyph on the schematic.

        Args:
            name: the net the glyph carries, e.g. GND or V3V3.
            x: placement x, in mils.
            y: placement y, in mils.
            kind: Power, Ground, AnalogGround or ProtectGround. Analog
                ground is a separate glyph on purpose: drawing AGND with
                the plain ground symbol loses the distinction the
                schematic exists to show.
            rotation: degrees.
            mirror: mirror the glyph.
        """
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}
        return _call("sch.create_net_flag", {
            "name": name, "x": _sch(x), "y": _sch(y), "kind": kind,
            "rotation": rotation, "mirror": mirror,
        })

    @mcp.tool()
    async def easyeda_get_arcs() -> dict[str, Any]:
        """Arc primitives on the current PCB."""
        return _call("pcb.arcs")

    @mcp.tool()
    async def easyeda_get_regions() -> dict[str, Any]:
        """Filled regions (copper pours) on the current PCB."""
        return _call("pcb.regions")

    @mcp.tool()
    async def easyeda_get_schematic_wires() -> dict[str, Any]:
        """Wires on the current schematic."""
        return _call("sch.wires")

    @mcp.tool()
    async def easyeda_get_attributes() -> dict[str, Any]:
        """Text attributes on the current PCB.

        Designators, values and free text. What silkscreen audits read,
        and where placeholder values hide.
        """
        return _call("pcb.attributes")

    @mcp.tool()
    async def easyeda_get_schematic_attributes() -> dict[str, Any]:
        """Text attributes on the current schematic."""
        return _call("sch.attributes")

    @mcp.tool()
    async def easyeda_get_dimensions() -> dict[str, Any]:
        """Dimension objects on the current PCB.

        What a fabricator reads off the drawing, so a board whose
        dimensions disagree with its outline is worth catching here.
        """
        return _call("pcb.dimensions")

    @mcp.tool()
    async def easyeda_create_net_label(
        name: str, x: float, y: float,
    ) -> dict[str, Any]:
        """Place a net label on the schematic, at a point.

        The coordinates are not decoration. A label connects whatever it
        sits on, so one placed away from a pin names a net that touches
        nothing, and the schematic still looks labelled.

        Args:
            name: the net the label carries.
            x: placement x, in mils. Put it at the pin.
            y: placement y, in mils.
        """
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}
        return _call("sch.create_net_label", {
            "name": name, "x": _sch(x), "y": _sch(y),
        })

    @mcp.tool()
    async def easyeda_link_3d_model(
        uuid: str = "", library_uuid: str = "",
        model_uuid: str = "", model_library_uuid: str = "",
        footprint_uuid: str = "", symbol_uuid: str = "",
    ) -> dict[str, Any]:
        """Bind a 3D model, footprint or symbol to a device.

        The EasyEDA counterpart to ``lib_link_3d_model`` on the Altium
        side. ``lib_Device.modify`` takes an ``association`` object
        carrying ``model3D``, ``footprint`` and ``symbol``, each as a
        uuid plus the library holding it, and that is how a 3D model
        reaches a part here.

        Recorded previously as a gap on the assumption the extension API
        might not expose it. It does, and ``LIB_3DModel`` carries a full
        create, get, search, modify, copy and delete surface besides.
        Find a model with ``easyeda_search_3d_models``.

        ONLY WHAT YOU NAME IS SENT. The footprint and model slots are
        explicitly nullable in the API, so passing a blank one would
        UNBIND that part while reporting success. Omitted arguments are
        left out of the call entirely rather than sent empty.

        Args:
            uuid: the device to bind to.
            library_uuid: the library holding the device. Also the
                default library for any binding whose own library is
                not given.
            model_uuid: the 3D model to attach.
            model_library_uuid: its library, if it differs.
            footprint_uuid: rebind the footprint, in the device's
                library unless another is implied.
            symbol_uuid: rebind the symbol, likewise.

        Returns:
            Dict with ``linked`` and ``bound``, the slots written.
        """
        if not str(uuid).strip():
            return {"ok": False, "reason": "uuid is required"}
        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required: lib_Device.modify takes the "
                "device's library as its second argument")}

        params: dict[str, Any] = {"uuid": uuid, "library_uuid": library_uuid}
        if str(model_uuid).strip():
            params["model_3d"] = {
                "uuid": model_uuid,
                "library_uuid": (str(model_library_uuid).strip()
                                 or library_uuid),
            }
        if str(footprint_uuid).strip():
            params["footprint"] = {"uuid": footprint_uuid}
        if str(symbol_uuid).strip():
            params["symbol"] = {"uuid": symbol_uuid}
        if len(params) == 2:
            return {"ok": False, "reason": (
                "give model_uuid, footprint_uuid or symbol_uuid. An empty "
                "association would report success while binding nothing")}
        return _call("lib.link_device_parts", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_search_3d_models(
        query: str = "", library_uuid: str = "", page: int = 1,
        items_of_page: int = 0,
    ) -> dict[str, Any]:
        """Search the editor's 3D model library.

        A footprint without a model leaves a hole in the assembled view,
        which is where mechanical clashes are actually spotted.

        TEN PER PAGE, AND THERE ARE MORE PAGES. Ten is the editor's
        default page size, not a ceiling: the documented signature
        carries ``itemsOfPage`` and ``page``, which this backend never
        used to pass. ``more_likely`` in the reply means a full page
        came back, and ``next_page`` says which page to ask for.

        Args:
            query: Substring matched by the editor's own search. Empty
                returns the editor's default first page rather than
                nothing.
            library_uuid: Restrict to one library. Read the reachable
                uuids from ``easyeda_list_libraries``.

        Returns:
            Dict with ``models``, ``result_count``, ``page``,
            ``capped`` and ``library_uuid``.
        """
        return _call("lib.search_3d_models",
                     _search_params(query, library_uuid, page,
                                    items_of_page))

    @mcp.tool()
    async def easyeda_get_paths() -> dict[str, Any]:
        """Where the editor keeps documents, projects and libraries.

        Read from the editor rather than inferred. The extension runs
        sandboxed and the server has no way to know these, so a guessed
        path is how an export ends up somewhere nobody looks.
        """
        return _call("sys.paths")

    @mcp.tool()
    async def easyeda_open_document(uuid: str = "") -> dict[str, Any]:
        """Open a document in the editor by uuid.

        Args:
            uuid: from a schematic, PCB or project listing.
        """
        if not str(uuid).strip():
            return {"ok": False, "reason": "uuid is required"}
        reply = _call("editor.open_document", timeout=60.0,
                      params={"uuid": uuid})
        if not reply.get("ok") or reply.get("opened") is False:
            return reply
        if reply.get("ready") is not None:
            # A build that waits for readability itself. Nothing to add.
            return reply

        # Wait for the document to become readable, for extension
        # builds that do not wait themselves.
        #
        # openDocument returns before the document can answer reads, so
        # a read issued immediately afterwards times out while the same
        # read a moment later succeeds.
        #
        # Polling ping is cheap and asks the one question that requires
        # the document to be loaded. The outcome is reported, because
        # opened but not readable is a distinct state that must not be
        # returned as a plain success.
        import time as _time

        deadline = _time.time() + 6.0
        kind = None
        while _time.time() < deadline:
            probe = _call("system.ping", timeout=8.0)
            kind = str(probe.get("document") or "") if probe.get("ok") else ""
            if kind in ("pcb", "schematic"):
                break
            _time.sleep(0.25)
        reply["ready"] = kind in ("pcb", "schematic")
        reply["document"] = kind
        if not reply["ready"]:
            reply["warning"] = (
                f"the document opened but reports kind {kind!r}, so a "
                f"read now may hang. Retry, or open it in the editor")
        return reply

    @mcp.tool()
    async def easyeda_activate_document(tab_id: str = "") -> dict[str, Any]:
        """Switch to an already-open TAB, by its tab id.

        Different from opening a document: this brings a tab that is
        already there to the front, and it is how a session moves
        between the schematic and the board.

        That matters more here than it would elsewhere. EasyEDA serves a
        different API depending on which document is focused, so most
        pcb_* reads answer nothing at all from a schematic tab and the
        reverse. Switching first is the fix for a whole family of
        "returned nothing" results.

        A TAB ID IS NOT A DOCUMENT UUID, and nothing converts one to the
        other. ``easyeda_open_document`` returns ``tab_id``, which is
        the only place in the API one can be obtained; if you have only
        a uuid, open the document instead. This previously passed a
        document uuid, which the call does not accept.

        Args:
            tab_id: from ``easyeda_open_document``.
        """
        if not str(tab_id).strip():
            return {"ok": False, "reason": (
                "tab_id is required: activateDocument takes a tab id, not "
                "a document uuid, and nothing converts between them. "
                "easyeda_open_document returns tab_id")}
        return _call("editor.activate_document", timeout=60.0,
                     params={"tab_id": tab_id})

    @mcp.tool()
    async def easyeda_mark_findings(
        markers: list | str | None = None,
        color: dict | str | None = None,
        line_width: float = 0.0,
        zoom: bool = False,
        tab_id: str = "",
    ) -> dict[str, Any]:
        """Draw review findings ON the board, where the reader is looking.

        The thing a review has been unable to do on this backend: point
        at the defect instead of describing where it is. Altium
        highlights violations in the editor; this is EasyEDA's
        equivalent, and it was left unbuilt while its arguments were
        unknown.

        AN OVERLAY, NOT GEOMETRY. Markers draw on top of the design,
        change nothing in it, and ``easyeda_clear_findings`` removes
        them all. That is why this is not confirm-guarded when
        everything else that draws is.

        Each marker needs the fields ITS OWN TYPE uses, and the wrong
        ones are refused rather than sent, because a marker the editor
        accepts and draws in the wrong place is a review pointing
        confidently at nothing:

        * ``point``     x, y
        * ``line``      startX, startY, endX, endY
        * ``rectangle`` left, right, top, bottom
        * ``circle``    x, y, r
        * ``arc``       startX, startY, endX, endY, angle

        Args:
            markers: list of shape dicts, each with ``type`` plus its
                fields. A JSON string is accepted.
            color: ``{r, g, b, alpha}``, 0-255 with alpha 0-1. The
                editor's default is used when omitted.
            line_width: stroke width in mils. Editor default when 0.
            zoom: frame the markers after drawing them.
            tab_id: which tab to draw on, from
                ``easyeda_open_document``. Defaults to the active one.

        Returns:
            Dict with ``marked`` and ``count``.
        """
        shapes = _as_list(markers, "markers")
        if isinstance(shapes, dict):
            return shapes
        if not shapes:
            return {"ok": False, "reason": (
                "markers is required: a non-empty list of shapes, each "
                "with a type of point, line, rectangle, circle or arc")}

        params: dict[str, Any] = {"markers": shapes, "zoom": bool(zoom)}
        if isinstance(color, str) and color.strip():
            parsed = _as_list(color, "color")
            if isinstance(parsed, dict):
                return parsed
        if isinstance(color, dict):
            params["color"] = color
        if line_width and float(line_width) > 0:
            params["line_width"] = float(line_width)
        if str(tab_id).strip():
            params["tab_id"] = str(tab_id).strip()
        return _call("editor.mark_findings", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_clear_findings(tab_id: str = "") -> dict[str, Any]:
        """Remove every marker drawn by ``easyeda_mark_findings``.

        Clears the whole overlay rather than one marker: the API
        removes them as a set and offers no per-marker handle.

        Args:
            tab_id: which tab. Defaults to the active one.
        """
        params = {}
        if str(tab_id).strip():
            params["tab_id"] = str(tab_id).strip()
        return _call("editor.clear_findings", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_zoom_to_all() -> dict[str, Any]:
        """Frame everything in the current document.

        Worth doing before easyeda_render_image: the render captures
        what the editor is showing, so a zoomed-in view produces a
        picture of one corner and nothing says the rest was cropped.
        """
        return _call("editor.zoom_to_all", timeout=30.0)

    @mcp.tool()
    async def easyeda_zoom_to_selection() -> dict[str, Any]:
        """Frame the current selection.

        Pairs with the select tools to put a finding on screen: select
        what an audit named, zoom to it, then render.
        """
        return _call("editor.zoom_to_selection", timeout=30.0)

    @mcp.tool()
    async def easyeda_get_strings() -> dict[str, Any]:
        """Text primitives on the current PCB.

        Silkscreen and copper text: designators, values, legends. What a
        mirrored-text or off-board-text audit reads.
        """
        return _call("pcb.strings")

    @mcp.tool()
    async def easyeda_get_pours() -> dict[str, Any]:
        """Copper pour outlines on the current PCB.

        The shape the user drew, not the copper it produced. Compare
        with ``easyeda_get_poured``: an outline with no poured copper is
        a pour that was never executed, which looks fine on screen and
        ships as a missing plane.
        """
        return _call("pcb.pours")

    @mcp.tool()
    async def easyeda_get_poured() -> dict[str, Any]:
        """Copper actually filled in by pouring."""
        return _call("pcb.poured")

    @mcp.tool()
    async def easyeda_get_fills() -> dict[str, Any]:
        """Solid fill primitives on the current PCB."""
        return _call("pcb.fills")

    @mcp.tool()
    async def easyeda_get_schematic_buses() -> dict[str, Any]:
        """Buses on the current schematic."""
        return _call("sch.buses")

    @mcp.tool()
    async def easyeda_save_schematic() -> dict[str, Any]:
        """Save the current schematic document.

        Separate from ``easyeda_save``, which saves the PCB. Saving one
        does not save the other, and assuming it does is how edits get
        lost.
        """
        return _call("sch.save", timeout=60.0)

    @mcp.tool()
    async def easyeda_get_images() -> dict[str, Any]:
        """Images placed on the current PCB.

        Worth checking before release: a linked rather than embedded
        image renders as a blank on any machine without the source file,
        which the Altium side audits for too.
        """
        return _call("pcb.images")

    @mcp.tool()
    async def easyeda_get_bounding_box(primitive_ids: str = "") -> dict[str, Any]:
        """Bounding box enclosing the named primitives.

        Args:
            primitive_ids: comma-separated ids from any query result.
        """
        ids = [i.strip() for i in str(primitive_ids).split(",") if i.strip()]
        if not ids:
            return {"ok": False, "reason": (
                "primitive_ids is required, comma separated, from a query "
                "result")}
        return _call("pcb.bbox", {"primitive_ids": ids})

    @mcp.tool()
    async def easyeda_get_environment() -> dict[str, Any]:
        """Which EasyEDA this is, and whether it is online.

        Worth reading FIRST when something behaves oddly. Pro, JLCEDA
        Pro and the private edition do not expose the same API surface,
        and offline mode changes what a library call can reach, so a
        failure that looks like a bug here is often an edition or a
        connectivity difference. Reporting it lets that be attributed
        rather than guessed at.
        """
        return _call("sys.environment")

    @mcp.tool()
    async def easyeda_list_boards_dmt() -> dict[str, Any]:
        """Boards in the workspace, at the document-manager level.

        Distinct from ``easyeda_list_boards``, which lists the PCBs in
        the open project.
        """
        return _call("dmt.boards")

    @mcp.tool()
    async def easyeda_list_panels() -> dict[str, Any]:
        """Panels in the workspace.

        A panel is how several boards are stepped and repeated for
        fabrication, so it is where an array that differs from the board
        it was built from would show up.
        """
        return _call("dmt.panels")

    @mcp.tool()
    async def easyeda_get_current_panel() -> dict[str, Any]:
        """The panel document currently open, if one is.

        No panel open is a normal answer rather than a failure, and it
        comes back as ``open: false`` so a caller can tell it apart from
        a read that went wrong.
        """
        return _call("dmt.current_panel")

    @mcp.tool()
    async def easyeda_get_panel(uuid: str) -> dict[str, Any]:
        """One panel by uuid, from easyeda_list_panels.

        Args:
            uuid: the panel's identifier.
        """
        if not str(uuid or "").strip():
            return {"ok": False, "reason": "uuid is required"}
        return _call("dmt.panel_info", {"uuid": uuid})

    @mcp.tool()
    async def easyeda_create_panel(name: str = "") -> dict[str, Any]:
        """Create a panel document.

        This is where a panelised fabrication starts on EasyEDA, and it
        is NOT the same thing as Altium's step-and-repeat: it makes the
        document, and nothing here arranges boards inside it. dmt_Panel
        has no add, insert or place method and neither does dmt_Board,
        so populating the panel appears not to be exposed to extensions
        at all. Treat this as document management.

        NAMING IS A SECOND STEP. ``createPanel()`` takes no arguments,
        so the panel is created with the editor's default name and then
        renamed. The reply reports ``created`` and ``named`` separately,
        because a panel that exists under the wrong name is a different
        outcome from one that was never made. Before this the name was
        passed to a method that does not accept it and was silently
        dropped, so the rename never happened at all.

        Args:
            name: what to call it. Applied by a follow-up rename. Left
                empty, the editor's default name stands.
        """
        params = {}
        if str(name or "").strip():
            params["name"] = name
        return _call("dmt.create_panel", params)

    @mcp.tool()
    async def easyeda_rename_panel(uuid: str, name: str) -> dict[str, Any]:
        """Rename a panel document.

        Args:
            uuid: the panel's identifier.
            name: the new name.
        """
        if not str(uuid or "").strip():
            return {"ok": False, "reason": "uuid is required"}
        if not str(name or "").strip():
            return {"ok": False, "reason": "name is required"}
        return _call("dmt.rename_panel", {"uuid": uuid, "name": name})

    @mcp.tool()
    async def easyeda_delete_panel(
        uuid: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete a panel document. There is no undo.

        Checkpoints on this backend save ONE open document, so a deleted
        panel is not something this side can put back.

        Args:
            uuid: the panel's identifier.
            confirm: must be true. Nothing is sent without it.
        """
        if not str(uuid or "").strip():
            return {"ok": False, "reason": "uuid is required"}
        if not confirm:
            return {
                "ok": False,
                "reason": ("pass confirm=true to delete this panel; "
                           "there is no undo"),
            }
        return _call("dmt.delete_panel", {"uuid": uuid, "confirm": True})

    @mcp.tool()
    async def easyeda_render_image(save_to: str = "") -> dict[str, Any]:
        """Render what the editor is currently showing, as an image.

        Use it. A board can pass every numeric check and still be
        visibly wrong, and this project's own library and placement work
        repeatedly found geometry bugs that scores hid. Looking is the
        check that catches those.

        The editor hands back a Blob, which does not survive JSON, so
        the extension packs it. An unpacked reply is reported as a
        FAILURE rather than as a successful render of nothing: this is
        the one tool whose whole purpose is to put a picture in front of
        someone, and succeeding without one defeats it entirely.

        Args:
            save_to: where to write the image. Without it the encoded
                bytes come back in the reply, which is fine for a small
                view and wasteful for a large one.
        """
        reply = _call("editor.render_image", timeout=120.0)
        if not reply.get("ok"):
            return reply
        if reply.get("rendered") is False:
            return {"ok": False,
                    "reason": str(reply.get("failed")
                                  or "the editor returned no image"),
                    "rendered": False}
        packed = reply.get("image")
        if not isinstance(packed, dict) or not packed.get("base64"):
            return {
                "ok": False,
                "reason": (
                    "the editor returned no usable image. A Blob does not "
                    "survive JSON, so an extension build that does not "
                    "pack it sends an empty object and this reads as a "
                    "render that produced nothing. Import the current "
                    "extension build."),
                "rendered": False,
                "raw_keys": sorted(reply),
            }
        if str(save_to).strip():
            return _save_export({"ok": True, "file": packed,
                                 "verified_live": reply.get("verified_live")},
                                save_to)
        return reply

    @mcp.tool()
    async def easyeda_get_selection() -> dict[str, Any]:
        """What the user currently has selected in the editor.

        The bridge between a human pointing at something and a tool
        acting on it, without either having to name coordinates.
        """
        return _call("pcb.selection")

    @mcp.tool()
    async def easyeda_clear_selection() -> dict[str, Any]:
        """Deselect everything in the editor."""
        return _call("pcb.clear_selection")

    @mcp.tool()
    async def easyeda_cross_probe(
        designators: str = "", pins: str = "", nets: str = "",
        highlight: bool = True, select: bool = True,
    ) -> dict[str, Any]:
        """Reveal parts, pins or nets in the editor by NAME.

        The counterpart to a query: having found something, put it in
        front of the human rather than describing where it is. This is
        the schematic-to-board link, so it works in the vocabulary a
        review speaks, designators and net names, rather than in
        internal ids.

        NOT BY PRIMITIVE ID. Use ``easyeda_select_primitives`` for
        those; the two identifier spaces are separate and this call
        matches nothing when handed an id. It previously passed
        primitive ids here and reported a count of whatever it was
        given, so it looked successful while selecting nothing.

        Args:
            designators: comma-separated, e.g. "U1,R4".
            pins: comma-separated ``designator_pin``, e.g. "U1_1,U1_2".
            nets: comma-separated net names.
            highlight: highlight the matches. On by default.
            select: select the matches. On by default.
        """
        def _split(raw: str) -> list[str]:
            return [i.strip() for i in str(raw).split(",") if i.strip()]

        params = {
            "designators": _split(designators),
            "pins": _split(pins),
            "nets": _split(nets),
            "highlight": highlight,
            "select": select,
        }
        if not (params["designators"] or params["pins"] or params["nets"]):
            return {"ok": False, "reason": (
                "give designators, pins or nets. Cross-probing selects by "
                "name, not by primitive id: for ids use "
                "easyeda_select_primitives")}
        return _call("pcb.cross_probe", params)

    @mcp.tool()
    async def easyeda_modify_component(
        primitive_id: str = "", changes: dict | None = None,
    ) -> dict[str, Any]:
        """Change properties of one placed component on the PCB.

        This is how a footprint is moved, rotated, flipped to the other
        side or locked: there is no separate move or rotate call, they
        are all properties.

        An empty ``changes`` is refused rather than sent. The editor
        would accept it and report success, which reads as "the change
        was applied" when nothing was.

        Args:
            primitive_id: from a component query.
            changes: property names to new values. Recognised: x, y,
                rotation, layer (TOP or BOTTOM, which is the side the
                part sits on), primitiveLock, addIntoBom, designator,
                name, uniqueId, manufacturer, manufacturerId, supplier,
                supplierId, and otherProperty for anything else.
        """
        if not str(primitive_id).strip():
            return {"ok": False, "reason": "primitive_id is required"}
        if not changes:
            return {"ok": False, "reason": (
                "changes must name at least one property; an empty change "
                "would report success while doing nothing")}
        return _call("pcb.modify_component",
                     {"primitive_id": primitive_id, "changes": changes})

    @mcp.tool()
    async def easyeda_get_project_info() -> dict[str, Any]:
        """The open project: name, uuid, and what it contains."""
        return _call("proj.info")

    @mcp.tool()
    async def easyeda_list_schematics() -> dict[str, Any]:
        """Every schematic in the open project."""
        return _call("sch.list_schematics")

    @mcp.tool()
    async def easyeda_list_schematic_pages() -> dict[str, Any]:
        """Pages of the current schematic.

        A multi-page schematic is the normal case on anything real, and
        a tool that only ever saw page one would silently report a
        fraction of the design.
        """
        return _call("sch.list_pages")

    def _schematic_scope() -> dict[str, Any]:
        """Which schematic was read, and how many were not.

        sch_Netlist.getNetlist is scoped to the open schematic, so
        every count a review prints describes that document rather than
        the project. Reporting the numbers without that context gives a
        reader a figure that answers a smaller question than the one
        they asked.

        Best effort by design. If the project cannot be enumerated the
        review still runs and says so, rather than implying full
        coverage.
        """
        listing = _call("system.invoke", {
            "class_name": "dmt_Schematic",
            "method": "getAllSchematicsInfo", "args": [],
        }, timeout=30.0)
        rows = (listing.get("value") or []) if listing.get("ok") else []
        if not isinstance(rows, list) or not rows:
            return {"scope_unknown": (
                "the project's schematics could not be listed, so "
                "whether this covers the whole design is unknown")}
        pages = sum(len(r.get("page") or []) for r in rows
                    if isinstance(r, dict))
        out: dict[str, Any] = {
            "schematics_in_project": len(rows),
            "pages_in_project": pages,
        }
        # What the netlist covers depends on WHICH schematic is open,
        # and the first version of this warning got it wrong.
        #
        # Measured on a live hierarchical project: the top schematic's
        # page holds 5 sheet symbols and its netlist has 111 parts, so
        # that netlist is FLATTENED across the hierarchy. A sub-block
        # opened on its own gave 45 parts, and the same designators
        # (IC1 with 7 loose pins of 61, IC3 with 3 of 12) appear in
        # both, which is what proves the containment rather than the
        # two merely being different sizes.
        #
        # So "the others were not examined" is false when the top
        # schematic is open, and a warning that cries missing coverage
        # on a complete review is one a reader learns to skip. The
        # numbers are reported and the ambiguity is named, without
        # asserting which case this is: nothing in the reply
        # distinguishes a top schematic from a block.
        if len(rows) > 1:
            out["scope_note"] = (
                f"the project has {len(rows)} schematics across "
                f"{pages} pages. A TOP schematic's netlist is flattened "
                f"and already covers the blocks below it; a block "
                f"opened on its own covers only itself. Compare "
                f"parts_in_netlist against the whole design to tell "
                f"which this is")
        return out

    @mcp.tool()
    async def easyeda_get_schematic_hierarchy() -> dict[str, Any]:
        """The sheet hierarchy: which blocks the open schematic contains.

        The vocabulary is not obvious and getting it wrong misreads the
        whole structure. A schematic page reports three kinds of
        component and only one of them is a part:

          sheet          the PAGE FRAME itself, at 0,0 with no
                         designator. Not a child sheet, despite the
                         name, which is the trap here.
          block_symbol   a hierarchical BLOCK, backed by its own
                         schematic document. subPartName carries the
                         block's name, measured as "ESP32.1" and
                         "Power.1".
          part           an ordinary component.

        Each block_symbol corresponds to a child schematic document
        rather than to a library symbol.

        This matters for a review because counting components on a top
        page returns the frame and the block outlines, not the parts
        inside them. The netlist is flattened and does cover the whole
        design, so netlist-based checks are correct; anything reading
        components directly is not.
        """
        components = _call("sch.components", timeout=60.0)
        if not components.get("ok"):
            return components

        rows = [c for c in (components.get("components") or [])
                if isinstance(c, dict)]
        blocks, parts, frames, other = [], 0, 0, []
        for row in rows:
            kind = str(row.get("componentType") or "").strip().lower()
            if kind == "block_symbol":
                blocks.append({
                    "name": str(row.get("subPartName") or "").strip(),
                    "designator": str(row.get("designator") or "").strip(),
                    "primitive_id": row.get("primitiveId"),
                    "symbol_uuid": str((row.get("symbol") or {}).get("uuid")
                                       or ""),
                })
            elif kind == "part":
                parts += 1
            elif kind == "sheet":
                frames += 1
            elif kind:
                other.append(kind)

        listing = _call("system.invoke", {
            "class_name": "dmt_Schematic",
            "method": "getAllSchematicsInfo", "args": [],
        }, timeout=30.0)
        documents = []
        if listing.get("ok") and isinstance(listing.get("value"), list):
            for entry in listing["value"]:
                if not isinstance(entry, dict):
                    continue
                documents.append({
                    "item_type": entry.get("itemType"),
                    "uuid": entry.get("uuid"),
                    "pages": [
                        {"uuid": pg.get("uuid"), "name": pg.get("name")}
                        for pg in (entry.get("page") or [])
                        if isinstance(pg, dict)
                    ],
                })

        result = {
            "ok": True,
            "verified_live": components.get("verified_live"),
            "blocks": blocks,
            "block_count": len(blocks),
            "parts_on_this_page": parts,
            "page_frames": frames,
            "schematic_documents": documents,
            "note": ("a block_symbol is a child schematic; the 'sheet' "
                     "entry is the page frame, not a child"),
        }
        if other:
            # An unmeasured componentType is worth surfacing rather
            # than folding into a count: the vocabulary here was
            # learned from one board.
            result["unrecognised_types"] = sorted(set(other))
        return result

    @mcp.tool()
    async def easyeda_get_schematic_pins() -> dict[str, Any]:
        """Every schematic pin, with its part and the net it sits on.

        Built from the NETLIST, and that is a measured decision rather
        than a preference.

        sch_PrimitivePin.getAll() returns only pins placed loose on a
        sheet, which is a symbol-editor thing: a live schematic with 89
        components reported zero. The per-component route the board
        side uses does not work here either. Measured:
        sch_PrimitiveComponent.getAllPinsByPrimitiveId,
        .getComponentDetail, .get and sch_PrimitivePin.getAllPrimitiveId
        ALL failed on a live schematic, the last of those taking no
        argument at all, so it is not a question of passing the wrong
        id. Only getAll() works on that class.

        The netlist has what those cannot give: each entry carries
        ``pins`` as pin number to net name, alongside the props that
        hold the designator. That is the whole of what a connectivity
        review needs.

        A pin on no net is reported with an empty net rather than
        dropped, because an unconnected pin is the finding.
        """
        reply = _call("sch.netlist", timeout=60.0)
        if not reply.get("ok"):
            return reply

        raw = reply.get("netlist")
        if isinstance(raw, str):
            # The editor hands this back as a JSON STRING, not an
            # object. Anything treating it as a dict sees nothing.
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError) as exc:
                return {"ok": False, "reason": (
                    f"the netlist did not parse as JSON: {exc}")}
        if not isinstance(raw, dict) or not raw:
            # No netlist means this is not a schematic. A SYMBOL
            # document holds its pins directly, and that is the read
            # for it, so the parity audit can compare a symbol's pins
            # against a footprint's pads.
            loose = _call("sch.pins", timeout=60.0)
            if not loose.get("ok"):
                return loose
            rows = [p for p in (loose.get("pins") or [])
                    if isinstance(p, dict)]
            return {
                "ok": True,
                "verified_live": loose.get("verified_live"),
                "source": "sch.pins",
                "pins": rows,
                "pin_count": len(rows),
                "note": ("read from the open document's own pins, which "
                         "is what a symbol carries; a schematic's part "
                         "pins come from the netlist instead"),
            }

        pins: list[dict[str, Any]] = []
        without_designator = 0
        for unique_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            props = entry.get("props") if isinstance(entry.get("props"),
                                                     dict) else {}
            designator = str(props.get("Designator") or "").strip()
            if not designator:
                without_designator += 1
            table = entry.get("pins")
            if not isinstance(table, dict):
                continue
            for number, net in table.items():
                pins.append({
                    "designator": designator or "(unnamed)",
                    "pin": str(number),
                    "net": str(net or ""),
                    "unique_id": str(unique_id),
                })

        pins.sort(key=lambda row: (row["designator"], row["pin"]))
        unconnected = [p for p in pins if not p["net"]]
        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "source": "netlist",
            "pins": pins,
            "pin_count": len(pins),
            "parts_in_netlist": len(raw),
            "unconnected_count": len(unconnected),
            "unconnected": unconnected,
        }
        result.update(_schematic_scope())
        if without_designator:
            result["parts_without_a_designator"] = without_designator
        return result

    @mcp.tool()
    async def easyeda_get_assembly_variants() -> dict[str, Any]:
        """Assembly variant configurations.

        Which parts are fitted in which build. The same question the
        Altium variant tools answer, and the reason a BOM alone is not
        enough to order a board.

        KNOWN NOT TO ANSWER. Measured twice against a live schematic
        holding 111 parts: the editor accepts the call and never
        returns. It is not a missing class and not an empty project,
        because the same document answers ``sch.components`` and
        ``sch.netlist``. The refusal arrives after a short budget rather
        than the full timeout, and it is a refusal, NOT an empty variant
        list: nothing was read, so nothing can be concluded about which
        parts are fitted.
        """
        return _call("sch.assembly_variants")

    def _suffix_from_magic(data: bytes) -> Optional[str]:
        """The extension a file's own first bytes imply, or None.

        Needed because the editor does not always report a media type:
        the schematic BOM arrives with an empty mime and is a genuine
        xlsx, so anything relying on the mime alone writes a spreadsheet
        under a name no application will open.

        Only formats whose signature is unambiguous. Guessing at a text
        format from its first bytes would be a different exercise, and a
        wrong extension is worse than none.
        """
        if not data:
            return None
        if data[:4] == b"%PDF":
            return ".pdf"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:2] == b"PK":
            # Both a zip and an Office file start PK. The Office
            # container names its content types near the front, which is
            # what separates a BOM from a gerber archive.
            head = data[:4096]
            if b"[Content_Types].xml" in head:
                if b"workbook.xml" in head or b"xl/" in head:
                    return ".xlsx"
                return ".xlsx"
            return ".zip"
        return None

    def _save_export(reply: dict, save_to: str) -> dict[str, Any]:
        """Turn a packed export reply into a file on disk.

        The editor's manufacture exports return file DATA (a Blob). The
        extension packs it as base64 because JSON.stringify(blob) is {},
        which is why every export used to arrive empty. This half writes
        the bytes where the caller asked.

        ``save_to`` is required: a gerber zip is megabytes, and handing
        that back as base64 through the tool reply serves nobody.
        """
        import base64
        import pathlib as _pathlib

        if not reply.get("ok"):
            return reply
        packed = reply.get("file")
        if packed is None:
            return {"ok": False, "reason": (
                "the editor returned no file. For schematic exports this "
                "usually means the schematic tab is not active; for PCB "
                "exports, that the board is not the focused document."),
                "verified_live": reply.get("verified_live")}
        if not str(save_to).strip():
            size = packed.get("size") if isinstance(packed, dict) else None
            return {"ok": False, "reason": (
                f"save_to is required: the export is a whole file"
                f"{f' ({size} bytes)' if size else ''} and belongs on "
                f"disk, not in a tool reply.")}
        if not isinstance(packed, dict):
            return {"ok": False, "reason": (
                f"the editor answered with {type(packed).__name__} where "
                f"file data was expected")}

        target = _pathlib.Path(save_to)
        kind = packed.get("kind")

        # Decode first, because the CONTENT is the only reliable guide
        # to what this file is. Measured: the schematic BOM arrives with
        # an EMPTY mime and is a real xlsx, so a media-type lookup alone
        # decides nothing and a caller gets a spreadsheet under a name
        # no application will open.
        data = None
        if kind == "base64":
            data = base64.b64decode(str(packed.get("base64") or ""))
        elif kind != "text":
            return {"ok": False, "reason": (
                f"unrecognised packed-file kind {kind!r}; the extension "
                f"and this tool disagree about the envelope")}

        detected = _suffix_for_mime(packed.get("mime"))
        if detected is None and data is not None:
            detected = _suffix_from_magic(data)

        # Only fills a suffix that is MISSING. A caller who named one
        # gets exactly the path they asked for, and a disagreement is
        # reported rather than corrected: renaming someone's chosen path
        # out from under them is worse than telling them.
        suffix_note = None
        if detected:
            if not target.suffix:
                target = target.with_suffix(detected)
            elif target.suffix.lower() != detected.lower():
                suffix_note = (
                    f"saved as {target.suffix} because that is what was "
                    f"asked for, but the contents are {detected}")

        target.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            target.write_bytes(data)
        elif kind == "text":
            target.write_text(str(packed.get("text") or ""),
                              encoding="utf-8")
        else:
            return {"ok": False, "reason": (
                f"unrecognised packed-file kind {kind!r}; the extension "
                f"and this tool disagree about the envelope")}

        saved = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "path": str(target),
            "size": target.stat().st_size,
            "mime": packed.get("mime"),
            "suggested_name": packed.get("name"),
        }
        if suffix_note:
            saved["suffix_note"] = suffix_note
        return saved

    @mcp.tool()
    async def easyeda_export_schematic_bom(save_to: str = "") -> dict[str, Any]:
        """The BOM as the schematic generates it.

        Distinct from the PCB BOM on purpose: the two disagree when a
        part is placed on one and not the other, and that disagreement
        is a finding rather than something to hide by picking one.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.sch_bom", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_simulation_netlist(save_to: str = "") -> dict[str, Any]:
        """The SPICE netlist for simulation.

        Note the standing rule on this project: a simulation model comes
        from the vendor, never generated. This exports the netlist the
        editor built from models already attached, and does not invent
        any.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.simulation_netlist", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_list_libraries() -> dict[str, Any]:
        """Libraries available in the editor: system, personal, project.

        THE ENUMERATION IS EMPTY. ``getAllLibrariesList`` returns no
        entries even against an editor holding a populated system
        library, so ``libraries`` being empty says nothing about what is
        installed. ``enumeration_empty`` flags that, rather than letting
        an empty list read as an empty workspace.

        What does work is ``known_library_uuids``, which carries the
        four uuids the named getters answer with. Those are what a
        search scopes to.

        Returns:
            Dict with ``libraries``, ``enumeration_empty`` and
            ``known_library_uuids`` (system, personal, project,
            favorite).
        """
        return _call("lib.list_libraries")

    @mcp.tool()
    async def easyeda_search_devices(
        query: str = "", library_uuid: str = "", page: int = 1,
        items_of_page: int = 0,
    ) -> dict[str, Any]:
        """Search the editor's device libraries.

        A device is symbol plus footprint plus metadata, which is what
        you place. Searching symbols alone finds drawings that may have
        no land pattern behind them.

        TEN RESULTS, ALWAYS. See ``easyeda_search_footprints``.

        Args:
            query: Substring matched by the editor's own search. Empty
                returns the editor's default first page rather than
                nothing.
            library_uuid: Restrict to one library. Read the reachable
                uuids from ``easyeda_list_libraries``.

        Returns:
            Dict with ``devices``, ``result_count``, ``page``,
            ``capped`` and ``library_uuid``.
        """
        return _call("lib.search_devices",
                     _search_params(query, library_uuid, page,
                                    items_of_page))

    @mcp.tool()
    async def easyeda_get_devices_by_lcsc(lcsc_ids: str = "") -> dict[str, Any]:
        """Look devices up by LCSC part number.

        The one lookup that starts from a number on a BOM rather than
        from a name someone chose.

        NOT ENOUGH TO PLACE A PART. Measured against a live library:
        each entry carries ``uuid``, ``symbolUuid`` and
        ``footprintUuid`` and NO ``libraryUuid``, while placing needs
        the library and device pair. So this identifies a part and
        cannot position one; search by MPN when the next step is
        placement.

        A part the library does not hold is named in ``missing`` rather
        than left as a gap in a shorter list. Asking for three and
        receiving two tells a caller nothing about which BOM line
        failed, and quietly resolving a BOM one part short is how a
        board gets built without it.

        Args:
            lcsc_ids: comma-separated LCSC ids, for example "C25804,C1525".
        """
        ids = [i.strip() for i in str(lcsc_ids).split(",") if i.strip()]
        if not ids:
            return {"ok": False, "reason": (
                "lcsc_ids is required, comma separated, for example "
                "'C25804,C1525'")}
        reply = _call("lib.devices_by_lcsc", {"lcsc_ids": ids})
        if not reply.get("ok"):
            return reply

        devices = reply.get("devices")
        found = len(devices) if isinstance(devices, list) else 0
        reply["requested"] = ids
        reply["requested_count"] = len(ids)
        reply["found_count"] = found
        if found < len(ids):
            # The editor answers with devices and no echo of the id that
            # produced each, so which specific id failed can only be
            # narrowed when exactly one is missing.
            reply["missing_count"] = len(ids) - found
            reply["missing"] = (
                [i for i in ids] if found == 0 else None)
            reply["partial_warning"] = (
                f"{len(ids)} ids requested and {found} devices returned. "
                f"The editor does not say which id produced which device, "
                f"so look each one up on its own to find the gap")
        if isinstance(devices, list) and devices and not any(
                d.get("libraryUuid") for d in devices
                if isinstance(d, dict)):
            reply["placement_note"] = (
                "these entries carry no libraryUuid, so they cannot be "
                "passed to easyeda_place_schematic_component. Search by "
                "MPN to get the library and device pair placement needs")
        return reply

    @mcp.tool()
    async def easyeda_search_symbols(
        query: str, library_uuid: str = "", page: int = 1,
        items_of_page: int = 0,
    ) -> dict[str, Any]:
        """Search the editor's symbol libraries.

        THE ONLY SEARCH THAT NEEDS A QUERY. The device, footprint and 3D
        model searches answer an empty one with a default page.
        ``lib_Symbol.search`` does not answer it at all: measured twice,
        the editor accepted the call and never returned. So the query is
        required here, and refusing it early is what keeps a blank
        search from hanging the connection.

        TEN RESULTS, ALWAYS. See ``easyeda_search_footprints``.

        Args:
            query: What to search for. Required.
            library_uuid: Restrict to one library. Read the reachable
                uuids from ``easyeda_list_libraries``.

        Returns:
            Dict with ``symbols``, ``result_count``, ``page``,
            ``capped`` and ``library_uuid``.
        """
        if not str(query).strip():
            return {"ok": False, "reason": (
                "query is required: lib_Symbol.search does not answer an "
                "empty query, and the call hangs rather than being "
                "refused")}
        return _call("lib.search_symbols",
                     _search_params(query, library_uuid, page,
                                    items_of_page))

    @mcp.tool()
    async def easyeda_search_footprints(
        query: str = "", library_uuid: str = "", page: int = 1,
        items_of_page: int = 0,
    ) -> dict[str, Any]:
        """Search the editor's footprint libraries.

        TEN PER PAGE, AND THERE ARE MORE PAGES. Ten is the default page
        size, not a ceiling. The earlier note that no page argument
        exists came from a numeric SECOND argument matching nothing,
        and the second argument is a library uuid; paging sits further
        along the signature. Ask for ``next_page`` when ``more_likely``
        is true, or raise ``items_of_page``.

        Args:
            query: What to search for. Empty returns the editor's
                default first page rather than nothing.
            library_uuid: Restrict to one library. Read the reachable
                uuids from ``easyeda_list_libraries``.

        Returns:
            Dict with ``footprints``, ``result_count``, ``page``,
            ``capped`` and ``library_uuid``.
        """
        return _call("lib.search_footprints",
                     _search_params(query, library_uuid, page,
                                    items_of_page))

    @mcp.tool()
    async def easyeda_get_symbol_image(
        uuid: str = "", library_uuid: str = "", sub_part_name: str = "",
    ) -> dict[str, Any]:
        """Render one symbol to an image.

        Worth using rather than skipping: geometry that scores well and
        looks wrong is a recurring failure in this project's own library
        work, and a picture is the only thing that catches it.

        BOTH UUIDS ARE REQUIRED. ``getRenderImage`` takes one source
        object, ``{symbolUuid, libraryUuid}``, and cannot resolve a
        symbol from its own uuid alone. Passing a bare uuid was recorded
        as the editor never answering, and the call was written off on
        that basis; the ids were good and the argument was the wrong
        shape. Both uuids come back on every search hit.

        Args:
            uuid: the symbol's identifier, from a search result.
            library_uuid: the library holding it, from the same hit.
            sub_part_name: one part of a multi-part symbol. Optional.
        """
        if not str(uuid).strip():
            return {"ok": False, "reason": "uuid is required"}
        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required: getRenderImage takes "
                "{symbolUuid, libraryUuid} and cannot find a symbol from "
                "its uuid alone. easyeda_search_symbols reports both")}
        params: dict[str, Any] = {"uuid": uuid, "library_uuid": library_uuid}
        if str(sub_part_name).strip():
            params["sub_part_name"] = sub_part_name
        return _call("lib.symbol_image", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_get_footprint_image(
        uuid: str = "", library_uuid: str = "",
    ) -> dict[str, Any]:
        """Render one footprint to an image.

        Check it against the manufacturer land pattern before trusting
        it, the same way ``lib_audit_footprint_vs_datasheet`` does on the
        Altium side. A rendered footprint is evidence of what was drawn,
        not of what is correct.

        BOTH UUIDS ARE REQUIRED, for the same reason as
        ``easyeda_get_symbol_image``: the call takes one source object,
        ``{footprintUuid, libraryUuid}``. A bare uuid was mistaken for
        the editor refusing to answer.

        Args:
            uuid: the footprint's identifier, from a search result.
            library_uuid: the library holding it, from the same hit.
        """
        if not str(uuid).strip():
            return {"ok": False, "reason": "uuid is required"}
        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required: getRenderImage takes "
                "{footprintUuid, libraryUuid} and cannot find a footprint "
                "from its uuid alone. easyeda_search_footprints reports "
                "both")}
        return _call("lib.footprint_image",
                     {"uuid": uuid, "library_uuid": library_uuid},
                     timeout=60.0)

    @mcp.tool()
    async def easyeda_export_gerber(save_to: str = "") -> dict[str, Any]:
        """Gerber fabrication data.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.gerber", timeout=180.0), save_to)

    #: What goes into a fabrication package, and whether the package is
    #: worth sending without it.
    #:
    #: REQUIRED means a fab house cannot build from what is left. The
    #: gerbers are the board and the pick-and-place and BOM are the
    #: assembly; a folder holding two of those three is not a partial
    #: package, it is a package that will come back with questions.
    #:
    #: Drill data is NOT a separate entry: EasyEDA's gerber export
    #: returns a zip that contains it, unlike Altium where NC drill is
    #: its own OutJob container. Listing it separately would invent an
    #: export that does not exist and report it missing on every run.
    _FAB_PARTS = (
        ("gerber", "export.gerber", "gerber.zip", True, 180.0),
        ("pick_and_place", "export.pick_and_place", "pick_and_place.csv",
         True, 120.0),
        ("bom", "export.bom", "bom.csv", True, 120.0),
        # Useful to a fab house and not fatal to omit: a netlist for
        # electrical test, and the board's own summary.
        ("ipcd356", "export.ipcd356", "ipc-d-356.ipc", False, 120.0),
        ("pcb_info", "export.pcb_info", "pcb_info.txt", False, 60.0),
    )

    #: Extras that are a deliberate choice rather than an oversight.
    _FAB_OPTIONAL = {
        "pdf": ("export.pdf", "drawing.pdf", 180.0),
        "step": ("export.3d", "board-3d.step", 180.0),
        "dxf": ("export.dxf", "outline.dxf", 120.0),
        "ipc2581": ("export.ipc2581", "ipc2581.xml", 180.0),
    }

    #: File extension for a reported media type.
    #:
    #: The editor decides the format and the caller cannot know it in
    #: advance: the BOM and the pick-and-place both come back as xlsx
    #: workbooks, not the csv their names suggest. Writing them under a
    #: guessed extension hands a fab house a spreadsheet called .csv,
    #: which either fails to open or is parsed as text and read wrongly.
    _MIME_SUFFIX = {
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
        "application/zip": ".zip",
        "application/pdf": ".pdf",
        "application/json": ".json",
        "text/csv": ".csv",
        "image/svg+xml": ".svg",
        "image/png": ".png",
    }

    def _suffix_for_mime(mime) -> Optional[str]:
        """The extension a reported media type implies, or None.

        Generic types are deliberately absent from the table. A 3D
        model is reported as text/plain, and renaming board-3d.step to
        board-3d.txt on that basis would replace a correct name with a
        useless one. A rename only happens when the media type is
        specific enough to be worth more than the caller's own choice.
        """
        base = str(mime or "").split(";")[0].strip().lower()
        return _MIME_SUFFIX.get(base)

    @mcp.tool()
    async def easyeda_generate_fab_package(
        output_dir: str,
        include: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Everything a fab house needs, in one folder, with a manifest.

        Runs the fabrication exports in turn and writes them side by
        side, then reports every file with its size and every export
        that did not produce one.

        THE PACKAGE IS NOT REPORTED COMPLETE UNLESS IT IS. A folder
        missing the pick-and-place looks exactly like a finished one to
        anybody who did not run it, and that is how a board reaches a
        fab house and comes back with questions a week later. If any
        required export fails, ``ok`` is false and ``missing`` names
        what is not there, even though the other files were written and
        are perfectly good.

        Everything written is listed either way. A partial package is
        worth keeping; it just must not be mistaken for a whole one.

        Drill data is inside the gerber zip on this backend rather than
        being its own export, which is where it differs from Altium.

        Args:
            output_dir: folder to write into. Created if absent.
            include: extras beyond the standard set, any of "pdf",
                "step", "dxf", "ipc2581". These are choices rather than
                omissions, so leaving one out never makes the package
                incomplete.
        """
        import pathlib as _pathlib

        if not str(output_dir or "").strip():
            return {"ok": False, "reason": "output_dir is required"}

        extras = [str(x).strip().lower() for x in (include or [])]
        unknown = [x for x in extras if x not in _FAB_OPTIONAL]
        if unknown:
            # Silently ignoring it would produce a package quietly
            # missing the thing the caller asked for by name.
            return {
                "ok": False,
                "reason": (f"unknown extras {unknown}; choose from "
                           f"{sorted(_FAB_OPTIONAL)}"),
            }

        folder = _pathlib.Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)

        wanted = list(_FAB_PARTS)
        for name in extras:
            command, filename, timeout = _FAB_OPTIONAL[name]
            wanted.append((name, command, filename, False, timeout))

        written = []
        missing = []
        for name, command, filename, required, timeout in wanted:
            outcome = _save_export(_call(command, timeout=timeout),
                                   str(folder / filename))
            if outcome.get("ok"):
                path = _pathlib.Path(str(outcome.get("path")))
                # Correct the extension to whatever the editor actually
                # produced. The name here is a default, not knowledge.
                suffix = _suffix_for_mime(outcome.get("mime"))
                renamed_from = None
                if suffix and path.suffix.lower() != suffix:
                    target = path.with_suffix(suffix)
                    try:
                        path.replace(target)
                        renamed_from = path.name
                        path = target
                    except OSError:
                        # Keep the file rather than lose it; the wrong
                        # name is recoverable and a failed export is not.
                        pass
                entry = {
                    "part": name,
                    "path": str(path),
                    "size": outcome.get("size"),
                    "mime": outcome.get("mime"),
                    "required": required,
                }
                if renamed_from:
                    entry["renamed_from"] = renamed_from
                written.append(entry)
                continue
            missing.append({
                "part": name,
                "required": required,
                "reason": outcome.get("reason")
                or outcome.get("unavailable")
                or "the export produced no file",
            })

        required_missing = [m["part"] for m in missing if m["required"]]
        result = {
            "ok": not required_missing,
            "output_dir": str(folder),
            "written": written,
            "file_count": len(written),
            "total_bytes": sum(w["size"] or 0 for w in written),
            "missing": missing,
        }
        if required_missing:
            result["reason"] = (
                f"the package is incomplete: {', '.join(required_missing)} "
                f"did not export. The files that did are in "
                f"{folder}, but this is not ready to send to a fab "
                f"house.")
            # The usual cause, and worth saying rather than making
            # somebody guess: these are PCB exports.
            result["check"] = (
                "PCB exports need the board as the focused document; a "
                "schematic tab produces nothing for all of them")
        return result

    @mcp.tool()
    async def easyeda_export_ipc2581(save_to: str = "") -> dict[str, Any]:
        """IPC-2581 fabrication data, the format that carries intent.

        Preferred over Gerber where the fabricator accepts it: netlist
        and stackup travel with the geometry instead of being inferred.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.ipc2581", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_export_ipcd356(save_to: str = "") -> dict[str, Any]:
        """IPC-D-356A netlist for bare-board electrical test.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.ipcd356", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_import_routing(
        kind: str = "", path: str = "", confirm: bool = False,
    ) -> dict[str, Any]:
        """Import an autorouter or autolayout result back onto the board.

        CLOSES THE ROUND TRIP that ``easyeda_export_dsn`` opens. The
        classic flow is: export a DSN, route it in an external
        autorouter, import the SES back. Only the outward half existed,
        so the loop stopped halfway.

        DESTRUCTIVE, and differently so per kind. A session file or an
        autoroute JSON replaces the board's ROUTING; an autolayout JSON
        replaces the PLACEMENT of every component. Whatever is there is
        discarded. Take an ``easyeda_checkpoint`` first.

        The file is read here and sent as base64, because the editor
        runs in a sandbox with no path this server can name. The
        extension rebuilds it as a File. The NAME travels with it, since
        the editor identifies the format by extension, and a name whose
        extension does not match the kind is refused rather than passed
        on: the wrong format is accepted by the editor and produces a
        board nobody asked for.

        NEVER RUN AGAINST A LIVE EDITOR. Built from the published
        signatures, which settle what the call expects and not what the
        editor does with it.

        Args:
            kind: one of "ses", "route_json", "layout_json".
            path: the file to send.
            confirm: must be true. Nothing happens without it.
        """
        import base64
        import os

        kinds = {"ses": ".ses", "route_json": ".json",
                 "layout_json": ".json"}
        if kind not in kinds:
            return {"ok": False, "reason": (
                f"kind must be one of {', '.join(sorted(kinds))}")}
        if not path.strip():
            return {"ok": False, "reason": "path is required"}
        if not os.path.isfile(path):
            return {"ok": False, "reason": f"no file at {path}"}
        name = os.path.basename(path)
        if not name.lower().endswith(kinds[kind]):
            return {"ok": False, "reason": (
                f"{name} does not end in {kinds[kind]}, which is what "
                f"kind={kind} expects. The editor identifies the format "
                f"by extension and accepts the wrong one silently")}
        if not confirm:
            what = ("the PLACEMENT of every component"
                    if kind == "layout_json" else "the routing")
            return {"ok": False, "reason": (
                f"importing this replaces {what} on the board, and "
                f"whatever is there now is discarded. Take an "
                f"easyeda_checkpoint, then pass confirm=True")}
        with open(path, "rb") as handle:
            payload = base64.b64encode(handle.read()).decode("ascii")
        return _call("pcb.import_routing", {
            "kind": kind,
            "file": {"base64": payload, "name": name},
            "confirm": True,
        }, timeout=300.0)

    @mcp.tool()
    async def easyeda_search_circuit_blocks(
        query: str = "", library_uuid: str = "", page: int = 1,
        items_of_page: int = 0,
    ) -> dict[str, Any]:
        """Find reusable circuit blocks in the libraries.

        A circuit block (EasyEDA calls it 复用模块) is a saved piece of
        schematic placed into a design. It is what this editor has
        INSTEAD of hierarchy: schematic pages here are siblings with an
        order, and nothing exposes a sheet symbol or a parent link, so
        reuse happens by placing a block rather than by nesting a sheet.

        Pair with ``easyeda_place_circuit_block``.

        Args:
            query: free text. Empty lists what the library holds.
            library_uuid: scope to one library.
            page: which page of results, from 1.
            items_of_page: results per page. The editor's default is
                ten when this is left at 0.
        """
        params = _search_params(query, library_uuid, page, items_of_page)
        return _call("lib.search_cbb", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_set_netlist(
        netlist: str = "", netlist_type: str = "", confirm: bool = False,
    ) -> dict[str, Any]:
        """REPLACE the board's entire connectivity from a netlist.

        THE MOST DESTRUCTIVE OPERATION ON THIS BACKEND. Every net on the
        board comes from the text you supply, and whatever it had before
        is gone. Take an ``easyeda_checkpoint`` first.

        The counterpart to reading the netlist, and the way an
        externally generated connectivity reaches the board.

        NOT A FILE IMPORT, despite sitting beside the autorouter
        imports. ``pcb_Net.setNetlist(type, netlist)`` takes a plain
        STRING, so no file plumbing is involved. It also lives on
        ``pcb_Net`` rather than ``pcb_Document``.

        NEVER RUN AGAINST A LIVE EDITOR. Every argument here comes from
        the published reference. The reference settles what the call
        expects, not what the editor does with it, and this one
        rewrites a board.

        Args:
            netlist: the netlist text.
            netlist_type: one of Allegro, DISA, DSNET, EasyEDA, JLCEDA,
                PADS. Leave empty to let the editor decide.
            confirm: must be true. Nothing happens without it.

        Returns:
            Dict with ``applied``, read from the editor's own answer
            rather than assumed.
        """
        if not netlist.strip():
            return {"ok": False, "reason": "netlist text is required"}
        allowed = ("Allegro", "DISA", "DSNET", "EasyEDA", "JLCEDA", "PADS")
        if netlist_type and netlist_type not in allowed:
            return {"ok": False, "reason": (
                f"netlist_type must be one of {', '.join(allowed)}, or "
                f"empty to let the editor decide")}
        if not confirm:
            return {"ok": False, "reason": (
                "set_netlist REPLACES the entire connectivity of the "
                "board. Every net comes from the text supplied and "
                "whatever the board had before is gone. Take an "
                "easyeda_checkpoint, then pass confirm=True")}
        params: dict[str, Any] = {"netlist": netlist, "confirm": True}
        if netlist_type:
            params["netlist_type"] = netlist_type
        return _call("pcb.set_netlist", params, timeout=180.0)

    @mcp.tool()
    async def easyeda_export_netlist(save_to: str = "") -> dict[str, Any]:
        """The PCB netlist file.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.netlist", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_altium(save_to: str = "") -> dict[str, Any]:
        """Export the design as an Altium Designer file.

        Worth knowing about on this project specifically: it is a route
        out of EasyEDA into the primary backend, done by EasyEDA's own
        exporter rather than by a converter written here. Check the
        result before trusting it; a vendor's own export is a better
        starting point than a third-party translation, not a guarantee.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.altium", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_export_bom(save_to: str = "") -> dict[str, Any]:
        """The BOM file, as the editor generates it.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.bom", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_dxf(save_to: str = "") -> dict[str, Any]:
        """The board outline and copper as DXF.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.dxf", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_3d(save_to: str = "") -> dict[str, Any]:
        """The 3D model of the assembled board.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.model_3d", timeout=180.0), save_to)

    # ---- writing to the board ---------------------------------------
    #
    # Layers are named, never numbered. EasyEDA's layer ids are a numeric
    # enum and their own guidance is to use the members rather than the
    # values, so the name travels and the extension resolves it against
    # the runtime's enum. A number chosen here would be this project's
    # copy of their numbering, wrong the day they insert a layer, and
    # wrong quietly: the primitive would land on a different layer rather
    # than fail.

    @mcp.tool()
    async def easyeda_add_line(
        start_x: float, start_y: float, end_x: float, end_y: float,
        layer: str = "TOP", net: str = "", width: float | None = None,
        locked: bool = False,
    ) -> dict[str, Any]:
        """Draw one line on the current PCB.

        Copper on a copper layer, so this is also how a track is drawn:
        EasyEDA has no separate track primitive, a routed segment is a
        line on TOP, BOTTOM or an inner layer with a net on it.

        Args:
            start_x: start x, in mils. The PCB canvas counts in mils
                one to one, unlike the schematic one.
            start_y: start y.
            end_x: end x.
            end_y: end y.
            layer: layer name, e.g. TOP, BOTTOM, TOP_SILKSCREEN,
                BOARD_OUTLINE. Rejected by the editor if unknown, with
                the known names in the message.
            net: net the line belongs to. Empty for silkscreen and
                outline, which have no net.
            width: line width. The editor's default when omitted.
            locked: lock the primitive against interactive edits.
        """
        params: dict[str, Any] = {
            "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y,
            "layer": layer, "net": net, "locked": locked,
        }
        if width is not None:
            params["width"] = width
        return _call("pcb.add_line", params)

    @mcp.tool()
    async def easyeda_add_arc(
        start_x: float, start_y: float, end_x: float, end_y: float,
        angle: float, layer: str = "TOP_SILKSCREEN", net: str = "",
        width: float | None = None,
    ) -> dict[str, Any]:
        """Draw one arc on the current PCB.

        The arc runs from start to end sweeping ``angle`` degrees, so the
        two endpoints and the angle together fix the radius. A full
        circle is not expressible as one arc: draw two of 180.

        Args:
            start_x: start x.
            start_y: start y.
            end_x: end x.
            end_y: end y.
            angle: swept angle in degrees. Sign sets the direction.
            layer: layer name.
            net: net, if the arc is copper.
            width: line width. The editor's default when omitted.
        """
        params: dict[str, Any] = {
            "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y,
            "angle": angle, "layer": layer, "net": net,
        }
        if width is not None:
            params["width"] = width
        return _call("pcb.add_arc", params)

    @mcp.tool()
    async def easyeda_add_via(
        x: float, y: float, hole_diameter: float, diameter: float,
        net: str = "",
    ) -> dict[str, Any]:
        """Place one via on the current PCB.

        Refused when ``diameter`` does not exceed ``hole_diameter``: the
        editor would accept it and produce a via with no annular ring,
        which is an unmanufacturable board rather than anything visible
        on screen.

        Args:
            x: centre x.
            y: centre y.
            hole_diameter: drill diameter.
            diameter: pad diameter. Must exceed the hole.
            net: net the via connects.
        """
        return _call("pcb.add_via", {
            "x": x, "y": y, "hole_diameter": hole_diameter,
            "diameter": diameter, "net": net,
        })

    @mcp.tool()
    async def easyeda_add_text(
        text: str, x: float, y: float,
        font_size: float = 1.0, width: float = 0.15,
        layer: str = "TOP_SILKSCREEN", align: str = "LEFT_BOTTOM",
        rotation: float = 0.0, font: str = "NotoSans",
        mirror: bool = False, locked: bool = False,
    ) -> dict[str, Any]:
        """Place one text string on the current PCB.

        Args:
            text: the string to place.
            x: anchor x. Which corner of the text this is depends on
                ``align``.
            y: anchor y.
            font_size: text height, in mils.
            width: stroke width.
            layer: layer name, e.g. TOP_SILKSCREEN.
            align: one of LEFT_TOP, LEFT_MIDDLE, LEFT_BOTTOM, CENTER_TOP,
                CENTER, CENTER_BOTTOM, RIGHT_TOP, RIGHT_MIDDLE,
                RIGHT_BOTTOM.
            rotation: degrees.
            font: font family name.
            mirror: mirror the text. What bottom-side text needs so it
                reads correctly from the bottom.
            locked: lock against interactive edits.
        """
        return _call("pcb.add_text", {
            "text": text, "x": x, "y": y,
            "font_size": font_size, "width": width,
            "layer": layer, "align": align, "rotation": rotation,
            "font": font, "mirror": mirror, "locked": locked,
        })

    @mcp.tool()
    async def easyeda_delete_primitives(
        kind: str, primitive_ids: list[str], confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete primitives from the current PCB by id.

        DESTRUCTIVE. Refused unless ``confirm`` is true, and the
        extension refuses independently, because that channel is
        reachable by anything speaking this protocol and cannot assume a
        caller already checked.

        Args:
            kind: which primitive class the ids belong to: line, arc,
                via, text, pad, fill, region, pour or component. Each
                class deletes only its own kind, and the caller knows
                what it created.
            primitive_ids: ids to delete.
            confirm: must be true for anything to happen.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "delete_primitives removes objects and is not undoable "
                "from here. Pass confirm=True if that is intended.")}
        if not primitive_ids:
            return {"ok": False, "reason": "primitive_ids must not be empty"}
        return _call("pcb.delete_primitives", {
            "kind": kind, "primitive_ids": list(primitive_ids),
            "confirm": True,
        })

    @mcp.tool()
    async def easyeda_cleanup_track_slivers(
        min_length_mils: float = 1.0,
        confirm: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Delete near-zero track stubs left behind by editing.

        Dragging and re-routing leaves fragments a fraction of a mil
        long. They are invisible at any sane zoom, they carry no
        current, and they generate DRC noise that hides real findings,
        which is the actual cost: a violation list nobody reads.

        DRY RUN BY DEFAULT, which is the opposite of most tools here and
        deliberate. This deletes copper. Seeing the list first, and how
        long the "slivers" actually are, is how you find out the
        threshold was wrong BEFORE the tracks are gone rather than
        after. Pass dry_run=False and confirm=True to apply.

        THE THRESHOLD IS CAPPED. Nothing above 10 mils is accepted: at
        that size a segment is real routing, and a cleanup that quietly
        removes real routing is far worse than one that leaves litter.
        A caller who wants to delete longer tracks should select them
        and say so.

        Altium offers a second mode, merging collinear runs into one
        track. That is NOT here: it creates copper as well as deleting
        it, and it needs to know that the shared point is a clean
        two-track junction with no via, pad or arc on it. Neither the
        creation nor that check is settled on this backend, and half of
        a merge is a broken net.

        Args:
            min_length_mils: delete tracks shorter than this. Default 1
                mil. Capped at 10.
            confirm: must be true to delete anything.
            dry_run: report what would go without touching the board.
                On by default.
        """
        import math

        if min_length_mils <= 0:
            return {"ok": False,
                    "reason": "min_length_mils must be positive"}
        if min_length_mils > 10:
            return {"ok": False, "reason": (
                f"min_length_mils={min_length_mils} is above the 10 mil "
                f"cap: a track that long is real routing, not a sliver. "
                f"Select what you want removed and delete it "
                f"explicitly.")}

        reply = _call("pcb.lines", timeout=60.0)
        if not reply.get("ok"):
            return reply

        slivers = []
        unreadable = 0
        examined = 0
        for line in reply.get("lines") or []:
            if not isinstance(line, dict):
                continue
            try:
                x1 = float(line["startX"])
                y1 = float(line["startY"])
                x2 = float(line["endX"])
                y2 = float(line["endY"])
            except (KeyError, TypeError, ValueError):
                # A segment whose geometry will not read has not been
                # measured, and must not be counted among those checked
                # and found fine.
                unreadable += 1
                continue
            examined += 1
            length = math.hypot(x2 - x1, y2 - y1)
            if length < min_length_mils:
                slivers.append({
                    "primitive_id": line.get("primitiveId"),
                    "length_mils": round(length, 4),
                    "net": line.get("net") or "",
                    "layer": line.get("layer"),
                    "x": x1, "y": y1,
                })

        slivers.sort(key=lambda s: s["length_mils"])
        summary = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "min_length_mils": min_length_mils,
            "tracks_examined": examined,
            "tracks_without_readable_geometry": unreadable,
            "slivers_found": len(slivers),
            "slivers": slivers,
        }
        if unreadable and not examined:
            summary["scope_warning"] = (
                f"none of the {unreadable} tracks reported readable "
                f"geometry, so nothing was actually measured")

        if dry_run:
            summary["dry_run"] = True
            summary["next"] = (
                "re-run with dry_run=False and confirm=True to delete "
                "these" if slivers else "nothing to delete")
            return summary

        if not confirm:
            return {
                "ok": False,
                "reason": ("this deletes copper; pass confirm=True "
                           "along with dry_run=False"),
                "slivers_found": len(slivers),
            }
        if not slivers:
            summary["deleted"] = 0
            return summary

        ids = [s["primitive_id"] for s in slivers if s["primitive_id"]]
        if len(ids) != len(slivers):
            # Deleting the subset that has ids would report a number
            # that does not match what was found, and leave the rest
            # with no way to say which they were.
            return {
                "ok": False,
                "reason": (f"{len(slivers) - len(ids)} of {len(slivers)} "
                           f"slivers reported no primitive id, so they "
                           f"cannot be addressed; nothing was deleted"),
                "slivers": slivers,
            }

        outcome = _call("pcb.delete_primitives", {
            "kind": "line", "primitive_ids": ids, "confirm": True,
        }, timeout=120.0)
        if not outcome.get("ok"):
            return outcome
        summary["deleted"] = outcome.get("deleted", len(ids))
        summary["delete_result"] = outcome
        return summary

    @mcp.tool()
    async def easyeda_add_zone(
        points: list[list[float]], layer: str = "TOP", net: str = "",
        name: str = "", priority: int | None = None,
        width: float | None = None, preserve_islands: bool = False,
    ) -> dict[str, Any]:
        """Pour a copper zone over a polygon on the current PCB.

        Args:
            points: the outline, as at least three ``[x, y]`` pairs.
                Repeating the first point at the end is accepted and
                dropped: EasyEDA's own polygon example does not repeat
                its start point, so passing the closing point through
                would add a zero-length segment.
            layer: copper layer name: TOP, BOTTOM or an inner layer.
            net: net the pour connects, e.g. GND.
            priority: pour priority, where overlapping pours resolve.
                The editor's default when omitted.
            name: pour name.
            width: outline width. The editor's default when omitted.
            preserve_islands: keep copper islands the pour cuts off
                instead of removing them.
        """
        # Three points is the minimum that encloses an area. Two would
        # be a line the editor accepts as a pour bounding nothing.
        pts = _points(points, "points", 3)
        if isinstance(pts, dict):
            return pts
        params: dict[str, Any] = {
            "points": pts,
            "layer": layer, "net": net,
            "preserve_islands": preserve_islands,
        }
        if name:
            params["name"] = name
        if priority is not None:
            params["priority"] = priority
        if width is not None:
            params["width"] = width
        return _call("pcb.add_zone", params, timeout=60.0)

    # ---- writing to the schematic -----------------------------------
    #
    # No layer here: a schematic sheet has none.
    #
    # Coordinates are in MILS, as everywhere else in this project, and
    # converted to EasyEDA's 0.01-inch schematic units on the way out.
    # The PCB tools above need no such conversion, because that canvas
    # already counts in mils.

    @mcp.tool()
    async def easyeda_add_wire(
        points: list[list[float]], net: str = "",
    ) -> dict[str, Any]:
        """Draw a wire on the current schematic.

        THE SCHEMATIC Y AXIS POINTS DOWN. Measured: every wire already
        on a live sheet reports geometry like
        ``[[400, -200, 300, -200]]``, and the editor stores exactly what
        it is given, applying no transformation of its own. So content
        above the origin has NEGATIVE y, and a wire meant to sit beside
        existing content needs coordinates in that same space.

        This is not converted for you, because
        ``easyeda_get_schematic_wires`` reports the editor's own
        geometry and silently flipping the sign here would make a
        read-modify-write land somewhere else. Note that
        ``sch_PrimitiveComponent`` reports POSITIVE y, so the two spaces
        differ and component coordinates cannot be used directly as wire
        coordinates.

        Args:
            points: the path, as at least two ``[x, y]`` pairs in MILS.
                More than two draws a polyline in one call rather than a
                wire per segment. Whole ``[x1, y1, x2, y2]`` segments
                are accepted too, which is the form the editor reports,
                so geometry read back can go straight in again.
            net: net to put the wire on. The editor infers one from what
                the wire touches when this is empty.
        """
        pts = _points(points, "points", 2, allow_segments=True)
        if isinstance(pts, dict):
            return pts
        # Every coordinate scales, whether the entry is a 2-number point
        # or a 4-number segment, so the conversion walks the entry
        # rather than naming indices 0 and 1.
        return _call("sch.add_wire", {
            "points": [[_sch(n) for n in p] for p in pts],
            "net": net,
        })

    @mcp.tool()
    async def easyeda_add_schematic_text(
        text: str, x: float, y: float, font_size: float | None = None,
        rotation: float = 0.0, font: str = "",
        bold: bool = False, italic: bool = False, underline: bool = False,
    ) -> dict[str, Any]:
        """Place a text note on the current schematic.

        Args:
            text: the string to place.
            x: anchor x, in mils.
            y: anchor y, in mils.
            font_size: text size. The editor's default when omitted.
            rotation: degrees.
            font: font family. The editor's default when empty.
            bold: bold.
            italic: italic.
            underline: underline.
        """
        params: dict[str, Any] = {
            "text": text, "x": _sch(x), "y": _sch(y), "rotation": rotation,
            "bold": bold, "italic": italic, "underline": underline,
        }
        if font:
            params["font"] = font
        if font_size is not None:
            params["font_size"] = font_size
        return _call("sch.add_text", params)

    @mcp.tool()
    async def easyeda_add_schematic_rectangle(
        x: float, y: float, width: float, height: float,
        corner_radius: float = 0.0, rotation: float = 0.0,
    ) -> dict[str, Any]:
        """Draw a rectangle on the current schematic.

        Used to box a functional block on a sheet, the same job the
        Altium side's ``sch_place_rectangle`` does.

        Args:
            x: TOP-LEFT corner x, in mils. Not a centre and not a
                bottom-left corner. Reading it as either puts the box a
                full height from where it was asked for, which still
                looks like a deliberate drawing.
            y: top-left corner y, in mils.
            width: width, in mils.
            height: height, in mils.
            corner_radius: rounded corner radius, in mils. Square when 0.
            rotation: degrees.
        """
        return _call("sch.add_rectangle", {
            "x": _sch(x), "y": _sch(y),
            "width": _sch(width), "height": _sch(height),
            "corner_radius": _sch(corner_radius), "rotation": rotation,
        })

    # ---- placing library parts --------------------------------------

    @mcp.tool()
    async def easyeda_place_schematic_component(
        library_uuid: str, uuid: str, x: float, y: float,
        rotation: float = 0.0, sub_part: str = "", mirror: bool = False,
        add_to_bom: bool = True, add_to_pcb: bool = True,
    ) -> dict[str, Any]:
        """Place a library part on the current schematic.

        The part is identified by the uuid pair that
        ``easyeda_search_devices`` returns, never by name: two libraries
        can hold the same name, and choosing one silently is how a board
        ends up with the wrong footprint under a BOM line that reads
        correctly.

        Args:
            library_uuid: the library the part lives in.
            uuid: the part.
            x: placement x, in mils.
            y: placement y, in mils.
            rotation: degrees.
            sub_part: which section, for a multi-part symbol such as one
                gate of a quad. The whole part when empty.
            mirror: mirror the symbol.
            add_to_bom: include it in the BOM. False for a part that is
                drawn but not fitted.
            add_to_pcb: give it a footprint on the board. False for a
                schematic-only symbol.
        """
        # BOTH uuids, named separately. Placing with a blank one asks
        # the editor for a part that cannot exist, and the reply that
        # comes back says nothing about which half was missing.
        missing = [n for n, v in (("library_uuid", library_uuid),
                                  ("uuid", uuid))
                   if not str(v or "").strip()]
        if missing:
            return {"ok": False, "reason": (
                f"{' and '.join(missing)} required. Both come from one row "
                f"of easyeda_search_devices; a part is identified by the "
                f"PAIR, because two libraries can hold the same name")}
        return _call("sch.place_component", {
            "library_uuid": library_uuid, "uuid": uuid,
            "x": _sch(x), "y": _sch(y),
            "rotation": rotation, "sub_part": sub_part,
            "mirror": mirror,
            "add_to_bom": add_to_bom, "add_to_pcb": add_to_pcb,
        })

    @mcp.tool()
    async def easyeda_place_pcb_component(
        library_uuid: str, uuid: str, x: float, y: float,
        layer: str = "TOP", rotation: float = 0.0, locked: bool = False,
    ) -> dict[str, Any]:
        """Place a footprint on the current PCB.

        Args:
            library_uuid: the library the footprint lives in.
            uuid: the footprint.
            x: placement x.
            y: placement y.
            layer: TOP or BOTTOM.
            rotation: degrees.
            locked: lock against interactive edits.
        """
        return _call("pcb.place_component", {
            "library_uuid": library_uuid, "uuid": uuid,
            "x": x, "y": y, "layer": layer, "rotation": rotation,
            "locked": locked,
        })

    @mcp.tool()
    async def easyeda_set_schematic_component_properties(
        primitive_id: str, changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Change properties of one placed schematic component.

        Args:
            primitive_id: the component, from a schematic component
                listing.
            changes: properties to set. Recognised: x, y, rotation,
                mirror, addIntoBom, addIntoPcb, designator, name,
                uniqueId, manufacturer, manufacturerId, supplier,
                supplierId, and otherProperty for anything else.
        """
        if not changes:
            return {"ok": False, "reason": "changes must not be empty"}
        return _call("sch.set_component_properties", {
            "primitive_id": primitive_id, "changes": dict(changes),
        })

    @mcp.tool()
    async def easyeda_export_pdf(save_to: str = "") -> dict[str, Any]:
        """The board as a PDF, as the editor renders it.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.pdf", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_export_pick_and_place(save_to: str = "") -> dict[str, Any]:
        """Component centroids for assembly.

        What a fab house needs to populate the board, alongside the
        Gerbers and the BOM.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.pick_and_place", timeout=120.0), save_to)

    def _bom_flag(props: dict):
        """Whether a netlist part belongs on the BOM.

        The board reports addIntoBom as a boolean; the schematic
        netlist spells it "Add into BOM" with the string "yes" or "no",
        measured on a live design. Anything else returns None, which
        the caller treats as "did not say" and INCLUDES, because
        absence is not an instruction to leave a part off a purchase
        order.
        """
        value = props.get("Add into BOM")
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in ("yes", "true", "1"):
            return True
        if text in ("no", "false", "0"):
            return False
        return None

    @mcp.tool()
    async def easyeda_export_bom_html(
        output_path: str = "",
        title: str = "Bill of Materials",
        include_excluded: bool = False,
    ) -> dict[str, Any]:
        """Export the board's BOM as a self-contained interactive page.

        One HTML file with no external CSS, JavaScript or EasyEDA
        dependency: sortable columns, a free-text filter, and a toggle
        between grouped (one row per value and footprint) and
        per-component views. Made for mailing to a manufacturer or
        filing with a board release, where the reader has no EDA tool.

        PARTS TICKED OFF THE BOM ARE LEFT OUT, and the count of what was
        left out is reported rather than the list quietly being shorter.
        Mounting holes, fiducials and test points are excluded on
        purpose and are most of what a healthy board excludes.

        A part that does not report the flag is INCLUDED. Absence is not
        a decision to leave it off, and defaulting the other way would
        drop parts from a purchase order on the strength of a missing
        key.

        Reads design.snapshot rather than the components list directly.
        The snapshot handler already resolves the trap that produced a
        measured bug on a live board: footprint and component arrive as
        OBJECTS carrying a name, not as strings, and reading them as
        strings returned an empty value for all 111 parts. Repeating
        that mapping here would reopen the same hole.

        Args:
            output_path: where to write the HTML. Defaults to
                ``bom.html`` in the workspace directory.
            title: heading and page title.
            include_excluded: keep parts marked off the BOM, flagged
                with a column rather than dropped. For checking what
                was excluded, not for sending to a manufacturer.
        """
        from pathlib import Path

        from ..config import get_config
        from ..render.bom_html import render_bom_html

        # The schematic is tried first.
        #
        # A bill of materials is a schematic artefact. Reading it from
        # the board would mean a design cannot produce a BOM until it
        # has been laid out, and the board carries less: the netlist
        # resolves manufacturer, device and footprint for every part,
        # including parts not yet placed.
        #
        # The board snapshot remains the fallback, since it is the only
        # source when a board is open and no schematic is.
        parts = []
        source = "netlist"
        reply = _call("sch.netlist", timeout=60.0)
        if reply.get("ok"):
            raw = reply.get("netlist")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = None
            if isinstance(raw, dict):
                for entry in raw.values():
                    if not isinstance(entry, dict):
                        continue
                    props = entry.get("props")
                    if not isinstance(props, dict):
                        continue
                    designator = str(props.get("Designator") or "").strip()
                    if not designator:
                        continue
                    pins = entry.get("pins")
                    parts.append({
                        "designator": designator,
                        "value": (props.get("Value")
                                  or props.get("Partnumber")
                                  or props.get("DeviceName")
                                  or props.get("Device") or ""),
                        "footprint": (props.get("Origin Footprint")
                                      or props.get("FootprintName")
                                      or props.get("Footprint Name") or ""),
                        "device": (props.get("DeviceName")
                                   or props.get("Device") or ""),
                        "pin_count": len(pins) if isinstance(pins, dict)
                        else 0,
                        "addIntoBom": _bom_flag(props),
                    })

        if not parts:
            source = "board snapshot"
            reply = _call("design.snapshot", timeout=120.0)
            if not reply.get("ok"):
                return reply
            parts = reply.get("parts")

        if not isinstance(parts, list) or not parts:
            # No parts is not a BOM. Writing an empty page here would
            # look like a board with nothing on it.
            return {
                "ok": False,
                "reason": ("the snapshot reported no parts, so there is "
                           "nothing to write. Open the PCB and check "
                           "the editor is answering"),
            }

        # Pin counts come from the snapshot's separate pins list, which
        # is keyed by designator because a flat pad list would lose
        # which part each pad belongs to.
        pin_counts: dict[str, int] = {}
        for pin in reply.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            owner = str(pin.get("designator") or "")
            if owner:
                pin_counts[owner] = pin_counts.get(owner, 0) + 1

        components = []
        excluded = 0
        unflagged = 0
        for part in parts:
            if not isinstance(part, dict):
                continue
            in_bom = part.get("addIntoBom")
            if in_bom is False and not include_excluded:
                excluded += 1
                continue
            if not isinstance(in_bom, bool):
                unflagged += 1
            designator = str(part.get("designator") or "")
            components.append({
                "designator": designator,
                "comment": part.get("value") or "",
                "footprint": part.get("footprint") or "",
                "lib_ref": part.get("device") or "",
                "pins": (part.get("pin_count")
                         if isinstance(part.get("pin_count"), int)
                         else pin_counts.get(designator, 0)),
            })

        html_str = render_bom_html(
            {"components": components, "count": len(components)},
            title=title)

        if output_path:
            target = Path(output_path)
        else:
            target = get_config().workspace_dir / "bom.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_str, encoding="utf-8")

        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "source": source,
            "html_path": str(target),
            "components": len(components),
            "excluded_from_bom": excluded,
            "bytes": len(html_str),
        }
        if unflagged:
            # Not a failure, but the reader should know the flag was
            # missing rather than believing every part was checked.
            result["parts_without_bom_flag"] = unflagged
            result["note"] = (
                f"{unflagged} parts did not report whether they belong "
                f"on the BOM and were INCLUDED; check them before "
                f"ordering")
        return result

    @mcp.tool()
    async def easyeda_export_test_points(save_to: str = "") -> dict[str, Any]:
        """The test point list.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.test_points", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_flying_probe(save_to: str = "") -> dict[str, Any]:
        """The flying probe test file.

        Electrical test for a run too small to justify a bed-of-nails
        fixture.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.flying_probe", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_dsn(save_to: str = "") -> dict[str, Any]:
        """The board as a Specctra DSN file.

        The input format external autorouters take. The route comes back
        as a SES file, which the editor imports.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.dsn", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_export_pads(save_to: str = "") -> dict[str, Any]:
        """The design in PADS format.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.pads", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_export_pcb_info(save_to: str = "") -> dict[str, Any]:
        """The board's fabrication parameters, as a file.

        Stackup, finish and the rest of what a quote needs, in the
        editor's own summary rather than one assembled here.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.pcb_info", timeout=120.0), save_to)

    @mcp.tool()
    async def easyeda_export_schematic_document(save_to: str = "") -> dict[str, Any]:
        """The schematic as a document file.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        """
        return _save_export(_call("export.schematic_document", timeout=180.0), save_to)

    @mcp.tool()
    async def easyeda_export_schematic_netlist(save_to: str = "") -> dict[str, Any]:
        """The netlist as the schematic editor generates it.

        Different from the PCB netlist export: this one is what the
        schematic says, which is the side to compare against when the
        two disagree.

        Args:
            save_to: where to write the exported file. Required in
                practice: the editor returns whole-file data, and
                without a destination the tool refuses rather than
                returning megabytes of base64.
        
        """
        return _save_export(_call("export.schematic_netlist", timeout=120.0), save_to)

    # ---- design rules -----------------------------------------------

    @mcp.tool()
    async def easyeda_create_net_class(
        name: str, nets: list[str],
        color: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Create a net class on the current PCB.

        Args:
            name: class name.
            nets: nets to put in it.
            color: how the class is drawn, as ``{"r":.., "g":.., "b":..}``
                with an optional ``alpha``. The editor chooses when
                omitted, which is why there is no default here: a colour
                invented on this side would silently restyle a board.
        """
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        if not nets:
            return {"ok": False, "reason": "nets must not be empty"}
        params: dict[str, Any] = {"name": name, "nets": list(nets)}
        if color:
            params["color"] = dict(color)
        return _call("pcb.create_net_class", params)

    @mcp.tool()
    async def easyeda_add_nets_to_net_class(
        name: str, nets: list[str],
    ) -> dict[str, Any]:
        """Add nets to an existing net class.

        Args:
            name: the class.
            nets: nets to add.
        """
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        if not nets:
            return {"ok": False, "reason": "nets must not be empty"}
        return _call("pcb.add_nets_to_net_class",
                     {"name": name, "nets": list(nets)})

    @mcp.tool()
    async def easyeda_create_differential_pair(
        name: str, positive_net: str, negative_net: str,
    ) -> dict[str, Any]:
        """Declare a differential pair on the current PCB.

        Declaring the pair is what makes the router treat the two nets
        together. This project can also infer pairs from a netlist, but
        an inferred pair the editor has not been told about routes as two
        unrelated nets.

        Args:
            name: pair name.
            positive_net: the P net.
            negative_net: the N net. Must differ from the P net: a pair
                of one net with itself routes as a pair and is not one.
        """
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        if positive_net == negative_net:
            return {"ok": False, "reason": (
                "positive_net and negative_net must differ")}
        return _call("pcb.create_differential_pair", {
            "name": name, "positive_net": positive_net,
            "negative_net": negative_net,
        })

    @mcp.tool()
    async def easyeda_create_length_match_group(
        name: str, nets: list[str],
        color: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Create an equal-length net group on the current PCB.

        The editor's own grouping, which is what its length tuning acts
        on. ``pcb_calc_length_match`` computes a target offline; this is
        how the board is told to hold it.

        Args:
            name: group name.
            nets: nets to match.
            color: as for a net class. The editor chooses when omitted.
        """
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        if not nets:
            return {"ok": False, "reason": "nets must not be empty"}
        params: dict[str, Any] = {"name": name, "nets": list(nets)}
        if color:
            params["color"] = dict(color)
        return _call("pcb.create_length_match_group", params)

    @mcp.tool()
    async def easyeda_add_nets_to_length_match_group(
        name: str, nets: list[str],
    ) -> dict[str, Any]:
        """Add nets to an existing equal-length group.

        Args:
            name: the group.
            nets: nets to add.
        """
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        if not nets:
            return {"ok": False, "reason": "nets must not be empty"}
        return _call("pcb.add_nets_to_length_match_group",
                     {"name": name, "nets": list(nets)})

    @mcp.tool()
    async def easyeda_get_length_match_groups() -> dict[str, Any]:
        """Equal-length net groups defined on the current PCB."""
        return _call("pcb.length_match_groups")

    @mcp.tool()
    async def easyeda_get_net_rules() -> dict[str, Any]:
        """Per-net rule values in force on the current PCB.

        The widths and clearances the editor will actually enforce, as
        opposed to what a calculator here suggests.
        """
        return _call("pcb.net_rules")

    @mcp.tool()
    async def easyeda_get_rule_configurations() -> dict[str, Any]:
        """Rule configurations on the current PCB, and which is active.

        A board can hold several rule sets and route against one. Which
        one is current decides what DRC means, so a violation count is
        only meaningful alongside it.
        """
        return _call("pcb.rule_configurations")

    # ---- layers -----------------------------------------------------

    @mcp.tool()
    async def easyeda_set_copper_layer_count(
        count: int, confirm: bool = False,
    ) -> dict[str, Any]:
        """Set how many copper layers the board has.

        DESTRUCTIVE when the count goes down: anything on a layer that
        disappears goes with it. Refused unless ``confirm`` is true.

        Args:
            count: an even number from 2 to 32. EasyEDA accepts no
                others, so an odd or larger value is refused here with
                the list rather than sent.
            confirm: must be true for anything to happen.
        """
        allowed = list(range(2, 34, 2))
        if count not in allowed:
            return {"ok": False, "reason": (
                f"count must be one of: "
                f"{', '.join(str(c) for c in allowed)}")}
        if not confirm:
            return {"ok": False, "reason": (
                "changing the copper layer count restructures the stackup "
                "and discards anything on a layer that is removed. Pass "
                "confirm=True if that is intended.")}
        return _call("pcb.set_copper_layer_count",
                     {"count": count, "confirm": True}, timeout=60.0)

    @mcp.tool()
    async def easyeda_set_layer_visibility(
        layers: list[str], visible: bool = True, exclusive: bool = False,
    ) -> dict[str, Any]:
        """Show or hide layers in the editor.

        Display only. Nothing on a hidden layer is removed, and DRC still
        sees it.

        Args:
            layers: layer names, e.g. ["TOP", "TOP_SILKSCREEN"].
            visible: show them. False hides them.
            exclusive: also invert every other layer, so showing one
                layer hides the rest. What you want before a screenshot
                of a single layer.
        """
        if not layers:
            return {"ok": False, "reason": "layers must not be empty"}
        return _call("pcb.set_layer_visibility", {
            "layers": list(layers), "visible": visible,
            "exclusive": exclusive,
        })

    @mcp.tool()
    async def easyeda_set_layer_lock(
        layers: list[str], locked: bool = True,
    ) -> dict[str, Any]:
        """Lock or unlock layers against editing.

        Args:
            layers: layer names.
            locked: lock them. False unlocks.
        """
        if not layers:
            return {"ok": False, "reason": "layers must not be empty"}
        return _call("pcb.set_layer_lock",
                     {"layers": list(layers), "locked": locked})

    @mcp.tool()
    async def easyeda_select_layer(layer: str) -> dict[str, Any]:
        """Make one layer the active one in the editor.

        Args:
            layer: layer name.
        """
        if not layer.strip():
            return {"ok": False, "reason": "layer is required"}
        return _call("pcb.select_layer", {"layer": layer})

    @mcp.tool()
    async def easyeda_modify_layer(
        layer: str, name: str = "", color: str = "",
        transparency: float | None = None,
    ) -> dict[str, Any]:
        """Rename or restyle one layer.

        An empty change is refused rather than sent: the editor would
        report success, which reads as the change having been applied.

        Args:
            layer: which layer.
            name: new display name.
            color: new colour.
            transparency: new transparency.
        """
        if not layer.strip():
            return {"ok": False, "reason": "layer is required"}
        params: dict[str, Any] = {"layer": layer}
        if name:
            params["name"] = name
        if color:
            params["color"] = color
        if transparency is not None:
            params["transparency"] = transparency
        if len(params) == 1:
            return {"ok": False, "reason": (
                "give at least one of name, color or transparency; an "
                "empty change reports success while doing nothing")}
        return _call("pcb.modify_layer", params)

    # ---- projects ---------------------------------------------------

    @mcp.tool()
    async def easyeda_list_projects() -> dict[str, Any]:
        """Every project uuid the editor lists.

        Uuids only. Pair with ``easyeda_get_project`` for names, which is
        two calls rather than one but avoids a list that goes stale in a
        different way from the editor's own.

        AN EMPTY LIST DOES NOT MEAN NO PROJECTS. Measured on a live
        editor with a team project open and working: the list came back
        empty while the project's own uuid answered
        ``easyeda_get_project`` perfectly. So when the list is empty this
        also reports the OPEN project separately, because "0 projects"
        while the user is looking at one reads as a broken tool.
        """
        reply = _call("proj.list")
        if not reply.get("ok"):
            return reply
        uuids = reply.get("project_uuids")
        if uuids:
            return reply

        info = _call("proj.info")
        project = info.get("project") if info.get("ok") else None
        reply["open_project"] = project if isinstance(project, dict) else None
        reply["note"] = (
            "the editor listed no projects. That is its own answer rather "
            "than a failed read, and it has been seen while a project was "
            "open and working, so it does not mean there are none. Any "
            "open project is reported here as open_project")
        return reply

    @mcp.tool()
    async def easyeda_get_project(uuid: str) -> dict[str, Any]:
        """One project's details, by uuid.

        Args:
            uuid: the project.
        """
        if not uuid.strip():
            return {"ok": False, "reason": "uuid is required"}
        return _call("proj.get", {"uuid": uuid})

    @mcp.tool()
    async def easyeda_open_project(uuid: str) -> dict[str, Any]:
        """Open a project in the editor.

        Changes what every other tool here is looking at, so a command
        sent immediately after may land on a document that is still
        loading.

        Args:
            uuid: the project.
        """
        if not uuid.strip():
            return {"ok": False, "reason": "uuid is required"}
        return _call("proj.open", {"uuid": uuid}, timeout=120.0)

    @mcp.tool()
    async def easyeda_create_project(
        name: str, internal_name: str = "", description: str = "",
        team_uuid: str = "", folder_uuid: str = "",
    ) -> dict[str, Any]:
        """Create a project.

        Args:
            name: the display name.
            internal_name: the short name, if it should differ from the
                display name.
            description: free text.
            team_uuid: create it under a team rather than personally.
            folder_uuid: put it in a folder.
        """
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        params: dict[str, Any] = {"name": name}
        for key, value in (("internal_name", internal_name),
                           ("description", description),
                           ("team_uuid", team_uuid),
                           ("folder_uuid", folder_uuid)):
            if value:
                params[key] = value
        return _call("proj.create", params, timeout=120.0)

    @mcp.tool()
    async def easyeda_import_schematic_changes(
        confirm: bool = False, schematic_uuid: str = "",
    ) -> dict[str, Any]:
        """Apply the schematic to the board: EasyEDA's ECO.

        DESTRUCTIVE. A component the schematic no longer has is removed
        from the board along with its routing, so this is refused unless
        ``confirm`` is true.

        Fails rather than guessing on an orphaned PCB with no schematic
        of its own, which is why ``schematic_uuid`` exists.

        Args:
            confirm: must be true for anything to happen.
            schematic_uuid: which schematic to take changes from. The
                one belonging to this board when empty.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "import_schematic_changes applies the schematic to the "
                "board, which can remove components and their routing. "
                "Pass confirm=True if that is intended.")}
        params: dict[str, Any] = {"confirm": True}
        if schematic_uuid:
            params["schematic_uuid"] = schematic_uuid
        return _call("pcb.import_changes", params, timeout=180.0)

    @mcp.tool()
    async def easyeda_zoom_to_board() -> dict[str, Any]:
        """Fit the board outline in the editor view.

        Display only. Worth calling before ``easyeda_render_image`` so
        the picture shows the board rather than wherever the user was
        last zoomed.
        """
        return _call("pcb.zoom_to_board")

    @mcp.tool()
    async def easyeda_delete_schematic_primitives(
        kind: str, primitive_ids: list[str], confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete primitives from the current schematic by id.

        DESTRUCTIVE. Refused unless ``confirm`` is true, and the
        extension refuses independently.

        Args:
            kind: which primitive class the ids belong to: wire, text,
                rectangle, component or attribute.
            primitive_ids: ids to delete.
            confirm: must be true for anything to happen.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "delete_schematic_primitives removes objects and is not "
                "undoable from here. Pass confirm=True if that is "
                "intended.")}
        if not primitive_ids:
            return {"ok": False, "reason": "primitive_ids must not be empty"}
        return _call("sch.delete_primitives", {
            "kind": kind, "primitive_ids": list(primitive_ids),
            "confirm": True,
        })

    # ---- turning a plan into EasyEDA calls ---------------------------

    @mcp.tool()
    async def easyeda_emit_template(
        template: dict[str, Any],
        plan_json: str | dict,
        resolved_parts: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Placement calls for a hand template: THE way to draw a sheet.

        The template is the sheet planned by hand, as for Altium's
        sch_preview_template: parts by designator at grid positions in
        mils, wires through pin anchors (``"U1.3"``), power ports and
        labels. This first pass places the parts where the template says,
        resolving each through the plan: an unresolved part becomes a
        search step and ``runnable`` stays false until resolved_parts
        names its library part. Run the calls, read the pins back with
        easyeda_get_schematic_pins (multiply its units by 10 for mils),
        then call easyeda_emit_template_connections.

        Args:
            template: the hand template; every plan part needs a position.
            plan_json: the DesignPlan, as JSON text or a dict.
            resolved_parts: refdes to ``{"library_uuid", "uuid"}``.
        """
        import json

        from pydantic import ValidationError

        from ..design.easyeda_emitter import emit_easyeda_plan
        from ..design.plan import DesignPlan

        payload = plan_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                return {"ok": False, "reason": f"plan_json is not JSON: {exc}"}
        try:
            plan = DesignPlan(**payload)
        except ValidationError as exc:
            return {"ok": False, "reason": "plan did not validate",
                    "errors": [str(e) for e in exc.errors()]}
        placements: dict[str, tuple[float, float, float]] = {}
        for part in (template or {}).get("parts") or []:
            try:
                placements[str(part["designator"])] = (
                    float(part.get("x", 0)), float(part.get("y", 0)),
                    float(part.get("rotation", 0) or 0))
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "reason": f"template part {part!r} needs "
                                               "designator, x and y"}
        emitted = emit_easyeda_plan(
            plan, placements=placements, resolved_parts=resolved_parts)
        if emitted.nets_pending:
            emitted.next_step = "easyeda_emit_template_connections"
        out = emitted.to_dict()
        out["ok"] = True
        return out

    @mcp.tool()
    async def easyeda_emit_template_connections(
        template: dict[str, Any],
        pin_positions: dict[str, list[float]],
        plan_json: str | dict = "",
        grid: int = 100,
    ) -> dict[str, Any]:
        """The wiring calls for a hand template, checked against the pins
        as placed. Second pass after easyeda_emit_template.

        The template's wires, ports and labels are checked the same way
        as on Altium (shorts, overlapping wires, crossings, dangling
        ends, off-grid points, and with the plan its nets pin by pin)
        and emitted only if there is no error.

        Args:
            template: the same hand template.
            pin_positions: ``"REFDES.PIN"`` to ``[x, y]`` in MILS, read
                back from the editor (its units times 10). Anchors in the
                template must use pin numbers here.
            plan_json: the DesignPlan, to check its nets.
            grid: grid in mils for the off-grid check.
        """
        import json

        from ..design import sch_template as st
        from ..design.easyeda_emitter import emit_template_connections

        try:
            pins = st.pins_from_positions(pin_positions or {})
        except st.TemplateError as exc:
            return {"ok": False, "reason": str(exc)}
        notes: list[dict] = []
        if plan_json:
            from pydantic import ValidationError

            from ..design.plan import DesignPlan
            text = plan_json if isinstance(plan_json, str) else json.dumps(plan_json)
            try:
                plan = DesignPlan.model_validate_json(text)
            except ValidationError as exc:
                return {"ok": False, "reason": "plan did not validate",
                        "errors": [str(e) for e in exc.errors()]}
            template, _, notes = st.merge_plan(template, plan)
        try:
            drawing = st.build(template, pins, grid=grid)
        except st.TemplateError as exc:
            return {"ok": False, "reason": str(exc)}
        for n in notes:
            (drawing.errors if n["level"] == "error" else drawing.warnings).append(n)
        out = {**emit_template_connections(drawing).to_dict(), **drawing.report()}
        out["ok"] = drawing.ok
        return out

    @mcp.tool()
    async def easyeda_add_schematic_circle(
        x: float, y: float, radius: float,
    ) -> dict[str, Any]:
        """Draw a circle on the current schematic.

        Args:
            x: centre x, in mils.
            y: centre y, in mils.
            radius: radius, in mils. Must be greater than zero: the
                editor would take a zero and draw nothing, which is a
                primitive that exists and cannot be seen.
        """
        if radius <= 0:
            return {"ok": False, "reason": "radius must be greater than 0"}
        return _call("sch.add_circle", {
            "x": _sch(x), "y": _sch(y), "radius": _sch(radius),
        })

    @mcp.tool()
    async def easyeda_add_schematic_polygon(
        points: list[list[float]],
    ) -> dict[str, Any]:
        """Draw a closed polygon on the current schematic.

        Args:
            points: at least three ``[x, y]`` pairs, in mils.
        """
        if len(points) < 3:
            return {"ok": False, "reason": (
                "points must be at least 3 [x, y] pairs")}
        return _call("sch.add_polygon", {
            "points": [[_sch(p[0]), _sch(p[1])] for p in points],
        })

    @mcp.tool()
    async def easyeda_get_schematic_selection() -> dict[str, Any]:
        """What is selected on the current schematic.

        The schematic counterpart of ``easyeda_get_selection``. Kept
        separate because the two editors hold their own selections, and
        merging them would report objects from a sheet the caller is not
        looking at.
        """
        return _call("sch.selection")

    @mcp.tool()
    async def easyeda_select_schematic_primitives(
        primitive_ids: list[str],
    ) -> dict[str, Any]:
        """Select primitives on the current schematic, by id.

        Args:
            primitive_ids: ids to select. Replaces the current
                selection rather than adding to it.
        """
        if not primitive_ids:
            return {"ok": False, "reason": "primitive_ids must not be empty"}
        return _call("sch.select", {"primitive_ids": list(primitive_ids)})

    @mcp.tool()
    async def easyeda_clear_schematic_selection() -> dict[str, Any]:
        """Deselect everything on the current schematic."""
        return _call("sch.clear_selection")

    # ---- cross-checks over what the editor reports -------------------
    #
    # These two compute rather than forward, which the rest of this
    # module deliberately avoids. The line is whether the editor has an
    # answer of its own: DRC and ERC do, and a second opinion competing
    # with the tool on screen would be worse than none. Neither of these
    # exists in EasyEDA's API at all, so there is nothing to disagree
    # with, and the alternative is the caller doing the same arithmetic
    # less carefully.

    @mcp.tool()
    async def easyeda_get_unconnected_pins() -> dict[str, Any]:
        """Which pins on the current PCB sit on no net.

        The shared ``review_design`` reports how many; this names them,
        which is the difference between knowing there is a problem and
        being able to go and look at it.

        A pin with no net is not automatically a fault. A mechanical
        mounting pad, a no-connect leg and a genuinely forgotten
        connection all look identical here, so this reports and does not
        judge.
        """
        snapshot = _call("design.snapshot", timeout=120.0)
        if not snapshot.get("ok"):
            return snapshot

        pins = snapshot.get("pins") or []
        unconnected = [
            {"designator": p.get("designator", ""), "pin": p.get("pin", "")}
            for p in pins if not (p.get("net") or "").strip()
        ]
        by_part: dict[str, int] = {}
        for entry in unconnected:
            by_part[entry["designator"]] = by_part.get(
                entry["designator"], 0) + 1

        return {
            "ok": True,
            "command": "design.snapshot",
            "verified_live": snapshot.get("verified_live"),
            "unconnected_count": len(unconnected),
            "total_pins": len(pins),
            "unconnected_pins": unconnected,
            "by_designator": dict(sorted(by_part.items())),
        }

    @mcp.tool()
    async def easyeda_compare_schematic_pcb() -> dict[str, Any]:
        """Do the schematic and the board hold the same components?

        Compared by designator, which is the only identifier both sides
        carry. Two lists that disagree mean the board has not been
        updated from the schematic, or has been updated and edited
        since.

        This is this project's comparison, not the editor's, and the
        reason is narrower than "EasyEDA has none".

        The editor does carry comparison calls. ``sys_Tool`` documents
        ``netlistComparison(netlist1, netlist2)``, marked beta with an
        unspecified return, and a live capture also shows
        ``pcbComparison`` and ``schematicComparison``, which the
        published reference does not describe at all. All three take
        two artefacts the CALLER supplies rather than reading the open
        design, so they answer a different question from this one.

        The other candidate, ``import_schematic_changes``, APPLIES the
        schematic rather than reporting on it: running it to find out
        what differs would change the board to answer a question about
        it.

        So this compares designator lists itself. Reaching for
        ``netlistComparison`` would mean exporting both netlists first
        and trusting an unspecified reply shape.
        """
        schematic = _call("sch.components", timeout=60.0)
        if not schematic.get("ok"):
            return schematic
        board = _call("pcb.components", timeout=60.0)
        if not board.get("ok"):
            return board

        def _designators(payload: dict, key: str) -> tuple[dict, int, int]:
            """(by designator, sheets skipped, items with no designator).

            The component list
            contains SHEETS as well as parts, told apart by
            ``componentType``, and a part's ``name`` is a display
            FORMULA (``={Partnumber}``) rather than a designator. The
            earlier version fell back to ``name``, so a part missing its
            designator was keyed by that formula and turned up as a
            component present in the schematic and absent from the
            board: a difference that does not exist.
            """
            out: dict[str, dict] = {}
            sheets = unidentified = 0
            for item in payload.get(key) or []:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("componentType") or "").strip().lower()
                if kind and kind != "part":
                    sheets += 1
                    continue
                designator = str(item.get("designator") or "").strip()
                if not designator:
                    # Counted, never guessed at. A part with no
                    # designator cannot be matched, and saying so beats
                    # inventing a key that always mismatches.
                    unidentified += 1
                    continue
                out[designator] = item
            return out, sheets, unidentified

        sch, sch_sheets, sch_unnamed = _designators(schematic, "components")
        pcb, _, pcb_unnamed = _designators(board, "components")

        only_schematic = sorted(set(sch) - set(pcb))
        only_board = sorted(set(pcb) - set(sch))

        return {
            "ok": True,
            "verified_live": board.get("verified_live"),
            "schematic_count": len(sch),
            "pcb_count": len(pcb),
            "sheets_skipped": sch_sheets,
            "schematic_without_designator": sch_unnamed,
            "pcb_without_designator": pcb_unnamed,
            "in_schematic_only": only_schematic,
            "in_pcb_only": only_board,
            "matched": len(set(sch) & set(pcb)),
            "consistent": not only_schematic and not only_board,
            "note": ("a part with no designator is counted, not matched: "
                     "an unannotated schematic reports differences that "
                     "annotation, not editing, resolves"),
        }

    # ---- authoring library items ------------------------------------

    @mcp.tool()
    async def easyeda_create_symbol(
        library_uuid: str, name: str, description: str = "",
    ) -> dict[str, Any]:
        """Create an empty symbol in a library, and return its uuid.

        A drawing, not a placeable part. Placing needs a DEVICE, which
        binds a symbol to a footprint: see
        ``easyeda_create_device``. Creating symbols and footprints and
        stopping there leaves a library nobody can place from, which
        looks like progress.

        Args:
            library_uuid: which library, from
                ``easyeda_list_libraries``. There is no default: a part
                created in the wrong library is found by nobody looking
                for it.
            name: the symbol name.
            description: free text.
        """
        if not library_uuid.strip():
            return {"ok": False, "reason": "library_uuid is required"}
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        params: dict[str, Any] = {
            "library_uuid": library_uuid, "name": name}
        if description:
            params["description"] = description
        return _call("lib.create_symbol", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_create_footprint(
        library_uuid: str, name: str, description: str = "",
    ) -> dict[str, Any]:
        """Create an empty footprint in a library, and return its uuid.

        Args:
            library_uuid: which library.
            name: the footprint name.
            description: free text.
        """
        if not library_uuid.strip():
            return {"ok": False, "reason": "library_uuid is required"}
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        params: dict[str, Any] = {
            "library_uuid": library_uuid, "name": name}
        if description:
            params["description"] = description
        return _call("lib.create_footprint", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_create_device(
        library_uuid: str, name: str,
        symbol_uuid: str = "", footprint_uuid: str = "",
        model_3d_uuid: str = "", description: str = "",
        symbol_library_uuid: str = "", footprint_library_uuid: str = "",
        model_3d_library_uuid: str = "",
    ) -> dict[str, Any]:
        """Bind a symbol and a footprint into a placeable device.

        This is the object the rest of this backend places. It is also
        where this project's atomic-parts standard lands: symbol,
        footprint and 3D model bound at the part level, so the BOM and
        the layout come out complete on the first pass rather than
        needing a footprint chosen again later.

        Refused with neither a symbol nor a footprint. The API accepts
        that and returns a uuid, so the empty shell would read as a
        created part until somebody tried to place it.

        Args:
            library_uuid: which library the device goes in.
            name: the device name.
            symbol_uuid: the symbol to bind.
            footprint_uuid: the footprint to bind.
            model_3d_uuid: the 3D model to bind.
            description: free text.
            symbol_library_uuid: the symbol's library, when it differs
                from the device's.
            footprint_library_uuid: likewise for the footprint.
            model_3d_library_uuid: likewise for the 3D model.
        """
        if not library_uuid.strip():
            return {"ok": False, "reason": "library_uuid is required"}
        if not name.strip():
            return {"ok": False, "reason": "name is required"}
        if not symbol_uuid.strip() and not footprint_uuid.strip():
            return {"ok": False, "reason": (
                "give at least symbol_uuid or footprint_uuid; a device "
                "bound to neither cannot be placed and would still "
                "report success")}
        params: dict[str, Any] = {
            "library_uuid": library_uuid, "name": name}
        for key, value in (
            ("symbol_uuid", symbol_uuid),
            ("footprint_uuid", footprint_uuid),
            ("model_3d_uuid", model_3d_uuid),
            ("description", description),
            ("symbol_library_uuid", symbol_library_uuid),
            ("footprint_library_uuid", footprint_library_uuid),
            ("model_3d_library_uuid", model_3d_library_uuid),
        ):
            if value:
                params[key] = value
        return _call("lib.create_device", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_add_polyline(
        points: list[list[float]], layer: str = "TOP", net: str = "",
        width: float | None = None, locked: bool = False,
    ) -> dict[str, Any]:
        """Draw a connected run of segments on the current PCB.

        One call instead of a line per segment, which matters for a
        routed run: every extra round trip is a chance for the board to
        be half-drawn if something fails partway.

        Args:
            points: at least two ``[x, y]`` pairs, in mils.
            layer: layer name.
            net: net the run belongs to.
            width: line width. The editor's default when omitted.
            locked: lock against interactive edits.
        """
        if len(points) < 2:
            return {"ok": False, "reason": (
                "points must be at least 2 [x, y] pairs")}
        params: dict[str, Any] = {
            "points": [list(p) for p in points],
            "layer": layer, "net": net, "locked": locked,
        }
        if width is not None:
            params["width"] = width
        return _call("pcb.add_polyline", params)

    @mcp.tool()
    async def easyeda_select_primitives(
        primitive_ids: list[str],
    ) -> dict[str, Any]:
        """Select primitives on the current PCB, by id.

        Args:
            primitive_ids: ids to select. Replaces the current
                selection rather than adding to it.
        """
        if not primitive_ids:
            return {"ok": False, "reason": "primitive_ids must not be empty"}
        return _call("pcb.select", {"primitive_ids": list(primitive_ids)})

    @mcp.tool()
    async def easyeda_get_board_outline() -> dict[str, Any]:
        """The board outline, as the segments drawn on its layer.

        EasyEDA has no outline object and no call that returns one: the
        outline is ordinary lines and arcs on the BOARD_OUTLINE layer.
        So this reports those segments and their extent rather than a
        shape, and the extent is a bounding box, not the board area. A
        rounded or cut-out board is smaller than its box.

        An empty result means nothing is drawn on that layer, which is a
        board with no outline rather than an error.
        """
        def _on_outline(item) -> bool:
            return (isinstance(item, dict)
                    and str(item.get("layer", "")).upper().replace(" ", "_")
                    in ("BOARD_OUTLINE", "11"))

        lines = _call("pcb.lines", timeout=60.0)
        if not lines.get("ok"):
            return lines
        segments = [i for i in (lines.get("lines") or []) if _on_outline(i)]

        # Arcs count. A rounded or routed board can draw its entire
        # outline as arcs with no straight segments at all, and reading
        # lines alone then reports no outline and disables every audit
        # that measures against the board edge.
        arcs_reply = _call("pcb.arcs", timeout=60.0)
        arcs = ([a for a in (arcs_reply.get("arcs") or []) if _on_outline(a)]
                if arcs_reply.get("ok") else [])
        segments = segments + arcs

        xs: list[float] = []
        ys: list[float] = []
        for item in segments:
            for key in ("startX", "endX"):
                value = item.get(key)
                if isinstance(value, (int, float)):
                    xs.append(float(value))
            for key in ("startY", "endY"):
                value = item.get(key)
                if isinstance(value, (int, float)):
                    ys.append(float(value))

        out: dict[str, Any] = {
            "ok": True,
            "verified_live": lines.get("verified_live"),
            "segment_count": len(segments),
            "arc_count": len(arcs),
            "segments": segments,
        }
        if arcs:
            # The box is built from arc ENDPOINTS, so a bulging arc
            # reads slightly small. That errs toward calling a pad
            # closer to the edge than it is, which is the safe
            # direction for a clearance check; overstating the board
            # would hide the very violations these audits look for.
            out["note"] = (
                f"{len(arcs)} of the outline segments are arcs, and the "
                f"bounding box uses their endpoints, so it may be "
                f"slightly smaller than the true outline")
        if xs and ys:
            out["bounding_box"] = {
                "min_x": min(xs), "min_y": min(ys),
                "max_x": max(xs), "max_y": max(ys),
                "width": max(xs) - min(xs), "height": max(ys) - min(ys),
            }
        return out

    @mcp.tool()
    async def easyeda_plan_placement(
        designators: Optional[list[str]] = None,
        fixed: Optional[list[str]] = None,
        region: Optional[dict[str, float]] = None,
        iterations: int = 400,
        grid_mils: float = 5.0,
        clearance_mils: float = 15.0,
        apply: bool = False,
        allow_worse: bool = False,
    ) -> dict[str, Any]:
        """Work out a better arrangement of the parts on the board.

        The solver is the same one the Altium side uses and has no EDA
        dependency at all: it takes components, nets and a region, and
        returns centroids that shorten total connection length while
        keeping parts from overlapping. Only the reading and writing is
        specific to this backend.

        PROPOSES BY DEFAULT. Nothing moves unless ``apply`` is true, so
        the normal use is to look at the numbers first: hpwl_before
        against hpwl_after says whether the arrangement is actually
        better, and overlap counts say whether it is legal.

        SIZES COME FROM THE BOUNDING BOX WHERE ONE CAN BE READ. A
        measured EasyEDA component reports position, rotation, layer and
        its pads, and no box, so the box is fetched separately. Where
        that fails the extent of the PADS is used instead, and the reply
        says so through ``size_source``, because pads understate a
        footprint: silkscreen and courtyard sit outside them, and
        courtyard is what collision is really about. A placement solved
        from understated sizes packs parts too tightly, and without the
        field nothing in the answer would reveal it.

        EasyEDA positions a part by its ORIGIN and the solver works in
        CENTROIDS, so both conversions happen here.

        Args:
            designators: only move these. Everything else still holds
                its place and pulls on its nets.
            fixed: never move these. Connectors, mounting holes,
                anything mechanically constrained.
            region: ``{"x1","y1","x2","y2"}`` in mils. Taken from the
                board outline when omitted.
            iterations: solver effort.
            grid_mils: snap resolution for the result.
            clearance_mils: breathing room between parts.
            apply: move the parts. Off by default.
        """
        from ..placement import (
            BoardRegion, PlaceComp, PlaceNet, PlaceOptions, PlacePin,
            plan_placement, rotate_offset,
        )

        reply = _call("pcb.components", timeout=60.0)
        if not reply.get("ok"):
            return reply
        raw = [c for c in (reply.get("components") or [])
               if isinstance(c, dict)]
        if not raw:
            return {"ok": False, "reason": (
                "the board reported no components. Check the PCB is the "
                "focused document: a schematic tab answers nothing here")}

        # Region, from the caller or from the outline.
        if region and all(k in region for k in ("x1", "y1", "x2", "y2")):
            bounds = {k: float(region[k]) for k in ("x1", "y1", "x2", "y2")}
        else:
            outline = await easyeda_get_board_outline()
            box = outline.get("bounding_box") if outline.get("ok") else None
            if not box:
                return {"ok": False, "reason": (
                    "no board outline was found, so there is nowhere to "
                    "place into. Draw an outline or pass an explicit "
                    "region {x1, y1, x2, y2} in mils")}
            bounds = {"x1": box["min_x"], "y1": box["min_y"],
                      "x2": box["max_x"], "y2": box["max_y"]}

        # Real bounding boxes where the editor will give them.
        ids = [str(c.get("primitiveId") or "") for c in raw]
        boxes: dict[str, dict] = {}
        if any(ids):
            got = _call("pcb.bboxes",
                        {"primitive_ids": [i for i in ids if i]},
                        timeout=60.0)
            if got.get("ok"):
                # The reply is {boxes: [{primitive_id, bbox}], measured,
                # of}, and each bbox is {minX, minY, maxX, maxY}. Both
                # the key and the corner names differ from the x1/y1
                # form used elsewhere in this file, so they are mapped
                # here rather than assumed.
                for entry in got.get("boxes") or []:
                    if not isinstance(entry, dict):
                        continue
                    key = str(entry.get("primitive_id")
                              or entry.get("primitiveId") or "")
                    box = entry.get("bbox")
                    if not key or not isinstance(box, dict):
                        continue
                    try:
                        boxes[key] = {
                            "x1": float(box["minX"]), "y1": float(box["minY"]),
                            "x2": float(box["maxX"]), "y2": float(box["maxY"]),
                        }
                    except (KeyError, TypeError, ValueError):
                        continue

        # Pin geometry comes from the FLAT pad read, matched to
        # components by position.
        #
        # The pads nested in a component carry a net and a pad number
        # and no coordinates, and their primitive ids share no values
        # with the flat pad list, so the two cannot be joined by id. A
        # bounding box does join them: a pad belongs to the component
        # whose box contains it. Without this every part is a point at
        # its centroid, the solver cannot tell where a net attaches,
        # and it cannot judge orientation at all.
        flat_pads = []
        pads_reply = _call("pcb.pads", timeout=60.0)
        if pads_reply.get("ok"):
            for pad in pads_reply.get("pads") or []:
                if not isinstance(pad, dict):
                    continue
                net = str(pad.get("net") or "").strip()
                if not net:
                    continue
                try:
                    flat_pads.append((float(pad["x"]), float(pad["y"]), net))
                except (KeyError, TypeError, ValueError):
                    continue

        chosen = {str(d).strip() for d in (designators or []) if str(d).strip()}
        pinned = {str(d).strip() for d in (fixed or []) if str(d).strip()}

        comps: list[PlaceComp] = []
        origins: dict[str, tuple[float, float]] = {}
        by_net: dict[str, set] = {}
        from_pads = 0
        from_box = 0
        skipped = 0

        for item in raw:
            ref = str(item.get("designator") or item.get("name") or "").strip()
            if not ref:
                skipped += 1
                continue
            try:
                ox = float(item.get("x"))
                oy = float(item.get("y"))
                rot = float(item.get("rotation") or 0)
            except (TypeError, ValueError):
                skipped += 1
                continue

            pads = [p for p in (item.get("pads") or []) if isinstance(p, dict)]

            # Net membership and pin geometry are collected separately
            # because the editor supplies them separately. A nested pad
            # carries net, padNumber and primitiveId, and no
            # coordinates, so requiring x and y here discards every pad
            # and leaves the solver with no nets at all: it then
            # optimises overlap and ignores wire length, which is not
            # placement.
            pad_nets = [str(p.get("net") or "").strip() for p in pads]
            pad_xy = []
            for pad in pads:
                try:
                    pad_xy.append((float(pad["x"]), float(pad["y"]),
                                   str(pad.get("net") or "")))
                except (KeyError, TypeError, ValueError):
                    continue

            box = boxes.get(str(item.get("primitiveId") or ""))
            width = height = None
            cx = cy = None
            x1 = y1 = x2 = y2 = 0.0
            if box:
                try:
                    x1, y1 = float(box["x1"]), float(box["y1"])
                    x2, y2 = float(box["x2"]), float(box["y2"])
                    width, height = abs(x2 - x1), abs(y2 - y1)
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    from_box += 1
                except (KeyError, TypeError, ValueError):
                    width = None
            if width is None and pad_xy:
                xs = [p[0] for p in pad_xy]
                ys = [p[1] for p in pad_xy]
                width, height = max(xs) - min(xs), max(ys) - min(ys)
                cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
                from_pads += 1
            if width is None:
                # No box and no readable pads: nothing to place with.
                skipped += 1
                continue

            # A one-pad or single-row part has a zero extent on one axis,
            # which would make it collide with nothing at all.
            width = max(float(width), 1.0)
            height = max(float(height), 1.0)

            # Pads inside this component's box, offset from its
            # centroid and un-rotated into the footprint frame.
            box_pads = [(px, py, net) for px, py, net in flat_pads
                        if x1 <= px <= x2 and y1 <= py <= y2] if box else []
            source_pads = pad_xy or box_pads
            pins = tuple(
                PlacePin(*rotate_offset(px - cx, py - cy, -rot), net=net)
                for px, py, net in source_pads if net
            )
            for net in pad_nets:
                if net:
                    by_net.setdefault(net, set()).add(ref)

            origins[ref] = (ox, oy, cx, cy, rot)
            comps.append(PlaceComp(
                ref=ref, w=width, h=height, cx=cx, cy=cy,
                layer=str(item.get("layer", "Top")),
                fixed=(ref in pinned
                       or (bool(chosen) and ref not in chosen)),
                rotation=rot, pins=pins,
                rotatable=bool(pins) and rot % 90 == 0,
            ))

        if len(comps) < 2:
            return {"ok": False, "reason": (
                f"only {len(comps)} placeable components were read; "
                f"there is nothing to arrange"),
                "skipped": skipped}

        nets = [PlaceNet(name=name, refs=tuple(sorted(refs)))
                for name, refs in by_net.items() if len(refs) > 1]

        result = plan_placement(
            comps, nets,
            BoardRegion(bounds["x1"], bounds["y1"],
                        bounds["x2"], bounds["y2"]),
            PlaceOptions(iterations=iterations, grid_mils=grid_mils,
                         clearance_mils=clearance_mils))

        moves = []
        for ref, (nx, ny) in result.positions.items():
            ox, oy, cx, cy, rot = origins[ref]
            dx, dy = nx - cx, ny - cy
            if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                continue
            moves.append({"designator": ref,
                          "from": {"x": round(ox, 4), "y": round(oy, 4)},
                          "to": {"x": round(ox + dx, 4),
                                 "y": round(oy + dy, 4)},
                          "delta": {"dx": round(dx, 4), "dy": round(dy, 4)}})
        moves.sort(key=lambda m: m["designator"])

        # Pin offsets need pad coordinates, which the component read
        # does not supply, so the solver treats each part as a point at
        # its centroid and does not re-orient anything. Reported rather
        # than left for a reader to infer from rotations that never
        # change.
        with_pins = sum(1 for c in comps if c.pins)
        out = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "components_placed": len(comps),
            "components_with_pin_geometry": with_pins,
            "nets_considered": len(nets),
            "region": bounds,
            "hpwl_before": round(result.hpwl_before, 2),
            "hpwl_after": round(result.hpwl_after, 2),
            "overlap_pairs_before": result.overlap_pairs_before,
            "overlap_pairs_after": result.overlap_pairs_after,
            "moves": moves,
            "move_count": len(moves),
            "skipped_components": skipped,
            "size_source": {"bounding_box": from_box, "pad_extent": from_pads},
            "notes": list(result.notes),
            "applied": False,
        }
        if from_pads:
            out["size_warning"] = (
                f"{from_pads} components were sized from their PADS "
                f"because no bounding box could be read. Pads understate "
                f"a footprint, so those parts may end up closer together "
                f"than the clearance suggests")
        # A result is not an improvement just because it is a result.
        #
        # The solver can return an arrangement with longer connections
        # and no fewer overlaps, particularly when it is stopped early.
        # Reporting that as a plan invites applying it, so the verdict
        # is stated and apply refuses unless the caller overrides it.
        shorter = result.hpwl_after <= result.hpwl_before
        cleaner = result.overlap_pairs_after <= result.overlap_pairs_before
        out["improved"] = bool(shorter and cleaner)
        if not out["improved"]:
            out["not_an_improvement"] = (
                f"connection length went "
                f"{round(result.hpwl_before)} to "
                f"{round(result.hpwl_after)} and overlapping pairs went "
                f"{result.overlap_pairs_before} to "
                f"{result.overlap_pairs_after}. Raise iterations, or "
                f"pass allow_worse=True to apply it anyway")

        if not moves:
            out["note"] = "the solver found nothing worth moving"
            return out
        if not apply:
            out["next"] = "re-run with apply=True to move the parts"
            return out

        if not out["improved"] and not allow_worse:
            return dict(out, ok=False, reason=out["not_an_improvement"])

        changes = []
        for move in moves:
            ref = move["designator"]
            primitive = next(
                (str(c.get("primitiveId")) for c in raw
                 if str(c.get("designator") or c.get("name") or "").strip()
                 == ref and c.get("primitiveId")), "")
            if not primitive:
                continue
            changes.append({"primitive_id": primitive,
                            "changes": {"x": move["to"]["x"],
                                        "y": move["to"]["y"]}})
        if len(changes) != len(moves):
            return dict(out, ok=False, reason=(
                f"{len(moves) - len(changes)} of {len(moves)} parts to "
                f"move reported no primitive id, so they cannot be "
                f"addressed; nothing was moved"))

        applied = _call("pcb.modify_components", {"changes": changes},
                        timeout=180.0)
        if not applied.get("ok"):
            return dict(out, ok=False, apply_result=applied)
        out["applied"] = True
        out["apply_result"] = applied
        return out

    @mcp.tool()
    async def easyeda_tune_length(
        net: str,
        add_length: float,
        x: float,
        y: float,
        layer: str = "TOP",
        amplitude: float = 40.0,
        pitch: float = 20.0,
        width: float = 6.0,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Add routed length to a net with a square serpentine.

        For matching a bus or a differential pair to a target length: a
        meander is laid at the point you choose, sized to add roughly
        the length you asked for, and the net's length is reported
        before and after.

        OPEN LOOP AND NOT DRC CHECKED, exactly as the Altium
        counterpart is, and for the same reason: neither editor offers
        a scriptable interactive tuner, so this is the approximation
        that is available rather than the tool anyone would design. You
        pick the spot; run a DRC afterwards.

        THE LENGTH IT ADDS IS NOT THE LENGTH YOU ASK FOR. A serpentine
        adds length in whole teeth, so the result is quantised to
        2*amplitude a tooth. Both numbers are reported, requested and
        achieved, because a tuner that silently rounds is a tuner that
        quietly misses a matching window.

        DRY RUN BY DEFAULT. This draws copper on a routed board.

        Args:
            net: the net to lengthen.
            add_length: how much to add, in mils. Achieved to the
                nearest tooth.
            x, y: where the meander starts.
            layer: layer to draw on.
            amplitude: how far each tooth reaches. Bigger teeth mean
                fewer of them and a coarser result.
            pitch: spacing between teeth along the run.
            width: track width. Match the net's existing width, or the
                meander is an impedance discontinuity.
            dry_run: report the geometry without drawing. On by default.
        """
        if not str(net or "").strip():
            return {"ok": False, "reason": "net is required"}
        if add_length <= 0:
            return {"ok": False, "reason": "add_length must be positive"}
        if amplitude <= 0 or pitch <= 0:
            return {"ok": False,
                    "reason": "amplitude and pitch must be positive"}
        if width <= 0:
            return {"ok": False, "reason": "width must be positive"}

        # One tooth is up, across, down, across. Against the straight
        # line it replaces, that is 2*amplitude of extra copper.
        per_tooth = 2.0 * amplitude
        teeth = int(round(add_length / per_tooth))
        if teeth < 1:
            return {"ok": False, "reason": (
                f"add_length={add_length} is less than one tooth of "
                f"{per_tooth} mils. Reduce the amplitude to tune more "
                f"finely")}
        achieved = teeth * per_tooth

        points: list[list[float]] = [[x, y]]
        cursor = x
        for index in range(teeth):
            reach = y + amplitude if index % 2 == 0 else y - amplitude
            points.append([cursor, reach])
            cursor += pitch
            points.append([cursor, reach])
        points.append([cursor, y])

        before = _call("pcb.net_length", {"net": net}, timeout=30.0)
        length_before = None
        if before.get("ok"):
            for key in ("length", "net_length", "total"):
                value = before.get(key)
                if isinstance(value, (int, float)):
                    length_before = float(value)
                    break

        result = {
            "ok": True,
            "net": net,
            "requested_add": add_length,
            "achieved_add": achieved,
            "teeth": teeth,
            "amplitude": amplitude,
            "pitch": pitch,
            "span": round(cursor - x, 4),
            "points": points,
            "length_before": length_before,
            "applied": False,
        }
        if abs(achieved - add_length) > 1e-9:
            result["note"] = (
                f"a serpentine adds length in whole teeth, so this adds "
                f"{achieved} rather than the {add_length} requested. "
                f"Reduce amplitude for a finer step")
        if dry_run:
            result["dry_run"] = True
            result["next"] = "re-run with dry_run=False to draw it"
            return result

        drawn = await easyeda_add_polyline(
            points=points, layer=layer, net=net, width=width)
        if not drawn.get("ok"):
            return dict(result, ok=False, apply_result=drawn)

        after = _call("pcb.net_length", {"net": net}, timeout=30.0)
        length_after = None
        if after.get("ok"):
            for key in ("length", "net_length", "total"):
                value = after.get(key)
                if isinstance(value, (int, float)):
                    length_after = float(value)
                    break
        result["applied"] = True
        result["length_after"] = length_after
        result["apply_result"] = drawn
        if length_before is not None and length_after is not None:
            result["measured_add"] = round(length_after - length_before, 4)
        result["check"] = (
            "not DRC checked: the meander was placed where you asked, "
            "not where it fits. Run a DRC")
        return result

    @mcp.tool()
    async def easyeda_place_stitching_vias(
        net: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        spacing: float = 50.0,
        diameter: float = 30.0,
        hole_diameter: float = 14.0,
        clearance: float = 10.0,
        dry_run: bool = True,
        max_vias: int = 400,
    ) -> dict[str, Any]:
        """Stitch a rectangle with vias on one net, usually ground.

        The standard EMC move: tie the reference planes together
        alongside a fast signal so return current has a short path, and
        around connectors and clock sources for the same reason. A
        return path that has to go the long way round is what turns a
        working board into one that fails emissions.

        DRY RUN BY DEFAULT, like the sliver cleanup and for the same
        reason: this adds copper in bulk, and seeing where before
        committing is how a wrong rectangle gets caught while it is
        still free to fix.

        A GRIDPOINT IS SKIPPED WHEN ANYTHING NOT ON THE NET IS WITHIN
        REACH. Reach is the via radius plus the clearance, measured
        against pads, other vias and track segments. Placing a ground
        via on top of a signal is not a DRC warning to clean up later;
        it is a short, and it is invisible on a plane pour.

        Same-net objects do not block, because a ground via next to a
        ground pad is the point of the exercise.

        WHAT THIS CANNOT SEE, and it matters: copper whose layer the
        editor will not resolve is left out of the check entirely and
        counted. Vias are through-holes and hit every layer, so any
        unchecked copper is a real risk rather than a formality.

        Args:
            net: the net to stitch, e.g. "GND".
            x1, y1, x2, y2: the rectangle, in mils, any corner order.
            spacing: grid pitch. A quarter of the shortest wavelength
                of interest is the usual rule; 50 mils is a common
                default for digital work.
            diameter: via pad diameter.
            hole_diameter: drill diameter. Must be smaller.
            clearance: extra space demanded around each via.
            dry_run: report the positions without placing. On by
                default.
            max_vias: refuse rather than place more than this. A
                mistyped rectangle is how a board gets a thousand vias.
        """
        import math

        if not str(net or "").strip():
            return {"ok": False, "reason": "net is required"}
        if diameter <= hole_diameter:
            return {"ok": False, "reason": (
                f"diameter {diameter} must exceed hole_diameter "
                f"{hole_diameter}, or the via has no annular ring")}
        if spacing <= 0:
            return {"ok": False, "reason": "spacing must be positive"}

        left, right = min(x1, x2), max(x1, x2)
        bottom, top = min(y1, y2), max(y1, y2)
        if right - left <= 0 or top - bottom <= 0:
            return {"ok": False,
                    "reason": "the rectangle has no area"}

        reach = diameter / 2.0 + clearance
        target = str(net).strip()

        # Everything that could be in the way. Pads and vias are points
        # with an extent; tracks are segments and need a distance to a
        # line, not to its endpoints, or a via lands mid-trace.
        table = _layer_table()
        blockers_xy: list[tuple[float, float, float]] = []
        segments: list[tuple[float, float, float, float, float]] = []
        unreadable = 0

        pads = _call("pcb.pads", timeout=60.0)
        if not pads.get("ok"):
            return pads
        for pad in pads.get("pads") or []:
            if not isinstance(pad, dict):
                continue
            if str(pad.get("net") or "").strip() == target:
                continue
            try:
                px, py = float(pad["x"]), float(pad["y"])
            except (KeyError, TypeError, ValueError):
                unreadable += 1
                continue
            shape = pad.get("pad")
            half = 0.0
            if isinstance(shape, list) and len(shape) >= 3:
                try:
                    half = max(float(shape[1]), float(shape[2])) / 2.0
                except (TypeError, ValueError):
                    half = 0.0
            blockers_xy.append((px, py, half))

        vias = _call("pcb.vias", timeout=60.0)
        for via in (vias.get("vias") or []) if vias.get("ok") else []:
            if not isinstance(via, dict):
                continue
            if str(via.get("net") or "").strip() == target:
                continue
            try:
                blockers_xy.append((
                    float(via["x"]), float(via["y"]),
                    float(via.get("size") or via.get("diameter") or 0) / 2.0))
            except (KeyError, TypeError, ValueError):
                unreadable += 1

        lines = _call("pcb.lines", timeout=60.0)
        for line in (lines.get("lines") or []) if lines.get("ok") else []:
            if not isinstance(line, dict):
                continue
            if str(line.get("net") or "").strip() == target:
                continue
            if _is_copper(line.get("layer"), table) is False:
                # Not copper, so not a short. Silkscreen under a via is
                # normal and blocking on it would refuse most of a
                # board. An UNKNOWN layer is not skipped here: it is
                # left in as a blocker, because a via is a through-hole
                # and being wrong in the permissive direction shorts a
                # board.
                continue
            try:
                segments.append((
                    float(line["startX"]), float(line["startY"]),
                    float(line["endX"]), float(line["endY"]),
                    float(line.get("width") or 0) / 2.0))
            except (KeyError, TypeError, ValueError):
                unreadable += 1

        def _clear(px: float, py: float) -> bool:
            for bx, by, half in blockers_xy:
                if math.hypot(px - bx, py - by) < reach + half:
                    return False
            for sx, sy, ex, ey, half in segments:
                dx, dy = ex - sx, ey - sy
                length2 = dx * dx + dy * dy
                if length2 <= 0:
                    near_x, near_y = sx, sy
                else:
                    # Distance to the SEGMENT, clamped to its ends. To
                    # the infinite line it would block vias nowhere
                    # near the track.
                    t = max(0.0, min(1.0, ((px - sx) * dx
                                           + (py - sy) * dy) / length2))
                    near_x, near_y = sx + t * dx, sy + t * dy
                if math.hypot(px - near_x, py - near_y) < reach + half:
                    return False
            return True

        positions = []
        blocked = 0
        y = bottom
        while y <= top:
            x = left
            while x <= right:
                if _clear(x, y):
                    positions.append({"x": round(x, 4), "y": round(y, 4)})
                else:
                    blocked += 1
                x += spacing
            y += spacing

        result = {
            "ok": True,
            "verified_live": pads.get("verified_live"),
            "net": target,
            "rectangle": {"x1": left, "y1": bottom, "x2": right, "y2": top},
            "spacing": spacing,
            "candidates": len(positions) + blocked,
            "clear": len(positions),
            "blocked": blocked,
            "unreadable_objects": unreadable,
            "positions": positions,
        }
        if unreadable:
            result["scope_warning"] = (
                f"{unreadable} objects could not be read, so they were "
                f"not checked against. A via is a through-hole and "
                f"reaches every layer, so anything unchecked is a "
                f"possible short rather than a formality")

        if not positions:
            result["note"] = (
                "every gridpoint was blocked; widen the rectangle, "
                "reduce the clearance, or check the net name")
            return result
        if len(positions) > max_vias:
            return dict(result, ok=False, reason=(
                f"{len(positions)} vias exceeds max_vias={max_vias}. "
                f"That is usually a rectangle in the wrong units or "
                f"the wrong place; raise the limit deliberately if it "
                f"is not"))
        if dry_run:
            result["dry_run"] = True
            result["next"] = "re-run with dry_run=False to place these"
            return result

        placed, failed = 0, []
        for spot in positions:
            reply = _call("pcb.add_via", {
                "x": spot["x"], "y": spot["y"],
                "hole_diameter": hole_diameter,
                "diameter": diameter, "net": target,
            }, timeout=30.0)
            if reply.get("ok"):
                placed += 1
            else:
                failed.append({"x": spot["x"], "y": spot["y"],
                               "reason": reply.get("reason")
                               or reply.get("unavailable")})
        result["placed"] = placed
        result["failed"] = failed
        if failed:
            # A partial stitch is not a stitch. Saying how far it got
            # is the difference between finishing the job and starting
            # it again from an unknown state.
            result["ok"] = False
            result["reason"] = (
                f"{placed} of {len(positions)} vias were placed; "
                f"{len(failed)} were refused")
        return result

    @mcp.tool()
    async def easyeda_route_plan(
        nets: Optional[list[str]] = None,
        clearance_mils: float = 8.0,
        track_width_mils: float = 10.0,
        via_size_mils: float = 24.0,
        via_drill_mils: float = 12.0,
    ) -> dict[str, Any]:
        """Route the board offline and return tracks and vias to place.

        The router is the one the Altium side uses (route_plan,
        pcb_autoroute) and has no EDA dependency: it takes pads, existing
        copper and a board outline, and returns segments checked by the
        exact DRC. Only the reading is specific to this backend.

        PLANS ONLY. Nothing is drawn. The result is in the shape
        easyeda_add_track and easyeda_add_via take, so applying it is a
        separate, visible step.

        LAYER SIDES COME FROM THE EDITOR, not from a table here. PCB
        reads carry a numeric layer, and which integer is top copper is
        not measured; getting it wrong would route on the wrong side of
        the board. The editor's own layer list is consulted, and
        anything it cannot resolve is EXCLUDED from the obstacle map
        with a count, rather than guessed onto a layer.

        That exclusion is the one thing to watch in the result. Copper
        left out of the obstacle map is copper the router will happily
        route through, so a non-zero ``obstacles_skipped`` means the
        plan may collide with something real.

        Args:
            nets: only route these. Everything else stays an obstacle.
            clearance_mils: copper-to-copper spacing.
            track_width_mils: width for new track.
            via_size_mils: via pad diameter.
            via_drill_mils: via hole diameter.
        """
        import asyncio

        from ..layout.route.geometry import plan

        pads_reply = _call("pcb.pads", timeout=60.0)
        if not pads_reply.get("ok"):
            return pads_reply

        outline = await easyeda_get_board_outline()
        box = outline.get("bounding_box") if outline.get("ok") else None
        if not box:
            return {"ok": False, "reason": (
                "no board outline was found, so the routing area is "
                "unknown. Draw an outline on the board-outline layer")}

        table = _layer_table()
        skipped = 0
        inner_copper = 0
        unresolved_layers: set = set()

        def _layer_word(raw, through_hole=False):
            """(router layer name, why) where the name may be None.

            The reason matters. A silkscreen line is EXPECTED to be
            absent from the obstacle map and is not a loss; a layer
            nobody can name is a real gap and has to be counted, or the
            warning fires on every board and stops meaning anything.
            """
            if through_hole:
                # Reaches every layer, which is what makes it an
                # obstacle on all of them.
                return "multilayer", "through"
            copper = _is_copper(raw, table)
            if copper is False:
                return None, "not-copper"
            side = _side_of_layer(raw, table)
            if side == "top":
                return "TopLayer", "copper"
            if side == "bottom":
                return "BottomLayer", "copper"
            if copper is True:
                # Inner copper. Real, in the way, and the router here
                # models two layers, so it cannot be represented rather
                # than merely being unknown. Counted as its own reason
                # so the reply can say which it is.
                return None, "inner-copper"
            return None, "unresolved"

        geom_pads = []
        for pad in pads_reply.get("pads") or []:
            if not isinstance(pad, dict):
                continue
            try:
                px, py = float(pad["x"]), float(pad["y"])
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            # Measured shape: pad: ["RECT", width, height, radius].
            shape = pad.get("pad")
            width = height = 0.0
            if isinstance(shape, list) and len(shape) >= 3:
                try:
                    width, height = float(shape[1]), float(shape[2])
                except (TypeError, ValueError):
                    width = height = 0.0
            if not width:
                for key in ("width", "x_size"):
                    value = pad.get(key)
                    if isinstance(value, (int, float)):
                        width = float(value)
                        break
            if not height:
                for key in ("height", "y_size"):
                    value = pad.get(key)
                    if isinstance(value, (int, float)):
                        height = float(value)
                        break
            through = pad.get("hole") not in (None, "", 0)
            word, why = _layer_word(pad.get("layer"), through)
            if word is None:
                # A pad is copper by definition, so even a "not-copper"
                # answer here means the layer was misread. Counted
                # either way.
                skipped += 1
                unresolved_layers.add(str(pad.get("layer")))
                continue
            geom_pads.append({
                "x": px, "y": py,
                "x_size": width or 1.0, "y_size": height or 1.0,
                "rotation": float(pad.get("rotation") or 0),
                "layer": word, "net": str(pad.get("net") or ""),
            })

        geom_tracks = []
        lines = _call("pcb.lines", timeout=60.0)
        for line in (lines.get("lines") or []) if lines.get("ok") else []:
            if not isinstance(line, dict):
                continue
            word, why = _layer_word(line.get("layer"))
            if word is None:
                if why == "unresolved":
                    skipped += 1
                    unresolved_layers.add(str(line.get("layer")))
                elif why == "inner-copper":
                    # Real copper on a layer this router does not
                    # model. Counted apart from "unresolved" because it
                    # is a different answer: not "nobody knows what
                    # this is" but "this is known, and known to be
                    # unrepresentable here".
                    inner_copper += 1
                # Silkscreen, mask and the board outline itself are not
                # copper and belong nowhere in the obstacle map, so
                # leaving them out is correct rather than a gap.
                # Counting them would make every board report skipped
                # obstacles, and a warning that fires always is one
                # nobody reads.
                continue
            try:
                geom_tracks.append({
                    "x1": float(line["startX"]), "y1": float(line["startY"]),
                    "x2": float(line["endX"]), "y2": float(line["endY"]),
                    "width": float(line.get("width") or track_width_mils),
                    "layer": word, "net": str(line.get("net") or ""),
                })
            except (KeyError, TypeError, ValueError):
                skipped += 1

        geom_vias = []
        vias = _call("pcb.vias", timeout=60.0)
        for via in (vias.get("vias") or []) if vias.get("ok") else []:
            if not isinstance(via, dict):
                continue
            try:
                geom_vias.append({
                    "x": float(via["x"]), "y": float(via["y"]),
                    "size": float(via.get("size")
                                  or via.get("diameter") or via_size_mils),
                    "net": str(via.get("net") or ""),
                })
            except (KeyError, TypeError, ValueError):
                skipped += 1

        if not geom_pads:
            return {"ok": False, "reason": (
                "no pads could be read with a resolvable layer, so "
                "there is nothing to route between"),
                "obstacles_skipped": skipped,
                "unresolved_layers": sorted(unresolved_layers)}

        geometry = {
            "bbox": {"x1": box["min_x"], "y1": box["min_y"],
                     "x2": box["max_x"], "y2": box["max_y"]},
            "pads": geom_pads, "tracks": geom_tracks, "vias": geom_vias,
        }
        rules = {
            "clearance_mils": clearance_mils,
            "track_width_mils": float(track_width_mils),
            "via_size_mils": via_size_mils,
            "via_drill_mils": via_drill_mils,
        }
        wanted = None if nets is None else [str(n) for n in nets if str(n).strip()]
        try:
            result = await asyncio.to_thread(plan, geometry, rules, wanted)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "reason": str(exc)}
        if not result.get("ok"):
            return result
        unknown = result.pop("unknown_nets", [])
        result["pads_read"] = len(geom_pads)
        result["pads_read"] = len(geom_pads)
        result["existing_tracks"] = len(geom_tracks)
        result["existing_vias"] = len(geom_vias)
        result["obstacles_skipped"] = skipped
        result["inner_copper_ignored"] = inner_copper
        if nets is not None:
            result["requested_nets"] = sorted(
                {str(n) for n in nets if str(n).strip()})
            result["unknown_nets"] = unknown
        if inner_copper:
            # Louder than the unresolved warning, because this one is
            # certain rather than suspected. A stackup can carry many
            # inner copper layers, so on anything but a two-layer board
            # this is most of the copper and a plan that ignores it is
            # not a plan.
            result["ok"] = False
            result["reason"] = (
                f"{inner_copper} segments sit on inner copper layers, "
                f"which this router does not model: it plans on top and "
                f"bottom only. A plan that ignores inner copper would "
                f"route through it. Use it for a two-layer board, or "
                f"route the inner layers in the editor")
        if skipped:
            result["unresolved_layers"] = sorted(unresolved_layers)
            result["scope_warning"] = (
                f"{skipped} objects were left out of the obstacle map "
                f"because their layer could not be resolved. The router "
                f"will route through anything it cannot see, so check "
                f"the plan against those layers before applying it")
        return result

    @mcp.tool()
    async def easyeda_audit_pads_near_board_edge(
        clearance: float = 20.0,
    ) -> dict[str, Any]:
        """Pads too close to the board edge to be made reliably.

        Distance is measured to the OUTLINE SEGMENTS, not to a bounding
        box. A box overstates a rounded or routed-out board, and it
        overstates it in the dangerous direction: a pad that sits close
        to a real curved edge reads as comfortably inside the box, so
        the check would miss exactly the violations it exists to find.

        Pad SIZE is a different matter. EasyEDA carries it in a shape
        object whose structure is not published, so where a width and
        height can be read the distance is copper-to-edge, and where
        they cannot it is centre-to-edge and understates the risk. Every
        result says which it is, and the summary counts them, so a run
        that could read no sizes is visible rather than quietly
        optimistic.

        Args:
            clearance: the minimum acceptable distance, in mils.
        """
        if clearance <= 0:
            return {"ok": False, "reason": "clearance must be positive"}

        outline = await easyeda_get_board_outline()
        if not outline.get("ok"):
            return outline
        segments = outline.get("segments") or []
        if not segments:
            return {"ok": False, "reason": (
                "no board outline is drawn, so there is no edge to "
                "measure against")}

        pads = _call("pcb.pads", timeout=60.0)
        if not pads.get("ok"):
            return pads

        edges = _edges_from(segments)
        if not edges:
            return {"ok": False, "reason": (
                "the outline segments carry no usable coordinates")}

        def _half_extent(pad: dict) -> float | None:
            """Half the pad's larger dimension, or None if unreadable.

            The editor reports the shape as a LIST of
            [shape, width, height, radius], not as a mapping. Reading
            only the mapping form leaves every pad unsized, and the
            audit then measures from the pad CENTRE: copper that
            crosses the board edge reports the clearance its centre
            has, which is the direction that hides the violation.

            The mapping form is still accepted, since nothing rules out
            another build reporting it that way.
            """
            shape = pad.get("pad")
            if isinstance(shape, list) and len(shape) >= 3:
                try:
                    return max(float(shape[1]), float(shape[2])) / 2.0
                except (TypeError, ValueError):
                    return None
            if isinstance(shape, dict):
                for wide, high in (("width", "height"), ("w", "h")):
                    if isinstance(shape.get(wide), (int, float)) and \
                            isinstance(shape.get(high), (int, float)):
                        return max(float(shape[wide]),
                                   float(shape[high])) / 2.0
            return None

        violations = []
        sized = 0
        checked = 0
        for pad in pads.get("pads") or []:
            if not isinstance(pad, dict):
                continue
            x, y = pad.get("x"), pad.get("y")
            if not isinstance(x, (int, float)) or \
                    not isinstance(y, (int, float)):
                continue
            checked += 1
            distance = min(_point_to_segment(float(x), float(y), *e)
                           for e in edges)
            half = _half_extent(pad)
            if half is not None:
                sized += 1
                distance -= half
            if distance < clearance:
                violations.append({
                    "pad": pad.get("padNumber", ""),
                    "net": pad.get("net", ""),
                    "x": float(x), "y": float(y),
                    "distance": round(distance, 3),
                    "measured_from": "copper" if half is not None
                    else "centre",
                })

        return {
            "ok": True,
            "verified_live": pads.get("verified_live"),
            "clearance": clearance,
            "pads_checked": checked,
            "pads_with_a_readable_size": sized,
            "violation_count": len(violations),
            "violations": sorted(violations, key=lambda v: v["distance"]),
        }

    @mcp.tool()
    async def easyeda_open_symbol(
        uuid: str, library_uuid: str,
    ) -> dict[str, Any]:
        """Open a library symbol for editing, and make it the active
        document.

        This is how a symbol gets its geometry on this backend. There is
        no separate library-drawing API: open the symbol, then use the
        ordinary schematic drawing tools (``easyeda_add_wire``,
        ``easyeda_add_schematic_rectangle``, and the rest), which now
        act on the symbol rather than on a sheet.

        It changes what every other tool is looking at, so anything sent
        straight after may land on a document still loading.

        Args:
            uuid: the symbol.
            library_uuid: the library it lives in.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        return _call("lib.open_symbol",
                     {"uuid": uuid, "library_uuid": library_uuid},
                     timeout=60.0)

    @mcp.tool()
    async def easyeda_open_footprint(
        uuid: str, library_uuid: str,
    ) -> dict[str, Any]:
        """Open a library footprint for editing.

        The PCB counterpart of ``easyeda_open_symbol``: open it, then
        the ordinary PCB drawing tools act on the footprint.

        Args:
            uuid: the footprint.
            library_uuid: the library it lives in.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        return _call("lib.open_footprint",
                     {"uuid": uuid, "library_uuid": library_uuid},
                     timeout=60.0)

    @mcp.tool()
    async def easyeda_rename_symbol(
        uuid: str, library_uuid: str,
        name: str = "", description: str = "",
    ) -> dict[str, Any]:
        """Rename a library symbol or change its description.

        Args:
            uuid: the symbol.
            library_uuid: the library it lives in.
            name: the new name. Left alone when empty.
            description: the new description. Left alone when empty.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not name.strip() and not description.strip():
            return {"ok": False, "reason": (
                "give a name or a description; an empty change reports "
                "success while doing nothing")}
        return _call("lib.modify_symbol", {
            "uuid": uuid, "library_uuid": library_uuid,
            "name": name, "description": description,
        })

    @mcp.tool()
    async def easyeda_rename_footprint(
        uuid: str, library_uuid: str,
        name: str = "", description: str = "",
    ) -> dict[str, Any]:
        """Rename a library footprint or change its description.

        Args:
            uuid: the footprint.
            library_uuid: the library it lives in.
            name: the new name. Left alone when empty.
            description: the new description. Left alone when empty.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not name.strip() and not description.strip():
            return {"ok": False, "reason": (
                "give a name or a description; an empty change reports "
                "success while doing nothing")}
        return _call("lib.modify_footprint", {
            "uuid": uuid, "library_uuid": library_uuid,
            "name": name, "description": description,
        })

    @mcp.tool()
    async def easyeda_copy_symbol(
        uuid: str, library_uuid: str, target_library_uuid: str,
        new_name: str = "",
    ) -> dict[str, Any]:
        """Copy a symbol into another library.

        The usual way to start from a vendor part rather than a blank
        one: copy it into your own library first, then edit the copy, so
        the original stays as shipped.

        Args:
            uuid: the symbol to copy.
            library_uuid: the library it currently lives in.
            target_library_uuid: where the copy goes.
            new_name: name for the copy. Keeps the original name when
                empty.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not target_library_uuid.strip():
            return {"ok": False, "reason": (
                "target_library_uuid is required")}
        return _call("lib.copy_symbol", {
            "uuid": uuid, "library_uuid": library_uuid,
            "target_library_uuid": target_library_uuid,
            "new_name": new_name,
        }, timeout=60.0)

    @mcp.tool()
    async def easyeda_copy_footprint(
        uuid: str, library_uuid: str, target_library_uuid: str,
        new_name: str = "",
    ) -> dict[str, Any]:
        """Copy a footprint into another library.

        Args:
            uuid: the footprint to copy.
            library_uuid: the library it currently lives in.
            target_library_uuid: where the copy goes.
            new_name: name for the copy. Keeps the original name when
                empty.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not target_library_uuid.strip():
            return {"ok": False, "reason": (
                "target_library_uuid is required")}
        return _call("lib.copy_footprint", {
            "uuid": uuid, "library_uuid": library_uuid,
            "target_library_uuid": target_library_uuid,
            "new_name": new_name,
        }, timeout=60.0)

    @mcp.tool()
    async def easyeda_get_embedded_objects() -> dict[str, Any]:
        """Binary embedded objects on the current PCB.

        Not the same as ``easyeda_get_images``, despite the name.
        EasyEDA keeps colour-silkscreen artwork as embedded binary
        objects whose payload is data rather than geometry, and they
        are a fabrication question of their own: a fabricator who
        cannot process them prints nothing where the artwork was.
        """
        return _call("pcb.embedded_objects")

    @mcp.tool()
    async def easyeda_add_schematic_arc(
        start_x: float, start_y: float,
        reference_x: float, reference_y: float,
        end_x: float, end_y: float,
    ) -> dict[str, Any]:
        """Draw an arc on the current schematic, through three points.

        Three points on the curve, NOT a centre and a sweep. The middle
        pair is a point the arc passes through. Passing a centre there
        draws a plausible-looking curve in the wrong place, which is the
        kind of mistake that survives a glance at the sheet.

        Args:
            start_x: start x, in mils.
            start_y: start y, in mils.
            reference_x: x of a point the arc passes THROUGH.
            reference_y: y of that point.
            end_x: end x, in mils.
            end_y: end y, in mils.
        """
        return _call("sch.add_arc", {
            "start_x": _sch(start_x), "start_y": _sch(start_y),
            "reference_x": _sch(reference_x),
            "reference_y": _sch(reference_y),
            "end_x": _sch(end_x), "end_y": _sch(end_y),
        })

    @mcp.tool()
    async def easyeda_get_library_classifications(
        library_uuid: str, kind: str = "SYMBOL",
    ) -> dict[str, Any]:
        """The category tree of one library.

        Worth reading before creating a part: a symbol or footprint made
        with no classification lands uncategorised, where it is found
        only by someone who already knows its name.

        Args:
            library_uuid: which library.
            kind: what the library holds: CBB, SYMBOL, DEVICE,
                FOOTPRINT, MODEL or PANEL_LIBRARY.
        """
        if not library_uuid.strip():
            return {"ok": False, "reason": "library_uuid is required"}
        return _call("lib.classifications",
                     {"library_uuid": library_uuid, "kind": kind})

    #: The tools an emitted plan can name, mapped to the functions that
    #: run them. A separate table from the emitter on purpose: the
    #: emitter produces names as data, and something has to turn a name
    #: back into a call. A guard checks the two agree, because a name
    #: missing here fails halfway through a run, after the earlier steps
    #: have already changed the design.
    _RUNNABLE = {
        "easyeda_ping": easyeda_ping,
        "easyeda_search_devices": easyeda_search_devices,
        "easyeda_place_schematic_component": easyeda_place_schematic_component,
        "easyeda_add_wire": easyeda_add_wire,
        "easyeda_create_net_label": easyeda_create_net_label,
        "easyeda_create_net_flag": easyeda_create_net_flag,
    }

    @mcp.tool()
    async def easyeda_run_plan(
        calls: list[dict[str, Any]], confirm: bool = False,
    ) -> dict[str, Any]:
        """Run a sequence produced by the emit tools, in order.

        DESTRUCTIVE: it places parts and draws on the open document.
        Refused unless ``confirm`` is true.

        Takes the ``calls`` list from ``easyeda_emit_template`` or
        ``easyeda_emit_template_connections`` rather than a plan, so what runs is
        exactly what was reviewed. Passing a plan here instead would
        re-emit it, and a sequence that was read and a sequence that
        ran would be two different things.

        STOPS AT THE FIRST FAILURE. Continuing would leave a design that
        is part-built and looks built: the remaining steps would place
        parts around a hole where one is missing, and the result reads
        as a finished schematic with a mistake in it rather than as a
        run that stopped.

        Args:
            calls: the ordered ``{"tool": ..., "arguments": {...}}``
                entries from an emit tool.
            confirm: must be true for anything to happen.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "run_plan places parts and draws on the open document. "
                "Pass confirm=True if that is intended.")}
        if not calls:
            return {"ok": False, "reason": "calls must not be empty"}

        unknown = sorted({
            str(c.get("tool", "")) for c in calls
            if str(c.get("tool", "")) not in _RUNNABLE
        })
        if unknown:
            # Checked before anything runs. Finding it halfway would
            # leave the design half-changed.
            return {"ok": False, "reason": (
                f"these steps name tools this runner cannot call: "
                f"{unknown}"), "ran": 0}

        # Which single-item steps have a bulk form, and what that form
        # calls its list. Both passes of a build are covered: placement
        # emits one step per part, connection one per wire, and a
        # design of any size pays a round trip for every one.
        coalescible = {
            "easyeda_place_schematic_component": (
                easyeda_place_schematic_components, "components"),
            "easyeda_add_wire": (easyeda_add_wires, "wires"),
        }

        results: list[dict[str, Any]] = []
        index = 0
        while index < len(calls):
            name = str(calls[index]["tool"])

            # A RUN of consecutive identical steps goes in one call.
            # Only consecutive ones: reordering steps around a wire or a
            # checkpoint would change what the plan does.
            run_end = index
            if name in coalescible:
                while (run_end < len(calls)
                       and str(calls[run_end]["tool"]) == name):
                    run_end += 1

            if run_end - index >= 2:
                bulk, key = coalescible[name]
                batch = [c.get("arguments") or {} for c in calls[index:run_end]]
                outcome = await bulk(**{key: batch})
                # Per-item results map back onto the original step
                # numbers. Reporting the batch as one step would turn
                # "step 12 failed, 11 applied" into "the batch failed",
                # which is what makes a half-built design hard to
                # recover from.
                items = outcome.get("results") or []
                for offset in range(run_end - index):
                    item = items[offset] if offset < len(items) else None
                    step_ok = bool(outcome.get("ok")) and bool(
                        item and item.get("ok"))
                    results.append({
                        "step": index + offset,
                        "tool": name,
                        "batched": True,
                        "result": item if item is not None else outcome,
                    })
                    if not step_ok:
                        return {
                            "ok": False,
                            "reason": (
                                f"step {index + offset} ({name}) failed "
                                f"inside a batched run of {run_end - index}, "
                                f"so the run stopped there"),
                            "ran": index + offset + 1,
                            "of": len(calls),
                            "results": results,
                        }
                index = run_end
                continue

            arguments = calls[index].get("arguments") or {}
            outcome = await _RUNNABLE[name](**arguments)
            results.append({"step": index, "tool": name, "result": outcome})
            if not outcome.get("ok"):
                return {
                    "ok": False,
                    "reason": (
                        f"step {index} ({name}) failed, so the run "
                        f"stopped there rather than building around it"),
                    "ran": index + 1,
                    "of": len(calls),
                    "results": results,
                }
            index += 1

        return {"ok": True, "ran": len(results), "of": len(calls),
                "results": results}

    # ---- checkpoints -------------------------------------------------
    #
    # The Altium side snapshots the project DIRECTORY, because it has
    # one it can reach. This backend does not: the extension is
    # sandboxed and the server has no path in common with it. What it
    # does have is the whole open document as a string, so a checkpoint
    # here is that string, written to the workspace.
    #
    # The limit that follows is worth stating rather than discovering: it
    # captures ONE document, the open one, not the project.

    def _checkpoint_dir():
        from ..config import get_config

        path = get_config().workspace_dir / "easyeda_checkpoints"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @mcp.tool()
    async def easyeda_checkpoint(label: str = "") -> dict[str, Any]:
        """Save the open document so a later change can be undone.

        Worth taking before ``easyeda_run_plan`` or any of the
        confirm-guarded tools, which is the whole reason it exists.

        Captures ONE document, the open one, not the project. The Altium
        side snapshots a whole project directory; this backend has no
        directory the server can reach, so what is saved is the document
        the editor currently has.

        Args:
            label: a note to recognise it by later.
        """
        import json
        import time

        captured = _call("sys.document_source", timeout=120.0)
        if not captured.get("ok"):
            return captured

        source = captured.get("source")
        if not isinstance(source, str) or not source:
            # An empty source would restore an empty document, which is
            # a worse outcome than not having a checkpoint at all.
            return {"ok": False, "reason": (
                "the editor returned no document source, so there is "
                "nothing to restore from and no checkpoint was made")}

        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        checkpoint_id = f"{stamp}-{abs(hash(source)) % 10000:04d}"
        record = {
            "id": checkpoint_id,
            "label": label,
            "document": captured.get("document", ""),
            "name": captured.get("name", ""),
            "captured": stamp,
            "source": source,
        }
        path = _checkpoint_dir() / f"{checkpoint_id}.json"
        path.write_text(json.dumps(record), encoding="utf-8")

        return {
            "ok": True,
            "checkpoint_id": checkpoint_id,
            "label": label,
            "document": record["document"],
            "name": record["name"],
            "size": len(source),
        }

    @mcp.tool()
    async def easyeda_list_checkpoints() -> dict[str, Any]:
        """Checkpoints taken on this machine, newest first."""
        import json

        out = []
        for path in sorted(_checkpoint_dir().glob("*.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt file is reported rather than skipped: a
                # checkpoint that cannot be read is exactly what someone
                # needs to know before relying on it.
                out.append({"id": path.stem, "unreadable": True})
                continue
            out.append({
                "id": record.get("id", path.stem),
                "label": record.get("label", ""),
                "document": record.get("document", ""),
                "name": record.get("name", ""),
                "captured": record.get("captured", ""),
                "size": len(record.get("source", "")),
            })
        return {"ok": True, "checkpoints": out, "count": len(out)}

    @mcp.tool()
    async def easyeda_restore_checkpoint(
        checkpoint_id: str, confirm: bool = False, force: bool = False,
    ) -> dict[str, Any]:
        """Put a saved document back, replacing what is open.

        DESTRUCTIVE: it replaces the whole open document. Refused
        unless ``confirm`` is true.

        Refused as well when the open document is not the one the
        checkpoint came from. Restoring a snapshot of board A over board
        B destroys B and looks like a successful restore, which is the
        worst shape a safety net can fail in. ``force`` overrides that
        for the case where the document was legitimately renamed.

        Args:
            checkpoint_id: from ``easyeda_list_checkpoints``.
            confirm: must be true for anything to happen.
            force: restore even onto a different document.
        """
        import json

        if not confirm:
            return {"ok": False, "reason": (
                "restore_checkpoint replaces the whole open document. "
                "Pass confirm=True if that is intended.")}

        path = _checkpoint_dir() / f"{checkpoint_id}.json"
        if not path.is_file():
            return {"ok": False, "reason": (
                f"no checkpoint {checkpoint_id!r}")}
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "reason": f"checkpoint unreadable: {exc}"}

        source = record.get("source")
        if not isinstance(source, str) or not source:
            return {"ok": False, "reason": (
                "the checkpoint holds no source; restoring it would "
                "empty the document")}

        if not force:
            current = _call("sys.document_source", timeout=120.0)
            if not current.get("ok"):
                return current
            here = str(current.get("name") or "")
            there = str(record.get("name") or "")
            if there and here and here != there:
                return {"ok": False, "reason": (
                    f"this checkpoint was taken on {there!r} and the open "
                    f"document is {here!r}. Restoring would replace a "
                    f"different design and report success. Pass "
                    f"force=True only if it was renamed.")}

        return _call("sys.set_document_source",
                     {"source": source, "confirm": True}, timeout=180.0)

    # ---- removing documents ------------------------------------------
    #
    # The Altium side has proj_remove_document, proj_delete_sheet and
    # proj_close; EasyEDA had nothing. These exist because a live
    # capabilities read confirmed the methods behind them.
    # Four other project-level gaps measured that day (annotate, variant
    # management, replace-component, project parameters) have no method
    # on any of the 92 classes, so they are not written rather than not
    # yet written.

    @mcp.tool()
    async def easyeda_delete_schematic(
        uuid: str, confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete a schematic and every page in it.

        DESTRUCTIVE. Refused unless ``confirm`` is true, and the
        extension refuses independently, so a caller that forgets on one
        side is still stopped on the other.

        Take a checkpoint first: ``easyeda_checkpoint`` saves the open
        document, and this removes a whole schematic, which no
        checkpoint here can put back.

        Args:
            uuid: the schematic to delete; list them with
                ``easyeda_list_schematics``.
            confirm: must be true for anything to happen.
        """
        if not uuid.strip():
            return {"ok": False, "reason": (
                "uuid is required; list them with easyeda_list_schematics")}
        if not confirm:
            return {"ok": False, "reason": (
                "delete_schematic removes the schematic and every page in "
                "it. Pass confirm=True if that is intended.")}
        return _call("proj.delete_schematic",
                     {"uuid": uuid, "confirm": True}, timeout=30.0)

    @mcp.tool()
    async def easyeda_delete_schematic_page(
        uuid: str, confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete one page of a schematic.

        DESTRUCTIVE, and the narrower of the two: this removes a single
        sheet rather than the whole schematic.

        Args:
            uuid: the page; list them with
                ``easyeda_list_schematic_pages``.
            confirm: must be true for anything to happen.
        """
        if not uuid.strip():
            return {"ok": False, "reason": (
                "uuid is required; list pages with "
                "easyeda_list_schematic_pages")}
        if not confirm:
            return {"ok": False, "reason": (
                "delete_schematic_page removes the page and everything "
                "drawn on it. Pass confirm=True if that is intended.")}
        return _call("proj.delete_schematic_page",
                     {"uuid": uuid, "confirm": True}, timeout=30.0)

    @mcp.tool()
    async def easyeda_delete_pcb(
        uuid: str, confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete a board, including its routing.

        DESTRUCTIVE. The routing is the expensive part: a board deleted
        by mistake is hours of work, and nothing on this side can
        restore it.

        Args:
            uuid: the board; list them with ``easyeda_list_boards``.
            confirm: must be true for anything to happen.
        """
        if not uuid.strip():
            return {"ok": False, "reason": (
                "uuid is required; list boards with easyeda_list_boards")}
        if not confirm:
            return {"ok": False, "reason": (
                "delete_pcb removes the board, including its routing. "
                "Pass confirm=True if that is intended.")}
        return _call("proj.delete_pcb",
                     {"uuid": uuid, "confirm": True}, timeout=30.0)

    @mcp.tool()
    async def easyeda_delete_project(
        uuid: str, confirm: bool = False,
    ) -> dict[str, Any]:
        """NOT POSSIBLE on EasyEDA. Delete the parts, or use the UI.

        EasyEDA'S API CANNOT DELETE A PROJECT. ``dmt_Project`` offers
        create, open, move and the info reads, and no other class
        deletes one either: the document tree can remove a board, a
        folder, a panel, a PCB, a schematic and a schematic page, but
        never the project itself.

        This previously called a method that does not exist, so it threw
        a TypeError AFTER the caller had confirmed a destructive
        operation. It now refuses up front and says what can be deleted.

        Use ``easyeda_delete_schematic``, ``easyeda_delete_pcb`` or
        ``easyeda_delete_panel`` for the contents, and the EasyEDA UI
        for the project.

        Args:
            uuid: accepted and unused, so an existing caller gets the
                explanation rather than a signature error.
            confirm: accepted and unused, for the same reason.
        """
        return {"ok": False, "reason": (
            "EasyEDA has no project-delete API. dmt_Project offers "
            "create, open, move and the info reads, and nothing "
            "anywhere deletes a project. Delete the contents with "
            "easyeda_delete_schematic, easyeda_delete_pcb or "
            "easyeda_delete_panel, and the project itself from the "
            "EasyEDA UI.")}

    @mcp.tool()
    async def easyeda_close_document(uuid: str) -> dict[str, Any]:
        """Close an open document without deleting anything.

        The counterpart to ``easyeda_open_document``, and deliberately
        not confirm-guarded: nothing is removed, and what happens to
        unsaved changes is the editor's own business.

        Args:
            uuid: the document to close.
        """
        if not uuid.strip():
            return {"ok": False, "reason": "uuid is required"}
        return _call("editor.close_document", {"uuid": uuid})

    # ---- creating documents ------------------------------------------

    @mcp.tool()
    async def easyeda_create_schematic(name: str = "") -> dict[str, Any]:
        """Add a schematic to the open project.

        Part of the from-scratch path: create the project, then a
        schematic and a board inside it. Without these the backend can
        only work on something a human made first, which is the
        difference between editing a design and authoring one.

        Args:
            name: what to call it. The editor picks a default when
                empty.
        """
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        return _call("proj.create_schematic", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_create_schematic_page(uuid: str) -> dict[str, Any]:
        """Add a page to an existing schematic.

        Args:
            uuid: the schematic, from ``easyeda_list_schematics``.
        """
        if not uuid.strip():
            return {"ok": False, "reason": "uuid is required"}
        return _call("proj.create_schematic_page", {"uuid": uuid},
                     timeout=60.0)

    @mcp.tool()
    async def easyeda_create_pcb(name: str = "") -> dict[str, Any]:
        """Add a board to the open project.

        Args:
            name: what to call it. The editor picks a default when
                empty.
        """
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        return _call("proj.create_pcb", params, timeout=60.0)

    @mcp.tool()
    async def easyeda_set_title_block(
        fields: dict[str, Any] | None = None, show: bool = True,
    ) -> dict[str, Any]:
        """Fill in the current schematic page's title block.

        What a printed sheet is identified by: title, author, revision,
        date. A design that reaches a fabricator with an empty title
        block is a real problem and an easy one to leave until last.

        Args:
            fields: field name to ``{"value": ..., "showTitle": bool,
                "showValue": bool}``. The field names are the editor's
                own, so read an existing sheet before guessing them.
            show: whether the title block is displayed at all. Setting
                fields while this is false fills in a block nobody sees.
        """
        params: dict[str, Any] = {"show": show}
        if fields:
            params["fields"] = dict(fields)
        return _call("sch.set_title_block", params)

    @mcp.tool()
    async def easyeda_get_workspaces() -> dict[str, Any]:
        """Workspaces the editor knows about, and which is current.

        Worth checking before creating anything: a project made in the
        wrong workspace is found by nobody looking for it.
        """
        return _call("sys.workspaces")

    @mcp.tool()
    async def easyeda_audit_vias_near_board_edge(
        clearance: float = 20.0,
    ) -> dict[str, Any]:
        """Vias too close to the board edge to survive routing.

        The same measurement as the pad check and the same reason for
        it, applied to vias: distance to the outline SEGMENTS, never to
        a bounding box, because a box overstates a rounded or routed-out
        board and hides the violations nearest a real curved edge.

        A via close to the edge is worse than a pad there. Depanelling
        cuts through the barrel, and the resulting open circuit is
        intermittent rather than dead, so it survives a functional test.

        Distance is measured to the via's copper where its diameter can
        be read, and to its centre otherwise, and each result says
        which.

        Args:
            clearance: the minimum acceptable distance, in mils.
        """
        if clearance <= 0:
            return {"ok": False, "reason": "clearance must be positive"}

        outline = await easyeda_get_board_outline()
        if not outline.get("ok"):
            return outline
        edges = _edges_from(outline.get("segments") or [])
        if not edges:
            return {"ok": False, "reason": (
                "no board outline is drawn, so there is no edge to "
                "measure against")}

        vias = _call("pcb.vias", timeout=60.0)
        if not vias.get("ok"):
            return vias

        violations = []
        sized = 0
        checked = 0
        for via in vias.get("vias") or []:
            if not isinstance(via, dict):
                continue
            x, y = via.get("x"), via.get("y")
            if not isinstance(x, (int, float)) or \
                    not isinstance(y, (int, float)):
                continue
            checked += 1
            distance = min(_point_to_segment(float(x), float(y), *e)
                           for e in edges)
            diameter = via.get("diameter")
            measured = "centre"
            if isinstance(diameter, (int, float)) and diameter > 0:
                distance -= float(diameter) / 2.0
                measured = "copper"
                sized += 1
            if distance < clearance:
                violations.append({
                    "net": via.get("net", ""),
                    "x": float(x), "y": float(y),
                    "distance": round(distance, 3),
                    "measured_from": measured,
                })

        return {
            "ok": True,
            "verified_live": vias.get("verified_live"),
            "clearance": clearance,
            "vias_checked": checked,
            "vias_with_a_readable_size": sized,
            "violation_count": len(violations),
            "violations": sorted(violations, key=lambda v: v["distance"]),
        }

    @mcp.tool()
    async def easyeda_audit_off_grid_components(
        grid: float = 25.0,
    ) -> dict[str, Any]:
        """Components whose origin does not sit on the placement grid.

        Off-grid parts are not wrong, they are awkward: every later
        alignment, array and length-match works from the grid, so one
        part off it turns tidy work into hand-nudging, and the offset
        propagates into whatever gets aligned to that part.

        Reports the offset rather than judging it. A part deliberately
        placed to a mechanical dimension is off-grid on purpose, and
        nothing here can tell that from a slip.

        Args:
            grid: the placement grid pitch, in mils.
        """
        if grid <= 0:
            return {"ok": False, "reason": "grid must be positive"}

        components = _call("pcb.components", timeout=60.0)
        if not components.get("ok"):
            return components

        def _offset(value: float) -> float:
            """Distance to the NEAREST grid line, not past the last one.

            The remainder alone reports how far past the previous line a
            part sits, so one a hair short of the next line reads as
            nearly a full pitch off. That is a real number about the
            wrong thing, and it sorts the worst offenders backwards.

            Negative coordinates need nothing extra: Python's modulo
            returns a non-negative result for a positive divisor, so a
            part left of the origin measures the same as one right of
            it. An abs() here would be dead code defending against a
            language this is not written in.
            """
            remainder = value % grid
            return min(remainder, grid - remainder)

        off = []
        checked = 0
        for part in components.get("components") or []:
            if not isinstance(part, dict):
                continue
            x, y = part.get("x"), part.get("y")
            if not isinstance(x, (int, float)) or \
                    not isinstance(y, (int, float)):
                continue
            checked += 1
            dx, dy = _offset(float(x)), _offset(float(y))
            # A hair off the grid is floating-point noise from a
            # rotation or a unit conversion, not a placement mistake.
            if max(dx, dy) > 0.001:
                off.append({
                    "designator": part.get("designator", ""),
                    "x": float(x), "y": float(y),
                    "offset_x": round(dx, 4), "offset_y": round(dy, 4),
                })

        return {
            "ok": True,
            "verified_live": components.get("verified_live"),
            "grid": grid,
            "components_checked": checked,
            "violation_count": len(off),
            "off_grid_count": len(off),
            "off_grid": sorted(
                off, key=lambda p: -max(p["offset_x"], p["offset_y"])),
        }

    @mcp.tool()
    async def easyeda_get_device(
        uuid: str, library_uuid: str,
    ) -> dict[str, Any]:
        """One library device: what it is and what it is bound to.

        THE 3D MODEL IS NOT IN THE DEVICE RECORD. ``lib_Device.get``
        returns an association carrying only symbol, footprint and
        images, while ``lib_Device.search`` reports ``model3DUuid`` for
        the same uuid. Read straight, the record says every device is
        unmodelled. So the model is looked up separately and reported as
        ``model_3d``, with ``model_3d_source`` saying where it came
        from:

        - ``get``: the record carried it after all.
        - ``search``: recovered from the search row. ``model_3d`` of
          None here means the device genuinely has no model.
        - ``unresolved``: the search did not reach this device, because
          searches cap at ten results and a common name hides the row.
          ``model_3d`` of None here means UNKNOWN, not absent, and must
          not be reported as a missing model.

        Args:
            uuid: the device.
            library_uuid: the library it lives in.

        Returns:
            Dict with ``device``, ``model_3d`` and ``model_3d_source``.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        return _call("lib.get_device",
                     {"uuid": uuid, "library_uuid": library_uuid})

    @mcp.tool()
    async def easyeda_copy_device(
        uuid: str, library_uuid: str, target_library_uuid: str,
        new_name: str = "",
    ) -> dict[str, Any]:
        """Copy a device into another library.

        The usual way to adopt a vendor part: copy it into your own
        library, then edit the copy, so the original stays as shipped
        and an update to the vendor library cannot silently change your
        board.

        The device is what gets placed, so copying only its symbol and
        footprint leaves nothing placeable behind.

        Args:
            uuid: the device to copy.
            library_uuid: the library it currently lives in.
            target_library_uuid: where the copy goes.
            new_name: name for the copy. Keeps the original when empty.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not target_library_uuid.strip():
            return {"ok": False, "reason": (
                "target_library_uuid is required")}
        return _call("lib.copy_device", {
            "uuid": uuid, "library_uuid": library_uuid,
            "target_library_uuid": target_library_uuid,
            "new_name": new_name,
        }, timeout=60.0)

    @mcp.tool()
    async def easyeda_delete_device(
        uuid: str, library_uuid: str, confirm: bool = False,
    ) -> dict[str, Any]:
        """Remove a device from a library.

        DESTRUCTIVE. Refused unless ``confirm`` is true, and the
        extension refuses independently.

        It does not touch boards already using the part: those keep the
        placed copy, so a design can keep working while the library
        entry it came from no longer exists.

        Args:
            uuid: the device.
            library_uuid: the library it lives in.
            confirm: must be true for anything to happen.
        """
        if not uuid.strip() or not library_uuid.strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not confirm:
            return {"ok": False, "reason": (
                "delete_device removes the part from the library. Pass "
                "confirm=True if that is intended.")}
        return _call("lib.delete_device", {
            "uuid": uuid, "library_uuid": library_uuid, "confirm": True,
        })

    @mcp.tool()
    async def easyeda_add_pad(
        pad_number: str, x: float, y: float,
        width: float, height: float | None = None,
        shape: str = "ELLIPSE", layer: str = "TOP", net: str = "",
        rotation: float = 0.0, corner_radius: float = 0.0,
        sides: int = 0,
        hole_diameter: float = 0.0, hole_length: float = 0.0,
    ) -> dict[str, Any]:
        """Place a pad on the current PCB or footprint.

        The piece that makes footprint authoring real: open a footprint
        with ``easyeda_open_footprint`` and the drawing tools give it an
        outline and silkscreen, but only pads give it something to
        solder.

        SMD or through-hole is decided by ``hole_diameter``. Leave it at
        zero for surface mount. A through-hole pad usually wants
        ``layer="MULTI"`` so its copper reaches both sides; putting a
        drilled pad on TOP alone leaves the bottom unconnected, which
        looks right on screen and fails on the board.

        Args:
            pad_number: what ties this pad to a symbol pin. Required and
                a string, because "0" and "" are different pads and an
                unnumbered pad is copper the netlist cannot reach.
            x: centre x, in mils.
            y: centre y, in mils.
            width: pad width. For REGULAR_POLYGON this is the diameter.
            height: pad height. Square with the width when omitted.
            shape: ELLIPSE, RECTANGLE, OBLONG or REGULAR_POLYGON.
            layer: TOP, BOTTOM, or MULTI for a through-hole pad.
            net: net the pad belongs to.
            rotation: degrees.
            corner_radius: rounded corners, RECTANGLE only.
            sides: number of sides, REGULAR_POLYGON only. Must exceed 2.
            hole_diameter: drill diameter. Zero means surface mount.
            hole_length: for a slotted hole, the slot length. Ignored
                when it does not exceed the diameter, which is how a
                round hole is asked for.
        """
        if not pad_number:
            return {"ok": False, "reason": "pad_number is required"}
        if width <= 0:
            return {"ok": False, "reason": "width must be positive"}
        if shape.upper() == "REGULAR_POLYGON" and sides <= 2:
            return {"ok": False, "reason": (
                "a regular polygon needs sides greater than 2")}
        if hole_diameter and hole_diameter >= width:
            # The drill would consume the pad, leaving a hole with no
            # annular ring: unmanufacturable, and it renders as a pad.
            return {"ok": False, "reason": (
                f"hole_diameter ({hole_diameter}) must be smaller than "
                f"the pad ({width}), or there is no annular ring")}

        params: dict[str, Any] = {
            "pad_number": pad_number, "x": x, "y": y,
            "width": width, "shape": shape, "layer": layer, "net": net,
            "rotation": rotation, "corner_radius": corner_radius,
        }
        if height is not None:
            params["height"] = height
        if sides:
            params["sides"] = sides
        if hole_diameter:
            params["hole_diameter"] = hole_diameter
        if hole_length:
            params["hole_length"] = hole_length
        return _call("pcb.add_pad", params)

    @mcp.tool()
    async def easyeda_add_pin(
        pin_number: str, x: float, y: float,
        name: str = "", pin_type: str = "UNDEFINED",
        rotation: float = 0.0, length: float | None = None,
    ) -> dict[str, Any]:
        """Place a pin on the current symbol.

        The symbol counterpart of ``easyeda_add_pad``, and the piece
        that makes a symbol connectable rather than merely drawn. Open a
        symbol with ``easyeda_open_symbol`` first; on an ordinary sheet
        this places a loose pin.

        Args:
            pin_number: what ties this pin to a footprint pad. Required
                and a string, since "0" and "" are different pins and a
                symbol whose pins carry no numbers cannot be matched to
                a package.
            x: the ELECTRICAL end, in mils, which is where a wire
                connects. Not the end at the symbol body: getting them
                the wrong way round leaves wires attaching to thin air
                at a point that looks like a pin.
            y: electrical end y, in mils.
            name: the pin's name, e.g. VCC or RESET.
            pin_type: BI, GROUND, HIZ, IN, OPEN_COLLECTOR, OPEN_EMITTER,
                OUT, PASSIVE, POWER, TERMINATOR or UNDEFINED. This is
                what ERC checks, and it does not change how the pin
                draws: two outputs tied together look exactly like an
                output driving an input.
            rotation: degrees. Which way the pin points out of the body.
            length: pin length. The editor's default when omitted.
        """
        if not pin_number:
            return {"ok": False, "reason": "pin_number is required"}
        params: dict[str, Any] = {
            "pin_number": pin_number,
            "x": _sch(x), "y": _sch(y),
            "name": name, "pin_type": pin_type, "rotation": rotation,
        }
        if length is not None:
            params["length"] = _sch(length)
        return _call("sch.add_pin", params)

    @mcp.tool()
    async def easyeda_get_net_lengths() -> dict[str, Any]:
        """Routed copper length of every net, in one call.

        The per-net tool asked net by net would cost a round trip each,
        so the loop runs inside the editor.

        A net the editor cannot measure comes back as ``null`` rather
        than zero, because zero means "carries no copper" and that is a
        finding, not a missing measurement.

        An EMPTY list is called out rather than returned as a count of
        zero. Net data is reachable from either document, so asking
        from a schematic tab answers with no entries instead of
        refusing, and "0 nets measured" reads as "no net carries any
        copper" when it means the board was never consulted.
        """
        reply = _call("pcb.net_lengths", timeout=180.0)
        if not reply.get("ok"):
            return reply
        entries = reply.get("lengths")
        if not entries:
            reply["measured"] = 0
            reply["note"] = (
                "no nets were measured, which is not the same as no net "
                "carrying copper. Net reads answer from either document, "
                "so a schematic tab returns an empty list rather than "
                "refusing. Open the PCB to measure it")
        else:
            reply["measured"] = len(entries)
        return reply

    @mcp.tool()
    async def easyeda_get_unrouted_nets() -> dict[str, Any]:
        """Nets with no routed copper at all.

        WHAT THIS CATCHES, precisely: a net that carries zero routed
        length. That is the common case worth finding before ordering,
        a connection nobody drew.

        WHAT IT DOES NOT CATCH: a net that is PARTLY routed. EasyEDA
        reports a length per net and no ratline list, so a net with one
        segment drawn and one pin still floating measures non-zero and
        reads as routed here. Run the editor's own DRC for that; this is
        a cheap first pass, not a replacement for it.

        A net whose length could not be measured is listed separately
        rather than counted as unrouted, since "no answer" and "no
        copper" are different and only one of them is a defect.
        """
        lengths = _call("pcb.net_lengths", timeout=180.0)
        if not lengths.get("ok"):
            return lengths

        unrouted, unmeasured, routed = [], [], 0
        for entry in lengths.get("lengths") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("net") or "")
            if not name:
                continue
            value = entry.get("length")
            if not isinstance(value, (int, float)):
                unmeasured.append(name)
            elif value <= 0:
                unrouted.append(name)
            else:
                routed += 1

        examined = len(unrouted) + len(unmeasured) + routed
        if examined == 0:
            # Zero nets measured is not zero nets unrouted, and the two
            # read identically in a summary: "unrouted_count: 0" on an
            # empty read says the board is fully routed. Measured on a
            # schematic tab, where pcb.net_lengths answers with an empty
            # list rather than refusing, because net data is reachable
            # from either document and there is simply no board.
            return {
                "ok": False,
                "reason": (
                    "no nets were measured, so nothing was checked. That "
                    "is not the same as a board with nothing unrouted. "
                    "pcb.net_lengths returned no entries, which on a "
                    "schematic tab means there is no board to measure"),
                "examined": 0,
            }

        return {
            "ok": True,
            "verified_live": lengths.get("verified_live"),
            "unrouted_count": len(unrouted),
            "unrouted": sorted(unrouted),
            "unmeasured": sorted(unmeasured),
            "routed_count": routed,
            "examined": examined,
            "note": ("finds nets with NO copper; a partly routed net "
                     "measures non-zero and is not listed"),
        }

    @mcp.tool()
    async def easyeda_audit_components_outside_outline() -> dict[str, Any]:
        """Components whose origin falls outside the board outline.

        A part off the board is not a drawing mistake to fix later: it
        is not manufactured. The panel is cut to the outline, so the
        part exists in the design, appears in the BOM, is bought and
        placed by nobody.

        Tested by counting how many outline edges a ray from the part
        crosses, which works on an unordered set of segments. That
        matters here because the outline is drawn as loose lines and
        arcs with no guaranteed order, so anything that assumed a walked
        polygon would silently mis-handle a board drawn out of sequence.

        Reports the component ORIGIN, not its body. A part whose origin
        is just inside while half its footprint hangs over the edge is
        not reported, so a clean result is not a promise that everything
        fits.
        """
        outline = await easyeda_get_board_outline()
        if not outline.get("ok"):
            return outline
        edges = _edges_from(outline.get("segments") or [])
        if not edges:
            return {"ok": False, "reason": (
                "no board outline is drawn, so there is nothing for a "
                "component to be outside of")}

        components = _call("pcb.components", timeout=60.0)
        if not components.get("ok"):
            return components

        def _inside(px: float, py: float) -> bool:
            # Ray cast along +x. Odd crossings means inside. Edges are
            # taken as a set, so segment order and direction do not
            # matter.
            crossings = 0
            for x1, y1, x2, y2 in edges:
                if (y1 > py) == (y2 > py):
                    continue          # the edge does not span this ray
                t = (py - y1) / (y2 - y1)
                if x1 + t * (x2 - x1) > px:
                    crossings += 1
            return crossings % 2 == 1

        outside = []
        checked = 0
        for part in components.get("components") or []:
            if not isinstance(part, dict):
                continue
            x, y = part.get("x"), part.get("y")
            if not isinstance(x, (int, float)) or \
                    not isinstance(y, (int, float)):
                continue
            checked += 1
            if not _inside(float(x), float(y)):
                outside.append({
                    "designator": part.get("designator", ""),
                    "x": float(x), "y": float(y),
                })

        return {
            "ok": True,
            "verified_live": components.get("verified_live"),
            "components_checked": checked,
            "violation_count": len(outside),
            "outside_count": len(outside),
            "outside": outside,
            "note": ("tests the component ORIGIN; a part whose origin is "
                     "inside while its body overhangs is not reported"),
        }

    #: Layers whose text is read from the BOTTOM of the board, so it has
    #: to be mirrored to come out the right way round.
    _BOTTOM_TEXT_LAYERS = frozenset({
        "BOTTOM", "BOTTOM_SILKSCREEN", "BOTTOM_ASSEMBLY",
        "BOTTOM_SOLDER_MASK", "BOTTOM_PASTE_MASK",
    })

    #: Layer names that put text on the TOP of the board.
    #:
    #: Needed as its own set rather than "everything that is not
    #: bottom", and that distinction is the whole bug this pair exists
    #: to fix.
    #:
    #: PCB reads come back with a NUMERIC layer. A live component
    #: reported layer 2, and two board-outline checks in this file
    #: already accept "11" beside "BOARD_OUTLINE" because somebody hit
    #: this once and fixed it in one place. Everywhere else,
    #: str(2).upper() is "2", which matches no name.
    #:
    #: For the mirrored-text audit that was not a missed check, it was
    #: an INVERTED one. Anything unrecognised fell through to "this is a
    #: top layer", so unmirrored bottom silkscreen (the defect the audit
    #: exists for, a silkscreen respin) read as clean, and correctly
    #: mirrored bottom text was reported as wrong.
    #:
    #: The numeric codes are deliberately NOT listed. Which integer
    #: means which layer is unmeasured, and a guess here decides whether
    #: a board ships with reversed silkscreen. An unrecognised layer is
    #: reported UNCHECKED instead, which is true, visible, and cannot
    #: mislead in either direction.
    _TOP_TEXT_LAYERS = frozenset({
        "TOP", "TOP_SILKSCREEN", "TOP_ASSEMBLY",
        "TOP_SOLDER_MASK", "TOP_PASTE_MASK",
    })

    def _layer_table() -> dict[str, dict]:
        """id -> {name, type}, straight from the editor.

        Every row carries a type, and that is the field to read: exact
        where a name is a heuristic, and stable where a name is
        whatever an inner layer was renamed to.

        The measured vocabulary, which the classifiers below are built
        from:

          TOP, BOTTOM              outer copper
          SIGNAL                   inner copper. THIRTY-TWO of them on
                                   this board, named things like
                                   "Int1 (GND)" and "Inner7"
          MULTI                    reaches every layer
          TOP_SILK, BOT_SILK       note BOT_, not BOTTOM_
          TOP_SOLDER_MASK, BOT_SOLDER_MASK
          TOP_PASTE_MASK, BOT_PASTE_MASK
          TOP_ASSEMBLY, BOT_ASSEMBLY
          TOP_STIFFENER, BOTTOM_STIFFENER
          OUTLINE, DOCUMENT, MECHANICAL, HOLE, DRILL_DRAWING,
          COMPONENT_SHAPE, COMPONENT_MARKING, COMPONENT_MODEL,
          PIN_SOLDERING, PIN_FLOATING, 3D_SHELL_*, SUBSTRATE,
          CUSTOM, OTHER

        Names cannot substitute. Inner copper is named freely, so a
        layer called "Int1 (GND)" starts with neither TOP nor BOTTOM and
        no name rule will place it. A router that cannot see inner
        copper routes straight through it.
        """
        reply = _call("pcb.layers", timeout=30.0)
        if not reply.get("ok"):
            return {}
        out: dict[str, dict] = {}
        rows = reply.get("layers")
        if isinstance(rows, dict):
            rows = [dict(v, id=k) if isinstance(v, dict) else
                    {"id": k, "name": v} for k, v in rows.items()]
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = None
            for field in ("id", "layerId", "number", "index"):
                value = row.get(field)
                if isinstance(value, (int, str)) and str(value).strip():
                    key = str(value).strip()
                    break
            if not key:
                continue
            name = ""
            for field in ("name", "layerName", "title"):
                value = row.get(field)
                if isinstance(value, str) and value.strip():
                    name = value.strip().upper().replace(" ", "_")
                    break
            out[key] = {"name": name,
                        "type": str(row.get("type") or "").upper()}
        return out

    #: Layer types that carry copper. Everything else is artwork.
    #:
    #: SIGNAL is the one that matters and the one a name-based rule
    #: misses: inner layers are named freely, so only the type says
    #: they are copper. MULTI spans every layer, which is what a
    #: through-hole pad sits on.
    _COPPER_TYPES = frozenset({"TOP", "BOTTOM", "SIGNAL", "MULTI"})

    def _side_of_layer(raw: Any, table: dict) -> Optional[str]:
        """"top", "bottom", or None when it is not established.

        None is a real answer and the important one. Anything that
        cannot be placed on a side must not fall through to a default,
        because the default silently becomes the verdict.

        Note BOT_SILK: the measured types abbreviate bottom to BOT on
        the artwork layers and spell it out on BOTTOM and
        BOTTOM_STIFFENER. Both spellings are handled, which a single
        startswith("BOTTOM") did not.
        """
        entry = table.get(str(raw or "").strip()) or {}
        kind = entry.get("type") or ""

        # The TYPE is authoritative when the editor gave one. Falling
        # through to the name after a type has already answered is how
        # a layer whose type says CUSTOM but whose name happens to
        # begin "Bottom" gets filed as bottom copper artwork. The
        # editor knows what its layers are; the name is whatever
        # somebody typed.
        if kind:
            if kind.startswith("BOT"):
                return "bottom"
            if kind.startswith("TOP"):
                return "top"
            return None

        # No type: either an older build or a raw layer value with no
        # entry in the table at all. Now the name is the only evidence
        # there is.
        name = entry.get("name") or str(raw or "").upper().replace(" ", "_")
        if name in _BOTTOM_TEXT_LAYERS:
            return "bottom"
        if name in _TOP_TEXT_LAYERS:
            return "top"
        if name.startswith("BOT"):
            return "bottom"
        if name.startswith("TOP"):
            return "top"
        return None

    def _is_copper(raw: Any, table: dict) -> Optional[bool]:
        """Whether a layer carries copper. None when unknown.

        Kept apart from the side on purpose. BOT_SILK is a bottom layer
        and is not copper, and one function answering both questions
        fills a router's obstacle map with silkscreen.
        """
        entry = table.get(str(raw or "").strip())
        if not entry:
            return None
        kind = entry.get("type") or ""
        if kind in _COPPER_TYPES:
            return True
        if kind:
            return False
        return None

    @mcp.tool()
    async def easyeda_audit_mirrored_text() -> dict[str, Any]:
        """Bottom-side text that will read backwards on the real board.

        Text on a bottom layer is viewed through the board, so it has to
        be mirrored to come out readable. Unmirrored bottom silkscreen
        looks perfectly correct in the editor, where you are looking at
        it from the top, and comes back from the fab reversed. That is a
        respin of the silkscreen for a defect nothing on screen shows.

        The reverse is reported too: mirrored text on a TOP layer reads
        backwards for the same reason in the other direction.

        Text whose layer or mirror flag cannot be read is listed as
        unchecked rather than assumed correct, since an assumption in
        either direction here is one that gets manufactured.
        """
        strings = _call("pcb.strings", timeout=60.0)
        if not strings.get("ok"):
            return strings

        # The editor is asked which layer each number is, rather than a
        # table here deciding it. PCB reads carry a NUMERIC layer, and
        # guessing that 2 means bottom would settle, wrongly, whether a
        # board ships with reversed silkscreen.
        #
        # Fetched only when a layer does not resolve by name, and once
        # per call. A board whose text already carries names costs no
        # extra read at all, which is most of them.
        lookup: Optional[dict[str, str]] = None

        wrong, unchecked, checked = [], [], 0
        for item in strings.get("strings") or []:
            if not isinstance(item, dict):
                continue
            raw_layer = item.get("layer", "")
            mirror = item.get("mirror")
            if raw_layer in ("", None) or not isinstance(mirror, bool):
                unchecked.append({
                    "text": item.get("text", ""),
                    "layer": raw_layer,
                    "reason": "no layer or no mirror flag to read",
                })
                continue
            side = _side_of_layer(raw_layer, {})
            if side is None:
                if lookup is None:
                    lookup = _layer_table()
                side = _side_of_layer(raw_layer, lookup)
            if side is None:
                # This used to fall through to "top", which inverted the
                # audit: bottom text read as top, so the unmirrored
                # defect reported clean and correctly mirrored text was
                # flagged as wrong.
                unchecked.append({
                    "text": item.get("text", ""),
                    "layer": raw_layer,
                    "reason": ("the layer is neither a known top nor a "
                               "known bottom layer, and the editor's "
                               "layer list did not resolve it, so which "
                               "side this text is on is not established"),
                })
                continue
            layer = str(raw_layer).upper().replace(" ", "_")
            on_bottom = side == "bottom"
            checked += 1
            if on_bottom and not mirror:
                wrong.append({"text": item.get("text", ""), "layer": layer,
                              "problem": "on a bottom layer and not mirrored"})
            elif not on_bottom and mirror:
                wrong.append({"text": item.get("text", ""), "layer": layer,
                              "problem": "on a top layer and mirrored"})

        return {
            "ok": True,
            "verified_live": strings.get("verified_live"),
            "text_checked": checked,
            "violation_count": len(wrong),
            "wrong_count": len(wrong),
            "wrong": wrong,
            "unchecked": unchecked,
        }

    @mcp.tool()
    async def easyeda_audit_track_widths(
        ignore_below: float = 0.0,
        ratio: float = 0.0,
    ) -> dict[str, Any]:
        """Nets routed at more than one width.

        REPORTS, does not judge. A net at several widths is often a
        mistake, a segment drawn before a rule was set or a rework that
        picked up the default. It is also how a deliberate taper into a
        fine-pitch pad looks, and nothing here can tell those apart, so
        the widths are listed and the call is left to a human.

        Counted per net per LAYER. The same net legitimately changes
        width when it moves between an inner layer and an outer one, so
        folding the layers together would report that as a defect on
        every multi-layer board. (The Altium audit folds them; this one
        deliberately does not.)

        This is also the ONLY width check that works here. The per-net
        rules measured on a live board carry the string "default"
        rather than a width, so nothing can be compared against a rule
        without first resolving a fab capability matrix; internal
        consistency needs no rule at all.

        Args:
            ignore_below: treat widths differing by less than this as
                the same, in mils. Rounding in a unit conversion can
                leave two nominally equal segments differing in the last
                decimal, which is noise rather than a finding.
            ratio: when above 1, report only nets whose widest and
                narrowest differ by more than this factor. 2.0 is the
                Altium audit's threshold and isolates the bug worth
                acting on: a rail routed wide for its current, then
                extended at the editor's default, leaving a thin stub
                welded to a wide bus. 0 (the default) keeps the
                report-everything behaviour.
        """
        lines = _call("pcb.lines", timeout=120.0)
        if not lines.get("ok"):
            return lines

        if ratio and ratio <= 1:
            return {"ok": False, "reason":
                    "ratio must be above 1, or 0 to report every net "
                    "with more than one width; at or below 1 every "
                    "variation qualifies and the filter means nothing"}

        # (net, layer) -> the widths seen, and the narrowest segment
        seen: dict[tuple[str, str], list[float]] = {}
        thinnest: dict[tuple[str, str], dict] = {}
        counted = 0
        for item in lines.get("lines") or []:
            if not isinstance(item, dict):
                continue
            net = str(item.get("net") or "").strip()
            width = item.get("lineWidth", item.get("width"))
            if not net or not isinstance(width, (int, float)):
                # Silkscreen and outline carry no net, and a segment
                # with no readable width says nothing either way.
                continue
            counted += 1
            key = (net, str(item.get("layer", "")))
            seen.setdefault(key, []).append(float(width))
            current = thinnest.get(key)
            if current is None or float(width) < current["width"]:
                thinnest[key] = {"width": float(width),
                                 "x": item.get("startX"),
                                 "y": item.get("startY")}

        findings = []
        for (net, layer), widths in sorted(seen.items()):
            distinct = sorted(set(widths))
            if len(distinct) < 2:
                continue
            if ignore_below and (distinct[-1] - distinct[0]) < ignore_below:
                continue
            span = (distinct[-1] / distinct[0]) if distinct[0] > 0 else 0.0
            if ratio and span <= ratio:
                continue
            thin = thinnest.get((net, layer)) or {}
            findings.append({
                "net": net, "layer": layer,
                "widths": distinct,
                "ratio": round(span, 3),
                "segments": len(widths),
                # Where to look first: a reviewer jumps to the narrow
                # section, not to whichever segment happened to sort
                # first.
                "thin_x": thin.get("x"),
                "thin_y": thin.get("y"),
            })

        # Worst first when a ratio was asked for; a plain report keeps
        # the stable net order it always had.
        if ratio:
            findings.sort(key=lambda f: f["ratio"], reverse=True)

        return {
            "ok": True,
            "verified_live": lines.get("verified_live"),
            "segments_counted": counted,
            "ratio": ratio,
            "violation_count": len(findings),
            "mixed_count": len(findings),
            "mixed": findings,
            "note": ("a deliberate taper looks the same as a mistake here; "
                     "the widths are reported, not judged"),
        }

    @mcp.tool()
    async def easyeda_add_fill(
        points: list[list[float]], layer: str = "TOP", net: str = "",
        width: float | None = None, locked: bool = False,
    ) -> dict[str, Any]:
        """Fill a polygon with solid copper on the current PCB.

        Different from a poured zone: a fill is solid and does not flow
        around what it meets, so it will short anything inside it that
        is on another net. Use a zone (``easyeda_add_zone``) for a
        ground plane that has to keep clearance; use a fill for a
        deliberate slab, a thermal pad or a shield.

        Args:
            points: the outline, as at least three ``[x, y]`` pairs in
                mils.
            layer: layer name.
            net: net the copper belongs to.
            width: outline width. The editor's default when omitted.
            locked: lock against interactive edits.
        """
        if len(points) < 3:
            return {"ok": False, "reason": (
                "points must be at least 3 [x, y] pairs")}
        params: dict[str, Any] = {
            "points": [list(p) for p in points],
            "layer": layer, "net": net, "locked": locked,
        }
        if width is not None:
            params["width"] = width
        return _call("pcb.add_fill", params)

    @mcp.tool()
    async def easyeda_add_region(
        points: list[list[float]], rules: list[str],
        layer: str = "TOP", name: str = "",
        width: float | None = None, locked: bool = False,
    ) -> dict[str, Any]:
        """Mark a keepout or constraint area on the current PCB.

        The area under a connector, beneath a mounting screw, or around
        an antenna, where something must NOT go.

        ``rules`` is required and has no default. A region with no rule
        is just an outline: it draws, it constrains nothing, and the
        board routes straight through the area somebody meant to
        protect, which looks exactly like a region that is working.

        Args:
            points: the outline, as at least three ``[x, y]`` pairs in
                mils.
            rules: one or more of NO_COMPONENTS, NO_WIRES, NO_FILLS,
                NO_POURS, NO_INNER_ELECTRICAL_LAYERS,
                FOLLOW_REGION_RULE.
            layer: layer name.
            name: a name for the region.
            width: outline width. The editor's default when omitted.
            locked: lock against interactive edits.
        """
        if len(points) < 3:
            return {"ok": False, "reason": (
                "points must be at least 3 [x, y] pairs")}
        if not rules:
            return {"ok": False, "reason": (
                "rules is required; a region with no rule constrains "
                "nothing and looks exactly like one that works")}
        params: dict[str, Any] = {
            "points": [list(p) for p in points], "rules": list(rules),
            "layer": layer, "locked": locked,
        }
        if name:
            params["name"] = name
        if width is not None:
            params["width"] = width
        return _call("pcb.add_region", params)

    #: How many points each dimension type takes. They are not
    #: interchangeable and the meaning of each point differs per type.
    _DIMENSION_POINTS = {"LENGTH": 4, "RADIUS": 3, "ANGLE": 3}

    @mcp.tool()
    async def easyeda_add_dimension(
        dimension_type: str, points: list[list[float]],
        layer: str = "DOCUMENT", width: float | None = None,
        precision: int | None = None,
    ) -> dict[str, Any]:
        """Draw a measured dimension on the current PCB.

        What a fabrication drawing is read from: board size, cutout
        positions, hole spacing. A board sent without them is one where
        the fab measures from the Gerbers and guesses the tolerance.

        Args:
            dimension_type: LENGTH, RADIUS or ANGLE. Each takes a
                different number of points and reads them differently,
                so they are not interchangeable.
            points: ``[x, y]`` pairs in mils. LENGTH takes four: first
                measurement end, first arrow end, second arrow end,
                second measurement end. RADIUS takes three: a point on
                the arc, the dimension line tail, the text corner.
                ANGLE takes three: one edge end, the vertex, the other
                edge end. A wrong count is refused rather than sent,
                since a dimension drawn from the wrong points still
                draws and reads as a measurement.
            layer: which layer to draw on. DOCUMENT by default, since a
                dimension is drawing rather than copper.
            width: line width. The editor's default when omitted.
            precision: decimal places on the measurement.
        """
        wanted = _DIMENSION_POINTS.get(dimension_type.upper())
        if not wanted:
            return {"ok": False, "reason": (
                f"dimension_type must be one of: "
                f"{', '.join(sorted(_DIMENSION_POINTS))}")}
        if len(points) != wanted:
            return {"ok": False, "reason": (
                f"a {dimension_type.upper()} dimension takes exactly "
                f"{wanted} points, and {len(points)} were given")}
        params: dict[str, Any] = {
            "dimension_type": dimension_type.upper(),
            "points": [list(p) for p in points],
            "layer": layer,
        }
        if width is not None:
            params["width"] = width
        if precision is not None:
            params["precision"] = precision
        return _call("pcb.add_dimension", params)

    @mcp.tool()
    async def easyeda_add_bus(
        name: str, points: list[list[float]],
    ) -> dict[str, Any]:
        """Draw a bus on the current schematic.

        Args:
            name: the bus name, and what carries its members, e.g.
                ``D[0..7]``. Required: a bus drawn without one is a
                thick line. It looks like a bus, groups nothing, and the
                signals a reader assumes are in it are not.
            points: the path, as at least two ``[x, y]`` pairs in mils.
        """
        if not name.strip():
            return {"ok": False, "reason": (
                'name is required, e.g. "D[0..7]"; a bus without one '
                "groups nothing while looking like a bus")}
        if len(points) < 2:
            return {"ok": False, "reason": (
                "points must be at least 2 [x, y] pairs")}
        return _call("sch.add_bus", {
            "name": name,
            "points": [[_sch(p[0]), _sch(p[1])] for p in points],
        })

    # ---- bulk edits --------------------------------------------------
    #
    # One round trip instead of one per component. Renumbering forty
    # parts through the single-component tool is forty requests over a
    # socket; the loop runs inside the editor instead.

    def _bulk_changes(changes: list[dict[str, Any]]) -> str:
        """Reject a malformed batch before any of it is applied.

        Checked up front on purpose: finding the bad entry halfway
        leaves the design part-edited, which is the state hardest to
        reason about afterwards.
        """
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                return f"entry {index} is not an object"
            if not str(change.get("primitive_id") or "").strip():
                return f"entry {index} has no primitive_id"
            properties = change.get("changes")
            if not isinstance(properties, dict) or not properties:
                return (f"entry {index} names no properties to change; an "
                        f"empty change reports success while doing nothing")
        return ""

    @mcp.tool()
    async def easyeda_modify_schematic_components(
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Change properties on many schematic components at once.

        Each result is reported individually rather than as one verdict.
        A partial failure is the case that matters: knowing THAT
        something failed is no use without knowing which, because the
        rest did apply and the design is now half-edited.

        Args:
            changes: a list of ``{"primitive_id": ..., "changes": {...}}``.
                The inner keys are the same ones
                ``easyeda_set_schematic_component_properties`` accepts:
                x, y, rotation, mirror, designator, name, manufacturer
                and the rest.
        """
        if not changes:
            return {"ok": False, "reason": "changes must not be empty"}
        problem = _bulk_changes(changes)
        if problem:
            return {"ok": False, "reason": problem}
        return _call("sch.modify_components",
                     {"changes": [dict(c) for c in changes]}, timeout=180.0)

    @mcp.tool()
    async def easyeda_increment_designators(
        delta: int,
        prefix: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Offset the trailing number of schematic designators.

        delta=100 turns R5 into R105. What it is for is copying a block:
        the copy arrives with the same designators as the original, and
        moving them out of the way is what makes re-annotation possible
        without every part colliding.

        COLLISIONS ARE CHECKED BEFORE ANYTHING IS SENT. A delta that
        lands one part on another leaves two components claiming one
        designator, which the netlist then reads as one part, and it is
        the kind of error that survives to fabrication. If any target
        name is already taken by a part that is not itself moving, the
        whole operation is refused and the clashes are named.

        THE ORDER OF APPLICATION MATTERS and is handled here. Shifting
        R1 to R5 up by one has to start at R5, or R1 becomes R2 while
        the real R2 still exists. Renames go descending for a positive
        delta and ascending for a negative one, so no intermediate state
        has two parts sharing a name.

        Parts whose designator has no trailing number are left alone and
        counted, not silently skipped: a sheet of them means the delta
        did nothing and the caller should know that rather than read a
        cheerful zero.

        Args:
            delta: non-zero integer added to each designator's number.
            prefix: restrict to one letter prefix, e.g. "R". Matched
                exactly and case-sensitively, so "R" does not touch
                "RN".
            dry_run: work out the renames and report them without
                sending anything.
        """
        import re as _re

        if delta == 0:
            return {"ok": False,
                    "reason": "delta must not be zero; nothing would change"}

        reply = _call("sch.components", timeout=60.0)
        if not reply.get("ok"):
            return reply

        pattern = _re.compile(r"^([A-Za-z_]+)(\d+)$")
        moving: list[tuple[str, str, str, int]] = []
        unnumbered = 0
        occupied: set[str] = set()

        for item in reply.get("components") or []:
            if not isinstance(item, dict):
                continue
            designator = str(item.get("designator")
                             or item.get("name") or "").strip()
            if not designator:
                continue
            occupied.add(designator)
            match = pattern.match(designator)
            if not match:
                unnumbered += 1
                continue
            letters, digits = match.group(1), match.group(2)
            if prefix and letters != prefix:
                continue
            primitive_id = str(item.get("primitiveId") or "")
            if not primitive_id:
                # Without an id there is nothing to address the change
                # to, so this part cannot be renamed and must not be
                # counted among those that will be.
                unnumbered += 1
                continue
            moving.append((primitive_id, designator,
                           f"{letters}{int(digits) + delta}",
                           int(digits)))

        if not moving:
            return {
                "ok": True, "modified": 0, "delta": delta,
                "components_without_a_number": unnumbered,
                "reason": ("no designator matched"
                           + (f" the prefix {prefix!r}" if prefix else "")
                           + "; nothing to do"),
            }

        # A target is a clash only if the name is held by a part that is
        # NOT itself moving out of it. R1 and R2 both shifting by one is
        # fine; R1 shifting onto a stationary R2 is not.
        vacating = {old for _, old, _, _ in moving}
        clashes = [{"from": old, "to": new} for _, old, new, _ in moving
                   if new in occupied and new not in vacating]
        # Two movers landing on the same name is the other way to
        # collide, and it cannot happen with a uniform delta unless the
        # sheet already had duplicates. Checked anyway, because "cannot
        # happen" is how duplicates reach a netlist.
        targets: dict[str, int] = {}
        for _, _, new, _ in moving:
            targets[new] = targets.get(new, 0) + 1
        doubled = sorted(n for n, count in targets.items() if count > 1)

        if clashes or doubled:
            return {
                "ok": False,
                "reason": ("this delta would put two components on one "
                           "designator, which a netlist reads as one "
                           "part; nothing was changed"),
                "collisions": clashes,
                "duplicate_targets": doubled,
                "delta": delta,
            }

        # Descending for a positive delta, ascending for a negative one,
        # so no intermediate state has two parts sharing a name.
        moving.sort(key=lambda row: row[3], reverse=delta > 0)
        renames = [{"from": old, "to": new} for _, old, new, _ in moving]

        if dry_run:
            return {
                "ok": True, "dry_run": True, "delta": delta,
                "would_modify": len(moving), "renames": renames,
                "components_without_a_number": unnumbered,
            }

        result = _call("sch.modify_components", {
            "changes": [{"primitive_id": pid, "changes": {"designator": new}}
                        for pid, _, new, _ in moving],
        }, timeout=180.0)
        if not result.get("ok"):
            return result

        result["delta"] = delta
        result["renames"] = renames
        result["components_without_a_number"] = unnumbered
        return result

    @mcp.tool()
    async def easyeda_modify_pcb_components(
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Change properties on many PCB components at once.

        The board counterpart of the schematic bulk edit, and how a
        placement pass applies: move, rotate and flip in one call
        instead of one per part.

        Args:
            changes: a list of ``{"primitive_id": ..., "changes": {...}}``.
                The inner keys are the ones ``easyeda_modify_component``
                accepts: x, y, rotation, layer, primitiveLock and the
                rest.
        """
        if not changes:
            return {"ok": False, "reason": "changes must not be empty"}
        problem = _bulk_changes(changes)
        if problem:
            return {"ok": False, "reason": problem}
        return _call("pcb.modify_components",
                     {"changes": [dict(c) for c in changes]}, timeout=180.0)

    @mcp.tool()
    async def easyeda_place_schematic_components(
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place many library parts on the schematic in one call.

        The bulk form of ``easyeda_place_schematic_component``, and what
        a plan of any size should use: placing forty parts one at a time
        is forty round trips over a socket.

        Each placement is reported individually. A batch that
        half-succeeds is the case worth designing for, because the parts
        that landed are on the sheet and a caller needs to know which so
        a retry does not place them twice.

        Args:
            components: a list of ``{"library_uuid": ..., "uuid": ...,
                "x": ..., "y": ..., "rotation": ...}``. Coordinates are
                in mils, as everywhere else here, and converted once on
                the way out.
        """
        if not components:
            return {"ok": False, "reason": "components must not be empty"}
        prepared = []
        for index, item in enumerate(components):
            if not isinstance(item, dict):
                return {"ok": False, "reason": f"entry {index} is not an object"}
            library_uuid = str(item.get("library_uuid") or "").strip()
            uuid = str(item.get("uuid") or "").strip()
            if not library_uuid or not uuid:
                return {"ok": False, "reason": (
                    f"entry {index} needs both library_uuid and uuid; there "
                    f"is no lookup by name, and picking a part would be "
                    f"invisible afterwards")}
            try:
                x = float(item["x"])
                y = float(item["y"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "reason": (
                    f"entry {index} has no usable x and y")}
            prepared.append({
                "library_uuid": library_uuid, "uuid": uuid,
                "x": _sch(x), "y": _sch(y),
                "rotation": float(item.get("rotation") or 0.0),
                "mirror": bool(item.get("mirror")),
                "add_to_bom": item.get("add_to_bom", True) is not False,
                "add_to_pcb": item.get("add_to_pcb", True) is not False,
            })
        return _call("sch.place_components", {"components": prepared},
                     timeout=300.0)

    @mcp.tool()
    async def easyeda_place_pcb_components(
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place many footprints on the board in one call.

        The board counterpart of the schematic bulk placement, and the
        same reason: placing forty footprints one at a time is forty
        round trips over a socket.

        Coordinates are in mils and take NO conversion here. The PCB
        canvas already counts in mils, unlike the schematic one, which
        is the difference this backend keeps having to be careful about.

        Stops at the first failure and reports the rest as skipped, so a
        batch leaves the board in the same knowable state a sequence of
        single calls would.

        Args:
            components: a list of ``{"library_uuid": ..., "uuid": ...,
                "x": ..., "y": ..., "layer": "TOP"|"BOTTOM",
                "rotation": ..., "locked": ...}``.
        """
        if not components:
            return {"ok": False, "reason": "components must not be empty"}
        prepared = []
        for index, item in enumerate(components):
            if not isinstance(item, dict):
                return {"ok": False, "reason": f"entry {index} is not an object"}
            library_uuid = str(item.get("library_uuid") or "").strip()
            uuid = str(item.get("uuid") or "").strip()
            if not library_uuid or not uuid:
                return {"ok": False, "reason": (
                    f"entry {index} needs both library_uuid and uuid; there "
                    f"is no lookup by name")}
            try:
                x = float(item["x"])
                y = float(item["y"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "reason": (
                    f"entry {index} has no usable x and y")}
            prepared.append({
                "library_uuid": library_uuid, "uuid": uuid,
                "x": x, "y": y,
                "layer": str(item.get("layer") or "TOP"),
                "rotation": float(item.get("rotation") or 0.0),
                "locked": bool(item.get("locked")),
            })
        return _call("pcb.place_components", {"components": prepared},
                     timeout=300.0)

    @mcp.tool()
    async def easyeda_add_wires(
        wires: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Draw many wires on the schematic in one call.

        The connections pass draws a wire per net segment, so a design
        of any size pays a round trip for each. This is that pass in one
        call.

        Coordinates are in mils and converted once here, the same as the
        single-wire tool.

        Stops at the first failure and reports the rest as skipped, so
        the sheet ends up in the state a sequence of single calls would
        leave it in.

        Args:
            wires: a list of ``{"points": [[x, y], ...], "net": ...}``.
                Each needs at least two points. The net may be empty,
                and then the editor infers one from what the wire
                touches.
        """
        if not wires:
            return {"ok": False, "reason": "wires must not be empty"}
        prepared = []
        for index, wire in enumerate(wires):
            if not isinstance(wire, dict):
                return {"ok": False, "reason": f"entry {index} is not an object"}
            points = wire.get("points")
            if not isinstance(points, list) or len(points) < 2:
                return {"ok": False, "reason": (
                    f"entry {index} needs at least 2 [x, y] points")}
            try:
                converted = [[_sch(float(p[0])), _sch(float(p[1]))]
                             for p in points]
            except (TypeError, ValueError, IndexError):
                return {"ok": False, "reason": (
                    f"entry {index} has a point that is not a pair of "
                    f"numbers")}
            prepared.append({"points": converted,
                             "net": str(wire.get("net") or "")})
        return _call("sch.add_wires", {"wires": prepared}, timeout=300.0)

    @mcp.tool()
    async def easyeda_audit_designator_collisions() -> dict[str, Any]:
        """Footprints on the board sharing one designator.

        Two footprints with the same designator are a real defect, not a
        matter of taste: the BOM counts one part, the netlist binds to
        one of them, and pick-and-place gets two positions for a line
        item that has one. It usually arrives from a copied placement or
        from an annotation that did not finish.

        BOARD ONLY, and that is deliberate. On a schematic a multi-part
        symbol puts the same designator on each of its parts by design,
        so the same check there would report every multi-part IC on the
        sheet. One footprint per designator is the rule that holds.

        Components with no readable designator are counted separately
        rather than grouped together, since an unannotated board would
        otherwise report as one enormous collision.
        """
        components = _call("pcb.components", timeout=60.0)
        if not components.get("ok"):
            return components

        seen: dict[str, list[Any]] = {}
        unnamed = 0
        counted = 0
        for item in components.get("components") or []:
            if not isinstance(item, dict):
                continue
            counted += 1
            designator = str(item.get("designator") or "").strip()
            if not designator:
                unnamed += 1
                continue
            seen.setdefault(designator, []).append(
                item.get("primitiveId") or item.get("uuid"))

        findings = [
            {"designator": designator, "count": len(ids),
             "primitive_ids": ids}
            for designator, ids in sorted(seen.items()) if len(ids) > 1
        ]
        return {
            "ok": True,
            "verified_live": components.get("verified_live"),
            "components_counted": counted,
            "unnamed_count": unnamed,
            "violation_count": len(findings),
            "collision_count": len(findings),
            "collisions": findings,
        }

    @mcp.tool()
    async def easyeda_audit_single_pad_nets() -> dict[str, Any]:
        """Nets that reach exactly one pad on the board.

        A net with one pad connects nothing. It is the shape a dropped
        wire leaves behind, and it survives layout because a one-pad net
        has nothing to route and so never appears as unrouted.

        REPORTS, does not judge. A test point, a mounting hole and a
        shield tab are all legitimately one pad on their own net, and
        nothing readable here separates those from a connection that was
        meant to exist.

        Counted over PADS, not the netlist. A net can carry tracks and
        vias and still reach one pad, so counting copper would hide
        exactly the case this looks for.
        """
        pads = _call("pcb.pads", timeout=120.0)
        if not pads.get("ok"):
            return pads

        per_net: dict[str, list[Any]] = {}
        unnetted = 0
        counted = 0
        for item in pads.get("pads") or []:
            if not isinstance(item, dict):
                continue
            counted += 1
            net = str(item.get("net") or "").strip()
            if not net:
                # An unconnected pad carries no net name at all, which
                # is a different finding from a net with one pad on it.
                unnetted += 1
                continue
            per_net.setdefault(net, []).append(
                item.get("primitiveId") or item.get("uuid"))

        findings = [
            {"net": net, "primitive_ids": ids}
            for net, ids in sorted(per_net.items()) if len(ids) == 1
        ]
        return {
            "ok": True,
            "verified_live": pads.get("verified_live"),
            "pads_counted": counted,
            "unnetted_pad_count": unnetted,
            "nets_counted": len(per_net),
            "violation_count": len(findings),
            "single_pad_count": len(findings),
            "single_pad_nets": findings,
            "note": ("a test point or mounting hole is legitimately a "
                     "one-pad net; these are reported, not judged"),
        }

    @mcp.tool()
    async def easyeda_find_component(
        query: str = "", scope: str = "both",
    ) -> dict[str, Any]:
        """Find components whose designator or value contains the query.

        Answers "where is R47", which otherwise means reading every
        component and filtering by hand.

        Searches the BOARD and the SCHEMATIC and says which side each
        hit came from. A part present on one and not the other is
        usually the reason someone is looking, so reporting only the
        board would answer half the question.

        Case-insensitive substring, not a pattern. A designator search
        for "R4" finds R4, R40 and R47, which is the useful behaviour
        when the exact name is what is being looked for.

        Field names are read defensively. EasyEDA's published reference
        lists methods rather than the shape of what they return, so
        several spellings are tried and a component that carries none of
        them is counted as unreadable rather than silently skipped.

        Args:
            query: substring to look for, matched against the
                designator and the value or name.
            scope: "both" (default), "pcb" or "schematic".
        """
        needle = str(query or "").strip().lower()
        if not needle:
            return {"ok": False, "reason": (
                "query is required; pass a designator or value substring "
                "such as R47 or 10k")}

        wanted = str(scope or "both").strip().lower()
        if wanted not in ("both", "pcb", "schematic"):
            return {"ok": False, "reason": (
                f"scope must be both, pcb or schematic, not {scope!r}")}

        #: The spellings a designator and a value have been seen under.
        #: Tried in order; the first present one wins.
        designator_keys = ("designator", "name", "reference", "refDes")
        value_keys = ("value", "comment", "displayValue", "text")

        def _first(item: dict, keys: tuple) -> str:
            for key in keys:
                found = item.get(key)
                if isinstance(found, str) and found.strip():
                    return found.strip()
            return ""

        matches = []
        unreadable = 0
        searched = 0
        sides = []
        if wanted in ("both", "pcb"):
            sides.append(("pcb", "pcb.components", "components"))
        if wanted in ("both", "schematic"):
            sides.append(("schematic", "sch.components", "components"))

        not_searched = []
        for side, command, key in sides:
            # THE SCHEMATIC SIDE READS THE NETLIST, not sch.components.
            #
            # sch.components describes the OPEN DOCUMENT. On a
            # hierarchical top sheet that is the page frame and the
            # block symbols, five entries where the design has a
            # hundred and eleven, so every part inside a block was
            # invisible and "where is IC1" answered "nowhere". The
            # netlist is flattened across the hierarchy and is the only
            # schematic read that sees the whole design.
            if side == "schematic":
                rows, netlist_reply = _netlist_entries()
                if rows is None:
                    not_searched.append(
                        {"side": side,
                         "reason": netlist_reply.get("reason")})
                    continue
                for designator, props, pins, unique_id in rows:
                    searched += 1
                    value = str(props.get("Value")
                                or props.get("DeviceName") or "").strip()
                    if not designator and not value:
                        unreadable += 1
                        continue
                    if (needle in designator.lower()
                            or needle in value.lower()):
                        matches.append({
                            "side": side,
                            "designator": designator,
                            "value": value,
                            "primitive_id": unique_id,
                        })
                continue

            reply = _call(command, timeout=60.0)
            if not reply.get("ok"):
                # ONE SIDE FAILING MUST NOT LOSE THE OTHER. Asking for
                # "both" from a schematic tab refused the whole search
                # because the board half was unreachable, even though
                # the schematic half answers perfectly. A caller looking
                # for R47 got nothing and no hits, which reads as "no
                # such part".
                #
                # Both sides failing is still a failure: returning an
                # empty result then would be the search reporting that
                # the part does not exist anywhere.
                not_searched.append({"side": side,
                                     "reason": reply.get("reason")})
                continue
            for item in reply.get(key) or []:
                if not isinstance(item, dict):
                    continue
                searched += 1
                designator = _first(item, designator_keys)
                value = _first(item, value_keys)
                if not designator and not value:
                    # Neither field is readable under any spelling. Not
                    # a miss: this component could not be searched at
                    # all, and reporting it as "not found" would be a
                    # different claim from "could not look".
                    unreadable += 1
                    continue
                if needle in designator.lower() or needle in value.lower():
                    matches.append({
                        "side": side,
                        "designator": designator,
                        "value": value,
                        "primitive_id": item.get("primitiveId")
                                        or item.get("uuid"),
                    })

        if len(not_searched) == len(sides):
            return {
                "ok": False,
                "reason": ("no side could be searched, so this is not a "
                           "report that the part is absent"),
                "not_searched": not_searched,
            }

        found_on = sorted({m["side"] for m in matches})
        searched_sides = [s for s, _, _ in sides
                          if s not in [n["side"] for n in not_searched]]
        result = {
            "ok": True,
            "query": query,
            "scope": wanted,
            "sides_searched": searched_sides,
            "searched": searched,
            "unreadable_count": unreadable,
            "count": len(matches),
            "found_on": found_on,
            "components": matches,
        }
        # The "present on one side only" conclusion needs BOTH sides
        # searched. Drawing it from a half search would report a part
        # missing from a board nobody looked at.
        if wanted == "both" and not not_searched and len(found_on) == 1:
            result["note"] = ("present on the schematic but not the board"
                              if found_on == ["schematic"] else
                              "present on the board but not the schematic")
        else:
            result["note"] = ""
        if not_searched:
            result["not_searched"] = not_searched
            result["scope_warning"] = (
                f"{len(not_searched)} of {len(sides)} sides could not be "
                f"searched, so a part absent from the results may simply "
                f"be on the side that was not read")
        return result

    #: symbol_gen speaks the discipline's vocabulary; the editor speaks
    #: its own. Anything unrecognised becomes UNDEFINED rather than
    #: guessing: a wrong pin type is not a drawing fault, it is an ERC
    #: result that looks authoritative and is not.
    _PIN_TYPE_FROM_DISCIPLINE = {
        "input": "IN", "in": "IN",
        "output": "OUT", "out": "OUT",
        "bidirectional": "BI", "io": "BI", "bi": "BI",
        "power": "POWER", "ground": "GROUND",
        "passive": "PASSIVE",
        "hiz": "HIZ", "tristate": "HIZ",
        "open_collector": "OPEN_COLLECTOR",
        "open_emitter": "OPEN_EMITTER",
        "terminator": "TERMINATOR",
    }

    @mcp.tool()
    async def easyeda_add_pins(
        pins: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place many pins on the current symbol in one call.

        A symbol is the worst case for one-call-per-primitive: an SOIC-8
        costs eight round trips and a 100-ball BGA costs a hundred.

        Coordinates are the ELECTRICAL end in mils, the same as the
        single-pin tool, converted once here.

        Stops at the first failure and marks the rest skipped, so a
        half-pinned symbol is never mistaken for a finished one.

        Args:
            pins: a list of ``{"pin_number": ..., "x": ..., "y": ...,
                "name": ..., "pin_type": ..., "rotation": ...,
                "length": ...}``.
        """
        if not pins:
            return {"ok": False, "reason": "pins must not be empty"}
        prepared = []
        for index, pin in enumerate(pins):
            if not isinstance(pin, dict):
                return {"ok": False, "reason": f"entry {index} is not an object"}
            number = str(pin.get("pin_number") or "").strip()
            if not number:
                return {"ok": False, "reason": (
                    f"entry {index} has no pin_number, which is what ties "
                    f"a symbol pin to a footprint pad")}
            try:
                x = _sch(float(pin["x"]))
                y = _sch(float(pin["y"]))
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "reason": (
                    f"entry {index} has no usable x and y")}
            entry: dict[str, Any] = {
                "pin_number": number, "x": x, "y": y,
                "name": str(pin.get("name") or ""),
                "pin_type": str(pin.get("pin_type") or "UNDEFINED").upper(),
                "rotation": float(pin.get("rotation") or 0.0),
            }
            length = pin.get("length")
            if isinstance(length, (int, float)):
                entry["length"] = _sch(float(length))
            prepared.append(entry)
        return _call("sch.add_pins", {"pins": prepared}, timeout=300.0)

    async def _easyeda_amplifier_symbol(library_uuid: str, name: str,
                                        description: str,
                                        part: dict) -> dict[str, Any]:
        """Triangle amplifier symbol: create, open, pins, triangle, marks."""
        import math

        created = await easyeda_create_symbol(
            library_uuid=library_uuid, name=name, description=description)
        if not created.get("ok"):
            return created
        uuid = str(created.get("uuid") or created.get("value") or "").strip()
        if not uuid:
            return {"ok": False, "reason": (
                "the editor created the symbol but returned no uuid, so "
                "it cannot be opened to draw into"), "created": created}
        opened = await easyeda_open_symbol(uuid=uuid,
                                           library_uuid=library_uuid)
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the symbol was created but could not be opened, so "
                "nothing was drawn into it"), "created": created,
                "opened": opened}
        batch = []
        for pin in part["pins"]:
            length = float(pin["length"])
            # symbol_gen gives the BODY end; the editor wants the
            # ELECTRICAL end, one pin length along the pin's direction,
            # which here includes straight up and straight down.
            a = math.radians(float(pin["rotation"]))
            discipline = str(pin.get("electrical_type") or "passive").lower()
            batch.append({
                "pin_number": pin["designator"], "name": pin["name"],
                "x": float(pin["x"]) + round(length * math.cos(a)),
                "y": float(pin["y"]) + round(length * math.sin(a)),
                "rotation": float(pin["rotation"]), "length": length,
                "pin_type": _PIN_TYPE_FROM_DISCIPLINE.get(
                    discipline, "UNDEFINED"),
            })
        placed = await easyeda_add_pins(pins=batch)
        if not placed.get("ok"):
            return {"ok": False, "reason": (
                "the symbol was created but its pins were not placed, so "
                "it exists and is unusable"), "pins": placed}
        drawn = [await easyeda_add_schematic_polygon(
            points=[[float(x), float(y)] for x, y in poly["points"]])
            for poly in part["polygons"]]
        for t in part["texts"]:
            drawn.append(await easyeda_add_schematic_text(
                text=t["text"], x=float(t["x"]), y=float(t["y"])))
        return {"ok": True, "name": name, "uuid": uuid, "body": "amplifier",
                "pin_count": len(batch),
                "steps": {"created": created, "pins": placed,
                          "body": drawn}}

    @mcp.tool()
    async def easyeda_create_ic_symbol(
        library_uuid: str = "",
        name: str = "",
        left_pins: list[dict[str, Any]] | None = None,
        right_pins: list[dict[str, Any]] | None = None,
        description: str = "",
        body: str = "auto",
    ) -> dict[str, Any]:
        """Create a COMPLETE IC symbol in one call.

        STANDARD SYMBOLS FIRST: a single op-amp, comparator, current-sense
        or instrumentation amplifier is drawn as a TRIANGLE (inputs left
        with + and -, output at the apex, supplies top and bottom) when
        ``body="auto"`` and the pin names form IN+/IN-/OUT. A dual or quad
        stays a rectangle here, and the reply says so; multi-part symbols
        are drawn as triangles on the Altium side. ``body="rectangle"``
        forces the box.

        You decide the functional grouping, inputs and power on the
        LEFT, outputs on the RIGHT. This lays the pins out on the grid,
        sizes the body to clear the pin names, and emits the whole
        symbol: create, then every pin in one batch, then the body.

        The geometry comes from the SAME module the Altium side uses, so
        a symbol drawn here follows the same discipline rules and the
        two backends cannot drift apart on what a good symbol looks
        like.

        Args:
            library_uuid: the library to create it in, from
                ``easyeda_list_libraries``.
            name: the component name.
            left_pins: pins on the left, top to bottom. Each a dict with
                ``designator``, ``name`` and optional
                ``electrical_type`` (input, output, power, passive and
                so on).
            right_pins: pins on the right, top to bottom, same shape.
            description: the component description.
        """
        from eda_agent.design.symbol_gen import generate_ic_symbol

        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required; list them with "
                "easyeda_list_libraries")}
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}
        left = list(left_pins or [])
        right = list(right_pins or [])
        if not left and not right:
            return {"ok": False, "reason": (
                "a symbol needs at least one pin on one side")}

        from eda_agent.design.symbol_gen import (
            generate_amplifier_symbol, looks_like_amplifier)

        wanted_body = str(body or "auto").strip().lower()
        amp = None
        amp_note = ""
        if wanted_body == "auto" and looks_like_amplifier(left + right,
                                                          description):
            try:
                candidate = generate_amplifier_symbol(left + right)
            except ValueError:
                candidate = None
            if candidate is not None and candidate.part_count == 1:
                amp = candidate
            elif candidate is not None:
                amp_note = (
                    f"{candidate.part_count - 1} amplifier units need one "
                    "triangle per part, which is drawn on the Altium side; "
                    "drew a rectangle here")
        if amp is not None:
            return await _easyeda_amplifier_symbol(
                str(library_uuid).strip(), str(name).strip(),
                str(description or ""), amp.parts[0])

        try:
            geometry = generate_ic_symbol(left, right)
        except ValueError as exc:
            # Duplicate or missing pin designators. Caught before
            # anything is created, so a rejected symbol leaves no
            # half-built component behind.
            return {"ok": False, "reason": str(exc)}

        created = await easyeda_create_symbol(
            library_uuid=str(library_uuid).strip(), name=str(name).strip(),
            description=str(description or ""))
        if not created.get("ok"):
            return created

        # Pins and the body land on whatever is OPEN, not on whatever
        # was just created. Skipping this draws a correct symbol onto
        # the wrong document, which is worse than failing: the new part
        # stays empty and something else acquires eight stray pins.
        uuid = str(created.get("uuid") or created.get("value") or "").strip()
        if not uuid:
            return {"ok": False, "reason": (
                "the editor created the symbol but returned no uuid, so "
                "it cannot be opened to draw into"), "created": created}
        opened = await easyeda_open_symbol(
            uuid=uuid, library_uuid=str(library_uuid).strip())
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the symbol was created but could not be opened, so "
                "nothing was drawn into it"), "created": created,
                "opened": opened}

        batch = []
        for pin in geometry.pins:
            length = float(pin["length"])
            # symbol_gen gives the BODY end; the editor wants the
            # ELECTRICAL end. A left pin (rotation 180) reaches back by
            # its own length, a right pin (rotation 0) reaches forward.
            reach = -length if float(pin["rotation"]) == 180 else length
            discipline = str(pin.get("electrical_type") or "passive").lower()
            batch.append({
                "pin_number": pin["designator"],
                "name": pin["name"],
                "x": float(pin["x"]) + reach,
                "y": float(pin["y"]),
                "rotation": float(pin["rotation"]),
                "length": length,
                "pin_type": _PIN_TYPE_FROM_DISCIPLINE.get(
                    discipline, "UNDEFINED"),
            })

        placed = await easyeda_add_pins(pins=batch)
        if not placed.get("ok"):
            return {"ok": False, "reason": (
                "the symbol was created but its pins were not placed, so "
                "it exists and is unusable"), "pins": placed}

        # symbol_gen gives two corners with y1 ABOVE y2, since it works
        # in a y-up frame. The rectangle tool wants a top-left corner
        # plus extents, so the height is the drop from y1 down to y2.
        body = geometry.body
        drawn = await easyeda_add_schematic_rectangle(
            x=float(body["x1"]), y=float(body["y1"]),
            width=abs(float(body["x2"]) - float(body["x1"])),
            height=abs(float(body["y1"]) - float(body["y2"])))

        return {
            **({"note": amp_note} if amp_note else {}),
            "ok": True,
            "name": str(name).strip(),
            "uuid": uuid,
            "pin_count": len(batch),
            "width_mils": geometry.width_mils,
            "height_mils": geometry.height_mils,
            "body": dict(body),
            "steps": {"created": created, "pins": placed, "body": drawn},
        }

    #: The passive glyphs whose geometry is rectangles and polygons
    #: only. Everything else in symbol_gen needs open line segments, and
    #: EasyEDA's schematic API has no line primitive to draw them with.
    #: Measured from generate_passive_symbol rather than listed by hand:
    #: see the guard in tests/test_easyeda_tools.py.
    _DRAWABLE_PASSIVES = ("resistor", "inductor")

    @mcp.tool()
    async def easyeda_create_passive_symbol(
        library_uuid: str = "", name: str = "", kind: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a complete 2-pin passive symbol in one call.

        The companion to ``easyeda_create_ic_symbol``, using the same
        shared geometry module, so a resistor drawn here matches one
        drawn on the Altium side.

        ONLY resistor and inductor for now, and the reason is a genuine
        limit rather than unfinished work. EasyEDA's schematic
        primitives are arc, circle, polygon, rectangle, text, wire, pin
        and bus, with no LINE among them. A resistor is a rectangle and
        an inductor is a row of arcs. A capacitor is two plates, a diode a bar, a crystal
        two plates and a fuse a lead through a body, and every one of
        those is an open segment.

        A wire would draw the shape and would also be electrical, so the
        symbol would carry connectivity nobody asked for and the fault
        would surface later as a net joining two unrelated things.

        Args:
            library_uuid: the library to create it in, from
                ``easyeda_list_libraries``.
            name: the component name.
            kind: resistor or inductor. The other kinds
                ``generate_passive_symbol`` knows are refused here with
                an explanation.
            description: the component description.
        """
        from eda_agent.design.symbol_gen import generate_passive_symbol

        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required; list them with "
                "easyeda_list_libraries")}
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}

        wanted = str(kind or "").strip().lower()
        if not wanted:
            return {"ok": False, "reason": (
                f"kind is required, one of: "
                f"{', '.join(_DRAWABLE_PASSIVES)}")}

        try:
            geometry = generate_passive_symbol(wanted)
        except (ValueError, KeyError) as exc:
            return {"ok": False, "reason": str(exc)}

        if geometry.lines:
            return {"ok": False, "reason": (
                f"a {wanted} glyph is drawn with {len(geometry.lines)} "
                f"open line segment(s), and EasyEDA's schematic API has "
                f"no line primitive. Drawing them as wires would give "
                f"the symbol electrical connections it should not have. "
                f"Drawable kinds here: "
                f"{', '.join(_DRAWABLE_PASSIVES)}")}

        created = await easyeda_create_symbol(
            library_uuid=str(library_uuid).strip(), name=str(name).strip(),
            description=str(description or ""))
        if not created.get("ok"):
            return created

        uuid = str(created.get("uuid") or "").strip()
        if not uuid:
            return {"ok": False, "reason": (
                "the editor created the symbol but returned no uuid, so "
                "it cannot be opened to draw into"), "created": created}
        opened = await easyeda_open_symbol(
            uuid=uuid, library_uuid=str(library_uuid).strip())
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the symbol was created but could not be opened, so "
                "nothing was drawn into it"), "created": created,
                "opened": opened}

        batch = []
        for pin in geometry.pins:
            length = float(pin["length"])
            # Same body-end to electrical-end conversion the IC symbol
            # makes, and the same consequence for getting it wrong.
            reach = -length if float(pin["rotation"]) == 180 else length
            discipline = str(pin.get("electrical_type") or "passive").lower()
            batch.append({
                "pin_number": pin["designator"],
                "name": pin["name"],
                "x": float(pin["x"]) + reach,
                "y": float(pin["y"]),
                "rotation": float(pin["rotation"]),
                "length": length,
                "pin_type": _PIN_TYPE_FROM_DISCIPLINE.get(
                    discipline, "UNDEFINED"),
            })

        placed = await easyeda_add_pins(pins=batch)
        if not placed.get("ok"):
            return {"ok": False, "reason": (
                "the symbol was created but its pins were not placed, so "
                "it exists and is unusable"), "pins": placed}

        drawn = []
        for rect in geometry.rectangles:
            drawn.append(await easyeda_add_schematic_rectangle(
                x=float(rect["x1"]), y=float(rect["y1"]),
                width=abs(float(rect["x2"]) - float(rect["x1"])),
                height=abs(float(rect["y1"]) - float(rect["y2"]))))
        for poly in geometry.polygons:
            drawn.append(await easyeda_add_schematic_polygon(
                points=[[float(x), float(y)] for x, y in poly["points"]]))
        # Arcs (an inductor's turns) go as three points on the curve, the
        # form easyeda_add_schematic_arc takes: start, middle, end.
        import math
        for arc in geometry.arcs:
            cx, cy, r = float(arc["x"]), float(arc["y"]), float(arc["radius"])
            pts = [(cx + r * math.cos(math.radians(a)),
                    cy + r * math.sin(math.radians(a)))
                   for a in (arc["start"], (arc["start"] + arc["end"]) / 2,
                             arc["end"])]
            drawn.append(await easyeda_add_schematic_arc(
                start_x=pts[0][0], start_y=pts[0][1],
                reference_x=pts[1][0], reference_y=pts[1][1],
                end_x=pts[2][0], end_y=pts[2][1]))

        return {
            "ok": True,
            "name": str(name).strip(),
            "uuid": uuid,
            "kind": wanted,
            "pin_count": len(batch),
            "shapes_drawn": len(drawn),
            "steps": {"created": created, "pins": placed, "shapes": drawn},
        }

    #: footprint_gen names layers the way Altium does. EasyEDA knows
    #: neither name, and has no mechanical layer at all, so the
    #: courtyard goes on the documentation layer.
    _FOOTPRINT_LAYER_TO_EASYEDA = {
        "TopOverlay": "TOP_SILKSCREEN",
        "BottomOverlay": "BOTTOM_SILKSCREEN",
        "Mechanical1": "DOCUMENT",
    }

    #: footprint_gen asks for a roundrect. EasyEDA has no such shape
    #: name; a rectangle plus a corner radius is the same thing, and the
    #: extents match either way.
    _FOOTPRINT_SHAPE_TO_EASYEDA = {
        "roundrect": "RECTANGLE",
        "rect": "RECTANGLE",
        "rectangle": "RECTANGLE",
        "round": "ELLIPSE",
        "circle": "ELLIPSE",
        "ellipse": "ELLIPSE",
        "oval": "OBLONG",
        "oblong": "OBLONG",
    }

    @mcp.tool()
    async def easyeda_add_pads(
        pads: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place many pads on the current PCB or footprint in one call.

        A QFP-64 is sixty-four pads, and one call each is sixty-four
        round trips over a socket.

        Coordinates are in mils and take NO conversion. The PCB canvas
        already counts in mils, unlike the schematic canvas, which is
        the difference this backend keeps having to be careful about.

        Stops at the first failure and marks the rest skipped, so a
        part-padded footprint is never mistaken for a finished one.

        Args:
            pads: a list of ``{"pad_number": ..., "x": ..., "y": ...,
                "width": ..., "height": ..., "shape": ..., "layer": ...,
                "rotation": ..., "hole_diameter": ...}``.
        """
        if not pads:
            return {"ok": False, "reason": "pads must not be empty"}
        prepared = []
        for index, pad in enumerate(pads):
            if not isinstance(pad, dict):
                return {"ok": False, "reason": f"entry {index} is not an object"}
            number = str(pad.get("pad_number") or "").strip()
            if not number:
                return {"ok": False, "reason": (
                    f"entry {index} has no pad_number, which is what ties "
                    f"a pad to a symbol pin")}
            try:
                x = float(pad["x"])
                y = float(pad["y"])
                width = float(pad["width"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "reason": (
                    f"entry {index} needs x, y and width as numbers")}
            height = pad.get("height")
            prepared.append({
                "pad_number": number, "x": x, "y": y, "width": width,
                "height": float(height) if isinstance(height, (int, float))
                          else width,
                "shape": str(pad.get("shape") or "ELLIPSE").upper(),
                "layer": str(pad.get("layer") or "TOP").upper(),
                "net": str(pad.get("net") or ""),
                "rotation": float(pad.get("rotation") or 0.0),
                "hole_diameter": float(pad.get("hole_diameter") or 0.0),
                "hole_length": float(pad.get("hole_length") or 0.0),
                "corner_radius": float(pad.get("corner_radius") or 0.0),
            })
        return _call("pcb.add_pads", {"pads": prepared}, timeout=300.0)

    @mcp.tool()
    async def easyeda_create_standard_footprint(
        library_uuid: str = "", name: str = "", family: str = "",
        pin_count: int = 0, pitch: float = 50.0,
        pad_width: float = 24.0, pad_height: float = 30.0,
        row_span: float = 0.0, corner_radius: float = 0.0,
        hole_diameter: float = 0.0, description: str = "",
        rows: int = 0, cols: int = 0, skip: "list[str] | None" = None,
        tab_width: float = 0.0, tab_height: float = 0.0,
        exposed_pad: float = 0.0, courtyard: float = 10.0,
        body_width: float = 0.0, body_height: float = 0.0,
    ) -> dict[str, Any]:
        """Create a COMPLETE standard footprint in one call.

        The board-side companion to ``easyeda_create_ic_symbol``, using
        the same shared geometry module the Altium backend uses, so a
        land pattern generated here matches one generated there.

        Emits the pads in one batch, then the silkscreen outline and the
        courtyard.

        THIS IS A STARTING POINT, NOT A LAND PATTERN FROM A DATASHEET.
        It computes a regular geometry from the numbers given; it does
        not know the part. Check it against the manufacturer's
        recommended land pattern before using it on a board.

        Args:
            library_uuid: the library to create it in, from
                ``easyeda_list_libraries``.
            name: the footprint name.
            family: chip, sip, dual, header, tab, quad or bga.
            pin_count: total pads; the signal pins for tab; ignored for
                bga, whose count is rows x cols.
            pitch: centre-to-centre within a row, in mils.
            pad_width: pad width, in mils. The shared generator's pad_w,
                with its two meanings kept: the pad's X size in every
                family but quad (so ACROSS the row in dual), and ALONG
                the row in quad. For a land pattern from a package
                drawing use easyeda_create_footprint_from_package.
            pad_height: pad height, in mils.
            row_span: centre-to-centre between opposing rows, in mils.
                Required for dual and quad.
            corner_radius: rounding for rectangular pads, in mils. The
                shared geometry asks for a roundrect and EasyEDA carries
                the rounding separately, so the radius is chosen here
                rather than invented.
            hole_diameter: drill diameter, in mils. Zero means surface
                mount. A header is through-hole by nature and comes out
                unsolderable without this, so it is not optional in
                practice for the header family.
            description: the footprint description.
            rows, cols: the ball grid, for bga.
            skip: ball names to leave out of a bga, e.g. ["A1"].
            tab_width, tab_height: the tab pad, for tab, in mils; the
                tab takes the next number after the signal pins.
            exposed_pad: a square centre pad for quad, in mils; 0 for
                none. It takes the next number after the pins.
            courtyard: courtyard clearance past the pads, in mils; 0
                leaves the courtyard out.
            body_width, body_height: the body for the silkscreen
                outline, in mils; 0 draws it round the pads.
        """
        from eda_agent.design.footprint_gen import generate_footprint

        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required; list them with "
                "easyeda_list_libraries")}
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}
        fam = str(family or "").strip().lower()
        # A bga is sized by rows and cols; demanding a pin count as well
        # refused every bga before the generator was reached.
        if pin_count <= 0 and fam != "bga":
            return {"ok": False, "reason": "pin_count must be positive"}

        try:
            # Every shape the generator takes is passed through. Without
            # tab_w/tab_h, rows/cols and exposed_pad, tab and bga were
            # refused on every call and a quad could not have its thermal
            # pad, though the Altium twin builds all three.
            geometry = generate_footprint(
                fam, int(pin_count),
                pitch=float(pitch), pad_w=float(pad_width),
                pad_h=float(pad_height), row_span=float(row_span),
                hole=float(hole_diameter), rows=int(rows or 0),
                cols=int(cols or 0), skip=list(skip or []),
                tab_w=float(tab_width or 0.0),
                tab_h=float(tab_height or 0.0),
                exposed_pad=float(exposed_pad or 0.0),
                courtyard=float(courtyard),
                body_w=float(body_width or 0.0),
                body_h=float(body_height or 0.0))
        except (ValueError, KeyError) as exc:
            return {"ok": False, "reason": str(exc)}

        created = await easyeda_create_footprint(
            library_uuid=str(library_uuid).strip(), name=str(name).strip(),
            description=str(description or ""))
        if not created.get("ok"):
            return created

        uuid = str(created.get("uuid") or "").strip()
        if not uuid:
            return {"ok": False, "reason": (
                "the editor created the footprint but returned no uuid, "
                "so it cannot be opened to draw into"), "created": created}
        opened = await easyeda_open_footprint(
            uuid=uuid, library_uuid=str(library_uuid).strip())
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the footprint was created but could not be opened, so "
                "nothing was drawn into it"), "created": created,
                "opened": opened}

        batch = []
        for pad in geometry.pads:
            hole = float(pad.get("hole_size") or 0.0)
            batch.append({
                "pad_number": str(pad["designator"]),
                # No conversion: this canvas is already mils.
                "x": float(pad["x"]), "y": float(pad["y"]),
                "width": float(pad["x_size"]), "height": float(pad["y_size"]),
                "shape": _FOOTPRINT_SHAPE_TO_EASYEDA.get(
                    str(pad.get("shape") or "").lower(), "RECTANGLE"),
                # A drilled pad needs copper on both sides. TOP alone
                # leaves the underside unconnected, which looks right on
                # screen and fails on the board.
                "layer": "MULTI" if hole > 0 else "TOP",
                "hole_diameter": hole,
                "corner_radius": float(corner_radius or 0.0),
            })

        placed = await easyeda_add_pads(pads=batch)
        if not placed.get("ok"):
            return {"ok": False, "reason": (
                "the footprint was created but its pads were not placed, "
                "so it exists and cannot be soldered"), "pads": placed}

        drawn = []
        unknown_layers = set()
        for track in geometry.all_tracks():
            source_layer = str(track.get("layer") or "")
            layer = _FOOTPRINT_LAYER_TO_EASYEDA.get(source_layer)
            if layer is None:
                # Named a layer with no EasyEDA counterpart. Silently
                # dropping it would leave a footprint missing its
                # courtyard with nothing to say so.
                unknown_layers.add(source_layer)
                continue
            drawn.append(await easyeda_add_polyline(
                points=[[float(track["x1"]), float(track["y1"])],
                        [float(track["x2"]), float(track["y2"])]],
                layer=layer, width=float(track.get("width") or 6.0)))

        return {
            "ok": True,
            "name": str(name).strip(),
            "uuid": uuid,
            "family": fam,
            "pad_count": len(batch),
            "tracks_drawn": len(drawn),
            "unmapped_layers": sorted(unknown_layers),
            "width_mils": geometry.width_mils,
            "height_mils": geometry.height_mils,
            "note": ("a computed land pattern, not one from a datasheet; "
                     "check it against the manufacturer's recommendation"),
            "steps": {"created": created, "pads": placed},
        }

    #: Readback tolerance for the package footprint, mils. EasyEDA's stored
    #: precision has not been measured, so this is a stated assumption: a
    #: tenth of a mil (2.54 um), far below any land pattern tolerance.
    _READBACK_TOL_MILS = 0.1

    @mcp.tool()
    async def easyeda_create_footprint_from_package(
        library_uuid: str = "", package: Any = None, name: str = "",
        density: str = "", description: str = "",
        courtyard_layer: str = "DOCUMENT",
        drafting: "dict | None" = None,
    ) -> dict[str, Any]:
        """Create a COMPLETE footprint from a datasheet package drawing.

        The EasyEDA twin of ``lib_create_footprint_from_package``, from the
        same engine: the lands come from IPC-7351B for the package's
        terminal dimensions and tolerances, not from a pad list typed in.
        Draws the pads (rounded rectangles with their corner radius,
        circles, ovals, holes and slots), silkscreen kept clear of copper
        with a pin 1 dot, the body outline and a reference text on
        TOP_ASSEMBLY, the courtyard, and paste windows over an exposed
        pad on TOP_PASTE_MASK. Then it reads the pads back from the
        editor and compares count, centres and sizes.

        What the extension cannot express is REFUSED before anything is
        created, not drawn wrong: a non-plated hole (its pad call passes
        no plating flag) and a manual paste or mask expansion. The
        exposed pad's own full paste opening cannot be closed from here
        either; the reply says so and names the pad.

        DATASHEET DISCIPLINE: transcribe the package from the
        manufacturer's drawing fetched in this conversation, each
        dimension as min/nom/max with its page, and name the datasheet
        in ``source``.

        Args:
            library_uuid: the library, from ``easyeda_list_libraries``.
            package: the PackageSpec as an object or JSON text, in mm.
                The same schema as lib_create_footprint_from_package:
                ``family`` plus the dimensions it needs; two-row parts
                have their rows left and right, ``lead_span_x`` across
                them, ``lead_length`` the terminal along the span and
                ``lead_width`` across it.
            name: footprint name; default the package's name.
            density: L, N or M; default the package's own (N).
            description: appended to the generated description.
            courtyard_layer: where the courtyard goes. DOCUMENT by
                default, where easyeda_create_standard_footprint puts it;
                nothing readable on this backend reports a courtyard, so
                pick another layer only if your library uses one.
            drafting: drafting rule overrides, as for the Altium tool.

        Returns:
            ``{ok, name, uuid, pad_count, drawn, readback, layers,
            warnings, notes, steps}``. Coordinates go to the editor in
            mils, converted from the model's mm.
        """
        import math

        from eda_agent.design.landpattern.emit import (
            easyeda_plan,
            model_from_package,
        )
        from eda_agent.design.landpattern.spec import SpecError
        from eda_agent.units import MM_PER_MIL

        if not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "library_uuid is required; list them with "
                "easyeda_list_libraries")}
        try:
            model = model_from_package(package, density=density, name=name,
                                       description=description,
                                       drafting=drafting)
        except SpecError as exc:
            return {"ok": False, "reason": str(exc)}
        plan = easyeda_plan(model, courtyard_layer=courtyard_layer)
        if plan["unsupported"]:
            return {"ok": False, "created": False, "reason": (
                "this footprint needs what the EasyEDA extension cannot "
                "draw, so nothing was created: "
                + "; ".join(plan["unsupported"])),
                "warnings": model.warnings}

        lib = str(library_uuid).strip()
        created = await easyeda_create_footprint(
            library_uuid=lib, name=model.name, description=model.description)
        if not created.get("ok"):
            return created
        uuid = str(created.get("uuid") or "").strip()
        if not uuid:
            return {"ok": False, "reason": (
                "the editor created the footprint but returned no uuid, "
                "so it cannot be opened to draw into"), "created": created}
        opened = await easyeda_open_footprint(uuid=uuid, library_uuid=lib)
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the footprint was created but could not be opened, so "
                "nothing was drawn into it"), "created": created,
                "opened": opened}

        steps: dict[str, Any] = {"created": created, "opened": opened}

        def incomplete(what: str, reply: Any) -> dict[str, Any]:
            return {"ok": False, "name": model.name, "uuid": uuid,
                    "reason": (f"{what} failed; the footprint exists and is "
                               "incomplete"),
                    "failed_reply": reply, "steps": steps}

        placed = await easyeda_add_pads(pads=plan["pads"])
        steps["pads"] = placed
        if not placed.get("ok") or int(placed.get("placed") or 0) != len(
                plan["pads"]):
            return incomplete("placing the pads", placed)

        drawn = {"polylines": 0, "fills": 0, "arcs": 0, "texts": 0}
        for line in plan["polylines"]:
            reply = await easyeda_add_polyline(
                points=line["points"], layer=line["layer"],
                width=line["width"])
            if not reply.get("ok"):
                return incomplete(f"a polyline on {line['layer']}", reply)
            drawn["polylines"] += 1
        for fill in plan["fills"]:
            reply = await easyeda_add_fill(points=fill["points"],
                                           layer=fill["layer"])
            if not reply.get("ok"):
                return incomplete(f"a fill on {fill['layer']}", reply)
            drawn["fills"] += 1
        for arc in plan["arcs"]:
            reply = await easyeda_add_arc(
                start_x=arc["start_x"], start_y=arc["start_y"],
                end_x=arc["end_x"], end_y=arc["end_y"], angle=arc["angle"],
                layer=arc["layer"], width=arc["width"])
            if not reply.get("ok"):
                return incomplete(f"an arc on {arc['layer']}", reply)
            drawn["arcs"] += 1
        for text in plan["texts"]:
            reply = await easyeda_add_text(
                text=text["text"], x=text["x"], y=text["y"],
                font_size=text["font_size"], width=text["width"],
                layer=text["layer"], align=text["align"])
            if not reply.get("ok"):
                return incomplete(f"the text on {text['layer']}", reply)
            drawn["texts"] += 1

        # Read the pads back from the open footprint and compare.
        back = _call("pcb.pads")
        steps["readback"] = {"ok": back.get("ok"),
                             "count": len(back.get("pads") or [])}
        mismatches: list[dict] = []
        if not back.get("ok") or not isinstance(back.get("pads"), list):
            mismatches.append({"check": "readback", "read": back.get("reason")})
            rows: list[dict] = []
        else:
            rows = _easyeda_pad_rows(back["pads"])
        if rows and len(rows) != len(plan["pads"]):
            mismatches.append({"check": "pad_count",
                               "expected": len(plan["pads"]),
                               "read": len(rows)})
        tol_mm = _READBACK_TOL_MILS * MM_PER_MIL
        unused = list(range(len(rows)))
        for want in plan["pads"]:
            cands = [i for i in unused
                     if rows[i]["name"] == str(want["pad_number"])]
            if not cands:
                if rows:
                    mismatches.append({"check": "pad_missing",
                                       "pad": want["pad_number"]})
                continue
            wx, wy = want["x"] * MM_PER_MIL, want["y"] * MM_PER_MIL
            i = min(cands, key=lambda k: math.hypot(
                rows[k]["x_mm"] - wx, rows[k]["y_mm"] - wy))
            unused.remove(i)
            got = rows[i]
            for key, value in (("x_mm", wx), ("y_mm", wy),
                               ("w_mm", want["width"] * MM_PER_MIL),
                               ("h_mm", want["height"] * MM_PER_MIL)):
                if abs(got[key] - value) > tol_mm:
                    mismatches.append({"check": key, "pad": want["pad_number"],
                                       "expected": round(value, 6),
                                       "read": round(got[key], 6)})
        for i in unused:
            mismatches.append({"check": "pad_extra", "pad": rows[i]["name"]})
        return {
            "ok": not mismatches,
            "name": model.name,
            "uuid": uuid,
            "family": model.family,
            "density": model.density,
            "description": model.description,
            "pad_count": len(plan["pads"]),
            "drawn": drawn,
            "readback": {"ok": not mismatches, "tolerance_mils":
                         _READBACK_TOL_MILS, "mismatches": mismatches},
            "layers": plan["layers"],
            "warnings": model.warnings,
            "notes": plan["notes"],
            "steps": steps,
        }

    #: The spellings a pin or pad number has been seen under. Tried in
    #: order. EasyEDA's published reference lists methods rather than
    #: the shape of what they return, so a single guessed key would
    #: report every part as unreadable.
    _NUMBER_KEYS = ("number", "pinNumber", "padNumber", "designator",
                    "name")

    @mcp.tool()
    async def easyeda_audit_device_pin_parity(
        uuid: str = "", library_uuid: str = "",
    ) -> dict[str, Any]:
        """Do a device's symbol and footprint agree on their connections?

        ``easyeda_create_device`` refuses a device bound to neither
        half, but nothing checks the two against each other. An 8-pin
        symbol bound to a 4-pad footprint binds without complaint and
        fails at the netlist or at assembly.

        Compares pin NUMBERS, not counts. A symbol with pins 1 to 8 and
        a footprint with pads 1 to 7 plus 9 has matching counts and is
        wrong.

        REPORTS, does not judge. A footprint legitimately carries
        mounting pads, thermal pads and shield tabs that no symbol pin
        stands behind, so an extra pad is often correct. A pin with no
        pad is the direction that is nearly always a fault: it is a
        connection the schematic makes and the board cannot.

        CHANGES THE OPEN DOCUMENT. Counting means opening the symbol and
        then the footprint, and the footprint is left open when this
        returns. That side effect is the reason this is a separate tool
        rather than a check inside create_device.

        Args:
            uuid: the device's uuid.
            library_uuid: the library holding it.
        """
        if not str(uuid).strip() or not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "both uuid and library_uuid are required; find them with "
                "easyeda_search_devices")}

        device = await easyeda_get_device(
            uuid=str(uuid).strip(), library_uuid=str(library_uuid).strip())
        if not device.get("ok"):
            return device

        detail = device.get("device")
        if not isinstance(detail, dict):
            return {"ok": False, "reason": (
                "the editor returned no device detail, so there is "
                "nothing to read the two halves from"), "device": device}

        def _uuid_for(*keys: str) -> str:
            for key in keys:
                found = detail.get(key)
                if isinstance(found, str) and found.strip():
                    return found.strip()
            return ""

        symbol_uuid = _uuid_for("symbolUuid", "symbol_uuid", "symbol")
        footprint_uuid = _uuid_for(
            "footprintUuid", "footprint_uuid", "footprint")
        if not symbol_uuid or not footprint_uuid:
            return {"ok": False, "reason": (
                "this device is not bound to both a symbol and a "
                "footprint, so there are not two halves to compare"),
                "symbol_uuid": symbol_uuid,
                "footprint_uuid": footprint_uuid}

        def _numbers(items, label: str):
            """Numbers read from a list of pins or pads, plus the misses."""
            found, unreadable = [], 0
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                for key in _NUMBER_KEYS:
                    value = item.get(key)
                    if isinstance(value, (str, int)) and str(value).strip():
                        found.append(str(value).strip())
                        break
                else:
                    # No spelling matched. Counting it as absent would
                    # report a connection missing that was never looked
                    # for, so it is counted apart.
                    unreadable += 1
            return found, unreadable

        opened = await easyeda_open_symbol(
            uuid=symbol_uuid, library_uuid=str(library_uuid).strip())
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the symbol could not be opened, so its pins could not "
                "be counted"), "opened": opened}
        pins_reply = await easyeda_get_schematic_pins()
        if not pins_reply.get("ok"):
            return pins_reply
        pins, pins_unreadable = _numbers(pins_reply.get("pins"), "pin")

        opened = await easyeda_open_footprint(
            uuid=footprint_uuid, library_uuid=str(library_uuid).strip())
        if not opened.get("ok"):
            return {"ok": False, "reason": (
                "the footprint could not be opened, so its pads could "
                "not be counted"), "opened": opened}
        pads_reply = await easyeda_get_pads()
        if not pads_reply.get("ok"):
            return pads_reply
        pads, pads_unreadable = _numbers(pads_reply.get("pads"), "pad")

        pin_set, pad_set = set(pins), set(pads)
        pins_without_pads = sorted(pin_set - pad_set)
        pads_without_pins = sorted(pad_set - pin_set)
        duplicate_pins = sorted({n for n in pins if pins.count(n) > 1})
        duplicate_pads = sorted({n for n in pads if pads.count(n) > 1})

        return {
            "ok": True,
            "verified_live": pins_reply.get("verified_live"),
            "symbol_uuid": symbol_uuid,
            "footprint_uuid": footprint_uuid,
            "pin_count": len(pins),
            "pad_count": len(pads),
            "pins_unreadable": pins_unreadable,
            "pads_unreadable": pads_unreadable,
            "duplicate_pins": duplicate_pins,
            "duplicate_pads": duplicate_pads,
            "pins_without_pads": pins_without_pads,
            "pads_without_pins": pads_without_pins,
            "matched": not pins_without_pads and not pads_without_pins,
            # The agreed field across every audit, so one summariser
            # reads them all. This tool needed a DECISION rather than a
            # rename: pin_count and pad_count are the two sides of a
            # comparison, not a problem count. What is wrong with a
            # device is each pin the board cannot reach, each pad the
            # schematic never mentions, and each number appearing twice
            # on either side, since a duplicate makes the mapping
            # ambiguous even when the totals agree.
            "violation_count": (len(pins_without_pads)
                                + len(pads_without_pins)
                                + len(duplicate_pins)
                                + len(duplicate_pads)),
            "note": ("a pad with no pin is often a mounting or thermal "
                     "pad and correct; a PIN with no pad is a connection "
                     "the schematic makes and the board cannot"),
        }

    #: How an alignment word maps onto what actually happens. Stated
    #: once: the tool validates against these keys and the docstring
    #: lists them, so a new mode cannot be accepted and undocumented.
    _ALIGNMENTS = ("left", "right", "top", "bottom", "center_x", "center_y")

    async def _components_by_designator(wanted: "list[str] | None"):
        """Positions of the named components, plus what could not be read.

        ``wanted`` of None means every component on the board, which is
        what the snap tool asks for. A list means only those.

        Field names are read defensively for the usual reason: EasyEDA's
        published reference lists methods, not the shape of what they
        return.
        """
        reply = _call("pcb.components", timeout=60.0)
        if not reply.get("ok"):
            return reply, {}, [], 0

        by_designator: dict[str, dict[str, Any]] = {}
        unreadable = 0
        for item in reply.get("components") or []:
            if not isinstance(item, dict):
                continue
            designator = ""
            for key in ("designator", "name", "reference", "refDes"):
                found = item.get(key)
                if isinstance(found, str) and found.strip():
                    designator = found.strip()
                    break
            if wanted is not None and designator not in wanted:
                continue
            if not designator:
                # No readable designator. Nothing to report it under,
                # and nothing to match a caller's list against.
                unreadable += 1
                continue
            x, y = item.get("x"), item.get("y")
            identifier = item.get("primitiveId") or item.get("uuid")
            if not isinstance(x, (int, float)) or not isinstance(
                    y, (int, float)) or not identifier:
                # Position or identity unreadable. Moving it would mean
                # computing a target from a position nobody read.
                unreadable += 1
                continue
            by_designator[designator] = {
                "primitive_id": identifier, "x": float(x), "y": float(y)}

        missing = (sorted(set(wanted) - set(by_designator))
                   if wanted is not None else [])
        return reply, by_designator, missing, unreadable

    @mcp.tool()
    async def easyeda_align_components(
        designators: str = "", alignment: str = "left",
    ) -> dict[str, Any]:
        """Line placed components up along one edge or centre.

        ALIGNS ORIGINS, not visible edges. EasyEDA reports a
        component's position as its origin and nothing readable gives
        its extent on the board, so for a row of identical parts this is
        exactly edge alignment and for a mixed row it is not. Worth
        knowing before using it on parts of different sizes.

        Moves ONE axis. Aligning left changes x and leaves y alone,
        which is what makes it an alignment rather than a stacking.

        One read and one bulk write, whatever the count.

        Args:
            designators: comma separated, for example "R1,R2,R3".
            alignment: left, right, top, bottom, center_x or center_y.
                The centre modes use the midpoint of the outermost two,
                so the group keeps its position rather than drifting
                toward wherever most of the parts happen to be.
        """
        wanted = [d.strip() for d in str(designators or "").split(",")
                  if d.strip()]
        if len(wanted) < 2:
            return {"ok": False, "reason": (
                "name at least two components; one component has nothing "
                "to align to")}

        mode = str(alignment or "").strip().lower()
        if mode not in _ALIGNMENTS:
            return {"ok": False, "reason": (
                f"alignment must be one of: {', '.join(_ALIGNMENTS)}")}

        reply, found, missing, unreadable = await _components_by_designator(
            wanted)
        if not reply.get("ok"):
            return reply
        if len(found) < 2:
            return {"ok": False, "reason": (
                f"found {len(found)} of the {len(wanted)} named "
                f"components on the board, which is not enough to align. "
                f"Not found: {missing}"), "unreadable": unreadable}

        xs = [c["x"] for c in found.values()]
        ys = [c["y"] for c in found.values()]
        axis, target = {
            "left": ("x", min(xs)),
            "right": ("x", max(xs)),
            "top": ("y", max(ys)),
            "bottom": ("y", min(ys)),
            "center_x": ("x", (min(xs) + max(xs)) / 2.0),
            "center_y": ("y", (min(ys) + max(ys)) / 2.0),
        }[mode]

        changes = [
            {"primitive_id": c["primitive_id"], "changes": {axis: target}}
            for c in found.values() if c[axis] != target
        ]
        if not changes:
            return {"ok": True, "moved": 0, "axis": axis, "target": target,
                    "already_aligned": True, "not_found": missing,
                    "unreadable": unreadable}

        applied = _call("pcb.modify_components", {"changes": changes},
                        timeout=300.0)
        return {
            "ok": bool(applied.get("ok")),
            "verified_live": applied.get("verified_live"),
            "alignment": mode, "axis": axis, "target": target,
            "moved": len(changes),
            "not_found": missing,
            "unreadable": unreadable,
            "result": applied,
        }

    @mcp.tool()
    async def easyeda_distribute_components(
        designators: str = "", axis: str = "x",
    ) -> dict[str, Any]:
        """Space placed components evenly between the outermost two.

        The outermost two do not move: they define the span. Everything
        between them is spread at equal spacing, in the order their
        current positions put them, not the order they were named.
        Sorting by the given order would let a mistyped list reorder the
        board.

        Like the alignment tool, this works on ORIGINS rather than
        visible edges, so equal origin spacing means equal gaps only
        when the parts are the same size.

        Args:
            designators: comma separated, for example "R1,R2,R3,R4".
                At least three, since two are already as evenly spaced
                as two things can be.
            axis: x to spread horizontally, y vertically.
        """
        wanted = [d.strip() for d in str(designators or "").split(",")
                  if d.strip()]
        if len(wanted) < 3:
            return {"ok": False, "reason": (
                "name at least three components; two are already as "
                "evenly spaced as two things can be")}

        which = str(axis or "").strip().lower()
        if which not in ("x", "y"):
            return {"ok": False, "reason": "axis must be x or y"}

        reply, found, missing, unreadable = await _components_by_designator(
            wanted)
        if not reply.get("ok"):
            return reply
        if len(found) < 3:
            return {"ok": False, "reason": (
                f"found {len(found)} of the {len(wanted)} named "
                f"components on the board, which is not enough to "
                f"distribute. Not found: {missing}"),
                "unreadable": unreadable}

        ordered = sorted(found.values(), key=lambda c: c[which])
        first, last = ordered[0][which], ordered[-1][which]
        step = (last - first) / (len(ordered) - 1)

        changes = []
        for index, component in enumerate(ordered[1:-1], start=1):
            target = first + step * index
            if component[which] != target:
                changes.append({"primitive_id": component["primitive_id"],
                                "changes": {which: target}})

        if not changes:
            return {"ok": True, "moved": 0, "axis": which, "spacing": step,
                    "already_even": True, "not_found": missing,
                    "unreadable": unreadable}

        applied = _call("pcb.modify_components", {"changes": changes},
                        timeout=300.0)
        return {
            "ok": bool(applied.get("ok")),
            "verified_live": applied.get("verified_live"),
            "axis": which, "spacing": step,
            "span": [first, last],
            "moved": len(changes),
            "not_found": missing,
            "unreadable": unreadable,
            "result": applied,
        }

    @mcp.tool()
    async def easyeda_snap_components_to_grid(
        designators: str = "", grid: float = 25.0, confirm: bool = False,
    ) -> dict[str, Any]:
        """Move placed components onto the nearest grid point.

        The other half of ``easyeda_audit_off_grid_components``, which
        reports offsets and cannot correct them.

        DESTRUCTIVE, and quietly so. Every part moves a fraction of a
        grid step, so the board looks the same afterwards and a mistake
        does not show in a screenshot. A connector aligned to a panel
        cutout, a mounting hole on a customer's pattern and a part
        placed to a mechanical dimension are all off-grid deliberately,
        and nothing here can tell them from a slip. Name the parts
        rather than snapping everything unless the board really is
        meant to be entirely on-grid.

        Reports each move with its before and after, so a wrong snap can
        be undone by hand.

        Args:
            designators: comma separated, for example "R1,R2,C7". Empty
                means EVERY component on the board.
            grid: the grid pitch in mils. Must be positive.
            confirm: must be true for anything to move.
        """
        if not confirm:
            return {"ok": False, "reason": (
                "snapping moves components a small distance each, which "
                "does not show in a render. Pass confirm=True if that is "
                "intended.")}
        if grid <= 0:
            return {"ok": False, "reason": "grid must be positive"}

        named = [d.strip() for d in str(designators or "").split(",")
                 if d.strip()]
        reply, found, missing, unreadable = await _components_by_designator(
            named or None)
        if not reply.get("ok"):
            return reply
        if not found:
            return {"ok": False, "reason": (
                f"no components to snap. Not found: {missing}"
                if missing else "the board has no readable components"),
                "unreadable": unreadable}

        def _snapped(value: float) -> float:
            return round(value / grid) * grid

        changes, moves = [], []
        for designator, component in sorted(found.items()):
            x, y = _snapped(component["x"]), _snapped(component["y"])
            if x == component["x"] and y == component["y"]:
                continue
            changes.append({"primitive_id": component["primitive_id"],
                            "changes": {"x": x, "y": y}})
            moves.append({
                "designator": designator,
                "from": [component["x"], component["y"]],
                "to": [x, y],
                "moved_by": [x - component["x"], y - component["y"]],
            })

        if not changes:
            return {"ok": True, "moved": 0, "grid": grid,
                    "already_on_grid": True, "considered": len(found),
                    "not_found": missing, "unreadable": unreadable}

        applied = _call("pcb.modify_components", {"changes": changes},
                        timeout=300.0)
        return {
            "ok": bool(applied.get("ok")),
            "verified_live": applied.get("verified_live"),
            "grid": grid,
            "considered": len(found),
            "moved": len(changes),
            "moves": moves,
            "not_found": missing,
            "unreadable": unreadable,
            "result": applied,
        }

    @mcp.tool()
    async def easyeda_get_bounding_boxes(
        primitive_ids: str = "",
    ) -> dict[str, Any]:
        """One bounding box per primitive, in a single call.

        Different from ``easyeda_get_bounding_box``, which returns ONE
        box enclosing everything it is given. That is the right answer
        for "how big is this group" and the wrong one for "where is each
        of these", and getting each separately used to cost a round trip
        apiece.

        A box that cannot be measured comes back as null rather than
        stopping the batch: this is a read, and the answers already
        gathered are worth keeping.

        Args:
            primitive_ids: comma separated, from any query result.
        """
        ids = [i.strip() for i in str(primitive_ids).split(",") if i.strip()]
        if not ids:
            return {"ok": False, "reason": (
                "primitive_ids is required, comma separated, from a query "
                "result")}
        return _call("pcb.bboxes", {"primitive_ids": ids}, timeout=300.0)

    @mcp.tool()
    async def easyeda_audit_placement_collisions(
        clearance: float = 0.0,
    ) -> dict[str, Any]:
        """Placed components whose extents overlap or nearly touch.

        REPORTS, does not judge. A bounding box encloses a footprint's
        silkscreen as well as its copper, and silkscreen crossing a
        neighbour is routine. What actually decides whether two parts
        can be assembled is a courtyard, and nothing readable on this
        backend reports one. So a pair here is a place to look.

        Two reads whatever the board size: every component, then every
        bounding box in one batch.

        Args:
            clearance: report pairs closer than this, in mils. Zero
                reports only true overlaps. A small positive value finds
                the near misses, which are often the real problem: parts
                two mils apart pass an overlap test and cannot be hand
                soldered.
        """
        if clearance < 0:
            return {"ok": False, "reason": "clearance cannot be negative"}

        components = _call("pcb.components", timeout=60.0)
        if not components.get("ok"):
            return components

        by_id: dict[str, str] = {}
        for item in components.get("components") or []:
            if not isinstance(item, dict):
                continue
            identifier = item.get("primitiveId") or item.get("uuid")
            if not identifier:
                continue
            designator = ""
            for key in ("designator", "name", "reference", "refDes"):
                found = item.get(key)
                if isinstance(found, str) and found.strip():
                    designator = found.strip()
                    break
            by_id[str(identifier)] = designator or str(identifier)

        if len(by_id) < 2:
            # The early return needs the agreed field too: an
            # aggregator reading a reply with no violation_count cannot
            # tell "nothing to check" from "key I do not know", and the
            # safe reading of the latter is not zero.
            return {"ok": True, "components_counted": len(by_id),
                    "pairs_checked": 0, "violation_count": 0,
                    "collision_count": 0,
                    "collisions": [],
                    "note": "fewer than two components: nothing can collide"}

        boxes = _call("pcb.bboxes",
                      {"primitive_ids": sorted(by_id)}, timeout=300.0)
        if not boxes.get("ok"):
            return boxes

        measured: dict[str, tuple] = {}
        unmeasured = 0
        for entry in boxes.get("boxes") or []:
            if not isinstance(entry, dict):
                continue
            box = entry.get("bbox")
            identifier = str(entry.get("primitive_id") or "")
            if not isinstance(box, dict) or not identifier:
                unmeasured += 1
                continue
            try:
                measured[identifier] = (
                    float(box["minX"]), float(box["minY"]),
                    float(box["maxX"]), float(box["maxY"]))
            except (KeyError, TypeError, ValueError):
                # A box in some other shape. Treating it as absent is
                # right: a pair not compared must not read as a pair
                # that was compared and found clear.
                unmeasured += 1

        findings = []
        identifiers = sorted(measured)
        pairs = 0
        for index, first in enumerate(identifiers):
            ax1, ay1, ax2, ay2 = measured[first]
            for second in identifiers[index + 1:]:
                pairs += 1
                bx1, by1, bx2, by2 = measured[second]
                # Gap along each axis: negative means they overlap on
                # that axis. Two boxes clash only when BOTH axes do.
                gap_x = max(bx1 - ax2, ax1 - bx2)
                gap_y = max(by1 - ay2, ay1 - by2)
                separation = max(gap_x, gap_y)
                # One comparison covers both cases. At clearance 0 a
                # separation of exactly 0 is two boxes touching, which
                # is not an overlap and is correctly skipped.
                if separation >= clearance:
                    continue
                findings.append({
                    "a": by_id.get(first, first),
                    "b": by_id.get(second, second),
                    "separation": separation,
                    "overlapping": separation < 0,
                })

        findings.sort(key=lambda f: f["separation"])
        return {
            "ok": True,
            "verified_live": boxes.get("verified_live"),
            "components_counted": len(by_id),
            "measured": len(measured),
            "unmeasured": unmeasured,
            "pairs_checked": pairs,
            "clearance": clearance,
            "violation_count": len(findings),
            "collision_count": len(findings),
            "collisions": findings,
            "note": ("a bounding box includes silkscreen, so an overlap "
                     "is a place to look rather than a fault; a "
                     "courtyard is what decides assembly and this "
                     "backend does not report one"),
        }

    @mcp.tool()
    async def easyeda_get_board_statistics() -> dict[str, Any]:
        """Counts and extents for the whole board, in one call.

        What a review opens with and what a fab quote needs: how many
        components, nets, pads, vias and track segments, how much copper
        is routed, how many layers, and how big the board is.

        Five reads bundled into one round trip. The board extent comes
        out of the same track list rather than a sixth read, since the
        outline on this backend is just lines on a particular layer.

        A section whose read fails is reported as UNAVAILABLE with the
        reason, never as zero. "No vias on this board" and "the via list
        could not be read" are different statements, and quoting a fab
        from the second one mistaken for the first is how a board comes
        back missing its drills.

        Track length is the sum of the segments the editor reports and
        is only as good as those coordinates. Segments whose endpoints
        cannot be read are counted separately rather than treated as
        zero length.
        """
        sections: dict[str, Any] = {}
        unavailable: dict[str, str] = {}

        def _read(name: str, command: str, key: str, timeout: float = 60.0):
            reply = _call(command, timeout=timeout)
            if not reply.get("ok"):
                unavailable[name] = str(
                    reply.get("reason") or "the editor did not answer")
                return None
            value = reply.get(key)
            return value if isinstance(value, list) else []

        components = _read("components", "pcb.components", "components")
        if components is not None:
            sections["component_count"] = len(components)

        nets = _read("nets", "pcb.nets", "nets")
        if nets is not None:
            sections["net_count"] = len(nets)

        pads = _read("pads", "pcb.pads", "pads", timeout=120.0)
        if pads is not None:
            netted = 0
            for pad in pads:
                if isinstance(pad, dict) and str(
                        pad.get("net") or "").strip():
                    netted += 1
            sections["pad_count"] = len(pads)
            sections["pads_on_a_net"] = netted

        vias = _read("vias", "pcb.vias", "vias")
        if vias is not None:
            sections["via_count"] = len(vias)

        layers = _read("layers", "pcb.layers", "layers")
        if layers is not None:
            sections["layer_count"] = len(layers)

        lines = _read("lines", "pcb.lines", "lines", timeout=120.0)
        if lines is not None:
            per_layer: dict[str, dict[str, float]] = {}
            widths: list[float] = []
            unmeasured = 0
            for line in lines:
                if not isinstance(line, dict):
                    continue
                layer = str(line.get("layer") or "unknown")
                entry = per_layer.setdefault(
                    layer, {"segments": 0, "length": 0.0})
                entry["segments"] += 1
                width = line.get("lineWidth", line.get("width"))
                if isinstance(width, (int, float)):
                    widths.append(float(width))
                try:
                    # startX/endX, the spelling get_board_outline
                    # already reads this same list with. x1/y1 is the
                    # obvious guess and is wrong here: it would mark
                    # every segment unmeasured and report a board with
                    # no routing on it.
                    dx = float(line["endX"]) - float(line["startX"])
                    dy = float(line["endY"]) - float(line["startY"])
                except (KeyError, TypeError, ValueError):
                    # Endpoints unreadable. Adding nothing would report
                    # this segment as zero length, which understates the
                    # copper on the board.
                    unmeasured += 1
                    continue
                entry["length"] += (dx * dx + dy * dy) ** 0.5

            sections["track_segment_count"] = len(lines)
            sections["tracks_by_layer"] = {
                layer: {"segments": int(v["segments"]),
                        "length_mils": round(v["length"], 3)}
                for layer, v in sorted(per_layer.items())}
            sections["routed_length_mils"] = round(
                sum(v["length"] for v in per_layer.values()), 3)
            sections["segments_without_readable_endpoints"] = unmeasured
            sections["narrowest_track_mils"] = min(widths) if widths else None
            sections["widest_track_mils"] = max(widths) if widths else None

        # Derived from the line list already read, plus the arcs.
        #
        # Lines alone are not enough: a rounded or routed board can draw
        # its entire outline as arcs with no straight segments, and the
        # extent then reads as absent on a board that plainly has one.
        # The arcs are a second read because the line list does not
        # contain them.
        if lines is None:
            unavailable["board_extent"] = unavailable.get(
                "lines", "the line list could not be read")
        else:
            outline_arcs = []
            arcs_reply = _call("pcb.arcs", timeout=60.0)
            if arcs_reply.get("ok"):
                outline_arcs = [a for a in (arcs_reply.get("arcs") or [])
                                if isinstance(a, dict)]
            xs: list[float] = []
            ys: list[float] = []
            for line in list(lines) + outline_arcs:
                if not isinstance(line, dict):
                    continue
                if str(line.get("layer", "")).upper().replace(
                        " ", "_") not in ("BOARD_OUTLINE", "11"):
                    continue
                for key in ("startX", "endX"):
                    value = line.get(key)
                    if isinstance(value, (int, float)):
                        xs.append(float(value))
                for key in ("startY", "endY"):
                    value = line.get(key)
                    if isinstance(value, (int, float)):
                        ys.append(float(value))
            if xs and ys:
                sections["board_extent"] = {
                    "min_x": min(xs), "min_y": min(ys),
                    "max_x": max(xs), "max_y": max(ys),
                    "width": max(xs) - min(xs),
                    "height": max(ys) - min(ys),
                }
            else:
                # Not a failure: a board with nothing on its outline
                # layer has no outline yet.
                unavailable["board_extent"] = (
                    "nothing is drawn on the board outline layer")

        # Reporting ok:True with an EMPTY sections dict is the same
        # failure the review aggregator had: with no editor connected
        # every section lands in `unavailable`, and a caller reading
        # the envelope alone is told the call succeeded. It did not
        # measure anything. Partial data is still a success, because
        # some sections legitimately have nothing to report; no data at
        # all is not.
        out = {
            "ok": bool(sections),
            "sections": sections,
            "unavailable": unavailable,
            "complete": not unavailable,
            "note": ("a section that could not be read is listed under "
                     "unavailable rather than reported as zero"),
        }
        if not sections:
            out["reason"] = (
                "not one section could be read, so this is not an empty "
                "board: nothing was measured. See 'unavailable' for why.")
        return out

    @mcp.tool()
    async def easyeda_delete_symbol(
        uuid: str = "", library_uuid: str = "", confirm: bool = False,
    ) -> dict[str, Any]:
        """Remove a symbol from a library.

        DESTRUCTIVE. Refused unless ``confirm`` is true, and the
        extension refuses independently.

        A symbol can be BOUND to a device, and deleting it leaves that
        device pointing at a drawing that no longer exists. Nothing
        readable on this backend reports which devices use which symbol,
        so this cannot check and does not pretend to. Delete the device
        first if the whole part is going.

        Boards already using the part keep their placed copies, so a
        design keeps working while the library entry is gone.

        Args:
            uuid: the symbol.
            library_uuid: the library it lives in.
            confirm: must be true for anything to happen.
        """
        if not str(uuid).strip() or not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not confirm:
            return {"ok": False, "reason": (
                "delete_symbol removes the drawing from the library, and "
                "any device bound to it will point at nothing. Pass "
                "confirm=True if that is intended.")}
        return _call("lib.delete_symbol", {
            "uuid": uuid, "library_uuid": library_uuid, "confirm": True,
        })

    @mcp.tool()
    async def easyeda_delete_footprint(
        uuid: str = "", library_uuid: str = "", confirm: bool = False,
    ) -> dict[str, Any]:
        """Remove a footprint from a library.

        DESTRUCTIVE. Refused unless ``confirm`` is true, and the
        extension refuses independently.

        The same warning as ``easyeda_delete_symbol``: a footprint bound
        to a device leaves that device pointing at a land pattern that
        is gone, and nothing readable here reports the binding.

        Args:
            uuid: the footprint.
            library_uuid: the library it lives in.
            confirm: must be true for anything to happen.
        """
        if not str(uuid).strip() or not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not confirm:
            return {"ok": False, "reason": (
                "delete_footprint removes the land pattern from the "
                "library, and any device bound to it will point at "
                "nothing. Pass confirm=True if that is intended.")}
        return _call("lib.delete_footprint", {
            "uuid": uuid, "library_uuid": library_uuid, "confirm": True,
        })

    @mcp.tool()
    async def easyeda_rename_device(
        uuid: str = "", library_uuid: str = "",
        name: str = "", description: str = "",
    ) -> dict[str, Any]:
        """Rename a library device or change its description.

        The device is the placeable object, so this is the name a
        designer searches for. Renaming the symbol and the footprint
        leaves that name unchanged, which is why the matrix needed this
        one to be complete.

        Args:
            uuid: the device.
            library_uuid: the library it lives in.
            name: the new name. Left alone when empty.
            description: the new description. Left alone when empty.
        """
        if not str(uuid).strip() or not str(library_uuid).strip():
            return {"ok": False, "reason": (
                "uuid and library_uuid are both required")}
        if not str(name).strip() and not str(description).strip():
            return {"ok": False, "reason": (
                "give a name or a description; an empty change reports "
                "success while doing nothing")}
        return _call("lib.modify_device", {
            "uuid": uuid, "library_uuid": library_uuid,
            "name": name, "description": description,
        })

    #: Whole-string matches, after trimming and lowercasing. Deliberately
    #: not a substring test: a resistor legitimately valued "1k" would
    #: match a substring rule for "1", and a part named "NAND" contains
    #: "na". Every entry here is a word somebody typed MEANING to come
    #: back to it.
    _PLACEHOLDER_VALUES = frozenset({
        "tbd", "tba", "todo", "fixme", "xxx", "xx",
        "?", "??", "???", "-", "--",
        "placeholder", "filler", "ask", "unknown",
        "n/a", "na", "none", "null", "value", "changeme",
    })

    @mcp.tool()
    async def easyeda_audit_placeholder_values(
        scope: str = "both",
    ) -> dict[str, Any]:
        """Components still carrying an "I'll fix this later" string.

        A part goes down as a placeholder, TBD goes in its value, and
        six months later the assembly drawing says TBD and the fab
        calls. Cheap to find here and expensive to find there.

        Matched whole-string after trimming, case-insensitive: tbd, tba,
        todo, fixme, xxx, xx, ?, ??, ???, -, --, placeholder, filler,
        ask, unknown, n/a, na, none, null, value, changeme.

        Whole-string on purpose. A substring rule would flag a resistor
        valued "1k" for containing "1" and a part named "NAND" for
        containing "na".

        EMPTY values are not reported. A connector, a mounting hole and
        most ICs carry no value by design, so flagging them would bury
        the real findings.

        Scans every readable string field rather than one named value,
        because EasyEDA's published reference lists methods and not the
        shape of what they return. That also catches a placeholder left
        in a manufacturer or part-number field, which is where it does
        the most damage.

        Args:
            scope: "both" (default), "pcb" or "schematic".
        """
        wanted = str(scope or "both").strip().lower()
        if wanted not in ("both", "pcb", "schematic"):
            return {"ok": False, "reason": (
                f"scope must be both, pcb or schematic, not {scope!r}")}

        sides = []
        if wanted in ("both", "pcb"):
            sides.append(("pcb", "pcb.components"))
        if wanted in ("both", "schematic"):
            sides.append(("schematic", "sch.components"))

        findings = []
        checked_fields = 0
        components = 0
        for side, command in sides:
            reply = _call(command, timeout=60.0)
            if not reply.get("ok"):
                return reply
            for item in reply.get("components") or []:
                if not isinstance(item, dict):
                    continue
                components += 1
                designator = ""
                for key in ("designator", "name", "reference", "refDes"):
                    found = item.get(key)
                    if isinstance(found, str) and found.strip():
                        designator = found.strip()
                        break
                for field, value in sorted(item.items()):
                    if not isinstance(value, str):
                        continue
                    checked_fields += 1
                    trimmed = value.strip()
                    if not trimmed:
                        # Empty is a different finding and belongs to a
                        # different audit; reporting it here would bury
                        # these.
                        continue
                    if trimmed.lower() in _PLACEHOLDER_VALUES:
                        findings.append({
                            "side": side,
                            "designator": designator or "(unnamed)",
                            "field": field,
                            "value": trimmed,
                        })

        findings.sort(key=lambda f: (f["designator"], f["field"]))
        return {
            "ok": True,
            "scope": wanted,
            "components_checked": components,
            "fields_checked": checked_fields,
            "violation_count": len(findings),
            "placeholder_count": len(findings),
            "placeholders": findings,
            "note": ("empty values are not reported here: most ICs and "
                     "connectors carry none by design"),
        }

    @mcp.tool()
    async def easyeda_audit_via_annular_ring(
        minimum_ring: float = 5.0,
    ) -> dict[str, Any]:
        """Vias whose copper ring around the drill is too thin to make.

        The ring is (diameter - holeDiameter) / 2. Too thin and drill
        wander breaks out of the pad: an open circuit that appears on
        some boards of a batch and not others, which is the most
        expensive kind of fault to chase.

        Field names are measured rather than assumed: a session on
        reported every via carrying ``diameter``,
        ``holeDiameter``, ``net``, ``x``, ``y`` and ``primitiveId``.
        A via missing either number is counted as unreadable rather
        than passed, since a ring nobody computed is not a ring that
        passed.

        Args:
            minimum_ring: smallest acceptable ring, in mils. The
                default is a common fab floor; check the chosen fab's
                capability table rather than trusting it.
        """
        if minimum_ring <= 0:
            return {"ok": False, "reason": "minimum_ring must be positive"}

        reply = _call("pcb.vias", timeout=60.0)
        if not reply.get("ok"):
            return reply

        findings = []
        unreadable = 0
        counted = 0
        for via in reply.get("vias") or []:
            if not isinstance(via, dict):
                continue
            counted += 1
            diameter = via.get("diameter")
            hole = via.get("holeDiameter")
            if not isinstance(diameter, (int, float)) or not isinstance(
                    hole, (int, float)):
                unreadable += 1
                continue
            ring = (float(diameter) - float(hole)) / 2.0
            if ring < minimum_ring:
                findings.append({
                    "primitive_id": via.get("primitiveId"),
                    "net": via.get("net"),
                    "x": via.get("x"), "y": via.get("y"),
                    "diameter": float(diameter),
                    "hole_diameter": float(hole),
                    "ring": round(ring, 3),
                    "negative": ring <= 0,
                })

        findings.sort(key=lambda f: f["ring"])
        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "vias_counted": counted,
            "unreadable": unreadable,
            "minimum_ring": minimum_ring,
            "violation_count": len(findings),
            "violations": findings,
            "note": ("a NEGATIVE ring means the drill is wider than the "
                     "pad, which renders as a via and cannot conduct"),
        }

    @mcp.tool()
    async def easyeda_audit_dangling_track_ends(
        tolerance: float = 1.0,
    ) -> dict[str, Any]:
        """Track ends that touch nothing else on their own net.

        Port of the Altium bad-connections audit. A segment whose end
        sits near, but not on, the next thing looks connected on
        screen at any sane zoom, and the photoplot does not bridge it:
        a real open, found at test rather than at review.

        NETS WITH A POUR ARE SKIPPED, and that decision is what makes
        the result readable. A ground track legitimately ends inside
        poured copper, and whether an endpoint lands in a polygon is
        not something this can test from the measured outline, so
        every ground track would be reported. Skipping those nets
        loses coverage on exactly the nets least likely to be broken,
        and keeps the report to signal nets where a dangling end is
        unambiguous. The count of skipped nets is returned rather than
        hidden.

        Fields are the measured ones: lines carry ``startX``,
        ``startY``, ``endX``, ``endY``, ``net`` and ``layer``; pads and
        vias carry ``x``, ``y`` and ``net``; pours carry ``net``. Arcs
        are excluded because no measured arc carried a net at all.

        Args:
            tolerance: how close counts as touching, in mils. The
                router places tracks on a sub-mil grid, so an exact
                match is the wrong test.

        Returns:
            {ok, checked, violation_count, violations: [{net, layer,
             x, y, primitive_id}], nets_skipped_for_pours}.
        """
        if tolerance < 0:
            return {"ok": False,
                    "reason": "tolerance must not be negative"}

        sections = {}
        verified = None
        for key, command in (("lines", "pcb.lines"), ("pads", "pcb.pads"),
                             ("vias", "pcb.vias"), ("pours", "pcb.pours")):
            reply = _call(command, timeout=120.0)
            if not reply.get("ok"):
                return reply
            verified = reply.get("verified_live")
            sections[key] = [x for x in (reply.get(key) or [])
                             if isinstance(x, dict)]

        poured_nets = {str(p.get("net") or "").strip()
                       for p in sections["pours"]}
        poured_nets.discard("")

        # Every point a track end may legitimately land on, per net.
        anchors: dict[str, list] = {}

        def _add(net, x, y):
            net = str(net or "").strip()
            if not net or not isinstance(x, (int, float)) or not isinstance(
                    y, (int, float)):
                return
            anchors.setdefault(net, []).append((float(x), float(y)))

        for pad in sections["pads"]:
            _add(pad.get("net"), pad.get("x"), pad.get("y"))
        for via in sections["vias"]:
            _add(via.get("net"), via.get("x"), via.get("y"))
        for line in sections["lines"]:
            _add(line.get("net"), line.get("startX"), line.get("startY"))
            _add(line.get("net"), line.get("endX"), line.get("endY"))

        limit = float(tolerance) ** 2
        findings = []
        checked = 0
        for line in sections["lines"]:
            net = str(line.get("net") or "").strip()
            if not net or net in poured_nets:
                continue
            here = anchors.get(net) or []
            for x_key, y_key in (("startX", "startY"), ("endX", "endY")):
                x, y = line.get(x_key), line.get(y_key)
                if not isinstance(x, (int, float)) or not isinstance(
                        y, (int, float)):
                    continue
                checked += 1
                # The endpoint matches ITSELF in the anchor list, so a
                # connected end has at least two hits and a dangling
                # one exactly the one.
                touching = sum(
                    1 for (ax, ay) in here
                    if (ax - x) ** 2 + (ay - y) ** 2 <= limit)
                if touching <= 1:
                    findings.append({
                        "net": net,
                        "layer": line.get("layer"),
                        "x": x, "y": y,
                        "primitive_id": line.get("primitiveId"),
                    })

        return {
            "ok": True,
            "verified_live": verified,
            "checked": checked,
            "tolerance": tolerance,
            "violation_count": len(findings),
            "violations": findings,
            "nets_skipped_for_pours": len(poured_nets),
            "note": ("nets with a pour are skipped: a track ending in "
                     "poured copper is correct, and testing that needs "
                     "polygon containment this does not attempt"),
        }

    @mcp.tool()
    async def easyeda_audit_parts_excluded_from_bom() -> dict[str, Any]:
        """Placed components that will not appear on the BOM.

        The EasyEDA analogue of the Altium not-fitted audit, which has
        no direct counterpart here because EasyEDA has no assembly
        variants. The DEFECT is the same and it is expensive: a part
        sits on the board, gets a footprint and gets routed, and
        nobody orders it, which is discovered at assembly.

        REPORTS, does not judge. Mounting holes, fiducials, test
        points and shield cans are excluded from the BOM on purpose
        and are the majority of what this finds on a healthy board.
        What it exists to surface is the ONE resistor somebody
        unticked while copying a block. So the list is grouped by
        whether the part carries a part number: an excluded component
        WITH a Partnumber is the interesting case, since a part
        deliberately off the BOM rarely has one.

        Fields are the measured ones: ``addIntoBom`` on each
        component, and ``Partnumber`` among its parameters.

        Returns:
            {ok, checked, violation_count, violations, excluded_total,
             source}. ``violations`` holds only the excluded parts that
            carry a part number; ``excluded_total`` counts them all.
        """
        reply = _schematic_parts()
        if not reply.get("ok"):
            return reply

        excluded_total = 0
        findings = []
        checked = 0
        for item in reply.get("parts") or []:
            props = item["params"]
            checked += 1
            flag = props.get("Add into BOM", props.get("addIntoBom"))
            if isinstance(flag, str):
                included = flag.strip().lower() not in ("no", "false", "0")
            elif flag is None:
                continue          # not reported: nothing to judge
            else:
                included = bool(flag)
            if included:
                continue
            excluded_total += 1
            mpn = str(props.get("Partnumber") or "").strip()
            if mpn and not mpn.startswith("="):
                findings.append({
                    "designator": item["designator"],
                    "part_number": mpn,
                    "name": props.get("Name"),
                })

        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "source": reply.get("source"),
            "checked": checked,
            "excluded_total": excluded_total,
            "violation_count": len(findings),
            "violations": findings,
            "note": ("a part with a part number that is off the BOM is "
                     "the case worth a look; mounting holes and "
                     "fiducials are excluded on purpose and carry none"),
        }

    @mcp.tool()
    async def easyeda_audit_degenerate_copper() -> dict[str, Any]:
        """Fills and pours whose outline cannot enclose an area.

        Port of the Altium invalid-regions audit. A region left behind
        by a cancelled pour, or mangled by an import, renders as
        nothing: the board LOOKS as though it has that ground plane
        and does not. Nothing on screen shows the difference, which is
        what makes it worth a check rather than a glance.

        Counting is deliberately conservative. The outline is a flat
        list mixing coordinates with segment markers, measured as
        ``[546.9685, 341.063, "L", 547.0866, ...]``, and only the
        numbers are counted: three points need six of them. If a
        marker like an arc carries extra numeric parameters the count
        comes out HIGH, so a real degenerate could be missed, but a
        healthy region is never flagged. Missing the odd defect is
        recoverable; a review tool that cries wolf is not.

        REPORTS, does not delete. Removing a region that turns out to
        be legitimately tiny is worse than listing it.

        Returns:
            {ok, checked, violation_count, violations: [{kind, layer,
             net, name, points_seen, primitive_id}]}.
        """
        findings = []
        checked = 0
        verified = None
        for kind, command in (("fill", "pcb.fills"), ("pour", "pcb.pours")):
            reply = _call(command, timeout=60.0)
            if not reply.get("ok"):
                return reply
            verified = reply.get("verified_live")
            for item in reply.get(kind + "s") or []:
                if not isinstance(item, dict):
                    continue
                checked += 1
                shape = item.get("complexPolygon")
                outline = None
                if isinstance(shape, dict):
                    outline = shape.get("polygon")
                elif isinstance(shape, list):
                    outline = shape
                numbers = [v for v in (outline or [])
                           if isinstance(v, (int, float))]
                # Three points, so six coordinates, is the minimum that
                # can enclose anything at all.
                if len(numbers) >= 6:
                    continue
                findings.append({
                    "kind": kind,
                    "layer": item.get("layer"),
                    "net": item.get("net"),
                    "name": item.get("pourName"),
                    "points_seen": len(numbers) // 2,
                    "primitive_id": item.get("primitiveId"),
                })

        return {
            "ok": True,
            "verified_live": verified,
            "checked": checked,
            "violation_count": len(findings),
            "violations": findings,
            "note": ("a region with no area renders as nothing, so the "
                     "board looks as though it has copper it does not"),
        }

    @mcp.tool()
    async def easyeda_audit_mpn_inconsistencies() -> dict[str, Any]:
        """The same library part placed with different part numbers.

        Two presumably-identical components pointing at different MPNs
        is almost always a defect: a typo, or an override made while
        cloning a sub-circuit. Occasionally it means the design wants
        two sources and lost its alternates table. Either way a
        reviewer should see it before the BOM is bought.

        Port of the Altium audit, which groups by library reference.
        Here that is ``Design Item ID`` plus ``Library Name`` from the
        component's parameters, falling back to the library item's
        uuid, which every component carries. The part number is
        ``Partnumber``. All three names are measured from a live
        schematic rather than assumed, which matters because the
        neighbouring ``PP Comment`` holds the formula ``=Comment`` and
        would have made a plausible-looking grouping key that groups
        nothing.

        Blank part numbers are counted separately, not compared: an
        unfilled field is a different problem from a contradictory one,
        and mixing them buries the contradiction.

        Returns:
            {ok, checked, violation_count, violations: [{library_item,
             part_numbers, designators}], blank_part_numbers}.
        """
        reply = _schematic_parts()
        if not reply.get("ok"):
            return reply

        groups: dict[str, dict[str, list]] = {}
        blank = 0
        checked = 0
        for item in reply.get("parts") or []:
            props = item["params"]
            designator = item["designator"]
            mpn = str(props.get("Partnumber") or "").strip()
            # A formula that never resolved is not a part number; the
            # same '=' that made ComponentLink1URL useless.
            if mpn.startswith("="):
                mpn = ""
            key = " / ".join(x for x in (
                str(props.get("Design Item ID") or "").strip(),
                str(props.get("Library Name") or "").strip(),
            ) if x) or item.get("library_uuid") or ""
            if not key:
                continue
            checked += 1
            if not mpn:
                blank += 1
                continue
            bucket = groups.setdefault(key, {"mpns": [], "designators": []})
            if mpn not in bucket["mpns"]:
                bucket["mpns"].append(mpn)
            bucket["designators"].append(designator or "?")

        violations = [
            {"library_item": key,
             "part_numbers": sorted(data["mpns"]),
             "designators": sorted(data["designators"])}
            for key, data in sorted(groups.items())
            if len(data["mpns"]) > 1
        ]
        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "checked": checked,
            "violation_count": len(violations),
            "violations": violations,
            "blank_part_numbers": blank,
            "note": ("a blank Partnumber is counted, not compared: an "
                     "unfilled field is a different problem from a "
                     "contradictory one"),
        }

    @mcp.tool()
    async def easyeda_audit_missing_datasheets() -> dict[str, Any]:
        """ICs with no fetchable datasheet URL among their parameters.

        Port of the Altium audit, checking the same slots it does:
        ``HelpURL``, ``Datasheet``, ``DatasheetURL`` and any
        ``ComponentLink*URL``. A slot counts only when its value
        contains ``://``, which is what separates a real link from a
        placeholder.

        That test earns its keep here. A live schematic reported
        ``ComponentLink1URL`` as the literal string ``=HelpURL``, an
        unresolved display formula rather than an address, and a
        presence check would have called that part documented.

        Reads the SCHEMATIC, because that is where parameters live: a
        schematic tab has to be active or the editor refuses, and the
        refusal is passed through rather than reported as a clean
        board. Component classification uses the same helper as the
        Altium side, so an IC is an IC on both.

        A MEASUREMENT GAP worth knowing before trusting a run. The
        only ``sch.components`` sample captured so far is a mounting
        hole, whose parameters are unresolved templates. The same
        board's ``sch.netlist`` reports a real IC with a fully
        resolved ``ComponentLink1URL`` pointing at a manufacturer PDF.
        So it is not yet established whether ``sch.components``
        resolves parameters for ordinary parts or returns templates
        for all of them, and if it returns templates this audit will
        report documented ICs as missing. Confirming that needs one
        sample of a real IC from ``sch.components``; until then, cross
        a surprising result against ``easyeda_get_schematic_netlist``.

        Returns:
            {ok, checked, violation_count, violations: [{designator,
             name, links_seen}]}. An IC with no parameters at all is a
            violation; a part that is not an IC is not checked.
        """
        from .audit import _component_class_from_designator

        reply = _schematic_parts()
        if not reply.get("ok"):
            return reply

        slots = ("HELPURL", "DATASHEET", "DATASHEETURL")
        findings = []
        checked = 0
        for item in reply.get("parts") or []:
            designator = item["designator"]
            if _component_class_from_designator(designator) != "ic":
                continue
            checked += 1
            props = item["params"]
            seen = []
            covered = False
            for key, value in props.items():
                upper = str(key).upper()
                if upper in slots or upper.startswith("COMPONENTLINK"):
                    text = str(value or "")
                    seen.append(key)
                    if "://" in text:
                        covered = True
            if not covered:
                findings.append({
                    "designator": designator,
                    "name": props.get("Name") or props.get("name"),
                    "links_seen": sorted(seen),
                })

        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "source": reply.get("source"),
            "checked": checked,
            "violation_count": len(findings),
            "violations": findings,
            "note": ("a slot counts only when it holds '://': a live "
                     "board carried ComponentLink1URL as the formula "
                     "'=HelpURL', which documents nothing"),
        }

    @mcp.tool()
    async def easyeda_audit_missing_decoupling() -> dict[str, Any]:
        """ICs whose power pins have no capacitor on the same net.

        The single most-skipped pre-release check: a missing local
        bypass passes ERC, passes DRC, and bites at first power-on as
        brown-outs and random resets.

        Runs the SAME engine as the Altium audit
        (find_missing_decoupling_from_bom), fed from measured EasyEDA
        data rather than a reimplementation, so the two backends put an
        IC in the same bucket. Components carry their pads nested, and
        each pad carries its net, so one read is enough.

        ONE LIMITATION, and it is the honest direction to fail in: a
        PCB pad has a NUMBER but no pin NAME, so a power pin is
        recognised by its NET name only (VCC / VDD / GND and the rest),
        never by a pin called "VDD" sitting on an oddly-named net. The
        shared engine SKIPS an IC with no recognised power pins rather
        than guessing, so such a part is left unreported instead of
        being reported wrongly. Reading pin names needs the schematic,
        which is a different document context.

        Returns:
            {ok, checked, violations, items: [{designator, status,
             uncovered_pins, covered_pin_count}]} where status is
            "missing" (no power pin has a cap) or "partial". Fully
            covered ICs are not listed: there is nothing to fix.
        """
        from .audit import find_missing_decoupling_from_bom

        reply = _call("pcb.components", timeout=120.0)
        if not reply.get("ok"):
            return reply

        components = []
        without_pads = 0
        for item in reply.get("components") or []:
            if not isinstance(item, dict):
                continue
            pads = item.get("pads")
            if not isinstance(pads, list) or not pads:
                without_pads += 1
                continue
            components.append({
                "designator": item.get("designator") or "",
                # The engine reads pin/name/net. A pad has no name, so
                # the key is written empty. That is DOCUMENTATION, not
                # a requirement: the engine reads it as
                # `.get("name") or ""`, so omitting it behaves
                # identically, and a mutation dropping it survives.
                # It stays because the call site is where a reader asks
                # what a pad does and does not carry.
                "pins": [
                    {"pin": str(p.get("padNumber") or ""),
                     "name": "",
                     "net": str(p.get("net") or "")}
                    for p in pads if isinstance(p, dict)
                ],
            })

        result = find_missing_decoupling_from_bom(
            {"components": components})
        result["ok"] = True
        # The shared engine answers with "violations" as a COUNT. Give
        # it the agreed name too rather than changing the engine, which
        # the Altium audit returns verbatim (task #56).
        result["violation_count"] = result.get("violations", 0)
        result["verified_live"] = reply.get("verified_live")
        result["components_without_pads"] = without_pads
        result["note"] = ("power pins are recognised by NET name only "
                          "here, since a PCB pad carries no pin name; "
                          "an IC with no recognised power pin is skipped "
                          "rather than guessed at")
        return result

    @mcp.tool()
    async def easyeda_audit_signal_vias_without_return(
        radius_mils: float = 50.0,
    ) -> dict[str, Any]:
        """Signal vias with no nearby ground / power via for return
        current.

        Port of the Altium audit, same heuristic on both backends: for
        every via NOT on a power/ground net, look for at least one
        power/ground via within ``radius_mils``. When a high-speed
        signal changes layers the return current has to cross the
        dielectric somewhere; a nearby reference via carries it
        cleanly, and without one it detours through the plane.

        Net classification mirrors the Altium side's
        IsPowerOrGroundNetName exactly (GND vocabulary, any name
        containing GND, and the V/+/- rail prefixes), so the two
        backends flag the same boards the same way. This is a
        SIMPLIFIED proximity heuristic, not a stackup-aware analyser;
        for first-pass review it catches the obvious cases.

        Via fields are the measured ones from a live session
        measured: every via carries ``net``, ``x``, ``y`` and
        ``primitiveId``. The PCB canvas is mils, so the radius needs no
        conversion. A via with unreadable coordinates is counted as
        unreadable rather than passed.

        Args:
            radius_mils: how close a return via must be. Default 50
                mils; typical good practice is within 1-2 mm.

        Returns:
            {ok, checked, violations, unreadable, radius_mils,
             items: [{primitive_id, net, x, y}]}.
        """
        if radius_mils <= 0:
            return {"ok": False,
                    "reason": "radius_mils must be a positive distance"}

        reply = _call("pcb.vias", timeout=60.0)
        if not reply.get("ok"):
            return reply

        signal: list[dict] = []
        reference: list[tuple[float, float]] = []
        unreadable = 0
        for via in reply.get("vias") or []:
            if not isinstance(via, dict):
                continue
            x, y = via.get("x"), via.get("y")
            if not isinstance(x, (int, float)) or not isinstance(
                    y, (int, float)):
                unreadable += 1
                continue
            if _is_power_or_ground_net(str(via.get("net") or "")):
                reference.append((float(x), float(y)))
            else:
                signal.append(via)

        radius_sq = float(radius_mils) ** 2
        items = []
        for via in signal:
            vx, vy = float(via["x"]), float(via["y"])
            if not any((vx - rx) ** 2 + (vy - ry) ** 2 <= radius_sq
                       for rx, ry in reference):
                items.append({
                    "primitive_id": via.get("primitiveId"),
                    "net": via.get("net"),
                    "x": via.get("x"), "y": via.get("y"),
                })

        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "checked": len(signal),
            # violation_count is the name every audit answers to; the
            # Altium original spells this one "violations" as a COUNT,
            # so both are kept and they say the same thing (task #56).
            "violation_count": len(items),
            "violations": len(items),
            "unreadable": unreadable,
            "radius_mils": radius_mils,
            "items": items,
            "note": ("proximity heuristic, not stackup-aware: it flags "
                     "a signal via with no reference via in range, not "
                     "every return-path problem"),
        }

    @mcp.tool()
    async def easyeda_audit_unconnected_component_pads(
        minimum_pads: int = 3,
    ) -> dict[str, Any]:
        """Components with pads on no net, counted per component.

        The shared unconnected-pins tool names loose pins board-wide;
        this groups them by OWNER, which is what a reviewer acts on: an
        IC with four floating pads is one finding, not four.

        ONE read. Components carry their pads nested (measured live
        measured: every component reported a ``pads`` array whose
        entries carry ``net`` and ``padNumber``), so there is no second
        query and no positional matching between two lists.

        REPORTS, does not judge: a mounting pad, a heatsink tab and a
        deliberately floating shield all look identical here. Two-pad
        parts are excluded by default because a decoupling cap mid-
        placement is noise, not news.

        Args:
            minimum_pads: only components with at least this many pads
                are examined. The default skips passives.
        """
        if minimum_pads < 1:
            return {"ok": False, "reason": "minimum_pads must be positive"}

        reply = _call("pcb.components", timeout=60.0)
        if not reply.get("ok"):
            return reply

        findings = []
        examined = 0
        without_pads = 0
        for item in reply.get("components") or []:
            if not isinstance(item, dict):
                continue
            pads = item.get("pads")
            if not isinstance(pads, list) or not pads:
                # A component whose pads are unreadable has not been
                # checked, and must not be filed as fully connected.
                without_pads += 1
                continue
            if len(pads) < minimum_pads:
                continue
            examined += 1
            floating = []
            for pad in pads:
                if not isinstance(pad, dict):
                    continue
                if not str(pad.get("net") or "").strip():
                    floating.append(str(pad.get("padNumber") or "?"))
            if floating:
                designator = ""
                for key in ("designator", "name"):
                    found = item.get(key)
                    if isinstance(found, str) and found.strip():
                        designator = found.strip()
                        break
                findings.append({
                    "designator": designator or "(unnamed)",
                    "primitive_id": item.get("primitiveId"),
                    "pad_count": len(pads),
                    "floating_count": len(floating),
                    "floating_pads": floating,
                })

        findings.sort(key=lambda f: -f["floating_count"])
        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "examined": examined,
            "components_without_readable_pads": without_pads,
            "minimum_pads": minimum_pads,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("a floating pad is a place to look, not a verdict: "
                     "mounting pads and shield tabs float by design"),
        }

    @mcp.tool()
    async def easyeda_audit_acute_angles(
        threshold_degrees: float = 90.0,
    ) -> dict[str, Any]:
        """Track joints that meet at an acute angle.

        An acute joint leaves a sliver of gap the etchant cannot clear
        reliably (an acid trap): some boards of a batch come back with a
        short or a spur there and others do not.

        Segments are grouped by NET and LAYER and compared only where
        they SHARE an endpoint, because two tracks of different nets
        meeting at any angle is a different problem, and a crossing is
        not a joint. The angle measured is between the two directions
        LEAVING the shared point, so a straight continuation reads as
        180 degrees and a hairpin as near zero.

        Field names are measured: every line carries
        ``startX``, ``startY``, ``endX``, ``endY``, ``net`` and
        ``layer``. Segments missing any are counted unreadable rather
        than skipped silently.

        Args:
            threshold_degrees: report joints tighter than this. The
                default of 90 reports everything acute; some fabs only
                care below 60.
        """
        import math

        if not 0 < threshold_degrees <= 180:
            return {"ok": False, "reason": (
                "threshold_degrees must be between 0 and 180")}

        reply = _call("pcb.lines", timeout=120.0)
        if not reply.get("ok"):
            return reply

        def _key(x: float, y: float) -> tuple:
            # Endpoints are floats; a hundredth of a mil is far tighter
            # than any real join and far looser than float noise.
            return (round(x * 100), round(y * 100))

        joints: dict[tuple, list] = {}
        unreadable = 0
        counted = 0
        for line in reply.get("lines") or []:
            if not isinstance(line, dict):
                continue
            counted += 1
            try:
                x1, y1 = float(line["startX"]), float(line["startY"])
                x2, y2 = float(line["endX"]), float(line["endY"])
            except (KeyError, TypeError, ValueError):
                unreadable += 1
                continue
            net = str(line.get("net") or "")
            layer = str(line.get("layer") or "")
            for own, other in (((x1, y1), (x2, y2)), ((x2, y2), (x1, y1))):
                joints.setdefault(
                    (net, layer, _key(*own)), []).append((own, other))

        findings = []
        for (net, layer, _), ends in joints.items():
            if len(ends) < 2:
                continue
            for a in range(len(ends)):
                for b in range(a + 1, len(ends)):
                    (px, py), (ax, ay) = ends[a]
                    _, (bx, by) = ends[b]
                    v1 = (ax - px, ay - py)
                    v2 = (bx - px, by - py)
                    n1 = math.hypot(*v1)
                    n2 = math.hypot(*v2)
                    if n1 == 0 or n2 == 0:
                        continue
                    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
                    angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                    if angle < threshold_degrees:
                        findings.append({
                            "net": net, "layer": layer,
                            "x": px, "y": py,
                            "angle_degrees": round(angle, 2),
                        })

        findings.sort(key=lambda f: f["angle_degrees"])
        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "segments_counted": counted,
            "unreadable": unreadable,
            "threshold_degrees": threshold_degrees,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("arcs are not examined: the measured arc shape "
                     "carries no net field, so an arc-to-track joint "
                     "cannot be attributed to a net from this data"),
        }

    @mcp.tool()
    async def easyeda_audit_via_antennas() -> dict[str, Any]:
        """Vias whose net has copper on fewer than two layers.

        A via joins layers. When everything its net owns sits on ONE
        layer, the via joins that layer to nothing: a stub into the
        board that radiates and reflects instead of connecting. It
        usually survives from a rerouted track whose via nobody deleted.

        Copper is counted from TRACKS, PADS, POURS and FILLS together,
        because a via legitimately lands in a plane pour that carries no
        tracks at all. A pad whose layer reads as MULTI reaches every
        layer by construction, so its net is never an antenna candidate.

        REPORTS, does not judge: a via placed for a plane that has not
        been poured yet looks exactly like a mistake until the pour
        exists.

        Field names are measured across all five reads.
        """
        sections = {}
        verified = None
        for name, command in (("vias", "pcb.vias"), ("lines", "pcb.lines"),
                              ("pads", "pcb.pads"), ("pours", "pcb.pours"),
                              ("fills", "pcb.fills")):
            reply = _call(command, timeout=120.0)
            if not reply.get("ok"):
                return reply
            verified = reply.get("verified_live")
            sections[name] = [item for item in (reply.get(name) or [])
                              if isinstance(item, dict)]

        layers_by_net: dict[str, set] = {}
        multi_nets: set = set()

        def _note(net_value, layer_value) -> None:
            net = str(net_value or "").strip()
            if not net:
                return
            layer = str(layer_value or "").strip()
            if not layer:
                return
            if "MULTI" in layer.upper():
                multi_nets.add(net)
                return
            layers_by_net.setdefault(net, set()).add(layer)

        for line in sections["lines"]:
            _note(line.get("net"), line.get("layer"))
        for pad in sections["pads"]:
            _note(pad.get("net"), pad.get("layer"))
        for pour in sections["pours"]:
            _note(pour.get("net"), pour.get("layer"))
        for fill in sections["fills"]:
            _note(fill.get("net"), fill.get("layer"))

        findings = []
        unnetted = 0
        for via in sections["vias"]:
            net = str(via.get("net") or "").strip()
            if not net:
                # A via with no net is its own different finding, and
                # folding it in here would mislabel it.
                unnetted += 1
                continue
            if net in multi_nets:
                continue
            layers = layers_by_net.get(net, set())
            if len(layers) < 2:
                findings.append({
                    "primitive_id": via.get("primitiveId"),
                    "net": net,
                    "x": via.get("x"), "y": via.get("y"),
                    "copper_layers": sorted(layers),
                })

        return {
            "ok": True,
            "verified_live": verified,
            "vias_counted": len(sections["vias"]),
            "unnetted_vias": unnetted,
            # violation_count is the agreed name across every audit, so
            # one summariser can read them all; the original key stays
            # for callers that already use it (task #56).
            "violation_count": len(findings),
            "antenna_count": len(findings),
            "antennas": findings,
            "note": ("a via for a plane not yet poured looks identical "
                     "to a leftover; reported, not judged"),
        }

    def _netlist_entries():
        """The netlist as (designator, props, pins) rows, or a failure.

        Returns (rows, error). Every schematic audit needs the same
        three things, and the netlist is the only schematic read that
        supplies pins at all.
        """
        reply = _call("sch.netlist", timeout=60.0)
        if not reply.get("ok"):
            return None, reply
        raw = reply.get("netlist")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError) as exc:
                return None, {"ok": False,
                              "reason": f"the netlist did not parse: {exc}"}
        if not isinstance(raw, dict) or not raw:
            return None, {"ok": False, "reason": (
                "the netlist is empty, so nothing was checked. That is "
                "not the same as a schematic with no problems")}
        rows = []
        for unique_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            props = entry.get("props") if isinstance(entry.get("props"),
                                                     dict) else {}
            pins = entry.get("pins") if isinstance(entry.get("pins"),
                                                   dict) else {}
            rows.append((str(props.get("Designator") or "").strip(),
                         props, pins, str(unique_id)))
        return rows, reply

    def _looks_small(value: str) -> bool:
        """Whether a capacitor value is high-frequency decoupling.

        Anything at or below 1uF counts. The distinction that matters
        is bulk against local: a 22uF electrolytic and a 100nF ceramic
        on the same rail do different jobs, and a rail carrying only
        the former is missing the one that handles switching edges.
        """
        text = str(value or "").strip().lower().replace(" ", "")
        if not text:
            return False
        import re as _re

        match = _re.match(r"^([0-9]*\.?[0-9]+)\s*([pnumµ]?)f?$", text)
        if not match:
            return False
        try:
            number = float(match.group(1))
        except ValueError:
            return False
        scale = {"p": 1e-12, "n": 1e-9, "u": 1e-6,
                 "µ": 1e-6, "m": 1e-3, "": 1.0}
        return number * scale.get(match.group(2), 1.0) <= 1.0e-6

    #: Net names that carry a supply rather than a signal.
    #:
    #: Matched on the name because the netlist gives pin numbers and no
    #: pin function, so there is no way to ask a part which of its pins
    #: is a supply. A rail that follows none of these conventions is
    #: reported as unrecognised rather than silently treated as signal.
    _GROUND_PATTERN = ("GND", "VSS", "AGND", "DGND", "PGND", "EARTH")
    _SUPPLY_PREFIX = ("+", "VCC", "VDD", "VBAT", "VSYS", "VBUS", "VIN",
                      "VDDA", "AVDD", "VDDIO", "3V3", "5V", "1V8")

    def _is_ground(net: str) -> bool:
        upper = net.upper().strip()
        return any(upper == g or upper.startswith(g + "_")
                   or upper.endswith("_" + g) for g in _GROUND_PATTERN)

    def _is_supply(net: str) -> bool:
        upper = net.upper().strip().lstrip("\\")
        if _is_ground(upper):
            return False
        return any(upper.startswith(s) for s in _SUPPLY_PREFIX)

    @mcp.tool()
    async def easyeda_audit_decoupling_schematic() -> dict[str, Any]:
        """Integrated circuits without a decoupling capacitor.

        The check a schematic review exists for. An IC whose supply pin
        has no local capacitor draws its switching current through the
        whole rail, which shows up as a board that works on the bench
        and fails intermittently, or fails emissions.

        The sibling audit ``missing_decoupling`` asks the same question
        of the BOARD, through the engine the Altium backend uses, so
        the two agree across backends. This one reads the SCHEMATIC,
        which differs in two ways that matter: it can be run before a
        board exists, and it counts capacitors against supply PINS
        rather than parts, so an IC with four supply pins and one
        capacitor is reported rather than passed.

        WHAT IS CHECKED. For each IC, every supply net it touches, and
        whether any capacitor sits on that same net with its other end
        on ground. Values are reported so the split between bulk and
        high-frequency decoupling is visible: a rail with a single 22uF
        and nothing small is a different finding from a rail with
        nothing at all.

        WHAT IS NOT CHECKED. Proximity. A schematic cannot say whether
        the capacitor is near the pin, and on a board it is the loop
        area that matters. This finds the missing capacitor, not the
        badly placed one.

        Supply and ground nets are recognised by name, since the
        netlist reports pin numbers without pin functions. Nets that
        match neither convention are listed as unrecognised so a
        non-standard rail name is visible rather than silently ignored.
        """
        from .audit import _component_class_from_designator

        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        # net -> [(designator, prefix)]
        members: dict[str, list] = {}
        for designator, props, pins, unique_id in rows:
            kind = _component_class_from_designator(designator)
            for net in pins.values():
                name = str(net or "").strip()
                if name:
                    members.setdefault(name, []).append((designator, kind))

        values = {d: str(p.get("Value") or "").strip()
                  for d, p, _, _ in rows}

        def _caps_on(net: str) -> list:
            """Capacitors on this net that also reach ground."""
            found = []
            for designator, kind in members.get(net, []):
                if not designator.upper().startswith("C"):
                    continue
                own = next((pins for d, _, pins, _ in rows
                            if d == designator), {})
                if any(_is_ground(str(v or "")) for v in own.values()):
                    found.append(designator)
            return found

        # Per rail: which IC supply pins hang off it, and what
        # decoupling it carries. Counted per PIN rather than per part,
        # because a part with four supply pins wants four capacitors
        # and a rail-level presence check would call it satisfied by
        # the first one.
        rails: dict[str, dict] = {}
        checked = 0
        unrecognised: set = set()
        for designator, props, pins, unique_id in rows:
            if _component_class_from_designator(designator) != "ic":
                continue
            checked += 1
            supplies = [str(v or "").strip() for v in pins.values()
                        if _is_supply(str(v or ""))]
            if not supplies:
                unrecognised.add(designator)
            for rail in supplies:
                entry = rails.setdefault(
                    rail, {"rail": rail, "supply_pins": 0, "ics": set()})
                entry["supply_pins"] += 1
                entry["ics"].add(designator)

        findings = []
        rail_report = []
        for rail in sorted(rails):
            entry = rails[rail]
            caps = _caps_on(rail)
            sizes = [values.get(c, "") for c in caps]
            local = [c for c, s in zip(caps, sizes) if _looks_small(s)]
            bulk = [c for c in caps if c not in local]
            row = {
                "rail": rail,
                "ic_supply_pins": entry["supply_pins"],
                "ics": sorted(entry["ics"]),
                "decoupling_capacitors": local,
                "bulk_capacitors": bulk,
                "values": sizes,
            }
            rail_report.append(row)

            if not caps:
                findings.append({
                    "rail": rail,
                    "severity": "error",
                    "ics": sorted(entry["ics"]),
                    "problem": (
                        f"{entry['supply_pins']} IC supply pins on this "
                        f"rail and no capacitor anywhere on it"),
                })
            elif not local:
                findings.append({
                    "rail": rail,
                    "severity": "warning",
                    "ics": sorted(entry["ics"]),
                    "bulk_capacitors": bulk,
                    "values": sizes,
                    "problem": (
                        "only bulk capacitance on this rail; nothing at "
                        "or below 1uF to carry the switching edges"),
                    "qualifier": (
                        "a convention rather than a datasheet breach. "
                        "Some parts specify input capacitance as a total "
                        "effective value and are satisfied by the bulk "
                        "capacitor alone, so confirm against the part "
                        "before acting on this"),
                })
            elif len(local) < entry["supply_pins"]:
                findings.append({
                    "rail": rail,
                    "severity": "warning",
                    "ics": sorted(entry["ics"]),
                    "decoupling_capacitors": local,
                    "problem": (
                        f"{len(local)} decoupling capacitors for "
                        f"{entry['supply_pins']} IC supply pins; the "
                        f"convention is one per pin"),
                })

        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "ics_checked": checked,
            "examined": checked,
            "rails": rail_report,
            "supply_rails": sorted(rails),
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("proximity is not checked: a schematic cannot say "
                     "whether the capacitor is near the pin, and on a "
                     "board it is the loop area that decides"),
        }
        if unrecognised:
            result["ics_with_no_recognised_supply"] = sorted(unrecognised)
            result["scope_warning"] = (
                f"{len(unrecognised)} ICs touch no net whose name looks "
                f"like a supply, so their decoupling was NOT checked. "
                f"A rail named outside the usual conventions reads this "
                f"way")
        result.update(_schematic_scope())
        return result

    def _base_net(net: str) -> str:
        """A net name with its hierarchy prefix removed.

        A flattened netlist reports a block's local net as
        ``$1I81\\I2C_SCL``. The prefix identifies the sheet instance and
        says nothing about what the net carries, so every check that
        reasons about a name has to look past it.
        """
        text = str(net or "").strip()
        return text.rsplit("\\", 1)[-1] if "\\" in text else text

    #: Signal names that require a pull-up to idle high.
    #:
    #: Open-drain buses hold the line low and rely on a resistor to
    #: return it. With no pull-up the bus never releases and the
    #: transaction fails outright, which makes this one of the few
    #: schematic faults that is unambiguous rather than a judgement.
    _OPEN_DRAIN_SIGNALS = ("SCL", "SDA", "SMBCLK", "SMBDAT")

    #: Maximum SDA and SCL rise time per bus mode, in nanoseconds.
    #:
    #: UM10204 Rev 6 Table 10. The bus capacitance ceiling in the same
    #: table is 400pF for Standard and Fast modes and 550pF for Fast
    #: mode Plus.
    _I2C_RISE_NS = {100: 1000.0, 400: 300.0, 1000: 120.0}
    _I2C_CB_MAX_PF = {100: 400.0, 400: 400.0, 1000: 550.0}

    #: The 30 to 70 percent RC rise, from UM10204 Rev 6 section 7.1.
    #:
    #: With thresholds at 0.3 and 0.7 of the rail the charge time works
    #: out at 0.8473 RC, which is what turns a rise time limit into a
    #: resistance limit: Rp(max) = tr / (0.8473 Cb).
    _I2C_RC_FACTOR = 0.8473

    def _resistance_ohms(value: str) -> "float | None":
        """Ohms from a printed resistor value, including RKM notation."""
        import re as _re

        text = str(value or "").strip().upper().replace(" ", "")
        text = text.removesuffix("OHM").removesuffix("OHMS")
        text = text.rstrip("ΩR") if text.endswith("Ω") else text
        if not text:
            return None
        scale = {"R": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}
        # RKM puts the multiplier where the decimal point goes, so 4K7
        # is 4700 and 2R2 is 2.2. It exists precisely because a printed
        # decimal point gets lost, and it turns up on real schematics.
        match = _re.match(r"^(\d+)([RKMG])(\d+)$", text)
        if match:
            return float(f"{match.group(1)}.{match.group(3)}") * scale[
                match.group(2)]
        match = _re.match(r"^(\d*\.?\d+)([RKMG]?)$", text)
        if match:
            try:
                return float(match.group(1)) * scale.get(match.group(2), 1.0)
            except ValueError:
                return None
        return None

    @mcp.tool()
    async def easyeda_audit_open_drain_pullups(
            bus_speed_khz: int = 100,
            bus_capacitance_pf: "float | None" = None) -> dict[str, Any]:
        """Open-drain buses with a missing or badly sized pull-up.

        An I2C or SMBus line is driven low and released, never driven
        high. Without a resistor to the rail it stays low, the bus
        never idles, and nothing on it answers. That part is not a
        margin question: the bus does not work.

        The SIZE is arithmetic rather than opinion. UM10204 section 7.1
        derives the rise between the 0.3 and 0.7 thresholds as 0.8473
        RC, so the rise time ceiling in Table 10 fixes a maximum
        resistance: Rp(max) = tr / (0.8473 Cb). Too large and the line
        cannot return high inside the bit period; too small and the
        driver cannot pull it down, since the same section sets
        Rp(min) from the 3mA sink current the specification requires.

        Bus capacitance is the one term a schematic cannot supply: it
        is wire, pins and connectors, and it belongs to the board. So
        rather than assume a figure and report a confident verdict,
        this reports the CROSSOVER capacitance at which each fitted
        resistor stops meeting the rise time, which is a number the
        layout can be checked against. Pass bus_capacitance_pf to get
        a straight verdict once that figure is known.

        The default speed is 100kHz because it is the most forgiving,
        so anything reported at the default is a finding at every
        speed. Set bus_speed_khz to the real bus rate to tighten it.
        """
        try:
            speed = int(bus_speed_khz)
        except (TypeError, ValueError):
            speed = -1
        if speed not in _I2C_RISE_NS:
            return {"ok": False, "reason": (
                f"bus_speed_khz must be one of "
                f"{sorted(_I2C_RISE_NS)}, got {bus_speed_khz!r}")}
        stated_cb = None
        if bus_capacitance_pf is not None:
            try:
                stated_cb = float(bus_capacitance_pf)
            except (TypeError, ValueError):
                return {"ok": False, "reason": (
                    f"bus_capacitance_pf must be a number, "
                    f"got {bus_capacitance_pf!r}")}
            if stated_cb <= 0:
                return {"ok": False,
                        "reason": "bus_capacitance_pf must be above zero"}

        rise_ns = _I2C_RISE_NS[speed]
        cb_ceiling = _I2C_CB_MAX_PF[speed]

        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        by_designator = {d: (p, pins) for d, p, pins, _ in rows}

        buses = []
        findings = []
        for designator, props, pins, unique_id in rows:
            for net in pins.values():
                name = str(net or "").strip()
                base = _base_net(name).upper()
                if not name:
                    continue
                if not any(base == s or base.endswith("_" + s)
                           or base.startswith(s + "_")
                           for s in _OPEN_DRAIN_SIGNALS):
                    continue
                if name not in [b["net"] for b in buses]:
                    buses.append({"net": name, "signal": base})

        for bus in buses:
            net = bus["net"]
            pullups = []
            for designator, (props, pins) in by_designator.items():
                if not designator.upper().startswith("R"):
                    continue
                if net not in [str(v or "").strip() for v in pins.values()]:
                    continue
                # The other end has to reach a supply. A resistor in
                # series with the bus touches it too, and calling that
                # a pull-up would report the bus as fine.
                if any(_is_supply(str(v or "")) for v in pins.values()):
                    pullups.append({
                        "designator": designator,
                        "value": str(props.get("Value") or "").strip(),
                        "rail": next(
                            (str(v).strip() for v in pins.values()
                             if _is_supply(str(v or ""))), ""),
                    })
            bus["pullups"] = pullups
            if not pullups:
                findings.append({
                    "net": net,
                    "signal": bus["signal"],
                    "severity": "error",
                    "problem": ("an open drain net with no resistor to a "
                                "supply; the line cannot return high"),
                })
                continue

            for pullup in pullups:
                ohms = _resistance_ohms(pullup["value"])
                if ohms is None or ohms <= 0:
                    pullup["unreadable_value"] = True
                    continue
                pullup["ohms"] = ohms
                # The capacitance at which this resistor exactly meets
                # the rise time. Above it the line is too slow. This is
                # the useful number when the real bus capacitance is
                # not yet known, because it is what the layout has to
                # come in under.
                crossover_pf = (rise_ns * 1e-9) / (
                    _I2C_RC_FACTOR * ohms) * 1e12
                pullup["crossover_capacitance_pf"] = round(crossover_pf, 1)
                pullup["rp_max_at_spec_ceiling_ohms"] = round(
                    (rise_ns * 1e-9)
                    / (_I2C_RC_FACTOR * cb_ceiling * 1e-12))

                if stated_cb is not None:
                    rp_max = (rise_ns * 1e-9) / (
                        _I2C_RC_FACTOR * stated_cb * 1e-12)
                    pullup["rp_max_ohms"] = round(rp_max)
                    if ohms > rp_max:
                        findings.append({
                            "net": net,
                            "designator": pullup["designator"],
                            "value": pullup["value"],
                            "severity": "error",
                            "bus_speed_khz": speed,
                            "bus_capacitance_pf": stated_cb,
                            "rp_max_ohms": round(rp_max),
                            "problem": (
                                f"{pullup['value']} exceeds the "
                                f"{round(rp_max)} ohm ceiling for a "
                                f"{rise_ns:.0f}ns rise into {stated_cb}pF, "
                                f"so the line cannot return high in time"),
                            "reference": ("UM10204 section 7.1 equation 1 "
                                          "and Table 10"),
                        })
                elif crossover_pf < cb_ceiling:
                    findings.append({
                        "net": net,
                        "designator": pullup["designator"],
                        "value": pullup["value"],
                        "severity": "warning",
                        "bus_speed_khz": speed,
                        "crossover_capacitance_pf": round(crossover_pf, 1),
                        "problem": (
                            f"{pullup['value']} meets the {rise_ns:.0f}ns "
                            f"rise time only while bus capacitance stays "
                            f"under {round(crossover_pf)}pF. The "
                            f"specification allows up to "
                            f"{cb_ceiling:.0f}pF, so this depends on the "
                            f"layout rather than being safe by design"),
                        "reference": ("UM10204 section 7.1 equation 1 and "
                                      "Table 10"),
                    })

                # The floor is set by the sink current the bus driver
                # must be able to swallow, 3mA up to Fast mode and 20mA
                # for Fast mode Plus, against a 0.4V output low.
                rail_volts = _rail_volts(pullup.get("rail", ""))
                if rail_volts is not None:
                    sink = 0.020 if speed >= 1000 else 0.003
                    rp_min = (rail_volts - 0.4) / sink
                    pullup["rp_min_ohms"] = round(rp_min)
                    if ohms < rp_min:
                        findings.append({
                            "net": net,
                            "designator": pullup["designator"],
                            "value": pullup["value"],
                            "severity": "error",
                            "rp_min_ohms": round(rp_min),
                            "problem": (
                                f"{pullup['value']} draws more than the "
                                f"{sink * 1000:.0f}mA a bus driver is "
                                f"required to sink from a "
                                f"{rail_volts}V rail"),
                            "reference": ("UM10204 section 7.1 "
                                          "equation 2"),
                        })

        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "bus_speed_khz": speed,
            "rise_time_ns": rise_ns,
            "bus_capacitance_pf": stated_cb,
            "buses_found": buses,
            "bus_count": len(buses),
            "examined": len(buses),
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("buses are recognised by net name, so one named "
                     "outside the convention is not seen at all. Sizing "
                     "follows UM10204 section 7.1"),
            **_schematic_scope(),
        }

    #: Nominal volts for rails whose name states the voltage.
    #:
    #: Only names that CARRY a number are converted. A rail called
    #: +VSYS or +VBAT has a voltage set by the design, not by its name,
    #: and inventing one would produce a confident wrong answer about
    #: derating. Those are reported as unknown instead.
    def _rail_volts(net: str) -> "float | None":
        import re as _re

        name = _base_net(net).upper().lstrip("+")
        match = _re.match(r"^(\d+)V(\d*)$", name)
        if match:
            whole, frac = match.group(1), match.group(2)
            return float(f"{whole}.{frac}") if frac else float(whole)
        match = _re.match(r"^V?(\d+)V(\d*)$", name)
        if match:
            whole, frac = match.group(1), match.group(2)
            return float(f"{whole}.{frac}") if frac else float(whole)
        if name in ("VUSB", "VBUS"):
            # Fixed by the USB specification rather than by this design.
            return 5.0
        return None

    def _rated_volts(description: str) -> "float | None":
        """The voltage rating stated in a part description."""
        import re as _re

        for match in _re.finditer(r"(\d+(?:\.\d+)?)\s*V\b",
                                  str(description or ""), _re.I):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            # A description states several voltages: an input range, an
            # output, a rating. The rating is the one that applies to a
            # capacitor, and on a passive description it is the only
            # voltage present, so the first is right for the parts this
            # check looks at.
            return value
        return None

    @mcp.tool()
    async def easyeda_audit_capacitor_voltage_margin(
            minimum_ratio: float = 2.0) -> dict[str, Any]:
        """Capacitors rated too close to the rail they sit on.

        A ceramic capacitor loses capacitance under DC bias, and an
        X5R or X7R part run near its rating can be down to a fraction
        of its marked value. The part meets its specification and the
        circuit still misbehaves, which is why this is worth checking
        on the schematic rather than discovering on the bench.

        The default asks for twice the rail voltage. That is the
        common working rule for ceramics rather than a figure from any
        one datasheet, so it is a parameter: a tantalum or an
        electrolytic is usually derated harder, a film part less.

        WHAT IS NOT CHECKED. The actual capacitance under bias, which
        needs the manufacturer's bias curve and cannot be derived from
        a description. This flags the parts whose curve is worth
        reading, and does not pretend to have read it.

        Rails are only judged when their NAME states a voltage. A rail
        called +VBAT or +VSYS has a voltage set by the design, and
        inventing one would produce a confident wrong verdict, so those
        are reported as unknown instead.
        """
        try:
            ratio = float(minimum_ratio)
        except (TypeError, ValueError):
            return {"ok": False,
                    "reason": f"minimum_ratio must be a number, "
                              f"got {minimum_ratio!r}"}
        if ratio <= 0:
            return {"ok": False,
                    "reason": "minimum_ratio must be greater than zero"}

        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        findings = []
        checked = 0
        unknown_rail = []
        unknown_rating = []
        for designator, props, pins, unique_id in rows:
            if not designator.upper().startswith("C"):
                continue
            rails = [str(v or "").strip() for v in pins.values()
                     if _is_supply(str(v or ""))]
            if not rails:
                continue
            rated = _rated_volts(props.get("Description"))
            if rated is None:
                unknown_rating.append(designator)
                continue
            for rail in rails:
                volts = _rail_volts(rail)
                if volts is None:
                    unknown_rail.append({"designator": designator,
                                         "rail": rail, "rated_volts": rated})
                    continue
                checked += 1
                if rated < volts * ratio:
                    findings.append({
                        "designator": designator,
                        "value": str(props.get("Value") or "").strip(),
                        "rail": rail,
                        "rail_volts": volts,
                        "rated_volts": rated,
                        "ratio": round(rated / volts, 2),
                        "severity": "error" if rated <= volts else "warning",
                        "problem": (
                            f"rated {rated}V on a {volts}V rail, a margin "
                            f"of {round(rated / volts, 2)}x against the "
                            f"{ratio}x asked for"),
                    })

        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "minimum_ratio": ratio,
            "capacitor_rails_checked": checked,
            "examined": checked,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("capacitance under DC bias is not computed: that "
                     "needs the manufacturer's bias curve. This flags "
                     "the parts whose curve is worth reading"),
        }
        if unknown_rail:
            result["rails_with_no_stated_voltage"] = unknown_rail
            result["scope_warning"] = (
                f"{len(unknown_rail)} capacitor rails were NOT judged "
                f"because the rail name states no voltage")
        if unknown_rating:
            result["capacitors_with_no_stated_rating"] = sorted(
                set(unknown_rating))
        result.update(_schematic_scope())
        return result

    @mcp.tool()
    async def easyeda_audit_crystal_load_caps() -> dict[str, Any]:
        """Crystals whose load capacitors are missing or mismatched.

        A crystal oscillates against the capacitance the circuit
        presents to it. Get that wrong and the part still works, which
        is what makes it worth checking on the schematic: the oscillator
        starts, the frequency is off, and it shows up as a serial link
        that fails only against certain peers or a clock that drifts
        with temperature.

        WHAT IS CHECKED. Each crystal pin reaches a capacitor to ground,
        and the two capacitors carry the SAME value. An unbalanced pair
        pulls the crystal off frequency and is the commoner mistake,
        because both capacitors are present so nothing looks wrong.

        WHAT IS NOT CHECKED. Whether the value is RIGHT. That needs the
        crystal's specified load capacitance from its datasheet and an
        estimate of stray capacitance, neither of which is in the
        schematic. The arithmetic is CL = (C1 C2)/(C1 + C2) + Cstray, so
        the numbers here are reported for that calculation rather than
        judged against a guess.

        A two pin crystal is assumed. A three or four pin oscillator has
        a supply and an output instead of a second load pin, and is
        reported as skipped rather than judged by a rule that does not
        apply to it.
        """
        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        by_net: dict[str, list] = {}
        for designator, props, pins, unique_id in rows:
            for number, net in pins.items():
                name = str(net or "").strip()
                if name:
                    by_net.setdefault(name, []).append((designator, number))

        values = {d: str(p.get("Value") or "").strip()
                  for d, p, _, _ in rows}
        pin_map = {d: pins for d, _, pins, _ in rows}

        findings = []
        crystals = []
        skipped = []
        for designator, props, pins, unique_id in rows:
            letters = "".join(c for c in designator if c.isalpha()).upper()
            if letters not in ("Y", "X", "XTAL"):
                continue
            signal_pins = [(n, str(v or "").strip())
                           for n, v in pins.items()
                           if str(v or "").strip()
                           and not _is_ground(str(v or ""))
                           and not _is_supply(str(v or ""))]
            if len(signal_pins) != 2:
                skipped.append({
                    "designator": designator,
                    "pin_count": len(pins),
                    "reason": ("not a two pin crystal, so the load capacitor "
                               "rule does not apply"),
                })
                continue

            caps = []
            for number, net in signal_pins:
                found = None
                for other, other_pin in by_net.get(net, []):
                    if other == designator:
                        continue
                    if not other.upper().startswith("C"):
                        continue
                    own = pin_map.get(other) or {}
                    if any(_is_ground(str(v or "")) for v in own.values()):
                        found = other
                        break
                caps.append({"pin": number, "net": net, "capacitor": found,
                             "value": values.get(found or "", "")})

            crystals.append({"designator": designator,
                             "device": str(props.get("DeviceName")
                                           or props.get("Device") or ""),
                             "load_capacitors": caps})

            missing = [c["pin"] for c in caps if not c["capacitor"]]
            if missing:
                findings.append({
                    "designator": designator,
                    "severity": "error",
                    "pins_without_a_load_capacitor": missing,
                    "problem": ("a crystal pin with no capacitor to ground; "
                                "the oscillator may not start, and if it "
                                "does the frequency is not the marked one"),
                })
                continue

            sizes = {c["value"] for c in caps}
            if len(sizes) > 1:
                findings.append({
                    "designator": designator,
                    "severity": "error",
                    "capacitors": [c["capacitor"] for c in caps],
                    "values": [c["value"] for c in caps],
                    "problem": ("the two load capacitors differ; an "
                                "unbalanced pair pulls the crystal off "
                                "frequency while looking correct, because "
                                "both parts are present"),
                })

        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "examined": len(crystals),
            "crystals": crystals,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("the VALUE is reported, not judged: the right one "
                     "needs the crystal's specified load capacitance and "
                     "an estimate of stray, via CL = (C1 C2)/(C1 + C2) "
                     "+ Cstray"),
        }
        if skipped:
            result["skipped"] = skipped
        result.update(_schematic_scope())
        return result

    #: Text that stands in for a value nobody filled in.
    #:
    #: Compared against the whole field rather than searched for, so a
    #: resistor legitimately valued "0" or a part described as "NA
    #: coating" is not swept up.
    _PLACEHOLDER_WORDS = frozenset({
        "TBD", "TBC", "TODO", "XXX", "???", "?", "N/A", "NA", "NONE",
        "VALUE", "PARTNUMBER", "PART NUMBER", "MPN", "CHANGEME",
        "FIXME", "PLACEHOLDER", "UNKNOWN", "-",
        # A lone asterisk, found on five parts of a live board as the
        # whole Datasheet field. Safe to list because the comparison is
        # against the ENTIRE field: a description that merely contains
        # an asterisk is untouched.
        "*",
    })

    #: Fields a review reads, and whether a blank one is a defect.
    #:
    #: A blank Value is only wrong on a part whose value IS its
    #: identity. A connector or a test point has no value and never
    #: will, and reporting those buries the resistor that does.
    _VALUE_BEARING = ("R", "C", "L", "FB")

    @mcp.tool()
    async def easyeda_audit_power_port_orientation() -> dict[str, Any]:
        """Power and ground glyphs pointing the wrong way.

        A ground symbol drawn upside down, or a rail flag turned
        sideways, is invariably an editing accident. It matters beyond
        tidiness: a VCC glyph pointing down reads as a ground at a
        glance, and one wrong-looking symbol holds the eye long enough
        to hide a real wiring fault underneath it.

        REPORTED AS INCONSISTENCY, NOT AGAINST A FIXED CONVENTION, and
        the difference is deliberate. The Altium check asserts absolute
        angles because Altium's power symbols carry a style
        (``ePowerBar``, ``ePowerGndPower``) that says which way each is
        meant to face. EasyEDA has no such style: a net flag is a net
        flag, and which rotation value points down has never been
        measured here. Asserting one would be a guess, and a guess in an
        audit produces confident findings about nothing.

        So this compares each glyph against the OTHERS OF ITS OWN KIND
        on the same sheet. Grounds are judged against grounds and rails
        against rails, because the two point opposite ways by
        convention. Where a group agrees except for one or two, those
        are reported. Where a group is evenly split, nothing is
        reported and the split is stated: with no majority there is no
        evidence of which way is right.

        Mirroring is reported alongside rotation, since a mirrored
        glyph can read wrongly at the same angle.

        Returns:
            Dict with ``checked``, ``violations``, ``items`` (each with
            ``net``, ``kind``, ``rotation``, ``mirror``, ``expected``
            and ``x``/``y``), and ``groups`` describing what each kind
            agreed on.
        """
        reply = _call("sch.components", timeout=60.0)
        if not reply.get("ok"):
            return reply
        parts = reply.get("components") or []

        # netflag and netport are the two glyph kinds that carry a net
        # and face a direction; part, sheet, block_symbol and
        # short_symbol are the rest of ESCH_PrimitiveComponentType.
        glyphs = [c for c in parts
                  if str(c.get("componentType", "")).lower()
                  in ("netflag", "netport")]
        if not glyphs:
            return {"ok": True, "checked": 0, "violations": 0,
                    "items": [], "groups": {},
                    "note": ("no net flags or ports on this sheet, so "
                             "there is nothing to compare")}

        buckets: dict[str, list] = {}
        for g in glyphs:
            net = str(g.get("net") or "")
            kind = "ground" if _is_ground_net(net) else "rail"
            buckets.setdefault(kind, []).append(g)

        items, groups = [], {}
        for kind, members in sorted(buckets.items()):
            poses: dict[tuple, int] = {}
            for g in members:
                key = (g.get("rotation"), bool(g.get("mirror")))
                poses[key] = poses.get(key, 0) + 1
            ranked = sorted(poses.items(), key=lambda kv: -kv[1])
            top, top_count = ranked[0]
            # A majority is required, not merely a plurality. Two
            # against two says nothing about which pair is wrong, and
            # reporting either would be inventing a convention.
            majority = top_count > len(members) / 2
            groups[kind] = {
                "count": len(members),
                "orientations": {f"rotation={k[0]},mirror={k[1]}": n
                                 for k, n in ranked},
                "agreed": (f"rotation={top[0]},mirror={top[1]}"
                           if majority else None),
            }
            if not majority:
                continue
            for g in members:
                key = (g.get("rotation"), bool(g.get("mirror")))
                if key == top:
                    continue
                items.append({
                    "net": str(g.get("net") or ""),
                    "kind": kind,
                    "rotation": g.get("rotation"),
                    "mirror": bool(g.get("mirror")),
                    "expected": f"rotation={top[0]},mirror={top[1]}",
                    "x": g.get("x"), "y": g.get("y"),
                })

        undecided = [k for k, v in groups.items() if v["agreed"] is None]
        out = {
            "ok": True,
            "checked": len(glyphs),
            "violations": len(items),
            "items": items,
            "groups": groups,
        }
        if undecided:
            out["note"] = (
                f"no majority orientation among the {', '.join(undecided)} "
                f"glyphs, so none were judged: an even split is not "
                f"evidence that either side is wrong")
        return out

    @mcp.tool()
    async def easyeda_audit_placeholder_values_schematic() -> dict[str, Any]:
        """Parts still carrying a placeholder instead of a real value.

        Two kinds, and the first is the one that hides.

        An UNRESOLVED FORMULA is a field left as "=Partnumber" or
        "=HelpURL", where the parameter it points at was never
        supplied. The editor shows the formula rather than an error, so
        it reads as filled in, and it survives all the way into a BOM
        as a part number nobody can order.

        A PLACEHOLDER WORD is TBD, TODO or similar, left during design
        and never revisited.

        A blank Value is reported only for parts whose value is their
        identity, which is resistors, capacitors, inductors and beads.
        A connector or a test point has no value and never will, so
        reporting those would bury the ones that matter.

        The sibling ``placeholder_values`` asks this of the BOARD. This
        reads the schematic, where the parameter is actually set and
        where the fix belongs.
        """
        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        fields = ("Value", "DeviceName", "Partnumber", "PartNumber",
                  "Manufacturer", "Footprint", "Description", "Datasheet")

        # Fields where a library template routinely leaves a formula on
        # EVERY part. Counted rather than itemised: a real board came
        # back with 111 identical ComponentLink1URL findings, which
        # buried the one part that had a genuinely unresolved value.
        # The datasheet link is already judged by missing_datasheets,
        # which requires the slot to hold a real URL.
        bulk_fields = ("ComponentLink1URL", "PP Comment")
        bulk: dict[str, int] = {}

        findings = []
        checked = 0
        for designator, props, pins, unique_id in rows:
            checked += 1
            for field in bulk_fields:
                if str(props.get(field) or "").strip().startswith("="):
                    bulk[field] = bulk.get(field, 0) + 1
            problems = []
            for field in fields:
                if field not in props:
                    continue
                text = str(props.get(field) or "").strip()
                if text.startswith("="):
                    problems.append({
                        "field": field,
                        "value": text,
                        "kind": "unresolved formula",
                    })
                elif text.upper() in _PLACEHOLDER_WORDS:
                    problems.append({
                        "field": field,
                        "value": text,
                        "kind": "placeholder text",
                    })
            letters = "".join(c for c in designator if c.isalpha()).upper()
            if (letters in _VALUE_BEARING
                    and not str(props.get("Value") or "").strip()):
                problems.append({
                    "field": "Value",
                    "value": "",
                    "kind": "blank on a part whose value is its identity",
                })
            if problems:
                findings.append({
                    "designator": designator or "(unnamed)",
                    "device": str(props.get("DeviceName")
                                  or props.get("Device") or ""),
                    "severity": "error",
                    "problems": problems,
                })

        findings.sort(key=lambda f: f["designator"])
        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "examined": checked,
            "fields_examined": list(fields),
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("a formula shows in the editor as text rather than "
                     "an error, so it reads as filled in and reaches the "
                     "BOM as a part number nobody can order"),
        }
        if bulk:
            result["formula_fields_counted_not_itemised"] = bulk
            result["scope_note_fields"] = (
                "these fields carry a template formula on most parts, so "
                "they are counted rather than listed one by one. "
                "ComponentLink1URL is judged properly by the "
                "missing_datasheets audit, which requires a real URL")
        result.update(_schematic_scope())
        return result

    @mcp.tool()
    async def easyeda_audit_duplicate_designators() -> dict[str, Any]:
        """Two parts sharing one designator.

        An unambiguous error rather than a judgement call. The netlist
        keys parts by a unique id, so two entries can carry the same
        Designator, and everything downstream then treats them as one
        part: the BOM orders a single item, the board expects a single
        footprint, and the pins of the two merge into a connectivity
        picture that matches neither.

        Usually a copied block whose designators were never renumbered,
        which easyeda_increment_designators exists to fix.
        """
        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        seen: dict[str, list] = {}
        unnamed = 0
        for designator, props, pins, unique_id in rows:
            if not designator:
                unnamed += 1
                continue
            seen.setdefault(designator, []).append({
                "unique_id": unique_id,
                "device": str(props.get("DeviceName")
                              or props.get("Device") or ""),
                "pin_count": len(pins),
            })

        findings = [{"designator": d, "count": len(items), "parts": items}
                    for d, items in sorted(seen.items()) if len(items) > 1]
        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "components_checked": len(rows) - unnamed,
            "parts_without_a_designator": unnamed,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
        }

    @mcp.tool()
    async def easyeda_audit_single_pin_nets_schematic() -> dict[str, Any]:
        """Nets that reach exactly one pin.

        A net with one pin connects nothing. It is usually a wire that
        stops short of its target, a label that never met a second
        wire, or a signal left over after an edit.

        A block opened on its own is the honest exception: signals
        crossing its boundary genuinely have one pin inside it, so this
        is worth running on the top schematic, where the netlist is
        flattened. The scope note in the reply says which case applies.
        """
        rows, reply = _netlist_entries()
        if rows is None:
            return reply

        members: dict[str, list] = {}
        for designator, props, pins, unique_id in rows:
            for number, net in pins.items():
                name = str(net or "").strip()
                if name:
                    members.setdefault(name, []).append(
                        f"{designator or '?'}.{number}")

        findings = [{"net": name, "pins": pins_on}
                    for name, pins_on in sorted(members.items())
                    if len(pins_on) < 2]
        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "nets_checked": len(members),
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("a block opened on its own reports its boundary "
                     "signals here; run this on the top schematic"),
        }
        result.update(_schematic_scope())
        return result

    @mcp.tool()
    async def easyeda_audit_unconnected_schematic_pins(
        ignore_prefixes: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Schematic pins that sit on no net.

        The schematic counterpart of the board's unconnected-pad check,
        and until now there was none: every audit here reads the PCB,
        so a schematic review ran three checks out of twenty-four and
        none of them looked at connectivity.

        Built on the netlist, because that is the only schematic read
        that returns pins at all. Measured: the per-pin and
        per-component APIs all fail on a live schematic, while the
        netlist carries pin number to net name for every part.

        GROUPED BY PART, which is what a reviewer acts on: an IC with
        four floating pins is one thing to look at, not four.

        REPORTS, does not judge. A deliberately unused pin, a no-connect
        and a forgotten wire look identical here, and nothing in the
        netlist distinguishes them.

        Args:
            ignore_prefixes: designator prefixes to skip, e.g.
                ["TP", "H"] for test points and mounting holes.
        """
        reply = _call("sch.netlist", timeout=60.0)
        if not reply.get("ok"):
            return reply

        raw = reply.get("netlist")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError) as exc:
                return {"ok": False,
                        "reason": f"the netlist did not parse: {exc}"}
        if not isinstance(raw, dict) or not raw:
            return {"ok": False, "reason": (
                "the netlist is empty, so nothing was checked. That is "
                "not the same as a schematic with no loose pins")}

        skip = tuple(str(x).strip() for x in (ignore_prefixes or [])
                     if str(x).strip())
        findings = []
        examined = 0
        skipped = 0
        pins_seen = 0
        for entry in raw.values():
            if not isinstance(entry, dict):
                continue
            table = entry.get("pins")
            if not isinstance(table, dict):
                continue
            props = entry.get("props") if isinstance(entry.get("props"),
                                                     dict) else {}
            designator = str(props.get("Designator") or "").strip()
            if skip and designator.startswith(skip):
                skipped += 1
                continue
            examined += 1
            pins_seen += len(table)
            loose = sorted(str(number) for number, net in table.items()
                           if not str(net or "").strip())
            if loose:
                findings.append({
                    "designator": designator or "(unnamed)",
                    # DeviceName first: Device carries the library's
                    # internal id, so reading it first put a 32 digit
                    # uuid where the part name belongs and made the
                    # finding unreadable.
                    "device": str(props.get("DeviceName")
                                  or props.get("Device") or ""),
                    "unconnected_count": len(loose),
                    "pins": loose,
                    "pin_count": len(table),
                })

        findings.sort(key=lambda f: -f["unconnected_count"])
        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "components_checked": examined,
            "components_skipped": skipped,
            "pins_checked": pins_seen,
            "violation_count": len(findings),
            "finding_count": len(findings),
            # BOTH UNITS, because the headline counts COMPONENTS and
            # everything around it counts pins. Four violations beside
            # 332 pins checked reads as four loose pins; the design had
            # thirteen, across four parts. Reporting one number without
            # the other made the audit look like it disagreed with
            # easyeda_get_schematic_pins when the two agreed exactly.
            "unconnected_pin_count": sum(
                f["unconnected_count"] for f in findings),
            "counts_note": ("violation_count counts COMPONENTS holding at "
                            "least one loose pin; unconnected_pin_count "
                            "counts the pins themselves"),
            "findings": findings,
            "note": ("a loose pin is a place to look, not a verdict: a "
                     "deliberate no-connect looks the same here"),
        }
        result.update(_schematic_scope())
        return result

    @mcp.tool()
    async def easyeda_audit_unlocked_components() -> dict[str, Any]:
        """Placed components that are not locked against being moved.

        An unlocked part is one stray click and drag away from moving,
        and nothing warns you. The gerbers still look plausible, so this
        is a class of error found at assembly rather than at review:
        connectors that no longer line up with the enclosure, a mounting
        hole that has drifted, an antenna keepout no longer where the
        layout was tuned for.

        REPORTS, does not judge. Most parts are unlocked for most of a
        layout and that is normal work in progress. What makes this
        worth running is the LAST pass before release, and the parts
        whose position was chosen for a mechanical reason rather than an
        electrical one, which is why the count matters less than which
        designators appear.

        ONE read, the same pcb.components every other placement audit
        makes, so adding it to a review costs nothing extra.

        Field provenance: ``primitiveLock`` was measured on a live
        111-component board and is recorded field for
        field in the snapshot fixture. A component that does not report
        it is counted unreadable rather than assumed locked: assuming
        would turn a missing field into a clean bill of health, which is
        the failure this project keeps finding.
        """
        reply = _call("pcb.components", timeout=60.0)
        if not reply.get("ok"):
            return reply

        findings = []
        examined = 0
        unreadable = 0
        for item in reply.get("components") or []:
            if not isinstance(item, dict):
                continue
            locked = item.get("primitiveLock")
            if not isinstance(locked, bool):
                # Neither locked nor unlocked: not examined.
                unreadable += 1
                continue
            examined += 1
            if locked:
                continue
            designator = ""
            for key in ("designator", "name"):
                found = item.get(key)
                if isinstance(found, str) and found.strip():
                    designator = found.strip()
                    break
            findings.append({
                "designator": designator or "(unnamed)",
                "primitive_id": item.get("primitiveId"),
                "layer": item.get("layer"),
                "x": item.get("x"),
                "y": item.get("y"),
            })

        findings.sort(key=lambda f: f["designator"])
        result = {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "components_checked": examined,
            "components_without_lock_state": unreadable,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("unlocked is normal during layout: this is a "
                     "release check, and which parts appear matters "
                     "more than how many"),
        }
        if unreadable and not examined:
            # Every component withheld the field. Zero findings here
            # means nothing was looked at, and saying "ok" without
            # saying that is the false-clean shape exactly.
            result["scope_warning"] = (
                f"none of the {unreadable} components reported a lock "
                f"state, so no component was actually checked")
        return result

    @mcp.tool()
    async def easyeda_audit_off_angle_components(
        tolerance_degrees: float = 0.5,
    ) -> dict[str, Any]:
        """Components rotated off the 90-degree grid.

        A part at 47 degrees is almost always a drag that slipped, and
        it is invisible in a table of coordinates: the position is
        fine, the courtyard barely moves, and the first person to
        notice is the pick-and-place operator.

        REPORTS, does not judge: RF layouts rotate parts deliberately,
        and nothing readable here tells a tuned 45-degree feed from a
        slip. The tolerance forgives float dust from imports.

        Field is measured: every component carries
        ``rotation``. One missing it is counted unreadable rather than
        passed as aligned.

        Args:
            tolerance_degrees: how far off a right angle still counts
                as on it. Half a degree forgives conversion noise
                without forgiving a slip.
        """
        if not 0 <= tolerance_degrees < 45:
            return {"ok": False, "reason": (
                "tolerance_degrees must be at least 0 and below 45")}

        reply = _call("pcb.components", timeout=60.0)
        if not reply.get("ok"):
            return reply

        findings = []
        unreadable = 0
        counted = 0
        for item in reply.get("components") or []:
            if not isinstance(item, dict):
                continue
            counted += 1
            rotation = item.get("rotation")
            if not isinstance(rotation, (int, float)):
                unreadable += 1
                continue
            nearest = round(float(rotation) / 90.0) * 90.0
            off_by = abs(float(rotation) - nearest)
            if off_by > tolerance_degrees:
                designator = ""
                for key in ("designator", "name"):
                    found = item.get(key)
                    if isinstance(found, str) and found.strip():
                        designator = found.strip()
                        break
                findings.append({
                    "designator": designator or "(unnamed)",
                    "primitive_id": item.get("primitiveId"),
                    "rotation": float(rotation),
                    "nearest_right_angle": nearest % 360,
                    "off_by": round(off_by, 3),
                })

        findings.sort(key=lambda f: -f["off_by"])
        return {
            "ok": True,
            "verified_live": reply.get("verified_live"),
            "components_counted": counted,
            "unreadable": unreadable,
            "tolerance_degrees": tolerance_degrees,
            "violation_count": len(findings),
            "finding_count": len(findings),
            "findings": findings,
            "note": ("RF parts rotate on purpose; a finding is a place "
                     "to look, not a fault"),
        }

    @mcp.tool()
    async def easyeda_get_measured_shapes(
        command: str = "",
    ) -> dict[str, Any]:
        """What live sessions have established about the editor's replies.

        The published API reference lists methods and not the shape of
        what they return, so this project measures shapes against a real
        editor and records them. This tool reads that record back:
        which commands have round-tripped, the field names their replies
        carried, and a truncated example value where one was sampled.

        Consult this BEFORE writing anything against a reply field. A
        guessed field name does not fail loudly; it reads nothing and
        reports a clean empty, which is the most expensive failure shape
        this backend has produced.

        Offline: reads the record, never the editor. Empty results mean
        no session has measured that yet, which is the honest answer.

        Args:
            command: one command name for its full detail, or empty for
                the overview of everything measured.
        """
        from eda_agent.bridge.easyeda_verified import load_verified

        record = load_verified()
        commands = record.get("commands") or {}
        shapes = record.get("shapes") or {}
        samples = record.get("samples") or {}

        wanted = str(command or "").strip()
        if wanted:
            return {
                "ok": True,
                "command": wanted,
                "verified_live": bool(commands.get(wanted)),
                "shape": shapes.get(wanted, ""),
                "sample": samples.get(wanted, ""),
                "recorded_at": record.get("recorded_at"),
                "editor": record.get("editor"),
            }

        return {
            "ok": True,
            "recorded_at": record.get("recorded_at"),
            "editor": record.get("editor"),
            "verified_count": sum(1 for v in commands.values() if v),
            "command_count": len(commands),
            "verified": sorted(k for k, v in commands.items() if v),
            "unverified": sorted(k for k, v in commands.items() if not v),
            "shapes": dict(sorted(shapes.items())),
        }

    @mcp.tool()
    async def easyeda_get_team() -> dict[str, Any]:
        """The current team, whose uuid every folder operation needs.

        EasyEDA files folders under a team even for a personal
        workspace, so this is the first call of any folder workflow.
        """
        return _call("dmt.team")

    @mcp.tool()
    async def easyeda_list_folders(team_uuid: str = "") -> dict[str, Any]:
        """Every project folder in a team, with its details.

        Args:
            team_uuid: from ``easyeda_get_team``.
        """
        if not str(team_uuid).strip():
            return {"ok": False, "reason": (
                "team_uuid is required; read it from easyeda_get_team")}
        return _call("dmt.folders", {"team_uuid": team_uuid},
                     timeout=120.0)

    @mcp.tool()
    async def easyeda_create_folder(
        team_uuid: str = "", name: str = "",
        parent_folder_uuid: str = "", description: str = "",
    ) -> dict[str, Any]:
        """Create a project folder, top-level or nested.

        Signature verified against the installed api-types.d.ts:
        the team uuid comes first everywhere in the folder API.

        Args:
            team_uuid: from ``easyeda_get_team``.
            name: the folder's name.
            parent_folder_uuid: parent for a nested folder; top-level
                when empty.
            description: optional description.
        """
        if not str(team_uuid).strip():
            return {"ok": False, "reason": (
                "team_uuid is required; read it from easyeda_get_team")}
        if not str(name).strip():
            return {"ok": False, "reason": "name is required"}
        return _call("dmt.create_folder", {
            "team_uuid": team_uuid, "name": name,
            "parent_folder_uuid": parent_folder_uuid,
            "description": description,
        })

    @mcp.tool()
    async def easyeda_move_project_to_folder(
        project_uuid: str = "", folder_uuid: str = "",
    ) -> dict[str, Any]:
        """Move a project into a folder, or to the top level.

        Args:
            project_uuid: from ``easyeda_list_projects``.
            folder_uuid: the destination; empty moves the project OUT
                of any folder, back to the top level, which is the
                documented meaning of omitting it.
        """
        if not str(project_uuid).strip():
            return {"ok": False, "reason": (
                "project_uuid is required; find it with "
                "easyeda_list_projects")}
        return _call("dmt.move_project_to_folder", {
            "project_uuid": project_uuid, "folder_uuid": folder_uuid,
        })

    # Captured at the END of registration, so it holds every audit
    # defined above. Built from locals() rather than a written list:
    # a list would go stale the first time an audit is added and
    # nothing would say so, and silently reviewing 18 of 19 checks is
    # exactly the failure this aggregator exists to avoid.
    def _takes_no_required_argument(fn) -> bool:
        import inspect

        return all(p.default is not inspect.Parameter.empty
                   for p in inspect.signature(fn).parameters.values())

    #: Every audit the review can actually RUN.
    #:
    #: Built from locals rather than a written list, so a new audit
    #: joins automatically; a written list goes stale the first time one
    #: is added and nothing says so.
    #:
    #: Filtered on arguments for a real reason rather than tidiness.
    #: easyeda_audit_footprint_vs_datasheet needs a land-pattern spec
    #: transcribed from a datasheet, and it audits the open FOOTPRINT
    #: rather than the board. Sweeping it in meant the review tried to
    #: call it with nothing and the whole review failed. An audit the
    #: review cannot supply input for is not a board check that went
    #: missing; it is a different kind of tool.
    #: Audits that examine a LIBRARY ITEM rather than the open design.
    #:
    #: The argument filter below catches most of these, because an
    #: audit that needs input usually declares it required.
    #: device_pin_parity does not: its arguments have defaults and it
    #: refuses at RUN time, so it slipped into every review and failed
    #: there, so it appears as a failure in every review. A review
    #: that reports a failure it causes itself teaches a reader to
    #: ignore failures.
    #:
    #: Not a board check that went missing. A different kind of tool,
    #: pointed at a library device the caller names.
    _LIBRARY_AUDITS = frozenset({"device_pin_parity"})

    #: Audits whose data the editor does not expose, kept out of the
    #: aggregate review and still callable on their own.
    #:
    #: mirrored_text needs board text. pcb_PrimitiveString and
    #: pcb_PrimitiveAttribute both accept the call and never answer,
    #: their getAllPrimitiveId fallback does the same, and
    #: pcb_PrimitiveText does not exist in the runtime. There is no
    #: fourth route, so including it costs every review the full
    #: handler timeout and produces a failure that is not a defect in
    #: the design or in this code.
    #:
    #: Listed in the reply as unavailable rather than dropped, so the
    #: gap stays visible and can be retested if the editor gains the
    #: read.
    _UNAVAILABLE_AUDITS = frozenset({"mirrored_text"})

    _AUDITS = {name[len("easyeda_audit_"):]: fn
               for name, fn in list(locals().items())
               if name.startswith("easyeda_audit_") and callable(fn)
               and _takes_no_required_argument(fn)
               and name[len("easyeda_audit_"):] not in _LIBRARY_AUDITS
               and name[len("easyeda_audit_"):] not in _UNAVAILABLE_AUDITS}

    #: What a design review is JUDGED from, section by section.
    #:
    #: The audits say what is wrong. This says what the design IS, which
    #: is the other half and the half a reviewer reads first: the
    #: netlist, the parts, the rules, what is unrouted. The Altium side
    #: has had `design_review_snapshot` for this all along; on EasyEDA
    #: every piece existed and nothing bundled them, so a reviewer made
    #: fifteen calls and had to know which ones exist.
    #:
    #: Values are (tool, timeout). The slow ones are off by default for
    #: the same reason Altium's are: DRC and ERC and a BOM export can
    #: each take a minute, and a reviewer wanting the cheap picture
    #: should not pay for them.
    def _unique_parts_from_netlist(netlist_reply):
        """Distinct manufacturer/part pairs, from the measured shape.

        `sch.netlist` is keyed by uid, each entry carrying a `props`
        dict whose part number is spelled `Partnumber`. All three names
        here are measured from a live board rather than guessed, which
        matters because a wrong field name does not fail: it reads
        nothing and reports a design with no parts in it.

        An unresolved template (`={Partnumber}`) is not a part number.
        Those appear in `sch.components` on a live board, and treating
        one as a real MPN would send a reviewer looking for a datasheet
        that cannot exist.
        """
        if not isinstance(netlist_reply, dict):
            return []
        entries = netlist_reply.get("netlist")
        if not isinstance(entries, dict):
            return []

        seen, parts = set(), []
        for entry in entries.values():
            props = (entry or {}).get("props") if isinstance(entry, dict) else None
            if not isinstance(props, dict):
                continue
            part = str(props.get("Partnumber") or "").strip()
            if not part or part.startswith("=") or "{" in part:
                continue
            maker = str(props.get("Manufacturer") or "").strip()
            key = (maker.lower(), part.lower())
            if key in seen:
                continue
            seen.add(key)
            parts.append({
                "manufacturer": maker,
                "part_number": part,
                "designators": str(props.get("Designator") or "").strip(),
            })
        return parts

    #: Captured the same way _AUDITS is, so a renamed tool breaks the
    #: build of this table rather than silently dropping a section.
    _registered_here = dict(locals())

    def _section_tool(name):
        tool = _registered_here.get(name)
        if tool is None:                               # pragma: no cover
            raise RuntimeError(
                f"the review snapshot names {name}, which is not "
                f"registered; the tool was renamed and the section "
                f"would have gone missing without saying so")
        return tool

    _SNAPSHOT_SECTIONS = {
        "project_info": _section_tool("easyeda_get_project_info"),
        "components": _section_tool("easyeda_get_components"),
        "schematic_components": _section_tool(
            "easyeda_get_schematic_components"),
        "netlist": _section_tool("easyeda_get_netlist"),
        "nets": _section_tool("easyeda_get_nets"),
        "net_classes": _section_tool("easyeda_get_net_classes"),
        "net_rules": _section_tool("easyeda_get_net_rules"),
        "layers": _section_tool("easyeda_get_layers"),
        "board_statistics": _section_tool("easyeda_get_board_statistics"),
        "unrouted": _section_tool("easyeda_get_unrouted_nets"),
        "unconnected_pins": _section_tool("easyeda_get_unconnected_pins"),
        "sch_pcb_compare": _section_tool("easyeda_compare_schematic_pcb"),
    }

    #: Slow enough to be opt-in, for the same reason Altium's are: each
    #: can take a minute, and a reviewer wanting the cheap picture
    #: should not pay for them.
    _SNAPSHOT_SLOW = {
        "drc": _section_tool("easyeda_run_drc"),
        "erc": _section_tool("easyeda_run_erc"),
    }

    @mcp.tool()
    async def easyeda_audit_footprint_vs_datasheet(
        spec_json: Any,
    ) -> dict[str, Any]:
        """Audit the OPEN footprint against the manufacturer's land pattern.

        The counterpart to ``lib_audit_footprint_vs_datasheet``. Every
        board-side audit on this backend checks the design; this is the
        one that checks the LIBRARY, and it catches the defect none of
        the others can see: a land pattern that does not match the part
        it is named after. A board can be perfectly routed onto a
        footprint that will not solder.

        Open the footprint first with ``easyeda_open_footprint``; the
        pads are read from the document in front of you.

        DATASHEET DISCIPLINE: the spec must be transcribed from the
        manufacturer datasheet fetched in this conversation, and
        ``spec.source`` must cite it (url + figure/page + part number).
        Distributor drawings, symbol metadata and memory of a package
        are not acceptable sources, and there are no built-in package
        tables here on purpose.

        The comparison itself is the SAME code the Altium side uses, so
        the two backends cannot drift into disagreeing about whether a
        given footprint matches a given datasheet. Only the reading and
        the units differ: EasyEDA reports PCB geometry in mils and the
        land-pattern spec is in mm, so the pads are converted here.

        Args:
            spec_json: the land-pattern spec, as an object or its JSON
                text. Same schema as the Altium tool: ``source``
                (required), plus explicit ``pads`` or a ``dual_row`` /
                ``quad`` generator. Spans are pad centre to pad centre
                (C), never the outer extent Z or a package lead span.

        Whatever the spec says, overlapping copper between pads of
        different names is an error and a gap under 0.1 mm a warning
        (``pad_clearance``), so a footprint matching a misread spec still
        fails.

        Returns:
            ``{ok, rotation_applied_deg, findings: [...]}``; ok is true
            only when nothing error-severity was found.
        """
        import json as _json

        from eda_agent.design.footprint_audit import (
            LandPatternSpec,
            audit_footprint_against_spec,
        )

        if isinstance(spec_json, str):
            try:
                spec_json = _json.loads(spec_json)
            except _json.JSONDecodeError as exc:
                return {"ok": False, "reason": (
                    f"spec_json is not valid JSON ({exc}); pass the "
                    f"land-pattern spec object or its JSON text")}
        try:
            spec = LandPatternSpec.model_validate(spec_json)
        except Exception as exc:                       # noqa: BLE001
            return {"ok": False, "reason": (
                f"spec_json does not match the land-pattern spec "
                f"schema: {exc}")}

        reply = _call("pcb.pads")
        if not reply.get("ok"):
            return reply

        raw = reply.get("pads")
        if not isinstance(raw, list) or not raw:
            return {"ok": False, "reason": (
                "the open document reports no pads. Open the footprint "
                "with easyeda_open_footprint first; auditing an empty "
                "read would report every datasheet pad as missing, "
                "which says nothing about the footprint")}

        # Measured shape: pads carry `padNumber`, and `pad` is a list
        # like ["RECT", w, h, r] rather than separate width and height
        # fields. Reading `width`/`height` off a pad returns nothing and
        # every dimension compares as zero, which reports a perfect
        # footprint as catastrophically wrong. The parsing is shared with
        # the package footprint tool's readback.
        pads = _easyeda_pad_rows(raw)

        report = audit_footprint_against_spec(spec, {"pads": pads})
        report["pad_count_read"] = len(pads)
        report["spec_source"] = spec.source.model_dump()
        report["units_note"] = (
            "pad geometry was read in mils and converted to mm to match "
            "the datasheet spec")
        return report

    @mcp.tool()
    async def easyeda_audit_footprint_vs_package(
        package: Any,
        density: str = "N",
    ) -> dict[str, Any]:
        """Audit the OPEN footprint against its package drawing, pads only.

        The EasyEDA twin of ``lib_audit_footprint_vs_package``, running the
        same comparison on the pads read from the document in front of
        you (open it with ``easyeda_open_footprint``): pad count,
        numbering, pin 1 and mirror images, pitch, row span, land sizes
        and centres against the IPC-7351B pattern, land over terminal,
        holes, paste and mask expansion fields, and the origin. Silkscreen,
        courtyard, assembly outline, paste windows and height are NOT read
        here: the footprint graphics are polylines and the extension has
        no read for them, so those checks are listed under
        ``not_examined`` rather than passed.

        Args:
            package: the PackageSpec as an object or JSON text.
            density: L, N or M, the IPC pattern compared against.

        Returns:
            ``{ok, errors, warnings, findings, examined, not_examined,
            alignment, metrics, notes}``.
        """
        from eda_agent.design.landpattern.audit import (
            audit_footprint_vs_package,
        )
        from eda_agent.design.landpattern.check import read_package
        from eda_agent.design.landpattern.emit import model_from_package
        from eda_agent.design.landpattern.spec import SpecError

        try:
            spec = read_package(package)
            model = model_from_package(spec, density=density or "N")
        except SpecError as exc:
            return {"ok": False, "reason": str(exc), "findings": []}
        reply = _call("pcb.pads")
        if not reply.get("ok"):
            return reply
        raw = reply.get("pads")
        if not isinstance(raw, list) or not raw:
            return {"ok": False, "reason": (
                "the open document reports no pads. Open the footprint "
                "with easyeda_open_footprint first; an empty read says "
                "nothing about the footprint"), "findings": []}
        pads = _easyeda_pad_rows(raw)
        report = audit_footprint_vs_package(
            spec, pads, model=model, primitives=None,
            primitives_reason=(
                "not read on EasyEDA: the footprint's silkscreen, courtyard "
                "and assembly are polylines and the extension has no read "
                "for them, and a footprint has no height field"))
        report["notes"] = list(report.get("notes") or []) + [
            "EasyEDA's footprint canvas has not been measured for its Y "
            "direction; if it runs Y down, every footprint reads here as a "
            "mirror image, so audit one known-good footprint first",
            "pad geometry was read in mils and converted to mm"]
        report["pad_count_read"] = len(pads)
        return report

    @mcp.tool()
    async def easyeda_review_snapshot(
        sections: "list[str] | None" = None,
        run_drc: bool = False,
        run_erc: bool = False,
    ) -> dict[str, Any]:
        """Fetch the design data a review is JUDGED from, in one call.

        The counterpart to ``easyeda_review_board``, and the half a
        reviewer reads first. The audits say what is wrong; this says
        what the design IS: the netlist, the parts on both sides, the
        nets and their classes and rules, the layer stack, what is
        unrouted, which pins sit on no net, and whether the board
        matches the schematic.

        Judge connectivity from THIS, never from a render.
        ``easyeda_render_image`` is geometry only: a picture can show a
        wire that shares no net, and hide a net that is electrically
        correct.

        A section that cannot be read is listed under
        ``sections_failed`` rather than returned empty, because an
        empty list and a failed read mean opposite things and a
        reviewer acting on the first when it was really the second
        concludes the design is clean.

        Args:
            sections: which sections to fetch; omit for all the cheap
                ones. Unknown names are refused rather than ignored,
                since a typo would otherwise silently narrow a review.
            run_drc: also run the editor's DRC. Off by default because
                it can take a minute.
            run_erc: also run the editor's ERC, same reasoning.

        Returns:
            ``{ok, sections_fetched, sections_failed, sections}``. ``ok``
            is False when NOTHING could be read, which is not the same
            as a design with nothing to report.
        """
        wanted = list(sections) if sections else list(_SNAPSHOT_SECTIONS)
        available = dict(_SNAPSHOT_SECTIONS)
        if run_drc:
            wanted.append("drc")
        if run_erc:
            wanted.append("erc")
        available.update(_SNAPSHOT_SLOW)

        unknown = [s for s in wanted if s not in available]
        if unknown:
            return {"ok": False, "reason": (
                f"unknown section(s) {unknown}; choose from "
                f"{sorted(available)}")}

        fetched: dict[str, Any] = {}
        failed: list[dict[str, str]] = []

        # One cache for the whole snapshot: several sections read the
        # same component and net lists, and on a real board that is the
        # same few hundred rows fetched again and again. Restored in the
        # finally without exception, because a cache outliving this call
        # would hand a later reader an older board.
        global _READ_CACHE
        outer_cache = _READ_CACHE
        _READ_CACHE = {}
        try:
            for name in wanted:
                try:
                    reply = await available[name]()
                except Exception as exc:               # noqa: BLE001
                    failed.append({"section": name,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if isinstance(reply, dict) and reply.get("ok") is False:
                    failed.append({"section": name,
                                   "error": str(reply.get("reason")
                                                or reply.get("unavailable")
                                                or "refused")})
                    continue
                fetched[name] = reply
        finally:
            _READ_CACHE = outer_cache

        # How much each section actually CARRIED.
        #
        # "12 sections fetched" reads as data being present, and a
        # snapshot of a board whose every read came back empty says
        # exactly that. It is the same collapse easyeda_review_board had
        # at two levels already: fetched is not the same as populated,
        # and only one of them tells a reviewer anything.
        # Bookkeeping is not data. Counting every non-empty container
        # made board_statistics report 13 rows on an entirely empty
        # board, because its `unavailable` bucket had 13 entries: the
        # section that says "I could read nothing" was counted as the
        # section carrying the most. The same distinction the envelope
        # guard needed, and it did not carry over on its own.
        _NOT_DATA = {
            "unavailable", "sections_failed", "unreadable", "refused",
            "note", "reason", "failed", "command", "ok", "verified_live",
            "complete", "scope_warning",
        }
        rows: dict[str, int] = {}
        for name, section in fetched.items():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if key in _NOT_DATA:
                    continue
                if isinstance(value, list):
                    # The LARGEST collection, not the sum of them.
                    #
                    # Summing double counts any section that returns a
                    # derived list beside its primary one. Measured
                    # easyeda_get_nets returns its nets plus
                    # a single_pin_nets summary of 1, and the snapshot
                    # reported 71 nets. Two tools disagreeing about the
                    # same board is worse than either being wrong
                    # alone, because it gives a reader no way to decide
                    # which to believe.
                    #
                    # A max is self maintaining where a list of
                    # exempt key names is not: the next tool to add a
                    # summary field would inflate the count again and
                    # nobody would notice a number that is merely
                    # slightly too big.
                    rows[name] = max(rows.get(name, 0), len(value))
                elif isinstance(value, dict):
                    # A dict of SCALARS is one record, not a collection.
                    # board_statistics carries twelve counters that exist
                    # whatever the board holds, so counting its keys
                    # reported the section that measured nothing as the
                    # one carrying the most data. Only entries that are
                    # themselves records count, which is what the
                    # uid-keyed netlist looks like.
                    rows[name] = max(rows.get(name, 0), sum(
                        1 for item in value.values()
                        if isinstance(item, (dict, list)) and item))

        # Adding zero still creates the key, so a section that carried
        # nothing was landing in sections_with_data with a count of 0.
        # Dropped rather than reported as an empty entry: the list is
        # read as "these have data", and a name in it that does not is
        # the same wrong answer this whole change is fixing.
        rows = {name: count for name, count in rows.items() if count}

        out: dict[str, Any] = {
            "ok": bool(fetched),
            "sections_fetched": sorted(fetched),
            "sections_with_data": sorted(rows),
            "rows_by_section": rows,
            "sections_failed": failed,
            "sections": fetched,
        }
        if fetched and not rows:
            out["scope_warning"] = (
                "every section came back EMPTY. That is what an empty "
                "design looks like and also what reading the wrong "
                "document looks like, so this is not evidence the design "
                "is small or clean. Check which document is open.")

        # The parts, and the standing instruction to check them against
        # their datasheets. Data alone is not a review: the Altium
        # snapshot has carried this block from the start, and without it
        # a reviewer reads a netlist and never asks whether the part can
        # do what the schematic assumes.
        #
        # Built from the measured EasyEDA shape rather than handed to
        # the shared extractor. That extractor walks a list under
        # "components" and looks for "PartNumber"; the netlist here is
        # keyed by uid with a "props" sub-dict and spells it
        # "Partnumber". Passing it straight through returns an empty
        # parts list and a guidance block saying there is nothing to
        # check, which is the quiet way to drop the datasheet discipline
        # entirely.
        unique_parts = _unique_parts_from_netlist(fetched.get("netlist"))
        if unique_parts:
            out["unique_parts"] = unique_parts
            try:
                from .datasheet_hints import build_guidance_block

                out["review_guidance"] = build_guidance_block(
                    unique_parts, context="design_review")
            except Exception:                          # noqa: BLE001
                # Guidance is an addition to the snapshot, never a
                # reason to lose it.
                pass
        if not fetched:
            out["reason"] = (
                "not one section could be read, so this is not an empty "
                "design: nothing was fetched. See 'sections_failed'.")
        elif failed:
            out["note"] = (
                f"{len(fetched)} of {len(wanted)} sections were read; the "
                f"rest are under 'sections_failed' and are NOT empty "
                f"results")
        return out

    @mcp.tool()
    async def easyeda_review_board(
        include_clean: bool = False,
    ) -> dict[str, Any]:
        """Run every EasyEDA audit and rank what they found.

        The one-call review, matching what ``design_review_snapshot``
        does on the Altium side. Otherwise a reviewer makes eighteen
        separate calls and has to remember which exist.

        THE COUNT IS READ, NEVER GUESSED. Each audit reports
        ``violation_count``; an audit that answers without it is listed
        under ``unreadable`` rather than counted as zero. That
        distinction is the whole point: a summariser that quietly reads
        a missing count as zero says "clean" about a board it never
        checked, which is worse than not summarising at all.

        Audits that REFUSE (no board open, nothing to measure) are
        separated from audits that ran and found nothing, because
        those two mean opposite things.

        Args:
            include_clean: also list the audits that ran and found
                nothing. Off by default: a review reads better as the
                problems, with the clean ones counted.

        Returns:
            {ok, audits_run, total_violations, findings: [{audit,
             violation_count}] worst first, clean: [names] or count,
             refused: [{audit, reason}], unreadable: [{audit, keys}]}.
        """
        findings: list[dict[str, Any]] = []
        clean: list[str] = []
        refused: list[dict[str, Any]] = []
        unreadable: list[dict[str, Any]] = []
        examined: dict[str, int] = {}

        # Share the reads across the whole review: four audits read
        # pcb.lines, several read pcb.vias, and on a live board that is
        # the same 813 segments fetched again and again. Cleared in the
        # finally below without exception, because a cache that
        # outlived the review would hand a later audit an older board.
        global _READ_CACHE
        outer_cache = _READ_CACHE
        _READ_CACHE = {}
        try:
            return await _review(_AUDITS, findings, clean, refused,
                                 unreadable, include_clean, examined)
        finally:
            _READ_CACHE = outer_cache

    async def _review(audits, findings, clean, refused, unreadable,
                      include_clean, examined):
        for name in sorted(audits):
            try:
                reply = await audits[name]()
            except Exception as exc:                   # noqa: BLE001
                # An audit that raises is a defect, but it must not
                # take the whole review down with it.
                unreadable.append({"audit": name,
                                   "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not isinstance(reply, dict):
                unreadable.append({"audit": name,
                                   "error": f"answered {type(reply).__name__}"})
                continue
            if reply.get("ok") is False:
                refused.append({"audit": name,
                                "reason": reply.get("reason")
                                or reply.get("unavailable") or "refused"})
                continue
            count = reply.get("violation_count")
            if not isinstance(count, int):
                unreadable.append({"audit": name,
                                   "keys": sorted(reply)[:8]})
                continue
            # How much this audit actually LOOKED at.
            #
            # "0 violations" is only good news if something was
            # examined. An empty read produces a perfectly clean review
            # of nothing, and the two are indistinguishable in the
            # summary. Twelve of the audits already report their scope;
            # the names are listed EXPLICITLY rather than matched on a
            # suffix, because a loose match on _count would have folded
            # in violation_count and called a clean 813-segment board
            # 813 problems.
            # The full set, curated by hand from what the audits
            # actually return. The first version listed four names taken
            # from a partial survey and silently under-counted: seven
            # audits report their scope as components_checked or
            # components_counted and contributed nothing.
            #
            # Curated rather than pattern-matched, because the near
            # misses are worse than the omissions. `minimum_pads` is a
            # THRESHOLD, so including it would add a constant to every
            # review. `nets_skipped_for_pours` is coverage LOST, the
            # opposite of examined. `blank_part_numbers`,
            # `components_without_pads` and `unnetted_vias` are
            # findings. Any suffix rule that caught the real ones would
            # catch several of those too.
            #
            # `examined` is on the list because it is the name this
            # very reply uses for the same quantity. Leaving it off
            # meant an audit that reported its scope under the obvious
            # name contributed nothing, and its coverage disappeared
            # from the summary while its findings stayed.
            for key in ("checked", "components_checked",
                        "components_counted", "examined",
                        "fields_checked", "nets_checked", "nets_counted",
                        "pads_checked", "pads_counted", "pairs_checked",
                        "segments_counted", "text_checked",
                        "vias_checked", "vias_counted"):
                value = reply.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    examined[name] = examined.get(name, 0) + value

            if count:
                findings.append({"audit": name, "violation_count": count})
            else:
                clean.append(name)

        findings.sort(key=lambda f: f["violation_count"], reverse=True)

        # audits_run counts the audits that actually PRODUCED a count.
        # It used to report len(_AUDITS), which is the size of the
        # registry and not a measurement of anything. With no editor
        # connected every audit refuses, and the reply read
        # "audits_run: 23, total_violations: 0" with the refusals
        # tucked below: a reviewer skimming the headline is told the
        # board is clean when not one check ran. The detail was always
        # correct and the summary contradicted it, which is the worst
        # arrangement of the two.
        ran = len(findings) + len(clean)
        total_examined = sum(examined.values())
        # A review where MOST audits refused is not a partial review,
        # it is not a review. On a schematic only a handful of these
        # audits apply, because nearly all of them read the board, and
        # reporting success with zero violations tells a reviewer the
        # design passed when most checks never ran.
        #
        # The threshold is deliberately not a percentage anyone has to
        # remember: if more audits refused than ran, the answer is
        # dominated by what could not be checked.
        # "Could not run here" is not "failed".
        #
        # On a schematic most of these audits refuse because they read
        # the board. Calling that a failed review is as wrong as
        # calling it a clean one: the first says the design passed when
        # nothing was checked, the second says something is broken when
        # the schematic review ran correctly and the board is simply
        # not open.
        #
        # The extension's refusal names the document mismatch exactly,
        # which is what makes this separable rather than guesswork.
        def _wrong_document(entry) -> bool:
            reason = str((entry or {}).get("reason") or "").lower()
            return ("needs a pcb document" in reason
                    or "needs a schematic document" in reason)

        not_applicable = [r for r in refused if _wrong_document(r)]
        genuine = [r for r in refused if not _wrong_document(r)]
        refused_more_than_ran = len(genuine) > ran
        out: dict[str, Any] = {
            "ok": ran > 0 and not refused_more_than_ran,
            "audits_run": ran,
            "audits_refused": len(genuine),
            "audits_not_applicable": len(not_applicable),
            "audits_total": len(_AUDITS),
            "total_violations": sum(f["violation_count"] for f in findings),
            "findings": findings,
            "clean_count": len(clean),
            "examined": total_examined,
            "examined_by_audit": examined,
            # EACH COUNT NAMES THE LIST IT COUNTS. Publishing the
            # unsplit list under "refused" put "audits_refused": 0 next
            # to a "refused" list of nineteen entries, which reads as a
            # reporting bug and makes a reader distrust the numbers that
            # are right.
            "refused": genuine,
            "not_applicable": not_applicable,
            "unreadable": unreadable,
        }
        if ran and refused_more_than_ran:
            out["reason"] = (
                f"only {ran} of {len(_AUDITS)} audits could run and "
                f"{len(genuine)} failed outright. This is NOT a clean "
                f"result: most checks never happened")
        if _UNAVAILABLE_AUDITS:
            out["unavailable_audits"] = {
                "mirrored_text": (
                    "board text cannot be read: every route to it "
                    "either hangs or is absent from the API"),
            }
        if not_applicable:
            # Said plainly and in the headline, because the number is
            # large and alarming on its own. Twenty-one skipped checks
            # sounds like a broken review until you know they are the
            # board checks and the board is not open.
            out["scope"] = (
                f"{len(not_applicable)} of {len(_AUDITS)} audits do not "
                f"apply to the open document and were not run. This is "
                f"a review of what is open, not of the whole design: "
                f"open the other document and run it again for the "
                f"rest")
        if ran and not total_examined:
            out["scope_warning"] = (
                "every audit that ran examined NOTHING, so a violation "
                "count of zero says the reads came back empty rather "
                "than that the design is sound. Check that the right "
                "document is open.")
        if include_clean:
            out["clean"] = clean
        if ran == 0:
            out["reason"] = (
                f"not one of the {len(_AUDITS)} audits could read the "
                f"board, so this is NOT a clean result: nothing was "
                f"checked. See 'refused' and 'unreadable' for why.")
        elif refused or unreadable:
            out["note"] = (
                f"{ran} of {len(_AUDITS)} audits produced a count; the "
                f"rest are under 'refused' or 'unreadable' and were NOT "
                f"counted as clean, which is a different thing")
        return out
