# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A wrong-document refusal must say where the operation DOES live.

"No PCB library is active" is true and unhelpful. It reports what is
missing and not that the same operation exists for the other document
kind, so the reasonable conclusion from it is that the capability is
absent. That conclusion was drawn and reported more than once, and each
time the tool existed under the other namespace.

Measured before the hint was added: 277 document-resolution refusals in
the Pascal, of which 5 named a tool.

The hint is attached in ``BuildErrorResponseDetailed`` rather than at
those 277 sites, so what this guards is the table and the call, not the
wording of each message. A new document-kind code added without a hint
fails here.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "altium"
_MAIN = (_SCRIPTS / "Main.pas").read_text(encoding="latin-1")

#: Codes that must carry a cross-document hint, because the same
#: operation genuinely exists for another document kind.
_MUST_HINT = ("NO_PCBLIB", "NO_SCHLIB", "NO_PCB", "NO_BOARD",
              "NO_SCHDOC", "NO_SCHEMATIC")

#: Codes deliberately left without one, with the reason. Listing them
#: is what makes the set above a decision rather than an oversight.
_NO_HINT_NEEDED = {
    "NO_DOCUMENT": "nothing is open at all, so there is no sibling",
    "NO_LIBRARY": "the message already says to supply library_path",
    "NO_SCH_SERVER": "an infrastructure failure, not a document mix-up",
    "USE_DEDICATED_TOOL": "the message already names the tool to call",
    # The board is the right document and the LAYER is the wrong kind, so
    # there is no sibling document to point at. The refusal already names
    # pcb_place_polygon_rect for the signal-layer case, which is the
    # redirect a hint would have added.
    "NOT_A_PLANE": "a wrong layer kind, not a wrong document, and the "
                   "message already names the tool for the other case",
    "WRONG_DOCUMENT_FOCUSED": "already names asked-for and actual",
    "WRONG_LIBRARY": "already names asked-for and actual",
    "WRONG_DOC_KIND": "already says to pass sheet_path",
    "WRONG_FOCUS": "already names the document to focus and the tool "
                   "that focuses it",
    "NOT_A_MEMBER": "the project resolved fine and the document simply is "
                    "not in it, so there is no other document to point at. "
                    "The message names the path that was asked for.",
}


def _hint_body() -> str:
    match = re.search(
        r"^Function CrossDocumentHint\b.*?(?=^(?:Function|Procedure)\s)",
        _MAIN, re.MULTILINE | re.DOTALL)
    assert match, ("CrossDocumentHint is gone; refusals no longer say where "
                   "the operation lives")
    return match.group(0)


def _codes_used_in_the_pascal() -> set[str]:
    """Every document-resolution error code the handlers actually raise."""
    codes: set[str] = set()
    for f in sorted(_SCRIPTS.glob("*.pas")):
        if f.name == "Altium_MCP.pas":
            continue  # build output, not a source
        text = f.read_text(encoding="latin-1")
        for code in re.findall(
                r"BuildErrorResponse(?:Detailed)?\(RequestId,\s*'([A-Z_]+)'",
                text):
            if re.search(r"NO_(PCB|SCH|LIB|BOARD|DOC)|WRONG_|NOT_A_|"
                         r"USE_DEDICATED|NO_FOCUS", code):
                codes.add(code)
    return codes


def test_the_scan_actually_found_the_refusals():
    """A regex that stopped matching would make this file vacuous."""
    codes = _codes_used_in_the_pascal()
    assert len(codes) >= 10, (
        f"only {len(codes)} document-resolution codes found; the scan broke "
        f"and this guard is checking nothing")
    assert "NO_PCBLIB" in codes


def test_the_hint_is_actually_applied_to_the_message():
    """A table nothing reads would pass every check below."""
    builder = re.search(
        r"^Function BuildErrorResponseDetailed\b.*?(?=^Function BuildErrorResponse\b)",
        _MAIN, re.MULTILINE | re.DOTALL)
    assert builder, "BuildErrorResponseDetailed not found"
    body = builder.group(0)
    assert "CrossDocumentHint(ErrorCode)" in body, (
        "the hint table is never consulted, so no refusal carries one")
    assert "ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Hint" in body, (
        "the hint is computed and never appended to the message")


def _builder_body() -> str:
    match = re.search(
        r"^Function BuildErrorResponseDetailed\b.*?(?=^Function BuildErrorResponse\b)",
        _MAIN, re.MULTILINE | re.DOTALL)
    assert match, "BuildErrorResponseDetailed not found"
    return match.group(0)


def _hint_arm() -> str:
    """The branch that runs only for a wrong-document code."""
    body = _builder_body()
    start = body.index("If Hint <> '' Then")
    return body[start:body.index("End;", start)]


def test_a_handler_that_already_points_is_not_double_hinted():
    """A specific pointer beats the generic one and must win."""
    arm = _hint_arm()
    assert "Pointed := MessageNamesATool(ErrorMsg)" in _builder_body(), (
        "without this, a message that already names a tool gets the "
        "generic hint bolted on after it")
    assert re.search(
        r"If Not Pointed Then\s+ErrorMsg := WithFullStop\(ErrorMsg\) \+ ' ' \+ Hint",
        arm), "the generic hint is no longer conditional on it"


