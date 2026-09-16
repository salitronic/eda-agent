# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The simulator never promises a reply key the Pascal does not emit.

Tests written against tests/altium_simulator.py encode ITS response
shape. Where the simulator and the Pascal disagree, a test passes and
the live call fails, which is the failure mode testing_simulator_caveat
warns about; this file MEASURES it. Both sides are machine readable:
the simulator builds JSON inside string constants, the Pascal emits
via '"key":' literal fragments and Json* helpers.

Direction matters. Only SIMULATOR-EXCESS keys are a defect here: a key
the simulator emits that the Pascal never does teaches a lying test.
The other direction (Pascal emits more than the simulator) is missing
simulator fidelity, not a lie, and stays out of scope.

First run found three, all fixed by making the simulator mirror the
Pascal: project.annotate answered {"annotated":true} for a handler
that emits success/renamed/skipped_locked/documents_processed;
library.search invented a "success" key; project.get_design_stats
invented "nets".

Extraction limits, so a pass is evidence rather than proof: simulator
keys come from string CONSTANTS (keys assembled character-by-character
are invisible); Pascal keys come from literal fragments and Json*
helper calls followed two levels deep; actions whose case arm inlines
work instead of dispatching to a function are skipped.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PASCAL_DIR = REPO / "scripts" / "altium"
SIMULATOR = REPO / "tests" / "altium_simulator.py"

#: (category, action) -> excess keys tolerated, each with its reason.
#: An entry is a decision about a DESIGNED compatibility path (the
#: bridge normalises the shapes), never a way to silence a finding.
TOLERATED: dict[tuple[str, str], dict[str, str]] = {}


# ---------------------------------------------------------------- simulator

def _simulator_keys() -> dict[tuple[str, str], set[str]]:
    """Reply keys per (category, action), from string constants.

    JSON keys appear INSIDE string constants ('{"added":%d}') while the
    simulator's internal bookkeeping dicts have bare-string keys that
    only look like JSON fragments in raw source text. Scanning constant
    VALUES via the AST kills that false-positive class, which produced
    9 of the first prototype's 12 findings.
    """
    tree = ast.parse(SIMULATOR.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], set[str]] = {}

    def action_of(test) -> str | None:
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "action"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)):
            return test.comparators[0].value
        return None

    def keys_in(nodes) -> set[str]:
        keys: set[str] = set()
        for node in nodes:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)):
                    keys |= set(re.findall(r'"(\w+)":', sub.value))
        return keys

    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if not (isinstance(method, ast.FunctionDef)
                    and method.name.startswith("_handle_")):
                continue
            category = method.name[len("_handle_"):]

            def visit(stmts):
                for stmt in stmts:
                    if isinstance(stmt, ast.If):
                        action = action_of(stmt.test)
                        if action is not None:
                            out[(category, action)] = keys_in(stmt.body)
                        visit(stmt.orelse)
                        if action is None:
                            visit(stmt.body)

            visit(method.body)
    return out


# ------------------------------------------------------------------ pascal

def _pascal_bodies() -> dict[str, dict[str, str]]:
    bodies: dict[str, dict[str, str]] = {}
    for path in sorted(PASCAL_DIR.glob("*.pas")):
        if path.name == "Altium_MCP.pas":     # generated bundle
            continue
        text = path.read_text(encoding="latin-1")
        starts = [(m.start(), m.group(1)) for m in
                  re.finditer(r"^(?:Function|Procedure)\s+(\w+)", text,
                              re.M)]
        table: dict[str, str] = {}
        for i, (pos, fname) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            table[fname] = text[pos:end]
        bodies[path.name] = table
    return bodies


def _routing(bodies):
    """category -> module, and (module, action) -> handler function."""
    # Located by content: the routing moved from Dispatcher.pas into
    # StatusForm.pas when the poll loop became a timer on the dashboard,
    # and a filename would pin this to where it happened to be.
    routes: dict[str, str] = {}
    for path in sorted(PASCAL_DIR.glob("*.pas")):
        if path.name == "Altium_MCP.pas":     # generated bundle
            continue
        found = dict(re.findall(
            r"'(\w+)':\s*Result\s*:=\s*(Handle\w+Command)",
            path.read_text(encoding="latin-1")))
        if found:
            routes = found
            break
    assert routes, "could not parse the dispatcher's category routing"
    module_of = {}
    for cat, fn in routes.items():
        for mod, table in bodies.items():
            if fn in table:
                module_of[cat] = mod
    handler = {}
    for mod, table in bodies.items():
        for body in table.values():
            for action, hfn in re.findall(
                    r"'([a-z_0-9]+)'\s*:\s*Result\s*:=\s*(\w+)\(", body):
                handler[(mod, action)] = hfn
    return module_of, handler


