# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Text passes over the imported board and its library footprints.

Done on the text, block by block, so everything else keeps its bytes:

- ``npth_sizes``: a non-plated hole the importer gave a 0 x 0 pad makes
  KiCad's board loader raise a message box, which hangs a headless load. The
  pad takes its drill size, before anything loads the board.
- ``map_layers``: the importer puts Altium's Mechanical N on User.N. The
  layers whose Altium kind is a courtyard or assembly layer go to KiCad's
  F/B.CrtYd and F/B.Fab. What the importer had already put on F.CrtYd is
  the component-centre cross, which is drawing, not courtyard: it goes to
  F.Fab when a real courtyard arrives.
- ``fields_off_silk``: hidden footprint fields other than Reference and
  Value go from the silkscreen to F.Fab.
- ``library_fields``: library footprints keep only Reference, Value,
  Datasheet and Description, and no sheet path.
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any

from .tree import blocks, replace_in_blocks, strip_blocks

#: Altium mechanical layer kinds to KiCad layers.
KIND_TO_LAYER = {
    "courtyard top": "F.CrtYd", "courtyard bottom": "B.CrtYd",
    "assembly top": "F.Fab", "assembly bottom": "B.Fab",
    "designator top": "F.Fab", "designator bottom": "B.Fab",
    "component center top": "F.Fab", "component center bottom": "B.Fab",
    "component outline top": "F.Fab", "component outline bottom": "B.Fab",
}

_KEEP_LIBRARY_FIELDS = {"Reference", "Value", "Datasheet", "Description"}


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    from ...atomicfile import replace_with_retry
    tmp = path + ".eda-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    replace_with_retry(tmp, path)


def npth_sizes(text: str) -> tuple[str, int]:
    """Zero-size non-plated pads take their drill size."""
    def fix(pad: str) -> str:
        if "np_thru_hole" not in pad:
            return pad
        size = re.search(r"\(size\s+([-\d.]+)\s+([-\d.]+)\)", pad)
        drill = re.search(r"\(drill\s+(?:oval\s+)?([-\d.]+)(?:\s+([-\d.]+))?", pad)
        if not size or not drill or float(size.group(1)) > 0 or float(size.group(2)) > 0:
            return pad
        d1 = drill.group(1)
        d2 = drill.group(2) or d1
        return pad[:size.start()] + f"(size {d1} {d2})" + pad[size.end():]
    return replace_in_blocks(text, "pad", fix)


def layer_map(mech_layers: list[dict[str, Any]]) -> dict[str, str]:
    """``User.N`` to a KiCad layer, from the Altium layers' kinds."""
    out = {}
    for m in mech_layers or []:
        kind = str(m.get("kind") or "").strip().lower()
        target = KIND_TO_LAYER.get(kind)
        number = re.search(r"(\d+)$", str(m.get("layer") or ""))
        if target and number:
            out[f"User.{number.group(1)}"] = target
    return out


def _is_centre_cross(line: str) -> bool:
    m = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)", line)
    if not m:
        return False
    x1, y1, x2, y2 = map(float, m.groups())
    flat = abs(y1) < 1e-6 and abs(y2) < 1e-6 and abs(x1 + x2) < 1e-6 and abs(x1 - x2) <= 1.0 + 1e-6
    upright = abs(x1) < 1e-6 and abs(x2) < 1e-6 and abs(y1 + y2) < 1e-6 and abs(y1 - y2) <= 1.0 + 1e-6
    return flat or upright


def _map_footprint(fp: str, mapping: dict[str, str], counts: dict[str, int]) -> str:
    gets_courtyard = any(v in ("F.CrtYd", "B.CrtYd") for v in mapping.values())
    if gets_courtyard:
        def cross(line: str) -> str:
            for side in ("F", "B"):
                tag = f'(layer "{side}.CrtYd")'
                if tag in line and _is_centre_cross(line):
                    counts["crosses_to_fab"] += 1
                    return line.replace(tag, f'(layer "{side}.Fab")')
            return line
        fp, _ = replace_in_blocks(fp, "fp_line", cross)
    for src, dst in mapping.items():
        n = fp.count(f'(layer "{src}")')
        if n:
            fp = fp.replace(f'(layer "{src}")', f'(layer "{dst}")')
            counts["remapped"] += n
    return fp


def fields_off_silk(fp: str, counts: dict[str, int]) -> str:
    def move(block: str) -> str:
        name = re.match(r'\(property\s+"([^"]*)"', block)
        if not name or name.group(1) in ("Reference", "Value"):
            return block
        if "(hide yes)" not in block:
            return block
        for side in ("F", "B"):
            tag = f'(layer "{side}.SilkS")'
            if tag in block:
                counts["fields_to_fab"] += 1
                return block.replace(tag, f'(layer "{side}.Fab")')
        return block
    out, _ = replace_in_blocks(fp, "property", move)
    return out


def map_layers(project_dir: str, board_path: str,
               mech_layers: list[dict[str, Any]]) -> dict[str, Any]:
    """The board and every project library footprint, layers by kind and
    hidden fields off the silkscreen."""
    mapping = layer_map(mech_layers)
    counts = {"remapped": 0, "crosses_to_fab": 0, "fields_to_fab": 0}
    text = read(board_path)
    text, _ = replace_in_blocks(
        text, "footprint", lambda fp: fields_off_silk(_map_footprint(fp, mapping, counts), counts))
    write(board_path, text)
    for path in glob.glob(os.path.join(project_dir, "*.pretty", "*.kicad_mod")):
        fp = read(path)
        new = fields_off_silk(_map_footprint(fp, mapping, counts), counts)
        if new != fp:
            write(path, new)
    return {"layer_map": mapping, **counts}


def library_fields(project_dir: str) -> int:
    """Library footprints without the per-part fields and sheet paths the
    board copies carried."""
    cleaned = 0
    for path in glob.glob(os.path.join(project_dir, "*.pretty", "*.kicad_mod")):
        text = read(path)
        new, _ = strip_blocks(
            text, "property",
            keep=lambda b: (re.match(r'\(property\s+"([^"]*)"', b) or [None, ""])[1] in _KEEP_LIBRARY_FIELDS)
        for head in ("path", "sheetname", "sheetfile"):
            new, _ = strip_blocks(new, head)
        if new != text:
            write(path, new)
            cleaned += 1
    return cleaned


def board_footprints(board_text: str) -> dict[str, str]:
    """Designator to footprint library id, from the board's text."""
    out = {}
    for a, b in blocks(board_text, "footprint"):
        fp = board_text[a:b]
        lib_id = re.match(r'\(footprint\s+"([^"]+)"', fp)
        ref = re.search(r'\(property\s+"Reference"\s+"([^"]*)"', fp)
        if lib_id and ref and ref.group(1):
            out[ref.group(1)] = lib_id.group(1)
    return out


def pad_nets(board_text: str) -> dict[tuple[str, str], str]:
    """``(designator, pad) -> net`` from the board's text."""
    out = {}
    for a, b in blocks(board_text, "footprint"):
        fp = board_text[a:b]
        ref = re.search(r'\(property\s+"Reference"\s+"([^"]*)"', fp)
        if not ref or not ref.group(1):
            continue
        for pa, pb in blocks(fp, "pad"):
            pad = fp[pa:pb]
            number = re.match(r'\(pad\s+"([^"]*)"', pad)
            net = re.search(r'\(net\s+(?:\d+\s+)?"([^"]*)"\)', pad)
            if number and net and number.group(1):
                out[(ref.group(1), number.group(1))] = net.group(1)
    return out
