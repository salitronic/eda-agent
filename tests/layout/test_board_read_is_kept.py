# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A live board read is used again while the board's stamp says nothing
changed: a full read of a dense board takes most of a minute, and a
routing session reads the same board several times in a row.

The stamp (pcb.get_layout_model, section stamp) counts the commands that
did more than look since the script's loop started, and gives the board's
object counts and every part's place.
"""

from __future__ import annotations

import pytest

from eda_agent.layout import read_altium as ra

from .test_read_altium import FILE, _replies


class _Bridge:
    def __init__(self):
        self.send_sections, self.calls = _replies()
        self.stamp = {"file": FILE, "loop": 7, "serial": 0, "counts": [1, 0, 1, 2, 2, 1, 0, 2, 0],
                      "parts": [[1500000, 2000000, 90.0, 1]]}
        self.bump_during_read = False

    def send_command(self, cmd, params, timeout=None):
        if params.get("section") == "stamp":
            return dict(self.stamp)
        if cmd != ra.COMMAND:
            return {"layers": []}
        if self.bump_during_read and params.get("section") == "copper":
            self.stamp["serial"] += 1
        return self.send_sections(cmd, params)

    def full_reads(self):
        return sum(1 for c in self.calls if c.get("section") == "board")


@pytest.fixture
def bridge(monkeypatch):
    b = _Bridge()
    monkeypatch.setattr("eda_agent.bridge.altium_bridge.get_bridge", lambda: b)
    monkeypatch.setattr(ra, "_READS", {})
    return b


def test_an_unchanged_board_is_read_once(bridge):
    first = ra.read_live_board(FILE)
    again = ra.read_live_board(FILE)
    assert bridge.full_reads() == 1 and again.meta["read"] == "kept"
    assert [p.key for p in again.pads] == [p.key for p in first.pads]
    # Each caller has its own copy: one changing it changes no other.
    again.tracks.clear()
    assert ra.read_live_board(FILE).tracks


@pytest.mark.parametrize("change", [
    lambda s: s.update(serial=s["serial"] + 1),          # a command wrote
    lambda s: s.update(loop=8),                          # the loop restarted
    lambda s: s["counts"].__setitem__(0, 2),             # a track was drawn
    lambda s: s["parts"][0].__setitem__(0, 1600000),     # a part was moved
])
def test_any_change_the_stamp_sees_reads_the_board_again(bridge, change):
    ra.read_live_board(FILE)
    change(bridge.stamp)
    assert "read" not in ra.read_live_board(FILE).meta
    assert bridge.full_reads() == 2


def test_a_read_a_command_wrote_during_is_not_kept(bridge):
    bridge.bump_during_read = True
    ra.read_live_board(FILE)
    bridge.bump_during_read = False
    ra.read_live_board(FILE)
    assert bridge.full_reads() == 2


def test_fresh_reads_it_again_and_a_wrong_board_is_refused(bridge):
    ra.read_live_board(FILE)
    ra.read_live_board(FILE, fresh=True)
    assert bridge.full_reads() == 2
    bridge.stamp["file"] = r"C:\other\client.PcbDoc"
    with pytest.raises(ra.WrongBoard):
        ra.read_live_board(FILE)
    assert bridge.full_reads() == 2


def test_a_script_without_the_stamp_reads_every_time(bridge):
    bridge.stamp = {"error": "BAD_SECTION"}
    ra.read_live_board(FILE)
    ra.read_live_board(FILE)
    assert bridge.full_reads() == 2


def test_the_script_counts_every_command_that_does_more_than_look():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "scripts" / "altium"
    form = (root / "StatusForm.pas").read_text(encoding="utf-8")
    assert "If Not CommandOnlyLooks(Command) Then CommandSerial := CommandSerial + 1;" in form
    pcb = (root / "PCB.pas").read_text(encoding="utf-8")
    assert "If Section = 'stamp' Then Body := LmStampSection(Board);" in pcb
    # The board read's own stack read goes by the name that only reads.
    assert "'get_physical_stack':      Result := PCB_PhysicalStack('{}', RequestId);" in pcb
    from eda_agent.safety import command_only_looks
    assert command_only_looks("pcb.get_physical_stack")
    assert command_only_looks("pcb.get_layout_model")
