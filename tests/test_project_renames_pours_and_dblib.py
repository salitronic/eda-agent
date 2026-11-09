# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Project-wide designator renames, a pour's outline, DbLib rows placed in
one call, 3D body positions, and the fixes made alongside them.

Tools are checked against a fake bridge for what they send and refuse; the
Pascal for the behaviour measured on AD 26.11 (a schematic line's two ends,
a sheet symbol anchored top-left, a project keeping a discarded edit, port
fields lost on save, object classes listing names).
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from eda_agent.bridge import altium_bridge
from eda_agent.tools import application, generic, library, pcb, project


@pytest.fixture(autouse=True)
def _no_pin_table(monkeypatch):
    """These build symbols to check their layout; the pin table check has
    its own tests (test_symbol_pins_against_the_pin_table.py)."""
    from eda_agent import preferences
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": "symbol_pins"})

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _routine(unit: str, name: str) -> str:
    text = (ALTIUM / unit).read_text(encoding="utf-8-sig")
    for kind in ("Function", "Procedure"):
        head = f"{kind} {name}("
        if head in text:
            at = text.index(head)
            return text[at:text.index("\nEnd;", at)]
    raise AssertionError(f"no {name} in {unit}")


def _unit(unit: str) -> str:
    return (ALTIUM / unit).read_text(encoding="utf-8-sig")


class _FakeBridge:
    def __init__(self, answers=None):
        self.sent = []
        self.answers = answers or {}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params))
        answer = self.answers.get(command, {"success": True})
        return answer(params) if callable(answer) else answer


def _registered(module, register, monkeypatch, answers=None):
    bridge = _FakeBridge(answers)
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(_Server())
    return tools, bridge


# --- renaming designators across a project ------------------------------------------

PROJECT = r"C:\qa\Q.PrjPcb"
SHEET_A, SHEET_B, SHEET_C = r"C:\qa\A.SchDoc", r"C:\qa\B.SchDoc", r"C:\qa\C.SchDoc"


class _Sheets:
    """Sub-parts as obj_query lists them; batch_modify renames them."""

    def __init__(self, rows, lose_reply=False):
        self.rows = [dict(r) for r in rows]
        self.lose_reply = lose_reply
        self.writes = 0

    def query(self, params):
        return {"objects": [dict(r) for r in self.rows], "count": len(self.rows),
                "sheets_processed": 3}

    def modify(self, params):
        self.writes += 1
        for op in params["operations"].split("~~"):
            scope = op.split(";", 1)[0][len("scope=doc:"):]
            where = op.split(";filter=", 1)[1].split(";set=", 1)[0]
            wanted = dict(c.split("=", 1) for c in where.split("|"))
            renamed = op.split(";set=Designator=", 1)[1]
            for row in self.rows:
                if (row["_doc"] == scope and row["Designator"] == wanted["Designator"]
                        and row["LibReference"] == wanted["LibReference"]):
                    row["Designator"] = renamed
        if self.lose_reply:
            raise ConnectionError("the reply never came")
        return {"operations_processed": 1}


def _subpart(sheet, name, uid, part=1, symbol="R"):
    return {"_doc": sheet, "Designator": name, "UniqueId": uid, "LibReference": symbol,
            "CurrentPartID": str(part)}


def _rename(monkeypatch, sheets, **kwargs):
    tools, bridge = _registered(generic, "register_generic_tools", monkeypatch, {
        "project.load_project_sheets": {"total_sheets": 3, "failed": []},
        "generic.query_objects": sheets.query,
        "generic.batch_modify": sheets.modify,
    })
    return asyncio.run(tools["sch_rename_designators"](project_path=PROJECT, **kwargs)), bridge


def _state(sheets):
    return sorted((Path(r["_doc"]).name, r["UniqueId"], r["Designator"]) for r in sheets.rows)


def test_a_swap_passes_through_temporary_names_and_keeps_a_part_whole(monkeypatch):
    # U1 is a two-part op amp whose parts carry their own UniqueIds (as on
    # AD 26.11); it is still one part and both parts are renamed.
    sheets = _Sheets([_subpart(SHEET_A, "U1", "AAA", 1, "OPA"),
                      _subpart(SHEET_A, "U1", "BBB", 2, "OPA"),
                      _subpart(SHEET_B, "U2", "CCC", 1, "OPA"), _subpart(SHEET_C, "R1", "DDD")])
    result, _ = _rename(monkeypatch, sheets, changes=[
        {"designator": "U1", "to": "U2"}, {"unique_id": "CCC", "to": "U1"}])
    assert result["ok"] and result["verified"], result
    assert sheets.writes == 2
    assert all(n.startswith("TMP") for names in result["temporary"]["now"].values()
               for n in names)
    assert _state(sheets) == [("A.SchDoc", "AAA", "U2"), ("A.SchDoc", "BBB", "U2"),
                              ("B.SchDoc", "CCC", "U1"), ("C.SchDoc", "DDD", "R1")]


