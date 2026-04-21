# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for the design-review snapshot orchestrator."""

from __future__ import annotations

import pytest

from eda_agent.tools.review import (
    DATASHEET_RULES,
    DEFAULT_SECTIONS,
    REVIEW_SECTIONS,
    _extract_unique_parts,
    _guidance_block,
)


class TestReviewConstants:
    def test_every_default_section_is_registered(self):
        for s in DEFAULT_SECTIONS:
            assert s in REVIEW_SECTIONS

    def test_every_section_has_a_valid_command_namespace(self):
        allowed = {"application", "project", "pcb", "generic", "library"}
        for name, (cmd, params, timeout) in REVIEW_SECTIONS.items():
            assert "." in cmd, f"section {name} command {cmd!r} missing namespace"
            ns = cmd.split(".", 1)[0]
            assert ns in allowed, (
                f"section {name} command {cmd!r} uses unknown namespace {ns!r}"
            )
            assert isinstance(params, dict)
            assert timeout > 0

    def test_datasheet_rules_are_nonempty_and_actionable(self):
        assert len(DATASHEET_RULES) >= 4
        joined = " ".join(DATASHEET_RULES).lower()
        # Every rule set must mention the datasheet explicitly.
        assert "datasheet" in joined
        # Key terms that the rules are expected to steer on.
        assert "pin" in joined
        assert "voltage" in joined


class TestExtractUniqueParts:
    def test_bom_with_manufacturer_part_numbers(self):
        bom = {
            "bom": [
                {"Manufacturer": "TI", "ManufacturerPartNumber": "LM358",
                 "Designator": "U1,U2"},
                {"Manufacturer": "TI", "ManufacturerPartNumber": "LM358",
                 "Designator": "U3"},
                {"Manufacturer": "Murata", "ManufacturerPartNumber": "GRM188",
                 "Designator": "C1..C10"},
            ]
        }
        parts = _extract_unique_parts(None, bom)
        assert len(parts) == 2
        assert any(
            p["manufacturer"] == "TI" and p["part_number"] == "LM358"
            for p in parts
        )

    def test_falls_back_to_components_when_no_bom(self):
        components = {
            "components": [
                {"designator": "R1", "Comment": "10k"},
                {"designator": "R2", "Comment": "10k"},
                {"designator": "R3", "Comment": "4.7k"},
            ]
        }
        parts = _extract_unique_parts(components, None)
        assert len(parts) == 2
        part_numbers = {p["part_number"] for p in parts}
        assert part_numbers == {"10k", "4.7k"}

    def test_empty_inputs_return_empty_list(self):
        assert _extract_unique_parts(None, None) == []
        assert _extract_unique_parts({}, {}) == []
        assert _extract_unique_parts({"components": []}, {"bom": []}) == []

    def test_case_insensitive_dedup(self):
        bom = {
            "bom": [
                {"Manufacturer": "TI", "ManufacturerPartNumber": "LM358"},
                {"Manufacturer": "ti", "ManufacturerPartNumber": "lm358"},
            ]
        }
        parts = _extract_unique_parts(None, bom)
        assert len(parts) == 1


class TestGuidanceBlock:
    def test_guidance_has_required_keys(self):
        block = _guidance_block([])
        assert "datasheet_rules" in block
        assert "action_required" in block
        assert "unique_part_count" in block
        assert "reminder" in block

    def test_unique_part_count_matches_input(self):
        parts = [{"part_number": "A"}, {"part_number": "B"}]
        assert _guidance_block(parts)["unique_part_count"] == 2

    def test_action_required_mentions_datasheet_and_webfetch(self):
        block = _guidance_block([])
        text = block["action_required"].lower()
        assert "datasheet" in text
        assert "webfetch" in text or "websearch" in text


class TestDesignReviewSnapshotOrchestration:
    """Smoke-test the orchestration against a fake bridge to verify
    each requested section maps to exactly one bridge call and errors
    are captured without aborting the whole snapshot.
    """

    @pytest.mark.asyncio
    async def test_snapshot_collects_sections_and_records_failures(
        self, monkeypatch
    ):
        called: list[tuple[str, dict, float]] = []

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                called.append((command, params or {}, timeout or 0))
                if command == "pcb.get_unrouted_nets":
                    raise RuntimeError("simulated timeout")
                if command == "pcb.get_components":
                    return {
                        "components": [
                            {"designator": "U1", "Comment": "STM32F411"},
                            {"designator": "U2", "Comment": "STM32F411"},
                            {"designator": "R1", "Comment": "10k"},
                        ]
                    }
                if command == "project.get_bom":
                    return {
                        "bom": [
                            {
                                "Manufacturer": "ST",
                                "ManufacturerPartNumber": "STM32F411RE",
                                "Designator": "U1,U2",
                            },
                        ]
                    }
                return {"ok": True, "command": command}

        monkeypatch.setattr(
            "eda_agent.tools.review.get_bridge", lambda: FakeBridge()
        )

        from eda_agent.tools import review as review_mod

        # Rebuild the tool by registering against a dummy MCP.
        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        review_mod.register_review_tools(DummyMcp())
        snapshot = captured["design_review_snapshot"]

        result = await snapshot(
            sections=["project_info", "components", "unrouted"],
            include_bom=True,
        )

        commands_called = {c for c, _, _ in called}
        assert "project.get_focused" in commands_called
        assert "pcb.get_components" in commands_called
        assert "pcb.get_unrouted_nets" in commands_called
        assert "project.get_bom" in commands_called

        assert "project_info" in result
        assert "components" in result
        # unrouted failed — must NOT be in result but MUST be in failed list.
        assert "unrouted" not in result
        assert any(
            f["section"] == "unrouted" for f in result["_sections_failed"]
        )

        # BOM-derived unique parts.
        parts = result["_unique_parts"]
        assert len(parts) == 1
        assert parts[0]["part_number"] == "STM32F411RE"
        assert parts[0]["manufacturer"] == "ST"

        # Guidance block always present and references datasheets.
        guidance = result["_review_guidance"]
        assert guidance["unique_part_count"] == 1
        assert "datasheet" in guidance["reminder"].lower()

    @pytest.mark.asyncio
    async def test_unknown_section_recorded_in_failures(self, monkeypatch):
        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                return {"ok": True}

        monkeypatch.setattr(
            "eda_agent.tools.review.get_bridge", lambda: FakeBridge()
        )

        from eda_agent.tools import review as review_mod

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        review_mod.register_review_tools(DummyMcp())
        snapshot = captured["design_review_snapshot"]

        result = await snapshot(
            sections=["project_info", "not_a_real_section"],
            include_bom=False,
        )

        assert "project_info" in result
        assert "_sections_failed" in result
        assert any(
            f["section"] == "not_a_real_section"
            and "Unknown section" in f["error"]
            for f in result["_sections_failed"]
        )

    @pytest.mark.asyncio
    async def test_datasheet_checklist_returns_rules(self, monkeypatch):
        from eda_agent.tools import review as review_mod

        captured = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        review_mod.register_review_tools(DummyMcp())
        checklist = captured["datasheet_checklist"]
        result = await checklist()
        assert "datasheet_rules" in result
        assert result["datasheet_rules"] == DATASHEET_RULES
        assert "action_required" in result
