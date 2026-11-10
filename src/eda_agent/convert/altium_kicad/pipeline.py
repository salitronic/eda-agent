# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The conversion, step by step, with the pin-to-net map checked throughout.

``convert`` runs every step on a new, empty output folder; the Altium
project is only read. Each step's result goes into the report, and the
conversion stops at the first step that fails, saying which.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from typing import Any, Optional

from . import altium_side, checks, footprints, hierarchy, importer, schematic
from .tree import find, first, read, write

#: Where KiCad installs on Windows, newest version first.
_INSTALL_GLOB = r"C:\Program Files\KiCad\*\bin\kicad-cli.exe"


def find_kicad_cli(explicit: str = "") -> str:
    if explicit and os.path.isfile(explicit):
        return explicit

    def version(path: str) -> tuple:
        m = re.search(r"KiCad\\([\d.]+)\\", path)
        return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)

    found = sorted(glob.glob(_INSTALL_GLOB), key=version, reverse=True)
    return found[0] if found else ""


def _cli(cli: str, *args: str, timeout: float = 600.0) -> subprocess.CompletedProcess:
    from ...noconsole import no_window
    return subprocess.run([cli, *args], capture_output=True, text=True,
                          timeout=timeout, **no_window())


def _kicad_python(cli: str) -> str:
    return os.path.join(os.path.dirname(cli), "python.exe")


def erc(cli: str, root_sheet: str, out_json: str) -> dict[str, Any]:
    """KiCad's ERC as JSON, counted by violation type."""
    _cli(cli, "sch", "erc", "--format", "json", "--severity-all",
         "-o", out_json, root_sheet)
    with open(out_json, encoding="utf-8") as fh:
        data = json.load(fh)
    counts: dict[str, int] = {}
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            counts[v.get("type", "?")] = counts.get(v.get("type", "?"), 0) + 1
    return {"data": data, "by_type": counts}


def netlist(cli: str, root_sheet: str, out_xml: str) -> str:
    r = _cli(cli, "sch", "export", "netlist", "--format", "kicadxml", "-o", out_xml, root_sheet)
    if r.returncode != 0 or not os.path.isfile(out_xml):
        raise RuntimeError(f"netlist export failed: {r.stderr[-400:]}")
    return out_xml


def power_flags(project_dir: str, project: str, cli: str, root_sheet: str,
                work: str) -> dict[str, Any]:
    """PWR_FLAGs where ERC finds a power input nothing drives.

    KiCad's ERC JSON gives positions in hundredths of the sheet's
    millimetres (KiCad 10.0.1): x 1.3462 is 134.62 mm. The flag goes on
    the pin ERC names, one per pin reported."""
    result = erc(cli, root_sheet, os.path.join(work, "erc_power.json"))
    wanted: dict[str, list] = {}
    for sheet in result["data"].get("sheets", []):
        path = sheet.get("path", "/")
        for v in sheet.get("violations", []):
            if v.get("type") != "power_pin_not_driven" or not v.get("items"):
                continue
            pos = v["items"][0].get("pos") or {}
            wanted.setdefault(path, []).append((round(pos.get("x", 0) * 100, 4),
                                                round(pos.get("y", 0) * 100, 4)))
    if not wanted:
        return {"added": 0}
    share = os.path.join(os.path.dirname(os.path.dirname(cli)), "share", "kicad")
    flag = schematic.power_flag_definition(share)
    if flag is None:
        return {"added": 0, "reason": "KiCad's power library has no PWR_FLAG"}
    lib = f"{project}_Power"
    lib_id = f"{lib}:PWR_FLAG"
    added = 0
    # ERC names a sheet by its path; the root sheet is "/", the others by
    # name. Positions are matched to the sheet that has a pin there.
    for path in schematic.sheets_in(project_dir):
        tree = read(path)
        points = schematic.connection_points(tree)
        mine = [p for pts in wanted.values() for p in pts if (round(p[0], 3), round(p[1], 3)) in points]
        if not mine:
            continue
        for p in mine:
            added += 1
            schematic.add_power_flag(tree, p, f"#FLG{added:02d}", lib_id, project)
        table = first(tree, "lib_symbols")
        if table is not None and not any(s[1] == lib_id for s in find(table, "symbol")):
            import copy
            d = copy.deepcopy(flag)
            d[1] = lib_id
            table.append(d)
        write(path, tree)
    lib_path = os.path.join(project_dir, f"{lib}.kicad_sym")
    if added and os.path.isfile(lib_path):
        library = read(lib_path)
        if not any(s[1] == "PWR_FLAG" for s in find(library, "symbol")):
            import copy
            library.append(copy.deepcopy(flag))
            write(lib_path, library)
    return {"added": added}


