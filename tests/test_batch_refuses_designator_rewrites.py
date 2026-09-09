# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A batch designator rewrite has no safe partial result.

Reported from a live project (issue #23): a 25-op obj_batch_modify that
normalised designator prefixes died part way, about ten ops had applied,
the partial rename reached disk, File > Revert All did not undo it, and
because two parts were both left as ``D?`` the netlister merged their
two separate anode nets. Two indicator circuits were silently shorted
and it was found only by diffing the compiled netlist.

The obvious fix, rolling the batch back on any failure, is not on offer:
DelphiScript has no transaction rollback, and promising one would be a
lie. Refusing the write is, and proj_annotate renumbers a project in one
operation. The singular obj_modify still accepts a rename, because one
write either happens or does not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import load

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


@pytest.fixture(scope="module")
def functions() -> dict[str, str]:
    return load(_PAS, minimum=80)


def test_the_batch_refuses_a_designator_write(functions):
    body = functions["Gen_BatchModify"]
    assert "SetWritesDesignator(SetStr)" in body
    assert "designator_refused_use_proj_annotate" in body


def test_the_refusal_names_the_tool_that_works(functions):
    """A refusal that does not say where to go sends the caller back to
    the same tool with the same payload."""
    body = functions["Gen_BatchModify"]
    assert "proj_annotate" in body


@pytest.mark.parametrize("spelling", ["'DESIGNATOR'", "'DESIGNATOR.TEXT'"])
def test_both_spellings_of_the_property_are_refused(functions, spelling):
    """The filter grammar accepts either, so a guard that knows only one
    is a guard with a documented way round it.

    Asserted against the Pascal itself rather than a Python model of it:
    the first version of this test re-implemented the parser and then
    checked its own re-implementation, which passed happily while the
    handler compared against one name.
    """
    body = functions["SetWritesDesignator"]
    assert f"PropName = {spelling}" in body


def test_the_comparison_survives_case_and_whitespace(functions):
    """``designator.text = R1`` is the same write as ``Designator.Text=R1``
    and reaches the same handler."""
    body = functions["SetWritesDesignator"]
    assert "UpperCase(Trim(" in body


def test_the_property_is_read_from_each_pipe_separated_pair(functions):
    """A set-string is ``A=1|B=2``: a designator write hidden behind a
    legitimate one must still be found."""
    body = functions["SetWritesDesignator"]
    assert "Pos('|', Remaining)" in body
    assert "Pos('=', Assignment)" in body
    assert "Copy(Assignment, 1, EqPos - 1)" in body, (
        "the name is no longer taken from the left of the first =")


def test_a_refused_op_does_not_stop_the_batch(functions):
    """It is recorded like any other skipped op, so the 24 legitimate
    ops in a 25-op batch still run."""
    body = functions["Gen_BatchModify"]
    guard = body.split("SetWritesDesignator(SetStr)", 1)[1][:400]
    assert "Note :=" in guard
    assert "Exit" not in guard.split("End;", 1)[0], (
        "the refusal aborts the whole batch instead of skipping one op")


def test_the_singular_modify_still_renames(functions):
    """One write either happens or does not, so the hazard is not there
    and removing the capability would cost more than it saves."""
    body = functions["Gen_ModifyObjects"]
    assert "SetWritesDesignator" not in body


def test_the_tool_docstring_tells_the_caller_before_they_try():
    """A refusal at the bridge is late: the tool is what the model reads
    when it is deciding how to do a bulk rename."""
    import inspect

    from eda_agent.tools import generic as generic_mod
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    generic_mod.register_generic_tools(registry)
    import asyncio
    tools = {t.name: t for t in asyncio.run(registry.list_tools())}
    doc = tools["obj_batch_modify"].description or ""
    assert "proj_annotate" in doc
    assert "designator" in doc.lower()
