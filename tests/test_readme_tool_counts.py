# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pin the README's per-section tool counts to the actual @mcp.tool count.

The catalog headers say things like ``### PCB (69 tools)``. When new
tools land, those counts go stale (last iteration: Library was
"22 tools" while pcb.py actually had 31, PCB was "55 tools" while
pcb.py actually had 69). The drift is silent because no one runs grep
against the README during development.

This test reads the actual @mcp.tool decorator count per file via AST
and verifies the README header matches. The mapping is explicit since
some sections cover multiple files (e.g. "Schematic and general"
spans generic.py).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "src" / "eda_agent" / "tools"
README = REPO_ROOT / "README.md"


# README section name -> tool source files that feed it. Order matters
# for the assertion message but not for the count.
SECTION_FILES: dict[str, tuple[str, ...]] = {
    "Application":            ("application.py",),
    "Project":                ("project.py",),
    "Library":                ("library.py",),
    # The README section's table includes the audit_* design-lint checks,
    # so its header count covers both source files.
    "Schematic and general":  ("generic.py", "audit.py"),
    "PCB":                    ("pcb.py",),
    "Design agent":           ("design.py",),
    "Routing":                ("route.py",),
}


def _count_mcp_tools(filename: str) -> int:
    """Count @mcp.tool() decorators on async functions in a file."""
    path = TOOLS_DIR / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if dec.func.attr == "tool":
                        count += 1
                        break
                elif isinstance(dec, ast.Attribute) and dec.attr == "tool":
                    count += 1
                    break
    return count


def _readme_section_counts() -> dict[str, int]:
    """Parse README section headers of the form ``### Name (N tools)``."""
    text = README.read_text(encoding="utf-8")
    out: dict[str, int] = {}
    for m in re.finditer(r"^### ([A-Za-z][A-Za-z\s]*?) \((\d+) tools\)",
                         text, re.MULTILINE):
        name = m.group(1).strip()
        out[name] = int(m.group(2))
    return out


def test_no_hand_written_section_counts_have_come_back():
    """The README must not carry per-section tool counts again.

    It used to state them by hand, one header per source file, and this
    test compared each against the @mcp.tool count. They drifted anyway:
    the headers read 65 Library and 105 PCB while the code had 67 and
    108, and the numbers were only corrected because this guard failed.

    That whole section was a hand-curated copy of
    docs/TOOL_REFERENCE.md, which is GENERATED and cannot drift, so it
    was deleted rather than repaired. With no second copy there is
    nothing left to disagree, and the right guard is the one that stops
    the duplication coming back.

    SECTION_FILES stays because the counts it derives are still the
    reference for what a section contains.
    """
    text = README.read_text(encoding="utf-8", errors="replace")
    revived = [line.strip() for line in text.splitlines()
               if _COUNTED_SECTION.match(line)]
    assert not revived, (
        "the README states per-section tool counts again:\n  "
        + "\n  ".join(revived)
        + "\n\nThose numbers duplicate docs/TOOL_REFERENCE.md, which is "
          "generated from the code. They drifted last time. Link the "
          "generated file instead of restating it.")


def test_the_section_counts_are_still_derivable():
    """The mapping itself must stay usable, or the guard above is prose.

    Without this, SECTION_FILES could name a file that no longer exists
    and nothing would notice, since nothing compares against it now.
    """
    for section, files in SECTION_FILES.items():
        total = sum(_count_mcp_tools(f) for f in files)
        assert total > 0, (
            f"section {section!r} maps to {files}, which register no "
            f"tools; the mapping is stale")


# ---------------------------------------------------------------------
# The HEADLINE numbers, which the section-header check above does not
# cover. These are the first sentence of the README, its feature list,
# and the PyPI description: the three places a reader meets a count
# before any table. They were "300+" and "150+" while the Altium
# backend registered 396, because nothing tied them to anything.
#
# The claims are deliberately approximate, so this checks the CLAIM
# rather than a literal: "around 400" has to stay a fair description of
# the real number, and "480+" has to stay a true lower bound. A guard
# demanding exactness here would just force churn on every tool added.
# ---------------------------------------------------------------------

PYPROJECT = REPO_ROOT / "pyproject.toml"

#: How far the real count may sit from an "around N" claim before the
#: wording stops being honest. Half a hundred either way: at 449 you
#: would say "around 450", at 351 you would say "around 350".
_APPROX_TOLERANCE = 50


def _registered(backend: str) -> int:
    import asyncio

    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, backend, "full")
    return len(asyncio.run(registry.list_tools()))


def test_the_approximate_altium_count_is_still_fair():
    actual = _registered("altium")
    readme = README.read_text(encoding="utf-8", errors="replace")

    claims = [int(m) for m in
              re.findall(r"(?:around|~)\s*(\d{3})\s*tools", readme, re.I)]
    assert claims, (
        "the README no longer states an approximate Altium tool count; "
        "if the wording changed, update this test to match it")

    wrong = sorted({c for c in claims if abs(c - actual) > _APPROX_TOLERANCE})
    assert not wrong, (
        f"the README claims {wrong} tools but the Altium backend "
        f"registers {actual}. Round {actual} to the nearest hundred and "
        f"update every headline claim.")


def test_the_both_backend_lower_bound_is_still_true():
    actual = _registered("both")
    readme = README.read_text(encoding="utf-8", errors="replace")

    bounds = [int(m) for m in re.findall(r"(\d{3})\+\s*with both", readme, re.I)]
    assert bounds, (
        "the README no longer states a '<N>+ with both' lower bound; if "
        "the wording changed, update this test to match it")

    broken = sorted({b for b in bounds if actual < b})
    assert not broken, (
        f"the README promises more than {broken} tools with both "
        f"backends but only {actual} register, so the claim is false.")


