# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Canned requests: the everyday asks, ready to send to whichever AI is in use.

Each request is an MCP prompt. The client lists them (Claude Desktop under
"+", Claude Code as /mcp__<server>__<name>, VS Code as /<server>.<name>) and
sends the text as the user's own message, so nothing here calls a model.

A request names the tools that answer it, per backend, and registers only on
a backend that has a list. The text is the same everywhere; only the tool
line differs, so a request never points at a tool the server does not have.

Users add their own, or replace a built-in by name, in ``requests.json`` in
the workspace: a list of ``{"name", "title", "description", "text",
"arguments": [{"name", "description", "required"}]}``, where ``{argument}``
in the text is filled from the arguments.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Optional

from pydantic import Field

logger = logging.getLogger(__name__)

REQUESTS_FILE = "requests.json"

#: Closes every built-in: the request asks for a report, and the user
#: decides what changes.
REPORT_ONLY = ("Report first and change nothing in the design until I say "
               "which fixes to make.")

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class Argument:
    name: str
    description: str
    required: bool = True
    #: How a given value reads in the text; an empty optional value drops it.
    phrase: str = "{}"


@dataclass(frozen=True)
class Request:
    name: str
    title: str
    description: str
    text: str
    #: Tools per backend. A backend absent here does not get the request;
    #: "*" is added to every backend's list.
    tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    arguments: tuple[Argument, ...] = ()

    def tools_for(self, backend: str) -> Optional[tuple[str, ...]]:
        own = self.tools.get("altium" if backend == "both" else backend)
        if own is None and "*" not in self.tools:
            return None
        shared = self.tools.get("*", ())
        return tuple(shared) + tuple(own or ())

    def render(self, backend: str, values: dict[str, str]) -> str:
        text = self.text
        for arg in self.arguments:
            value = (values.get(arg.name) or "").strip()
            text = text.replace("{" + arg.name + "}", arg.phrase.format(value) if value else "")
        text = re.sub(r"[ \t]+\n", "\n", text)
        tools = self.tools_for(backend)
        if tools:
            from .folds import call_form
            shown = list(dict.fromkeys(call_form(t) for t in tools))
            text += "\n\nTools for this: " + ", ".join(f"`{t}`" for t in shown) + "."
        return text


