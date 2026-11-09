# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A symbol's pins are checked against the datasheet's pin table."""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design.pin_table import check_pin_table, norm


def _pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF, several lines of Helvetica text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        body = " T* ".join(f"({ln}) Tj" for ln in lines)
        stream = f"BT /F1 10 Tf 14 TL 72 720 Td {body} ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


_TABLE = [["Electrical characteristics", "VIN max 6 V"],
          ["Pin Functions", "PIN NAME NO. I/O DESCRIPTION",
           "VIN 1 I Input supply", "GND 2 - Ground",
           "EN/UVLO 3 I Enable, active high", "RESET# 4 O Open drain reset",
           "VOUT 5 O Regulated output", "NC 6 - Not connected",
           "PG", "7 O Power good"]]


@pytest.fixture
def sheet(tmp_path):
    pdf = tmp_path / "ldo.pdf"
    pdf.write_bytes(_pdf(_TABLE))
    return str(pdf)


def _pins(*pairs):
    return [{"designator": d, "name": n} for d, n in pairs]


def test_pins_as_printed_pass_with_the_page_found_by_its_heading(sheet):
    out = check_pin_table({"path": sheet}, _pins(
        ("1", "VIN"), ("2", "GND"), ("3", "EN/UVLO"), ("4", "\\R\\E\\S\\E\\T\\"),
        ("5", "VOUT"), ("6", "NC"), ("7", "PG")))
    assert out["ok"], out
    assert out["pages"] == [2] and out["found"] == 7


def test_a_swapped_pair_is_refused_and_the_table_rows_shown(sheet):
    out = check_pin_table({"path": sheet, "page": "2"},
                          _pins(("1", "GND"), ("2", "VIN"), ("5", "VOUT")))
    assert not out["ok"]
    assert [m["pin"] for m in out["missing"]] == ["1", "2"]
    assert any("VIN 1" in r for r in out["missing"][0]["rows_with_number"])
    assert "1 GND" in out["reason"]


def test_a_page_without_the_table_finds_nothing(sheet):
    out = check_pin_table({"path": sheet, "page": "1"}, _pins(("1", "VIN")))
    assert not out["ok"] and out["missing"][0]["pin"] == "1"


@pytest.mark.parametrize("datasheet,words", [
    (None, "give datasheet"),
    ({"path": "C:/no/such.pdf"}, "names no file"),
])
def test_a_missing_datasheet_is_refused(datasheet, words):
    out = check_pin_table(datasheet, _pins(("1", "VIN")))
    assert not out["ok"] and words in out["reason"]


def test_names_compare_without_overbars_slashes_or_case():
    assert norm("\\R\\E\\S\\E\\T\\") == norm("RESET#") == norm("/reset") == "RESET"
    assert norm("VDD_IO") == norm("VDDIO")


def _tool(monkeypatch, checks_off=""):
    from eda_agent import preferences
    from eda_agent.tools import library

    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append(command)
            if command == "application.get_active_document":
                return {"file_path": "C:/lib/Parts.SchLib"}
            return {"success": True}

    monkeypatch.setattr(library, "get_bridge", lambda: Bridge())
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": checks_off})
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    library.register_library_tools(Mcp())
    return tools, sent


def test_the_ic_tool_builds_nothing_from_a_wrong_pin(monkeypatch, sheet):
    tools, sent = _tool(monkeypatch)
    out = asyncio.run(tools["lib_create_ic_symbol"](
        name="LDO", left_pins=_pins(("1", "VIN"), ("3", "EN")),
        right_pins=_pins(("5", "VOUT")), datasheet={"path": sheet, "page": "2"}))
    assert not out["ok"] and out["check"] == "symbol_pins"
    assert out["pin_table"]["missing"][0]["pin"] == "3"
    assert not any(c.startswith("library.") for c in sent)


def test_the_parts_tool_checks_every_part_and_the_shared_pins(monkeypatch, sheet):
    tools, sent = _tool(monkeypatch)
    out = asyncio.run(tools["lib_create_symbol_in_parts"](
        name="LDO", parts=[{"left_pins": _pins(("1", "VIN"))},
                           {"right_pins": _pins(("5", "VOUT"))}],
        shared_pins={"left_pins": _pins(("2", "VSS"))},
        datasheet={"path": sheet, "page": "2"}))
    assert not out["ok"] and out["pin_table"]["missing"][0]["name"] == "VSS"


def test_a_connector_named_by_number_needs_no_table(monkeypatch):
    tools, sent = _tool(monkeypatch)
    out = asyncio.run(tools["lib_create_ic_symbol"](
        name="HDR3", left_pins=_pins(("1", "1"), ("2", "2"), ("3", ""))
        , right_pins=[]))
    assert out.get("ok", True) is not False, out
    assert "number only" in out["pin_table"]["note"]


def test_the_panel_can_switch_the_pin_table_check_off(monkeypatch):
    tools, sent = _tool(monkeypatch, checks_off="symbol_pins")
    out = asyncio.run(tools["lib_create_ic_symbol"](
        name="LDO", left_pins=_pins(("1", "VIN")), right_pins=_pins(("5", "VOUT"))))
    assert out["unchecked"]["check"] == "symbol_pins"
    assert "library.add_pins" in sent


def test_pins_into_a_symbol_just_made_are_checked_too(monkeypatch, sheet):
    from eda_agent.tools import library
    monkeypatch.setattr(library, "_NEW_SYMBOL", [])
    tools, sent = _tool(monkeypatch)
    asyncio.run(tools["lib_create_symbol"](name="LDO"))
    bad = asyncio.run(tools["lib_add_pins"](
        pins=_pins(("1", "GND")), datasheet={"path": sheet, "page": "2"}))
    assert not bad["ok"] and bad["added"] == 0 and bad["symbol"] == "LDO"
    assert "library.add_pins" not in sent
    good = asyncio.run(tools["lib_add_pins"](
        pins=_pins(("1", "VIN")), datasheet={"path": sheet, "page": "2"}))
    assert good["pin_table"]["found"] == 1 and "library.add_pins" in sent
    # Another symbol made current: its pins are edits, not a new build.
    asyncio.run(tools["lib_set_current_component"](component_name="OLD"))
    assert asyncio.run(tools["lib_add_pins"](pins=_pins(("9", "X"))))["success"]
