# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A call stops early for a dialog only when the dialog stays.

The bridge looks for a blocking dialog every half second. A dialog seen
on one look and gone on the next (a progress window Altium closes by
itself) must not stop the call; the same dialog on two looks in a row
must, without waiting out the timeout.
"""
from __future__ import annotations

import time

import pytest

from eda_agent.bridge import altium_bridge
from eda_agent.bridge.altium_bridge import AltiumBridge, AltiumTimeoutError

_ECO = {"blocked": True, "dialog_count": 1, "summary": "Altium is BLOCKED by a modal.",
        "dialogs": [{"hwnd": 7, "title": "Engineering Change Order"}]}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    b = AltiumBridge()
    b.config.workspace_dir = tmp_path
    monkeypatch.setattr(b, "_note_fault", lambda *a, **k: None)
    monkeypatch.setattr(b, "_recover_script_crash", lambda *a, **k: None)
    monkeypatch.setenv("EDA_AGENT_POINTER_FILE", str(tmp_path / "no-pointer.txt"))
    monkeypatch.setattr(altium_bridge, "_DIALOG_PROBE_AFTER", 0.0)
    monkeypatch.setattr(altium_bridge, "_DIALOG_PROBE_EVERY", 0.02)
    return b


def test_a_dialog_that_stays_stops_the_call_early(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: _ECO)
    started = time.monotonic()
    with pytest.raises(AltiumTimeoutError) as caught:
        bridge._poll_response("deadbeef", timeout=5.0)
    assert time.monotonic() - started < 1.0
    assert "showing a modal dialog" in str(caught.value)


def test_a_dialog_seen_once_does_not(bridge, monkeypatch):
    looks = iter([_ECO])
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: next(looks, None))
    with pytest.raises(AltiumTimeoutError) as caught:
        bridge._poll_response("deadbeef", timeout=0.4)
    assert "showing a modal dialog" not in str(caught.value)


def test_two_different_dialogs_are_not_one_that_stayed():
    other = dict(_ECO, dialogs=[{"hwnd": 8, "title": "Confirm"}])
    assert altium_bridge._blocking_signature(_ECO) != altium_bridge._blocking_signature(other)
    assert altium_bridge._blocking_signature(None) == ""
    assert altium_bridge._blocking_signature({"blocked": False}) == ""
