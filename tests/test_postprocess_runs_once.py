# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A PostProcess the Finally already makes is never made inside the Try too.

``Exit`` inside a ``Try`` still runs the ``Finally``. Nineteen error paths
called ``PCBServer.PostProcess`` (or ``SchServer.ProcessControl.PostProcess``)
and then exited, and the ``Finally`` called it again: two PostProcess calls
for one PreProcess. The PCB server nests these, so the second one closes a
level that belongs to somebody else.

The rule checked here is wider than "PostProcess then Exit": inside the Try
section of a Try/Finally whose Finally calls ``X.PostProcess``, no
``X.PostProcess`` may appear at all. Proj_Annotate had the same double
call without an Exit next to it: a PostProcess per sheet inside a loop,
then the Finally closing every sheet again, and a later PreProcess in the
same section hid it from a rule that allowed "closed and reopened". A
pair that has to nest inside such a block belongs in its own function.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import functions, strip_comments

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: Dotted names as one token, so "PCBServer.PostProcess" and
#: "SchServer.ProcessControl.PostProcess" each arrive whole.
_TOKEN = re.compile(r"[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*|\S")
#: Keywords that open a block closed by End.
_OPENS = {"begin", "case", "try", "record", "asm"}


def _blank_strings(text: str) -> str:
    """String literals emptied, so 'End' or 'PostProcess' in one is not code."""
    out, inside = [], False
    for ch in text:
        if ch == "'":
            inside = not inside
            out.append(ch)
        elif inside:
            out.append("\n" if ch == "\n" else " ")
        else:
            out.append(ch)
    return "".join(out)


def _call(token: str, method: str):
    """The receiver of ``token`` when it calls ``method``, else None."""
    parts = [p.strip().lower() for p in token.split(".")]
    if len(parts) < 2 or parts[-1] != method:
        return None
    return ".".join(parts[:-1])


def _closer(token: str):
    """The server a token closes an edit on, or None. PcbEndEdit is the
    board server's, through the depth count (Main.pas)."""
    if token.strip().lower() == "pcbendedit":
        return "pcbserver"
    return _call(token, "postprocess")


def double_postprocess(body: str) -> tuple[list, int]:
    """(offending lines, Try/Finally-with-PostProcess blocks examined)."""
    toks = [(m.group(0), m.start()) for m in _TOKEN.finditer(body)]
    stack, blocks = [], []
    for i, (tok, _) in enumerate(toks):
        low = tok.lower()
        if low in _OPENS:
            stack.append([low, i, None])
        elif low == "finally" and stack and stack[-1][0] == "try":
            stack[-1][2] = i
        elif low == "end" and stack:
            kind, start, mid = stack.pop()
            if kind == "try" and mid is not None:
                blocks.append((start, mid, i))
    found, examined = [], 0
    for start, mid, end in blocks:
        closers = {_closer(toks[k][0]) for k in range(mid + 1, end)}
        closers.discard(None)
        if not closers:
            continue
        examined += 1
        for k in range(start + 1, mid):
            if _closer(toks[k][0]) in closers:
                found.append(body.count("\n", 0, toks[k][1]) + 1)
    return found, examined


def _units():
    return [p for p in sorted(SCRIPTS.glob("*.pas")) if p.name != "Altium_MCP.pas"]


def test_no_postprocess_runs_twice():
    offenders, examined = [], 0
    for path in _units():
        text = _blank_strings(strip_comments(
            path.read_text(encoding="utf-8", errors="replace")))
        for name, body in functions(text).items():
            lines, seen = double_postprocess(body)
            examined += seen
            offenders += [f"{path.name}:{name} (line {n} of the function)"
                          for n in lines]
    # A tokenizer that stops matching would pass over nothing.
    assert examined >= 100, f"only {examined} Try/Finally blocks examined"
    assert not offenders, (
        "PostProcess inside a Try whose Finally already calls it (an Exit "
        "inside the Try still runs the Finally): " + "; ".join(offenders))


_BAD = """
Function F(RequestId : String) : String;
Begin
    PCBServer.PreProcess;
    Try
        If X = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := 'x';
            Exit;
        End;
    Finally
        PCBServer.PostProcess;
    End;
End;
"""

_LOOP = """
Function G(RequestId : String) : String;
Begin
    Try
        For I := 0 To 3 Do
        Begin
            SchServer.ProcessControl.PreProcess(D, '');
            SchServer.ProcessControl.PostProcess(D, 'Edit');
            Continue;
        End;
        SchServer.ProcessControl.PreProcess(D, '');
        Exit;
    Finally
        SchServer.ProcessControl.PostProcess(D, 'Edit');
    End;
End;
"""


def test_the_detector_finds_both_shapes():
    assert double_postprocess(_BAD)[0] == [8]
    assert double_postprocess(_LOOP)[0] == [8]


def test_the_detector_reads_the_edit_helpers():
    """Board handlers end their edits with PcbEndEdit now; a scan that
    only knew PCBServer.PostProcess would examine none of them."""
    helpers = (_BAD.replace("PCBServer.PreProcess;", "PcbBeginEdit(0);")
               .replace("PCBServer.PostProcess;", "PcbEndEdit(0);"))
    assert double_postprocess(helpers)[0] == [8]


def test_the_detector_passes_the_fixed_shape_and_other_servers():
    fixed = _BAD.replace("            PCBServer.PostProcess;\n            Result", "            Result")
    assert double_postprocess(fixed) == ([], 1)
    # A PostProcess for a different server is not the one the Finally makes.
    other = _BAD.replace("            PCBServer.PostProcess;\n",
                         "            SchServer.ProcessControl.PostProcess(D, '');\n")
    assert double_postprocess(other)[0] == []
    # Comment and string mentions are not calls.
    quoted = _blank_strings(strip_comments(
        _BAD.replace("            PCBServer.PostProcess;\n            Result",
                     "            { PCBServer.PostProcess; }\n"
                     "            Result := 'PCBServer.PostProcess;';\n            Result")))
    assert double_postprocess(quoted)[0] == []
