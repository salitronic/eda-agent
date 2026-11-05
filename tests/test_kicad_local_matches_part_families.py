# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The local KiCad library is found by a full part number.

KiCad names a family with x for the letters that vary: the round LED
board's STM32F042F6P6 is "STM32F042F6Px" there, and a search for the
part number found nothing.
"""

from __future__ import annotations

from eda_agent.libimport.providers.kicad_local import KicadLocalProvider


def _provider(symbols):
    p = KicadLocalProvider.__new__(KicadLocalProvider)
    p.name = "kicad_local"
    p._index = lambda: symbols
    return p


def test_a_full_part_number_finds_its_family():
    p = _provider([("MCU_ST_STM32F0", "STM32F042F6Px"),
                   ("MCU_ST_STM32F0", "STM32F042K6Tx")])
    hits = p.search("STM32F042F6P6")
    assert [h.mpn for h in hits] == ["STM32F042F6Px"]
    assert hits[0].extra["wildcard"] is True


def test_exact_hits_come_first():
    p = _provider([("A", "LM358x"), ("B", "LM3580")])
    assert [h.mpn for h in p.search("LM3580")] == ["LM3580", "LM358x"]
