# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""obj_query refuses '|' between property names and names the separator.

Sent on, "SheetFileName.Text|Location.X" came back as one key with an
empty value and SheetFileName.Text was reported as unknown (#293).
"""
from __future__ import annotations

from tests.test_metric_grid_coordinates import _tools

import asyncio


def test_a_pipe_in_properties_is_refused_before_sending(monkeypatch):
    tools, sent = _tools(monkeypatch)
    out = asyncio.run(tools["obj_query"](
        object_type="eSheetSymbol", properties="SheetFileName.Text|Location.X",
        filter="Location.X=10000"))
    assert sent == []
    assert out["error"] == "BAD_PROPERTIES" and out["objects"] == []
    assert "'SheetFileName.Text,Location.X'" in out["reason"]


def test_commas_still_go_through(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["obj_query"](object_type="eSheetSymbol",
                                   properties="SheetFileName.Text,Location.X"))
    assert sent and sent[0][1]["properties"] == "SheetFileName.Text,Location.X"
