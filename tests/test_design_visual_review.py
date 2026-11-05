# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Integration tests for the design_visual_review MCP tool.

A routing fake bridge supplies the active-document kind and geometry; the
pure renderer and the Edge rasterizer are monkeypatched so the test stays
hermetic and focuses on the tool's orchestration: target detection, file
output, the rubric/loop payload, and the rasterize toggle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.tools import render as render_module


def _capture_tool(monkeypatch, fake_bridge):
    monkeypatch.setattr(render_module, "get_bridge", lambda: fake_bridge)
    # Keep the renderer + rasterizer out of the way; they have their own tests.
    monkeypatch.setattr(render_module, "render_sch_svg",
                        lambda geom, opts: '<svg viewBox="0 0 800 400">sch</svg>')
    monkeypatch.setattr(render_module, "render_pcb_svg",
                        lambda geom, opts: '<svg viewBox="0 0 1000 500">pcb</svg>')
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    render_module.register_render_tools(DummyMcp())
    return captured["design_visual_review"]


class _Bridge:
    def __init__(self, kind, sch=None, pcb=None):
        self._kind = kind
        self._sch = sch or {"doc": "main.SchDoc", "counts": {"components": 3},
                            "bbox": {"width": 800, "height": 400}}
        self._pcb = pcb or {"doc": "board.PcbDoc", "counts": {"tracks": 9},
                            "bbox": {"width": 1000, "height": 500}}
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == "application.get_active_document":
            return {"document_kind": self._kind}
        if command == "generic.get_sch_geometry":
            return self._sch
        if command == "generic.get_pcb_geometry":
            return self._pcb
        return {}


def _patch_workspace(monkeypatch, tmp_path):
    """Point the render tools' workspace at a tmp dir."""
    class _Cfg:
        workspace_dir = tmp_path

    monkeypatch.setattr(render_module, "get_config", lambda: _Cfg())


@pytest.mark.asyncio
async def test_default_output_goes_to_renders_subdir(tmp_path, monkeypatch):
    # Keep the user's folder clean: default renders land in workspace/renders/,
    # not the workspace root.
    bridge = _Bridge("PcbDoc")
    tool = _capture_tool(monkeypatch, bridge)
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        render_module, "rasterize_svg",
        lambda svg, png, width=0, height=0: {"ok": True, "png_path": png},
    )
    out = await tool(rasterize=True)  # no output_path -> default
    assert out["ok"] is True
    assert (tmp_path / "renders") in Path(out["svg_path"]).parents
    # Nothing dumped directly in the workspace root.
    assert not list(tmp_path.glob("*.svg"))


@pytest.mark.asyncio
async def test_raster_intermediate_is_cleaned_up(tmp_path, monkeypatch):
    bridge = _Bridge("PcbDoc")
    tool = _capture_tool(monkeypatch, bridge)
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        render_module, "rasterize_svg",
        lambda svg, png, width=0, height=0: {"ok": True, "png_path": png},
    )
    await tool(rasterize=True)
    # The scratch .raster.svg must NOT linger anywhere under renders/.
    assert not list((tmp_path / "renders").glob("*.raster.svg"))


@pytest.mark.asyncio
async def test_auto_detects_schematic(tmp_path, monkeypatch):
    bridge = _Bridge("SchDoc")
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(output_path=str(tmp_path / "r.svg"), rasterize=False)
    assert out["ok"] is True
    assert out["target"] == "schematic"
    assert out["svg_path"].endswith("r.svg")
    assert (tmp_path / "r.svg").exists()
    assert out["rubric"], "expected a schematic rubric"
    assert out["loop_protocol"]
    assert "r.svg" in out["next_step"]


@pytest.mark.asyncio
async def test_auto_detects_pcb(tmp_path, monkeypatch):
    bridge = _Bridge("PcbDoc")
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(output_path=str(tmp_path / "r.svg"), rasterize=False)
    assert out["target"] == "pcb"
    assert out["counts"] == {"tracks": 9}
    # PCB rubric points at the outline audit, by the tool that runs it.
    audits = {a for item in out["rubric"] for a in item["audits"]}
    assert "audit kind=components_outside_board_outline" in audits


