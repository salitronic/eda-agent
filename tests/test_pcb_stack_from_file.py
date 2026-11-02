# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The saved stack, read from the file, catches what the legacy read missed.

Found on a production board (AD 25.8.1, 2026-10-27). A 4- to 6-layer change
through pcb_add_layer / pcb_remove_layer / pcb_modify_layer read back as
intended in pcb_get_layer_stackup. The saved V9 stack held two dielectrics
between L1 and L2, one of them -0.3944 mil, summing to 1.77 mil where the
legacy view said 4.33. The records below copy that board's pattern (names
generic, values as found).
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.fileio import altium_pcb_stack as stack
from eda_agent.tools import pcb as pcb_tools

_TOP, _BOT = 16777217, 16842751


def _v9(entries):
    out = []
    for i, e in enumerate(entries):
        for k, v in e.items():
            out.append(f"V9_STACK_LAYER{i}_{k}={v}")
    return out


def _legacy(chain):
    out = []
    numbers = [n for n, *_ in chain]
    for i, (n, name, cop, dt, h) in enumerate(chain):
        prev = numbers[i - 1] if i else 0
        nxt = numbers[i + 1] if i + 1 < len(numbers) else 0
        out += [f"LAYER{n}NAME={name}", f"LAYER{n}PREV={prev}", f"LAYER{n}NEXT={nxt}",
                f"LAYER{n}COPTHICK={cop}", f"LAYER{n}DIELTYPE={dt}",
                f"LAYER{n}DIELHEIGHT={h}"]
    return out


def _data(v9, legacy) -> bytes:
    return ("|RECORD=Board|" + "|".join(v9 + legacy) + "|").encode("latin-1")


def _cu(name, layer_id, t="1.378mil"):
    return {"NAME": name, "LAYERID": layer_id, "COPTHICK": t}


def _diel(name, n, t, kind="2", dk="4.000"):
    return {"NAME": name, "LAYERID": 17039360 + n, "DIELTYPE": kind,
            "DIELHEIGHT": t, "DIELCONST": dk, "DIELMATERIAL": "FR-4"}


MASK_TOP = {"NAME": "Top Solder", "LAYERID": 16973834, "DIELTYPE": "3",
            "DIELHEIGHT": "0.4mil", "DIELCONST": "3.500"}
MASK_BOT = {"NAME": "Bottom Solder", "LAYERID": 16973835, "DIELTYPE": "3",
            "DIELHEIGHT": "0.4mil", "DIELCONST": "3.500"}

BROKEN = _data(
    _v9([{"NAME": "Top Overlay", "LAYERID": 16973830}, MASK_TOP,
         _cu("L1", _TOP), _diel("Dielectric 1", 1, "-0.3944mil"),
         _diel("Dielectric 2", 4, "2.165mil"), _cu("L2", _TOP + 1),
         _diel("Dielectric 6", 6, "3.937mil", "1"), _cu("L3", _BOT),
         MASK_BOT]),
    _legacy([(1, "L1", "1.378mil", 2, "4.33mil"),
             (2, "L2", "1.378mil", 1, "3.937mil"),
             (32, "L3", "1.378mil", 0, "12.6mil")]))

GOOD = _data(
    _v9([MASK_TOP, _cu("Top", _TOP, "0.7mil"),
         _diel("Dielectric 1", 1, "4.7244mil"), _diel("Dielectric 2", 4, "4.7244mil"),
         _cu("Plane", 16842753, "0.7mil"), _diel("Dielectric 3", 2, "55.1181mil", "1", "4.550"),
         _cu("Bottom", _BOT, "0.7mil"), MASK_BOT]),
    _legacy([(1, "Top", "0.7mil", 2, "9.4488mil"),
             (39, "Plane", "0.7mil", 1, "55.1181mil"),
             (32, "Bottom", "0.7mil", 0, "12.6mil")]))


def _read(monkeypatch, data):
    monkeypatch.setattr(stack, "read_board_data", lambda path: data)
    return stack.read_stack("board.PcbDoc")


