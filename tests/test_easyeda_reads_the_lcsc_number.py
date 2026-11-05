# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An EasyEDA component reports its LCSC number, JLC class and stock.

Measured on the live service: the number sits at ``lcsc.number`` and in
the "Supplier Part" attribute, never at ``szlcsc.code``, so every
inspect answered ``lcsc_id: ""`` and an agent choosing JLC basic parts
could not see the class it was choosing by.
"""
from __future__ import annotations

from eda_agent.libimport.easyeda import parse_component


def _payload(**result):
    base = {"title": "X", "dataStr": {"head": {"c_para": {
        "Manufacturer Part": "STM32F071C8T6",
        "Supplier Part": "C89041",
        "JLCPCB Part Class": "Extended Part"}}}}
    base.update(result)
    return {"success": True, "result": base}


def test_the_live_layout_gives_number_class_and_stock():
    comp = parse_component(_payload(
        lcsc={"number": "C89041", "stock": 82},
        szlcsc={"number": "C89041", "stock": 0}))
    out = comp.to_dict()
    assert out["lcsc_id"] == "C89041"
    assert out["jlc_part_class"] == "Extended Part"
    assert out["lcsc_stock"] == 82


def test_the_supplier_part_attribute_alone_is_enough():
    comp = parse_component(_payload())
    assert comp.lcsc_id == "C89041"
    assert comp.lcsc_stock is None, "an unreported stock is not zero"


def test_the_older_code_spelling_still_reads():
    comp = parse_component({"result": {"szlcsc": {"code": "C7420"}}})
    assert comp.lcsc_id == "C7420"
