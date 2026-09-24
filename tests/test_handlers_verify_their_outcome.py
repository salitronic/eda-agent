# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guards for the defects found by the live sweep of 2026-08-24.

Every one of these was a tool reporting success for something it never
did. They are grouped here because they are one bug wearing eleven
faces, and because the fixes are easy to undo by accident: a handler
that stops re-reading still compiles, still returns, and still looks
right in review. Only the read-back distinguishes them.

These are SOURCE guards. The Pascal cannot be executed here, so each
one pins the specific call or literal whose absence caused the measured
failure. That is weaker than driving the handler, and it is the reason
each test names what was MEASURED rather than describing an intention.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASCAL = ROOT / "scripts" / "altium"
TOOLS = ROOT / "src" / "eda_agent" / "tools"


def _src(name: str) -> str:
    return (PASCAL / name).read_text(encoding="utf-8", errors="replace")


def _body(text: str, func: str) -> str:
    """The source of one Pascal function, up to the next one."""
    start = text.index(f"Function {func}(")
    nxt = text.find("\nFunction ", start + 1)
    return text[start:nxt if nxt != -1 else len(text)]


def _decommented(text: str) -> str:
    """Drop brace comments so a mention in prose is not read as code.

    STRING LITERALS FIRST, and this is not a detail. A regex that strips
    every ``{...}`` also eats the JSON these handlers build, because
    ``'{"drc_confirmed":false'`` opens a brace inside a quoted string.
    A previous scan in this project reported 92 defined functions as
    missing for exactly that reason, and the first draft of this file
    reproduced it: two guards failed against source that was correct.

    Pascal strings are single-quoted, with '' as an escaped quote, and
    a comment cannot start inside one. So track the quote state and
    only treat a brace as a comment when outside it.
    """
    out, in_string, in_comment = [], False, False
    for ch in text:
        if in_comment:
            if ch == "}":
                in_comment = False
            out.append(" " if ch != "\n" else "\n")
            continue
        if ch == "'":
            in_string = not in_string
            out.append(ch)
            continue
        if ch == "{" and not in_string:
            in_comment = True
            out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------
# The dangerous one: a close that aimed at somebody else's project.
# ---------------------------------------------------------------

def test_project_scoped_saves_are_not_workspace_wide():
    """proj_close and proj_save took a project_path and saved EVERYTHING.

    MEASURED: proj_close on a scratch project raised an Unsaved Changes
    prompt naming a client project with 17 documents. Answering it would
    have written and closed work the caller never named.
    """
    text = _decommented(_src("Project.pas"))
    for func in ("Proj_Close", "Proj_Save"):
        body = _body(text, func)
        assert "WorkspaceManager:SaveAll" not in body, (
            f"{func} saves the whole workspace despite taking a "
            f"project_path; use SaveProjectMembers")
        assert "SaveProjectMembers" in body, (
            f"{func} must save only the project it was given")


def test_close_confirms_the_project_actually_went_away():
    body = _body(_decommented(_src("Project.pas")), "Proj_Close")
    assert "FindProjectByPath" in body, (
        "proj_close must look the project up again; a cancelled save "
        "prompt aborts the close and the process layer cannot say so")


# ---------------------------------------------------------------
# Handlers that did nothing and said otherwise.
# ---------------------------------------------------------------

def test_remove_document_removes_rather_than_closing_a_window():
    """CloseObject shuts the editor window and leaves membership alone.

    MEASURED: success reported, and the .PrjPcb still carried the
    DocumentPath afterwards.
    """
    body = _body(_decommented(_src("Project.pas")), "Proj_RemoveDocument")
    assert "DM_RemoveSourceDocument" in body, (
        "proj_remove_document must call DM_RemoveSourceDocument")
    assert "WorkspaceManager:CloseObject" not in body, (
        "closing the document window is not removing it from the project")


