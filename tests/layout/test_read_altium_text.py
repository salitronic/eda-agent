# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tracks and arcs from Altium's text of the board, as the script's copper
read sends them; and the read falls back to the script whenever the text
cannot give them exactly."""

from __future__ import annotations

import pytest

from eda_agent.layout import read_altium as ra
from eda_agent.layout import read_altium_text as rt

from .test_read_altium import FILE, _replies

TEXT = "\r\n".join([
    "|RECORD=Board|FILENAME=x",
    "|RECORD=Net|ID=0|NAME=GND",
    "|RECORD=Net|ID=1|NAME=CLK",
    "|RECORD=Component|ID=0|X=100mil|Y=200mil",
    "|RECORD=Text|COMPONENT=0|DESIGNATOR=True|TEXT=U1",
    "|RECORD=Text|COMPONENT=0|COMMENT=True|TEXT=MCU",
    "|RECORD=Polygon|ID=0|NET=0|NAME=71,78,68|LAYER=BOTTOM",
    "|RECORD=Track|NET=1|LAYER=TOP|X1=61059.1696mil|Y1=-0.5mil|X2=10mil|Y2=0mil|WIDTH=3.937mil",
    "|RECORD=Track|POLYGON=0|LAYER=BOTTOM|X1=1mil|Y1=2mil|X2=3mil|Y2=4mil|WIDTH=5mil",
    "|RECORD=Track|COMPONENT=0|LAYER=TOPOVERLAY|X1=0mil|Y1=0mil|X2=1mil|Y2=0mil|WIDTH=7.874mil",
    "|RECORD=Track|KEEPOUT=TRUE|LAYER=KEEPOUT|X1=0mil|Y1=0mil|X2=1mil|Y2=0mil|WIDTH=10mil",
    "|RECORD=Arc|LAYER=MECHANICAL17|LOCATION.X=1mil|LOCATION.Y=2mil|RADIUS=3mil"
    "|STARTANGLE= 0.00000000000000E+0000|ENDANGLE= 1.15482258462412E+0002|WIDTH=1mil",
    "",
])


def test_tracks_and_arcs_are_the_script_entries():
    got = rt.tracks_and_arcs(TEXT)
    assert got[0] == {"k": "track", "v": [610591696, -5000, 100000, 0, 39370], "layer": "TopLayer",
                      "net": "CLK", "comp": "", "keepout": False, "in_polygon": False,
                      "pour": "", "pour_net": ""}
    poured = got[1]
    assert (poured["in_polygon"], poured["pour"], poured["pour_net"], poured["net"]) == (
        True, "GND", "GND", "")
    assert got[2]["comp"] == "U1" and got[2]["layer"] == "TopOverlay"
    assert got[3]["keepout"] and got[3]["layer"] == "KeepOutLayer"
    arc = got[4]
    assert arc["layer"] == "Mechanical17" and arc["v"][:3] == [10000, 20000, 30000]
    assert arc["v"][3] == 0 and isinstance(arc["v"][3], int)
    assert arc["v"][4] == pytest.approx(115.482258462412, abs=1e-12)


@pytest.mark.parametrize("value", ["1.23456mil", "1mm", "1e3mil", ""])
def test_a_length_that_is_not_whole_internal_units_is_refused(value):
    with pytest.raises(rt.TextUnusable):
        rt.length(value)


@pytest.mark.parametrize("broken", [
    "|RECORD=Track|NET=9|LAYER=TOP|X1=0mil|Y1=0mil|X2=1mil|Y2=0mil|WIDTH=1mil",
    "|RECORD=Track|LAYER=SOMENEWLAYER|X1=0mil|Y1=0mil|X2=1mil|Y2=0mil|WIDTH=1mil",
    "|RECORD=Track|POLYGON=-2|LAYER=TOP|X1=0mil|Y1=0mil|X2=1mil|Y2=0mil|WIDTH=1mil",
    "|RECORD=Net|ID=5|NAME=VCC\xb1",
])
def test_what_the_text_cannot_give_exactly_is_refused(broken):
    with pytest.raises(rt.TextUnusable):
        rt.tracks_and_arcs(TEXT + broken + "\r\n")


def _bridge_with_text(tmp_path, text, monkeypatch):
    send, calls = _replies()
    path = tmp_path / "board_text.txt"
    path.write_bytes(text.encode("latin-1"))

    def wrapped(cmd, params):
        if params.get("section") == "text":
            return {"path": str(path), "problem": ""}
        if params.get("section") == "copper" and params.get("kinds"):
            calls.append(params)
            return {"copper": [], "count": 0, "more": False}
        return send(cmd, params)
    return wrapped, calls, path


def test_a_board_reads_its_tracks_from_the_text_and_deletes_the_file(tmp_path, monkeypatch):
    send, calls, path = _bridge_with_text(tmp_path, TEXT, monkeypatch)
    board = ra.read_board(send, expect_file=FILE)
    assert board.meta["copper_read"] == "text"
    assert any(t.net == "CLK" for t in board.tracks)
    assert not path.exists()
    asked = [c["kinds"] for c in calls if c.get("kinds")]
    assert asked == [ra._SCRIPT_KINDS] and "grouped" in asked[0] and "track" not in asked[0]


def test_text_it_cannot_use_reads_all_copper_through_the_script(tmp_path, monkeypatch):
    send, calls, path = _bridge_with_text(tmp_path, TEXT + "|RECORD=Track|LAYER=WHAT|\r\n", monkeypatch)
    board = ra.read_board(send, expect_file=FILE)
    assert board.meta["copper_read"] == "script"
    assert not path.exists()


def test_an_older_script_without_the_text_reads_as_before():
    send, calls = _replies()
    board = ra.read_board(send, expect_file=FILE)
    assert board.meta["copper_read"] == "script"
