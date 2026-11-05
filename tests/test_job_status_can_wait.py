# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""design_job_status can hold the call until a job finishes.

A demo run started the router, said it would "check the result when it
finishes" and ended its turn; nothing notifies a session when a job
ends, so it was never woken and the run stopped half routed.
"""
from __future__ import annotations

import asyncio
import threading
import time

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


def _slow(gate):
    def run(params):
        gate.wait(5)
        return {"routed": 1}
    return run


def test_it_returns_when_the_job_finishes(monkeypatch):
    store = jobs_mod.JobStore()
    monkeypatch.setattr(jobs_mod, "get_job_store", lambda: store)
    gate = threading.Event()
    job = store.submit("route", _slow(gate), {})
    threading.Timer(0.3, gate.set).start()
    t0 = time.monotonic()
    out = asyncio.run(_tools()["design_job_status"](job, wait_seconds=10))
    assert out["status"] == "done"
    assert time.monotonic() - t0 < 5


def test_without_a_wait_it_answers_at_once(monkeypatch):
    store = jobs_mod.JobStore()
    monkeypatch.setattr(jobs_mod, "get_job_store", lambda: store)
    gate = threading.Event()
    job = store.submit("route", _slow(gate), {})
    t0 = time.monotonic()
    out = asyncio.run(_tools()["design_job_status"](job))
    gate.set()
    assert out["status"] in ("queued", "running")
    assert time.monotonic() - t0 < 1


def test_the_wait_stays_inside_the_client_limit(monkeypatch):
    """Claude Code moves a call past 120 s to the background, which is
    where the wait would be lost again."""
    store = jobs_mod.JobStore()
    monkeypatch.setattr(jobs_mod, "get_job_store", lambda: store)
    asked = []
    monkeypatch.setattr(store, "wait", lambda job, t: asked.append(t) or True)
    job = store.submit("route", lambda params: {}, {})
    asyncio.run(_tools()["design_job_status"](job, wait_seconds=600))
    assert asked == [100.0]
