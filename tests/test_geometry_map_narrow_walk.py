# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The geometry map's dump can be narrowed to kinds and one layer.

The dump walked every free primitive of the board on every layer before
each apply: 2.7 s on a 10-layer board, paid by a caller that edits a few
dozen tracks on one mechanical layer. pcb.dump_free_primitives now takes
``kinds`` and ``layer``; the dump writes the walk it used as a W line, the
edit file carries it, and pcb.apply_free_primitives walks the same set, so
the ordinals the apply counts are the dump's.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from eda_agent.geometry_map import build_edits, parse_dump

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"
C = 10000  # internal units per mil


def _routine(name: str) -> str:
    code = _PCB.read_text(encoding="ascii")
    start = re.search(rf"(?m)^Function {name}\(", code)
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"\{[^}]*\}", "", body)


# --- Python: the W line travels from the dump into the edit file -------------


def test_a_narrowed_dump_is_read_with_its_walk():
    dump = parse_dump("W\ttrack,arc\tMechanical21\n"
                      f"T\t0\t57\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t57\tMechanical21\n")
    assert dump.walk == ("track,arc", "Mechanical21")
    assert len(dump.tracks) == 1


def test_the_edit_file_walks_what_the_dump_walked():
    dump = parse_dump("W\ttrack\tMechanical21\n"
                      f"T\t3\t57\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t57\tMechanical21\n")
    lines, match = build_edits({"tracks": [{"layer": "Mechanical21", "width": 10,
                                            "key": [0, 0, 50, 0],
                                            "new": [1, 1, 51, 1]}]}, dump)
    assert match["tracks"]["matched"] == 1
    assert lines[0] == "W\ttrack\tMechanical21"
    assert lines[1].startswith("T\t3\t")