def test_set_parameter_uses_the_api_that_works():
    """DocumentAddParameter wrote nothing, and its only citation was ours.

    The sole occurrence of that process name in reference/ is CoAltium,
    a vendored copy of this project, so the evidence for it was
    circular. DM_AddParameter is used by an independent script.
    """
    body = _body(_decommented(_src("Project.pas")), "Proj_SetParameter")
    assert "DM_AddParameter" in body
    assert "WorkspaceManager:DocumentAddParameter" not in body


def test_the_write_tools_read_back_before_claiming_success():
    """Each of these echoed its argument and called that a result."""
    proj = _decommented(_src("Project.pas"))
    lib = _decommented(_src("Library.pas"))

    # proj_set_parameter must re-read DM_Parameters after writing.
    setp = _body(proj, "Proj_SetParameter")
    assert setp.count("DM_ParameterCount") >= 2, (
        "proj_set_parameter must re-read the parameters after saving, "
        "not echo the requested value back")

    # lib_create_symbol must resolve the symbol it claims to have made.
    create = _body(lib, "Lib_CreateSymbol")
    assert "GetState_SchComponentByLibRef" in create, (
        "lib_create_symbol returned success for a part_count=3 call that "
        "created no component at all; it must resolve the symbol")


def test_file_producing_tools_check_the_file_exists():
    """proj_export_pdf named a PDF it never wrote; run_output likewise."""
    proj = _decommented(_src("Project.pas"))
    assert "FileExists" in _body(proj, "Proj_ExportPDF"), (
        "proj_export_pdf must confirm the file before claiming it")
    out = _body(proj, "Proj_GenerateOutput")
    assert "DirectoryExists" in out and "FileExists" in out, (
        "proj_run_output reported generated:true having written nothing")


def test_run_process_reports_dispatch_not_success():
    """Altium accepts an unknown process name without error.

    MEASURED: obj_run_process("Sch:ThisProcessDoesNotExist") returned
    success true. Same defect as #83, which was fixed in app_run_menu
    while this sibling was left.
    """
    body = _body(_decommented(_src("Generic.pas")), "Gen_RunProcess")
    assert '"dispatched":true' in body
    assert '"success":true' not in body


def test_a_zero_drc_cannot_masquerade_as_a_clean_board():
    """Cancelling the DRC dialog produced violation_count 0.

    Identical to a board that genuinely passes, which is the worst
    shape this bug takes: it does not fail, it reports good news.
    """
    body = _body(_decommented(_src("PCB.pas")), "PCB_RunDRC")
    assert "drc_confirmed" in body, (
        "a zero from pcb_run_drc must be qualified by whether the check "
        "actually ran")


# ---------------------------------------------------------------
# Geometry.
# ---------------------------------------------------------------

def test_footprint_primitives_are_placed_relative_to_the_footprint():
    """Pads landed 50000 mils from the footprint they belonged to.

    MEASURED: a footprint authored by these tools and placed on a board
    reported a bounding box of 52452 x 50144 mils, a part over four feet
    across, because MilsToCoord(x) is an ABSOLUTE board coordinate and a
    PcbLib footprint sits at Altium's library origin.
    """
    text = _src("Library.pas")
    for func in ("Lib_AddFootprintPad", "Lib_AddFootprintPads",
                 "Lib_AddFootprintTrack", "Lib_AddFootprintTracks",
                 "Lib_AddFootprintArc", "Lib_AddFootprintText"):
        body = _decommented(_body(text, func))
        writes = re.findall(r"(?:Pad|Track|Arc|Text)\.\w*"
                            r"(?:X|Y|X1|Y1|X2|Y2|XCenter|YCenter|"
                            r"XLocation|YLocation)\s*:=\s*([^;]+);", body)
        placement = [w for w in writes if "MilsToCoord" in w]
        assert placement, f"{func}: no coordinate writes found to check"
        for w in placement:
            assert "FootprintOrigin" in w or "Footprint." in w, (
                f"{func} writes an absolute board coordinate: {w.strip()!r}")


