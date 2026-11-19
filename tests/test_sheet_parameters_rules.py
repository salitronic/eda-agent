# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A sheet's own parameters can be listed, added, set and removed one at a
time, sheet rules included.

sch_get_sheet_parameters gave a sheet parameter's name and value only;
proj_set_document_parameter changed the FIRST parameter of a name and
added one only when the name was missing, so the second of two
parameters with one name could not be reached, and a sheet rule (a
parameter named Rule) could not be added at all: every sheet already has
a Rule. sch_sheet_parameters lists every parameter of the sheet itself
with its index, its read-only state and whether it is a system parameter
or a rule, and adds, sets or removes one picked by index or by a name
that does not repeat. A rule goes into the empty system Rule first, as
Altium's own panel does. A system parameter with a locked name is not
removed, since Altium makes it again on the next load and only its value
would be lost; a value Altium holds read-only is not written.

The decisions are compiled with Free Pascal, as Altium runs them.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import safety
from eda_agent.tools import generic

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_GENERIC = _ALTIUM / "Generic.pas"
SEP = "|"
FILE_SEP = "\u00a6"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _funcs() -> dict[str, str]:
    return load(_GENERIC, minimum=20)


def _handler() -> str:
    return _flat(_funcs()["Gen_SheetParameters"])


# -- the decisions, compiled ---------------------------------------------------

