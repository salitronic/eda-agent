# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The compiled-netlist cache must not outlive the design it describes.

SmartCompile skips DM_Compile for COMPILE_CACHE_TTL_MS when the project
reports no dirty documents. Two holes made that return connectivity from
before an edit:

  InvalidateCompileCache was defined in Main.pas and CALLED FROM NOWHERE.
  A grep for it returned exactly one hit, its own definition. So a write
  followed inside the TTL by a connectivity read answered from the stale
  cache, unless that particular handler happened to dirty the document,
  which is not uniform: many writes go through ProcessControl and mark it
  modified, others assign through SetState_ and do not.

  ProjectHasDirtyDocs swallowed every exception and left Result False, so
  a project whose documents could not be read at all reported nothing
  dirty. Reading nothing and there being nothing are different answers
  and only one of them means the cache is still good.

These tests are deliberately about the two mechanisms rather than about
one tool's output, because the defect was structural: no single tool was
wrong, the shared cache under all of them was.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_cross_validate import (          # noqa: F401  (fixture)
    fpc_executable,
    read_outputs,
    write_inputs,
)

SCRIPTS = Path(__file__).parent.parent / "scripts" / "altium"
MAIN = SCRIPTS / "Main.pas"
CROSS_VALIDATOR = Path(__file__).parent / "cross_validate_pascal.pas"


def _dispatch_unit() -> Path:
    """The unit carrying the per-request dispatch, wherever it lives.

    It was Dispatcher.pas until the blocking polling loop became a timer
    on the dashboard: a form's event handler resolves only inside the
    form's own unit, so ProcessSingleRequest and the read-only classifier
    had to move into StatusForm.pas alongside the timer. Not one thing
    these tests check changed, yet all of them failed, so find the unit
    by what it CONTAINS and let the code move again without this
    noticing.
    """
    for path in sorted(SCRIPTS.glob("*.pas")):
        if path.name == "Altium_MCP.pas":        # generated, would double count
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^Function\s+ProcessSingleRequest\b", text, re.M):
            return path
    raise AssertionError(
        "no .pas file defines ProcessSingleRequest, so the search for the "
        "dispatch unit has gone blind and these tests check nothing")


DISPATCHER = _dispatch_unit()


def _function_source(text: str, name: str) -> str:
    """One Pascal function, from its header to its closing End."""
    start = text.index("Function " + name)
    end = text.index("\nEnd;", start) + len("\nEnd;")
    return text[start:end]


# ---------------------------------------------------------------------------
# Hole 1: the invalidator was never called.
# ---------------------------------------------------------------------------

def test_the_invalidator_is_actually_called():
    """The original defect was dead code, not wrong code.

    InvalidateCompileCache did exactly the right thing and no caller ever
    reached it, so every write left the cache in place. A test that only
    checked what the procedure DOES would have passed throughout.
    """
    hits = []
    for path in SCRIPTS.glob("*.pas"):
        if path.name == "Altium_MCP.pas":        # generated, would double count
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # COMMENTS ARE NOT CALL SITES. Without this the guard counted the
        # sentence in Dispatcher.pas explaining that the procedure was
        # never called, and so passed with the call removed. Caught by
        # mutating the defect back in, not by reading the test.
        text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
        text = re.sub(r"//.*", " ", text)
        for i, line in enumerate(text.splitlines(), 1):
            if "InvalidateCompileCache" in line and "Procedure" not in line:
                hits.append(f"{path.name}:{i}")
    assert hits, (
        "InvalidateCompileCache has no call sites, so nothing invalidates "
        "the compiled-netlist cache and a read after a write can answer "
        "from before it")


def test_the_invalidation_runs_for_writes_and_not_for_reads():
    """It has to be conditional, or the cache is pointless.

    Invalidating unconditionally would recompile on every read, which is
    the cost the TTL exists to avoid.
    """
    text = DISPATCHER.read_text(encoding="utf-8", errors="replace")
    assert "If Not CommandIsReadOnly(Command) Then InvalidateCompileCache" in text


def test_the_invalidation_also_covers_the_exception_path():
    """A handler that threw may have written something first.

    Placing the call after the Try/Except rather than inside the success
    branch is the whole point: a partial write is exactly when a cached
    netlist is worth least.
    """
    text = DISPATCHER.read_text(encoding="utf-8", errors="replace")
    exception_branch = text.index("ResultTag := 'EXCEPTION';")
    invalidate = text.index("If Not CommandIsReadOnly(Command) Then")
    assert invalidate > exception_branch, (
        "the invalidation sits before the exception branch, so a handler "
        "that threw part way through a write leaves the cache in place")


# ---------------------------------------------------------------------------
# The classifier. Unknown must mean write.
# ---------------------------------------------------------------------------

def command_is_read_only(command: str) -> bool:
    """Python mirror of the Pascal, cross-validated below."""
    action = command.strip().lower()
    dot = action.find(".")
    if dot >= 0:
        action = action[dot + 1:]
    prefixes = ("get_", "list_", "query", "read_", "find_", "count",
                "audit_", "check_", "calc_", "export_", "render_",
                "probe_", "inspect_", "diff_", "compare_")
    return action.startswith(prefixes) or action == "ping"


@pytest.mark.parametrize("command", [
    "generic.set_component_part_id",
    "generic.create_object",
    "pcb.place_via",
    "project.compile",
    "library.rename_component",
    "generic.some_tool_added_next_year",
    "",
    "no_namespace_at_all",
])
def test_anything_not_known_to_be_a_read_counts_as_a_write(command):
    """The bias must be towards invalidating.

    An unnecessary invalidation costs one recompile. A missed one hands
    back a netlist that predates the edit, and the caller cannot tell.
    """
    assert command_is_read_only(command) is False


