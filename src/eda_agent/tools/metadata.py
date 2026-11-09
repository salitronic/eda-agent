# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Out-of-band metadata for the MCP tool surface.

FastMCP (mcp 1.5.x, pinned <2) has no ``tags``/``annotations`` field on its
tool model, so tool metadata cannot ride on the ``@mcp.tool()`` decorator.
This module keeps a decorator-independent registry instead, keyed by tool
name, that other layers consume:

- the future discovery layer (a ``tool_catalog`` meta-tool) filters by
  ``category`` / ``interaction`` so a client need not load 345 schemas;
- the docs generator groups by ``category`` and badges by ``maturity``;
- a CI drift test asserts every registered tool resolves here and that no
  override names a tool that no longer exists.

Three axes:

``category`` -- derived from the name prefix; 100% mechanical.

``maturity`` -- how well the tool is verified.
    ``offline``    pure Python, deterministic, fully covered by CI unit
                   tests; never touches Altium.
    ``simulator``  bridge-backed, and EVERY command it sends is
                   implemented by the in-repo Python Altium simulator, so
                   the whole call can be driven in CI with no Altium.
    ``live_only``  bridge-backed, and at least one command it sends has
                   no simulator handler; verifiable only against a real
                   Altium session.
  ``simulator`` is measured against the simulator itself rather than
  estimated, and a guard test fails if the two disagree. Read it as
  "exercisable without Altium", not as "a test exercises it today":
  those are different claims and only the first is enforced here.
  The Phase 1.3 nightly live-run is the intended source of a future
  promotion to a ``verified`` tier; when that lands, record per-tool
  results in ``MATURITY_OVERRIDES``.

``interaction`` -- what running the tool does to the Altium session.
    ``readonly``   queries / reads; no mutation.
    ``silent``     mutates, no dialog -- the loop stays responsive.
    ``modal``      pops a non-suppressible Altium dialog that BLOCKS the
                   polling loop until it is answered; answer it yourself
                   with app_press_dialog_button (see the bridge audit).
    ``partial``    succeeds but leaves the job incomplete; a follow-up tool
                   is required to finish.
