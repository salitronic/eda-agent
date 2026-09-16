# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A parameter Python sends must be one the Pascal handler reads.

This is the quietest way a bridge call fails. The command dispatches,
the handler runs, the response says ok, and the parameter is simply
never read, so the caller gets a successful-looking result for work
that did not happen.

It has already shipped. ``force_recompile`` was sent as
``proj_force_recompile`` at three call sites while Project.pas read
``force_recompile``, so every caller asking for fresh connectivity
silently got the cached compile. The tests covering it asserted the
PYTHON spelling rather than the contract, so they passed throughout.
No test compared the two sides until this one.

Two things this has to get right, both learned from real bugs:

* Resolve the handler by FULL NAMESPACE. Four action names are
  dispatched in two modules each with different parameter names on each
  side, so picking "the handler called get_components" can compare
  against the wrong one and invent a finding or miss a real one.
* FOLLOW HELPER CALLS. Handlers routinely pass ``Params`` down to a
  helper that does the reading. Stopping at the handler body reports
  every one of those keys as unread.

The mirror direction, a RESPONSE key Python reads that the handler
never writes, was measured and deliberately not turned into a guard.
Tracking only the variable bound to the send result, in Load context,
it found 6 candidates across 40 commands and every one was a
deliberate fallback chain (``raw.get("violations", raw.get("items",
[]))``, ``result.get("results") or result.get("components")``). That
tolerance is idiomatic here and is not distinguishable from a mistake
without modelling the chains, so the check would produce noise
and require a standing allowlist. Revisit if a real response-key
mismatch ever ships.

