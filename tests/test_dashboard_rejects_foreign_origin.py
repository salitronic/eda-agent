# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard drives the EDA application and has no authentication.

``/api/tool/run`` invokes any registered tool with caller-supplied
arguments, which on this project means reading and mutating client
designs under NDA. The only thing between a web page and the board is
that the server listens on loopback.

LOOPBACK IS NOT THE PROTECTION IT LOOKS LIKE. Ordinary CSRF is already
blocked, because the endpoint needs a JSON content type and that forces
a preflight nothing here approves, and the tests below pin that so it
cannot be relaxed by accident. DNS REBINDING is a different attack: a
page served from a domain whose DNS then answers 127.0.0.1 counts as
same-origin to the browser, so no preflight applies and the request
looks entirely ordinary.

MEASURED before the guard existed: a JSON POST carrying
Host: evil.example.com was accepted and ran a command against a live
Altium, and GET /api/tools listed all 412 tools to the same caller.

What separates the cases is the Host header, because a browser sends
the name the user typed. These tests exercise the real app through its
own routes rather than asserting on source.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from eda_agent.web.dashboard import create_app


@pytest.fixture
def client():
    app = create_app(workspace_dir=pathlib.Path(tempfile.mkdtemp()))
    app.testing = True
    return app.test_client()


# --------------------------------------------------------------------
# The attack.
# --------------------------------------------------------------------

def test_a_rebound_request_cannot_run_a_tool(client):
    """The regression, in the exact shape that worked."""
    r = client.post("/api/tool/run", json={"name": "app_ping", "args": {}},
                    headers={"Host": "evil.example.com"})

    assert r.status_code == 403, (
        "a request addressed to somebody else's domain reached the tool "
        "runner, which is how DNS rebinding drives the editor")


def test_a_rebound_request_cannot_even_enumerate_the_tools(client):
    """Reads leak too: the catalogue is a map of what can be driven."""
    r = client.get("/api/tools", headers={"Host": "evil.example.com"})

    assert r.status_code == 403
    assert not (r.get_json() or {}).get("tools")


def test_a_cross_origin_request_is_refused(client):
    """Cheap second gate, caught before the body is read."""
    r = client.post("/api/tool/run", json={"name": "app_ping"},
                    headers={"Host": "127.0.0.1:8766",
                             "Origin": "http://evil.example.com"})

    assert r.status_code == 403


def test_the_guard_covers_every_route_not_just_the_tool_runner(client):
    """A guard on one endpoint would leave the rest reachable."""
    for path in ("/", "/api/tools", "/api/recovery"):
        r = client.get(path, headers={"Host": "evil.example.com"})
        assert r.status_code == 403, f"{path} answered a foreign Host"


# --------------------------------------------------------------------
# What must keep working. A guard that breaks the dashboard gets removed.
# --------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1:8766", "localhost:8766",
                                  "[::1]:8766", "127.0.0.1", "LOCALHOST"])
def test_loopback_names_are_allowed(client, host):
    r = client.get("/api/tools", headers={"Host": host})

    assert r.status_code == 200, f"{host} is how people reach this"
    assert (r.get_json() or {}).get("tools"), "the catalogue went empty"


def test_the_page_itself_still_loads(client):
    assert client.get("/").status_code == 200


def test_a_same_origin_header_is_allowed(client):
    r = client.get("/api/tools", headers={"Host": "localhost:8766",
                                          "Origin": "http://localhost:8766"})

    assert r.status_code == 200


def test_a_missing_origin_is_not_treated_as_hostile(client):
    """Same-origin GETs and direct navigation send no Origin at all.

    Refusing those would break the dashboard while looking strict.
    """
    r = client.get("/api/tools", headers={"Host": "127.0.0.1:8766"})

    assert r.status_code == 200


def test_no_setting_lets_another_machine_in(client, monkeypatch):
    """The dashboard is for this computer only: no variable widens it."""
    monkeypatch.setenv("EDA_AGENT_DASHBOARD_ALLOWED_HOSTS", "workshop-pc.lan")

    assert client.get("/api/tools",
                      headers={"Host": "workshop-pc.lan:8766"}
                      ).status_code == 403


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "workshop-pc.lan"])
def test_the_dashboard_will_not_listen_beyond_loopback(host):
    from eda_agent.web import dashboard

    with pytest.raises(SystemExit):
        dashboard.main(["--host", host])


def test_the_cli_will_not_start_it_beyond_loopback(monkeypatch):
    import sys
    from eda_agent import server

    monkeypatch.setattr(sys, "argv", ["eda-agent", "dashboard",
                                      "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        server.main()


# --------------------------------------------------------------------
# The pre-existing defence, pinned so it is not lost.
# --------------------------------------------------------------------

@pytest.mark.parametrize("content_type", ["text/plain",
                                          "application/x-www-form-urlencoded"])
def test_a_simple_request_body_still_cannot_reach_the_runner(client,
                                                             content_type):
    """These content types skip the CORS preflight.

    The runner requires JSON, which is what forces a preflight and
    blocks ordinary CSRF. Accepting a form body would reopen it even
    with the Host check in place, because a rebound page is not the
    only way to send one.
    """
    r = client.post("/api/tool/run",
                    data='{"name": "app_ping", "args": {}}',
                    content_type=content_type,
                    headers={"Host": "127.0.0.1:8766"})

    assert r.status_code >= 400
