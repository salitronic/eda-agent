# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every handler that makes a PCB rule refuses a name already on the board.

Altium adds a second rule of the same name without complaint, and its
priority order then decides which of the two applies, so a create run
twice left two rules where one was asked for.
"""
from __future__ import annotations

import re
from pathlib import Path

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routines(text: str):
    starts = [m.start() for m in re.finditer(r"(?m)^(?:Function|Procedure)\s+\w+", text)]
    for a, b in zip(starts, starts[1:] + [len(text)]):
        yield text[a:b]


def test_each_rule_factory_call_checks_the_name_first():
    makers = []
    for path in sorted(_PAS.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        for body in _routines(path.read_text(encoding="utf-8", errors="replace")):
            code = re.sub(r"\{[^}]*\}", "", body)
            made = code.find("PCBRuleFactory")
            if made < 0:
                continue
            name = re.match(r"\w+\s+(\w+)", body).group(1)
            makers.append(name)
            checked = code.find("PCB_FindRuleByName")
            assert 0 <= checked < made, (
                f"{path.name}:{name} makes a rule without first refusing a "
                "name already on the board (PCB_FindRuleByName)")
    assert {"PCB_CreateDesignRule", "PCB_CreateRoom"} <= set(makers)
