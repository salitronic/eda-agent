# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Text an agent reads as instruction must match the code it describes.

Two claims live here, both about prose that is consumed rather than
browsed: the tools the discipline rules name, and the number of
pipeline stages that half a dozen documents state as a fact.

---

The discipline rules must not tell the agent to call a missing tool.

``design_get_discipline`` serves ``design/discipline.py`` verbatim to
the client, and the autonomous flow reads it once at the start of a run
and works from it. The rules name tools directly ("author the symbol
via ``lib_create_symbol`` + ``lib_add_pins``"), so a renamed or removed
tool turns an instruction into a dead end that the agent discovers only
by calling it.

``test_docstring_references.py`` checks the same thing for MCP tool
docstrings, but stops at ``tools/``. This file is the higher-stakes
half: a docstring is read by a model deciding between tools it can
already see, while the discipline text is read as instruction.

Field names are excluded by DERIVING them from the plan models rather
than by listing them. ``lib_ref`` and ``lib_path`` are ``Part`` fields
that happen to start with a tool prefix, and an allowlist naming them
would have to be maintained by hand and would silently absorb a real
stale tool that later took a matching name.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DISCIPLINE = REPO / "src" / "eda_agent" / "design" / "discipline.py"

#: Prefixes that make a backticked identifier look like a tool name.
_TOOL_PREFIXES = (
    "app_", "proj_", "lib_", "obj_", "sch_", "pcb_", "audit_", "design_",
    "sim_", "route_", "tool_", "part_", "kicad_",
)


def _plan_field_names() -> set[str]:
    """Every field on the plan models, which are not tools."""
    from eda_agent.design.plan import DesignPlan, Net, Part, PinRef

    names: set[str] = set()
    for model in (DesignPlan, Part, Net, PinRef):
        names |= set(model.model_fields)
    return names


def _registered_tools() -> set[str]:
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, "both", "full")
    return {t.name for t in asyncio.run(registry.list_tools())}


def _tool_shaped_references() -> set[str]:
    text = DISCIPLINE.read_text(encoding="utf-8", errors="replace")
    return {name for name in re.findall(r"`([a-z][a-z0-9_]{3,})`", text)
            if name.startswith(_TOOL_PREFIXES)}


def test_every_tool_the_discipline_names_exists():
    referenced = _tool_shaped_references()
    assert len(referenced) > 30, (
        f"only {len(referenced)} tool-shaped names found in "
        f"discipline.py; the scan is not seeing the rules and this "
        f"guard proves nothing")

    missing = sorted(referenced - _registered_tools() - _plan_field_names())
    assert not missing, (
        "the discipline rules tell the agent to use these, but nothing "
        "registers them, so following the rules leads to a failed "
        f"call:\n  " + "\n  ".join(missing))


def test_plan_fields_are_excluded_by_derivation():
    """The exclusion must come from the models, not from a list here.

    If the plan models stop carrying these, the exclusion should
    disappear with them rather than linger as an allowlist entry that
    quietly forgives a real stale tool name.
    """
    fields = _plan_field_names()
    assert {"lib_ref", "lib_path"} <= fields, (
        "Part no longer has lib_ref / lib_path, so the reason these are "
        "excluded from the tool check has gone; re-check whether "
        "discipline.py still mentions them and why")
    # They must actually be reachable through the scan, or the exclusion
    # is doing nothing and the test above is weaker than it looks.
    assert "lib_ref" in _tool_shaped_references()


# ---------------------------------------------------------------------
# The pipeline stage COUNT. `session.STAGES` is the source of truth and
# is already pinned to `state_machine.STAGE_PLAYBOOKS` by
# tests/design/test_state_machine.py. What nothing pinned is the number
# written out in prose, which appears in eight places including two
# tool docstrings that are served to MCP clients and the skill file the
# autonomous flow reads. Adding a stage would leave all eight saying 13.
# ---------------------------------------------------------------------

