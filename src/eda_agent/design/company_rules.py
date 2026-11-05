# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A company's own design rules, kept as JSON and checked against a design.

A rules file is ``{"name": ..., "checks": [...]}``. Every check has an
``id``, a ``check`` (one of KINDS below), a ``level`` (error, warning or
note) and the fields its kind asks for; ``"off": true`` leaves it out.
Files live in ``rules/`` under EDA_AGENT_POLICY_DIR, one per rule set, and
belong to the user: the tools read and run them and never write them. A
``note`` check carries guidance for a reviewer and neither passes nor fails.

What the checks read from a design, collected by the tool from the open
project and board:

    symbols        [{designator, lib_ref}]
    symbol_params  {designator: {parameter: value}}
    nets           [{name, pins: ["U1.8", ...]}]
    placed         [{designator, side}]           side "Top" or "Bottom"
    board_rules    [{name, descriptor, enabled}]

A check whose input could not be read is reported as not run, never as
passed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from ..units import MM_PER_MIL

LEVELS = ("error", "warning", "note")
_NAME = r"[A-Za-z0-9_-]{1,64}"


@dataclass(frozen=True)
class Kind:
    fields: tuple[str, ...]          # what a check of this kind must give
    reads: frozenset[str]            # what it reads from the design


KINDS: dict[str, Kind] = {
    "params_present": Kind(("parameters",), frozenset({"symbols", "symbol_params"})),
    "param_matches": Kind(("parameter", "pattern"), frozenset({"symbols", "symbol_params"})),
    "param_not_in": Kind(("parameter", "values"), frozenset({"symbols", "symbol_params"})),
    "prefix_for_symbol": Kind(("map",), frozenset({"symbols"})),
    "net_names": Kind(("pattern",), frozenset({"nets"})),
    "no_lone_pins": Kind((), frozenset({"nets"})),
    "net_has_part": Kind(("nets", "prefix"), frozenset({"nets"})),
    "board_rule_present": Kind(("kind",), frozenset({"board_rules"})),
    "parts_on_side": Kind(("prefixes", "side"), frozenset({"placed"})),
    "note": Kind(("text",), frozenset()),
}
#: Net names Altium makes up for unnamed nets.
_MADE_UP_NET = re.compile(r"(?:Net|NetC|N\$)\w*")
_STATED_LENGTH = re.compile(r"(\w+)\s*=\s*(\d*\.?\d+)\s*(mm|mil|um)\b", re.I)


def to_mm(text: str) -> float:
    """"0.15mm", "6 mil" or "150um" in mm."""
    hit = re.fullmatch(r"\s*(\d*\.?\d+)\s*(mm|mil|um)\s*", text or "", re.I)
    if not hit:
        raise ValueError(f"{text!r} is not a length (a number then mm, mil or um)")
    scale = {"mm": 1.0, "mil": MM_PER_MIL, "um": 0.001}[hit.group(2).lower()]
    return float(hit.group(1)) * scale


def _bad_pattern(pattern: Any) -> Optional[str]:
    try:
        re.compile(pattern)
    except (re.error, TypeError) as exc:
        return str(exc)
    return None


def problems_in(rules: Any) -> list[str]:
    """Everything wrong with a rules file; empty when it can run."""
    if not isinstance(rules, dict) or not isinstance(rules.get("checks"), list):
        return ["a rules file is an object with a 'checks' list"]
    found = []
    if not re.fullmatch(_NAME, str(rules.get("name", ""))):
        found.append("'name' is letters, digits, _ and - (64 at most)")
    used: set[str] = set()
    for n, check in enumerate(rules["checks"]):
        if not isinstance(check, dict):
            found.append(f"checks[{n}] is not an object")
            continue
        label = f"checks[{n}] ({check.get('id', '?')})"
        cid, kind = str(check.get("id", "")), check.get("check")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", cid):
            found.append(f"{label}: the id is missing or unusable")
        elif cid in used:
            found.append(f"{label}: the id is taken by an earlier check")
        used.add(cid)
        if kind not in KINDS:
            found.append(f"{label}: check {kind!r} is none of {', '.join(KINDS)}")
            continue
        found += [f"{label}: {f} is missing" for f in KINDS[kind].fields if f not in check]
        if check.get("level", "warning") not in LEVELS:
            found.append(f"{label}: level is error, warning or note")
        for key in ("pattern", "nets"):
            if key in check and _bad_pattern(check[key]):
                found.append(f"{label}: {key} is not a regular expression: "
                             f"{_bad_pattern(check[key])}")
        for entry in check.get("map", []) if kind == "prefix_for_symbol" else []:
            if not isinstance(entry, dict) or _bad_pattern(entry.get("symbol")) \
                    or not isinstance(entry.get("prefixes"), list):
                found.append(f"{label}: each map entry is {{symbol: pattern, prefixes: [..]}}")
        for key, length in (check.get("at_least") or {}).items():
            try:
                to_mm(length)
            except ValueError as exc:
                found.append(f"{label}: at_least {key}: {exc}")
    return found