def test_a_name_still_held_by_another_part_is_refused_before_writing(monkeypatch):
    sheets = _Sheets([_subpart(SHEET_A, "R1", "A1"), _subpart(SHEET_B, "R2", "B1")])
    result, bridge = _rename(monkeypatch, sheets, changes=[{"designator": "R1", "to": "R2"}])
    assert not result["ok"] and "R2 would name 2 parts" in result["reason"]
    assert sheets.writes == 0 and "generic.batch_modify" not in [c for c, _ in bridge.sent]


def test_copies_of_a_sheet_need_the_sheet_named_and_their_duplicates_are_listed(monkeypatch):
    sheets = _Sheets([_subpart(SHEET_A, "U5", "A1", symbol="X"),
                      _subpart(SHEET_B, "U5", "B1", symbol="X"), _subpart(SHEET_C, "R9", "C1")])
    result, _ = _rename(monkeypatch, sheets, changes=[{"designator": "U5", "to": "U6"}])
    assert not result["ok"] and result["reason"].startswith("U5 names 2 parts")
    assert result["sheets"] == ["A.SchDoc", "B.SchDoc"]
    result, _ = _rename(monkeypatch, sheets, dry_run=True,
                        changes=[{"designator": "R9", "to": "R10"}])
    assert result["ok"] and result["duplicates_left"] == ["U5"] and sheets.writes == 0
    result, _ = _rename(monkeypatch, sheets,
                        changes=[{"designator": "U5", "sheet": "B.SchDoc", "to": "U6"}])
    assert result["verified"]
    assert ("B.SchDoc", "B1", "U6") in _state(sheets)
    assert ("A.SchDoc", "A1", "U5") in _state(sheets)


def test_unannotated_parts_on_one_sheet_are_not_mistaken_for_one(monkeypatch):
    sheets = _Sheets([_subpart(SHEET_A, "R?", "A1"), _subpart(SHEET_A, "R?", "A2")])
    result, _ = _rename(monkeypatch, sheets, changes=[{"designator": "R?", "to": "R1"}])
    assert not result["ok"] and "annotate first" in result["reason"]
    assert sheets.writes == 0


def test_a_lost_reply_is_settled_by_reading_the_project(monkeypatch):
    sheets = _Sheets([_subpart(SHEET_A, "R1", "A1"), _subpart(SHEET_B, "R2", "B1")],
                     lose_reply=True)
    result, _ = _rename(monkeypatch, sheets, changes=[
        {"designator": "R1", "to": "R2"}, {"designator": "R2", "to": "R1"}])
    assert result["verified"], result
    assert "error" in result["final"]["reply"]
    assert _state(sheets) == [("A.SchDoc", "A1", "R2"), ("B.SchDoc", "B1", "R1")]


def test_a_step_that_does_not_read_back_stops_and_says_where_things_are(monkeypatch):
    sheets = _Sheets([_subpart(SHEET_A, "R1", "A1"), _subpart(SHEET_B, "R2", "B1")])
    sheets.modify = lambda params: {"operations_processed": 0}
    result, _ = _rename(monkeypatch, sheets, changes=[{"designator": "R1", "to": "R3"}])
    assert not result["ok"] and result["stage"] == "temporary"
    assert result["temporary"]["now"] == {"R1 on A.SchDoc": ["R1"]}


@pytest.mark.parametrize("changes", [
    [], [{"to": "R2"}], [{"designator": "R1", "unique_id": "X", "to": "R2"}],
    [{"designator": "R1", "to": "R|2"}], ["R1"],
])
def test_a_malformed_change_list_is_refused_first(monkeypatch, changes):
    sheets = _Sheets([_subpart(SHEET_A, "R1", "A1")])
    result, bridge = _rename(monkeypatch, sheets, changes=changes)
    assert result["ok"] is False and bridge.sent == []


# --- a pour's outline --------------------------------------------------------------------