def _insert_stackup(board_text: str, block: str, thickness: float) -> tuple[str, bool]:
    if "(stackup" in board_text:
        return board_text, False
    i = board_text.find("(setup")
    if i < 0:
        return board_text, False
    j = i + len("(setup")
    board_text = board_text[:j] + "\n\t\t" + block + board_text[j:]
    board_text = re.sub(r"\(thickness [\d.]+\)", f"(thickness {thickness})", board_text, count=1)
    return board_text, True


def _project_classes(pro_path: str, classes: list[dict], names: dict[str, str]) -> int:
    """Net classes into the .kicad_pro, members by pattern."""
    if not classes or not os.path.isfile(pro_path):
        return 0
    with open(pro_path, encoding="utf-8") as fh:
        pro = json.load(fh)
    ns = pro.setdefault("net_settings", {})
    have = ns.setdefault("classes", [])
    default = next((c for c in have if c.get("name") == "Default"), {})
    patterns = ns.setdefault("netclass_patterns", []) or []
    for c in classes:
        if not any(x.get("name") == c["name"] for x in have):
            new = dict(default)
            new["name"] = c["name"]
            have.append(new)
        for net in c["nets"]:
            patterns.append({"netclass": c["name"], "pattern": names.get(net, net)})
    ns["netclass_patterns"] = patterns
    from ...atomicfile import replace_with_retry
    tmp = pro_path + ".eda-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(pro, fh, indent=2)
    replace_with_retry(tmp, pro_path)
    return len(classes)


