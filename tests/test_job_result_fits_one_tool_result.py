# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""design_job_result trims a router result to fit one tool result.

Measured: the result of routing a 61-LED board was 254,408 characters
(1,502 tracks, 282 vias), and the client set it aside in a file whose
path named the user instead of reading it.
"""
from __future__ import annotations

import asyncio
import json

from eda_agent.config import get_config
from eda_agent.design import jobs as jobs_mod
from eda_agent.tools import design as design_tools


def _tools():
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    design_tools.register_design_tools(_Mcp())
    return tools


def _routed(params):
    return {"summary": {"routed": 100},
            "tracks": [{"x1": i, "y1": 0, "x2": i + 1, "y2": 0, "width": 8,
                        "layer": "TopLayer", "net_name": f"N{i}"} for i in range(1500)],
            "vias": [{"x": i, "y": i} for i in range(300)],
            "pairs": [{"pair": "USB"}]}


def test_long_lists_are_counted_and_written_out(monkeypatch, tmp_path):
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path)
    store = jobs_mod.JobStore()
    monkeypatch.setattr(jobs_mod, "get_job_store", lambda: store)
    job = store.submit("route", _routed, {})
    store.wait(job, 5)
    out = asyncio.run(_tools()["design_job_result"](job))
    assert len(json.dumps(out)) < 20_000
    assert len(out["result"]["tracks"]) == 25 and "1500" in out["trimmed"]["tracks"]
    assert out["result"]["pairs"] == [{"pair": "USB"}]
    full = json.loads(open(out["full_path"], encoding="utf-8").read())
    assert len(full["result"]["tracks"]) == 1500


def test_full_returns_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path)
    store = jobs_mod.JobStore()
    monkeypatch.setattr(jobs_mod, "get_job_store", lambda: store)
    job = store.submit("route", _routed, {})
    store.wait(job, 5)
    out = asyncio.run(_tools()["design_job_result"](job, full=True))
    assert len(out["result"]["tracks"]) == 1500 and "trimmed" not in out