def test_a_whole_board_dump_writes_no_walk():
    dump = parse_dump(f"T\t0\t1\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t1\tTopLayer\n")
    assert dump.walk is None
    lines, _ = build_edits({"tracks": [{"layer": "TopLayer", "width": 10,
                                        "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}]}, dump)
    assert not any(l.startswith("W\t") for l in lines)


class _Bridge:
    def __init__(self, workspace, dump_text):
        self.config = type("Cfg", (), {"workspace_dir": workspace})()
        self.dump_text = dump_text
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.dump_free_primitives":
            Path(params["out_path"]).write_text(self.dump_text, encoding="ascii")
            return {"board": params.get("board_path", ""), "tracks": 1}
        if command == "pcb.apply_free_primitives":
            return {"applied": 1, "stale": 0, "missing": 0}
        raise AssertionError(command)


def _tools(monkeypatch, bridge):
    import eda_agent.tools.pcb as pcb

    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(Mcp())
    return tools


@pytest.mark.asyncio
async def test_the_tool_narrows_the_dump_and_the_apply_follows(tmp_path, monkeypatch):
    bridge = _Bridge(tmp_path, "W\ttrack\tMechanical21\n"
                               f"T\t0\t57\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t57\tMechanical21\n")
    tools = _tools(monkeypatch, bridge)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"tracks": [{"layer": "Mechanical21", "width": 10,
                                          "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}]}))
    out = await tools["pcb_apply_geometry_map"](str(mp), board_path="b",
                                                kinds=" track ", layer="Mechanical21")
    command, params = bridge.sent[0]
    assert command == "pcb.dump_free_primitives"
    assert params["kinds"] == "track" and params["layer"] == "Mechanical21"
    edits = Path(bridge.sent[-1][1]["in_path"]).read_bytes()
    assert edits.startswith(b"\xef\xbb\xbfW\ttrack\tMechanical21\n")
    assert out["apply"]["applied"] == 1


@pytest.mark.asyncio
async def test_the_tool_sends_no_filter_unless_asked(tmp_path, monkeypatch):
    bridge = _Bridge(tmp_path, f"T\t0\t1\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t1\tTopLayer\n")
    tools = _tools(monkeypatch, bridge)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"tracks": []}))
    await tools["pcb_apply_geometry_map"](str(mp), board_path="b", dry_run=True)
    params = bridge.sent[0][1]
    assert "kinds" not in params and "layer" not in params


# --- Pascal: the dump narrows, the apply walks the same set -------------------


def test_the_dump_reads_kinds_and_a_layer_and_refuses_what_it_cannot_walk():
    body = _routine("PCB_DumpFreePrimitives")
    assert "ExtractJsonValue(Params, 'kinds')" in body
    assert "GmKindsBad(Kinds)" in body and "'BAD_KINDS'" in body
    assert "Walk := GmWalkSet(Board, LayerStr, Bad);" in body and "'UNKNOWN_LAYER'" in body


def test_every_kind_is_walked_only_when_asked_and_on_the_narrowed_set():
    body = _routine("PCB_DumpFreePrimitives")
    for kind in ("track", "arc", "via", "text", "fill", "region", "polygon"):
        assert "If GmWants(Kinds, '%s') Then" % kind in body, kind
    assert body.count("Iter.AddFilter_IPCB_LayerSet(Walk);") == 7
    assert body.count("If Walk = Nil Then") == 7


def test_the_dump_writes_its_walk_only_when_narrowed():
    body = _routine("PCB_DumpFreePrimitives")
    w = body.index("Dump.Add('W' + #9 + Kinds + #9 + LayerStr);")
    assert body.index("If (Kinds <> '') Or (LayerStr <> '') Then") < w
    assert w < body.index("If GmWants(Kinds, 'track') Then")


def test_the_apply_walks_the_dumps_set():
    body = _routine("PCB_ApplyFreePrimitives")
    w = body.index("If Copy(Lines[I], 1, 2) = 'W' + #9 Then")
    walk = body.index("Walk := GmWalkSet(Board, LayerStr, Bad);", w)
    assert walk < body.index("PcbBeginEdit(0);")
    # both walks: the kinds' and the polygons' own
    assert body.count("Iter.AddFilter_IPCB_LayerSet(Walk);") == 2


def _fn(head):
    code = _PCB.read_text(encoding="ascii")
    fn = code[code.index(head):]
    return fn[:re.search(r"(?m)^End;", fn).end()]


def test_the_listed_layers_are_one_v7_set():
    fn = _fn("Function GmWalkSet(Board : IPCB_Board; Layers : String; Var Bad : String)")
    assert "If Rest = '' Then Exit;" in fn            # no layer: every layer
    assert "Result := LayerSet.CreateLayerSet;" in fn
    assert "OnLayer := ResolveLayerId(Board, Name);" in fn
    assert "Result.Include(OnLayer);" in fn
    assert "Bad := Name;" in fn                        # an unknown one is named


def test_every_kind_writes_through_the_filters_but_counts_every_walked_object():
    body = _routine("PCB_DumpFreePrimitives")
    assert body.count("GmKeep(") == 7
    assert "GmNameOk(PName, NameMask)" in body
    # the ordinal counts every object of the walk, written or not
    for n in ("NT", "NA", "NV", "NX", "NF", "NR", "NP"):
        assert re.search(r"Inc\(%s\);\s*End;\s*Inc\(Idx\);" % n, body), n


def test_the_write_filters_are_read_and_bad_bounds_refused():
    body = _routine("PCB_DumpFreePrimitives")
    for key in ("'nets'", "'selected'", "'name'", "'bounds'"):
        assert "ExtractJsonValue(Params, %s)" % key in body, key
    assert "'BAD_BOUNDS'" in body and "If BoxV.Count <> 4 Then" in body


def test_the_write_filters_say_what_they_keep():
    keep = _fn("Function GmKeep(")
    assert "If NName = '' Then Exit;" in keep          # no net: not on a listed net
    assert "Pos(',' + UpperCase(NName) + ',', ',' + UpperCase(Nets) + ',') = 0" in keep
    assert "If Not Sel Then Exit;" in keep
    assert "(BR.Right < BX1) Or (BR.Left > BX2) Or (BR.Top < BY1) Or (BR.Bottom > BY2)" in keep
    name = _fn("Function GmNameOk(")
    assert "If Copy(Mask, Length(Mask), 1) = '*' Then" in name


@pytest.mark.asyncio
async def test_the_tool_passes_every_write_filter(tmp_path, monkeypatch):
    bridge = _Bridge(tmp_path, f"T\t0\t1\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t1\tTopLayer\n")
    tools = _tools(monkeypatch, bridge)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"tracks": []}))
    await tools["pcb_apply_geometry_map"](str(mp), board_path="b", dry_run=True,
                                          kinds="polygon", layer="Mechanical21,Mechanical22",
                                          nets="GND,VCC", selected=True,
                                          name="FLUIDGON*", bounds="0,0,100,100")
    params = bridge.sent[0][1]
    assert params["layer"] == "Mechanical21,Mechanical22"
    assert params["nets"] == "GND,VCC" and params["selected"] == "true"
    assert params["name"] == "FLUIDGON*" and params["bounds"] == "0,0,100,100"
