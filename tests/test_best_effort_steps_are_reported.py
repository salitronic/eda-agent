# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A best-effort step that fails must say so in the reply.

Two tools do something helpful before their real work and carry on if it
fails. Carrying on is right. Not mentioning it is not, because in both
cases the thing that failed is the thing the caller asked for.

``app_checkpoint(save_first=True)`` saves the project so its file holds
what Altium holds. If that save fails it still snapshots, which is better
than nothing, but the project file in the checkpoint is then the one on
disk. A checkpoint is an undo, and one that quietly holds less than the
caller believes is worse than a failure they can see.

``design_review_snapshot(force_recompile=True)`` refreshes connectivity
before reading it. If that fails the sections still return, drawn from
whatever compile state was already current. A review is then reasoning
about possibly stale netlist data while believing it is fresh, which is
the same defect this release fixed in the force_recompile flag itself,
arrived at from the other direction.

Both follow ``proj_print_all_variants``, which was fixed the same way
earlier in this release: report the OUTCOME of the best-effort step
rather than swallowing the exception.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import application as application_module
from eda_agent.tools import review as review_module


def _capture(module, register_name, tool_name, monkeypatch, bridge):
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    getattr(module, register_name)(DummyMcp())
    return captured[tool_name]


class _Bridge:
    """Fails one named command, answers everything else.

    ``_resolve_project_dir`` and ``_checkpoint_store`` are closures
    inside ``register_application_tools`` and cannot be patched from
    module scope, so the project lookup is answered here and the real
    path runs.
    """

    def __init__(self, fail_command=None, project_dir=""):
        self.fail_command = fail_command
        self.project_dir = project_dir
        self.calls: list[str] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == self.fail_command:
            raise RuntimeError("Altium is busy")
        if command == "project.get_project_path":
            return {"project_dir": self.project_dir,
                    "project_name": "P.PrjPcb"}
        return {"ok": True}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Redirect the checkpoint store away from the real workspace.

    ``get_config`` returns a module-level singleton built at import
    time, so setting EDA_AGENT_WORKSPACE here would be too late.
    ``_checkpoint_store`` calls ``get_config()`` when the tool runs, so
    replacing the object is enough and monkeypatch puts it back.
    """
    from eda_agent import config as config_module

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(
        config_module, "config",
        config_module.config.model_copy(update={"workspace_dir": ws}))

    project = tmp_path / "proj"
    project.mkdir()
    (project / "P.PrjPcb").write_text("stub", encoding="utf-8")
    return project


@pytest.mark.asyncio
async def test_a_failed_save_is_reported_by_the_checkpoint(
        monkeypatch, workspace):
    # The checkpoint saves its own project only (project.save), never
    # every open document.
    bridge = _Bridge(fail_command="project.save",
                     project_dir=str(workspace))
    tool = _capture(application_module, "register_application_tools",
                    "app_checkpoint", monkeypatch, bridge)

    out = await tool()

    assert "application.save_all" not in bridge.calls
    assert out["saved"] is False, (
        "a checkpoint taken after a failed save must not look like one "
        "taken after a successful save")
    assert out["save_error"]
    assert "on disk" in out["note"].lower()
    # The checkpoint is still taken: something beats nothing.
    assert "checkpoint" in out


@pytest.mark.asyncio
async def test_a_successful_save_says_so(monkeypatch, workspace):
    bridge = _Bridge(project_dir=str(workspace))
    tool = _capture(application_module, "register_application_tools",
                    "app_checkpoint", monkeypatch, bridge)

    out = await tool()

    assert out["saved"] is True
    assert "save_error" not in out
    assert "note" not in out


@pytest.mark.asyncio
async def test_no_save_requested_means_no_claim_either_way(
        monkeypatch, workspace):
    """With save_first=False there is nothing to report, so report nothing."""
    bridge = _Bridge(project_dir=str(workspace))
    tool = _capture(application_module, "register_application_tools",
                    "app_checkpoint", monkeypatch, bridge)

    out = await tool(save_first=False)

    assert "saved" not in out
    assert "application.save_all" not in bridge.calls


@pytest.mark.asyncio
async def test_a_failed_recompile_is_reported_by_the_review(monkeypatch):
    bridge = _Bridge(fail_command="project.force_recompile")
    tool = _capture(review_module, "register_review_tools",
                    "design_review_snapshot", monkeypatch, bridge)

    out = await tool(sections=["components"], include_bom=False,
                     force_recompile=True)

    assert out["_recompile_failed"], (
        "the caller asked for fresh connectivity and did not get it; "
        "silence here means the review trusts stale data")
    assert "stale" in out["_recompile_note"].lower()


@pytest.mark.asyncio
async def test_a_review_without_force_recompile_claims_nothing(monkeypatch):
    bridge = _Bridge()
    tool = _capture(review_module, "register_review_tools",
                    "design_review_snapshot", monkeypatch, bridge)

    out = await tool(sections=["components"], include_bom=False)

    assert "_recompile_failed" not in out
    assert "project.force_recompile" not in bridge.calls
