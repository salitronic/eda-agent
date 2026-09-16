# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every Pascal handler should be callable from Python, or listed here.

A handler that nothing dispatches to is dead weight: it was written,
reviewed and deployed, and no tool can reach it. There is no raw
command passthrough (``obj_run_process`` runs Altium PROCESSES, not
bridge commands), and ``tool_invoke`` only reaches registered Python
tools, so an unreachable handler is unreachable full stop.

The point of the allowlist is that it should SHRINK. A new entry means
somebody wrote Pascal with no way to call it.

Most current entries are singular forms whose bulk equivalent is the one
actually exposed, matching the standing preference for batch tools over
per-item loops (``move_component`` is superseded by
``pcb.batch_move_components``, and so on). Two are not superseded by
anything and are genuinely unexposed:

* ``fillet_corners`` -- rounds acute track joins. Its own header says it
  has NOT been validated against a live Altium session, so exposing it
  would present unvalidated code as a first-class tool.
* ``measure_distance`` / ``place_compile_mask`` -- no equivalent found.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PASCAL_DIR = ROOT / "scripts" / "altium"
PY_DIR = ROOT / "src" / "eda_agent"

#: Modules whose command literals belong to a DIFFERENT bridge. This
#: scan asks which Pascal handlers Python calls, and it accepts any
#: dotted literal by its tail, so an EasyEDA command that happens to end
#: in the same word makes an Altium handler look reachable. That is how
#: "pcb.place_component", sent to EasyEDA, marked Altium's Pascal
#: place_component as called by something. Named by path rather than by
#: the word "easyeda" appearing in a filename, because lib_easyeda_import
#: is an Altium tool and its literals do count.
_OTHER_BRIDGE_MODULES = {
    ("tools", "easyeda.py"),
    ("bridge", "easyeda_bridge.py"),
}


def _is_other_bridge(path: Path) -> bool:
    return (path.parent.name, path.name) in _OTHER_BRIDGE_MODULES


#: Handlers with no Python sender today. Shrink this; do not grow it
#: without recording why the handler cannot be reached.
KNOWN_UNREACHABLE = {
    # Singular forms; the bulk sibling is what the tools call.
    "add_pin",
    "add_symbol_line",
    "move_component",
    "place_component",
    "place_track",
    "place_wire",
    "place_sch_component_from_library",
    "set_designator",
    "set_label_format",
    "set_sch_component_parameters",
    "attach_spice_primitive",
    "zoom_to_xy",
    # Genuinely unexposed.
    "fillet_corners",
    "measure_distance",
    "place_compile_mask",
    # Deliberately unreachable from Python: the handler refuses, and
    # pcb_set_via_soldermask_relief refuses locally rather than send to
    # it. Reaching it means putting the command on the wire, and a
    # session running a deployed script older than 2026.09.10.3 still
    # has the write that takes the scripting engine down. The handler
    # stays as the second layer, for tool_invoke and raw commands.
    # See tests/test_via_soldermask_relief_is_refused.py.
    "set_via_soldermask_relief",
}

#: The modules do NOT all dispatch the same way, and matching only one
#: style silently skips whole files. Audit.pas uses an if/else-if chain
#: on ``Action``, and some cases inline their body instead of calling a
#: function. Matching only ``'x': Result := Fn(`` missed all 29 audit
#: handlers and reported them as absent.
_DISPATCH_PATTERNS = (
    re.compile(r"'([a-z_0-9]+)'\s*:\s*Result\s*:=\s*(\w+)\("),  # case -> fn
    re.compile(r"'([a-z_0-9]+)'\s*:\s*(Begin)"),                  # inline case
    re.compile(r"Action\s*=\s*'([a-z_0-9]+)'()"),                 # else-if chain
)


