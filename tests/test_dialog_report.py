# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Saying what is on screen, and never overstating how well it is known.

The reporting failures this guards against all happened in one live
session. A modal error sat on screen while the tool that caused it
reported no dialog. A "no differences" dialog blocked the editor while a
plan waited for an Engineering Change Order that was never coming. And
an empty message list read as "the dialog says nothing" when the dialog
was in fact displaying two lines of text that Win32 cannot see.

The last one is the subtle one and it drives most of this file. Altium
paints dialog text with Delphi TLabel, which owns no window handle:
GetWindowText, WM_GETTEXT and the MSAA tree were all measured empty on a
real dialog, so the text is recovered by OCR. OCR is a GUESS, and the
contract is that a caller can always tell which parts of a report are
exact and which are recognised.

Fixtures are shapes recorded from real Altium dialogs, so this runs in
CI with no editor and no OCR engine.
"""

from __future__ import annotations

import pytest

from eda_agent.ui import dialog_report as dr
from eda_agent.ui import windows as w


@pytest.fixture(autouse=True)
def _no_ocr_leak():
    """Recognised text is cached by window handle, and these fixtures
    reuse handles, so without this a later case reads an earlier case's
    text and the failure looks like a logic bug."""
    dr.forget_ocr()
    yield
    dr.forget_ocr()


def _control(class_name, text, enabled=True, style=0):
    return w.Control(hwnd=abs(hash((class_name, text))) % 100000,
                     class_name=class_name, text=text, enabled=enabled,
                     style=style)


def _window(title, class_name, pairs):
    return w.Window(hwnd=4242, class_name=class_name, title=title, pid=1,
                    controls=[_control(c, t) for c, t in pairs])


#: Recorded from the live dialog. Two panels carrying no window text at
#: all (the message is painted on them) and one real button.
NO_DIFFERENCES = _window(
    "Comparator Results (No Differences)", "TMessageForm",
    [("TXPExtPanel", ""), ("TXPExtPanel", ""), ("TXPBitBtn", "OK")])

#: Recorded from the live Engineering Change Order.
ECO = _window(
    "Engineering Change Order", "TChangeManagementForm",
    [("TXPExtPanel", ""), ("TdxTreeList", ""), ("TThemedScrollBar", ""),
     ("TXPExtPanel", ""), ("TXPCheckBox", "Only Show Errors"),
     ("TXPBitBtn", "Validate Changes"), ("TXPBitBtn", "Execute Changes"),
     ("TXPBitBtn", "&Report Changes..."), ("TXPBitBtn", "Close")])


# --------------------------------------------------------------------
# Classification.
# --------------------------------------------------------------------

@pytest.mark.parametrize("title,message,expected", [
    ("Comparator Results (No Differences)", [], "nothing_to_do"),
    ("Engineering Change Order", [], "engineering_change_order"),
    ("Error", ["Cannot compare a source document against its owner "
               "project SCH"], "error"),
    ("Information", ["Nothing to update"], "nothing_to_do"),
    ("Confirm", ["Are you sure?"], "confirm"),
    ("Update From Libraries", [], "wizard"),
    ("Something Altium Has Never Shown Us", [], "unknown"),
])
def test_dialogs_are_classified(title, message, expected):
    assert dr.classify(title, message) == expected


def test_the_message_is_classified_when_the_caption_is_generic():
    """A bare 'Error' caption says nothing; the body carries the meaning.

    Measured: Altium's compare refusal is titled just "Error" and puts
    "Cannot compare a source document ..." in the painted body.
    """
    assert dr.classify("Error", []) == "error"
    assert dr.classify("Altium Designer",
                       ["Cannot compare a source document"]) == "error"


def test_error_and_confirm_demand_a_human():
    for title in ("Error", "Warning", "Confirm"):
        assert dr.classify(title, []) in dr.NEEDS_A_HUMAN


def test_nothing_to_do_does_not_demand_a_human():
    """The distinction the plan runner depends on.

    "No differences" is a SUCCESS. Treating it as needing attention
    would stall an automation on a design that is already correct.
    """
    assert dr.classify("Comparator Results (No Differences)",
                       []) not in dr.NEEDS_A_HUMAN


# --------------------------------------------------------------------
# Honesty about where the text came from.
# --------------------------------------------------------------------

def test_readable_text_is_marked_as_read_not_guessed(monkeypatch):
    monkeypatch.setattr(dr.ocr, "read_window_text",
                        lambda *a, **k: ["should not be called"])
    window = _window("Some Dialog", "TMessageForm",
                     [("TXPLabel", "Everything is fine"),
                      ("TXPBitBtn", "OK")])

    out = dr.describe_dialog(window)

    assert out["message"] == ["Everything is fine"]
    assert out["message_source"] == "window_text"
    assert "ocr_text" not in out, (
        "OCR must not run when the text was read exactly")


def test_painted_text_is_recovered_and_labelled_as_ocr(monkeypatch):
    """The whole point. This dialog's body is invisible to Win32."""
    monkeypatch.setattr(dr.ocr, "read_window_text", lambda *a, **k: [
        "Comparing Schematic Document [X.SchDoc] And PCB Document",
        "No Differences Detected", "0K"])

    out = dr.describe_dialog(NO_DIFFERENCES)

    assert out["message"] == [], "Win32 genuinely reads nothing here"
    assert out["message_source"] == "ocr"
    assert "No Differences Detected" in out["ocr_text"]
    assert "RECOGNISED FROM PIXELS" in out["message_note"]


