# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The Layer Stack Manager's impedance solver runs from a tool (issue #397).

The document the manager opens for the board has ComputeImpedance, with no
arguments and no dialog; ApplyToPcb writes what it solved into the board.
What a line solves is its own calculation mode in the manager's model:
TraceWidth finds the width, TraceClearance the pair gap, Impedance only the
impedance of the width as it is. The apply writes the manager's whole copy
of the stack, so it is refused while that copy disagrees with the board's
stack, as pcb_move_layer is. Measured on AD 25.8.1.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.layout import stack_model as sm
from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load, strip_comments

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_ComputeImpedance"]


# --- the script ---------------------------------------------------------------

def test_the_command_is_routed():
    text = _PCB.read_text(encoding="latin-1")
    assert re.search(r"'compute_impedance':\s+Result := PCB_ComputeImpedance\(Params, RequestId\);",
                     text)


def test_the_manager_is_refused_where_the_script_cannot_reach_it():
    body = _body()
    assert body.index("Result := LayerStackManagerRefusal(RequestId);") < body.index(
        "LSM := LayerStackManager;")


def test_the_cached_provider_is_dropped_before_the_document_opens():
    body = _body()
    drop = body.index("LSM.RemoveLayerStackProvider(Board);")
    assert drop < body.index("Doc := LSM.OpenDocumentFromPcb(Board);")


def test_the_model_is_read_before_the_solve():
    body = _body()
    assert body.index("Before := Doc.SerializeToString(1);") < body.index("Doc.ComputeImpedance;")


def test_a_failed_solve_is_refused_and_nothing_applied():
    body = _body()
    solve = body.index("Doc.ComputeImpedance;")
    fail = body.index("'SOLVE_FAILED'")
    assert solve < fail < body.index("Doc.ApplyToPcb(Board.FileName);")
    assert "Exit;" in body[fail:fail + 200]


def test_the_board_is_written_only_when_asked():
    body = _body()
    assert "DoApply := LowerCase(Trim(ExtractJsonValue(Params, 'apply'))) <> 'false';" in body
    gate = body.index("If DoApply Then")
    assert gate < body.index("Doc.ApplyToPcb(Board.FileName);")
    assert body.index("'APPLY_FAILED'") > gate


def test_after_an_apply_the_model_is_read_again_from_the_board():
    body = _body()
    tail = body[body.index("Doc.ApplyToPcb(Board.FileName);"):]
    drop = tail.index("LSM.RemoveLayerStackProvider(Board);")
    reopen = tail.index("Doc := LSM.OpenDocumentFromPcb(Board);")
    assert drop < reopen < tail.index("After := Doc.SerializeToString(1);")
    assert "MarkDocDirtyByPath(Board.FileName);" in tail


def test_the_reply_carries_both_models_and_the_times():
    body = _body()
    for key in ("applied", "solve_ms", "apply_ms", "model_before", "model_after"):
        assert f"'{key}'" in body


def test_no_line_mode_is_written():
    # This tool solves; it never changes what a line solves.
    code = strip_comments(_body())
    assert "CalcMode" not in code and "LoadFromFile" not in code


# --- the model's lines --------------------------------------------------------

def _model(l1_mode="TraceWidth", l1_width="7.5mil", l1_zo="49.6158815ohm",
           l8_gap="5mil", l8_err="0.768237%", s50_width="0mil", s50_z="0ohm",
           s50_gap="5mil"):
    return (
        '\ufeff<StackupDocument xmlns="http://altium.com/ns/LayerStackManager">'
        '<Stackup><Stacks><Stack Id="s0" Name="Board Layer Stack"><Layers>'
        '<Layer Id="l1" Name="L1 (TOP)"/><Layer Id="l2" Name="L2 (GND)"/>'
        '<Layer Id="l3" Name="L3"/><Layer Id="l7" Name="L7 (GND)"/>'
        '<Layer Id="l8" Name="L8 (BOT)"/></Layers></Stack></Stacks>'
        '<ImpedanceProfiles>'
        '<ImpedanceProfile Id="p0" AutoName="D100" Type="Differential" '
        'Impedance="100ohm" Tolerance="10%"><TransmissionLines>'
        f'<TransmissionLine LayerId="l1" StackId="s0" IsDifferential="True" '
        f'CalcMode="{l1_mode}" BottomRefLayerId="l2" TraceWidth="{l1_width}" '
        f'TraceGap="5mil" ImpedanceError="0.768237%">'
        f'<ElectricParameters Ze="85.7ohm" Zo="{l1_zo}"/></TransmissionLine>'
        f'<TransmissionLine LayerId="l8" StackId="s0" IsDifferential="True" '
        f'CalcMode="TraceClearance" TopRefLayerId="l7" TraceWidth="7.5mil" '
        f'TraceGap="{l8_gap}" ImpedanceError="{l8_err}">'
        '<ElectricParameters Ze="85.7ohm" Zo="49.6158815ohm"/></TransmissionLine>'
        '</TransmissionLines></ImpedanceProfile>'
        '<ImpedanceProfile Id="p1" AutoName="S50" CustomName="SE-50" Type="Single" '
        'Impedance="50ohm" Tolerance="10%"><TransmissionLines>'
        f'<TransmissionLine LayerId="l3" StackId="s0" CalcMode="Impedance" '
        f'TopRefLayerId="l2" TraceWidth="{s50_width}" TraceGap="{s50_gap}" '
        f'ImpedanceError="0%">'
        f'<ElectricParameters Z="{s50_z}"/></TransmissionLine>'
        '</TransmissionLines></ImpedanceProfile>'
        '</ImpedanceProfiles></Stackup></StackupDocument>')