# --------------------------------------------------------------------------
# A refusal says what IS focused, not only what is not
# --------------------------------------------------------------------------

def _note_body() -> str:
    match = re.search(
        r"^Function FocusedDocumentNote\b.*?(?=^(?:Function|Procedure)\s)",
        _MAIN, re.MULTILINE | re.DOTALL)
    assert match, "FocusedDocumentNote is gone"
    return match.group(0)


def test_a_wrong_document_refusal_names_what_is_focused():
    """"No schematic document is active", said with a .SchLib focused.

    The session that met it went looking for the call that had stolen the
    focus, and blamed one that never touches it. Naming the .SchLib would
    have made the next move obvious. Scoped to the wrong-document branch:
    a note on EVERY error would name a document on a timeout.
    """
    arm = _hint_arm()
    assert "Focus := FocusedDocumentNote(0)" in arm, (
        "wrong-document refusals no longer say what is focused")
    assert "ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Focus" in arm, (
        "the focused document is read and never reaches the message")
    outside = _builder_body().replace(arm, "")
    assert "FocusedDocumentNote" not in outside, (
        "the focus note is added outside the wrong-document branch, so "
        "unrelated errors name a document too")


def test_the_pointer_check_reads_the_handlers_words_not_the_file_name():
    """power_lib_parts.SchLib contains "lib_", which reads as a pointer.

    Decided after the note went on, a library named like that would
    suppress the one hint the caller needed.
    """
    body = _builder_body()
    assert (body.index("Pointed := MessageNamesATool(ErrorMsg)")
            < body.index("ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Focus")), (
        "the tool-name check runs after the file name is appended")


def test_the_note_gives_a_file_name_not_a_path():
    """A full path carries the user's name into every log and transcript."""
    body = _note_body()
    assert "DM_FocusedDocument" in body
    assert "ExtractFileName(Doc.DM_FullPath)" in body, (
        "the note no longer reduces the path to a file name")
    assert body.count("DM_FullPath") == 1, (
        "DM_FullPath is read somewhere other than inside ExtractFileName")


def test_the_note_touches_no_editor_server():
    """It runs inside the error builder, on every wrong-document refusal.

    Referencing SchServer or PCBServer while that server is not loaded is
    an undeclared-identifier modal that Try/Except cannot catch, and here
    it would turn a refusal into a halted polling loop.
    """
    body = _note_body()
    for server in ("SchServer", "PCBServer", "GetCurrentPCBBoard",
                   "GetCurrentSchDocument"):
        assert server not in body, f"FocusedDocumentNote references {server}"


def test_the_note_is_defined_before_the_builder_calls_it():
    """DelphiScript has no forward declarations."""
    assert (_MAIN.index("Function FocusedDocumentNote")
            < _MAIN.index("Function BuildErrorResponseDetailed"))


@pytest.mark.parametrize("code", _MUST_HINT)
def test_every_document_kind_code_carries_a_hint(code):
    assert f"'{code}'" in _hint_body(), (
        f"{code} refuses without saying where the operation does live, "
        f"which reads as the capability being absent")


@pytest.mark.parametrize("code", sorted(_codes_used_in_the_pascal()))
def test_every_code_is_either_hinted_or_listed_as_not_needing_one(code):
    """The point of the guard: a NEW code cannot slip in unconsidered."""
    hinted = f"'{code}'" in _hint_body()
    assert hinted or code in _NO_HINT_NEEDED, (
        f"{code} is a document-resolution refusal with no cross-document "
        f"hint and no entry in _NO_HINT_NEEDED. Either add it to "
        f"CrossDocumentHint, or record here why it needs none")


@pytest.mark.parametrize("code,why", sorted(_NO_HINT_NEEDED.items()))
def test_the_exemptions_are_still_real_codes(code, why):
    """An exemption for a code nobody raises is dead weight that hides
    the next one."""
    assert code in _codes_used_in_the_pascal(), (
        f"{code} is exempted but no handler raises it any more")
    assert why.strip()


def test_the_hint_names_a_namespace_that_exists():
    body = _hint_body()
    for namespace in ("pcb_", "lib_", "sch_", "obj_"):
        assert namespace in body, (
            f"the hint never mentions the {namespace} namespace")


def test_a_note_never_runs_on_from_the_handlers_message():
    """Live: "No schematic document is active No document has editor focus."

    Handler messages rarely end in a full stop. Both appends go through
    WithFullStop, and it must be defined before the builder calls it.
    """
    arm = _hint_arm()
    assert arm.count("WithFullStop(ErrorMsg)") == 2, (
        "an append in the wrong-document branch skips WithFullStop")
    assert (_MAIN.index("Function WithFullStop")
            < _MAIN.index("Function BuildErrorResponseDetailed"))
    body = re.search(r"^Function WithFullStop\b.*?^End;", _MAIN,
                     re.MULTILINE | re.DOTALL).group(0)
    for mark in ("'.'", "'!'", "'?'"):
        assert mark in body, f"WithFullStop no longer treats {mark} as an ending"
