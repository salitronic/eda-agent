# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_ping says which side is the old one when the versions differ.

An editor still ran a server from an older install while Altium had the
script project of a newer one open. The ping answered "STALE SCRIPT
CACHE: Altium is running version 2026.11.26.1, but the on-disk bundle is
2026.11.25.3. To recompile, close the script project ... reopen it".
Altium's compile was the current one; reopening the script project would
have changed nothing. The stale side was the server. When the running
script is the newer one the message now says so and names the cure for
that side; Altium older than the bundle keeps the stale-cache message.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent import cli
from eda_agent.tools import application as app_mod
from eda_agent.tools.registry import ToolRegistry

BUNDLED = Path("D:/site/eda_agent/scripts")


def _ping(monkeypatch, running, bundled):
    class _Bridge:
        def is_altium_running(self):
            return True

        def ping_with_version(self):
            return {"pong": True, "script_version": running,
                    "altium_version": "23.3.1.30"}

    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(app_mod, "get_bundled_scripts_path", lambda: BUNDLED)
    monkeypatch.setattr(app_mod, "_bundled_script_version", lambda: bundled)
    monkeypatch.setattr(cli, "get_default_scripts_dest", lambda: BUNDLED)
    registry = ToolRegistry()
    app_mod.register_application_tools(registry)
    tools = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    return asyncio.run(tools["app_ping"]())


def test_altium_newer_than_the_bundle_names_the_server(monkeypatch):
    out = _ping(monkeypatch, "2026.11.26.1", "2026.11.25.3")
    assert out["success"] is True and out["version_match"] is False
    msg = out["message"]
    assert msg.startswith("SERVER OLDER THAN THE SCRIPT")
    assert "2026.11.26.1" in msg and "2026.11.25.3" in msg
    assert str(BUNDLED / "Main.pas") in msg
    assert "Reopening the script project changes nothing" in msg
    assert app_mod.SERVER_STEP in msg
    assert "To recompile" not in msg and app_mod.RELOAD_STEP not in msg


def test_altium_older_than_the_bundle_keeps_the_stale_cache_message(monkeypatch):
    out = _ping(monkeypatch, "2026.11.25.3", "2026.11.26.1")
    assert out["version_match"] is False
    assert out["message"].startswith("STALE SCRIPT CACHE")
    assert app_mod.RELOAD_STEP in out["message"]
    assert "SERVER OLDER" not in out["message"]


def test_equal_versions_match(monkeypatch):
    out = _ping(monkeypatch, "2026.11.26.1", "2026.11.26.1")
    assert out["version_match"] is True
    assert "OLDER" not in out["message"] and "STALE" not in out["message"]


@pytest.mark.parametrize("running, bundled, newer", [
    ("2026.11.26.1", "2026.11.25.3", True),
    ("2026.11.26.10", "2026.11.26.9", True),     # numbers, not text
    ("2026.11.26.2", "2026.11.26.10", False),
    ("2026.11.26.1", "2026.11.26.1", False),
    ("2026.11.26.1.1", "2026.11.26.1", True),
    ("dev", "2026.11.26.1", False),              # unreadable: not newer
    ("2026.11.26.1", "local-build", False),
])
def test_versions_compare_as_numbers(running, bundled, newer):
    assert app_mod._newer(running, bundled) is newer


def test_an_unreadable_version_keeps_the_stale_cache_message(monkeypatch):
    out = _ping(monkeypatch, "dev", "2026.11.26.1")
    assert out["message"].startswith("STALE SCRIPT CACHE")