def test_each_line_names_its_mode_and_whether_it_is_a_pair():
    (d100, s50) = sm.read_stack_model(_model())["impedance_lines"]
    top, bot = d100["lines"]
    assert (top["calc_mode"], top["differential"]) == ("TraceWidth", True)
    assert (bot["calc_mode"], bot["differential"]) == ("TraceClearance", True)
    assert (s50["lines"][0]["calc_mode"], s50["lines"][0]["differential"]) == ("Impedance", False)


def test_a_pair_reads_twice_its_odd_mode_impedance():
    (d100, s50) = sm.read_stack_model(_model())["impedance_lines"]
    assert d100["lines"][0]["impedance_ohms"] == round(2 * 49.6158815, 6)
    # a single line keeps its own Z
    assert s50["lines"][0]["impedance_ohms"] == 0


def test_the_report_says_what_each_line_solves():
    rep = sm.impedance_solve_report(_model(), _model())
    got = [(r["profile"], r["layer"], r["solves"]) for r in rep["lines"]]
    assert got == [("D100", "L1 (TOP)", "width"), ("D100", "L8 (BOT)", "gap"),
                   ("SE-50", "L3", "impedance")]
    assert rep["lines_changed"] == 0
    assert all(r["changed"] == {} for r in rep["lines"])


def test_an_unknown_mode_is_reported_as_the_model_words_it():
    rep = sm.impedance_solve_report(_model(l1_mode="Manual"), _model(l1_mode="Future"))
    assert [r["solves"] for r in rep["lines"]][:1] == ["Future"]
    assert sm.SOLVES["Manual"] == "manual"


def test_the_report_gives_each_value_the_solve_changed():
    after = _model(l1_width="7.3224mil", l1_zo="50.0195474ohm", l8_gap="5.144mil",
                   l8_err="0.0008%")
    rep = sm.impedance_solve_report(_model(), after)
    top, bot, s50 = rep["lines"]
    assert top["changed"]["width_mils"] == [7.5, 7.3224]
    assert top["changed"]["ohms"] == [round(2 * 49.6158815, 6), round(2 * 50.0195474, 6)]
    assert "gap_mils" not in top["changed"]
    assert bot["changed"]["gap_mils"] == [5.0, 5.144]
    assert bot["changed"]["error_percent"] == [0.768237, 0.0008]
    assert "width_mils" not in bot["changed"]
    assert s50["changed"] == {}
    assert rep["lines_changed"] == 2


def test_a_single_line_has_no_gap():
    # The model may carry a gap on a single line; it means nothing there.
    rep = sm.impedance_solve_report(_model(), _model(s50_gap="8mil"))
    s50 = rep["lines"][2]
    assert s50["gap_mils"] is None
    assert s50["changed"] == {}
    assert rep["lines"][0]["gap_mils"] == 5.0


def test_tolerance_is_judged_on_the_size_of_the_error():
    rep = sm.impedance_solve_report(_model(), _model(l8_err="-12%"))
    top, bot, _ = rep["lines"]
    assert (top["within_tolerance"], bot["within_tolerance"]) == (True, False)
    assert (top["target_ohms"], bot["target_ohms"]) == (100, 100)
    assert rep["outside_tolerance"] == 1