def test_an_empty_message_never_reads_as_an_empty_dialog(monkeypatch):
    """With OCR unavailable the report must still not claim silence."""
    monkeypatch.setattr(dr.ocr, "read_window_text", lambda *a, **k: [])

    out = dr.describe_dialog(NO_DIFFERENCES)

    assert out["message_source"] == "none"
    assert "NOT evidence the dialog is empty" in out["message_note"]
    assert out["has_unreadable_content"] is True, (
        "panels with no window text mean painted content, which is the "
        "difference between 'says nothing' and 'I cannot see it'")


def test_ocr_copies_of_known_strings_are_dropped(monkeypatch):
    """The title and buttons are read exactly; an OCR copy adds only risk.

    Measured: the recogniser returned '0K' for the OK button and 'x' for
    the close glyph. Anything it produces that duplicates a string
    already known exactly is noise.
    """
    monkeypatch.setattr(dr.ocr, "read_window_text", lambda *a, **k: [
        "Comparator Results (No Differences)", "OK", "Real message here"])

    out = dr.describe_dialog(NO_DIFFERENCES)

    assert out["ocr_text"] == ["Real message here"]


def test_recognising_happens_once_per_dialog_not_once_per_look(monkeypatch):
    """A driver POLLS, and recognising costs about two seconds.

    Measured: without this the loop spawned the Windows OCR engine on
    every pass over the same unchanged dialog, which made a working run
    look like a hang.
    """
    calls = []

    def counting(hwnd, *a, **k):
        calls.append(hwnd)
        return ["No Differences Detected"]

    monkeypatch.setattr(dr.ocr, "read_window_text", counting)

    for _ in range(5):
        out = dr.describe_dialog(NO_DIFFERENCES)

    assert len(calls) == 1, f"recognised {len(calls)} times, expected once"
    assert out["ocr_text"] == ["No Differences Detected"]


def test_a_reused_handle_does_not_inherit_the_old_text(monkeypatch):
    """Windows reuses handles. A new dialog on an old handle must not
    be described with the previous dialog's message."""
    monkeypatch.setattr(dr.ocr, "read_window_text",
                        lambda *a, **k: ["first message"])
    first = dr.describe_dialog(NO_DIFFERENCES)

    monkeypatch.setattr(dr.ocr, "read_window_text",
                        lambda *a, **k: ["second message"])
    same_handle_new_dialog = _window(
        "A Different Dialog", "TMessageForm",
        [("TXPExtPanel", ""), ("TXPBitBtn", "OK")])
    same_handle_new_dialog.hwnd = NO_DIFFERENCES.hwnd
    second = dr.describe_dialog(same_handle_new_dialog)

    assert first["ocr_text"] == ["first message"]
    assert second["ocr_text"] == ["second message"]


def test_the_eco_grid_is_reported_as_unreadable():
    """An ECO's pending changes cannot be enumerated before executing."""
    assert ECO.has_unreadable_content() is True


def test_the_eco_buttons_survive_the_report():
    out = dr.describe_dialog(ECO, use_ocr=False)
    captions = {b["caption"] for b in out["buttons"]}
    assert {"Validate Changes", "Execute Changes", "Close"} <= captions
    assert "Only Show Errors" not in captions, "that is a checkbox"


# --------------------------------------------------------------------
# The process-level answer.
# --------------------------------------------------------------------

def test_blocked_is_reported_and_leads_the_summary(monkeypatch):
    monkeypatch.setattr(w, "available", lambda: True)
    monkeypatch.setattr(w, "dialogs", lambda pid: [NO_DIFFERENCES])
    monkeypatch.setattr(w, "is_blocked", lambda pid: True)
    monkeypatch.setattr(dr.ocr, "read_window_text",
                        lambda *a, **k: ["No Differences Detected"])

    out = dr.report(1234)

    assert out["blocked"] is True
    assert out["summary"].startswith("Altium is BLOCKED by a modal."), (
        "a blocked editor is the single fact that explains a silent "
        "bridge, so it leads")
    assert out["kinds"] == ["nothing_to_do"]
    assert out["needs_a_human"] is False


def test_no_dialogs_is_stated_plainly(monkeypatch):
    monkeypatch.setattr(w, "available", lambda: True)
    monkeypatch.setattr(w, "dialogs", lambda pid: [])
    monkeypatch.setattr(w, "is_blocked", lambda pid: False)

    out = dr.report(1234)

    assert out["dialog_count"] == 0
    assert out["summary"] == "no dialog is open"