def test_the_pour_outline_names_its_pour_and_refuses_a_bad_index(monkeypatch):
    tools, bridge = _registered(pcb, "register_pcb_tools", monkeypatch)
    assert asyncio.run(tools["pcb_get_pour_outline"](index=-1))["ok"] is False
    assert not bridge.sent
    asyncio.run(tools["pcb_get_pour_outline"](index=2, name=" GND Top "))
    assert bridge.sent[-1] == ("pcb.pour_outline", {"index": "2", "name": "GND Top"})
    command = _routine("PCB.pas", "PCB_PourOutline")
    assert "'POUR_CHANGED'" in command
    assert "Pour.Segments[Corners]" in command          # the closing vertex is reported
    assert "Seg.Radius" in _routine("PCB.pas", "PourCornerJson")


# --- DbLib rows in one call ------------------------------------------------------------------

def test_dblib_rows_go_as_one_op_each_and_unsendable_values_are_refused(monkeypatch):
    tools, bridge = _registered(library, "register_library_tools", monkeypatch)
    dblib = r"C:\db\Parts.DbLib"
    asyncio.run(tools["sch_place_dblib_rows"](
        library_path=dblib, table="Resistors",
        rows=[{"key": "R-10K", "x": 1000.5, "y": 200},
              {"key": "C-100N", "x": 0, "y": 0, "table": "Caps", "rotation": 90,
               "part_id": 2, "designator": "C7"}]))
    command, params = bridge.sent[-1]
    assert command == "library.place_dblib_rows"
    first, second = params["rows"].split("~~")
    assert first == f"key=R-10K;table=Resistors;library_path={dblib};x=1000.5;y=200;rotation=0"
    assert second.endswith("rotation=90;part_id=2;designator=C7") and "table=Caps" in second
    for bad in ({"key": "A;B", "x": 0, "y": 0}, {"key": "A~~B", "x": 0, "y": 0}):
        refused = asyncio.run(tools["sch_place_dblib_rows"](library_path=dblib, table="T",
                                                            rows=[bad]))
        assert refused["success"] is False and "cannot carry" in refused["error"]
    unknown = asyncio.run(tools["sch_place_dblib_rows"](
        library_path=dblib, table="T", rows=[{"key": "A", "x": 0, "y": 0, "colour": 1}]))
    assert "colour" in unknown["error"]
    assert "'place_dblib_rows':" in _unit("Library.pas")


def test_one_and_many_dblib_rows_go_through_the_same_worker():
    one = _routine("Library.pas", "Lib_PlaceDbLibComponent")
    assert "DbLibPlaceTarget(" in one and "DbLibPlaceOne(" in one
    many = _routine("Library.pas", "Lib_PlaceDbLibRows")
    assert "DbLibRowProblem(" in many and "DbLibPlaceOne(" in many
    assert "DbLibPlaceTarget(" in _routine("Library.pas", "DbLibRowProblem")
    assert "LoadComponentFromDatabaseLibrary" in _routine("Library.pas", "DbLibPlaceOne")


def test_a_relink_to_a_key_on_several_rows_changes_nothing(monkeypatch):
    tools, bridge = _registered(generic, "register_generic_tools", monkeypatch, {
        "library.get_dblib_record": {"found": True, "match_count": 2, "scan_capped": False}})
    accepted = inspect.signature(tools["sch_relink_components"]).parameters
    kwargs = {k: v for k, v in {"library_path": r"C:\db\Parts.DbLib", "dblib_table": "R",
                                "dblib_key": "K"}.items() if k in accepted}
    first = next(iter(accepted))
    kwargs.setdefault(first, ["R1"] if "designators" in first else "R1")
    result = asyncio.run(tools["sch_relink_components"](**kwargs))
    assert result.get("error") == "DBLIB_KEY_NOT_UNIQUE", result
    assert "2 rows have it" in result["reason"]
    assert [c for c, _ in bridge.sent] == ["library.get_dblib_record"]


# --- a footprint's 3D body ------------------------------------------------------------------

