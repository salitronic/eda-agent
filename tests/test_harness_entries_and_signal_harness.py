# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Harness connectors with entries, signal harnesses, and a connector move.

#294: a connector came out empty, no tool drew a signal harness or could
find one, eHarnessEntry was no query type, and Location written on a
connector read back moved while nothing moved on the sheet.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.pascal_source import functions, strip_comments
from tests.test_metric_grid_coordinates import _tools

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


@pytest.fixture(scope="module")
def src():
    return strip_comments(_PAS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fns(src):
    return functions(src)


def test_entries_and_signal_harnesses_are_object_types(src):
    assert "N = 'harnessentry'    Then Result := eHarnessEntry" in src
    assert "N = 'signalharness'   Then Result := eSignalHarness" in src
    assert "'eHarnessEntry, eSignalHarness, '" in src


def test_a_signal_harness_has_vertices_and_no_text(fns):
    assert "(Id = eSignalHarness)" in fns["SchHasVertexArray"]


def test_an_entry_is_placed_then_put_back_where_asked(fns):
    body = fns["AddHarnessEntries"]
    add = body.index("Harness.AddAndPositionSchObject(Entry);")
    assert body.index("Try Entry.Side := WantSide;", add) > add
    assert body.index("Try Entry.DistanceFromTop := MilsToCoordF(Dist);", add) > add
    assert "If (GotSide = WantSide) And (Abs(GotDist - Dist) < 0.01) Then Inc(Placed);" in body


def test_the_connector_adds_its_entries_inside_the_edit(fns):
    body = fns["Gen_PlaceHarnessConnector"]
    reg = body.index("SchRegisterObject(SchDoc, Harness);")
    add = body.index("AddHarnessEntries(Harness, EntryNames")
    assert reg < add < body.index("SchEndEdit(SchDoc, 'Edit');")
    assert '"entries_placed":' in body


def test_a_connector_moves_with_its_entries(fns):
    for name in ("SetSchProperty", "ApplySetProperties"):
        body = fns[name]
        assert "Else If Obj.ObjectId = eHarnessConnector Then" in body, name
        assert "MoveHarnessConnector(Obj, Loc.X, Loc.Y)" in body, name


def test_the_signal_harness_is_built_like_a_wire_and_read_back(fns):
    body = fns["Gen_PlaceSignalHarness"]
    assert "SchServer.SchObjectFactory(eSignalHarness, eCreate_Default)" in body
    assert "Harness.SetState_Vertex(I + 1," in body
    assert 'BoolToJsonStr(Count = Xs.Count)' in body


def test_the_tools_send_entries_and_vertices(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["sch_place_harness_connector"](
        x=1000, y=2000, entries=["USB+", "USB-"], entry_side="right",
        entry_pitch=98.425))
    asyncio.run(tools["sch_place_signal_harness"](
        vertices=[[1500, 2700], [2000.5, 2700]]))
    assert sent[0][1]["entries"] == "USB+;USB-"
    assert sent[0][1]["entry_side"] == "right"
    assert sent[0][1]["entry_pitch"] == "98.425"
    assert sent[1] == ("generic.place_signal_harness",
                       {"vertices": "1500,2700,2000.5,2700"})


@pytest.mark.parametrize("kwargs,words", [
    (dict(entries=["A;B"]), "';'"),
    (dict(entries=["A"], entry_side="up"), "left, right"),
    (dict(entries=["A"], entry_pitch=0), "above 0"),
])
def test_a_bad_entry_request_is_refused_before_sending(monkeypatch, kwargs, words):
    tools, sent = _tools(monkeypatch)
    out = asyncio.run(tools["sch_place_harness_connector"](x=0, y=0, **kwargs))
    assert sent == [] and words in out["reason"]


def test_a_one_point_harness_is_refused(monkeypatch):
    tools, sent = _tools(monkeypatch)
    out = asyncio.run(tools["sch_place_signal_harness"](vertices=[[0, 0]]))
    assert sent == [] and out["success"] is False


def test_the_type_label_follows_the_connector(fns):
    body = fns["MoveHarnessConnector"]
    move = body.index("Harness.MoveToXY(X, Y);")
    assert body.index("LabelWas := TypeLabel.Location;") < move
    after = body[move:]
    assert "TypeLabel.Location := Point(X + (LabelWas.X - Was.X), Y + (LabelWas.Y - Was.Y));" in after
    assert "LabelNow" not in body
    assert "MoveByXY" not in body


def test_each_entry_is_put_on_the_connector_edge(fns):
    # The add left entries stacked below the sheet origin (2400, -50 on a
    # connector at 2000, 3000); the edge point is set from side and distance.
    body = fns["AddHarnessEntries"]
    add = body.index("Harness.AddAndPositionSchObject(Entry);")
    assert body.index("Entry.Location := Point(MilsToCoordF(EdgeX), MilsToCoordF(EdgeY))", add) > add
    assert "If WantSide = eLeftSide Then EdgeY := EdgeY - Dist" in body


def test_the_type_label_starts_under_the_connector(fns):
    body = fns["Gen_PlaceHarnessConnector"]
    assert "TypeLabel.Location := Point(MilsToCoordF(X), MilsToCoordF(Y - H - 100));" in body
