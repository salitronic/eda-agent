# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A programmatic PCB selection must not be vetoed by the Selection Filter.

The PCB / PcbLib editor's Selection Filter (the per-type toggles in the
Properties panel) also applies to ``Prim.Selected := True``: for a type
switched off there the assignment is silently refused. A handler that
selects and then runs a process on the selection (PCB:Clear,
PCB:PlaceBoardOutline, PCB:Gloss, ...) or reports what it selected then
did nothing for that type and still answered success. Measured live: with
Tracks off in the filter a track was not selected; with
``Board.SelectedObjects_FilterDisabled := True`` it was.

The guard: every function that selects PCB objects switches the filter
off first (``PcbSelectFilterOff``) and puts it back from a Finally
(``PcbSelectFilterRestore``), or is only reached from a function that
does so around the call.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import functions, strip_comments

ROOT = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNIT_ORDER = ["Main", "Utils", "Application", "Project", "Library",
              "PCBGeneric", "PCB", "Generic", "StatusForm", "Dispatcher",
              "SelfTest", "Audit"]

OFF = "PcbSelectFilterOff"
RESTORE = "PcbSelectFilterRestore"

# A selection flag set to anything but a literal False. Schematic objects
# use .Selection, not .Selected, so this only matches PCB primitives.
SELECTS = re.compile(r"\.Selected\s*:=\s*(?!False\b)|\bSelectedObjects_Add\s*\(",
                     re.IGNORECASE)


def _units() -> dict[str, dict[str, str]]:
    units = {}
    for name in UNIT_ORDER:
        path = ROOT / f"{name}.pas"
        if path.exists():
            text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            units[name] = functions(text)
    total = sum(len(f) for f in units.values())
    assert total >= 500, f"only {total} functions parsed: the scan would pass over nothing"
    return units


def _all_functions() -> dict[str, str]:
    found: dict[str, str] = {}
    for funcs in _units().values():
        found.update(funcs)
    return found


def _is_schematic_only(body: str) -> bool:
    return "ISch_" in body and "IPCB_" not in body


def _selecting(funcs: dict[str, str]) -> dict[str, int]:
    """{name: offset of its first PCB selection} for every selecting function."""
    out = {}
    for name, body in funcs.items():
        if name in (OFF, RESTORE) or _is_schematic_only(body):
            continue
        m = SELECTS.search(body)
        if m:
            out[name] = m.start()
    return out


def _covered_span(body: str):
    """(start, end) between the first Off and the last Restore, else None."""
    off = body.find(OFF + "(")
    restore = body.rfind(RESTORE + "(")
    if off < 0 or restore < 0 or restore < off:
        return None
    return off, restore


def _restore_in_finally(body: str) -> list[str]:
    """Every Restore call must sit inside a Finally block that is still open."""
    problems = []
    for m in re.finditer(re.escape(RESTORE) + r"\s*\(", body):
        before = body[:m.start()]
        fin = [f.end() for f in re.finditer(r"\bFinally\b", before, re.IGNORECASE)]
        if not fin:
            problems.append("no Finally before it")
            continue
        depth = 0
        closed = False
        for tok in re.finditer(r"\b(Try|Begin|Case|End)\b", before[fin[-1]:],
                               re.IGNORECASE):
            depth += -1 if tok.group(1).lower() == "end" else 1
            if depth < 0:
                closed = True
                break
        if closed:
            problems.append("after its Finally block has closed")
    return problems


def _callers(funcs: dict[str, str], name: str) -> dict[str, list[int]]:
    pat = re.compile(r"\b" + re.escape(name) + r"\s*\(", re.IGNORECASE)
    out = {}
    for other, body in funcs.items():
        if other == name:
            continue
        hits = [m.start() for m in pat.finditer(body)]
        if hits:
            out[other] = hits
    return out


def _reached_only_covered(funcs, name, seen) -> list[str]:
    """Paths from a top-level function down to ``name`` with no bypass on them."""
    if name in seen:
        return []
    seen = seen | {name}
    callers = _callers(funcs, name)
    if not callers:
        return [name]
    bad = []
    for caller, hits in callers.items():
        span = _covered_span(funcs[caller])
        if span and all(span[0] < h < span[1] for h in hits):
            continue
        bad += [f"{p} -> {name}" for p in _reached_only_covered(funcs, caller, seen)]
    return bad


def test_the_scan_finds_the_known_selecting_paths():
    sel = _selecting(_all_functions())
    for name in ("Lib_SelectFootprintPrimitives", "PCB_Gloss", "PCB_Panelize",
                 "PCB_AutoSizeBoardOutline", "PCB_FilterVariantComponents",
                 "Proj_CrossProbe", "SetPCBProperty"):
        assert name in sel, f"{name} no longer reads as selecting: the scan is broken"


def test_every_pcb_selection_switches_the_selection_filter_off():
    funcs = _all_functions()
    problems = []
    for name, first in sorted(_selecting(funcs).items()):
        span = _covered_span(funcs[name])
        if span:
            if not span[0] < first:
                problems.append(f"{name}: {OFF} comes after the selection")
            last = max(m.start() for m in SELECTS.finditer(funcs[name]))
            if not last < span[1]:
                problems.append(f"{name}: {RESTORE} comes before the last selection")
            continue
        for path in _reached_only_covered(funcs, name, frozenset()):
            problems.append(f"{name}: selects with the filter on, reached by {path}")
    assert not problems, "\n".join(problems)


def test_the_filter_is_put_back_from_a_finally():
    funcs = _all_functions()
    problems = []
    for name, body in sorted(funcs.items()):
        if name in (OFF, RESTORE):
            continue
        offs = len(re.findall(re.escape(OFF) + r"\s*\(", body))
        restores = len(re.findall(re.escape(RESTORE) + r"\s*\(", body))
        if offs or restores:
            if offs != restores:
                problems.append(f"{name}: {offs} {OFF} but {restores} {RESTORE}")
            for p in _restore_in_finally(body):
                problems.append(f"{name}: {RESTORE} {p}")
    assert not problems, "\n".join(problems)


def test_the_helpers_disable_and_restore_and_come_first():
    units = _units()
    home = [u for u, f in units.items() if OFF in f and RESTORE in f]
    assert home, "the helper pair is gone"
    # Defined in a unit every caller can see: Main or Utils precede them all.
    assert home[0] in ("Main", "Utils"), home
    off = units[home[0]][OFF]
    restore = units[home[0]][RESTORE]
    assert re.search(r"Result\s*:=\s*Board\.SelectedObjects_FilterDisabled", off)
    assert re.search(r"Board\.SelectedObjects_FilterDisabled\s*:=\s*True\b", off)
    assert re.search(r"Board\.SelectedObjects_FilterDisabled\s*:=\s*Was\b", restore)
