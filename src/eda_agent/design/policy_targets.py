# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A company's board rules and ERC levels, kept in its policy folder, and
what a board or project would need to match them.

``board_rules.json`` lists the PCB design rules a board should carry:

    {"rules": [{"name": "HV_Clearance", "rule_type": "clearance",
                "scope": "InNetClass('HV')", "value": 20},
               {"name": "Power_Width", "rule_type": "width",
                "scope": "InNetClass('PWR')", "value": 12, "max_value": 100,
                "favored_value": 20, "comment": "house minimum"}]}

Each rule takes pcb_create_design_rule's arguments, plus ``enabled``,
``comment`` and ``set`` (more pcb_set_rule_properties fields, written after).

``erc_levels.json`` is the default ERC set and ``erc_levels/<name>.json``
the named ones: ``{"extends": "base", "levels": {"Nets with no driving
source": "no_report"}}``. A set's own levels win over the one it extends;
a level of null drops an inherited one.

Nothing here reads Altium or writes a file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from ..units import MILS_PER_MM

RULE_TYPES = {
    "clearance", "width", "via_size", "differential_pairs",
    "solder_mask_expansion", "paste_mask_expansion", "vias_under_smd",
    "polygon_connect", "plane_connect", "length", "signal_impedance",
    "flight_time_rising", "flight_time_falling",
}
_CREATE_KEYS = {"rule_type", "value", "max_value", "favored_value",
                "max_uncoupled_length", "scope", "net_scope", "allowed",
                "connect_style"}
_RULE_KEYS = _CREATE_KEYS | {"name", "enabled", "comment", "set"}
LEVELS = {"no_report", "warning", "error", "fatal"}
_SET_NAME = r"[A-Za-z0-9_-]+"

#: How a rule's values reach pcb_set_rule_properties when the rule is
#: already on the board: (value, max_value, favored_value) field names.
_UPDATE_FIELDS = {
    "clearance": ("gap_mils", None, None),
    "width": ("min_width_mils", "max_width_mils", "favored_width_mils"),
    "via_size": ("min_hole_size_mils", "max_hole_size_mils", None),
    "differential_pairs": ("dp_min_gap_mils", "dp_max_gap_mils", "dp_preferred_gap_mils"),
    "length": ("min_length_mils", "max_length_mils", None),
    "signal_impedance": ("min_signal_impedance_ohms", "max_signal_impedance_ohms", None),
    "flight_time_rising": ("max_flight_time_ns", None, None),
    "flight_time_falling": ("max_flight_time_ns", None, None),
    "polygon_connect": (None, None, None),
    "plane_connect": (None, None, None),
}


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: not JSON ({exc.msg}, line {exc.lineno})") from None


def rule_problems(rules: Any) -> list[str]:
    """What is wrong with a board_rules list, each named by its rule."""
    if not isinstance(rules, list):
        return ["rules is a list"]
    out, seen = [], set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            out.append(f"rule {i + 1} is not an object")
            continue
        name = str(rule.get("name") or "").strip()
        who = name or f"rule {i + 1}"
        if not name:
            out.append(f"{who} has no name")
        elif name.lower() in seen:
            out.append(f"{who} is listed twice")
        seen.add(name.lower())
        if rule.get("rule_type", "clearance") not in RULE_TYPES:
            out.append(f"{who}: rule_type is one of {', '.join(sorted(RULE_TYPES))}")
        unknown = sorted(set(rule) - _RULE_KEYS - {k for k in rule if k.startswith("_")})
        if unknown:
            out.append(f"{who}: unknown keys {unknown}")
        if "set" in rule and (not isinstance(rule["set"], dict) or any(
                isinstance(v, (dict, list)) or v is None for v in rule["set"].values())):
            out.append(f"{who}: set is an object of pcb_set_rule_properties fields, "
                       "each a number, text or true/false")
        for key in ("value", "max_value", "favored_value", "max_uncoupled_length"):
            if key in rule and (isinstance(rule[key], bool)
                                or not isinstance(rule[key], (int, float))):
                out.append(f"{who}: {key} is a number")
    return out


def load_board_rules(folder: Optional[Path]) -> list[dict]:
    """The policy's board rules; [] when it has none."""
    path = folder / "board_rules.json" if folder else None
    if path is None or not path.is_file():
        return []
    raw = _read(path)
    rules = raw.get("rules") if isinstance(raw, dict) else None
    trouble = rule_problems(rules)
    if trouble:
        raise ValueError("board_rules.json: " + "; ".join(trouble))
    return rules


def create_args(rule: dict) -> dict[str, Any]:
    """pcb_create_design_rule's arguments for a policy rule."""
    args = {k: rule[k] for k in _CREATE_KEYS if k in rule}
    args["name"] = rule["name"]
    args.setdefault("rule_type", "clearance")
    return args


def update_args(rule: dict) -> tuple[dict[str, Any], str]:
    """pcb_set_rule_properties fields that bring a rule already on the
    board to the policy, and '' or why its value cannot be changed there."""
    kind = rule.get("rule_type", "clearance")
    args: dict[str, Any] = {}
    note = ""
    fields = _UPDATE_FIELDS.get(kind)
    if fields is None:
        if any(k in rule for k in ("value", "allowed")):
            note = (f"a {kind} rule's value cannot be changed in place; delete "
                    "the rule and apply again to remake it")
    else:
        for key, field in zip(("value", "max_value", "favored_value"), fields):
            if field and key in rule:
                args[field] = rule[key]
    if kind in ("polygon_connect", "plane_connect") and "connect_style" in rule:
        args["connect_style"] = rule["connect_style"]
    if "scope" in rule:
        args["scope1"] = rule["scope"]
    for key in ("enabled", "comment"):
        if key in rule:
            args[key] = rule[key]
    args.update(rule.get("set") or {})
    return args, note


def _same_scope(a: Any, b: Any) -> bool:
    squash = lambda s: re.sub(r"\s+", "", str(s or "")).lower()  # noqa: E731
    return squash(a) == squash(b)


def plan_board_rules(targets: list[dict], board: list[dict]) -> dict[str, Any]:
    """What applying the policy would do to a board's rules.

    ``create``: rules the board lacks. ``update``: rules it has, with
    the fields that would be written and ``differs``, the readable fields
    that do not match now (scope, enabled, comment, a clearance gap); the
    other values are written and read back on apply rather than compared.
    ``extra``: rules on the board the policy does not list, never deleted.
    """
    on_board = {str(r.get("name", "")).lower(): r for r in board if isinstance(r, dict)}
    create, update = [], []
    for rule in targets:
        have = on_board.get(rule["name"].lower())
        if have is None:
            create.append(create_args(rule))
            continue
        args, note = update_args(rule)
        differs = []
        if "scope" in rule and not _same_scope(rule["scope"], have.get("scope_1")):
            differs.append({"field": "scope", "board": have.get("scope_1"),
                            "policy": rule["scope"]})
        if "enabled" in rule and bool(have.get("enabled")) != bool(rule["enabled"]):
            differs.append({"field": "enabled", "board": have.get("enabled"),
                            "policy": rule["enabled"]})
        if "comment" in rule and str(have.get("comment") or "") != str(rule["comment"]):
            differs.append({"field": "comment", "board": have.get("comment"),
                            "policy": rule["comment"]})
        gap = have.get("gap_mm")
        if (rule.get("rule_type", "clearance") == "clearance" and "value" in rule
                and isinstance(gap, (int, float))
                and abs(gap * MILS_PER_MM - float(rule["value"])) > 0.01):
            differs.append({"field": "value", "board": round(gap * MILS_PER_MM, 3),
                            "policy": rule["value"]})
        row = {"name": have.get("name", rule["name"]), "write": args, "differs": differs}
        if note:
            row["note"] = note
        update.append(row)
    listed = {r["name"].lower() for r in targets}
    extra = sorted(str(r.get("name", "")) for r in board
                   if isinstance(r, dict) and str(r.get("name", "")).lower() not in listed)
    return {"create": create, "update": update, "extra": extra}


def erc_set_names(folder: Optional[Path]) -> list[str]:
    """The named ERC sets in the policy, and "" for erc_levels.json."""
    if folder is None:
        return []
    names = [""] if (folder / "erc_levels.json").is_file() else []
    sets = folder / "erc_levels"
    if sets.is_dir():
        names += sorted(p.stem for p in sets.glob("*.json"))
    return names


def _erc_file(folder: Path, name: str) -> Path:
    if name and not re.fullmatch(_SET_NAME, name):
        raise ValueError("an ERC set name is letters, digits, _ and -")
    return folder / "erc_levels.json" if not name else folder / "erc_levels" / f"{name}.json"


def load_erc_levels(folder: Optional[Path], name: str = "") -> dict[str, Any]:
    """An ERC set with what it extends folded in, nearest file winning.

    Returns ``levels`` ({violation: level}) and ``chain``, the files
    merged, base first.
    """
    if folder is None:
        raise FileNotFoundError("set EDA_AGENT_POLICY_DIR to a policy folder")
    chain: list[str] = []
    layers: list[dict] = []
    current: Optional[str] = name
    while current is not None:
        path = _erc_file(folder, current)
        if not path.is_file():
            raise FileNotFoundError(f"no ERC set {current or '(default)'} at {path}")
        if str(path) in chain:
            raise ValueError(f"ERC sets extend each other in a loop: {chain + [str(path)]}")
        chain.append(str(path))
        raw = _read(path)
        if not isinstance(raw, dict) or not isinstance(raw.get("levels", {}), dict):
            raise ValueError(f"{path.name}: an object with a levels object")
        for kind, level in raw.get("levels", {}).items():
            if level is not None and str(level).lower() not in LEVELS:
                raise ValueError(f"{path.name}: {kind!r} is one of "
                                 f"{', '.join(sorted(LEVELS))} or null")
            if "~~" in str(kind) or "=" in str(kind):
                raise ValueError(f"{path.name}: {kind!r} cannot hold '~~' or '='")
        layers.append(raw.get("levels", {}))
        nxt = raw.get("extends")
        current = None if nxt is None else str(nxt)
    levels: dict[str, str] = {}
    for layer in reversed(layers):
        for kind, level in layer.items():
            if level is None:
                levels.pop(kind, None)
            else:
                levels[kind] = str(level).lower()
    return {"levels": levels, "chain": list(reversed(chain))}


def plan_erc_levels(targets: dict[str, str], current: list[dict]) -> dict[str, Any]:
    """Which ERC levels differ between a project and the policy.

    A target names a violation by its description (case aside) or its
    kind number, as proj_set_erc_levels takes it. ``unknown``: targets
    the project does not list.
    """
    by_desc = {str(r.get("description", "")).lower(): r for r in current if isinstance(r, dict)}
    by_kind = {str(r.get("kind")): r for r in current if isinstance(r, dict)}
    change, same, unknown = [], [], []
    for kind, level in targets.items():
        row = by_desc.get(str(kind).lower()) or by_kind.get(str(kind))
        if row is None:
            unknown.append(kind)
        elif str(row.get("level", "")).lower() == level:
            same.append(kind)
        else:
            change.append({"violation": row.get("description", kind),
                           "project": row.get("level"), "policy": level})
    return {"change": change, "same": same, "unknown": unknown}
