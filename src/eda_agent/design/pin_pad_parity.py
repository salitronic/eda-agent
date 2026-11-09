# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Symbol pin numbers against footprint pad names, before they are linked.

A symbol numbered 1 to 8 linked to a footprint whose pads are A1 to B4, or
one pin short of its exposed pad, compiles cleanly and is only found on the
board: the pad has no net, or the pin goes nowhere.
"""
from __future__ import annotations

from typing import Any, Iterable


def _plated(pad: dict[str, Any]) -> bool:
    return str(pad.get("plated", "true")).strip().lower() not in ("false", "0", "no")


def pin_pad_parity(pins: Iterable[dict[str, Any]],
                   pads: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """``{ok, pins, pads, pins_without_pad, pads_without_pin, ignored_pads}``.

    Every pin number needs a pad of that name, and every named plated pad
    a pin. Pads with no name, and unplated holes, carry no net and are left
    out (``ignored_pads``). Several pads of one name (an exposed pad drawn
    in parts) and one number on several parts of a symbol are allowed.
    Alternate display modes of a pin are the same pin and count once.
    """
    numbers: list[str] = []
    for pin in pins:
        if int(pin.get("display_mode", 0) or 0) != 0:
            continue
        numbers.append(str(pin.get("designator", "")).strip())
    named: list[str] = []
    ignored = 0
    for pad in pads:
        name = str(pad.get("name", "")).strip()
        if not name or not _plated(pad):
            ignored += 1
            continue
        named.append(name)
    pad_set = {n.upper() for n in named}
    pin_set = {n.upper() for n in numbers if n}
    unnumbered = sum(1 for n in numbers if not n)
    no_pad = sorted({n for n in numbers if n and n.upper() not in pad_set}, key=_order)
    no_pin = sorted({n for n in named if n.upper() not in pin_set}, key=_order)
    out: dict[str, Any] = {
        "ok": not no_pad and not no_pin and not unnumbered,
        "pins": len(pin_set), "pads": len(pad_set),
        "pins_without_pad": no_pad, "pads_without_pin": no_pin,
        "ignored_pads": ignored}
    if unnumbered:
        out["pins_without_number"] = unnumbered
    return out


def _order(name: str) -> tuple[Any, ...]:
    """1, 2, 10 in number order; A1, A2, B1 by letters then number."""
    head = name.rstrip("0123456789")
    tail = name[len(head):]
    return (head, int(tail) if tail else -1, name)


def parity_reason(found: dict[str, Any], footprint: str) -> str:
    """The refusal, naming what differs."""
    parts = []
    if found.get("pins_without_pad"):
        parts.append("pins with no pad: " + ", ".join(found["pins_without_pad"][:20]))
    if found.get("pads_without_pin"):
        parts.append("pads with no pin: " + ", ".join(found["pads_without_pin"][:20]))
    if found.get("pins_without_number"):
        parts.append(f"{found['pins_without_number']} pin(s) without a number")
    return (f"the symbol's pins and {footprint}'s pads do not match ({'; '.join(parts)}). "
            "Renumber the pins or rename the pads so each pin has its pad, "
            "then link again")
