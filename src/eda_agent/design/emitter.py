# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SchematicCanvas -> Altium, one-shot batched IPC.

The emitter is intentionally dumb: it makes zero layout decisions and
zero geometry computations. Everything it writes was decided by the
pipeline / canvas. If the canvas has 9 components, 24 wires, 5 power
ports, the emitter makes one IPC call per kind to push them all to
Altium under one PreProcess/PostProcess each.

Compare to the legacy executor.py path: that file interleaves layout
decisions (where pin X is, what stub length to use, whether to consolidate
ports) with Altium IPC calls. The new pipeline does all of that in pure
Python; this module is just the transcription stage.

Failure modes the emitter surfaces (not hides):
- Project create/open failure -> stop, return result with `ok=False`.
- Sheet create/open failure -> stop, return result with `ok=False`.
- Bulk place reported a failed_refdes -> per-refdes EmitFailure record.
- Any bulk wire/label/port call raises -> note added, emit continues so
  partial-success is visible rather than silent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from eda_agent.design._wiring import _sheet_path
from eda_agent.bridge.payload import payload_safe
from eda_agent.design.canvas import SchematicCanvas, SymbolInstance

logger = logging.getLogger("eda_agent.design.emitter")


# Per-call timeouts. Generous because the first place call into a fresh
# SchLib can take 20-30s to populate the editor's component cache.
_PLACE_TIMEOUT_S = 60.0
_PARAM_TIMEOUT_S = 30.0
_BULK_TIMEOUT_S = 30.0
_SAVE_TIMEOUT_S = 60.0


@dataclass
class EmitFailure:
    """One Altium-reported failure during transcription."""

    refdes: str
    code: str
    reason: str


