# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Symbol conventions, checked rather than learned.

The KiCad Library Convention is a written ruleset for what a well-formed
schematic symbol looks like. It is worth encoding directly: a convention
somebody already wrote down does not need a model to rediscover it, and
a rule states its verdict with a reason where a learned score states a
number.

This is the symbol half. The footprint half already exists in
``footprint_policy`` (layers, courtyard, designator duplication), and the
rules here deliberately do not overlap it.

WHY RULES AND NOT A MODEL. A preference model fitted to 312 layout
comparisons scored 58% on a binary choice, because the six features it
had were global scalars that cannot see a convention. Grid alignment,
pin length and electrical typing are not matters of taste and there is
nothing to learn about them.

WHAT IS DELIBERATELY NOT CHECKED. Anything needing the rendered glyph
(text overlap, body proportions) or the datasheet (whether a pin is
named correctly). Those are real KLC concerns and cannot be decided from
the pin table alone, so they are absent rather than guessed at.

Severities follow the rest of the project: 'error' means the symbol is
wrong, 'warning' means it is unconventional and will read oddly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: Schematic pins sit on a 100 mil grid so wires meet them without a
#: manual nudge. This is the rule that silently breaks connectivity: an
#: off-grid pin looks connected and is not.
GRID_MILS = 100

#: A pin shorter than this crowds the body; longer than this wastes
#: sheet. Both ends are conventions rather than errors.
MIN_PIN_LENGTH = 100
MAX_PIN_LENGTH = 300

#: Leaving every pin 'passive' is the default a generator emits when it
#: does not know better. It costs ERC its only signal, so a symbol with
#: several pins and no typing at all is called out.
_UNTYPED = ("", "passive", "unspecified")
_MIN_PINS_FOR_TYPING = 4


@dataclass
class KlcFinding:
    """One convention violation, with the reason it matters."""

    rule: str
    severity: str            # "error" | "warning"
    pin: str                 # pin designator, "" for symbol-wide findings
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "pin": self.pin,
            "message": self.message,
        }


@dataclass
class KlcReport:
    """Findings for one symbol, plus what was actually examined.

    ``pins_checked`` is reported because a clean result over zero pins
    and a clean result over forty are different answers, and only one of
    them is evidence.
    """

    symbol: str = ""
    pins_checked: int = 0
    findings: list[KlcFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ok": self.ok,
            "pins_checked": self.pins_checked,
            "error_count": sum(1 for f in self.findings if f.severity == "error"),
            "warning_count": sum(
                1 for f in self.findings if f.severity == "warning"),
            "findings": [f.as_dict() for f in self.findings],
        }


def _off_grid(value: int) -> bool:
    return int(value) % GRID_MILS != 0


def check_symbol(symbol: Any, name: str = "") -> KlcReport:
    """Check one symbol's pins against the conventions above.

    Accepts anything exposing ``pins`` as a sequence of objects with
    designator / x / y / length / electrical_type, which is the shape of
    ``design.symbols.SymbolModel``.
    """
    pins = list(getattr(symbol, "pins", None) or [])
    report = KlcReport(
        symbol=name or str(getattr(symbol, "lib_ref", "") or ""),
        pins_checked=len(pins),
    )
    if not pins:
        report.findings.append(KlcFinding(
            rule="S0", severity="warning", pin="",
            message="the symbol has no pins, so nothing could be checked"))
        return report

    seen: dict[str, int] = {}
    for pin in pins:
        ref = str(getattr(pin, "designator", "") or "?")

        # S4.1 -- an off-grid pin looks connected and is not.
        if _off_grid(getattr(pin, "x", 0)) or _off_grid(getattr(pin, "y", 0)):
            report.findings.append(KlcFinding(
                rule="S4.1", severity="error", pin=ref,
                message=(
                    f"pin {ref} sits at ({pin.x}, {pin.y}), off the "
                    f"{GRID_MILS} mil grid. A wire drawn on grid will not "
                    f"meet it, and the sheet looks connected while the "
                    f"netlist disagrees")))

        # S4.2 -- length is a convention at both ends.
        length = int(getattr(pin, "length", 0) or 0)
        if length % 50 != 0 or not (MIN_PIN_LENGTH <= length <= MAX_PIN_LENGTH):
            report.findings.append(KlcFinding(
                rule="S4.2", severity="warning", pin=ref,
                message=(
                    f"pin {ref} is {length} mil; convention is a multiple of "
                    f"50 between {MIN_PIN_LENGTH} and {MAX_PIN_LENGTH}")))

        # S4.3 -- a duplicate pin number is an error, not a style note.
        if ref in seen:
            report.findings.append(KlcFinding(
                rule="S4.3", severity="error", pin=ref,
                message=(
                    f"pin number {ref} appears more than once; the netlister "
                    f"cannot tell the two apart")))
        seen[ref] = seen.get(ref, 0) + 1

    # S5 -- a symbol where nothing is typed gives ERC nothing to work on.
    if len(pins) >= _MIN_PINS_FOR_TYPING:
        typed = [
            p for p in pins
            if str(getattr(p, "electrical_type", "") or "").lower()
            not in _UNTYPED
        ]
        if not typed:
            report.findings.append(KlcFinding(
                rule="S5", severity="warning", pin="",
                message=(
                    f"all {len(pins)} pins are untyped, which is what a "
                    f"generator emits when it does not know better. ERC "
                    f"cannot flag a driven output or a floating input "
                    f"without pin types")))

    return report


def check_symbols(symbols: dict[str, Any]) -> dict[str, Any]:
    """Check several symbols. ``symbols`` maps name to symbol model."""
    reports = [check_symbol(sym, name) for name, sym in symbols.items()]
    return {
        "symbols_checked": len(reports),
        "symbols_with_errors": sum(1 for r in reports if not r.ok),
        "reports": [r.as_dict() for r in reports],
    }
