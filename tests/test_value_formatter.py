# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Component values in the RKM code of IEC 60062: 4K7, 2u2, 10uF, 3V3.

Libraries and schematics carry values written every way there is: 4.7k,
4K7, 4,7 kOhm, 4700, 2.2uF, 2u2, 0.0000022. A decimal point vanishes in a
scaled font or a print ("4.7K" reads "47K"), and "1.000" is one or a
thousand depending on a locale the design file does not record. The value
engine (the Vf functions in Utils.pas, reached as application.format_value
and the app_format_value tool) reads any of them and writes ONE form, with
a letter where the point would be, and never rounds: a value is digits and
a power of ten, the letters only move the point.

The engine's rules are pinned by tests/value_formatter_vectors.json, which
the Free Pascal run below takes case by case.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import application
from eda_agent.tools.application import fold_value_text

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "altium"
VECTORS = json.loads((Path(__file__).parent / "value_formatter_vectors.json")
                     .read_text(encoding="utf-8"))

#: the engine, in the order Utils.pas declares it
ENGINE = ["VfField", "VfErr", "VfIsDigit", "VfIsMult", "VfKind", "VfUnitOf",
          "VfKindOfUnit", "VfNegativeOk", "VfUnitLen", "VfUnitTok",
          "VfMultPower", "VfLetterPower", "VfOutLetter", "VfZeros", "VfPlain",
          "VfCanon", "VfToString", "VfReplaceWord", "VfNormalise",
          "VfLetterWord", "VfSkipSpace", "VfScan", "VfLastPos", "VfCount",
          "VfNumber", "VfAddNote", "VfParse", "VfCleanup"]


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **_):
        self.sent.append((command, params))
        return {"ok": True}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _call(monkeypatch, **kwargs):
    bridge = _Bridge()
    monkeypatch.setattr(application, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    application.register_application_tools(mcp)
    result = asyncio.run(mcp.tools["app_format_value"](**kwargs))
    return result, bridge.sent


def test_the_tool_folds_the_symbols_the_scripts_cannot_receive(monkeypatch):
    _, sent = _call(monkeypatch, action="cleanup", text="4.7 kΩ",
                    kind="R", decimal=",")
    assert sent == [("application.format_value",
                     {"action": "cleanup", "text": "4.7 kR", "kind": "R",
                      "decimal": ","})]
    # grouping spaces go before NFKC, which would make them a plain space
    assert fold_value_text("10 000µF") == "10000uF"
    assert fold_value_text("−3.3 V") == "-3.3 V"
    assert fold_value_text("-40 °C") == "-40 C"
    assert fold_value_text("2.2μF").isascii()


def test_the_tool_sends_a_value_exactly(monkeypatch, tmp_path):
    # tostring needs a format chosen on the panel's Config tab.
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    (tmp_path / "preferences.json").write_text('{"value_format":"rkm"}')
    _, sent = _call(monkeypatch, action="tostring", kind="C", value=4.7e-6)
    assert sent == [("application.format_value",
                     {"action": "tostring", "value": "4.7e-06", "kind": "C"})]
    _, sent = _call(monkeypatch, action="tostring", kind="R",
                    value="1234.56789012345678901234567890")
    assert sent[0][1]["value"] == "1234.56789012345678901234567890"


@pytest.mark.parametrize("kwargs", [
    {"action": "round", "text": "4K7"},
    {"action": "cleanup"},
    {"action": "cleanup", "text": "  "},
    {"action": "tostring", "value": "1"},
    {"action": "tostring", "kind": "R"},
    {"action": "tostring", "kind": "R", "value": True},
    {"action": "cleanup", "text": "4K7", "decimal": ";"},
])
def test_the_tool_refuses_before_the_bridge(monkeypatch, kwargs):
    result, sent = _call(monkeypatch, **kwargs)
    assert sent == [] and result["ok"] is False


def test_the_command_is_dispatched_and_touches_no_document():
    switch = _routine("Application.pas", "HandleApplicationCommand")
    assert "'format_value':        Result := App_FormatValue(Params, RequestId);" in switch
    handler = _routine("Application.pas", "App_FormatValue")
    for call in ("VfToString(Kind, Value)", "VfParse(Text, Kind, Sep)",
                 "VfCleanup(Text, Kind, Sep)"):
        assert call in handler
    assert "'BAD_VALUE'" in handler and "'UNKNOWN_KIND'" in handler
    # read-only: the compile cache is not invalidated by a value lookup
    assert "Or (Verb = 'format_value');" in _routine("StatusForm.pas", "CommandIsReadOnly")


def test_the_engine_does_no_floating_point_arithmetic():
    for name in ENGINE:
        body = _routine("Utils.pas", name)
        for banned in ("StrToFloat", "FloatToStr", "Double", "Extended", "Round("):
            assert banned not in body, (name, banned)
    # the global separator is read, never written (upstream issue #21)
    assert "DecimalSeparator :=" not in "".join(_routine("Utils.pas", n) for n in ENGINE)


def test_the_vectors_are_ascii_out_and_exact():
    for case in VECTORS["parse"]:
        if case.get("cleanup"):
            out = case["cleanup"]
            assert out.isascii() and "." not in out and "," not in out, case
    for case in VECTORS["tostring"]:
        if "text" in case:
            assert case["text"].isascii() and "." not in case["text"], case


def _pas(text: str) -> str:
    """A Pascal string expression for text: quoted ASCII runs, #n for the rest."""
    parts, run = [], ""
    for ch in text:
        if 32 <= ord(ch) < 127:
            run += "''" if ch == "'" else ch
        else:
            if run:
                parts.append(f"'{run}'")
                run = ""
            parts.append(f"#{ord(ch)}")
    if run or not parts:
        parts.append(f"'{run}'")
    return "+".join(parts)


def test_the_engine_meets_every_vector_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks, cases = [], []
    for case in VECTORS["parse"]:
        cases.append(case)
        n = len(cases)
        text = _pas(case["text"] if case.get("raw") else fold_value_text(case["text"]))
        call = f"VfParse({text}, '{case['kind']}', '{case['decimal']}')"
        if "error" in case:
            checks.append(f"  if VfField({call}, 0) <> 'ERR' then Fail({n}, {call});")
            continue
        checks.append(f"  R := {call};")
        checks.append(f"  if (VfField(R, 0) <> 'OK') or (VfField(R, 1) <> '{case['found']}')"
                      f" or (VfField(R, 2) <> '{case['value']}')"
                      f" or (VfField(R, 3) <> {_pas(case['rest'])}) then Fail({n}, R);")
        if case.get("cleanup"):
            cl = f"VfCleanup({text}, '{case['kind']}', '{case['decimal']}')"
            checks.append(f"  if VfField({cl}, 1) <> '{case['cleanup']}' then Fail({n}, {cl});")
    for case in VECTORS["tostring"]:
        cases.append(case)
        n = len(cases)
        call = f"VfToString('{case['kind']}', '{case['value']}')"
        if "error" in case:
            checks.append(f"  if VfField({call}, 0) <> 'ERR' then Fail({n}, {call});")
        else:
            checks.append(f"  if VfField({call}, 1) <> '{case['text']}' then Fail({n}, {call});")
    program = "\n".join([
        "program valueformatter;", "{$mode delphi}{$H+}", "uses SysUtils;",
        *(_routine("Utils.pas", name) for name in ENGINE),
        "procedure Fail(N: Integer; Got: String);",
        "begin", "  WriteLn(N, ' ', Got);", "  Halt(1);", "end;",
        "var R: String;",
        "begin",
        *checks,
        "end.",
    ])
    path = tmp_path / "valueformatter.pas"
    path.write_text(program, encoding="latin-1")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("valueformatter.exe" if os.name == "nt" else "valueformatter")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = int(ran.stdout.split()[0])
        pytest.fail(f"case {n} {cases[n - 1]!r} came out as {ran.stdout.strip()!r}")
