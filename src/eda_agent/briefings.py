# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What to know before working on one kind of document, per backend.

The server instructions carry only what applies to every document: a
client keeps the first 4096 characters of them and drops the rest. What
applies to one kind (how a schematic is drawn, how a board filter is
looked up) is here, and tool_guide returns it for a document_kind given
with no task. Tool names here are checked against each backend's surface
by tests/test_briefings.py.
"""

from __future__ import annotations

#: The kinds a briefing is given for; ``library`` means both libraries.
KINDS = ("board", "schematic", "pcblib", "schlib", "project", "outjob")
ALIASES = {"library": ("pcblib", "schlib")}

# Altium draws from a hand template (decided 2026-10-20: the automatic
# layout is being retired; a planned template came out clean first time
# where the engine did not after many rounds).
_SCHEMATIC_ALTIUM = (
    "A SCHEMATIC IS DRAWN FROM A HAND TEMPLATE, as an engineer draws,\n"
    "on the 100 mil grid: the IC in the middle, rails as straight wires\n"
    "with capacitors hanging straight down to ground, a divider as a\n"
    "vertical stack whose midpoint runs across to its pin, net labels only\n"
    "where a signal leaves the block. Read the pins (lib_get_pin_list),\n"
    "write the template (parts, wires through pin anchors like U1.EN,\n"
    "power ports, labels, expected nets), check it with\n"
    "sch_preview_template and look at its PNG, then draw it with\n"
    "sch_draw_template, which refuses a short or a misplaced part before\n"
    "the first wire. To CHANGE a block, redraw it from a template on a\n"
    "fresh sheet; the sch_ and obj_ tools are for small repairs.\n"
)

_SCHEMATIC_EASYEDA = (
    "A SCHEMATIC IS DRAWN FROM A HAND TEMPLATE. Build the DesignPlan,\n"
    "then plan the sheet the way an engineer draws it, every coordinate\n"
    "on the 100 mil grid: the IC in the middle, rails as straight wires\n"
    "with their capacitors hanging straight down to ground, a divider\n"
    "whose midpoint runs straight across to its pin, net labels only\n"
    "where a signal leaves the block. easyeda_emit_template returns the\n"
    "calls that place the parts there; run them, read the pins back with\n"
    "easyeda_get_schematic_pins, and easyeda_emit_template_connections\n"
    "checks the template's wiring against those pins and the plan's\n"
    "nets and returns the calls that draw it.\n"
)

#: Altium only: a filter or set string goes to Altium as written, so its
#: numbers are Altium's own, mils, whatever the session's unit (#448).
_OBJ_TEXT_ALTIUM = (
    "obj_ filter and set text goes to Altium as written: its lengths are\n"
    "mil in any session; obj_query and obj_modify take X_mm as mm.\n"
)

_BOARD_ALTIUM = (
    "ON A BOARD, FILTER WHERE ALTIUM CAN LOOK IT UP. An obj_ filter\n"
    "with an exact Net=, Component=, Layer= or BoundsLeft/BoundsBottom\n"
    "goes straight to its objects; any other condition (X=, Y=, Name)\n"
    "checks every object of the type, seconds on a busy board.\n"
    "pcb_get_copper reads a layer's copper in bulk; tool_guide names\n"
    "the quick route for a task.\n"
)

_PCBLIB_ALTIUM = (
    "lib_ tools act on the .PcbLib in front, and pcb_layer_view,\n"
    "pcb_pad_relief, pcb_renumber_pads and pcb_set_grids do too; any\n"
    "other pcb_ tool acts on a board, even with a library in front.\n"
    "Lands are built from the datasheet's drawing with\n"
    "lib_create_footprint_from_package, which checks them against it.\n"
)

_SCHLIB_ALTIUM = (
    "lib_ tools act on the .SchLib in front. A symbol is drawn with\n"
    "lib_create_ic_symbol, or lib_create_passive_symbol for a discrete\n"
    "part; lib_get_pin_list reads its pins back.\n"
)

_PROJECT_ALTIUM = (
    "proj_ tools act on the focused project, which follows the document\n"
    "in front; with several projects open, give project_path.\n"
    "proj_get_hierarchy reads the sheet hierarchy and its breaks,\n"
    "proj_get_net_items what names each net.\n"
)

_OUTJOB_ALTIUM = (
    "proj_create_outjob makes an OutJob, proj_list_outjob_containers\n"
    "lists its outputs, proj_run_outjob runs one container and\n"
    "proj_run_outjob_all every one; proj_run_output runs one output.\n"
)

_BRIEFINGS: dict[str, dict[str, str]] = {
    "altium": {
        "board": _BOARD_ALTIUM + "\n" + _OBJ_TEXT_ALTIUM,
        "schematic": _SCHEMATIC_ALTIUM + "\n" + _OBJ_TEXT_ALTIUM,
        "pcblib": _PCBLIB_ALTIUM,
        "schlib": _SCHLIB_ALTIUM,
        "project": _PROJECT_ALTIUM,
        "outjob": _OUTJOB_ALTIUM,
    },
    "easyeda": {"schematic": _SCHEMATIC_EASYEDA},
    "kicad": {},
}
_BRIEFINGS["both"] = _BRIEFINGS["altium"]


def briefing(kind: str, backend: str = "altium") -> str:
    """The briefing for one document kind on ``backend``: ``library``
    gives both libraries'. '' when there is none."""
    kinds = ALIASES.get(kind, (kind,))
    table = _BRIEFINGS.get((backend or "altium").strip().lower(), {})
    return "\n".join(table[k] for k in kinds if k in table)
