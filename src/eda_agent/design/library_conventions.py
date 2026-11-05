# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Conventions a company keeps in its libraries.

IPC-7351 footprint names encode the package: CAPC1005X55N is a chip
1.0 x 0.5 mm and 0.55 mm high; SOT95P280X130-5N has five pins at 0.95 mm
pitch across 2.80 mm, 1.30 mm high; QFN50P400X400X100-21N has 21 pads at
0.50 mm on a 4 x 4 mm body, 1.00 mm high. Decoded, the name can be held
against the footprint's pads.

A naming policy is the user's own ``naming.json`` in EDA_AGENT_POLICY_DIR:
for each of footprint, symbol and component an optional ``template`` of
``{tokens}`` and a ``pattern`` every name has to match. Without that file
no naming is enforced.

A pin table is a CSV, ``number,name,type`` plus optional ``side`` and
``description`` columns, copied from a datasheet. It is checked and split
into the left and right pin lists lib_create_ic_symbol takes. It speaks
for the datasheet only once someone has compared it with the cited page.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# --- IPC-7351 names -----------------------------------------------------------

#: Most specific first; every number is in hundredths of a mm unless noted.
_IPC_FORMS = (
    ("four-sided", re.compile(
        r"(?P<prefix>[A-Z]+?)(?P<pitch>\d{2,3})P(?P<span>\d{3,4})X(?P<span2>\d{3,4})"
        r"X(?P<height>\d{2,3})-(?P<pins>\d{1,3})(?P<density>[LNM])")),
    ("two-sided", re.compile(
        r"(?P<prefix>[A-Z]+?)(?P<pitch>\d{2,3})P(?P<span>\d{3,4})X(?P<height>\d{2,3})"
        r"-(?P<pins>\d{1,3})(?P<density>[LNM])")),
    # Chip length and width are in tenths of a mm.
    ("chip", re.compile(
        r"(?P<prefix>[A-Z]+C[A-Z]*?)(?P<length>\d{2})(?P<width>\d{2})X(?P<height>\d{2,3})"
        r"(?P<density>[LNM])")),
)
_TENTHS = {"length", "width"}


def read_ipc_name(name: str) -> Optional[dict[str, Any]]:
    """The package an IPC-7351 footprint name describes, in mm, or None."""
    text = (name or "").strip().upper()
    for family, form in _IPC_FORMS:
        hit = form.fullmatch(text)
        if not hit:
            continue
        info: dict[str, Any] = {"family": family}
        for key, raw in hit.groupdict().items():
            if key in ("prefix", "density"):
                info[key] = raw
            elif key == "pins":
                info[key] = int(raw)
            else:
                info[key] = int(raw) / (10 if key in _TENTHS else 100)
        info.setdefault("pins", 2)
        return info
    return None


def smallest_pitch(pads: list[dict], snap_mm: float = 0.01) -> Optional[float]:
    """The closest spacing between neighbouring pads in any row or column (mm)."""
    gaps: list[float] = []
    for across, along in (("x_mm", "y_mm"), ("y_mm", "x_mm")):
        lines: dict[int, list[float]] = {}
        for pad in pads:
            try:
                lines.setdefault(round(float(pad[across]) / snap_mm), []).append(
                    float(pad[along]))
            except (KeyError, TypeError, ValueError):
                pass
        for spots in lines.values():
            spots.sort()
            gaps += [b - a for a, b in zip(spots, spots[1:]) if b - a > snap_mm]
    return min(gaps) if gaps else None


def compare_ipc_name(name: str, pads: list[dict]) -> dict[str, Any]:
    """Whether the pads of a footprint match what its IPC name claims."""
    claim = read_ipc_name(name)
    if claim is None:
        return {"footprint": name, "ipc_name": False}
    issues = []
    # A through-hole mounting pad is not a pin; count without them too.
    pins_only = [p for p in pads if not (str(p.get("layer", "")).lower() == "multilayer"
                                         and float(p.get("hole_mm") or 0) > 0)] or pads
    if claim["pins"] not in (len(pads), len(pins_only)):
        issues.append(f"{claim['pins']} pins by name, {len(pads)} pads drawn")
    if claim.get("pitch") and len(pads) > 2:
        measured = smallest_pitch(pads)
        if measured is not None and abs(measured - claim["pitch"]) > 0.011:
            issues.append(f"{claim['pitch']:.2f} mm pitch by name, pads "
                          f"{measured:.3f} mm apart")
    return {"footprint": name, "ipc_name": True, "decoded": claim, "pads": len(pads),
            "matches": not issues, "issues": issues}


# --- naming policy --------------------------------------------------------------

NAME_KINDS = ("footprint", "symbol", "component")


def policy_folder() -> Optional[Path]:
    where = os.environ.get("EDA_AGENT_POLICY_DIR", "").strip()
    return Path(where) if where else None


