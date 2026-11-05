# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A layer can be moved up or down the stack, the Layer Stack Manager's way.

The manager's intelligent move swaps a layer with its neighbour of the
same kind and carries the dielectrics along. Its manager, document,
editors and provider are not in the script engine's identifier table, so
they are late bound through Variants. The provider is cached and answers
with the stack as it was before the last change unless it is dropped
first. On a symmetric stack the move mirrors itself on the opposite
layers; ``alone`` switches symmetry off for the move and must put it back
whatever happens.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_MoveLayer"]


def test_late_bound_objects_are_held_in_variants():
    body = _body()
    decl = re.search(r"LSM, Doc, DocEd, LayEd, StEd, LSP, LSI, LI : Variant;", body)
    assert decl
    assert "St : ILsmSdkLayerStack;" in body
    assert "Sts : ILsmSdkLayerStackCollection;" in body


def test_the_cached_provider_is_dropped_before_it_is_read():
    body = _body()
    drop = body.index("LSM.RemoveLayerStackProvider(Board);")
    assert drop < body.index("LSP := GetLayerStackProvider(Board);")


def test_the_layer_is_matched_by_name_ignoring_case():
    body = _body()
    assert "LowerCase(LI.GetState_Name) = LowerCase(LayerName)" in body
    assert "LayerGuid := LI.GetState_GUID;" in body
    assert "'NOT_FOUND'" in body


def test_a_move_is_asked_for_before_it_is_made():
    body = _body()
    for d in ("Up", "Down"):
        can = body.index(f"LayEd.CanIntelliMoveLayer{d}(LayerGuid, StackId)")
        move = body.index(f"LayEd.IntelliMoveLayer{d}(LayerGuid, StackId)")
        assert can < move
    refuse = body.index("If Not Can Then")
    assert refuse < body.index("LayEd.IntelliMoveLayerUp(")
    assert "Refused := True;" in body[refuse:refuse + 80]


def test_each_move_is_applied_to_the_board():
    body = _body()
    loop = body[body.index("While Done < Steps Do"):body.index("Finally")]
    assert loop.index("Doc := LSM.OpenDocumentFromPcb(Board);") < loop.index("IntelliMoveLayerUp(")
    after = loop[loop.index("IntelliMoveLayerDown("):]
    assert after.index("Doc.ApplyToPcb(Board.FileName);") < after.index("Inc(Done);")


def test_symmetry_is_switched_off_only_when_asked_and_restored_in_finally():
    body = _body()
    off = body.index("St.SetState_IsSymmetric(False);")
    assert "If Alone And WasSym Then" in body[:off]
    fin = body.index("Finally")
    assert off < fin
    tail = body[fin:]
    assert "If SymOff Then" in tail
    on = tail.index("St.SetState_IsSymmetric(True);")
    assert tail.index("Doc := LSM.OpenDocumentFromPcb(Board);") < on
    assert on < tail.index("Doc.ApplyToPcb(Board.FileName);")


def test_bad_direction_and_steps_are_refused():
    body = _body()
    assert "If (Dir <> 'up') And (Dir <> 'down') Then" in body
    assert "If (Steps < 1) Or (Steps > 64) Then" in body
    assert body.count("'BAD_PARAM'") == 2


def test_the_reply_says_what_happened():
    body = _body()
    for key in ("steps_requested", "steps_done", "refused", "symmetric",
                "alone", "stack"):
        assert f"'{key}'" in body
    assert "JsonBool('mirrored', WasSym And Not Alone)" in body
    assert '\'"order_before":\' + Before' in body
    assert '\'"order_after":\' + MoveLayerStackOrder(Board)' in body
    assert "MarkDocDirtyByPath(Board.FileName);" in body


def test_the_command_is_routed():
    text = _PCB.read_text(encoding="latin-1")
    assert re.search(r"'move_layer':\s+Result := PCB_MoveLayer\(Params, RequestId\);", text)


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


_MANAGER = {"layers": [{"name": "L1", "type": "copper", "thickness_mils": 1.4},
                       {"name": "D1", "type": "core", "thickness_mils": 8.0,
                        "dielectric_constant": "4", "material": "FR4"}]}
_BOARD = {"layers": [{"name": "L1", "kind": "copper", "thickness_mils": 1.4},
                     {"name": "D1", "kind": "dielectric", "thickness_mils": 8.0,
                      "constant": 4.0, "material": "FR4", "dielectric_type": 1}]}
_STALE = {"layers": [{"name": "L1", "kind": "copper", "thickness_mils": 2.8},
                     _BOARD["layers"][1]]}


class _Bridge:
    """The two reads the guard makes answer a stack; pcb.move_layer is
    recorded in ``moves``."""
    def __init__(self, board=_BOARD):
        self.sent = []
        self.board = board

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.layer_stack_manager":
            return _MANAGER
        if command == "pcb.physical_stack":
            return self.board
        return {"success": True}

    @property
    def moves(self):
        return [s for s in self.sent if s[0] == "pcb.move_layer"]


def _call(monkeypatch, board=_BOARD, **kw):
    bridge = _Bridge(board)
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_move_layer"](**kw))
    return out, bridge.moves


def test_params_reach_the_script(monkeypatch):
    _, sent = _call(monkeypatch, layer=" Layer 2 ", direction="Down", steps=2, alone=True)
    assert sent == [("pcb.move_layer", {
        "layer": "Layer 2", "direction": "down", "steps": "2", "alone": "true"})]


def test_defaults(monkeypatch):
    _, sent = _call(monkeypatch, layer="Layer 3", direction="up")
    assert sent[0][1] == {"layer": "Layer 3", "direction": "up", "steps": "1",
                          "alone": "false"}


def test_bad_input_is_refused_before_sending(monkeypatch):
    for kw in ({"layer": "L2", "direction": "left"},
               {"layer": "", "direction": "up"},
               {"layer": "L2", "direction": "up", "steps": 0},
               {"layer": "L2", "direction": "up", "steps": 65},
               {"layer": "L2", "direction": "up", "steps": True}):
        out, sent = _call(monkeypatch, **kw)
        assert out["ok"] is False and not sent, kw


def test_the_edges_of_steps_are_accepted(monkeypatch):
    for n in (1, 64):
        _, sent = _call(monkeypatch, layer="L2", direction="up", steps=n)
        assert sent[0][1]["steps"] == str(n)


# Measured on AD 25.8.1: once a board carries the Layer Stack Manager's own
# model, a script write to the board's stack does not reach it, and
# applying the manager's document (which every move does) wrote its old
# values back over the board's (copper 2.8 mil back to 1.4).
def test_a_stale_manager_refuses_the_move(monkeypatch):
    out, moves = _call(monkeypatch, board=_STALE, layer="L1", direction="down")
    assert out["ok"] is False and not moves
    assert out["out_of_step"] == [{"layer": "L1", "field": "thickness_mils",
                                   "board": 2.8, "stack_manager": 1.4}]


def test_force_moves_a_stale_board_without_comparing(monkeypatch):
    bridge = _Bridge(_STALE)
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    asyncio.run(mcp.tools["pcb_move_layer"](layer="L1", direction="down", force=True))
    assert [c for c, _ in bridge.sent] == ["pcb.move_layer"]


def test_symmetry_is_read_back_after_the_move():
    body = _body()
    restore = body.index("Finally")
    read = body.index("SymAfter := St.IsSymmetric;")
    assert restore < read < body.index("JsonBool('symmetric_after', SymAfter)")
