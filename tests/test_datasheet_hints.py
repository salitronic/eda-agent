# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for the shared datasheet_hints module.

Datasheet discipline ships inside the package so every user who
installs eda-agent (not just its authors) gets the reminder surfaced
in tool responses. These tests enforce that:

  - The rule set is non-trivial and explicitly requires searching +
    downloading when a datasheet isn't on hand.
  - Every component-touching response surface carries
    `_datasheet_guidance` and `_datasheet_parts`.
  - The guidance points Claude to WebSearch / WebFetch by name.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.datasheet_hints import (
    DATASHEET_RULES,
    build_guidance_block,
    extract_unique_parts,
    tag_response,
)


class TestDatasheetRules:
    def test_rules_are_nonempty(self):
        assert len(DATASHEET_RULES) >= 4

    def test_rules_require_search_and_download_when_unavailable(self):
        joined = " ".join(DATASHEET_RULES).lower()
        # Must explicitly require searching + fetching, not just
        # "consult the datasheet".
        assert "websearch" in joined
        assert "webfetch" in joined

    def test_rules_forbid_fabrication(self):
        joined = " ".join(DATASHEET_RULES).lower()
        assert (
            "fabricate" in joined
            or "guess" in joined
            or "llm-generate" in joined
            or "do not" in joined
        )


class TestExtractUniqueParts:
    def test_bom_priority_over_components(self):
        bom = {"bom": [
            {"Manufacturer": "TI", "ManufacturerPartNumber": "LM358"},
        ]}
        comps = {"components": [
            {"designator": "U1", "comment": "Generic op-amp"},
        ]}
        parts = extract_unique_parts(components=comps, bom=bom)
        # BOM entry wins
        assert len(parts) >= 1
        assert parts[0]["manufacturer"] == "TI"
        assert parts[0]["part_number"] == "LM358"

    def test_fallback_to_components_when_bom_empty(self):
        parts = extract_unique_parts(
            components={"components": [
                {"designator": "R1", "Comment": "10k"},
                {"designator": "R2", "Comment": "10k"},
            ]},
            bom=None,
        )
        assert len(parts) == 1
        assert parts[0]["part_number"] == "10k"

    def test_handles_none_inputs(self):
        assert extract_unique_parts(None, None) == []
        assert extract_unique_parts({}, {}) == []


class TestBuildGuidanceBlock:
    def test_contains_all_required_keys(self):
        block = build_guidance_block([], context="test")
        for k in (
            "datasheet_rules",
            "action_required",
            "unique_part_count",
            "search_hints",
            "context",
        ):
            assert k in block

    def test_action_required_names_web_tools(self):
        block = build_guidance_block([], context="test")
        text = block["action_required"].lower()
        assert "websearch" in text
        assert "webfetch" in text

    def test_search_hints_provide_filetype_pdf_query(self):
        parts = [{
            "manufacturer": "Texas Instruments",
            "part_number": "LM358",
            "designators": "U1,U2",
        }]
        block = build_guidance_block(parts, context="test")
        hint = block["search_hints"][0]
        assert hint["part_number"] == "LM358"
        assert "filetype:pdf" in hint["datasheet_query"]
        assert "Texas Instruments" in hint["datasheet_query"]


class TestTagResponse:
    def test_adds_guidance_and_parts_keys(self):
        response = {"components": [
            {"designator": "U1", "comment": "LM358"},
        ]}
        tagged = tag_response(
            response, components=response, context="pcb_get_components"
        )
        assert "_datasheet_guidance" in tagged
        assert "_datasheet_parts" in tagged
        assert tagged is response  # mutates in place

    def test_noop_on_non_dict(self):
        assert tag_response("not a dict") == "not a dict"
        assert tag_response(None) is None
        assert tag_response([1, 2, 3]) == [1, 2, 3]

    def test_explicit_parts_override_extraction(self):
        response = {"components": [{"designator": "R1", "comment": "10k"}]}
        parts = [{"manufacturer": "TI", "part_number": "TL072",
                  "designators": "U3"}]
        tagged = tag_response(
            response, explicit_parts=parts, context="readiness"
        )
        assert tagged["_datasheet_parts"] == parts


class TestIntegrationWithRegisteredTools:
    """Confirm that key component-touching tool responses carry the
    guidance when run against a fake bridge.
    """

    @pytest.mark.asyncio
    async def test_pcb_get_components_tags_response(self, monkeypatch):
        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                assert command == "pcb.get_components"
                return {
                    "components": [
                        {"designator": "U1", "comment": "STM32F411",
                         "footprint": "LQFP64"},
                    ],
                    "count": 1,
                }

        monkeypatch.setattr(
            "eda_agent.tools.pcb.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import pcb

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        pcb.register_pcb_tools(DummyMcp())
        result = await captured["pcb_get_components"]()
        assert "_datasheet_guidance" in result
        assert "_datasheet_parts" in result
        # STM32F411 should surface as a part needing a datasheet.
        assert len(result["_datasheet_parts"]) == 1

    @pytest.mark.asyncio
    async def test_attach_to_altium_carries_system_reminder(self, monkeypatch):
        class FakeBridge:
            def attach(self): pass
            def ping(self): return True
            def get_altium_status(self): return {}

        monkeypatch.setattr(
            "eda_agent.tools.application.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import application

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        application.register_application_tools(DummyMcp())
        result = await captured["attach_to_altium"]()
        reminder = result.get("_system_reminder")
        assert reminder is not None
        assert "datasheet" in reminder["title"].lower()
        assert reminder["datasheet_rules"] == DATASHEET_RULES
