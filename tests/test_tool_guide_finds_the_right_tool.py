# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The scoreboard for ``tool_guide``, measured at the entry point agents use.

THIS TESTS ``guidance_for`` WITH THE LIVE DOCSTRINGS, NOT A HELPER.  The
earlier eval called ``derived_recipes`` directly and passed four questions
that failed through the real tool, because in production the docstring
search only ran when the curated table missed ENTIRELY, and a curated
recipe sharing a single common word ("board", "library") counted as a
hit.  Three of those four "fixed" questions were answered with the
mechanical-layers recipe.  An eval that exercises a code path production
skips is measuring something nobody receives.

The cost of getting this wrong is not a missed answer, it is a confident
wrong one.  Measured over one week (2026-09-19 to 2026-09-23): asked how
to set Design Item ID, the guide said use ``lib_set_mech_layers`` and
avoid ``obj_modify``; asked how to place a part, it offered
``lib_set_mech_layers`` and ``obj_delete``.  Agents that followed the
server's own instruction to consult it before concluding a capability
was absent got steered to the wrong tool, failed, and fell back to
driving Altium's dialogs through UI automation, once crashing it.

Two sets:

* ``QUESTIONS`` are real questions that cost real sessions, plus the
  held-out sets that have since been tuned against.  Every one must be
  answered, and a new one belongs here whenever an agent misses a tool
  that exists.
* ``HELD_OUT`` are paraphrases written BEFORE the guide was changed and
  never tuned against.  They report whether the fix generalises or just
  memorised the questions above.  They carry a floor, not a requirement,
  so that nobody is tempted to add their exact wording as keywords.
  Once a miss here IS fixed, the set is spent: move it into
  ``QUESTIONS`` and write a new one before the next change.