@pytest.mark.parametrize("command", [
    "project.get_nets", "generic.query_objects", "application.ping",
    "pcb.list_layers", "audit.audit_find_single_pin_nets",
    "project.export_bom", "pcb.render_svg", "library.compare_libraries",
])
def test_reads_keep_the_cache(command):
    assert command_is_read_only(command) is True


def test_the_classifier_has_no_catch_all_true():
    """A trailing `Result := True` would silently disable invalidation.

    The function must be a closed list of read prefixes and nothing else,
    since any fallback to True is a fallback to reusing a stale netlist.
    """
    source = _function_source(
        DISPATCHER.read_text(encoding="utf-8", errors="replace"),
        "CommandIsReadOnly")
    body = source[source.index("Begin"):]
    assignments = re.findall(r"Result\s*:=\s*(\w+)", body)
    assert "True" not in assignments, (
        "CommandIsReadOnly assigns Result := True outside the prefix "
        "expression, which would treat some unknown command as a read")


# ---------------------------------------------------------------------------
# The copy in the cross-validator must not drift from the original.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ActionHasPrefix", "CommandIsReadOnly"])
def test_the_cross_validated_copy_matches_the_real_source(name):
    """A hand-maintained copy passes in exactly the case that matters.

    cross_validate_pascal.pas holds copies of the real functions so Free
    Pascal can run them. If the original changes and the copy does not,
    the cross-validation goes on proving the OLD function correct and
    reports success, which is worse than not running.
    """
    original = _function_source(
        DISPATCHER.read_text(encoding="utf-8", errors="replace"), name)
    copy = _function_source(
        CROSS_VALIDATOR.read_text(encoding="utf-8", errors="replace"), name)

    def flat(text):
        return re.sub(r"\s+", " ", text).strip()

    assert flat(original) == flat(copy), (
        f"{name} in cross_validate_pascal.pas has drifted from "
        f"Dispatcher.pas, so the cross-validation is checking a stale copy")


def test_pascal_and_python_agree_on_the_classifier(fpc_executable, tmp_path):
    """The real Pascal, compiled and run, against the mirror above.

    The mirror is what the parametrized tests exercise; without this it
    would only prove the mirror self-consistent, which says nothing about
    what Altium runs.
    """
    commands = [
        "generic.set_component_part_id", "project.get_nets",
        "generic.query_objects", "pcb.place_via", "application.ping",
        "library.rename_component", "audit.audit_find_via_antennas",
        "project.export_bom", "generic.unknown_future_tool",
        "no_namespace", "", "PROJECT.GET_NETS", "  project.get_nets  ",
        "pcb.render_svg", "generic.compare_documents",
    ]
    cases = [("CommandIsReadOnly", [c]) for c in commands]

    input_file = tmp_path / "cv_in.txt"
    output_file = tmp_path / "cv_out.txt"
    write_inputs(cases, str(input_file))

    result = subprocess.run(
        [fpc_executable, str(input_file), str(output_file)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"{result.stdout} {result.stderr}"

    pascal = read_outputs(str(output_file))
    assert len(pascal) == len(commands)

    mismatches = []
    for command, got in zip(commands, pascal):
        want = "true" if command_is_read_only(command) else "false"
        if got != want:
            mismatches.append(f"  {command!r}: Pascal {got!r}, Python {want!r}")
    assert not mismatches, "classifier disagrees:\n" + "\n".join(mismatches)


# ---------------------------------------------------------------------------
# Hole 2: the dirty check failed clean.
# ---------------------------------------------------------------------------

def test_an_unreadable_document_counts_as_dirty():
    """Swallowing the exception was the bug.

    The old body was `Except End;`, which left Result False, so a project
    whose documents could not be reached reported itself clean and the
    cache was reused.
    """
    source = _function_source(
        MAIN.read_text(encoding="utf-8", errors="replace"),
        "ProjectHasDirtyDocs")
    assert "Except End;" not in source, (
        "ProjectHasDirtyDocs swallows an exception, which makes an "
        "unreadable project read as a clean one")

    # EACH FAILURE BRANCH INDIVIDUALLY. Counting `Result := True` across
    # the whole function was not enough: changing the unreadable branch
    # to `Continue` left three of them elsewhere and the guard passed.
    # Found by mutating that branch back, not by reading the test.
    lines = source.splitlines()
    for condition in ("If Doc = Nil Then", "If Not Readable Then"):
        where = next((i for i, line in enumerate(lines)
                      if condition in line), None)
        assert where is not None, f"{condition!r} is gone from the function"
        following = " ".join(lines[where:where + 5])
        assert "Result := True" in following, (
            f"{condition!r} does not set Result := True, so a document "
            f"this cannot read is treated as evidence that the project is "
            f"clean and the cached netlist is reused")


def test_a_closed_document_still_counts_as_clean():
    """The fail-safe must not become fail-always.

    A Nil ServerDoc means the document is not open in the editor, and a
    closed document cannot hold unsaved edits. Treating that as dirty
    would force a recompile on every call for any project with a closed
    sheet, which is most of them, and the TTL would never apply.
    """
    source = _function_source(
        MAIN.read_text(encoding="utf-8", errors="replace"),
        "ProjectHasDirtyDocs")
    assert "If ServerDoc <> Nil Then" in source, (
        "a Nil server document must be skipped rather than treated as "
        "dirty, or the compile cache is effectively disabled")
