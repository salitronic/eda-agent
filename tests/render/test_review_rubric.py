# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the visual-review rubric + loop protocol."""

from __future__ import annotations

from eda_agent.render.review_rubric import visual_review_guidance


def test_schematic_rubric_shape():
    g = visual_review_guidance("schematic")
    assert g["rubric"], "schematic rubric must be non-empty"
    assert g["loop_protocol"], "loop protocol must be present"
    for item in g["rubric"]:
        assert "check" in item and item["check"]
        assert "audits" in item and isinstance(item["audits"], list)


def test_pcb_rubric_references_real_audits():
    g = visual_review_guidance("pcb")
    audits = {a for item in g["rubric"] for a in item["audits"]}
    # A few audits we know exist and that the PCB rubric should point at,
    # each by the tool that runs it.
    assert "audit kind=components_outside_board_outline" in audits
    assert "pcb_check_placement_collision" in audits


def test_aliases_resolve():
    assert visual_review_guidance("sch")["rubric"] == \
        visual_review_guidance("schematic")["rubric"]
    assert visual_review_guidance("board")["rubric"] == \
        visual_review_guidance("pcb")["rubric"]


def test_unknown_target_empty_rubric_but_protocol_present():
    g = visual_review_guidance("nonsense")
    assert g["rubric"] == []
    assert g["loop_protocol"]


def test_guidance_is_copied_not_shared():
    a = visual_review_guidance("pcb")
    a["rubric"][0]["check"] = "MUTATED"
    b = visual_review_guidance("pcb")
    assert b["rubric"][0]["check"] != "MUTATED"
