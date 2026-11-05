# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Hand-written PROSE names tools that exist, not just the catalogs.

``test_readme_names_real_tools.py`` checks the README's catalog
sections. The prose was unguarded, and prose is where a tool gets
described, renamed and then quietly left behind: a reader is told to
call something that no longer exists and finds out at the tool call.
The gap was found by mutating a tool name in a paragraph and watching
every existing guard pass.

Measured before being written, because a guard nobody trusts is worse
than none: of 360 backticked tool-shaped tokens in the README, 356 are
registered tools and 4 are parameters or reply fields. That ratio is
what makes this worth having; the response-key guard measured earlier
in this project was declined at 68 percent false positives.

Covers the hand-written docs too, on the same reasoning. TOOL_REFERENCE
is excluded because it is generated, and has its own freshness guard.

Only tokens starting with a real tool-family prefix are considered, so
``save_to``, ``verified_live`` and ``violation_count`` are never
candidates, and ``pcb.components`` is excluded by carrying a dot.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"


def _prose_files() -> list[Path]:
    files = [_README, _ROOT / "CONTRIBUTING.md"]
    files += [p for p in sorted((_ROOT / "docs").glob("*.md"))
              # Generated from the tool surface, and guarded for
              # freshness by tests/test_gen_tool_reference.py.
              if p.name != "TOOL_REFERENCE.md"]
    return [p for p in files if p.exists()]

#: A backticked token has to start with one of these to be treated as
#: a claim about a tool. Anything else is a parameter, a field or a
#: word.
_FAMILIES = ("easyeda_", "lib_", "pcb_", "sch_", "proj_", "obj_", "app_",
             "audit_", "design_", "kicad_", "route_", "sim_", "part_")

#: Tool-SHAPED tokens that are deliberately not tools. Each is named
#: with what it actually is, so the list cannot quietly absorb a real
#: mistake.
NOT_TOOLS: dict[str, str] = {
    "kicad_local": "a part provider id, listed in the provider table",
    "part_count": "a parameter of lib_create_symbol",
    "part_id": "a parameter of sch_place_dblib_component",
    "part_id_applied": "a reply field of sch_place_dblib_component",
    "pcb_only": "a reply field of the schematic/PCB comparison",
    "sch_only": "a reply field of the schematic/PCB comparison",
    # The namespace PREFIXES, which the readme names when explaining
    # that a tool's prefix tells you which document it acts on. They
    # end in an underscore precisely because they are not whole names.
    "lib_": "the library namespace prefix, not a tool",
    "pcb_": "the board namespace prefix, not a tool",
    "sch_": "the schematic namespace prefix, not a tool",
    "obj_": "the generic object namespace prefix, not a tool",
}


def _stage_names() -> set[str]:
    """Design-flow STAGE names, which look exactly like tool names.

    ``sch_to_pcb`` was excused here by hand, and the same confusion
    then annotated a whole loop step as unavailable in the autonomy
    guide because a stage name matched the tool pattern. Twice is a
    rule, not a coincidence: read the canonical list rather than
    naming one member, so a stage added later is excused
    automatically instead of failing this test for the wrong reason.
    """
    from eda_agent.design.session import STAGES

    return set(STAGES)


def _registered() -> set[str]:
    from eda_agent.tools import register_backend

    names: set[str] = set()
    for backend in ("altium", "kicad", "easyeda"):
        captured: dict = {}

        class _Mcp:
            def tool(self, *a, **k):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn
                return deco

        server = _Mcp()
        register_backend(server, backend)
        names |= set(captured)
        # Reached through a family tool or a bulk tool; the reference
        # lists each with the tool that runs it.
        names |= set(getattr(server, "hidden", {}) or {})
    return names


def _candidates(path: Path) -> set[str]:
    tokens = set(re.findall(r"`([a-z][a-z0-9_]+)`",
                            path.read_text(encoding="utf-8",
                                           errors="replace")))
    return {t for t in tokens if t.startswith(_FAMILIES)}


def _all_candidates() -> set[str]:
    found: set[str] = set()
    for path in _prose_files():
        found |= _candidates(path)
    return found


def test_the_scan_finds_enough_to_be_worth_running():
    """A floor, not a target.

    The threshold was 200 while the README carried a hand-written copy
    of the tool reference, which was most of the tokens on its own. That
    section was deleted rather than moved, because it duplicated a
    GENERATED file and had already drifted from it, so the honest count
    across the prose is now around 110.

    Lowered to match, and no lower: the number still has to be large
    enough that a scan returning a handful, or nothing, fails here
    rather than passing as a clean result.
    """
    found = _all_candidates()
    assert len(found) > 80, (
        f"only {len(found)} tool-shaped tokens found across the prose; "
        f"the scan broke and this file is guarding a remnant")


@pytest.mark.parametrize(
    "path", _prose_files(), ids=lambda p: p.name)
def test_every_tool_the_prose_names_exists(path):
    unknown = sorted(_candidates(path) - _registered()
                     - set(NOT_TOOLS) - _stage_names())
    assert not unknown, (
        f"{path.name} names {unknown}, which no backend registers. A "
        f"reader following that finds out at the tool call. Fix the "
        f"name, or add it to NOT_TOOLS saying what it really is.")


def test_the_exception_list_does_not_go_stale():
    """An entry for something that became a real tool, or that the
    prose no longer mentions, hides the next real mistake."""
    real = _registered()
    mentioned = _all_candidates()
    for name, reason in NOT_TOOLS.items():
        assert name not in real, (
            f"NOT_TOOLS excuses {name!r} as {reason!r}, but it is now a "
            f"registered tool. Delete the entry.")
        assert name in mentioned, (
            f"NOT_TOOLS excuses {name!r}, which the prose no longer "
            f"mentions. Delete the entry.")