"""

from __future__ import annotations

# --- maturity tiers ---------------------------------------------------------
OFFLINE = "offline"
SIMULATOR = "simulator"
LIVE_ONLY = "live_only"
MATURITIES = (OFFLINE, SIMULATOR, LIVE_ONLY)

# --- interaction classes ----------------------------------------------------
READONLY = "readonly"
SILENT = "silent"
MODAL = "modal"
PARTIAL = "partial"
INTERACTIONS = (READONLY, SILENT, MODAL, PARTIAL)

# --- category derivation ----------------------------------------------------
# Ordered longest-prefix-first so ``application`` wins over ``app_`` etc.
_CATEGORY_BY_PREFIX = (
    ("app_", "application"),
    ("proj_", "project"),
    ("lib_", "library"),
    ("obj_", "generic"),
    ("sch_", "schematic"),
    ("pcb_", "pcb"),
    ("audit_", "audit"),
    ("design_", "design"),
    ("sim_", "simulation"),
    ("route_", "routing"),
    ("tool_", "meta"),
    ("part_", "parts"),
    ("kicad_", "kicad"),
    # Before "easy" would ever be reached by a shorter prefix. Order
    # matters here: the first match wins, so a new prefix that is a
    # prefix of an existing one has to go above it.
    ("easyeda_", "easyeda"),
    # Package checks and renders work on a footprint before it exists in any
    # library; filed with the library tools that draw it.
    ("footprint_", "library"),
    # A datasheet is a part's document, fetched like part_fetch fetches a
    # part, and with no editor involved.
    ("datasheet_", "parts"),
    # Altium 365 through its own API or Nexar; no editor involved.
    ("altium365_", "altium365"),
)

# Tools every one of whose bridge commands the in-repo Altium simulator
# implements, so the whole call can be driven in CI with no Altium.
#
# This list is GROUND TRUTH, not an estimate, and is not maintained by
# hand: tests/test_simulator_maturity_is_measured.py probes the real
# simulator for every tool and fails unless this set matches EXACTLY, in
# both directions. Adding a simulator handler is what promotes a tool;
# the test tells you the new list.
#
# It replaced a per-CATEGORY guess (application / project / library /
# generic / schematic were assumed covered). That proxy was wrong on
# more than half the bridge-backed surface: 131 tools claimed coverage
# for commands the simulator answers with UNKNOWN_ACTION, and 18 it
# fully answers were published as live_only. A category is not a
# handler, and a tool is only covered when EVERY command it sends is,
# because one unanswered call ends the run just as surely.
_SIMULATOR_TOOLS = frozenset({
    "app_checkpoint", "app_context", "app_detach", "app_get_active_document",
    "app_get_version",
    "app_list_documents", "app_set_active_document",
    "audit_find_missing_decoupling",
    "audit_find_pin_net_name_mismatches",
    "audit_find_unconnected_ic_pins", "design_lint_report",
    "lib_add_footprint_arc", "lib_add_footprint_pad",
    "lib_add_footprint_pads", "lib_add_footprint_track",
    "lib_add_footprint_tracks", "lib_add_pins",
    "lib_add_symbol_polygon", "lib_add_symbol_rectangle", "lib_add_symbol_text",
    "lib_audit_footprint_vs_datasheet", "lib_batch_rename", "lib_batch_set_params", "lib_create_3d_model",
    "lib_create_footprint",
    "lib_create_ic_symbol",
    "lib_create_standard_footprint",
    "lib_create_symbol", "lib_delete_footprint_primitives", "lib_diff_libraries",
    "lib_get_component_details", "lib_get_components", "lib_get_pad_geometry",
    "lib_get_pin_list",
    "lib_link_3d_model", "lib_link_footprint",
    "obj_create", "obj_delete", "obj_deselect_all", "obj_get_font_id",
    "obj_get_font_spec", "obj_modify", "obj_query", "obj_run_process",
    "obj_select", "obj_zoom", "pcb_bind_pad_nets",
    "pcb_build_from_project", "pcb_create_nets_from_list",
    "pcb_get_board_outline", "pcb_get_board_statistics",
    "pcb_get_components", "pcb_get_nets", "pcb_get_unrouted_nets",
    "pcb_get_vias", "pcb_move_components", "pcb_place_components",
    "pcb_place_tracks", "pcb_place_via", "pcb_run_drc",
    "proj_add_document", "proj_annotate", "proj_close", "proj_compile",
    "proj_create", "proj_cross_probe", "proj_export_bom_html",
    "proj_export_netlist", "proj_export_pdf", "proj_get_board_info",
    "proj_get_bom", "proj_get_component_info", "proj_get_focused",
    "proj_get_nets", "proj_get_parameters", "proj_get_stats",
    "proj_list_documents", "proj_list_open", "proj_list_variants", "proj_open",
    "proj_remove_document", "proj_run_output", "proj_save",
    "proj_set_parameter", "proj_update_from_libraries",
    "sch_place_comment", "sch_tidy_port_references",
})
# "parts" queries part providers (registries, local KiCad libraries)
# and never touches the Altium bridge, so it works with no Altium
# running, which is what "offline" means here.
_OFFLINE_CATEGORIES = frozenset({"routing", "meta", "parts"})
# pcb / audit / simulation -> live_only; design is mixed (mostly offline,
# handled by the offline classifier below).

# Tools that are pure Python regardless of their prefix (deterministic, no
# bridge round-trip). Substring match keeps the calculators in one rule.
_OFFLINE_SUBSTRINGS = ("_calc_", "calc_", "_compute_", "compute_")

# Pure Python tools filed under a category that is otherwise bridge-backed:
# the package checks compute over a package description and never reach an
# editor, so they work with nothing running.
_OFFLINE_BY_NAME = frozenset({"footprint_check_package",
                              "footprint_render_package"})

# ...except calculators that read their input FROM THE BOARD. The
# substring rule above assumes a calc tool is pure maths; these query
# Altium first, so reporting them as offline sends anyone filtering for
# "works without Altium" straight into a bridge error.
_CALC_NEEDS_BRIDGE = frozenset({"pcb_calc_polygon_area"})

# design_* tools that are pure Python (per the design-agent surface: plan
# authoring, analysis, and derivation all run offline). The bridge-backed
# design tools are the explicit exceptions in _DESIGN_BRIDGE below.
_DESIGN_BRIDGE = frozenset(
    {
        "design_audit_schematic",
        "design_validate",
        "design_review_snapshot",
        "design_snapshot_inventory",
        "design_datasheet_checklist",
        # Both fetch board state over the bridge before reporting.
        "design_lint_report",
        "design_visual_review",
        # Reads the live sheet over the bridge. It CATCHES the failure
        # and answers anyway, so with no Altium running it returns a
        # degraded result rather than an error, and calling it offline
        # is what sends somebody to rely on that result.
        "design_plan_from_sheet",
    }
)

# --- explicit interaction overrides (from the DelphiScript bridge audit) ----
# Only tools that are NOT plain readonly/silent under the default rules.
INTERACTION_OVERRIDES = {
    # Who edits which document: reading it, or handing one over, writes
    # nothing to a design.
    "app_document_owners": READONLY,
    "app_claim_document": READONLY,
    # Reads the rules, or keeps an approved copy beside the board: nothing
    # in the design changes.
    "pcb_drc_rules": READONLY,
    # Non-suppressible modal dialogs that block the single-threaded loop.
    "proj_sync_pcb": MODAL,          # ECO / Update-PCB dialog
    "proj_sync_schematic": MODAL,    # Update-Schematic dialog
    "pcb_add_teardrops": MODAL,      # Teardrop dialog
    "pcb_remove_teardrops": MODAL,   # Teardrop dialog
    # The wizard and its ECO, which block the loop for as long as they
    # are up. This tool is the one that clicks them rather than a human,
    # but that does not make it silent: while it runs, the bridge cannot
    # answer, and anyone filtering for operations that keep the session
    # responsive must not be handed this one.
    "app_update_from_libraries": MODAL,
    # Altium's Confirm box for the netlist update blocks the loop until it
    # is answered; the tool presses Yes itself, as above.
    "pcb_update_free_primitive_nets": MODAL,
    # Pulls new code into the installation and can restart the Altium
    # loop. It is filed under meta, which counts as offline, and offline
    # would otherwise read as readonly.
    "server_update": SILENT,
    # MEASURED MODAL during the live sweep of 2026-08-24. Each of these was
    # published as silent, so anything filtering for "keeps the session
    # responsive" was handed a tool that blocks the bridge until a human
    # answers a dialog. The dialog seen is named beside each one.
    # pcb_run_drc was here: PCB:DesignRuleCheck opens the Design Rule
    # Checker (TDesignRuleCheckForm), measured behind it a 30-minute dead
    # loop. It now runs IPCB_Board.RunBatchDesignRuleCheck by default,
    # MEASURED 2026-10-23 on AD 26.11: 0.45 s, no dialog, report written,
    # so it is silent; allow_modal=True still takes the dialog route.
    "pcb_run_drc": SILENT,
    "proj_export_pdf": MODAL,         # Preview PCB / print preview
    "proj_run_output": MODAL,         # Altium's exporter dialogs
    # These three raise "Unsaved Changes" whenever the target is dirty, and
    # that one is a WPF dialog whose buttons expose no window handles, so
    # app_press_dialog_button cannot answer it. Keyboard only.
    "lib_reload_library": MODAL,
    "proj_remove_document": MODAL,
    "proj_close": MODAL,
    # Succeeds but leaves the job incomplete.
    "pcb_place_components": PARTIAL,  # geometry only; needs pcb_build_from_project for nets
    # "diff" homograph: _READONLY_SUBSTRINGS carries "_diff_" for
    # COMPARISON tools (lib_diff_libraries, proj_get_differences), but
    # here it means DIFFERENTIAL. This one creates an object on the
    # board, and reporting it readonly tells anyone filtering for safe
    # operations the opposite of the truth.
    "pcb_create_diff_pair": SILENT,
    # Launches Altium's INTERACTIVE polygon mode: the user draws the
    # boundary by clicking. "silent" claimed it had completed a mutation,
    # so an agent would believe a polygon exists when none does.
    "pcb_start_polygon_placement": PARTIAL,
    # Renders the design and hands back an image; it changes nothing.
    # This one read as readonly only because it was WRONGLY classified
    # offline, and the offline->readonly shortcut carried it. Correcting
    # the maturity removed that shortcut and dropped it to "silent"
    # (mutates). State the truth explicitly rather than depending on a
    # side effect of another rule.
    "design_visual_review": READONLY,
    # Reports a pin's root, its electrical connection point, and every
    # object sitting on each. Pure inspection -- it exists precisely so a
    # caller can debug connectivity WITHOUT touching the sheet. The obj_
    # prefix defaults to "silent" (mutating), which is the opposite of
    # the truth here, so say so explicitly.
    "obj_explain_pin": READONLY,
    # Reads the board (or a saved board model) and measures it: overlaps,
    # gaps, connectivity, corners, return vias, plane islands. Nothing is
    # written. The pcb_ prefix defaults to silent, which would tell anyone
    # filtering for safe operations that an audit edits the board.
    "pcb_layout_audit": READONLY,
    # Measures two objects and asks the clearance rule on the pair; writes
    # nothing. The pcb_ prefix alone would call it mutating.
    "pcb_measure_clearance": READONLY,
    # Reads the Layer Stack Manager's own data for the board (layers with
    # their properties, drill pairs, spans, profiles, optionally its model)
    # and writes nothing; the pcb_ prefix alone would call it silent.
    "pcb_layer_stack_manager": READONLY,
    # Reads a database library's declared tables and fields. The lib_ prefix
    # does not make that clear to the classifier; it writes nothing.
    "lib_dblib_info": READONLY,
    # part_fetch writes library files when given download_dir. The
    # "parts" category is offline, and offline falls back to READONLY,
    # which would advertise a tool that touches the filesystem as
    # read-only. Every other file-writing tool here (lib_easyeda_import,
    # lib_extract_cse_zip, proj_export_pdf, pcb_render_svg) is SILENT,
    # so match them. part_search never writes and stays readonly.
    "part_fetch": SILENT,
    # Writes the downloaded PDF and its sidecar into the workspace cache.
    # Filed under parts, which is offline and so falls back to READONLY;
    # a tool that writes files is SILENT here, as part_fetch is.
    "datasheet_fetch": SILENT,
    # Writes the SVG (and a PNG) it draws, as pcb_render_svg does.
    "footprint_render_package": SILENT,
    # Writes the .cir / .net deck it builds into the workspace.
    "design_export_spice_deck": SILENT,
    # Reads the footprint's pads, primitives and the library's layers and
    # writes nothing. The lib_ prefix says nothing about that, and the
    # default would publish an audit as a tool that edits the library.
    "lib_audit_footprint_vs_package": READONLY,
    # Read Altium 365 or the user's policy files, or read a library and
    # report; none of them changes anything. The Altium 365 changes
    # (publish, release, set parameters) keep the SILENT default.
    "altium365_check_setup": READONLY, "altium365_list_workspaces": READONLY,
    "altium365_list_projects": READONLY, "altium365_get_project": READONLY,
    "altium365_list_library_folders": READONLY, "altium365_list_components": READONLY,
    "altium365_get_component": READONLY, "altium365_read_object": READONLY,
    "altium365_describe_type": READONLY, "altium365_run_query": READONLY,
    "footprint_read_ipc_name": READONLY, "lib_verify_ipc_names": READONLY,
    "lib_check_naming": READONLY, "lib_make_name": READONLY,
    "lib_read_pin_table": READONLY, "design_check_company_rules": READONLY,
    "design_read_rules_export": READONLY, "design_phase_gate": READONLY,
    "design_phase_standing": READONLY,
    # Appends to the product's ledger of stage answers in the workspace.
    "design_phase_answer": SILENT,
    # Writes the deck and its results into a run folder in the workspace.
    "design_simulate_deck": SILENT,
}

# --- explicit maturity overrides -------------------------------------------
# Populate from the Phase 1.3 nightly live-run as tools become verified.
MATURITY_OVERRIDES: dict[str, str] = {
    # Empty on purpose, and it should stay that way for SIMULATOR: that
    # tier is now derived from _SIMULATOR_TOOLS, which is measured
    # against the simulator and guarded by a test. Pinning a tool here
    # would let a claim outlive the handler that justified it, which is
    # the failure this file just came out of.
    #
    # Reserved for the Phase 1.3 nightly live-run, whose per-tool results
    # ARE external evidence and cannot be derived from the source.
}

# Readonly name markers. A tool whose name starts with / contains one of
# these does not mutate the design.
_READONLY_PREFIXES = ("get_", "list_", "find_", "query_", "audit_")
_READONLY_SUBSTRINGS = (
    "_get_", "_list_", "_find_", "_query_", "_describe_", "_preview_",
    "_review_", "_validate", "_compare_", "_calc_", "_compute_", "_snapshot",
    "_checklist", "_readiness", "_stats", "_search", "_diff_", "_report",
)


# The EDA-agnostic main-flow tools carry no prefix, so prefix derivation
# alone dropped them into "other" -- and "other" is not one of the
# categories tool_catalog documents. That made the product's own headline
# entry points invisible to a client discovering by category, which bites
# hardest under the minimal toolset where the catalog IS the interface.
_CATEGORY_BY_NAME = {
    "review_design": "core",
    "get_board_info": "core",
    "list_components": "core",
    "list_nets": "core",
    "run_drc": "core",
    "run_erc": "core",
    # Updates eda-agent itself, on every backend: about the tool surface,
    # like tool_catalog, rather than about any one EDA tool.
    "server_update": "meta",
    # The family tool for the audits; its members keep the audit_ prefix.
    "audit": "audit",
}


#: The EasyEDA tools all share one prefix, so the prefix table would
#: file every one of them under a single "easyeda" heading. That is not
#: cosmetic: ``tool_catalog`` filters BY category, and it is the way a
#: client with a tool-count limit finds anything at all. One bucket of
#: 128 is the same as no index, while Altium's surface is browsable in
#: thirteen.
#:
#: The subject is already encoded in the command each tool sends, so it
#: is read from there rather than restated as a table that would drift.
_EASYEDA_NAMESPACE_CATEGORY = {
    "pcb": "pcb",
    "sch": "schematic",
    "lib": "library",
    "proj": "project",
    # Altium files its exports under project (proj_export_*), so the
    # same heading keeps one habit across backends.
    "export": "project",
    "design": "design",
    "system": "application",
    "sys": "application",
    "editor": "application",
    "dmt": "application",
}

#: EasyEDA tools whose category the command namespace gets wrong, or
#: which send no command at all. Each one is a judgement, so each is
#: written down rather than inferred.
#:
#: Tools NAMED `easyeda_audit_*` are not listed here. They were, all of
#: them, which made the name the real rule and the list a hand-kept copy
#: of it: a new audit read as a pcb tool until someone remembered to add
#: a line. The prefix is applied directly below instead.
_EASYEDA_CATEGORY_OVERRIDE = {
    # Cross-checks that do NOT carry the audit prefix. They read through
    # pcb/sch commands, but what they are FOR is finding defects, which
    # is what audit means here.
    "easyeda_compare_schematic_pcb": "audit",
    "easyeda_get_unconnected_pins": "audit",
    "easyeda_get_unrouted_nets": "audit",
    # Sends no command of its own: it runs every audit and ranks what
    # they found, so the deriver has no literal command to read. Filed
    # under audit, which is where someone looking for "run the checks"
    # will look for it.
    "easyeda_review_board": "audit",
    # Same shape, opposite job: it fetches the design DATA a review is
    # judged from rather than running the checks, so the deriver has no
    # single command to read here either. Filed under design, because
    # what it returns is the design, not a verdict on it.
    "easyeda_review_snapshot": "design",
    # Sends no command because there is nothing to send: EasyEDA has no
    # project-delete API at all, so the tool exists only to explain that
    # and point at what CAN be deleted. The deriver reads the command
    # namespace, and a tool that never calls one has nothing to read.
    # Still filed under project, since that is where someone looking for
    # it will look, and finding the explanation is the point.
    "easyeda_delete_project": "project",
    # Reads design.snapshot and renders a page, so the deriver files it
    # under design, and design is an OFFLINE category. Both halves of
    # that are wrong here and the second one is dangerous: offline falls
    # back to READONLY, and the sweep calls readonly tools with their
    # defaults. This one defaults to writing bom.html into the
    # workspace, so it would overwrite a file nobody asked it to touch
    # while claiming to be read-only.
    #
    # Same trap part_fetch fell into, recorded a few lines up. Filed
    # under project with the other exports, including its own Altium
    # twin proj_export_bom_html, which is live_only and silent.
    "easyeda_export_bom_html": "project",
    # Sends no command of its own: it runs the fabrication exports in
    # turn and judges whether the result is sendable, so the deriver
    # has no single literal command to read. Filed with its Altium
    # twin, proj_generate_fab_package.
    "easyeda_generate_fab_package": "project",
    # Searches both documents, so the command it sends is chosen at run
    # time. The deriver below reads a LITERAL first argument, which a
    # computed one does not offer, and a tool with no derivable category
    # falls back to the flat one. Filed where the Altium equivalent
    # lives, proj_find_component.
    "easyeda_find_component": "project",
    # Sends nothing directly: it delegates to create_symbol, add_pins
    # and add_rectangle, whose categories disagree with each other
    # anyway (library, then schematic twice). What it MAKES is a library
    # part, which is the heading a reader would look under.
    #
    # Any tool built out of other tools lands here for the same reason,
    # and the guard in tests/test_tool_metadata.py names the offender
    # rather than letting it fall back quietly.
    "easyeda_create_ic_symbol": "library",
    "easyeda_create_passive_symbol": "library",
    "easyeda_create_standard_footprint": "library",
    # Delegates the drawing like the one above; its only command of its
    # own is the pad readback, a pcb read the deriver would file it under.
    "easyeda_create_footprint_from_package": "library",
    # Bundles five reads through a helper that takes the command as an
    # argument, so there is no literal for the deriver to see. Every
    # one of those reads is a pcb command.
    "easyeda_get_board_statistics": "pcb",
    # Reads the local measurement record, never the editor, so there is
    # no command to derive from. Filed under application beside the
    # other record-reading tool, list_checkpoints.
    "easyeda_get_measured_shapes": "application",
    # Pure computation over a plan; they send nothing.
    "easyeda_emit_template": "design",
    "easyeda_emit_template_connections": "design",
    # Sends nothing directly either: it dispatches to whichever tools an
    # emitted plan names, so there is no command namespace to read.
    "easyeda_run_plan": "design",
    # Reads the local checkpoint store and never asks the editor, so
    # there is no command to derive from. Filed where the Altium
    # equivalent lives, app_list_checkpoints.
    "easyeda_list_checkpoints": "application",
}

_easyeda_categories: dict[str, str] | None = None


def _easyeda_category(name: str) -> "str | None":
    """Category for one EasyEDA tool, from the command it sends.

    Built once, by reading the tool module, so adding a tool files it
    correctly with no list to update. A tool that sends nothing and has
    no override returns None and falls back to the prefix.
    """
    global _easyeda_categories
    if _easyeda_categories is None:
        import ast
        import pathlib

        _easyeda_categories = {}
        source = (pathlib.Path(__file__).with_name("easyeda.py")
                  .read_text(encoding="utf-8"))
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("easyeda_")):
                continue
            for inner in ast.walk(node):
                if not (isinstance(inner, ast.Call)
                        and getattr(inner.func, "id", "") == "_call"):
                    continue
                if not inner.args or not isinstance(
                        inner.args[0], ast.Constant):
                    continue
                namespace = str(inner.args[0].value).split(".", 1)[0]
                category = _EASYEDA_NAMESPACE_CATEGORY.get(namespace)
                if category:
                    _easyeda_categories.setdefault(node.name, category)
                break
    if name.startswith("easyeda_audit_"):
        # Ahead of the namespace, which would file these under whatever
        # they happen to read: an audit of pads is not a pad tool.
        return "audit"
    return (_EASYEDA_CATEGORY_OVERRIDE.get(name)
            or _easyeda_categories.get(name))


def category_of(name: str) -> str:
    """Tool category from an explicit name, else the prefix.

    ``other`` only when neither matches, which a test treats as a defect
    rather than an acceptable default.
    """
    explicit = _CATEGORY_BY_NAME.get(name)
    if explicit:
        return explicit
    if name.startswith("easyeda_"):
        derived = _easyeda_category(name)
        if derived:
            return derived
    for prefix, cat in _CATEGORY_BY_PREFIX:
        if name.startswith(prefix):
            return cat
    return "other"


def _is_offline(name: str, category: str) -> bool:
    if category in _OFFLINE_CATEGORIES:
        return True
    if name in _OFFLINE_BY_NAME:
        return True
    if name in _CALC_NEEDS_BRIDGE:
        return False
    if any(s in name for s in _OFFLINE_SUBSTRINGS):
        return True
    if category == "design" and name not in _DESIGN_BRIDGE:
        return True
    return False


def maturity_of(name: str, category: str | None = None) -> str:
    if name in MATURITY_OVERRIDES:
        return MATURITY_OVERRIDES[name]
    category = category or category_of(name)
    if _is_offline(name, category):
        return OFFLINE
    if name in _SIMULATOR_TOOLS:
        return SIMULATOR
    return LIVE_ONLY


def interaction_of(name: str, category: str | None = None) -> str:
    if name in INTERACTION_OVERRIDES:
        return INTERACTION_OVERRIDES[name]
    category = category or category_of(name)
    if category == "audit":
        return READONLY
    if name.startswith(_READONLY_PREFIXES):
        return READONLY
    if any(s in name for s in _READONLY_SUBSTRINGS):
        return READONLY
    # Offline calculators/authoring tools don't touch Altium at all -> treat
    # as readonly with respect to the live session.
    if maturity_of(name, category) == OFFLINE:
        return READONLY
    return SILENT


def _fold_metadata(name: str) -> "dict[str, str] | None":
    """A family tool takes its members' category and their most demanding
    maturity and interaction, since any kind may be the one called."""
    from .folds import FOLDS, built_members

    if name not in FOLDS:
        return None
    members = list(built_members(name).values()) or list(FOLDS[name].members.values())
    if not members:
        return None
    records = [tool_metadata(m) for m in members]
    maturity_rank = {OFFLINE: 0, SIMULATOR: 1, LIVE_ONLY: 2}
    interaction_rank = {READONLY: 0, SILENT: 1, PARTIAL: 2, MODAL: 3}
    return {
        "name": name,
        "category": records[0]["category"],
        "maturity": max((r["maturity"] for r in records), key=maturity_rank.get),
        "interaction": max((r["interaction"] for r in records),
                           key=interaction_rank.get),
    }


def tool_metadata(name: str) -> dict[str, str]:
    """Full metadata record for one tool name."""
    folded = _fold_metadata(name)
    if folded:
        return folded
    category = category_of(name)
    return {
        "name": name,
        "category": category,
        "maturity": maturity_of(name, category),
        "interaction": interaction_of(name, category),
    }


def catalog(names) -> list[dict[str, str]]:
    """Metadata for many tools, sorted by (category, name)."""
    records = [tool_metadata(n) for n in names]
    records.sort(key=lambda r: (r["category"], r["name"]))
    return records
