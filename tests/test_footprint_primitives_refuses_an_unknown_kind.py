# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_get_footprint_primitives refuses a kind it does not know.

``kinds="regions,texts"`` returned empty region and text lists and no
error, on a footprint whose unfiltered read showed a region and a text:
the script matches whole singular words between ``|`` and anything else
matches nothing. An empty list read as "this footprint has none".
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import library
from eda_agent.tools.library import _primitive_kinds


@pytest.mark.parametrize("given, sent", [
    ("via|body", "via|body"),
    ("regions,texts", "region|text"),
    (" Track | ARC ", "track|arc"),
    ("bodies; vias", "body|via"),
    ("text|text|texts", "text"),
])
def test_a_comma_and_a_plural_are_read_as_meant(given, sent):
    assert _primitive_kinds(given) == (sent, [])


@pytest.mark.parametrize("given, unknown", [
    ("pads", ["pads"]),
    ("region|polygon", ["polygon"]),
    ("silk", ["silk"]),
])
def test_an_unknown_word_is_named(given, unknown):
    assert _primitive_kinds(given)[1] == unknown


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kw):
        self.sent.append((command, dict(params or {})))
        return {"name": "FP", "regions": [], "texts": [], "body_models": []}


def _tool(monkeypatch):
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    bridge = _Bridge()
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    library.register_library_tools(_Mcp())
    return tools["lib_get_footprint_primitives"], bridge


def test_an_unknown_kind_is_refused_before_anything_is_read(monkeypatch):
    read, bridge = _tool(monkeypatch)
    out = asyncio.run(read("FP", kinds="region|polygon"))
    assert out["ok"] is False and "'polygon'" in out["reason"]
    assert "track, arc, region, fill, text, via, body" in out["reason"]
    assert bridge.sent == []


def test_the_script_is_sent_the_words_it_matches(monkeypatch):
    read, bridge = _tool(monkeypatch)
    asyncio.run(read("FP", kinds="regions,texts"))
    assert bridge.sent[0][1]["kinds"] == "region|text"


def test_no_kinds_still_reads_everything(monkeypatch):
    read, bridge = _tool(monkeypatch)
    asyncio.run(read("FP"))
    assert "kinds" not in bridge.sent[0][1]
