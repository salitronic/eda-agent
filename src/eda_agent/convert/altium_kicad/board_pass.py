# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The board pass, run by KiCad's own Python (it needs ``pcbnew``).

Run as ``python.exe board_pass.py job.json``; the job is written by
``pipeline`` and the result printed as one JSON line. It:

- names each board net as the schematic names it (the importer leaves them
  flat while the schematic's are hierarchical, which KiCad reports as a
  conflict on every net),
- sets the zones' clearance to the board clearance rule (the importer put
  0.5 mm on every zone), and fills each as its Altium pour (narrowest
  neck, island removal),
- gives unnamed mounting holes references and keeps them off the BOM and
  position files,
- copies the schematic's fields onto the footprints,
- sets the design settings from the Altium rules,
- saves every footprint into project ``.pretty`` libraries and writes the
  fp-lib-table.

Nothing here imports eda_agent: KiCad's interpreter does not have it.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET


def _schematic(netlist_xml):
    pin_net, fields = {}, {}
    root = ET.parse(netlist_xml).getroot()
    for net in root.iter("net"):
        for node in net.iter("node"):
            pin_net[(node.get("ref"), node.get("pin"))] = net.get("name")
    for comp in root.iter("comp"):
        f = {fl.get("name"): (fl.text or "") for fl in comp.iter("field")}
        f["Value"] = comp.findtext("value") or ""
        f["Datasheet"] = comp.findtext("datasheet") or ""
        if comp.findtext("description"):
            f["Description"] = comp.findtext("description")
        fields[comp.get("ref")] = f
    return pin_net, fields


def _squash(name):
    return "".join(name.split()).lower()


def _pour_settings(board, pours, pcbnew):
    """Each copper zone filled as its Altium pour is: the same narrowest
    neck and the same island removal. A zone meets its pour by net and
    layer (Altium's TopLayer is KiCad's "Top Layer" without the space),
    else by net when the net has one pour."""
    modes = {"always": pcbnew.ISLAND_REMOVAL_MODE_ALWAYS,
             "never": pcbnew.ISLAND_REMOVAL_MODE_NEVER,
             "area": pcbnew.ISLAND_REMOVAL_MODE_AREA}
    by_net = {}
    for p in pours:
        by_net.setdefault(p["net"], []).append(p)
    done = 0
    for zone in board.Zones():
        if zone.GetIsRuleArea():
            continue
        mine = by_net.get(zone.GetNetname()) or []
        names = {_squash(board.GetLayerName(l)) for l in zone.GetLayerSet().Seq()}
        names |= {_squash(pcbnew.LSET.Name(l)) for l in zone.GetLayerSet().Seq()}
        match = [p for p in mine if _squash(p["layer"]) in names]
        if not match and len(mine) == 1:
            match = mine
        if len(match) != 1:
            continue
        p = match[0]
        zone.SetMinThickness(pcbnew.FromMM(p["min_width_mm"]))
        zone.SetIslandRemovalMode(modes[p["islands"]])
        if p["islands"] == "area":
            zone.SetMinIslandArea(int(p["min_island_mm2"] * 1e12))
        done += 1
    return done


