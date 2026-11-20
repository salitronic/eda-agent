# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the async job runner (roadmap 1.5)."""

from __future__ import annotations

import threading

from eda_agent.design.jobs import (
    DONE,
    ERROR,
    JOB_KINDS,
    JobStore,
)


def test_submit_runs_and_completes():
    store = JobStore()
    jid = store.submit("test", lambda x: x * 2, 21)
    assert store.wait(jid, timeout=5)
    assert store.status(jid)["status"] == DONE
    assert store.result(jid)["result"] == 42


def test_error_is_captured_not_raised():
    store = JobStore()

    def boom():
        raise ValueError("nope")

    jid = store.submit("test", boom)
    assert store.wait(jid, timeout=5)
    rec = store.status(jid)
    assert rec["status"] == ERROR
    assert "nope" in rec["error"]
    # result payload is None on error, and fetching doesn't raise.
    assert store.result(jid)["result"] is None


def test_result_hidden_until_done():
    store = JobStore()
    gate = threading.Event()
    jid = store.submit("test", lambda: (gate.wait(5), "value")[1])
    # Before completion the summary carries status but no result field leak.
    summ = store.status(jid)
    assert "result" not in summ
    gate.set()
    assert store.wait(jid, timeout=5)
    assert store.result(jid)["result"] == "value"


def test_summary_has_stable_keys_without_result():
    # summary() is built explicitly (no asdict) so it never deep-copies the
    # result; it must carry exactly the lifecycle fields and never `result`.
    store = JobStore()
    jid = store.submit("test", lambda: {"big": [0] * 1000})
    assert store.wait(jid, timeout=5)
    summ = store.status(jid)
    assert set(summ) == {"id", "kind", "status", "created", "started",
                         "finished", "error"}
    assert "result" not in summ
    # The real result is still retrievable via result().
    assert store.result(jid)["result"] == {"big": [0] * 1000}


def test_ids_are_monotonic_and_list_is_newest_first():
    store = JobStore()
    a = store.submit("test", lambda: 1)
    b = store.submit("test", lambda: 2)
    store.wait(a, timeout=5)
    store.wait(b, timeout=5)
    ids = [j["id"] for j in store.list()]
    assert ids == sorted(ids, reverse=True)
    assert a.startswith("job-0001-") and b.startswith("job-0002-")
    # One token per store: two servers writing to the same workspace
    # never give two jobs one id.
    assert a.split("-")[2] == b.split("-")[2] != JobStore().submit("test", lambda: 3).split("-")[2]


def test_unknown_job_id():
    store = JobStore()
    assert store.status("nope") is None
    assert store.result("nope") is None
    assert store.wait("nope", timeout=0.1) is False


def test_bounded_workers_still_complete_all():
    store = JobStore(max_workers=1)
    ids = [store.submit("test", lambda i=i: i, ) for i in range(5)]
    for jid in ids:
        assert store.wait(jid, timeout=5)
    assert all(store.status(j)["status"] == DONE for j in ids)


def test_route_job_kind_runs_on_geometry():
    # The registered 'route' job kind runs the board router on a minimal
    # geometry dict and returns route_plan's result.
    store = JobStore()
    pad = {"x_size": 40, "y_size": 40, "layer": "TopLayer", "net": "A"}
    geometry = {
        "outline": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]],
        "pads": [dict(pad, x=200, y=200), dict(pad, x=1600, y=900)],
    }
    jid = store.submit("route", JOB_KINDS["route"], {"geometry": geometry})
    assert store.wait(jid, timeout=60)
    res = store.result(jid)
    assert res["status"] == DONE
    assert res["result"]["ok"] is True
    assert res["result"]["summary"]["completion"] == 1.0


def test_route_job_kind_requires_geometry():
    store = JobStore()
    jid = store.submit("route", JOB_KINDS["route"], {})
    assert store.wait(jid, timeout=5)
    assert store.status(jid)["status"] == ERROR