@pytest.mark.asyncio
async def test_unknown_active_document_errors(tmp_path, monkeypatch):
    bridge = _Bridge("")  # no kind
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(rasterize=False)
    assert out["ok"] is False
    assert "could not detect" in out["reason"]


@pytest.mark.asyncio
async def test_explicit_target_skips_active_document_lookup(tmp_path, monkeypatch):
    bridge = _Bridge("SchDoc")
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(target="pcb", output_path=str(tmp_path / "r.svg"),
                     rasterize=False)
    assert out["target"] == "pcb"
    assert "application.get_active_document" not in bridge.calls


@pytest.mark.asyncio
async def test_rasterize_path_sets_png(tmp_path, monkeypatch):
    bridge = _Bridge("PcbDoc")
    tool = _capture_tool(monkeypatch, bridge)
    monkeypatch.setattr(
        render_module, "rasterize_svg",
        lambda svg, png, width=0, height=0: {"ok": True, "png_path": png},
    )
    out = await tool(output_path=str(tmp_path / "r.svg"), rasterize=True)
    assert out["png_path"] == str(tmp_path / "r.png")
    assert out["next_step"].endswith("`loop_protocol`.")
    assert "r.png" in out["next_step"]


@pytest.mark.asyncio
async def test_rasterize_failure_falls_back_to_svg(tmp_path, monkeypatch):
    bridge = _Bridge("SchDoc")
    tool = _capture_tool(monkeypatch, bridge)
    monkeypatch.setattr(
        render_module, "rasterize_svg",
        lambda svg, png, width=0, height=0: {"ok": False, "reason": "no browser",
                                             "png_path": None},
    )
    out = await tool(output_path=str(tmp_path / "r.svg"), rasterize=True)
    assert out["png_path"] is None
    assert out["rasterize_note"] == "no browser"
    assert "r.svg" in out["next_step"]


# ---------------------- libraries are not documents ------------------
#
# DM_DocumentKind spells libraries 'PCBLIB' and 'SCHLIB', so the plain
# substring test that picks the render target reads "pcb" out of
# "pcblib" and sent a PcbLib down the PcbDoc geometry path. The user got
# an obscure geometry failure instead of being told the active document
# is a library, and this is not a rare state: every library-authoring
# call leaves a SchLib or PcbLib active, which is precisely when someone
# renders to check their work.

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["PCBLIB", "PcbLib", "SCHLIB", "SchLib"])
async def test_a_library_is_not_auto_detected_as_a_document(
    kind, tmp_path, monkeypatch
):
    bridge = _Bridge(kind)
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(output_path=str(tmp_path / "r.svg"), rasterize=False)

    assert out["ok"] is False, (
        f"{kind} was auto-detected as a renderable document")
    assert "librar" in out["reason"].lower(), out["reason"]
    # And it must not have gone off to fetch geometry for a document
    # that is not open.
    assert "generic.get_pcb_geometry" not in bridge.calls
    assert "generic.get_sch_geometry" not in bridge.calls


@pytest.mark.asyncio
async def test_the_reason_says_how_to_proceed(tmp_path, monkeypatch):
    """An error that only says "no" costs the caller another round trip."""
    bridge = _Bridge("PCBLIB")
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(rasterize=False)
    reason = out["reason"].lower()
    assert "target=" in reason, reason
    assert out["active_document"] == {"document_kind": "PCBLIB"}


@pytest.mark.asyncio
@pytest.mark.parametrize("target,expected", [
    ("pcb", "pcb"), ("schematic", "schematic"),
])
async def test_an_explicit_target_still_overrides_a_library(
    target, expected, tmp_path, monkeypatch
):
    """The guard is on AUTO-detection only. A caller who names the target
    has said what they want and keeps it."""
    bridge = _Bridge("PCBLIB")
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(target=target, output_path=str(tmp_path / "r.svg"),
                     rasterize=False)
    assert out["ok"] is True
    assert out["target"] == expected
