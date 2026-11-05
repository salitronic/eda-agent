# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""MCP Tools for Altium Designer.

Error-shape convention (two families, consistent within each):

- Bridge-backed tools (anything that round-trips Altium) report failures as
  ``{"error": "<message>", <count_field>: 0, ...}`` -- or raise, in which
  case the MCP layer surfaces the exception text. Successful payloads come
  from the Pascal handler verbatim.
- Offline calculators (``pcb_calc_*``, ``design_compute_*``, exporters)
  report ``{"ok": False, "reason": "<message>"}`` and ``{"ok": True, ...}``
  on success.

New tools should match the family they belong to rather than invent a third
shape.
"""

from typing import Any

from .application import (
    register_application_tools,
    register_meta_tools,
)
from .guidance import register_guidance_tools
from .update import register_update_tools
from .uiauto import register_uiauto_tools
from .project import register_project_tools
from .library import register_library_tools
from .ipc_batch import register_ipc_batch_tools
from .generic import register_generic_tools
from .pcb import register_pcb_tools
from .review import register_review_tools
from .sim import register_sim_tools
from .design import register_design_tools
from .render import register_render_tools
from .audit import register_audit_tools
from .route import register_route_tools
from .sch_template import register_sch_template_tools
from .easyeda import register_easyeda_tools
from .kicad import register_kicad_tools
from .kicad_board import register_kicad_board_tools
from .kicad_sch import register_kicad_sch_tools
from .kicad_lib import register_kicad_lib_tools
from .eda import register_eda_tools
from .calc import register_calc_tools, register_physics_calc_tools
from .plan import register_plan_tools
from .parts import register_parts_tools
from .live import register_live_tools
from .footprint import register_footprint_tools
from .conventions import register_convention_tools
from .altium365 import register_altium365_tools
from .requests import register_request_prompts

# Recognised backends. "altium" is the default and stays the full historical
# suite; "kicad" exposes only the KiCad-native tools; "easyeda" drives
# EasyEDA Pro through its extension API; "both" unions Altium and KiCad for
# the rare user who drives both EDA tools from one server.
#
# "both" deliberately does NOT include easyeda. It exists for the two
# desktop tools a user is likely to run side by side, and quietly widening
# it would change what an existing setting means.
BACKENDS = ("altium", "kicad", "easyeda", "both")
DEFAULT_BACKEND = "altium"

# "full" advertises every tool. "minimal" registers them all internally
# but advertises only tool_catalog + tool_invoke, for MCP clients that
# cap tool count or stall serializing hundreds of schemas at startup.
# See eda_agent.tools.registry for why this beats merging tools into
# generic dispatchers.
TOOLSETS = ("full", "minimal")
DEFAULT_TOOLSET = "full"

#: Design tools that compute over a plan and never touch a bridge, so
#: they work on any backend.
#:
#: The tools that BUILD and check a plan are not Altium's; on EasyEDA a
#: plan is drawn with ``easyeda_emit_template`` and
#: ``easyeda_emit_template_connections`` and run with ``easyeda_run_plan``.
#:
#: MEASURED, not judged by name. Each was called with the Altium bridge
#: replaced by a tripwire, and only the ones that never reached it are
#: here: a tool that catches a bridge failure and answers anyway would
#: quietly answer with less than it appears to on a backend with no
#: Altium.
#:
#: A test re-runs that measurement, so this list cannot go stale by
#: someone adding a bridge call to a tool named here. It only asks
#: whether a NAMED tool stays offline, so a tool that stopped touching
#: the bridge stays withheld until someone re-measures it; when doing
#: that, trip every route to the bridge, not only
#: ``eda_agent.bridge.get_bridge``.
OFFLINE_DESIGN_TOOLS = (
    "design_add_circuit_block",
    "design_add_part",
    "design_apply_hierarchy",
    "design_autonomy_guide",
    "design_bom_file",
    "design_compose_netlist",
    "design_compute_component_value",
    "design_connect_bus",
    "design_describe_circuits",
    "design_edit_plan",
    "design_fab_vendor_profile",
    "design_generate_bom",
    "design_get_laminate",
    "design_get_stackup",
    "design_get_discipline",
    "design_list_circuit_blocks",
    "design_list_fab_vendors",
    "design_list_stackups",
    "design_load_fab_profile",
    "design_next_action",
    "design_phase_answer",
    "design_phase_gate",
    "design_phase_standing",
    "design_plan_hierarchy",
    "design_review_file",
    "design_session_log",
    "design_session_report",
    "design_session_resume",
    "design_session_start",
    "design_session_status",
    "design_simulate_deck",
    "design_solve_netlist_file",
    "design_suggest_diff_pair_traces",
    "design_suggest_partition",
    "design_synthesize_rules",
    "design_validate_requirement",
)


def register_offline_design_tools(mcp) -> None:
    """Register the plan-building design tools that need no bridge.

    The design tools are defined together in one function, so they are
    captured into a throwaway registry and only the named ones are
    re-registered. That keeps the split in one list rather than
    scattering conditionals through a 2000-line module.
    """
    from .design import register_design_tools
    from .registry import ToolRegistry

    capture = ToolRegistry()
    register_design_tools(capture)

    missing = [n for n in OFFLINE_DESIGN_TOOLS if capture.get(n) is None]
    if missing:
        # A renamed tool would otherwise drop silently off this backend.
        raise RuntimeError(
            f"OFFLINE_DESIGN_TOOLS names tools that no longer exist: "
            f"{missing}")

    for name in OFFLINE_DESIGN_TOOLS:
        mcp.tool()(capture.get(name).fn)


#: Review tools that need no bridge. Measured the same way as
#: OFFLINE_DESIGN_TOOLS: called with the Altium bridge replaced by a
#: tripwire, and the tripwire did not fire. The datasheet discipline is
#: about how a part is verified, not about which editor is open, so
#: withholding it from a backend taught nothing except that the rules
#: are Altium's. They are not.
OFFLINE_REVIEW_TOOLS = (
    "design_datasheet_checklist",
)


def register_offline_review_tools(mcp) -> None:
    """Register the review tools that need no bridge.

    Same capture-and-reselect shape as the design half, and the same
    reason: the review tools are defined together, and a conditional
    per tool inside that module would scatter the split.
    """
    from .registry import ToolRegistry
    from .review import register_review_tools

    capture = ToolRegistry()
    register_review_tools(capture)

    missing = [n for n in OFFLINE_REVIEW_TOOLS if capture.get(n) is None]
    if missing:
        # A renamed tool would otherwise drop silently off this backend.
        raise RuntimeError(
            f"OFFLINE_REVIEW_TOOLS names tools that no longer exist: "
            f"{missing}")

    for name in OFFLINE_REVIEW_TOOLS:
        mcp.tool()(capture.get(name).fn)


def register_all_tools(mcp):
    """Register all Altium tools with the MCP server."""
    # Dialog-driving tools. Altium-only and Windows-only:
    # they press buttons in the GUI for the one operation that
    # has no API at all, so they do not belong on any other
    # backend and are registered with the Altium suite alone.
    register_uiauto_tools(mcp)
    register_application_tools(mcp)
    register_project_tools(mcp)
    register_library_tools(mcp)
    register_ipc_batch_tools(mcp)
    register_generic_tools(mcp)
    register_pcb_tools(mcp)
    register_review_tools(mcp)
    register_sim_tools(mcp)
    register_design_tools(mcp)
    register_render_tools(mcp)
    register_audit_tools(mcp)
    register_route_tools(mcp)
    register_sch_template_tools(mcp)
    register_convention_tools(mcp)
    register_altium365_tools(mcp)


def register_backend(
    mcp, backend: str = DEFAULT_BACKEND, toolset: str = DEFAULT_TOOLSET,
) -> str:
    """Register the tool surface for one backend and return the name used.

    An unknown backend falls back to the default rather than raising, so a
    stray value in the environment can never leave the server toolless.

    Args:
        toolset: "full" advertises every tool. "minimal" advertises only
            tool_catalog and tool_invoke while keeping all the others
            reachable through them, for clients that cannot cope with a
            few hundred tools.
    """
    toolset = (toolset or DEFAULT_TOOLSET).strip().lower()
    if toolset not in TOOLSETS:
        toolset = DEFAULT_TOOLSET
    if toolset == "minimal":
        name = _register_minimal(mcp, backend)
    else:
        name = _register_full(mcp, backend)
    _drop_schema_titles(mcp)
    # The canned requests go on the real server under either toolset: a
    # minimal server hides tools, not the prompts a user picks from.
    register_request_prompts(mcp, name)
    return name


def _without_titles(schema: Any) -> Any:
    """A JSON schema without the "title" keywords pydantic generates.

    Each one repeats its property's name ("layer" -> "Layer") and is sent
    with every tool listing. A property that is itself called "title" is
    a name under "properties", not the keyword, and is kept.
    """
    if isinstance(schema, list):
        return [_without_titles(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            out[key] = {name: _without_titles(sub) for name, sub in value.items()}
        else:
            out[key] = _without_titles(value)
    return out


def _drop_schema_titles(mcp) -> None:
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return
    for tool in manager.list_tools():
        tool.parameters = _without_titles(tool.parameters)


def _register_minimal(mcp, backend: str) -> str:
    """Capture the whole surface, then advertise only the meta-tools."""
    from .registry import MINIMAL_TOOLS, ToolRegistry

    registry = ToolRegistry()
    name = _register_full(registry, backend)
    missing = [t for t in MINIMAL_TOOLS if t not in registry]
    if missing:
        # Advertising nothing usable would be worse than a full surface,
        # so fall back loudly rather than serve an unusable server.
        raise RuntimeError(
            f"minimal toolset needs {missing}, which the backend did not "
            f"register")
    for tool_name in MINIMAL_TOOLS:
        # These close over `registry`, so they discover and dispatch
        # across everything captured above even though the client sees
        # only these two.
        mcp.tool()(registry.get(tool_name).fn)
    return name


def _register_full(server, backend: str = DEFAULT_BACKEND) -> str:
    # Families of tools that differ by a mode register as one tool each;
    # see folds.py. The members stay reachable through tool_invoke.
    from .folds import FoldingServer
    mcp = FoldingServer(server)
    backend = _register_surface(mcp, backend)
    mcp.finish()
    return backend


def _register_surface(mcp, backend: str = DEFAULT_BACKEND) -> str:
    backend = (backend or DEFAULT_BACKEND).strip().lower()
    if backend not in BACKENDS:
        backend = DEFAULT_BACKEND
    # ONE SOURCE OF TRUTH FOR WHICH EDA IS ACTIVE. The neutral tools
    # (review_design and the rest) used to resolve their backend from
    # EDA_AGENT_BACKEND independently of this argument, so the two agreed
    # only while a single caller set both. Registering the EasyEDA
    # surface without also exporting the variable produced EasyEDA tools
    # over an ALTIUM resolver, and review_design reported a clean,
    # plausible review of an entirely different design.
    from ..core.backends import set_active_backend
    set_active_backend(backend)
    if backend in ("altium", "both"):
        register_all_tools(mcp)
    if backend in ("kicad", "both"):
        register_kicad_tools(mcp)
        register_kicad_board_tools(mcp)
        register_kicad_sch_tools(mcp)
        register_kicad_lib_tools(mcp)
    # The engineering calculators (pcb_calc_*) are pure physics and
    # EDA-independent. Altium's register_all_tools already defines them, so add
    # them only for a KiCad-only server to reach calculator parity without
    # double-registering under "both".
    if backend == "easyeda":
        register_easyeda_tools(mcp)
    if backend in ("easyeda", "kicad"):
        # The plan-building half of the design family. EasyEDA draws the
        # plan with its own template tools, KiCad with kicad_generate_*.
        register_offline_design_tools(mcp)
    if backend in ("kicad", "easyeda"):
        register_calc_tools(mcp)
        register_plan_tools(mcp)
        register_offline_review_tools(mcp)
    # The EDA-agnostic main-flow tools (review_design, get_board_info,
    # list_components, list_nets) work on whichever backend is active, so they
    # register for every backend.
    register_eda_tools(mcp)
    # tool_catalog / tool_invoke describe the tool surface itself rather than
    # any one EDA tool, so they belong on every backend too. They also have to
    # exist for the minimal toolset to be possible at all.
    register_meta_tools(mcp)
    # Updating eda-agent itself belongs to no EDA tool either.
    register_update_tools(mcp)
    # tool_guide answers which tool for which DOCUMENT, and what is proven
    # impossible. That is a different question from tool_catalog's "what is
    # this called", and it is the one that has been getting wrong answers,
    # so it registers everywhere the catalogue does.
    register_guidance_tools(mcp)
    # Part sourcing is EDA-agnostic: the providers answer about parts, not
    # about Altium or KiCad, so both backends get them.
    register_parts_tools(mcp)
    # The live layout view draws the engine's board model and the decision
    # log, neither of which belongs to one EDA tool, so every backend gets it.
    register_live_tools(mcp)
    # Datasheet fetch and the package checks work on the package, not on an
    # editor, so every backend gets them.
    register_footprint_tools(mcp)
    # Calculators with no Altium twin: register_calc_tools stays off the
    # Altium backend because pcb.py defines its names there, which would
    # leave these missing from the default backend.
    register_physics_calc_tools(mcp)
    return backend


__all__ = [
    "register_all_tools",
    "register_backend",
    "register_easyeda_tools",
    "register_kicad_tools",
    "register_kicad_board_tools",
    "register_kicad_sch_tools",
    "register_kicad_lib_tools",
    "register_eda_tools",
    "register_calc_tools",
    "register_physics_calc_tools",
    "register_plan_tools",
    "register_offline_design_tools",
    "OFFLINE_DESIGN_TOOLS",
    "register_offline_review_tools",
    "OFFLINE_REVIEW_TOOLS",
    "BACKENDS",
    "DEFAULT_BACKEND",
    "TOOLSETS",
    "DEFAULT_TOOLSET",
    "register_application_tools",
    "register_guidance_tools",
    "register_uiauto_tools",
    "register_meta_tools",
    "register_update_tools",
    "register_parts_tools",
    "register_live_tools",
    "register_footprint_tools",
    "register_convention_tools",
    "register_altium365_tools",
    "register_request_prompts",
    "register_project_tools",
    "register_library_tools",
    "register_ipc_batch_tools",
    "register_generic_tools",
    "register_pcb_tools",
    "register_review_tools",
    "register_sim_tools",
    "register_design_tools",
    "register_render_tools",
    "register_audit_tools",
    "register_route_tools",
    "register_sch_template_tools",
    "register_kicad_tools",
]