def _dispatched_handlers() -> dict[str, tuple[str, str]]:
    """command -> (module, Pascal function or marker) for every style."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(PASCAL_DIR.glob("*.pas")):
        # Altium_MCP.pas is the built bundle, not a source module; it
        # would double-count every handler.
        if path.name == "Altium_MCP.pas":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _DISPATCH_PATTERNS:
            for m in pattern.finditer(text):
                found.setdefault(
                    m.group(1), (path.name, m.group(2) or "inline"))
    return found


def _sent_command_tails() -> set[str]:
    """Command names Python actually passes to send_command[_async].

    Compared by TAIL ("pcb.place_via" -> "place_via") so this needs no
    hardcoded list of module prefixes. An earlier version guessed the
    prefixes and omitted "audit", which reported every reachable audit
    handler as dead.
    """
    tails: set[str] = set()
    for path in PY_DIR.rglob("*.py"):
        if _is_other_bridge(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", ""))
            if name not in ("send_command", "send_command_async"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                cmd = first.value
                tails.add(cmd.split(".", 1)[1] if "." in cmd else cmd)
    return tails


def _python_string_constants() -> set[str]:
    """Every string literal in the Python source.

    Deliberately AST-based. Regexing for the send site does not work:
    parameters and command names reach the bridge through dict literals,
    subscript assignment, tuple tables and f-string payloads, and a
    pattern that misses one idiom reports reachable handlers as dead.
    """
    out: set[str] = set()
    for path in PY_DIR.rglob("*.py"):
        if _is_other_bridge(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    return out


@pytest.fixture(scope="module")
def unreachable() -> dict[str, tuple[str, str]]:
    handlers = _dispatched_handlers()
    # 331 today. A big drop means a dispatch style stopped matching,
    # which would make this whole guard pass vacuously.
    assert len(handlers) > 320, (
        f"dispatch scan found only {len(handlers)} handlers; a "
        f"dispatch style probably stopped matching")
    tails = _sent_command_tails()
    assert len(tails) > 250, "send_command scan found too little to be real"
    # A string constant also counts. Not every call goes through
    # send_command directly -- the dashboard wraps it in _bridge_call --
    # so accept any literal that IS the command or ends in ".<command>".
    # Matching the suffix rather than a hardcoded prefix list is what
    # keeps this from breaking each time a new module prefix appears.
    strings = _python_string_constants()
    suffixed = {s.rsplit(".", 1)[1] for s in strings if "." in s}
    reachable = tails | strings | suffixed
    return {cmd: where for cmd, where in handlers.items()
            if cmd not in reachable}


def test_no_new_unreachable_handler(unreachable):
    new = {c: w for c, w in unreachable.items() if c not in KNOWN_UNREACHABLE}
    assert not new, (
        "Pascal handlers with no Python caller:\n  "
        + "\n  ".join(f"{c} ({w[0]}:{w[1]})" for c, w in sorted(new.items()))
        + "\nEither expose one, or add it to KNOWN_UNREACHABLE with a reason.")


def test_allowlist_does_not_go_stale(unreachable):
    """An entry that became reachable must leave the list.

    Otherwise the allowlist silently grants permission for handlers that
    no longer need it, and stops describing reality.
    """
    stale = KNOWN_UNREACHABLE - set(unreachable)
    assert not stale, (
        f"{sorted(stale)} are reachable now; drop them from "
        f"KNOWN_UNREACHABLE.")


def test_scan_finds_the_bundle_is_excluded():
    """The built bundle must not be scanned as a source module.

    Altium_MCP.pas concatenates the modules, so counting it would double
    every handler and mask a real gap behind a duplicate.
    """
    assert (PASCAL_DIR / "Altium_MCP.pas").exists()
    assert "Altium_MCP.pas" not in {
        m for m, _ in _dispatched_handlers().values()}


# ---------------------- the other direction --------------------------
#
# Above: a Pascal handler nothing calls, which is dead weight.
# Below: a Python command with NO handler, which is worse. The tool is
# registered, documented and callable; it just fails with UNKNOWN_ACTION
# the first time anyone runs it against live Altium. Nothing offline
# notices, because the simulator answers whatever the test asks it to.

#: Commands sent from Python that no .pas dispatches, with the reason.
#: Empty is the goal. An entry here is a promise the bridge cannot keep.
KNOWN_UNHANDLED: dict[str, str] = {}


# ------------------- the right handler, not just a handler ------------
#
# The check below matches on the ACTION only ("pcb.get_components" ->
# "get_components"), which cannot tell a handler in the right module
# from one in the wrong module. Four action names are dispatched in TWO
# modules each, with different parameter names on each side:
#
#     get_components  Library.pas + PCB.pas
#     get_nets        PCB.pas + Project.pas
#     run_process     Application.pas + Generic.pas
#     save_all        Application.pas + Project.pas
#
# So "there exists a handler somewhere" is not the property that
# matters. ProcessCommand routes on the CATEGORY, and a command whose
# action is implemented only under a different category reaches the
# else-branch and answers UNKNOWN_ACTION, while the action-only check
# stays green because the name was found in some other file.
#
# Both the routing table and the module each handler lives in are read
# from the Pascal, so this cannot drift from the dispatcher it models.


def _category_to_module() -> dict[str, str]:
    """Parsed from ProcessCommand's ``Case Category Of`` block."""
    # Located by content: the routing moved from Dispatcher.pas into
    # StatusForm.pas when the poll loop became a timer on the dashboard,
    # and a filename would pin this to where it happened to be.
    routes: dict[str, str] = {}
    for path in sorted(PASCAL_DIR.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        found = dict(re.findall(
            r"'(\w+)':\s*Result\s*:=\s*(Handle\w+Command)",
            path.read_text(encoding="utf-8", errors="replace")))
        if found:
            routes = found
            break
    assert routes, "could not parse the dispatcher's category routing"

    defined_in: dict[str, str] = {}
    for path in sorted(PASCAL_DIR.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for fn in set(routes.values()):
            if re.search(r"^Function\s+" + fn + r"\b", body, re.M):
                defined_in[fn] = path.name
    return {cat: defined_in[fn] for cat, fn in routes.items()
            if fn in defined_in}


def _modules_dispatching() -> dict[str, set[str]]:
    """action -> every module that dispatches it.

    Distinct from ``_dispatched_handlers``, which keeps only the first
    module per action. That flattening is exactly what hides a
    duplicate, so this one must not reuse it.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(PASCAL_DIR.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _DISPATCH_PATTERNS:
            for m in pattern.finditer(text):
                out.setdefault(m.group(1), set()).add(path.name)
    return out


def test_every_command_routes_to_a_handler_in_its_own_category():
    cat_module = _category_to_module()
    dispatching = _modules_dispatching()

    misrouted = []
    checked = 0
    for cmd, where in _sent_commands().items():
        category, action = cmd.split(".", 1)
        want = cat_module.get(category)
        modules = dispatching.get(action)
        if not want or not modules:
            continue  # unknown category / no handler: other tests own these
        checked += 1
        if want not in modules:
            misrouted.append(
                f"{cmd} ({where}): dispatched in {sorted(modules)}, but "
                f"category '{category}' routes to {want}")

    assert checked > 200, (
        f"only {checked} commands checked against their category; the "
        f"routing parse or the dispatch scan has gone blind")
    assert not misrouted, (
        "these commands reach ProcessCommand's else-branch and answer "
        "UNKNOWN_ACTION, because the handler with that name lives under "
        "a different category:\n  " + "\n  ".join(sorted(misrouted)))


def test_the_duplicate_action_names_are_still_duplicated():
    """The reason the check above exists, pinned so it cannot silently go.

    If one of these stops being ambiguous the action-only check is
    adequate again for it, which is worth noticing rather than
    assuming. If a NEW duplicate appears, this fails and points at it.
    """
    dispatching = _modules_dispatching()
    duplicated = {a: sorted(m) for a, m in dispatching.items() if len(m) > 1}
    assert duplicated == {
        "get_components": ["Library.pas", "PCB.pas"],
        "get_nets": ["PCB.pas", "Project.pas"],
        "run_process": ["Application.pas", "Generic.pas"],
        # The same operation on the two document kinds that have
        # mechanical layers. A library and a board resolve their target
        # differently, so they cannot share a handler, and the caller
        # names the one it means through the category.
        "set_mech_layers": ["Library.pas", "PCB.pas"],
        "save_all": ["Application.pas", "Project.pas"],
    }, (f"the set of action names dispatched in two modules changed: "
        f"{duplicated}")


def _sent_commands() -> dict[str, str]:
    """Every literal ``category.action`` Python sends -> first call site.

    Only literals are visible. A command assembled at runtime sends no
    constant, so it is absent here rather than wrongly reported, which
    is the safe direction for every check built on this.
    """
    sent: dict[str, str] = {}
    for path in PY_DIR.rglob("*.py"):
        if _is_other_bridge(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
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
            sent.setdefault(cmd, f"{path.name}:{node.lineno}")

    assert len(sent) > 200, (
        f"only found {len(sent)} literal send_command targets; the scan "
        f"is not seeing the call sites it thinks it is")
    return sent


@pytest.fixture(scope="module")
def unhandled() -> dict[str, str]:
    handlers = _dispatched_handlers()
    assert len(handlers) > 320, (
        f"dispatch scan found only {len(handlers)} handlers; a dispatch "
        f"style probably stopped matching, which would make this pass "
        f"vacuously")

    sent = _sent_commands()
    return {cmd: where for cmd, where in sent.items()
            if cmd.split(".", 1)[1] not in handlers}


def test_every_command_python_sends_has_a_handler(unhandled):
    """A tool whose command no handler dispatches cannot ever work."""
    new = {c: w for c, w in unhandled.items() if c not in KNOWN_UNHANDLED}
    assert not new, (
        "these commands are sent from Python but no .pas dispatches them, "
        "so the call returns UNKNOWN_ACTION against live Altium:\n"
        + "\n".join(f"  {c}  ({w})" for c, w in sorted(new.items())))


def test_the_unhandled_allowlist_does_not_go_stale(unhandled):
    """An entry that is no longer needed hides the next real one."""
    stale = sorted(set(KNOWN_UNHANDLED) - set(unhandled))
    assert not stale, (
        f"these are now handled and should leave KNOWN_UNHANDLED: {stale}")


def test_every_scanned_source_file_actually_parses():
    """Guard the scans themselves.

    Every collector above walks src/ with ``except SyntaxError:
    continue``, which is reasonable defensively and dangerous silently: a
    file that fails to parse contributes nothing, so its send_command
    calls vanish from the scan and its handlers start looking dead while
    its unhandled commands stop being reported.

    Found by accident. A planted bogus command was NOT caught, and the
    reason was that the plant itself had the wrong indentation, so the
    whole file dropped out of the scan and the guard passed on a smaller
    set. Asserting the skip set is empty is what makes the counts above
    mean what they claim.
    """
    broken = []
    for path in PY_DIR.rglob("*.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            broken.append(f"{path.relative_to(ROOT)}: {exc}")
    assert not broken, (
        "these files do not parse, so every scan in this module silently "
        "skipped them:\n" + "\n".join(broken))