BUILT_IN: tuple[Request, ...] = (
    Request(
        name="review_board",
        title="Review the design",
        description="A release review of the open design, findings ranked by severity.",
        text=("Review the design that is open now the way an experienced engineer "
              "would before release. Rank the findings by severity; give each one's "
              "location (designator, net, sheet or board coordinates), why it matters "
              "and the fix. {focus}\n\n" + REPORT_ONLY),
        tools={"*": ("review_design", "run_drc"),
               "altium": ("pcb_layout_audit", "audit_find_acute_angles",
                          "audit_find_via_antennas"),
               "kicad": ("kicad_full_review",),
               "easyeda": ()},
        arguments=(Argument("focus", "An area to concentrate on, for example "
                                     "'the power stage'", required=False,
                            phrase="Concentrate on {}."),),
    ),
    Request(
        name="explain_drc",
        title="Run DRC and explain",
        description="Design rule check, violations grouped by rule with cause and fix.",
        text=("Run the design rule check on the open board. Group the violations "
              "by rule and, for each group, say what causes it, whether it is a real "
              "defect or a rule that does not fit this board, and how to fix it. Put "
              "the groups that would stop fabrication first.\n\n" + REPORT_ONLY),
        tools={"*": ("run_drc",),
               "altium": ("pcb_get_design_rules", "pcb_get_clearance_violations"),
               "kicad": (), "easyeda": ()},
    ),
    Request(
        name="explain_erc",
        title="Run ERC and explain",
        description="Electrical rule check, real problems separated from noise.",
        text=("Run the electrical rule check on the project. Group the messages, "
              "separate real connection problems from warnings that are expected in "
              "this design, and give the fix for each real one.\n\n" + REPORT_ONLY),
        tools={"*": ("run_erc",),
               "altium": ("proj_get_erc_violations", "proj_get_messages"),
               "kicad": (), "easyeda": ()},
    ),
    Request(
        name="check_sync",
        title="Schematic and board in sync?",
        description="Differences between the schematic and the board, and which way each should go.",
        text=("Compare the schematic with the board. List every difference "
              "(missing or extra parts, net changes, footprint or value changes) "
              "and say for each whether the board should follow the schematic or "
              "the schematic was changed on purpose.\n\n" + REPORT_ONLY),
        tools={"altium": ("proj_compare_sch_pcb", "proj_get_differences"),
               "kicad": ("kicad_compare_sch_pcb",),
               "easyeda": ("easyeda_compare_schematic_pcb",)},
    ),
    Request(
        name="schematic_check",
        title="Check the schematic",
        description="Unconnected pins, single-pin nets, missing decoupling, placeholders.",
        text=("Check the schematic for connection and completeness problems: "
              "unconnected IC pins, nets with one pin, ICs without decoupling, "
              "conflicting or orphan net labels, unmatched ports, placeholder values "
              "and parts without a datasheet. Say which hits are real and which are "
              "intended.\n\n" + REPORT_ONLY),
        tools={"altium": ("audit_find_unconnected_ic_pins", "audit_find_single_pin_nets",
                          "audit_find_missing_decoupling", "audit_find_net_label_conflicts",
                          "audit_find_orphan_net_labels", "audit_find_unmatched_ports",
                          "audit_find_placeholder_values", "audit_find_missing_datasheets"),
               "kicad": ("run_erc", "kicad_review_schematic", "kicad_get_unconnected_pins"),
               "easyeda": ("easyeda_audit_unconnected_schematic_pins",
                           "easyeda_audit_single_pin_nets_schematic",
                           "easyeda_audit_decoupling_schematic",
                           "easyeda_audit_placeholder_values_schematic",
                           "easyeda_audit_missing_datasheets")},
    ),
    Request(
        name="bom_check",
        title="Check the BOM",
        description="Missing or inconsistent part numbers, placeholder values, unfitted parts.",
        text=("Check the bill of materials: parts without a manufacturer part "
              "number, the same value with different part numbers, placeholder "
              "values, and parts not fitted or left out of the BOM. List what needs "
              "fixing before the parts can be ordered.\n\n" + REPORT_ONLY),
        tools={"altium": ("proj_get_bom", "audit_find_mpn_inconsistencies",
                          "audit_find_placeholder_values", "audit_variant_not_fitted"),
               "kicad": ("kicad_get_bom",),
               "easyeda": ("easyeda_audit_mpn_inconsistencies",
                           "easyeda_audit_placeholder_values",
                           "easyeda_audit_parts_excluded_from_bom")},
    ),
    Request(
        name="routing_status",
        title="What is left to route?",
        description="Unrouted nets by class, and a sensible order to route them.",
        text=("List the nets that are not fully routed on the open board, grouped "
              "by net class, and propose the order to route them in (critical and "
              "power first), with anything that blocks a route.\n\n" + REPORT_ONLY),
        tools={"altium": ("pcb_get_unrouted_nets", "pcb_get_net_classes"),
               "easyeda": ("easyeda_get_unrouted_nets", "easyeda_get_net_classes")},
    ),
    Request(
        name="release_check",
        title="Ready for fabrication?",
        description="A go / no-go table for sending the board out.",
        text=("Decide whether the board is ready to send for fabrication. Give a "
              "table of checks (design rules, electrical rules, schematic and board "
              "in sync, parts inside the outline, pads near the edge, mirrored text, "
              "overlapping designators, datasheets) with pass or fail and the "
              "blocking items, then a go or no-go.\n\n" + REPORT_ONLY),
        tools={"*": ("run_drc", "run_erc"),
               "altium": ("proj_compare_sch_pcb", "audit_find_components_outside_board_outline",
                          "audit_find_pads_near_board_edge", "audit_find_mirrored_pcb_text",
                          "audit_find_designator_collisions", "audit_find_missing_datasheets"),
               "kicad": ("kicad_compare_sch_pcb",),
               "easyeda": ("easyeda_compare_schematic_pcb",
                           "easyeda_audit_components_outside_outline",
                           "easyeda_audit_pads_near_board_edge", "easyeda_audit_mirrored_text",
                           "easyeda_audit_designator_collisions")},
    ),
    Request(
        name="check_part",
        title="Check a part against its datasheet",
        description="Pins, footprint, ratings in this circuit and the support parts it needs.",
        text=("Check {designator} against its datasheet: the pin connections, the "
              "footprint, its ratings against what this circuit puts on it, and the "
              "support parts the datasheet asks for (decoupling, pull-ups, "
              "compensation). Cite the datasheet page for each point.\n\n" + REPORT_ONLY),
        tools={"*": ("design_datasheet_checklist", "list_components"),
               "altium": ("proj_get_component_info", "lib_audit_footprint_vs_datasheet"),
               "kicad": ("kicad_get_component_details",),
               "easyeda": ("easyeda_audit_footprint_vs_datasheet",)},
        arguments=(Argument("designator", "The part to check, for example U3"),),
    ),
    Request(
        name="trace_net",
        title="Where does this net go?",
        description="Every pin on a net and whether the connection looks right.",
        text=("Trace net {net}: every pin on it with its part and sheet, where it "
              "leaves a sheet, and whether anything on it looks wrong (a missing "
              "pull-up, a pin of the wrong direction, a single connection).\n\n"
              + REPORT_ONLY),
        tools={"*": ("list_nets",),
               "altium": ("obj_crossref_net", "proj_get_connectivity"),
               "kicad": ("kicad_get_net",),
               "easyeda": ("easyeda_get_netlist",)},
        arguments=(Argument("net", "The net name, for example VBUS"),),
    ),
    Request(
        name="describe_design",
        title="Describe this design",
        description="What the design is: function, blocks, rails, interfaces and key parts.",
        text=("Describe the design that is open, from the design itself: what it "
              "does, its functional blocks, the supply rails and where they come "
              "from, the external interfaces and the key parts. Mark anything you "
              "infer rather than read as an inference."),
        tools={"*": ("get_board_info", "list_components", "list_nets")},
    ),
)


