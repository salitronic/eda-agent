# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Execute and close the Engineering Change Order an Update PCB opens.

Presses go through Win32 messages to the dialog's own buttons, so neither
the mouse nor the keyboard is taken from the user. The order is the one a
person follows: Execute Changes, wait until Altium greys that button out
(the run has finished), then Close. What the change order did is read
back from the board afterwards; its grid cannot be read.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from . import windows

TITLE = "Engineering Change Order"


def _find(pid: int):
    for dialog in windows.dialogs(pid):
        if TITLE.lower() in (dialog.title or "").lower():
            return dialog
    return None


def _button(pid: int, hwnd: int, caption: str):
    for dialog in windows.dialogs(pid):
        if dialog.hwnd == hwnd:
            return dialog.find_button(caption)
    return None


def drive(pid: int, stop: threading.Event, appear_within: float = 60.0,
          run_within: float = 300.0, poll: float = 0.1) -> dict[str, Any]:
    """Wait for the change order, execute it and close it.

    Returns ``appeared``, ``executed``, ``finished`` (the Execute button
    greyed out before ``run_within``) and ``closed``; ``reason`` when a
    step did not happen. Stops waiting when ``stop`` is set.
    """
    out: dict[str, Any] = {"appeared": False, "executed": False,
                           "finished": False, "closed": False}
    deadline = time.monotonic() + appear_within
    dialog = None
    while not stop.is_set() and time.monotonic() < deadline:
        dialog = _find(pid)
        if dialog is not None:
            break
        time.sleep(poll)
    if dialog is None:
        out["reason"] = "no change order appeared"
        return out
    out["appeared"] = True

    execute = dialog.find_button("Execute Changes")
    if execute is None or not execute.enabled:
        out["reason"] = "Execute Changes was not available"
    else:
        windows.click(execute)
        out["executed"] = True
        deadline = time.monotonic() + run_within
        while time.monotonic() < deadline:
            time.sleep(poll * 2)
            again: Optional[Any] = _button(pid, dialog.hwnd, "Execute Changes")
            if again is None or not again.enabled:
                out["finished"] = True
                break
        if not out["finished"]:
            out["reason"] = (f"the change order was still running after "
                             f"{run_within:.0f} s; it was left open")
            return out

    close = _button(pid, dialog.hwnd, "Close")
    if close is not None:
        windows.click(close)
        # Altium keeps the change order's window handle after closing it,
        # so closed means no longer listed among the open dialogs.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not any(d.hwnd == dialog.hwnd for d in windows.dialogs(pid)):
                out["closed"] = True
                break
            time.sleep(poll)
    if not out["closed"]:
        out["reason"] = out.get("reason") or "Close did not shut the change order"
    return out