def test_the_broken_write_is_caught(monkeypatch):
    out = _read(monkeypatch, BROKEN)
    assert out["ok"] is False
    checks = [(f["severity"], f["check"]) for f in out["findings"]]
    assert ("error", "thickness") in checks
    assert ("error", "views_disagree") in checks
    disagree = next(f for f in out["findings"] if f["check"] == "views_disagree")
    assert "1.7706" in disagree["detail"] and "4.33" in disagree["detail"]


def test_a_good_stack_with_split_prepreg_passes(monkeypatch):
    out = _read(monkeypatch, GOOD)
    assert out["ok"] is True
    assert [f["check"] for f in out["findings"]] == ["split_dielectric"]
    assert out["summary"]["copper_layers"] == 3
    assert out["summary"]["dielectric_layers"] == 3
    assert out["summary"]["board_mils"] == pytest.approx(2.1 + 64.5669, abs=1e-3)


def test_kinds_come_from_the_layer_id():
    layers = stack.physical_stack(stack.board_keys(GOOD))
    assert [l["kind"] for l in layers] == [
        "solder_mask", "copper", "dielectric", "dielectric", "copper",
        "dielectric", "copper", "solder_mask"]
    assert layers[5]["dielectric_type"] == "core"
    assert layers[5]["dielectric_constant"] == pytest.approx(4.55)


def test_copper_against_copper_is_an_error():
    layers = [{"name": "A", "kind": "copper", "thickness_mils": 1.0},
              {"name": "B", "kind": "copper", "thickness_mils": 1.0}]
    assert stack.check(layers, [])[0]["check"] == "no_dielectric"


def test_copper_order_differing_between_views_is_an_error(monkeypatch):
    data = _data(
        _v9([_cu("Top", _TOP), _diel("D1", 1, "5mil"), _cu("Bottom", _BOT)]),
        _legacy([(1, "Bottom", "1.378mil", 2, "5mil"),
                 (32, "Top", "1.378mil", 0, "0mil")]))
    out = _read(monkeypatch, data)
    assert out["ok"] is False and out["findings"][0]["check"] == "views_disagree"


@pytest.mark.parametrize("text,mils", [
    ("1.378mil", 1.378), ("0.035mm", 0.035 / 0.0254), ("35um", 35 / 25.4),
    ("12", 12.0), ("-0.3944mil", -0.3944), ("", None), ("abc", None)])
def test_units(text, mils):
    got = stack.to_mils(text)
    assert got == (pytest.approx(mils) if mils is not None else None)


def test_expected_stack_mismatches_are_listed():
    layers = stack.physical_stack(stack.board_keys(GOOD))
    expected = [{"kind": "copper", "name": "Top"},
                {"kind": "dielectric", "thickness_mils": 4.7244},
                {"kind": "dielectric", "thickness_mils": 4.7244},
                {"kind": "copper", "name": "Plane"},
                {"kind": "dielectric", "thickness_mils": 37.4, "dielectric_type": "core"},
                {"kind": "copper", "name": "Bottom"}]
    out = stack.compare_expected(layers, expected)
    assert [(m["check"], m["order"]) for m in out] == [("thickness_mils", 4)]


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append(command)
        return {"success": True}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return mcp.tools, bridge


def test_the_tool_reads_a_file_and_applies_expected(monkeypatch, tmp_path):
    board = tmp_path / "b.PcbDoc"
    board.write_bytes(b"not read")
    monkeypatch.setattr(stack, "read_board_data", lambda path: GOOD)
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["pcb_stack_from_file"](
        str(board), expected=[{"kind": "copper", "name": "Top"}]))
    assert out["ok"] is False and out["mismatches"][0]["check"] == "count"
    assert bridge.sent == []
    missing = asyncio.run(tools["pcb_stack_from_file"](str(tmp_path / "none.PcbDoc")))
    assert missing["ok"] is False


def test_the_stack_reader_is_classed_offline():
    from eda_agent.tools import metadata

    assert metadata.maturity_of("pcb_stack_from_file") == metadata.OFFLINE