Covers 224 of the 228 commands whose keys are readable statically,
against 588 distinct keys. Bound stated so a pass is not over-read:
keys assembled at runtime are invisible on the Python side, only
``ExtractJsonValue`` / ``GetBatchField`` reads are visible on the
Pascal side, and only handlers dispatched as ``'action': Result := Fn(``
resolve to a body, so a case arm that inlines its work is skipped. A
finding is therefore always worth reading; a pass does not prove every
key is read.
"""

from __future__ import annotations

import ast
import collections
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PASCAL_DIR = REPO / "scripts" / "altium"
PY_DIR = REPO / "src" / "eda_agent"

#: Keys the handler deliberately does not read, with the reason. An
#: entry here is a decision, not an exemption to be added casually.
INTENTIONALLY_UNREAD: dict[tuple[str, str], str] = {
    ("library.link_3d_model", "rotation_x"):
        "Accepted and deliberately not applied: IPCB_ComponentBody "
        "exposes a planar Rotation only, so there is no X tilt to set. "
        "The reply reports which assignments the board accepted rather "
        "than claiming success, and the parameter stays in the schema "
        "so a caller is not silently rejected.",
    ("library.link_3d_model", "rotation_y"):
        "Same as rotation_x: no Y tilt property exists on the body.",
}

_CASE_FN = re.compile(r"'([a-z_0-9]+)'\s*:\s*Result\s*:=\s*(\w+)\(")
_ROUTE = re.compile(r"'(\w+)':\s*Result\s*:=\s*(Handle\w+Command)")
_READS_KEY = re.compile(
    r"(?:ExtractJsonValue|GetBatchField)\s*\(\s*[\w.\[\]]+\s*,\s*'([\w.]+)'")
_CALLS = re.compile(r"\b([A-Z]\w+)\s*\(")


def _pascal_sources() -> dict[str, str]:
    # Altium_MCP.pas is the generated bundle; including it would make
    # every handler look like it lives in two places.
    return {p.name: p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(PASCAL_DIR.glob("*.pas"))
            if p.name != "Altium_MCP.pas"}


def _function_bodies(text: str) -> dict[str, str]:
    """Each Function/Procedure body, from its header to the next one."""
    out: dict[str, str] = {}
    starts = [(m.start(), m.group(1)) for m in
              re.finditer(r"^(?:Function|Procedure)\s+(\w+)", text, re.M)]
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        out[name] = text[pos:end]
    return out


class _Pascal:
    def __init__(self) -> None:
        self.sources = _pascal_sources()
        self.bodies = {m: _function_bodies(t) for m, t in self.sources.items()}

        # Found by content, not by filename. The routing lived in
        # Dispatcher.pas until the polling loop became a timer on the
        # dashboard, which forced ProcessCommand into StatusForm.pas: a
        # form's event handler resolves only inside the form's own unit.
        # Nothing this class models changed, so a filename is the wrong
        # thing to pin it to.
        routes: dict[str, str] = {}
        for _text in [t for _, t in sorted(self.sources.items())]:
            found = dict(_ROUTE.findall(_text))
            if found:
                routes = found
                break
        assert routes, "could not parse the dispatcher's category routing"
        module_of = {fn: m for fn in set(routes.values())
                     for m, t in self.sources.items()
                     if re.search(r"^Function\s+" + fn + r"\b", t, re.M)}
        self.category_module = {c: module_of[f] for c, f in routes.items()
                                if f in module_of}

        self.handler: dict[tuple[str, str], str] = {}
        for module, text in self.sources.items():
            for action, fn in _CASE_FN.findall(text):
                self.handler[(module, action)] = fn

    def keys_read(self, module: str, fn: str, depth: int = 2,
                  seen: set[str] | None = None) -> set[str]:
        """Keys read by ``fn``, following helper calls ``depth`` deep."""
        seen = set() if seen is None else seen
        if fn in seen or depth < 0:
            return set()
        seen.add(fn)
        body = self.bodies.get(module, {}).get(fn)
        if body is None:
            for other, table in self.bodies.items():   # helper elsewhere
                if fn in table:
                    body, module = table[fn], other
                    break
        if body is None:
            return set()
        keys = set(_READS_KEY.findall(body))
        for callee in set(_CALLS.findall(body)):
            if callee != fn:
                keys |= self.keys_read(module, callee, depth - 1, seen)
        return keys


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodes belonging to ``scope`` itself, not to functions inside it.

    Essential, not tidiness. Every tool is a nested function inside a
    ``register_*_tools`` factory, so walking the enclosing scope would
    pool the ``params`` dicts of all ~60 tools in a module and credit
    every one of their keys to every send in the file. That does not
    fail loudly; it makes the audit claim each command sends every key
    the module ever mentions.
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _send_calls(nodes):
    """``(command, params_node, lineno)`` for each send in ``nodes``."""
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.attr if isinstance(fn, ast.Attribute)
                else getattr(fn, "id", ""))
        if name not in ("send_command", "send_command_async"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        cmd = node.args[0].value
        if not isinstance(cmd, str) or "." not in cmd:
            continue
        params = node.args[1] if len(node.args) > 1 else None
        yield cmd, params, node.lineno


def _keys_sent() -> tuple[dict[str, set[str]], dict[tuple[str, str], str]]:
    """Wire keys each command sends, from BOTH ways they are written.

    A dict literal at the call site is the obvious one. The other is a
    ``params`` dict built up first and passed by name:

        params = {"project_path": path}
        if force_recompile:
            params["force_recompile"] = "true"
        bridge.send_command("project.get_nets", params)

    That second form is not a stylistic variant to be tidied away, it
    is how OPTIONAL parameters are written throughout this codebase,
    and it is the exact shape of the force_recompile bug. Reading only
    dict literals would leave every optional parameter unchecked, which
    is the half most likely to be wrong: a required key fails loudly
    the first time anyone calls the tool, an optional one just quietly
    does nothing.
    """
    sent: dict[str, set[str]] = collections.defaultdict(set)
    where: dict[tuple[str, str], str] = {}

    def record(cmd, key, path, lineno):
        sent[cmd].add(key)
        where.setdefault((cmd, key), f"{path.name}:{lineno}")

    for path in PY_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        # A params dict is matched to the send in its OWN function, so
        # keys never leak between the sibling tools of a module.
        scopes = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.Module))]
        for scope in scopes:
            own = _own_nodes(scope)
            sends = list(_send_calls(own))
            if not sends:
                continue
            # name -> keys assigned into it within this same function
            assigned: dict[str, set[str]] = collections.defaultdict(set)
            for node in own:
                target = None
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    target = node.targets[0]
                elif isinstance(node, ast.AnnAssign):
                    target = node.target
                if isinstance(target, ast.Subscript) and isinstance(
                        target.value, ast.Name):
                    idx = target.slice
                    if isinstance(idx, ast.Constant) and isinstance(
                            idx.value, str):
                        assigned[target.value.id].add(idx.value)
                # dicts built as literals then passed by name
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Dict)):
                    for key in node.value.keys:
                        if isinstance(key, ast.Constant) and isinstance(
                                key.value, str):
                            assigned[node.targets[0].id].add(key.value)
                # params.update({...})
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "update"
                        and isinstance(node.func.value, ast.Name)
                        and node.args
                        and isinstance(node.args[0], ast.Dict)):
                    for key in node.args[0].keys:
                        if isinstance(key, ast.Constant) and isinstance(
                                key.value, str):
                            assigned[node.func.value.id].add(key.value)

            for cmd, params, lineno in sends:
                if isinstance(params, ast.Dict):
                    for key in params.keys:
                        if isinstance(key, ast.Constant) and isinstance(
                                key.value, str):
                            record(cmd, key.value, path, lineno)
                elif isinstance(params, ast.Name):
                    for key in assigned.get(params.id, ()):
                        record(cmd, key, path, lineno)
    return sent, where


def _run_audit(depth: int = 2):
    """(unread findings, commands checked) at a given helper depth.

    ``depth`` is a parameter so a test can compare the result against a
    shallower run. Following helper calls is the difference between a
    guard and a noise generator, and nothing else here would notice if
    it silently stopped working.
    """
    pas = _Pascal()
    sent, where = _keys_sent()
    unread, checked = [], 0
    for cmd, keys in sorted(sent.items()):
        category, action = cmd.split(".", 1)
        module = pas.category_module.get(category)
        if not module:
            continue
        fn = pas.handler.get((module, action))
        if not fn:
            continue
        read = pas.keys_read(module, fn, depth=depth)
        if not read:
            # The handler reads nothing this can see: either it takes no
            # parameters, or it reads them somewhere the scan misses.
            # Skipping is right for the first and unavoidable for the
            # second, so the count is asserted separately rather than
            # letting a resolver failure hide here.
            continue
        checked += 1
        for key in sorted(keys - read):
            unread.append((cmd, key, where[(cmd, key)], f"{module}:{fn}"))
    return unread, checked


@pytest.fixture(scope="module")
def audit():
    return _run_audit(depth=2)


def test_every_parameter_sent_is_read_by_its_handler(audit):
    unread, _checked = audit
    new = [u for u in unread if (u[0], u[1]) not in INTENTIONALLY_UNREAD]
    assert not new, (
        "these parameters are sent but the handler never reads them, so "
        "the call reports success for work it did not do:\n  "
        + "\n  ".join(f"{cmd} sends '{key}' ({site}), handler {h}"
                      for cmd, key, site, h in new)
        + "\nFix the spelling on whichever side is wrong, or add an "
          "entry to INTENTIONALLY_UNREAD with the reason.")


def test_the_audit_actually_inspected_the_bridge(audit):
    """A resolver that matched nothing would pass while checking nothing."""
    _unread, checked = audit
    assert checked > 190, (
        f"only {checked} commands resolved to a handler with readable "
        f"keys (224 today); the dispatch scan, the body split, the key "
        f"regex or the params-dict tracking has stopped matching and "
        f"this guard has gone blind")


def test_the_intentional_list_does_not_go_stale(audit):
    """An entry that no longer applies hides the next real finding."""
    unread, _checked = audit
    live = {(cmd, key) for cmd, key, _site, _h in unread}
    stale = sorted(k for k in INTENTIONALLY_UNREAD if k not in live)
    assert not stale, (
        f"these are listed as intentionally unread but the handler now "
        f"reads them (or no longer receives them): {stale}")


def test_helper_calls_are_followed(audit):
    """Not following them turns the guard into a noise generator.

    Asserted against the audit's OWN output rather than against the
    resolver in isolation, so disabling the following anywhere in the
    pipeline fails here. Handlers routinely pass ``Params`` to a helper
    that does the reading; stopping at the handler body reports every
    one of those keys as unread.
    """
    deep_unread, deep_checked = audit
    shallow_unread, _shallow_checked = _run_audit(depth=0)
    assert len(shallow_unread) > len(deep_unread), (
        f"following helper calls changed nothing ({len(shallow_unread)} "
        f"findings shallow vs {len(deep_unread)} deep), so either the "
        f"call following is broken or no handler delegates any more")
    assert deep_checked > 190


def test_the_namespace_resolution_picks_the_right_module():
    """The four ambiguous action names must resolve per category."""
    pas = _Pascal()
    assert pas.category_module["pcb"] == "PCB.pas"
    assert pas.category_module["library"] == "Library.pas"
    assert pas.category_module["project"] == "Project.pas"
    assert pas.category_module["application"] == "Application.pas"
    # get_components exists in both Library.pas and PCB.pas; each
    # category must find its own.
    assert ("Library.pas", "get_components") in pas.handler
    assert ("PCB.pas", "get_components") in pas.handler
    assert (pas.handler[("Library.pas", "get_components")]
            != pas.handler[("PCB.pas", "get_components")])