def inputs_of(rules: dict) -> set[str]:
    """The design data the enabled checks read."""
    return set().union(*(KINDS[c["check"]].reads for c in rules["checks"]
                         if not c.get("off") and c.get("check") in KINDS))


def rule_files(policy: Optional[Path]) -> list[str]:
    folder = policy / "rules" if policy else None
    return sorted(p.stem for p in folder.glob("*.json")) if folder and folder.is_dir() else []


def open_rules(policy: Optional[Path], name: str) -> dict:
    if not re.fullmatch(_NAME, name or ""):
        raise ValueError("a rules name is letters, digits, _ and -")
    if policy is None:
        raise FileNotFoundError(f"no rules {name}: set EDA_AGENT_POLICY_DIR")
    path = policy / "rules" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no rules {name} in {path.parent}")
    rules = json.loads(path.read_text(encoding="utf-8"))
    trouble = problems_in(rules)
    if trouble:
        raise ValueError(f"rules {name}: " + "; ".join(trouble))
    return rules


def _letters(designator: str) -> str:
    hit = re.match(r"[A-Za-z]+", designator or "")
    return hit.group(0).upper() if hit else ""


def _covers(check: dict, designator: str) -> bool:
    """Whether the check's optional ``only`` limits include this part."""
    only = check.get("only") or {}
    prefixes = [p.upper() for p in only.get("prefixes", [])]
    if prefixes and _letters(designator) not in prefixes:
        return False
    return not only.get("pattern") or bool(re.fullmatch(only["pattern"], designator))


Hit = tuple[str, str]          # (object, message)


def _params_present(check: dict, data: dict) -> Iterator[Hit]:
    for part in data["symbols"]:
        ref = part["designator"]
        if _covers(check, ref):
            values = data["symbol_params"].get(ref, {})
            for name in check["parameters"]:
                if not str(values.get(name, "")).strip():
                    yield ref, f"{name} is empty or missing"


def _param_matches(check: dict, data: dict) -> Iterator[Hit]:
    for part in data["symbols"]:
        ref = part["designator"]
        value = data["symbol_params"].get(ref, {}).get(check["parameter"])
        if value is not None and _covers(check, ref) \
                and not re.fullmatch(check["pattern"], str(value)):
            yield ref, f"{check['parameter']} {value!r} does not fit {check['pattern']}"


def _param_not_in(check: dict, data: dict) -> Iterator[Hit]:
    banned = {str(v).casefold() for v in check["values"]}
    for part in data["symbols"]:
        ref = part["designator"]
        value = data["symbol_params"].get(ref, {}).get(check["parameter"])
        if value is not None and str(value).casefold() in banned and _covers(check, ref):
            yield ref, f"{check['parameter']} {value!r} is not allowed"


def _prefix_for_symbol(check: dict, data: dict) -> Iterator[Hit]:
    for part in data["symbols"]:
        symbol = part.get("lib_ref", "")
        entry = next((e for e in check["map"] if re.search(e["symbol"], symbol)), None)
        if entry and _letters(part["designator"]) not in [p.upper() for p in entry["prefixes"]]:
            yield part["designator"], (f"{symbol} is designated "
                                       + " or ".join(entry["prefixes"]))


def _net_names(check: dict, data: dict) -> Iterator[Hit]:
    for net in data["nets"]:
        if check.get("skip_unnamed", True) and _MADE_UP_NET.fullmatch(net["name"]):
            continue
        if not re.fullmatch(check["pattern"], net["name"]):
            yield net["name"], f"does not fit {check['pattern']}"


