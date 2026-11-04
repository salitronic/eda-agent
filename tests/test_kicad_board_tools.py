# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The KiCad board tools over a fake board that stores real item messages.

The fake answers like KiCad's API (create, update, delete, commit) but
keeps the items in memory, so the tools' builders and patches are the
real ones and what comes back is read from the messages they built.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json

import pytest
from mcp.server.fastmcp import FastMCP

from eda_agent.bridge import kicad_api as api

_ids = itertools.count(1)


class FakeBoard:
    def __init__(self):
        self.store: dict[str, api.Item] = {}
        self.commits = 0
        self.saved = False
        self.keep_width = None  # a width KiCad "keeps" whatever is asked

    def _new_id(self, item: api.Item) -> api.Item:
        m = item.message.copy()
        m.set_msg(4 if item.kind == "shape" else 1, api.kiid(f"id-{next(_ids)}"))
        return api.Item(item.type_name, m)

    def items(self, *kinds):
        return [i for i in self.store.values() if not kinds or i.kind in kinds]

    def items_by_id(self, ids):
        return [self.store[i] for i in ids if i in self.store]

    def items_on_nets(self, nets, *kinds):
        return [i for i in self.items(*kinds) if i.read().get("net") in nets]

    def selection(self, *kinds):
        return []

    @contextlib.contextmanager
    def commit(self, message=""):
        self.commits += 1
        yield

    def create(self, items):
        out = []
        for it in items:
            made = self._new_id(it)
            self.store[made.id] = made
            out.append({"ok": True, "status": "ok", "item": made.read()})
        return out

    def update(self, items):
        out = []
        for it in items:
            if self.keep_width is not None and it.kind == "track":
                it = api.Item(it.type_name, it.message.copy().set_msg(4, api.dist(self.keep_width)))
            self.store[it.id] = it
            out.append({"ok": True, "status": "ok", "item": it.read()})
        return out

    def delete(self, ids):
        out = []
        for i in ids:
            gone = self.store.pop(i, None) is not None
            out.append({"id": i, "ok": gone, "status": "ok" if gone else "no such item"})
        return out

    def layer(self, name):
        return api.layer_number(name)

    def save(self):
        self.saved = True

    def refill_zones(self, wait_s=15.0):
        return True


@pytest.fixture
def board(monkeypatch):
    import eda_agent.tools.kicad_board as kb
    fake = FakeBoard()
    monkeypatch.setattr(kb, "_board", lambda: fake)
    return fake


@pytest.fixture(scope="module")
def mcp():
    from eda_agent.tools.kicad_board import register_kicad_board_tools
    m = FastMCP("t")
    register_kicad_board_tools(m)
    return m


def _call(mcp, name, **kwargs):
    r = asyncio.run(mcp.call_tool(name, kwargs))
    if isinstance(r, tuple):
        r = r[0]
    return json.loads(r[0].text)


def test_a_polyline_is_one_commit_and_a_bad_item_is_named(mcp, board):
    r = _call(mcp, "kicad_place_tracks", items=[
        {"points": [[0, 0], [10, 0], [10, 5]], "net": "GND"},
        {"points": [[1, 1]]}], width_mm=0.3)
    assert r["succeeded"] == 1 and r["failed_items"] == [1]
    assert len(r["items"][0]["created"]) == 2
    assert board.commits == 1
    widths = {t.read()["width_mm"] for t in board.items("track")}
    assert widths == {0.3}


def test_modify_reports_a_value_kicad_did_not_keep(mcp, board):
    made = _call(mcp, "kicad_place_tracks", items=[{"points": [[0, 0], [5, 0]]}], width_mm=0.2)
    tid = made["items"][0]["id"]
    ok = _call(mcp, "kicad_modify_items", items=[{"id": tid, "width_mm": 0.5, "layer": "B.Cu"}])
    assert ok["ok"] and ok["items"][0]["width_mm"] == 0.5 and ok["items"][0]["layer"] == "B.Cu"
    board.keep_width = 0.25
    kept = _call(mcp, "kicad_modify_items", items=[{"id": tid, "width_mm": 0.8}])
    assert kept["ok"] is False and "width_mm" in kept["items"][0]["reason"]


def test_modify_where_changes_every_match(mcp, board):
    _call(mcp, "kicad_place_vias", items=[{"x_mm": 1, "y_mm": 1}, {"x_mm": 2, "y_mm": 1}],
          net="GND")
    r = _call(mcp, "kicad_modify_items", where={"kinds": ["via"]},
              set={"diameter_mm": 1.0, "tented": True})
    assert r["ok"] and r["count"] == 2
    assert all(i["diameter_mm"] == 1.0 and i["tented"]["front"] is True for i in r["items"])


def test_delete_refuses_an_empty_filter_and_checks_the_rest(mcp, board):
    assert _call(mcp, "kicad_delete_items", where={})["ok"] is False
    _call(mcp, "kicad_place_vias", items=[{"x_mm": 3, "y_mm": 3}], net="SIG")
    r = _call(mcp, "kicad_delete_items", where={"nets": ["SIG"], "kinds": ["via"]})
    assert r["ok"] and r["count"] == 1 and not board.items("via")