def _from_json(entry: Any) -> Optional[Request]:
    """One user request, or None with a warning when it cannot be used."""
    if not isinstance(entry, dict):
        logger.warning("%s: skipped an entry that is not an object", REQUESTS_FILE)
        return None
    name = str(entry.get("name", "")).strip()
    text = str(entry.get("text", "")).strip()
    if not _NAME.match(name) or not text:
        logger.warning("%s: skipped %r: it needs a lowercase name (letters, digits, "
                       "underscores) and a text", REQUESTS_FILE, name)
        return None
    args = []
    for a in entry.get("arguments") or ():
        if isinstance(a, dict) and _NAME.match(str(a.get("name", ""))):
            args.append(Argument(str(a["name"]), str(a.get("description", "")),
                                 bool(a.get("required", True))))
    return Request(name=name, title=str(entry.get("title") or name),
                   description=str(entry.get("description") or ""),
                   text=text, tools={"*": ()}, arguments=tuple(args))


def load_requests(workspace: Optional[Path] = None) -> list[Request]:
    """The built-ins, with the workspace file's entries added or replacing by name."""
    if workspace is None:
        from ..config import get_config
        workspace = Path(get_config().workspace_dir)
    merged = {r.name: r for r in BUILT_IN}
    path = Path(workspace) / REQUESTS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        data = []
    except (OSError, ValueError) as exc:
        logger.warning("%s could not be read: %s", path, exc)
        data = []
    for entry in data if isinstance(data, list) else ():
        req = _from_json(entry)
        if req is not None:
            merged[req.name] = req
    return list(merged.values())


def _prompt_fn(req: Request, backend: str):
    """A function whose signature is the request's arguments, for FastMCP."""
    def fn(**values: str) -> str:
        return req.render(backend, values)

    params = []
    for arg in sorted(req.arguments, key=lambda a: not a.required):
        ann = Annotated[str, Field(description=arg.description)]
        params.append(inspect.Parameter(
            arg.name, inspect.Parameter.KEYWORD_ONLY, annotation=ann,
            default=inspect.Parameter.empty if arg.required else ""))
    fn.__signature__ = inspect.Signature(params, return_annotation=str)
    fn.__annotations__ = {p.name: p.annotation for p in params}
    fn.__annotations__["return"] = str
    fn.__name__ = req.name
    return fn


def register_request_prompts(mcp, backend: str,
                             workspace: Optional[Path] = None) -> list[str]:
    """Register the canned requests that apply to ``backend``; return their names."""
    if not hasattr(mcp, "prompt"):
        return []
    names = []
    for req in load_requests(workspace):
        if req.tools_for(backend) is None:
            continue
        mcp.prompt(name=req.name, title=req.title,
                   description=req.description)(_prompt_fn(req, backend))
        names.append(req.name)
    return names