def _routine(unit: str, name: str) -> str:
    code = (_ALTIUM / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^Function {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _pas(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


_ROUTINES = [("Utils.pas", "IsIntStr"),
             ("Generic.pas", "SheetParamNameLocked"),
             ("Generic.pas", "SheetParamValueLocked"),
             ("Generic.pas", "SheetParamRemovable"),
             ("Generic.pas", "SheetRuleSlotFree"),
             ("Generic.pas", "SheetParamIsRuleText"),
             ("Generic.pas", "SheetRuleValueProblem"),
             ("Generic.pas", "SheetParamIndexOf"),
             ("Generic.pas", "SheetParamPickProblem")]


def _run(tmp_path: Path, body: list[str]) -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    source = "\n\n".join(_routine(u, n) for u, n in _ROUTINES)
    program = "\n".join([
        "program sheetparams;", "{$mode delphi}{$H+}", "uses SysUtils;",
        "function B(V : Boolean) : String;",
        "begin if V then Result := 'T' else Result := 'F'; end;",
        source, "var Code, Msg : String;", "begin", *body, "end."])
    path = tmp_path / "sheetparams.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("sheetparams.exe" if os.name == "nt" else "sheetparams")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


def test_the_read_only_state_decides_what_may_change(tmp_path):
    body = []
    for state in range(4):
        body.append(f"  WriteLn(B(SheetParamNameLocked({state})), B(SheetParamValueLocked({state})));")
    # (system, state): a system parameter with a locked name stays; a user
    # parameter, and a Rule a user added (system, state 0), may go.
    for system, state in (("False", 0), ("False", 1), ("True", 0),
                          ("True", 1), ("True", 2), ("True", 3)):
        body.append(f"  WriteLn(B(SheetParamRemovable({system}, {state})));")
    out = _run(tmp_path, body)
    assert out[:4] == ["FF", "TF", "FT", "TT"]
    assert out[4:] == ["T", "T", "T", "F", "F", "F"]


def test_the_empty_system_rule_and_a_rule_record_are_told_apart(tmp_path):
    slots = [("Rule", "*", 1), ("rule", " * ", 1), ("Rule", "*", 0),
             ("Rule", "RULEKIND=Width", 1), ("Title", "*", 1)]
    texts = ["UNIONINDEX=0|RULEKIND=Clearance|GAP=10mil", "rulekind=Width",
             "PcbType=ProtelPcb", "*"]
    values = [("Title", "anything"), ("Rule", "*"), ("Rule", "RULEKIND=Width"),
              ("Rule", "PcbType=ProtelPcb"), ("rule", "")]
    body = [f"  WriteLn(B(SheetRuleSlotFree({_pas(n)}, {_pas(v)}, {s})));"
            for n, v, s in slots]
    body += [f"  WriteLn(B(SheetParamIsRuleText({_pas(t)})));" for t in texts]
    body += [f"  WriteLn('[', SheetRuleValueProblem({_pas(n)}, {_pas(v)}), ']');"
             for n, v in values]
    out = _run(tmp_path, body)
    assert out[:5] == ["T", "T", "F", "F", "F"]
    assert out[5:9] == ["T", "T", "F", "F"]
    problems = out[9:]
    assert problems[:3] == ["[]", "[]", "[]"]
    assert all("RULEKIND" in p for p in problems[3:]), problems


def test_an_index_is_read_by_its_characters(tmp_path):
    cases = [("3", 3), (" 0 ", 0), ("12345678", 12345678), ("-1", -1),
             ("x", -1), ("1.5", -1), ("", -1), ("123456789", -1),
             ("99999999999", -1)]
    out = _run(tmp_path, [f"  WriteLn(SheetParamIndexOf({_pas(s)}));" for s, _ in cases])
    assert out == [str(want) for _, want in cases]


@pytest.mark.parametrize("args, code, words", [
    (("", -1, "", "", 5, 0, ""), "MISSING_PARAM", "index"),
    (("7", 7, "", "", 5, 0, ""), "BAD_INDEX", "0 to 4"),
    (("5", 5, "", "", 5, 0, ""), "BAD_INDEX", "0 to 4"),
    (("x", -1, "", "", 5, 0, ""), "BAD_INDEX", "has 5"),
    (("2", 2, "", "Title", 5, 0, ""), "", ""),
    # index and name together must agree; the name is matched without case
    (("2", 2, "title", "Title", 5, 1, "2"), "", ""),
    (("2", 2, "Author", "Title", 5, 1, "4"), "PARAMETER_CHANGED", '"Title", not "Author"'),
    # an index picks one of a repeated name
    (("0", 0, "Rule", "Rule", 30, 2, "0, 5"), "", ""),
    (("", -1, "Rule", "", 30, 2, "20, 28"), "AMBIGUOUS_NAME", "index 20, 28"),
    (("", -1, "Nope", "", 30, 0, ""), "NOT_FOUND", '"Nope"'),
    (("", -1, "Title", "", 30, 1, "3"), "", ""),
])
def test_one_parameter_is_picked_or_the_reason_is_named(tmp_path, args, code, words):
    idx, want, name, name_at, count, hits, hit_list = args
    call = (f"SheetParamPickProblem({_pas(idx)}, {want}, {_pas(name)}, {_pas(name_at)}, "
            f"{count}, {hits}, {_pas(hit_list)}, Code)")
    out = _run(tmp_path, [f"  Msg := {call};", "  WriteLn(Code);", "  WriteLn(Msg);"])
    got_code = out[0] if out else ""
    got_msg = out[1] if len(out) > 1 else ""
    assert got_code == code
    assert words in got_msg
    assert (got_msg == "") == (code == "")


# -- the handler ---------------------------------------------------------------

def test_the_command_is_dispatched_and_the_list_carries_the_new_fields():
    funcs = _funcs()
    assert ("'sheet_parameters': Result := Gen_SheetParameters(Params, RequestId);"
            in funcs["HandleGenericCommand"])
    assert "SheetParamJson(Param, ParamCount)" in _flat(funcs["Gen_GetSheetParameters"])
    one = _flat(funcs["SheetParamJson"])
    for key in ("index", "name", "value", "hidden", "read_only_state",
                "name_read_only", "value_read_only", "system", "rule",
                "removable", "empty_rule_slot"):
        assert f"'{key}'" in one, key
    for member in ("P.ReadOnlyState", "P.IsSystemParameter", "P.IsRule", "P.IsHidden"):
        assert member in one, member
    for derived in ("JsonBool('name_read_only', SheetParamNameLocked(State))",
                    "JsonBool('value_read_only', SheetParamValueLocked(State))",
                    "JsonBool('removable', SheetParamRemovable(SysFlag, State))",
                    "If SheetRuleSlotFree(PName, PValue, State) Then"):
        assert derived in one, derived


def test_only_the_sheets_own_parameters_are_walked():
    body = _handler()
    assert "SchDoc.ObjectId = eSchLib" in body
    assert body.count("It.SetState_IterationDepth(eIterateFirstLevel);") == 3
    assert body.count("It.AddFilter_ObjectSet(MkSet(eParameter));") == 3
    # Typed locals: the members are asked of a parameter, never of a part.
    var_block = body[:body.index(" Begin ")]
    assert "P, Target, Slot, Made, Exact, LastNamed : ISch_Parameter;" in var_block


def test_add_fills_the_empty_rule_first_else_makes_a_parameter():
    body = _handler()
    assert "Add_Parameter" not in body
    slot = body.index("If SheetRuleSlotFree(PName, PText, State) Then")
    begin = body.index("SchBeginModify(Slot);")
    write = body.index("Slot.Text := Value;")
    end = body.index("SchEndModify(Slot);")
    made = body.index("Made := SchServer.SchObjectFactory(eParameter, eCreate_Default);")
    assert slot < begin < write < end < made
    for step in ("Made.Name := Name;", "Made.Text := Value;",
                 "Try Made.IsHidden := Hidden;",
                 "SchDoc.RegisterSchObjectInContainer(Made);",
                 "SchRegisterObject(SchDoc, Made);"):
        assert made < body.index(step), step
    assert "If RuleName And (Not SheetParamIsRuleText(Value)) Then" in body
    # A rule is applied only when it reads back as a rule record.
    assert "Applied := Found And RuleFlag And SheetParamIsRuleText(Got)" in body
    # A name already on the sheet is added all the same, with a warning.
    assert "Else If (Route = 'new') And (Hits > 0) And (Not RuleName) Then" in body
    assert "set and remove now need the index" in body


def test_set_refuses_a_read_only_value_and_writes_inside_the_modify():
    body = _handler()
    guard = body.index("If SetValue And SheetParamValueLocked(State) Then")
    rule = body.index("Problem := SheetRuleValueProblem(OldName, Value);")
    begin = body.index("SchBeginModify(Target);")
    write = body.index("If SetValue Then Target.Text := Value;")
    hide = body.index("Try Target.IsHidden := (HiddenStr = 'true');")
    end = body.index("SchEndModify(Target);")
    assert guard < rule < begin < write < hide < end
    assert "'VALUE_READ_ONLY'" in body


def test_remove_refuses_a_system_parameter_and_reads_the_count_back():
    body = _handler()
    assert "Remove_Parameter" not in body
    guard = body.index("If Not SheetParamRemovable(SysFlag, State) Then")
    remove = body.index("SchDoc.RemoveSchObject(Target);")
    after = body.index("After := SheetParamCount(SchDoc);", remove)
    assert guard < remove < after
    assert "Applied := After < Count;" in body
    assert "set its value back to * to clear the rule" in body


def test_every_write_marks_the_sheet_and_says_the_save_is_pending():
    body = _handler()
    assert "MarkDocDirtyByPath(DocPath);" in body
    assert "JsonBool('saved', False)" in body
    assert "JsonBool('pending_save', True)" in body
    assert "DoFileSave" not in body and "SaveDocument" not in body


def test_remove_counts_as_a_delete_and_list_as_a_read():
    assert safety.command_is_read_only("generic.get_sheet_parameters")
    assert not safety.command_is_read_only("generic.sheet_parameters")
    assert safety.command_deletes("generic.sheet_parameters", {"action": "remove"})
    assert not safety.command_deletes("generic.sheet_parameters", {"action": "set"})


# -- the tool ------------------------------------------------------------------

LISTED = {"success": True, "count": 4, "parameters": [
    {"index": 0, "name": "Title", "value": "PSU", "hidden": True,
     "read_only_state": 1, "system": True, "rule": False, "removable": False},
    {"index": 1, "name": "Rule", "value": "*", "hidden": True,
     "read_only_state": 1, "system": True, "rule": True, "removable": False,
     "empty_rule_slot": True},
    {"index": 2, "name": "Rule", "hidden": True, "read_only_state": 0,
     "system": True, "rule": True, "removable": True,
     "value": SEP.join(["UNIONINDEX=0", "RULEKIND=Clearance",
                        "NAME=Schematic Clearance Constraint", "GAP=10mil",
                        "OBJECTCLEARANCES= "])},
    {"index": 3, "name": "Rule", "hidden": True, "read_only_state": 0,
     "system": True, "rule": True, "removable": True,
     "value": FILE_SEP.join(["RULEKIND=Width", "NAME=W", "MAXLIMIT=20mil"])},
]}


class _Bridge:
    def __init__(self, reply):
        self.sent = []
        self.reply = reply

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, dict(params)))
        return dict(self.reply, parameters=[dict(p) for p in self.reply["parameters"]]) \
            if "parameters" in self.reply else dict(self.reply)


def _call(monkeypatch, tool="sch_sheet_parameters", reply=None, **kwargs):
    bridge = _Bridge(reply if reply is not None else {"success": True})
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return asyncio.run(captured[tool](**kwargs)), bridge.sent


def test_list_reads_the_sheet_and_parses_each_rule(monkeypatch):
    out, sent = _call(monkeypatch, reply=LISTED, file_path=r"C:\p\a.SchDoc")
    assert sent == [("generic.get_sheet_parameters", {"file_path": r"C:\p\a.SchDoc"})]
    rows = out["parameters"]
    assert "rule_fields" not in rows[0] and "rule_fields" not in rows[1]
    assert rows[2]["rule_fields"]["RULEKIND"] == "Clearance"
    assert rows[2]["rule_fields"]["NAME"] == "Schematic Clearance Constraint"
    # the saved-file separator reads the same
    assert rows[3]["rule_fields"] == {"RULEKIND": "Width", "NAME": "W", "MAXLIMIT": "20mil"}


def test_list_by_name_keeps_the_sheet_index(monkeypatch):
    out, _ = _call(monkeypatch, reply=LISTED, action="list", name="rule")
    assert [p["index"] for p in out["parameters"]] == [1, 2, 3]
    assert out["count"] == 3 and out["sheet_count"] == 4


def test_an_older_list_gets_its_index_and_rule_fields(monkeypatch):
    old = {"count": 2, "parameters": [
        {"name": "Title", "value": "PSU"},
        {"name": "Rule", "value": "RULEKIND=Width|NAME=W"}]}
    out, _ = _call(monkeypatch, tool="sch_get_sheet_parameters", reply=old)
    assert [p["index"] for p in out["parameters"]] == [0, 1]
    assert out["parameters"][1]["rule_fields"] == {"RULEKIND": "Width", "NAME": "W"}


def test_add_always_sends_the_parameter_hidden(monkeypatch):
    # Altium reads every sheet-level parameter back hidden after a reload,
    # so the tool offers no visible one and always writes it hidden.
    _, sent = _call(monkeypatch, action="add", name="Checked", value="VH")
    assert sent == [("generic.sheet_parameters", {
        "action": "add", "name": "Checked", "value": "VH",
        "set_value": "true", "hidden": "true"})]
    _, sent = _call(monkeypatch, action="add", name="Note", value="")
    assert sent[0][1]["value"] == "" and sent[0][1]["hidden"] == "true"


def test_the_tool_takes_no_hidden_argument(monkeypatch):
    with pytest.raises(TypeError):
        _call(monkeypatch, action="add", name="Note", value="x", hidden=False)
    _, sent = _call(monkeypatch, action="set", name="Title", value="x")
    assert "hidden" not in sent[0][1]


def test_a_rule_is_sent_as_a_record_named_rule(monkeypatch):
    rule = {"rulekind": "Clearance", "NAME": "HV", "GAP": "20mil"}
    _, sent = _call(monkeypatch, action="add", rule=rule, file_path=r"C:\p\a.SchDoc")
    args = sent[0][1]
    assert args["name"] == "Rule"
    assert args["value"] == SEP.join(["RULEKIND=Clearance", "NAME=HV", "GAP=20mil"])
    # set by index with a rule names it Rule, so a wrong index is caught
    _, sent = _call(monkeypatch, action="set", index=2, rule=rule)
    assert sent[0][1]["name"] == "Rule" and sent[0][1]["index"] == "2"


def test_a_rule_copied_from_a_file_is_joined_as_altium_holds_it(monkeypatch):
    _, sent = _call(monkeypatch, action="add", name="Rule",
                    value=FILE_SEP.join(["RULEKIND=Width", "NAME=W"]))
    assert sent[0][1]["value"] == SEP.join(["RULEKIND=Width", "NAME=W"])
    _, sent = _call(monkeypatch, action="set", name="Title", value=f"A{FILE_SEP}B")
    assert sent[0][1]["value"] == f"A{FILE_SEP}B"


def test_set_and_remove_pick_by_index_or_name(monkeypatch):
    _, sent = _call(monkeypatch, action="set", name="Title", value="")
    assert sent[0][1] == {"action": "set", "name": "Title", "value": "",
                          "set_value": "true"}
    _, sent = _call(monkeypatch, action="remove", index=3, name="Rule")
    assert sent[0][1] == {"action": "remove", "index": "3", "name": "Rule"}


def test_a_write_reply_carries_the_rule_fields(monkeypatch):
    reply = {"success": True, "action": "add", "applied": True,
             "parameter": {"index": 1, "name": "Rule", "rule": True,
                           "value": "RULEKIND=Width|NAME=W"}}
    out, _ = _call(monkeypatch, reply=reply, action="add", rule={"RULEKIND": "Width", "NAME": "W"})
    assert out["parameter"]["rule_fields"] == {"RULEKIND": "Width", "NAME": "W"}


@pytest.mark.parametrize("kwargs", [
    {"action": "rename"},
    {"action": "list", "value": "x"},
    {"action": "list", "index": 1},
    {"action": "add", "value": "x"},
    {"action": "add", "name": "A"},
    {"action": "add", "name": "A", "value": "x", "index": 2},
    {"action": "add", "name": "Rule", "value": "PcbType=ProtelPcb"},
    {"action": "add", "name": "Title", "rule": {"RULEKIND": "Width"}},
    {"action": "add", "rule": {"RULEKIND": "Width"}, "value": "x"},
    {"action": "add", "rule": {"GAP": "5mil"}},
    {"action": "add", "rule": {"RULEKIND": f"a{SEP}b"}},
    {"action": "set", "value": "x"},
    {"action": "set", "name": "Title"},
    {"action": "set", "name": "Title", "value": "x", "index": -1},
    {"action": "set", "name": "Title", "value": "x", "index": True},
    {"action": "remove"},
    {"action": "remove", "name": "Title", "value": "x"},
])
def test_a_bad_call_sends_nothing(monkeypatch, kwargs):
    out, sent = _call(monkeypatch, **kwargs)
    assert sent == []
    assert out["success"] is False
    assert "Nothing was changed" in out["error"]["message"]


def test_the_directive_rule_messages_are_unchanged():
    _, error = generic.rule_text({"GAP": "5mil"}, "parameter_list[0].rule")
    assert error.startswith("parameter_list[0].rule needs RULEKIND")
    assert generic.rule_fields("PcbType=ProtelPcb") == {}
    assert generic.rule_fields("*") == {}