def test_a_keepout_is_a_rule_area(mcp, board):
    r = _call(mcp, "kicad_place_zones", items=[{
        "points": [[0, 0], [5, 0], [5, 5]], "keepout": {"copper": True, "vias": False},
        "layers": ["F.Cu", "B.Cu"]}])
    item = r["items"][0]
    assert item["zone_type"] == "rule_area" and item["keepout"]["vias"] is False
    assert "refilled" not in r


def test_copies_are_offset_and_get_new_ids(mcp, board):
    made = _call(mcp, "kicad_place_tracks", items=[{"points": [[0, 0], [1, 0]]}])
    tid = made["items"][0]["id"]
    r = _call(mcp, "kicad_copy_items", ids=[tid], dx_mm=2, count=2)
    starts = sorted(i["start_mm"][0] for i in r["items"])
    assert starts == [2.0, 4.0] and tid not in {i["id"] for i in r["items"]}


def test_cleanup_finds_zero_length_and_duplicates(mcp, board):
    _call(mcp, "kicad_place_tracks", items=[{"points": [[0, 0], [0, 0]]},
                                            {"points": [[0, 0], [3, 0]]},
                                            {"points": [[3, 0], [0, 0]]}])
    dry = _call(mcp, "kicad_cleanup_tracks", dry_run=True)
    assert dry["found"] == 2 and dry["deleted"] == 0
    done = _call(mcp, "kicad_cleanup_tracks")
    assert done["deleted"] == 2 and len(board.items("track")) == 1


def test_arc_from_centre_and_angles():
    from eda_agent.tools.kicad_board import _arc_points
    s, m, e = _arc_points({"center_mm": [0, 0], "radius_mm": 1, "start_deg": 0, "end_deg": 90})
    assert s == (1.0, 0.0) and e == (0.0, -1.0)
    assert abs(m[0] - 0.707107) < 1e-5 and abs(m[1] + 0.707107) < 1e-5


def test_clearance_between_two_vias(board):
    from eda_agent.tools.kicad_board import _clearance
    a = api.Item("Via", api.make_via((0, 0), 0.6, 0.3).message)
    b = api.Item("Via", api.make_via((2, 0), 0.6, 0.3).message)
    assert _clearance(board, a, b) == pytest.approx(1.4)


def test_translate_and_rotate_a_track():
    t = api.make_track((1, 0), (2, 0), 0.2, 3)
    moved = api.translate(t, 1, 1).read()
    assert moved["start_mm"] == [2.0, 1.0] and moved["end_mm"] == [3.0, 1.0]
    turned = api.rotate_about(t, 0, 0, 90).read()
    assert turned["start_mm"] == pytest.approx([0.0, -1.0])


def test_patch_names_a_field_it_cannot_set():
    t = api.make_track((0, 0), (1, 0), 0.2, 3)
    with pytest.raises(ValueError, match="radius_mm cannot be set on a track"):
        api.patch(t, {"radius_mm": 1})


def test_net_class_delete_goes_to_the_project_file(mcp, board, monkeypatch, tmp_path):
    """Deleting through the API crashes KiCad 10.0.1, so it never does."""
    import eda_agent.tools.kicad_board as kb
    pro = tmp_path / "b.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {
        "classes": [{"name": "Default"}, {"name": "HV"}],
        "netclass_patterns": [{"netclass": "HV", "pattern": "VBUS"},
                              {"netclass": "Default", "pattern": "X"}]}}), encoding="utf-8")
    monkeypatch.setattr(kb, "_project_files", lambda: (str(pro), str(tmp_path / "b.kicad_dru")))
    sent = []
    board.net_classes = lambda: [{"name": "Default"}, {"name": "HV"}]
    board.set_net_classes = lambda specs: sent.append(specs)
    r = _call(mcp, "kicad_set_net_classes", delete=["HV", "NOPE"])
    assert sent == [] and r["items"][0]["ok"] and r["items"][1]["ok"] is False
    data = json.loads(pro.read_text(encoding="utf-8"))["net_settings"]
    assert [c["name"] for c in data["classes"]] == ["Default"]
    assert data["netclass_patterns"] == [{"netclass": "Default", "pattern": "X"}]


def test_rule_text_round_trips(tmp_path):
    from eda_agent.tools.kicad_board import _drop_rules, _read_rules, _rule_text, _write_rules
    path = str(tmp_path / "b.kicad_dru")
    text = "(version 1)\n# a comment\n\n" + _rule_text(
        {"name": "hv", "condition": "A.NetClass == 'HV'", "layer": "outer",
         "constraints": ["clearance (min 1mm)"]}) + "\n"
    _write_rules(path, text)
    rules = _read_rules(path)
    assert rules[0]["name"] == "hv" and rules[0]["constraints"] == ["clearance (min 1mm)"]
    assert rules[0]["condition"] == "A.NetClass == 'HV'"
    _write_rules(path, _drop_rules(text, {"hv"}))
    assert _read_rules(path) == []
