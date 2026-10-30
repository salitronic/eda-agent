# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every tool takes one item or a list of them, and a list is one request.

A tool given ``items`` runs its own code once per item; the items'
requests to Altium are gathered and sent as ONE bulk request per command,
which Altium runs inside one edit. Without ``items`` nothing changes.

The bridge here is the real send path (send_command_async and
send_bulk_async) with only the wire faked, so what is tested is the
gathering, the splitting of the reply and the per-item errors.
"""

from __future__ import annotations

import asyncio

import pytest

import eda_agent.bridge as bridge_pkg
from eda_agent.bridge import altium_bridge as ab
from eda_agent.tools import bulk as tool_bulk

REAL_SEND_ASYNC = ab.AltiumBridge.send_command_async
REAL_SEND_BULK = ab.AltiumBridge.send_bulk_async
ITEM_OUTCOME = ab.AltiumBridge._bulk_item_outcome


def _envelope(data=None, error=None):
    if error:
        return {"protocol_version": 1, "id": "x", "success": False,
                "data": None, "error": {"code": "INVALID_PARAMETER",
                                        "message": error}}
    return {"protocol_version": 1, "id": "x", "success": True,
            "data": data or {}, "error": None}


class WireBridge:
    """The real send path over a fake wire that records every request."""

    send_command_async = REAL_SEND_ASYNC
    send_bulk_async = REAL_SEND_BULK
    _bulk_item_outcome = staticmethod(ITEM_OUTCOME)

    def __init__(self, fail_on=None):
        self.requests: list = []
        self.fail_on = fail_on or (lambda params: None)

    async def send_command_direct(self, command, params=None, timeout=None):
        params = dict(params or {})
        self.requests.append((command, params))
        count = params.get("bulk_count")
        if count is None:
            reason = self.fail_on(params)
            if reason:
                raise ab.AltiumCommandError(reason, code="INVALID_PARAMETER")
            return {"echo": params}
        envelopes, stopped = [], False
        for i in range(count):
            item = params[f"bulk_item_{i}"]
            if stopped:
                envelopes.append({"success": False, "skipped": True})
                continue
            reason = self.fail_on(item)
            if reason:
                envelopes.append(_envelope(error=reason))
                stopped = not params.get("bulk_continue")
            else:
                envelopes.append(_envelope({"echo": item}))
        return {"bulk": True, "items": envelopes}


@pytest.fixture
def wire(monkeypatch):
    bridge = WireBridge()
    monkeypatch.setattr(bridge_pkg, "get_bridge", lambda: bridge)
    return bridge


def _tool(name="pcb_place_thing"):
    async def place_thing(x: float, y: float, layer: str = "TopLayer") -> dict:
        """Place a thing."""
        return await bridge_pkg.get_bridge().send_command_async(
            "pcb.place_thing", {"x": x, "y": y, "layer": layer})
    place_thing.__name__ = name
    return tool_bulk.with_items(place_thing, name)


def test_a_single_call_is_unchanged(wire):
    tool = _tool()
    out = asyncio.run(tool(x=1, y=2))
    assert out == {"echo": {"x": 1, "y": 2, "layer": "TopLayer"}}
    assert wire.requests == [("pcb.place_thing",
                              {"x": 1, "y": 2, "layer": "TopLayer"})]


def test_items_are_one_request_with_a_reply_each(wire):
    tool = _tool()
    out = asyncio.run(tool(items=[{"x": 1, "y": 1}, {"x": 2, "y": 2},
                                  {"x": 3, "y": 3}], layer="BottomLayer"))
    assert len(wire.requests) == 1, "three items must be ONE request"
    command, params = wire.requests[0]
    assert command == "pcb.place_thing" and params["bulk_count"] == 3
    assert params["bulk_item_2"] == {"x": 3, "y": 3, "layer": "BottomLayer"}
    assert out["succeeded"] == 3 and out["altium_requests"] == 1
    assert [i["echo"]["x"] for i in out["items"]] == [1, 2, 3]


def test_a_read_then_a_write_is_two_requests_for_any_count(wire):
    async def move(designator: str) -> dict:
        b = bridge_pkg.get_bridge()
        where = await b.send_command_async("pcb.read", {"d": designator})
        return await b.send_command_async("pcb.move", {"d": designator,
                                                       "from": where["echo"]})
    tool = tool_bulk.with_items(move, "pcb_move_one")

    out = asyncio.run(tool(items=[{"designator": f"R{i}"} for i in range(10)]))

    assert [c for c, _ in wire.requests] == ["pcb.read", "pcb.move"]
    assert out["succeeded"] == 10


def test_a_failed_item_stops_the_rest_and_says_which(monkeypatch):
    bridge = WireBridge(fail_on=lambda p: "off the board" if p.get("x") == 2 else None)
    monkeypatch.setattr(bridge_pkg, "get_bridge", lambda: bridge)
    out = asyncio.run(_tool()(items=[{"x": 1, "y": 0}, {"x": 2, "y": 0},
                                     {"x": 3, "y": 0}]))
    first, second, third = out["items"]
    assert "echo" in first
    assert second["ok"] is False and "off the board" in second["error"]
    assert third["ok"] is False and third["code"] == "SKIPPED"
    assert out["failed"] == 2


def test_a_bad_item_does_not_block_the_others(wire):
    out = asyncio.run(_tool()(items=[{"x": 1, "y": 1}, {"x": "wide", "y": 2},
                                     "not a dict", {"x": 4, "y": 4}]))
    assert out["succeeded"] == 2 and out["failed"] == 2
    assert out["items"][1]["ok"] is False and "bad arguments" in out["items"][1]["reason"]
    assert wire.requests[0][1]["bulk_count"] == 2


def test_a_single_call_missing_a_required_argument_is_refused(wire):
    with pytest.raises(ValueError, match="needs y"):
        asyncio.run(_tool()(x=1))
    assert wire.requests == []


def test_the_schema_offers_items_and_no_longer_requires_the_rest():
    import inspect
    sig = inspect.signature(_tool())
    assert "items" in sig.parameters
    assert sig.parameters["x"].default is None


def test_every_advertised_tool_that_should_take_items_does():
    """The guard: a tool registered some other way would quietly have no
    bulk form, and one on the no-bulk list must not offer it."""
    from eda_agent.server import mcp

    tools = asyncio.run(mcp.list_tools())
    lacking = []
    for t in tools:
        props = t.inputSchema.get("properties") or {}
        has = "items" in props
        if t.name in tool_bulk.NO_BULK or t.name.startswith(tool_bulk.NO_BULK_PREFIXES):
            assert not has, t.name
        elif t.name in tool_bulk.LIST_NATIVE:
            assert not has, f"{t.name} takes its own list; a second is noise"
            assert any("array" in str(p) for p in props.values()), (
                f"{t.name} is listed as taking a list and takes none")
        elif not has:
            lacking.append(t.name)
    assert not lacking, f"no bulk form: {lacking}"
    assert tool_bulk.LIST_NATIVE <= {t.name for t in tools}


def test_the_folded_batch_names_are_unadvertised_and_still_reachable():
    from eda_agent.server import mcp
    from eda_agent.tools.folds import FOLDED_BATCH, call_form, reach_of

    advertised = {t.name for t in asyncio.run(mcp.list_tools())}
    hidden = set(getattr(mcp, "hidden", {}) or {})
    for old, new in FOLDED_BATCH.items():
        assert old not in advertised and old in hidden, old
        assert new in advertised, new
        assert call_form(old) == new
        assert f"{new}(items=" in reach_of(old)


class _RecordingBridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"ok": True}


def _generic_tools(monkeypatch):
    from eda_agent.tools import generic

    bridge = _RecordingBridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    generic.register_generic_tools(DummyMcp())
    return captured, bridge


@pytest.mark.parametrize("single,batch,shared,items,extra", [
    ("obj_modify", "obj_batch_modify", {"object_type": "ePin",
                                        "scope": "lib_component:U1"},
     [{"filter": "Designator=1", "set": "Name=VDD"},
      {"filter": "Designator=2", "set": "Name=GND|Electrical=4"}], {}),
    ("obj_create", "obj_batch_create", {"object_type": "eNetLabel"},
     [{"properties": "Text=A|Location.X=100|Location.Y=0"},
      {"properties": "Text=B|Location.X=100|Location.Y=100"}], {}),
    ("obj_delete", "obj_batch_delete", {"object_type": "eJunction"},
     [{"filter": "Location.X=100"}, {"filter": "Location.X=200"}], {}),
])
def test_a_list_runs_the_same_handler_the_batch_tool_ran(monkeypatch, single, batch,
                                                         shared, items, extra):
    tools, bridge = _generic_tools(monkeypatch)
    asyncio.run(tools[single](items=items, **shared, **extra))
    asyncio.run(tools[batch]([{**shared, **i} for i in items], **extra))
    (first, second) = bridge.sent
    assert first == second, "the list form must send what the batch tool sent"
    assert first[0].startswith("generic.batch_")


def test_a_list_delete_with_an_empty_filter_still_needs_confirmation(monkeypatch):
    tools, bridge = _generic_tools(monkeypatch)
    out = asyncio.run(tools["obj_delete"](object_type="eWire",
                                          items=[{"filter": "Color=255"}, {}]))
    assert bridge.sent == [] and "unfiltered_operations" in out


def test_a_single_call_without_its_fields_is_refused(monkeypatch):
    tools, bridge = _generic_tools(monkeypatch)
    assert asyncio.run(tools["obj_modify"](set="Name=A"))["ok"] is False
    assert asyncio.run(tools["obj_create"]())["ok"] is False
    assert asyncio.run(tools["obj_delete"]())["ok"] is False
    assert asyncio.run(tools["obj_modify"](items=["x"]))["ok"] is False
    assert bridge.sent == []


def _library_tools(monkeypatch, tmp_path):
    from eda_agent.tools import library

    bridge = _RecordingBridge()
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)

    class _Config:
        workspace_dir = tmp_path

        def ensure_workspace(self):
            pass

    monkeypatch.setattr(library, "get_config", lambda: _Config())
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library.register_library_tools(DummyMcp())
    return captured, bridge


def test_renames_by_name_go_to_the_batch_rename(monkeypatch, tmp_path):
    tools, bridge = _library_tools(monkeypatch, tmp_path)
    asyncio.run(tools["lib_rename_component"](
        library_path="C:/x.SchLib",
        items=[{"component_name": "A", "new_name": "B"},
               {"component_name": "C", "new_name": "D"}]))
    (command, params), = bridge.sent
    assert command == "library.batch_rename"
    assert params["library_path"] == "C:/x.SchLib"
    assert (tmp_path / "batch_rename.txt").read_text() == "A|B\nC|D\n"


def test_renames_by_index_run_one_by_one(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_pkg, "get_bridge", lambda: _RecordingBridge())
    tools, bridge = _library_tools(monkeypatch, tmp_path)
    out = asyncio.run(tools["lib_rename_component"](items=[
        {"component_index": 3, "new_name": "B"},
        {"component_name": "C", "new_name": "D"}]))
    assert [c for c, _ in bridge.sent] == ["library.rename_component"] * 2
    assert bridge.sent[0][1]["component_index"] == 3
    assert out["succeeded"] == 2