def test_a_body_centre_needs_the_body_and_is_checked_after_the_move(monkeypatch, tmp_path):
    lib = tmp_path / "L.PcbLib"
    lib.write_bytes(b"")
    answer = {"after": [{"index": 1, "center_x_mils": 10.0, "center_y_mils": -5.5}]}
    tools, bridge = _registered(library, "register_library_tools", monkeypatch,
                                {"library.set_3d_body": answer})
    monkeypatch.setattr(library.pcblib_models, "saved_body_models", lambda *a: [])
    no_body = asyncio.run(tools["lib_set_3d_body"](footprint_name="F", library_path=str(lib),
                                                   center_x=10))
    assert no_body["ok"] is False and not bridge.sent
    moved = asyncio.run(tools["lib_set_3d_body"](footprint_name="F", library_path=str(lib),
                                                 body_index=1, center_x=0.254, units="mm"))
    assert bridge.sent[-1][1]["center_x"] == "10.0" and "center_y" not in bridge.sent[-1][1]
    assert moved["moved_as_asked"] is True
    missed = asyncio.run(tools["lib_set_3d_body"](footprint_name="F", library_path=str(lib),
                                                  body_index=1, center_x=11))
    assert missed["moved_as_asked"] is False
    body = _routine("Library.pas", "Lib_Set3DBody")
    assert "Body.MoveByXY(" in body and "BodyCentre(Body, OrgX, OrgY, NowX, NowY)" in body


# --- fractions of a mil, empty rule writes, keep-outs --------------------------------------------

@pytest.mark.parametrize("module, register, tool, names", [
    (generic, "register_generic_tools", "sch_place_net_label", ("x", "y")),
    (generic, "register_generic_tools", "sch_place_sheet_symbol", ("x1", "y1", "x2", "y2")),
    (pcb, "register_pcb_tools", "pcb_place_fill", ("x1", "y1", "x2", "y2")),
    (pcb, "register_pcb_tools", "pcb_place_arc", ("x_center", "y_center", "radius", "width")),
    (pcb, "register_pcb_tools", "pcb_set_track_width", ("width_mils",)),
    (pcb, "register_pcb_tools", "pcb_trim_extend_track", ("from_x", "tolerance_mils")),
    (library, "register_library_tools", "sch_place_dblib_component", ("x", "y")),
])
def test_coordinates_and_lengths_accept_fractions_of_a_mil(monkeypatch, module, register,
                                                           tool, names):
    tools, _ = _registered(module, register, monkeypatch)
    signature = inspect.signature(tools[tool]).parameters
    for name in names:
        assert signature[name].annotation in (float, "float"), (tool, name)


def test_a_fractional_keepout_width_reaches_altium_unrounded(monkeypatch):
    tools, bridge = _registered(pcb, "register_pcb_tools", monkeypatch)
    asyncio.run(tools["pcb_keepout_board_outline"](width_mils=12.5))
    assert bridge.sent[-1] == ("pcb.keepout_board_outline", {"width": "12.5"})
    refused = asyncio.run(tools["pcb_keepout_board_outline"](width_mils=float("nan")))
    assert refused["success"] is False and len(bridge.sent) == 1


def test_a_rule_write_that_changes_nothing_never_reaches_altium(monkeypatch):
    tools, bridge = _registered(pcb, "register_pcb_tools", monkeypatch)
    result = asyncio.run(tools["pcb_set_rule_properties"](name="Clearance"))
    assert result["ok"] is False and result["properties_updated"] == 0 and not bridge.sent


# --- documents ----------------------------------------------------------------------------------

def test_a_document_in_a_folder_that_is_not_there_is_refused_twice_over(monkeypatch, tmp_path):
    tools, bridge = _registered(application, "register_application_tools", monkeypatch)
    result = asyncio.run(tools["app_create_document"](
        kind="SCH", file_path=str(tmp_path / "missing" / "A.SchDoc")))
    assert result["error"] == "NOT_SAVED" and not bridge.sent
    body = _routine("Application.pas", "App_CreateDocument")
    assert body.index("DirectoryExists(ExtractFilePath(FilePath))") < body.index("OpenNewDocument")
    # An old file at the path proves nothing: only a new file time does.
    assert "FileAge(FilePath) <> AgeBefore" in body
    assert body.index("If Not Saved Then") < body.index("DM_AddSourceDocument")


def test_closing_counts_the_bridges_edits_and_a_project_that_keeps_one():
    body = _routine("Application.pas", "App_CloseDocument")
    assert "DocWasEdited(FilePath)" in body and "ForgetDocEdited(FilePath)" in body
    # Measured: with its project still open, a discarded schematic edit came
    # back on reopening, so such a close is not called discarded.
    assert "project_may_hold_edit" in body and "DocBelongsToOpenProject(FilePath)" in body