def test_pad_shape_is_compared_against_a_pad_shape():
    """eRoundRectangle is a SCHEMATIC object id, not a pad shape.

    Both identifiers exist, so nothing errored and the comparison simply
    never matched: every rounded-rectangle pad read back as round.
    """
    text = _decommented(_src("Library.pas"))
    assert "Pad.TopShape = eRoundRectangle" not in text, (
        "pad shape compared against a schematic object id")
    assert "Pad.TopShape = eRoundedRectangular" in text


# ---------------------------------------------------------------
# Python side.
# ---------------------------------------------------------------

def test_batch_files_are_not_written_with_translated_newlines():
    """CRLF left a carriage return glued to the LAST field on each line.

    A rename therefore wrote a LibReference ending in CR, and a
    parameter got a CR in its value.
    """
    src = (TOOLS / "library.py").read_text(encoding="utf-8")
    writers = re.findall(r"open\(batch_path[^)]*\)", src)
    assert writers, "no batch writers found"
    for w in writers:
        assert 'newline=""' in w, f"batch writer translates newlines: {w}"


def test_the_pascal_trims_the_batch_fields_too():
    """Both halves, because either alone is a contract stated once."""
    text = _decommented(_src("Library.pas"))
    for func in ("Lib_BatchRename", "Lib_BatchSetParams"):
        body = _body(text, func)
        assert "Trim(Copy(" in body, (
            f"{func} must trim parsed fields; a writer is easy to change "
            f"back by accident")


def test_batch_failures_say_which_item_and_why():
    """failed:1 with no reason is not something a caller can act on."""
    text = _decommented(_src("Library.pas"))
    for func in ("Lib_BatchRename", "Lib_BatchSetParams"):
        assert "AddFailReason" in _body(text, func), (
            f"{func} reports only a count")


def test_the_measured_modal_tools_are_declared_modal():
    """Published as silent, each one blocks the bridge on a dialog."""
    from eda_agent.tools.metadata import INTERACTION_OVERRIDES, MODAL
    for name in ("pcb_run_drc", "proj_export_pdf", "proj_run_output",
                 "lib_reload_library", "proj_remove_document", "proj_close"):
        assert INTERACTION_OVERRIDES.get(name) == MODAL, (
            f"{name} was measured raising a modal that blocks the bridge")


def test_the_press_tool_can_answer_a_handleless_dialog():
    """WPF dialogs expose no button handles, so click has no target.

    MEASURED: "Unsaved Changes" blocked the bridge for over two minutes
    while app_press_dialog_button could only refuse. A plain Enter
    cleared it.
    """
    from eda_agent.ui import windows
    assert hasattr(windows, "press_key")
    src = (TOOLS / "uiauto.py").read_text(encoding="utf-8")
    assert "press_key" in src, (
        "app_press_dialog_button has no keyboard fallback, so a dialog "
        "with no button handles cannot be answered at all")


def test_listing_a_menu_that_exposes_nothing_is_not_a_success():
    """MEASURED: File returned ok true with items [] three times."""
    src = (ROOT / "src" / "eda_agent" / "ui" / "menu.py").read_text(
        encoding="utf-8")
    assert "exposed no readable" in src, (
        "an empty menu listing must not report ok true")


# ---------------------------------------------------------------
# Found while verifying the fixes above, on 2026-08-25.
# ---------------------------------------------------------------

def test_library_lookups_fall_back_to_walking_the_document():
    """The index only knows components the library was LOADED with.

    MEASURED in a brand-new empty library: lib_create_symbol succeeded
    and resolved its own symbol by name, and the very next command could
    not find it, while Component_1 from the file resolved throughout.
    Authoring a symbol and using it immediately could not work at all.
    """
    text = _decommented(_src("Library.pas"))
    assert "Function ScanLibForComponent" in text
    assert "Function LookupLibComponent" in text

    # Every caller goes through a wrapper; only the in-memory half may
    # touch the raw index lookup, and LookupLibComponent is built on it.
    raw = [m for m in re.finditer(r"\w+\.GetState_SchComponentByLibRef\(", text)]
    inside_helper = _body(text, "FindLibComponentInMemory")
    assert len(raw) == inside_helper.count(".GetState_SchComponentByLibRef("), (
        "a by-name lookup bypasses the wrappers, so it cannot see a "
        "symbol created in this session")
    assert "ScanLibForComponent" in inside_helper
    assert "FindLibComponentInMemory(" in _body(text, "LookupLibComponent")


