# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every unit-level Pascal variable is either per-request or session state.

Per-request variables are reset in ResetRequestState / ResetItemState
(StatusForm.pas), or by the pump before dispatch. Session variables are
named below. A variable declared later that is in neither list fails
here, so it cannot silently carry one request's state into the next.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import functions, strip_comments

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNITS = ("Main", "Utils", "Application", "Project", "Library", "PCBGeneric",
         "PCB", "Generic", "Audit", "StatusForm", "Dispatcher")

# Cleared by the pump before dispatch (StatusForm.ProcessSingleRequest), not by
# ResetRequestState: restoring the session's document can note a next step.
CLEARED_BY_THE_PUMP = {"LastErrorId", "LastErrorCode", "LastErrorMsg", "_NextStepStr"}

# Released at the start of every request by SchReleaseHeld.
HELD_SCHEMATIC_EDIT = {"SchHeld", "SchHeldDoc", "SchHeldPath"}

SESSION = {
    # Workspace, pump and polling configuration.
    "WorkspaceDir", "Running", "PollIntervalActiveMs", "PollIntervalIdleMs",
    "IdleThreshold", "AutoShutdownMs", "ConfiguredShutdownMs", "YieldIterations", "YieldEveryNActive",
    "PumpStopPath", "PumpIdleCount", "PumpLastActivityMs", "PumpInterval",
    "PumpInTick", "PumpFinalised", "PumpQuitting", "PumpTicked",
    # Config tab settings.
    "SavingOff", "ValueFormat", "ValueUnit", "EditsMode", "DeletingMode",
    "ClosingMode", "UiAutomationMode", "KeepInViewMode", "DialogMode", "ChecksOff",
    # Save passes and the documents edited this session.
    "SaveAttempts", "LibsSkippedClean", "SkippedLibNames", "EditedDocs",
    "RunOutJobsOpened", "SavePassSeen", "SaveScope", "CleanLeftAlone",
    # Caches and counters kept across requests.
    "LastCompileTick", "LastCompiledProject", "WriteSerial", "CompiledAtSerial", "DocOwners", "CastErrorCount",
    "LastCreatedLibComponent", "LastCreatedLibComponentName", "LastCreatedLibDocAddr",
    "JsonIdxDoc0", "JsonIdxDoc1", "JsonIdxDoc2", "JsonIdxDoc3",
    "JsonIdx0", "JsonIdx1", "JsonIdx2", "JsonIdx3", "JsonIdxNext",
    "JsonNestedSeen",
    # Session slots and queue notes between callers.
    "SlotSession", "SlotStartTick", "QueueNoteTick",
    # The tool call holding the turn, and when its last command finished.
    "HoldCall", "HoldStartTick", "LastDoneTick", "JustPicked",
    # Dashboard.
    "HidePingsFlag", "OnlySlowFlag", "AlwaysOnTopFlag", "PausedFlag",
    "RenewRequested", "SpinnerFrame", "InFlightCommand", "InFlightStartMs",
    "InFlightActive", "FilterText", "PerfNames", "PerfCountStrs",
    "PerfTotalStrs", "PerfMaxStrs", "LastActivityTick", "LastPingMs",
    "OpenWebEnabled", "SavedHintHidePause", "StatusStartTick",
    "StatusRequestCount", "StatusLastCommand", "StatusTotalAltiumMs",
}


def _unit_globals() -> set[str]:
    found: set[str] = set()
    for unit in UNITS:
        code = strip_comments((SCRIPTS / f"{unit}.pas").read_text(
            encoding="utf-8", errors="replace"))
        for block in re.finditer(
                r"(?ms)^Var\b(.*?)^(Function|Procedure|Const|Var|Type|Begin)\b", code):
            if block.group(2) == "Begin":
                continue  # a routine's own locals
            for names in re.findall(r"^\s*([\w ,]+?)\s*:\s*[\w.]+\s*;",
                                    block.group(1), re.M):
                found.update(n.strip() for n in names.split(","))
    assert len(found) > 50, "the declaration scan found almost nothing"
    return found


def _reset_by_dispatch() -> set[str]:
    status = functions(strip_comments((SCRIPTS / "StatusForm.pas").read_text(
        encoding="utf-8", errors="replace")))
    utils = functions(strip_comments((SCRIPTS / "Utils.pas").read_text(
        encoding="utf-8", errors="replace")))
    body = (status["ResetRequestState"] + status["ResetItemState"]
            + utils["ResetPropertyDiag"])
    return set(re.findall(r"^\s*(\w+)\s*:=", body, re.M))


def test_every_unit_variable_is_classified():
    known = (_reset_by_dispatch() | CLEARED_BY_THE_PUMP | HELD_SCHEMATIC_EDIT
             | SESSION)
    unclassified = sorted(_unit_globals() - known)
    assert not unclassified, (
        f"{unclassified}: reset these in ResetRequestState or ResetItemState "
        "if they describe one request, or add them to SESSION here if they "
        "are meant to live across requests")


def test_the_lists_name_only_variables_that_exist():
    stale = sorted((SESSION | CLEARED_BY_THE_PUMP | HELD_SCHEMATIC_EDIT)
                   - _unit_globals())
    assert not stale, f"{stale} are no longer declared; drop them from the lists"


def test_dispatch_resets_before_any_handler_runs():
    status = functions(strip_comments((SCRIPTS / "StatusForm.pas").read_text(
        encoding="utf-8", errors="replace")))
    command = status["ProcessCommand"]
    assert command.index("ResetRequestState(0);") < command.index("ProcessOne(")
    one = status["ProcessOne"]
    assert one.index("ResetItemState(0);") < one.index("Case Category Of")
