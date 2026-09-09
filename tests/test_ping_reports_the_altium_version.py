# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_ping carries the Altium build, and nothing acts on it.

Two bug reports of a wedged polling loop were both an Altium far below
the versions this is developed against, naming interfaces that build
does not declare. Each cost a round trip of "which Altium?" and was
re-diagnosed afterwards.

REPORTED, NEVER ENFORCED. An old build runs most of this toolset
perfectly well; the reporter in one of those threads had dozens of
successful calls in the same session before one handler failed.
Refusing to start would take away the part that works to prevent the
part that does not.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import application as app_mod
from eda_agent.tools.registry import ToolRegistry

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Application.pas"


def _tools():
    registry = ToolRegistry()
    app_mod.register_application_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


class _Bridge:
    def __init__(self, ping=None, running=True):
        self._ping = ping
        self._running = running

    def is_altium_running(self):
        return self._running

    def ping_with_version(self):
        return self._ping


def test_the_version_reaches_the_caller(monkeypatch):
    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge(
        {"pong": True, "script_version": "2026.09.09.4",
         "altium_version": "26.9.1.9"}))
    out = asyncio.run(_tools()["app_ping"]())
    assert out["altium_version"] == "26.9.1.9"


def test_an_old_build_is_reported_not_refused(monkeypatch):
    """The whole point. AD12 answers the ping; the reply says so and the
    call succeeds."""
    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge(
        {"pong": True, "script_version": "2026.09.09.4",
         "altium_version": "12.0.0.23161"}))
    out = asyncio.run(_tools()["app_ping"]())
    assert out["success"] is True
    assert out["altium_version"].startswith("12.")
    joined = " ".join(str(v) for v in out.values()).lower()
    assert "unsupported" not in joined and "too old" not in joined


def test_a_script_that_cannot_answer_reports_an_empty_string(monkeypatch):
    """Client.GetProductVersion is wrapped; an empty answer is a fact,
    not a reason to fail the ping."""
    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge(
        {"pong": True, "script_version": "2026.09.09.4"}))
    out = asyncio.run(_tools()["app_ping"]())
    assert out["success"] is True
    assert out["altium_version"] == ""


def test_the_field_is_present_when_altium_is_not_running(monkeypatch):
    """A caller reading the field should not have to branch on whether
    the key exists."""
    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge(running=False))
    out = asyncio.run(_tools()["app_ping"]())
    assert out["altium_version"] == ""


def test_the_field_is_present_when_the_script_is_silent(monkeypatch):
    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge(ping=None))
    out = asyncio.run(_tools()["app_ping"]())
    assert out["altium_version"] == ""


def test_the_session_opening_call_carries_it_too():
    """app_context is what a session opens with, so it is where the
    version lands in a transcript without anyone thinking to ask."""
    import inspect

    src = inspect.getsource(app_mod)
    context = src.split("async def app_context", 1)[1].split(
        "    async def ", 1)[0]
    assert '"altium_version": ping.get("altium_version")' in context, (
        "app_context no longer reports the Altium build")


def test_nothing_in_the_toolset_refuses_on_a_version():
    """The guard for the decision itself: report, do not enforce."""
    src = Path(__file__).resolve().parents[1] / "src" / "eda_agent"
    # A refusal is a COMPARISON or a raise, not any line with an angle
    # bracket in it: the first version of this flagged a dashboard
    # endpoint called altium_version because its "-> Response" matched.
    # Any comparison on a line that mentions it, or a refusal word. The
    # operator does not have to sit next to the name: the mutation that
    # caught the first version of this wrote
    # ``(result.get("altium_version") or "") < "20"``, where two closing
    # brackets and an ``or`` stand between the two.
    comparison = re.compile(r"==|!=|<=|>=|<|>")
    refusal = re.compile(r"\b(raise|refuse[sd]?|abort)\b")
    offenders = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"altium_version", text):
            line = text[text.rfind(chr(10), 0, m.start()) + 1:
                        text.find(chr(10), m.end())]
            if line.lstrip().startswith("def "):
                continue
            # "->" is a type hint, not a comparison.
            if comparison.search(line.replace("->", "")) or refusal.search(line):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "the Altium version is being acted on, not just reported: "
        + "; ".join(offenders))


def test_the_pascal_cannot_fail_the_ping_over_it():
    """Client.GetProductVersion is wrapped, because a ping that dies is
    worse than a ping with one empty field."""
    text = _PAS.read_text(encoding="utf-8", errors="replace")
    body = text.split("Function App_Ping(", 1)[1].split(chr(10) + "End;", 1)[0]
    assert "Try" in body and "Except" in body
    assert "Ver := '';" in body