def convert(prjpcb: str, out_dir: str, altium: Optional[dict[str, Any]] = None,
            kicad_cli: str = "", remove_title_block: bool = True) -> dict[str, Any]:
    """Altium project ``prjpcb`` to a KiCad project in the empty ``out_dir``.

    ``altium`` is what ``altium_side.read_board`` read (rules, stack-up,
    net classes, layer kinds, pins); without it the conversion still runs
    and those parts are reported as not done.
    """
    report: dict[str, Any] = {"ok": False, "out_dir": out_dir, "steps": {}}
    steps = report["steps"]
    cli = find_kicad_cli(kicad_cli)
    if not cli:
        report["reason"] = "kicad-cli was not found; pass kicad_cli"
        return report
    altium = altium or {}
    name = os.path.splitext(os.path.basename(prjpcb))[0]
    work = os.path.join(out_dir, ".eda_agent_convert")

    try:
        steps["import"] = importer.import_project(cli, prjpcb, out_dir)
    except importer.ImportFailed as e:
        report.update(stage="import", reason=str(e), dialogs=e.seen)
        return report
    os.makedirs(work, exist_ok=True)
    # KiCad was closed after the import; its lock file stays behind.
    for lock in glob.glob(os.path.join(out_dir, "~*.lck")):
        try:
            os.remove(lock)
        except OSError:
            pass
    root = os.path.join(out_dir, name + ".kicad_sch")
    board = os.path.join(out_dir, name + ".kicad_pcb")

    text = footprints.read(board)
    text, fixed = footprints.npth_sizes(text)
    footprints.write(board, text)
    steps["npth_pads_sized"] = fixed
    fp_ids = footprints.board_footprints(text)
    before_board = footprints.pad_nets(text)

    if hierarchy.is_flat(out_dir, name):
        steps["hierarchy"] = hierarchy.build_root(out_dir, name)
        steps["hierarchy"]["labels"] = hierarchy.globalize_labels(
            out_dir, hierarchy.child_files(out_dir, name))
    steps["schematic"] = schematic.normalize(out_dir, name, fp_ids)
    copies = [f for f in os.listdir(out_dir) if re.search(r"\.(SchLib|IntLib)$", f, re.I)]
    for f in copies:
        os.remove(os.path.join(out_dir, f))
    steps["schematic"]["altium_library_copies_removed"] = copies

    wiring = []
    for path in schematic.sheets_in(out_dir):
        tree = read(path)
        w = schematic.trim_stubs(tree)
        if remove_title_block:
            w["title_block_removed"] = schematic.remove_title_block(tree)
        write(path, tree)
        wiring.append({"sheet": os.path.basename(path), **w})
    steps["wiring"] = wiring
    steps["power_flags"] = power_flags(out_dir, name, cli, root, work)

    xml = netlist(cli, root, os.path.join(work, "schematic.xml"))
    sch_pins = checks.netlist_pins(xml)
    alt_pins = checks.altium_pins(altium.get("pins") or [])
    steps["check_schematic_vs_board"] = checks.compare(sch_pins, checks.from_pairs(before_board.items()))
    if alt_pins:
        steps["check_schematic_vs_altium"] = checks.compare(sch_pins, alt_pins)

    rules = altium_side.board_rules(altium.get("rules") or [])
    job = {"board": board, "netlist": xml, "rules": rules}
    job_path = os.path.join(work, "board_job.json")
    with open(job_path, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    script = os.path.join(os.path.dirname(__file__), "board_pass.py")
    from ...noconsole import no_window
    run = subprocess.run([_kicad_python(cli), script, job_path], capture_output=True,
                         text=True, timeout=900, **no_window())
    lines = [ln for ln in run.stdout.splitlines() if ln.startswith("{")]
    if run.returncode != 0 or not lines:
        report.update(stage="board", reason=(run.stderr or run.stdout)[-800:])
        return report
    steps["board"] = json.loads(lines[-1])
    if steps["board"].get("error"):
        report.update(stage="board", reason=steps["board"]["error"])
        return report

    steps["layers"] = footprints.map_layers(out_dir, board, altium.get("mech_layers") or [])
    steps["library_footprints_cleaned"] = footprints.library_fields(out_dir)

    text = footprints.read(board)
    if altium.get("stackup"):
        block, thickness = altium_side.stackup_block(altium["stackup"])
        text, done = _insert_stackup(text, block, thickness)
        footprints.write(board, text)
        steps["stackup"] = {"inserted": done, "thickness_mm": thickness,
                            "copper_layers": len(altium["stackup"])}
    names = altium_side.net_name_map(alt_pins, sch_pins)
    steps["net_classes"] = _project_classes(os.path.join(out_dir, name + ".kicad_pro"),
                                            altium_side.net_classes(altium.get("net_classes") or []),
                                            names)
    if altium.get("rules"):
        dru, skipped = altium_side.rules_file(altium["rules"], names)
        with open(os.path.join(out_dir, name + ".kicad_dru"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dru)
        defined = {c["name"] for c in altium_side.net_classes(altium.get("net_classes") or [])}
        steps["rules"] = {"board_settings": rules, "rules_file": name + ".kicad_dru",
                          "not_ported": skipped,
                          # A rule scoped to a class the board does not define
                          # matches nothing, in Altium as in KiCad.
                          "rules_naming_missing_classes": sorted(
                              altium_side.classes_named(altium["rules"]) - defined)}

    drc_json = os.path.join(work, "drc.json")
    _cli(cli, "pcb", "drc", "--refill-zones", "--save-board", "--format", "json",
         "--severity-all", "-o", drc_json, board, timeout=900)
    drc_counts: dict[str, int] = {}
    if os.path.isfile(drc_json):
        with open(drc_json, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in ("violations", "unconnected_items", "schematic_parity"):
            for v in data.get(key, []):
                t = v.get("type", key)
                drc_counts[t] = drc_counts.get(t, 0) + 1
    steps["drc"] = drc_counts
    steps["erc"] = erc(cli, root, os.path.join(work, "erc_final.json"))["by_type"]

    final = checks.compare(sch_pins, checks.from_pairs(footprints.pad_nets(footprints.read(board)).items()))
    steps["check_final_schematic_vs_board"] = final
    report["ok"] = (final["mismatches"] == 0
                    and steps["check_schematic_vs_board"]["mismatches"] == 0
                    and steps.get("check_schematic_vs_altium", {"mismatches": 0})["mismatches"] == 0)
    report["project"] = os.path.join(out_dir, name + ".kicad_pro")
    if not report["ok"]:
        report["reason"] = "the pin-to-net map changed: see the check_* steps"
    return report
