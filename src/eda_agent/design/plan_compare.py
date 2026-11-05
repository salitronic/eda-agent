# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Two DesignPlans side by side: which parts and nets came, went or changed,
and which pins changed net.

Used to review an edit before drawing it, or to hold a design read back
from its sheets (design_plan_from_sheet) against the plan it was drawn from.
"""
from __future__ import annotations

from typing import Any

from .plan import DesignPlan

#: The part fields compared.
COMPARED = ("lib_ref", "value", "footprint", "manufacturer", "mpn", "sheet")


def _pin_nets(plan: DesignPlan) -> dict[str, str]:
    return {f"{pin.refdes}.{pin.pin}": net.name for net in plan.nets for pin in net.pins}


def compare_plans(before: DesignPlan, after: DesignPlan) -> dict[str, Any]:
    old_parts = {p.refdes: p for p in before.parts}
    new_parts = {p.refdes: p for p in after.parts}
    edited = []
    for ref in sorted(old_parts.keys() & new_parts.keys()):
        delta = {}
        for name in COMPARED:
            was, now = getattr(old_parts[ref], name), getattr(new_parts[ref], name)
            if was != now:
                delta[name] = {"was": was, "now": now}
        if delta:
            edited.append({"refdes": ref, "fields": delta})

    old_pins, new_pins = _pin_nets(before), _pin_nets(after)
    old_nets, new_nets = set(old_pins.values()), set(new_pins.values())
    old_nets |= {n.name for n in before.nets}
    new_nets |= {n.name for n in after.nets}
    rewired = []
    for net in sorted(old_nets & new_nets):
        lost = sorted(p for p, n in old_pins.items() if n == net and new_pins.get(p) != net)
        gained = sorted(p for p, n in new_pins.items() if n == net and old_pins.get(p) != net)
        if lost or gained:
            rewired.append({"net": net, "lost_pins": lost, "gained_pins": gained})
    result = {
        "new_parts": sorted(new_parts.keys() - old_parts.keys()),
        "dropped_parts": sorted(old_parts.keys() - new_parts.keys()),
        "edited_parts": edited,
        "new_nets": sorted(new_nets - old_nets),
        "dropped_nets": sorted(old_nets - new_nets),
        "rewired_nets": rewired,
        "pins_changing_net": [{"pin": p, "was": old_pins[p], "now": new_pins[p]}
                              for p in sorted(old_pins.keys() & new_pins.keys())
                              if old_pins[p] != new_pins[p]],
    }
    result["identical"] = not any(result.values())
    return result