def _no_lone_pins(check: dict, data: dict) -> Iterator[Hit]:
    excused = [re.compile(p) for p in check.get("except", [])]
    for net in data["nets"]:
        if len(net["pins"]) < 2 and not any(e.fullmatch(net["name"]) for e in excused):
            yield net["name"], f"{len(net['pins'])} pin on the net"


def _net_has_part(check: dict, data: dict) -> Iterator[Hit]:
    which, prefix = re.compile(check["nets"]), check["prefix"].upper()
    need = int(check.get("at_least", 1))
    for net in data["nets"]:
        if which.search(net["name"]):
            parts = {pin.split(".")[0] for pin in net["pins"]}
            have = sum(1 for p in parts if _letters(p) == prefix)
            if have < need:
                yield net["name"], f"has {have} {prefix} part(s), needs {need}"


def _board_rule_present(check: dict, data: dict) -> Iterator[Hit]:
    word = check["kind"].lower()
    present = [r for r in data["board_rules"]
               if word in f"{r.get('descriptor', '')} {r.get('name', '')}".lower()
               and str(r.get("enabled", True)).lower() in ("true", "1")
               and (not check.get("name") or r.get("name") == check["name"])]
    if not present:
        yield check["kind"], "no enabled board rule of this kind"
        return
    for key, floor_text in (check.get("at_least") or {}).items():
        floor = to_mm(floor_text)
        # A kind word catches neighbours too ("clearance" also finds
        # silk-to-silk, which states Clearance rather than Gap), so the floor
        # applies to the rules that state this key.
        stating = 0
        for rule in present:
            stated = {k.lower(): to_mm(v + u) for k, v, u in
                      _STATED_LENGTH.findall(str(rule.get("descriptor", "")))}
            if key.lower() in stated:
                stating += 1
                if stated[key.lower()] + 1e-9 < floor:
                    yield rule.get("name", ""), (f"{key} {stated[key.lower()]:.4f} mm, "
                                                 f"below {floor:.4f} mm")
        if not stating:
            yield check["kind"], f"none of these rules states {key}"


def _parts_on_side(check: dict, data: dict) -> Iterator[Hit]:
    prefixes = {p.upper() for p in check["prefixes"]}
    side = check["side"].lower()
    for part in data["placed"]:
        if _letters(part["designator"]) in prefixes \
                and not str(part.get("side", "")).lower().startswith(side):
            yield part["designator"], f"placed on {part.get('side')}, belongs on {check['side']}"


_RUNNERS: dict[str, Callable[[dict, dict], Iterator[Hit]]] = {
    "params_present": _params_present, "param_matches": _param_matches,
    "param_not_in": _param_not_in, "prefix_for_symbol": _prefix_for_symbol,
    "net_names": _net_names, "no_lone_pins": _no_lone_pins,
    "net_has_part": _net_has_part, "board_rule_present": _board_rule_present,
    "parts_on_side": _parts_on_side,
}


def run_rules(rules: dict, data: dict) -> dict:
    """Findings, notes and checks not run, for one rules file on one design."""
    findings, notes, not_run = [], [], []
    for check in rules["checks"]:
        if check.get("off"):
            continue
        absent = sorted(KINDS[check["check"]].reads - set(data))
        if absent:
            not_run.append({"check": check["id"], "why": "could not read "
                            + ", ".join(absent)})
            continue
        level = check.get("level", "note" if check["check"] == "note" else "warning")
        if check["check"] == "note":
            notes.append({"check": check["id"], "level": level, "text": check["text"]})
            continue
        findings += [{"check": check["id"], "level": level, "object": obj, "message": msg}
                     for obj, msg in _RUNNERS[check["check"]](check, data)]
    tally = {lv: sum(1 for f in findings if f["level"] == lv) for lv in LEVELS}
    return {"rules": rules["name"], "passed": not tally["error"], "tally": tally,
            "findings": findings, "notes": notes, "not_run": not_run}


def read_rules_export(text: str) -> list[dict[str, str]]:
    """Records of an Altium rules export (.RUL): one rule per line, fields
    ``|KEY=VALUE|``, keys upper-cased but otherwise as Altium writes them
    (they differ between rule kinds and Altium versions)."""
    records = []
    for line in (text or "").splitlines():
        pairs = [chunk.split("=", 1) for chunk in line.strip().strip("|").split("|")
                 if "=" in chunk]
        if pairs:
            records.append({k.strip().upper(): v.strip() for k, v in pairs})
    return records