"""

from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools.guidance import guidance_for

#: How deep an agent reads. A correct tool at rank 12 is not an answer.
TOP_N = 5


def _docs() -> dict[str, str]:
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_backend(reg, "both", "full")
    return {t.name: (t.description or "")
            for t in asyncio.run(reg.list_tools())}


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    d = _docs()
    assert len(d) > 300, f"only {len(d)} docstrings; the registry did not load"
    return d


def answer(question: str, docs: dict[str, str]) -> list[str]:
    """The tools in the order an agent reading ``tool_guide`` meets them."""
    out = guidance_for(question, "", "altium", docs)
    seen: list[str] = []
    for block in ("recipes", "derived"):
        for r in out.get(block, []):
            for t in r.get("use", []):
                if t not in seen:
                    seen.append(t)
    return seen


def _found(tools: list[str], wanted: set[str]) -> int | None:
    for i, t in enumerate(tools[:TOP_N], 1):
        if t in wanted:
            return i
    return None


#: (what a stuck caller typed, tools that would have answered it)
QUESTIONS = [
    # Older sessions, previously "passing" only through a helper.
    ("place a 3d step model on the board", {"pcb_place_3d_body"}),
    ("remove dead copper from a polygon", {"pcb_modify_polygon"}),
    ("delete a project variant", {"proj_delete_variant"}),
    ("repour polygons", {"pcb_repour_polygons"}),
    # The week of 2026-09-19.
    ("place a component from a library onto a schematic sheet",
     {"sch_place_components", "design_execute_plan"}),
    ("place a part from an IntLib",
     {"sch_place_components", "design_execute_plan"}),
    ("delete a text frame from a schematic", {"obj_delete"}),
    ("remove a note or text box from a sheet", {"obj_delete"}),
    ("set the design item id on many library components",
     {"lib_clear_source_library"}),
    ("fix Not Found components after a library relink",
     {"lib_clear_source_library", "sch_clear_source_library"}),
    ("bulk change a property on every symbol in a schlib without renaming",
     {"lib_clear_source_library", "lib_batch_set_params"}),
    ("mirror a schematic component horizontally", {"sch_mirror_component"}),
    ("flip a part on the schematic", {"sch_mirror_component"}),
    ("change pin electrical type on part 3 of a multi-part symbol",
     {"obj_modify"}),
    ("copy a library symbol to a new name", {"lib_copy_component"}),
    # The first held-out set. Seven passed untuned; the other three (put,
    # reflect, duplicate) were then fixed by adding those words, which
    # spent the set as a measure. All ten stay here as regressions.
    ("put a resistor from my schlib on the sheet",
     {"sch_place_components", "design_execute_plan"}),
    ("how do I remove a text box I placed", {"obj_delete"}),
    ("parts show Not Found in the properties panel",
     {"lib_clear_source_library", "sch_clear_source_library"}),
    ("reflect a symbol left to right", {"sch_mirror_component"}),
    ("duplicate a library part under another name", {"lib_copy_component"}),
    ("edit a pin on the second gate of a quad opamp symbol", {"obj_modify"}),
    ("update DesignItemId for all parts in the library",
     {"lib_clear_source_library"}),
    ("delete a note from the schematic", {"obj_delete"}),
    ("place a symbol from an integrated library",
     {"sch_place_components", "design_execute_plan"}),
    ("set the electrical type of a pin in section B of a multi part component",
     {"obj_modify"}),
]

#: The second held-out set, written 2026-09-23 BEFORE put, reflect and
#: duplicate were added, and scored then: 8 of 10. The two misses ("add
#: ... to my schematic", "drop ... onto the sheet") are left alone on
#: purpose. Fix them by adding those words and this set is spent too,
#: and has to be replaced the same way.
HELD_OUT = [
    ("add a capacitor from the library to my schematic",
     {"sch_place_components", "design_execute_plan"}),
    ("get rid of a text frame on the sheet", {"obj_delete"}),
    ("symbols say not found after I moved the library",
     {"lib_clear_source_library", "sch_clear_source_library"}),
    ("turn a symbol around horizontally on the schematic",
     {"sch_mirror_component"}),
    ("make a copy of a symbol in the same schlib", {"lib_copy_component"}),
    ("change a pin type on the third unit of a multipart symbol",
     {"obj_modify"}),
    ("sync the design item id with the lib ref for every symbol",
     {"lib_clear_source_library"}),
    ("erase a note on a schematic sheet", {"obj_delete"}),
    ("drop an opamp from an intlib onto the sheet",
     {"sch_place_components", "design_execute_plan"}),
    ("clone a schlib component with a new name", {"lib_copy_component"}),
]

#: The held-out floor. Raised only when the held-out score rises by itself.
HELD_OUT_FLOOR = 0.6


@pytest.mark.parametrize("question,wanted", QUESTIONS,
                         ids=[q for q, _ in QUESTIONS])
def test_a_question_that_cost_a_session_is_answered(question, wanted, docs):
    tools = answer(question, docs)
    rank = _found(tools, wanted)
    assert rank is not None, (
        f"tool_guide({question!r}) did not put any of {sorted(wanted)} in "
        f"its top {TOP_N}. It returned {tools[:TOP_N]}. An agent reading "
        f"this concludes the capability does not exist, and the fallback "
        f"it reaches for is driving Altium's dialogs by hand.")


def test_held_out_paraphrases_generalise(docs):
    """Reported, with a floor. Never add these phrasings as keywords."""
    results = []
    for question, wanted in HELD_OUT:
        rank = _found(answer(question, docs), wanted)
        results.append((question, rank))
    score = sum(1 for _q, r in results if r) / len(results)
    missed = [q for q, r in results if not r]
    assert score >= HELD_OUT_FLOOR, (
        f"held-out score {score:.0%} is below the {HELD_OUT_FLOOR:.0%} "
        f"floor, so the guide answers the questions it was tuned on and "
        f"not the same questions reworded. Missed: {missed}")


def test_the_guide_never_confidently_names_an_unrelated_tool(docs):
    """A wrong answer is worse than none, because it looks authoritative.

    These are tools the guide returned, first, for questions they have
    nothing to do with. They must not come back for the same questions.
    """
    wrong = [
        ("set the design item id on many library components",
         "lib_set_mech_layers"),
        ("place a component from a library onto a schematic sheet",
         "lib_set_mech_layers"),
        ("copy a library symbol to a new name", "lib_set_mech_layers"),
        ("place a 3d step model on the board", "pcb_set_mech_layers"),
        # The stencil-paste recipe, first, on the word "variant".
        ("delete a project variant", "pcb_apply_dnp_paste_exclusion"),
    ]
    for question, bad in wrong:
        tools = answer(question, docs)[:TOP_N]
        assert bad not in tools, (
            f"tool_guide({question!r}) still offers {bad}, which has "
            f"nothing to do with the question: {tools}")


#: (the question, the tool the session reached for instead and should not have)
TRAPS = [
    ("set the design item id on many library components",
     "app_set_dialog_control"),
    ("set the design item id on many library components",
     "lib_rename_component"),
    ("place a component from a library onto a schematic sheet",
     "app_run_ui_command"),
    ("delete a text frame from a schematic", "sch_place_text_frame"),
    ("mirror a schematic component horizontally", "obj_modify"),
]


@pytest.mark.parametrize("question,trap", TRAPS,
                         ids=[f"{q} / {t}" for q, t in TRAPS])
def test_the_trap_a_session_fell_into_is_named(question, trap, docs):
    """Finding the right tool is half of it. The other half is the warning.

    The docstring search can find lib_clear_source_library on its own, so
    the scoreboard above still passes with the curated recipe deleted. What
    the search cannot produce is the "avoid" list: the session that asked
    about Design Item ID was about to set it one symbol at a time through
    the Properties panel, and a rename would have recreated the Not Found
    state it was fixing. Only a curated recipe says that.
    """
    out = guidance_for(question, "", "altium", docs)
    assert out["recipes"], f"tool_guide({question!r}) returned no curated recipe"
    avoided = [a["tool"] for a in out["recipes"][0].get("avoid", [])]
    assert trap in avoided, (
        f"tool_guide({question!r}) leads with "
        f"{out['recipes'][0].get('task')!r}, which does not warn off {trap}. "
        f"It warns off {avoided}.")
