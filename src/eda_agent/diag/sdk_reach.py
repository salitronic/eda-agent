# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""How much of Altium's object model obj_query and obj_modify can reach.

Those tools read and write properties through hand-written chains of
name tests: GetSchProperty and SetSchProperty in Generic.pas, GetPCBProperty
and SetPCBProperty in PCBGeneric.pas, and the helpers they call. For every
object type the tools accept, this finds the type's interface in our index
of Altium's SDK (reference/altium_sdk, not part of the repository), takes
the properties it declares (``GetState_``/``SetState_`` members, inherited
ones too) and counts how many the chains name.

A dotted chain name counts for its first part (``Location.X`` reaches
``Location``), which can only overstate the reach slightly.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = _ROOT / "scripts" / "altium"
INDEX = _ROOT / "reference" / "altium_sdk" / "altium_sdk_index.json"

#: Domain: (unit, read chain, write chain, interface prefix, type-word routine, its unit)
DOMAINS = {
    "SCH": ("Generic.pas", "GetSchProperty", "SetSchProperty", "ISch_",
            "ObjectTypeFromString", "Generic.pas"),
    "PCB": ("PCBGeneric.pas", "GetPCBProperty", "SetPCBProperty", "IPCB_",
            "ObjectTypeFromStringPCB", "Utils.pas"),
}

_HEADER = re.compile(r"^(?:Function|Procedure) (\w+)\(", re.M)
_TESTED = re.compile(r"PropName\s*=\s*'([^']+)'")
_CALLED = re.compile(r"\b(\w+)\s*\(")
_TYPE_WORD = re.compile(r"(?:N|Raw) = '([a-z]+)'\s*Then Result := (e\w+)")


def routines_of(unit: str) -> dict[str, str]:
    """Each top-level routine of a Pascal unit, by name."""
    text = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    marks = list(_HEADER.finditer(text))
    return {m.group(1): text[m.start():(marks[n + 1].start() if n + 1 < len(marks)
                                        else len(text))]
            for n, m in enumerate(marks)}


def names_tested(unit: str, chain: str) -> set[str]:
    """Property names a chain tests, itself and one call level below."""
    code = routines_of(unit)
    found = set(_TESTED.findall(code[chain]))
    for callee in set(_CALLED.findall(code[chain])) - {chain}:
        found |= set(_TESTED.findall(code.get(callee, "")))
    return {name.split(".")[0] for name in found}


def type_words(domain: str) -> dict[str, str]:
    """The object-type words the tools take, each to its Altium constant."""
    *_, routine, unit = DOMAINS[domain]
    return dict(_TYPE_WORD.findall(routines_of(unit)[routine]))


class SdkIndex:
    """Interfaces of Altium's SDK, as our index records them."""

    def __init__(self, path: Path):
        self.path = path
        self.interfaces: dict = json.loads(path.read_text(encoding="utf-8-sig"))["interfaces"]

    def properties(self, interface: str) -> tuple[set[str], set[str]]:
        """(readable, writable) property names, inherited ones included."""
        reads: set[str] = set()
        writes: set[str] = set()
        pending, visited = [interface], set()
        while pending:
            name = pending.pop()
            if name in visited or name not in self.interfaces:
                continue
            visited.add(name)
            entry = self.interfaces[name]
            for method in entry.get("methods", []):
                head, _, rest = method[0].partition("_")
                if head == "GetState":
                    reads.add(rest)
                elif head == "SetState":
                    writes.add(rest)
            pending.extend(entry.get("bases", []))
        return reads, writes

    def interface_for(self, constant: str, prefix: str) -> Optional[str]:
        """The interface an object-type constant stands for, looked up in the
        index: the name after ``e``, also without a trailing ``Object`` or a
        leading ``Sch``, and ``Poly`` read as ``Polygon``."""
        stem = constant[1:]
        guesses = [stem, re.sub(r"Object$", "", stem), re.sub(r"^Sch", "", stem)]
        guesses += [g + "gon" for g in guesses if g.endswith("Poly")]
        return next((prefix + g for g in guesses if prefix + g in self.interfaces), None)


@dataclass
class Reach:
    word: str
    constant: str
    interface: Optional[str]
    readable: set[str]
    writable: set[str]
    reads_named: set[str]
    writes_named: set[str]

    @property
    def declared(self) -> set[str]:
        return self.readable | self.writable

    @property
    def reached(self) -> set[str]:
        return (self.readable & self.reads_named) | (self.writable & self.writes_named)


def index_path() -> Path:
    return Path(os.environ.get("EDA_AGENT_SDK_INDEX") or INDEX)


def measure(sdk: SdkIndex) -> list[Reach]:
    rows = []
    for domain, (unit, getter, setter, prefix, *_rest) in DOMAINS.items():
        reads, writes = names_tested(unit, getter), names_tested(unit, setter)
        seen: set[str] = set()
        for word, constant in sorted(type_words(domain).items()):
            if constant in seen:
                continue
            seen.add(constant)
            interface = sdk.interface_for(constant, prefix)
            readable, writable = sdk.properties(interface) if interface else (set(), set())
            rows.append(Reach(word, constant, interface, readable, writable, reads, writes))
    return rows


def _share(got: int, of: int) -> str:
    return f"{got} of {of} ({100 * got / max(of, 1):.1f}%)"


def report(rows: list[Reach], detail: int = 0) -> str:
    known = [r for r in rows if r.interface]
    out = [_share(sum(len(r.reached) for r in known), sum(len(r.declared) for r in known))
           + f" declared properties reached, over {len(known)} object types"]
    for prefix, title in (("ISch_", "Schematic"), ("IPCB_", "Board")):
        group = [r for r in known if r.interface.startswith(prefix)]
        if not group:
            continue
        group.sort(key=lambda r: len(r.reached) - len(r.declared))
        out.append(f"\n{title}: " + _share(sum(len(r.reached) for r in group),
                                           sum(len(r.declared) for r in group)))
        for r in group:
            out.append(f"  {r.word:<18} {r.interface:<26} "
                       f"{len(r.reached):>3}/{len(r.declared)}")
            unreached = sorted(r.declared - r.reached)
            if detail and unreached:
                more = " ..." if len(unreached) > detail else ""
                out.append("      not reached: " + ", ".join(unreached[:detail]) + more)
    unknown = [r for r in rows if not r.interface]
    if unknown:
        out.append("\nNo interface found in the index for: "
                   + ", ".join(f"{r.word} ({r.constant})" for r in unknown))
    return "\n".join(out)


def as_dict(rows: list[Reach]) -> dict:
    known = [r for r in rows if r.interface]
    return {"reached": sum(len(r.reached) for r in known),
            "declared": sum(len(r.declared) for r in known),
            "types": [{"type": r.word, "constant": r.constant, "interface": r.interface,
                       "declared": len(r.declared), "reached": len(r.reached),
                       "reads_not_reached": sorted(r.readable - r.reads_named),
                       "writes_not_reached": sorted(r.writable - r.writes_named)}
                      for r in rows]}
