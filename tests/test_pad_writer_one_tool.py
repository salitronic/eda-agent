# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_set_pad_properties is the one advertised pad writer.

It picks pads by name, by the editor's selection, or by the id the read
gives. Items that all carry an id go to the id writer as ONE call (every
row checked first, one walk, all or nothing); items by name run one by
one as before. lib_set_pad_geometry stays callable by name but is not
advertised, so an agent finds one pad writer, not two.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.server import mcp
from eda_agent.tools import library


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


@pytest.fixture
def bridge(monkeypatch):
    b = _Bridge()
    monkeypatch.setattr(library, "get_bridge", lambda: b)
    return b


def _call(**kw):
    out = asyncio.run(mcp.call_tool("lib_set_pad_properties", kw))
    payload = out[1] if isinstance(out, tuple) else out
    if isinstance(payload, list):
        payload = json.loads(payload[0].text)
    return payload


def test_items_by_id_are_one_call_to_the_id_writer(bridge):
    out = _call(footprint_name="F", items=[
        {"id": "A1", "new_name": "5", "x_mm": 1.0, "y_mm": 2.0},
        {"id": "A2", "new_name": "7"}])
    assert out == {"success": True}
    assert len(bridge.sent) == 1
    command, params = bridge.sent[0]
    assert command == "library.set_pad_geometry"
    assert params["footprint_name"] == "F"
    assert params["rows"] == "id=A1;x_mm=1;y_mm=2;new_name=5~~id=A2;new_name=7"


def test_one_pad_by_id_uses_the_id_writer(bridge):
    _call(footprint_name="F", id="A1", x_size=40)
    assert bridge.sent == [("library.set_pad_geometry",
                            {"footprint_name": "F", "rows": "id=A1;x_size=40",
                             "library_path": ""})]


def test_items_by_name_still_run_one_by_one(bridge):
    out = _call(footprint_name="F", items=[{"pad_name": "1", "x_size": 40},
                                           {"pad_name": "2", "x_size": 50}])
    assert out["bulk"] is True and out["succeeded"] == 2
    assert [c for c, _ in bridge.sent] == ["library.set_pad_properties"] * 2
    assert [p["pad_name"] for _, p in bridge.sent] == ["1", "2"]


@pytest.mark.parametrize("items, words", [
    ([{"id": "A1", "x_size": 40}, {"pad_name": "2", "x_size": 50}],
     "every item an id, or none"),
    ([{"id": "A1", "pad_name": "1", "x_size": 40}], "not both"),
    ([{"id": "A1", "x_size": 40, "footprint_name": "G"},
      {"id": "A2", "x_size": 40}], "footprint_name differs"),
])
def test_a_list_that_mixes_ways_of_naming_is_refused_unwritten(bridge, items, words):
    out = _call(footprint_name="F", items=items)
    assert out["success"] is False and words in out["error"]
    assert bridge.sent == []


def test_the_position_guard_belongs_to_a_pad_named_by_id(bridge):
    out = _call(footprint_name="F", pad_name="1", x_mm=1.0)
    assert out["success"] is False and "x / y" in out["error"]
    assert bridge.sent == []


def test_one_pad_writer_is_advertised_and_the_id_writer_stays_reachable():
    advertised = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "lib_set_pad_properties" in advertised
    assert "lib_set_pad_geometry" not in advertised
    assert "lib_set_pad_geometry" in (getattr(mcp, "hidden", {}) or {})


def test_every_field_the_id_writer_takes_can_be_given_by_id():
    """The fields passed on for an id are the tool's own arguments, so a
    new pad field cannot be added to one writer and silently dropped."""
    import inspect

    from eda_agent.tools.bulk import single

    tool = next(t for t in asyncio.run(mcp.list_tools())
                if t.name == "lib_set_pad_properties")
    props = set(tool.inputSchema["properties"])
    assert set(library._PAD_CALL_FIELDS) <= props
    fn = mcp.hidden["lib_set_pad_geometry"].fn
    assert "rows" in inspect.signature(single(fn)).parameters
    expected = props - {"pad_name", "selected", "items"}
    assert expected == set(library._PAD_CALL_FIELDS)
