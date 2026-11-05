# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every tool the README's catalog names must actually be registered.

``tests/test_readme_tool_counts.py`` checks that each ``### Name (N
tools)`` header matches the number of ``@mcp.tool`` decorators in that
section's source files. That is a COUNT, and a count cannot see a wrong
NAME: the table can advertise a tool nobody implemented and still add up.

It did. ``pcb_fillet_corners`` sat in the track-operations row with a
description of what it does, and no such tool has ever existed. The
Pascal handler ``PCB_FilletCorners`` is real and dispatched, but exposing
it was deliberately declined, and
``tests/test_bridge_handlers_reachable.py`` records why: the handler's
own header says it has not been validated against a live Altium session,
so a first-class tool would present unvalidated code as ready. Both
landed in commit 8853d93; only the README got the tool it described.

The cost of the gap is specific. The README catalog is what a user or an
agent reads to decide what to call, so an entry there is a promise. A
promise for a tool that does not exist produces a "no such tool" error
at the least convenient moment, and the reader cannot tell whether they
typed it wrong or the docs are stale.

Scoped to the FIRST COLUMN of catalog rows, which is the tool-name
column. The description column is prose and legitimately mentions
parameters (``part_count``, ``sch_only``) and net-class names that look
tool-shaped but are not, so checking every backticked token there would
be noise.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import re

import pytest

from tests import documentation_set

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"

#: A catalog row starts with a pipe, then the tool-name cell.
_ROW = re.compile(r"^\|\s*(`[^|]+`)\s*\|")
_NAME = re.compile(r"`([a-z][a-z0-9_]*)`")

#: Names in the first column that are deliberately not tools.
_NOT_TOOLS: set[str] = set()


def _registered_tools() -> set[str]:
    captured: set[str] = set()

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured.add(fn.__name__)
                return fn
            return deco

        def prompt(self, *a, **k):
            return lambda fn: fn

        def resource(self, *a, **k):
            return lambda fn: fn

    tools_dir = _ROOT / "src" / "eda_agent" / "tools"
    for path in sorted(tools_dir.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        dotted = (path.relative_to(tools_dir).with_suffix("")
                  .as_posix().replace("/", "."))
        module = importlib.import_module(f"eda_agent.tools.{dotted}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("register_") and fn.__module__ == module.__name__:
                try:
                    fn(_Mcp())
                except Exception:  # noqa: BLE001 - a backend may decline
                    pass
    # The family tools are made at registration from their members.
    from eda_agent.tools.folds import FOLDS
    return captured | set(FOLDS)


#: A catalog section, the same anchor test_readme_tool_counts.py uses.
#: Two heading forms, because the catalog moved. The README wrote
#: "### Library (67 tools)" by hand; the generated reference writes
#: "## library (67)". Matching only the first silently scoped the scan
#: to nothing once the hand-written copy was deleted, and the guard
#: below is what caught that.
_SECTION = re.compile(r"^#{2,3} [A-Za-z][A-Za-z\s]*? \(\d+( tools)?\)\s*$")
_ANY_HEADING = re.compile(r"^#{1,4}\s")


def _catalog_names() -> list[str]:
    """Tool names from the first column of catalog rows only.

    Scoped to sections whose header carries a ``(N tools)`` count,
    because other tables in the README have a name-shaped first column
    too: the part-provider table lists ``easyeda``, ``kicad_local`` and
    ``partreel``, which are providers rather than tools. The existing
    count guard scopes the same way for the same reason.
    """
    names: list[str] = []
    in_catalog = False
    for line in documentation_set.all_text().splitlines():
        if _ANY_HEADING.match(line):
            in_catalog = bool(_SECTION.match(line))
            continue
        if not in_catalog:
            continue
        match = _ROW.match(line)
        if match:
            names.extend(_NAME.findall(match.group(1)))
    return names


def test_the_catalog_rows_were_found():
    """Guard the guard: a table reformat must not silently check zero."""
    names = _catalog_names()
    assert len(names) > 150, (
        f"only {len(names)} tool names parsed out of the catalog; "
        "the table format changed and this test is no longer reading it")


def test_every_named_tool_is_registered():
    registered = _registered_tools()
    assert len(registered) > 400, (
        f"only {len(registered)} tools registered; the registration walk "
        "is broken and this test would pass by finding nothing to check")

    missing = sorted({n for n in _catalog_names()
                      if n not in registered and n not in _NOT_TOOLS})
    assert not missing, (
        f"the README catalog names {len(missing)} tool(s) that are not "
        f"registered: {missing}. Either the tool was removed and the row "
        "is stale, or it was never written and the row is a promise the "
        "code does not keep."
    )


def test_a_registered_tool_is_recognised():
    """The comparison works in the direction that matters."""
    registered = _registered_tools()
    assert "pcb_place_tracks" in registered
    assert "pcb_fillet_corners" not in registered, (
        "exposing this was declined on purpose, see "
        "tests/test_bridge_handlers_reachable.py; if that changed, this "
        "assertion should be updated deliberately rather than deleted"
    )


def test_the_readme_documents_every_backend_the_code_offers():
    """The backend list is stated in the README and in BACKENDS.

    Nothing connects them. A backend added to the code but missing from
    the docs is invisible to anyone deciding whether this project drives
    their EDA tool, which is the one question the README exists to
    answer.
    """
    from eda_agent.tools import BACKENDS

    docs = documentation_set.prose_text()

    for backend in BACKENDS:
        assert f"`{backend}`" in docs, (
            f"backend {backend!r} is selectable but no documentation file "
            f"names it")


def test_the_readme_names_the_easyeda_environment_variables():
    """A variable nobody can find is a feature nobody can enable."""
    import pathlib
    import re

    from eda_agent.bridge import easyeda_bridge

    docs = documentation_set.prose_text()

    source = pathlib.Path(easyeda_bridge.__file__).read_text(encoding="utf-8")
    variables = set(re.findall(r'environ\.get\(\s*"(EDA_AGENT_\w+)"', source))
    assert variables, "no environment variables parsed; the pattern drifted"

    for name in variables:
        # Word boundary, not substring: documenting EDA_AGENT_EASYEDA_PORTS
        # would satisfy a plain `in` check while leaving the real
        # variable unset.
        assert re.search(rf"\b{re.escape(name)}\b", docs), (
            f"{name} is read by the bridge but named in no documentation "
            f"file")
