# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Placing an Altium 365 comment through the interface (issue #146).

The sequence was measured on AD 26.11: Place > Comment, J L for Jump To
Location, Enter to place, then the popup's CommentTextBox and Post. These
tests replace the interface layer with a fake that behaves as Altium did,
so each decision in ui/comments.py is checked without Altium.
"""
from __future__ import annotations

import pytest

from eda_agent.ui import comments, controls, dialog_report, menu, uia, windows
from eda_agent.ui.windows import Control, Window

FRAME = 1
JUMP = 50
#: As measured: the tree lists the Y field first, X sits above it.
FIELD_Y = Control(hwnd=11, class_name="TXPEdit", text="3000mil",
                  rect=(853, 538, 1091, 559))
FIELD_X = Control(hwnd=12, class_name="TXPEdit", text="4000mil",
                  rect=(853, 506, 1091, 527))


class FakeAltium:
    """Altium's comment pick, as it answered on screen."""

    def __init__(self, menu_ok=True, refusal="", popup=True):
        self.menu_ok, self.refusal, self.popup = menu_ok, refusal, popup
        self.keys, self.written, self.pressed, self.closed = [], [], [], []
        self.open_dialogs, self.popup_open, self.value = [], False, ""

    def install(self, monkeypatch):
        monkeypatch.setattr(menu, "frame", lambda pid: Window(FRAME, "TDocumentForm", "AD", pid))
        monkeypatch.setattr(menu, "click_path", self.click_path)
        monkeypatch.setattr(windows, "send_keys", self.send_keys)
        monkeypatch.setattr(windows, "dialogs", lambda pid: list(self.open_dialogs))
        monkeypatch.setattr(windows, "capture", self.capture)
        monkeypatch.setattr(windows, "wait_until", lambda check, timeout, poll=0.02: bool(check()))
        monkeypatch.setattr(windows, "wait_for_close", lambda hwnd, timeout, poll=0.05: True)
        monkeypatch.setattr(windows, "close_window", self.close_window)
        monkeypatch.setattr(controls, "set_text", self.set_text)
        monkeypatch.setattr(uia, "invoke", self.invoke)
        monkeypatch.setattr(uia, "set_value", self.set_value)
        monkeypatch.setattr(uia, "element_info", self.element_info)
        monkeypatch.setattr(dialog_report, "describe_dialog",
                            lambda window, use_ocr=True: {"message": [self.refusal]})

    def click_path(self, pid, path, **_):
        if not self.menu_ok:
            return {"ok": False, "reason": "'Comment...' is not in 'Place'"}
        assert path == comments.MENU_PATH
        return {"ok": True, "opened": []}

    def send_keys(self, sequence, hold=0.03, target=None):
        self.keys.append(sequence)
        if sequence == "j l":
            if self.refusal:
                self.open_dialogs = [Window(60, "TMessageForm", "Altium Designer", 7)]
            else:
                self.open_dialogs = [Window(JUMP, "TSchJumpLocation", comments.JUMP_TITLE, 7,
                                            controls=[FIELD_Y, FIELD_X])]
        elif sequence == "enter" and self.popup:
            self.popup_open = True
        return {"ok": True}

    def capture(self, hwnd):
        return next((d for d in self.open_dialogs if d.hwnd == hwnd), None)

    def close_window(self, hwnd):
        self.closed.append(hwnd)
        self.open_dialogs = [d for d in self.open_dialogs if d.hwnd != hwnd]

    def set_text(self, hwnd, text, settle=0.2):
        self.written.append((hwnd, text))
        return {"ok": True, "text": text}

    def invoke(self, hwnd, name):
        self.pressed.append((hwnd, name))
        if hwnd == JUMP and name == "OK":
            self.open_dialogs = []
            return {"ok": True}
        if hwnd == FRAME and name == comments.POST and self.value:
            self.popup_open = False
            return {"ok": True}
        return {"ok": False, "reason": f"no element named {name!r}"}

    def set_value(self, hwnd, name, text):
        assert (hwnd, name) == (FRAME, comments.TEXT_BOX)
        self.value = text
        return {"ok": True, "value": text}

    def element_info(self, hwnd, name, control_type=None):
        if hwnd == FRAME and name == comments.TEXT_BOX and self.popup_open:
            return {"name": "", "automation_id": comments.TEXT_BOX, "enabled": True}
        return None


@pytest.fixture
def altium(monkeypatch):
    def make(**kwargs):
        fake = FakeAltium(**kwargs)
        fake.install(monkeypatch)
        return fake
    return make


def test_the_point_goes_into_the_fields_by_position_and_is_posted(altium):
    fake = altium()
    out = comments.place(7, 2000, 5000, "check R12")
    assert out["ok"] and out["posted"]
    # X into the UPPER field, though the tree lists Y first.
    assert fake.written == [(FIELD_X.hwnd, "2000mil"), (FIELD_Y.hwnd, "5000mil")]
    assert fake.keys == ["j l", "enter"]
    assert fake.value == "check R12"
    assert (FRAME, comments.POST) in fake.pressed
    assert "Workspace" in out["kept"]


def test_a_box_instead_of_the_jump_is_read_closed_and_reported(altium):
    fake = altium(refusal="Document is not under version control")
    out = comments.place(7, 2000, 5000, "check R12")
    assert not out["ok"]
    assert out["altium"] == "Document is not under version control"
    assert fake.open_dialogs == [] and 60 in fake.closed
    assert fake.keys[-1] == "escape escape", "the pick is left running"
    assert fake.written == [] and fake.value == ""


def test_no_popup_means_nothing_is_typed_or_posted(altium):
    fake = altium(popup=False)
    out = comments.place(7, 2000, 5000, "check R12")
    assert not out["ok"] and "posted" not in out
    assert comments.NEEDS_WORKSPACE in out["reason"]
    assert fake.value == "" and (FRAME, comments.POST) not in fake.pressed
    assert fake.keys[-1] == "escape escape"


def test_a_sheet_without_the_menu_entry_is_refused_with_the_reason(altium):
    fake = altium(menu_ok=False)
    out = comments.place(7, 2000, 5000, "check R12")
    assert not out["ok"]
    assert comments.NEEDS_WORKSPACE in out["reason"]
    assert fake.keys == []
