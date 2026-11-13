# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The generic schematic getter and setter ask a member only of the types
that have it, and one table decides which those are.

DelphiScript resolves a member on the object at run time. Asked of an
object whose interface lacks it, the engine raises "Undeclared identifier"
in a modal no Try catches, and the polling loop stops. The table in
eda_agent.sch_members says which types carry each such member;
scripts/altium/gen_sch_members.py writes SchMemberGate from it, and
GetSchProperty / SetSchProperty ask that gate before touching the object.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from eda_agent import sch_members as sm
from eda_agent.tools import generic as generic_tools
from tests.pascal_source import load

_ROOT = Path(__file__).resolve().parents[1]
_GENERIC = _ROOT / "scripts" / "altium" / "Generic.pas"
_GEN = _ROOT / "scripts" / "altium" / "gen_sch_members.py"
_SDK = _ROOT / "reference" / "altium_sdk" / "altium_sdk_index.json"


def _units():
    return load(_GENERIC, minimum=20)


def _decommented(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


# --- the generated gate --------------------------------------------------------

def test_the_gate_in_generic_pas_is_the_table():
    run = subprocess.run([sys.executable, str(_GEN), "--check"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_generated_gate_matches_each_row():
    from importlib import util
    spec = util.spec_from_file_location("gen_sch_members", _GEN)
    gen = util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    text = gen.render()
    for row in sm.ROWS:
        for m in row.members:
            assert "(PropName = '%s')" % m in text
        for t in row.types:
            assert "(Id = %s)" % t in text


def test_a_member_is_in_one_row_only():
    seen = [m for row in sm.ROWS for m in row.members]
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("routine", ["GetSchProperty", "SetSchProperty"])
def test_the_gate_is_asked_before_the_object_is_touched(routine):
    body = _decommented(_units()[routine])
    gate = body.index("If SchMemberGate(Obj, PropName) = 0 Then")
    assert gate < body.index("Try")
    refusal = body[gate:body.index("Try")]
    word = "unreadable" if routine == "GetSchProperty" else "unknown"
    assert "NotePropertyDiag('%s', PropName);" % word in refusal
    assert "Exit;" in refusal


def test_the_gate_is_defined_before_its_callers():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    at = raw.index("Function SchMemberGate(")
    assert at < raw.index("Function GetSchProperty(")
    assert at < raw.index("Function SetSchProperty(")


def test_no_hand_written_gate_is_left():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    for old in ("SchObjectIsPart", "SchObjectIsPinLike", "SchObjectHasIOType",
                "SchObjectHasAlignment", "SchObjectHasStyle", "SchObjectHasWidth",
                "SchObjectHasLineWidth"):
        assert old not in raw, old


@pytest.mark.parametrize("routine", ["GetSchProperty", "SetSchProperty"])
@pytest.mark.parametrize("member", sorted(sm.TYPES_BY_MEMBER))
def test_a_member_is_reached_only_under_its_own_name(routine, member):
    """The gate looks at PropName, so a member read in another property's
    branch would pass it unasked. Every Obj.<member> sits in its own
    branch, or behind a type check of its own."""
    code = _decommented(_units()[routine])
    for hit in re.finditer(r"\bObj\.%s\b" % member, code):
        before = code[:hit.start()]
        names = re.findall(r"PropName = '([A-Za-z.]+)'", before[-700:])
        own = names and names[-1] == member
        own = own or (member == "ComponentDescription" and names and names[-1] == "Description")
        typed = re.search(r"Obj\.ObjectId (?:<>|=) e\w+", before[-600:])
        assert own or typed, (routine, member, hit.start())


# --- the table's evidence ------------------------------------------------------

#: Members the SDK index declares under another name.
_SDK_NAMES = {"PartCount": "PartCountNoPart0"}


@pytest.mark.skipif(not _SDK.exists(), reason="the SDK index is a local reference")
@pytest.mark.parametrize("row", sm.ROWS, ids=lambda r: r.members[0])
def test_every_interface_in_a_row_declares_its_members(row):
    ifaces = json.loads(_SDK.read_text(encoding="utf-8-sig"))["interfaces"]
    by = {}
    for k, v in ifaces.items():
        by.setdefault(k.split(".")[-1], []).append(v)

    def members(name, seen=()):
        out = set()
        for v in by.get(name, []):
            for p in v.get("props", []):
                out.add(p[0] if isinstance(p, list) else p)
            for m in v.get("methods", []):
                out.add(re.sub(r"^(Get|Set)State_", "", m[0]))
            for b in v.get("bases", []):
                b = b.split(".")[-1]
                if b not in seen:
                    out |= members(b, seen + (name,))
        return out

    for iface in row.sdk:
        have = members(iface)
        for m in row.members:
            assert _SDK_NAMES.get(m, m) in have, (iface, m)


def test_a_type_without_its_interface_in_the_row_says_why():
    for row in sm.ROWS:
        named = {"e" + i[len("ISch_"):] for i in row.sdk} | {"eSchComponent"}
        for t in row.types:
            if t not in named:
                assert t[1:] in row.note, (row.members[0], t)


def test_each_row_was_measured():
    for row in sm.ROWS:
        assert row.measured, row.members


# --- what a caller is told -----------------------------------------------------

def test_refusals_name_the_types_that_have_the_member():
    why = sm.member_refusal("eWire", "IOType")
    assert why and "ePort" in why and "eSheetEntry" in why
    assert sm.member_refusal("ePort", "IOType") is None
    assert sm.member_refusal("ePin", "Width") is not None


def test_a_board_type_or_an_ungoverned_member_is_not_judged():
    assert sm.member_refusal("eTrackObject", "Width") is None
    assert sm.member_refusal("eWire", "Location.X") is None
    assert sm.member_refusal("", "IOType") is None


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    async def send_command_async(self, command, params=None, timeout=None):
        return {"objects": [], "count": 0, "matched": 0}


def _tool(monkeypatch, name, **kw):
    monkeypatch.setattr(generic_tools, "get_bridge", lambda: _Bridge())
    mcp = _Mcp()
    generic_tools.register_generic_tools(mcp)
    return asyncio.run(mcp.tools[name](**kw))


def test_a_query_says_which_properties_the_type_lacks(monkeypatch):
    out = _tool(monkeypatch, "obj_query", object_type="eWire",
                properties="LineWidth,IOType")
    assert list(out["not_on_this_type"]) == ["IOType"]
    out = _tool(monkeypatch, "obj_query", object_type="ePort", properties="IOType")
    assert "not_on_this_type" not in out


def test_a_modify_says_which_settings_the_type_lacks(monkeypatch):
    out = _tool(monkeypatch, "obj_modify", object_type="eNetLabel",
                set="Text=A|Width=10")
    assert list(out.get("not_on_this_type", {})) == ["Width"]
