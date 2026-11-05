# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Copper asked onto a net the board does not have is refused or skipped,
never placed without a net under a reply that says it was placed.

Every PCB handler that looks a net up by name and binds new copper to it
must act on the lookup finding nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: What binds a looked-up net to copper the handler creates.
BINDS = re.compile(r"BindPrimitiveToNet\(|\.Net\s*:=\s*FoundNet\b")
#: What acts on the lookup finding nothing.
REFUSES = re.compile(r"FindNetByName\(Board, NetStr\) = Nil|(FoundNet|RNet|Net)\s*=\s*Nil\s+Then"
                     r"|NET_NOT_FOUND|NetNotFoundError")
#: Handlers that bind copper they did not create (a polygon or pad
#: already there, a move) and answer the lookup themselves.
CREATES = re.compile(r"PCBObjectFactory\(")


def _handlers() -> dict[str, str]:
    text = PCB.read_text(encoding="utf-8", errors="replace")
    out = {}
    for m in re.finditer(r"^Function (PCB_\w+)\(.*?^End;", text, re.S | re.M):
        out[m.group(1)] = m.group(0)
    return out


def test_every_placement_on_a_named_net_acts_on_a_missing_one():
    missing = [name for name, body in _handlers().items()
               if "FindNetByName(" in body and BINDS.search(body)
               and CREATES.search(body) and not REFUSES.search(body)]
    assert not missing, missing


def test_the_guard_sees_the_placements():
    names = {n for n, b in _handlers().items()
             if "FindNetByName(" in b and BINDS.search(b) and CREATES.search(b)}
    assert {"PCB_PlaceTracks", "PCB_PlaceVias", "PCB_PlacePad", "PCB_PlaceRegion",
            "PCB_PlaceFill", "PCB_PlaceVia", "PCB_PlaceTrack"} <= names