def test_a_profile_is_matched_by_name_when_the_order_moved():
    before = _model()
    swapped = re.sub(r"(<ImpedanceProfile Id=\"p0\".*?</ImpedanceProfile>)"
                     r"(<ImpedanceProfile Id=\"p1\".*?</ImpedanceProfile>)",
                     r"\2\1", _model(s50_width="3mil", s50_z="51ohm"), flags=re.S)
    rep = sm.impedance_solve_report(before, swapped)
    s50 = rep["lines"][0]
    assert s50["profile"] == "SE-50"
    assert s50["changed"]["width_mils"] == [0, 3.0]
    assert s50["changed"]["ohms"] == [0, 51]


# --- the tool -----------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


_MANAGER = {"layers": [{"name": "L1", "type": "copper", "thickness_mils": 1.4}]}
_BOARD = {"layers": [{"name": "L1", "kind": "copper", "thickness_mils": 1.4}]}
_STALE = {"layers": [{"name": "L1", "kind": "copper", "thickness_mils": 2.8}]}
_SOLVED = _model(l1_width="7.3224mil", l1_zo="50.0195474ohm")


class _Bridge:
    def __init__(self, board=_BOARD, reply=None):
        self.sent = []
        self.board = board
        self.reply = reply

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.layer_stack_manager":
            return _MANAGER
        if command == "pcb.physical_stack":
            return self.board
        if command == "pcb.compute_impedance":
            if self.reply is not None:
                return self.reply
            return {"applied": params.get("apply") == "true", "solve_ms": 78,
                    "apply_ms": 109 if params.get("apply") == "true" else -1,
                    "model_before": _model(), "model_after": _SOLVED}
        return {"success": True}

    def solves(self):
        return [s for s in self.sent if s[0] == "pcb.compute_impedance"]


def _call(monkeypatch, bridge=None, **kw):
    bridge = bridge or _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools["pcb_compute_impedance"](**kw)), bridge


def test_a_solve_is_applied_by_default_and_reported_per_line(monkeypatch):
    out, bridge = _call(monkeypatch)
    assert bridge.solves() == [("pcb.compute_impedance", {"apply": "true"})]
    assert (out["applied"], out["solve_ms"], out["apply_ms"]) == (True, 78, 109)
    assert out["lines_changed"] == 1
    assert out["lines"][0]["changed"]["width_mils"] == [7.5, 7.3224]
    assert "model_before" not in out and "model_after" not in out


def test_an_apply_is_refused_while_the_manager_disagrees(monkeypatch):
    out, bridge = _call(monkeypatch, bridge=_Bridge(board=_STALE))
    assert out["ok"] is False
    assert out["out_of_step"][0]["field"] == "thickness_mils"
    assert "pcb_sync_stack_manager" in out["reason"]
    assert bridge.solves() == []


def test_force_applies_without_comparing(monkeypatch):
    out, bridge = _call(monkeypatch, bridge=_Bridge(board=_STALE), force=True)
    assert [s[0] for s in bridge.sent] == ["pcb.compute_impedance"]
    assert out["applied"] is True


def test_a_solve_without_apply_needs_no_comparison(monkeypatch):
    out, bridge = _call(monkeypatch, bridge=_Bridge(board=_STALE), apply=False)
    assert bridge.sent == [("pcb.compute_impedance", {"apply": "false"})]
    assert (out["applied"], out["apply_ms"]) == (False, -1)


def test_a_refusal_from_the_script_is_passed_on(monkeypatch):
    refusal = {"success": False, "error": {"code": "SOLVE_FAILED"}}
    out, _ = _call(monkeypatch, bridge=_Bridge(reply=refusal))
    assert out == refusal


def test_a_model_that_does_not_parse_is_said(monkeypatch):
    broken = {"applied": True, "solve_ms": 1, "apply_ms": 1,
              "model_before": "<StackupDocument", "model_after": "<StackupDocument"}
    out, _ = _call(monkeypatch, bridge=_Bridge(reply=broken))
    assert out["ok"] is False and out["applied"] is True
    assert "did not parse" in out["reason"]


def test_the_tool_is_known_to_need_altium_and_to_write():
    # "_compute_" in a name reads as a pure calculation; this one runs
    # Altium's solver on the board and writes the result into it.
    from eda_agent.safety import command_only_looks
    from eda_agent.tools import metadata as meta

    assert meta.maturity_of("pcb_compute_impedance") == meta.LIVE_ONLY
    assert meta.interaction_of("pcb_compute_impedance") == meta.SILENT
    assert not command_only_looks("pcb.compute_impedance")
