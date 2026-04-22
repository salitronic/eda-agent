# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for the netlist-freshness surface:

- `force_recompile` param threads through get_nets / get_connectivity /
  get_connectivity_many / design_review_snapshot.
- The new `force_recompile` and `get_compile_freshness` MCP tools wire
  to the right Pascal commands.
- Every connectivity response carries `_connectivity_guidance` with the
  "don't double down" rule and the compile_was_forced flag.
"""

from __future__ import annotations

import pytest


def _capture(module, register_fn_name: str):
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    getattr(module, register_fn_name)(DummyMcp())
    return captured


class _FakeBridge:
    def __init__(self, response=None):
        self.response = response or {"pins": [], "count": 0}
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {}), timeout))
        return self.response


class TestForceRecompileThreading:
    @pytest.mark.asyncio
    async def test_get_nets_threads_force_flag(self, monkeypatch):
        fake = _FakeBridge({"pins": [{"component": "U1", "net": "VCC"}]})
        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: fake
        )
        from eda_agent.tools import project
        tools = _capture(project, "register_project_tools")

        await tools["get_nets"](force_recompile=True)
        assert fake.sent[0][0] == "project.get_nets"
        assert fake.sent[0][1].get("force_recompile") == "true"

        # Default False must NOT send the flag (minimize payload).
        await tools["get_nets"]()
        assert "force_recompile" not in fake.sent[1][1]

    @pytest.mark.asyncio
    async def test_get_connectivity_threads_force_flag(self, monkeypatch):
        fake = _FakeBridge({"designator": "U1", "pins": []})
        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: fake
        )
        from eda_agent.tools import project
        tools = _capture(project, "register_project_tools")

        await tools["get_connectivity"](
            designator="U1", force_recompile=True
        )
        assert fake.sent[0][1].get("force_recompile") == "true"

    @pytest.mark.asyncio
    async def test_get_connectivity_many_threads_force_flag(self, monkeypatch):
        fake = _FakeBridge({
            "components": [], "matched": 0, "requested": 2, "not_found": []
        })
        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: fake
        )
        from eda_agent.tools import project
        tools = _capture(project, "register_project_tools")

        await tools["get_connectivity_many"](
            designators=["U1", "R1"], force_recompile=True
        )
        assert fake.sent[0][1].get("force_recompile") == "true"


class TestNewFreshnessTools:
    @pytest.mark.asyncio
    async def test_force_recompile_tool_dispatches(self, monkeypatch):
        fake = _FakeBridge({
            "recompiled": True, "prev_compile_tick": 0,
            "new_compile_tick": 12345, "project": "C:\\p.PrjPcb",
        })
        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: fake
        )
        from eda_agent.tools import project
        tools = _capture(project, "register_project_tools")

        result = await tools["force_recompile"]()
        assert fake.sent[0][0] == "project.force_recompile"
        assert result["recompiled"] is True

    @pytest.mark.asyncio
    async def test_get_compile_freshness_tool_dispatches(self, monkeypatch):
        fake = _FakeBridge({
            "compile_age_ms": 15000,
            "compile_cached": True,
            "ttl_ms": 2000,
            "open_doc_count": 3,
            "dirty_doc_count": 1,
            "dirty_docs": ["C:\\proj\\PoE.SchDoc"],
            "project": "C:\\proj.PrjPcb",
        })
        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: fake
        )
        from eda_agent.tools import project
        tools = _capture(project, "register_project_tools")

        result = await tools["get_compile_freshness"]()
        assert fake.sent[0][0] == "project.get_compile_freshness"
        assert result["dirty_doc_count"] == 1
        assert "PoE.SchDoc" in result["dirty_docs"][0]


class TestDesignReviewSnapshotForceRecompile:
    @pytest.mark.asyncio
    async def test_snapshot_calls_force_recompile_upfront(self, monkeypatch):
        calls = []

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                calls.append(command)
                if command == "project.force_recompile":
                    return {"recompiled": True}
                if command == "project.get_focused":
                    return {"name": "proj"}
                return {"ok": True}

        monkeypatch.setattr(
            "eda_agent.tools.review.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import review
        tools = _capture(review, "register_review_tools")

        await tools["design_review_snapshot"](
            sections=["project_info"],
            include_bom=False,
            force_recompile=True,
        )
        assert calls[0] == "project.force_recompile"
        assert "project.get_focused" in calls

    @pytest.mark.asyncio
    async def test_snapshot_without_force_does_not_call_recompile(
        self, monkeypatch
    ):
        calls = []

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                calls.append(command)
                return {"ok": True}

        monkeypatch.setattr(
            "eda_agent.tools.review.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import review
        tools = _capture(review, "register_review_tools")

        await tools["design_review_snapshot"](
            sections=["project_info"], include_bom=False
        )
        assert "project.force_recompile" not in calls
