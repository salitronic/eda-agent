# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Design > Netlist > Update Free Primitives From Component Pads.

MEASURED (AD 25.8, 2026-11-02): PCB:Netlist with
Action=UpdateFreePrimitiveNets raises a modal box titled "Confirm",
"Update free-primitives nets from component-pad nets?", Yes / No, and the
script waits in RunProcess until it is answered. After Yes a track with no
net touching pad C60.1 had the net PGND.

The handler counts the free copper with no net before and after and lists
what gained a net. The tool presses Yes on that box and on nothing else,
and refuses before sending anything when it cannot press at all, because a
raw send would block the bridge until a person answers.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.bridge.exceptions import AltiumTimeoutError
from eda_agent.tools import metadata, pcb
from eda_agent.ui import dialog_report, uia, windows

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
PROMPT_TEXT = "Update free-primitives nets from component-pad nets?"


def _routine(name: str) -> str:
    text = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    at = text.index(f"Function {name}(")
    return text[at:text.index("\nEnd;", at)]


# --- Pascal ------------------------------------------------------------------

def test_the_command_is_dispatched():
    dispatch = _routine("HandlePCBCommand")
    assert ("'update_free_primitive_nets': Result := "
            "PCB_UpdateFreePrimitiveNets(Params, RequestId);") in dispatch


def test_the_netlist_process_runs_with_the_update_action():
    body = _routine("PCB_UpdateFreePrimitiveNets")
    reset = body.index("ResetParameters;")
    action = body.index("AddStringParameter('Action', 'UpdateFreePrimitiveNets');")
    run = body.index("RunProcess('PCB:Netlist');")
    assert reset < action < run


def test_no_board_is_refused():
    body = _routine("PCB_UpdateFreePrimitiveNets")
    assert "'NO_PCB'" in body
    assert body.index("'NO_PCB'") < body.index("RunProcess(")


def test_free_copper_is_counted_before_and_after_the_process():
    body = _routine("PCB_UpdateFreePrimitiveNets")
    run = body.index("RunProcess('PCB:Netlist');")
    before = body.index("NoNetBefore := FreeNetsWalk(Board, Seen, True,")
    after = body.index("NoNetAfter := FreeNetsWalk(Board, Seen, False, Changed, Listed);")
    assert before < run < after
    for key in ("no_net_before", "no_net_after", "changed_count", "changed_truncated"):
        assert f"'{key}'" in body
    assert '"changed":[\' + Listed' in body


def test_the_walk_is_filtered_to_free_copper():
    walk = _routine("FreeNetsWalk")
    assert ("Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, "
            "eFillObject, eRegionObject));") in walk
    assert "Board.BoardIterator_Destroy(Iter);" in walk
    assert "Owned := P.InComponent Or P.InPolygon Or P.InDimension;" in walk
    # A copper REGION only: a board cutout is a no-net region on MultiLayer.
    assert "If (Not Owned) And ConnectedCopperIsCopper(P) Then" in walk
    region = _routine("ConnectedCopperIsCopper")
    assert "Result := Rgn.Kind = eRegionKind_Copper;" in region


def test_a_change_is_an_object_that_had_no_net_before():
    walk = _routine("FreeNetsWalk")
    assert "If Before Then Seen.Add(IntToStr(P.I_ObjectAddress));" in walk
    assert ("Else If (Not Before) And (Seen.IndexOf(IntToStr(P.I_ObjectAddress)) "
            ">= 0) Then") in walk
    assert "If Changed <= 200 Then" in walk
    for key in ("kind", "layer", "net"):
        assert f"JsonStr('{key}'" in walk
    for key, side in (("x1", "Left"), ("y1", "Bottom"), ("x2", "Right"), ("y2", "Top")):
        assert f"JsonFloat('{key}', CoordToMilsF(R.{side}))" in walk


def test_the_seen_list_is_sorted_for_lookups():
    body = _routine("PCB_UpdateFreePrimitiveNets")
    assert "Seen.Sorted := True;" in body
    assert "Seen.Free;" in body


# --- the MCP tool -------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def keep(fn):
            self.tools[fn.__name__] = fn
            return fn
        return keep

    def __getattr__(self, name):
        return lambda *a, **k: (lambda fn: fn)


class _Dialog:
    def __init__(self, hwnd, title):
        self.hwnd = hwnd
        self.title = title
        self.controls = []


REPLY = {"dispatched": True, "no_net_before": 3, "no_net_after": 1,
         "changed_count": 2, "changed": []}


class _Altium:
    """A bridge whose handler waits until its Confirm box is answered."""

    def __init__(self, title="Confirm", text=PROMPT_TEXT, pid=77,
                 probe_first=False, ocr_only=False):
        self.dialogs = {9: (title, text)}
        self.pid = pid
        self.presses = []
        self.sent = []
        self.probe_first = probe_first
        # The real box draws its message in owner-drawn panels: UI
        # Automation sees only the panel class names, OCR sees the text.
        self.ocr_only = ocr_only

    def get_altium_status(self):
        if self.pid is None:
            return {"running": True, "pid": None,
                    "reason": "2 Altium processes are running"}
        return {"running": True, "pid": self.pid}

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, params, timeout))
        if self.probe_first:
            raise AltiumTimeoutError(
                "Altium is showing a modal dialog 3s into this call",
                details={"dialogs": {"blocked": True, "dialogs": [
                    {"title": "Confirm", "hwnd": 9}]}})
        for _ in range(100):
            if 9 not in self.dialogs:
                return dict(REPLY)
            await asyncio.sleep(0.01)
        return {"dispatched": True, "no_net_before": 3, "no_net_after": 3,
                "changed_count": 0, "changed": []}

    def list_dialogs(self, pid):
        assert pid == self.pid
        return [_Dialog(h, t) for h, (t, _x) in self.dialogs.items()]

    def describe_window(self, hwnd, depth=4, limit=400):
        _title, text = self.dialogs.get(hwnd, ("", ""))
        if self.ocr_only:
            text = "XPPanels.TXPExtPanel"
        return {"ok": True, "elements": [{"name": text}, {"name": "Yes"},
                                         {"name": "No"}]}

    def describe_dialog(self, dialog, use_ocr=True):
        _title, text = self.dialogs.get(dialog.hwnd, ("", ""))
        return {"message": [], "ocr_text": [text, "Yes", "No"] if self.ocr_only else []}

    def invoke(self, hwnd, name):
        self.presses.append((hwnd, name))
        self.dialogs.pop(hwnd, None)
        return {"ok": True, "element": name, "how": "invoke"}


