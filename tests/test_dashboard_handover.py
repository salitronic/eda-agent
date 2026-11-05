# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard port goes to the newest code: a dashboard stops when a
newer copy asks, and an older or equal one keeps it."""
from __future__ import annotations

import io
import json

from eda_agent import server
from eda_agent.web import dashboard
from eda_agent.web.dashboard import create_app


def test_it_yields_only_to_newer_code(tmp_path, monkeypatch):
    stopped = []
    monkeypatch.setitem(dashboard.SHUTDOWN, "stop", lambda: stopped.append(1))
    monkeypatch.setattr(dashboard.threading, "Timer",
                        lambda delay, fn: type("T", (), {"start": lambda self: fn()})())
    client = create_app(workspace_dir=tmp_path).test_client()
    mine = client.get("/api/dashboard/info").get_json()["code"]
    assert mine > 0
    assert client.post("/api/dashboard/yield", json={"code": mine}).get_json()["ok"] is False
    assert client.post("/api/dashboard/yield", json={"code": mine - 1}).get_json()["ok"] is False
    assert stopped == []
    reply = client.post("/api/dashboard/yield", json={"code": mine + 1}).get_json()
    assert reply["yielding"] and stopped == [1]


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_dashboard_too_old_to_ask_keeps_the_port(monkeypatch):
    import urllib.error
    import urllib.request

    def old(url, *a, **k):
        raise urllib.error.HTTPError(url, 404, "NOT FOUND", None, None)
    monkeypatch.setattr(urllib.request, "urlopen", old)
    assert server._take_over_dashboard("127.0.0.1", 1, 99, wait=0.1) is False


def test_a_newer_server_takes_the_port(monkeypatch):
    import urllib.request
    asked = []

    def answer(req, *a, **k):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/info"):
            return _Reply(json.dumps({"code": 1.0}).encode())
        asked.append(json.loads(req.data)["code"])
        return _Reply(json.dumps({"yielding": True}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", answer)
    monkeypatch.setattr(server, "_probe_port_owner", lambda host, port: None)
    assert server._take_over_dashboard("127.0.0.1", 1, 99, wait=0.5) is True
    assert asked and asked[0] > 1.0
