# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium project to KiCad project: ``proj_convert_to_kicad``.

The conversion itself is ``convert.altium_kicad``; this registers it on the
Altium server, where the open board's rules, stack-up, net classes, layer
kinds and compiled netlist are read through the bridge first.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any

from ..bridge import get_bridge


async def _project_and_board(bridge, project_path: str, board_path: str) -> tuple[str, str]:
    if not project_path:
        reply = await bridge.send_command_async("project.get_project_path")
        project_path = str((reply or {}).get("project_path") or (reply or {}).get("path") or "")
    if not board_path and project_path:
        docs = await bridge.send_command_async("project.get_documents",
                                               {"project_path": project_path})
        rows = docs if isinstance(docs, list) else (docs or {}).get("documents") or []
        boards = [d.get("file_path") for d in rows
                  if str(d.get("file_path", "")).lower().endswith(".pcbdoc")]
        board_path = boards[0] if len(boards) == 1 else ""
    return project_path, board_path


def _kicad_nets(netlist: dict[str, Any]) -> dict[str, list[str]]:
    return {n["name"]: [f"{d['reference']}.{d['pin']}" for d in n["nodes"]]
            for n in netlist.get("nets") or []}


def _altium_nets(reply: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in (reply or {}).get("pins", []) if isinstance(reply, dict) else []:
        comp, pin, net = (str(p.get(k, "")).strip() for k in ("component", "pin", "net"))
        if comp and pin and net:
            out.setdefault(net, []).append(f"{comp}.{pin}")
    return out


#: How far a pad may land from KiCad's centre, in mils, before it counts.
PAD_TOLERANCE_MILS = 1.0


async def _unique_ids(mcp, documents: list[str]) -> dict[str, str]:
    from ..convert.kicad_altium.run import call

    out: dict[str, str] = {}
    for doc in documents:
        reply = await call(mcp, "obj_query", {
            "object_type": "eSchComponent", "properties": "Designator.Text,UniqueId",
            "scope": f"doc:{doc}"})
        for o in (reply or {}).get("objects", []) if isinstance(reply, dict) else []:
            ref, uid = str(o.get("Designator.Text", "")), str(o.get("UniqueId", ""))
            if ref and uid:
                out.setdefault(ref, uid)
    return out


async def _check_pads(mcp, board: dict[str, Any], placed: list[str]) -> dict[str, Any]:
    """Each placed part's pads read back from Altium against KiCad's."""
    from ..convert.kicad_altium import board as kboard
    from ..convert.kicad_altium.run import call

    want = {k: [list(p) for p in v] for k, v in kboard.pad_positions(board).items()}
    off: list[dict[str, Any]] = []
    checked = 0
    ours = set(placed)
    # Every pad on the board with its own position, in one read; a per-part
    # read by pad name gives every pad of a shared number the first one's.
    reply = await call(mcp, "obj_query", {"object_type": "ePadObject",
                                          "properties": "Name,X,Y,Component",
                                          "length_units": "mil"})
    for pad in (reply or {}).get("objects", []) if isinstance(reply, dict) else []:
        ref = str(pad.get("Component", ""))
        if ref not in ours:
            continue
        key = f"{ref}.{pad.get('Name', '')}"
        if not want.get(key):
            continue
        checked += 1
        got = (float(pad.get("X", 0)), float(pad.get("Y", 0)))
        # Several pads can share a number: each is paired with the nearest
        # KiCad pad of that number not yet taken.
        near = min(want[key], key=lambda p: math.dist(p, got))
        want[key].remove(near)
        if math.dist(near, got) > PAD_TOLERANCE_MILS:
            off.append({"pad": key, "kicad": near, "altium": list(got)})
    return {"checked": checked, "off": len(off), "examples": off[:10],
            "parts_off": sorted({o["pad"].split(".")[0] for o in off}),
            "same": checked > 0 and not off}


async def _check_libraries(mcp, project: dict[str, Any], lib: dict[str, Any],
                           schlib: str, pcblib: str) -> dict[str, Any]:
    """Every symbol and footprint read back from the new libraries and
    compared with KiCad's original (convert.kicad_altium.verify)."""
    from ..convert.kicad_altium import plan as kplan
    from ..convert.kicad_altium import verify
    from ..convert.kicad_altium.run import call

    differences: dict[str, list[str]] = {}
    checked = {"symbols": 0, "footprints": 0}

    await call(mcp, "app_set_active_document", {"file_path": schlib})
    wants: dict[str, dict] = {}
    for lid, altium_name in sorted(lib["symbols"].items()):
        try:
            wants[altium_name] = verify.expected_symbol(project["symbols"][lid])
        except (KeyError, ValueError, IndexError) as exc:
            differences[f"symbol {altium_name}"] = [f"KiCad's symbol could not be read: {exc}"]
    names = list(wants)
    details = await _each(mcp, "lib_get_component_details",
                          [{"component_name": n} for n in names], {"length_units": "mil"})
    queries, owners = [], []
    for name in names:
        for part in range(1, wants[name]["parts"] + 1):
            scope = f"lib_component:{name}@{part}"
            queries += [
                {"object_type": "eRectangle", "scope": scope,
                 "properties": "Location.X,Location.Y,Corner.X,Corner.Y"},
                {"object_type": "eLine", "scope": scope,
                 "properties": "Location.X,Location.Y,Corner.X,Corner.Y"},
                {"object_type": "sch_arc", "scope": scope,
                 "properties": "Location.X,Location.Y,Radius,StartAngle,EndAngle"},
                {"object_type": "eLabel", "scope": scope, "properties": "Text"}]
            owners.append((name, part))
    rows = await _each(mcp, "obj_query", queries, {"length_units": "mil"})
    art: dict[str, dict[int, dict]] = {n: {} for n in names}
    for k, (name, part) in enumerate(owners):
        objs = [(r or {}).get("objects") or [] for r in rows[4 * k:4 * k + 4]]
        art[name][part] = verify.symbol_art_from_rows(*objs)
    for name, got in zip(names, details):
        if not isinstance(got, dict) or "pins" not in got:
            differences[f"symbol {name}"] = [f"could not be read back: {got}"]
            continue
        checked["symbols"] += 1
        found = verify.compare_symbol(wants[name], got["pins"], art[name])
        if found:
            differences[f"symbol {name}"] = found

    first_fp: dict[str, dict] = {}
    for f in (project.get("board") or {}).get("footprints") or []:
        first_fp.setdefault(f["lib_id"], f)
    await call(mcp, "app_set_active_document", {"file_path": pcblib})
    footprints = sorted(lib["footprints"].items())
    each = [{"footprint_name": n} for _, n in footprints]
    shared = {"library_path": pcblib, "length_units": "mm"}
    pads_all = await _each(mcp, "lib_get_pad_geometry", each, shared)
    prims_all = await _each(mcp, "lib_get_footprint_primitives", each,
                            {**shared, "kinds": "track|arc|region|fill|text"})
    for (fid, altium_name), pads, prims in zip(footprints, pads_all, prims_all):
        want = verify.expected_footprint(kplan.library_footprint(first_fp[fid]))
        if not isinstance(pads, dict) or "pads" not in pads:
            differences[f"footprint {altium_name}"] = [f"could not be read back: {pads}"]
            continue
        checked["footprints"] += 1
        found = verify.compare_footprint(want, pads["pads"], prims if isinstance(prims, dict) else {})
        if found:
            differences[f"footprint {altium_name}"] = found
    return {"same": not differences, "checked": checked,
            "differ": len(differences), "differences": differences}


async def _each(mcp, tool: str, items: list[dict[str, Any]],
                shared: dict[str, Any]) -> list[Any]:
    """``tool`` for every item, 200 to a call, each item's reply in order
    (an empty dict where a call gave no list back)."""
    from ..convert.kicad_altium.run import MAX_ITEMS, call

    out: list[Any] = []
    for i in range(0, len(items), MAX_ITEMS):
        chunk = items[i:i + MAX_ITEMS]
        reply = await call(mcp, tool, {**shared, "items": chunk})
        got = (reply or {}).get("items") if isinstance(reply, dict) else None
        if not isinstance(got, list) or len(got) != len(chunk):
            got = [reply] * len(chunk)
        out += got
    return out


async def _import_board(mcp, project, lib, sch, out_dir, name, prj, pcblib, altium_nets):
    """The board stage: the PcbDoc, its outline, stack, parts, copper and
    rules, then every pad checked against KiCad's and a DRC."""
    from ..convert.kicad_altium import board as kboard
    from ..convert.kicad_altium.run import call, run_steps

    board = project["board"]
    doc = os.path.join(out_dir, name + ".PcbDoc")
    pin_net = {pin: net for net, pins in altium_nets.items() for pin in pins}
    nets = kboard.net_map(board, pin_net)
    sheets = [os.path.join(out_dir, d) for d in sch["documents"].values()]
    ids = await _unique_ids(mcp, sheets)
    plan = kboard.board_plan(board, doc, pcblib, lib["footprints"], nets, ids,
                             project.get("net_classes") or {})
    steps = [{"tool": "app_create_document", "args": {"kind": "PCB", "file_path": doc,
                                                      "project_path": prj}}] + plan["steps"]
    stage = await run_steps(mcp, steps, "board")
    out: dict[str, Any] = {"ok": stage["ok"], "stage": stage, "document": doc,
                           "warnings": plan["warnings"],
                           "renamed_nets": {k: v for k, v in nets.items() if k != v}}
    if not stage["ok"]:
        return out
    placed = [p["designator"] for p in plan["placements"] if p.get("designator")]
    out["pads"] = await _check_pads(mcp, board, placed)
    drc = await call(mcp, "pcb_run_drc", {})
    if isinstance(drc, dict):
        out["drc"] = {k: drc.get(k) for k in ("violation_count", "verdict", "report_rules")
                      if k in drc}
    out["ok"] = out["pads"]["same"]
    return out


def register_convert_tools(mcp):

    @mcp.tool()
    async def proj_import_from_kicad(
        kicad_project: str,
        output_dir: str,
        dry_run: bool = True,
        kicad_cli: str = "",
    ) -> dict[str, Any]:
        """Rebuild a KiCad project in Altium: libraries, schematic, board.

        Altium draws everything through this server's own tools: each
        symbol and footprint the project uses, as KiCad draws it (no grid
        snapping), into a SchLib and PcbLib of the new project's own, then
        every sheet from a template (parts where KiCad put them,
        mirrored as there, wires, power ports, net labels, ports, sheet
        symbols and entries, No ERC markers, the parts' fields), then the
        board (outline, layer stack, parts on their side and angle, tracks,
        arcs, vias, zones as pours, net classes and their rules). KiCad's
        files are only read. Global labels become power ports.

        Checks: every symbol and footprint read back from the new
        libraries against KiCad's own (pins, body drawing, pads, drills,
        drawing layers); any difference stops the import before a sheet is
        drawn and is listed in ``libraries.differences``, to be fixed, not
        passed over. Altium's compiled netlist against KiCad's (kicad-cli), pin
        group by pin group (``nets.same``); every pad read back against
        KiCad's centre (``board.pads``); a DRC (``board.drc``). Copper
        takes the Altium name of the net its pads are on
        (``board.renamed_nets``).

        Args:
            kicad_project: The .kicad_pro, its root .kicad_sch, or its
                folder.
            output_dir: An empty folder for the Altium project.
            dry_run: Only plan (default): step counts, what is not
                carried, and each sheet's template checked against KiCad's
                own pin positions (``sheets``), with Altium untouched.
            kicad_cli: kicad-cli.exe for the netlist check; empty = the
                newest KiCad installed.

        Returns:
            ``ok``, ``project`` (the .PrjPcb), ``stages`` (each with
            ``done``/``of`` and the step that failed, if one did),
            ``sheets``, ``warnings`` and ``nets``.
        """
        from ..convert.altium_kicad.pipeline import find_kicad_cli
        from ..convert.kicad_altium import plan as kplan
        from ..convert.kicad_altium.read import read_project
        from ..convert.kicad_altium.run import call, failed, run_steps
        from ..core.kicad_netlist import get_schematic_netlist
        from ..design import sch_template as st

        try:
            project = await asyncio.to_thread(read_project, kicad_project)
        except (ValueError, OSError) as exc:
            return {"ok": False, "reason": f"cannot read {kicad_project}: {exc}"}
        if not project["sheets"]:
            return {"ok": False, "reason": "the project has no schematic"}
        name = kplan._clean(project["name"])
        out_dir = os.path.abspath(output_dir)
        prj = os.path.join(out_dir, name + ".PrjPcb")
        schlib = os.path.join(out_dir, name + ".SchLib")
        pcblib = os.path.join(out_dir, name + ".PcbLib")
        lib = kplan.library_plan(project, schlib, pcblib)
        sch = kplan.schematic_plan(project, out_dir, prj, lib, schlib)

        sheets = []
        for s in project["sheets"]:
            doc = sch["documents"][s["file"]]
            d = st.build(sch["templates"][doc], kplan.preview_pins(s), grid=kplan.SHEET_GRID)
            sheets.append({"document": doc, "parts": len(s["parts"]),
                           "errors": [f"{e['check']}: {e['where']}: {e['detail']}"
                                      for e in d.errors][:10]})
        report: dict[str, Any] = {
            "ok": True, "project": prj, "dry_run": dry_run,
            "symbols": len(lib["symbols"]), "footprints": len(lib["footprints"]),
            "steps": {"libraries": len(lib["steps"]), "schematic": len(sch["steps"])},
            "sheets": sheets, "warnings": project["warnings"] + lib["warnings"] + sch["warnings"],
        }
        if project.get("board"):
            from ..convert.kicad_altium import board as kboard

            b = project["board"]
            preview = kboard.board_plan(b, os.path.join(out_dir, name + ".PcbDoc"), pcblib,
                                        lib["footprints"], {n: n for n in b["nets"]}, {},
                                        project.get("net_classes") or {})
            report["steps"]["board"] = len(preview["steps"]) + 1
            report["board_counts"] = {k: len(b[k]) for k in (
                "footprints", "tracks", "arcs", "vias", "zones")}
            report["warnings"] += preview["warnings"]
        if any(s["errors"] for s in sheets):
            report["ok"] = False
            report["reason"] = ("a sheet's template does not hold together; nothing would "
                                "be drawn on it (see sheets[].errors)")
        if dry_run or not report["ok"]:
            return report

        if os.path.isdir(out_dir) and os.listdir(out_dir):
            return {**report, "ok": False, "reason": f"{out_dir} is not empty"}
        # Altium finds a library by its file name: an open document of the
        # same name elsewhere is taken for the new one and every part fails
        # to resolve.
        ours = {os.path.basename(p).lower() for p in (prj, schlib, pcblib)}
        ours |= {d.lower() for d in sch["documents"].values()}
        listed = await call(mcp, "app_list_documents", {})
        rows = listed.get("result", []) if isinstance(listed, dict) else listed or []
        clash = sorted({r.get("file_path", "") for r in rows if isinstance(r, dict)
                        and r.get("loaded") and str(r.get("file_name", "")).lower() in ours})
        if clash:
            return {**report, "ok": False, "open_with_the_same_name": clash, "reason": (
                "documents with the names this import creates are open; close them "
                "first (app_close_document), or Altium takes them for the new ones")}
        os.makedirs(out_dir, exist_ok=True)
        stages = []
        setup = [
            {"tool": "proj_create", "args": {"project_path": prj}},
            {"tool": "app_create_document", "args": {"kind": "SCHLIB", "file_path": schlib,
                                                     "project_path": prj}},
            {"tool": "app_create_document", "args": {"kind": "PCBLIB", "file_path": pcblib,
                                                     "project_path": prj}},
        ]
        for stage, steps in (("project", setup),
                             ("libraries", lib["steps"] + [{"tool": "app_save_all", "args": {}}]),
                             ("schematic", sch["steps"])):
            result = await run_steps(mcp, steps, stage)
            stages.append(result)
            if not result["ok"]:
                return {**report, "ok": False, "stages": stages}
            if stage == "libraries":
                # Nothing is drawn from a library that differs from KiCad's.
                report["libraries"] = await _check_libraries(mcp, project, lib, schlib, pcblib)
                if not report["libraries"]["same"]:
                    return {**report, "ok": False, "stages": stages, "reason": (
                        "symbols or footprints built in Altium differ from KiCad's "
                        "(libraries.differences); nothing was drawn from them")}
        report["stages"] = stages

        await call(mcp, "proj_compile", {"project_path": prj})
        altium = _altium_nets(await get_bridge().send_command_async(
            "project.get_nets", {"limit": "100000"}))
        if project.get("board"):
            board_report = await _import_board(mcp, project, lib, sch, out_dir, name,
                                               prj, pcblib, altium)
            report["board"] = board_report
            stages.append(board_report["stage"])
            if not board_report["ok"]:
                report["ok"] = False
        cli = find_kicad_cli(kicad_cli)
        if not cli:
            report["nets"] = {"checked": False, "reason": "kicad-cli was not found"}
        else:
            try:
                kicad = _kicad_nets(await get_schematic_netlist(cli, project["files"]["sch"]))
                report["nets"] = kplan.compare_nets(kicad, altium)
                report["ok"] = report["ok"] and report["nets"]["same"]
            except RuntimeError as exc:
                report["nets"] = {"checked": False, "reason": str(exc)}
        saved = await call(mcp, "app_save_all", {})
        if failed(saved):
            report["ok"] = False
            report["save"] = saved
        return report

    @mcp.tool()
    async def proj_convert_to_kicad(
        output_dir: str,
        project_path: str = "",
        board_path: str = "",
        kicad_cli: str = "",
        remove_title_block: bool = True,
    ) -> dict[str, Any]:
        """Convert an Altium project into a clean KiCad project.

        KiCad's own Altium importer brings the project in (kicad.exe is
        driven through its import dialogs). The result is then made into
        a project to keep:
        - one symbol per part in project libraries;
        - power ports as power symbols;
        - Value, Footprint and Datasheet filled in;
        - sheets on the 1.27 mm grid, wire stubs trimmed;
        - a flat project given a root sheet;
        - board nets named as the schematic names them;
        - rules, stack-up and net classes read from the open Altium board;
        - footprints in project libraries, mechanical layers mapped by kind;
        - pours filled with Altium's neck width and island removal;
        - Altium's variants as KiCad variants (not fitted = DNP, varied
          values as field values).
        The Altium files are only read.

        Every step is checked against the pin-to-net map. ``ok`` is true
        only when the schematic, the board and Altium's own compiled
        netlist put the same pins together. Takes a few minutes: KiCad
        opens on screen while it imports.

        Args:
            output_dir: An empty folder for the KiCad project.
            project_path: The .PrjPcb; empty = the focused project.
            board_path: Its .PcbDoc; empty = the project's only board.
            kicad_cli: kicad-cli.exe; empty = the newest KiCad installed.
            remove_title_block: Take out what each sheet's Altium template
                drew (border, title block, logo), found by the template
                objects' own boxes, so what the user drew stays; KiCad
                draws its own.

        Returns:
            ``ok``, ``project`` (the .kicad_pro), ``steps`` (what each step
            did, with the ``check_*`` pin comparisons, ERC and DRC counts
            and the rules not ported), and ``stage`` / ``reason`` when a
            step stopped it.
        """
        from ..convert.altium_kicad import altium_side, pipeline

        bridge = get_bridge()
        project_path, board_path = await _project_and_board(bridge, project_path, board_path)
        if not project_path or not os.path.isfile(project_path):
            return {"ok": False, "reason": "no Altium project: pass project_path"}
        if not board_path:
            return {"ok": False, "reason": "the project has no single board: pass board_path"}
        if os.path.isdir(output_dir) and os.listdir(output_dir):
            return {"ok": False, "reason": f"{output_dir} is not empty"}
        facts = await altium_side.read_board(bridge, board_path, project_path)
        report = await asyncio.to_thread(pipeline.convert, project_path, output_dir, facts,
                                         kicad_cli, remove_title_block)
        report["altium"] = {"project": project_path, "board": board_path,
                            "pins_read": len(facts.get("pins") or [])}
        return report