def run(job):
    import pcbnew

    mm = pcbnew.FromMM
    board = pcbnew.LoadBoard(job["board"])
    pin_net, fields = _schematic(job["netlist"])
    out = {"nets_renamed": 0, "net_conflicts": [], "zones": 0, "holes": [],
           "fields_set": 0, "library_footprints": 0, "libraries": []}

    # Board nets named as the schematic names them.
    renames = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            key = (fp.GetReference(), pad.GetNumber())
            old = pad.GetNetname()
            if key in pin_net and old:
                new = pin_net[key]
                if renames.setdefault(old, new) != new:
                    out["net_conflicts"].append([old, renames[old], new])
    if out["net_conflicts"]:
        out["error"] = ("a board net reaches pins the schematic puts on two "
                        "nets; nothing was renamed")
        return out
    by_name = board.GetNetsByName()
    for old, new in renames.items():
        if old != new and old in by_name:
            by_name[old].SetNetname(new)
            out["nets_renamed"] += 1

    clearance = job.get("rules", {}).get("clearance_mm")
    if clearance:
        for zone in board.Zones():
            if not zone.GetIsRuleArea():
                zone.SetLocalClearance(mm(clearance))
                out["zones"] += 1
    out["zones_filled_as_altium"] = _pour_settings(board, job.get("pours") or [], pcbnew)

    holes = [fp for fp in board.GetFootprints() if not fp.GetReference()]
    holes.sort(key=lambda f: (f.GetPosition().y, f.GetPosition().x))
    mechanical = job.get("mechanical_library") or "Mechanical"
    for i, fp in enumerate(holes, 1):
        fp.SetReference(f"H{i}")
        # A footprint of its own in the project's mechanical library, named
        # by its hole, rather than the importer's anonymous one.
        drills = [p.GetDrillSizeX() for p in fp.Pads() if p.GetDrillSizeX() > 0]
        if drills:
            size = f"{round(pcbnew.ToMM(max(drills)), 2):g}"
            plated = any(p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH for p in fp.Pads())
            name = f"MountingHole_{size}mm" + ("" if plated else "_NPTH")
            fp.SetFPID(pcbnew.LIB_ID(mechanical, name))
            fp.SetValue(name)
        fp.SetBoardOnly(True)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        out["holes"].append(f"H{i}")

    skip = {"Reference", "Footprint"}
    for fp in board.GetFootprints():
        values = fields.get(fp.GetReference())
        if not values:
            continue
        for name, value in values.items():
            if name in skip:
                continue
            if name == "Value":
                fp.SetValue(value)
            else:
                fp.SetField(name, value)
                field = fp.GetField(name)
                if field is not None:
                    field.SetVisible(False)
            out["fields_set"] += 1

    rules = job.get("rules", {})
    ds = board.GetDesignSettings()
    if rules.get("clearance_mm"):
        ds.m_MinClearance = mm(rules["clearance_mm"])
        # Copper to a hole: Altium's clearance rule covers it; its hole to
        # hole rule is a different thing.
        ds.m_HoleClearance = mm(rules["clearance_mm"])
    if rules.get("via_min_drill_mm"):
        ds.m_MinThroughDrill = mm(rules["via_min_drill_mm"])
    if rules.get("via_min_diameter_mm"):
        ds.m_ViasMinSize = mm(rules["via_min_diameter_mm"])
    if rules.get("track_min_mm"):
        ds.m_TrackMinWidth = mm(rules["track_min_mm"])
    if rules.get("hole_to_hole_mm"):
        ds.m_HoleToHoleMin = mm(rules["hole_to_hole_mm"])
    if rules.get("edge_clearance_mm"):
        ds.m_CopperEdgeClearance = mm(rules["edge_clearance_mm"])
    if rules.get("mask_expansion_mm") is not None:
        ds.m_SolderMaskExpansion = mm(rules["mask_expansion_mm"])
    default = ds.m_NetSettings.GetDefaultNetclass()
    if rules.get("clearance_mm"):
        default.SetClearance(mm(rules["clearance_mm"]))
    if rules.get("track_mm"):
        default.SetTrackWidth(mm(rules["track_mm"]))
    if rules.get("via_diameter_mm"):
        default.SetViaDiameter(mm(rules["via_diameter_mm"]))
    if rules.get("via_drill_mm"):
        default.SetViaDrill(mm(rules["via_drill_mm"]))

    pcbnew.SaveBoard(job["board"], board)

    # Project footprint libraries, one per library the board names.
    io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
    done = set()
    project_dir = os.path.dirname(job["board"])
    for fp in board.GetFootprints():
        lib = str(fp.GetFPID().GetLibNickname()) or job.get("default_library", "Project")
        name = str(fp.GetFPID().GetLibItemName())
        if not name or (lib, name) in done:
            continue
        folder = os.path.join(project_dir, lib + ".pretty")
        os.makedirs(folder, exist_ok=True)
        copy = pcbnew.FOOTPRINT(fp)
        if copy.IsFlipped():
            side = getattr(pcbnew, "FLIP_DIRECTION_TOP_BOTTOM", False)
            copy.Flip(copy.GetPosition(), side)
        copy.SetOrientationDegrees(0)
        copy.SetPosition(pcbnew.VECTOR2I(0, 0))
        copy.SetReference("REF**")
        copy.SetValue(name)
        for pad in copy.Pads():
            pad.SetNetCode(0)
        copy.SetPath(pcbnew.KIID_PATH())
        io.FootprintSave(folder, copy)
        done.add((lib, name))
    libs = sorted({lib for lib, _ in done})
    rows = "".join(f'  (lib (name "{lib}")(type "KiCad")(uri "${{KIPRJMOD}}/{lib}.pretty")(options "")(descr ""))\n'
                   for lib in libs)
    with open(os.path.join(project_dir, "fp-lib-table"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"(fp_lib_table\n  (version 7)\n{rows})\n")
    out["library_footprints"] = len(done)
    out["libraries"] = libs
    return out


def _copy_text(src, dst):
    dst.SetText(src.GetText())
    dst.SetAttributes(src)
    dst.SetPosition(src.GetPosition())
    dst.SetLayer(src.GetLayer())
    dst.SetVisible(src.IsVisible())


def _swap(board, old, new):
    """``new`` (a library copy) in ``old``'s place: position, side,
    orientation, fields, pad nets, attributes and its schematic link."""
    import pcbnew
    new.SetParent(board)
    new.SetPosition(old.GetPosition())
    if new.GetLayer() != old.GetLayer():
        side = getattr(pcbnew, "FLIP_DIRECTION_TOP_BOTTOM", False)
        new.Flip(new.GetPosition(), side)
    new.SetOrientation(old.GetOrientation())
    new.SetFPID(old.GetFPID())
    for field in old.GetFields():
        name = field.GetName()
        if not new.HasField(name):
            new.SetField(name, field.GetText())
        target = new.GetField(name)
        if target is not None:
            _copy_text(field, target)
    by_number = {}
    for pad in old.Pads():
        by_number.setdefault(pad.GetNumber(), []).append(pad)
    for pad in new.Pads():
        same = by_number.get(pad.GetNumber())
        if same:
            pad.SetNet(same.pop(0).GetNet())
    new.SetAttributes(old.GetAttributes())
    new.SetPath(old.GetPath())
    new.SetSheetname(old.GetSheetname())
    new.SetSheetfile(old.GetSheetfile())
    new.SetLocked(old.IsLocked())
    board.Remove(old)
    board.Add(new)


def sync(job):
    """Every board footprint that differs from its project library copy is
    replaced by that copy. The copy was saved from one instance; another
    placed at another angle came in from Altium with its shapes rounded
    differently (a pin 1 mark off by 0.00001 mm at 180 degrees), and KiCad
    reports each such part as not matching its library."""
    import pcbnew
    board = pcbnew.LoadBoard(job["board"])
    project_dir = os.path.dirname(job["board"])
    swapped, missing = [], []
    for fp in list(board.GetFootprints()):
        lib = str(fp.GetFPID().GetLibNickname())
        name = str(fp.GetFPID().GetLibItemName())
        folder = os.path.join(project_dir, lib + ".pretty")
        if not lib or not os.path.isfile(os.path.join(folder, name + ".kicad_mod")):
            missing.append(fp.GetReference())
            continue
        copy = pcbnew.FootprintLoad(folder, name)
        if copy is None or not fp.FootprintNeedsUpdate(copy):
            continue
        _swap(board, fp, copy)
        swapped.append(fp.GetReference())
    if swapped:
        pcbnew.SaveBoard(job["board"], board)
    return {"replaced_from_library": swapped, "not_in_a_project_library": missing}


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        job = json.load(fh)
    result = sync(job) if job.get("mode") == "sync" else run(job)
    print(json.dumps(result))
