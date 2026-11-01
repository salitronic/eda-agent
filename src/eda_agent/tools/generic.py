# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Generic primitive tools for Altium Designer MCP Server.

These primitives provide a thin, generic interface to Altium objects.
All intelligence lives in the Python/MCP side, the DelphiScript is just
a pass-through layer for object iteration, property access, and process execution.
"""

import difflib
import re
from typing import Any, Optional
from ..bridge.payload import payload_safe
from ..bridge import get_bridge
from ..safety import refuse_process, untrusted_text_note
from .library import _mils
from .pin_hints import pin_location_hint
from ..scope import to_wire as scope_to_wire
from .bulk_hints import BulkHintTracker
from .editor_options import run_options


#: Every key ``sch_place_components`` reads from a placement dict.
#:
#: A KEY OUTSIDE THIS SET IS A CALLER MISTAKE AND MUST NOT BE IGNORED.
#: Dropping it silently is how a single typo became hours of debugging in
#: the field on 2026-09-21: ``source_library`` was passed instead of
#: ``library_path``, the field went missing, the Pascal received an empty
#: library path, and LoadComponentFromLibrary returned Nil -- reported as
#: ``LOAD_FAILED``, which names the LIBRARY. Four spellings of the path,
#: two sheets, an extracted .SchLib and the native Place Part dialog were
#: all tried against an error whose real cause was the key name, because
#: nothing anywhere said the field had not arrived.
#:
#: The tool's own arguments are already protected: an unknown top-level
#: argument is rejected by the schema. Only the contents of these dicts
#: were unchecked.
PLACEMENT_KEYS = frozenset({
    "library_path",
    "lib_reference",
    "x",
    "y",
    "rotation",
    "designator",
    "footprint",
    "part_id",
})


#: The keys a managed placement dict is read for.
MANAGED_PLACEMENT_KEYS = frozenset({
    "design_item_id",
    "x",
    "y",
    "rotation",
    "designator",
})


def unknown_placement_keys(
    placements: list[dict[str, Any]],
    valid: frozenset[str] = PLACEMENT_KEYS,
) -> dict[str, Optional[str]]:
    """``{unrecognised_key: nearest_valid_key_or_None}``.

    The suggestion matters more than the rejection: ``source_library``
    resolves to ``library_path`` and ``lib_ref`` to ``lib_reference``,
    which are exactly the two mistakes that have actually been made.
    """
    found: dict[str, Optional[str]] = {}
    for item in placements:
        if not isinstance(item, dict):
            continue
        for key in item:
            if key in valid or key in found:
                continue
            near = difflib.get_close_matches(
                str(key), sorted(valid), n=1, cutoff=0.5)
            found[key] = near[0] if near else None
    return found


def _unknown_keys_refusal(placements: list[dict[str, Any]],
                          valid: frozenset[str],
                          consequence: str) -> Optional[dict[str, Any]]:
    """The reply for placement dicts with keys nothing reads, or None."""
    unknown = unknown_placement_keys(placements, valid)
    if not unknown:
        return None
    described = [
        f"{key!r}" + (f" (did you mean {near!r}?)" if near else "")
        for key, near in sorted(unknown.items())]
    return {
        "error": "UNKNOWN_PLACEMENT_KEYS",
        "reason": (
            "placement dicts carry keys this tool does not read, and acting "
            f"on the rest would {consequence}: " + ", ".join(described)),
        "valid_keys": sorted(valid),
        "placed": 0,
        "failed": 0,
        "total": len(placements),
    }


async def _focus_sheet(bridge: Any,
                       document_path: Optional[str]) -> Optional[dict[str, Any]]:
    """Make ``document_path`` the active sheet; the reply if that failed.

    Placement lands on the active schematic, and a sheet just created is
    not focused, so without this the parts can land on another open one.
    """
    if not document_path:
        return None
    focus = await bridge.send_command_async(
        "application.set_active_document", {"file_path": document_path})
    if isinstance(focus, dict) and not focus.get("success", True):
        return {
            "error": "FOCUS_FAILED",
            "reason": f"could not focus {document_path} before placement; "
                      "aborting to avoid placing on the wrong sheet",
            "focus_result": focus,
            "placed": 0,
        }
    return None


#: How many components in one call count as "drawing a sheet" rather than
#: editing one. Four is the smallest group that is unmistakably a layout:
#: an IC with three passives around it.
_MANUAL_LAYOUT_BULK = 4

#: How many components already on the sheet still count as empty. Not zero,
#: because a sheet often carries a connector or a mounting hole placed
#: before the real work starts, and refusing there would be wrong.
_MANUAL_LAYOUT_EMPTY = 2


async def _refuse_manual_sheet_layout(
    bridge,
    n_placements: int,
    document_path: "Optional[str]",
    allow_manual_layout: bool,
) -> "Optional[dict[str, Any]]":
    """Refuse a bulk placement that is really a schematic being hand-drawn.

    An LLM asked to draw a schematic reaches for this tool and places
    parts one call at a time, then wires them segment by segment, which
    produces a netlist-correct sheet that reads like nothing a person
    would draw. sch_draw_template draws a planned block with the pin ends
    worked out and the wiring checked, so the refusal names it. Returns
    the refusal payload, or None to let the placement proceed.

    DOES NOT BLOCK THE TEMPLATE TOOL. ``sch_draw_template`` sends
    ``generic.place_sch_components_from_library`` directly, not through
    this tool, so the gate cannot cut off the path it is pointing at.

    Only fires on a BULK placement into an EMPTY sheet: adding parts to a
    sheet that already has work on it is ordinary editing, and one or two
    parts is never a layout. The count costs a round trip, so it is only
    asked for when the bulk threshold is already met.

    Fails OPEN. If the count cannot be read the placement goes ahead: a
    gate that blocks real work because an unrelated bridge call failed
    would be worse than the drawing it is trying to prevent.
    """
    if allow_manual_layout or n_placements < _MANUAL_LAYOUT_BULK:
        return None
    scope = f"doc:{document_path}" if document_path else "active_doc"
    try:
        counted = await bridge.send_command_async(
            "generic.get_object_count",
            {"object_type": "eSchComponent",
             "scope": scope_to_wire(scope), "filter": ""},
        )
    except Exception:                       # noqa: BLE001 - fail open
        return None
    if not isinstance(counted, dict):
        return None
    existing = counted.get("count")
    if not isinstance(existing, int) or existing > _MANUAL_LAYOUT_EMPTY:
        return None
    return {
        "error": (
            f"refused: this places {n_placements} components onto a sheet "
            f"that has {existing}, which is a schematic being drawn by "
            f"hand, part by part. Draw a sheet from a template instead: "
            f"plan every part, wire, port and label on the grid, check it "
            f"with sch_preview_template and draw it with "
            f"sch_draw_template, which works out the pin ends and refuses "
            f"a short before wiring. To place these parts on their own "
            f"anyway, pass allow_manual_layout=true."
        ),
        "refused": True,
        "placed": 0,
        "use_instead": "sch_draw_template",
        "existing_components": existing,
    }


_FILTER_NAME = re.compile(r"[A-Za-z0-9_.]+")
# Name, operator, value. The operator is the first = ! < > or ~ (names hold
# none), as the script's FilterOpPos reads it; a lone '!' is no operator.
_FILTER_COND = re.compile(r"([^=!<>~]*)(!=|!~|<=|>=|=|<|>|~)(.*)", re.S)


def _merge_items(items: Any, shared: dict[str, Any]) -> Any:
    """The items of a list call with the arguments given beside them;
    an item's own value wins. A refusal dict when items is not a list of
    dicts."""
    if not isinstance(items, list) or not items:
        return {"ok": False, "reason": "items must be a non-empty list of "
                                       "argument dicts"}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return {"ok": False, "reason": f"items[{i}] is not a dict of "
                                           "arguments; nothing was sent"}
    return [{**shared, **item} for item in items]


def filter_problem(filt: str) -> str:
    """The first condition of ``filt`` that is not Name<op>Value, or ''.

    A filter is conditions joined by ``|`` and nothing else, each Name=Value,
    Name!=Value, a numeric Name<Value, >, <=, >=, or a wildcard Name~pattern /
    Name!~pattern (* any run, ? one character, case ignored). The script used to skip
    a condition it could not parse, which emptied the filter: obj_delete
    given an Altium query expression removed every track on a board and
    reported success. The script now refuses such a filter too; this says
    so before anything is sent.
    """
    for cond in str(filt or "").split("|"):
        if not cond.strip():
            continue
        m = _FILTER_COND.fullmatch(cond)
        if not m or not _FILTER_NAME.fullmatch(m.group(1)):
            return cond
    return ""


def bad_filter(cond: str, **extra: Any) -> dict[str, Any]:
    """The refusal for a filter condition that is not Name=Value."""
    hint = ""
    if _FILTER_NAME.fullmatch(cond.strip()):
        hint = f" For a true/false property write {cond.strip()}=true."
    return {"error": f'Filter condition "{cond}" is not Name=Value. A filter is '
                     "Name=Value conditions joined by |, each matched exactly "
                     "(also Name!=Value, Name<Value, >, <=, >= on numbers, and "
                     "Name~pattern / Name!~pattern with * and ? wildcards); Altium "
                     "query expressions (OnLayer(...), Or, And) and bare names are "
                     f"not understood.{hint} Nothing was sent.", **extra}


def _mark_erc_page(result: Any) -> Any:
    """Say plainly when an ERC list is only part of the violations.

    The list used to stop at 100 with nothing on the reply to say so: 240
    violations came back as violation_count 240 and 100 entries. A script
    from before 2026.10.21 sends no ``returned`` and ignores ``offset``,
    so the page is worked out here from what it did send.
    """
    if not isinstance(result, dict) or "violation_count" not in result:
        return result
    items = result.get("violations")
    if not isinstance(items, list):
        return result
    if "returned" not in result:
        result["returned"] = len(items)
        result["truncated"] = len(items) < int(result["violation_count"] or 0)
    if result.get("truncated"):
        result["warning"] = (
            f"{result['returned']} of {result['violation_count']} violations "
            "returned. Pass limit=0 for all of them, or offset=next_offset "
            "for the next page.")
    return result


async def _reopen_sheet(bridge) -> dict[str, Any]:
    """Save, close and reopen the active sheet, and bring it back to front.

    MEASURED on AD 26.11: after a sheet size change the ERC still judged
    the sheet against its old frame ("Off sheet Net Label PROBE at
    6000mil,9500mil" on an A3 sheet that had been A4), through saves and
    forced recompiles, until the document was closed and opened again.
    A demo run then switched the check off to get a clean ERC.
    """
    from ..bridge.exceptions import AltiumError

    try:
        active = await bridge.send_command_async("application.get_active_document", {})
        path = str((active or {}).get("file_path") or "") if isinstance(active, dict) else ""
        if not path.lower().endswith(".schdoc"):
            return {"done": False, "reason": "the active document is not a saved sheet"}
        await bridge.send_command_async("application.save_document", {"file_path": path},
                                        timeout=60)
        await bridge.send_command_async("application.close_document", {"file_path": path},
                                        timeout=60)
        await bridge.send_command_async("application.run_process", {
            "process_name": "WorkspaceManager:OpenObject",
            "parameters": f"ObjectKind=Document|FileName={path}"})
        await bridge.send_command_async("application.set_active_document", {"file_path": path})
        return {"done": True, "file_path": path,
                "why": "Altium's ERC keeps a sheet's old frame until it is reopened"}
    except AltiumError as exc:
        return {"done": False, "reason": str(exc)}


#: Schematic editor preferences: argument name to Altium's.
_SCH_EDITOR_OPTIONS = {
    "snap_to_electrical_grid": "SnapToHotSpot",
    "electrical_grid_distance": "HotSpotGridDistance",
    "drag_orthogonal": "MaintainOrthogonal",
    "components_cut_wires": "ComponentsCutWires",
    "optimize_wires": "OptimizePolylines",
    "single_slash_negation": "SingleSlashNegation",
    "convert_special_strings": "ConvertSpecialStrings",
    "mark_manual_parameters": "MarkManualParameters",
    "show_pin_direction": "ShowPinDirection",
    "auto_zoom": "AutoZoom",
    "click_clears_selection": "ClickClearsSelection",
    "double_click_runs_inspector": "DoubleClickRunsInspector",
    "undo_levels": "UndoRedoStackSize",
    "pin_name_margin": "PinNameMargin",
    "pin_number_margin": "PinNumberMargin",
    "multi_part_naming": "MultiPartNamingMethod",
    "increment_primary": "StringIncA",
    "increment_secondary": "StringIncB",
    "power_ground_name": "DefaultPowerGndName",
    "signal_ground_name": "DefaultSignalGndName",
    "earth_name": "DefaultEarthName",
    "expand_designators": "ExpandComponentDesignators",
    "expand_net_labels": "ExpandNetLabelsText",
    "expand_ports": "ExpandPortsName",
    "expand_sheet_number": "ExpandSheetNumber",
    "expand_document_number": "ExpandDocumentNumber",
    "watermark_read_only_sheets": "WatermarkReadOnlySheet",
    "always_drag": "AlwaysDrag",
    "use_printer_fonts": "DisplayPrinterFonts",
    "default_template": "DefaultTemplateFileName",
    "increment_drops_leading_zero": "StringIncRemoveLeadingZero",
    "ctrl_double_click_goes_down": "CtrlDbleClickGoesDown",
    "confirm_selection_memory_clear": "ConfirmSelectionMemoryClear",
    "allow_device_sheet_editing": "AllowDeviceSheetEditing",
    "watermark_device_sheets": "WatermarkDeviceSheet",
    "cutter_grid_multiple": "CutterGridSizeMultiple",
    "cutter_fixed_length": "CutterFixedLength",
    "physical_view_dim_level": "PhysicalViewDimLevel",
}

#: The schematic options given in mils (Altium keeps 10000 to the mil).
_SCH_MARGINS = ("pin_name_margin", "pin_number_margin", "cutter_fixed_length")

#: Measured on AD 26.11: on a port both read
#: back at once, then the saved and reopened sheet had OverrideDisplayString
#: empty and HarnessColor at its default; AutoSize kept.
_PORT_FIELDS_DO_NOT_SAVE = (
    "OverrideDisplayString and HarnessColor read back on the port now, but do "
    "not survive saving and reopening the sheet (AD 26.11). AutoSize does.")


def register_generic_tools(mcp):
    """Register generic primitive tools with the MCP server."""

    @mcp.tool()
    async def obj_query(
        object_type: str,
        properties: str,
        scope: str = "active_doc",
        filter: str = "",
        limit: int = 0,
    ) -> dict[str, Any]:
        """Query schematic OR PCB objects and read their properties.

        Args:
            object_type: Altium object type constant.
                Schematic: "eNetLabel", "ePort", "ePowerObject", "eSchComponent",
                "eWire", "eBus", "eBusEntry", "eParameter", "ePin",
                "eLabel", "eLine", "ePolyline", "eRectangle", "eSheetSymbol", "eSheetEntry",
                "eNoERC", "eJunction"; symbol graphics as "sch_polygon",
                "sch_arc", "sch_ellipse", "sch_elliptical_arc", "sch_bezier"
                (plain "polygon" and "arc" are the board's), the bare
                "ellipse", "elliptical_arc" and "bezier", and "eDesignator" and
                "eRoundRectangle"
                PCB: "eTrackObject", "ePadObject", "eViaObject", "eComponentObject",
                "eArcObject", "eFillObject", "eTextObject", "eRuleObject", "eDimensionObject",
                "eSplitPlaneObject" (a plane layer's regions, with Net
                and Layer),
                "eConnectionObject" (the ratsnest: one line per unrouted
                connection, with X1, Y1, X2, Y2, Layer1, Layer2 and Net)
            properties: Comma-separated property names to return.

                SCHEMATIC objects use the dotted spelling:
                "Text,Location.X,Location.Y" for net labels;
                "Designator,Name,ConnectionX,ConnectionY" for pins
                (ConnectionX/Y is the ELECTRICAL end a wire must touch;
                Location is the body-side root);
                "Designator.Text,Comment.Text,LibReference" for
                components, and "DisplayMode,DisplayModeCount" (the
                alternate drawing, settable with obj_modify) and
                "FootprintPinMap" (the current footprint model's pin map,
                "(G:1),(D:2),(S:3)"; obj_modify also takes "G:1;D:2" and
                merges by schematic pin, so a map cannot be cleared).
                "TextFontId" is a sheet or harness entry's label font. A
                managed component also reads
                "LibraryIdentifier,DesignItemId,ItemGUID,RevisionGUID"
                and the other Vault/Symbol GUIDs.

                PCB PRIMITIVES ARE FLAT, NOT DOTTED: a track's net is
                "Net", never "Net.Name". "Net,Layer,Width" for tracks;
                "Net,Layer,HoleSize,Size" for vias;
                "Designator,Comment,Pattern,Rotation,X,Y" for
                components; "Text,Height,StrokeWidth,IsDesignator" for
                texts, and "MirrorFlag" (settable with obj_modify; the text
                is kept where it is). Polygons ("polygon") read and, with
                obj_modify, set their pour settings: "Name,Grid,TrackSize,
                MinTrack,ArcApproximation,NeckWidthThreshold" (decimal
                mils), "IslandAreaThreshold" (square mils),
                "PolyHatchStyle,PourOver" (raw ordinals), "UseOctagons",
                and the read-only "PourIndex,Poured"; nothing repours.
                An unknown PCB property is refused with the valid
                list. Lengths are decimal mils.

                A board type returns EVERY primitive of that type;
                filter "InComponent=false|InPolygon=false" for free
                copper. Mechanical-layer tracks have no net.
            scope: Document scope:
                "active_doc", current sheet only (default)
                "project", all SCH sheets in focused project
                "doc:C:\\path\\to\\Sheet.SchDoc", specific sheet by path (no focus change)
                "project:C:\\path\\to\\Project.PrjPcb", specific project by path
                "lib_component:NAME", a named symbol in the active SchLib
                "lib_component:NAME@3", part 3 of a MULTI-PART symbol:
                    without the suffix only part 1 is seen, so query each
                    part. OwnerPartId tells which part a primitive is on
                    (0 = shared).
            filter: Pipe-separated property=value conditions (AND), e.g.
                "Text=VCC" or "Designator.Text=R1"; "" matches all. A PCB
                length matches as a number in mils ('.' or ',' decimal).
                "Net!=GND" excludes; "Y>4250|Y<=5000" compares numbers
                (a value that is not a number matches no < > <= >=);
                "Name~POUR_*" matches a wildcard pattern (* any run, ? one
                character, case ignored), "Net!~VCC*" excludes by one. The
                same grammar holds for obj_modify, obj_delete and obj_select.
            limit: Maximum number of objects to return (0 = unlimited)

        Returns:
            Dictionary with "objects" array and "count"
        """
        bad = filter_problem(filter)
        if bad:
            return bad_filter(bad, objects=[], count=0)
        bridge = get_bridge()
        params = {
            "scope": scope,
            "object_type": object_type,
            "filter": filter,
            "properties": properties,
        }
        if limit > 0:
            params["limit"] = str(limit)
        result = await bridge.send_command_async(
            "generic.query_objects",
            params,
        )
        # Pin location is the body root, not the connection point, and
        # that keeps being rediscovered by getting it wrong. Say it on
        # the reply the caller is already reading.
        hint = pin_location_hint(object_type, properties)
        if hint and isinstance(result, dict):
            result["_hint_pin_connection"] = hint
        # Free text read out of a design file is DATA. The file may have
        # come from a customer, a vendor or a third-party library, so the
        # person who typed that Comment is not necessarily the operator,
        # and it reaches the model verbatim through this tool.
        untrusted = untrusted_text_note(properties)
        if untrusted and isinstance(result, dict):
            result["_untrusted_content"] = untrusted
        return result

    @mcp.tool()
    async def obj_explain_pin(
        designator: str,
        pin: str,
        sheet_path: str = "",
        project_path: str = "",
    ) -> dict[str, Any]:
        """Explain WHY a schematic pin is on the net it is on.

        Lists every object at the pin's CONNECTION point (what drives its
        net; two different labels there is a short) and at its body-side
        ROOT (electrically inert; a label there leaves the pin floating).
        A label at the root belongs at ``connect_x_mils`` /
        ``connect_y_mils``. Objects match by exact location, not
        bounding box.

        Without ``sheet_path`` the active sheet is searched first, then
        the sheets of the project (``project_path``, or the focused one),
        so two open projects that both have a U1 answer for the sheet
        in front of you.

        Args:
            designator: Component designator, e.g. "U10".
            pin: Pin NUMBER (not pin name), e.g. "5".
            sheet_path: Search only this open SchDoc.
            project_path: Search this project's sheets (.PrjPcb path)
                instead of the focused project's; the active sheet is
                then not searched first.

        Returns:
            ``designator, pin, pin_name, sheet, sheet_path`` (the full
            path of the sheet the pin is on), ``found_on``
            (named_sheet, active_sheet_in_project,
            active_sheet_outside_project or project), ``orientation,
            pin_length_mils, root_x/y_mils, connect_x/y_mils,
            compiled_net`` (from the project's netlist, only when the
            sheet is in that project), and ``at_connection`` /
            ``at_root`` lists of ``{kind, text, x_mils, y_mils}``.
        """
        bridge = get_bridge()
        params = {"designator": designator, "pin": pin}
        if sheet_path:
            params["sheet_path"] = sheet_path
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "generic.explain_pin", params,
        )
        # The compiled net comes from a project's netlist, so it is only
        # read when the pin was found in that project. A sheet outside it
        # (or one the caller named with no project) has its own U1.
        # A script from before 2026.10.21 sends no found_on and searched
        # the focused project only, as the netlist read does.
        found_on = result.get("found_on") if isinstance(result, dict) else None
        in_project = found_on in (None, "project", "active_sheet_in_project") or (
            found_on == "named_sheet" and bool(project_path))
        if isinstance(result, dict) and "error" not in result and in_project:
            try:
                conn_params = {"designator": designator}
                if project_path:
                    conn_params["project_path"] = project_path
                conn = await bridge.send_command_async(
                    "project.get_connectivity", conn_params,
                )
                for p in conn.get("pins", []) if isinstance(conn, dict) else []:
                    if str(p.get("pin_number")) == str(pin):
                        result["compiled_net"] = p.get("net")
                        break
            except Exception:
                pass
        return result

    @mcp.tool()
    async def obj_modify(
        object_type: str = "",
        set: str = "",
        scope: str = "active_doc",
        filter: str = "",
        items: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Apply ONE set of property values to every object matching ONE filter.

        When each object needs a DIFFERENT value (10 pins to 10 positions,
        distinct designators), give ``items``: a list of
        {scope, object_type, filter, set} dicts, one per filter. They run in
        ONE Altium transaction (one undo step); arguments given beside
        ``items`` apply to every item. No rollback: items before a failure
        stay applied. After a designator list, check the netlist: duplicate
        designators merge nets silently (proj_annotate renumbers without
        duplicates). With items a value in ``set`` may hold ';' but no
        field may hold '~~'.

        Args:
            object_type: Altium object type constant (see `obj_query`)
            set: Pipe-separated assignments, e.g. "Text=NEW_NAME" or
                "Location.X=100|Location.Y=200". Board lengths are
                decimal mils; a non-number is reported failed, never
                written as 0. Board properties include, besides the
                usual geometry: HolePositiveTolerance and
                HoleNegativeTolerance on pads and vias ("unset" when
                none is set; Altium keeps a negative tolerance as its
                size; IsCounterHole reads); BarCode* and Multiline* on a
                text (Altium recalculates the barcode and multiline sizes
                from the text: read them back); TextHeight,
                TextFormat, TextPrecision, TextPrefix, TextSuffix,
                Arrow* and Extension* on a dimension; PolygonType,
                BorderWidth, AvoidObsticles (Altium's spelling),
                ClipAcuteCorners, MitreCorners, IgnoreViolations and
                ArcPourMode on a pour; EnablePinSwapping and
                EnablePartSwapping on a part (IsBGA reads); also on a
                part LockStrings, NameAutoPosition and CommentAutoPosition
                (Altium's anchor numbers; writing one moves the text
                there), FootprintDescription, DefaultPCB3DModel
                (PackageSpecificHash reads), SourceDescription,
                JumpersVisible, ChannelOffset and GroupNum; on a board
                pad JumperID, SwapID_Pad, SwapID_Part, SwappedPadName and
                OwnerPart_ID; on pads and vias PasteMaskExpansionMode and
                SolderMaskExpansionMode (Altium's numbers: from the rule or
                manual), PasteMaskUsePercent, PasteMaskPercent and
                PasteMaskEnabled; on tracks, arcs and vias IsPreRoute
                (the autorouter keeps it) and UserRouted; on a pour DrawRemovedNecks,
                DrawRemovedIslands, DrawDeadCopper, ExpandOutline and
                AutoGenerateName; on board text Inverted,
                UseInvertedRectangle, InvertedTTTextBorder,
                TTFInvertedTextJustify, TTFOffsetFromInvertedRect and
                ConvertedString (reads: the text as shown, special
                strings resolved); on a 3D body OverrideColor (draw its
                own colour, not the model's), Texture (an image file) and
                TextureRotation; on a board pad DrillType (how the hole
                is made: 0 drilled, 1 punched, 2 laser, 3 plasma) and
                IsVirtualPin (reads); on a placed part FlippedOnLayer
                (reads: its "flip on layer" option, not its side); on
                board text BorderSpaceType (Altium's number).
                Schematic pins take
                Symbol_Inner, Symbol_Outer, Symbol_InnerEdge,
                Symbol_OuterEdge and Symbol_LineWidth (Altium's numbers),
                HiddenNetName and DefinedFunctions (the pin's functions,
                ";"-separated; writing replaces the list); schematic parts
                take ShowHiddenPins, ShowHiddenFields, PinsMoveable,
                PartIdLocked, DisplayFieldNames, OverideColors (Altium's
                spelling) and PinColor. Pins also take where their name
                and number sit: Name_PositionMode,
                Name_CustomPosition_Margin,
                Name_CustomPosition_RotationAnchor,
                Name_CustomPosition_RotationRelative and the same four
                for Designator_ (Altium's numbers), DefaultValue and
                FormalType; schematic parts also LibraryPath (the library
                the symbol came from) and AliasAsText. Schematic parameters
                take ReadOnlyState, ParamType, AllowLibrarySynchronize
                and AllowDatabaseSynchronize, and report NameIsReadOnly,
                ValueIsReadOnly, IsRule, IsSystemParameter and
                IsConfigurable; ports take AutoSize,
                OverrideDisplayString and HarnessColor (the last two do
                not survive save and reopen; the reply notes it). Symbol pins
                take SwapIdPart (shared by every pin of the part),
                SwapIdPin, SwapIdPair, SwapIdPartPin.
            scope: "active_doc" (default), "project" (all SCH sheets),
                "doc:C:\\path\\Sheet.SchDoc" (no focus change), or
                "lib_component:NAME" (a symbol in the active SchLib,
                selected for you).
            filter: Pipe-separated property=value conditions (AND logic)

            items: Several filter+set operations at once (see above).

        Returns:
            "matched", "sheets_processed", and on a schematic "modified"
            (something was written) with "saved" false: the sheet is
            marked modified, not saved. Run app_save_all to write it.
            With items: operations_processed, operations_skipped,
            total_matched and per-item ``results`` with ``matched`` and a
            ``note`` ("no_objects_matched" when nothing matched).

        Example - rename a net on every sheet:
            obj_modify(object_type="eNetLabel", scope="project",
                       filter="Text=OLD_NET", set="Text=NEW_NET")
        Example - edit a pin on any part of a multi-part SchLib symbol:
            obj_modify(object_type="ePin", scope="lib_component:R_0402",
                       filter="Designator=1", set="Name=A")
        Example - put board copper on a net, or take it off (track, arc,
        via, pad, fill, region, polygon; the net must exist, "" = no net):
            obj_modify(object_type="eRegionObject",
                       filter="Layer=TopLayer|Net=GND", set="Net=")
        Example - a different value per pin, one call:
            obj_modify(scope="lib_component:U_MCU", object_type="ePin",
                       items=[{"filter": "Designator=1", "set": "Name=VDD"},
                              {"filter": "Designator=2", "set": "Name=GND"}])
        """
        if items is not None:
            shared = {k: v for k, v in (("scope", scope),
                                        ("object_type", object_type),
                                        ("filter", filter), ("set", set)) if v}
            ops = _merge_items(items, shared)
            if isinstance(ops, dict):
                return ops
            return await obj_batch_modify(ops)
        if not object_type or not set:
            return {"ok": False, "reason":
                    "obj_modify needs object_type and set, or items"}
        bad = filter_problem(filter)
        if bad:
            return bad_filter(bad, matched=0)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.modify_objects",
            {
                "scope": scope_to_wire(scope),
                "object_type": object_type,
                "filter": filter,
                "set": set,
            },
        )
        hint = BulkHintTracker.record_and_hint("obj_modify")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        if (isinstance(result, dict) and object_type == "ePort"
                and re.search(r"(^|\|)\s*(OverrideDisplayString|HarnessColor)\s*=", set)):
            result["note"] = _PORT_FIELDS_DO_NOT_SAVE
        return result

    @mcp.tool()
    async def obj_create(
        object_type: str = "",
        properties: str = "",
        container: str = "document",
        items: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Create and place a schematic object.

        For several, give ``items``: a list of {object_type, properties,
        container} dicts, created in ONE Altium transaction (one undo
        step); arguments given beside ``items`` apply to every item.

        Args:
            object_type: Altium object type constant (see `obj_query`)
            properties: Pipe-separated property=value assignments, e.g.:
                "Text=MY_NET|Location.X=100|Location.Y=200"
            container: "document" (place on active schematic) or
                      "component" (add to current library component)
            items: Several objects at once (see above).

        Example, three net labels in one call:
            obj_create(object_type="eNetLabel", items=[
                {"properties": "Text=VCC|Location.X=100|Location.Y=200"},
                {"properties": "Text=GND|Location.X=100|Location.Y=400"},
                {"properties": "Text=SCK|Location.X=300|Location.Y=200"}])

        Returns:
            Dictionary confirming creation. With items: created, failed,
            total and the failures by index. Known unsupported properties
            (IsHidden on anything but pins, parameters, designators and
            sheet names; Text on a type without it, such as eParameterSet
            or eWire) reject that item before registration with reason
            UNSUPPORTED_PROPERTY; the other items still run.
        """
        if items is not None:
            shared = {"object_type": object_type, "properties": properties}
            shared = {k: v for k, v in shared.items() if v}
            if container != "document":
                shared["container"] = container
            ops = _merge_items(items, shared)
            if isinstance(ops, dict):
                return ops
            return await obj_batch_create(ops)
        if not object_type:
            return {"ok": False, "reason":
                    "obj_create needs object_type, or items"}
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.create_object",
            {
                "object_type": object_type,
                "properties": properties,
                "container": container,
            },
        )
        hint = BulkHintTracker.record_and_hint("obj_create")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def obj_delete(
        object_type: str = "",
        scope: str = "active_doc",
        filter: str = "",
        confirm_delete_all: bool = False,
        items: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Find and delete schematic or PCB objects.

        For several type/filter sets at once, give ``items``: a list of
        {scope, object_type, filter} dicts, run in one round trip; an item
        with an empty filter needs confirm_delete_all, or nothing is sent.
        Arguments given beside ``items`` apply to every item.

        A PCB type deletes from the active board and also matches
        footprint primitives and polygon hatching unless the filter says
        "InComponent=false|InPolygon=false". To take up routing use
        ``pcb_unroute``.

        Args:
            object_type: Altium object type constant (see `obj_query`)
            scope: "active_doc", "project", "doc:PATH", or
                "lib_component:NAME" (a named symbol in the active SchLib).
                A PCB type takes only "active_doc".
            filter: Pipe-separated property=value conditions (AND logic).
                    WARNING: empty filter deletes ALL objects of the type.
            confirm_delete_all: Must be True to delete all objects when filter is empty.
            items: Several deletes at once (see above). Reply:
                operations_processed, total, total_deleted and per-item
                ``results`` with ``deleted``; ``partial:`` notes and
                ``operations_partial`` as for a single delete.

        A filter that is not Name=Value pairs (an Altium query expression
        such as ``OnLayer('TopLayer') Or ...``, or a bare name) is refused,
        and nothing is deleted.

        Returns:
            Dictionary with "matched" count (number deleted). When a
            removal fails part way, ``partial`` is true, ``delete_error``
            says why, and ``matched`` still counts the objects already
            removed. Query before repeating a delete that failed or timed
            out: the first part of it may already have happened.
        """
        if items is not None:
            shared = {k: v for k, v in (("scope", scope),
                                        ("object_type", object_type),
                                        ("filter", filter)) if v}
            ops = _merge_items(items, shared)
            if isinstance(ops, dict):
                return ops
            return await obj_batch_delete(ops, confirm_delete_all=confirm_delete_all)
        if not object_type:
            return {"ok": False, "reason":
                    "obj_delete needs object_type, or items", "matched": 0}
        bad = filter_problem(filter)
        if bad:
            return bad_filter(bad, matched=0)
        if not filter and not confirm_delete_all:
            return {
                "error": "Safety guard: empty filter would delete ALL objects of type "
                f"'{object_type}'. Provide a filter to select specific objects, "
                "or set confirm_delete_all=True to delete all.",
                "matched": 0,
            }
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.delete_objects",
            {
                "scope": scope_to_wire(scope),
                "object_type": object_type,
                "filter": filter,
            },
        )
        hint = BulkHintTracker.record_and_hint("obj_delete")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def obj_get_font_spec(
        font_id: int,
    ) -> dict[str, Any]:
        """Get font properties for a given font ID.

        The font an object's FontId names.

        Args:
            font_id: Font ID from an object's FontId property

        Returns:
            Dictionary with font_id, size, rotation, bold, italic, underline,
            strikeout, font_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_font_spec",
            {"font_id": str(font_id)},
        )
        return result

    @mcp.tool()
    async def obj_get_font_id(
        size: int,
        font_name: str = "Arial",
        bold: bool = False,
        italic: bool = False,
        rotation: int = 0,
        underline: bool = False,
        strikeout: bool = False,
    ) -> dict[str, Any]:
        """Get or create a font ID for the given font properties.

        Looks up (or creates) an entry in the Altium font table matching the
        specified properties and returns its font ID. Use the returned font_id
        to set an object's FontId property via `obj_modify`: labels, net
        labels, power objects, cross-sheet connectors, parameters,
        designators, sheet names, ports, text frames and notes. A sheet
        entry's or harness entry's label font is TextFontId.

        Args:
            size: Font size in points (e.g., 8, 10, 12)
            font_name: Font family name (default "Arial")
            bold: Whether the font is bold
            italic: Whether the font is italic
            rotation: Text rotation in degrees (0, 90, 180, 270)
            underline: Whether the font is underlined
            strikeout: Whether the font has strikeout

        Returns:
            Dictionary with font_id
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_font_id",
            {
                "size": str(size),
                "font_name": font_name,
                "bold": "true" if bold else "false",
                "italic": "true" if italic else "false",
                "rotation": str(rotation),
                "underline": "true" if underline else "false",
                "strikeout": "true" if strikeout else "false",
            },
        )
        return result

    @mcp.tool()
    async def obj_select(
        object_type: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Select objects matching a filter on the active document.

        Sets the selection state on matching schematic or PCB objects for
        visual highlighting. Use `obj_deselect_all` to clear.

        Args:
            object_type: Altium object type (schematic or PCB)
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            ``{"matched": N, "selected": M, "success": bool}``. ``matched``
            is what the filter picked; ``selected`` is how many of those
            read back as selected afterwards, and ``success`` is false
            with a ``reason`` when that is fewer. A select reports
            nothing about property readability.
        """
        bad = filter_problem(filter)
        if bad:
            return bad_filter(bad, matched=0)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.select_objects",
            {"object_type": object_type, "filter": filter},
        )
        return result

    @mcp.tool()
    async def obj_deselect_all() -> dict[str, Any]:
        """Clear all object selection on the active document.

        Returns:
            Dictionary confirming deselection
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.deselect_all", {})
        return result

    @mcp.tool()
    async def obj_zoom(
        action: str = "fit",
        x: Optional[float] = None,
        y: Optional[float] = None,
        radius: Optional[float] = None,
    ) -> dict[str, Any]:
        """Control the viewport zoom level of the focused document.

        Works on a schematic, a schematic library, a board and a footprint
        library; the focused tab decides which editor zooms.

        Args:
            action: "fit" (Fit All Objects, which can cut a sheet's frame
                off), "document" (a schematic's Fit Document: the whole
                sheet with its frame), "board" (the board alone, not
                parts parked beside it; a schematic fits as "fit"),
                "selection" (zoom to selected objects) or "point" (zoom
                to x, y)
            x, y: the point for "point", in mils (a footprint's frame in
                a PCB library)
            radius: half the width of the view round the point on a board
                or footprint, mils (default 200)

        Returns:
            Dictionary confirming the zoom action
        """
        bridge = get_bridge()
        if action == "point":
            if x is None or y is None:
                return {"success": False,
                        "error": 'action "point" needs x and y'}
            params: dict[str, Any] = {"x": str(float(x)), "y": str(float(y))}
            if radius is not None:
                params["radius"] = str(float(radius))
            return await bridge.send_command_async("generic.zoom_to_xy", params)
        result = await bridge.send_command_async(
            "generic.zoom", {"action": action}
        )
        return result

    @mcp.tool()
    async def sch_place_comment(text: str, x: float, y: float,
                                document: str = "") -> dict[str, Any]:
        """Post an Altium 365 review comment at a point on a schematic.

        A comment is kept only on a project saved to an Altium 365
        Workspace. On a local project it is placed but stays unsaved, and
        closing the project asks "Save To Workspace" (Yes moves the
        project into the Workspace, No drops the comment). A free sheet
        offers no comment at all. There is no API for comments, so this
        drives the interface: Place > Comment, Jump To Location to the
        point, then the popup's text box and Post. It brings Altium to the
        front and leaves the view zoomed in on the point.

        Args:
            text: The comment.
            x, y: The point on the sheet, in mils.
            document: The schematic's full path. Empty uses the focused
                document, which must be a schematic.

        Returns:
            ``ok``, ``posted``, ``document``, ``x``, ``y`` and ``kept``
            (where the comment is stored); or ``ok: False`` with
            ``reason`` and, when Altium refused, ``altium`` with its
            message. The posted text is not read back: the Comments and
            Tasks panel cannot be read.
        """
        # Here and not with the interface tools, which send no bridge
        # command: this one focuses its document through the bridge first.
        import asyncio
        import os

        from ..bridge.exceptions import AltiumError
        from ..ui import comments, menu, uia
        from .uiauto import _altium_pid

        if not str(text or "").strip():
            return {"ok": False, "reason": "text is empty; Altium posts no empty comment"}
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not uia.available() or not menu.available():
            return {"ok": False, "reason": (
                "placing a comment needs UI Automation, pywin32 and comtypes")}

        bridge = get_bridge()
        if document:
            try:
                await bridge.send_command_async(
                    "application.set_active_document", {"file_path": document})
            except AltiumError as exc:
                # A project's sheet can be listed but not loaded yet; open
                # it as app_set_active_document does.
                if "not loaded" not in str(exc).lower() or not os.path.isfile(document):
                    raise
                await bridge.send_command_async("application.run_process", {
                    "process_name": "WorkspaceManager:OpenObject",
                    "parameters": f"ObjectKind=Document|FileName={document}"})
                await bridge.send_command_async(
                    "application.set_active_document", {"file_path": document})
        active = await bridge.send_command_async(
            "application.get_active_document", {}) or {}
        path = str(active.get("file_path") or "")
        kind = str(active.get("document_kind") or "").upper()
        if kind not in ("SCH", "SCHDOC") and not path.lower().endswith(".schdoc"):
            return {"ok": False, "document": path, "reason": (
                f"the focused document is {kind or 'none'}, not a schematic. "
                f"Pass document= with the sheet's path")}
        if not menu.bring_to_front(pid):
            return {"ok": False, "reason": "could not focus Altium"}

        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(
            None, lambda: comments.place(pid, float(x), float(y), str(text)))
        out["document"] = path
        return out

    @mcp.tool()
    async def sch_mirror_component(
        designator: str,
        mirrored: bool | None = None,
        doc_path: str = "",
    ) -> dict[str, Any]:
        """Mirror a placed schematic component horizontally.

        USE THIS RATHER THAN WRITING `IsMirrored` with obj_modify, which
        sets the flag and leaves the drawing unchanged. This reflects the
        primitives as Altium's own mirror does.

        Args:
            designator: component to mirror, e.g. "U1"
            mirrored: True or False for an absolute state; omit to
                toggle. Asking for the state it already has is a no-op,
                not a second reflection.
            doc_path: sheet to act on. Defaults to the focused schematic.

        Every primitive is reflected about the component's X; a kind it
        cannot reflect stays put and is counted in primitives_skipped.

        Returns:
            Dictionary with designator, mirrored (the resulting state),
            primitives_moved, primitives_skipped, and changed.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if doc_path:
            params["doc_path"] = doc_path
        if mirrored is not None:
            params["mirrored"] = "true" if mirrored else "false"
        return await bridge.send_command_async(
            "generic.mirror_component", params
        )

    @mcp.tool()
    async def sch_rename_designators(
        project_path: str,
        changes: list[dict[str, str]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Rename designators across a whole project, swaps included, refusing
        any collision before the first write.

        Each rename names a part by its current ``designator`` (with
        ``sheet``, a file name or full path, when copies of a sheet share
        it) or by the ``unique_id`` of any of its parts (obj_query
        ``UniqueId``), and gives ``to``. A part is its sub-parts on one
        sheet with one designator and library reference, so a multi-part
        part is renamed whole. Every sheet is loaded and every part read
        first: a name the batch hands out that another part keeps, a part
        named twice, or one not found refuses the whole batch with nothing
        written. The parts then go through unique temporary names before
        their final ones, so R1 to R2 and R2 to R1 works, and the project
        is read back after each step. Nothing is saved (app_save_all).
        To renumber a whole design, proj_annotate stays the tool.

        Args:
            project_path: The .PrjPcb, a full path.
            changes: ``[{"designator": "R1", "to": "R2"}, ...]``, with
                ``sheet`` when needed, or ``unique_id`` in place of
                ``designator``.
            dry_run: Check and plan only.

        Returns:
            ``ok``, ``changes`` (``sheet``, ``from``, ``to``, ``parts``),
            ``sheets``, ``components``, ``duplicates_left`` (designators
            parts the batch does not touch still share, as on copied sheets
            not annotated yet); when written, ``verified`` and per step
            ``temporary`` and ``final`` with what was read back. A step
            that did not read back as planned stops with ``stage`` and
            ``now`` (each changed part's designators as the project holds
            them), so nothing is left unreported.
        """
        import ntpath
        import os
        import secrets

        if not os.path.isabs(project_path) or not project_path.lower().endswith(
                (".prjpcb", ".prjpcbm")):
            return {"ok": False, "reason": "project_path is the full path of a .PrjPcb"}
        def field(change: Any, key: str) -> str:
            return str(change.get(key) or "").strip() if isinstance(change, dict) else ""

        asked = [(field(c, "unique_id"), field(c, "designator"), field(c, "sheet"),
                  field(c, "to")) for c in (changes if isinstance(changes, list) else [])]
        if not asked:
            return {"ok": False, "reason": "changes is a list of {designator or "
                    "unique_id, to}"}
        for n, (uid, des, _sheet, to) in enumerate(asked):
            if not to or bool(uid) == bool(des):
                return {"ok": False, "reason": f"changes[{n}] needs ``to`` and exactly one "
                        "of designator or unique_id"}
            if set(des + to) & set("|;~=\r\n"):
                return {"ok": False, "reason": f"changes[{n}] holds | ; ~ = or a line "
                        "break, which the batch format cannot carry"}

        bridge = get_bridge()
        loaded = await bridge.send_command_async(
            "project.load_project_sheets", {"project_path": project_path}, timeout=300.0)
        total = loaded.get("total_sheets") if isinstance(loaded, dict) else None
        if not isinstance(total, int) or total < 1 or (loaded.get("failed") or []):
            return {"ok": False, "reason": "not every sheet of the project loaded",
                    "load": loaded}

        Key = tuple[str, str, str]

        async def read() -> dict[Key, dict[str, Any]]:
            """(sheet, designator, library reference), folded -> the part."""
            reply = await bridge.send_command_async("generic.query_objects", {
                "scope": "project:" + project_path, "object_type": "eSchComponent",
                "filter": "", "properties": "UniqueId,Designator,LibReference,CurrentPartID"},
                timeout=300.0)
            rows = reply.get("objects") if isinstance(reply, dict) else None
            if not isinstance(rows, list) or reply.get("sheets_processed") != total:
                raise RuntimeError("the parts of every sheet could not be read: "
                                   + str(reply)[:300])
            parts: dict[Key, dict[str, Any]] = {}
            for row in rows:
                doc = str(row.get("_doc") or "")
                name = str(row.get("Designator") or "").strip()
                lib = str(row.get("LibReference") or "")
                part = parts.setdefault((doc.upper(), name.upper(), lib.upper()), {
                    "doc": doc, "name": name, "lib": lib, "uids": [], "part_ids": []})
                part["uids"].append(str(row.get("UniqueId") or "").upper())
                part["part_ids"].append(str(row.get("CurrentPartID") or ""))
            return parts

        try:
            parts = await read()
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}

        def sheet_of(key: Key) -> str:
            return ntpath.basename(parts[key]["doc"])

        wanted: dict[Key, str] = {}
        for uid, des, sheet, to in asked:
            if uid:
                found = [k for k, p in parts.items() if uid.upper() in p["uids"]]
                label = f"UniqueId {uid}"
            else:
                found = [k for k, p in parts.items() if p["name"].upper() == des.upper()
                         and (not sheet or sheet.upper() in (p["doc"].upper(),
                                                             sheet_of(k).upper()))]
                label = des + (f" on {sheet}" if sheet else "")
            if len(found) != 1:
                return {"ok": False, "reason": (f"{label} names {len(found)} parts; "
                        "add its sheet") if found else f"no part is {label}",
                        "sheets": sorted(sheet_of(k) for k in found)}
            key = found[0]
            ids = parts[key]["part_ids"]
            if len(set(ids)) != len(ids):
                return {"ok": False, "reason": f"{label} is more than one part on "
                        f"{sheet_of(key)} (part {', '.join(ids)}): annotate first"}
            if key in wanted:
                return {"ok": False, "reason": f"{label} is renamed twice"}
            wanted[key] = to

        final = {k: wanted.get(k, p["name"]) for k, p in parts.items()}
        owners_after: dict[str, list[Key]] = {}
        for key, name in final.items():
            owners_after.setdefault(name.upper(), []).append(key)
        # A name the batch hands out must end on one part. Duplicates it does
        # not touch (copied sheets not yet annotated) are reported, not refused.
        for key, to in wanted.items():
            holders = owners_after[to.upper()]
            if len(holders) > 1:
                return {"ok": False, "reason": f"{to} would name {len(holders)} parts",
                        "designator": to,
                        "on": sorted(f"{parts[k]['name']} on {sheet_of(k)}" for k in holders)}
        duplicates = sorted(n for n, keys in owners_after.items() if len(keys) > 1)

        changed = [k for k, to in wanted.items() if to != parts[k]["name"]]
        plan = {"ok": True, "dry_run": dry_run, "sheets": total,
                "components": len(parts), "duplicates_left": duplicates,
                "changes": [{"sheet": sheet_of(k), "from": parts[k]["name"],
                             "to": wanted[k], "parts": len(parts[k]["uids"])}
                            for k in changed]}
        if dry_run or not changed:
            return plan

        taken = set(owners_after) | {p["name"].upper() for p in parts.values()}
        temporary: dict[Key, str] = {}
        for key in changed:
            name = "TMP" + secrets.token_hex(4).upper()
            while name.upper() in taken:
                name = "TMP" + secrets.token_hex(4).upper()
            taken.add(name.upper())
            temporary[key] = name

        async def step(old: dict[Key, str], new: dict[Key, str]) -> dict[str, Any]:
            """Rename each changed part from old to new, then read back."""
            ops = "~~".join(
                f"scope=doc:{parts[k]['doc']};object_type=eSchComponent;"
                f"filter=Designator={old[k]}|LibReference={parts[k]['lib']};set=Designator={new[k]}"
                for k in changed)
            reply: Any = None
            try:
                reply = await bridge.send_command_async(
                    "generic.batch_modify", {"operations": ops}, timeout=300.0)
            except Exception as exc:  # the write may have landed; the read says
                reply = {"error": str(exc)}
            after = await read()
            now: dict[str, list[str]] = {}
            good = True
            for key, p in parts.items():
                want = new[key] if key in new else p["name"]
                got = after.get((key[0], want.upper(), key[2]))
                if got is None or sorted(got["uids"]) != sorted(p["uids"]):
                    good = False
                if key in new:
                    now[f"{p['name']} on {sheet_of(key)}"] = sorted(
                        q["name"] for q in after.values() if set(q["uids"]) & set(p["uids"]))
            return {"verified": good and len(after) == len(parts), "now": now,
                    "reply": reply}

        original = {k: parts[k]["name"] for k in changed}
        try:
            first = await step(original, temporary)
            if not first["verified"]:
                return {**plan, "ok": False, "stage": "temporary", "temporary": first}
            second = await step(temporary, {k: wanted[k] for k in changed})
        except RuntimeError as exc:
            return {**plan, "ok": False, "stage": "read back", "reason": str(exc),
                    "temporary_names": {f"{parts[k]['name']} on {sheet_of(k)}": t
                                        for k, t in temporary.items()}}
        out = {**plan, "ok": second["verified"], "verified": second["verified"],
               "temporary": first, "final": second, "saved": False}
        if not second["verified"]:
            out["stage"] = "final"
        return out

    @mcp.tool()
    async def obj_batch_modify(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Apply many filter+set operations in ONE call (obj_modify items).

        What ``obj_modify(items=[...])`` runs, kept under this name for
        callers that use it.

        No rollback: ops that ran before a failure stay applied. After a
        designator batch, check the netlist: duplicate designators merge
        nets silently. proj_annotate renumbers without duplicates.

        Args:
            operations: List of operation dicts, each with:
                - scope: "active_doc" (default), "project",
                  "doc:C:\\path\\to\\file.SchDoc", or "lib_component:NAME"
                  (a symbol in the active SchLib; ops may name different
                  symbols).
                - object_type: e.g. "ePin", "eParameter", "eSchComponent",
                  or a PCB type such as "eTrackObject" (active board,
                  scope "active_doc" only).
                - filter: Pipe-separated conditions, e.g. "Name=VDD".
                - set: Pipe-separated assignments, e.g.
                  "Location.X=300|Location.Y=-100|Orientation=2". A value
                  may contain ';' but not '|'.

        Returns:
            operations_processed, operations_skipped, total_matched, and
            per-op ``results`` with ``matched`` and a ``note``
            ("no_objects_matched" when nothing matched).

        Example op: ``{"scope": "lib_component:R_0402", "object_type":
        "ePin", "filter": "Designator=1", "set": "Name=A"}``.
        """
        # Operations use the '~~' batch framing shared with obj_batch_delete.
        # The old framing joined operations with '|' -- the SAME character
        # that separates conditions inside `filter` and assignments inside
        # `set`. Any op with a two-condition filter or a two-property set was
        # torn into fragments on the Altium side and the fragments after the
        # first were dropped without a trace (observed: 4 eSchComponent ops
        # followed by 8 eNetLabel ops reported operations_processed=4).
        # '~~' cannot occur in an Altium name, filter, or property string.
        # set goes LAST and the script reads everything after ';set=', so a
        # value may hold ';' (Text=first;second). The fields before it
        # cannot, and nothing can hold '~~'; same refusal as obj_batch_delete.
        op_strings = []
        for i, op in enumerate(operations):
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            filt = op.get("filter", "")
            set_str = op.get("set", "")
            for field, value, delims in (("scope", scope, (";", "~~")),
                                         ("object_type", obj_type, (";", "~~")),
                                         ("filter", filt, (";", "~~")),
                                         ("set", set_str, ("~~",))):
                hit = [d for d in delims if d in str(value)]
                if hit:
                    return {"ok": False, "reason":
                            f"operations[{i}].{field} contains '{hit[0]}', a "
                            "batch delimiter the wire format cannot carry "
                            "there; nothing was sent",
                            "operations_processed": 0}
            if not obj_type or not set_str:
                # Skipped, it vanished from the batch without a trace.
                return {"ok": False, "reason":
                        f"operations[{i}] needs object_type and set; "
                        "nothing was sent", "operations_processed": 0}
            bad = filter_problem(filt)
            if bad:
                return bad_filter(bad, operations_processed=0)
            op_strings.append(
                f"scope={scope};object_type={obj_type};"
                f"filter={filt};set={set_str}"
            )

        if not op_strings:
            return {"error": "No valid operations provided", "operations_processed": 0}

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.batch_modify",
            {"operations": "~~".join(op_strings)},
        )
        return result

    @mcp.tool()
    async def obj_run_process(
        process_name: str,
        parameters: str = "",
    ) -> dict[str, Any]:
        """Run an Altium process command via the generic primitive layer.

        Wraps any Altium RunProcess call with structured pipe-separated parameters.

        Args:
            process_name: The Altium process identifier (e.g., "Sch:Compile",
                "WorkspaceManager:OpenObject", "PCB:Zoom")
            parameters: Optional pipe-separated key=value parameter pairs, e.g.:
                "ObjectKind=Document|FileName=C:\\path\\to\\file.SchDoc"

        Returns:
            Dictionary with execution result
        """
        refusal = refuse_process(process_name)
        if refusal is not None:
            return {"success": False, "error": "REFUSED_BY_POLICY",
                    "reason": refusal, "process": process_name}

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.run_process",
            {
                "process": process_name,
                "params": parameters,
            },
        )
        if isinstance(result, dict):
            out = {"success": True, **result}
        elif result:
            out = {"success": True, "data": result}
        else:
            out = {"success": True, "process": process_name}
        try:
            from ..ui.altium_processes import unlisted
            out.update(unlisted(process_name) or {})
        except Exception:                       # noqa: BLE001 - a hint only
            pass
        return out

    @mcp.tool()
    async def proj_run_erc(limit: int = 100,
                           include_suppressed: bool = False) -> dict[str, Any]:
        """Run Electrical Rules Check on the focused project.

        Compiles the project first (required), runs ERC, and returns
        what it found, the same list ``proj_get_erc_violations`` reads:
        what Altium reports. A violation a No ERC marker covers, or of a
        type set to No Report, is left out and counted in
        ``suppressed_count`` / ``not_reported_count``.

        Args:
            limit: How many violations to return; 0 returns all of them.
            include_suppressed: list those too, each with ``suppressed``
                and ``level``.

        Returns:
            ``violation_count`` and ``violations``: each with its kind,
            document, detail and the related objects, whose cross-probe
            string names the exact pin or net. ``truncated`` is true when
            the list stops short of ``violation_count``; read the rest
            with ``proj_get_erc_violations(offset=next_offset)``.
        """
        bridge = get_bridge()
        params = {"limit": str(max(0, int(limit))),
                  "include_suppressed": "true" if include_suppressed else "false"}
        result = await bridge.send_command_async("generic.run_erc", params)
        # A script from before 2026.10.21 answers the run with a message
        # only; read the list it left behind instead of returning nothing.
        if isinstance(result, dict) and "violation_count" not in result:
            result = await bridge.send_command_async(
                "generic.get_erc_violations", params)
        return _mark_erc_page(result)

    @mcp.tool()
    async def sch_set_components_parameters(
        stamps: list[dict[str, str]],
        sheet_path: str = "",
    ) -> dict[str, Any]:
        """Bulk-update parameters on placed schematic components, in one
        call and one undo group. The follow-up to the parameter audits.

        Args:
            stamps: One dict per component: ``designator`` plus any
                parameter key/values, e.g. ``{"designator": "R1",
                "Value": "10k", "MPN": "..."}``. A parameter the part
                lacks is created, hidden, at the part. ``Value`` writes
                the part's Comment and also a Value parameter the part
                already has; ``Description`` writes the part's own
                description (as a parameter Altium dropped it on reload).
            sheet_path: Optional SchDoc path; default the focused one.

        Returns:
            ``{updated, failed, total}``; ``failed`` counts stamps that
            could not be applied (designator not found and the like).
            A ``Footprint`` key repoints the part's footprint model;
            ``footprints_applied`` counts the ones that read back the
            new name and ``footprints_not_applied`` lists designators
            whose part has no footprint model or did not take it.
        """
        # Encode the list-of-dicts into the Pascal handler's expected
        # wire format: stamps separated by `~~`, fields within a stamp
        # separated by `;`, key=value pairs.
        if not stamps:
            return {"ok": False, "reason": "stamps list is empty"}
        encoded_stamps = []
        for stamp in stamps:
            if "designator" not in stamp:
                return {"ok": False,
                        "reason": f"stamp missing 'designator': {stamp}"}
            # The designator was interpolated RAW here while every other
            # value was escaped, so the one field guaranteed to come
            # from the caller was the one that could reshape the
            # payload. Both go through the shared sanitiser now; the
            # local copy also collapsed "~~" differently from
            # library.py, so identical input produced different output
            # depending on which tool sent it.
            fields = [f"designator={payload_safe(stamp['designator'])}"]
            for key, val in stamp.items():
                if key == "designator":
                    continue
                fields.append(f"{key}={payload_safe(val)}")
            encoded_stamps.append(";".join(fields))
        params: dict[str, str] = {"stamps": "~~".join(encoded_stamps)}
        if sheet_path:
            params["sheet_path"] = sheet_path
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_sch_components_parameters", params, timeout=60.0)

    @mcp.tool()
    async def proj_get_erc_violations(
        limit: int = 100,
        offset: int = 0,
        include_suppressed: bool = False,
    ) -> dict[str, Any]:
        """Return the ERC violation list produced by the last
        ``proj_run_erc()`` invocation.

        A cheap re-read; ``proj_run_erc()`` does the slow check. Each
        violation lists its ``related_objects`` (``kind``, ``document``
        and the ``cross_probe`` string Altium uses to find that exact
        pin or net). Act only on a named object: a NoERC marker placed by
        guesswork hides a real disconnection. ``related_object_count``
        tells a violation with no objects from one whose objects could
        not be read.

        Args:
            limit: How many violations to return; 0 returns all of them.
            offset: Index of the first violation, to page through a long
                list without running ERC again.
            include_suppressed: also list violations a No ERC marker
                covers or whose type is set to No Report; left out and
                only counted by default, as Altium's Messages panel does.

        Returns:
            ``{"violation_count", "offset", "returned", "truncated",
            "next_offset" (when truncated), "violations": [{"index",
            "description", "detail", "related_object_count",
            "related_objects": [{"kind", "document", "cross_probe"}]}]}``
        """
        bridge = get_bridge()
        params = {"limit": str(max(0, int(limit))),
                  "offset": str(max(0, int(offset))),
                  "include_suppressed": "true" if include_suppressed else "false"}
        result = await bridge.send_command_async(
            "generic.get_erc_violations", params)
        return _mark_erc_page(result)

    @mcp.tool()
    async def obj_highlight_net(
        net_name: str,
        clear_existing: bool = True,
        context: str = "",
    ) -> dict[str, Any]:
        """Highlight a net by name in the focused schematic or PCB document.

        On a board it sets the net's IsHighlighted. On a schematic it
        SELECTS the matching net labels, power ports, ports and sheet
        entries on the active sheet, replacing the selection. It follows
        the focused document; ``context`` forces "pcb" or "schematic".

        Args:
            net_name: Exact net name to highlight (e.g., "VCC", "GND",
                "NET_D0"). Returns NOT_FOUND if the net doesn't exist
                on the PCB, or `highlighted=0` on schematic if no
                primitive carries that net name on the active sheet
                (check other sheets).
            clear_existing: Clear existing highlights first (default True).
            context: Empty (default, follow the focused document),
                "schematic", or "pcb".

        Returns:
            Dict with success, net, context ("pcb" or "schematic"), and
            `highlighted` (count of matches, 1 for PCB, N for sch).
        """
        bridge = get_bridge()
        params = {
            "net_name": net_name,
            "clear_existing": "true" if clear_existing else "false",
        }
        if context:
            params["context"] = context
        result = await bridge.send_command_async(
            "generic.highlight_net",
            params,
        )
        return result

    @mcp.tool()
    async def obj_crossref_net(net_name: str) -> dict[str, Any]:
        """Compare the schematic vs PCB membership of a named net.

        The schematic's pins and the board's pads on the net, and the
        difference both ways: ``pcb_only`` pins are connections an Update
        PCB from Schematic would remove.

        Args:
            net_name: Exact net name as it appears in the schematic
                (case-sensitive).

        Returns:
            Dict with:
              - net_name
              - sch_pin_count, pcb_pin_count
              - matched (count of pins present on both sides)
              - sch_only_count, pcb_only_count
              - in_sync (True only when both sides list the same pins
                AND the net exists on at least one side)
              - sch_pins[], pcb_pins[], each entry is
                "Designator.PinNumber"
              - sch_only[] (missing from the board), pcb_only[] (on
                the board only)
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.crossref_net", {"net_name": net_name}
        )

    @mcp.tool()
    async def obj_clear_highlights() -> dict[str, Any]:
        """Clear all net highlights in the active schematic or PCB document.

        Returns:
            Dictionary confirming highlights were cleared
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.clear_highlights", {})
        return result

    @mcp.tool()
    async def proj_add_sheet(
        name: str = "NewSheet",
    ) -> dict[str, Any]:
        """Create a new schematic sheet and add it to the focused project.

        The sheet is created in the same directory as the project file.
        Refused (FILE_EXISTS) when a file of that name is already there,
        as one removed with proj_delete_sheet is: it would open with its
        old drawing.

        Args:
            name: Name for the new sheet (without .SchDoc extension).
                  Default "NewSheet".

        Returns:
            Dictionary with the path of the newly created sheet
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.add_sheet",
            {"name": name},
        )
        return result

    @mcp.tool()
    async def proj_delete_sheet(
        file_path: str,
    ) -> dict[str, Any]:
        """Remove a schematic sheet from the focused project.

        Safety check: refuses to remove the last remaining schematic sheet.
        The file is removed from the project but not deleted from disk, and
        closed unless it has unsaved edits. The project file is saved, its
        leftover sections taken out and read back, as proj_remove_document
        does.

        Args:
            file_path: Full path to the .SchDoc file to remove

        Returns:
            ``removed``, ``project_saved`` and ``in_project_file`` (the
            file on disk still lists it); ``success`` only when it does not.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.delete_sheet",
            {"file_path": file_path},
        )
        from ..config import get_config
        from ..prjpcb import finish_removal
        return finish_removal(
            result, str(get_config().workspace_dir / "backups" / "prjpcb"))

    @mcp.tool()
    async def obj_switch_view(
        mode: str = "3d",
    ) -> dict[str, Any]:
        """Toggle between 2D and 3D view for PCB documents.

        Args:
            mode: Target view mode, "3d" or "2d" (default "3d")

        Returns:
            Dictionary confirming the view switch
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.switch_view",
            {"mode": mode},
        )
        return result

    @mcp.tool()
    async def obj_refresh_document() -> dict[str, Any]:
        """Force a redraw/refresh of the current document.


        Returns:
            Dictionary confirming the refresh
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.refresh_document", {})
        return result

    @mcp.tool()
    async def proj_get_unconnected_pins() -> dict[str, Any]:
        """Find unconnected/floating pins in the focused project.

        Compiles the project first.

        Returns:
            Dictionary with "count" and "unconnected_pins" array, each entry
            having designator, pin_number, pin_name, and sheet path
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_unconnected_pins", {}, timeout=60.0
        )
        return result

    @mcp.tool()
    async def sch_stub_pins(
        stub_length_mils: float = 100,
        only_designators: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Draw a short wire stub + net label on every unconnected schematic pin.

        Each floating pin (as `proj_get_unconnected_pins` finds them) gets
        a wire out from its end and a net label "<designator>_<pin_number>",
        so ERC reports it as a named single-pin net.

        Args:
            stub_length_mils: Length of each stub wire in mils (default 100)
            only_designators: If given, only stub pins on these components

        Returns:
            Dictionary with "stubbed" and "failed" counts, plus the pin list
            that was acted on
        """
        bridge = get_bridge()
        floating = await bridge.send_command_async(
            "generic.get_unconnected_pins", {}, timeout=60.0
        )
        pins = floating.get("unconnected_pins", []) if isinstance(floating, dict) else []
        if only_designators:
            wanted = {d.upper() for d in only_designators}
            pins = [p for p in pins if str(p.get("designator", "")).upper() in wanted]
        if not pins:
            return {"stubbed": 0, "failed": 0, "pins": [], "note": "no unconnected pins found"}

        records = []
        for p in pins:
            desig = str(p.get("designator", "")).strip()
            pin_num = str(p.get("pin_number", "")).strip()
            if not desig or not pin_num:
                continue
            label = f"{desig}_{pin_num}"
            records.append(f"{desig},{pin_num},{label}")

        result = await bridge.send_command_async(
            "generic.stub_pins",
            {"pins": "|".join(records), "stub_length_mils": _mils(stub_length_mils)},
            timeout=120.0,
        )
        if isinstance(result, dict):
            result["pins"] = records
        return result

    @mcp.tool()
    async def sch_place_rectangle(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        solid: bool = False,
        line_width: int = 1,
    ) -> dict[str, Any]:
        """Place a rectangle shape on the active schematic (decorative only).

        Args:
            x1, y1, x2, y2: Opposite corners in mils
            solid: Fill the rectangle (default False = outline only)
            line_width: 1 (small), 2 (medium), 3 (large)

        Returns:
            Dictionary confirming rectangle placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_rectangle",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "solid": "true" if solid else "false",
                "line_width": str(line_width),
            },
        )
        return result

    @mcp.tool()
    async def sch_place_line(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        line_width: int = 1,
    ) -> dict[str, Any]:
        """Place a decorative line on the active schematic.

        This is a graphic line, not a signal wire. Use `sch_place_wires`
        for electrical connections. Use this for hand-drawn borders,
        arrows, diagram overlays.

        Args:
            x1, y1, x2, y2: Endpoints in mils
            line_width: 1 (small), 2 (medium), 3 (large)

        Returns:
            Dictionary confirming line placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_line",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "line_width": str(line_width),
            },
        )
        return result

    @mcp.tool()
    async def sch_place_note(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        text: str,
    ) -> dict[str, Any]:
        """Place a text-box note on the active schematic.


        Args:
            x1, y1, x2, y2: Note box corners in mils
            text: Note content

        Returns:
            Dictionary confirming note placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_note",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "text": text,
            },
        )
        return result

    @mcp.tool()
    async def sch_place_sheet_symbol(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        sheet_file_name: str,
        sheet_name: str = "",
    ) -> dict[str, Any]:
        """Place a sheet symbol linking to a child schematic document.

        sheet_file_name must match a .SchDoc in the project exactly, or
        the symbol is dangling. Then add its entries with
        `sch_place_sheet_entry`.

        Args:
            x1, y1, x2, y2: Sheet symbol box corners in mils
            sheet_file_name: Child SchDoc filename (e.g. "PSU.SchDoc")
            sheet_name: Display label (default: file name without extension)

        Returns:
            Dictionary confirming sheet symbol placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_sheet_symbol",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "sheet_file_name": sheet_file_name,
                "sheet_name": sheet_name,
            },
        )
        return result

    @mcp.tool()
    async def sch_autoposition_labels(
        names: Optional[list[str]] = None,
        method: str = "rotate",
    ) -> dict[str, Any]:
        """Put part designators and comments, and sheet-symbol names and file
        names, back where Altium positions them, on the active schematic.

        For labels left at the sheet origin (the bottom-left corner) or
        anywhere odd after a scripted placement. Each label's auto-position
        is turned back on and Altium is made to position it, as rotating a
        part and rotating it back does by hand. A label moved on purpose
        (obj_modify Designator.Location.X) loses that position.

        Args:
            names: Designators and sheet names to fix; none means every
                part and sheet symbol on the sheet.
            method: What makes Altium reposition: "rotate" (90 degrees and
                back), "move" (one grid step and back) or "modify" (an
                edit with no change).

        Returns:
            ``count`` and per object ``name``, ``kind``, ``location`` and
            every label ``before`` and ``after`` (x, y in mils and
            autoposition).
        """
        names = [str(n).strip() for n in (names or []) if str(n).strip()]
        bad = [n for n in names if "|" in n]
        if bad:
            return {"ok": False, "reason": f"a name cannot contain '|': {bad}"}
        if method not in ("rotate", "move", "modify"):
            return {"ok": False, "reason": "method is rotate, move or modify"}
        return await get_bridge().send_command_async(
            "generic.autoposition_labels",
            {"names": "|".join(names), "method": method})

    @mcp.tool()
    async def sch_place_sheet_entry(
        sheet_name: str,
        entry_name: str,
        io_type: str = "unspecified",
        side: str = "left",
        distance_from_top: float = 100,
        font_id: int = 0,
    ) -> dict[str, Any]:
        """Place a sheet entry port on an existing sheet symbol.

        Sheet entries are the ports on a sheet symbol that map to
        hierarchical ports inside the child sheet. Name must match a
        port defined in the child .SchDoc for electrical continuity.

        Args:
            sheet_name: SheetName of the target sheet symbol
            entry_name: Port name (must match a port in the child sheet)
            io_type: "input" | "output" | "bidirectional" | "unspecified"
            side: "left" | "right" | "top" | "bottom"
            distance_from_top: Position along the chosen side in mils
            font_id: The entry's font. 0 takes the font the sheet's
                entries already use, else its net labels' font.

        Returns:
            Dictionary confirming sheet entry placement, with the
            ``font_id`` read back.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_sheet_entry",
            {
                "sheet_name": sheet_name,
                "entry_name": entry_name,
                "io_type": io_type,
                "side": side,
                "distance_from_top": _mils(distance_from_top),
                "font_id": str(int(font_id)),
            },
        )
        return result

    @mcp.tool()
    async def sch_place_bus_entry(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> dict[str, Any]:
        """Place a bus entry (45 degree stub) connecting a wire to a bus.

        Bus entries are the angled stubs that tap a single-signal wire
        off a bus line. Start point should sit on the bus; end point
        should sit on the wire (both usually at 45 degrees offset).

        Args:
            x1, y1: Start coordinates in mils (typically on the bus)
            x2, y2: End coordinates in mils (typically on the wire)

        Returns:
            Dictionary confirming bus entry placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_bus_entry",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        return result

    @mcp.tool()
    async def sch_set_sheet_size(
        style: str = "",
        custom_width: int = 0,
        custom_height: int = 0,
        orientation: str = "",
    ) -> dict[str, Any]:
        """Set the sheet size / template style of the active schematic.

        Changes SheetStyle on the current SchDoc. Use a named style
        (A-E, A0-A4, Letter, Legal, Tabloid) or "Custom" with explicit
        width and height in mils. Give ``style``, ``orientation`` or both.

        Args:
            style: Sheet size name, e.g. "A", "A3", "A4", "Letter", "Custom"
            custom_width: Custom sheet width in mils (only if style="Custom")
            custom_height: Custom sheet height in mils (only if style="Custom")
            orientation: "landscape" or "portrait"; empty leaves it as is

        Returns:
            Dictionary confirming the style change
        """
        orientation = orientation.strip().lower()
        if orientation not in ("", "landscape", "portrait"):
            return {"success": False, "error": (
                f"orientation must be landscape or portrait, not {orientation!r}")}
        if not style.strip() and not orientation:
            return {"success": False, "error": "give style, orientation or both"}
        params = {
            "style": style,
            "custom_width": str(custom_width),
            "custom_height": str(custom_height),
        }
        if orientation:
            params["orientation"] = orientation
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.set_sheet_size", params)
        if isinstance(result, dict) and result.get("success") is not False:
            result["reopened"] = await _reopen_sheet(bridge)
        return result

    @mcp.tool()
    async def sch_set_sheet_options(
        zones_x: Optional[int] = None,
        zones_y: Optional[int] = None,
        margin_width: Optional[float] = None,
        border_style: Optional[int] = None,
        reference_zones: Optional[bool] = None,
        show_template_graphics: Optional[bool] = None,
        custom_zones_x: Optional[int] = None,
        custom_zones_y: Optional[int] = None,
        custom_margin_width: Optional[float] = None,
    ) -> dict[str, Any]:
        """Read, or set, the active sheet's reference zones, margin, border
        style and template graphics. Call with nothing to read.

        On a standard sheet size the zones and margin follow the size, so
        zones_x, zones_y and margin_width do not take; the custom_* ones
        apply to a custom-size sheet. Every value is read back; one that
        did not take is in ``not_applied``.

        Args:
            zones_x, zones_y: Reference zones across and down (from 1).
            margin_width: Sheet margin in mils.
            border_style: Altium's border style number (read one back
                first; 0 is the standard border).
            reference_zones: Show the zone letters and numbers.
            show_template_graphics: Show the template's graphics.
            custom_zones_x, custom_zones_y, custom_margin_width: The same
                for a custom-size sheet.

        Returns:
            ``changed``, ``options`` (read back after any write) and
            ``not_applied``.
        """
        params: dict[str, Any] = {}
        for key, value in (("zones_x", zones_x), ("zones_y", zones_y),
                           ("custom_zones_x", custom_zones_x),
                           ("custom_zones_y", custom_zones_y),
                           ("border_style", border_style)):
            if value is not None:
                if value < (0 if key == "border_style" else 1):
                    return {"ok": False, "reason": f"{key} is out of range"}
                params[key] = str(int(value))
        for key, value in (("margin_width", margin_width),
                           ("custom_margin_width", custom_margin_width)):
            if value is not None:
                if value < 0:
                    return {"ok": False, "reason": f"{key} is mils from 0"}
                params[key] = str(value)
        for key, value in (("reference_zones", reference_zones),
                           ("show_template_graphics", show_template_graphics)):
            if value is not None:
                params[key] = "true" if value else "false"
        reply = await get_bridge().send_command_async("generic.sheet_options", params)
        if isinstance(reply, dict) and params:
            got = reply.get("options") or {}
            keys = {"margin_width": "margin_width_mils",
                    "custom_margin_width": "custom_margin_width_mils"}
            missed = []
            for key, sent in params.items():
                have = got.get(keys.get(key, key))
                if isinstance(have, bool):
                    ok = have == (sent == "true")
                else:
                    try:
                        ok = abs(float(have) - float(sent)) < 0.01
                    except (TypeError, ValueError):
                        ok = False
                if not ok:
                    missed.append(key)
            reply["not_applied"] = missed
        return reply

    @mcp.tool()
    async def sch_audit_outside_sheet(limit: int = 100,
                                      apply: bool = False) -> dict[str, Any]:
        """Find anything drawn outside the active schematic's sheet, and
        with ``apply`` bring it back inside.

        Reads the sheet's REAL frame from Altium (an A3 frame is 15500 x
        11100 mils, not A3 paper), every object's bounding box, and the
        template's own graphics, which become the title-block keep-out
        rather than objects. Reports what is past the border or over the
        title block, then a verdict:
        - ``fits``: nothing outside.
        - ``shift``: the drawing fits once moved by ``dx``, ``dy``.
        - ``enlarge``: it does not fit; ``to_size`` is the first larger
          sheet of the SAME family (A4 -> A3, ANSI A -> B) that holds it.
        - ``too_big``: nothing in the family holds it.
        A shift is whole 100 mil steps, so everything stays on the grid.

        ``apply`` (default False, which changes nothing) fixes it the
        preferred way first: a ``shift`` moves EVERY object on the sheet
        by dx, dy, wires and labels with the parts, so no connection
        changes. An ``enlarge`` on a sheet without a template sets the
        larger sheet size, then shifts if that is still needed. A sheet
        drawn from a template is not enlarged here: ``to_template`` names
        the same-family template to move it to. ``actions`` lists what was
        done and the reply is the audit after it.
        """
        from eda_agent.design.sheet_fit import Box, fit_report

        bridge = get_bridge()

        async def audit() -> dict[str, Any]:
            frame = await bridge.send_command_async(
                "generic.get_sheet_frame", {"limit": "20000"})
            if not isinstance(frame, dict) or "frame_x" not in frame:
                return {"ok": False, "reason": "could not read the sheet frame",
                        "reply": frame}
            return _outside_sheet_report(frame, Box, fit_report, limit)

        report = await audit()
        if not apply or not report.get("ok"):
            return report
        actions: list[dict[str, Any]] = []
        first = report.get("verdict")
        if first == "enlarge":
            if report.get("template"):
                report["actions"] = actions
                report["not_applied"] = (
                    "the sheet uses a template; move it to "
                    f"{report.get('to_template') or 'a larger template'} "
                    "first, then run this again to shift what remains")
                return report
            to = str(report.get("to_size", ""))
            if to.upper() not in _SHEET_STYLES_SETTABLE:
                report["actions"] = actions
                report["not_applied"] = f"sheet size {to} cannot be set by script"
                return report
            reply = await bridge.send_command_async(
                "generic.set_sheet_size", {"style": to, "custom_width": "0",
                                           "custom_height": "0"})
            actions.append({"action": "set_sheet_size", "to": to,
                            "reply": reply,
                            "reopened": await _reopen_sheet(bridge)})
            report = await audit()
        if report.get("ok") and report.get("verdict") == "shift":
            dx, dy = int(report["dx"]), int(report["dy"])
            reply = await bridge.send_command_async(
                "generic.shift_sheet", {"dx": str(dx), "dy": str(dy)})
            actions.append({"action": "shift", "dx": dx, "dy": dy,
                            "reply": reply})
            report = await audit()
        report["actions"] = actions
        report["verdict_before"] = first
        return report

    @mcp.tool()
    async def sch_place_bus(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> dict[str, Any]:
        """Place a bus segment between two XY coordinates on the active schematic.

        Name it with a net label in bus syntax (DATA[0..7]), then add bus
        entries and wires.

        Args:
            x1: Start X coordinate in mils
            y1: Start Y coordinate in mils
            x2: End X coordinate in mils
            y2: End Y coordinate in mils

        Returns:
            Dictionary confirming bus placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_bus",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        return result

    @mcp.tool()
    async def sch_place_net_label(
        text: str,
        x: float,
        y: float,
        orientation: int = 0,
    ) -> dict[str, Any]:
        """Place a net label at coordinates on the active schematic.

        The label takes the font most of the sheet's net labels use, and
        Altium's native net-label maroon (Color 128) always.

        Args:
            text: Net name for the label (e.g., "VCC", "GND", "SDA")
            x: X coordinate in mils
            y: Y coordinate in mils
            orientation: Label rotation (0=0deg, 1=90deg, 2=180deg, 3=270deg)

        Returns:
            Dictionary confirming placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_net_label",
            {
                "text": text,
                "x": str(x),
                "y": str(y),
                "orientation": str(orientation),
            },
        )
        return result

    @mcp.tool()
    async def sch_place_port(
        name: str,
        x: float,
        y: float,
        style: str = "right",
        io_type: str = "bidirectional",
        width: float = 0,
        font_id: int = 0,
    ) -> dict[str, Any]:
        """Place a port on the active schematic for inter-sheet connectivity.

        Args:
            name: Port name (maps to net name)
            x: X coordinate in mils
            y: Y coordinate in mils
            style: Arrow style, "none", "left", "right", "left_right"
            io_type: I/O direction, "unspecified", "output", "input", "bidirectional"
            width: Port length in mils. Auto-size is turned off so it
                holds; 0 leaves the port sizing itself to its name.
            font_id: The port's font. 0 takes the font the sheet's ports
                already use, else its net labels' font.

        Returns:
            ``font_id``, ``width`` and ``auto_size`` read back.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_port",
            {
                "name": name,
                "x": _mils(x),
                "y": _mils(y),
                "style": style,
                "io_type": io_type,
                "width": _mils(width),
                "font_id": str(int(font_id)),
            },
        )
        return result

    @mcp.tool()
    async def sch_tidy_port_references(
        sheet_path: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Move each port's cross-reference off its wire, to the free end.

        A port moved without its wire keeps its "Sheet[zone]" reference on
        the wired end, over the wire. Each reference goes 20 mil past the
        free end. Vertical ports are ``skipped``. The sheet is not saved.

        Args:
            sheet_path: The .SchDoc.
            dry_run: True (default) only lists what would move.

        Returns:
            ``to_move`` (port, which end is wired and how that was decided,
            x and justification now and new), ``already_right``,
            ``skipped`` (with why), ``orphan_references``; once written
            ``moved`` and ``not_moved``.
        """
        from ..design.port_reference_side import place_references

        bridge = get_bridge()
        scope = scope_to_wire(f"doc:{sheet_path}")

        async def query(kind: str, fields: str, where: str = "") -> list:
            reply = await bridge.send_command_async("generic.query_objects", {
                "scope": scope, "object_type": kind, "filter": where,
                "properties": fields})
            return (reply.get("objects") or []) if isinstance(reply, dict) else []

        ports = await query("ePort", "Name,Location.X,Location.Y,Width,Style,ConnectedEnd")
        references = await query(
            "eParameter", "Name,Text,Location.X,Location.Y,Justification", "Name=CrossRef")
        result = place_references(
            ports, await query("eWire", "Vertices"), references,
            await query("ePin", "ConnectionX,ConnectionY"))
        result.update(sheet_path=sheet_path, dry_run=dry_run,
                      ports=len(ports), references=len(references))
        if not references:
            result["note"] = ("The sheet has no CrossRef parameters: Port Cross "
                              "References is off, or the project was not compiled "
                              "after turning it on.")
        if dry_run:
            return result
        moved, not_moved = [], []
        for entry in result["to_move"]:
            reply = await bridge.send_command_async("generic.modify_objects", {
                "scope": scope, "object_type": "eParameter",
                "filter": "|".join(["Name=CrossRef", f"Location.X={_mils(entry['x'])}",
                                    f"Location.Y={_mils(entry['y'])}"]),
                "set": "|".join([f"Location.X={_mils(entry['new_x'])}",
                                 f"Justification={entry['new_justification']}"])})
            matched = reply.get("matched", 0) if isinstance(reply, dict) else 0
            (moved if matched == 1 else not_moved).append(
                {"port": entry["port"], "reply": reply})
        result.update(moved=moved, not_moved=not_moved)
        return result

    @mcp.tool()
    async def sch_place_power_port(
        text: str,
        x: float,
        y: float,
        style: str = "circle",
        orientation: int = -1,
        font_id: int = 0,
    ) -> dict[str, Any]:
        """Place a power port symbol (VCC, GND, etc.) on the active schematic.

        Args:
            text: Net name for the power port (e.g., "VCC", "GND", "+3V3")
            x: X coordinate in mils
            y: Y coordinate in mils
            style: Symbol style:
                "circle", circle symbol (default, typical for VCC)
                "arrow", arrow symbol
                "bar", bar/line symbol
                "wave", wave symbol
                "gnd_power", power ground symbol
                "gnd_signal", signal ground symbol
                "gnd_earth", earth ground symbol
                Any other name is refused rather than drawn as a circle.
            orientation: which way the glyph points from the connection
                point: 0=right, 1=up, 2=left, 3=down. Leave at -1 to let
                the bridge decide by style: the three ground glyphs point
                down, the supply glyphs (circle, arrow, bar, wave) point up.
            font_id: The net name's font. 0 takes the font the sheet's
                power ports already use, else its net labels' font.

        Returns:
            The placement, with the ``font_id`` the power port got.
        """
        style = (style or "circle").strip().lower()
        if style not in POWER_PORT_STYLES:
            return {
                "success": False,
                "error": f"Unknown power port style {style!r}. Use one of: "
                         + ", ".join(POWER_PORT_STYLES),
            }
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_power_port",
            {
                "text": text,
                "x": _mils(x),
                "y": _mils(y),
                "style": style,
                "orientation": str(int(orientation)),
                "font_id": str(int(font_id)),
            },
        )
        return result

    @mcp.tool()
    async def sch_get_sheet_parameters(
        file_path: str = "",
    ) -> dict[str, Any]:
        """Get title block parameters (title, revision, date, etc.) from a schematic sheet.

        Reads all parameters from the sheet without modifying anything.

        Args:
            file_path: Full path to a specific .SchDoc file. If empty, reads
                       from the active document.

        Returns:
            Dictionary with "count" and "parameters" array, each having
            "name" and "value"
        """
        bridge = get_bridge()
        params = {}
        if file_path:
            params["file_path"] = file_path
        result = await bridge.send_command_async(
            "generic.get_sheet_parameters", params
        )
        return result

    @mcp.tool()
    async def obj_copy(
        object_type: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Copy matching schematic objects to the clipboard.

        Selects objects matching the filter, copies them to the system
        clipboard, then clears selection.

        Args:
            object_type: Altium schematic object type (see `obj_query`)
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with count of copied objects
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.copy_objects",
            {"object_type": object_type, "filter": filter},
        )
        return result

    @mcp.tool()
    async def obj_count(
        object_type: str,
        scope: str = "active_doc",
        filter: str = "",
    ) -> dict[str, Any]:
        """Quick count of objects by type, faster than `obj_query` when you only need the count.

        Args:
            object_type: Altium object type constant (see `obj_query` for options)
            scope: "active_doc" (default), "project", or
                "doc:C:\\path\\Sheet.SchDoc" for a specific loaded sheet.
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with "count" (and "sheets_processed" for project scope)
        """
        bad = filter_problem(filter)
        if bad:
            return bad_filter(bad, count=0)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_object_count",
            {"object_type": object_type, "scope": scope_to_wire(scope), "filter": filter},
        )
        # The PCB active-doc path runs the query handler, which ships a list of
        # (empty, since no properties were requested) objects alongside the
        # count. This tool only promises the count -- drop the wasteful array.
        if isinstance(result, dict):
            result.pop("objects", None)
        return result

    @mcp.tool()
    async def sch_place_no_erc(
        x: float,
        y: float,
    ) -> dict[str, Any]:
        """Place a No-ERC marker at coordinates to suppress the ERC violations
        there.

        Put it on the pin's wire end (its connection point), not on the
        body side. The marker is placed active and
        suppressing ALL violations, as Altium's own placed No ERC does; to
        narrow it, write ErrorKindSetToSuppress / SuppressAll=false on the
        eNoERC object with obj_modify. obj_query reads SuppressAll,
        IsActive, ErrorKindSetToSuppress, ConnectionPairsToSuppress and
        SuppressableErrors.

        Args:
            x: X coordinate in mils
            y: Y coordinate in mils

        Returns:
            Dictionary confirming placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_no_erc",
            {"x": str(x), "y": str(y)},
        )
        return result

    @mcp.tool()
    async def sch_place_junction(
        x: float,
        y: float,
    ) -> dict[str, Any]:
        """Place a MANUAL junction at coordinates on the active schematic.

        A T NEEDS NONE: Altium draws a junction itself where a wire ends
        on another. Use this only where two wires CROSS and must connect;
        a plain crossing is not a connection.

        Args:
            x: X coordinate in mils
            y: Y coordinate in mils

        Returns:
            Dictionary confirming placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_junction",
            {"x": str(x), "y": str(y)},
        )
        return result

    @mcp.tool()
    async def obj_get_document_info() -> dict[str, Any]:
        """Get comprehensive info about the active document.

        For schematics: file path, kind, sheet size, title block visibility,
        snap grid, visible grid, unit system, and custom dimensions.
        For PCB: file path, kind, origin, snap grid.

        Returns:
            Dictionary with document properties
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.get_document_info", {})
        return result

    @mcp.tool()
    async def obj_set_grid(
        snap_grid: int = 0,
        visible_grid: int = 0,
        snap_grid_on: Optional[bool] = None,
        visible_grid_on: Optional[bool] = None,
        hotspot_grid: int = 0,
        hotspot_grid_on: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Set the snap, visible and electrical grids for the active
        schematic. pcb_set_grids sets a board's grids.

        Args:
            snap_grid: Snap grid size in mils (0 = don't change)
            visible_grid: Visible grid size in mils (0 = don't change)
            snap_grid_on: Switch the snap grid on or off (None = don't change)
            visible_grid_on: Switch the visible grid on or off (None = don't change)
            hotspot_grid: Electrical grid in mils, how far a wire end is
                pulled onto a pin or wire (0 = don't change)
            hotspot_grid_on: Switch the electrical grid on or off (None =
                don't change)

        Returns:
            Dictionary with the resulting grid sizes and on/off state
        """
        bridge = get_bridge()
        params: dict[str, str] = {}
        if snap_grid > 0:
            params["snap_grid"] = str(snap_grid)
        if visible_grid > 0:
            params["visible_grid"] = str(visible_grid)
        if snap_grid_on is not None:
            params["snap_grid_on"] = "true" if snap_grid_on else "false"
        if visible_grid_on is not None:
            params["visible_grid_on"] = "true" if visible_grid_on else "false"
        if hotspot_grid > 0:
            params["hotspot_grid"] = str(hotspot_grid)
        if hotspot_grid_on is not None:
            params["hotspot_grid_on"] = "true" if hotspot_grid_on else "false"
        result = await bridge.send_command_async("generic.set_grid", params)
        return result

    @mcp.tool()
    async def sch_optimize_wires() -> dict[str, Any]:
        """Tidy the wires on the active sheet with Altium's own wire
        optimiser: wires that continue one another are joined and
        overlapping pieces trimmed. Connectivity is unchanged.

        Returns:
            ``sheet``, ``wires_before`` / ``wires_after`` and
            ``vertices_before`` / ``vertices_after``.
        """
        return await get_bridge().send_command_async("generic.optimize_wires", {})

    @mcp.tool()
    async def sch_reference_zones(
        kinds: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Where each part, net label, port, power port, sheet symbol and
        off-sheet connector sits on the active sheet, as the border zone
        a printed drawing is read by ("B3"). The zone is Altium's own; a
        sheet without zones gives an empty one.

        Args:
            kinds: Any of "component", "net_label", "port", "power_port",
                "sheet_symbol", "cross_sheet"; None for all.

        Returns:
            ``sheet``, ``count`` and ``objects``: each ``kind``, ``text``
            (designator, label, port or sheet name), ``zone`` and ``x`` /
            ``y`` in mils.
        """
        allowed = ("component", "net_label", "port", "power_port", "sheet_symbol",
                   "cross_sheet")
        params: dict[str, Any] = {}
        if kinds:
            wanted = [k.strip().lower() for k in kinds]
            bad = [k for k in wanted if k not in allowed]
            if bad:
                return {"ok": False, "reason": f"unknown kind {bad[0]}; kinds are "
                        + ", ".join(allowed)}
            params["kinds"] = ",".join(wanted)
        return await get_bridge().send_command_async("generic.reference_zones", params)

    @mcp.tool()
    async def sch_editor_options(
        snap_to_electrical_grid: Optional[bool] = None,
        electrical_grid_distance: Optional[int] = None,
        drag_orthogonal: Optional[bool] = None,
        components_cut_wires: Optional[bool] = None,
        optimize_wires: Optional[bool] = None,
        single_slash_negation: Optional[bool] = None,
        convert_special_strings: Optional[bool] = None,
        mark_manual_parameters: Optional[bool] = None,
        show_pin_direction: Optional[bool] = None,
        auto_zoom: Optional[bool] = None,
        click_clears_selection: Optional[bool] = None,
        double_click_runs_inspector: Optional[bool] = None,
        undo_levels: Optional[int] = None,
        pin_name_margin: Optional[int] = None,
        pin_number_margin: Optional[int] = None,
        multi_part_naming: Optional[int] = None,
        increment_primary: Optional[str] = None,
        increment_secondary: Optional[str] = None,
        power_ground_name: Optional[str] = None,
        signal_ground_name: Optional[str] = None,
        earth_name: Optional[str] = None,
        expand_designators: Optional[bool] = None,
        expand_net_labels: Optional[bool] = None,
        expand_ports: Optional[bool] = None,
        expand_sheet_number: Optional[bool] = None,
        expand_document_number: Optional[bool] = None,
        watermark_read_only_sheets: Optional[bool] = None,
        always_drag: Optional[bool] = None,
        use_printer_fonts: Optional[bool] = None,
        default_template: Optional[str] = None,
        increment_drops_leading_zero: Optional[bool] = None,
        ctrl_double_click_goes_down: Optional[bool] = None,
        confirm_selection_memory_clear: Optional[bool] = None,
        allow_device_sheet_editing: Optional[bool] = None,
        watermark_device_sheets: Optional[bool] = None,
        cutter_grid_multiple: Optional[int] = None,
        cutter_fixed_length: Optional[int] = None,
        physical_view_dim_level: Optional[int] = None,
    ) -> dict[str, Any]:
        """Read, or set, the schematic editor's preferences.

        These are Altium's own preferences and apply to every sheet. Call
        with nothing to read; every option set is read back.

        Args:
            snap_to_electrical_grid: Pull wire ends onto pins and wires.
            electrical_grid_distance: How far they are pulled (Altium's
                number).
            drag_orthogonal: Wires stay at right angles when dragging.
            components_cut_wires: A part dropped on a wire cuts it.
            optimize_wires: Join and trim overlapping wires.
            single_slash_negation: One leading backslash bars the
                whole name.
            convert_special_strings: Show special strings as values.
            mark_manual_parameters: Mark parameters moved by hand.
            show_pin_direction: Draw each pin's direction arrow.
            auto_zoom: Zoom to a part found by a cross-probe.
            click_clears_selection: A click on empty space clears it.
            double_click_runs_inspector: Double-click opens the
                Inspector.
            undo_levels: How many steps undo keeps.
            pin_name_margin: Gap between the body edge and a pin name,
                in mils.
            pin_number_margin: Gap from the body edge to a pin number,
                in mils.
            multi_part_naming: How multi-part designators are written
                (Altium's number: U1A, U1:1 ...).
            increment_primary: The step for repeated placement, e.g. "1".
            increment_secondary: The second step, e.g. "1".
            power_ground_name: Net name of a new power ground port.
            signal_ground_name: Net name of a new signal ground port.
            earth_name: Net name of a new earth port.
            expand_designators / expand_net_labels / expand_ports /
                expand_sheet_number / expand_document_number: In a
                multi-channel design, show these as they are on each
                channel rather than as drawn.
            watermark_read_only_sheets: Mark sheets that cannot be
                edited.
            always_drag: Moving a part drags its wires along.
            use_printer_fonts: Draw text with the printer's fonts.
            default_template: The template file new sheets start from.
            increment_drops_leading_zero: Repeated placement drops a
                leading zero (R09 is followed by R10, not R010).
            ctrl_double_click_goes_down: Ctrl + double-click on a sheet
                symbol opens the sheet below.
            confirm_selection_memory_clear: Ask before a selection
                memory is cleared.
            allow_device_sheet_editing: Device sheets may be edited.
            watermark_device_sheets: Mark device sheets.
            cutter_grid_multiple: The wire cutter's length in snap grids.
            cutter_fixed_length: The wire cutter's fixed length, in mils.
            physical_view_dim_level: How far the physical view dims what
                is not in focus (Altium's number).

        Returns:
            ``options`` (each option by these names), ``changed``,
            ``not_applied``, ``refused`` and ``unknown``.
        """
        values = {
            "snap_to_electrical_grid": snap_to_electrical_grid,
            "electrical_grid_distance": electrical_grid_distance,
            "drag_orthogonal": drag_orthogonal,
            "components_cut_wires": components_cut_wires,
            "optimize_wires": optimize_wires,
            "single_slash_negation": single_slash_negation,
            "convert_special_strings": convert_special_strings,
            "mark_manual_parameters": mark_manual_parameters,
            "show_pin_direction": show_pin_direction,
            "auto_zoom": auto_zoom,
            "click_clears_selection": click_clears_selection,
            "double_click_runs_inspector": double_click_runs_inspector,
            "undo_levels": undo_levels,
            "pin_name_margin": pin_name_margin,
            "pin_number_margin": pin_number_margin,
            "multi_part_naming": multi_part_naming,
            "increment_primary": increment_primary,
            "increment_secondary": increment_secondary,
            "power_ground_name": power_ground_name,
            "signal_ground_name": signal_ground_name,
            "earth_name": earth_name,
            "expand_designators": expand_designators,
            "expand_net_labels": expand_net_labels,
            "expand_ports": expand_ports,
            "expand_sheet_number": expand_sheet_number,
            "expand_document_number": expand_document_number,
            "watermark_read_only_sheets": watermark_read_only_sheets,
            "always_drag": always_drag,
            "use_printer_fonts": use_printer_fonts,
            "default_template": default_template,
            "increment_drops_leading_zero": increment_drops_leading_zero,
            "ctrl_double_click_goes_down": ctrl_double_click_goes_down,
            "confirm_selection_memory_clear": confirm_selection_memory_clear,
            "allow_device_sheet_editing": allow_device_sheet_editing,
            "watermark_device_sheets": watermark_device_sheets,
            "cutter_grid_multiple": cutter_grid_multiple,
            "cutter_fixed_length": cutter_fixed_length,
            "physical_view_dim_level": physical_view_dim_level,
        }
        for key, value in values.items():
            if isinstance(value, int) and not isinstance(value, bool) and value < 0:
                return {"ok": False, "reason": f"{key} is 0 or more"}
        # Altium keeps the pin margins at 10000 to the mil, like every
        # schematic length (measured: its defaults read 500000 and 800000,
        # 50 and 80 mils).
        for key in _SCH_MARGINS:
            if values[key] is not None:
                values[key] = values[key] * 10000
        reply = await run_options("generic.sch_editor_options", _SCH_EDITOR_OPTIONS, values)
        options = reply.get("options") if isinstance(reply, dict) else None
        if isinstance(options, dict):
            for key in _SCH_MARGINS:
                if isinstance(options.get(key), int):
                    options[key] = options[key] / 10000
        return reply

    @mcp.tool()
    async def sch_set_units(
        unit: str,
    ) -> dict[str, Any]:
        """Set the unit system for the active schematic.

        `obj_get_document_info` reads the current one.

        Args:
            unit: one of
                "mil"           , imperial, mils (0.001 in)
                "inch"          , imperial, inches
                "dxp"           , DXP default
                "auto_imperial" , auto-scaled imperial display
                "mm"            , metric, millimetres
                "cm"            , metric, centimetres
                "m"             , metric, metres
                "auto_metric"   , auto-scaled metric display

        Returns:
            Dictionary confirming the change, with the resulting
            unit_system ("imperial" or "metric").
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.set_sch_units", {"unit": unit}
        )
        return result

    @mcp.tool()
    async def sch_add_directive(
        x: float,
        y: float,
        param_name: str,
        param_value: str,
    ) -> dict[str, Any]:
        """Place a parameter-set directive at (x, y) on the active schematic.

        Attaches one ``name=value`` parameter to the wire or net under it,
        e.g. ``DifferentialPair=USB`` or ``NetClass=HighSpeed``.

        Args:
            x, y: Location in mils, place ON the target wire/net.
            param_name: Parameter name (e.g. "NetClass", "DifferentialPair")
            param_value: Parameter value

        Returns:
            Dictionary confirming placement.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_directive",
            {
                "x": str(x),
                "y": str(y),
                "param_name": param_name,
                "param_value": param_value,
            },
        )
        return result

    @mcp.tool()
    async def sch_get_directives() -> dict[str, Any]:
        """Enumerate parameter-set directives on the active schematic sheet.

        Each with its location and the parameters it carries.

        Returns:
            Dictionary with ``directives`` array, each entry has
            ``name``, ``x``, ``y``, and a ``parameters`` list of
            ``{name, value}`` objects, plus ``count``.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.get_directives", {})
        return result

    @mcp.tool()
    async def sch_place_image(
        image_path: str,
        x: float,
        y: float,
        width: float = 500,
        height: float = 500,
    ) -> dict[str, Any]:
        """Place an image/logo on the active schematic.

        Args:
            image_path: Full path to the image file (BMP, JPG, PNG, etc.)
            x: X coordinate in mils (bottom-left corner)
            y: Y coordinate in mils (bottom-left corner)
            width: Image width in mils (default 500)
            height: Image height in mils (default 500)

        Returns:
            Dictionary confirming placement with path and dimensions
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_image",
            {
                "image_path": image_path,
                "x": str(x),
                "y": str(y),
                "width": str(width),
                "height": str(height),
            },
        )
        return result

    @mcp.tool()
    async def proj_replace_component(
        designator: str,
        new_lib_ref: str,
        new_library: str = "",
        library_path: str = "",
        allow_pin_changes: bool = False,
    ) -> dict[str, Any]:
        """Replace a part on the active sheet with another library part.

        With ``library_path`` (a .SchLib) the symbol itself is swapped: the
        new symbol goes where the old one is, every pin's connection point
        is compared, and only then is the old part removed. The designator
        and UniqueId carry over, so the board keeps its link. A pin that
        would move or disappear refuses the swap with nothing changed
        (``PIN_MISMATCH``), unless ``allow_pin_changes``.

        Without ``library_path`` only the library link changes
        (LibReference, DesignItemId, library name); the symbol, pins and
        graphics on the sheet stay the old part's, and the reply says
        ``symbol_replaced: false``.

        Args:
            designator: Component designator to replace (e.g., "U1", "R3")
            new_lib_ref: New library reference / component name
            new_library: New source library name for the relink (optional,
                keeps current if empty)
            library_path: .SchLib holding ``new_lib_ref``; swaps the symbol.
            allow_pin_changes: Swap even when pins move or disappear; the
                wires on those pins are then left unconnected.

        Returns:
            ``symbol_replaced``. After a swap: ``pins_kept``,
            ``pins_missing``, ``pins_moved``, ``pins_added``,
            ``parameters_not_on_new_symbol`` (old parameters the new
            symbol lacks, not copied) and ``unique_id_kept``.
        """
        bridge = get_bridge()
        params = {
            "designator": designator,
            "new_lib_ref": new_lib_ref,
        }
        if new_library:
            params["new_library"] = new_library
        if library_path:
            params["library_path"] = library_path
            params["allow_pin_changes"] = "true" if allow_pin_changes else "false"
        result = await bridge.send_command_async(
            "generic.replace_component", params
        )
        return result

    @mcp.tool()
    async def sch_relink_components(
        designators: list[str],
        library_path: str = "",
        lib_ref: str = "",
        dblib_table: str = "",
        dblib_key: str = "",
        footprint: str = "",
        footprint_library: str = "",
        sheet_path: str = "",
        all_sheets: bool = False,
        dry_run: bool = False,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Point placed parts at another library, without Altium's dialog.

        The kind follows ``library_path``: a .SchLib or .IntLib links the
        symbol (``lib_ref``) from that file; a .DbLib links a database row
        (``dblib_table`` and ``dblib_key``, the row's key). ``footprint``
        and ``footprint_library`` repoint the current footprint model.
        Only the link changes: the symbol drawn on the sheet stays until
        Altium updates from the library (``app_update_from_libraries``);
        ``proj_replace_component`` swaps the symbol itself.

        Checked FIRST, so nothing changes on a bad target: a SchLib/IntLib
        given by full path must contain ``lib_ref``; the DbLib key must
        pick out one row, with the whole table read (else
        ``DBLIB_KEY_NOT_UNIQUE``), and that row's symbol and footprint
        fill ``lib_ref`` and
        ``footprint`` when they are not given. Every part is read back:
        ``parts`` has each one's link ``before`` and ``after``, and a
        field that did not take is named in ``not_applied``.

        Args:
            designators: the parts to relink, e.g. ["R1", "R2"].
            library_path: target .SchLib, .IntLib or .DbLib (full path to
                verify; a bare file name is written without checking).
            lib_ref: the symbol name in that library.
            dblib_table: DbLib table.
            dblib_key: the row's key, which becomes the Design Item ID.
            footprint: footprint name for the current footprint model.
            footprint_library: .PcbLib the footprint model points at.
            sheet_path: open .SchDoc; empty = the focused sheet.
            all_sheets: every open schematic of the focused project.
            dry_run: report what would change, write nothing.
            verify: check the target before writing (default True).
        """
        import os

        names = [d.strip() for d in (designators or []) if str(d).strip()]
        if not names:
            return {"success": False, "error": "MISSING_PARAM",
                    "reason": "designators is required"}
        if any("," in d for d in names):
            return {"success": False, "error": "BAD_PARAM",
                    "reason": "a designator cannot contain a comma"}
        ext = os.path.splitext(library_path)[1].lower()
        kind = {".schlib": "schlib", ".intlib": "intlib",
                ".dblib": "dblib"}.get(ext, "")
        if library_path and not kind:
            return {"success": False, "error": "BAD_PARAM",
                    "reason": "library_path must be a .SchLib, .IntLib or "
                              ".DbLib"}
        if kind == "dblib" and not (dblib_table and dblib_key):
            return {"success": False, "error": "MISSING_PARAM",
                    "reason": "a .DbLib link needs dblib_table and dblib_key"}
        bridge = get_bridge()
        row = None
        if kind == "dblib" and verify and os.path.isabs(library_path):
            row = await bridge.send_command_async(
                "library.get_dblib_record",
                {"library_path": library_path, "table": dblib_table,
                 "key": dblib_key}, timeout=120.0)
            if not (isinstance(row, dict) and row.get("found")):
                return {"success": False, "error": "DBLIB_ROW_NOT_FOUND",
                        "reason": (f"{dblib_key!r} is not a row of "
                                   f"{dblib_table!r} in {library_path}; "
                                   f"nothing was changed"),
                        "lookup": row}
            # The part would link to whichever row Altium found first: with
            # the key on several rows, or the table not read to its end,
            # that row is not known.
            rows_found, read_all = row.get("match_count"), row.get("scan_capped") is not True
            if rows_found != 1 or not read_all:
                why = (f"{rows_found} rows have it" if read_all
                       else "the lookup stopped before the end of the table")
                return {"success": False, "error": "DBLIB_KEY_NOT_UNIQUE",
                        "reason": f"{dblib_key!r} in {dblib_table!r}: {why}; nothing "
                                  "was changed", "lookup": row}
            lib_ref = lib_ref or str(row.get("symbol_ref") or "")
            prints = row.get("footprints") or []
            if not footprint and prints:
                footprint = str(prints[0].get("ref") or "")
                footprint_library = footprint_library or str(
                    prints[0].get("library") or "")
        params = {
            "designators": ",".join(names), "kind": kind,
            "library": library_path, "lib_ref": lib_ref,
            "design_item_id": dblib_key if kind == "dblib" else "",
            "table": dblib_table, "footprint": footprint,
            "footprint_library": footprint_library,
            "verify": "true" if verify else "false",
            "dry_run": "true" if dry_run else "false",
        }
        sheets = [sheet_path] if sheet_path else [""]
        if all_sheets:
            docs = await bridge.send_command_async("project.get_documents", {})
            docs = docs.get("documents", docs) if isinstance(docs, dict) else docs
            sheets = [d.get("file_path") for d in (docs or [])
                      if isinstance(d, dict)
                      and str(d.get("document_kind", "")).upper() == "SCH"]
        replies, found = [], set()
        for sheet in sheets:
            try:
                reply = await bridge.send_command_async(
                    "generic.relink_components",
                    {**params, "sheet_path": sheet or ""})
            except Exception as exc:                 # noqa: BLE001
                if not all_sheets:
                    raise
                reply = {"success": False, "sheet_path": sheet,
                         "error": getattr(exc, "code", type(exc).__name__),
                         "reason": str(exc)}
            replies.append(reply)
            for part in (reply or {}).get("parts", []) or []:
                found.add(part.get("designator"))
        out = {
            "success": all(r.get("success") for r in replies) and bool(found),
            "dry_run": dry_run, "kind": kind or None,
            "lib_ref": lib_ref or None, "footprint": footprint or None,
            "matched": sum(int(r.get("matched", 0)) for r in replies),
            "changed": sum(int(r.get("changed", 0)) for r in replies),
            "failed": sum(int(r.get("failed", 0)) for r in replies),
            "footprint_models_added": sum(
                int(r.get("footprint_models_added", 0)) for r in replies),
            "not_found": [d for d in names if d not in found],
            "sheets": replies if all_sheets else None,
            "parts": [p for r in replies for p in (r.get("parts") or [])],
        }
        if row is not None:
            out["dblib_row"] = {k: row.get(k) for k in
                                ("symbol_ref", "symbol_library", "footprints")}
        if not dry_run and out["changed"]:
            out["next_step"] = ("the link changed; app_update_from_libraries "
                                "brings the sheet's symbol and parameters "
                                "in line with it")
        return out

    @mcp.tool()
    async def sch_clear_source_library(
        sheet_path: str = "",
        designators: Optional[list[str]] = None,
        clear_source_library: bool = True,
        sync_design_item_id: bool = True,
    ) -> dict[str, Any]:
        """Unpin placed schematic components from a stale source library.

        After a library is renamed or moved, placed parts still point at it
        (``SourceLibraryName``, ``DesignItemId``) and show ``<Not Found>``.
        This clears the first and sets the second to LibReference, so
        Altium re-matches from the Available Libraries. DesignItemId is a
        property, not a parameter (for one part: ``obj_modify``).

        Args:
            sheet_path: absolute .SchDoc path; empty = the focused
                sheet. For a whole project, call once per sheet (see
                proj_list_documents).
            designators: restrict to these designators (whole-string,
                case-sensitive); None/empty = every component on the
                sheet.
            clear_source_library: clear ``SourceLibraryName``.
            sync_design_item_id: set ``DesignItemId`` = LibReference
                where they differ.

        Returns:
            {"total": inspected, "cleared_source_library": n,
             "synced_design_item_id": n}.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "sheet_path": sheet_path,
            "clear_source_library": "true" if clear_source_library else "false",
            "sync_design_item_id": "true" if sync_design_item_id else "false",
        }
        if designators:
            # The list rides a comma-separated field; a designator
            # containing a comma would arrive as two designators, and a
            # fragment like 'R1' can match a real component. Refuse
            # rather than strip: stripping can produce a different
            # valid designator.
            bad = [d for d in designators if "," in str(d)]
            if bad:
                return {"ok": False, "reason":
                        f"designators {bad} contain a comma, which is "
                        "the wire-format separator; fragments could "
                        "match other components, so the call is refused"}
            params["designators"] = ",".join(designators)
        return await bridge.send_command_async(
            "generic.clear_sch_source_library", params
        )

    @mcp.tool()
    async def sch_get_constraint_groups() -> dict[str, Any]:
        """Enumerate IDocument.DM_ConstraintGroups on the active schematic.

        FPGA-style pin and timing constraints attached to the document.

        Returns:
            Dict with groups (list of {target_kind, target_id,
            constraint_count, constraints[{kind, data}, ...]}) and count.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.get_constraint_groups", {}
        )

    @mcp.tool()
    async def sch_place_harness_connector(
        x: float,
        y: float,
        width: float = 500,
        height: float = 800,
        harness_type: str = "",
    ) -> dict[str, Any]:
        """Place a harness connector on the active schematic sheet.


        Args:
            x, y: Bottom-left corner in mils.
            width, height: Connector rectangle size in mils.
            harness_type: Named harness type (optional).

        Returns:
            Dict with success, x, y, width, height, harness_type.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x": str(x), "y": str(y),
            "width": str(width), "height": str(height),
        }
        if harness_type:
            params["harness_type"] = harness_type
        return await bridge.send_command_async(
            "generic.place_harness_connector", params
        )

    @mcp.tool()
    async def sch_place_cross_sheet_connector(
        x: float,
        y: float,
        net: str = "",
        side: str = "",
    ) -> dict[str, Any]:
        """Place a cross-sheet connector (off-sheet port) on the active sheet.

        Connects to the same net name on another sheet.

        Args:
            x, y: Connector location in mils.
            net: Net name the connector binds to.
            side: "left" or "right" orientation (optional).

        Returns:
            Dict with success, x, y, net.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"x": str(x), "y": str(y)}
        if net:
            params["net"] = net
        if side:
            params["side"] = side
        return await bridge.send_command_async(
            "generic.place_cross_sheet_connector", params
        )

    @mcp.tool()
    async def sch_place_text_frame(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        text: str,
        align: str = "left",
        font_id: int = 0,
    ) -> dict[str, Any]:
        """Place a multi-line text frame (note block) on the active sheet.

        A text frame is a bordered rectangle that word-wraps a block of
        text. A newline, or a typed ``\\n``, becomes Altium's own line
        break.

        Args:
            x1, y1, x2, y2: The two rectangle corners in mils.
            text: Frame contents.
            align: Horizontal alignment "left", "center", or "right".
            font_id: The frame's font. 0 takes the font the sheet's text
                frames already use, else its net labels' font.

        Returns:
            The placement: the frame's corners and the ``font_id`` it got.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_text_frame",
            {
                "x1": _mils(x1),
                "y1": _mils(y1),
                "x2": _mils(x2),
                "y2": _mils(y2),
                "text": text,
                "align": align,
                "font_id": str(int(font_id)),
            },
        )

    @mcp.tool()
    async def sch_generate_toc(
        x1: float = 200,
        y1: float = 200,
        x2: float = 2400,
        y2: float = 1800,
        title: str = "Table of Contents",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Place a table-of-contents note listing every schematic sheet.

        Reads the project's document list, keeps the .SchDoc sheets in
        project order, and drops a numbered text frame on the active sheet.
        Run it on a dedicated cover/index sheet. Re-running does not remove a
        prior frame, delete the old one first if you regenerate.

        Args:
            x1, y1, x2, y2: Frame corners in mils (default a tall left box).
            title: Heading line above the list.
            project_path: Optional project; uses active if omitted.

        Returns:
            Dict with sheet_count and the placement result, or an error if
            no schematic sheets were found.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        docs = await bridge.send_command_async("project.get_documents", params)
        if isinstance(docs, dict):
            docs = docs.get("documents", docs.get("items", []))
        if not isinstance(docs, list):
            docs = []

        sheets = [
            d for d in docs
            if str(d.get("file_name", "")).lower().endswith(".schdoc")
        ]
        if not sheets:
            return {"success": False, "error": "no .SchDoc sheets found in project"}

        lines = [title, ""]
        for i, d in enumerate(sheets, start=1):
            name = str(d.get("file_name", "")).strip()
            if name.lower().endswith(".schdoc"):
                name = name[: -len(".SchDoc")]
            lines.append(f"{i}. {name}")
        text = "\n".join(lines)

        result = await bridge.send_command_async(
            "generic.place_text_frame",
            {
                "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
                "text": text, "align": "left",
            },
        )
        if isinstance(result, dict):
            result["sheet_count"] = len(sheets)
        return result

    @mcp.tool()
    async def sch_set_net_tie(
        designator: str,
        mode: str = "nobom",
    ) -> dict[str, Any]:
        """Mark a placed schematic component as a net tie.

        Joins the nets on its pins while keeping them separate nets, so
        ERC and DRC accept the join. Sets the part's ComponentKind.

        Args:
            designator: Component to convert (e.g. "NT1").
            mode: "nobom" (default, hidden from BOM, kept through sync) or
                "bom" (appears in the BOM).

        Returns:
            {"designator", "component_kind"} or NOT_FOUND.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_net_tie",
            {"designator": designator, "mode": mode},
        )

    @mcp.tool()
    async def sch_increment_designators(
        delta: int,
        prefix: str = "",
    ) -> dict[str, Any]:
        """Offset the trailing number of schematic designators by a delta.

        Adds ``delta`` to the numeric suffix of every component designator
        on the active sheet (delta=100 turns R5 into R105), optionally
        only those with a prefix.

        Args:
            delta: Non-zero integer added to each designator's number.
            prefix: If set (e.g. "R"), only designators with this letter
                prefix are changed.

        Returns:
            Dict with success, modified (count), delta.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"delta": str(delta)}
        if prefix:
            params["prefix"] = prefix
        return await bridge.send_command_async(
            "generic.increment_designators", params
        )

    @mcp.tool()
    async def sch_toggle_pin_visibility(
        designator: str = "",
        show_name: Optional[bool] = None,
        show_designator: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Show or hide pin name / designator labels on schematic symbols.

        Targets a single component (by ``designator``) or every component
        on the active sheet when ``designator`` is empty. Provide
        ``show_name`` and/or ``show_designator`` to set those flags; omit a
        flag to leave it unchanged.

        Args:
            designator: Component to target, or "" for all components.
            show_name: True/False to set pin-name visibility (optional).
            show_designator: True/False to set pin-designator visibility.

        Returns:
            Dict with success and pins_modified (count).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if designator:
            params["designator"] = designator
        if show_name is not None:
            params["show_name"] = "true" if show_name else "false"
        if show_designator is not None:
            params["show_designator"] = "true" if show_designator else "false"
        return await bridge.send_command_async(
            "generic.toggle_pin_visibility", params
        )

    @mcp.tool()
    async def sch_set_component_part_id(
        designator: str,
        part_id: int,
        location_x: int | None = None,
        location_y: int | None = None,
    ) -> dict[str, Any]:
        """Switch the active sub-part on a multi-part schematic component.

        Sets which gate (U1A, U1B, ...) a symbol shows; IDs are 1-based.
        Every gate of a device shares the designator (and unique id), so
        when it names several symbols, pass the location of the one you
        mean: without it the call refuses and lists the candidates with
        their locations and part ids.

        Args:
            designator: Component reference (e.g., "U1").
            part_id: Sub-part index, 1-based (1=A, 2=B, ...). A value
                above PartCount is accepted by Altium and does not take,
                so the result is read back and a mismatch is reported.
            location_x: Symbol X in mils, to choose one of several.
            location_y: Symbol Y in mils, to choose one of several.

        Returns:
            Dict with success, designator, part_id, the location written
            and how many symbols carried the designator. On ambiguity,
            an AMBIGUOUS_DESIGNATOR error carrying `candidates`.
        """
        bridge = get_bridge()
        params: dict[str, str] = {
            "designator": designator, "part_id": str(part_id)}
        if location_x is not None:
            params["location_x"] = str(int(location_x))
        if location_y is not None:
            params["location_y"] = str(int(location_y))
        return await bridge.send_command_async(
            "generic.set_component_part_id", params,
        )

    @mcp.tool()
    async def sch_set_component_unique_id(
        designator: str,
        unique_id: str,
    ) -> dict[str, Any]:
        """Set UniqueId on every placed symbol with this designator.

        The gates of a multi-part device are one package only when they
        share UniqueId. Every matching symbol is stamped and read back;
        if any kept its old id the call fails and names it.

        Args:
            designator: Component reference as drawn (e.g. "U_COMP2B").
            unique_id: Target UniqueId, usually copied from the A unit.

        Returns:
            Dict with success, designator, unique_id, symbols_written,
            symbols_found, and per-symbol locations with the id each
            reads back.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_component_unique_id",
            {"designator": designator, "unique_id": unique_id},
        )

    @mcp.tool()
    async def sch_replicate_component(
        designator: str,
        part_id: int = 0,
        x: Optional[int] = None,
        y: Optional[int] = None,
        new_designator: str = "",
    ) -> dict[str, Any]:
        """Copy a placed schematic part and share the master's UniqueId.

        The gates of a multi-part device are one package only when they
        share UniqueId; the copy keeps the source's (written before it is
        placed, the only way it sticks). Optionally switches the part id
        and moves it.

        Args:
            designator: Source instance to copy (e.g. "U8").
            part_id: 1-based CurrentPartID for the copy. 0 = keep source.
            x, y: Placement in mils. Omit both to leave the replica
                where Replicate dropped it.
            new_designator: Designator for the copy. Empty keeps the
                source designator (required for multi-part packing).

        Returns:
            Dict with success, source/copy UniqueIds, part_id, and
            ``shared`` (true when the copy kept the master's UniqueId).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if part_id >= 1:
            params["part_id"] = str(part_id)
        if x is not None:
            params["x"] = str(x)
        if y is not None:
            params["y"] = str(y)
        if new_designator:
            params["new_designator"] = new_designator
        return await bridge.send_command_async(
            "generic.replicate_sch_component",
            params,
        )

    @mcp.tool()
    async def sch_place_probe(
        x: float,
        y: float,
        net_name: str = "",
        probe_method: str = "probed_nets_only",
        name: str = "",
    ) -> dict[str, Any]:
        """Place a simulation voltage probe on the active schematic.

        The probe measures the voltage of the net under it, so place it
        on a wire. It is the probe Altium's Place > Probes > Voltage
        makes: a simulation run reports and plots it, and
        sim_get_probes lists it with the node it resolved to.

        Args:
            x, y: Probe location in mils, on a wire.
            net_name: Echoed back; the net comes from the wire.
            probe_method: Echoed back.
            name: A name shown instead of the net's (ProbeCustomName).

        Returns:
            Dict with success, x, y, net_name, name, measures.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_probe",
            {
                "x": str(x), "y": str(y),
                "net_name": net_name,
                "probe_method": probe_method,
                "name": name,
            },
        )

    @mcp.tool()
    async def sch_add_datafile_link(
        designator: str,
        file_path: str,
        kind: str = "",
    ) -> dict[str, Any]:
        """Attach a datafile link to a component's current implementation.

        E.g. an IBIS or simulation model file.

        Args:
            designator: Component reference (e.g., "U1").
            file_path: Full path to the file being linked.
            kind: Optional implementation-specific type
                (e.g., "SimModel", "IBIS").

        Returns:
            Dict with success, designator, file_path.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "designator": designator,
            "file_path": file_path,
        }
        if kind:
            params["kind"] = kind
        return await bridge.send_command_async(
            "generic.add_datafile_link", params
        )

    # --------------------------------------------------------------
    # Batch tools using the '~~'-separator format.
    # --------------------------------------------------------------

    @mcp.tool()
    async def obj_batch_create(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create many schematic objects in ONE call (obj_create items).

        What ``obj_create(items=[...])`` runs, kept under this name for
        callers that use it.

        One Altium transaction for the whole list.

        Args:
            operations: List of create dicts, each with:
                - scope: "active_doc" (default), only active_doc is
                  currently honored for creates.
                - object_type: Altium type name (e.g. "eNetLabel",
                  "eJunction", "eNoERC").
                - properties: pipe-separated ``Name=Value`` list, same
                  format as ``obj_create`` accepts.
                - container: "document" (default) or "component" (for
                  library-symbol contents when a lib is active).

        Example, drop 3 net labels in one call:
            obj_batch_create(operations=[
                {"object_type": "eNetLabel",
                 "properties": "Text=VCC|Location.X=100|Location.Y=200"},
                {"object_type": "eNetLabel",
                 "properties": "Text=GND|Location.X=100|Location.Y=400"},
                {"object_type": "eNetLabel",
                 "properties": "Text=SCK|Location.X=300|Location.Y=200"},
            ])

        Returns:
            Dict with created, failed, total counts and indexed failures.
            Known unsupported properties (IsHidden on anything but pins,
            parameters, designators and sheet names; Text on a type without
            it, such as eParameterSet or eWire) reject that item before
            registration.
            Its failure includes reason UNSUPPORTED_PROPERTY, property and
            object_type. Other valid items still run.
        """
        op_strs: list[str] = []
        for i, op in enumerate(operations):
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            props = op.get("properties", "")
            container = op.get("container", "")
            if not obj_type:
                return {"ok": False, "reason": f"operations[{i}] needs "
                        "object_type; nothing was sent", "created": 0}
            # `properties` is PIPE-separated ("Text=X|Location.X=100"),
            # so ";" is not a legitimate separator inside it and "=" is.
            # payload_safe leaves "=" alone for exactly that reason.
            fields = [
                f"scope={payload_safe(scope)}",
                f"object_type={payload_safe(obj_type)}",
                f"properties={payload_safe(props)}",
            ]
            if container:
                fields.append(f"container={payload_safe(container)}")
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid operations", "created": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.batch_create",
            {"operations": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def obj_batch_delete(
        operations: list[dict[str, str]],
        confirm_delete_all: bool = False,
    ) -> dict[str, Any]:
        """Delete objects for many scope/type/filter sets (obj_delete items).

        What ``obj_delete(items=[...])`` runs, kept under this name for
        callers that use it. One round trip.

        Args:
            operations: List of delete dicts, each with:
                - scope: "active_doc" (default), "project", or
                  "doc:<absolute_path>".
                - object_type: Altium type name (e.g. "eJunction",
                  "eNoERC", "eWire", or a PCB type such as
                  "eTrackObject", which deletes from the active board
                  and takes only scope "active_doc").
                - filter: pipe-separated ``PropName=Value`` filter
                  conditions (AND logic), same format as
                  ``obj_delete``. An EMPTY filter deletes every object
                  of that type in that scope.
            confirm_delete_all: required when any operation has an empty
                filter; otherwise nothing is sent.

        Returns:
            operations_processed, total, total_deleted and ``results``
            (per operation ``deleted`` and a ``note`` when it deleted
            nothing). An operation that stopped part way has a note
            starting ``partial:`` and its ``deleted`` counts what it
            removed; ``operations_partial`` counts them. Query before
            repeating a batch that failed or timed out.
        """
        op_strs: list[str] = []
        sweeping: list[str] = []
        for i, op in enumerate(operations):
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            filt = op.get("filter", "")
            # ';' separates fields within an op and '~~' separates ops,
            # so either inside a value reshapes the batch. The dangerous
            # shape: a '~~' inside filter fabricates a NEW op the
            # confirm_delete_all guard below never saw, and an
            # unfiltered delete rides in unconfirmed. Refuse rather than
            # strip: stripping can produce a different valid value.
            for field, value in (("scope", scope),
                                 ("object_type", obj_type),
                                 ("filter", filt)):
                if ";" in str(value) or "~~" in str(value):
                    return {"ok": False, "reason":
                            f"operations[{i}].{field} contains ';' or "
                            "'~~', which are the batch delimiters; the "
                            "wire format cannot carry them, so this "
                            "batch is refused rather than reshaped",
                            "operations_processed": 0}
            if not obj_type:
                return {"ok": False, "reason": f"operations[{i}] needs "
                        "object_type; nothing was sent",
                        "operations_processed": 0}
            bad = filter_problem(filt)
            if bad:
                return bad_filter(bad, operations_processed=0)
            if not str(filt).strip():
                sweeping.append(f"{obj_type} in {scope}")
            op_strs.append(
                f"scope={scope};object_type={obj_type};filter={filt}"
            )

        if not op_strs:
            return {"error": "No valid operations", "operations_processed": 0}

        # obj_delete refuses an unfiltered delete and then tells the
        # caller to come here instead, so without this the guard is just
        # a detour. One op with a blank filter is enough: the others
        # would still run, and a partly-applied batch is harder to
        # reason about afterwards than one that did nothing.
        if sweeping and not confirm_delete_all:
            return {
                "error": (
                    "Safety guard: these operations have an empty filter "
                    f"and would delete ALL matching objects: {sweeping}. "
                    "Add a filter to select specific objects, or set "
                    "confirm_delete_all=True to sweep them."
                ),
                "operations_processed": 0,
                "unfiltered_operations": sweeping,
            }

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.batch_delete",
            {"operations": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def sch_place_wires(
        wires: list[dict[str, int]],
    ) -> dict[str, Any]:
        """Place MANY wire segments on the active schematic in ONE call.

        NOT THE WAY TO WIRE A SHEET: sch_draw_template draws a whole
        block from a template, with the pin ends worked out and the
        wiring checked. Use this for a few segments on an existing sheet.

        Args:
            wires: List of wire dicts, each with x1, y1, x2, y2 in mils.

        Returns:
            Dict with placed, failed, total counts.
        """
        op_strs: list[str] = []
        for w in wires:
            op_strs.append(
                f"x1={int(w.get('x1', 0))};y1={int(w.get('y1', 0))};"
                f"x2={int(w.get('x2', 0))};y2={int(w.get('y2', 0))}"
            )
        if not op_strs:
            return {"error": "No wires provided", "placed": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_wires",
            {"wires": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def sch_place_components(
        placements: list[dict[str, Any]],
        document_path: Optional[str] = None,
        allow_manual_layout: bool = False,
        tidy: bool = True,
    ) -> dict[str, Any]:
        """Place MANY schematic components from libraries in ONE call.

        NOT THE WAY TO DRAW A SCHEMATIC: four or more parts onto a sheet
        with two or fewer is refused. sch_draw_template draws a whole
        block from a template. Use this to add parts to an existing sheet.
        Parts land on the ACTIVE sheet unless ``document_path`` is given;
        a sheet just created is not focused, so always pass it.

        Args:
            placements: [{lib_reference (required), x, y (mils),
                library_path (empty: an open library), designator,
                rotation (0/90/180/270), footprint, part_id}]. With
                ``footprint`` naming one of the symbol's PCB models (any
                case), that model becomes current; ``part_id`` picks which
                part of a multi-part symbol goes down.
            document_path: .SchDoc to focus and place onto.
            allow_manual_layout: True places your own coordinates onto an
                empty sheet anyway; say why.
            tidy: Make each placed part match the sheet: its library
                link (a loaded part says "*", and Altium then will not
                change its Design Item ID), the parameters its models
                bring hidden, and the sheet's designator font. Listed
                per part in ``tidied``.

        Returns:
            Dict with placed, failed, total counts and footprints_chosen;
            footprint_issues lists a part whose symbol has no model of
            that name (with the models it has) or whose choice did not
            take. Such a part is placed on its library's model.
        """
        refusal = _unknown_keys_refusal(
            placements, PLACEMENT_KEYS,
            "place parts from a library path that never arrived")
        if refusal is not None:
            return refusal

        op_strs: list[str] = []
        for p in placements:
            lib_ref = str(p.get("lib_reference", "")).strip()
            if not lib_ref:
                continue
            fields = [
                f"library_path={payload_safe(p.get('library_path', ''))}",
                f"lib_reference={payload_safe(lib_ref)}",
                f"x={_mils(p.get('x', 0))}",
                f"y={_mils(p.get('y', 0))}",
                f"rotation={int(p.get('rotation', 0))}",
            ]
            if p.get("designator"):
                fields.append(f"designator={p['designator']}")
            if p.get("footprint"):
                fields.append(f"footprint={payload_safe(p['footprint'])}")
            part_no = p.get("part_id")
            if part_no is not None:
                fields.append("part_id=" + str(int(part_no)))
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid placements", "placed": 0}

        bridge = get_bridge()
        refusal = await _refuse_manual_sheet_layout(
            bridge, len(op_strs), document_path, allow_manual_layout)
        if refusal is None:
            refusal = await _focus_sheet(bridge, document_path)
        if refusal is not None:
            return refusal
        batch = {"placements": "~~".join(op_strs), "tidy": str(bool(tidy)).lower()}
        return await bridge.send_command_async(
            "generic.place_sch_components_from_library", batch)

    @mcp.tool()
    async def sch_place_vault_components(
        placements: list[dict[str, Any]],
        vault: str = "",
        document_path: Optional[str] = None,
        lib_identifier_kind: Optional[int] = None,
        allow_manual_layout: bool = False,
    ) -> dict[str, Any]:
        """Place MANY managed (Workspace) components in ONE call.

        ``sch_place_components`` for Workspace parts, placed by Design
        Item ID and kept linked. The same hand-drawing rule applies (four
        or more parts onto a near-empty sheet is refused). Part numbers
        are not looked up; ``lib_get_vault_component`` checks an ID.

        Args:
            placements: dicts with "design_item_id" (required), ``x``,
                ``y`` (mils), optional ``designator`` and ``rotation``.
            vault: The Workspace name; usually read off managed parts on
                an open sheet. If refused, ask the user, don't invent it.
            document_path: The .SchDoc to focus and place onto.
            lib_identifier_kind: Load kind, default 4 (VaultName).
            allow_manual_layout: Place onto a near-empty sheet anyway.

        Returns:
            ``placed``, ``linked`` (read back a VaultGUID and ItemGUID),
            ``failed``, ``total``, ``vault``, lib_identifier_kind,
            ``loader_callable``, ``note``, ``items`` (``reason``
            PLACED_WITHOUT_LINK, NOT_FOUND_IN_VAULT or LOAD_RAISED).
        """
        refusal = _unknown_keys_refusal(
            placements, MANAGED_PLACEMENT_KEYS,
            "place parts without what those keys were meant to say")
        if refusal is not None:
            return refusal
        op_strs: list[str] = []
        for p in placements:
            item_id = str(p.get("design_item_id", "")).strip()
            if not item_id:
                continue
            fields = [
                f"design_item_id={payload_safe(item_id)}",
                f"x={_mils(p.get('x', 0))}",
                f"y={_mils(p.get('y', 0))}",
                f"rotation={int(p.get('rotation', 0))}",
            ]
            if p.get("designator"):
                fields.append(f"designator={payload_safe(p['designator'])}")
            op_strs.append(";".join(fields))
        if not op_strs:
            return {"error": "No valid placements", "placed": 0}

        bridge = get_bridge()
        refusal = await _refuse_manual_sheet_layout(
            bridge, len(op_strs), document_path, allow_manual_layout)
        if refusal is None:
            refusal = await _focus_sheet(bridge, document_path)
        if refusal is not None:
            return refusal
        params: dict[str, Any] = {"placements": "~~".join(op_strs)}
        if vault:
            params["vault"] = vault
        if lib_identifier_kind is not None:
            params["lib_identifier_kind"] = int(lib_identifier_kind)
        return await bridge.send_command_async(
            "generic.place_sch_vault_components", params)

    @mcp.tool()
    async def sim_attach_primitives(
        attachments: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Attach SPICE primitives to MANY components in ONE call.

        For the ``needs_primitive`` list from `sim_get_readiness`, all in
        one call.

        Args:
            attachments: List of attach dicts, each with:
                - designator (str, required)
                - primitive  (str, required), R/L/C/V/I/D/Q/M/X
                - value      (str, optional), "10k", "100n",
                  "DC 5", "SIN(0 1 1k)", etc.
                - spice_model (str, optional), model name for
                  semi/sub-circuit parts
                - sim_kind   (str, optional), "General" /
                  "Subcircuit" / "Model"

        Returns:
            Dict with attached, failed, total counts.
        """
        op_strs: list[str] = []
        for a in attachments:
            desig = str(a.get("designator", "")).strip()
            prim = str(a.get("primitive", "")).strip().upper()
            if not desig or not prim:
                continue
            fields = [f"designator={desig}", f"primitive={prim}"]
            if a.get("value"):
                fields.append(f"value={a['value']}")
            if a.get("spice_model"):
                fields.append(f"spice_model={a['spice_model']}")
            if a.get("sim_kind"):
                fields.append(f"sim_kind={a['sim_kind']}")
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid attachments", "attached": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.attach_spice_primitives",
            {"attachments": "~~".join(op_strs)},
        )

    # Schematic power ports, net labels, and free text are placed via the
    # dedicated handlers place_power_port / place_net_label / place_note.


#: Power port styles the bridge draws (Utils.pas IsPowerPortStyle).
POWER_PORT_STYLES = ("circle", "arrow", "bar", "wave",
                     "gnd_power", "gnd_signal", "gnd_earth")

#: Sheet sizes generic.set_sheet_size can set (the OrCAD set it cannot).
_SHEET_STYLES_SETTABLE = frozenset({
    "A4", "A3", "A2", "A1", "A0", "A", "B", "C", "D", "E",
    "LETTER", "LEGAL", "TABLOID"})


def _outside_sheet_report(frame: dict, Box, fit_report, limit: int) -> dict:
    """Outside-the-sheet verdict from a generic.get_sheet_frame reply.

    The title block is the template's graphics in the bottom-right quarter of
    the frame; without a template, Altium's standard block is assumed when
    the title block is on."""
    fx, fy = float(frame["frame_x"]), float(frame["frame_y"])
    boxes = []
    for o in frame.get("objects") or []:
        x1, y1, x2, y2 = o["bbox"]
        kind = str(o.get("type", ""))
        if kind == "parameter":     # a document field, never drawn at 0,0
            continue
        boxes.append(Box(kind, o.get("label", ""), min(x1, x2), min(y1, y2),
                         max(x1, x2), max(y1, y2)))
    title = None
    corner = [b for b in frame.get("template_boxes") or []
              if min(b[0], b[2]) > fx / 2 and max(b[1], b[3]) < fy / 2]
    if corner:
        tx1 = min(min(b[0], b[2]) for b in corner)
        ty2 = max(max(b[1], b[3]) for b in corner)
        title = (fx - tx1, ty2)
    elif frame.get("title_block_on", True):
        from eda_agent.design.sheet_fit import DEFAULT_TITLE_BLOCK
        title = DEFAULT_TITLE_BLOCK
    style_names = ["A4", "A3", "A2", "A1", "A0", "A", "B", "C", "D", "E",
                   "LETTER", "LEGAL", "TABLOID", "ORCADA", "ORCADB", "ORCADC",
                   "ORCADD", "ORCADE"]
    style = int(frame.get("sheet_style", 0) or 0)
    size = "CUSTOM" if frame.get("use_custom") else (
        style_names[style] if 0 <= style < len(style_names) else "CUSTOM")
    report = fit_report(boxes, size, frame=(fx, fy), title_block=title)
    report["outside"] = report["outside"][:limit]
    if report.get("verdict") == "enlarge" and frame.get("template"):
        from eda_agent.design.sheet_fit import template_for_size
        report["to_template"] = template_for_size(frame["template"],
                                                  report["to_size"])
        if not report["to_template"]:
            report["template_note"] = (
                f"no {report['to_size']} template of the same family was "
                f"found beside {frame['template']}")
    return {"ok": True, "file_path": frame.get("file_path", ""),
            "template": frame.get("template", ""), "objects": len(boxes),
            "title_block": list(title) if title else None,
            "truncated": bool(frame.get("truncated")), **report}