def test_save_all_reports_what_reached_disk():
    """MEASURED twice, and the second time the guard was the problem.

    First: saved:true while every save was being declined. Altium refuses
    to save while a command is active in the editor and asks whether to
    write a copy instead, and DoFileSave does not raise, so "no exception"
    was never evidence of anything.

    Then: saved:true while nothing was written at all, because the check
    added for the first case walked the workspace exactly as the save
    does. Same GetWorkspace, same DM_Projects loop, same silent Exit when
    it comes back Nil, so an empty enumeration made the save write nothing
    AND the count report zero, and zero read as success. A verifier that
    shares the failure mode of the thing it verifies cannot catch it, and
    a session lost 29 edits to exactly that.

    So the evidence now has to be independent of Altium: a document either
    got newer on disk or it did not.
    """
    body = _body(_decommented(_src("Application.pas")), "App_SaveAll")

    assert "CountChangedAges" in body, (
        "app_save_all must verify by file timestamp, which is independent "
        "of every Altium-side signal in this path. Both ServerDoc.Modified "
        "and CountDirtyDocuments have been observed reporting clean while "
        "changes were pending")
    assert "documents_written" in body, (
        "the reply must say how many documents actually got newer, or the "
        "caller cannot tell a save from a no-op")

    # Success must not be the only outcome the handler can produce.
    assert '"saved":false' in body, (
        "app_save_all has no failure branch, so it cannot report a save "
        "that did not happen")

    # And no claim of success may be made before the evidence is gathered.
    save_call = body.index("SaveAllDirty(0)")
    for pos in _positions(body, '"saved":true'):
        assert pos > save_call, (
            "app_save_all reports saved:true before the save pass has run, "
            "so the claim cannot be based on anything")


def _positions(text, needle):
    out, at = [], text.find(needle)
    while at != -1:
        out.append(at)
        at = text.find(needle, at + 1)
    return out


def test_a_pcb_query_refuses_a_scope_it_cannot_honour():
    """MEASURED: eArcObject scoped to a named SYMBOL returned 19 arcs
    from an unrelated client board, with nothing to reveal the swap."""
    body = _body(_decommented(_src("Generic.pas")), "Gen_QueryObjects")
    assert "SCOPE_NOT_SUPPORTED" in body, (
        "a PCB object type silently ignores scope; it must refuse instead")
    # And the refusal must come before the SchLib side effect.
    assert body.index("SCOPE_NOT_SUPPORTED") < body.index("ApplyLibComponentScope"), (
        "ApplyLibComponentScope moves the SchLib's current component, so a "
        "query about to be refused must not run it first")


def test_pcb_transactions_survive_an_exception():
    """An unclosed PreProcess poisons Altium until it is RESTARTED.

    While the PCB server believes a command is active, every save of a
    PCB document is refused with "A command is currently active and save
    cannot be completed at this time" and the editor offers to write a
    copy instead. Restarting the polling loop does NOT clear it, because
    the state lives in the PCB server rather than the script, and
    neither does Escape in the editor.

    MEASURED on 2026-08-25: a PcbLib and its board went a full day
    without a successful save while SchLib documents beside them saved
    normally, so the authored footprints existed only in memory.

    The loop body calls MatchesFilterPCB, BuildObjectJsonPCB and
    ApplySetPropertiesPCB on caller-supplied property names, so an
    exception there is an ordinary outcome rather than a remote one.
    """
    body = _body(_decommented(_src("PCBGeneric.pas")), "ProcessPCBBoardObjects")
    assert body.count("PCBServer.PreProcess") >= 1
    # Every PreProcess must be followed by a Finally before the function ends.
    for m in re.finditer(r"PCBServer\.PreProcess", body):
        rest = body[m.end():]
        fin, post = rest.find("Finally"), rest.find("PCBServer.PostProcess")
        assert fin != -1 and post != -1 and fin < post, (
            "a PCBServer.PreProcess is not unwound by a Finally; an "
            "exception in the loop would leave Altium unable to save any "
            "PCB document until it is restarted")


