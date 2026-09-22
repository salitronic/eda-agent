# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The limits on what the bridge will do, and the twin that must agree.

Untrusted text reaches the model by design: a component ``Comment``, a
net label or a parameter value is free text out of a design file that
somebody else may have written, and it lands in context verbatim.  What
matters is what that text can reach in turn.

Most of the write surface is bounded to the design.  ``obj_run_process``
is not: it takes a process name AND free-form parameters, and Altium
publishes ``ScriptingSystem:RunScriptText``, so that one tool is
general-purpose code execution inside an application with full
filesystem access.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eda_agent.safety import (
    READONLY_ENV,
    READONLY_VERB_PREFIXES,
    command_is_read_only,
    refuse_command,
    refuse_process,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_FORM = REPO_ROOT / "scripts" / "altium" / "StatusForm.pas"


# --------------------------------------------------------------------------
# code execution through the process layer
# --------------------------------------------------------------------------

@pytest.mark.parametrize("process", [
    "ScriptingSystem:RunScript",
    "ScriptingSystem:RunScriptFile",
    "ScriptingSystem:RunScriptText",
    "scriptingsystem:runscripttext",      # Altium is not case-sensitive
    "  ScriptingSystem:RunScriptText  ",  # nor is whitespace a defence
])
def test_the_scripting_family_is_refused(process: str) -> None:
    """This is the one tool that turns read text into executed code."""
    assert refuse_process(process) is not None, (
        f"{process} would run arbitrary script code inside Altium")


@pytest.mark.parametrize("process", [
    "Sch:Zoom",
    "SCH:NextComponentPart",
    "PCB:Zoom",
    "IntegratedLibrary:ExtractSources",
    "WorkspaceManager:SaveObject",
])
def test_ordinary_processes_still_run(process: str) -> None:
    """The limit must not cost real functionality.

    A guard that refuses legitimate work gets removed, and then the
    dangerous case is unguarded too.
    """
    assert refuse_process(process) is None, (
        f"{process} is a normal operation and must not be refused")


# --------------------------------------------------------------------------
# read-only mode
# --------------------------------------------------------------------------

def test_an_unknown_command_counts_as_a_write():
    """Fail closed. Every command added later is refused by default."""
    assert command_is_read_only("generic.frobnicate") is False


def test_reads_and_writes_are_told_apart():
    assert command_is_read_only("generic.query_objects")
    assert command_is_read_only("application.ping")
    assert not command_is_read_only("generic.modify_objects")
    assert not command_is_read_only("generic.place_sch_components_from_library")


def test_readonly_mode_refuses_writes_and_allows_reads(monkeypatch):
    monkeypatch.setenv(READONLY_ENV, "1")
    assert refuse_command("generic.modify_objects") is not None
    assert refuse_command("generic.query_objects") is None


def test_the_flag_is_off_by_default(monkeypatch):
    """Nobody's workflow changes unless they opt in."""
    monkeypatch.delenv(READONLY_ENV, raising=False)
    assert refuse_command("generic.modify_objects") is None


# --------------------------------------------------------------------------
# the twin
# --------------------------------------------------------------------------

def test_the_python_and_pascal_readonly_lists_agree():
    """``CommandIsReadOnly`` in StatusForm.pas is the other copy.

    The Pascal one decides when to invalidate a cached netlist; the
    Python one decides whether a command may run at all. A verb that one
    calls a read and the other calls a write is either a stale netlist
    or an unenforced limit, and neither announces itself.
    """
    src = STATUS_FORM.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Function CommandIsReadOnly.*?\nEnd;", src, re.DOTALL)
    assert m, "CommandIsReadOnly is gone from StatusForm.pas"
    pascal = set(re.findall(r"ActionHasPrefix\(Verb,\s*'([a-z_]+)'\)", m.group(0)))
    python = set(READONLY_VERB_PREFIXES)
    assert pascal, "the Pascal prefix scan found nothing; it has changed shape"
    assert pascal == python, (
        f"the two read-only lists have drifted.\n"
        f"  only in Pascal: {sorted(pascal - python)}\n"
        f"  only in Python: {sorted(python - pascal)}")


# --------------------------------------------------------------------------
# SECURITY.md makes claims about this code
# --------------------------------------------------------------------------

SECURITY_MD = REPO_ROOT / "SECURITY.md"


def test_security_md_names_variables_that_exist():
    """A security document that names a wrong flag is worse than silence.

    Someone setting the variable believes a capability is off when it is
    not, and nothing tells them otherwise.
    """
    from eda_agent.ui.windows import UI_AUTOMATION_ENV

    text = SECURITY_MD.read_text(encoding="utf-8", errors="replace")
    for var in (READONLY_ENV, UI_AUTOMATION_ENV):
        assert var in text, (
            f"{var} is enforced in code but SECURITY.md does not mention "
            f"it, so nobody knows the capability can be turned off")

    for claimed in re.findall(r"`(EDA_AGENT_[A-Z_]+)", text):
        assert claimed in {READONLY_ENV, UI_AUTOMATION_ENV}, (
            f"SECURITY.md documents {claimed}, which no code reads. A "
            f"reader setting it would believe something was off.")


def test_security_md_claim_about_the_scripting_family_is_true():
    text = SECURITY_MD.read_text(encoding="utf-8", errors="replace")
    assert "ScriptingSystem" in text
    for verb in ("RunScript", "RunScriptFile", "RunScriptText"):
        assert refuse_process(f"ScriptingSystem:{verb}") is not None, (
            f"SECURITY.md says ScriptingSystem:{verb} is refused, and it "
            f"is not")


def test_security_md_claim_about_the_audit_trail_is_true():
    """It says every command is logged with its response."""
    text = SECURITY_MD.read_text(encoding="utf-8", errors="replace")
    assert "activity.log" in text
    pascal = (REPO_ROOT / "scripts" / "altium" / "Main.pas").read_text(
        encoding="utf-8", errors="replace")
    assert "activity.log" in pascal, (
        "SECURITY.md promises an audit trail that the Pascal no longer "
        "writes")