#: Everywhere the count is written by hand.
_STAGE_COUNT_CLAIM_FILES = (
    "skills/autodesign/SKILL.md",
    "docs/AUTONOMOUS_DESIGN.md",
    "README.md",
    "src/eda_agent/design/autonomy.py",
    "src/eda_agent/design/session.py",
    "src/eda_agent/tools/design.py",
)

#: "13 stages", "13-stage", "13 stage pipeline".
_STAGE_COUNT = re.compile(r"\b(\d+)[\s-]stage")


def _stage_count() -> int:
    from eda_agent.design.session import STAGES

    return len(STAGES)


def test_every_stated_stage_count_matches_the_pipeline():
    expected = _stage_count()
    wrong, seen = [], 0
    for rel in _STAGE_COUNT_CLAIM_FILES:
        path = REPO / rel
        if not path.is_file():
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8",
                               errors="replace").splitlines(), 1):
            for claimed in _STAGE_COUNT.findall(line):
                seen += 1
                if int(claimed) != expected:
                    wrong.append(f"{rel}:{lineno} says {claimed}, "
                                 f"pipeline has {expected}")

    assert seen >= 6, (
        f"only found {seen} stated stage counts across "
        f"{len(_STAGE_COUNT_CLAIM_FILES)} files; the wording changed and "
        f"this guard is no longer watching them")
    assert not wrong, (
        "these state a stage count the pipeline does not have, and two "
        "of them are served to clients as tool documentation:\n  "
        + "\n  ".join(wrong))


def test_the_stated_retry_limit_matches_the_state_machine():
    """SKILL.md promises the agent a specific number of retries.

    ``MAX_STAGE_ATTEMPTS`` decides when a failing stage escalates to a
    human question. The skill file states that number so the agent
    knows how many attempts it has before the run blocks. Lowering the
    constant without editing the file leaves the agent planning around
    retries it no longer gets.

    Not guarded alongside it: the "discipline rule N" citations
    scattered through the source comments. The check that would catch
    the likely failure, a rule inserted mid-list so every N stays valid
    but shifts meaning, needs a hand-written "rule 17 means fill"
    table, which is the same duplicated-fact problem one level up. An
    existence-only check would pass that mutation while looking like
    coverage.
    """
    from eda_agent.design.state_machine import MAX_STAGE_ATTEMPTS

    skill = (REPO / "skills" / "autodesign" / "SKILL.md")
    if not skill.is_file():
        return
    text = skill.read_text(encoding="utf-8", errors="replace")

    claims = [int(n) for n in
              re.findall(r"fails\s+(\d+)\s+times", text)]
    assert claims, (
        "SKILL.md no longer states a retry limit; if the wording "
        "changed, update this test to match it")
    wrong = sorted({c for c in claims if c != MAX_STAGE_ATTEMPTS})
    assert not wrong, (
        f"SKILL.md tells the agent a stage escalates after {wrong} "
        f"failures but MAX_STAGE_ATTEMPTS is {MAX_STAGE_ATTEMPTS}.")


def test_the_stage_count_scan_would_notice_a_wrong_number():
    """The regex has to match the phrasings actually used."""
    assert _STAGE_COUNT.findall("the 13 stages are done") == ["13"]
    assert _STAGE_COUNT.findall("a 13-stage state machine") == ["13"]
    assert _STAGE_COUNT.findall("across the 13-stage pipeline") == ["13"]
    assert not _STAGE_COUNT.findall("thirteen stages")


def test_the_scan_sees_real_tool_names():
    """A regex that matched nothing would pass the check above."""
    referenced = _tool_shaped_references()
    real = _registered_tools()
    assert len(referenced & real) > 30, (
        f"only {len(referenced & real)} of the names resolve to real "
        f"tools; the scan or the registry has changed shape")
    for expected in ("design_execute_plan", "design_validate",
                     "lib_add_pins"):
        assert expected in referenced, (
            f"discipline.py no longer mentions {expected}; if the rules "
            f"were rewritten, re-check what this guard covers")