def test_the_recovery_tool_unwinds_more_than_one_level():
    """One PostProcess was MEASURED not to clear a real stuck state.

    PreProcess nests and the depth cannot be queried, so the only way
    down is to unwind further than any plausible leak. Calling it with
    nothing outstanding is harmless.
    """
    body = _body(_decommented(_src("Application.pas")), "App_ExitActiveCommand")
    assert re.search(r"For\s+I\s*:=\s*1\s+To\s+\d+", body), (
        "app_exit_active_command must call PostProcess repeatedly")


def test_every_bridge_call_watches_for_a_modal_while_it_waits():
    """A blocked call must say so in seconds, not after the timeout.

    A modal blocks Altium's single-threaded scripting engine, so the
    handler can neither answer nor explain. MEASURED across one working
    day: nine separate calls each burned their full 120s or 300s window
    while the same "A command is currently active" prompt sat on screen
    the entire time.

    The probe must be INSIDE the poll loop, not only on the timeout
    path, and it must repeat: a handler can raise a dialog part-way
    through, which a single early check would miss.
    """
    src = (ROOT / "src" / "eda_agent" / "bridge"
           / "altium_bridge.py").read_text(encoding="utf-8")

    assert "_DIALOG_PROBE_AFTER" in src and "_DIALOG_PROBE_EVERY" in src

    start = src.index("def _poll_loop")
    end = src.index("\n    def ", start + 1)
    loop = src[start:end]

    assert "_dialog_probe()" in loop, (
        "the poll loop never looks for a dialog, so a blocked call waits "
        "out its whole timeout before anyone finds out why")
    assert "_DIALOG_PROBE_EVERY" in loop, (
        "the probe must repeat on an interval; a one-shot check misses a "
        "dialog raised part-way through a long handler")

    # The first probe must be soon. A grace longer than a few seconds
    # re-creates the silence this removes.
    after = float(re.search(r"_DIALOG_PROBE_AFTER\s*=\s*([\d.]+)", src).group(1))
    every = float(re.search(r"_DIALOG_PROBE_EVERY\s*=\s*([\d.]+)", src).group(1))
    assert after <= 5.0, f"first dialog check waits {after}s, too long"
    assert every <= 5.0, f"dialog re-check every {every}s, too slow"


def test_a_library_lookup_rereads_the_document_as_a_last_resort():
    """Saving is NOT enough to make a new symbol findable by name.

    MEASURED in order: lib_create_symbol succeeded and resolved its own
    symbol inside the creating command; app_save_all reported
    still_dirty 0 and the saved file provably contained the symbol;
    every by-name lookup still missed; CloseObject then OpenObject and
    the name resolved again.

    So the reopen is the step that matters, and it belongs in the
    save/read path rather than in the caller's head.
    """
    text = _decommented(_src("Library.pas"))
    assert "Function RefreshSchLibFromDisk" in text

    refresh = _body(text, "RefreshSchLibFromDisk")
    assert "DoFileSave" in refresh, (
        "the reopen must save first; closing a dirty document loses the "
        "edits or raises a prompt nothing here can answer")
    assert "CloseObject" in refresh and "OpenObject" in refresh

    lookup = _body(text, "LookupLibComponent")
    assert "RefreshSchLibFromDisk" in lookup, (
        "the lookup must re-read the document when index and walk both "
        "miss, which is the only thing measured to work")
    assert "RefreshingLib" in lookup, (
        "the reopen retry must be guarded against re-entering")
