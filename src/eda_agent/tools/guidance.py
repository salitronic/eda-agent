# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Task-oriented guidance: which tool, on which document, and what bites.

``tool_catalog`` answers "what is this tool called". It cannot answer
"what is the right approach for this document kind", and that is the
question that has actually been getting wrong answers.

The failures this exists for were all the same shape: a capability was
reported ABSENT when it was present, or a board tool was aimed at a
library. Both produce a confident wrong answer rather than an error, so
neither is self-correcting.

The three answers a caller needs are kept distinct, because collapsing
them is what makes the wrong ones convincing:

* here is the tool, and here is what it needs first
* the tool you were reaching for acts on a DIFFERENT document
* this is genuinely not possible, and here is why

The third matters as much as the first. Without it there is no way to
distinguish "you missed it" from "it does not exist", so the same dead
end gets investigated again every time it comes up.

Tool names here are checked against the live surface by
``tests/test_tool_guide_names_real_tools.py``. A recipe that names a
tool which has been renamed or dropped fails there rather than sending
a caller somewhere empty.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Document kinds a recipe can apply to. ``any`` means it does not turn
#: on which document is in front.
DOCUMENT_KINDS = ("library", "board", "schematic", "project", "any")


#: Each recipe: what you are trying to do, the tools that do it, and the
#: tools that LOOK like they do it but act on another document.
#:
#: ``avoid`` is the load-bearing field. Naming the right tool only helps
#: a caller who is already looking here; naming the wrong one by the
#: reason it is wrong is what makes a recipe findable from the mistake.
_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "task": "Delete primitives inside a footprint",
        "keywords": ("delete", "primitive", "footprint", "silkscreen",
                     "track", "clear", "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_delete_footprint_primitives"],
        "avoid": [
            ("obj_delete", "resolves a BOARD, not the library in front"),
            ("pcb_delete_object", "resolves a BOARD, not the library"),
        ],
        "note": (
            "Both board tools open the first PcbDoc an open project holds "
            "when none is focused, so aimed at a library they do not fail. "
            "They remove primitives from a board you never named. Pads are "
            "excluded unless include_pads is set, since removing one "
            "changes the pinout rather than the drawing."),
    },
    {
        "task": "Set mechanical layer names and kinds on a BOARD",
        "keywords": ("mechanical", "layer", "mechkind", "kind", "rename",
                     "pair", "paired", "board"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_mech_layer_names", "pcb_set_mech_layers",
                "pcb_set_mech_layer_kind"],
        "avoid": [
            ("lib_set_mech_layers", "refuses a PcbDoc: it is the LIBRARY "
                                    "one. Use pcb_set_mech_layers here"),
        ],
        "note": (
            "Read the board first with pcb_get_mech_layer_names: it reports "
            "what each layer actually HOLDS, which a PcbDoc header does not "
            "carry, so kinds can be moved with the geometry in view. Paired "
            "kinds come from a SECOND enumeration with no side suffix and "
            "are written on the pair, not on either layer. Writing a paired "
            "kind as though it were a layer kind silently does nothing."),
    },
    {
        "task": "Set mechanical layer names and kinds in a LIBRARY",
        "keywords": ("mechanical", "layer", "mechkind", "kind", "rename",
                     "pair", "paired", "library", "pcblib"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_set_mech_layers", "lib_run_across"],
        "avoid": [
            ("pcb_set_mech_layers", "acts on a BOARD. In a library use "
                                    "lib_set_mech_layers"),
        ],
        "note": (
            "lib_run_across applies the same layer setup to every library "
            "in a folder in one call. Paired kinds are written on the pair "
            "rather than on either layer here too."),
    },
    {
        "task": "Delete a parameter from a library symbol",
        "keywords": ("parameter", "delete", "remove", "symbol", "schlib",
                     "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_delete"],
        "avoid": [],
        "note": (
            "Scope it with lib_component:NAME. A parameter is owned by its "
            "component, so a document-level delete is a silent no-op, and a "
            "SchLib holds no PLACED components for a component walk to "
            "find. Scoping resolves the symbol first, which is what makes "
            "the delete reach it."),
    },
    {
        "task": "Create a schematic symbol: standard symbols, not rectangles",
        "keywords": ("symbol", "create", "new", "draw", "author", "opamp",
                     "op-amp", "amplifier", "comparator", "triangle",
                     "current-sense", "resistor", "capacitor", "inductor",
                     "diode", "zener", "tvs", "transistor", "mosfet", "bjt",
                     "switch", "schlib"),
        "when": (("create", "symbol"), ("new", "symbol"), ("draw", "symbol"),
                 ("opamp",), ("op-amp",), ("amplifier",), ("comparator",),
                 ("triangle",)),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_get_components", "lib_copy_component",
                "lib_create_passive_symbol", "lib_create_ic_symbol"],
        "avoid": [
            ("lib_add_symbol_rectangle", "a rectangle body is only for an "
                                         "IC with no conventional symbol. "
                                         "Amplifiers get the triangle and "
                                         "discretes their glyph"),
        ],
        "note": (
            "Use the conventional symbol. First look in the user's own "
            "library for a part already drawn that way (a triangle op-amp, "
            "a MOSFET) and copy it with lib_copy_component, then rename and "
            "renumber its pins to the new datasheet: it keeps the house "
            "style. Otherwise lib_create_passive_symbol draws discretes "
            "(R, C, L as arcs, diode, LED, zener, schottky, TVS, crystal, "
            "fuse, switch, push button, N/P MOSFET, NPN/PNP), and "
            "lib_create_ic_symbol draws an op-amp, comparator, current-sense "
            "or instrumentation amplifier as a triangle automatically: "
            "inputs left with + and -, output at the apex, supplies top and "
            "bottom, one triangle per part for a dual or quad. A rectangle "
            "is right only for an IC with no conventional symbol."),
    },
    {
        "task": "Read or change one part of a multi-part symbol",
        "keywords": ("multi-part", "multipart", "part", "gate", "section",
                     "symbol", "pins", "electrical"),
        # gate and section were keywords before; these make them trigger
        # the recipe on their own again, as the table always intended.
        "when": (("gate",), ("section",), ("multi", "part"), ("multipart",)),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify", "lib_get_pin_list",
                "sch_set_component_part_id", "lib_set_symbol_parts"],
        "avoid": [],
        "note": (
            "Suffix the scope with @N, as lib_component:NAME@2, for both "
            "obj_query and obj_modify. A SchLib iterator only ever yields "
            "the part the editor is DISPLAYING, so without the suffix a read "
            "or a write reaches whichever part happens to be showing, not "
            "part 1. lib_get_pin_list reads every part at once and is the "
            "check to run afterwards. Changing a pin's electrical type on a "
            "part the editor is not showing is obj_modify with the @N scope "
            "and a filter on the pin's Designator; it does not need a "
            "rebuild. lib_set_symbol_parts adds or removes parts and "
            "alternate display modes (removed from the end, with their "
            "pins)."),
    },
    {
        "task": "Place a part from a library onto a schematic",
        "keywords": ("place", "part", "component", "symbol", "library",
                     "intlib", "schlib", "sheet", "schematic"),
        "when": (("place", "library"), ("place", "intlib"),
                 ("place", "schlib"), ("place", "part")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_place_components", "sch_draw_template",
                "lib_get_components"],
        "avoid": [
            ("app_run_ui_command", "driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
            ("app_click_menu", "driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
        ],
        "note": (
            "Each placement is a dict with library_path, lib_reference, x, "
            "y and optionally designator, rotation, footprint. The key is "
            "library_path: source_library and lib_ref are not read, and "
            "the call now refuses them by name rather than failing with "
            "LOAD_FAILED. library_path may be the .SchLib or the .IntLib; "
            "an .IntLib is resolved to its source .SchLib, including the "
            "library-package layout where it compiles into a Project "
            "Outputs folder. Adding parts to a sheet that exists is this "
            "tool; drawing a whole block or sheet is sch_draw_template."),
    },
    {
        "task": "Draw a schematic sheet or block",
        "keywords": ("draw", "schematic", "sheet", "block", "circuit",
                     "wire", "wiring", "neat", "tidy", "redraw", "template"),
        "when": (("draw", "schematic"), ("draw", "sheet"),
                 ("draw", "block"), ("redraw", "schematic"),
                 ("wire", "schematic")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["lib_get_pin_list", "sch_preview_template",
                "sch_draw_template"],
        "avoid": [
            ("sch_place_wires", "segment by segment, with pin ends worked out "
                                "by hand; the template tool computes them and "
                                "checks the wiring"),
        ],
        "note": (
            "Plan the block as an engineer draws it, every coordinate on "
            "the 100 mil grid: IC in the middle, rails as straight wires, "
            "capacitors hanging straight down to ground, a divider whose "
            "midpoint runs straight to its pin, labels only where a signal "
            "leaves the block. Wire paths take pin anchors (U1.EN, U1.11), "
            "so pin ends are never computed by hand. Supply ports are "
            "style bar pointing up, grounds gnd_power pointing down. Give "
            "the expected nets so the netlist is checked pin by pin. "
            "Preview, read the PNG, fix, then draw on a fresh sheet."),
    },
    {
        "task": "Delete an object placed on a schematic",
        "keywords": ("delete", "text", "frame", "textframe", "note",
                     "label", "wire", "junction", "rectangle", "image",
                     "probe", "object"),
        "when": (("delete", "text"), ("delete", "frame"),
                 ("delete", "note"), ("delete", "label"),
                 ("delete", "wire"), ("delete", "object")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["obj_delete", "obj_query"],
        "avoid": [
            ("sch_place_text_frame", "CREATES a text frame; obj_delete "
                                     "with object_type eTextFrame removes one"),
            ("sch_place_note", "CREATES a note; obj_delete with "
                               "object_type eNote removes one"),
            ("app_canvas", "driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
        ],
        "note": (
            "obj_delete with object_type and a filter, e.g. eTextFrame, "
            "eNote, eNetLabel, eWire, eJunction, eImage, eProbe. Every "
            "type a sch_place_ tool can create is also addressable here. "
            "Query first with obj_query to see what the filter will match, "
            "because a delete reports how many it removed, not which."),
    },
    {
        "task": "Fix Not Found parts, or set Design Item ID across a library",
        "keywords": ("design", "item", "id", "designitemid", "found",
                     "relink", "provenance", "source", "stale", "sync"),
        "when": (("item", "id"), ("not", "found"), ("found", "library"),
                 ("relink",)),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["lib_clear_source_library", "sch_clear_source_library",
                "lib_normalize_implementations"],
        "avoid": [
            ("lib_rename_component", "changes LibReference, which placed "
                                     "designs link through, and recreates "
                                     "the Not Found state it was meant to fix"),
            ("lib_batch_rename", "changes LibReference; same trap as "
                                 "lib_rename_component"),
            ("app_set_dialog_control", "the Properties panel's Design Item ID "
                                       "field is one symbol at a time and may "
                                       "rename the component; driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
        ],
        "note": (
            "DesignItemId is the library item a placed part re-matches "
            "against, and a stale one after a re-link is what shows as Not "
            "Found. lib_clear_source_library sets DesignItemId to the "
            "LibReference on every symbol in a library in one call, and "
            "leaves LibReference untouched, so placed designs keep "
            "linking. sch_clear_source_library does the same on placed "
            "parts, sheet by sheet. Both always clear SourceLibraryName. "
            "Run it on a copy of the library first, and check total in the "
            "reply before trusting the synced count."),
    },
    {
        "task": "Relink placed parts to another library, database row or "
                "footprint library",
        "keywords": ("relink", "link", "dblib", "database", "intlib",
                     "schlib", "library", "footprint", "pcblib", "point",
                     "repoint", "migrate", "move", "row"),
        "when": (("relink",), ("database", "link"), ("dblib",),
                 ("link", "library"), ("footprint", "library")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_relink_components", "lib_dblib_search",
                "app_update_from_libraries"],
        "avoid": [
            ("app_set_dialog_control", "the Properties panel changes one "
                                       "part at a time and cannot be read "
                                       "back; driving Altium's dialogs is a "
                                       "last resort"),
            ("obj_modify", "one field at a time with no check that the "
                           "target exists; a half-written link is the Not "
                           "Found state"),
        ],
        "note": (
            "sch_relink_components checks the target first (the symbol in "
            "a SchLib/IntLib, the row in a DbLib), fills a DbLib row's own "
            "symbol and footprint, writes every link field together and "
            "reads each back, with dry_run to preview. It changes the link "
            "only; app_update_from_libraries then brings the drawn symbol "
            "and parameters in line. proj_replace_component swaps the "
            "symbol itself."),
    },
    {
        "task": "Mirror or flip a placed schematic component",
        "keywords": ("mirror", "mirrored", "ismirrored", "flip",
                     "horizontal", "horizontally"),
        "when": (("mirror",),),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_mirror_component"],
        "avoid": [
            ("obj_modify", "setting IsMirrored alone moves nothing: the "
                           "canvas draws from the primitives' coordinates, "
                           "and the flag reads back as set while the part "
                           "stays unmirrored"),
        ],
        "note": (
            "sch_mirror_component reflects the primitives about the "
            "component anchor, flips pin orientation, moves the designator "
            "and comment, and sets the flag, which is what the editor's own "
            "mirror does. Calling it twice returns the part to where it "
            "started. Arcs are reported in skipped_kinds, not moved."),
    },
    {
        "task": "Run DRC or ERC",
        "keywords": ("drc", "erc", "rules", "check", "violations"),
        "document_kind": "any",
        "backends": ("altium", "kicad", "easyeda"),
        "use": ["run_drc", "run_erc"],
        # Scoped: app_run_menu is an Altium tool, and offering it as a
        # trap on a backend that has no such tool teaches a distinction
        # that does not exist there.
        "avoid": [
            ("app_run_menu", "the menu path reports success for a run that "
                             "did nothing, and returns no violations",
             ("altium",)),
        ],
        "note": (
            "The dedicated tools validate the document context and return "
            "the violation list. run_drc and run_erc are the EDA-agnostic "
            "pair and work on every backend."),
    },
    {
        "task": "Suppress or restore stencil paste on Not-Fitted parts",
        # Not "variant": one keyword is enough to offer a recipe, so this
        # one led the answer to every variant question, including "delete
        # a project variant". A real stencil question says paste, stencil,
        # DNP or fitted.
        "keywords": ("paste", "stencil", "dnp", "not-fitted", "notfitted",
                     "aperture", "restore"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["audit_variant_not_fitted", "pcb_apply_dnp_paste_exclusion"],
        "avoid": [],
        "note": (
            "Restore refuses to guess: call it with the SAME designators you "
            "applied. Resolving the list from the current variant means you "
            "can exclude under one variant, switch, and leave a component "
            "that is fitted in the new one with its aperture still "
            "suppressed. Nothing reports that. The call succeeds, the pad "
            "looks ordinary in the editor, and the part comes back from "
            "assembly unsoldered."),
    },
    {
        "task": "Place a power port for a rail",
        "keywords": ("power", "port", "rail", "vcc", "gnd", "ground",
                     "orientation", "symbol"),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_place_power_port", "audit_power_port_orientation"],
        "avoid": [],
        "note": (
            "Pass orientation explicitly for a rail drawn with style=bar. "
            "The style-based default groups bar and wave with the grounds, "
            "so a VCC bar comes out pointing DOWN and reads as a ground "
            "symbol. orientation=1 is what a rail wants."),
    },
    {
        "task": "Rename a PCB component designator",
        "keywords": ("designator", "rename", "annotate", "renumber"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["proj_annotate"],
        "avoid": [
            ("obj_modify", "writing a designator on a PCB component from a "
                           "script CRASHES Altium"),
        ],
        "note": (
            "Annotation is the supported route and keeps the schematic and "
            "board in step, which a direct rename would not."),
    },
    {
        "task": "Add or adjust a 3D body in a footprint",
        "keywords": ("3d", "body", "step", "model", "footprint", "height",
                     "standoff", "opacity", "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_link_3d_model", "lib_set_3d_body", "lib_create_3d_model",
                "lib_export_3d_model",
                "lib_auto_link_3d_models",
                "lib_update_footprint_heights_from_3d"],
        "avoid": [
            ("pcb_place_3d_body", "places a free body on a BOARD, not "
                                  "inside the footprint in front"),
            ("obj_modify", "its StandoffHeight and BodyOpacity3D writes "
                           "act on a BOARD, not the library in front"),
            ("obj_run_process", "an Altium process this build does not "
                                "know does nothing and reports no error, "
                                "so a guessed 3D command looks like it ran"),
        ],
        "note": (
            "lib_link_3d_model puts a STEP model in a footprint with its "
            "offsets and rotations; lib_create_3d_model builds one from "
            "solids read off the datasheet drawing, for a part no package "
            "family covers (connector, LED with a lens, header); "
            "lib_set_3d_body reads or changes an "
            "existing body's standoff, overall height, opacity and layer "
            "in place (on a STEP body the standoff is the model's Z offset, "
            "and its saved_bodies give the saved rotations and Z offset); "
            "lib_auto_link_3d_models matches models "
            "to footprints by name across a library; and "
            "lib_update_footprint_heights_from_3d sets each footprint's "
            "height from its body. lib_get_footprint_primitives counts the "
            "bodies a footprint carries and the layer each is on."),
    },
    {
        "task": "Simulate a supply, inrush, hot-plug or load step in Altium",
        "keywords": ("simulate", "simulation", "spice", "transient", "inrush",
                     "hotplug", "hot-plug", "ringing", "droop", "sim", "ngspice",
                     "waveform"),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["sim_run_deck", "sim_get_results", "sim_get_readiness",
                "sim_setup_analysis", "sim_run", "sim_get_setup",
                "sim_get_probes", "sch_place_probe"],
        "avoid": [
            ("sim_attach_primitives", "it writes parameters, not the model "
                                      "link Altium needs, so a sheet of your "
                                      "own symbols still fails with Missing "
                                      "component models"),
        ],
        "note": (
            "sim_run_deck runs a plain SPICE deck you write (sources, the "
            "cable, fuse, ESD, bulk and load as elements, .TRAN, a .PLOT "
            "line, .END) in Altium's simulator and opens the waves as a "
            "chart; the reply gives each wave's min, max and final value. "
            "sim_run runs a schematic whose parts all carry simulation "
            "models, such as Altium's own simulation examples; "
            "sim_run(analyses=[\"dc\"]) runs only the analyses named, and "
            "sim_get_setup shows what a run would do. sch_place_probe puts "
            "a voltage probe on a wire and sim_get_probes lists the probes. "
            "An IC's behaviour needs its vendor's SPICE model."),
    },
    {
        "task": "Place parts in a ring or on an arc (LED rings, round boards)",
        "keywords": ("ring", "circle", "circular", "round", "radial", "polar",
                     "arc", "concentric", "led ring"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_place_ring", "pcb_copy_tracks_radial", "pcb_move_components"],
        "avoid": [],
        "note": (
            "pcb_place_ring spaces parts evenly round a circle or an arc, each "
            "turned to face the centre (rotation_offset sets which way), in "
            "one exact batch placement; call it once per ring. "
            "pcb_copy_tracks_radial copies one cell's routing round the same "
            "centre."),
    },
    {
        "task": "Make a PDF of the schematics or the board without a person there",
        "keywords": ("pdf", "print", "schematic pdf", "smart pdf", "publish",
                     "documentation", "drawing"),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["proj_list_outjob_containers", "proj_run_outjob",
                "proj_create_outjob"],
        "avoid": [
            ("proj_export_pdf", "it opens Altium's PRINT dialog with the "
                                "user's real printer selected and a live "
                                "Print button; nothing is written until a "
                                "person finishes it, and a wrong press "
                                "prints paper"),
        ],
        "note": (
            "A PDF is written without dialogs by an OutJob container whose "
            "medium is PDF (Publish). proj_list_outjob_containers lists the "
            "project's OutJobs and their containers; proj_run_outjob runs "
            "one. With no OutJob, ask the user which of their OutJobs to copy "
            "and use proj_create_outjob."),
    },
    {
        "task": "Route a USB or differential pair at a controlled impedance",
        "keywords": ("usb", "differential", "diff", "pair", "impedance", "90",
                     "100", "ohm", "coupled", "dp", "dm", "lvds", "matched"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_layer_stackup", "pcb_get_impedance_profiles",
                "pcb_calc_trace_width_for_impedance",
                "pcb_create_diff_pair", "pcb_create_design_rule",
                "pcb_set_rule_properties", "pcb_autoroute"],
        "avoid": [
            ("pcb_place_tracks", "two tracks drawn one by one are not coupled "
                                 "and drift apart at every corner"),
        ],
        "note": (
            "If pcb_get_impedance_profiles lists a profile for the target, "
            "use Altium's own width and gap from it. Otherwise read the "
            "stack-up (dielectric height to the reference plane, Er), "
            "then pcb_calc_trace_width_for_impedance with geometry "
            "microstrip_diff for the target (USB 2.0: 90 ohm differential) "
            "and a chosen gap. pcb_create_diff_pair names the pair; "
            "pcb_create_design_rule rule_type differential_pairs with scope "
            "InDifferentialPair('<name>') sets the gap, and "
            "pcb_set_rule_properties its widths. pcb_autoroute routes the pair "
            "first, coupled at that width and gap, and reports it under "
            "pairs (length, skew); a pair it reports with a note was routed "
            "at defaults, not to an impedance. Keep a solid ground plane "
            "beside it: each pair's ``reference`` names the plane next to its "
            "layer and that plane's net. A part in series with the pair (an "
            "ESD protector, a common-mode choke) splits each line into two "
            "nets; the router follows a pair through a small flow-through "
            "part (source \"through U1\"), but defining both halves as pairs "
            "is surer, and a pair the schematic does not define is removed "
            "by the next schematic sync."),
    },
    {
        "task": "Pour copper and stitch it on a round or shaped board",
        "keywords": ("pour", "ground", "gnd", "stitch", "stitching",
                     "keepout", "keep-out", "outline", "round", "circular",
                     "shaped", "thermal"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_place_polygon_board", "pcb_keepout_board_outline",
                "pcb_modify_polygon", "pcb_place_polygon_rect",
                "pcb_place_stitching_vias", "pcb_repour_polygons"],
        "avoid": [
            ("pcb_place_region", "a region is fixed copper; it does not pour "
                                 "around other nets or clear dead copper"),
        ],
        "note": (
            "pcb_place_polygon_board pours over the board's own outline, arcs "
            "included, after drawing the outline as a keep-out "
            "(pcb_keepout_board_outline) so the copper keeps its distance "
            "from the edge. Or draw the keep-out and use pcb_place_polygon_rect "
            "larger than the board: the pour stops at the keep-out and dead "
            "copper is removed (pcb_modify_polygon sets that on a pour "
            "already there). Pour top and bottom with the ground net, then "
            "pcb_place_stitching_vias with no rectangle stitches the whole "
            "board on grid points inside its outline, never in a pad."),
    },
    {
        "task": "Move every 3D body of a library onto one layer",
        "keywords": ("3d", "body", "bodies", "layer", "mechanical", "move",
                     "cleanup", "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_move_bodies_to_layer"],
        "avoid": [
            ("obj_modify", "edits objects on a BOARD, not the footprints "
                           "of the library in front"),
        ],
        "note": (
            "Every footprint, or the named ones; a body already on the "
            "layer is left alone. A mechanical layer the library has not "
            "enabled is accepted with a warning, since Altium does not "
            "draw what is on it."),
    },
    {
        "task": "Redraw the assembly layer of a library's footprints",
        "keywords": ("assembly", "outline", "designator", "mechanical",
                     "redraw", "cleanup", "footprint", "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_redraw_assembly"],
        "avoid": [
            ("lib_add_footprint_tracks", "draws one footprint by hand, "
                                         "from coordinates you supply"),
        ],
        "note": (
            "A rectangle round the 3D bodies, the courtyard or the pads, "
            "and a .Designator in its middle. With replace (the default) "
            "the tracks, arcs, texts, fills and regions already on that "
            "layer are cleared first, never bodies or pads, so it needs "
            "confirm=true."),
    },
    {
        "task": "Place or adjust a 3D body on a board",
        "keywords": ("3d", "body", "step", "model", "board", "opacity",
                     "transparent", "standoff", "height"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_place_3d_body", "obj_query", "obj_modify"],
        "avoid": [
            ("lib_link_3d_model", "edits a footprint in a LIBRARY, not the "
                                  "board"),
        ],
        "note": (
            "pcb_place_3d_body puts a free STEP body on the board. On "
            "eComponentBodyObject, obj_modify writes StandoffHeight and "
            "OverallHeight in mils and BodyOpacity3D from 0 (clear) to 1 "
            "(solid), not a percentage."),
    },
    {
        "task": "Check which design rule really applies to a pad, part or net",
        "keywords": ("rule", "applies", "dominant", "scope", "priority",
                     "clearance", "width", "matches", "covers"),
        "when": (("rule", "applies"), ("rule", "scope"), ("dominant", "rule"),
                 ("which", "rule")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_find_dominant_rule", "pcb_check_rule_scope",
                "pcb_get_design_rules"],
        "avoid": [],
        "note": (
            "pcb_find_dominant_rule asks Altium which rule of a kind governs "
            "one object, or a pair (\"U1.3\" a pad, \"U1\" a part, "
            "\"net:GND\" a net), priorities resolved: what DRC and the router "
            "use. pcb_check_rule_scope says whether one named rule's scopes "
            "take in the objects at all. Run both after writing a rule."),
    },
    {
        "task": "Mark parts not fitted, or give them an alternate part, in a variant",
        "keywords": ("fitted", "dnp", "alternate", "populate",
                     "variation"),
        "when": (("not", "fitted"), ("dnp",), ("alternate", "part"),
                 ("variation",)),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["proj_list_variants", "proj_set_component_variation",
                "proj_create_variant", "proj_delete_variant",
                "proj_set_active_variant", "proj_save"],
        "avoid": [],
        "note": (
            "proj_set_component_variation sets one part's entry in a "
            "variant (fitted, not_fitted, alternate) or removes it, and "
            "reads it back; save the project afterwards."),
    },
    {
        "task": "Varied values and the BOM of a variant",
        "keywords": ("varied", "variation"),
        "when": (("variant", "value"), ("variant", "parameter"),
                 ("rename", "variant"), ("copy", "variant"),
                 ("duplicate", "variant"), ("variant", "bom"),
                 ("bom", "variant")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["proj_set_parameter_variation", "proj_rename_variant",
                "proj_copy_variant", "proj_get_bom", "proj_list_variants",
                "proj_save"],
        "avoid": [],
        "note": (
            "proj_set_parameter_variation sets or clears the values one part "
            "takes in a variant (e.g. Value 4k7 instead of 10k). "
            "proj_get_bom with variant= drops that variant's not-fitted "
            "parts and applies its alternates and values. proj_copy_variant "
            "starts a variant from another; save the project afterwards."),
    },
    {
        "task": "Set up drill pairs, backdrills and drill drawings",
        "keywords": ("drill", "pair", "backdrill", "back-drill", "drawing",
                     "guide", "blind", "buried"),
        "when": (("drill", "pair"), ("backdrill",), ("drill", "drawing")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_drill_pairs", "pcb_add_drill_pair",
                "pcb_set_drill_pair"],
        "avoid": [],
        "note": "pcb_add_drill_pair adds a blind or buried pair between two "
                "copper layers. Hole tolerances are obj_modify "
                "HolePositiveTolerance / HoleNegativeTolerance on pads and "
                "vias, in mils.",
    },
    {
        "task": "Put a barcode, QR code or multiline text on the board",
        "keywords": ("barcode", "qr", "serial", "label", "multiline",
                     "text", "frame", "code"),
        "when": (("barcode",), ("qr",), ("multiline",)),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_place_text", "obj_query", "obj_modify"],
        "avoid": [],
        "note": (
            "Place the text, then obj_modify on eTextObject: BarCodeKind, "
            "BarCodeRenderMode (Altium's numbers: read one back from a "
            "barcode made in the GUI), BarCodeFullWidth / FullHeight / "
            "XMargin / YMargin / MinWidth in mils, BarCodeShowText, "
            "BarCodeInverted; or Multiline with MultilineTextWidth / Height."),
    },
    {
        "task": "Format a board dimension's text and arrows",
        "keywords": ("dimension", "arrow", "precision", "units", "prefix",
                     "suffix", "extension"),
        "when": (("dimension", "text"), ("dimension", "arrow"),
                 ("dimension", "precision")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_place_dimension", "obj_query", "obj_modify"],
        "avoid": [],
        "note": (
            "obj_modify on eDimensionObject: TextHeight, TextWidth, "
            "TextLineWidth, TextGap, ArrowSize, ArrowLength, ArrowLineWidth, "
            "ExtensionOffset, ExtensionLineWidth, ExtensionPickGap (mils); "
            "TextPrecision, TextDimensionUnit, TextPosition, ArrowPosition "
            "(Altium's numbers); TextFormat, TextPrefix, TextSuffix."),
    },
    {
        "task": "Set a schematic sheet's reference zones, margin or border",
        "keywords": ("zones", "zone", "margin", "border", "sheet",
                     "template", "graphics"),
        "when": (("sheet", "zone"), ("sheet", "margin"), ("sheet", "border")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_set_sheet_options", "sch_set_sheet_size"],
        "avoid": [],
        "note": "sch_set_sheet_options with no arguments reads the settings.",
    },
    {
        "task": "Give a library symbol aliases (other part names)",
        "keywords": ("alias", "aliases", "symbol", "name", "variant", "part"),
        "when": (("alias",), ("aliases",)),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_set_symbol_aliases"],
        "avoid": [],
        "note": "Save the library afterwards.",
    },
    {
        "task": "Compare the classes and pairs the schematic declares with the board",
        "keywords": ("class", "classes", "differential", "pair", "room",
                     "declared", "directive", "schematic", "board"),
        "when": (("declared", "class"), ("schematic", "class"),
                 ("net", "class", "directive")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["proj_get_declared_design_objects", "pcb_get_net_classes",
                "pcb_get_differential_pairs"],
        "avoid": [],
        "note": (
            "proj_get_declared_design_objects reads the compiled project: "
            "what an Update PCB would create. A class or pair there and not "
            "on the board means the update has not been run."),
    },
    {
        "task": "Set up pin and part swapping",
        "keywords": ("swap", "swapping", "pin", "part", "gate", "equivalent"),
        "when": (("swap",), ("swapping",)),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify"],
        "avoid": [],
        "note": (
            "Symbol pins take SwapIdPart, SwapIdPin, SwapIdPair and "
            "SwapIdPartPin through obj_modify (lib_component:NAME scope in "
            "a SchLib). On the board, a part's EnablePinSwapping and "
            "EnablePartSwapping switch it on."),
    },
    {
        "task": "Length-match a bus through series resistors or capacitors",
        "keywords": ("xsignal", "xsignals", "series", "length", "match",
                     "ddr", "termination", "coupling"),
        "when": (("xsignal",), ("length", "series"), ("match", "through")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_create_xsignals", "pcb_get_xsignals",
                "pcb_delete_xsignals", "pcb_create_design_rule",
                "pcb_tune_length"],
        "avoid": [("pcb_get_trace_lengths", "measures one net, so it "
                                            "stops at the series part")],
        "note": (
            "pcb_create_xsignals takes the two end pins (\"U4.4\", "
            "\"LED1.3\") and Altium finds the path through the series "
            "parts; give class_name to group them for a matched-length "
            "rule. pcb_get_xsignals reads the lengths back."),
    },
    {
        "task": "Find where a net gets its name",
        "keywords": ("net", "name", "named", "label", "power", "port",
                     "sheet", "entry", "connector", "auto"),
        "when": (("net", "named"), ("net", "name", "where"),
                 ("auto", "generated", "net")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["proj_get_net_items", "proj_get_nets"],
        "avoid": [],
        "note": (
            "proj_get_net_items lists each compiled net's labels, power "
            "ports, ports, sheet entries and off-sheet connectors with "
            "sheet and position, and flags names the compiler made up. "
            "named_only=True skips nets nothing names."),
    },
    {
        "task": "Check a schematic library with Altium's component rule check",
        "keywords": ("schlib", "check", "rule", "missing", "errors",
                     "description", "symbols"),
        "when": (("library", "check"), ("schlib", "check"),
                 ("missing", "footprint", "symbol")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_check_schlib", "lib_audit_styles"],
        "avoid": [],
        "note": "Reads Altium's <library>.ERR report back as one entry per symbol.",
    },
    {
        "task": "Make a PCB library from a board, or an integrated library from a project",
        "keywords": ("make", "board", "integrated", "intlib", "harvest",
                     "extract"),
        "when": (("library", "from", "board"), ("pcb", "library", "board"),
                 ("integrated", "library", "project"), ("make", "intlib")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["pcb_make_library", "proj_make_integrated_library",
                "proj_make_schematic_library"],
        "avoid": [],
        "note": ("Altium's Make PCB Library and Make Integrated Library, both "
                 "without a dialog; the PcbLib is saved and closed, the IntLib "
                 "lands beside the project."),
    },
    {
        "task": "Check a PCB library with the component rule check",
        "keywords": ("pcblib", "shorted", "unconnected", "mirrored"),
        "when": (("pcb", "library", "check"), ("pcblib", "check"),
                 ("shorted", "copper"), ("unconnected", "copper")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_check_pcblib"],
        "avoid": [],
        "note": ("Altium's Component Rule Check for a PcbLib runs only from its "
                 "dialog; this runs the same checks with Altium's own distance "
                 "between primitives."),
    },
    {
        "task": "Pad and via templates: read them, make one, save them to a .PvLib",
        "keywords": ("pvlib", "template", "templates", "pad", "via", "padstack"),
        "when": (("pvlib",), ("via", "template"), ("pad", "template"),
                 ("pad", "via", "library")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_pad_via_library", "pcb_add_pad_via_template",
                "pcb_make_pad_via_library", "lib_install_library",
                "pcb_apply_via_template"],
        "avoid": [],
        "note": ("A board keeps its own pad/via library; pcb_make_pad_via_library "
                 "writes it to a file and can install it."),
    },
    {
        "task": "Set a symbol pin's package length or propagation delay",
        "keywords": ("pin", "package", "length", "propagation", "delay", "die"),
        "when": (("package", "length"), ("propagation", "delay"),
                 ("pin", "delay")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_set_pin_timing", "lib_get_pin_list"],
        "avoid": [],
        "note": "lib_get_pin_list reads both values back for every pin.",
    },
    {
        "task": "Stages to production: feasibility, prototype, EVT, DVT, PVT",
        "keywords": ("evt", "dvt", "pvt", "npi", "gate", "feasibility", "prototype",
                     "production", "phase"),
        "when": (("evt",), ("dvt",), ("pvt",), ("npi",), ("phase", "gate"),
                 ("ready", "production")),
        "document_kind": "any",
        "backends": ("altium", "easyeda"),
        "use": ["design_phase_standing", "design_phase_gate", "design_phase_answer"],
        "avoid": [],
        "note": ("A product is in the first stage that does not clear. Each "
                 "check names the tools that answer it; record each result with "
                 "its evidence."),
    },
    {
        "task": "Simulate a SPICE deck in ngspice or LTspice",
        "keywords": ("ltspice", "ngspice", "spice", "deck", "simulate", "netlist"),
        "when": (("ltspice",), ("ngspice", "deck"), ("simulate", "deck"),
                 ("spice", "deck")),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["design_simulate_deck", "design_simulators", "design_export_spice_deck"],
        "avoid": [],
        "note": ("engine='auto' prefers ngspice and uses LTspice when only it is "
                 "installed; name 'ltspice' to run there. Altium's own simulator "
                 "is sim_run_deck."),
    },
    {
        "task": "Check footprint names against IPC-7351 and the company naming policy",
        "keywords": ("ipc", "7351", "naming", "policy", "convention", "name"),
        "when": (("ipc", "name"), ("naming", "policy"), ("naming", "convention")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_verify_ipc_names", "footprint_read_ipc_name", "lib_check_naming",
                "lib_make_name"],
        "avoid": [],
        "note": "The naming policy is the user's naming.json in EDA_AGENT_POLICY_DIR.",
    },
    {
        "task": "Turn a datasheet pin table into symbol pins",
        "keywords": ("pin", "table", "csv", "pinout", "datasheet"),
        "when": (("pin", "table"), ("pinout", "csv")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_read_pin_table", "lib_create_ic_symbol"],
        "avoid": [],
        "note": "Checks numbers, names and types, then splits pins left and right.",
    },
    {
        "task": "Check the design against the company's own rules",
        "keywords": ("house", "company", "ruleset", "guidelines", "rules", "policy"),
        "when": (("house", "rules"), ("company", "rules"), ("ruleset",),
                 ("design", "guidelines")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["design_check_company_rules", "design_read_rules_export"],
        "avoid": [],
        "note": "Rulesets are JSON in rules/ under EDA_AGENT_POLICY_DIR.",
    },
    {
        "task": "Compare two design plans",
        "keywords": ("plan", "compare", "changed", "difference"),
        "when": (("compare", "plans"), ("what", "changed", "plan")),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["design_compare_plans"],
        "avoid": [],
        "note": "Parts and nets added, removed or changed, and pins moved.",
    },
    {
        "task": "Altium 365: workspaces, projects, the managed library",
        "keywords": ("365", "altium365", "workspace", "nexar", "managed", "release",
                     "publish"),
        "when": (("altium", "365"), ("altium365",), ("nexar",), ("managed", "component"),
                 ("release", "component")),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["altium365_check_setup", "altium365_list_workspaces",
                "altium365_list_projects", "altium365_list_components",
                "altium365_run_query", "altium365_describe_type",
                "altium365_publish_library_file", "altium365_release_component",
                "altium365_set_component_parameters"],
        "avoid": [],
        "note": ("Set up with EDA_AGENT_ALTIUM365_DOMAIN and EDA_AGENT_ALTIUM365_TOKEN, "
                 "or Nexar with eda-agent altium365-login. Changes need confirm=True."),
    },
    {
        "task": "Read one pour's outline, arcs included",
        "keywords": ("pour", "polygon", "outline", "vertices", "arc", "shape"),
        "when": (("polygon", "outline"), ("pour", "outline"), ("polygon", "vertices"),
                 ("pour", "vertices")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_pour_outline", "pcb_get_polygons"],
        "avoid": [],
        "note": ("Index and name from pcb_get_polygons; the outline the pour fills, "
                 "not the copper it poured."),
    },
    {
        "task": "Read the copper one pour poured, holes included",
        "keywords": ("poured", "holes", "pieces"),
        "when": (("poured", "copper"), ("pour", "holes"), ("pour", "pieces"),
                 ("poured", "pieces")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_pour_copper", "pcb_get_polygons"],
        "avoid": [],
        "note": ("Index and name from pcb_get_polygons; each poured piece with its "
                 "outline and holes. A hatched pour's tracks are read by pcb_get_copper."),
    },
    {
        "task": "Rename or swap designators across a whole project",
        "keywords": ("swap", "designator", "designators"),
        "when": (("swap", "designator"), ("rename", "designator"),
                 ("swap", "designators"), ("rename", "designators")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_rename_designators"],
        "avoid": ["obj_modify"],
        "note": ("Collisions are refused before any write; parts go through "
                 "temporary names, so a swap works. proj_annotate renumbers a "
                 "whole design."),
    },
    {
        "task": "Place many database library (DbLib) parts at once",
        "keywords": ("dblib", "database"),
        "when": (("dblib", "parts"), ("database", "library", "parts"),
                 ("place", "dblib")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_place_dblib_rows", "sch_place_dblib_component",
                "lib_dblib_search"],
        "avoid": [],
        "note": "One call; each row placed and read back, a failed row reported.",
    },
    {
        "task": "Move a footprint's 3D body within the footprint",
        "keywords": ("3d", "body", "model", "move", "centre", "center", "offset",
                     "position"),
        "when": (("move", "3d", "body"), ("3d", "body", "position"),
                 ("3d", "model", "offset"), ("centre", "3d", "body")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_set_3d_body"],
        "avoid": ["obj_modify"],
        "note": ("center_x / center_y with body_index put the body's box centre "
                 "there, relative to the footprint origin, and read it back."),
    },
    {
        "task": "Group placed components by package before replicating a channel layout",
        "keywords": ("same", "package", "channel", "repeated", "placed"),
        "when": (("same", "package"), ("repeated", "channel")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_find_same_package", "pcb_replicate_layout",
                "pcb_copy_component_placement"],
        "avoid": [],
        "note": "With a designator it lists the parts sharing that part's package.",
    },
    {
        "task": "Reset designator and comment positions on placed parts",
        "keywords": ("designator", "comment", "position", "autoposition",
                     "anchor", "text", "lock", "strings"),
        "when": (("designator", "position"), ("comment", "position"),
                 ("autoposition",)),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_auto_position_text", "obj_modify",
                "pcb_autoplace_silkscreen", "pcb_set_text_visibility"],
        "avoid": [],
        "note": (
            "Altium moves only shown text: a hidden designator takes the "
            "anchor and stays where it is. obj_modify on eComponentObject "
            "also writes LockStrings, NameAutoPosition and "
            "CommentAutoPosition. pcb_autoplace_silkscreen is the one that "
            "looks for silk clearance."),
    },
    {
        "task": "Hide a net's connection lines or keep its loops while routing",
        "keywords": ("ratsnest", "connection", "lines", "hide", "show",
                     "loop", "removal", "loops"),
        "when": (("hide", "ratsnest"), ("connection", "lines"),
                 ("loop", "removal")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_net_display"],
        "avoid": [],
        "note": "Reads back each net's connections_shown and loop_removal.",
    },
    {
        "task": "Make a schematic library from a project's placed symbols",
        "keywords": ("make", "schematic", "library", "symbols", "project",
                     "extract", "harvest"),
        "when": (("schematic", "library", "project"),
                 ("symbols", "project", "library")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["proj_load_sheets", "proj_make_schematic_library"],
        "avoid": [],
        "note": "One sheet must be loaded; the new library is saved and left open.",
    },
    {
        "task": "Write one footprint to a PCB library file of its own",
        "keywords": ("footprint", "single", "own", "file", "export", "pcblib",
                     "separate"),
        "when": (("footprint", "own", "library"), ("one", "footprint", "file")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_save_footprint_library"],
        "avoid": [],
        "note": ("Splitting a whole library into one file per part is not "
                 "reachable from a script on AD 26.11; call this once per "
                 "footprint instead."),
    },
    {
        "task": "Set a symbol pin's functions, symbols or hidden net",
        "keywords": ("pin", "function", "functions", "alternate", "symbol",
                     "dot", "clock", "hidden", "net"),
        "when": (("pin", "function"), ("pin", "symbol"), ("hidden", "net")),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify"],
        "avoid": [],
        "note": ("obj_modify on ePin: DefinedFunctions (\"PA0;ADC_IN0\", "
                 "writing replaces the list), Symbol_Inner, Symbol_Outer, "
                 "Symbol_InnerEdge, Symbol_OuterEdge, Symbol_LineWidth "
                 "(Altium's numbers) and HiddenNetName. On eSchComponent: "
                 "ShowHiddenPins, ShowHiddenFields, PinsMoveable, "
                 "PartIdLocked, DisplayFieldNames, OverideColors, PinColor. "
                 "Where a pin's name and number sit: Name_PositionMode, "
                 "Name_CustomPosition_Margin and the same for Designator_."),
    },
    {
        "task": "Mark board pads as jumper ends or swap groups",
        "keywords": ("jumper", "swap", "pad", "group", "board"),
        "when": (("jumper",), ("pad", "swap")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify"],
        "avoid": [],
        "note": "obj_modify on ePadObject: JumperID (0 none), SwapID_Pad, SwapID_Part.",
    },
    {
        "task": "Reorder a project's documents or add library search paths",
        "keywords": ("order", "reorder", "document", "sheet", "search",
                     "path", "port", "entry", "names", "identifier",
                     "scope", "hierarchy", "output"),
        "when": (("reorder",), ("search", "path"), ("document", "order"),
                 ("port", "net", "names"), ("net", "identifier", "scope"),
                 ("output", "path")),
        "document_kind": "project",
        "backends": ("altium",),
        "use": ["proj_project_setup", "proj_save", "proj_get_options"],
        "avoid": [("proj_set_parameter", "makes a user parameter of that "
                                         "name; the option does not change")],
        "note": ("proj_project_setup also sets the net identifier scope "
                 "(hierarchy_mode: automatic, flat, hierarchical, global, "
                 "strict_hierarchical), the output path, sheet numbers on "
                 "local net names and the pin swap options. Changes stay in "
                 "memory until proj_save."),
    },
    {
        "task": "Show pad and via net names, single layer mode or highlighting on boards",
        "keywords": ("pad", "net", "names", "numbers", "via", "single", "layer",
                     "highlight", "special", "strings", "display", "show"),
        "when": (("pad", "net", "names"), ("pad", "numbers"),
                 ("single", "layer", "mode"), ("highlight",)),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_view_options"],
        "avoid": [("pcb_set_layer_visibility", "shows or hides layers, not "
                   "these drawing preferences")],
        "note": "Altium's own preferences, for every board; a board must be open.",
    },
    {
        "task": "Change the board editor's rotation step or selection behaviour",
        "keywords": ("rotation", "step", "rotate", "locked", "protect",
                     "selection", "inspector", "duplicate", "designators",
                     "pan"),
        "when": (("rotation", "step"), ("protect", "locked"),
                 ("duplicate", "designators")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_edit_options"],
        "avoid": [],
        "note": "Undo levels are reported but cannot be set from here.",
    },
    {
        "task": "Change schematic editor preferences such as default ground names or auto-increment",
        "keywords": ("schematic", "preferences", "ground", "earth", "increment",
                     "cut", "wires", "orthogonal", "negation", "margin",
                     "electrical", "hotspot"),
        "when": (("schematic", "preferences"), ("default", "ground"),
                 ("auto", "increment"), ("components", "cut", "wires"),
                 ("pin", "name", "margin")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_editor_options", "obj_set_grid"],
        "avoid": [],
        "note": ("Altium's own preferences, for every sheet. A sheet's "
                 "electrical grid is obj_set_grid hotspot_grid."),
    },
    {
        "task": "Change the board editor's routing preferences",
        "keywords": ("routing", "route", "mode", "walkaround", "push", "via",
                     "plow", "pour", "shove", "dead", "ends", "preferences"),
        "when": (("routing", "mode"), ("auto", "via"), ("plow", "pour"),
                 ("dead", "ends")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_route_options"],
        "avoid": [("pcb_create_design_rule", "a design rule constrains the "
                   "route; these say how the editor routes")],
        "note": "Altium's own preferences, for every board; a board must be open.",
    },
    {
        "task": "Find which pours contain a point on the board",
        "keywords": ("pour", "pours", "polygon", "point", "inside", "contains",
                     "via", "overlap"),
        "when": (("inside", "pour"), ("point", "pour"), ("which", "pour")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_pours_at"],
        "avoid": [],
        "note": "Tests the pour outline, not the poured copper.",
    },
    {
        "task": "Set a differential pair rule's impedance limits",
        "keywords": ("differential", "pair", "impedance", "ohms", "rule",
                     "driven", "profile"),
        "when": (("differential", "impedance"), ("pair", "impedance")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_design_rules", "pcb_set_rule_properties"],
        "avoid": [],
        "note": ("pcb_set_rule_properties dp_impedance_driven and "
                 "dp_min/max/preferred_impedance on the Differential Pairs "
                 "Routing rule. A rule with from_schematic true is rewritten "
                 "from the schematic: change it there."),
    },
    {
        "task": "Grow or shrink a pour's outline",
        "keywords": ("pour", "polygon", "grow", "shrink", "outline", "pull",
                     "back", "edge", "expand"),
        "when": (("grow", "pour"), ("shrink", "pour"), ("pull", "back", "pour")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_polygons", "pcb_grow_pour", "pcb_repour_polygons"],
        "avoid": [("pcb_modify_polygon", "changes a pour's net, layer or "
                   "settings, not its outline")],
        "note": "A negative distance shrinks. Repour afterwards.",
    },
    {
        "task": "Check how a part's pads connect to the internal planes",
        "keywords": ("plane", "planes", "relief", "thermal", "direct", "connect",
                     "connection", "pad", "clearance"),
        "when": (("plane", "connect"), ("pad", "relief"), ("plane", "relief")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_plane_connections", "pcb_set_rule_properties"],
        "avoid": [],
        "note": "Relief, direct or none as the rules decide it, with the relief sizes.",
    },
    {
        "task": "Change height, component clearance, corner, antenna, fanout or width-impedance rule settings",
        "keywords": ("height", "vertical", "gap", "corner", "setback", "antenna",
                     "fanout", "bga", "impedance", "width", "rule", "copper"),
        "when": (("height", "rule"), ("vertical", "gap"), ("corner", "setback"),
                 ("antenna",), ("fanout",), ("width", "impedance")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_design_rules", "pcb_set_rule_properties"],
        "avoid": [],
        "note": ("Each setting is for its own rule kind and is refused on "
                 "another; read back in settings."),
    },
    {
        "task": "Tidy a schematic sheet's wires",
        "keywords": ("wire", "wires", "tidy", "join", "merge", "overlap",
                     "optimize", "segments", "clean"),
        "when": (("tidy", "wires"), ("join", "wires"), ("merge", "wire")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_optimize_wires"],
        "avoid": [],
        "note": "Joins wires that continue one another and trims overlaps; connectivity stays.",
    },
    {
        "task": "Mark routing as pre-routed or set a pad's paste opening as a percentage",
        "keywords": ("pre", "route", "preroute", "autorouter", "keep", "paste",
                     "percent", "stencil", "mask", "mode"),
        "when": (("pre", "route"), ("paste", "percent"), ("mask", "mode")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify"],
        "avoid": [],
        "note": ("obj_modify IsPreRoute on tracks, arcs and vias; on pads "
                 "PasteMaskUsePercent with PasteMaskPercent, and the mask "
                 "modes (Altium's numbers: rule or manual)."),
    },
    {
        "task": "Set up a rigid-flex board: split it into regions, give each a layer stack, add bend lines",
        "keywords": ("rigid", "flex", "rigid-flex", "region", "regions", "split",
                     "bend", "bending", "stack", "substack", "fold", "planning"),
        "when": (("rigid", "flex"), ("bend", "line"), ("split", "region"),
                 ("layer", "stack", "region"), ("flex", "region")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_board_regions", "pcb_split_board", "pcb_set_board_region",
                "pcb_layer_stacks", "pcb_add_bend_line", "pcb_modify_bend_line",
                "pcb_delete_bend_line", "pcb_remove_split_line"],
        "avoid": [("pcb_set_board_shape", "reshapes the outline and rebuilds every "
                   "region from the split lines")],
        "note": ("Split, name the halves (they come back alike), make a flex "
                 "stack with only the flex copper layers, assign it, then draw "
                 "bend lines across the flex region. A board whose regions were "
                 "not made by split lines refuses split changes."),
    },
    {
        "task": "Gloss or retrace existing routing",
        "keywords": ("gloss", "retrace", "smooth", "clean", "routing", "tracks",
                     "shorten", "tidy"),
        "when": (("gloss",), ("retrace",), ("smooth", "routing")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_gloss"],
        "avoid": [("pcb_cleanup_tracks", "removes broken and overlapping pieces; "
                   "it does not reshape a route")],
        "note": "Per net; reports track count and length before and after. Check hand-routed copper.",
    },
    {
        "task": "Work in the layer stack manager: dielectrics, thickness, impedance profiles, layer order",
        "keywords": ("stack", "stackup", "dielectric", "loss", "tangent", "df", "dk",
                     "thickness", "impedance", "profile", "solder", "mask", "coverlay",
                     "stiffener", "insert", "above", "below", "via", "backdrill"),
        "when": (("loss", "tangent"), ("impedance", "profile"), ("board", "thickness"),
                 ("stack", "thickness"), ("dielectric",), ("stiffener",),
                 ("insert", "layer"), ("back", "drill"), ("via", "plugged")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_physical_stack", "pcb_set_dielectric", "pcb_add_layer",
                "pcb_impedance_profile", "pcb_impedance_profile_layer",
                "pcb_get_impedance_profiles", "pcb_via_structures"],
        "avoid": [("pcb_modify_layer", "edits a copper layer's own settings")],
        "note": ("pcb_physical_stack lists every layer with Z and thickness; "
                 "pcb_set_dielectric sets Dk, Df, thickness and stiffener on a "
                 "dielectric or mask layer; pcb_add_layer takes above / below."),
    },
    {
        "task": "Highlight nets in a colour on the board",
        "keywords": ("net", "color", "colour", "highlight", "override",
                     "schematic", "board"),
        "when": (("net", "color"), ("net", "colour"), ("highlight", "color"),
                 ("highlight", "colour")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_net_display", "proj_net_color", "pcb_view_options"],
        "avoid": [("obj_highlight_net", "selects and zooms to a net; it gives it no colour")],
        "note": ("pcb_net_display color + use_net_color colours nets on the board; "
                 "from_project copies each net's project colour (proj_net_color, "
                 "the schematic's) so both highlight alike."),
    },
    {
        "task": "Set the board's Gerber or print setup",
        "keywords": ("gerber", "print", "printout", "aperture", "film", "scale",
                     "rs274x", "decimals", "flash"),
        "when": (("gerber", "setup"), ("gerber", "options"), ("print", "setup"),
                 ("print", "scale"), ("aperture",)),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_gerber_options", "pcb_print_options"],
        "avoid": [("proj_generate_fab_package", "writes the files; these set what they hold")],
        "note": "Saved in the board; every option set is read back.",
    },
    {
        "task": "Set what pasting keeps, or a paste array",
        "keywords": ("paste", "array", "repeat", "increment", "circular"),
        "when": (("paste", "array"), ("paste", "special"), ("repeat", "paste")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_paste_options"],
        "avoid": [],
        "note": "Altium's preferences, for every board; steps in mils.",
    },
    {
        "task": "Find which sheet zone a part, label or port is in",
        "keywords": ("zone", "reference", "border", "grid", "where", "sheet"),
        "when": (("sheet", "zone"), ("reference", "zone"), ("which", "zone")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_reference_zones"],
        "avoid": [],
        "note": "Altium's own zone (\"3B\") for each object on the active sheet.",
    },
    {
        "task": "Set the board's snap, track, via or visible grid",
        "keywords": ("grid", "snap", "track", "via", "visible", "dot",
                     "board"),
        "when": (("board", "grid"), ("snap", "grid"), ("track", "grid"),
                 ("via", "grid")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_set_grids"],
        "avoid": [("obj_set_grid", "sets the schematic's grid, not the board's")],
        "note": ("Mils. Every value is read back; the grid for moving parts "
                 "follows the snap grid."),
    },
    {
        "task": "Export one placed part's 3D model to STEP",
        "keywords": ("step", "3d", "model", "export", "part", "component",
                     "placed"),
        "when": (("part", "step"), ("component", "model", "export"),
                 ("one", "part", "3d")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_export_component_model"],
        "avoid": [("proj_export_step", "writes the whole board")],
        "note": "Writes the part's bodies as placed, with no dialog.",
    },
    {
        "task": "Turn Altium's auto-save on or off or change how often it runs",
        "keywords": ("autosave", "auto", "save", "backup", "recovery",
                     "interval"),
        "when": (("auto", "save"), ("autosave",), ("backup", "interval")),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["app_autosave"],
        "avoid": [],
        "note": "A setting of Altium itself, for every document; minutes.",
    },
    {
        "task": "Lock a schematic parameter or keep it from library updates",
        "keywords": ("parameter", "lock", "read", "only", "synchronize",
                     "update", "library", "database"),
        "when": (("parameter", "read", "only"), ("parameter", "synchronize"),
                 ("lock", "parameter")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify"],
        "avoid": [],
        "note": ("obj_modify on eParameter: ReadOnlyState and ParamType "
                 "(Altium's numbers), AllowLibrarySynchronize, "
                 "AllowDatabaseSynchronize. Ports (ePort): AutoSize, "
                 "OverrideDisplayString, HarnessColor; the last two read "
                 "back but are lost on save and reopen (AD 26.11)."),
    },
    {
        "task": "Show a pour's removed copper or let Altium name pours",
        "keywords": ("pour", "polygon", "dead", "copper", "necks", "islands",
                     "name", "removed"),
        "when": (("dead", "copper"), ("removed", "necks"),
                 ("removed", "islands"), ("pour", "name")),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify"],
        "avoid": [],
        "note": ("obj_modify on ePolyObject: DrawDeadCopper, "
                 "DrawRemovedNecks, DrawRemovedIslands, ExpandOutline, "
                 "AutoGenerateName (renames the pour from Altium's "
                 "template at once). Board text: Inverted, "
                 "UseInvertedRectangle, InvertedTTTextBorder; "
                 "ConvertedString reads the text as shown."),
    },
)


#: Proven not possible, with the reason. Kept separate from the recipes
#: because "no tool exists" and "no route exists" call for different
#: responses: the first is a gap worth filling, the second is a dead end
#: worth remembering.
_NOT_POSSIBLE: tuple[dict[str, Any], ...] = (
    {
        "capability": "Rename a PCB designator directly from a script",
        "backends": ("altium",),
        "why": "Writing the designator on a PCB component crashes Altium.",
        "do_instead": ["proj_annotate"],
    },
    {
        "capability": "Apply an ECO without a human click",
        "backends": ("altium",),
        "why": ("The Engineering Change Order dialog is non-suppressible by "
                "design. It can be launched, not completed."),
        "do_instead": [],
    },
    {
        "capability": "Use hierarchical sheets in a schematic",
        "backends": ("easyeda",),
        "why": ("There is no hierarchy. Schematic PAGES are siblings with "
                "an order, and no class exposes a sheet symbol, a sheet "
                "entry or a parent link. A netlist here is flat because "
                "nothing is nested, not because anything was flattened, so "
                "a report describing an EasyEDA design as hierarchical is "
                "wrong. Reuse happens through circuit blocks instead."),
        "do_instead": ["easyeda_list_schematic_pages",
                       "easyeda_search_circuit_blocks"],
    },
    {
        "capability": "Place a reusable circuit block onto a schematic",
        "backends": ("easyeda",),
        "why": ("Documented and not present. lib_Cbb is fully available at "
                "runtime, so blocks can be found and managed, but "
                "sch_PrimitiveComponent.placeCbbSchematicPage is absent "
                "from the captured surface of editor 2.2.47.7 even though "
                "the reference specifies it. A newer build may carry it: "
                "this is a build gap, not an API gap."),
        "do_instead": ["easyeda_search_circuit_blocks"],
    },
    {
        "capability": "Add or remove teardrops",
        "backends": ("easyeda",),
        "why": ("No teardrop method exists. Checked in BOTH sources, "
                "because neither alone is conclusive: absent from the "
                "official class reference, and absent from a 675-method "
                "runtime capture. The only mention anywhere is a gerber "
                "export option, which is a rendering choice rather than "
                "an edit to the board."),
        "do_instead": [],
    },
    {
        "capability": "Place thieving copper",
        "backends": ("easyeda",),
        "why": ("No thieving method in the class reference or in the "
                "675-method runtime capture."),
        "do_instead": [],
    },
    {
        "capability": "Tune track length with a serpentine",
        "backends": ("easyeda",),
        "why": ("No length-tuning method in either source. Length "
                "MATCHING exists as a CONSTRAINT through pcb_Drc's "
                "equal-length net groups, which is the rule rather than "
                "the detour: the editor can be told which nets must "
                "match, and cannot be asked to draw the serpentine that "
                "makes them."),
        "do_instead": ["easyeda_create_length_match_group",
                       "easyeda_add_nets_to_length_match_group"],
    },
    {
        "capability": "Create test points on the board",
        "backends": ("easyeda",),
        "why": ("Only the EXPORT exists: getTestPointFile writes the "
                "flying-probe file for whatever the board already has. "
                "Nothing creates a test point, in either source."),
        "do_instead": ["easyeda_export_test_points"],
    },
    {
        "capability": "Delete a whole project",
        "backends": ("easyeda",),
        "why": ("EasyEDA's API has no project delete. dmt_Project offers "
                "create, open, move and the info reads, and no other class "
                "deletes one. The document tree can remove a board, folder, "
                "panel, PCB, schematic or schematic page, never the project."),
        "do_instead": ["easyeda_delete_schematic", "easyeda_delete_pcb",
                       "easyeda_delete_panel"],
    },
    {
        "capability": "Run DRC or ERC over the IPC API",
        "backends": ("kicad",),
        "why": ("KiCad's IPC API does not expose them. They run through "
                "kicad-cli instead, which this server drives for you."),
        "do_instead": ["run_drc", "run_erc"],
    },
)


#: Words that carry no subject. Without this a question matches any
#: entry whose prose happens to contain "the", which turned "calibrate
#: the coffee machine" into a hit on an unrelated dead end. A guide that
#: answers questions it was not asked is worse than one that misses,
#: because the answer looks authoritative.
_STOPWORDS = frozenset((
    "the", "and", "for", "with", "from", "into", "onto", "that", "this",
    "how", "can", "you", "was", "are", "does", "did", "any", "all",
    "get", "set", "use", "using", "make", "want", "need", "should",
    "many", "every", "each", "some", "without", "after", "before", "new",
    "one", "its", "their", "have", "has", "not",
    # Two-letter function words. "id" and "3d" are deliberately absent:
    # both are real subjects ("design item id", "3d body").
    "to", "of", "on", "in", "is", "it", "by", "at", "or", "an", "as",
    "be", "if", "my", "me", "do", "so", "up", "we", "us",
))


#: Words that name the WORLD rather than the task. Every question about a
#: library mentions "library"; every question about a board mentions
#: "board". Letting one of these alone qualify a curated recipe is what
#: made "place a 3d step model on the board" answer with the board
#: mechanical-layers recipe, and "set the design item id on many library
#: components" answer with the library one. They still count as a
#: tie-break, never as the reason a recipe is offered.
_GENERIC = frozenset((
    "board", "library", "schlib", "pcblib", "intlib", "symbol", "part",
    "component", "sheet", "schematic", "document", "project", "design",
    "place", "delete", "remove", "add", "create", "change", "edit",
    "update", "name", "rename", "kind", "type", "pin", "property",
    "object", "tool", "file", "value",
))


#: Words a caller uses for something the tools call differently. Every
#: entry is justified by a question that missed. Adding a word to pass a
#: held-out paraphrase is how a guide learns its test instead of its job,
#: so when put, reflect and duplicate were added for three held-out
#: misses, those questions moved into the tuned set and a NEW held-out
#: set was written first, and scored, before the change.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "put": ("place",),
    "reflect": ("mirror",),
    "duplicate": ("copy",),
    "flip": ("mirror",),
    "erase": ("delete",),
    "remove": ("delete",),
    "note": ("text", "frame"),
    "textbox": ("text", "frame"),
    "notfound": ("not", "found"),
    "relink": ("link", "library"),
    # "bulk change a property on every symbol" missed every batch_ tool,
    # because the tools say batch and the caller said bulk.
    "bulk": ("batch",),
}


def _stem(word: str) -> str:
    """Fold the plurals that decide matches: parts, symbols, libraries."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    """Whole words, camelCase and snake_case split, plurals folded.

    WHOLE WORDS, because substring matching found "name" inside "rename"
    and offered the mechanical-layers recipe for copying a symbol under a
    new name. CAMELCASE SPLIT, because docstrings say ``DesignItemId``
    and a caller types "design item id", and the two never met.
    """
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    out: set[str] = set()
    for raw in re.split(r"[^A-Za-z0-9]+", text.lower()):
        if len(raw) < 2 or raw in _STOPWORDS:
            continue
        out.add(_stem(raw))
    return out


def _query_tokens(task: str) -> set[str]:
    """The caller's words, plus the words the tools use for the same thing."""
    words = _tokens(task)
    joined = re.sub(r"[^a-z0-9]+", "", (task or "").lower())
    for key, extra in _SYNONYMS.items():
        if key in words or (len(key) > 6 and key in joined):
            words.update(extra)
    return words


_DELETE_VERBS = frozenset(("delete", "remove", "erase", "clear"))
_CREATE_VERBS = frozenset(("place", "add", "create", "put", "insert"))


def _direction_penalty(query: set[str], tool_words: set[str]) -> int:
    """A question about removing something is not answered by a creator.

    Asked to delete a text frame, the docstring search ranked
    sch_place_text_frame first: it matched "text" and "frame" better than
    anything that deletes. The verb decides which way the answer faces.
    """
    wants_delete = bool(query & _DELETE_VERBS)
    wants_create = bool(query & _CREATE_VERBS)
    if wants_delete and not wants_create and tool_words & _CREATE_VERBS:
        return 1
    if wants_create and not wants_delete and tool_words & _DELETE_VERBS:
        return 1
    return 0


def _score(recipe: dict, task: str, document_kind: str) -> int:
    """How much of the question this recipe accounts for. 0 means no.

    Ranked rather than first-match: "delete a component parameter" in a
    library hits both the parameter recipe and the footprint-primitive
    one on the word "delete", and returning whichever comes first in the
    table produced a confident WRONG answer, which is the exact failure
    this module exists to prevent. The subject words have to outweigh
    the verb, so a keyword hit counts for more than a tool-name hit.
    """
    if document_kind and recipe["document_kind"] not in (document_kind, "any"):
        return 0
    if not task:
        return 1
    words = _query_tokens(task)
    if not words:
        return 1

    # A DEAD END is matched on its title, generic words included: it has
    # no keyword list, and "Delete a whole project" is made of nothing
    # but world words. It needs two title words AND at least half the
    # title, because a spurious dead end is the most damaging answer this
    # guide can give: it tells the caller that something possible is not.
    if recipe.get("dead_end"):
        title = _tokens(recipe["task"])
        overlap = words & title
        if len(overlap) >= 2 and len(overlap) * 2 >= len(title):
            return 3 * len(overlap)
        return 0

    keywords: set[str] = set()
    for k in recipe["keywords"]:
        keywords |= _tokens(k)
    title = _tokens(recipe["task"])

    # A recipe is OFFERED only on a word that is specific to it. A
    # generic word ("library", "board", "place") shared with the question
    # used to be enough, and produced confident wrong answers: the
    # mechanical-layers recipe for placing a part, for copying a symbol,
    # and for setting a Design Item ID.
    specific = {w for w in words if w not in _GENERIC}
    specific_hits = specific & (keywords | title)

    # ``when`` patterns are for tasks that are made ENTIRELY of world
    # words. "Place a part from a library" contains nothing specific, so
    # the rule above could never offer it; a pattern names the
    # combination that does mean it, which one generic word never does.
    by_pattern = any({_stem(w) for w in pattern} <= words
                     for pattern in recipe.get("when", ()))

    if not by_pattern and not specific_hits:
        return 0

    score = 3 * len(specific_hits & title) + 3 * len(specific_hits & keywords)
    # Generic words break ties between recipes that both qualified.
    score += len((words - specific) & (keywords | title))
    if by_pattern:
        score += 6
    return score


def _matches(recipe: dict, task: str, document_kind: str) -> bool:
    return _score(recipe, task, document_kind) > 0


def avoid_entries(recipe: dict, backend: str = ""):
    """The ``avoid`` list for one recipe, narrowed to a backend.

    An entry may carry its own backend tuple as a third element. Without
    that narrowing a trap belonging to one editor is presented on every
    editor, which is the same wrong-tool-for-this-context error the
    guide exists to prevent.
    """
    out = []
    for entry in recipe["avoid"]:
        name, why = entry[0], entry[1]
        scope = entry[2] if len(entry) > 2 else recipe["backends"]
        if backend and backend not in scope:
            continue
        out.append({"tool": name, "why": why})
    return out


#: Phrases a docstring uses to send the reader somewhere else. Each is
#: a recipe that somebody already wrote next to the code, which is the
#: only place a recipe cannot go stale: the redirect and the tool it
#: redirects to are edited together or not at all.
_REDIRECT_MARKERS = (
    "use this rather than",
    "use this instead of",
    "prefer ",
    " instead of ",
    " rather than ",
    "this is the library tool",
    "acts on an open",
)

#: Written like a tool name. Used to pull the target out of a redirect
#: sentence rather than guessing which words are tools.
_TOOLNAME = re.compile(r"(?<![a-z0-9_])((?:app|lib|pcb|sch|obj|proj|design|audit|run|part"
                       r"|easyeda|kicad|sim|route|tool)_[a-z0-9_]+)")


def derived_recipes(task: str, docs: dict[str, str],
                    limit: int = 6) -> list[dict[str, Any]]:
    """Recipes read off the tool docstrings, for what the table misses.

    THE HAND-WRITTEN TABLE IS TEN ENTRIES AGAINST SEVEN HUNDRED TOOLS,
    and an empty answer from it reads as "no such capability" however
    carefully the note says otherwise. That reading has been made and
    reported more than once.

    Docstrings already carry the redirects: "use this rather than
    lib_link_3d_model", "this is the LIBRARY tool", "acts on an open
    .PcbDoc". They are maintained because they sit next to the code
    they describe, which is exactly what a separate list of ten is not.

    So a miss falls back to reading them. This finds fewer, vaguer
    answers than a curated recipe, and it finds them for every tool
    rather than for ten.
    """
    words = _query_tokens(task)
    if not words or not docs:
        return []
    specific = {w for w in words if w not in _GENERIC}

    out: list[tuple[int, str, dict[str, Any]]] = []
    for name, doc in docs.items():
        if not doc:
            continue
        name_words = _tokens(name)
        summary = _tokens((doc.strip().splitlines() or [""])[0])
        body = _tokens(doc)
        # The name counts most, the first line next, the body least: a
        # tool called pcb_place_3d_body answers "place a 3d body" better
        # than one whose tenth paragraph mentions it. Specific words
        # count double, so "library" cannot outvote "mirror".
        score = 0
        for w in words:
            weight = 2 if w in specific else 1
            if w in name_words:
                score += 4 * weight
            elif w in summary:
                score += 2 * weight
            elif w in body:
                score += 1 * weight
        # Divided, not subtracted. A fixed deduction lost to a strong name
        # match: sch_place_text_frame kept first place for "delete a text
        # frame" because "text" and "frame" outweighed any constant.
        if _direction_penalty(words, name_words):
            score //= 3
        if score <= 0:
            continue

        redirects: list[str] = []
        for line in doc.splitlines():
            ll = line.lower()
            if any(m in ll for m in _REDIRECT_MARKERS):
                for other in _TOOLNAME.findall(line):
                    if other != name and other not in redirects:
                        redirects.append(other)
        out.append((score, name, {
            "use": [name],
            "summary": (doc.strip().splitlines() or [""])[0].strip(),
            "see_also": redirects,
        }))

    out.sort(key=lambda t: (-t[0], t[1]))
    return [r for _s, _n, r in out[:limit]]


def guidance_for(task: str = "", document_kind: str = "",
                 backend: str = "",
                 docs: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """The recipes and dead ends matching one question. Pure, testable."""
    task = (task or "").strip()
    document_kind = (document_kind or "").strip().lower()
    backend = (backend or "").strip().lower()

    if document_kind and document_kind not in DOCUMENT_KINDS:
        return {"ok": False, "reason": (
            f"document_kind must be one of {', '.join(DOCUMENT_KINDS)}")}

    def on_backend(entry) -> bool:
        return not backend or backend in entry["backends"]

    ranked = sorted(
        ((_score(r, task, document_kind), i, r)
         for i, r in enumerate(_RECIPES) if on_backend(r)),
        key=lambda t: (-t[0], t[1]))
    recipes = []
    for score, _i, r in ranked:
        if score <= 0:
            continue
        # Copied, not mutated: the module-level table is shared by every
        # caller and narrowing it in place would leak one request's
        # backend filter into the next.
        recipes.append(dict(r, avoid=avoid_entries(r, backend)))
    scored_dead = []
    for i, d in enumerate(_NOT_POSSIBLE):
        if not on_backend(d):
            continue
        shim = {"task": d["capability"], "keywords": (),
                "use": d["do_instead"], "document_kind": "any",
                "dead_end": True}
        score = _score(shim, task, "")
        if score > 0:
            scored_dead.append((score, i, d))
    scored_dead.sort(key=lambda t: (-t[0], t[1]))
    dead_ends = [d for _s, _i, d in scored_dead]

    # ALWAYS, not only when the curated table missed. The old rule was
    # sound in principle and wrong in practice: a curated recipe sharing
    # one common word counted as a hit, so the docstring search almost
    # never ran, and the tool that actually answered the question was
    # never shown. Curated recipes still come first in the reply, which
    # is what "a hand-written recipe is the better answer" requires; they
    # just no longer hide everything else. Tools already named by a
    # curated recipe are not repeated.
    derived: list[dict[str, Any]] = []
    if task and docs:
        named = {t for r in recipes for t in r.get("use", [])}
        derived = [d for d in derived_recipes(task, docs)
                   if not set(d.get("use", [])) <= named]

    return {
        "ok": True,
        "matched": len(recipes),
        "recipes": recipes,
        "not_possible": dead_ends,
        "derived": derived,
        # An empty result is a real answer and must not read as an error.
        # It means this file has nothing on the subject, NOT that the
        # server cannot do it: fall back to tool_catalog.
        "note": ("No curated recipe covers this. `derived` is read off "
                 "the tool docstrings and is the next best thing; "
                 "tool_catalog searches the whole surface."
                 if derived else
                 ("No recipe covers this; search the surface with "
                  "tool_catalog before concluding a tool does not exist."
                  if not recipes and not dead_ends else "")),
    }


def with_reach(result: dict[str, Any], reach_of) -> dict[str, Any]:
    """Add ``reach``: how to call each named tool that is a member of a
    family tool or a single-item tool, neither of which is advertised."""
    if not result.get("ok"):
        return result
    named: set[str] = set()
    for group in ("recipes", "derived"):
        for entry in result.get(group) or []:
            for key in ("use", "avoid"):
                for item in entry.get(key) or []:
                    named.add(item.get("tool", "") if isinstance(item, dict)
                              else str(item))
    for dead in result.get("not_possible") or []:
        for item in dead.get("do_instead") or []:
            named.add(str(item))
    reach = {n: reach_of(n) for n in sorted(named) if n and reach_of(n)}
    if reach:
        result = dict(result, reach=reach)
    return result


def register_guidance_tools(mcp):
    """Register the guidance tool. Backend-agnostic, like the meta pair."""

    @mcp.tool()
    async def tool_guide(task: str = "", document_kind: str = "",
                         backend: str = "") -> dict[str, Any]:
        """How to do something correctly, and what is genuinely impossible.

        USE THIS BEFORE CONCLUDING A CAPABILITY IS MISSING. The costly
        mistakes are aiming a board tool at a library and reporting a
        capability absent when it exists; both give a confident wrong
        answer, not an error.

        Answers three things, kept apart: the tool and its
        prerequisites, the tool you were probably reaching for and why
        it acts on another document, and what is proven impossible,
        with the reason. An empty result means only that this guide has
        nothing on it; fall back to ``tool_catalog``.

        Ask here for help when a tool seems missing, you are not sure
        which tool to use, an operation seems not supported, or a call
        failed because the wrong document was open.

        Args:
            task: What you are trying to do, in your own words, e.g.
                "delete silkscreen from a footprint". Matched on
                keywords, so a rough phrase is fine.
            document_kind: Narrow to one of library, board, schematic,
                project, any. Optional.
            backend: altium, kicad or easyeda. Optional; omit to see
                every backend's answer.

        Returns:
            Dict with ``recipes`` (each with ``use``, ``avoid`` and a
            ``note``), ``not_possible``, and ``matched``.
        """
        # The docstrings are read from the live registry rather than a
        # copy, so a tool renamed or re-described is reflected the moment
        # it is, with nothing to update here.
        from .folds import reach_of

        docs: dict[str, str] = {}
        try:
            specs = list(await mcp.list_tools())
            specs += list((getattr(mcp, "hidden", {}) or {}).values())
            for spec in specs:
                docs[spec.name] = getattr(spec, "description", "") or ""
        except Exception:            # noqa: BLE001
            docs = {}
        result = guidance_for(task, document_kind, backend, docs)
        return with_reach(result, reach_of)
