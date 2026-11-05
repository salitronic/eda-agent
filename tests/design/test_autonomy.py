# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the autonomy guide + prompt (roadmap 2.1 client packaging)."""

from __future__ import annotations

from eda_agent.design.autonomy import autonomy_guide, autonomy_prompt_text
from eda_agent.design.session import STAGES
from eda_agent.design.state_machine import STAGE_PLAYBOOKS
from eda_agent.tools.folds import call_form


def test_guide_covers_all_stages_without_drift():
    g = autonomy_guide()
    guide_stages = [s["stage"] for s in g["stages"]]
    assert guide_stages == list(STAGES)
    for s in g["stages"]:
        # Guide content mirrors the state machine's playbooks (no drift).
        assert s["goal"] == STAGE_PLAYBOOKS[s["stage"]]["goal"]
        # Each named by the advertised tool that runs it.
        assert s["tools"] == list(dict.fromkeys(
            call_form(t) for t in STAGE_PLAYBOOKS[s["stage"]]["tools"]))


def test_guide_has_loop_and_constraints():
    g = autonomy_guide()
    assert g["loop"] and g["constraints"]
    joined = " ".join(g["loop"]).lower()
    assert "design_next_action" in joined
    assert "design_session_start" in joined
    assert "checkpoint" in joined


def test_prompt_text_embeds_requirement():
    txt = autonomy_prompt_text("2-layer USB blinker")
    assert "2-layer USB blinker" in txt
    assert "design_session_start" in txt
    # Without a requirement, no "this run" section.
    assert "This run's requirement" not in autonomy_prompt_text("")


def test_guide_tool_and_prompt_registered():
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    tools = {t.name for t in asyncio.run(m.list_tools())}
    prompts = {p.name for p in asyncio.run(m.list_prompts())}
    assert "design_autonomy_guide" in tools
    assert "autonomous_design" in prompts
