# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_place_text mirrors a text placed on a bottom layer.

The placer never set MirrorFlag, so a text on BottomOverlay read backwards
on the board, and the server's own audit then reported it as
bottom_overlay_text_is_not_mirrored. Altium's Place String mirrors a text on
a bottom-side layer by itself. Now an empty ``mirror`` does the same on
bottom copper, overlay, solder mask and paste; ``true`` / ``false`` force
it (a bottom mechanical layer needs ``true``). The text is mirrored last and
in place, through the same routine obj_modify uses for MirrorFlag, so its
box stays where the caller put it and x / y mean the same either way.

The decision is lifted out of the scripts and run under Free Pascal; the
handler is checked for the order of the calls.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import functions, strip_comments

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_PCB = SCRIPTS / "PCB.pas"
_LIBRARY = SCRIPTS / "Library.pas"


def _place() -> str:
    return functions(strip_comments(_PCB.read_text(encoding="utf-8")))["PCB_PlaceText"]


def _routine(path: Path, name: str) -> str:
    code = path.read_text(encoding="utf-8", errors="replace")
    start = re.search(r"(?m)^Function %s\b" % name, code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


#: (mirror as sent, layer, mirrored). Empty = by layer.
CASES = [
    ("", "eBottomOverlay", True),
    ("", "eBottomLayer", True),
    ("", "eBottomSolder", True),
    ("", "eBottomPaste", True),
    ("", "eTopOverlay", False),
    ("", "eTopLayer", False),
    ("", "eTopSolder", False),
    ("", "eTopPaste", False),
    ("", "eMechanical1", False),      # a mechanical layer is never guessed
    ("true", "eTopOverlay", True),
    ("TRUE", "eMechanical1", True),
    ("false", "eBottomOverlay", False),
    ("False", "eBottomLayer", False),
]


def test_a_text_is_mirrored_on_a_bottom_layer_unless_told(tmp_path):
    fpc = _fpc()
    lines = [
        "program bottommirror;", "{$mode delphi}", "uses SysUtils;",
        "type TLayer = Integer;",
        "const eTopLayer = 1; eBottomLayer = 32; eTopOverlay = 33;",
        "  eBottomOverlay = 34; eTopPaste = 35; eBottomPaste = 36;",
        "  eTopSolder = 37; eBottomSolder = 38; eMechanical1 = 57;",
        _routine(_LIBRARY, "PcbCheckIsBottomSide"),
        _routine(_PCB, "TextFlag"),
        _routine(_PCB, "TextMirrorWanted"),
        "begin",
    ]
    for sent, layer, _ in CASES:
        lines.append(f"  WriteLn(TextMirrorWanted(TextFlag('{sent}'), {layer}));")
    lines.append("end.")
    path = tmp_path / "bottommirror.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("bottommirror.exe" if os.name == "nt" else "bottommirror")
    got = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
    assert got == [str(want).upper() for _, _, want in CASES], got


def test_mirror_is_read_and_a_bad_value_refused_before_anything_is_made():
    body = _place()
    assert "MirrorF := TextFlag(ExtractJsonValue(Params, 'mirror'));" in body
    refuse = body.index("(MirrorF = -2)")
    assert refuse < body.index("PCBServer.PCBObjectFactory(eTextObject")
    assert "inverted and mirror true or false" in body


def test_the_text_is_mirrored_last_in_place_inside_the_edit():
    body = _place()
    mirror = body.index("If TextMirrorWanted(MirrorF, TargetLayer) Then\n"
                        "            SetTextMirrorInPlace(TextObj, True);")
    # after it is added, framed, placed and turned; before the edit closes
    assert body.index("Board.AddPCBObject(TextObj);") < mirror
    assert body.rindex("TextObj.Rotation := TRot;") < mirror
    assert body.rindex("TextObj.YLocation :=") < mirror
    assert mirror < body.index("PcbEndEdit(0);")
    # never the bare flip, which moves the text by its own width
    assert "TextObj.MirrorFlag :=" not in body


def test_the_reply_reads_the_mirror_back():
    assert '"mirror":\' + BoolToJsonStr(TextObj.MirrorFlag)' in _place()


# ---------------------------------------------------------------- the tool

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
        self.sent.append((command, dict(params or {})))
        return {"placed": True}


def _sent(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    asyncio.run(mcp.tools["pcb_place_text"](text="T", x=0, y=0, **kw))
    return bridge.sent[0][1]


def test_no_mirror_leaves_it_to_the_layer(monkeypatch):
    assert "mirror" not in _sent(monkeypatch, layer="BottomOverlay")


@pytest.mark.parametrize("flag,sent", [(True, "true"), (False, "false")])
def test_a_mirror_given_is_sent(monkeypatch, flag, sent):
    assert _sent(monkeypatch, layer="Mechanical13", mirror=flag)["mirror"] == sent