def test_blocked_with_no_dialog_does_not_claim_all_is_well(monkeypatch):
    """Blocked but nothing found is its own state, not a clean bill.

    It means something is holding the window that this cannot see, and
    saying "no dialog is open" alone would send a caller looking in the
    wrong place.
    """
    monkeypatch.setattr(w, "available", lambda: True)
    monkeypatch.setattr(w, "dialogs", lambda pid: [])
    monkeypatch.setattr(w, "is_blocked", lambda pid: True)

    out = dr.report(1234)

    assert "something else is holding it" in out["summary"]


def test_it_refuses_rather_than_guessing_without_pywin32(monkeypatch):
    monkeypatch.setattr(w, "available", lambda: False)
    out = dr.report(1234)
    assert out["ok"] is False and "pywin32" in out["reason"]


# --------------------------------------------------------------------
# The information kind, and the precedence that keeps it safe.
#
# Found live: Force Annotate All Schematics finishes with a box
# captioned "Information" saying "No Designator changes are required in
# Project", offering only OK. It classified as unknown, so the driver
# stopped dead and a human had to finish the run. Nearly every Altium
# command ends with one of these.
#
# The risk in fixing it is obvious: an earlier defect was the driver
# pressing OK on ANY unrecognised dialog, which was fixed by making
# unknown always stop. These tests exist to prove that fix survived.
# --------------------------------------------------------------------

def test_an_information_box_is_recognised():
    from eda_agent.ui import dialog_report

    assert dialog_report.classify(
        "Information",
        ["No Designator changes are required in Project"]) == "information"


def test_information_is_not_in_needs_a_human():
    from eda_agent.ui import dialog_report

    assert "information" not in dialog_report.NEEDS_A_HUMAN, (
        "a report with nothing to decide does not need a human")


def test_an_unknown_dialog_offering_ok_still_stops():
    """The fix that must not regress.

    Recognising the BUTTON is not recognising the DIALOG.
    """
    from eda_agent.ui import dialog_driver, dialog_report

    dialog = {"title": "Some Box", "message": ["whatever"],
              "kind": dialog_report.classify("Some Box", ["whatever"]),
              "buttons": [{"caption": "OK", "enabled": 1}]}
    assert dialog["kind"] == "unknown"
    caption, why = dialog_driver.decide(dialog, "proceed", False, set(),
                                        allow_confirm=True)
    assert caption is None and "not recognised" in why


def test_a_failure_announced_in_an_information_box_still_stops():
    """Precedence, which is the whole safety argument.

    Altium reuses the Information caption for things that went wrong,
    so the kind is decided from the MESSAGE first and the caption only
    when nothing else matched.
    """
    from eda_agent.ui import dialog_driver, dialog_report

    for text, expected in (("Cannot open the file", "error"),
                           ("Warning: 3 nets unrouted", "warning"),
                           ("Overwrite the existing file?", "confirm")):
        kind = dialog_report.classify("Information", [text])
        assert kind == expected, f"{text!r} classified {kind}"

        dialog = {"title": "Information", "message": [text], "kind": kind,
                  "buttons": [{"caption": "OK", "enabled": 1}]}
        caption, _ = dialog_driver.decide(dialog, "proceed", False, set())
        assert caption is None, (
            f"{text!r} reads as {kind} and must not be clicked past")


def test_an_information_box_is_acknowledged():
    from eda_agent.ui import dialog_driver, dialog_report

    dialog = {"title": "Information",
              "message": ["No Designator changes are required"],
              "kind": dialog_report.classify(
                  "Information", ["No Designator changes are required"]),
              "buttons": [{"caption": "OK", "enabled": 1}]}
    caption, why = dialog_driver.decide(dialog, "proceed", False, set())
    assert caption == "OK"
    assert "nothing to decide" in why


def test_the_title_only_tier_runs_after_every_message_pattern():
    """Structure, so the precedence cannot be reordered by accident."""
    import inspect

    from eda_agent.ui import dialog_report

    source = inspect.getsource(dialog_report.classify)
    assert source.index("_KINDS") < source.index("_TITLE_ONLY_KINDS"), (
        "matching the caption before the message would let an error "
        "captioned Information be clicked past")


def test_ocr_words_are_grouped_into_screen_rows():
    """A grid read line by line comes back split by column, so a message
    lost the row it belongs to. Words are placed by position instead."""
    from eda_agent.ui import ocr

    words = ["40\t200\t20\tAdd", "700\t202\t20\tFootprint", "860\t201\t20\tNot",
             "300\t199\t20\tD10", "40\t240\t20\tAdd", "300\t241\t20\tR50",
             "bad line"]
    assert ocr.rows_from_words(words) == ["Add D10 Footprint Not", "Add R50"]