def test_the_pypi_description_agrees_with_the_readme():
    """pyproject is the copy nobody thinks to update.

    It is not rendered anywhere in the repo, so a stale count there
    survives every review and shows up only on the PyPI page.
    """
    actual = _registered("altium")
    text = PYPROJECT.read_text(encoding="utf-8", errors="replace")
    description = re.search(r'^description\s*=\s*"(.*)"', text, re.M)
    assert description, "no description in pyproject.toml"

    claims = [int(m) for m in
              re.findall(r"(?:around|~)\s*(\d{3})\s*tools",
                         description.group(1), re.I)]
    assert claims, (
        "the PyPI description no longer states a tool count; if that is "
        "deliberate, drop this test")

    wrong = sorted({c for c in claims if abs(c - actual) > _APPROX_TOLERANCE})
    assert not wrong, (
        f"pyproject.toml advertises {wrong} tools on PyPI but the Altium "
        f"backend registers {actual}.")


# ---------------------------------------------------------------------
# Every tool NAMED in a catalog table must still exist. A renamed or
# removed tool leaves behind a row that reads like documentation and
# sends the reader after something that is not there.
#
# Only that direction is checked. The README lists 194 of the 481
# registered tools on purpose: a complete table would be unusable, and
# which ones earn a row is an editorial call rather than a fact to pin.
#
# Scoped to sections whose header carries a tool count, which is what
# marks a catalog table. Rows in other tables, the part providers for
# instance, name things that are not tools.
# ---------------------------------------------------------------------

_TOOL_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]+)`")
#: The count suffix is optional now. The README wrote its headers by
#: hand as "### Library (67 tools)"; the generated reference writes
#: "## library (67)". Requiring the word silently scoped this scan to
#: nothing once the hand-written copy was deleted.
_COUNTED_SECTION = re.compile(r"^#{2,3}\s+(.*?)\s*\((\d+)(?:\s+tools?)?\)\s*$")


def _tools_named_in(text: str) -> dict[str, str]:
    """Tool name -> the section header it appears under.

    Takes the text rather than reading the README so the scoping can be
    exercised on layouts the real file does not currently have.
    """
    named: dict[str, str] = {}
    section = None
    for line in text.splitlines():
        header = _COUNTED_SECTION.match(line)
        if header:
            section = header.group(1)
            continue
        if line.startswith("#"):
            section = None          # left the catalog section
            continue
        if section:
            row = _TOOL_ROW.match(line)
            if row:
                named.setdefault(row.group(1), section)
    return named


def _tools_named_in_catalog_sections() -> dict[str, str]:
    """Across the whole documentation set, not the README alone.

    The catalog moved out of the README into the generated reference,
    and a tool named in any published file is the same promise to a
    reader wherever it sits.
    """
    from tests import documentation_set

    return _tools_named_in(documentation_set.all_text())


def _registered_both() -> set[str]:
    """Every tool any backend registers.

    "both" means Altium plus KiCad, and EasyEDA is a third backend that
    it does not include. Once the documentation set grew to cover all
    three, comparing against "both" alone reported every easyeda_* tool
    as undocumented, which said nothing about the tools and everything
    about the comparison set.
    """
    import asyncio

    from eda_agent.server import register_backend
    from eda_agent.tools import BACKENDS
    from eda_agent.tools.registry import ToolRegistry

    names: set[str] = set()
    for backend in BACKENDS:
        registry = ToolRegistry()
        register_backend(registry, backend, "full")
        names |= {t.name for t in asyncio.run(registry.list_tools())}
        # Reached through a family tool or a bulk tool, and listed so.
        names |= set(getattr(registry, "hidden", {}) or {})
    return names


def test_every_tool_named_in_the_readme_exists():
    named = _tools_named_in_catalog_sections()
    assert len(named) > 100, (
        f"only {len(named)} tool rows parsed out of the README catalog "
        f"sections; the table shape changed and this check is blind")

    real = _registered_both()
    missing = sorted(f"{name}  (under '{named[name]}')"
                     for name in named if name not in real)
    assert not missing, (
        "the README documents these tools but nothing registers them, so "
        "a reader is sent after something that does not exist:\n  "
        + "\n  ".join(missing))


def test_a_table_outside_a_catalog_section_is_not_read_as_tools():
    """Rows elsewhere have the same shape and must stay out of scope.

    The part providers (easyeda, kicad_local, partreel) are written as
    a table of backticked names, identical in shape to a tool row.
    Counting them would fail the check above on correct documentation,
    and the obvious fix would be an allowlist that then hides a
    genuinely stale row later.

    Exercised on synthetic text, with the non-tool table placed AFTER a
    catalog section. In the real README it happens to sit 337 lines
    earlier, so testing against the file would pass on document order
    alone and prove nothing about the scoping: deleting the reset that
    ends a section left that version of this test green.
    """
    named = _tools_named_in(
        "### PCB (2 tools)\n"
        "| `pcb_get_nets` | reads nets |\n"
        "| `pcb_place_via` | places a via |\n"
        "\n"
        "## Part sourcing\n"
        "| `easyeda` | a provider, not a tool |\n"
        "| `partreel` | also a provider |\n"
    )
    assert set(named) == {"pcb_get_nets", "pcb_place_via"}, named
    assert named["pcb_get_nets"] == "PCB"


def test_the_real_provider_table_is_still_out_of_scope():
    """And the same holds for the README as it is actually written."""
    named = _tools_named_in_catalog_sections()
    for provider in ("easyeda", "kicad_local", "partreel"):
        assert provider not in named, (
            f"{provider} is a part provider, not a tool, but it was "
            f"picked up as one")

