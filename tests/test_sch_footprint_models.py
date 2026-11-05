# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A placed part's footprint model is chosen by name, and its pin map set.

A part can carry many PCB models (a generic NMOS carries twenty). Placing
it "on" one of them did nothing, and naming one through the Footprint key
renamed whichever model was current into a second copy of that name, link
and pin map included. A model of that name is now made the only current
one, and the pin map of the current model can be read and written.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.test_sch_properties_match_their_interface import GENERIC, _routine, _source


def _body(name: str) -> str:
    return _routine(_source(), name)


def test_the_chooser_clears_then_sets_then_reads_every_flag_back():
    body = _body("ChooseFootprintModel")
    # Collected during the walk, written only after it.
    walk_end = body.index("Comp.SchIterator_Destroy(ImplIter)")
    assert "IsCurrent :=" not in body[:walk_end]
    cleared = body.index("Impl.IsCurrent := False", walk_end)
    chosen = body.index("Impl.IsCurrent := True", walk_end)
    readback = body.index("Held := True", walk_end)
    assert cleared < chosen < readback
    # A choice that does not hold puts every flag back, cleared ones first.
    restore = body[body.index("If Held Then"):]
    assert restore.index("= '0' Then") < restore.index("= '1' Then")
    assert "Result := -1" in restore
    # Matched ignoring case and outer spaces; the list is never freed.
    assert "UpperCase(Trim(MN)) = UpperCase(Trim(Name))" in body
    assert ".Free" not in body


def test_a_name_the_part_has_is_chosen_not_renamed():
    body = _body("SetPlacedFootprintName")
    choose = body.index("ChooseFootprintModel(Comp, NewName, Before, After)")
    rename = body.index("Target.ModelName := NewName")
    assert choose < rename
    between = body[choose:rename]
    assert "If Choice <> 0 Then" in between and "Exit;" in between


def test_placement_chooses_the_named_model_and_warns_on_a_miss():
    body = _body("Gen_PlaceSchComponentsFromLibrary")
    assert "ChooseFootprintModel(Comp, Footprint, FpBefore, FpAfter)" in body
    assert "FOOTPRINT_NOT_ON_SYMBOL" in body and "FOOTPRINT_NOT_APPLIED" in body
    assert "footprints_chosen" in body
    # Warned, not refused: the part is still counted as placed.
    assert body.index("ChooseFootprintModel(") < body.rindex("Inc(Placed)")


def test_the_component_properties_are_gated_to_components():
    for name in ("GetSchProperty", "SetSchProperty"):
        body = _body(name)
        branch = body[body.index("(PropName = 'DisplayMode') Or"):]
        branch = branch[:branch.index("Else If PropName = 'FontId'")] \
            if "Else If PropName = 'FontId'" in branch else branch[:1500]
        assert "eSchComponent" in branch
    pre = _body("UnsupportedSchProperty")
    gate = pre[pre.index("(PropName = 'FootprintPinMap')"):]
    assert gate.index("(PropName = 'ComponentKind'))") < gate.index(
        "And (Obj.ObjectId <> eSchComponent)") < 120
    setter = _body("SetSchProperty")
    mode = setter[setter.index("Else If PropName = 'DisplayMode' Then"):]
    assert mode.index("Comp.DisplayModeCount") < mode.index("Comp.DisplayMode :=")


def test_pin_maps_are_parsed_and_checked(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    helpers = "\n".join(_routine(_source(), n)
                        for n in ("CanonicalPinMap", "PinMapHolds"))
    # DelphiScript's StringReplace takes -1 for "all"; FPC wants the set.
    helpers = helpers.replace(", -1)", ", [rfReplaceAll])")
    program = """program pinmaps;
{$mode delphi}
uses SysUtils;
@@HELPERS@@
procedure Check(Got, Want: String; Code: Integer);
begin
  if Got <> Want then begin WriteLn(Code, ': ', Got); Halt(Code); end;
end;
begin
  Check(CanonicalPinMap('(G:1),(D:2),(S:3)'), '(G:1),(D:2),(S:3)', 1);
  Check(CanonicalPinMap('G:1;D:2'), '(G:1),(D:2)', 2);
  Check(CanonicalPinMap(' G -> 1 , D->2 '), '(G:1),(D:2)', 3);
  Check(CanonicalPinMap('G1'), '', 4);
  Check(CanonicalPinMap('G:'), '', 5);
  Check(CanonicalPinMap(''), '', 6);
  if not PinMapHolds('(G:1),(D:2)', '(S:3), (D:2),(G:1)') then Halt(7);
  if PinMapHolds('(G:1),(D:2)', '(G:1),(D:3)') then Halt(8);
  if PinMapHolds('', '(G:1)') then Halt(9);
  if PinMapHolds('(G:1)', '(G:11)') then Halt(10);
end.
""".replace("@@HELPERS@@", helpers)
    path = tmp_path / "pinmaps.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = path.with_suffix(".exe" if os.name == "nt" else "")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout


def test_the_helpers_are_defined_before_their_first_use():
    text = GENERIC.read_text(encoding="utf-8", errors="replace")
    first_use = text.index("Function GetSchProperty(")
    for name in ("CurrentFootprintImpl", "ChooseFootprintModel",
                 "WriteFootprintPinMap", "ReadFootprintPinMap"):
        assert text.index(f"Function {name}(") < first_use, name
