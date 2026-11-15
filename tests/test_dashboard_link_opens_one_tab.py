# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The status form's dashboard link opens one browser tab.

The button writes open_dashboard.url, and every server process (one per
MCP server of every Claude session) and the dashboard itself watch for
it. Each read it, then deleted it, so all those that saw it in the same
half second opened a tab: a dozen tabs for one click. The file is now
taken by a rename that only one process can win.
"""

from __future__ import annotations

import inspect
import multiprocessing
from pathlib import Path

from eda_agent.atomicfile import take_file_text
from eda_agent.bridge import altium_bridge as ab
from eda_agent.web import dashboard


def _race(path: str, start, out) -> None:
    start.wait()
    out.put(take_file_text(path))


def test_of_many_processes_one_takes_the_link(tmp_path):
    link = tmp_path / "open_dashboard.url"
    link.write_text("http://127.0.0.1:8766/", encoding="utf-8")
    ctx = multiprocessing.get_context("spawn")
    start, out = ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=_race, args=(str(link), start, out))
             for _ in range(12)]
    for p in procs:
        p.start()
    start.set()
    got = [out.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=60)
    assert [g for g in got if g is not None] == ["http://127.0.0.1:8766/"]
    assert list(tmp_path.iterdir()) == []


def test_a_missing_link_is_none(tmp_path):
    assert take_file_text(tmp_path / "open_dashboard.url") is None


def test_both_watchers_take_the_link():
    for fn in (ab.AltiumBridge._maybe_open_dashboard_sentinel,
               dashboard._watch_open_dashboard_sentinel):
        src = inspect.getsource(fn)
        assert "take_file_text(sentinel)" in src, fn.__name__
        assert "sentinel.unlink()" not in src, fn.__name__
