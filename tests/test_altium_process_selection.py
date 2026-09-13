# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""With two Altium processes, act on the one running the script.

find_altium_process returned whichever X2.exe the scan yielded first. A
crashed session leaves a windowless orphan, and when it enumerated first
every UI tool read the orphan's windows: no dialog open, no button to
press, while a modal sat on screen in the real instance. Bridge calls
kept working, because they go through the file channel to whichever
instance runs the script, so half the toolset talked to one Altium and
half to the other.

The rule now: the process owning the bridge's status window wins, then
the only one with a visible window, and otherwise nobody. A guess reads
the wrong process's dialogs, and an empty read from the wrong process
looks exactly like a correct one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from eda_agent.bridge import process_manager as pm
from eda_agent.bridge.process_manager import AltiumProcessManager

_DFM = (Path(__file__).resolve().parents[1] / "scripts" / "altium"
        / "StatusForm.dfm")

ORPHAN, SCRIPTED, OTHER = 1111, 2222, 3333


class _Proc:
    def __init__(self, pid, name="X2.exe"):
        self.info = {"pid": pid, "name": name,
                     "exe": rf"C:\Altium\{name}", "cmdline": [name]}


@pytest.fixture()
def world(monkeypatch):
    """Set the processes and the window owners the manager will see."""
    state = {"pids": [], "form": set(), "visible": set(), "lookups": 0,
             "windows_readable": True}

    def process_iter(_attrs):
        return iter([_Proc(p) for p in state["pids"]]
                    + [_Proc(9999, "notepad.exe")])

    def window_owner_pids(title_prefix=None):
        state["lookups"] += 1
        if not state["windows_readable"]:
            return None
        if title_prefix is None:
            return set(state["visible"])
        assert title_prefix == AltiumProcessManager.STATUS_FORM_TITLE
        return set(state["form"])

    monkeypatch.setattr(pm.psutil, "process_iter", process_iter)
    monkeypatch.setattr(AltiumProcessManager, "_window_owner_pids",
                        staticmethod(window_owner_pids))
    return state


def test_the_reported_case_an_orphan_enumerated_first(world):
    """The orphan comes first in the scan and has no windows. The real
    instance shows the status window. The real instance wins."""
    world["pids"] = [ORPHAN, SCRIPTED]
    world["form"] = {SCRIPTED}
    world["visible"] = {SCRIPTED}

    sel = AltiumProcessManager().select_altium_process()
    assert sel.process is not None and sel.process.pid == SCRIPTED
    assert sel.selected_by == "status_form"


def test_the_status_window_beats_another_windowed_instance(world):
    """Two live instances, one running the bridge. Being visible is not
    enough; running the script is what makes it the target."""
    world["pids"] = [OTHER, SCRIPTED]
    world["form"] = {SCRIPTED}
    world["visible"] = {OTHER, SCRIPTED}

    sel = AltiumProcessManager().select_altium_process()
    assert sel.process.pid == SCRIPTED
    assert sel.selected_by == "status_form"


def test_an_orphan_loses_to_the_only_windowed_instance(world):
    """Bridge not started yet, so no status window anywhere; the orphan
    still cannot be the one showing the user's dialogs."""
    world["pids"] = [ORPHAN, OTHER]
    world["visible"] = {OTHER}

    sel = AltiumProcessManager().select_altium_process()
    assert sel.process.pid == OTHER
    assert sel.selected_by == "only_windowed"


def test_two_indistinguishable_instances_are_refused_not_guessed(world):
    world["pids"] = [OTHER, SCRIPTED]
    world["visible"] = {OTHER, SCRIPTED}

    sel = AltiumProcessManager().select_altium_process()
    assert sel.process is None
    assert sel.selected_by == "ambiguous"
    assert str(OTHER) in sel.reason and str(SCRIPTED) in sel.reason


def test_unreadable_windows_with_two_candidates_are_refused(world):
    world["pids"] = [ORPHAN, SCRIPTED]
    world["windows_readable"] = False

    sel = AltiumProcessManager().select_altium_process()
    assert sel.process is None and sel.selected_by == "ambiguous"
    assert "could not be read" in sel.reason


def test_one_instance_costs_no_window_enumeration(world):
    """The ordinary case must not pay for the rare one."""
    world["pids"] = [SCRIPTED]

    sel = AltiumProcessManager().select_altium_process()
    assert sel.process.pid == SCRIPTED
    assert sel.selected_by == "only_candidate"
    assert world["lookups"] == 0


def test_no_altium_is_none_not_ambiguous(world):
    sel = AltiumProcessManager().select_altium_process()
    assert sel.process is None and sel.candidates == []
    assert sel.selected_by == "none"


def test_every_legacy_entry_point_uses_the_selection(world):
    """find_altium_process, get_altium_info and get_altium_pid are all
    still called. None of them may keep the first-match behaviour."""
    world["pids"] = [ORPHAN, SCRIPTED]
    world["form"] = {SCRIPTED}
    manager = AltiumProcessManager()
    assert manager.find_altium_process().pid == SCRIPTED
    assert manager.get_altium_info().pid == SCRIPTED
    assert manager.get_altium_pid() == SCRIPTED


def test_status_reports_the_ambiguity_instead_of_a_pid(world, tmp_path):
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import configure

    configure(workspace_dir=tmp_path)
    world["pids"] = [OTHER, SCRIPTED]
    world["visible"] = {OTHER, SCRIPTED}

    status = AltiumBridge().get_altium_status()
    assert status["running"] is True
    assert status["pid"] is None
    assert status["ambiguous"] is True
    assert status["candidate_pids"] == [OTHER, SCRIPTED]
    assert status["candidate_count"] == 2


def test_status_names_how_a_pid_was_chosen(world, tmp_path):
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import configure

    configure(workspace_dir=tmp_path)
    world["pids"] = [ORPHAN, SCRIPTED]
    world["form"] = {SCRIPTED}

    status = AltiumBridge().get_altium_status()
    assert status["pid"] == SCRIPTED
    assert status["selected_by"] == "status_form"
    assert "ambiguous" not in status


def test_ui_tools_refuse_an_ambiguous_status(monkeypatch):
    """_altium_pid is the one door every UI tool goes through."""
    import eda_agent.bridge as bridge_pkg
    from eda_agent.tools import uiauto
    from eda_agent.ui import windows

    class _Bridge:
        def get_altium_status(self):
            return {"running": True, "pid": None, "ambiguous": True,
                    "reason": "2 Altium processes are running",
                    "candidate_pids": [OTHER, SCRIPTED]}

    monkeypatch.setattr(windows, "automation_enabled", lambda: True)
    monkeypatch.setattr(bridge_pkg, "get_bridge", lambda: _Bridge())

    pid, problem = uiauto._altium_pid()
    assert pid is None
    assert problem["ok"] is False
    assert problem["candidate_pids"] == [OTHER, SCRIPTED]
    assert "not running" not in problem["reason"], (
        "an ambiguous status was reported as Altium not running")


def test_the_title_matches_the_form_the_script_opens():
    """The selection keys on a caption the Pascal side owns. If the form
    is renamed and this constant is not, every multi-instance machine
    silently falls back to guessing by visibility."""
    text = _DFM.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^\s*Caption = '([^']*)'", text, re.M)
    assert m, "no Caption found in StatusForm.dfm"
    assert m.group(1).startswith(AltiumProcessManager.STATUS_FORM_TITLE)