ADDITIVE = _data(
    _v9([_cu("Top", _TOP), _diel("D1", 1, "4mil"),
         dict(_cu("Mid", _TOP + 1), ISADDITIVELAYER="TRUE",
              ELECTRICALRESISTIVITY="2147483647.000000"),
         _diel("D2", 2, "4mil"), _cu("Bottom", _BOT)]),
    _legacy([(1, "Top", "1.378mil", 1, "4mil"), (2, "Mid", "1.378mil", 1, "4mil"),
             (32, "Bottom", "1.378mil", 0, "0mil")]))


def test_additive_or_non_conductive_copper_is_an_error(monkeypatch):
    """A board after copper was inserted through the pre-AD19 stack: thicknesses right,
    two inner layers printed-electronics additive, one non-conductive. Altium
    drew it paper-thin in 3D with no holes (2026-10-27)."""
    out = _read(monkeypatch, ADDITIVE)
    assert out["ok"] is False
    checks = [f["check"] for f in out["findings"] if f["severity"] == "error"]
    assert checks == ["additive", "non_conductive"]
    mid = next(layer for layer in out["layers"] if layer["name"] == "Mid")
    assert mid["additive"] is True and mid["resistivity"] > 1e9
    top = next(layer for layer in out["layers"] if layer["name"] == "Top")
    assert top["additive"] is False and top["resistivity"] is None


def _model(layers):
    import base64
    import zlib

    body = "".join(
        f'<Layer Id="{lid}" TypeId="t" Name="{name}"><Properties>'
        f'<Property Name="Thickness" Type="LengthValue">{thick}</Property>'
        f'</Properties></Layer>' for lid, name, thick in layers)
    xml = f'<StackupDocument><Layers>{body}</Layers></StackupDocument>'
    return base64.b64encode(zlib.compress(xml.encode())).decode()


def test_a_stale_layer_stack_manager_model_is_a_warning(monkeypatch):
    """A script stack write updated the stack but not the manager's own XML, which
    went on showing the old 4-layer values (AD 25.8.1, 2026-10-27)."""
    v9 = [dict(_cu("Top", _TOP), ID="{AAA}"), dict(_diel("D1", 1, "4.33mil"), ID="{BBB}"),
          dict(_cu("Bottom", _BOT), ID="{CCC}")]
    model = _model([("aaa", "Top Layer", "0.01778mm"), ("bbb", "Dielectric 1", "0.11mm"),
                    ("ccc", "Bottom Layer", "0.035mm")])
    data = _data(_v9(v9) + [f"V9_STACKCUSTOMDATA={model}"],
                 _legacy([(1, "Top", "1.378mil", 2, "4.33mil"), (32, "Bottom", "1.378mil", 0, "0mil")]))
    out = _read(monkeypatch, data)
    assert out["ok"] is True                       # a warning, not an error
    (warn,) = [f for f in out["findings"] if f["check"] == "stack_manager_model_stale"]
    assert len(warn["layers"]) == 1 and warn["layers"][0].startswith("Top:")
    assert "pcb_reset_stack_manager_model" in warn["detail"]


def test_the_reset_is_refused_while_altium_has_the_board_open(monkeypatch, tmp_path):
    board = tmp_path / "b.PcbDoc"
    board.write_bytes(b"x")

    class _Open(_Bridge):
        async def send_command_async(self, command, params=None, timeout=None):
            self.sent.append(command)
            return [{"file_path": str(board), "loaded": True}]

    bridge = _Open()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    called = []
    monkeypatch.setattr(stack, "reset_manager_model",
                        lambda path, dry_run=True: called.append(path) or {"found": 1})
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_reset_stack_manager_model"](str(board), dry_run=False))
    assert out["ok"] is False and "close it first" in out["reason"] and called == []
    dry = asyncio.run(mcp.tools["pcb_reset_stack_manager_model"](str(board)))
    assert dry["ok"] is True and called == [str(board)]
