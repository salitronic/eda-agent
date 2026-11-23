# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A stack model load keeps design rules linked to their impedance profile (#433).

pcb.load_stack_manager_model loads a model into the Layer Stack Manager's
document and applies it to the board; pcb_sync_stack_manager runs it, and so
does every tool that resyncs the manager after changing the board's stack.
The load gives every layer, stack and profile in the manager a new id, and
Altium then clears the profile of each width or differential pair rule that
named one: the rule stays impedance driven with no profile. Measured on
AD 25.8.1, read back from the saved file. The links are now read before the
load, by the profile's place and name, and written back with the new ids
after the apply, in the form the rule held them ({UPPER-CASE}; a profile
gives its id bare and lower case). No modify bracket: one moves a rule to
the top of its kind's priorities (#400).
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

from tests.pascal_source import load, strip_comments

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


@pytest.fixture(scope="module")
def fns():
    return load(_PCB)


def _routine(name: str) -> str:
    code = _PCB.read_text(encoding="utf-8", errors="replace")
    start = re.search(r"(?m)^Function %s\b" % name, code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


# --- the id helpers, compiled ------------------------------------------------

#: (new id as the profile gives it, old id as the rule held it, written)
IDS = [
    ("1031b26b-ca79-4498-a0d8-ecbe3f120c78", "{392F3BA9-ACA7-47D8-8BB3-CF2CA6AB60A3}",
     "{1031B26B-CA79-4498-A0D8-ECBE3F120C78}"),
    ("1031b26b-ca79-4498-a0d8-ecbe3f120c78", " {392f3ba9} ",
     "{1031B26B-CA79-4498-A0D8-ECBE3F120C78}"),
    ("1031b26b-ca79-4498-a0d8-ecbe3f120c78", "392f3ba9-aca7",
     "1031b26b-ca79-4498-a0d8-ecbe3f120c78"),
]

#: (one id, another, the same profile)
KEYS = [
    ("{392F3BA9-ACA7}", "392f3ba9-aca7", True),
    (" {392F3BA9-ACA7} ", "{392f3ba9-aca7}", True),
    ("{392F3BA9-ACA7}", "{392F3BA9-ACA8}", False),
    ("", "{392F3BA9-ACA7}", False),
]


def test_ids_are_compared_and_written_in_the_rule_form(tmp_path):
    fpc = _fpc()
    lines = [
        "program profileids;", "{$mode delphi}", "uses SysUtils;",
        "const eRule_MaxMinWidth = 2; eRule_DifferentialPairsRouting = 51;",
        _routine("ProfileIdKey"),
        _routine("ProfileIdLike"),
        _routine("ProfileLinkKind"),
        "begin",
    ]
    for new, old, _ in IDS:
        lines.append(f"  WriteLn(ProfileIdLike('{new}', '{old}'));")
    for a, b, _ in KEYS:
        lines.append(f"  WriteLn(ProfileIdKey('{a}') = ProfileIdKey('{b}'));")
    for kind in (2, 51, 0, 1, 50, 52):
        lines.append(f"  WriteLn(ProfileLinkKind({kind}));")
    lines.append("end.")
    path = tmp_path / "profileids.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("profileids.exe" if os.name == "nt" else "profileids")
    got = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
    want = ([w for _, _, w in IDS] + [str(s).upper() for _, _, s in KEYS]
            + ["TRUE", "TRUE", "FALSE", "FALSE", "FALSE", "FALSE"])
    assert got == want, got


# --- the handler -------------------------------------------------------------

def test_the_links_are_read_before_the_load(fns):
    body = fns["PCB_LoadStackManagerModel"]
    assert body.index("ProfileLinksRead(Board, Rules, Places, Names, Ids);") < body.index(
        "Doc.LoadFromFile(Path, 0);")


def test_the_links_are_written_after_the_last_apply_and_before_the_read_back(fns):
    body = strip_comments(fns["PCB_LoadStackManagerModel"])
    restore = body.index("Links := ProfileLinksRestore(Board, Rules, Places, Names, Ids, Relinked);")
    assert body.rindex("Doc.ApplyToPcb(Board.FileName);") < restore
    assert restore < body.index("SymAfter := St.IsSymmetric;")


def test_the_lists_are_freed_whatever_happens(fns):
    body = fns["PCB_LoadStackManagerModel"]
    fin = body.rindex("Finally")
    for name in ("Rules", "Places", "Names", "Ids"):
        assert f"{name} := TStringList.Create;" in body
        assert f"{name}.Free;" in body[fin:]
    assert body.index("Ids := TStringList.Create;") < body.index("ProfileLinksRead(")


def test_the_reply_says_what_became_of_each_link(fns):
    body = fns["PCB_LoadStackManagerModel"]
    assert "JsonInt('rules_relinked', Relinked)" in body
    assert "'\"profile_rules\":[' + Links + ']}'" in body


def test_only_width_and_pair_rules_are_touched(fns):
    for name in ("RuleProfileId", "SetRuleProfileId"):
        body = fns[name]
        assert "If R.RuleKind = eRule_MaxMinWidth Then" in body
        assert "Else If R.RuleKind = eRule_DifferentialPairsRouting Then" in body
    assert "If ProfileLinkKind(R.RuleKind) Then" in fns["ProfileLinksRead"]
    assert "ProfileLinkKind(R.RuleKind)" in fns["ProfileLinksRestore"]


def test_a_link_is_recorded_only_to_a_profile_the_board_has(fns):
    body = fns["ProfileLinksRead"]
    assert "If Id <> '' Then" in body
    at = body.index("At := ProfilePlace(Stack, Id);")
    assert body.index("If At >= 0 Then") > at
    assert body.index("Rules.Add(R.Name);") > body.index("If At >= 0 Then")


def test_the_profile_is_found_by_place_then_by_a_unique_name(fns):
    body = fns["ProfileAfterLoad"]
    place = body.index("P := Stack.GetImpedanceProfileByIndex(Place);")
    assert "If P.Name = Name Then" in body[place:place + 120]
    assert "If Hits = 1 Then Result := Found;" in body


def test_a_link_that_still_holds_is_not_written(fns):
    body = fns["ProfileLinksRestore"]
    kept = body.index("Else If ProfileIdKey(Cur) = ProfileIdKey(P.Id) Then")
    write = body.index("SetRuleProfileId(R, ProfileIdLike(P.Id, Ids[Idx]));")
    assert kept < write
    assert "State := 'kept'" in body[kept:write]


def test_a_relink_is_read_back(fns):
    body = fns["ProfileLinksRestore"]
    write = body.index("SetRuleProfileId(R, ProfileIdLike(P.Id, Ids[Idx]));")
    after = body[write:]
    assert after.index("If ProfileIdKey(RuleProfileId(R)) = ProfileIdKey(P.Id) Then") < after.index(
        "State := 'relinked';")
    assert "State := 'failed';" in after
    assert "State := 'profile_gone'" in body


def test_no_rule_is_bracketed_for_a_modify(fns):
    # PCBM_BeginModify / EndModify move a rule to priority 1 of its kind.
    for name in ("ProfileLinksRestore", "SetRuleProfileId"):
        code = strip_comments(fns[name])
        assert "BeginModify" not in code and "EndModify" not in code


# --- the resync passes it on -------------------------------------------------

_MODEL = ("\ufeff|STACKUPVERSION=1|LAYER_V8_0ID={L}|LAYER_V8_0NAME=Top Layer"
          "|LAYER_V8_0COPTHICK=1.4mil|LAYER_V8_0$LSM$Thickness=1.4mil")
_MANAGER = {"layers": [{"name": "Top Layer", "type": "copper", "thickness_mils": 1.4}],
            "model": _MODEL}
_BOARD = {"layers": [{"name": "Top Layer", "kind": "copper", "thickness_mils": 2.8}]}
_LINKS = [{"rule": "W1", "profile": "S50", "state": "relinked"},
          {"rule": "D1", "profile": "D90", "state": "kept"}]


class _Bridge:
    def __init__(self):
        self.loaded = False

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "pcb.layer_stack_manager":
            if self.loaded:
                return {"layers": [dict(_MANAGER["layers"][0], thickness_mils=2.8)]}
            return _MANAGER
        if command == "pcb.physical_stack":
            return _BOARD
        if command == "pcb.load_stack_manager_model":
            self.loaded = True
            return {"loaded": True, "symmetric": False, "symmetric_after": False,
                    "rules_relinked": 1, "profile_rules": _LINKS}
        raise AssertionError(command)


def test_the_resync_reports_the_links(monkeypatch):
    out = asyncio.run(pcb_tools.sync_stack_manager(_Bridge()))
    assert out["rules_relinked"] == 1
    assert out["profile_rules"] == _LINKS
    assert out["in_step_after"] is True
