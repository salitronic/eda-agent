# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The pin-to-net map, compared across the conversion.

Net names change on the way (the board's flat names become the schematic's
hierarchical ones), so what is compared is which pins share a net: the
partition of pins into nets. A pin in a different group on one side is a
connection the conversion made or broke. Pins on no net, or alone on one,
are left out: the sides name unconnected pins differently.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any, Iterable

Pin = tuple[str, str]


def from_pairs(pairs: Iterable[tuple[Pin, str]]) -> dict[Pin, str]:
    out = {}
    for pin, net in pairs:
        if net and not net.startswith(("unconnected-", "?")):
            out[pin] = net
    return out


def netlist_pins(xml_path: str) -> dict[Pin, str]:
    """``(designator, pin) -> net`` from a KiCad XML netlist."""
    pairs = []
    for net in ET.parse(xml_path).getroot().iter("net"):
        for node in net.iter("node"):
            pairs.append(((node.get("ref"), node.get("pin")), net.get("name") or ""))
    return from_pairs(pairs)


def altium_pins(rows: list[dict[str, Any]]) -> dict[Pin, str]:
    """From ``proj_get_nets`` rows (component, pin, net)."""
    return from_pairs(((str(r.get("component")), str(r.get("pin"))), str(r.get("net") or ""))
                      for r in rows or [])


def _groups(pins: dict[Pin, str]) -> dict[Pin, frozenset]:
    by_net = defaultdict(set)
    for pin, net in pins.items():
        by_net[net].add(pin)
    out = {}
    for members in by_net.values():
        if len(members) < 2:
            continue
        group = frozenset(members)
        for pin in members:
            out[pin] = group
    return out


def compare(a: dict[Pin, str], b: dict[Pin, str], limit: int = 25) -> dict[str, Any]:
    """Pins whose net-mates differ between ``a`` and ``b``."""
    ga, gb = _groups(a), _groups(b)
    differ = []
    for pin in sorted(set(ga) | set(gb)):
        if ga.get(pin, frozenset({pin})) != gb.get(pin, frozenset({pin})):
            differ.append(pin)
    sample = []
    for pin in differ[:limit]:
        sample.append({"pin": f"{pin[0]}.{pin[1]}", "net_a": a.get(pin), "net_b": b.get(pin),
                       "only_with_a": sorted(f"{p[0]}.{p[1]}" for p in ga.get(pin, set()) - gb.get(pin, set())),
                       "only_with_b": sorted(f"{p[0]}.{p[1]}" for p in gb.get(pin, set()) - ga.get(pin, set()))})
    return {"pins_compared": len(set(ga) | set(gb)), "mismatches": len(differ),
            "examples": sample}
