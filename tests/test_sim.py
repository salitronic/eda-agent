# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for the SPICE simulation tools."""

from __future__ import annotations

import pytest

from eda_agent.tools.sim import (
    SPICE_MODEL_RULES,
    _guidance_block,
    _suggest_vendor_search,
)


class TestSpiceModelRules:
    def test_rules_are_nonempty(self):
        assert len(SPICE_MODEL_RULES) >= 4

    def test_rules_mention_vendor_and_never_fabricate(self):
        joined = " ".join(SPICE_MODEL_RULES).lower()
        assert "manufacturer" in joined or "vendor" in joined
        assert "datasheet" in joined
        # Must explicitly forbid LLM-generated models.
        assert (
            "not generate" in joined
            or "not fabricate" in joined
            or "do not generate" in joined
            or "do not fabricate" in joined
        )


class TestSuggestVendorSearch:
    def test_mfr_and_part_produce_filetype_scoped_query(self):
        entry = {
            "designator": "U3",
            "manufacturer": "Texas Instruments",
            "manufacturer_part": "TL072CP",
            "comment": "Dual op-amp",
        }
        hint = _suggest_vendor_search(entry)
        assert hint["designator"] == "U3"
        assert "TL072CP" in hint["search_part"]
        assert "filetype:lib" in hint["web_search_query"]
        assert "Texas Instruments" in hint["web_search_query"]
        assert "datasheet" in hint["datasheet_query"].lower()

    def test_falls_back_to_comment_when_no_part_number(self):
        entry = {
            "designator": "Q1",
            "manufacturer": "",
            "manufacturer_part": "",
            "comment": "2N3904",
            "lib_ref": "NPN",
        }
        hint = _suggest_vendor_search(entry)
        assert "2N3904" in hint["search_part"]
        assert "2N3904" in hint["web_search_query"]


class TestGuidanceBlock:
    def test_guidance_required_keys(self):
        block = _guidance_block([], [])
        assert "spice_model_rules" in block
        assert "action_required" in block
        assert "search_hints" in block
        assert "primitive_hints" in block

    def test_search_hints_one_per_needs_file_entry(self):
        needs_file = [
            {"designator": "U1", "manufacturer_part": "LM358"},
            {"designator": "U2", "manufacturer_part": "TL072"},
        ]
        block = _guidance_block(needs_file, [])
        assert len(block["search_hints"]) == 2
        assert block["search_hints"][0]["designator"] == "U1"

    def test_primitive_hints_carry_prefix_and_value(self):
        needs_prim = [
            {
                "designator": "R1",
                "comment": "10k",
                "suggested_prefix": "R",
                "suggested_value": "10k",
            }
        ]
        block = _guidance_block([], needs_prim)
        assert block["primitive_hints"][0]["suggested_prefix"] == "R"
        assert block["primitive_hints"][0]["suggested_value"] == "10k"

    def test_action_required_forbids_fabrication(self):
        text = _guidance_block([], [])["action_required"].lower()
        assert "fabricate" in text or "generate" in text
        assert "vendor" in text


class TestSimToolsOrchestration:
    @pytest.mark.asyncio
    async def test_readiness_response_gets_guidance_injected(
        self, monkeypatch
    ):
        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                assert command == "generic.get_simulation_readiness"
                return {
                    "ready": [{"designator": "R1"}],
                    "ready_count": 1,
                    "needs_primitive": [
                        {
                            "designator": "C1",
                            "comment": "100n",
                            "suggested_prefix": "C",
                            "suggested_value": "100n",
                        }
                    ],
                    "needs_primitive_count": 1,
                    "needs_file": [
                        {
                            "designator": "U1",
                            "comment": "LM358",
                            "manufacturer": "TI",
                            "manufacturer_part": "LM358",
                        }
                    ],
                    "needs_file_count": 1,
                }

        monkeypatch.setattr(
            "eda_agent.tools.sim.get_bridge", lambda: FakeBridge()
        )

        from eda_agent.tools import sim as sim_mod

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        sim_mod.register_sim_tools(DummyMcp())
        result = await captured["sch_get_simulation_readiness"]()

        assert result["ready_count"] == 1
        assert result["needs_file_count"] == 1
        guidance = result["_spice_guidance"]
        assert len(guidance["search_hints"]) == 1
        assert len(guidance["primitive_hints"]) == 1
        assert guidance["search_hints"][0]["designator"] == "U1"

    @pytest.mark.asyncio
    async def test_attach_primitive_passes_only_nonempty(self, monkeypatch):
        sent: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                sent["command"] = command
                sent["params"] = params
                return {"success": True}

        monkeypatch.setattr(
            "eda_agent.tools.sim.get_bridge", lambda: FakeBridge()
        )

        from eda_agent.tools import sim as sim_mod

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        sim_mod.register_sim_tools(DummyMcp())
        await captured["sch_attach_spice_primitive"](
            designator="R5", primitive="R", value="4.7k"
        )
        assert sent["command"] == "generic.attach_spice_primitive"
        assert sent["params"]["designator"] == "R5"
        assert sent["params"]["primitive"] == "R"
        assert sent["params"]["value"] == "4.7k"
        # Empty optional fields shouldn't leak through as empty strings
        assert "spice_model" not in sent["params"]
        assert "sim_kind" not in sent["params"]

    @pytest.mark.asyncio
    async def test_attach_model_requires_model_name(self, monkeypatch):
        sent: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                sent["command"] = command
                sent["params"] = params
                return {"success": True}

        monkeypatch.setattr(
            "eda_agent.tools.sim.get_bridge", lambda: FakeBridge()
        )

        from eda_agent.tools import sim as sim_mod

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        sim_mod.register_sim_tools(DummyMcp())
        await captured["sch_attach_spice_model"](
            designator="U3",
            file_path=r"C:\models\TL072.cir",
            model_name="TL072",
        )
        assert sent["params"]["model_name"] == "TL072"
        assert sent["params"]["primitive"] == "X"

    @pytest.mark.asyncio
    async def test_sim_run_dispatches_run_simulation(self, monkeypatch):
        sent: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                sent["command"] = command
                sent["params"] = params
                sent["timeout"] = timeout
                return {"success": True}

        monkeypatch.setattr(
            "eda_agent.tools.sim.get_bridge", lambda: FakeBridge()
        )

        from eda_agent.tools import sim as sim_mod

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        sim_mod.register_sim_tools(DummyMcp())
        await captured["sim_run"](analysis="transient")
        assert sent["command"] == "generic.run_simulation"
        assert sent["params"]["analysis"] == "transient"
        # Simulation should get a long timeout; profile-driven runs can take minutes.
        assert sent["timeout"] >= 60.0
