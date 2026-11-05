# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Place an Altium 365 comment on a schematic, through the interface.

THERE IS NO API FOR IT. Running Sch:CreateCommentThread from the bridge
script halts the script engine: the command waits for a click inside
the script's own call, and the loop stops for every session until it is
restarted. The menu route here runs the same command from Altium's own
message loop, and nothing in it needs the bridge.

Measured on AD 26.11 (issue #146):

- A free sheet's Place menu has no Comment entry.
- On a local project the comment is placed but kept unsaved: closing the
  project asks "Save To Workspace: You have unsaved comments". Yes moves
  the project into the Workspace, No drops the comment. Nothing is
  written next to the project. Only a Workspace project keeps comments.
- A Workspace project's sheet not yet saved to the server raised
  "Document is not under version control" when the command was run
  from the bridge script.
- During the pick, J then L opens Jump To Location. Its two fields carry
  no labels and the tree lists Y first; X is the upper one on screen.
  The jump also zooms the view in on the point.
- Enter then places the point and opens a WPF popup inside the main
  window's UIA tree, not as a dialog: an edit with automation id
  CommentTextBox and a Post button that is disabled until the box holds
  text. Setting the box through the Value pattern enables it.
- The popup and the marker are invisible to a window capture, and the
  Comments and Tasks panel is owner-drawn, so the posted text cannot be
  read back here. Posting is confirmed by the popup closing.
"""

from __future__ import annotations

from typing import Any

from . import dialog_report, menu, uia, windows

#: The menu entry that starts the pick, on the schematic editor's menu.
MENU_PATH = "Place|Comment..."

#: The dialog J L opens during an interactive pick.
JUMP_TITLE = "Jump To Location"

#: The popup's text box and button, by UIA automation id and name.
TEXT_BOX = "CommentTextBox"
POST = "Post"

#: Said whenever no comment could start, because that is the usual cause.
NEEDS_WORKSPACE = (
    "Altium offers comments only on a document of a project, and keeps "
    "them only on a project saved to an Altium 365 Workspace")

#: Said with every posted comment: whether the project is in a Workspace
#: is not readable through the bridge, and on a local one this is what
#: happens next.
KEPT_ONLY_IN_A_WORKSPACE = (
    "On a project saved to an Altium 365 Workspace the comment is stored "
    "there. On a local project it stays unsaved: closing the project asks "
    "'Save To Workspace: You have unsaved comments', where Yes moves the "
    "project into the Workspace and No drops the comment")


def _jump_fields(dialog) -> tuple:
    """The X and Y edits of Jump To Location, by position, or (None, None).

    The tree lists Y first, so the order there is the wrong one to trust;
    on screen X sits above Y.
    """
    edits = [c for c in dialog.controls if c.class_name == "TXPEdit" and c.visible]
    if len(edits) != 2:
        return None, None
    upper, lower = sorted(edits, key=lambda c: (c.rect[1], c.rect[0]))
    return upper, lower


def _close_refusal(window) -> str:
    """Read a box Altium raised instead of the pick, close it, say what it said."""
    described = dialog_report.describe_dialog(window)
    message = " ".join(described.get("message") or []) or window.title
    if not uia.invoke(window.hwnd, "OK").get("ok"):
        windows.close_window(window.hwnd)
    windows.wait_for_close(window.hwnd, timeout=3.0)
    return message


def _leave_pick(frame_hwnd: int) -> None:
    """End a pick still running, so the editor is not left waiting for a click."""
    try:
        windows.send_keys("escape escape", target=frame_hwnd)
    except Exception:                            # noqa: BLE001
        pass


def place(pid: int, x: float, y: float, text: str,
          timeout: float = 8.0) -> dict[str, Any]:
    """Place one comment at (x, y) mils on the focused schematic and post it.

    The caller focuses the document first. Returns ``ok`` and ``posted``,
    or ``ok: False`` with ``reason`` and, when Altium said why, ``altium``.
    """
    frame = menu.frame(pid)
    if frame is None:
        return {"ok": False, "reason": "no Altium main window"}

    # COM is per thread, and this runs on a worker thread.
    try:
        import comtypes
        comtypes.CoInitialize()
        owned = True
    except Exception:                            # pragma: no cover - guard
        owned = False
    try:
        before = {d.hwnd for d in windows.dialogs(pid)}
        clicked = menu.click_path(pid, MENU_PATH)
        if not clicked.get("ok"):
            # Measured: a free sheet's Place menu has no Comment entry.
            return {"ok": False, "reason": (
                f"{MENU_PATH} could not be run: {clicked.get('reason')}. "
                f"{NEEDS_WORKSPACE}"), "offered": clicked.get("offered")}

        windows.send_keys("j l", target=frame.hwnd)

        def fresh() -> list:
            return [d for d in windows.dialogs(pid) if d.hwnd not in before]

        windows.wait_until(lambda: bool(fresh()), timeout)
        opened = fresh()
        found = opened[0] if opened else None
        if found is None:
            _leave_pick(frame.hwnd)
            return {"ok": False, "reason": (
                f"{JUMP_TITLE} did not open after {MENU_PATH}, so the "
                f"comment pick did not start. {NEEDS_WORKSPACE}")}
        if JUMP_TITLE.lower() not in (found.title or "").lower():
            said = _close_refusal(found)
            # The box can come twice; answer whatever is left.
            for extra in fresh():
                _close_refusal(extra)
            _leave_pick(frame.hwnd)
            return {"ok": False, "altium": said, "reason": (
                f"Altium refused the comment: {said!r}. {NEEDS_WORKSPACE}")}

        # Captured again: the first sight of a dialog can predate its fields.
        windows.wait_until(
            lambda: _jump_fields(windows.capture(found.hwnd) or found)[0] is not None,
            3.0)
        found = windows.capture(found.hwnd) or found
        field_x, field_y = _jump_fields(found)
        if field_x is None:
            windows.close_window(found.hwnd)
            _leave_pick(frame.hwnd)
            return {"ok": False, "reason": (
                f"{JUMP_TITLE} does not show the two coordinate fields "
                f"this was measured with")}
        from . import controls
        for field, value in ((field_x, x), (field_y, y)):
            wrote = controls.set_text(field.hwnd, f"{value:g}mil")
            if not wrote.get("ok"):
                windows.close_window(found.hwnd)
                _leave_pick(frame.hwnd)
                return {"ok": False, "reason": (
                    f"could not write {value:g}mil into {JUMP_TITLE}: "
                    f"{wrote.get('reason')}")}
        if not uia.invoke(found.hwnd, "OK").get("ok"):
            windows.close_window(found.hwnd)
            _leave_pick(frame.hwnd)
            return {"ok": False, "reason": f"could not press OK in {JUMP_TITLE}"}
        windows.wait_for_close(found.hwnd, timeout=3.0)

        # Enter is the click at the jumped point.
        windows.send_keys("enter", target=frame.hwnd)
        if not windows.wait_until(
                lambda: uia.element_info(frame.hwnd, TEXT_BOX) is not None,
                timeout):
            _leave_pick(frame.hwnd)
            return {"ok": False, "reason": (
                f"no comment popup opened at ({x:g}, {y:g}). "
                f"{NEEDS_WORKSPACE}")}

        wrote = uia.set_value(frame.hwnd, TEXT_BOX, text)
        if not wrote.get("ok"):
            _leave_pick(frame.hwnd)
            return {"ok": False, "reason": (
                f"the comment text could not be set: {wrote.get('reason')}. "
                f"The popup was closed unposted")}
        pressed = uia.invoke(frame.hwnd, POST)
        if not pressed.get("ok"):
            _leave_pick(frame.hwnd)
            return {"ok": False, "reason": (
                f"Post could not be pressed: {pressed.get('reason')}. "
                f"The popup was closed unposted")}
        closed = windows.wait_until(
            lambda: uia.element_info(frame.hwnd, TEXT_BOX) is None, timeout)
        if not closed:
            return {"ok": False, "posted": False, "reason": (
                "Post was pressed but the popup is still open; read it with "
                "app_ui read_window")}
        return {"ok": True, "posted": True, "x": x, "y": y,
                "kept": KEPT_ONLY_IN_A_WORKSPACE, "note": (
                    "the popup closed after Post. The text cannot be read "
                    "back here: the Comments and Tasks panel is owner-drawn. "
                    "The jump left the view zoomed in on the point")}
    finally:
        if owned:
            try:
                comtypes.CoUninitialize()
            except Exception:                    # pragma: no cover - guard
                pass