#: The envelope wrappers add their own keys; following INTO them would
#: credit every handler with the envelope, hiding nothing but proving
#: less.
_SKIP_CALLEES = {"BuildSuccessResponse", "BuildErrorResponse",
                 "EscapeJsonString", "ExtractJsonValue"}


def _emitted_keys(bodies, module, fn, depth=2, seen=None) -> set[str]:
    seen = set() if seen is None else seen
    if fn in seen or depth < 0:
        return set()
    seen.add(fn)
    body = bodies.get(module, {}).get(fn)
    if body is None:
        for other, table in bodies.items():
            if fn in table:
                body, module = table[fn], other
                break
    if body is None:
        return set()
    keys = set(re.findall(r'"(\w+)":', body))
    keys |= set(re.findall(r"Json\w+\('(\w+)'", body))
    for callee in set(re.findall(r"\b([A-Z]\w+)\(", body)):
        if callee != fn and callee not in _SKIP_CALLEES:
            keys |= _emitted_keys(bodies, module, callee, depth - 1, seen)
    return keys


def _compare(depth: int = 2):
    """(findings, comparable count) at a given helper-following depth."""
    sim = _simulator_keys()
    bodies = _pascal_bodies()
    module_of, handler = _routing(bodies)
    findings: list[tuple[str, str, list[str]]] = []
    comparable = 0
    for (cat, action), skeys in sorted(sim.items()):
        mod = module_of.get(cat)
        hfn = handler.get((mod, action)) if mod else None
        if hfn is None:
            continue        # inlined or simulator-only action: skipped
        comparable += 1
        excess = skeys - _emitted_keys(bodies, mod, hfn, depth=depth)
        excess -= set(TOLERATED.get((cat, action), {}))
        if excess:
            findings.append((cat, action, sorted(excess)))
    return findings, comparable


@pytest.fixture(scope="module")
def comparison():
    return _compare(depth=2)


def test_the_simulator_promises_no_key_the_pascal_never_emits(comparison):
    findings, _ = comparison
    assert not findings, (
        "the simulator answers with keys the Pascal handler never "
        "emits, so a test asserting them passes here and fails live: "
        + "; ".join(f"{c}.{a}: {k}" for c, a, k in findings))


def test_the_comparison_actually_covered_the_surface(comparison):
    _, comparable = comparison
    assert comparable >= 60, (
        f"only {comparable} actions were comparable; the routing or "
        "extraction broke and this file is guarding a remnant")


def test_helper_following_is_doing_something():
    """Depth-2 must credit handlers with MORE emitted keys than
    depth-0, or the traversal is decoration (the observable-outcome
    discipline from test_request_keys_are_read.py, with the observable
    that actually moves here: with the current simulator both depths
    happen to flag zero actions, so finding counts cannot distinguish
    a working traversal from a dead one, but the KEY SETS can; today 7
    handlers gain 56 keys through their helpers)."""
    sim = _simulator_keys()
    bodies = _pascal_bodies()
    module_of, handler = _routing(bodies)
    changed = shallow_total = deep_total = 0
    for (cat, action) in sorted(sim):
        mod = module_of.get(cat)
        hfn = handler.get((mod, action)) if mod else None
        if hfn is None:
            continue
        shallow = _emitted_keys(bodies, mod, hfn, depth=0)
        deep = _emitted_keys(bodies, mod, hfn, depth=2)
        shallow_total += len(shallow)
        deep_total += len(deep)
        if shallow != deep:
            changed += 1
    assert deep_total > shallow_total and changed > 0, (
        f"helper following changed {changed} key sets "
        f"({shallow_total} keys shallow, {deep_total} deep); the "
        "traversal resolves nothing and a helper-emitted key would be "
        "flagged as simulator excess")


def test_the_tolerated_list_does_not_go_stale():
    """Every tolerated entry must still name a real divergence; an
    entry the comparison no longer needs is deleted, not kept."""
    findings, _ = _compare(depth=2)
    flagged = {(c, a) for c, a, _ in findings}
    raw_findings, _ = _compare(depth=2)
    del raw_findings
    for key, keys in TOLERATED.items():
        sim = _simulator_keys().get(key, set())
        assert set(keys) <= sim, (
            f"TOLERATED entry {key} excuses keys {sorted(keys)} the "
            "simulator no longer emits; delete the entry")
    assert not flagged & set(TOLERATED), (
        "an action is both flagged and tolerated; the tolerated keys "
        "list is incomplete for it")