def test_port_fields_that_do_not_survive_a_save_are_pointed_out(monkeypatch):
    tools, _ = _registered(generic, "register_generic_tools", monkeypatch,
                           {"generic.modify_objects": lambda params: {"matched": 1}})
    noted = asyncio.run(tools["obj_modify"](object_type="ePort", filter="Name=P",
                                            set="AutoSize=false|HarnessColor=255"))
    assert "do not survive saving" in noted["note"]
    quiet = asyncio.run(tools["obj_modify"](object_type="ePort", filter="Name=P",
                                            set="AutoSize=false"))
    assert "note" not in quiet


def test_a_reply_written_in_the_windows_codepage_still_reads(tmp_path):
    reply = tmp_path / "r.json"
    reply.write_bytes('{"text": "10 \u00b5F"}'.encode("cp1252"))
    if altium_bridge.locale.getencoding().lower().replace("-", "") in ("utf8", "utf8sig"):
        pytest.skip("this system's codepage is UTF-8")
    assert altium_bridge._read_response_json(reply) == {"text": "10 \u00b5F"}
    reply.write_bytes(b'\xef\xbb\xbf{"a": 1}')
    assert altium_bridge._read_response_json(reply) == {"a": 1}


# --- what the Pascal does, as measured on AD 26.11 -----------------------------------------------

def test_a_line_is_answered_from_its_ends_before_the_polyline_branch():
    assert "GetState_Vertex" not in _routine("Generic.pas", "SchLineEndProperty")
    text = _unit("Generic.pas")
    line = text.index("Result := SchLineEndProperty(Obj, PropName)")
    polyline = text.index(
        "Else If PropName = 'VerticesCount' Then Result := IntToStr(Obj.GetState_VerticesCount)")
    assert line < polyline


def test_a_sheet_symbol_hangs_from_its_top_left_corner():
    symbol = _routine("Generic.pas", "Gen_PlaceSheetSymbol")
    assert "Sym.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y2));" in symbol
    entry = _routine("Generic.pas", "Gen_PlaceSheetEntry")
    assert "Entry.Location := Point(MilsToCoordF(EdgeX), MilsToCoordF(EdgeY))" in entry


def test_class_members_and_pair_legs_are_read_as_names():
    members = _routine("Project.pas", "ClassMembersJson")
    assert "IDMObject" not in members and "Name := Cls.DM_Members(I)" in members
    declared = _routine("Project.pas", "Proj_GetDeclaredDesignObjects")
    assert "Pair.DM_PositiveNet" not in declared and "Pair.DM_Members(J)" in declared


def test_annotation_sorts_on_altiums_own_coordinates():
    text = _unit("Project.pas")
    assert "XCoords.Add(IntToStr(Comp.Location.X));" in text
    assert "IntToStr(CoordToMils(Comp.Location" not in text


def test_an_xsignal_class_is_read_back_not_assumed():
    body = _routine("PCB.pas", "PCB_CreateXSignals")
    assert "ClassListsMember(SigClass, PP.Name" in body and "not_in_class" in body


def test_the_board_update_wants_this_projects_board_in_focus():
    body = _routine("Project.pas", "Proj_UpdatePCB")
    assert "PathKey(FocusedPath) <> PathKey(PcbPath)" in body
    assert "PathKey(FocusedProject.DM_ProjectFullPath)" in body


# --- libraries, symbols and calculators ---------------------------------------------------------

def test_a_clean_batch_drc_report_is_read_in_full():
    from eda_agent.export.drc_report import read_drc_report
    report = read_drc_report(str(FIXTURES / "drc_batch_report_clean.html"))
    assert report["counts"]["rule_violations"] == 0 and report["stopped_at_limit"] is False
    assert len(report["rules"]) == 15 and report["rules_add_up"] is True
    assert read_drc_report(str(FIXTURES / "missing.html")) is None


def test_a_symbol_keeps_gap_rows_and_hidden_pins_on_the_grid():
    from eda_agent.design.symbol_gen import generate_ic_symbol
    geom = generate_ic_symbol(
        [{"designator": "1", "name": "A"}, None, {"designator": "2", "name": "B", "hidden": True}],
        [{"designator": "3", "name": "Y"}], pin_length=300, pin_pitch=200)
    left = sorted((p for p in geom.pins if p["rotation"] == 180), key=lambda p: -p["y"])
    assert [p["y"] for p in left] == [0, -400]          # the middle row is the gap
    assert [p["hidden"] for p in left] == [False, True]
    assert all(p["length"] == 300 for p in geom.pins)
    assert geom.body["y2"] == -600 and geom.height_mils == 800
    for bad in ({"pin_length": 250}, {"pin_pitch": 150}, {"char_width": 0}):
        with pytest.raises(ValueError):
            generate_ic_symbol([{"designator": "1", "name": "A"}], [], **bad)