@pytest.fixture()
def altium(monkeypatch):
    def install(automation=True, ui=True, **kwargs):
        fake = _Altium(**kwargs)
        monkeypatch.setattr(pcb, "get_bridge", lambda: fake)
        monkeypatch.setattr(windows, "available", lambda: ui)
        monkeypatch.setattr(windows, "automation_enabled", lambda: automation)
        monkeypatch.setattr(windows, "dialogs", fake.list_dialogs)
        monkeypatch.setattr(windows, "wait_for_close",
                            lambda hwnd, timeout=0: hwnd not in fake.dialogs)
        monkeypatch.setattr(uia, "available", lambda: ui)
        monkeypatch.setattr(uia, "describe_window", fake.describe_window)
        monkeypatch.setattr(uia, "invoke", fake.invoke)
        monkeypatch.setattr(dialog_report, "describe_dialog", fake.describe_dialog)
        mcp = _Mcp()
        pcb.register_pcb_tools(mcp)
        return fake, mcp.tools["pcb_update_free_primitive_nets"]
    return install


def test_yes_is_pressed_on_the_update_prompt(altium):
    fake, tool = altium()
    out = asyncio.run(tool())
    assert fake.presses == [(9, "Yes")]
    assert fake.sent[0][0] == "pcb.update_free_primitive_nets"
    assert fake.sent[0][2] >= 300
    assert out["prompt_answered"] is True
    assert out["changed_count"] == 2


def test_yes_is_pressed_when_only_ocr_reads_the_message(altium):
    fake, tool = altium(ocr_only=True)
    out = asyncio.run(tool())
    assert fake.presses == [(9, "Yes")]
    assert out["prompt_answered"] is True


def test_nothing_is_pressed_when_ocr_reads_another_question(altium):
    fake, tool = altium(text="Delete 4 objects?", ocr_only=True)
    out = asyncio.run(tool())
    assert fake.presses == []
    assert out["prompt_answered"] is False


def test_an_unreadable_confirm_box_is_left_alone(altium, monkeypatch):
    # OCR failing must not stop the watcher or press anything.
    fake, tool = altium(ocr_only=True)

    def broken(dialog, use_ocr=True):
        raise OSError("OCR failed")
    monkeypatch.setattr(dialog_report, "describe_dialog", broken)
    out = asyncio.run(tool())
    assert fake.presses == []
    assert out["prompt_answered"] is False


@pytest.mark.parametrize("title,text", [
    ("Information", PROMPT_TEXT),
    ("Confirm", "Delete 4 objects?"),
    ("Confirm Save Locally", PROMPT_TEXT),
])
def test_nothing_is_pressed_on_another_dialog(altium, title, text):
    fake, tool = altium(title=title, text=text)
    out = asyncio.run(tool())
    assert fake.presses == []
    assert out["prompt_answered"] is False
    assert "prompt_note" in out


@pytest.mark.parametrize("kwargs", [
    {"automation": False}, {"ui": False}, {"pid": None}])
def test_without_automation_nothing_is_sent(altium, kwargs):
    fake, tool = altium(**kwargs)
    out = asyncio.run(tool())
    assert out["success"] is False
    assert out["prompt_answered"] is False
    assert "nothing was sent" in out["reason"]
    assert fake.sent == []
    assert fake.presses == []


def test_the_prompt_is_still_answered_when_the_bridge_gave_up_first(altium):
    fake, tool = altium(probe_first=True)
    out = asyncio.run(tool())
    assert fake.presses == [(9, "Yes")]
    assert out["success"] is False
    assert out["prompt_answered"] is True


def test_another_timeout_is_raised(altium, monkeypatch):
    fake, tool = altium(probe_first=True)
    fake.dialogs = {}

    async def other(command, params=None, timeout=None):
        raise AltiumTimeoutError("No response", details={"dialogs": None})

    monkeypatch.setattr(fake, "send_command_async", other)
    with pytest.raises(AltiumTimeoutError):
        asyncio.run(tool())


def test_the_prompt_test_needs_title_and_text():
    assert pcb._is_free_nets_prompt("Confirm", PROMPT_TEXT)
    assert pcb._is_free_nets_prompt(" Confirm ", "x " + PROMPT_TEXT)
    assert not pcb._is_free_nets_prompt("Confirm", "Update all nets?")
    assert not pcb._is_free_nets_prompt("Warning", PROMPT_TEXT)


def test_the_tool_is_published_as_modal():
    assert metadata.interaction_of("pcb_update_free_primitive_nets", "pcb") == "modal"


def test_the_docstring_warns_about_a_direct_call():
    doc = re.sub(r"\s+", " ", _tool_doc())
    assert "direct bridge call" in doc
    assert "blocks the bridge" in doc


def _tool_doc():
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return mcp.tools["pcb_update_free_primitive_nets"].__doc__
