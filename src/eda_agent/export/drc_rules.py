# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What a DRC was checked against, and whether that is what was approved.

A DRC that finds nothing says the board meets its rules, not that the
rules are the ones the designer set. A clearance loosened from 6 mil to
2 mil, a rule switched out of DRC, a scope narrowed or a rule deleted
all make the same board pass. So every DRC reply carries the rule sheet
it ran under, a fingerprint of it, and its difference from the rules the
user approved (a file beside the board), and its verdict says "passed
under changed rules" rather than "pass" when they differ. Rule changes
made through the tools are logged beside it too.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from ..units import MILS_PER_MM

#: Fields that decide what a rule checks. The comment is left out: it
#: changes nothing about the check.
_FIELDS = ("descriptor", "scope_1", "scope_2", "priority", "enabled",
           "drc_enabled", "gap_mm", "from_schematic")

BASELINE_SUFFIX = ".DrcRules.json"
LOG_SUFFIX = ".DrcRules.log.jsonl"


def sheet(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rules as checked: name and the fields that decide the check."""
    out = []
    for r in rules or []:
        row = {"name": str(r.get("name", ""))}
        for f in _FIELDS:
            if f in r:
                row[f] = r[f]
        out.append(row)
    return sorted(out, key=lambda r: r["name"].lower())


def fingerprint(rules: list[dict[str, Any]]) -> str:
    """A short hash of the rule sheet; equal sheets, equal fingerprints."""
    text = json.dumps(sheet(rules), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def baseline_path(board_path: str) -> Optional[Path]:
    if not board_path:
        return None
    p = Path(board_path)
    return p.with_name(p.stem + BASELINE_SUFFIX)


def log_path(board_path: str) -> Optional[Path]:
    if not board_path:
        return None
    p = Path(board_path)
    return p.with_name(p.stem + LOG_SUFFIX)


def read_baseline(board_path: str) -> Optional[dict[str, Any]]:
    p = baseline_path(board_path)
    if p is None or not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rules"), list) else None


def write_baseline(board_path: str, rules: list[dict[str, Any]],
                   note: str = "") -> dict[str, Any]:
    p = baseline_path(board_path)
    if p is None:
        raise ValueError("no board path to keep the approved rules beside")
    data = {
        "board": str(board_path),
        "approved_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "fingerprint": fingerprint(rules),
        "rules": sheet(rules),
    }
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return {"baseline_path": str(p), "fingerprint": data["fingerprint"],
            "rules": len(data["rules"]), "approved_at": data["approved_at"]}


_VALUE = re.compile(r"([A-Za-z][\w ]*?)\s*=\s*(-?\d+(?:\.\d+)?)\s*(mil|mm|um|°|deg)?",
                    re.IGNORECASE)


def _values(descriptor: str) -> dict[str, float]:
    """Numbers a descriptor names (``Gap=10mil``), in mils."""
    out: dict[str, float] = {}
    for m in _VALUE.finditer(descriptor or ""):
        key = m.group(1).strip().lower()
        val = float(m.group(2))
        unit = (m.group(3) or "").lower()
        if unit == "mm":
            val = val * MILS_PER_MM
        elif unit == "um":
            val = val * MILS_PER_MM / 1000.0
        out[key] = round(val, 4)
    return out


def _direction(key: str, before: float, after: float) -> str:
    """'loosened', 'tightened' or 'changed' for one value of a rule."""
    if after == before:
        return "same"
    k = key.lower()
    if "pref" in k:
        return "changed"
    lower_is_looser = any(w in k for w in ("min", "gap", "clearance", "expansion",
                                           "width", "ring", "spacing", "distance"))
    higher_is_looser = "max" in k or "limit" in k or "tolerance" in k
    if "max" in k:
        lower_is_looser = False
    if lower_is_looser:
        return "loosened" if after < before else "tightened"
    if higher_is_looser:
        return "loosened" if after > before else "tightened"
    return "changed"


def diff(approved: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each rule added, removed or changed since the approved sheet.

    ``loosened`` is True where the change can only let more through
    (removed, switched off, a gap made smaller), None where it may
    (a new rule, a new scope or priority, a value with no known
    direction): those need a person to look. False where it is stricter.
    """
    a = {r["name"]: r for r in sheet(approved)}
    c = {r["name"]: r for r in sheet(current)}
    out: list[dict[str, Any]] = []
    for name in sorted(set(a) | set(c), key=str.lower):
        before, after = a.get(name), c.get(name)
        if before is None:
            out.append({"rule": name, "change": "added", "after": after,
                        "loosened": None})
            continue
        if after is None:
            out.append({"rule": name, "change": "removed", "before": before,
                        "loosened": True})
            continue
        for f in _FIELDS:
            bv, av = before.get(f), after.get(f)
            if bv == av:
                continue
            entry = {"rule": name, "change": f, "before": bv, "after": av}
            if f in ("enabled", "drc_enabled"):
                entry["loosened"] = bool(bv) and not bool(av)
            elif f == "gap_mm" and isinstance(bv, (int, float)) and isinstance(av, (int, float)):
                entry["loosened"] = av < bv
            elif f == "descriptor":
                bvals, avals = _values(str(bv)), _values(str(av))
                moves = {k: _direction(k, bvals[k], avals[k])
                         for k in bvals if k in avals and bvals[k] != avals[k]}
                entry["values"] = moves
                if any(m == "loosened" for m in moves.values()):
                    entry["loosened"] = True
                elif moves and all(m == "tightened" for m in moves.values()) \
                        and set(bvals) == set(avals):
                    entry["loosened"] = False
                else:
                    entry["loosened"] = None
            else:
                entry["loosened"] = None
            out.append(entry)
    return out


def not_checked(rules: list[dict[str, Any]],
                report_rules: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Rules this DRC did not check, and why."""
    # Altium's report names each rule it checked by its descriptor
    # (MEASURED: "Hole Size Constraint (Min=1mil) (Max=130mil) (All)"),
    # with a row for every rule checked, violations or not.
    def norm(text: Any) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    listed = None
    if report_rules is not None:
        listed = {norm(r.get("rule", "")) for r in report_rules}
    out = []
    for r in sheet(rules):
        if r.get("enabled") is False:
            out.append({"rule": r["name"], "why": "disabled"})
        elif r.get("drc_enabled") is False:
            out.append({"rule": r["name"], "why": "switched out of DRC"})
        elif listed is not None and norm(r.get("descriptor")) not in listed:
            out.append({"rule": r["name"], "why": "not in Altium's report"})
    return out


def log_change(board_path: str, tool: str, args: dict[str, Any],
               changes: list[dict[str, Any]], session: str = "") -> None:
    """Append one rule change made through a tool to the board's log."""
    p = log_path(board_path)
    if p is None or not changes:
        return
    entry = {"at": _dt.datetime.now().isoformat(timespec="seconds"),
             "session": session, "tool": tool, "args": args,
             "changes": changes}
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def read_log(board_path: str, since: str = "") -> list[dict[str, Any]]:
    p = log_path(board_path)
    if p is None or not p.is_file():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not since or str(e.get("at", "")) >= since:
                out.append(e)
    except OSError:
        return []
    return out


def verdict(drc: dict[str, Any], baseline: Optional[dict[str, Any]],
            changes: list[dict[str, Any]]) -> tuple[str, str]:
    """One word for the result and the sentence that explains it."""
    if drc.get("drc_confirmed") is not True:
        return "not_run", "the DRC did not confirm it ran; no result"
    if drc.get("report_stopped_at_limit"):
        return "incomplete", "Altium stopped at its violation limit; the check is not complete"
    n = int(drc.get("violation_count") or 0)
    if n:
        loose = sum(1 for c in changes if c.get("loosened") is True)
        if baseline is not None and changes:
            return "fail", (f"{n} violation(s), under rules that differ from the approved "
                            f"ones ({loose} change(s) let more through): the count is not "
                            f"the count under the approved rules")
        return "fail", f"{n} violation(s)"
    waived = int((drc.get("report_counts") or {}).get("waived_violations") or 0)
    if baseline is None:
        return ("pass_unapproved_rules",
                "no violations, under rules nobody has approved yet: read rules.sheet, "
                "and approve it with pcb_drc_rules(action='approve') once the user has")
    if changes:
        loose = sum(1 for c in changes if c.get("loosened") is True)
        review = sum(1 for c in changes if c.get("loosened") is None)
        return ("pass_under_changed_rules",
                f"no violations, but the rules differ from the approved ones: "
                f"{loose} change(s) let more through, {review} need review "
                f"(rules.changes_since_approval)")
    if waived:
        return "pass_with_waivers", f"no violations under the approved rules; {waived} waived"
    return "pass", "no violations under the approved rules"


async def enrich_drc(bridge, result: dict[str, Any]) -> None:
    """Add to a batch DRC reply what Altium's report says, the rules it ran
    under, their difference from the approved ones, and the verdict.

    Every route that runs Altium's DRC goes through this, so none of them
    can report a pass without saying under which rules.
    """
    from .drc_report import read_drc_report

    if result.get("drc_confirmed") is True and isinstance(result.get("report_path"), str):
        report = read_drc_report(result["report_path"])
        if report:
            result["report_counts"] = report["counts"]
            if report["rules"] is not None:
                result["report_rules"] = report["rules"]
                result["report_rules_add_up"] = report["rules_add_up"]
            result["report_stopped_at_limit"] = report["stopped_at_limit"]
            if report["stopped_at_limit"]:
                result["report_violation_limit"] = report["violation_limit"]
    try:
        got = await bridge.send_command_async("pcb.get_design_rules", {})
    except Exception as exc:  # the verdict must say the rules are unknown
        result["rules"] = {"read": False, "reason": f"could not read the rules: {exc}"}
        result["verdict"] = "rules_unknown"
        result["verdict_reason"] = "the rules this DRC ran under could not be read"
        return
    rules = (got or {}).get("rules") or []
    board = str(result.get("board_path") or (got or {}).get("board_path") or "")
    baseline = read_baseline(board)
    changes = diff(baseline["rules"], rules) if baseline else []
    section: dict[str, Any] = {
        "read": True,
        "count": len(rules),
        "fingerprint": fingerprint(rules),
        "sheet": sheet(rules),
        "not_checked": not_checked(rules, result.get("report_rules")),
        "approved": baseline is not None,
    }
    if baseline:
        section["approved_at"] = baseline.get("approved_at")
        section["approved_fingerprint"] = baseline.get("fingerprint")
        section["changes_since_approval"] = changes
        section["logged_changes_since_approval"] = read_log(
            board, since=str(baseline.get("approved_at") or ""))
    else:
        section["baseline_path"] = str(baseline_path(board) or "")
    try:
        pours = await bridge.send_command_async("generic.query_objects", {
            "object_type": "ePolyObject", "properties": "Name,Net,Layer",
            "filter": "IgnoreViolations=true", "limit": "0"})
        section["pours_ignoring_violations"] = (pours or {}).get("objects", [])
    except Exception:  # unread is None, never an empty list that reads as none
        section["pours_ignoring_violations"] = None
    result["rules"] = section
    word, why = verdict(result, baseline, changes)
    if word.startswith("pass") and section["pours_ignoring_violations"]:
        why += (f"; {len(section['pours_ignoring_violations'])} pour(s) are set "
                "to ignore violations")
    result["verdict"] = word
    result["verdict_reason"] = why


async def logged_rule_edit(bridge, tool: str, args: dict[str, Any], run) -> Any:
    """Run ``run()`` (an edit that can change design rules) and say what it
    changed: the rules are read before and after, every difference goes
    into the reply as ``rule_changes`` and into the board's rule log, so
    the next DRC lists it beside its verdict.
    """
    from ..bridge.altium_bridge import _SESSION_TAG

    try:
        before = await bridge.send_command_async("pcb.get_design_rules", {})
    except Exception:  # the edit still runs; only its log is lost, and the reply says so
        before = None
    result = await run()
    if before is None and isinstance(result, dict):
        result["rule_changes_unread"] = "the rules could not be read before the edit"
    if isinstance(before, dict) and isinstance(result, dict):
        try:
            after = await bridge.send_command_async("pcb.get_design_rules", {})
            changes = diff(before.get("rules") or [], after.get("rules") or [])
            board = str(after.get("board_path") or before.get("board_path") or "")
            log_change(board, tool, args, changes, session=_SESSION_TAG)
            result["rule_changes"] = changes
        except Exception as exc:
            result["rule_changes_unread"] = str(exc)
    return result


def is_rule_type(object_type: str) -> bool:
    """Whether a generic object type names design rules."""
    return str(object_type or "").strip().lower() in ("rule", "ruleobject", "erule", "eruleobject")
