# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The pad creators refuse a shape word they do not know.

MEASURED on AD 25.8.1: library.add_footprint_pads with shape=rectangle made a
ROUND pad 2.0 x 1.2 (read back as an oblong) and replied success. Both pad
creators compared the word as given against round / rectangular / octagonal
/ roundrect and sent anything else to round, so "rectangle", "rect" or
"Rectangular" each gave a round pad, while set_pad_properties already
refused the same word. All three writers now share CanonicalPadShape: case
and spaces are ignored, the rounded rectangle spellings are kept, and an
unknown word is refused before any pad is made (a batch as a whole).

The shape function and the refusal text are lifted out of Library.pas and
run under Free Pascal; the order of the checks is read from the source.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.altium_simulator import AltiumSimulator

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"

_KNOWN = {"": "", "round": "round", "rectangular": "rectangular",
          "octagonal": "octagonal", "roundrect": "roundrect",
          "Rectangular": "rectangular", " ROUND ": "round",
          "roundrectangle": "roundrect", "roundedrect": "roundrect",
          "rounded_rectangular": "roundrect"}
_UNKNOWN = ["rectangle", "rect", "oval", "square", "circle", "obround"]


def _code() -> str:
    return LIBRARY.read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body,
                  flags=re.S)


def _words(tmp_path, words):
    """{word: CanonicalPadShape(word)} from the real routine."""
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    code = _code()
    lines = [f"  Writeln('[{w}]|' + CanonicalPadShape('{w}'));" for w in words]
    lines.append("  Writeln('REFUSAL|' + PadShapeRefusal('rect, oval'));")
    program = "\n".join([
        "program padshape;", "{$mode delphi}", "uses SysUtils;",
        _routine(code, "CanonicalPadShape"),
        _routine(code, "PadShapeRefusal"),
        "begin", *lines, "end.",
    ])
    path = tmp_path / "padshape.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True,
                           text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("padshape.exe" if os.name == "nt" else "padshape")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    out = {}
    for line in ran.stdout.splitlines():
        key, value = line.split("|", 1)
        out[key] = value
    return out


def test_the_four_shapes_and_their_spellings_are_taken(tmp_path):
    out = _words(tmp_path, list(_KNOWN))
    for word, want in _KNOWN.items():
        assert out[f"[{word}]"] == want, word


def test_any_other_word_is_marked_unknown_as_given(tmp_path):
    out = _words(tmp_path, _UNKNOWN)
    for word in _UNKNOWN:
        assert out[f"[{word}]"] == "?" + word, word
    assert out["REFUSAL"] == ("shape must be round, rectangular, octagonal or "
                              "roundrect (got: rect, oval)")


def _body(name: str) -> str:
    return _routine(_code(), name)


def test_the_single_pad_creator_refuses_before_making_the_pad():
    body = _body("Lib_AddFootprintPad")
    ask = body.index("Shape := CanonicalPadShape(ExtractJsonValue(Params, 'shape'));")
    refuse = body.index("'INVALID_SHAPE'")
    assert ask < refuse < body.index("PcbBeginEdit(")
    assert refuse < body.index("PCBObjectFactory(ePadObject")


def test_the_batch_creator_checks_every_op_before_making_any_pad():
    body = _body("Lib_AddFootprintPads")
    refuse = body.index("'INVALID_SHAPE'")
    begin = body.index("PcbBeginEdit(")
    assert refuse < begin
    first_pass = body[:begin]
    assert "CanonicalPadShape(GetBatchField(Op, 'shape'))" in first_pass
    assert "NextBatchOp(Remaining)" in first_pass
    # the walk that makes the pads starts over from the whole payload
    assert first_pass.rindex("Remaining := PadsStr;") > refuse
    making = body[begin:]
    assert "Shape := CanonicalPadShape(GetBatchField(Op, 'shape'));" in making


def test_no_pad_writer_reads_the_shape_word_raw():
    for name in ("Lib_AddFootprintPad", "Lib_AddFootprintPads"):
        body = _body(name)
        assert "Shape := ExtractJsonValue(Params, 'shape')" not in body, name
        assert "Shape := GetBatchField(Op, 'shape')" not in body, name
    code = _code()
    assert "Shape := LowerCase(PadArg(Src, IsOp, 'shape'))" not in code
    assert "Shape := CanonicalPadShape(PadArg(Src, IsOp, 'shape'));" in code


def test_the_helpers_come_before_their_callers():
    code = _code()
    for helper in ("CanonicalPadShape", "PadShapeRefusal"):
        at = code.index(f"Function {helper}(")
        assert at < code.index("Function Lib_AddFootprintPad(")
        assert at < code.index("Function Lib_AddFootprintPads(")


def _sim(tmp_path, action, params):
    sim = AltiumSimulator(str(tmp_path))
    sim.lib_has_pcblib = True
    return sim, json.loads(sim._handle_library(action, params, "r1"))


def test_the_simulator_refuses_a_batch_with_an_unknown_shape(tmp_path):
    pads = ("designator=1;x=0;y=0;shape=rectangular~~"
            "designator=2;x=50;y=0;shape=rectangle~~"
            "designator=3;x=100;y=0;shape=Rectangle")
    sim, reply = _sim(tmp_path, "add_footprint_pads", {"pads": pads})
    assert reply["success"] is False
    assert reply["error"]["code"] == "INVALID_SHAPE"
    assert "(got: rectangle, Rectangle)" in reply["error"]["message"]
    assert not sim.lib_pads


def test_the_simulator_takes_a_batch_of_known_words(tmp_path):
    pads = ("designator=1;x=0;y=0;shape=Rectangular~~"
            "designator=2;x=50;y=0;shape=roundrect~~designator=3;x=100;y=0")
    sim, reply = _sim(tmp_path, "add_footprint_pads", {"pads": pads})
    assert reply["success"] is True
    assert len(sim.lib_pads) == 3


def test_the_simulator_refuses_one_pad_with_an_unknown_shape(tmp_path):
    _sim_, reply = _sim(tmp_path, "add_footprint_pad",
                        {"designator": "1", "shape": "rect"})
    assert reply["success"] is False
    assert reply["error"]["code"] == "INVALID_SHAPE"
