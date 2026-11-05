# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every net label a tool places is Altium's native maroon.

The colour used to follow the majority of the sheet's labels, and fell
back to 0 (black) on a sheet with none. The font still follows the sheet.
"""
from __future__ import annotations

import re
from pathlib import Path

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
GENERIC = (ALTIUM / "Generic.pas").read_text(encoding="utf-8")


def _routines_creating_net_labels() -> dict[str, str]:
    """Name -> body of every Generic.pas routine that creates a net label."""
    found = {}
    for m in re.finditer(r"^Function (\w+)\(.*?^End;", GENERIC, re.M | re.S):
        if "SchObjectFactory(eNetLabel" in m.group(0):
            found[m.group(1)] = m.group(0)
    return found


def test_the_colour_is_the_native_maroon():
    main = (ALTIUM / "Main.pas").read_text(encoding="utf-8")
    assert re.search(r"^\s*NET_LABEL_COLOR = 128;", main, re.M)
    # BGR packing: 128 is red 128, green 0, blue 0.
    assert (128 & 0xFF, (128 >> 8) & 0xFF, (128 >> 16) & 0xFF) == (128, 0, 0)


def test_every_net_label_writer_sets_it_unconditionally():
    routines = _routines_creating_net_labels()
    assert set(routines) >= {"Gen_PlaceNetLabel", "Gen_PlaceNetLabels"}
    assert len(routines) == 3, sorted(routines)
    for name, body in routines.items():
        writes = re.findall(r"NetLabel\.Color := (\w+);", body)
        assert writes == ["NET_LABEL_COLOR"], (name, writes)
        # Not inside the font branch: a sheet with no labels still gets it.
        lines = body.splitlines()
        indent = {key: next(len(ln) - len(ln.lstrip()) for ln in lines if key in ln)
                  for key in ("If StyleFound Then", "NetLabel.Color := NET_LABEL_COLOR;")}
        assert len(set(indent.values())) == 1, (name, indent)


def test_the_sheet_majority_picks_only_the_font():
    m = re.search(r"^Function InferNetLabelStyle\(.*?^End;", GENERIC, re.M | re.S)
    assert m and "Color" not in m.group(0).split("Begin", 1)[1]
