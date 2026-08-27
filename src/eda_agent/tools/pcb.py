# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""PCB-specific tools for Altium Designer MCP Server.

Provides high-level PCB operations: net classes, design rules, DRC,
component placement, trace lengths, layer stackup, board outline, etc.
"""

import json
from typing import Any, Optional, Union

from pydantic import ValidationError

from ..bridge import get_bridge
from ..placement import (
    BoardRegion,
    PlaceComp,
    PlaceNet,
    PlaceOptions,
    PlacePin,
    hpwl,
    overlap_pair_count,
    plan_placement,
    rotate_offset,
)
from .bulk_hints import BulkHintTracker
from .datasheet_hints import tag_response
from ..bridge.payload import payload_safe


def _build_objective_report(
    place_comps: list,
    positions: dict[str, tuple],
    rotations: dict[str, float],
    place_nets: list,
    region_obj,
    grid_mils: float,
    clearance_mils: float,
) -> dict[str, Any]:
    """Score a proposed placement on the full multi-term objective.

    Pure Python; reuses the scorer in ``design.pcb_placement`` so both
    auto-placement cores report the same un-weighted term breakdown
    (wirelength, layer-change proxy, congestion, overlap, board-edge,
    decoupling proximity, connector banding, thermal) plus the weighted
    total, a legality flag, and the achieved utilization. Components all
    sit on the top side here, so the via term is structurally zero.
    """
    from ..design import pcb_placement as _construct

    rules = _construct.DesignRules(
        grid=float(grid_mils),
        component_clr=float(clearance_mils),
    )
    pos = {r: [float(c[0]), float(c[1])] for r, c in positions.items()}
    rot = {c.ref: float(rotations.get(c.ref, c.rotation)) for c in place_comps}
    sides = {c.ref: 1 for c in place_comps}
    report = _construct.score(
        place_comps, pos, rot, sides, place_nets, region_obj,
        rules, _construct.ObjectiveWeights(),
    )
    out = report.as_dict()
    out["weighted_total"] = round(report.weighted_total, 3)
    out["legal"] = bool(report.legal)
    out["utilization"] = round(report.utilization, 4)
    for key in list(out):
        if isinstance(out[key], float):
            out[key] = round(out[key], 3)
    return out


def _build_net_length_report(
    place_comps: list,
    positions: dict[str, tuple],
    rotations: dict[str, float],
    place_nets: list,
    critical: set,
    top: int = 8,
) -> dict[str, Any]:
    """Per-net physical bounding span (mils) of the proposed placement.

    Reuses ``design.pcb_placement.net_spans`` so the diagnostic matches the
    same pin geometry the scorer sees. Returns the ``top`` longest nets
    (the routing risk / candidates to mark critical) plus the achieved span
    of every net the caller already flagged critical.
    """
    from ..design import pcb_placement as _construct

    pos = {r: [float(c[0]), float(c[1])] for r, c in positions.items()}
    rot = {c.ref: float(rotations.get(c.ref, c.rotation)) for c in place_comps}
    sides = {c.ref: 1 for c in place_comps}
    spans = _construct.net_spans(place_comps, pos, rot, sides, place_nets)
    ordered = sorted(spans.items(), key=lambda kv: (-kv[1], kv[0]))
    report: dict[str, Any] = {
        "longest_nets": [
            {"net": n, "span_mils": round(v, 1)} for n, v in ordered[:top]
        ],
    }
    if critical:
        report["critical_net_spans"] = {
            n: round(spans[n], 1) for n, _ in ordered if n.upper() in critical
        }
    return report


def _build_decoupling_report(
    place_comps: list,
    positions: dict[str, tuple],
    rotations: dict[str, float],
    place_nets: list,
) -> list:
    """Structural decoupling analysis of the proposed placement.

    Reuses ``design.pcb_placement.decoupling_report`` to surface which cap
    decouples which IC (found on the connectivity graph, never by name) and
    the achieved centre-to-power-pin distance in mils, worst first -- so the
    caller can confirm decaps landed tight against their ICs.
    """
    from ..design import pcb_placement as _construct

    pos = {r: [float(c[0]), float(c[1])] for r, c in positions.items()}
    rot = {c.ref: float(rotations.get(c.ref, c.rotation)) for c in place_comps}
    sides = {c.ref: 1 for c in place_comps}
    return _construct.decoupling_report(place_comps, pos, rot, sides, place_nets)


# A net touching more than this many parts is treated as a power/ground rail
# (routed as a plane/pour) and excluded from the SIGNAL ratsnest count, since
# its crossings do not become signal vias.
_RATSNEST_SIGNAL_FANOUT = 4


def _build_ratsnest_report(
    place_comps: list,
    positions: dict[str, tuple],
    rotations: dict[str, float],
    place_nets: list,
) -> dict[str, Any]:
    """Ratsnest crossing counts as a routability / via-pressure indicator.

    ``signal_crossings`` excludes high-fanout rails (planes carry no signal
    via) and is the figure that predicts routing difficulty; ``total_crossings``
    is the conservative all-net count (power assumed routed as traces).
    """
    from ..design import pcb_placement as _construct

    pos = {r: [float(c[0]), float(c[1])] for r, c in positions.items()}
    rot = {c.ref: float(rotations.get(c.ref, c.rotation)) for c in place_comps}
    sides = {c.ref: 1 for c in place_comps}
    return {
        "signal_crossings": _construct.ratsnest_crossings(
            place_comps, pos, rot, sides, place_nets,
            max_fanout=_RATSNEST_SIGNAL_FANOUT),
        "total_crossings": _construct.ratsnest_crossings(
            place_comps, pos, rot, sides, place_nets),
        "signal_fanout_cap": _RATSNEST_SIGNAL_FANOUT,
    }


def _build_placement_summary(
    objective_report: dict[str, Any],
    ratsnest: dict[str, Any],
    decoupling: list,
    suggested_board: Optional[dict[str, Any]],
) -> str:
    """One-line, human/LLM-readable assessment of the placement, synthesising
    the detailed reports so a caller can judge quality at a glance."""
    parts: list[str] = []
    parts.append("legal" if objective_report.get("legal")
                 else "ILLEGAL (courtyard overlap)")
    util = objective_report.get("utilization")
    if util is not None:
        parts.append(f"{round(float(util) * 100)}% board utilization")
    sx = ratsnest.get("signal_crossings")
    if sx is not None:
        tag = "routable" if sx <= 2 else "review routability"
        parts.append(f"{sx} signal-net ratsnest crossing(s) ({tag})")
    if decoupling:
        worst = max(d["distance_mils"] for d in decoupling)
        parts.append(f"{len(decoupling)} decap(s) detected, worst "
                     f"{worst:.0f} mils from its IC power pin")
    if suggested_board:
        parts.append(f"suggested board {suggested_board['width']:.0f}x"
                     f"{suggested_board['height']:.0f} mils")
    return "; ".join(parts) + "."


NETLIST_CSV_HEADER = ("component", "pin", "pin_name", "net")

# The fitted-classes pcb_filter_variant_components can select. Validated
# in Python because the Pascal cannot: PCB_FilterVariantComponents tests
# 'all_fitted', 'fitted_original' and 'alternate' in an If chain whose
# implicit else is not_fitted, so an unrecognised word silently selects
# the not-fitted parts. It then echoes the caller's own string back as
# "select", so the reply CONFIRMS the class that was not selected.
#
# A typo of "alternate" therefore selects the opposite class and says it
# did what was asked. Anything acting on that selection, a delete, a
# component class, a variant review, acts on the wrong components.
FILTER_VARIANT_SELECT = (
    "not_fitted", "fitted_original", "alternate", "all_fitted")


def parse_tabular_netlist(text: str) -> dict[str, Any]:
    """Parse the tabular netlist CSV that ``proj_export_netlist`` writes.

    One ``component,pin,pin_name,net`` row per pin-net node, header row
    first. Pure Python (no Altium round-trip), so the SCH->PCB bridge
    derivation is unit-testable offline.

    Returns ``{"ok": True, "nodes": [(component, pin, pin_name, net), ...],
    "skipped_rows": N}`` -- nodes in file order, rows with a missing
    component/pin/net or a wrong field count counted in ``skipped_rows`` --
    or ``{"ok": False, "reason": ...}`` when the text is empty or the
    header is not the tabular-netlist header.
    """
    import csv
    import io

    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    if not rows:
        return {"ok": False, "reason": "empty netlist text"}
    header = tuple(c.strip().lower() for c in rows[0])
    if header != NETLIST_CSV_HEADER:
        return {
            "ok": False,
            "reason": (
                "not a tabular netlist (expected header "
                f"'{','.join(NETLIST_CSV_HEADER)}', got "
                f"'{','.join(rows[0])}'); export with "
                "proj_export_netlist(net_format='tabular')"
            ),
        }
    nodes: list[tuple[str, str, str, str]] = []
    skipped = 0
    for row in rows[1:]:
        if len(row) != 4:
            skipped += 1
            continue
        comp, pin, pin_name, net = (c.strip() for c in row)
        if not comp or not pin or not net:
            skipped += 1
            continue
        nodes.append((comp, pin, pin_name, net))
    return {"ok": True, "nodes": nodes, "skipped_rows": skipped}


def derive_netlist_build(
    nodes: list[tuple[str, str, str, str]],
) -> dict[str, Any]:
    """Derive the net-creation and pad-binding work lists from netlist nodes.

    Input: ``(component, pin, pin_name, net)`` tuples (the shape
    ``parse_tabular_netlist`` returns). Output dict:

    - ``nets``: distinct net names, sorted (deterministic input for
      ``pcb_create_nets_from_list``).
    - ``bindings``: one ``{"designator", "pin", "net"}`` dict per node,
      grouped by designator (the Pascal handler caches the component
      lookup per consecutive designator run). Duplicate
      (designator, pin) rows collapse to the first occurrence.
    - ``components``: distinct designators, sorted.
    """
    nets: set[str] = set()
    components: set[str] = set()
    seen_pads: set[tuple[str, str]] = set()
    by_comp: dict[str, list[dict[str, str]]] = {}
    for comp, pin, _pin_name, net in nodes:
        nets.add(net)
        components.add(comp)
        if (comp, pin) in seen_pads:
            continue
        seen_pads.add((comp, pin))
        by_comp.setdefault(comp, []).append(
            {"designator": comp, "pin": pin, "net": net}
        )
    bindings = [b for comp in sorted(by_comp) for b in by_comp[comp]]
    return {
        "nets": sorted(nets),
        "bindings": bindings,
        "components": sorted(components),
    }


def _encode_bindings_param(bindings: list[dict[str, Any]]) -> str:
    """Encode binding dicts into the ``~~``-op / ``;``-field wire grammar
    the Pascal ``NextBatchOp``/``GetBatchField`` helpers parse. Entries
    missing designator/pin/net are dropped (the caller reports counts)."""
    ops: list[str] = []
    for b in bindings:
        desig = str(b.get("designator", "")).strip()
        pin = str(b.get("pin", "")).strip()
        net = str(b.get("net", "")).strip()
        if not desig or not pin or not net:
            continue
        ops.append(f"designator={desig};pin={pin};net={net}")
    return "~~".join(ops)


#: The signature defaults of pcb_calc_termination's unit-suffixed
#: arguments, kept here so the alias handling can tell "the caller did
#: not pass this" from "the caller passed exactly the default". Without
#: that distinction a conflicting alias cannot be detected at all, and
#: the choice is between silently preferring one value and refusing
#: every call that supplies a terse spelling.
_TERMINATION_DEFAULTS = {
    "z0_ohms": 50.0,
    "dielectric_constant": 4.2,
    "driver_impedance_ohms": 0.0,
    "dielectric_height_mils": 0.0,
}


def register_pcb_tools(mcp):
    """Register PCB tools with the MCP server."""

    @mcp.tool()
    async def pcb_get_nets() -> dict[str, Any]:
        """Get all unique net names from the active PCB board.

        Returns:
            Dictionary with "nets" array of net name strings and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_nets", {})
        return result

    @mcp.tool()
    async def pcb_focus_board(board_path: str) -> dict[str, Any]:
        """Make a specific PCB the focused/current board.

        When several PcbDocs are open, the other PCB tools
        (`pcb_get_components`, `pcb_delete_object`, `pcb_delete_net`,
        `pcb_plan_placement`, `design_visual_review`, …) operate on the
        *focused* board, and `app_set_active_document` does NOT reliably set
        that for a PcbDoc. Call this first to point them all at the board
        you mean. (`pcb_place_components` already accepts `board_path`
        directly.)

        Args:
            board_path: Absolute path to the .PcbDoc to focus.

        Returns:
            Dict with ``focused`` and the resolved ``board`` file name.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.focus_board", {"board_path": board_path}
        )

    @mcp.tool()
    async def pcb_delete_net(
        nets: Optional[list[str]] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete nets from the active PCB.

        By default removes only EMPTY nets (no connected pads / tracks /
        vias): the cleanup for stray nets left behind after deleting
        components, e.g. nets created by `pcb_place_components`' synced
        mode. A net that still has connections is skipped unless
        ``force=True`` (forcing orphans those pads/tracks, so use it
        deliberately).

        Args:
            nets: Specific net names to delete. Omit (or pass an empty
                list) to sweep ALL empty nets on the board.
            force: Also delete nets that still have connected primitives
                (orphans them). Default False.

        A net name containing a COMMA is refused rather than sent. The
        list rides one comma-separated field and the handler splits it
        with ``Pos(',', ...)``, so such a name would arrive as two, and
        the fragments can match real nets: ``VCC,GND`` becomes ``VCC``
        and ``GND``. With ``force=True`` that deletes nets nobody named
        and orphans their pads and tracks.

        Returns:
            Dict with ``deleted`` (count removed), ``skipped_connected``
            (count), and ``skipped_nets`` (names skipped because still
            connected). Or ``{"ok": False, "reason": ...}`` if a name
            contains a comma, in which case nothing is sent.
        """
        wanted = [str(n).strip() for n in (nets or []) if str(n).strip()]
        unsendable = [n for n in wanted if "," in n]
        if unsendable:
            return {
                "ok": False,
                "reason": (
                    f"these net names contain a comma and cannot be sent: "
                    f"{unsendable}. The list travels as one "
                    "comma-separated field, so each would arrive as two "
                    "names, and a fragment can match a real net: "
                    "'VCC,GND' becomes 'VCC' and 'GND'. Delete them "
                    "individually through the Altium UI."
                ),
            }
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.delete_nets",
            {
                "nets": ",".join(payload_safe(n) for n in wanted),
                "force": "true" if force else "false",
            },
        )

    @mcp.tool()
    async def pcb_get_net_classes() -> dict[str, Any]:
        """Get all net classes from the active PCB.

        Only returns class metadata, IPCB_ObjectClass.MemberCount and
        MemberName[] are not exposed in Altium's DelphiScript host, so
        per-member enumeration has to be done by iterating eNetObject and
        grouping by each net's parent class.

        Returns:
            Dictionary with "net_classes" array (each with name, super_class)
            and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_net_classes", {})
        return result

    @mcp.tool()
    async def pcb_create_net_class(
        name: str,
        nets: str,
    ) -> dict[str, Any]:
        """Create a net class (or add nets to an existing one) on the active PCB.

        Args:
            name: Name for the net class (e.g., "PowerNets", "HighSpeed")
            nets: Comma-separated list of net names to add
                  (e.g., "VCC,GND,3V3")

        Returns:
            Dictionary with class_name, class_created (bool), nets_added count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.create_net_class",
            {"name": name, "nets": nets},
        )
        return result

    @mcp.tool()
    async def pcb_create_nets_from_list(nets: list[str]) -> dict[str, Any]:
        """Create net objects on the active PCB for names not already there.

        First leg of the netlist-driven SCH->PCB bridge (Altium's ECO is
        not scriptable): place footprints with `pcb_place_components`,
        create the nets here, bind pads with `pcb_bind_pad_nets`, then
        verify with `proj_compare_sch_pcb`. The whole list is one IPC
        round-trip; names already on the board are counted, not duplicated.

        Args:
            nets: Net names to ensure exist (e.g. from the compiled
                netlist). Duplicates and empty strings are ignored.

        Returns:
            Dict with ``created`` and ``existing`` counts.
        """
        names: list[str] = []
        seen: set[str] = set()
        for n in nets:
            s = str(n).strip()
            if s and s not in seen:
                seen.add(s)
                names.append(s)
        if not names:
            return {"error": "No valid net names", "created": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.create_nets_from_list", {"nets": "|".join(names)}
        )

    @mcp.tool()
    async def pcb_bind_pad_nets(bindings: list[dict[str, Any]]) -> dict[str, Any]:
        """Assign component pads to existing board nets, batch in ONE call.

        Second leg of the netlist-driven SCH->PCB bridge: after
        `pcb_create_nets_from_list`, feed every (designator, pin, net)
        row of the compiled netlist here. Each binding finds the placed
        component, matches the pad by name, and sets its net. Nets must
        already exist on the board -- a binding to a missing net fails
        (it does not create the net).

        Bindings are processed in order; keep rows for the same
        designator adjacent (the handler caches the component lookup per
        consecutive run -- `pcb_build_from_project` orders them this way
        automatically).

        Args:
            bindings: List of dicts, each with ``designator`` (e.g. "U1"),
                ``pin`` (pad name, e.g. "3" or "A1"), ``net``.

        Returns:
            Dict with ``bound`` / ``failed`` counts plus
            ``missing_components`` / ``missing_pads`` / ``missing_nets``
            name lists (each capped at 50 entries).
        """
        encoded = _encode_bindings_param(bindings)
        if not encoded:
            return {"error": "No valid bindings (need designator + pin "
                    "+ net)", "bound": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.bind_pad_nets", {"bindings": encoded}
        )

    @mcp.tool()
    async def pcb_build_from_project(
        netlist_csv_path: str = "",
    ) -> dict[str, Any]:
        """Build the PCB's net set and pad connectivity from the netlist.

        The SCH->PCB bridge orchestrator (replaces the unscriptable ECO
        netlist transfer): derives the distinct net list and every
        (designator, pin, net) binding from the compiled netlist, creates
        the missing nets (`pcb_create_nets_from_list`) and binds every
        pad (`pcb_bind_pad_nets`) in two IPC round-trips.

        It does NOT place footprints -- `pcb_place_components` exists for
        that. Intended sequence: place components, then run this to build
        nets and bind pads, then verify with `proj_compare_sch_pcb`.

        Args:
            netlist_csv_path: Optional path to a tabular netlist CSV
                written by ``proj_export_netlist(net_format="tabular")``
                (``component,pin,pin_name,net`` rows). Empty (default) =
                pull the compiled netlist from the open project directly.

        Returns:
            Dict with ``components`` (distinct designators in the
            netlist), ``nets_created``, ``nets_existing``, ``pads_bound``,
            ``failed``, plus the ``missing_components`` /
            ``missing_pads`` / ``missing_nets`` lists from the bind step.
        """
        from pathlib import Path

        if netlist_csv_path:
            p = Path(netlist_csv_path)
            if not p.is_file():
                return {"error": f"netlist CSV not found: {netlist_csv_path}",
                        "pads_bound": 0}
            parsed = parse_tabular_netlist(p.read_text(encoding="utf-8-sig"))
            if not parsed["ok"]:
                return {"error": parsed["reason"], "pads_bound": 0}
            nodes = parsed["nodes"]
        else:
            bridge = get_bridge()
            data = await bridge.send_command_async(
                "project.get_nets", {"limit": "100000"}
            )
            pins = data.get("pins", []) if isinstance(data, dict) else []
            nodes = []
            for pin_rec in pins:
                comp = str(pin_rec.get("component", "")).strip()
                pin = str(pin_rec.get("pin", "")).strip()
                pin_name = str(pin_rec.get("pin_name", "")).strip()
                net = str(pin_rec.get("net", "")).strip()
                if comp and pin and net:
                    nodes.append((comp, pin, pin_name, net))

        if not nodes:
            return {"error": "netlist has no pin-net nodes (is the "
                    "project compiled / the CSV non-empty?)",
                    "pads_bound": 0}

        work = derive_netlist_build(nodes)
        bridge = get_bridge()
        net_result = await bridge.send_command_async(
            "pcb.create_nets_from_list", {"nets": "|".join(work["nets"])}
        )
        bind_result = await bridge.send_command_async(
            "pcb.bind_pad_nets",
            {"bindings": _encode_bindings_param(work["bindings"])},
        )
        return {
            "components": len(work["components"]),
            "nets_created": net_result.get("created", 0),
            "nets_existing": net_result.get("existing", 0),
            "pads_bound": bind_result.get("bound", 0),
            "failed": bind_result.get("failed", 0),
            "missing_components": bind_result.get("missing_components", []),
            "missing_pads": bind_result.get("missing_pads", []),
            "missing_nets": bind_result.get("missing_nets", []),
        }

    @mcp.tool()
    async def pcb_get_design_rules() -> dict[str, Any]:
        """Get all design rules from the active PCB.

        Returns:
            Dictionary with "rules" array (each with name, rule_kind, enabled,
            priority, scope_1, scope_2, comment, descriptor) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_design_rules", {})
        return result

    @mcp.tool()
    async def pcb_set_rules_enabled(
        names: list[str],
        enabled: bool,
        match: str = "name",
    ) -> dict[str, Any]:
        """Bulk-toggle the DRC-enabled flag on design rules by name.

        Useful for focused review passes: disable a noisy class of rules
        to surface the violations that matter (e.g. silence Silk-to-Silk
        while you sweep Clearance), or re-enable a set before a release
        sweep. Matches case-insensitively; supports trailing-* wildcards
        so a rule family can be targeted without listing every name
        (``"DiffPair_*"`` hits every rule whose name starts with that).

        Note that this flips ``DRCEnabled`` (whether DRC actually checks the rule),
        not ``Enabled`` (whether the rule exists in the rule list); the
        former is the right knob for review iteration.

        Args:
            names: Rule names to toggle. With ``match="name"`` (default)
                each entry is matched case-insensitively against
                ``Rule.Name``; a trailing ``*`` wildcards the suffix
                (``"Clearance*"``). With ``match="kind"`` each entry is
                a TRuleKind ordinal as a string (look up via
                ``pcb_get_design_rules``).
            enabled: New value for ``DRCEnabled``. True turns the rule
                ON in DRC; False disables it for this run.
            match: ``"name"`` (default) or ``"kind"``.

        Returns:
            Dict with:
              - ``matched``: rules that matched any pattern
              - ``updated``: how many actually changed value
              - ``enabled``: the requested target value (for confirmation)
              - ``items``: per-matched ``{name, kind, prev_enabled, new_enabled}``
        """
        if not names:
            return {"ok": False, "reason": "names list is empty"}
        # The list rides a pipe-separated field, and this tool MUTATES
        # rule state: 'A|B' would arrive as two patterns, and with the
        # trailing-* wildcard a fragment can plausibly match real rules.
        # Refuse rather than strip: stripping produces a different
        # valid pattern.
        bad = [n for n in names if "|" in str(n)]
        if bad:
            return {"ok": False, "reason":
                    f"rule names {bad} contain '|', which is the "
                    "wire-format separator; fragments could match other "
                    "rules, so the call is refused"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.set_rules_enabled",
            {
                "names": "|".join(str(n) for n in names),
                "enabled": "true" if enabled else "false",
                "match": match,
            },
        )

    @mcp.tool()
    async def pcb_get_clearance_violations(
        net: str = "",
    ) -> dict[str, Any]:
        """Read the violations ALREADY on the board, optionally filtered
        to one net. Does NOT run DRC.

        This is a pure board read: it walks the ``eViolationObject``
        records the last DRC run (batch or online) left behind. It
        opens no dialog and cannot block the bridge. Use it as the
        default way to inspect violations.

        It used to trigger ``PCB:DesignRuleCheck`` first, which raises
        Altium's MODAL "Design Rule Checker" setup dialog: on a
        241-component board that wedged the whole bridge for 30+ min
        until the client timed out. A "get" tool must not do that, so
        the trigger is gone. ``pcb_run_drc(allow_modal=True)`` is now
        the only way to force a fresh run.

        CAVEAT -- ``violation_count: 0`` means "no violations are
        stored on this board", NOT "the board passes". If DRC has
        never been run in this Altium session, there is nothing to
        read and the answer is 0 either way. The response carries
        ``drc_triggered: false`` and a ``note`` saying so. To get
        fresh data, run DRC from Altium's UI (Tools > Design Rule
        Check) or call ``pcb_run_drc(allow_modal=True)`` and be ready
        to click the dialog.

        Filter is substring-matched against the violation's Description
        and Name, so it catches both "Net USB_DP and Net GND" clearance
        warnings and net-named via antennas.

        Each violation carries ``x_mils`` / ``y_mils`` / ``layer`` for
        the agent to navigate to, plus ``primitive1`` / ``primitive2``
        objects with ``{detail, type, net, layer, x_mils, y_mils}``.

        Args:
            net: Net name to filter by (substring match). Empty string
                returns ALL stored violations.

        Returns:
            Dict with ``{violation_count, drc_triggered, note,
            violations}``. Capped at 200.
        """
        bridge = get_bridge()
        params = {"net": net} if net else {}
        return await bridge.send_command_async(
            "pcb.get_clearance_violations", params, timeout=90.0)

    @mcp.tool()
    async def pcb_run_drc(allow_modal: bool = False) -> dict[str, Any]:
        """Run Design Rule Check (DRC) on the active PCB. MODAL --
        BLOCKS THE BRIDGE. Opt in with ``allow_modal=True``.

        DANGER: Altium has no non-interactive DRC trigger exposed to
        DelphiScript. ``PCB:DesignRuleCheck`` raises the MODAL "Design
        Rule Checker [mil]" setup dialog and the worker sits behind it
        until a HUMAN clicks Cancel/OK. Nothing else on the bridge
        runs meanwhile: one observed call left the loop dead for 30+
        min, the client timed out at 1800 s, and every in-flight job
        was stranded. No parameter suppresses the dialog, so this tool
        refuses to fire unless the caller passes ``allow_modal=True``,
        which is a promise that a human is at the keyboard.

        PREFER ``pcb_get_clearance_violations`` -- it reads the
        violations already stored on the board, runs no DRC, and
        cannot block.

        PATHOLOGICAL CASE: a full DRC on a placed-but-unrouted board
        (many components, zero tracks, everything unrouted) checks
        every pad pair against every rule and is enormously slow for
        an answer that is already known -- "nothing is routed yet".
        Check ``pcb_get_board_statistics`` / ``pcb_get_unrouted_nets``
        first; if track count is 0, do not run DRC.

        With ``allow_modal=True`` it executes the DRC and returns up
        to 100 violations with full location data, so the agent can
        jump straight to the offending spot instead of guessing from
        the description.

        Per-violation shape:
          - ``name``, ``description``, ``rule``: text from Altium
          - ``x_mils`` / ``y_mils`` / ``layer``: violation's bbox
            centre (the "go here" hint)
          - ``primitive1`` / ``primitive2``: objects with
            ``{detail, type, net, layer, x_mils, y_mils}``. ``type``
            is the object kind (Track / Pad / Via / Region / Comp);
            ``net`` is the net name. Two primitives are involved in
            most rule kinds (Clearance, Short Circuit); a single-
            primitive violation (e.g. AcuteAngle) leaves
            ``primitive2`` with empty fields.

        Use ``primitive1.net`` and ``primitive2.net`` to spot the
        nets in conflict; use ``x_mils`` / ``y_mils`` to drive
        ``proj_cross_probe`` or the dashboard's Drawing tab to the site.

        Args:
            allow_modal: Must be True to actually run DRC, and it
                means "I accept that the bridge blocks on a modal
                dialog until a human clicks it". Default False
                refuses without touching Altium.

        Returns:
            Dict with ``violation_count`` (full count even if > 100),
            ``drc_triggered``, and ``violations`` array (first 100).
            With ``allow_modal=False``: ``{ok: False, reason: ...}``
            and no bridge call at all.
        """
        if not allow_modal:
            return {
                "ok": False,
                "reason": (
                    "pcb_run_drc opens Altium's MODAL Design Rule Checker "
                    "dialog and blocks the whole bridge until a human "
                    "closes it (observed: 30+ min dead loop, client "
                    "timeout). Refusing by default. Use "
                    "pcb_get_clearance_violations to read the violations "
                    "already on the board, or pass allow_modal=True if a "
                    "human is at the keyboard to dismiss the dialog."),
                "drc_triggered": False,
            }
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.run_drc", {"allow_modal": "true"})
        return result

    @mcp.tool()
    async def pcb_get_components() -> dict[str, Any]:
        """Get all components from the active PCB with position and properties.

        DATASHEET DISCIPLINE: The response carries a `_datasheet_guidance`
        block. Before drawing any conclusion about a listed part's
        electrical behavior, pin function, or voltage rating, fetch its
        manufacturer datasheet (use WebSearch + WebFetch if you don't
        already have it). Library metadata here is NOT authoritative.

        Returns:
            Dictionary with "components" array (each with designator, x, y,
            rotation, layer, footprint) and "count", plus
            `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_components", {})
        return tag_response(result, components=result, context="pcb_get_components")

    @mcp.tool()
    async def pcb_move_components(
        moves: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Move and/or rotate MANY PCB components in ONE IPC round-trip.

        PREFER THIS over moving one component per call. Each singular
        move is a full LLM turn (5-15 s); one call to this tool
        repositions every component in the list in a single Altium
        transaction.

        Typical uses: applying a full layout pass, running an
        auto-placement result, undoing and redoing a placement set,
        adjusting a row of components relative to each other.

        Args:
            moves: List of move dicts. Each dict supports:
                designator (required) , target component
                x        (optional)   , new X in mils
                y        (optional)   , new Y in mils
                rotation (optional)   , new rotation in degrees

            Example:
                [
                  {"designator": "U1", "x": 5000, "y": 5000, "rotation": 0},
                  {"designator": "R1", "x": 5200, "y": 4800},
                  {"designator": "C1", "rotation": 90},
                ]

        Returns:
            Dictionary with per-designator results and a count.
        """
        # Pack each move as comma-separated fields: designator,x,y,rotation
        # (empty field = leave that property unchanged). Moves joined by '|'.
        # This format is unambiguous and matches PCB_PlaceTracks.
        ops: list[str] = []
        for m in moves:
            desig = str(m.get("designator", "")).strip()
            if not desig:
                continue
            # Moves are comma-packed then pipe-joined, and this MOVES
            # components: a designator carrying either delimiter would
            # re-shape into a different move whose fragment can match a
            # real part. Refuse rather than strip ('R|1' stripped is
            # 'R1', a real component).
            if "," in desig or "|" in desig:
                return {"ok": False, "reason":
                        f"designator {desig!r} contains ',' or '|', "
                        "which are the wire-format separators; a "
                        "fragment could move another component, so the "
                        "call is refused", "moves_applied": 0}
            x_str = (
                str(round(m["x"])) if "x" in m and m["x"] is not None else ""
            )
            y_str = (
                str(round(m["y"])) if "y" in m and m["y"] is not None else ""
            )
            rot_str = (
                str(m["rotation"])
                if "rotation" in m and m["rotation"] is not None
                else ""
            )
            if x_str == "" and y_str == "" and rot_str == "":
                continue
            ops.append(f"{desig},{x_str},{y_str},{rot_str}")

        if not ops:
            return {"error": "No valid moves provided", "moves_applied": 0}

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.batch_move_components",
            {"moves": "|".join(ops)},
        )
        return result

    @mcp.tool()
    async def pcb_import_placement(
        placements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Position components from a coordinate list (pick-and-place import).

        The inverse of `pcb_export_coordinates`. Each entry repositions a
        component already on the board, by designator. Absolute coordinates in
        mils, rotation in degrees; supplying `side`/`layer` flips the component
        to that side.

        Args:
            placements: list of dicts, each supporting:
                designator (required)
                x        (optional) , X in mils
                y        (optional) , Y in mils
                rotation (optional) , degrees
                side     (optional) , "top" or "bottom"
                layer    (optional) , "TopLayer" / "BottomLayer" (overrides side)

        Returns:
            {"applied": N, "failed": M}.
        """
        ops: list[str] = []
        for p in placements:
            desig = str(p.get("designator", "")).strip()
            if not desig:
                continue
            x_str = str(round(p["x"])) if p.get("x") is not None else ""
            y_str = str(round(p["y"])) if p.get("y") is not None else ""
            rot_str = str(p["rotation"]) if p.get("rotation") is not None else ""
            layer = p.get("layer")
            if not layer and p.get("side") is not None:
                side = str(p["side"]).strip().lower()
                layer = "BottomLayer" if side in ("bottom", "bot", "b") else "TopLayer"
            layer_str = str(layer) if layer else ""
            ops.append(f"{desig},{x_str},{y_str},{rot_str},{layer_str}")
        if not ops:
            return {"error": "No valid placements provided", "applied": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.import_placement", {"placements": "|".join(ops)}
        )

    @mcp.tool()
    async def pcb_add_teardrops() -> dict[str, Any]:
        """Add teardrops to pad/via-track junctions board-wide.

        Launches Altium's Teardrop command on all objects. The Teardrop dialog
        is modal and cannot be suppressed from script (same limitation as the
        ECO dialog): choose Add and confirm it in Altium. Returns once the
        command is dispatched.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.teardrops", {})

    @mcp.tool()
    async def pcb_remove_teardrops() -> dict[str, Any]:
        """Remove teardrops board-wide.

        Opens the same modal Teardrop dialog as `pcb_add_teardrops`; choose
        Remove and confirm it in Altium.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.teardrops", {})

    @mcp.tool()
    async def pcb_autoplace_silkscreen() -> dict[str, Any]:
        """Reposition component designators to clear pads and other silk.

        For every visible designator, tries a ring of auto-position anchors and
        keeps the first that overlaps no pad or other silk text; otherwise
        leaves the designator where it was. First-fit, not a global optimum;
        pair with the silk audits and `design_visual_review` to check the
        result.

        Returns:
            {"placed": N, "skipped": M, "total": T}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.autoplace_silkscreen", {})

    @mcp.tool()
    async def pcb_tune_length(
        net: str,
        add_length_mils: int,
        x_mils: int,
        y_mils: int,
        layer: str = "TopLayer",
        amplitude_mils: int = 40,
        width_mils: int = 6,
    ) -> dict[str, Any]:
        """Add approximate routed length to a net with a square serpentine.

        Lays a square meander on `net` at (x_mils, y_mils) sized to add about
        `add_length_mils`, then reports the net's routed length before and
        after.

        Open-loop and NOT DRC-checked: you choose where the meander goes and
        verify clearance afterward (`pcb_run_drc`). Use it to get a net close to
        a target, then finish in Altium's interactive tuner if needed. There is
        no scriptable interactive tuner in Altium, so this is the available
        approximation.

        Args:
            net: net name.
            add_length_mils: target length to add (mils).
            x_mils, y_mils: anchor point of the meander (mils).
            layer: routing layer (default TopLayer).
            amplitude_mils: meander height; bump count = add_length / (2*amplitude).
            width_mils: track width.

        Returns:
            {"length_before_mils", "length_after_mils", "added_mils", "bumps",
             "drc_checked": false}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.tune_length",
            {
                "net": net,
                "add_length_mils": add_length_mils,
                "x_mils": x_mils,
                "y_mils": y_mils,
                "layer": layer,
                "amplitude_mils": amplitude_mils,
                "width_mils": width_mils,
            },
        )

    @mcp.tool()
    async def pcb_panelize(
        child_path: str,
        board_width_mils: int,
        board_height_mils: int,
        rows: int = 2,
        cols: int = 2,
        col_gap_mils: int = 100,
        row_gap_mils: int = 100,
        border_mils: int = 200,
        tooling_holes: bool = True,
        fiducials: bool = True,
    ) -> dict[str, Any]:
        """Build a production panel on the CURRENT (blank) PCB document.

        Open a new empty .PcbDoc and run this on it. Creates an embedded-board
        array of `child_path`, a rectangular panel outline, corner tooling
        holes, and fiducials. Supply the source board size
        (`board_width_mils` / `board_height_mils`).

        Args:
            child_path: path to the source .PcbDoc to array.
            board_width_mils, board_height_mils: source board size (mils).
            rows, cols: array dimensions.
            col_gap_mils, row_gap_mils: spacing between adjacent boards.
            border_mils: rail width around the array (also half the tooling inset).
            tooling_holes, fiducials: whether to add them.

        Returns:
            {"panel_width_mils", "panel_height_mils", "rows", "cols",
             "tooling_holes", "fiducials"}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.panelize",
            {
                "child_path": child_path,
                "board_width_mils": board_width_mils,
                "board_height_mils": board_height_mils,
                "rows": rows,
                "cols": cols,
                "col_gap_mils": col_gap_mils,
                "row_gap_mils": row_gap_mils,
                "border_mils": border_mils,
                "tooling_holes": tooling_holes,
                "fiducials": fiducials,
            },
        )

    @mcp.tool()
    async def pcb_delete_invalid_objects() -> dict[str, Any]:
        """Remove degenerate primitives from the active PCB.

        Deletes zero-length tracks and zero-area regions (board hygiene that
        accumulates from edits and imports). Returns the count removed.

        Returns:
            {"removed": N}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.delete_invalid_objects", {})

    @mcp.tool()
    async def pcb_audit_pad_center_connected() -> dict[str, Any]:
        """Find pads whose center has no copper entering it (acid-pad check).

        For every netted pad, checks that a track / arc / via on the same net
        reaches the pad centroid. A pad connected only at its edge (an "acid
        pad") can fail in fab. Read-only; reports offenders with designator,
        pad, net, and location.

        Returns:
            {"checked": N, "offenders": M, "items": [...]}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.audit_pad_center_connected", {})

    @mcp.tool()
    async def pcb_auto_size_board_outline(margin_mils: int = 100) -> dict[str, Any]:
        """Fit the board outline around the embedded-board array(s) plus a margin.

        Use after `pcb_panelize` (or any embedded-board layout) to size the
        panel outline to its content. Unions the embedded boards' extents, adds
        `margin_mils` on every side, and converts the result to the board
        outline.

        Args:
            margin_mils: border added on each side (default 100).

        Returns:
            {"width_mils", "height_mils"}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.auto_size_board_outline", {"margin_mils": margin_mils}
        )

    @mcp.tool()
    async def pcb_normalize_vias() -> dict[str, Any]:
        """Snap every via to its dominant routing-via-style rule.

        Sets each via's diameter and hole to the rule's preferred values, so a
        board with mixed/hand-edited via sizes conforms to the design rules.

        Returns:
            {"checked": N, "changed": M}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.normalize_vias", {})

    @mcp.tool()
    async def pcb_copy_designators_to_mech(layer: str = "Mechanical1") -> dict[str, Any]:
        """Copy each component designator onto a mechanical layer (assembly prep).

        Places a `.Designator` special-string text on `layer` over every
        component, for an assembly drawing that survives silkscreen edits.

        Args:
            layer: target mechanical layer (default Mechanical1).

        Returns:
            {"copied": N, "layer": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.copy_designators_to_mech", {"layer": layer}
        )

    @mcp.tool()
    async def pcb_trim_extend_track(
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        tolerance_mils: int = 5,
    ) -> dict[str, Any]:
        """Trim or extend one track endpoint along the track's own slope.

        Finds the track endpoint nearest (from_x, from_y), then slides it to
        the perpendicular projection of (to_x, to_y) onto that track's line.
        The track stays collinear (no bend introduced) and the opposite end
        stays put, so this cleanly lengthens a track up to a target or pulls
        it back. All coordinates in mils.

        Args:
            from_x, from_y: Picks which endpoint moves (nearest one wins).
            to_x, to_y: Target the moving end is projected toward.
            tolerance_mils: How close (from_x, from_y) must be to a real
                endpoint (default 5).

        Returns:
            {"moved_end", "old_x", "old_y", "new_x", "new_y", "layer"}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.trim_extend_track",
            {
                "from_x": str(from_x),
                "from_y": str(from_y),
                "to_x": str(to_x),
                "to_y": str(to_y),
                "tolerance_mils": str(tolerance_mils),
            },
        )

    @mcp.tool()
    async def pcb_cleanup_tracks(
        mode: str = "slivers",
        min_length_mils: int = 1,
    ) -> dict[str, Any]:
        """Tidy stray track geometry: delete slivers and/or merge collinear runs.

        Modes:
        - "slivers" (default): delete every track shorter than
          min_length_mils. Catches the zero/near-zero stubs left behind by
          editing.
        - "merge": join two collinear, same-layer, same-width, same-net tracks
          that meet end to end into a single track. The merge only fires when
          the shared point is a clean degree-2 junction (exactly those two
          track ends, with no via, pad, arc, or third track present), so it is
          safe to run on routed copper without breaking connectivity.
        - "both": slivers pass, then merge pass.

        Args:
            mode: "slivers", "merge", or "both" (default "slivers").
            min_length_mils: Sliver threshold (default 1).

        Returns:
            {"slivers_deleted", "merged", "mode"}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.cleanup_tracks",
            {"mode": mode, "min_length_mils": str(min_length_mils)},
            timeout=120.0,
        )

    @mcp.tool()
    async def pcb_place_thieving_pads(
        layer: str = "TopLayer",
        pad_size_mils: int = 20,
        pitch_mils: int = 50,
        clearance_mils: int = 15,
        margin_mils: int = 100,
    ) -> dict[str, Any]:
        """Fill bare copper area with a grid of isolated thieving pads.

        Drops a regular grid of small round pads on `layer`, inside the board
        outline minus `margin_mils`. A grid point is skipped whenever any
        existing primitive (track, arc, via, pad, fill, region, polygon, or
        component body) sits within half-pad + clearance of it, so pads land
        only in genuinely empty regions. Pads carry no net. Evens out plating
        current on sparse boards. Capped at 5000 pads per run.

        Args:
            layer: Copper layer to populate (default TopLayer).
            pad_size_mils: Pad diameter (default 20).
            pitch_mils: Grid spacing (default 50).
            clearance_mils: Keep-out from existing copper (default 15).
            margin_mils: Inset from board edge (default 100).

        Returns:
            {"placed", "scanned", "layer"}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.place_thieving_pads",
            {
                "layer": layer,
                "pad_size_mils": str(pad_size_mils),
                "pitch_mils": str(pitch_mils),
                "clearance_mils": str(clearance_mils),
                "margin_mils": str(margin_mils),
            },
            timeout=180.0,
        )

    @mcp.tool()
    async def pcb_move_tracks_to_layer(
        net_name: str,
        target_layer: str,
        via_size_mils: int = 50,
        via_hole_mils: int = 28,
    ) -> dict[str, Any]:
        """Move all tracks of a net to one layer, adding vias where needed.

        Relayers every track of `net_name` onto `target_layer`, then drops a
        via at each same-net single-layer (SMD) pad that is NOT on the target
        layer, since that pad can now only connect through a layer change.
        Multilayer (through-hole) pads need no via. This flattens a net's
        routing onto one layer, so use it deliberately.

        Args:
            net_name: Net whose tracks move.
            target_layer: Destination signal layer (e.g. "BottomLayer").
            via_size_mils: Via pad diameter for added vias (default 50).
            via_hole_mils: Via hole diameter (default 28).

        Returns:
            {"net", "target_layer", "moved", "vias_added"}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.move_tracks_to_layer",
            {
                "net_name": net_name,
                "target_layer": target_layer,
                "via_size_mils": str(via_size_mils),
                "via_hole_mils": str(via_hole_mils),
            },
            timeout=120.0,
        )

    @mcp.tool()
    async def pcb_bevel_polygon_corners(
        index: int = 0,
        net_name: str = "",
        bevel_mils: int = 25,
    ) -> dict[str, Any]:
        """Chamfer the corners of a copper polygon pour.

        Replaces each sharp vertex with two points set back along its edges by
        bevel_mils (auto-clamped so neighbouring bevels never overlap), giving
        every corner a straight 45-style cut. Only straight-outline polygons
        are handled, a polygon containing arc segments is left untouched. The
        polygon is repoured after the edit.

        Args:
            index: Which polygon to bevel (0-based, in board iteration order).
            net_name: If set, count only polygons on this net when applying
                index.
            bevel_mils: Set-back distance per edge (default 25).

        Returns:
            {"beveled", "index", "orig_vertices", "new_vertices", "bevel_mils"},
            or an error code (NOT_FOUND / BAD_POLYGON / HAS_ARC).
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.bevel_polygon_corners",
            {"index": str(index), "net_name": net_name, "bevel_mils": str(bevel_mils)},
        )

    @mcp.tool()
    async def pcb_plan_placement(
        designators: Optional[list[str]] = None,
        fixed: Optional[list[str]] = None,
        region: Optional[dict[str, float]] = None,
        iterations: int = 400,
        grid_mils: float = 5.0,
        clearance_mils: float = 15.0,
        max_net_fanout: int = 0,
        exclude_nets: Optional[list[str]] = None,
        critical_nets: Optional[list[str]] = None,
        critical_weight: float = 3.0,
        edge_parts: Optional[dict[str, str]] = None,
        keepout_groups: Optional[dict[str, str]] = None,
        match_groups: Optional[dict[str, str]] = None,
        match_roles: Optional[dict[str, str]] = None,
        plan_json: Optional[Union[str, dict]] = None,
        reseed_grid: bool = False,
        optimize_rotation: bool = True,
        engine: str = "refine",
        restarts: int = 1,
        render_png: Optional[str] = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Connectivity-driven auto-placement: shorten wirelength, keep
        components legal. **Dry-run by default** -- returns a proposed
        move list and quality metrics without touching the board.

        A pure-Python solver run on the current board. It reads the
        compiled netlist and every component's real footprint bounding
        box, then improves positions to minimize half-perimeter
        wirelength (HPWL) while keeping components inside the board
        outline and free of same-layer overlaps.

        Two solver cores are available via ``engine``:

        - ``"refine"`` (default): a force-directed relaxation that nudges
          the board's CURRENT positions, then a legalization pass. Best
          for tidying an already-placed board.
        - ``"construct"``: a from-scratch constructor that sizes the
          placement, seeds parts at connectivity-weighted centroids,
          chooses orientations, legalizes, and runs a short cooled
          polish on a full multi-term objective (wirelength, overlap,
          board-edge, decoupling proximity, connector banding, thermal).
          Best for an unplaced or badly scrambled board. The full
          per-term objective is always returned under ``objective_report``.

        With ``optimize_rotation`` (default on) it also picks each
        eligible part's orientation (0/90/180/270) to point its pins at
        the nets they connect -- the classic auto-place win for 2-pin
        passives. This needs per-pin geometry, so the tool also reads the
        board's pads and maps them to components; orientation is only
        considered for top-side parts currently sitting at an orthogonal
        angle (others keep their rotation).

        Workflow: call once with ``apply=False`` (default), read the
        ``hpwl_improvement_pct`` and ``overlap_pairs_after``, eyeball the
        ``moves``, then call again with ``apply=True`` to commit them in
        one ``pcb.batch_move_components`` transaction. Components on
        opposite layers may share an X/Y footprint (they cannot
        physically collide).

        Seeding: by default the solver refines the board's CURRENT
        positions (small, sensible nudges). Pass ``reseed_grid=True`` for
        a from-scratch re-place (ignores current positions) -- use only
        on an unplaced or badly-scrambled board.

        Args:
            designators: If given, ONLY these components are moved; every
                other placed component is held fixed but still attracts
                its nets and blocks overlaps. If omitted, all components
                are candidates (minus ``fixed``).
            fixed: Designators to pin in place (connectors, mounting
                holes, anything already positioned). Naming-agnostic --
                you choose which; the tool does not guess by prefix.
            region: Placement bounds ``{x1, y1, x2, y2}`` in mils. If
                omitted, the board outline's bounding rectangle is used.
            iterations: Global-placement relaxation steps (default 400).
                More iterations = better convergence, longer runtime.
            grid_mils: Snap grid for final positions (default 5).
            clearance_mils: Extra copper-to-copper breathing room added
                to each pair's half-extents (default 15).
            max_net_fanout: Skip nets touching more than this many
                components (default 0 = keep all). Set e.g. 20 to ignore
                power/ground planes that do not usefully guide placement.
            exclude_nets: Explicit net names to ignore (e.g. ``["GND"]``).
            critical_nets: Net names to treat as placement-critical (e.g. a
                clock, a high-speed pair, a sensitive analog feedback line).
                Their pull is scaled by ``critical_weight`` so the solver
                keeps their endpoints close and shortens those traces first,
                at a small cost to ordinary nets. You (the planner) decide
                which nets matter; the tool does not guess by name.
            critical_weight: Multiplier applied to ``critical_nets`` pull
                (default 3.0, clamped to [1, 20]). Higher = more aggressive
                shortening of the critical nets versus the rest.
            edge_parts: ``{refdes: band}`` mapping parts that must sit on a
                board EDGE to which edge -- ``"L"``/``"R"``/``"T"``/``"B"``
                (left/right/top/bottom). The solver seats each against that
                edge (centred on the free axis) and holds it there. Use for
                connectors, mounting holes, USB/headers -- anything needing
                cable or panel access. You (the planner) decide which; the
                tool does not guess by designator prefix.
            keepout_groups: ``{refdes: group}`` mixed-signal separation tags.
                Parts in DIFFERENT groups are pushed apart (the
                industrial-engineering "undesirable adjacency" relationship)
                -- segregate a noisy switching / digital section from a
                sensitive analog / RF one. The group string is opaque (only
                equality matters); you (the planner) assign the domains
                semantically. ``construct`` engine only. Separating the groups
                usually also shortens wirelength by de-interleaving them.
            match_groups: ``{refdes: group}`` matched-pair keep-together tags.
                Parts in the SAME group are pulled ADJACENT even when they
                share no net -- the absolutely-necessary-adjacency relationship
                / analog common-centroid matching (a differential pair's two
                input resistors, a current mirror, a matched array). HPWL only
                co-locates parts that share a net; this co-locates matched ones
                that don't. ``construct`` engine only.
            match_roles: ``{refdes: role}`` common-centroid sub-device labels
                WITHIN a match_group (e.g. ``A``/``B`` for the two matched
                halves of a cross-quad or interdigitated array). The optimiser
                drives each role's centroid onto a common point so a linear
                process/thermal gradient cancels -- it picks the balanced
                ABBA/ABAB arrangement that ``match_groups`` alone (which only
                keeps the cells compact) cannot distinguish from an unbalanced
                AABB. Only meaningful with >= 2 roles per group and >= 2 cells
                per role; needs ``match_groups`` set too. ``construct`` only.
            plan_json: The DesignPlan (JSON string or dict) you authored. When
                given, the placer auto-derives matched-pair ``match_groups``
                from differential nets and analog/digital ``keepout_groups``
                from the plan's net roles, and MERGES them under the explicit
                ``match_groups`` / ``keepout_groups`` args (explicit wins per
                refdes). No-op for a plan with no differential / mixed-signal
                structure. What was inferred is echoed in ``auto_constraints``.
            reseed_grid: Re-place from a fresh grid instead of refining
                current positions (default False).
            optimize_rotation: Also choose part orientation to shorten
                pin-level wirelength (default True). Set False to keep
                every part's current rotation.
            engine: ``"refine"`` (default, nudge current positions) or
                ``"construct"`` (from-scratch placement). ``"construct"``
                implies a from-scratch re-place regardless of
                ``reseed_grid``.
            restarts: ``"construct"`` engine only. When > 1, run that many
                seeded restarts and keep the lowest-objective legal
                placement (variance reduction at a cost linear in
                ``restarts``). Default 1 (single deterministic run).
            render_png: Optional file path. When set, also render a preview
                image of the proposed placement (board outline, courtyards,
                net stars) to that path and return it as ``preview_png``
                (offline, matplotlib). Rendering never breaks the move data;
                failures surface as ``preview_error``.
            apply: When True, commit the moves to the board. Default
                False (dry-run).

        Returns:
            Dict with:
              - ``dry_run``: bool (True unless ``apply`` and moves exist)
              - ``engine``: the solver core used
              - ``component_count``, ``movable_count``, ``fixed_count``
              - ``net_count`` (nets used after filtering)
              - ``pin_count`` (pads mapped to components for rotation)
              - ``summary``: a one-line plain-language assessment synthesising
                legality, utilization, signal routability, decoupling, and the
                suggested board size -- read this first to judge the placement
              - ``hpwl_before``, ``hpwl_after``, ``hpwl_improvement_pct``
              - ``overlap_pairs_before``, ``overlap_pairs_after``
              - ``objective_report``: every objective term un-weighted
                (``hpwl``, ``via``, ``cong``, ``clear``, ``edge``,
                ``decap``, ``conn``, ``therm``) plus ``weighted_total``,
                ``legal``, and ``utilization`` for the proposed layout
              - ``net_length_report``: ``longest_nets`` (the few nets with
                the largest physical bounding span in mils -- the routing
                risk, and the candidates to mark ``critical_nets``) plus
                ``critical_net_spans`` echoing the achieved span of each
                net you flagged critical, so you can confirm it paid off
              - ``ratsnest``: routability / via-pressure indicator from the
                straight-line MST of each net. ``signal_crossings`` (rails
                above ``signal_fanout_cap`` pins excluded, since planes carry
                no signal via) predicts routing difficulty -- different-net
                MST edges that cross need a layer change; ``total_crossings``
                is the conservative all-net count
              - ``decoupling_report``: the engine's structural decoupling
                analysis -- a list of ``{decap, ic, distance_mils}`` (worst
                first) naming which cap decouples which IC (found on the
                connectivity graph, never by reference) and how close it
                landed to the served power pin. Empty when no decoupling is
                structurally identifiable
              - ``suggested_board``: for the ``construct`` engine, the board
                rectangle it sized and tightened to the placement
                (``{x1, y1, x2, y2, width, height}`` in mils) -- the "best
                PCB size" recommendation. ``null`` for the ``refine`` engine,
                which works inside the existing outline
              - ``moved_count``, ``rotated_count`` and ``moves`` (each
                ``{designator, from: {x, y, rotation}, to: {x, y,
                rotation}}`` in mils/degrees; ``to.rotation`` is null
                when the orientation was unchanged)
              - ``region`` used, ``notes`` from the solver, and
                ``apply_result`` when ``apply=True``.
        """
        bridge = get_bridge()

        comp_resp = await bridge.send_command_async("pcb.get_components", {})
        raw_comps = comp_resp.get("components") if isinstance(comp_resp, dict) else None
        if not raw_comps:
            return {
                "error": "NO_COMPONENTS",
                "reason": "pcb.get_components returned no components",
                "raw": comp_resp,
            }

        # Resolve placement region from the board outline if not given.
        region_used: dict[str, float]
        if region and all(k in region for k in ("x1", "y1", "x2", "y2")):
            region_used = {k: float(region[k]) for k in ("x1", "y1", "x2", "y2")}
        else:
            outline = await bridge.send_command_async("pcb.get_board_outline", {})
            br = outline.get("bounding_rect") if isinstance(outline, dict) else None
            if not br:
                return {
                    "error": "NO_BOARD_OUTLINE",
                    "reason": "No board outline; pass an explicit region "
                    "{x1, y1, x2, y2} in mils.",
                    "raw": outline,
                }
            region_used = {
                "x1": float(br.get("left", 0)),
                "y1": float(br.get("bottom", 0)),
                "x2": float(br.get("right", 0)),
                "y2": float(br.get("top", 0)),
            }

        # Build the solver's component list. Capture each part's centroid,
        # origin, current rotation, bbox (for the pad->component spatial
        # join), and C0 = the centroid's offset from the origin at
        # rotation 0 -- needed to convert a solved (centroid, rotation)
        # back to an Altium move (which sets the origin and rotates about
        # it).
        def _is_orthogonal(deg: float) -> bool:
            return abs((deg % 90.0)) < 0.5 or abs((deg % 90.0) - 90.0) < 0.5

        fixed_set = {str(d).strip() for d in (fixed or []) if str(d).strip()}
        selected = {str(d).strip() for d in (designators or []) if str(d).strip()}
        geom: dict[str, dict[str, Any]] = {}
        place_comps: list[PlaceComp] = []
        comp_by_ref: dict[str, PlaceComp] = {}
        all_designators: list[str] = []
        for c in raw_comps:
            ref = str(c.get("designator", "")).strip()
            if not ref:
                continue
            all_designators.append(ref)
            bbox = c.get("bbox") or {}
            try:
                w = max(1.0, float(bbox.get("width", 0)))
                h = max(1.0, float(bbox.get("height", 0)))
                x1 = float(bbox.get("x1", 0))
                y1 = float(bbox.get("y1", 0))
                x2 = float(bbox.get("x2", 0))
                y2 = float(bbox.get("y2", 0))
                ox = float(c.get("x", 0))
                oy = float(c.get("y", 0))
                rot = float(c.get("rotation", 0) or 0)
            except (TypeError, ValueError):
                continue
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            layer = str(c.get("layer", "Top")) or "Top"
            # C0 = back-rotated centroid-from-origin vector (rotation 0).
            c0 = rotate_offset(cx - ox, cy - oy, -rot)
            geom[ref] = {
                "cx": cx, "cy": cy, "ox": ox, "oy": oy, "rot": rot,
                "c0": c0,
                "bbox": (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                "layer": layer,
            }
            # Fixed if explicitly pinned, or (when a selection is given)
            # not part of the selected set.
            is_fixed = ref in fixed_set or (bool(selected) and ref not in selected)
            pc = PlaceComp(
                ref=ref,
                w=w,
                h=h,
                cx=cx,
                cy=cy,
                layer=layer,
                fixed=is_fixed,
                rotation=rot,
            )
            place_comps.append(pc)
            comp_by_ref[ref] = pc

        if not place_comps:
            return {"error": "NO_PLACEABLE_COMPONENTS", "raw": comp_resp}

        # Build the net graph from the compiled netlist. One batch IPC.
        net_resp = await bridge.send_command_async(
            "project.get_connectivity_batch",
            {"designators": "~~".join(all_designators)},
            timeout=180.0,
        )
        exclude = {str(n).strip().upper() for n in (exclude_nets or [])}
        critical = {str(n).strip().upper() for n in (critical_nets or [])}
        crit_w = max(1.0, min(float(critical_weight), 20.0))
        # refdes -> edge band, validated to the four sides. Tag the matching
        # placed component so the solver seats it against that board edge.
        edge_band_of = {
            str(r).strip(): str(b).strip().upper()[:1]
            for r, b in (edge_parts or {}).items()
            if str(b).strip().upper()[:1] in ("L", "R", "T", "B")
        }
        for ref, band in edge_band_of.items():
            pc = comp_by_ref.get(ref)
            if pc is not None:
                pc.edge = True
                pc.edge_band = band
        # Role-driven auto-constraints: when the planner hands us its plan, we
        # derive the matched-pair (differential) match_groups and the
        # mixed-signal (analog/digital) keepout_groups from the plan's net
        # roles and MERGE them under the explicit args. Explicit tags win on a
        # per-refdes conflict, so the planner can always override. No-op when
        # plan_json is absent or the plan has no differential / mixed-signal
        # structure, so existing callers are unaffected.
        auto_constraints: dict[str, Any] = {}
        if plan_json is not None:
            try:
                from ..design.placement_constraints import (
                    infer_placement_constraints,
                    merge_groups,
                )
                from ..design.plan import DesignPlan
                _payload = (plan_json if isinstance(plan_json, dict)
                            else json.loads(plan_json))
                _plan = DesignPlan.model_validate(_payload)
                _c = infer_placement_constraints(_plan)
                match_groups = merge_groups(_c.match_groups, match_groups)
                keepout_groups = merge_groups(_c.keepout_groups, keepout_groups)
                auto_constraints = {
                    "match_groups_inferred": _c.match_groups,
                    "keepout_groups_inferred": _c.keepout_groups,
                }
            except (ValueError, json.JSONDecodeError, ValidationError) as exc:
                auto_constraints = {"plan_json_error": str(exc)}
        # Mixed-signal keep-apart tags: parts in DIFFERENT groups are pushed
        # apart by the separation term (construct engine only). The planner
        # supplies the grouping semantically (e.g. analog vs digital vs RF);
        # the tag string itself is opaque, only equality matters.
        for ref, grp in (keepout_groups or {}).items():
            pc = comp_by_ref.get(str(ref).strip())
            tag = str(grp).strip()
            if pc is not None and tag:
                pc.keepout_group = tag
        # Matched-pair keep-together tags: parts in the SAME group are pulled
        # adjacent even with no shared net (diff pair, current mirror, matched
        # array). Construct engine only.
        for ref, grp in (match_groups or {}).items():
            pc = comp_by_ref.get(str(ref).strip())
            tag = str(grp).strip()
            if pc is not None and tag:
                pc.match_group = tag
        # Common-centroid sub-device roles: within a match_group, parts sharing
        # a role form one matched half (the 'A' transistors vs the 'B' ones of
        # a cross-quad / interdigitated array). Drives the two halves' centroids
        # to coincide so a process/thermal gradient cancels. Needs match_group
        # too -- a role with no group is inert.
        for ref, role in (match_roles or {}).items():
            pc = comp_by_ref.get(str(ref).strip())
            tag = str(role).strip()
            if pc is not None and tag:
                pc.match_role = tag

        def _net_weight(net_name: str) -> float:
            return crit_w if net_name.upper() in critical else 1.0
        net_members: dict[str, list[str]] = {}
        comps_conn = net_resp.get("components") if isinstance(net_resp, dict) else None
        for c in comps_conn or []:
            ref = str(c.get("designator", "")).strip()
            if not ref:
                continue
            for pin in c.get("pins") or []:
                net = str(pin.get("net", "")).strip()
                if not net or net.upper() in exclude:
                    continue
                members = net_members.setdefault(net, [])
                if ref not in members:
                    members.append(ref)

        place_nets: list[PlaceNet] = []
        for net, members in net_members.items():
            if len(members) < 2:
                continue
            if max_net_fanout and len(members) > max_net_fanout:
                continue
            place_nets.append(
                PlaceNet(tuple(members), name=net, weight=_net_weight(net))
            )

        # Pin geometry: one board-wide pad query, then a spatial join (pad
        # center -> smallest containing component bbox) + back-rotation to
        # recover rotation-0 local offsets. Needed for rotation
        # optimization AND -- crucially -- to derive the net graph when
        # there is no compiled schematic netlist (a PCB-only / synced-
        # placement board, where connectivity lives on the pads, not in a
        # schematic). Only top-side, orthogonally-placed parts are made
        # rotatable.
        pin_count = 0
        netgraph_from_pads = False
        if optimize_rotation or not net_members:
            pad_resp = await bridge.send_command_async(
                "generic.query_objects",
                {"object_type": "ePadObject", "properties": "X,Y,Net",
                 "scope": "active_doc", "filter": ""},
                timeout=120.0,
            )
            pads = pad_resp.get("objects") if isinstance(pad_resp, dict) else None
            bbox_list = [(ref, geom[ref]["bbox"]) for ref in geom]
            pins_by_ref: dict[str, list[PlacePin]] = {}
            pad_net_members: dict[str, list[str]] = {}
            for pad in pads or []:
                try:
                    px = float(pad.get("X"))
                    py = float(pad.get("Y"))
                except (TypeError, ValueError):
                    continue
                net = str(pad.get("Net", "")).strip()
                if not net or net.upper() in exclude:
                    continue
                owner = None
                owner_area = None
                for ref, (bx1, by1, bx2, by2) in bbox_list:
                    if bx1 <= px <= bx2 and by1 <= py <= by2:
                        area = (bx2 - bx1) * (by2 - by1)
                        if owner_area is None or area < owner_area:
                            owner_area = area
                            owner = ref
                if owner is None:
                    continue
                g = geom[owner]
                lx, ly = rotate_offset(px - g["cx"], py - g["cy"], -g["rot"])
                pins_by_ref.setdefault(owner, []).append(PlacePin(lx, ly, net))
                m = pad_net_members.setdefault(net, [])
                if owner not in m:
                    m.append(owner)
            if optimize_rotation:
                for ref, pins in pins_by_ref.items():
                    pc = comp_by_ref[ref]
                    pc.pins = tuple(pins)
                    pc.rotatable = (
                        pc.layer.lower().startswith("top")
                        and _is_orthogonal(geom[ref]["rot"])
                    )
                    pin_count += len(pins)
            # No schematic netlist -> build the net graph from PCB pad nets
            # so the spring placement (and rotation) actually optimize.
            if not net_members and pad_net_members:
                net_members = pad_net_members
                netgraph_from_pads = True
                place_nets = []
                for net, members in net_members.items():
                    if len(members) < 2:
                        continue
                    if max_net_fanout and len(members) > max_net_fanout:
                        continue
                    place_nets.append(
                        PlaceNet(tuple(members), name=net, weight=_net_weight(net))
                    )

        region_obj = BoardRegion(
            region_used["x1"], region_used["y1"],
            region_used["x2"], region_used["y2"],
        )

        engine_norm = str(engine or "refine").strip().lower()
        if engine_norm not in ("refine", "construct"):
            return {
                "error": "BAD_ENGINE",
                "reason": "engine must be 'refine' or 'construct'",
            }

        # Seed positions / rotations of the input, for before-metrics.
        in_pos = {ref: (g["cx"], g["cy"]) for ref, g in geom.items()}
        in_rot = {ref: g["rot"] for ref, g in geom.items()}

        if engine_norm == "construct":
            # From-scratch placement core (sizes, seeds, legalizes, polishes
            # a full multi-term objective). Imported lazily so the tool
            # module stays cheap to import.
            from ..design import pcb_placement as _construct

            rules = _construct.DesignRules(
                grid=float(grid_mils),
                component_clr=float(clearance_mils),
            )
            # Auto-group each crystal/resonator with its two load caps so the
            # keep-together term clusters the oscillator tight at the MCU's
            # crystal pins (critical for short, symmetric, low-noise traces).
            # Structural + naming-agnostic; an explicit planner match_group
            # always wins.
            for ref, grp in _construct._infer_crystal_groups(
                place_comps, place_nets
            ).items():
                pc = comp_by_ref.get(ref)
                if pc is not None and not getattr(pc, "match_group", ""):
                    pc.match_group = grp
            # Auto-group a switching regulator's switch-node parts (inductor +
            # catch diode / sync FET + bootstrap cap) so keep-together keeps the
            # high-di/dt loop compact -- the dominant EMI rule for any
            # buck/boost. Structural; an explicit planner match_group wins.
            for ref, grp in _construct._infer_switch_node_groups(
                place_comps, place_nets
            ).items():
                pc = comp_by_ref.get(ref)
                if pc is not None and not getattr(pc, "match_group", ""):
                    pc.match_group = grp
            cons_opts = _construct.ConstructOptions()
            n_restarts = max(1, int(restarts))
            if n_restarts > 1:
                # Several seeded restarts; keep the lowest-objective legal one.
                cres = _construct.construct_placement_best_of(
                    place_comps, place_nets, rules,
                    seeds=tuple(range(n_restarts)), base_opts=cons_opts,
                )
            else:
                cres = _construct.construct_placement(
                    place_comps, place_nets, rules, cons_opts
                )
            # Visual repair: tighten any keep-together cluster (crystal + load
            # caps, matched pair) the analytic objective left scattered. Reads
            # the perceptual compactness metric and relocates the cluster tight
            # against the IC it serves, gated to never regress the combined
            # analytic-plus-visual objective. A no-op when nothing is scattered.
            cres = _construct.tighten_match_clusters(
                place_comps, place_nets, rules, cres)
            # The construct engine sizes (and tightens) its own board; surface
            # that as the recommended outline -- the "best PCB size" answer.
            suggested_board = {
                "x1": round(cres.region.x1, 1), "y1": round(cres.region.y1, 1),
                "x2": round(cres.region.x2, 1), "y2": round(cres.region.y2, 1),
                "width": round(cres.region.width, 1),
                "height": round(cres.region.height, 1),
            }
            result_positions = {r: (c[0], c[1]) for r, c in cres.centroids.items()}
            result_rotations = dict(cres.rotations)
            result_rotated = {
                r: result_rotations[r]
                for r in result_rotations
                if abs(result_rotations.get(r, 0.0) - in_rot.get(r, 0.0)) > 1e-6
            }
            hpwl_before = hpwl(in_pos, place_nets)
            hpwl_after = hpwl(result_positions, place_nets)
            overlap_before = overlap_pair_count(
                place_comps, in_pos, float(clearance_mils), in_rot
            )
            overlap_after = overlap_pair_count(
                place_comps, result_positions, float(clearance_mils),
                result_rotations,
            )
            solver_notes = list(cres.notes)
        else:
            # The refine engine nudges the board's CURRENT positions inside the
            # given outline; it does not propose a new board size.
            suggested_board = None
            options = PlaceOptions(
                iterations=max(0, int(iterations)),
                grid_mils=float(grid_mils),
                clearance_mils=float(clearance_mils),
                reseed_grid=bool(reseed_grid),
                optimize_rotation=bool(optimize_rotation),
            )
            result = plan_placement(place_comps, place_nets, region_obj, options)
            result_positions = dict(result.positions)
            result_rotations = dict(result.rotations)
            result_rotated = dict(result.rotated)
            hpwl_before = result.hpwl_before
            hpwl_after = result.hpwl_after
            overlap_before = result.overlap_pairs_before
            overlap_after = result.overlap_pairs_after
            solver_notes = list(result.notes)

        # Full per-term objective report on the proposed layout. Computed
        # for both engines from the same pure-Python scorer so the caller
        # always sees the trade-off, not just a single HPWL number.
        objective_report = _build_objective_report(
            place_comps, result_positions, result_rotations,
            place_nets, region_obj, float(grid_mils), float(clearance_mils),
        )

        # Per-net wirelength diagnostic: the longest nets are the routing
        # risk, and the candidates the caller may want to mark critical and
        # re-place. Also echoes the achieved span of any flagged critical net.
        net_length_report = _build_net_length_report(
            place_comps, result_positions, result_rotations,
            place_nets, critical,
        )

        # Structural decoupling analysis: which cap decouples which IC and how
        # close it landed -- confirms decaps sit tight against their ICs.
        decoupling = _build_decoupling_report(
            place_comps, result_positions, result_rotations, place_nets,
        )

        # Ratsnest crossings: a routability / via-pressure indicator. The
        # signal figure excludes high-fanout rails (planes/pours carry no
        # signal via); the total is the conservative all-net count.
        ratsnest = _build_ratsnest_report(
            place_comps, result_positions, result_rotations, place_nets,
        )

        # Convert each solved (centroid, rotation) back to an Altium move.
        # Altium sets the origin (Comp.x/y) and rotates the body about it,
        # so new_origin = target_centroid - R(newRot) * C0, where C0 is
        # the centroid's offset from the origin at rotation 0. Emit a move
        # when the part shifted past half the snap grid OR was re-oriented.
        threshold = max(1.0, float(grid_mils) / 2.0)
        moves: list[dict[str, Any]] = []
        for ref, (ncx, ncy) in result_positions.items():
            comp = comp_by_ref.get(ref)
            if comp is None or comp.fixed:
                continue
            g = geom[ref]
            new_rot = float(result_rotations.get(ref, g["rot"]))
            rotated = ref in result_rotated
            c0x, c0y = g["c0"]
            r0x, r0y = rotate_offset(c0x, c0y, new_rot)
            new_ox = int(round(ncx - r0x))
            new_oy = int(round(ncy - r0y))
            old_ox = int(round(g["ox"]))
            old_oy = int(round(g["oy"]))
            moved_xy = (abs(new_ox - old_ox) >= threshold
                        or abs(new_oy - old_oy) >= threshold)
            if not moved_xy and not rotated:
                continue
            moves.append({
                "designator": ref,
                "from": {"x": old_ox, "y": old_oy, "rotation": g["rot"]},
                "to": {"x": new_ox, "y": new_oy,
                       "rotation": new_rot if rotated else None},
            })

        improvement = (
            round((hpwl_before - hpwl_after) / hpwl_before * 100.0, 2)
            if hpwl_before > 0 else 0.0
        )
        movable_count = sum(1 for c in place_comps if not c.fixed)
        rotated_count = sum(1 for m in moves if m["to"]["rotation"] is not None)
        summary: dict[str, Any] = {
            "dry_run": True,
            "engine": engine_norm,
            "component_count": len(place_comps),
            "movable_count": movable_count,
            "fixed_count": len(place_comps) - movable_count,
            "net_count": len(place_nets),
            "net_graph_source": "pcb_pads" if netgraph_from_pads else "schematic",
            "pin_count": pin_count,
            "hpwl_before": round(hpwl_before, 1),
            "hpwl_after": round(hpwl_after, 1),
            "hpwl_improvement_pct": improvement,
            "overlap_pairs_before": overlap_before,
            "overlap_pairs_after": overlap_after,
            "summary": _build_placement_summary(
                objective_report, ratsnest, decoupling, suggested_board),
            "objective_report": objective_report,
            "net_length_report": net_length_report,
            "ratsnest": ratsnest,
            "decoupling_report": decoupling,
            "suggested_board": suggested_board,
            "moved_count": len(moves),
            "rotated_count": rotated_count,
            "moves": moves,
            "region": region_used,
            "notes": solver_notes,
        }
        if auto_constraints:
            summary["auto_constraints"] = auto_constraints

        if apply and moves:
            # Pack as designator,x,y,rotation (empty rotation field leaves
            # the orientation unchanged).
            ops = []
            for m in moves:
                rot = m["to"]["rotation"]
                rot_str = "" if rot is None else str(rot)
                ops.append(
                    f"{m['designator']},{m['to']['x']},{m['to']['y']},{rot_str}"
                )
            apply_result = await bridge.send_command_async(
                "pcb.batch_move_components",
                {"moves": "|".join(ops)},
            )
            summary["dry_run"] = False
            summary["apply_result"] = apply_result
        elif apply and not moves:
            summary["apply_result"] = {"moves_applied": 0,
                                       "reason": "no moves to apply"}

        if render_png:
            try:
                from pathlib import Path
                from ..design.illustrate import placement_png
                out = Path(render_png)
                out.parent.mkdir(parents=True, exist_ok=True)
                title = (f"PCB placement ({engine_norm})  "
                         f"HPWL {objective_report.get('hpwl', 0.0):.0f}  "
                         f"legal={objective_report.get('legal')}")
                placement_png(place_comps, result_positions, region_obj,
                              place_nets, str(out), title=title,
                              rotations=result_rotations)
                summary["preview_png"] = str(out)
            except Exception as exc:  # rendering must never break the move data
                summary["preview_error"] = str(exc)

        return summary

    @mcp.tool()
    async def pcb_copy_component_placement(
        mapping: dict[str, str],
        include_designator: bool = True,
        include_comment: bool = True,
    ) -> dict[str, Any]:
        """Clone placement from source components onto destination components.

        For each ``src -> dst`` pair, copy the source's PCB placement
        (layer, X, Y, rotation) onto the destination. With the optional
        flags also clone the designator-text placement (X/Y offset from
        component centre, rotation, size, width, layer, on/off) and the
        comment-text placement.

        Real-world use: a board with N identical channels (filter / amp /
        switching stages). Lay out channel 1 once, then call this tool
        with a mapping like ``{"R1": "R11", "C1": "C11", "U1": "U2"}``
        and channel 2 picks up channel 1's layout instantly. Saves the
        manual click-and-drag-drag-drag of replicating identical layouts.

        Mapping-driven rather than relying on sort-order matching of
        selections -- the agent-callable equivalent of a manual replicate.

        Args:
            mapping: ``{source_designator: dest_designator}``. Each
                source must already be placed somewhere; each dest gets
                overwritten with the source's placement.
            include_designator: Also copy designator-text placement
                (offset, rotation, size, layer, visibility). Default
                True.
            include_comment: Also copy comment-text placement. Default
                True.

        Returns:
            Dict with:
              - ``applied``: pairs that succeeded
              - ``failed``: pairs that failed (src/dst missing or apply
                exception)
              - ``items``: per-pair ``{src, dst, ok, error}``
        """
        if not mapping:
            return {"applied": 0, "failed": 0, "items": [],
                    "error": "mapping is empty"}
        pairs = "|".join(f"{s}={d}" for s, d in mapping.items())
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.copy_component_placement",
            {
                "mapping": pairs,
                "include_designator": "true" if include_designator else "false",
                "include_comment": "true" if include_comment else "false",
            },
        )

    @mcp.tool()
    async def pcb_replicate_layout(
        mapping: dict[str, str],
        nets: Optional[list[str]] = None,
        move_components: bool = False,
    ) -> dict[str, Any]:
        """Replicate a routed channel's ROUTING onto a matching channel.

        Where ``pcb_copy_component_placement`` only relocates components,
        this copies the source group's routing -- tracks, arcs, vias,
        polygons, regions, fills -- onto the destination group and remaps
        each copy's net from the source net to the corresponding
        destination net. The classic multi-channel "lay out channel 1,
        replicate to channels 2..N" workflow, end to end.

        Positioning: one rigid transform is derived from the FIRST mapping
        pair (the anchor) -- the copied routing is rotated by
        ``dstRot - srcRot`` about the source anchor, then translated by
        ``dstAnchor - srcAnchor``, so it lands on the destination
        components where they already sit. The destination components are
        left in place unless ``move_components=True``.

        Which routing is copied (naming-agnostic): routing on nets
        INTERNAL to the source group -- every component pad on the net
        belongs to a mapped source component. Nets that escape the group
        (a shared GND / power pour) are deliberately left alone. Pass an
        explicit ``nets`` list to override and copy exactly those.

        Preconditions: the destination components must already be placed
        congruently with the source (e.g. run
        ``pcb_copy_component_placement`` first, or pass
        ``move_components=True``). ``congruence_warnings`` in the result
        counts destination parts that do not match the anchor transform --
        their routing may not line up.

        Args:
            mapping: ``{source_designator: dest_designator}``. The first
                pair is the transform anchor; include every component in
                the channel so its internal nets are recognised.
            nets: Optional explicit source net names to copy, instead of
                the internal-net auto-detection.
            move_components: Also relocate the destination components onto
                the rigid transform, guaranteeing the routing aligns.
                Default False (destination stays where it is).

        Returns:
            Dict with:
              - ``copied``: routing primitives replicated
              - ``net_assigned``: copies given a destination net
              - ``internal_nets``: source nets selected for copying
              - ``shared_nets_skipped``: group nets left alone (escape the
                group)
              - ``congruence_warnings``: destination parts off the anchor
                transform (routing may not align)
              - ``notes``: any caveats
        """
        if not mapping:
            return {"copied": 0, "error": "mapping is empty"}
        pairs = "|".join(f"{s}={d}" for s, d in mapping.items())
        args = {"mapping": pairs}
        if nets:
            args["nets"] = "|".join(nets)
        if move_components:
            args["move_components"] = "true"
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.replicate_layout", args)

    @mcp.tool()
    async def pcb_filter_variant_components(
        variant_name: str,
        select: str = "not_fitted",
    ) -> dict[str, Any]:
        """Select a variant's components of one fitted-class on the board.

        Classifies every component under ``variant_name`` (fitted original /
        alternate / not fitted) and selects exactly the chosen class on the
        active PCB, deselecting the rest -- so, e.g., the not-fitted parts of a
        variant stand out for review or for building a component class. The
        agent-callable equivalent of the community VariantFilter script;
        selection uses the deterministic component API, not a query process.

        Args:
            variant_name: The project variant to classify against.
            select: Which class to select -- ``not_fitted`` (default),
                ``fitted_original``, ``alternate``, or ``all_fitted``
                (fitted_original + alternate).

        An unrecognised ``select`` is REJECTED rather than guessed. The
        handler's If chain falls through to not-fitted for any word it
        does not know, and the reply echoes back the word you sent, so a
        typo would select the opposite class and report success.

        Returns:
            {"variant", "select", "matched", "designators"}, or
            ``{"ok": False, "reason": ...}`` if ``select`` is not one of
            the four classes, in which case nothing is sent or selected.
        """
        if select not in FILTER_VARIANT_SELECT:
            return {
                "ok": False,
                "reason": (
                    f"select must be one of "
                    f"{', '.join(FILTER_VARIANT_SELECT)}, not "
                    f"{select!r}. Unknown values are not rejected by the "
                    "board handler: they fall through to not_fitted, and "
                    "the reply echoes the word you sent, so a typo would "
                    "select the opposite class and look like it worked."
                ),
            }
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.filter_variant_components",
            {"variant_name": variant_name, "select": select},
        )

    @mcp.tool()
    async def pcb_renumber_pads(
        order: str = "lr_tb",
        start: int = 1,
        increment: int = 1,
        prefix: str = "",
    ) -> dict[str, Any]:
        """Renumber the current PcbLib footprint's pads in spatial order.

        The non-interactive form of the community RenumberPads tool: instead
        of clicking each pad in order, the footprint's pads are sorted by
        position and assigned sequential designators. Rows/columns are banded
        by a small tolerance so a grid numbers cleanly. Operates on the active
        PCB library's current footprint.

        Args:
            order: ``lr_tb`` (default) numbers rows top-to-bottom,
                left-to-right within a row; ``tb_lr`` numbers columns
                left-to-right, top-to-bottom within a column.
            start: First designator number (default 1).
            increment: Step between successive pads (default 1).
            prefix: Optional string prefixed to each number (e.g. ``A`` ->
                ``A1``, ``A2``).

        Returns:
            {"renumbered", "order", "mapping": [{old, new}, ...]} or an error.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.renumber_pads",
            {
                "order": order,
                "start": str(start),
                "increment": str(increment),
                "prefix": prefix,
            },
        )

    @mcp.tool()
    async def pcb_copy_tracks_radial(
        center_x: int,
        center_y: int,
        count: int,
        angle_step: Optional[float] = None,
    ) -> dict[str, Any]:
        """Array the selected tracks/arcs/vias radially about a center point.

        Replicates the current selection ``count - 1`` more times, each rotated
        a further ``angle_step`` degrees about ``(center_x, center_y)`` -- a
        circular/radial copy. Reuses the same verified Replicate +
        RotateAroundXY transform as ``pcb_replicate_layout``. Copies are added
        unselected so the source set stays put.

        Args:
            center_x: Rotation-center X in mils.
            center_y: Rotation-center Y in mils.
            count: Total instances including the original (>= 2).
            angle_step: Degrees between instances. Defaults to ``360 / count``
                (a full even ring).

        Returns:
            {"copied", "count", "angle_step"} or an error.
        """
        bridge = get_bridge()
        args: dict[str, Any] = {
            "center_x": str(center_x),
            "center_y": str(center_y),
            "count": str(count),
        }
        if angle_step is not None:
            args["angle_step"] = str(angle_step)
        return await bridge.send_command_async("pcb.copy_tracks_radial", args)

    @mcp.tool()
    async def pcb_scale(
        ratio: float,
        anchor: str = "selection_center",
    ) -> dict[str, Any]:
        """Scale the selected free primitives by a ratio about an anchor.

        Each coordinate maps ``P' = anchor + ratio*(P - anchor)`` and sizes
        scale by ``ratio``. Handles free tracks, arcs, vias, pads, fills and
        text. Primitives inside a component, dimension, or polygon are skipped
        (as are polygons/regions) -- so this scales free copper and artwork,
        not footprints. Grounded in the community PCBScale tool.

        Args:
            ratio: Scale factor (> 0). ``0.95`` shrinks 5%, ``1.05`` grows 5%.
            anchor: ``selection_center`` (default), ``board_center``, or
                ``origin``.

        Returns:
            {"scaled", "skipped", "ratio", "anchor_x", "anchor_y"} or an error.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.scale", {"ratio": str(ratio), "anchor": anchor}
        )

    @mcp.tool()
    async def pcb_place_stitching_vias(
        net: str,
        x1_mils: int,
        y1_mils: int,
        x2_mils: int,
        y2_mils: int,
        spacing_mils: int = 50,
        via_size_mils: int = 30,
        via_hole_mils: int = 14,
        clearance_mils: int = 10,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Place a grid of stitching vias on a named net within a rectangle.

        Standard RF / EMC tool: place GND stitch vias around high-speed
        traces so the return current has a low-inductance path between
        reference planes. Also used near connectors / clock generators
        / EMI-sensitive areas.

        Algorithm: walk a regular grid inside the rectangle; for each
        gridpoint, spatial-iterate within ``via_size_mils/2 +
        clearance_mils``. Skip the gridpoint if any non-same-net
        primitive (pad / via / track / arc) is in range. Otherwise
        create a top→bottom through-via on the target net.

        **The default is dry-run** because mutating a board with N×M
        new vias is risky. Confirm the ``placed`` / ``skipped`` count
        looks right, then call again with ``dry_run=False``.

        Args:
            net: Target net (must already exist on the board).
            x1_mils, y1_mils, x2_mils, y2_mils: Inclusive rectangle
                where the grid is placed. PCB origin is bottom-left
                by Altium convention.
            spacing_mils: Grid spacing (default 50). For GND stitching
                near a high-speed bus, /4 of the wavelength of the
                highest harmonic is a common rule of thumb.
            via_size_mils: Via pad diameter (default 30).
            via_hole_mils: Via drill diameter (default 14).
            clearance_mils: Min gap to any non-same-net existing
                primitive (default 10).
            dry_run: When True (default), only count would-be
                placements without mutating the board.

        Returns:
            Dict with ``{net, dry_run, placed, skipped, spacing_mils,
            clearance_mils}``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.place_stitching_vias",
            {
                "net": net,
                "x1_mils": str(round(x1_mils)),
                "y1_mils": str(round(y1_mils)),
                "x2_mils": str(round(x2_mils)),
                "y2_mils": str(round(y2_mils)),
                "spacing_mils": str(round(spacing_mils)),
                "via_size_mils": str(round(via_size_mils)),
                "via_hole_mils": str(round(via_hole_mils)),
                "clearance_mils": str(round(clearance_mils)),
                "dry_run": "true" if dry_run else "false",
            },
            timeout=60.0,
        )

    @mcp.tool()
    async def pcb_calc_impedance(
        geometry: str,
        width_mils: float,
        dielectric_height_mils: float,
        dielectric_constant: float = 4.2,
        copper_oz: float = 1.0,
        spacing_mils: float = 0.0,
    ) -> dict[str, Any]:
        """Characteristic impedance of a PCB trace via IPC-2141 / Wadell
        closed-form approximations. Pure math, no Altium hit.

        Use this BEFORE routing a high-speed signal to pick the track
        width that matches your transceiver's target impedance (USB
        90 Ω diff, HDMI 100 Ω diff, single-ended PCIe 50 Ω, MIPI 100 Ω
        diff, etc). For controlled-impedance fab spec sheets, the
        accuracy of these closed-form formulas is ± ~10% -- good
        enough for picking widths but fab houses will refine the
        actual stackup. For real controlled-impedance boards, send
        the stackup to your fab and use their Polar Si9000 / similar
        report values.

        Chaining with the layer stack: call ``pcb_get_layer_stackup``
        first to read the actual dielectric heights and εr values
        from the project, then feed those into this calculator. This
        is essential -- guessing FR-4 at εr=4.2 ignores the stackup
        the user actually picked and can give a 15-20% off answer
        if they're using Megtron 6 or Rogers.

        Supported geometries:
          - ``"microstrip"`` -- single-ended outer-layer track with
            reference plane below. IPC-2141 microstrip formula.
          - ``"microstrip_diff"`` -- differential pair on outer
            layer. Adds Wadell's mutual-coupling correction.
          - ``"stripline"`` -- single-ended inner-layer track between
            two reference planes.
          - ``"stripline_diff"`` -- differential pair on an inner
            layer between two reference planes.

        Formula sources:
          - IPC-2141 microstrip:
            Z₀ = (87 / √(εr + 1.41)) × ln(5.98 × h / (0.8 × w + t))
          - Symmetric stripline:
            Z₀ = (60 / √εr) × ln(4 × b / (0.67 × π × (0.8 × w + t)))
          - Differential variants apply the standard Wadell
            correction: Zdiff ≈ 2 × Z₀ × (1 - 0.48 × exp(-0.96 × s/h))
            for microstrip, similarly with empirical coefficients
            for stripline.

        Args:
            geometry: One of ``"microstrip"``, ``"microstrip_diff"``,
                ``"stripline"``, ``"stripline_diff"``.
            width_mils: Track width in mils. For differential, this
                is the width of EACH conductor (not the pair).
            dielectric_height_mils: For microstrip, the dielectric
                thickness BELOW the trace (track-to-reference-plane).
                For stripline, this is the TOTAL dielectric thickness
                between the two reference planes (``b`` in IPC).
            dielectric_constant: εr of the dielectric. Common: 4.2
                (FR-4 at 1 GHz), 3.5 (Megtron 6), 3.0 (Rogers 4350B),
                2.2 (PTFE). Default 4.2.
            copper_oz: Copper weight in oz/ft² (used for ``t``,
                trace thickness). 1 oz = 1.378 mils. Default 1.0.
            spacing_mils: Edge-to-edge gap between conductors of a
                differential pair, in mils. Ignored for single-ended.

        Returns:
            Dict with:
              - ``geometry``, ``width_mils``, ``dielectric_height_mils``,
                ``dielectric_constant``, ``thickness_mils``,
                ``spacing_mils`` (when diff)
              - ``z0_ohms``: single-ended characteristic impedance
              - ``zdiff_ohms`` (diff geometries only): differential
                pair impedance
              - ``propagation_delay_ps_per_inch``: propagation delay,
                derived from the effective εr (microstrip uses
                εr_eff = (εr + 1)/2; stripline uses εr directly).
        """
        import math
        geometry = geometry.strip().lower()
        valid = ("microstrip", "microstrip_diff",
                 "stripline", "stripline_diff")
        if geometry not in valid:
            return {"ok": False,
                    "reason": "geometry must be one of " + ", ".join(valid)}
        if width_mils <= 0 or dielectric_height_mils <= 0:
            return {"ok": False,
                    "reason": "width_mils and dielectric_height_mils must be > 0"}
        if dielectric_constant <= 0:
            return {"ok": False,
                    "reason": "dielectric_constant must be > 0"}
        is_diff = geometry.endswith("_diff")
        if is_diff and spacing_mils <= 0:
            return {"ok": False,
                    "reason": "spacing_mils must be > 0 for differential geometries"}

        # The two forward formulas live in design/impedance_sizing.py,
        # where trace_width_for_impedance already inverts them. They
        # were inlined here as well, character for character, so the
        # IPC-2141 constants were stated twice with nothing enforcing
        # agreement: the same defect as the mils-to-mm factor in task
        # #43, and the reason calc.py can now offer this tool without
        # becoming a third copy.
        from ..design.impedance_sizing import z0_microstrip, z0_stripline
        from ..units import OZ_TO_MILS

        w = float(width_mils)
        h = float(dielectric_height_mils)
        er = float(dielectric_constant)
        t = copper_oz * OZ_TO_MILS          # copper thickness, mils

        if geometry.startswith("microstrip"):
            z0 = z0_microstrip(w, h, er, copper_oz=copper_oz)
            er_eff = (er + 1.0) / 2.0
        else:
            # Symmetric stripline (trace centered between two planes,
            # h is the FULL dielectric thickness between planes).
            z0 = z0_stripline(w, h, er, copper_oz=copper_oz)
            er_eff = er

        # The same validity check the other copy of this tool applies.
        #
        # Shared rather than restated for the reason the constants above
        # are shared: this tool exists twice, here for Altium and in
        # tools/calc.py for KiCad and EasyEDA. Guarding only one of them
        # left this one still returning a NEGATIVE impedance as a
        # success, which is precisely the drift the comment above warns
        # about.
        from ..design.impedance_sizing import impedance_validity

        checked = impedance_validity(z0, w, h, er)
        if not checked["usable"]:
            return {
                "ok": False,
                "reason": checked["reason"],
                "geometry": geometry,
                "width_mils": w,
                "dielectric_height_mils": h,
                "width_to_height_ratio": checked["width_to_height_ratio"],
            }

        # Propagation delay: c0 = 11.8 in/ns in vacuum;
        # tpd = sqrt(er_eff) / c0 = sqrt(er_eff) * 1000 / 11.8 ps/in
        tpd = math.sqrt(er_eff) * 1000.0 / 11.8

        out: dict[str, Any] = {
            "ok": True,
            "geometry": geometry,
            "width_mils": w,
            "dielectric_height_mils": h,
            "dielectric_constant": er,
            "thickness_mils": round(t, 3),
            "z0_ohms": round(z0, 1),
            "propagation_delay_ps_per_inch": round(tpd, 2),
            "width_to_height_ratio": checked["width_to_height_ratio"],
        }
        if checked["outside_validity_range"]:
            out["outside_validity_range"] = True
            out["accuracy_warning"] = checked["warning"]
        if is_diff:
            # Same reasoning as the Z0 formulas above: the coupling
            # factor was stated here AND in impedance_sizing, where the
            # inverse solver uses it. Two copies of a Wadell
            # approximation is two chances to edit one of them.
            from ..design.impedance_sizing import diff_coupling_factor

            s = float(spacing_mils)
            out["spacing_mils"] = s
            out["zdiff_ohms"] = round(
                2.0 * z0 * diff_coupling_factor(geometry, s, h), 1)
        return out

    @mcp.tool()
    async def pcb_calc_trace_width_for_impedance(
        target_ohms: float,
        geometry: str,
        dielectric_height_mils: float,
        dielectric_constant: float = 4.2,
        copper_oz: float = 1.0,
        spacing_mils: float = 0.0,
    ) -> dict[str, Any]:
        """Trace WIDTH to hit a target impedance (inverse of pcb_calc_impedance).

        The design-time complement: instead of "what impedance does this width
        give?", it answers "I need 90 ohm USB / 100 ohm HDMI differential, or
        50 ohm single-ended; how wide?". Inverts the same IPC-2141 / Wadell
        closed forms, so it round-trips with the forward calc. Pure math, no
        Altium. Same +/-10 % caveat -- a fab field solver refines the stackup;
        read the real dielectric heights / er with ``pcb_get_layer_stackup``.

        Args:
            target_ohms: Single-ended Z0 for ``microstrip``/``stripline``, or
                the differential Zdiff for ``microstrip_diff``/
                ``stripline_diff``.
            geometry: One of microstrip, microstrip_diff, stripline,
                stripline_diff.
            dielectric_height_mils: Trace-to-plane height (microstrip) or full
                between-plane thickness (stripline).
            dielectric_constant: er (default 4.2 FR-4).
            copper_oz: Copper weight for trace thickness (default 1.0).
            spacing_mils: Edge-to-edge gap (required for ``*_diff``).

        Returns:
            ``{"ok": True, "width_mils", "single_ended_z0_ohms", "feasible",
            ...}`` (feasible False when the target needs a width <= 0: raise the
            dielectric height or lower the target), or ``{"ok": False, ...}``.
        """
        from ..design.impedance_sizing import trace_width_for_impedance
        try:
            r = trace_width_for_impedance(
                target_ohms, geometry, dielectric_height_mils,
                dielectric_constant=dielectric_constant, copper_oz=copper_oz,
                spacing_mils=spacing_mils)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        out: dict[str, Any] = {
            "ok": True,
            "geometry": r.geometry,
            "target_ohms": target_ohms,
            "feasible": r.feasible,
            "width_mils": round(r.width_mils, 2),
            "single_ended_z0_ohms": round(r.single_ended_z0_ohms, 1),
            "dielectric_height_mils": dielectric_height_mils,
            "dielectric_constant": dielectric_constant,
        }
        if r.geometry.endswith("_diff"):
            out["spacing_mils"] = spacing_mils
        out["summary"] = (
            f"{target_ohms:g} ohm {r.geometry}: "
            + (f"width {r.width_mils:.2f} mil" if r.feasible else
               "infeasible (target too high for this stackup)"))
        return out

    @mcp.tool()
    async def pcb_calc_termination(
        length_mils: float,
        rise_time_ns: float,
        z0_ohms: Optional[float] = None,
        dielectric_constant: Optional[float] = None,
        geometry: str = "microstrip",
        driver_impedance_ohms: Optional[float] = None,
        vcc: float = 0.0,
        width_mils: float = 0.0,
        dielectric_height_mils: Optional[float] = None,
        length_fraction: float = 1.0 / 6.0,
        multi_load: bool = False,
        z0: Optional[float] = None,
        er: Optional[float] = None,
        driver_impedance: Optional[float] = None,
        height_mils: Optional[float] = None,
    ) -> dict[str, Any]:
        """Decide if a net needs termination and size the resistor(s).

        Two questions the impedance calc does not answer: is this net
        electrically long for its edge rate, and -- if so -- what termination
        value? A net stays a lumped wire while its one-way flight time is under
        ``length_fraction`` of the rise time (Johnson & Graham's 1/6 rule by
        default; pass 1/2 for the looser convention); past the critical length
        the reflection arrives during the edge and the net must be matched to
        ``z0_ohms``. Pure math, no Altium. Propagation speed uses the effective
        dielectric constant (Er for stripline; reduced for microstrip, Hammerstad
        when ``width_mils``/``dielectric_height_mils`` are given, else the
        0.475*Er+0.67 approximation).

        Args:
            length_mils: Routed (or estimated) net length.
            rise_time_ns: Signal edge rate (the driver's 10-90 % rise time).
            z0_ohms: Line characteristic impedance (size it with
                ``pcb_calc_impedance``; default 50).
            dielectric_constant: er (default 4.2 FR-4).
            geometry: ``microstrip`` or ``stripline``.
            driver_impedance_ohms: Driver output impedance, subtracted for the
                series value (``Rs = Z0 - Rdriver``).
            vcc: Supply rail; enables the Thevenin split option when > 0.
            width_mils, dielectric_height_mils: Microstrip geometry for the
                Hammerstad Er_eff (optional; falls back to the approximation).
            length_fraction: Electrically-long threshold (default 1/6).
            multi_load: True for a bus / multi-receiver net (recommends
                far-end Thevenin/parallel instead of source series).

        Returns:
            ``{"ok": True, "needs_termination", "recommended", "critical_length_mils",
            "flight_time_ns", "options": {...}, "summary"}`` -- ``options`` carries
            the ideal and nearest-E24 resistor values (and the AC cap) for every
            applicable scheme.
        """
        from ..design.signal_integrity import recommend_termination
        try:
            # The terse spellings are what the EasyEDA and KiCad builds
            # take, so accepting both here means one call works on any
            # backend.
            #
            # Preferring the alias unconditionally was the first attempt
            # and it is wrong: a caller passing z0_ohms=75 and z0=50
            # would have silently been given 50 and told it succeeded.
            # These name ONE quantity, so disagreement is a mistake in
            # the call and the only safe answer is to say so.
            # The defaults moved OUT of the signature and into
            # _TERMINATION_DEFAULTS so that "not supplied" is
            # distinguishable from "supplied, and happens to equal the
            # default". Detecting the conflict any other way needs a
            # comparison against the default value, and that silently
            # misses the case where the caller passed exactly it: the
            # first version of this passed every test except the one
            # that supplied dielectric_constant=4.2 alongside a
            # disagreeing er, which is precisely the call a client
            # written for another backend makes. Behaviour is unchanged
            # for every caller; only the advertised default moves into
            # the docstring.
            resolved = {}
            for terse, terse_name, verbose, verbose_name in (
                    (z0, "z0", z0_ohms, "z0_ohms"),
                    (er, "er", dielectric_constant, "dielectric_constant"),
                    (driver_impedance, "driver_impedance",
                     driver_impedance_ohms, "driver_impedance_ohms"),
                    (height_mils, "height_mils",
                     dielectric_height_mils, "dielectric_height_mils")):
                if (terse is not None and verbose is not None
                        and terse != verbose):
                    return {"ok": False, "reason": (
                        f"{verbose_name}={verbose} and {terse_name}="
                        f"{terse} were both given and disagree; they are "
                        f"the same quantity, so pass one")}
                given = verbose if verbose is not None else terse
                resolved[verbose_name] = (
                    given if given is not None
                    else _TERMINATION_DEFAULTS[verbose_name])
            z0_ohms = resolved["z0_ohms"]
            dielectric_constant = resolved["dielectric_constant"]
            driver_impedance_ohms = resolved["driver_impedance_ohms"]
            dielectric_height_mils = resolved["dielectric_height_mils"]
            adv = recommend_termination(
                length_mils, rise_time_ns, z0_ohms, dielectric_constant,
                geometry=geometry,
                driver_impedance=driver_impedance_ohms or None,
                vcc=vcc or None,
                width_mils=width_mils or None,
                height_mils=dielectric_height_mils or None,
                fraction=length_fraction, multi_load=multi_load)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        options: dict[str, Any] = {}
        if adv.series is not None:
            options["series"] = {
                "r_ohms": round(adv.series.r_series, 2),
                "r_e24": adv.series.r_series_e24}
        if adv.parallel is not None:
            options["parallel"] = {
                "r_ohms": round(adv.parallel.r_parallel, 2),
                "r_e24": adv.parallel.r_parallel_e24}
        if adv.thevenin is not None:
            options["thevenin"] = {
                "r_pullup_ohms": round(adv.thevenin.r_pullup, 2),
                "r_pulldown_ohms": round(adv.thevenin.r_pulldown, 2),
                "r_pullup_e24": adv.thevenin.r_pullup_e24,
                "r_pulldown_e24": adv.thevenin.r_pulldown_e24,
                "v_bias": round(adv.thevenin.v_bias, 3),
                "static_power_w": round(adv.thevenin.static_power_w, 4)}
        if adv.ac is not None:
            options["ac"] = {
                "r_ohms": round(adv.ac.r_parallel, 2),
                "r_e24": adv.ac.r_parallel_e24,
                "capacitance_pf": round(adv.ac.capacitance_f * 1e12, 1)}
        return {
            "ok": True,
            "needs_termination": adv.needs_termination,
            "recommended": adv.recommended,
            "electrically_long": adv.electrical.electrically_long,
            "critical_length_mils": round(adv.electrical.critical_length_mils, 1),
            "flight_time_ns": round(adv.electrical.flight_time_ns, 4),
            "delay_ratio": round(adv.electrical.delay_ratio, 2),
            "options": options,
            "summary": adv.note,
        }

    @mcp.tool()
    async def pcb_calc_length_match(
        lengths: Optional[dict[str, float]] = None,
        skew_budget_ps: float = 0.0,
        rise_time_ns: float = 0.0,
        match_fraction: float = 0.1,
        dielectric_constant: float = 4.2,
        geometry: str = "stripline",
        width_mils: float = 0.0,
        dielectric_height_mils: float = 0.0,
    ) -> dict[str, Any]:
        """Length-match tolerance and serpentine compensation for a bus / pair.

        Turns a timing budget into the length-match window the routing must hold,
        and -- given the routed lengths -- the serpentine copper each net needs.
        The complement of Altium's ``pcb_tune_length`` (which executes the
        meander) and ``pcb_get_trace_lengths`` (which reads lengths back). Pure
        math, no Altium. Rests on the ns/in == ps/mil identity, so skew maps to
        length exactly; propagation speed uses the effective dielectric constant
        (stripline = Er, the usual inner-layer bus case; microstrip reduced).

        The skew budget is taken from ``skew_budget_ps`` if > 0, else from
        ``match_fraction * rise_time_ns`` (the "match to within 10-20 % of the
        edge" rule).

        Args:
            lengths: Optional ``{net: length_mils}`` of routed lengths; when
                given, returns a per-net match report targeting the longest net.
            skew_budget_ps: Allowed skew in ps (e.g. a DDR byte-lane budget).
            rise_time_ns: Edge rate, used with ``match_fraction`` if no ps budget.
            match_fraction: Fraction of the rise time allowed as skew (default 0.1).
            dielectric_constant: er (default 4.2 FR-4).
            geometry: ``stripline`` (default) or ``microstrip``.
            width_mils, dielectric_height_mils: Microstrip geometry for the
                Hammerstad Er_eff (optional).

        Returns:
            ``{"ok": True, "er_eff", "t_pd_ps_per_mil", "skew_budget_ps",
            "tolerance_mils", "members"?: [{net, length_mils, mismatch_mils,
            skew_ps, compensation_mils, within_tolerance}], "target_length_mils"?,
            "worst_skew_ps"?, "all_matched"?, "summary"}``.
        """
        from ..design.length_matching import assess_length_match
        try:
            res = assess_length_match(
                dielectric_constant=dielectric_constant, geometry=geometry,
                width_mils=width_mils or None,
                dielectric_height_mils=dielectric_height_mils or None,
                skew_budget_ps=skew_budget_ps or None,
                rise_time_ns=rise_time_ns or None,
                match_fraction=match_fraction,
                lengths=lengths or None)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        tol = res["tolerance_mils"]
        out: dict[str, Any] = {
            "ok": True,
            "er_eff": round(res["er_eff"], 3),
            "t_pd_ps_per_mil": round(res["t_pd_ps_per_mil"], 4),
            "skew_budget_ps": res["skew_budget_ps"],
            "tolerance_mils": round(tol, 2) if tol is not None else None,
            # Which geometry produced the delay. A stripline uses er
            # directly and a microstrip an effective er near half of it,
            # so the two differ by roughly a quarter on every length
            # converted here. This defaults to stripline while
            # pcb_calc_termination defaults to microstrip, and neither
            # reply used to say so.
            "geometry": geometry,
            "dielectric_constant": dielectric_constant,
            "geometry_note": (
                f"delay computed for {geometry}. A stripline uses er "
                f"directly and a microstrip uses an effective er near "
                f"half of it, so the two differ by roughly a quarter. "
                f"Pass geometry explicitly if this board is not "
                f"{geometry}."),
        }
        rep = res.get("report")
        if rep is not None:
            out["target_length_mils"] = rep.target_length_mils
            out["worst_skew_ps"] = round(rep.worst_skew_ps, 3)
            out["all_matched"] = rep.all_matched
            out["members"] = [
                {"net": m.name, "length_mils": m.length_mils,
                 "mismatch_mils": round(m.mismatch_mils, 2),
                 "skew_ps": round(m.skew_ps, 3),
                 "compensation_mils": round(m.compensation_mils, 2),
                 "within_tolerance": m.within_tolerance}
                for m in rep.members]
            n_bad = sum(1 for m in rep.members if not m.within_tolerance)
            out["summary"] = (
                f"target {rep.target_length_mils:.0f} mils, worst skew "
                f"{rep.worst_skew_ps:.1f} ps"
                + (f", {n_bad} net(s) over the {tol:.0f}-mil window"
                   if tol is not None and n_bad else
                   (", all matched" if tol is not None else "")))
        else:
            out["summary"] = (
                f"match window {tol:.1f} mils for a {res['skew_budget_ps']:.1f} ps "
                f"budget on {geometry}" if tol is not None else
                f"no budget given ({geometry}, {res['t_pd_ps_per_mil']:.3f} ps/mil)")
        return out

    @mcp.tool()
    async def pcb_calc_thermal_vias(
        drill_mm: float,
        board_thickness_mm: Optional[float] = None,
        length_mm: Optional[float] = None,
        plating_um: float = 25.0,
        filled_copper: bool = False,
        k_cu: float = 385.0,
        power_w: float = 0.0,
        delta_t_c: float = 0.0,
        target_k_per_w: float = 0.0,
        via_count: int = 0,
    ) -> dict[str, Any]:
        """Size a thermal-via field under a power pad (Fourier conduction).

        The thermal calcs answer "what junction-to-ambient resistance does the
        part need" (``required_theta_ja``); this answers "how many vias get the
        heat into the board". A plated via is a copper tube of axial resistance
        ``R = L / (k * A)``; vias conduct in parallel (``R_array = R/N``). Give a
        target -- ``target_k_per_w`` directly, or ``power_w`` with a ``delta_t_c``
        budget (target = dT/P) -- to solve the count, or an explicit
        ``via_count`` to score a layout. Pure math, no Altium. Models the via
        field's conduction bottleneck and assumes good copper spreading on the
        pad and the receiving plane (a thin-copper spreading term is left to a
        field solver).

        Args:
            drill_mm: Finished via drill diameter.
            board_thickness_mm: Conduction length -- top layer to the heat- The EasyEDA and KiCad builds
                spell this ``length_mm``; either is accepted.
            length_mm: Alias for ``board_thickness_mm``.
                spreading plane (full thickness for a bottom plane).
            plating_um: Barrel plating thickness (default 25 = 1 oz).
            filled_copper: True for a copper-filled via (full-circle copper),
                False for an unfilled / resin-filled barrel (annulus only).
            k_cu: Copper thermal conductivity, W/(m.K) (default 385).
            power_w: Dissipation, for the target (with ``delta_t_c``) and the
                realized temperature rise.
            delta_t_c: Allowed rise across the via field.
            target_k_per_w: Explicit target array resistance.
            via_count: Score this exact count instead of solving one.

        Returns:
            ``{"ok": True, "via_count", "single_via_k_per_w", "array_k_per_w",
            "target_k_per_w", "temp_rise_c", "barrel_area_mm2", "summary"}``.
        """
        from ..design.thermal_vias import assess_thermal_vias
        from .calc import _either
        try:
            board_thickness_mm = _either(
                "board_thickness_mm", board_thickness_mm,
                "length_mm", length_mm)
            rep = assess_thermal_vias(
                drill_mm, plating_um, board_thickness_mm,
                filled_copper=filled_copper, k_cu=k_cu,
                power_w=power_w or None, delta_t_c=delta_t_c or None,
                target_k_per_w=target_k_per_w or None,
                via_count=via_count or None)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        rise = (f", {rep.temp_rise_c:.1f} C rise"
                if rep.temp_rise_c is not None else "")
        return {
            "ok": True,
            "via_count": rep.via_count,
            "single_via_k_per_w": round(rep.single_via_k_per_w, 1),
            "array_k_per_w": round(rep.array_k_per_w, 2),
            "target_k_per_w": (round(rep.target_k_per_w, 2)
                               if rep.target_k_per_w is not None else None),
            "temp_rise_c": (round(rep.temp_rise_c, 2)
                            if rep.temp_rise_c is not None else None),
            "barrel_area_mm2": round(rep.barrel_area_mm2, 4),
            "summary": (
                f"{rep.via_count} via(s) -> {rep.array_k_per_w:.1f} K/W"
                f" ({rep.single_via_k_per_w:.0f} K/W each){rise}"),
        }

    @mcp.tool()
    async def pcb_calc_track_current_capacity(
        width_mils: float,
        copper_oz: float = 1.0,
        layer: str = "external",
        length_mils: float = 0.0,
    ) -> dict[str, Any]:
        """IPC-2221 current-carrying capacity for a PCB track.

        Pure math, no Altium hit -- safe to call without an attached
        session. Returns the current the track can sustain at each
        of several temperature rises (1, 5, 10, 20, 30 °C above
        ambient), plus DC resistance if a length is supplied.

        Formula (IPC-2221, also IPC-2152's curve-fit predecessor):
          ``I = k × (ΔT)^0.44 × (h × w)^0.725``
        where ``h`` and ``w`` are in mils, ``ΔT`` in °C, and:
          ``k = 0.048`` for external layers (top, bottom)
          ``k = 0.024`` for internal layers (mid)
        Internal-layer derating is the dominant correction; running a
        signal that draws 5 A on TopLayer might be fine but on an
        inner plane the same width only handles ~3 A at the same
        ΔT because heat dissipates more slowly.

        Copper thickness conversion: 1 oz/ft² ≈ 1.378 mils. So
        ``copper_oz=1.0`` → ``h = 1.378 mils``, ``copper_oz=2.0`` →
        ``h = 2.756 mils``.

        Resistance: ``R = ρ × L / (h × w)`` with ρ = 6.7 × 10⁻⁷ Ω-mil
        (annealed copper, 25 °C). Multiply current × resistance to
        get voltage drop across the track.

        Exposes the track-current math so the agent can apply it
        across an entire power net after pulling track widths from
        ``pcb_get_trace_lengths``.

        Args:
            width_mils: Track width in mils.
            copper_oz: Copper weight in oz/ft². Common values:
                0.5 oz (HDI inner), 1.0 oz (standard), 2.0 oz (power
                planes), 3.0 oz (heavy-current).
            layer: ``"external"`` (top / bottom) or ``"internal"``
                (mid / inner planes). Defaults to external.
            length_mils: If > 0, also returns DC resistance and
                voltage drop at each temperature rise.

        Returns:
            Dict with:
              - ``width_mils``, ``copper_oz``, ``thickness_mils``, ``layer``
              - ``current_amps`` (dict, keys ``1c``, ``5c``, ``10c``,
                ``20c``, ``30c``)
              - ``resistance_mohm`` (only if length_mils > 0)
              - ``voltage_drop_mv`` (only if length_mils > 0, dict
                keyed same as ``current_amps``)
        """
        if width_mils <= 0:
            return {"ok": False, "reason": "width_mils must be > 0"}
        if copper_oz <= 0:
            return {"ok": False, "reason": "copper_oz must be > 0"}
        layer_norm = layer.strip().lower()
        if layer_norm not in ("external", "internal"):
            return {"ok": False,
                    "reason": "layer must be 'external' or 'internal'"}

        from ..units import OZ_TO_MILS

        thickness_mils = copper_oz * OZ_TO_MILS
        k = 0.024 if layer_norm == "internal" else 0.048
        b = 0.44
        c = 0.725
        hw_c = (thickness_mils * width_mils) ** c

        rises = (1, 5, 10, 20, 30)
        current = {f"{t}c": round(k * (t ** b) * hw_c, 3) for t in rises}
        out: dict[str, Any] = {
            "ok": True,
            "width_mils": width_mils,
            "copper_oz": copper_oz,
            "thickness_mils": round(thickness_mils, 3),
            "layer": layer_norm,
            "current_amps": current,
        }
        if length_mils > 0:
            rho = 6.7e-7
            r_ohm = rho * length_mils / (thickness_mils * width_mils)
            out["resistance_mohm"] = round(r_ohm * 1000.0, 4)
            out["voltage_drop_mv"] = {
                k_: round(current[k_] * r_ohm * 1000.0, 3) for k_ in current
            }
        return out

    @mcp.tool()
    async def pcb_calc_trace_width_for_current(
        current_amps: Optional[float] = None,
        copper_oz: float = 1.0,
        delta_t_c: float = 10.0,
        layer: str = "external",
        margin: float = 0.2,
        length_mils: float = 0.0,
        current_a: Optional[float] = None,
    ) -> dict[str, Any]:
        """Minimum track WIDTH to carry a target current (inverse IPC-2221).

        The design-time complement of ``pcb_calc_track_current_capacity``:
        instead of "what current does this width carry?", it answers "I need N
        amps, how wide?". Pure math, no Altium hit.

        Solves ``I = k * dT^0.44 * (h*w)^0.725`` for ``w`` with the same
        constants as the forward tool (so they round-trip), ``k = 0.048``
        external / ``0.024`` internal, ``h = copper_oz * 1.378 mils``.

        Args:
            current_amps: Target current the track must carry. The
                EasyEDA and KiCad builds spell this ``current_a``;
                either is accepted here, so one call works on any
                backend.
            current_a: Alias for ``current_amps``.
            copper_oz: Copper weight, oz/ft^2 (0.5 / 1.0 / 2.0 / 3.0).
            delta_t_c: Allowed temperature rise above ambient (10 degC is a
                common conservative budget; 20-30 degC for tight boards).
            layer: ``"external"`` (top/bottom) or ``"internal"`` (inner; needs
                ~2.6x the width for the same rise).
            margin: Fractional widening above the bare IPC minimum for the
                recommendation (default 0.2 = 20 %).
            length_mils: If > 0, also returns DC resistance and voltage drop at
                the recommended width.

        Returns:
            ``{"ok": True, "min_width_mils", "recommended_width_mils",
            "copper_oz", "delta_t_c", "layer", ...optional resistance/drop}``
            or ``{"ok": False, "reason": ...}``.
        """
        from ..design.trace_sizing import trace_width_for_current
        from .calc import _either
        try:
            current_amps = _either("current_amps", current_amps,
                                   "current_a", current_a)
            r = trace_width_for_current(
                current_amps, copper_oz=copper_oz, delta_t_c=delta_t_c,
                layer=layer, margin=margin, length_mils=length_mils)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        out: dict[str, Any] = {
            "ok": True,
            "current_amps": current_amps,
            "min_width_mils": round(r.min_width_mils, 3),
            "recommended_width_mils": r.recommended_width_mils,
            "copper_oz": copper_oz,
            "delta_t_c": delta_t_c,
            "layer": r.layer,
            "summary": (
                f"{current_amps:g} A at {delta_t_c:g} degC rise ({r.layer}, "
                f"{copper_oz:g} oz): min {r.min_width_mils:.1f} mil, "
                f"use {r.recommended_width_mils:g} mil"),
        }
        if r.resistance_mohm is not None:
            out["resistance_mohm"] = round(r.resistance_mohm, 4)
            out["voltage_drop_mv"] = round(r.voltage_drop_mv, 3)
        return out

    @mcp.tool()
    async def pcb_add_testpoints_for_net_class(
        net_class: str,
        type: str = "smd",
        pad_size_mils: int = 40,
        hole_size_mils: int = 20,
        fab_top: bool = False,
        fab_bottom: bool = False,
        assy_top: bool = True,
        assy_bottom: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """For each net in a netclass that does NOT already have a
        testpoint, place a new pad above the board outline with the
        net assigned and the standard ``IsTestpoint`` /
        ``IsAssyTestpoint`` flags set.

        Typical workflow:
          1. Set up a netclass in Altium called ``TestPoints`` and
             add the signals you want probed (clocks, power rails,
             reset, debug signals).
          2. Call this tool with ``net_class="TestPoints"`` and the
             pad geometry your bed-of-nails fixture requires.
          3. Open the PCB; the new pads sit in a row above the
             board, with the right net + testpoint flags already set.
          4. Drag them into their final positions (or run
             ``pcb_move_components`` if you've pre-computed where
             they go).

        Coverage detection: a net is treated as "already covered" if
        any pad or via on it carries ANY of the four testpoint flags
        (so DFM tools, fab-side bed-of-nails vendors, and assembly-
        side probe vendors all see the same coverage). Pass
        ``force=True`` to override and place anyway.

        Agent-friendly: no GUI, results returned for follow-up.

        Args:
            net_class: Netclass name to scan.
            type: ``"smd"`` (top-layer SMD pad, no hole) or
                ``"through_hole"`` (multi-layer pad with drill).
            pad_size_mils: Outer pad size in mils. Default 40.
            hole_size_mils: Drill diameter, only used for through-hole
                testpoints. Default 20.
            fab_top: Set ``IsTestpoint_Top`` (top-side bed-of-nails).
            fab_bottom: Set ``IsTestpoint_Bottom``.
            assy_top: Set ``IsAssyTestpoint_Top`` (top-side assembly
                probe). Default True.
            assy_bottom: Set ``IsAssyTestpoint_Bottom``.
            force: Ignore existing testpoint coverage and always
                place a new pad.

        Returns:
            Dict with ``{net_class, type, placed, skipped_already_covered,
            placed_nets[], skipped_nets[]}``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.add_testpoints_for_net_class",
            {
                "net_class": net_class,
                "type": type,
                "pad_size_mils": str(round(pad_size_mils)),
                "hole_size_mils": str(round(hole_size_mils)),
                "fab_top": "true" if fab_top else "false",
                "fab_bottom": "true" if fab_bottom else "false",
                "assy_top": "true" if assy_top else "false",
                "assy_bottom": "true" if assy_bottom else "false",
                "force": "true" if force else "false",
            },
            timeout=60.0,
        )

    @mcp.tool()
    async def pcb_apply_dnp_paste_exclusion(
        designators: Optional[list[str]] = None,
        restore: bool = False,
        dry_run: bool = False,
        use_current_variant: bool = False,
    ) -> dict[str, Any]:
        """Suppress stencil paste on Not-Fitted (DNP) components.

        The remediation half of ``audit_variant_not_fitted``. A Not-Fitted
        component is on the BOM as a placeholder and must NOT receive
        paste: the SMT line would otherwise deposit paste on empty pads,
        and the bridging that follows shows up as rework.

        With no ``designators`` this asks ``audit_variant_not_fitted``
        for the components Not Fitted in the project's CURRENT variant,
        so the selection follows the variant you actually have open.
        Pass an explicit list to override that.

        Mechanism: each surface pad gets a manual PasteMaskExpansion of
        minus its larger dimension, which collapses the stencil aperture
        whatever the pad shape. Through-hole pads are left alone and
        counted separately, since they have no aperture to suppress.

        Reversible: ``restore=True`` discards the manual override so
        Altium recomputes the expansion from the design rules.

        RESTORE THE SAME LIST YOU APPLIED. ``restore`` will NOT guess:
        called without ``designators`` it refuses and tells you what to
        pass. That is deliberate. Resolving a restore from the current
        variant means excluding under one variant, switching, then
        restoring, and leaving any component that was excluded but is
        fitted in the new variant with its aperture still suppressed.
        Nothing would report it: the call succeeds, the pad looks
        ordinary in the editor, and the part comes back from assembly
        unsoldered.

        The apply reply lists every component it touched in ``items``.
        Pass those designators back to restore. If you really do want
        the current variant's Not-Fitted list, ask for it with
        ``use_current_variant=True`` and you own the choice.

        Apply is unaffected: with no ``designators`` it uses the current
        variant's Not-Fitted list, which is the whole point of applying.

        Mismatch is visible either way. ``components_requested`` versus
        ``components_matched`` in the reply reports how many of the
        names you passed were actually found on this board.

        Args:
            designators: Components to treat. Omit on an apply to use
                the current variant's Not-Fitted list. Required on a
                restore unless ``use_current_variant`` is set.
            restore: Undo a previous exclusion instead of applying one.
            dry_run: Report which components WOULD be treated and change
                nothing. Worth using first: this edits the board, and a
                wrong variant selection would strip paste off parts that
                are meant to be fitted.
            use_current_variant: Allow a restore with no ``designators``
                to resolve from the variant open right now. Off by
                default because that is the failure above.

        Returns:
            Dict with ``restored``, ``components_requested``,
            ``components_matched``, ``pads_changed``,
            ``pads_skipped_through_hole`` and per-component ``items``.
            In dry-run mode: ``dry_run``, ``designators`` and ``source``.
            On a refused restore: ``{"ok": False, "reason": ...}`` and
            nothing is read from or written to the board.
        """
        bridge = get_bridge()

        source = "explicit"
        variant = ""
        names = [str(d).strip() for d in (designators or []) if str(d).strip()]

        # Refuse BEFORE asking the bridge anything. A restore that
        # resolves from whatever variant happens to be open is the one
        # way this tool can leave a fitted part with no paste, and the
        # result is indistinguishable from success.
        if restore and not names and not use_current_variant:
            return {
                "ok": False,
                "reason": (
                    "restore needs the designators that were excluded. "
                    "Resolving them from the variant open right now "
                    "would restore the wrong components if the variant "
                    "changed since the apply, leaving a fitted part "
                    "with no stencil aperture. Pass the designators "
                    "from the apply reply's 'items', or set "
                    "use_current_variant=True to accept that risk "
                    "deliberately."
                ),
            }

        if not names:
            source = "variant_not_fitted"
            found = await bridge.send_command_async(
                "audit.variant_not_fitted", {})
            items = (found or {}).get("items") or []
            names = [str(i.get("designator", "")).strip()
                     for i in items if isinstance(i, dict)]
            names = [n for n in names if n]
            variant = str((found or {}).get("variant", ""))
            if not names:
                return {
                    "ok": True,
                    "components_matched": 0,
                    "pads_changed": 0,
                    "source": source,
                    "variant": (found or {}).get("variant", ""),
                    "note": "no Not-Fitted components in the current "
                            "variant; nothing to exclude",
                }

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "restore": restore,
                "source": source,
                "variant": variant,
                "designators": names,
                "note": "nothing was changed; re-run with dry_run=False "
                        "to apply",
            }

        # The designator list rides ONE field, so a designator containing
        # the separator would silently split into two names and treat a
        # component nobody asked for. This used to STRIP the separator,
        # which prevents the split but can produce a DIFFERENT valid
        # name: 'R|1' became 'R1', a real component, whose paste this
        # tool then changes. Refusing tells the caller what happened.
        bad = [n for n in names if "|" in payload_safe(n)]
        if bad:
            return {"ok": False, "reason":
                    f"designators {bad} contain '|', which is the "
                    "wire-format separator; stripping it could target a "
                    "different real component, so the call is refused"}
        safe = [s for s in (payload_safe(n) for n in names) if s]
        result = await bridge.send_command_async(
            "pcb.apply_dnp_paste_exclusion",
            {
                "designators": "|".join(safe),
                "restore": "true" if restore else "false",
            },
        )
        # Report WHERE the list came from. An apply and a later restore
        # that resolved against different variants is the failure mode
        # that leaves a fitted part with no paste, and without these two
        # keys the two replies are indistinguishable.
        if isinstance(result, dict):
            result.setdefault("source", source)
            if variant:
                result.setdefault("variant", variant)
        return result

    @mcp.tool()
    async def pcb_make_paste_grid(
        designator: str,
        pad_name: str,
        min_grid_size_mils: int = 15,
        min_gap_mils: int = 5,
        min_coverage_pct: float = 60.0,
    ) -> dict[str, Any]:
        """Split a single pad's solder-paste opening into a grid of
        smaller fills. The classic use case is the central thermal
        pad on a QFN / DFN / QFP -- a full-area paste opening makes
        the IC "swim" sideways during reflow as the molten solder
        pool reduces friction. Splitting the opening into smaller
        squares totalling ~50-75% coverage gives the IC something
        to bond to while letting flux gases escape.

        Standard datasheet recommendations:
          - QFN / DFN exposed pad: 50-75% coverage, 0.3-0.5 mm
            squares with ≥ 0.2 mm gap (≈ 15 mils / 8 mils)
          - QFP heat-spreader: 60-70% coverage
          - Large connector body pads: 60% is usually enough

        ALWAYS consult the IC manufacturer's recommended stencil
        pattern before defaulting to this script -- some packages
        specify a star, diagonal, or windowpane pattern; this tool
        only does a regular grid.

        Args:
            designator: Component reference designator, e.g. ``"U5"``.
            pad_name: Pad identifier on that component. For QFN /
                DFN exposed pads this is usually ``"0"`` or the
                largest-numbered pad. Check the footprint first.
            min_grid_size_mils: Starting grid block size, in mils.
                Bumped up in 5-mil steps if coverage % is below
                target. Default 15 mils (≈ 0.38 mm).
            min_gap_mils: Minimum spacing between grid blocks, in
                mils. Default 5 mils (≈ 0.13 mm).
            min_coverage_pct: Target paste coverage as a percentage
                of pad area, 0-100. Default 60. Iterates grid size
                upward until met.

        Returns:
            Dict with ``{designator, pad_name, grid_x, grid_y,
            grid_size_mils, gap_x_mils, gap_y_mils, fills_placed,
            coverage_pct}``. The existing full-area paste opening is
            suppressed via ``PasteMaskExpansion = -pad_dimension`` on
            the pad cache before the grid fills are laid down.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.make_paste_grid",
            {
                "designator": designator,
                "pad_name": pad_name,
                "min_grid_size_mils": str(round(min_grid_size_mils)),
                "min_gap_mils": str(round(min_gap_mils)),
                "min_coverage_pct": str(float(min_coverage_pct)),
            },
            timeout=30.0,
        )

    @mcp.tool()
    async def pcb_get_differential_pairs() -> dict[str, Any]:
        """Enumerate every ``IPCB_DifferentialPair`` on the active PCB
        and report each pair's two halves with length skew.

        Length mismatch between the two halves of a diff pair is one
        of the most common high-speed routing bugs. Transceiver
        specs:
          - **USB 2.0**: skew < 150 mils
          - **USB 3.x SuperSpeed**: skew < 5 mils per pair
          - **HDMI**: skew < 200 mils within pair, < 800 mils between
          - **MIPI D-PHY (high-rate)**: skew < 5 mils
          - **PCIe Gen3+**: skew < 5 mils within lane
          - **Ethernet PHY**: typically < 50 mils
        Catching skew violations pre-fab saves a respin -- the agent
        should call this near the end of a layout review.

        Returns one row per pair with:
          - ``name``: pair name (e.g. ``"USB_DATA"`` if the SCH had
            ``.DiffPair`` directives ``USB_DATA_P`` / ``USB_DATA_N``)
          - ``positive_net`` / ``negative_net``: the two member nets
          - ``pos_length_mils`` / ``neg_length_mils``: summed track +
            arc length for each half (signal layers only)
          - ``skew_mils``: absolute difference, the number you compare
            against the transceiver spec
          - ``both_routed``: false means one half is still ratsnest-
            only (lengths are unreliable in that state)

        Uses the PCB API ``IPCB_DifferentialPair`` interface.

        Returns:
            Dict with ``{count, pairs: [...]}``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.get_differential_pairs", {}, timeout=30.0,
        )

    @mcp.tool()
    async def pcb_clear_source_footprint_library(
        designator_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        """Clear ``Comp.SourceFootprintLibrary`` on board components.

        Background: when a project is created from an Integrated
        Library, every placed component remembers WHICH library it
        came from. If the user later renames, moves, or consolidates
        that library, ECO and "Update From Library" start failing
        with "library not found" warnings because the component
        still points at the stale path. Clearing
        ``SourceFootprintLibrary`` unpins it so Altium re-matches by
        library-reference name from whatever's currently in
        Available Libraries.

        This is a standard step when consolidating multiple legacy
        libraries into a single curated library: drop the new lib
        into Available Libraries, call this tool, then run an ECO --
        the components now bind to the new library.

        Generalized with an optional filter.

        Args:
            designator_filter: Restrict to these designators
                (e.g. ``["U1", "U5"]``). ``None`` / empty clears all
                components on the board. Match is case-sensitive
                whole-string.

        Returns:
            Dict with ``{total, cleared}``. ``total`` counts components
            inspected after filter; ``cleared`` counts ones that
            actually had a non-empty SourceFootprintLibrary cleared.
        """
        bridge = get_bridge()
        params: dict[str, str] = {}
        if designator_filter:
            # A '|' inside a designator splits into two filter entries,
            # and a fragment like 'R1' matches a real component whose
            # library pin this tool then clears. Refuse rather than
            # strip.
            bad = [d for d in designator_filter if "|" in str(d)]
            if bad:
                return {"ok": False, "reason":
                        f"designators {bad} contain '|', which is the "
                        "wire-format separator; fragments could match "
                        "other components, so the call is refused"}
            params["designator_filter"] = "|".join(
                str(d) for d in designator_filter)
        return await bridge.send_command_async(
            "pcb.clear_source_footprint_library", params, timeout=30.0,
        )

    @mcp.tool()
    async def pcb_get_fab_stats() -> dict[str, Any]:
        """DFM (Design For Manufacturing) summary -- the numbers fab
        houses ask for on their quote forms.

        One IPC call returns:
          - ``board_width_mm``, ``board_height_mm``, ``board_area_mm2``
          - ``num_copper_layers``
          - ``vias_total``, ``vias_through``, ``vias_blind``,
            ``vias_buried``
          - ``pads_plated``, ``pads_unplated``, ``pads_slotted``
          - ``min_annular_ring_mils`` -- across all plated holes
            (vias + plated pads). Most fabs need ≥ 4 mil for the
            standard process; sub-4-mil bumps to HDI.
          - ``min_track_width_mils`` -- minimum across all copper
            layers. Standard fabs offer 4-5 mil; sub-3 mil is HDI.
          - ``smallest_hole_mils``, ``largest_hole_mils``,
            ``distinct_hole_count`` -- the drill bit story. Fab
            houses charge per distinct drill diameter; collapsing
            from 7 distinct sizes to 4 can shave noticeable cost.

        Useful before sending gerbers to the fab: the agent can spot
        sub-process-minimum values and warn the user. Aspect-ratio
        check (depth / drill) is NOT included here because it
        requires layer-stack walking; if you need it, fetch
        ``pcb_get_layer_stackup`` separately.

        Returns:
            Dict with all keys above. All linear measurements use the
            stated unit suffix (``_mm`` vs ``_mils``).
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.get_fab_stats", {})

    @mcp.tool()
    async def pcb_lock_net_routing(
        nets: list[str],
        lock: bool,
        lock_components: bool = False,
    ) -> dict[str, Any]:
        """Lock or unlock track / arc / via primitives on a list of nets.

        Standard workflow: before letting the autorouter touch the board,
        lock the power / ground / clock / oscillator nets so a partial
        re-route pass doesn't undo your careful hand-routing on them.
        Set ``lock=False`` afterwards to free everything up.

        Locked primitives have ``Moveable=False``; the autorouter and
        the interactive editor respect that flag when DXP Preferences →
        PCB Editor → General → "Protect Locked Objects" is enabled.

        The net-list-driven MCP equivalent of a cursor-driven lock.

        Args:
            nets: Net names to lock / unlock (e.g. ``["VCC", "GND",
                "CLK_24M"]``). Case-sensitive.
            lock: True to lock (sets Moveable=False), False to unlock.
            lock_components: Also lock any component with at least one
                pad on a matched net. Useful to freeze the connectors /
                regulators that anchor a hand-routed power section.
                Default False.

        Returns:
            Dict with:
              - ``locked``: True / False mirror of the input
              - ``matched_primitives``: track + arc + via objects whose
                net matched
              - ``updated_primitives``: how many actually changed state
              - ``matched_components``: components touched (only filled
                when ``lock_components=True``)
              - ``updated_components``: components whose Moveable flipped
        """
        if not nets:
            return {"ok": False, "reason": "nets list is empty"}
        # A '|' inside a net name splits into two names, and net-name
        # fragments ('VCC', 'GND') are the most likely of all fragments
        # to match real objects; this tool then flips their Moveable
        # state. Refuse rather than strip.
        bad = [n for n in nets if "|" in str(n)]
        if bad:
            return {"ok": False, "reason":
                    f"net names {bad} contain '|', which is the "
                    "wire-format separator; fragments could match other "
                    "nets, so the call is refused"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.lock_net_routing",
            {
                "nets": "|".join(str(n) for n in nets),
                "lock": "true" if lock else "false",
                "lock_components": "true" if lock_components else "false",
            },
        )

    @mcp.tool()
    async def pcb_set_text_visibility(
        designators: bool | None = None,
        comments: bool | None = None,
        filter: list[str] | None = None,
    ) -> dict[str, Any]:
        """Bulk-toggle component designator and / or comment visibility.

        For the common workflows:
          - Hide all designators before generating an assembly PDF.
          - Show comments during a value-review pass.
          - Hide just the noisy 0402 designators while keeping IC labels.

        Args:
            designators: If True/False, set ``Component.NameOn`` for
                matched components. ``None`` leaves designator visibility
                alone.
            comments: If True/False, set ``Component.CommentOn``. ``None``
                leaves comment visibility alone.
            filter: Restrict to these designator names (e.g. ``["U1",
                "U2", "C5"]``). ``None`` applies to every component.
                Match is case-sensitive whole-string.

        At least one of ``designators`` / ``comments`` must be supplied.

        Returns:
            Dict with:
              - ``matched``: components inspected (after filter)
              - ``updated_names``: components whose NameOn flipped
              - ``updated_comments``: components whose CommentOn flipped
        """
        if designators is None and comments is None:
            return {"ok": False,
                    "reason": ("supply at least one of designators / "
                               "comments")}
        bridge = get_bridge()
        params: dict[str, str] = {}
        if designators is not None:
            params["designators"] = "true" if designators else "false"
        if comments is not None:
            params["comments"] = "true" if comments else "false"
        if filter:
            params["filter"] = "|".join(str(d) for d in filter)
        return await bridge.send_command_async(
            "pcb.set_text_visibility", params,
        )

    @mcp.tool()
    async def pcb_check_placement_collision(
        designator: str,
        x: int,
        y: int,
        rotation: Optional[float] = None,
        margin_mils: int = 0,
    ) -> dict[str, Any]:
        """Dry-run check whether moving a component to (x, y[, rotation])
        would overlap any other placed component on the same side.

        DOES NOT actually move the component. Computes the predicted
        axis-aligned bounding box at the proposed pose, then AABB-tests
        against every other component's current bounding rect. Use this
        BEFORE every `pcb_move_components` call when placing parts on a
        board that already has placed parts.

        Args:
            designator: Component to test (must exist on the board).
            x: Proposed X position in mils (component reference point).
            y: Proposed Y position in mils.
            rotation: Proposed rotation in degrees. When None the target's
                current rotation is used. Quarter-turn deltas (~+/-90 from
                current) swap the AABB dimensions; other angles use the
                current-orientation bbox as an approximation.
            margin_mils: Extra clearance to require around the target.
                Default 0 (touching bounding boxes count as collision).

        Returns:
            Dict with:
              - designator: target.
              - proposed: {x, y, rotation, bbox:{x1,y1,x2,y2}, margin_mils}.
              - clear: True if no collisions, False otherwise.
              - colliding_count: number of collisions.
              - colliding: list of {designator, bbox:{...}} per collider.

        Caveats:
            - AABB only; rotated non-square footprints will report an
              inflated bbox.
            - Same-side check is automatic; the target's bbox is compared
              only against components on the same layer (Top vs Bottom).
            - Does not detect courtyard violations or pad-to-pad clearances,
              only solid-box overlap. Use DRC for actual clearance rules.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "designator": designator,
            "x": str(round(x)),
            "y": str(round(y)),
            "margin_mils": str(round(margin_mils)),
        }
        if rotation is not None:
            params["rotation"] = str(rotation)
        return await bridge.send_command_async(
            "pcb.check_placement_collision", params,
        )

    @mcp.tool()
    async def pcb_get_trace_lengths(
        net: str = "",
    ) -> dict[str, Any]:
        """Get total routed track length per net on the active PCB.

        Sums all track segment lengths for each net. Useful for length
        matching analysis and checking differential pair balance.

        Args:
            net: Optional net name filter. If provided, only returns the
                 length for that specific net. Empty = all nets.

        Returns:
            Dictionary with "trace_lengths" array (each with net name and
            length_mils) and "net_count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if net:
            params["net"] = net
        result = await bridge.send_command_async("pcb.get_trace_lengths", params)
        return result

    @mcp.tool()
    async def pcb_get_layer_stackup() -> dict[str, Any]:
        """Get the full PCB layer stackup information.

        Returns copper layers with thickness, dielectric type/height/constant,
        and board name.

        Returns:
            Dictionary with "layers" array (each with name, order,
            copper_thickness_mils, dielectric_type, dielectric_height_mils,
            dielectric_constant), "layer_count", and "board_name"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_layer_stackup", {})
        return result

    @mcp.tool()
    async def pcb_export_stackup_csv(output_path: str = "") -> dict[str, Any]:
        """Write the layer stackup to a conventional fab CSV report.

        Fetches the structured stackup (``pcb_get_layer_stackup``) and writes
        it as the board-house stackup table: one row per physical layer,
        copper and dielectric interleaved top-to-bottom, thickness in mil and
        mm, dielectric constant where it applies. Columns: index, layer, type,
        material, thickness_mil, thickness_mm, dielectric_constant.

        Args:
            output_path: Destination .csv file. Defaults to
                ``workspace/<board>_stackup.csv``.

        Returns:
            {"output_path", "rows", "layer_count", "total_thickness_mils"} or
            an error dict if the board / stackup could not be read.
        """
        from pathlib import Path

        from ..config import get_config
        from ..export.stackup_csv import (
            format_stackup_csv,
            stackup_total_thickness_mils,
        )

        bridge = get_bridge()
        stackup = await bridge.send_command_async("pcb.get_layer_stackup", {})
        if not isinstance(stackup, dict) or "layers" not in stackup:
            return {"success": False,
                    "error": "could not read layer stackup (open a .PcbDoc)"}

        csv_text = format_stackup_csv(stackup)
        if output_path:
            out = Path(output_path)
        else:
            board = str(stackup.get("board_name") or "board").strip() or "board"
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in board)
            out = get_config().workspace_dir / f"{safe}_stackup.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(csv_text, encoding="utf-8")

        # rows = data rows (exclude the header line).
        rows = max(0, csv_text.count("\n") - 1)
        return {
            "success": True,
            "output_path": str(out),
            "rows": rows,
            "layer_count": stackup.get("layer_count", len(stackup.get("layers") or [])),
            "total_thickness_mils": round(stackup_total_thickness_mils(stackup), 3),
        }

    @mcp.tool()
    async def pcb_add_layer(layer: str) -> dict[str, Any]:
        """Insert a copper layer into the PCB layer stack.

        Calls IPCB_LayerStack.InsertLayer with the requested TLayer enum.
        Valid names include MidLayer1..MidLayer30 (signal layers) and
        InternalPlane1..InternalPlane16 (power / ground planes). Top / Bottom
        are always present, they cannot be added.

        Args:
            layer: Layer name, e.g. "MidLayer1", "InternalPlane1"

        Returns:
            Dictionary confirming the layer was inserted.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.add_layer", {"layer": layer}
        )
        return result

    @mcp.tool()
    async def pcb_remove_layer(layer: str) -> dict[str, Any]:
        """Remove a copper layer from the PCB layer stack.

        Calls IPCB_LayerStack.RemoveFromStack on the requested layer.
        Does nothing if the layer is not currently in the stack.

        Args:
            layer: Layer name, e.g. "MidLayer1", "InternalPlane2"

        Returns:
            Dictionary confirming the layer was removed.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.remove_layer", {"layer": layer}
        )
        return result

    @mcp.tool()
    async def pcb_modify_layer(
        layer: str,
        name: str = "",
        copper_thickness_mils: int = 0,
        dielectric_type: str = "",
        dielectric_height_mils: int = 0,
        dielectric_constant: float = 0.0,
        dielectric_material: str = "",
    ) -> dict[str, Any]:
        """Tune properties on an existing copper layer.

        Every optional parameter is applied only if provided (non-empty
        string / non-zero number). Maps to:
          name                   IPCB_LayerObject.Name
          copper_thickness_mils  IPCB_LayerObject.CopperThickness
          dielectric_type        Dielectric.DielectricType
                                 (one of "none", "core", "prepreg", "surface")
          dielectric_height_mils Dielectric.DielectricHeight
          dielectric_constant    Dielectric.DielectricConstant
          dielectric_material    Dielectric.DielectricMaterial

        Args:
            layer: Target layer name, e.g. "MidLayer1"
            name: New layer name (optional)
            copper_thickness_mils: New copper thickness in mils (optional)
            dielectric_type: "none" | "core" | "prepreg" | "surface" (optional)
            dielectric_height_mils: New dielectric height in mils (optional)
            dielectric_constant: New Dk value (optional)
            dielectric_material: New dielectric material string (optional)

        Returns:
            Dictionary confirming the changes.
        """
        bridge = get_bridge()
        params: dict[str, str] = {"layer": layer}
        if name:
            params["name"] = name
        if copper_thickness_mils:
            params["copper_thickness_mils"] = str(copper_thickness_mils)
        if dielectric_type:
            params["dielectric_type"] = dielectric_type
        if dielectric_height_mils:
            params["dielectric_height_mils"] = str(dielectric_height_mils)
        if dielectric_constant:
            params["dielectric_constant"] = str(dielectric_constant)
        if dielectric_material:
            params["dielectric_material"] = dielectric_material
        result = await bridge.send_command_async("pcb.modify_layer", params)
        return result

    @mcp.tool()
    async def pcb_set_plane_net(layer: str, net: str) -> dict[str, Any]:
        """Tie an internal plane layer to a net.

        An Altium internal plane is a NEGATIVE layer: the copper is
        everywhere except where the plane is cleared, and the net lives on
        the LAYER, not on a poured object. Pouring a polygon on a plane
        layer is a different thing and does not connect the plane, so this
        is the tool for "make Internal Plane 1 the PGND plane" --
        ``pcb_place_polygon_rect`` is not.

        The net must already exist on the board (see ``pcb_get_nets``).
        The layer must be an internal plane; a signal layer is refused
        with ``NOT_A_PLANE``. Layer names are resolved against the real
        stack, so both "Internal Plane 1" (as ``pcb_get_layer_stackup``
        prints it) and "InternalPlane1" work, and a name this board does
        not have is refused with ``UNKNOWN_LAYER`` rather than silently
        retargeting another layer.

        The assignment is READ BACK before reporting. If the read-back
        disagrees the call answers ``success: false`` with ``applied:
        false`` and a note, and the board is NOT saved.

        Args:
            layer: Internal plane layer, e.g. "InternalPlane1" or the name
                the stackup reports for it
            net: Existing net name to tie the plane to, e.g. "PGND"

        Returns:
            Dictionary with "success", "layer" (the RESOLVED layer),
            "layer_name", "net", "net_readback", "applied" and "note".
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_plane_net", {"layer": layer, "net": net}
        )
        return result

    @mcp.tool()
    async def pcb_get_board_outline() -> dict[str, Any]:
        """Get the board outline vertices and bounding rectangle.

        Returns outline geometry as a list of vertices (line segments and
        arcs) plus the bounding rectangle dimensions.

        Returns:
            Dictionary with "point_count", "vertices" array (each with
            index, kind, x, y, and optionally cx, cy, angle1, angle2 for arcs),
            and "bounding_rect" (left, bottom, right, top in mils)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_board_outline", {})
        return result

    @mcp.tool()
    async def pcb_get_selected_objects(
        properties: str = "ObjectId,X,Y,Layer,Net",
    ) -> dict[str, Any]:
        """Get properties of currently selected objects on the active PCB.

        Args:
            properties: Comma-separated property names to return.
                Available: ObjectId, X, Y, X1, Y1, X2, Y2, Layer, Net,
                Width, Name, Rotation, HoleSize, TopXSize, TopYSize,
                Size, Pattern, SourceDesignator, Text, Descriptor, Selected

        Returns:
            Dictionary with "objects" array and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.get_selected_objects",
            {"properties": properties},
        )
        return result

    @mcp.tool()
    async def pcb_set_layer_visibility(
        layer: str,
        visible: bool = True,
    ) -> dict[str, Any]:
        """Show or hide a specific PCB layer.

        Args:
            layer: Layer name string, e.g.:
                Copper: "TopLayer", "BottomLayer", "MidLayer1"-"MidLayer30"
                Overlay: "TopOverlay", "BottomOverlay"
                Mask: "TopPaste", "BottomPaste", "TopSolder", "BottomSolder"
                Plane: "InternalPlane1"-"InternalPlane16"
                Other: "DrillGuide", "DrillDrawing", "MultiLayer",
                       "KeepOutLayer", "Mechanical1"-"Mechanical16"
            visible: True to show, False to hide

        Returns:
            Dictionary with layer name and visibility state
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_layer_visibility",
            {"layer": layer, "visible": "true" if visible else "false"},
        )
        return result

    @mcp.tool()
    async def pcb_repour_polygons() -> dict[str, Any]:
        """Repour all polygon pours on the active PCB.

        Triggers a full repour of all polygon copper pours, which
        recalculates thermal reliefs and clearances.

        Returns:
            Dictionary confirming repour completed
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.repour_polygons", {})
        return result

    @mcp.tool()
    async def pcb_set_board_shape(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any]:
        """Define the physical PCB board outline as a rectangle.

        Overwrites the current board shape. Use this right after creating a
        new PCB document to establish the board size before placing parts.
        Coordinates are in mils; (x1,y1) and (x2,y2) are opposite corners in
        any order.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils

        Returns:
            Dictionary confirming the new outline rectangle
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_board_shape",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        return result

    @mcp.tool()
    async def pcb_place_polygon_rect(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        net: str = "",
        layer: str = "TopLayer",
        pour_over: bool = True,
    ) -> dict[str, Any]:
        """Drop a copper polygon pour on a rectangular area.

        Useful for placing a ground plane or power plane: pass the board's
        corners, the layer, and the net name (typically "GND"). Set
        pour_over=False to force the pour around same-net tracks/pads
        instead of covering them.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            net: Net name to assign (empty = no-net fill, unusual)
            layer: Copper layer (default "TopLayer")
            pour_over: Pour over same-net objects (default True)

        Returns:
            Dictionary confirming the polygon placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_polygon_rect",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "net": net,
                "layer": layer,
                "pour_over": "true" if pour_over else "false",
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_via_array(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        pitch: int = 50,
        net: str = "",
        size: int = 30,
        hole_size: int = 12,
        low_layer: str = "TopLayer",
        high_layer: str = "BottomLayer",
    ) -> dict[str, Any]:
        """Stitch vias in a regular grid across a rectangle.

        Typical use: GND stitching between top and bottom layer copper
        pours. Places vias at every (pitch × pitch) grid intersection
        inside the rectangle.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            pitch: Grid spacing in mils (default 50)
            net: Net to assign (typically "GND"; empty = no net)
            size: Via pad diameter in mils (default 30)
            hole_size: Drill hole diameter in mils (default 12)
            low_layer: Start layer (default "TopLayer")
            high_layer: End layer (default "BottomLayer")

        Returns:
            Dictionary with count of vias placed and rectangle/pitch echo
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_via_array",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "pitch": str(pitch),
                "net": net,
                "size": str(size),
                "hole_size": str(hole_size),
                "low_layer": low_layer,
                "high_layer": high_layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_via(
        x: int,
        y: int,
        net: str = "",
        size: int = 50,
        hole_size: int = 28,
        low_layer: str = "TopLayer",
        high_layer: str = "BottomLayer",
    ) -> dict[str, Any]:
        """Place a via at specific coordinates on the active PCB.

        Args:
            x: Via X position in mils
            y: Via Y position in mils
            net: Net name to assign (optional, empty = no net)
            size: Via pad diameter in mils (default 50)
            hole_size: Drill hole diameter in mils (default 28)
            low_layer: Start layer (default "TopLayer")
            high_layer: End layer (default "BottomLayer")

        Returns:
            Dictionary with placed via position and size
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x": str(x),
            "y": str(y),
            "size": str(size),
            "hole_size": str(hole_size),
            "low_layer": low_layer,
            "high_layer": high_layer,
        }
        if net:
            params["net"] = net
        result = await bridge.send_command_async("pcb.place_via", params)
        return result

    @mcp.tool()
    async def pcb_place_tracks(
        tracks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place many track segments on the active PCB in ONE IPC round-trip.

        PREFER THIS over placing one segment at a time whenever you have
        more than one segment to place. The whole batch is wrapped in
        a single PreProcess/PostProcess and a single save, so 50
        tracks take roughly the same wall time as 1. Typical uses:
        routing a full net, laying down a whole stitch pattern,
        replicating a motif, drawing a keepout rectangle.

        Args:
            tracks: List of track dicts. Each dict supports:
                x1, y1, x2, y2 (required, mils)
                width (default 10), layer (default "TopLayer"),
                net_name (optional, empty = no net)

            Example:
                [
                  {"x1": 5010, "y1": 4785, "x2": 5070, "y2": 4785,
                   "width": 10, "net_name": "NetC8_2"},
                  {"x1": 5070, "y1": 4785, "x2": 5070, "y2": 4862,
                   "width": 10, "net_name": "NetC8_2"},
                ]

        Returns:
            Dictionary with "placed" and "failed" counts
        """
        parts = []
        for t in tracks:
            x1 = int(t["x1"])
            y1 = int(t["y1"])
            x2 = int(t["x2"])
            y2 = int(t["y2"])
            width = int(t.get("width", 10))
            layer = str(t.get("layer", "TopLayer"))
            net = str(t.get("net_name", ""))
            parts.append(f"{x1},{y1},{x2},{y2},{width},{layer},{net}")
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_tracks", {"tracks": "|".join(parts)}
        )
        return result

    @mcp.tool()
    async def pcb_place_arc(
        x_center: int,
        y_center: int,
        radius: int,
        start_angle: float = 0,
        end_angle: float = 360,
        width: int = 10,
        layer: str = "TopLayer",
    ) -> dict[str, Any]:
        """Place an arc on the active PCB.

        Creates a circular arc segment defined by center, radius, and
        angular range.

        Args:
            x_center: Arc center X position in mils
            y_center: Arc center Y position in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees (default 0)
            end_angle: End angle in degrees (default 360 = full circle)
            width: Arc line width in mils (default 10)
            layer: PCB layer name (default "TopLayer")

        Returns:
            Dictionary with placed arc geometry and layer
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x_center": str(x_center),
            "y_center": str(y_center),
            "radius": str(radius),
            "start_angle": str(start_angle),
            "end_angle": str(end_angle),
            "width": str(width),
            "layer": layer,
        }
        result = await bridge.send_command_async("pcb.place_arc", params)
        return result

    @mcp.tool()
    async def pcb_place_text(
        text: str,
        x: int,
        y: int,
        layer: str = "TopOverlay",
        height: int = 60,
        rotation: float = 0,
    ) -> dict[str, Any]:
        """Place a text string on the active PCB.

        Args:
            text: Text content to place
            x: Text X position in mils
            y: Text Y position in mils
            layer: PCB layer name (default "TopOverlay"). Common choices:
                "TopOverlay", "BottomOverlay", "TopLayer", "BottomLayer",
                "Mechanical1"-"Mechanical16"
            height: Text height in mils (default 60)
            rotation: Rotation angle in degrees (default 0)

        Returns:
            Dictionary with placed text properties
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "text": text,
            "x": str(x),
            "y": str(y),
            "layer": layer,
            "height": str(height),
            "rotation": str(rotation),
        }
        result = await bridge.send_command_async("pcb.place_text", params)
        return result

    @mcp.tool()
    async def pcb_place_fill(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        layer: str = "TopLayer",
        net_name: str = "",
    ) -> dict[str, Any]:
        """Place a rectangular copper fill on the active PCB.

        Creates a solid copper rectangle. Useful for thermal pads,
        ground planes, and copper pours in specific areas.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Second corner X in mils
            y2: Second corner Y in mils
            layer: PCB layer name (default "TopLayer")
            net_name: Net name to assign (optional, empty = no net)

        Returns:
            Dictionary with placed fill coordinates and layer
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x1": str(x1),
            "y1": str(y1),
            "x2": str(x2),
            "y2": str(y2),
            "layer": layer,
        }
        if net_name:
            params["net_name"] = net_name
        result = await bridge.send_command_async("pcb.place_fill", params)
        return result

    @mcp.tool()
    async def pcb_start_polygon_placement(
        layer: str = "TopLayer",
        net_name: str = "",
    ) -> dict[str, Any]:
        """Start INTERACTIVE polygon pour placement on the active PCB.

        This is an interactive command, it launches Altium's polygon
        placement mode. The user must then draw the polygon boundary
        interactively in Altium Designer (clicks define vertices, right-click
        or Escape completes). It does NOT create a polygon programmatically
        from coordinates.

        Args:
            layer: Target copper layer (default "TopLayer")
            net_name: Net to assign to the polygon pour (optional)

        Returns:
            Dictionary confirming polygon placement mode was initiated
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"layer": layer}
        if net_name:
            params["net_name"] = net_name
        result = await bridge.send_command_async("pcb.start_polygon_placement", params)
        return result

    @mcp.tool()
    async def pcb_create_design_rule(
        name: str,
        rule_type: str = "clearance",
        value: int = 10,
        max_value: Optional[int] = None,
        favored_value: Optional[int] = None,
        max_uncoupled_length: Optional[int] = None,
        scope: str = "",
        net_scope: str = "different_nets",
    ) -> dict[str, Any]:
        """Create a new design rule on the active PCB.

        Args:
            name: Rule name (e.g., "Min Clearance 6mil").
            rule_type: Type of rule to create. Options:
                ``"clearance"`` - Electrical clearance (value = gap in mils).
                ``"width"`` - Track width constraint (value = min width
                    in mils; max_value / favored_value optional).
                ``"via_size"`` - Hole size constraint (value = min hole
                    in mils; max_value optional).
                ``"differential_pairs"`` - Differential-pair routing rule
                    (value = min gap, max_value = max gap,
                    favored_value = preferred gap, max_uncoupled_length =
                    max uncoupled length in mils).
            value: Rule's primary value in mils. For width / via_size /
                differential_pairs this is the MIN side. Default 10.
            max_value: For width / via_size / differential_pairs. When
                supplied, sets the MAX side independently of value. When
                None, falls back to 5x value (legacy default).
            favored_value: For width / differential_pairs. Sets the
                preferred value. Defaults to value when None.
            max_uncoupled_length: For differential_pairs only.
                MaxUncoupledLength in mils (single scalar, not per-layer).
                Defaults to 1000 mils when None.
            scope: Optional query expression for Scope1. Pick the
                predicate that matches the rule kind:
                  - net-based rules (clearance, width, via_size):
                    ``"InNet('GND')"``, ``"InNetClass('Power')"``,
                    ``"All"``.
                  - differential_pairs: ``"InDifferentialPair('USB')"``,
                    ``"InDifferentialPairClass('HighSpeed')"``,
                    ``"IsDifferentialPair"`` (matches any diff pair),
                    or ``"All"``.
                Mixing predicates across kinds (e.g., ``InNet`` on a
                differential_pairs rule) creates a rule that never matches.
            net_scope: Which nets the rule applies between. Options:
                ``"different_nets"`` (default) for Clearance rules;
                ``"any_net"`` for all-pairs; ``"same_net"`` for same-net
                only. Has no effect on differential_pairs.

        Returns:
            Dictionary with created rule details.

        Note: Creating ComponentClearance, HoleToHoleClearance,
        BoardOutlineClearance, and other newer rule kinds is not
        supported on this Altium build because the relevant symbolic
        constants (eRule_ComponentClearance, ...) are not exposed in
        DelphiScript. Existing rules of those kinds can be UPDATED via
        ``pcb_set_rule_properties`` (gap_mils field), which dispatches
        through Rule.RuleKind without needing the symbol.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "name": name,
            "rule_type": rule_type,
            "value": str(value),
            "net_scope": net_scope,
        }
        if max_value is not None:
            params["max_value"] = str(max_value)
        if favored_value is not None:
            params["favored_value"] = str(favored_value)
        if max_uncoupled_length is not None:
            params["max_uncoupled_length"] = str(max_uncoupled_length)
        if scope:
            params["scope"] = scope
        result = await bridge.send_command_async("pcb.create_design_rule", params)
        return result

    @mcp.tool()
    async def pcb_delete_design_rule(
        name: str,
    ) -> dict[str, Any]:
        """Delete a design rule by name from the active PCB.

        Args:
            name: Exact name of the design rule to delete

        Returns:
            Dictionary confirming deletion
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.delete_design_rule",
            {"name": name},
        )
        return result

    @mcp.tool()
    async def pcb_get_component_pads(
        designator: str,
    ) -> dict[str, Any]:
        """Get all pads of a specific PCB component.

        Returns detailed pad information including pin name, position,
        net assignment, size, and hole information.

        DATASHEET DISCIPLINE: Pad name -> pin function mapping comes
        from the footprint, which can be wrong (especially the thermal
        pad on QFN/DFN, which is rarely numbered consistently across
        vendors). Before stating which pin a pad corresponds to,
        cross-check the manufacturer datasheet's mechanical /
        recommended-land-pattern section. The response carries
        `_datasheet_guidance` + `_datasheet_parts`.

        Args:
            designator: Component reference designator (e.g., "U1", "J3")

        Returns:
            Dictionary with "designator", "pads" array (each with name,
            x, y, net, layer, hole_size, top_x_size, top_y_size,
            rotation), "pad_count", plus `_datasheet_guidance` +
            `_datasheet_parts`.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.get_component_pads",
            {"designator": designator},
        )
        if isinstance(result, dict):
            explicit = [{
                "manufacturer": "",
                "part_number": "",
                "designators": designator,
            }]
            return tag_response(
                result,
                explicit_parts=explicit,
                context="pcb_get_component_pads",
            )
        return result

    @mcp.tool()
    async def pcb_flip_component(
        designator: str,
    ) -> dict[str, Any]:
        """Flip a component to the other side of the board (top to bottom
        or bottom to top).

        Args:
            designator: Component reference designator (e.g., "U1", "R5")

        Returns:
            Dictionary with designator, old_layer, and new_layer
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.flip_component",
            {"designator": designator},
        )
        return result

    @mcp.tool()
    async def pcb_align_components(
        designators: str,
        alignment: str = "left",
    ) -> dict[str, Any]:
        """Align multiple PCB components along a common edge or center.

        Args:
            designators: Comma-separated component reference designators
                (e.g., "R1,R2,R3,R4")
            alignment: Alignment mode. Options:
                "left" - Align to leftmost X
                "right" - Align to rightmost X
                "top" - Align to topmost Y
                "bottom" - Align to bottommost Y
                "center_x" - Center horizontally
                "center_y" - Center vertically

        Returns:
            Dictionary with alignment result and component count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.align_components",
            {"designators": designators, "alignment": alignment},
        )
        return result

    @mcp.tool()
    async def pcb_snap_to_grid(
        designator: str,
        grid_size: int = 50,
    ) -> dict[str, Any]:
        """Snap a component to the nearest grid point.

        Rounds the component's X and Y position to the nearest multiple
        of the specified grid size.

        Args:
            designator: Component reference designator (e.g., "U1", "R5")
            grid_size: Grid spacing in mils (default 50)

        Returns:
            Dictionary with designator, old and new positions, and grid_size
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.snap_to_grid",
            {"designator": designator, "grid_size": str(grid_size)},
        )
        return result

    @mcp.tool()
    async def pcb_get_diff_pair_rules() -> dict[str, Any]:
        """Get all differential pair routing rules from PCB design rules.

        Returns design rules of kind eRule_DifferentialPairsRouting, these
        are routing rules, NOT IPCB_DifferentialPair pair objects on the board.

        Returns:
            Dictionary with "diff_pair_rules" array (each with name, enabled,
            scope_1, scope_2, comment, descriptor) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_diff_pair_rules", {})
        return result

    @mcp.tool()
    async def pcb_get_vias() -> dict[str, Any]:
        """Get all vias on the active PCB board.

        Returns via position, pad size, hole size, net assignment, and
        start/end layer for every via on the board.

        Returns:
            Dictionary with "vias" array (each with x, y, size, hole_size,
            net, low_layer, high_layer) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_vias", {})
        return result

    @mcp.tool()
    async def pcb_calc_polygon_area(
        net: str = "",
        layer: str = "",
    ) -> dict[str, Any]:
        """Report the area of each copper polygon on the active board.

        Returns each polygon's overall boundary area in square mils and
        square millimetres, optionally filtered by net and/or layer.
        Useful for estimating copper coverage / plane area.

        Args:
            net: Only polygons on this net (optional).
            layer: Only polygons on this layer name (optional).

        Returns:
            Dict with ``polygons`` (each: name, net, layer, area_sq_mils,
            area_sq_mm) and ``count``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if net:
            params["net"] = net
        if layer:
            params["layer"] = layer
        return await bridge.send_command_async("pcb.calc_polygon_area", params)

    @mcp.tool()
    async def pcb_set_via_soldermask_relief(
        expansion_mils: int = 4,
        net: str = "",
    ) -> dict[str, Any]:
        """Open soldermask over via barrels (barrel relief).

        Sets each via's soldermask expansion-from-hole-edge so the via
        barrel gets a soldermask opening, optionally limited to one net.
        This is a common fab requirement that design rules don't expose
        directly per-via.

        Args:
            expansion_mils: Soldermask expansion from the hole edge, in
                mils (default 4).
            net: Only vias on this net (optional; default all vias).

        Returns:
            Dict with success, modified (count), expansion_mils.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"expansion_mils": str(expansion_mils)}
        if net:
            params["net"] = net
        return await bridge.send_command_async(
            "pcb.set_via_soldermask_relief", params
        )

    @mcp.tool()
    async def pcb_get_mech_layer_names() -> dict[str, Any]:
        """List the enabled mechanical layers on the active board with names.

        Returns each displayed/enabled mechanical layer's internal layer id
        and its custom name (e.g. "Assembly Top", "Courtyard"), so an agent
        can target the right mechanical layer by name rather than guessing
        "Mechanical 1".

        Each entry also carries its ``kind``, which is what the layer is FOR
        rather than what it is called. Read it before setting one: a kind
        belongs to a single layer, so assigning it moves it. ``kind_id`` of
        -1 means this Altium build has no mechanical layer kinds.

        Returns:
            Dict with ``mechanical_layers`` (each: layer, name, kind,
            kind_id) and ``count``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.get_mech_layer_names", {})

    @mcp.tool()
    async def pcb_get_layer_display() -> dict[str, Any]:
        """Visibility and colour for every layer on the active board.

        Covers signal, plane, mechanical, mask, paste, silk, keepout and
        multilayer. Each entry gives the internal ``layer`` id, the user's
        ``name`` for it, whether it is ``visible``, and its colour both as
        Altium's raw integer and as ``color_hex``.

        Read this before recolouring: Altium stores a colour as a Windows
        TColor, which is byte-reversed against the #RRGGBB people write,
        and getting that backwards produces a plausible wrong colour
        rather than an error.

        Returns:
            Dict with ``layers``, ``count`` and ``visible_count``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("pcb.get_layer_display", {})

    @mcp.tool()
    async def pcb_set_layer_color(layer: str, color: str) -> dict[str, Any]:
        """Recolour one layer.

        The colour is given as ``#RRGGBB``. Altium holds it internally as
        a byte-reversed TColor, and that conversion happens on the Altium
        side so no caller has to know about it.

        The write is READ BACK before reporting success, because a
        refused assignment raises nothing and would otherwise be reported
        as having worked.

        Colour is a display setting rather than a property of the board,
        so it follows the installation rather than travelling with the
        file.

        Args:
            layer: Layer name, e.g. "TopOverlay" or "Mechanical13".
            color: Colour as "#RRGGBB".

        Returns:
            Dict with ``layer``, ``color`` and ``color_hex``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.set_layer_color", {"layer": layer, "color": color}
        )

    @mcp.tool()
    async def pcb_set_mech_layer_kind(
        layer: str, kind: str, partner_layer: str = ""
    ) -> dict[str, Any]:
        """Set what a mechanical layer is FOR, not what it is called.

        Renaming a layer "Courtyard Top" does not make it one. The KIND is
        a separate property, and it is what every feature resolving a layer
        by purpose reads: courtyard checking, assembly drawings, 3D body
        placement and the IPC-4761 via treatments. A layer with no kind is
        skipped by all of them, so the outlines get drawn and nothing uses
        them.

        A SINGLE kind such as "Fab Notes" belongs to ONE layer at a time.
        If another layer already holds it, that layer is cleared first and
        named in ``cleared_from``, because leaving two layers claiming one
        purpose is a state the stack manager does not intend.

        A PAIRED kind, meaning any name ending in Top or Bottom, works
        differently: it is held by the layer PAIR rather than by either
        layer, under a separate set of names with the side dropped. So
        "Courtyard Top" is stored as the pair kind "Courtyard" across the
        two layers, and ``partner_layer`` is required to say which layer
        carries the other side. Writing it without a partner is refused
        rather than silently doing nothing.

        The write is READ BACK before reporting success. A refused
        assignment raises nothing here, so trusting it would report success
        for having changed nothing.

        Args:
            layer: Mechanical layer, e.g. "Mechanical13".
            kind: A kind name such as "Courtyard Top", "Assembly Top",
                "3D Body Top", "Component Outline Top", "Fab Notes" or
                "Not Set". A bare number is also accepted, so a kind added
                by a later Altium release can be set without waiting for
                the name map to catch up. Read the current assignment with
                ``pcb_get_mech_layer_names``.
            partner_layer: The mechanical layer carrying the other side,
                required when ``kind`` ends in Top or Bottom and ignored
                otherwise. The two layers are joined as a pair, which is
                what the kind is then written to.

        Returns:
            Dict with ``layer``, ``kind``, ``kind_id``, ``paired``,
            ``pair_kind``, ``partner_layer`` and ``cleared_from``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"layer": layer, "kind": kind}
        if partner_layer:
            params["partner_layer"] = partner_layer
        return await bridge.send_command_async(
            "pcb.set_mech_layer_kind", params
        )

    @mcp.tool()
    async def pcb_delete_object(
        x: int,
        y: int,
        layer: str = "TopLayer",
        object_type: str = "track",
    ) -> dict[str, Any]:
        """Delete a PCB object closest to specific coordinates on a layer.

        Finds the nearest matching object within 100 mils of the given
        coordinates and removes it from the board.

        Args:
            x: Target X position in mils
            y: Target Y position in mils
            layer: PCB layer name (default "TopLayer")
            object_type: Type of object to delete. Options:
                "track" - Track segment
                "via" - Via
                "fill" - Copper fill
                "text" - Text string
                "pad" - Free pad
                "arc" - Arc
                "polygon" - Copper polygon pour
                "region" - Region / split-plane
                "component" - Placed component
                (polygon/region/component/arc are matched by bounding-box
                centre; for bulk/filter-based deletes use ``obj_delete``)

        Returns:
            Dictionary with deleted status, object_type, and distance_mils
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.delete_object",
            {"x": str(x), "y": str(y), "layer": layer, "object_type": object_type},
        )
        return result

    @mcp.tool()
    async def pcb_get_pad_properties(
        net: str = "",
        designator: str = "",
    ) -> dict[str, Any]:
        """Get detailed pad information filtered by net or component.

        Returns pad shape, size, hole, thermal relief, and solder/paste
        mask expansion details. Provide at least one filter (net or
        designator) to avoid returning all pads on the board.

        Args:
            net: Filter by net name (e.g., "GND", "VCC"). Optional.
            designator: Filter by component designator (e.g., "U1"). Optional.

        Returns:
            Dictionary with "pads" array (each with name, component, x, y,
            net, layer, shape, top_x_size, top_y_size, hole_size, rotation,
            is_smd, solder_mask_expansion, paste_mask_expansion) and "count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if net:
            params["net"] = net
        if designator:
            params["designator"] = designator
        result = await bridge.send_command_async("pcb.get_pad_properties", params)
        return result

    @mcp.tool()
    async def pcb_set_track_width(
        net_name: str,
        width_mils: int,
    ) -> dict[str, Any]:
        """Modify track width for all tracks on a specific net.

        Changes the width of every routed track segment assigned to
        the given net. Useful for adjusting power or signal trace widths.

        Args:
            net_name: Name of the net whose tracks to modify (e.g., "VCC")
            width_mils: New track width in mils (e.g., 10, 20, 50)

        Returns:
            Dictionary with net_name, width_mils, and tracks_modified count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_track_width",
            {"net_name": net_name, "width_mils": str(width_mils)},
        )
        return result

    @mcp.tool()
    async def pcb_get_unrouted_nets() -> dict[str, Any]:
        """Get list of nets with unrouted connections (ratsnest lines).

        Identifies nets that still have ratsnest lines, meaning they are
        not fully routed. Useful for checking routing completion status.

        Returns:
            Dictionary with "unrouted_nets" array (each with net name and
            unrouted_connections count), "net_count", and "total_unrouted"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_unrouted_nets", {})
        return result

    @mcp.tool()
    async def pcb_get_polygons() -> dict[str, Any]:
        """Get all polygon pours on the active PCB with copper area.

        Each polygon row carries:
          - ``index``, ``name``: positional + Altium-assigned name
          - ``net``: assigned net (often ``GND`` / a power rail)
          - ``layer``: ``TopLayer``, ``InternalPlane1``, etc.
          - ``hatch_style``: ``Solid`` / ``45Degree`` / ``Horizontal`` ...
          - ``pour_over``, ``remove_dead_copper``: pour-policy flags
          - ``area_sqmils`` / ``area_mm2``: ACTUAL copper area after
            the pour has been computed (excludes thermal-relief and
            clearance cutouts). Use this for current-capacity audits
            (multiply by copper thickness for cubic copper, then
            apply IPC-2152 / IPC-2221).
          - ``bbox_mm2``: bounding-rectangle area; ratio
            ``area_mm2 / bbox_mm2`` shows how much of the outline is
            real copper. Low ratio = pour is fighting with cutouts.
          - ``vertex_count``: a smooth pour has 4-8 vertices; many
            more usually means hand-editing that may have introduced
            narrow necks.

        Pair with ``pcb_calc_track_current_capacity`` for
        per-net current-carrying analysis.

        Returns:
            Dict with ``{polygons: [...], count: N}``.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_polygons", {})
        return result

    @mcp.tool()
    async def pcb_modify_polygon(
        index: int,
        net: str = "",
        layer: str = "",
        hatch_style: str = "",
    ) -> dict[str, Any]:
        """Modify a polygon pour's properties.

        Changes net, layer, or hatching style of an existing polygon pour.
        Use pcb_get_polygons first to find the polygon index.

        Args:
            index: Polygon index (from pcb_get_polygons output)
            net: New net name to assign (optional, empty = no change)
            layer: New layer name (optional, empty = no change)
            hatch_style: New hatch style (optional). Options:
                "Solid" - Solid copper fill
                "45Degree" - 45-degree crosshatch
                "90Degree" - 90-degree crosshatch
                "Horizontal" - Horizontal lines
                "Vertical" - Vertical lines

        Returns:
            Dictionary with modified status, index, and polygon name
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"index": str(index)}
        if net:
            params["net"] = net
        if layer:
            params["layer"] = layer
        if hatch_style:
            params["hatch_style"] = hatch_style
        result = await bridge.send_command_async("pcb.modify_polygon", params)
        return result

    @mcp.tool()
    async def pcb_get_room_rules() -> dict[str, Any]:
        """Get all room-like rules (confinement constraint design rules).

        Returns design rules of kind eRule_ConfinementConstraint, these are
        NOT physical IPCB_Room objects on the board. The rule bounding rect
        is reported as x1/y1/x2/y2 in mils.

        Returns:
            Dictionary with "room_rules" array (each with name, enabled, kind,
            scope_1, comment, x1, y1, x2, y2) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_room_rules", {})
        return result

    @mcp.tool()
    async def pcb_create_room(
        name: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        components: str = "",
    ) -> dict[str, Any]:
        """Create a room for component grouping on the active PCB.

        Creates a confinement constraint rule that defines a rectangular
        region. Components can be assigned via scope expression.

        Args:
            name: Room name (e.g., "Power Section", "USB Block")
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Second corner X in mils
            y2: Second corner Y in mils
            components: Comma-separated component designators to confine
                (e.g., "U1,U2,R1,R2"). Optional; empty = applies to all.

        Returns:
            Dictionary with created status, name, coordinates, and scope
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "name": name,
            "x1": str(x1),
            "y1": str(y1),
            "x2": str(x2),
            "y2": str(y2),
        }
        if components:
            params["components"] = components
        result = await bridge.send_command_async("pcb.create_room", params)
        return result

    @mcp.tool()
    async def pcb_get_board_statistics() -> dict[str, Any]:
        """Get comprehensive statistics for the active PCB board.

        Returns counts of all object types, total trace length, board
        dimensions, and layer count. Useful for design reviews and
        progress tracking.

        Returns:
            Dictionary with track_count, via_count, pad_count,
            component_count, fill_count, text_count, polygon_count,
            unrouted_connections, total_trace_length_mils,
            board_width_mils, board_height_mils, board_area_sq_mils,
            layer_count, and board_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_board_statistics", {})
        return result

    @mcp.tool()
    async def pcb_export_coordinates() -> dict[str, Any]:
        """Export component placement coordinates formatted for pick-and-place (like `pcb_get_components`, but the pick-and-place shape: adds side, omits bbox detail).

        Returns designator, footprint, comment, position (x, y),
        rotation, layer, and side (Top/Bottom) for every component.
        Useful for manufacturing pick-and-place machine programming.

        Returns:
            Dictionary with "placements" array (each with designator,
            footprint, comment, x, y, rotation, layer, side) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.export_coordinates", {})
        return result

    @mcp.tool()
    async def pcb_create_diff_pair(
        positive_net: str,
        negative_net: str,
        name: str = "",
    ) -> dict[str, Any]:
        """Create a differential pair object from two existing nets.

        The two nets must already exist on the board (typically present
        after update_pcb / ECO). The diff-pair object lets Altium apply
        differential routing constraints and the interactive router to
        honour impedance / matched-length rules between the pair.

        Args:
            positive_net: Positive-side net name (e.g. "USB_DP")
            negative_net: Negative-side net name (e.g. "USB_DM")
            name: Optional diff-pair name (defaults to "<pos>_<neg>")

        Returns:
            Dictionary confirming creation with name, positive_net, negative_net
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.create_diff_pair",
            {
                "positive_net": positive_net,
                "negative_net": negative_net,
                "name": name,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_region(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        layer: str = "TopLayer",
        net: str = "",
    ) -> dict[str, Any]:
        """Place a solid copper region on a rectangular area.

        Regions are solid primitives; unlike polygons, they don't participate
        in the connectivity engine unless you assign a net. Use for
        mechanical copper zones, thermal pads, or solder-mask openings.
        For a true ground plane with ratsnest tracking, prefer
        pcb_place_polygon_rect.

        Args:
            x1, y1, x2, y2: Opposite corners in mils (any order)
            layer: Copper or mech layer (default "TopLayer")
            net: Optional net assignment

        Returns:
            Dictionary confirming the region placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_region",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "layer": layer,
                "net": net,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_dimension(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        layer: str = "TopOverlay",
        orientation: str = "",
    ) -> dict[str, Any]:
        """Place a linear dimension between two points.

        Horizontal dimension measures delta-X, vertical measures delta-Y.
        Orientation auto-detects from the larger axis delta if not given.

        Args:
            x1, y1: First reference point in mils
            x2, y2: Second reference point in mils
            layer: Layer to draw the dimension on (default "TopOverlay")
            orientation: "horizontal" or "vertical" ("" = auto)

        Returns:
            Dictionary confirming the dimension placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_dimension",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "layer": layer,
                "orientation": orientation,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_pad(
        x: int,
        y: int,
        name: str = "",
        net: str = "",
        shape: str = "round",
        x_size: int = 60,
        y_size: int = 60,
        hole_size: int = 0,
        layer: str = "TopLayer",
    ) -> dict[str, Any]:
        """Place a standalone pad on the active PCB.

        Not part of any component. Use for fiducials, test points,
        mounting holes. Set hole_size=0 for surface-mount pads,
        nonzero for through-hole.

        Args:
            x, y: Position in mils
            name: Pad designator / label (optional)
            net: Net to connect to (optional)
            shape: "round" (default) / "rect" / "oct"
            x_size, y_size: Pad dimensions in mils
            hole_size: Drill diameter in mils (0 = SMD)
            layer: Copper layer (default "TopLayer")

        Returns:
            Dictionary confirming pad placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_pad",
            {
                "x": str(x),
                "y": str(y),
                "name": name,
                "net": net,
                "shape": shape,
                "x_size": str(x_size),
                "y_size": str(y_size),
                "hole_size": str(hole_size),
                "layer": layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_components(
        placements: list[dict[str, Any]],
        board_path: str = "",
    ) -> dict[str, Any]:
        """Place one or many footprints from PcbLibs onto the board in ONE call.

        The scriptable substitute for ECO / *Design ▸ Update PCB Document*:
        Altium's ECO always raises a modal, so for unattended population this
        drops footprints straight onto the board. It resolves the board once
        and places every footprint in a single Altium transaction (one IPC
        round-trip). Pass a single-element list to place just one.

        Two modes per component: **geometry only** (omit ``unique_id`` /
        ``pad_nets``) leaves pads unconnected (DRC flags them), good for
        artwork / panelization / fiducials / placement studies; **synced**
        (pass both) stamps the schematic UniqueId so a later ECO matches the
        part and creates+assigns each pad's net for real connectivity. Read
        ``unique_id`` via ``query_objects(eSchComponent,
        "Designator.Text,UniqueId")`` and ``pad_nets`` from the compiled
        netlist (``proj_get_connectivity_many`` → pad number → net).

        ``board_path``: target a specific .PcbDoc when several are open
        (resolved once for the whole batch). Otherwise the focused board
        is used.

        Args:
            placements: List of placement dicts, each with:
                footprint (required), library_path (required, .PcbLib),
                x, y (mils), designator, lib_reference, comment,
                rotation, layer, unique_id, and pad_nets
                (``{pad_name: net_name}``); omit unique_id/pad_nets for
                geometry-only, include them for a synced placement.
            board_path: Absolute .PcbDoc path to place onto (optional).

        Returns:
            Dict with ``placed`` (count), ``failed`` (count), ``total``.
        """
        recs: list[str] = []
        for p in placements:
            fp = str(p.get("footprint", "")).strip()
            lib = str(p.get("library_path", "")).strip()
            if not fp or not lib:
                continue
            fields = [f"footprint=={fp}", f"library_path=={lib}"]
            for key in ("lib_reference", "designator", "comment",
                        "layer", "unique_id"):
                v = str(p.get(key, "") or "").strip()
                if v:
                    fields.append(f"{key}=={v}")
            fields.append(f"x=={int(p.get('x', 0))}")
            fields.append(f"y=={int(p.get('y', 0))}")
            if p.get("rotation") is not None:
                fields.append(f"rotation=={p.get('rotation')}")
            pn = p.get("pad_nets")
            if pn:
                pn_str = "|".join(
                    f"{str(k).strip()}={str(v).strip()}"
                    for k, v in pn.items()
                    if str(k).strip() and str(v).strip()
                )
                if pn_str:
                    fields.append(f"pad_nets=={pn_str}")
            recs.append(";;".join(fields))

        if not recs:
            return {"error": "No valid placements (need footprint + "
                    "library_path)", "placed": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.place_components",
            {"placements": "~~".join(recs), "board_path": board_path},
        )

    @mcp.tool()
    async def pcb_place_angular_dimension(
        center_x: int,
        center_y: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int = 100,
        layer: str = "TopOverlay",
    ) -> dict[str, Any]:
        """Place an angular dimension (angle between two reference directions).

        Args:
            center_x, center_y: Vertex of the angle in mils
            x1, y1: First reference direction endpoint in mils
            x2, y2: Second reference direction endpoint in mils
            radius: Arc radius at which to draw the dimension in mils
            layer: Layer (default "TopOverlay")

        Returns:
            Dictionary confirming the angular dimension
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_angular_dimension",
            {
                "center_x": str(center_x),
                "center_y": str(center_y),
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "radius": str(radius),
                "layer": layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_radial_dimension(
        center_x: int,
        center_y: int,
        radius: int,
        layer: str = "TopOverlay",
    ) -> dict[str, Any]:
        """Place a radial dimension around a center point with a given radius.

        Args:
            center_x, center_y: Center point in mils
            radius: Radius to dimension in mils
            layer: Layer (default "TopOverlay")

        Returns:
            Dictionary confirming the radial dimension
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_radial_dimension",
            {
                "center_x": str(center_x),
                "center_y": str(center_y),
                "radius": str(radius),
                "layer": layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_distribute_components(
        designators: str,
        axis: str = "x",
        start: int = 0,
        end: int = 1000,
    ) -> dict[str, Any]:
        """Evenly space components along an axis.

        Moves each named component so its X (or Y) coordinate lands at
        equally-spaced stops from `start` to `end`. Order follows the
        designators list, designators="R1,R2,R3" with start=0 end=200
        places R1 at 0, R2 at 100, R3 at 200 on the chosen axis. Y (or X)
        is untouched.

        Args:
            designators: Comma-separated list of component designators
            axis: "x" or "y" (default "x")
            start: First position in mils
            end: Last position in mils

        Returns:
            Dictionary with distribution result
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.distribute_components",
            {
                "designators": designators,
                "axis": axis,
                "start": str(start),
                "end": str(end),
            },
        )
        return result

    @mcp.tool()
    async def pcb_get_rule_properties(name: str) -> dict[str, Any]:
        """Read properties of a named PCB design rule.

        Returns metadata (name, rule_kind, enabled, priority) plus a
        ``descriptor`` string that contains the rule's constraint values
        in human-readable form, e.g.:

            "Width Constraint (Min=0.102mm) (Max=5.08mm) (Preferred=0.127mm) (All)"
            "Clearance Constraint (Gap=0.127mm) (All),(All)"
            "Hole Size Constraint (Min=0.1mm) (Max=4mm) (All)"
            "Routing Via (Templates Used To Check Via: v30h10m0mx0, ...) (All)"

        Constraint values live on per-kind subtype interfaces
        (IPCB_ClearanceConstraint, IPCB_MaxMinWidthConstraint, ...) which
        cannot be safely accessed from a base IPCB_Rule reference in
        DelphiScript, so we surface the descriptor string instead. Parse
        it client-side if you need numeric values.

        Args:
            name: Design rule name (e.g., "Clearance", "Width", "RoutingVias").

        Returns:
            Dict with name, rule_kind, enabled, priority, descriptor.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.get_rule_properties", {"name": name}
        )

    @mcp.tool()
    async def pcb_set_rule_properties(
        name: str,
        enabled: bool | None = None,
        scope1: str | None = None,
        scope2: str | None = None,
        comment: str | None = None,
        gap_mils: int | None = None,
        min_width_mils: int | None = None,
        max_width_mils: int | None = None,
        favored_width_mils: int | None = None,
        min_hole_size_mils: int | None = None,
        max_hole_size_mils: int | None = None,
    ) -> dict[str, Any]:
        """Update metadata AND constraint values of a named PCB design rule.

        Metadata fields always apply. Constraint fields are dispatched
        by the rule's underlying RuleKind:

        - Clearance (kind 0), ComponentClearance (kind 24), and
          HoleToHoleClearance (kind 52): ``gap_mils``. All three share
          the Gap property on IPCB_ClearanceConstraint.
        - Width (kind 2): ``min_width_mils`` / ``max_width_mils`` /
          ``favored_width_mils`` (applied to every layer).
        - HoleSize (kind 42): ``min_hole_size_mils`` / ``max_hole_size_mils``.

        Pass only the parameters you want to change; everything else
        stays untouched. Each successful field write increments
        ``properties_updated`` in the response.

        NOTE: Priority is NOT writable from this tool. ``IPCB_Rule.Priority``
        is a read-only function in the PCB scripting API
        (``Function Priority : TRulePrecedence``), not a property; there
        is no ``SetState_Priority`` or ``SetPriority`` method either.
        Assigning to it crashes the DelphiScript engine. To reorder rule
        priorities, drag them in Altium's UI (PCB > Rules and Constraints
        Editor; rules earlier in a category have higher priority).

        Args:
            name: Rule name to update.
            enabled: Whether DRC enforces the rule.
            scope1 / scope2: Rule scope query expressions.
            comment: Free-form comment.
            gap_mils: Clearance gap. Applies to Clearance,
                ComponentClearance, and HoleToHoleClearance rules.
            min_width_mils / max_width_mils / favored_width_mils:
                Width constraint values (applied to every layer).
            min_hole_size_mils / max_hole_size_mils: HoleSize
                constraint limits.

        Returns:
            Dict with name, rule_kind, and properties_updated count.
        """
        params: dict[str, Any] = {"name": name}
        for key, value in [
            ("scope1", scope1),
            ("scope2", scope2),
            ("comment", comment),
            ("gap_mils", gap_mils),
            ("min_width_mils", min_width_mils),
            ("max_width_mils", max_width_mils),
            ("favored_width_mils", favored_width_mils),
            ("min_hole_size_mils", min_hole_size_mils),
            ("max_hole_size_mils", max_hole_size_mils),
        ]:
            if value is not None:
                params[key] = str(value)
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"

        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.set_rule_properties", params
        )

    @mcp.tool()
    async def pcb_place_embedded_board(
        child_path: str,
        x: int,
        y: int,
        rows: int = 1,
        cols: int = 1,
        row_spacing_mils: int = 0,
        col_spacing_mils: int = 0,
        mirror: bool = False,
        layer: str = "TopLayer",
    ) -> dict[str, Any]:
        """Place an embedded-board array (panel) referencing a child PCB.

        An embedded board is a grid of copies of a child .PcbDoc file,
        used for panelization (multi-up arrays, step-and-repeat).

        Args:
            child_path: Full path to the child .PcbDoc file.
            x, y: Bottom-left corner of the array in mils.
            rows, cols: Grid size (default 1x1).
            row_spacing_mils, col_spacing_mils: Gap between array cells.
            mirror: Flip the child board over when True.
            layer: Placement layer for the embedded-board primitive.

        Returns:
            Dict with success, child_path, rows, cols, x, y.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "pcb.place_embedded_board",
            {
                "child_path": child_path,
                "x": str(x),
                "y": str(y),
                "rows": str(rows),
                "cols": str(cols),
                "row_spacing_mils": str(row_spacing_mils),
                "col_spacing_mils": str(col_spacing_mils),
                "mirror": "true" if mirror else "false",
                "layer": layer,
            },
        )
