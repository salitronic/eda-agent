# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tools for a company's own conventions: IPC footprint names, its naming
policy, datasheet pin tables, its design rules, Altium rule exports, and
comparing two plans.

The conventions are the user's files in EDA_AGENT_POLICY_DIR (``naming.json``
and ``rules/*.json``). These tools only read them, and with none set they
enforce nothing. Designs are read through the bridge like everywhere else.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional, Union

from ..bridge import get_bridge


def _unset(what: str) -> dict[str, Any]:
    return {"ok": False, "reason": f"there is no {what}: add it to EDA_AGENT_POLICY_DIR "
            "(naming.json or rules/<name>.json)"}


async def _read_design(project_path: str, wanted: set[str]) -> tuple[dict, list[str]]:
    """The inputs a rules file needs, read from the open project and board."""
    bridge = get_bridge()
    scope: dict[str, Any] = {"limit": "100000"}
    if project_path:
        scope["project_path"] = project_path
    data: dict[str, Any] = {}
    remarks: list[str] = []
    if wanted & {"symbols", "symbol_params"}:
        bom = await bridge.send_command_async("project.get_bom", scope, timeout=300.0) or {}
        data["symbols"] = [{"designator": row["designator"], "lib_ref": row.get("lib_ref", "")}
                           for row in bom.get("components", []) if row.get("designator")]
    if "symbol_params" in wanted:
        data["symbol_params"] = {}
        if data["symbols"]:
            ask: dict[str, Any] = {
                "designators": "~~".join(s["designator"] for s in data["symbols"]),
                "with_pin_nets": "false", **({"project_path": project_path}
                                            if project_path else {})}
            info = await bridge.send_command_async(
                "project.get_component_info_batch", ask, timeout=300.0) or {}
            data["symbol_params"] = {row.get("designator"): row.get("parameters") or {}
                                     for row in info.get("components", [])}
    if "nets" in wanted:
        reply = await bridge.send_command_async("project.get_nets", scope, timeout=300.0) or {}
        if reply.get("limit_hit"):
            remarks.append("the net list was cut at its limit, so net checks saw part of it")
        pins: dict[str, list[str]] = {}
        for row in reply.get("pins", []):
            if row.get("net"):
                pins.setdefault(row["net"], []).append(f"{row.get('component')}.{row.get('pin')}")
        data["nets"] = [{"name": name, "pins": found} for name, found in pins.items()]
    board_inputs = wanted & {"placed", "board_rules"}
    try:
        if "placed" in board_inputs:
            reply = await bridge.send_command_async("pcb.get_components", {}, timeout=120.0)
            data["placed"] = [{"designator": c.get("designator", ""), "side": c.get("layer", "")}
                              for c in (reply or {}).get("components", [])]
        if "board_rules" in board_inputs:
            reply = await bridge.send_command_async("pcb.get_design_rules", {}, timeout=120.0)
            data["board_rules"] = [{"name": r.get("name", ""),
                                    "descriptor": r.get("descriptor", ""),
                                    "enabled": r.get("enabled", True)}
                                   for r in (reply or {}).get("rules", [])]
    except Exception as exc:  # noqa: BLE001 - no board open: its checks do not run
        remarks.append(f"the board could not be read ({exc}), so its checks did not run")
        for key in board_inputs:
            data.pop(key, None)
    return data, remarks


async def _library_names(library_path: str) -> list[str]:
    bridge = get_bridge()
    if library_path.lower().endswith(".pcblib"):
        reply = await bridge.send_command_async(
            "library.get_footprints", {"library_path": library_path}, timeout=120.0)
        return [f["name"] if isinstance(f, dict) else str(f)
                for f in (reply or {}).get("footprints", [])]
    reply = await bridge.send_command_async(
        "library.get_components", {"library_path": library_path}, timeout=120.0)
    return [c.get("name", "") for c in (reply or {}).get("components", [])]


def _naming():
    """(policy or None, refusal or None)."""
    from ..design.library_conventions import NamingPolicy
    try:
        policy = NamingPolicy.load()
    except (OSError, ValueError) as exc:
        return None, {"ok": False, "reason": f"naming.json could not be read: {exc}"}
    return policy, (None if policy else _unset("naming policy"))


def register_convention_tools(mcp) -> None:

    @mcp.tool()
    async def footprint_read_ipc_name(name: str) -> dict[str, Any]:
        """Decode an IPC-7351 footprint name into its package, in mm.

        CAPC1005X55N: a 1.0 x 0.5 mm chip, 0.55 mm high. SOT95P280X130-5N:
        five pins at 0.95 mm pitch across 2.80 mm, 1.30 mm high.
        QFN50P400X400X100-21N: 21 pads at 0.50 mm on a 4 x 4 mm body.

        Returns:
            ``ipc_name`` (whether it decodes) and the numbers: ``family``
            (chip, two-sided, four-sided), ``pins``, ``pitch``, ``span``,
            ``length``/``width``, ``height`` and ``density``.
        """
        from ..design.library_conventions import read_ipc_name
        decoded = read_ipc_name(name)
        return {"name": name, "ipc_name": decoded is not None, **(decoded or {})}

    @mcp.tool()
    async def lib_verify_ipc_names(library_path: str, footprints: Optional[list[str]] = None,
                                   limit: int = 200) -> dict[str, Any]:
        """Check that every footprint with an IPC-7351 name has the pads the
        name claims: the pin count and, for leaded packages, the pitch.

        Footprints whose names are not IPC names are listed separately, not
        failed. The library is read through Altium and not changed.

        Args:
            library_path: The .PcbLib.
            footprints: Just these; all of them by default.
            limit: The most footprints read.

        Returns:
            ``checked``, ``mismatched`` (each with ``issues``), ``matched``,
            ``not_ipc_names`` and ``cut_short``.
        """
        from ..design.library_conventions import compare_ipc_name
        names = list(footprints or []) or await _library_names(library_path)
        result: dict[str, Any] = {"library_path": library_path, "checked": 0, "matched": [],
                                  "mismatched": [], "not_ipc_names": [],
                                  "cut_short": len(names) > limit}
        bridge = get_bridge()
        for name in names[:max(1, limit)]:
            pads = await bridge.send_command_async(
                "library.get_pad_geometry",
                {"footprint_name": name, "library_path": library_path}, timeout=120.0)
            verdict = compare_ipc_name(name, (pads or {}).get("pads", []))
            result["checked"] += 1
            if not verdict["ipc_name"]:
                result["not_ipc_names"].append(name)
            elif verdict["matches"]:
                result["matched"].append(name)
            else:
                result["mismatched"].append(verdict)
        return result

    @mcp.tool()
    async def lib_check_naming(kind: str, names: Optional[list[str]] = None,
                               library_path: str = "") -> dict[str, Any]:
        """Check names against the company naming policy (naming.json in
        EDA_AGENT_POLICY_DIR); without one nothing is enforced.

        Args:
            kind: footprint, symbol or component.
            names: The names to check; or library_path to take them from a
                .PcbLib (footprints) or .SchLib (symbols).
            library_path: The library to take names from.

        Returns:
            ``checked``, ``off_policy`` ({name, why}) and the ``pattern``.
        """
        from ..design.library_conventions import NAME_KINDS
        if kind not in NAME_KINDS:
            return {"ok": False, "reason": f"kind is one of {', '.join(NAME_KINDS)}"}
        policy, refusal = _naming()
        if refusal:
            return refusal
        if not policy.pattern(kind):
            return {"ok": True, "checked": 0, "note": f"the policy has no rule for {kind} names"}
        found = list(names or []) or (await _library_names(library_path) if library_path
                                      else [])
        off = [{"name": n, "why": why} for n in found if (why := policy.breach(kind, n))]
        return {"ok": True, "kind": kind, "checked": len(found), "off_policy": off,
                "pattern": policy.pattern(kind)}

    @mcp.tool()
    async def lib_make_name(kind: str, tokens: dict[str, str]) -> dict[str, Any]:
        """Build a name from the naming policy's template for a kind and check
        it against the policy's pattern.

        Args:
            kind: footprint, symbol or component.
            tokens: Values for the template, e.g. {"mpn": "TPS62130RGTR",
                "manufacturer": "Texas Instruments"}; a manufacturer gives
                ``mfr_short`` from the policy's abbreviations.
        """
        policy, refusal = _naming()
        if refusal:
            return refusal
        try:
            return {"ok": True, "kind": kind, "name": policy.make(kind, tokens)}
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}

    @mcp.tool()
    async def lib_read_pin_table(table_csv: str,
                                 package_pins: Optional[int] = None) -> dict[str, Any]:
        """Check a pin table copied from a datasheet and turn it into symbol pins.

        CSV with the header ``number,name,type``, optionally ``side``
        (left, right, auto) and ``description``. Types: input, output, io,
        bidirectional, passive, power, open_collector, open_emitter, hiz,
        and the usual short spellings. ``left_pins`` and ``right_pins`` go
        straight to lib_create_ic_symbol. Compare the table with the
        datasheet page it came from before trusting it.

        Args:
            table_csv: The table.
            package_pins: The package's pin count, to catch a missing pin.

        Returns:
            ``ok``, ``errors`` (unusable as it is), ``warnings`` (worth a
            look), ``pins``, and ``left_pins`` / ``right_pins``.
        """
        from ..design.library_conventions import read_pin_table, review_pin_table, split_sides
        try:
            rows = read_pin_table(table_csv)
        except (ValueError, csv.Error) as exc:
            return {"ok": False, "errors": [str(exc)]}
        errors, warnings = review_pin_table(rows, package_pins)
        result: dict[str, Any] = {"ok": not errors, "errors": errors, "warnings": warnings,
                                  "pins": len(rows)}
        if not errors:
            result.update(split_sides(rows))
        return result

    @mcp.tool()
    async def design_check_company_rules(rules: Union[str, dict] = "", project_path: str = "",
                                         data: Optional[dict] = None) -> dict[str, Any]:
        """Check the open design against one of the company's rules files.

        Rules files are JSON in ``rules/`` under EDA_AGENT_POLICY_DIR; an
        empty ``rules`` lists them. Rules given as an object are checked
        for mistakes and then run, so a file can be tried before it is
        saved. Check kinds: params_present, param_matches, param_not_in,
        prefix_for_symbol, net_names, no_lone_pins, net_has_part,
        board_rule_present, parts_on_side, and note (guidance that never
        passes or fails).

        The design is read through Altium (BOM, parameters, nets, and the
        board's parts and rules when a board is open); ``data`` stands in
        for that read. A check whose input could not be read is listed in
        ``not_run`` and never counts as passed.

        Returns:
            ``passed`` (no error-level finding), ``tally``, ``findings``,
            ``notes``, ``not_run``, and ``remarks`` about the read.
        """
        from ..design.company_rules import (inputs_of, open_rules, problems_in,
                                            rule_files, run_rules)
        from ..design.library_conventions import policy_folder
        folder = policy_folder()
        if isinstance(rules, dict):
            trouble = problems_in(rules)
            if trouble:
                return {"ok": False, "reason": "the rules have mistakes", "problems": trouble}
            loaded = rules
        elif not rules:
            return {"ok": True, "rule_files": rule_files(folder)} if folder \
                else _unset("rules file")
        else:
            try:
                loaded = open_rules(folder, rules)
            except (OSError, ValueError) as exc:
                return {"ok": False, "reason": str(exc)}
        remarks: list[str] = []
        if data is None:
            data, remarks = await _read_design(project_path, inputs_of(loaded))
        return {**run_rules(loaded, data), "remarks": remarks}

    @mcp.tool()
    async def design_read_rules_export(path: str) -> dict[str, Any]:
        """Read an Altium design-rules export (.RUL): one record per rule,
        with its fields as Altium wrote them (they differ by rule kind and
        version). Reads only the file; the open board's own rules come from
        pcb_get_design_rules.
        """
        source = Path(path)
        if not source.is_file() or source.suffix.lower() != ".rul":
            return {"ok": False, "reason": "path names an existing .RUL file"}
        from ..design.company_rules import read_rules_export
        from ..sim.deck_runner import decode_output
        records = read_rules_export(decode_output(source))
        return {"ok": True, "path": str(source), "count": len(records), "rules": records}

    @mcp.tool()
    async def design_compare_plans(before: Union[str, dict],
                                   after: Union[str, dict]) -> dict[str, Any]:
        """Compare two DesignPlans: parts new, dropped or edited (library
        reference, value, footprint, maker, part number, sheet), nets new,
        dropped or rewired, and pins that changed net.

        Review an edit before drawing it, or hold a plan read back from the
        sheets (design_plan_from_sheet) against the plan it was drawn from.
        """
        from pydantic import ValidationError

        from ..design.plan import DesignPlan
        from ..design.plan_compare import compare_plans
        plans = {}
        for label, given in (("before", before), ("after", after)):
            try:
                plans[label] = DesignPlan.model_validate(
                    json.loads(given) if isinstance(given, str) else given)
            except (json.JSONDecodeError, ValidationError) as exc:
                return {"ok": False, "reason": f"{label}: {exc}"}
        return {"ok": True, **compare_plans(plans["before"], plans["after"])}