def test_grouped_designators_and_a_bad_part_number_are_refused_first(monkeypatch):
    tools, bridge = _registered(library, "register_library_tools", monkeypatch)
    grouped = asyncio.run(tools["lib_add_pins"](pins=[{"designator": "A4,A6", "name": "GND"}]))
    assert grouped["ok"] is False and "A4,A6" in grouped["reason"]
    for bad in (-1, True, 1.5):
        refused = asyncio.run(tools["lib_add_pins"](pins=[{"designator": "1", "name": "A"}],
                                                    pins_on_part=bad))
        assert refused["ok"] is False and not bridge.sent
    asyncio.run(tools["lib_add_pins"](pins=[{"designator": "1", "name": "A"}], pins_on_part=2))
    assert bridge.sent and "owner_part_id=2" in bridge.sent[-1][1]["pins"].replace(" ", "")


def test_a_symbol_in_parts_gives_each_part_its_body_and_pins(monkeypatch):
    tools, bridge = _registered(library, "register_library_tools", monkeypatch,
                                {"library.add_pins": {"added": 3}})
    made = asyncio.run(tools["lib_create_symbol_in_parts"](name="OPA2", parts=[
        {"left_pins": [{"designator": "1", "name": "IN"}],
         "right_pins": [{"designator": "2", "name": "OUT"}]},
        {"left_pins": [{"designator": "3", "name": "V+"}], "right_pins": []}]))
    assert made["ok"] and made["part_count"] == 2
    rectangles = [p for c, p in bridge.sent if c == "library.add_symbol_rectangle"]
    assert [r["owner_part_id"] for r in rectangles] == ["1", "2"]
    assert [c for c, _ in bridge.sent].index("library.add_pins") > max(
        i for i, (c, _) in enumerate(bridge.sent) if c == "library.add_symbol_rectangle")
    tools, bridge = _registered(library, "register_library_tools", monkeypatch)
    twice = asyncio.run(tools["lib_create_symbol_in_parts"](name="X", parts=[
        {"left_pins": [{"designator": "1", "name": "A"}]},
        {"left_pins": [{"designator": "1", "name": "B"}]}]))
    assert twice["ok"] is False and "parts 1 and 2" in twice["reason"] and not bridge.sent


def test_shared_pins_go_on_part_zero_at_one_place_on_every_part(monkeypatch):
    tools, bridge = _registered(library, "register_library_tools", monkeypatch,
                                {"library.add_pins": {"added": 9}})
    made = asyncio.run(tools["lib_create_symbol_in_parts"](name="OPA2", parts=[
        {"left_pins": [{"designator": "3", "name": "+"}, {"designator": "2", "name": "-"}],
         "right_pins": [{"designator": "1", "name": "OUT"}]},
        {"left_pins": [{"designator": "5", "name": "+"}, {"designator": "6", "name": "-"},
                       {"designator": "9", "name": "TRIM"}],
         "right_pins": [{"designator": "7", "name": "OUTPUT_B"}]}],
        shared_pins={"left_pins": [{"designator": "8", "name": "V+"}],
                     "right_pins": [{"designator": "4", "name": "V-"}]}))
    assert made["ok"] and made["shared"] == ["8", "4"]
    # the pins call, not the last one: the view is refreshed after it
    sent = next(p for c, p in bridge.sent if c == "library.add_pins")["pins"].replace(" ", "")
    rows = [dict(f.split("=", 1) for f in row.split(";") if "=" in f)
            for row in sent.split("~~") if row]
    owner = {r["designator"]: r["owner_part_id"] for r in rows}
    assert owner["8"] == owner["4"] == "0" and owner["1"] == "1" and owner["7"] == "2"
    # each shared pin once, below the tallest part's pins, and every body
    # the same width and tall enough to hold them
    assert sum(r["designator"] == "8" for r in rows) == 1
    v_minus = next(r for r in rows if r["designator"] == "4")
    assert int(v_minus["y"]) < -200
    bodies = [p for c, p in bridge.sent if c == "library.add_symbol_rectangle"]
    assert len({(b["x1"], b["x2"]) for b in bodies}) == 1
    assert all(int(b["y2"]) < int(v_minus["y"]) for b in bodies)
    assert int(v_minus["x"]) == int(bodies[0]["x2"])
    # a designator both shared and on a part, or shared twice, sends nothing
    for parts_pin, shared in (("8", [{"designator": "8", "name": "V+"}]),
                              ("1", [{"designator": "8", "name": "V+"},
                                     {"designator": "8", "name": "V"}])):
        tools, bridge = _registered(library, "register_library_tools", monkeypatch)
        refused = asyncio.run(tools["lib_create_symbol_in_parts"](name="X", parts=[
            {"left_pins": [{"designator": parts_pin, "name": "A"}]},
            {"left_pins": [{"designator": "2", "name": "B"}]}],
            shared_pins={"left_pins": shared}))
        assert refused["ok"] is False and not bridge.sent


