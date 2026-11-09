# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Config tab on the Altium status panel keeps the user's choices.

The panel writes ``preferences.json`` in the workspace; the script loads it
when the pump starts and the Python side reads the same file. No file, or a
value nobody chose, means nothing is reformatted.

The Pascal routines are copied out of Main.pas by name and run under Free
Pascal, so what is tested is what Altium runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import preferences
from tests.pascal_source import functions, strip_comments

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(unit: str) -> str:
    return (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


_STUBS = """
const PREFS_FILE = 'preferences.json';
var ValueFormat, ValueUnit, EditsMode, DeletingMode, ClosingMode,
    UiAutomationMode, KeepInViewMode, DialogMode, ChecksOff, WorkspaceDir : String;

function ExtractJsonValue(Json, Key : String) : String;
var P, Q : Integer;
begin
  Result := '';
  P := Pos('"' + Key + '":"', Json);
  if P = 0 then Exit;
  P := P + Length(Key) + 4;
  Q := P;
  while (Q <= Length(Json)) and (Json[Q] <> '"') do Inc(Q);
  Result := Copy(Json, P, Q - P);
end;

function ReadFileContent(FilePath : String) : String;
var L : TStringList;
begin
  L := TStringList.Create;
  try L.LoadFromFile(FilePath); Result := L.Text; finally L.Free; end;
end;

procedure WriteFileContent(FilePath : String; Content : String);
var L : TStringList;
begin
  L := TStringList.Create;
  try L.Text := Content; L.SaveToFile(FilePath); finally L.Free; end;
end;
"""


def _run(tmp_path: Path, body: list[str], extra: tuple[str, ...] = ()) -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    main = _code("Main.pas")
    program = "\n".join([
        "program prefs;", "{$mode delphi}{$H+}", "uses SysUtils, Classes;",
        _STUBS, _routine(main, "LoadPreferences"), _routine(main, "StorePreferences"),
        *(_routine(_code("StatusForm.pas"), n) for n in extra),
        "begin",
        f"  WorkspaceDir := '{tmp_path}' + PathDelim;",
        *body,
        "end.",
    ])
    path = tmp_path / "prefs.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("prefs.exe" if os.name == "nt" else "prefs")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


def test_the_choice_is_kept_across_a_restart_and_defaults_to_none(tmp_path):
    out = _run(tmp_path, [
        "  ValueFormat := 'rkm'; ValueUnit := 'with'; LoadPreferences(0);"
        " WriteLn(ValueFormat, ' ', ValueUnit);",
        "  ValueFormat := 'rkm'; ValueUnit := 'with'; StorePreferences(0);",
        "  ValueFormat := ''; ValueUnit := ''; LoadPreferences(0);"
        " WriteLn(ValueFormat, ' ', ValueUnit);",
        "  ValueFormat := 'si'; ValueUnit := 'with'; StorePreferences(0);",
        "  ValueFormat := ''; ValueUnit := ''; LoadPreferences(0);"
        " WriteLn(ValueFormat, ' ', ValueUnit);",
    ])
    assert out == ["none without", "rkm without", "si with"]
    # The file the panel writes is the one the Python side reads.
    assert preferences.preferences(tmp_path)["value_format"] == "si"
    assert preferences.preferences(tmp_path)["value_unit"] == "with"


def test_an_unknown_value_in_the_file_means_none(tmp_path):
    (tmp_path / "preferences.json").write_text(
        '{"value_format":"RKM2","value_unit":"yes"}')
    assert _run(tmp_path, ["  LoadPreferences(0); WriteLn(ValueFormat, ' ', ValueUnit);"]
                ) == ["none without"]


def test_python_reads_the_same_file(tmp_path):
    assert preferences.value_format(tmp_path) == "none"           # no file
    for stored, seen in (('{"value_format":"rkm"}', "rkm"),
                         ('{"value_format":"SI"}', "si"),
                         ('{"value_format":"eng"}', "none"),
                         ('{"other":"x"}', "none"),
                         ("[1, 2]", "none"),
                         ("not json", "none")):
        (tmp_path / "preferences.json").write_text(stored)
        assert preferences.value_format(tmp_path) == seen, stored
    assert preferences.preferences(tmp_path) == {
        "value_format": "none", "value_unit": "without", "edits": "allowed",
        "deleting": "allowed", "closing": "allowed",
        "ui_automation": "on", "keep_in_view": "off", "dialogs": "auto"}
    for stored, seen in (('{"value_format":"si","value_unit":"with"}', "with"),
                         ('{"value_format":"si","value_unit":"WITH"}', "with"),
                         ('{"value_format":"si","value_unit":"yes"}', "without"),
                         # The RKM code has no unit, whatever the file says.
                         ('{"value_format":"rkm","value_unit":"with"}', "without"),
                         ('{"value_unit":"with"}', "without"),
                         ("{}", "without")):
        (tmp_path / "preferences.json").write_text(stored)
        assert preferences.value_unit(tmp_path) == seen, stored


def test_the_config_tab_is_wired_and_painted_at_start():
    dfm = (SCRIPTS / "StatusForm.dfm").read_text(encoding="ascii")
    form = functions(strip_comments(_code("StatusForm.pas")))
    for handler in ("tab_ConfigClick", "tab_ConfigEnter", "tab_ConfigLeave",
                    "opt_FmtNoneClick", "opt_FmtRkmClick", "opt_FmtSiClick",
                    "opt_FmtSiUnitClick", "opt_EditsAllowedClick",
                    "opt_EditsReadOnlyClick", "opt_AutoOnClick", "opt_AutoOffClick",
                    "opt_ViewOffClick", "opt_ViewOnClick",
                    "opt_DeleteAllowedClick", "opt_DeleteRefusedClick",
                    "opt_CloseAllowedClick", "opt_CloseRefusedClick",
                    "opt_EcoAutoClick", "opt_EcoManualClick"):
        assert f"= {handler}" in dfm, handler
        assert handler in form, handler
    for option, call in (("None", "'none', 'without'"), ("Rkm", "'rkm', 'without'"),
                         ("Si", "'si', 'without'"), ("SiUnit", "'si', 'with'")):
        assert f"ChooseValueFormat({call})" in form[f"opt_Fmt{option}Click"]
    assert "StorePreferences(0)" in form["ChooseValueFormat"]
    assert "PaintValueFormat(0)" in form["ShowStatusForm"]
    assert "PaintAgentSettings(0)" in form["ShowStatusForm"]
    for option, call in (("EditsAllowed", "'edits', 'allowed'"),
                         ("EditsReadOnly", "'edits', 'readonly'"),
                         ("AutoOn", "'ui_automation', 'on'"),
                         ("AutoOff", "'ui_automation', 'off'"),
                         ("ViewOff", "'keep_in_view', 'off'"),
                         ("ViewOn", "'keep_in_view', 'on'"),
                         ("DeleteAllowed", "'deleting', 'allowed'"),
                         ("DeleteRefused", "'deleting', 'refused'"),
                         ("CloseAllowed", "'closing', 'allowed'"),
                         ("CloseRefused", "'closing', 'refused'"),
                         ("EcoAuto", "'dialogs', 'auto'"),
                         ("EcoManual", "'dialogs', 'manual'")):
        assert f"ChooseAgentSetting({call})" in form[f"opt_{option}Click"]
    assert "StorePreferences(0)" in form["ChooseAgentSetting"]
    # Loaded before the form is shown, so the first paint is the stored choice.
    pump = form["StartMCPPump"]
    assert pump.index("LoadPreferences(0)") < pump.index("ShowStatusForm(0)")


def test_one_tab_shows_at_a_time():
    show = functions(strip_comments(_code("StatusForm.pas")))["ShowTab"]
    for line in ("mmo_Log.Visible := Which = 0;", "mmo_Perf.Visible := Which = 1;",
                 "pnl_Config.Visible := Which = 2;"):
        assert line in show


def test_the_agent_settings_are_kept_and_default_to_permissive(tmp_path):
    out = _run(tmp_path, [
        "  LoadPreferences(0); WriteLn(EditsMode, ' ', UiAutomationMode, ' ', KeepInViewMode, ' ', DialogMode);",
        "  EditsMode := 'readonly'; UiAutomationMode := 'off'; KeepInViewMode := 'on'; DialogMode := 'manual';",
        "  StorePreferences(0);",
        "  EditsMode := ''; UiAutomationMode := ''; KeepInViewMode := ''; DialogMode := '';",
        "  LoadPreferences(0); WriteLn(EditsMode, ' ', UiAutomationMode, ' ', KeepInViewMode, ' ', DialogMode);",
    ])
    assert out == ["allowed on off auto", "readonly off on manual"]
    assert preferences.edits_read_only(tmp_path)
    assert preferences.ui_automation_off(tmp_path)
    assert preferences.keep_in_view(tmp_path)
    assert not preferences.dialogs_auto(tmp_path)


def test_dialogs_default_to_automatic(tmp_path):
    assert preferences.dialogs_auto(tmp_path)


def test_the_permissions_are_refused_where_every_request_passes():
    """The bridge refuses itself, so a client on older Python is held to the
    panel too; a command the panel allows goes on to its handler."""
    form = functions(strip_comments(_code("StatusForm.pas")))
    request = form["ProcessSingleRequest"]
    asked = request.index("ResponseContent := SettingRefusal(Command, Params, RequestId);")
    assert asked < request.index("ResponseContent := ProcessCommand(Command, Params, RequestId);")
    assert "If ResponseContent = '' Then" in request[asked:]
    refusal = form["SettingRefusal"]
    for guard, code in (("(EditsMode = 'readonly') And Not CommandIsReadOnly(Command)", "'READ_ONLY'"),
                        ("(DeletingMode = 'refused') And CommandDeletes(Command, Params)", "'DELETE_REFUSED'"),
                        ("(ClosingMode = 'refused') And CommandCloses(Command)", "'CLOSE_REFUSED'")):
        at = refusal.index(guard)
        assert code in refusal[at:at + 300], code


_CLASSIFIED = [
    ("pcb.delete_object", {}), ("generic.delete_objects", {}), ("generic.batch_delete", {}),
    ("library.remove_model", {}), ("pcb.remove_layer", {}), ("project.remove_document", {}),
    ("pcb.unroute", {}), ("library.clear_source_library", {}),
    ("generic.clear_sch_source_library", {}), ("pcb.clear_source_footprint_library", {}),
    ("pcb.embedded_data", {"action": "delete"}), ("pcb.component_parameters", {"action": "remove"}),
    ("pcb.embedded_data", {"action": "write"}), ("generic.clear_highlights", {}),
    ("pcb.place_tracks", {}), ("generic.modify_objects", {}), ("pcb.get_components", {}),
    ("application.close_document", {}), ("project.close", {}), ("project.compile", {}),
]


_DISPATCH_UNITS = {"Application": "application", "Project": "project",
                   "Library": "library", "Generic": "generic", "PCB": "pcb",
                   "Audit": "audit"}


def _every_dispatched_command() -> list[tuple[str, dict]]:
    commands = []
    for unit, category in _DISPATCH_UNITS.items():
        for verb in sorted(set(re.findall(r"^\s+'(\w+)':", _code(f"{unit}.pas"), flags=re.M))):
            commands.append((f"{category}.{verb}", {}))
    assert len(commands) > 300, "the dispatch scan found almost nothing"
    return commands


def test_python_and_pascal_agree_on_every_command(tmp_path):
    """The Pascal rules are generated from safety.py; run both on every
    dispatched command, the hand-picked cases and odd spellings."""
    from eda_agent import safety
    cases = _CLASSIFIED + _every_dispatched_command() + [
        ("PCB.Delete_Object", {}), (" project.close ", {}), ("nodot", {}),
        ("Application.Ping", {}), ("generic.x", {"action": " Remove "})]
    lines = []
    for command, params in cases:
        js = json.dumps(params).replace("'", "''").replace(": ", ":")
        lines.append(f"  WriteLn(CommandDeletes('{command}', '{js}'), ' ', "
                     f"CommandCloses('{command}'), ' ', CommandIsReadOnly('{command}'));")
    out = _run(tmp_path, lines, extra=("ActionHasPrefix", "CommandVerb", "CommandIsReadOnly",
                                       "CommandDeletes", "CommandCloses"))
    expected = [" ".join(str(v).upper() for v in (
                    safety.command_deletes(c, p), safety.command_closes(c),
                    safety.command_is_read_only(c)))
                for c, p in cases]
    wrong = [(c, got, want) for (c, _), got, want in zip(cases, out, expected) if got != want]
    assert not wrong, wrong[:10]
    deletes = [c for (c, p), line in zip(_CLASSIFIED, out) if line.startswith("TRUE")]
    assert len(deletes) == 12, deletes
    assert "generic.clear_highlights" not in deletes


def test_the_pascal_rules_are_generated_from_safety_py():
    from eda_agent import safety
    code = _code("StatusForm.pas").replace("\r\n", "\n")
    start = code.index(safety.PASCAL_RULES_BEGIN)
    end = code.index(safety.PASCAL_RULES_END) + len(safety.PASCAL_RULES_END)
    assert code[start:end] == safety.pascal_command_rules(), (
        "StatusForm.pas command rules are stale; run python scripts/altium/sync_rules.py")


def test_every_deleting_verb_in_the_dispatch_tables_is_caught():
    verbs = set()
    for unit in ("Application", "Project", "Library", "Generic", "PCB", "Audit"):
        verbs |= set(re.findall(r"^\s+'((?:delete|remove)_\w+|batch_delete)':",
                                _code(f"{unit}.pas"), flags=re.M))
    from eda_agent import safety
    assert len(verbs) >= 15, verbs
    assert all(safety.command_deletes(f"x.{v}") for v in verbs)


def _workspace(monkeypatch, tmp_path, **prefs):
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    (tmp_path / "preferences.json").write_text(json.dumps(prefs))


def test_python_refuses_an_edit_when_the_panel_says_read_only(monkeypatch, tmp_path):
    from eda_agent import safety
    monkeypatch.delenv(safety.READONLY_ENV, raising=False)
    _workspace(monkeypatch, tmp_path, edits="allowed")
    assert safety.refuse_command("pcb.place_tracks") is None
    _workspace(monkeypatch, tmp_path, edits="readonly")
    assert "Config tab" in safety.refuse_command("pcb.place_tracks")
    assert safety.refuse_command("pcb.get_components") is None
    assert safety.readonly_mode()


def test_the_environment_variable_still_makes_it_read_only(monkeypatch, tmp_path):
    from eda_agent import safety
    _workspace(monkeypatch, tmp_path, edits="allowed")
    monkeypatch.setenv(safety.READONLY_ENV, "1")
    assert safety.READONLY_ENV in safety.refuse_command("pcb.place_tracks")


def test_ui_automation_stops_on_either_switch(monkeypatch, tmp_path):
    from eda_agent.ui import windows
    monkeypatch.delenv(windows.UI_AUTOMATION_ENV, raising=False)
    _workspace(monkeypatch, tmp_path, ui_automation="on")
    assert windows.automation_enabled()
    _workspace(monkeypatch, tmp_path, ui_automation="off")
    assert not windows.automation_enabled()
    with pytest.raises(windows.AutomationDisabled, match="Config tab"):
        windows._gate("a click")
    _workspace(monkeypatch, tmp_path, ui_automation="on")
    monkeypatch.setenv(windows.UI_AUTOMATION_ENV, "0")
    assert not windows.automation_enabled()


def test_keep_in_view_follows_the_panel(monkeypatch, tmp_path):
    from eda_agent.bridge import altium_bridge as ab
    monkeypatch.delenv(ab.KEEP_IN_VIEW_ENV, raising=False)
    _workspace(monkeypatch, tmp_path, keep_in_view="off")
    assert not ab._wants_board_in_view("pcb.place_tracks", now=1e9)
    _workspace(monkeypatch, tmp_path, keep_in_view="on")
    assert ab._wants_board_in_view("pcb.place_tracks", now=2e9)
    assert not ab._wants_board_in_view("pcb.get_components", now=3e9)


def test_python_refuses_deletes_and_closes_when_the_panel_says_so(monkeypatch, tmp_path):
    from eda_agent import safety
    monkeypatch.delenv(safety.READONLY_ENV, raising=False)
    _workspace(monkeypatch, tmp_path, deleting="allowed", closing="allowed")
    assert safety.refuse_command("pcb.delete_object") is None
    assert safety.refuse_command("project.close") is None
    _workspace(monkeypatch, tmp_path, deleting="refused", closing="refused")
    assert "deleting is switched off" in safety.refuse_command("pcb.delete_object")
    assert "deleting is switched off" in safety.refuse_command(
        "pcb.embedded_data", {"action": "delete"})
    assert safety.refuse_command("pcb.embedded_data", {"action": "write"}) is None
    assert "closing documents" in safety.refuse_command("application.close_document")
    assert safety.refuse_command("pcb.place_tracks") is None


_CHECK_STUBS = """
type TLog = record Lines : TStringList; end;
var mmo_Log : TLog;
procedure PaintAgentSettings(Dummy : Integer); begin end;
"""


def test_every_check_is_on_until_the_panel_turns_it_off(tmp_path):
    assert preferences.checks_off(tmp_path) == set()
    assert all(preferences.check_on(n, tmp_path) for n in preferences.CHECKS)
    with pytest.raises(KeyError):
        preferences.check_on("no_such_check", tmp_path)
    (tmp_path / "preferences.json").write_text(
        '{"checks_off":"spice_model, footprint_drawing,bogus"}')
    assert preferences.checks_off(tmp_path) == {"spice_model", "footprint_drawing"}
    assert not preferences.check_on("footprint_drawing", tmp_path)
    assert preferences.check_on("pins_pads", tmp_path)
    note = preferences.check_skipped("footprint_drawing")
    assert note["check"] == "footprint_drawing" and "switched off" in note["skipped"]


def test_the_panel_turns_checks_off_and_on_and_keeps_them(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    global _STUBS
    saved = _STUBS
    _STUBS = saved + _CHECK_STUBS
    try:
        out = _run(tmp_path, [
            "  mmo_Log.Lines := TStringList.Create;",
            "  LoadPreferences(0); WriteLn('[', ChecksOff, ']');",
            "  ChooseCheck('spice_model', False); ChooseCheck('fab_export', False);",
            "  ChooseCheck('spice_model', False);",
            "  WriteLn(ChecksOff, ' ', CheckIsOff('spice_model'), ' ', CheckIsOff('model_body'));",
            "  ChecksOff := ''; LoadPreferences(0); WriteLn(ChecksOff);",
            "  ChooseCheck('spice_model', True); WriteLn(ChecksOff);",
            "  ChooseCheck('fab_export', True); WriteLn('[', ChecksOff, ']');",
        ], extra=("CheckIsOff", "ChooseCheck"))
    finally:
        _STUBS = saved
    assert out == ["[]", "fab_export,spice_model TRUE FALSE",
                   "fab_export,spice_model", "fab_export", "[]"]


def test_the_check_switches_are_wired_on_the_config_tab():
    dfm = (SCRIPTS / "StatusForm.dfm").read_text(encoding="ascii")
    form = functions(strip_comments(_code("StatusForm.pas")))
    painted = form["PaintChecks"]
    names = {"Fp": "footprint_drawing", "Sym": "symbol_pins", "Pad": "pins_pads",
             "Body": "model_body", "Spice": "spice_model", "Fab": "fab_export"}
    assert set(names.values()) == set(preferences.CHECKS)
    for suffix, name in names.items():
        for state, turn_on in (("On", "True"), ("Off", "False")):
            handler = f"opt_Chk{suffix}{state}Click"
            assert f"= {handler}" in dfm, handler
            assert f"ChooseCheck('{name}', {turn_on})" in form[handler], handler
            assert f"opt_Chk{suffix}{state}" in painted, handler
    assert "StorePreferences(0)" in form["ChooseCheck"]
    assert "PaintChecks(0)" in form["PaintAgentSettings"]
    assert "lbl_CfgChecksHint" in painted


def test_config_md_the_panel_and_the_server_name_the_same_checks():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "CONFIG.md").read_text(
        encoding="utf-8")
    dfm = (SCRIPTS / "StatusForm.dfm").read_text(encoding="ascii")
    assert set(preferences.CHECK_LABELS) == set(preferences.CHECKS) == set(
        preferences.CHECK_REASONS)
    for name, label in preferences.CHECK_LABELS.items():
        row = f"| {label} | {preferences.CHECKS[name]} | {preferences.CHECK_REASONS[name]} |"
        assert row in doc, name
        assert f"Caption = '{label}'" in dfm, label
        hint = preferences.if_the_user_insists(name)
        assert f'"{label}" off on the Config' in hint and "CONFIG.md" in hint
        assert label in preferences.checks_guide()


def test_tool_guide_answers_a_user_who_insists():
    from eda_agent.tools import guidance
    recipe = next(r for r in guidance._RECIPES if "go ahead anyway" in r["task"])
    assert recipe["note"] == preferences.checks_guide()
    assert "lib_set_pad_properties" in [name for name, _ in recipe["avoid"]]