@dataclass
class NamingPolicy:
    rules: dict[str, dict]
    abbreviations: dict[str, str]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["NamingPolicy"]:
        """The user's policy, or None when there is no naming.json."""
        if path is None:
            folder = policy_folder()
            path = folder / "naming.json" if folder else None
            if path is None or not path.is_file():
                return None
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = {}
        for kind in NAME_KINDS:
            rule = raw.get(kind)
            if rule is None:
                continue
            if not isinstance(rule, dict) or not rule.get("pattern"):
                raise ValueError(f"naming.json: {kind} has no pattern")
            re.compile(rule["pattern"])
            rules[kind] = rule
        return cls(rules, dict(raw.get("manufacturer_abbreviations") or {}))

    def pattern(self, kind: str) -> Optional[str]:
        return (self.rules.get(kind) or {}).get("pattern")

    def breach(self, kind: str, name: str) -> Optional[str]:
        """Why a name does not follow the policy, or None when it does."""
        wanted = self.pattern(kind)
        if wanted and not re.fullmatch(wanted, name or ""):
            return f"{name!r} does not fit the {kind} pattern {wanted}"
        return None

    def make(self, kind: str, values: dict[str, str]) -> str:
        """A name from the kind's template; every token it names is needed."""
        template = (self.rules.get(kind) or {}).get("template")
        if not template:
            raise ValueError(f"the policy has no {kind} template")
        given = {k: str(v) for k, v in values.items() if v not in (None, "")}
        maker = given.get("manufacturer")
        if maker and "mfr_short" not in given:
            given["mfr_short"] = self.abbreviations.get(maker) or \
                re.sub(r"[^A-Za-z0-9]", "", maker).upper()[:8]
        wanted = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        lacking = sorted(wanted - set(given))
        if lacking:
            raise ValueError(f"the {kind} template {template} needs {', '.join(lacking)}")
        name = template.format(**{k: re.sub(r"[^A-Za-z0-9_.+\-]", "_", v).upper()
                                  for k, v in given.items()})
        problem = self.breach(kind, name)
        if problem:
            raise ValueError(problem)
        return name


# --- datasheet pin tables -----------------------------------------------------

#: Each electrical type lib_create_ic_symbol takes, with the other ways
#: datasheets write it.
_TYPE_SPELLINGS = {
    "input": ("input", "in", "i"),
    "output": ("output", "out", "o"),
    "io": ("io", "i/o"),
    "bidirectional": ("bidirectional", "bidir"),
    "passive": ("passive",),
    "power": ("power", "pwr", "ground", "gnd"),
    "open_collector": ("open_collector", "open drain", "od"),
    "open_emitter": ("open_emitter",),
    "hiz": ("hiz", "tristate"),
}
PIN_TYPE_OF = {spelling: kind for kind, spellings in _TYPE_SPELLINGS.items()
               for spelling in spellings}
_RIGHT_SIDE_TYPES = {"output", "io", "bidirectional", "open_collector", "open_emitter", "hiz"}
_PIN_NUMBER = re.compile(r"(?:[A-Z]{0,2}\d{1,4}|EP|TAB|PAD)")
_SUPPLY_NAME = re.compile(
    r"(?:V(?:CC|DD|IN|OUT|BAT|REF|S|IO)\w*|GND\w*|VSS\w*|[ADP]GND|AVDD|DVDD)", re.I)


@dataclass
class PinRow:
    number: str
    name: str
    type: str
    side: str = "auto"
    description: str = ""

    @property
    def kind(self) -> Optional[str]:
        return PIN_TYPE_OF.get(self.type)


def read_pin_table(text: str) -> list[PinRow]:
    """The rows of a pin-table CSV."""
    table = csv.DictReader(io.StringIO((text or "").strip()))
    columns = {c.strip().lower() for c in table.fieldnames or []}
    if not {"number", "name", "type"} <= columns:
        raise ValueError("a pin table has the columns number, name and type (side and "
                         "description are optional)")
    rows = []
    for raw in table:
        cell = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        rows.append(PinRow(cell["number"].upper(), cell["name"], cell["type"].lower(),
                           (cell.get("side") or "auto").lower(), cell.get("description", "")))
    return rows


def review_pin_table(rows: list[PinRow], package_pins: Optional[int] = None
                     ) -> tuple[list[str], list[str]]:
    """(errors that make the table unusable, warnings someone should check)."""
    errors: list[str] = [] if rows else ["the table has no rows"]
    warnings: list[str] = []
    numbers: set[str] = set()
    for row in rows:
        label = f"pin {row.number}"
        if not _PIN_NUMBER.fullmatch(row.number):
            errors.append(f"{row.number!r} is not a pin number")
        if row.number in numbers:
            errors.append(f"{label} appears twice")
        numbers.add(row.number)
        if not row.name:
            errors.append(f"{label} has no name")
        if row.kind is None:
            errors.append(f"{label}: {row.type!r} is not a pin type ("
                          + ", ".join(_TYPE_SPELLINGS) + ")")
        elif _SUPPLY_NAME.fullmatch(row.name) and row.kind not in ("power", "passive",
                                                                     "output"):
            warnings.append(f"{label} {row.name} reads as a supply but is typed {row.type}")
        if row.side not in ("left", "right", "auto"):
            errors.append(f"{label}: side {row.side!r} is not left, right or auto")
    if package_pins is not None and len(rows) != package_pins:
        errors.append(f"the table has {len(rows)} pins, the package {package_pins}")
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.name.upper()] = tally.get(row.name.upper(), 0) + 1
    for name, count in sorted(tally.items()):
        if count > 1 and name not in ("NC", "DNC") and not _SUPPLY_NAME.fullmatch(name):
            warnings.append(f"signal {name} is on {count} pins")
    return errors, warnings


def split_sides(rows: list[PinRow]) -> dict[str, list[dict]]:
    """Left and right pin lists for lib_create_ic_symbol. A side the table
    names is kept; otherwise inputs and supplies go left, outputs and
    bidirectional pins right, and anything else to the shorter side."""
    sides: dict[str, list[dict]] = {"left": [], "right": []}
    for row in rows:
        kind = row.kind or "passive"
        side = row.side
        if side == "auto":
            if kind in ("input", "power"):
                side = "left"
            elif kind in _RIGHT_SIDE_TYPES:
                side = "right"
            else:
                side = "left" if len(sides["left"]) <= len(sides["right"]) else "right"
        sides[side].append({"designator": row.number, "name": row.name,
                            "electrical_type": kind})
    return {"left_pins": sides["left"], "right_pins": sides["right"]}
