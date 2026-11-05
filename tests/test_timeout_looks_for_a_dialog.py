# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A silent bridge must say WHY it is silent, not guess.

Three different things produce identical silence: the polling loop is
gone, the handler is looping, or a modal is blocking the scripting
engine. They need opposite responses. Restarting the loop because it
looked dead throws away whatever a blocked handler was in the middle
of, and it was the standing advice: the timeout named a dead loop and
told the reader to go and check for a dialog themselves.

MEASURED 2026-08-18: a "Comparator Results" box sat unanswered while
every bridge call timed out reporting the loop was probably not
running. It was running. It was waiting for a button.

The dialog reader never touches the IPC that is stuck, so the timeout
can simply look. These tests drive the real timeout path with a bridge
whose workspace is empty, so nothing can answer and the timeout is
genuine rather than simulated.
"""

from __future__ import annotations

import pytest

from eda_agent.bridge.altium_bridge import AltiumBridge, AltiumTimeoutError


class _Process:
    pid = 4242
    exe_path = "X2.EXE"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A bridge pointed at an empty workspace, so every call times out."""
    b = AltiumBridge()
    b.config.workspace_dir = tmp_path
    monkeypatch.setattr(b, "_note_fault", lambda *a, **k: None)
    # No pointer naming another workspace: an earlier test in the session
    # may have written one, and the timeout would then name it instead.
    monkeypatch.setenv("EDA_AGENT_POINTER_FILE", str(tmp_path / "no-pointer.txt"))
    return b


def _timeout(bridge):
    with pytest.raises(AltiumTimeoutError) as caught:
        bridge._poll_response("deadbeef", timeout=0.3)
    return caught.value


def test_a_modal_is_named_instead_of_blaming_the_loop(bridge, monkeypatch):
    """The regression. The advice was wrong in exactly this case."""
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: {
        "dialog_count": 1,
        "summary": "Altium is BLOCKED by a modal. nothing_to_do: "
                   "'Comparator Results (No Differences)'"})

    error = _timeout(bridge)

    assert "DIALOG IS ON SCREEN" in str(error)
    assert "Comparator Results" in str(error)
    assert "probably not running" not in str(error), (
        "with a modal on screen the loop is blocked, not absent, and "
        "saying otherwise sends the reader to restart it")


def test_the_dialog_is_carried_in_the_details_not_only_the_text(
        bridge, monkeypatch):
    """A caller should not have to parse prose to react."""
    probe = {"dialog_count": 1, "summary": "a modal is open"}
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: probe)

    error = _timeout(bridge)

    assert error.details.get("dialogs") == probe


def test_the_message_names_the_tools_that_still_work(bridge, monkeypatch):
    """Both of them read Win32, so they answer while the bridge cannot."""
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: {
        "dialog_count": 1, "summary": "a modal is open"})

    text = str(_timeout(bridge))

    assert "app_list_open_dialogs" in text
    assert "app_press_dialog_button" in text


def test_with_no_dialog_the_original_diagnosis_stands(bridge, monkeypatch):
    """The probe must not soften a genuinely dead loop.

    A check that changed the message either way would make the timeout
    useless in the commonest case, which is that the script really was
    never started.
    """
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: None)

    text = str(_timeout(bridge))

    assert "probably not running" in text
    assert "DIALOG IS ON SCREEN" not in text


def test_a_probe_that_explodes_does_not_replace_the_timeout(
        bridge, monkeypatch):
    """The caller needs the timeout far more than the diagnosis.

    The probe reaches into Win32 and the accessible layer, neither of
    which is guaranteed to behave, so it is wrapped. Asserted by making
    the underlying report raise rather than by trusting the try block
    to be there.
    """
    import eda_agent.ui.dialog_report as report

    def boom(*a, **k):
        raise OSError("the accessible layer fell over")

    monkeypatch.setattr(report, "report", boom)
    monkeypatch.setattr(bridge.process_manager, "get_altium_info",
                        lambda: _Process())

    error = _timeout(bridge)

    assert "No response within" in str(error), (
        "a failed probe must not turn a timeout into something else")
    assert error.details.get("dialogs") is None


def test_the_probe_does_not_use_the_bridge(bridge):
    """The property the whole idea rests on.

    If the probe sent a command it would queue behind the very call
    that is stuck, and would never answer.
    """
    import inspect

    source = inspect.getsource(AltiumBridge._dialog_probe)
    assert "send_command" not in source
    assert "_execute_command" not in source
    assert "dialog_report" in source