@dataclass
class EmitResult:
    project_path: str = ""
    ok: bool = True
    sheets_emitted: list[str] = field(default_factory=list)
    placed_refdes: list[str] = field(default_factory=list)
    wires_emitted: int = 0
    labels_emitted: int = 0
    power_ports_emitted: int = 0
    junctions_emitted: int = 0
    buses_emitted: int = 0
    bus_entries_emitted: int = 0
    failures: list[EmitFailure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Set by `emit_canvas_delta`: which components were added, moved,
    #: removed and left alone. Empty on a full emit, which touches all.
    delta: dict = field(default_factory=dict)


def emit_canvas(
    canvas: SchematicCanvas,
    project_path: str,
    bridge: Any,
    *,
    parameter_stamps: Optional[dict[str, dict[str, str]]] = None,
) -> EmitResult:
    """Transcribe a SchematicCanvas to a fresh-or-reused Altium project.

    Args:
        canvas: The fully populated SchematicCanvas from pipeline.
        project_path: Absolute path to the target .PrjPcb. Created if
            it does not exist.
        bridge: AltiumBridge (or anything exposing send_command).
        parameter_stamps: Optional {refdes: {param_name: value}} to stamp
            on each placed component after placement. Useful for
            Value / Manufacturer / MPN / Datasheet binding.

    Returns:
        EmitResult with one entry per sheet emitted and per failed refdes.
    """
    result = EmitResult(project_path=project_path)
    project = Path(project_path)

    _open_or_create_project(project, bridge, result)
    if not result.ok:
        return result

    for sheet in canvas.sheets:
        _emit_sheet(canvas, sheet.name, project, bridge, result,
                    parameter_stamps=parameter_stamps)

    try:
        bridge.send_command("application.save_all", {}, timeout=_SAVE_TIMEOUT_S)
        result.notes.append("save_all completed")
    except Exception as exc:
        result.ok = False
        result.notes.append(f"save_all failed: {exc}")

    return result


def _open_or_create_project(
    project: Path, bridge: Any, result: EmitResult
) -> None:
    """Open the .PrjPcb if it already exists, else create it."""
    if project.exists():
        try:
            bridge.send_command("project.open", {"project_path": str(project)})
            result.notes.append(f"Opened project: {project}")
        except Exception as exc:
            result.ok = False
            result.notes.append(f"project.open failed: {exc}")
        return
    try:
        bridge.send_command(
            "project.create",
            {"project_path": str(project), "project_type": "PCB"},
        )
        result.notes.append(f"Created project: {project}")
    except Exception as exc:
        result.ok = False
        result.notes.append(f"project.create failed: {exc}")


def _ensure_sheet_loaded(
    project: Path, sheet_name: str, bridge: Any, result: EmitResult
) -> Optional[Path]:
    """Open the sheet at its canonical path or create it; return the path."""
    sheet_path = _sheet_path(project, sheet_name)
    if sheet_path.exists():
        try:
            bridge.send_command(
                "application.run_process",
                {
                    "process_name": "WorkspaceManager:OpenObject",
                    "parameters": "ObjectKind=Document|FileName=" + str(sheet_path),
                },
            )
            # Says what was REQUESTED, not what happened. App_RunProcess
            # fires Altium's RunProcess and answers success
            # unconditionally, because RunProcess reports no status, so
            # "loaded" would be asserted on no evidence and a failed load
            # produced two contradictory notes. The outcome is checked one
            # step later: set_active_document refuses a document that is
            # not loaded (NOT_LOADED), which aborts this sheet.
            result.notes.append(f"Requested load of existing sheet: {sheet_path}")
        except Exception as exc:
            result.ok = False
            result.notes.append(f"OpenObject failed for {sheet_name}: {exc}")
            return None
        return sheet_path
    try:
        bridge.send_command(
            "application.create_document",
            {
                "kind": "SCH",
                "file_path": str(sheet_path),
                "name": sheet_name,
                "add_to_project": "true",
            },
        )
        result.notes.append(f"Created sheet: {sheet_path}")
    except Exception as exc:
        result.ok = False
        result.notes.append(f"create_document failed for {sheet_name}: {exc}")
        return None
    # create_document's add_to_project=true attaches the new doc to whatever
    # is the currently FOCUSED project. Right after project.create, the
    # focused project may still be "Free Documents" (or a previously open
    # project), so the new sheet ends up orphaned. Explicitly attach it
    # to OUR target project to guarantee membership; the call is a no-op
    # if the doc is already a member.
    try:
        bridge.send_command(
            "project.add_document",
            {
                "document_path": str(sheet_path),
                "project_path": str(project),
            },
        )
    except Exception as exc:
        # Not fatal -- the sheet is on disk; project.add_document may
        # have other names across Altium versions. Surface the warning
        # but keep going; the user can manually attach if needed.
        result.notes.append(
            f"explicit project.add_document failed for {sheet_name} -> "
            f"{project}: {exc}. If the sheet shows as 'Free Documents', "
            f"add it to the project manually or check the bridge handler."
        )
    return sheet_path


#: Object types that make up a sheet's COPPER, as opposed to its parts.
#: A delta emit clears and redraws these wholesale: they are cheap to
#: place, they carry no user-owned state (a wire has no designator, no
#: parameters, no lock), and identifying an individual segment through
#: the filter syntax is not reliable enough to diff them one by one.
_COPPER_TYPES = ("eWire", "eJunction", "eNetLabel", "ePowerObject",
                 "eBus", "eBusEntry")


def emit_canvas_delta(
    canvas: SchematicCanvas,
    snapshot: dict,
    project_path: str,
    bridge: Any,
    *,
    parameter_stamps: Optional[dict[str, dict[str, str]]] = None,
) -> EmitResult:
    """Bring an existing sheet to ``canvas``, touching only what differs.

    ``emit_canvas`` transcribes a whole canvas onto a fresh-or-reused
    project, which re-places every component. On a sheet somebody has
    worked on that throws away what makes the component theirs: locked
    designators, stamped parameters, hand-set properties. This moves the
    parts that moved, places the ones that are new, deletes the ones that
    went, and leaves every other component untouched.

    COPPER IS REDRAWN, NOT DIFFED. Wires, junctions, labels, ports, buses
    and bus entries are cleared and re-emitted from the canvas. They hold
    no user-owned state and the filter syntax cannot address one segment
    among many reliably, so a per-segment diff would be guesswork; a
    redraw is exact. The expensive, lossy half is the components, and
    that half is a true delta.

    ``snapshot`` is the ``canvas`` dict from ``<project>.canvas.json``,
    which is what says where each refdes was left.

    ONE SHEET ONLY. Every command here runs against the ACTIVE document,
    so the sheet is opened and activated first and a multi-sheet canvas
    is refused rather than half-applied to whichever sheet happened to
    be sheet zero.
    """
    result = EmitResult(project_path=project_path)
    if len(canvas.sheets) > 1:
        result.ok = False
        result.notes.append(
            f"delta emit takes one sheet; this canvas has "
            f"{len(canvas.sheets)}: "
            f"{', '.join(s.name for s in canvas.sheets)}"
        )
        return result
    sheet_name = canvas.sheets[0].name if canvas.sheets else "main"

    # Everything below filters on "active_doc". Without this the delta
    # lands on whatever document Altium happens to have focused, and
    # reports success for edits to a sheet nobody asked about.
    sheet_path = _ensure_sheet_loaded(Path(project_path), sheet_name,
                                      bridge, result)
    if sheet_path is None:
        return result
    try:
        bridge.send_command("application.set_active_document",
                            {"file_path": str(sheet_path)})
        result.sheets_emitted.append(sheet_name)
    except Exception as exc:                    # noqa: BLE001
        result.ok = False
        result.notes.append(f"set_active_document {sheet_name} failed: {exc}")
        return result

    # BOTH SIDES ARE FILTERED TO THIS SHEET. A project snapshot holds
    # every sheet's instances, and comparing all of them against one
    # sheet's canvas reports every part of every other sheet as removed.
    # The deletes that follows are scoped to the active document and so
    # would match nothing, but the delta would say parts went that are
    # still there. A snapshot instance with no sheet recorded is taken
    # as this one, which is what a single-sheet snapshot looks like.
    was = {
        i.get("refdes"): i
        for i in (snapshot or {}).get("instances", [])
        if i.get("refdes") and i.get("sheet", sheet_name) == sheet_name
    }
    now = {i.refdes: i for i in canvas.instances_on(sheet_name)}

    # A REFDES WHOSE SYMBOL CHANGED IS NOT THE SAME PART. The plan can
    # swap what R1 is, and moving the old symbol to the new coordinate
    # would leave the sheet holding a part the plan no longer contains,
    # wired as if it were the new one. Replace it instead.
    def _swapped(refdes: str) -> bool:
        old, new = was[refdes], now[refdes]
        return (str(old.get("lib_ref", "")) != new.symbol.lib_ref
                or str(old.get("lib_path", "")) != new.symbol.lib_path)

    both = set(now) & set(was)
    swapped = sorted(r for r in both if _swapped(r))
    added = [now[r] for r in sorted((set(now) - set(was)) | set(swapped))]
    removed = sorted((set(was) - set(now)) | set(swapped))
    both -= set(swapped)
    moved = [
        now[r] for r in sorted(both)
        if (int(was[r].get("x", 0)) != now[r].x
            or int(was[r].get("y", 0)) != now[r].y
            or int(was[r].get("rotation", 0)) % 360 != now[r].rotation % 360)
    ]

    for refdes in removed:
        try:
            bridge.send_command("generic.delete_objects", {
                "scope": "active_doc", "object_type": "eSchComponent",
                "filter": f"Designator={refdes}"})
        except Exception as exc:                # noqa: BLE001
            result.ok = False
            result.failures.append(EmitFailure(
                refdes=refdes, code="DELETE_FAILED",
                reason=f"delete raised: {exc}"))

    for inst in moved:
        # ORIENTATION FIRST, LOCATION SECOND, as two ops. Measured on a
        # pin: a combined `Location.X=..|Orientation=..` applied the
        # location and dropped the orientation, because writing Location
        # triggers a re-layout that can snapshot the previous value.
        # SetSchProperty in Generic.pas carries that note and prescribes
        # the split; ApplySetProperties still coalesces Location into a
        # first pass and applies everything else after it, which is the
        # unsafe order. Both ops here filter on the designator, which
        # does not change, so neither has to chase a moved coordinate.
        try:
            if int(was[inst.refdes].get("rotation", 0)) % 360 != inst.rotation % 360:
                bridge.send_command("generic.modify_objects", {
                    "scope": "active_doc", "object_type": "eSchComponent",
                    "filter": f"Designator={inst.refdes}",
                    "set": f"Orientation={(inst.rotation % 360) // 90}"})
            bridge.send_command("generic.modify_objects", {
                "scope": "active_doc", "object_type": "eSchComponent",
                "filter": f"Designator={inst.refdes}",
                "set": f"Location.X={inst.x}|Location.Y={inst.y}"})
        except Exception as exc:                # noqa: BLE001
            result.ok = False
            result.failures.append(EmitFailure(
                refdes=inst.refdes, code="MOVE_FAILED",
                reason=f"move raised: {exc}"))

    if added:
        _emit_placements(added, bridge, result)
    # Stamp only the parts that are NEW. A part that merely moved keeps
    # whatever parameters it has on the sheet, which is the point of a
    # delta: re-stamping would overwrite a value somebody edited by hand.
    if added and parameter_stamps:
        _emit_parameter_stamps(added, parameter_stamps, sheet_path, bridge,
                               result)
    # TEXT POSITIONS FOLLOW THE MOVE, unlike the stamps. Gen_SetSchTextPositions
    # writes the designator and comment at an ABSOLUTE sheet coordinate with
    # Autoposition off, so a part that moves leaves its own designator behind
    # on the sheet until this runs for it too.
    if added or moved:
        _emit_text_positions(added + moved, sheet_path, bridge, result)

    for object_type in _COPPER_TYPES:
        try:
            # An empty filter matches every object of the type, which
            # is what clearing the copper means. confirm_delete_all is
            # a guard on the TOOL, not a parameter the handler reads;
            # sending it here only looked like a safety belt.
            bridge.send_command("generic.delete_objects", {
                "scope": "active_doc", "object_type": object_type,
                "filter": ""})
        except Exception as exc:                # noqa: BLE001
            result.failures.append(EmitFailure(
                refdes="", code="CLEAR_FAILED",
                reason=f"clearing {object_type} raised: {exc}"))

    _emit_wires(list(canvas.wires_on(sheet_name)), bridge, result, sheet_name)
    _emit_labels([l for l in canvas.labels if l.sheet == sheet_name],
                 bridge, result, sheet_name)
    _emit_power_ports([p for p in canvas.power_ports if p.sheet == sheet_name],
                      bridge, result, sheet_name)
    _emit_junctions([j for j in canvas.junctions if j.sheet == sheet_name],
                    bridge, result, sheet_name)
    _emit_buses([b for b in canvas.buses if b.sheet == sheet_name],
                bridge, result, sheet_name)
    _emit_bus_entries([e for e in canvas.bus_entries if e.sheet == sheet_name],
                      bridge, result, sheet_name)

    result.delta = {
        "added": [i.refdes for i in added],
        "moved": [i.refdes for i in moved],
        "removed": removed,
        "untouched": sorted(both - {i.refdes for i in moved}),
        "replaced": swapped,
    }
    return result


def _emit_sheet_size(
    canvas: SchematicCanvas, sheet_name: str, bridge: Any, result: EmitResult
) -> None:
    """Put the plan's paper on the document before anything is drawn.

    THE PLACER ALREADY SIZED TO IT. sheet_bounds spreads a layout across
    the sheet the plan declares, so a plan asking for A3 is laid out to
    A3 and, on a document nobody resized, drawn past the edge of an A4
    frame. Nothing failed and nothing said so: the parts are all there,
    the netlist is right, and the border is in the wrong place.

    Set BEFORE placing, because the size is a document property and
    changing it afterwards would move the frame under parts already
    positioned against it.

    A4 is skipped because it is what a new document already is, which
    keeps the common case at zero extra calls, and a failure is a note
    rather than an abort: the sheet is still correct, only its border is
    the wrong size.
    """
    sheet = next((s for s in canvas.sheets if s.name == sheet_name), None)
    size = (getattr(sheet, "size", "") or "").strip()
    if not size or size.upper() == "A4":
        return
    try:
        bridge.send_command("generic.set_sheet_size", {"style": size})
        result.notes.append(f"sheet {sheet_name}: size set to {size}")
    except Exception as exc:                    # noqa: BLE001
        result.notes.append(
            f"sheet {sheet_name}: could not set size to {size} ({exc}); the "
            f"layout was computed for {size} and the border is still the "
            f"document default")


def _emit_sheet(
    canvas: SchematicCanvas,
    sheet_name: str,
    project: Path,
    bridge: Any,
    result: EmitResult,
    *,
    parameter_stamps: Optional[dict[str, dict[str, str]]] = None,
) -> None:
    """Emit one sheet's worth of components, wires, labels, ports, junctions."""
    sheet_path = _ensure_sheet_loaded(project, sheet_name, bridge, result)
    if sheet_path is None:
        return
    try:
        bridge.send_command(
            "application.set_active_document", {"file_path": str(sheet_path)}
        )
        result.sheets_emitted.append(sheet_name)
    except Exception as exc:
        result.notes.append(f"set_active_document {sheet_name} failed: {exc}")
        return

    _emit_sheet_size(canvas, sheet_name, bridge, result)

    # 1. Bulk place components.
    instances = canvas.instances_on(sheet_name)
    if instances:
        _emit_placements(instances, bridge, result)
    # 2. Stamp Value / Manufacturer / MPN / Footprint / etc.
    if instances and parameter_stamps:
        _emit_parameter_stamps(
            instances, parameter_stamps, sheet_path, bridge, result
        )
        _emit_text_positions(instances, sheet_path, bridge, result)
    # 3. Bulk wires.
    wires = canvas.wires_on(sheet_name)
    if wires:
        _emit_wires(wires, bridge, result, sheet_name)
    # 3b. Buses + bus entries (before labels, so the bus exists when the
    # per-signal net labels are placed on the entries' wire stubs).
    buses = canvas.buses_on(sheet_name)
    if buses:
        _emit_buses(buses, bridge, result, sheet_name)
    bus_entries = canvas.bus_entries_on(sheet_name)
    if bus_entries:
        _emit_bus_entries(bus_entries, bridge, result, sheet_name)
    # 4. Bulk junctions (after wires).
    junctions = canvas.junctions_on(sheet_name)
    if junctions:
        _emit_junctions(junctions, bridge, result, sheet_name)
    # 5. Bulk labels.
    labels = canvas.labels_on(sheet_name)
    if labels:
        _emit_labels(labels, bridge, result, sheet_name)
    # 6. Bulk power ports.
    ports = canvas.power_ports_on(sheet_name)
    if ports:
        _emit_power_ports(ports, bridge, result, sheet_name)


def _emit_placements(
    instances: list[SymbolInstance], bridge: Any, result: EmitResult
) -> None:
    ops: list[str] = []
    for inst in instances:
        ops.append(
            f"library_path={inst.symbol.lib_path};"
            f"lib_reference={inst.symbol.lib_ref};"
            f"x={inst.x};y={inst.y};"
            f"designator={inst.refdes};"
            f"rotation={inst.rotation};"
            f"footprint="
        )
    try:
        resp = bridge.send_command(
            "generic.place_sch_components_from_library",
            {"placements": "~~".join(ops)},
            timeout=_PLACE_TIMEOUT_S * max(1, len(ops) // 4),
        )
    except Exception as exc:
        for inst in instances:
            result.failures.append(EmitFailure(
                refdes=inst.refdes, code="PLACE_FAILED",
                reason=f"bulk place raised: {exc}",
            ))
        return
    # Parse Pascal's failed_refdes (bug #86 fix). Pascal returns
    # "DESIG:CODE,DESIG:CODE" for any place op that silently skipped.
    failed_map: dict[str, str] = {}
    if isinstance(resp, dict):
        raw = str(resp.get("failed_refdes", "") or "")
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                rd, code = entry.split(":", 1)
                failed_map[rd.strip()] = code.strip() or "PLACE_FAILED"
            else:
                failed_map[entry] = "PLACE_FAILED"
    for inst in instances:
        if inst.refdes in failed_map:
            result.failures.append(EmitFailure(
                refdes=inst.refdes,
                code=failed_map[inst.refdes],
                reason=(
                    f"Pascal place handler skipped "
                    f"lib_ref={inst.symbol.lib_ref!r} from "
                    f"lib_path={inst.symbol.lib_path!r}"
                ),
            ))
        else:
            result.placed_refdes.append(inst.refdes)


def _emit_parameter_stamps(
    instances: list[SymbolInstance],
    parameter_stamps: dict[str, dict[str, str]],
    sheet_path: Path,
    bridge: Any,
    result: EmitResult,
) -> None:
    ops: list[str] = []
    for inst in instances:
        stamps = parameter_stamps.get(inst.refdes)
        if not stamps:
            continue
        # A third variant of the payload sanitiser used to live here: it
        # substituted ";" but not "~~", and left the designator raw. A
        # plan-authored parameter value carrying "~~" would therefore
        # end its operation early and forge an extra stamp -- silently,
        # because the payload stays syntactically valid. One shared rule
        # now, from the bridge layer that owns the grammar.
        fields = [f"designator={payload_safe(inst.refdes)}"]
        for k, v in stamps.items():
            if not k or v is None:
                continue
            vs = str(v).strip()
            if not vs:
                continue
            fields.append(f"{payload_safe(k)}={payload_safe(vs)}")
        if len(fields) > 1:
            ops.append(";".join(fields))
    if not ops:
        return
    try:
        bridge.send_command(
            "generic.set_sch_components_parameters",
            {"stamps": "~~".join(ops), "sheet_path": str(sheet_path)},
            timeout=_PARAM_TIMEOUT_S * max(1, len(ops) // 8),
        )
    except Exception as exc:
        result.notes.append(f"parameter stamp pass failed: {exc}")
        return

    # Discipline rule 16: Manufacturer, Manufacturer Part Number, and
    # Datasheet must be invisible on the schematic body by default --
    # only Designator + Value are user-facing. The stamping API creates
    # the parameters with default visibility, so we sweep the placed
    # sheet right after stamping and flip IsHidden=true on those three
    # parameter names across every component.
    _DISCIPLINE_HIDDEN_PARAMS = (
        "Manufacturer",
        "Manufacturer Part Number",
        "Datasheet",
    )
    for param_name in _DISCIPLINE_HIDDEN_PARAMS:
        try:
            bridge.send_command(
                "generic.modify_objects",
                {
                    "object_type": "eParameter",
                    "scope": f"doc:{sheet_path}",
                    "filter": f"Name={param_name}",
                    "set": "IsHidden=true",
                },
                timeout=_PARAM_TIMEOUT_S,
            )
        except Exception as exc:
            result.notes.append(
                f"hide-{param_name} pass failed: {exc}"
            )


def _emit_wires(
    wires: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    payload = "~~".join(
        f"x1={w.x1};y1={w.y1};x2={w.x2};y2={w.y2}" for w in wires
    )
    try:
        bridge.send_command(
            "generic.place_wires",
            {"wires": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(wires) // 8),
        )
        result.wires_emitted += len(wires)
    except Exception as exc:
        result.notes.append(f"place_wires for {sheet_name} failed: {exc}")


def _emit_junctions(
    junctions: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    payload = "~~".join(f"x={j.x};y={j.y}" for j in junctions)
    try:
        bridge.send_command(
            "generic.place_junctions",
            {"junctions": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(junctions) // 8),
        )
        result.junctions_emitted += len(junctions)
    except Exception as exc:
        result.notes.append(f"place_junctions for {sheet_name} failed: {exc}")


def _emit_buses(
    buses: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    # The Altium bridge exposes single-object place_bus / place_bus_entry (no
    # bulk variant). Buses are few (one short line per IC bus stub), so the
    # per-object loop is fine. Stop on the first failure so a broken bridge
    # doesn't spam one note per segment.
    for b in buses:
        try:
            bridge.send_command(
                "generic.place_bus",
                {"x1": str(b.x1), "y1": str(b.y1),
                 "x2": str(b.x2), "y2": str(b.y2)},
                timeout=_BULK_TIMEOUT_S,
            )
            result.buses_emitted += 1
        except Exception as exc:
            result.notes.append(f"place_bus for {sheet_name} failed: {exc}")
            break


def _emit_bus_entries(
    entries: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    for e in entries:
        try:
            bridge.send_command(
                "generic.place_bus_entry",
                {"x1": str(e.x1), "y1": str(e.y1),
                 "x2": str(e.x2), "y2": str(e.y2)},
                timeout=_BULK_TIMEOUT_S,
            )
            result.bus_entries_emitted += 1
        except Exception as exc:
            result.notes.append(
                f"place_bus_entry for {sheet_name} failed: {exc}")
            break


def _emit_text_positions(
    instances: list, sheet_path: Path, bridge: Any, result: EmitResult
) -> None:
    """Mirror text_placement's collision-free annotation anchors onto the
    live sheet (Designator / Comment sub-object Locations). Skips
    instances the pass left at defaults."""
    ops: list[str] = []
    for inst in instances:
        dp = getattr(inst, "designator_pos", None)
        if dp is None:
            continue
        fields = [
            f"designator={payload_safe(inst.refdes)}",
            f"dx={dp[0]}", f"dy={dp[1]}",
        ]
        vp = getattr(inst, "value_pos", None)
        if vp is not None:
            fields.append(f"vx={vp[0]}")
            fields.append(f"vy={vp[1]}")
        ops.append(";".join(fields))
    if not ops:
        return
    try:
        bridge.send_command(
            "generic.set_sch_text_positions",
            {"positions": "~~".join(ops), "sheet_path": str(sheet_path)},
            timeout=_PARAM_TIMEOUT_S * max(1, len(ops) // 8),
        )
    except Exception as exc:
        result.notes.append(f"text position pass failed: {exc}")


def _emit_labels(
    labels: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    payload = "~~".join(
        f"text={l.text};x={l.x};y={l.y};orientation={l.orientation}"
        f";justification={getattr(l, 'justification', 0)}"
        for l in labels
    )
    try:
        bridge.send_command(
            "generic.place_net_labels",
            {"labels": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(labels) // 8),
        )
        result.labels_emitted += len(labels)
    except Exception as exc:
        result.notes.append(f"place_net_labels for {sheet_name} failed: {exc}")


def _emit_power_ports(
    ports: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    # Power-port orientation: VCC up (1), GND down (3), determined per-glyph.
    payload = "~~".join(
        f"text={p.text};x={p.x};y={p.y};style={p.style};orientation={3 if 'gnd' in p.style.lower() else 1}"
        for p in ports
    )
    try:
        bridge.send_command(
            "generic.place_power_ports",
            {"ports": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(ports) // 8),
        )
        result.power_ports_emitted += len(ports)
    except Exception as exc:
        result.notes.append(f"place_power_ports for {sheet_name} failed: {exc}")
