# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_ping and app_attach carry the datasheet reminder now and then.

The reminder is about 750 tokens, and it rode on every health check. It
now comes on the first call in a server and on every Nth call after it,
counted across both tools, with N from EDA_AGENT_REMINDER_EVERY.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import application as app_mod
from eda_agent.tools.registry import ToolRegistry


class _Bridge:
    def attach(self):
        pass

    def ping(self):
        return True

    def is_altium_running(self):
        return True

    def ping_with_version(self):
        return {"pong": True, "script_version": "x"}


def _tools(monkeypatch, every=None):
    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(app_mod, "_reminder_calls", 0)
    if every is None:
        monkeypatch.delenv(app_mod._REMINDER_EVERY_ENV, raising=False)
    else:
        monkeypatch.setenv(app_mod._REMINDER_EVERY_ENV, every)
    registry = ToolRegistry()
    app_mod.register_application_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


def _carried(tools, names):
    return ["_system_reminder" in asyncio.run(tools[n]()) for n in names]


def test_first_call_then_every_tenth(monkeypatch):
    tools = _tools(monkeypatch)
    got = _carried(tools, ["app_ping"] * 21)
    assert [i for i, c in enumerate(got) if c] == [0, 10, 20]


def test_ping_and_attach_share_one_count(monkeypatch):
    tools = _tools(monkeypatch, "2")
    got = _carried(tools, ["app_attach", "app_ping", "app_ping", "app_attach"])
    assert got == [True, False, True, False]


@pytest.mark.parametrize("raw", ["1", "0", "-3"])
def test_one_or_less_sends_it_every_time(monkeypatch, raw):
    tools = _tools(monkeypatch, raw)
    assert all(_carried(tools, ["app_ping"] * 3))


def test_a_bad_value_keeps_the_default(monkeypatch):
    tools = _tools(monkeypatch, "often")
    got = _carried(tools, ["app_ping"] * 11)
    assert [i for i, c in enumerate(got) if c] == [0, 10]


def test_the_reminder_is_the_whole_rule_when_sent(monkeypatch):
    tools = _tools(monkeypatch)
    out = asyncio.run(tools["app_ping"]())
    assert out["_system_reminder"] is app_mod._SESSION_REMINDER
