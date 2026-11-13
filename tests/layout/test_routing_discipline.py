# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Routing discipline on the Config tab: enforced (the default) asks for a
sourced brief before a critical net is routed and waits on every finding;
advisory routes and names; off asks for neither. Read on every call.
"""

from __future__ import annotations

import json

import pytest

from eda_agent.layout.model import Pad
from tests.layout.test_routing_findings_gate import BOARD_FILE, SMD, _board, _Bridge, _job
from tests.conftest import install_bridge_fake


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    from eda_agent.layout import read_altium
    from eda_agent.tools import route as route_module

    bridge = _Bridge()
    workspace = install_bridge_fake(monkeypatch, tmp_path, bridge)
    monkeypatch.setattr(route_module, "get_bridge", lambda: bridge)

    def radio_board(expect_file):
        b = _board()
        b.pads += [Pad("ANT1", "1", 300, 350, net="RF_OUT", copper=list(SMD)),
                   Pad("U1", "5", 300, 250, net="RF_OUT", copper=list(SMD))]
        return b

    monkeypatch.setattr(read_altium, "read_live_board", radio_board)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    route_module.register_route_tools(DummyMcp())

    def level(name):
        (workspace / "preferences.json").write_text(json.dumps({"routing": name}))

    return captured, bridge, level


@pytest.mark.asyncio
async def test_enforced_holds_back_and_waits(tools):
    t, bridge, level = tools
    out = await t["pcb_route_round"](expect_file=BOARD_FILE, nets=["RF_OUT"])
    assert "job_id" not in out and out["brief_needed"][0]["net"] == "RF_OUT"
    assert "routing_discipline" not in out
    out = await t["pcb_autoroute_apply"](job_id=_job(), expect_file=BOARD_FILE,
                                         checkpoint=False)
    assert out["unanswered"] and bridge.calls == []


@pytest.mark.asyncio
async def test_advisory_routes_and_names_and_writes_with_the_findings_listed(tools):
    t, bridge, level = tools
    level("advisory")
    out = await t["pcb_route_round"](expect_file=BOARD_FILE, nets=["RF_OUT"], advise=False)
    assert "job_id" in out, out
    assert out["routed_without_brief"][0]["net"] == "RF_OUT"
    assert out["routing_discipline"] == "advisory" and "Config tab" in out["discipline_note"]
    out = await t["pcb_autoroute_apply"](job_id=_job(), expect_file=BOARD_FILE,
                                         checkpoint=False)
    assert out["tracks"]["placed"] > 0
    assert [f["id"] for f in out["unanswered"]] == ["length:A"]


@pytest.mark.asyncio
async def test_off_asks_for_nothing_and_a_change_counts_from_the_next_call(tools):
    t, bridge, level = tools
    level("off")
    out = await t["pcb_autoroute"](expect_file=BOARD_FILE, nets=["RF_OUT"])
    assert out["job_id"] and "brief_needed" not in out and "routed_without_brief" not in out
    assert out["routing_discipline"] == "off"
    out = await t["pcb_autoroute_apply"](job_id=_job(), expect_file=BOARD_FILE,
                                         checkpoint=False)
    assert out["tracks"]["placed"] > 0 and "unanswered" not in out
    level("enforced")
    out = await t["pcb_autoroute"](expect_file=BOARD_FILE, nets=["RF_OUT"])
    assert "job_id" not in out and out["brief_needed"][0]["net"] == "RF_OUT"


@pytest.mark.asyncio
async def test_the_brief_the_ai_gave_is_obeyed_at_every_level(tools):
    from eda_agent.layout.route import brief as B

    t, _, level = tools
    B.save(BOARD_FILE, B.Brief.from_list([{"nets": ["A"], "min_length": 500,
                                           "max_length": 100, "source": "test"}]))
    for name in ("enforced", "advisory", "off"):
        level(name)
        out = await t["pcb_autoroute"](expect_file=BOARD_FILE, nets=["A"])
        assert out["error"] == "the routing brief cannot be obeyed as written", name