def test_the_current_capacity_answers_the_rise_asked_about(monkeypatch):
    tools, _ = _registered(pcb, "register_pcb_tools", monkeypatch)
    capacity = tools["pcb_calc_track_current_capacity"]
    ten = asyncio.run(capacity(width_mils=20, copper_oz=1.0))
    twenty = asyncio.run(capacity(width_mils=20, copper_oz=1.0, delta_t_c=20))
    assert ten["delta_t_c"] == 10
    assert ten["current_a"] == pytest.approx(ten["current_amps"]["10c"], rel=0.01)
    assert twenty["current_a"] > ten["current_a"]
    assert asyncio.run(capacity(width_mils=20, delta_t_c=0))["ok"] is False


def test_user_net_classes_are_counted_apart_from_altiums_own(monkeypatch):
    tools, _ = _registered(pcb, "register_pcb_tools", monkeypatch, {"pcb.get_net_classes":
        lambda p: {"net_classes": [{"name": "All Nets", "super_class": True}, {"name": "PWR"}],
                   "count": 2}})
    result = asyncio.run(tools["pcb_get_net_classes"]())
    assert result["count"] == 2 and result["user_class_count"] == 1


def test_the_library_and_text_fixes_are_in_the_pascal():
    assert "'ALIAS_TAKEN'" in _routine("Library.pas", "Lib_SetSymbolAliases")
    assert "SetOwnerPartFrom(Arc, Component, ExtractJsonValue(Params, 'owner_part_id'))" in _unit(
        "Library.pas")
    assert "'exact_part_counts'" in _unit("Library.pas")
    assert "eIterateFirstLevel" in _routine("Generic.pas", "SetCompParamText")
    assert "JsonRaw('candidates', SchComponentCandidates(SchDoc, Designator))" in _unit(
        "Generic.pas")
    assert "(PropName = 'FontName')" in _unit("PCBGeneric.pas")
    assert "bbox_no_name_comment" in _routine("PCB.pas", "PCB_GetComponents")


def test_a_netlist_drops_the_other_parts_question_marks_and_writes_its_own_line_ends(
        monkeypatch, tmp_path):
    rows = [{"component": "U1", "pin": "A1", "pin_name": "VDD", "net": "3V3"},
            {"component": "U1", "pin": "A1", "pin_name": "VDD", "net": "?"},
            {"component": "U1", "pin": "B2", "pin_name": "NC", "net": "?"},
            {"component": "U1", "pin": "B2", "pin_name": "NC", "net": "?"},
            {"component": "R1", "pin": "1", "pin_name": "1", "net": "3V3"}]
    tools, _ = _registered(project, "register_project_tools", monkeypatch,
                           {"project.get_nets": {"pins": rows}})
    pads = asyncio.run(tools["proj_export_netlist"](output_path=str(tmp_path / "n.net")))
    assert pads["no_net_rows_dropped"] == 1 and pads["node_count"] == 3
    raw = (tmp_path / "n.net").read_bytes()
    assert b"\r\r\n" not in raw and raw.count(b"\r\n") == raw.count(b"\n")
    assert b"*SIGNAL* ?\r\nU1.B2\r\n" in raw and b"R1.1 U1.A1" in raw
    asyncio.run(tools["proj_export_netlist"](output_path=str(tmp_path / "n.csv"),
                                             net_format="tabular"))
    assert b"\r" not in (tmp_path / "n.csv").read_bytes()
