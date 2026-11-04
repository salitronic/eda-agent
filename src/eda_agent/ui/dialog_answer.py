# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Answer one Altium dialog a tool opens, from a short list of steps.

A tool whose bridge command raises a modal (the change order, the
Teardrops options) waits on the bridge while this runs on a thread:
wait for the dialog, set its options, press its buttons. Settings and
presses go through messages to the dialog's own controls, so neither the
mouse nor the keyboard is taken from the user, and each setting is read
back. When a setting does not take, the dialog is cancelled rather than
committed with options nobody chose.

Whether tools answer their dialogs at all is the Config tab's Dialogs
switch (preferences.dialogs_auto).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional, Union

from . import windows


@dataclass(frozen=True)
class Check:
    """Set the checkbox or radio button labelled ``label``."""
    label: str
    on: bool = True


@dataclass(frozen=True)
class Press:
    """Press a button. ``until_greyed`` waits for the button to grey out
    (a run that has finished); ``closes`` waits for the dialog to go."""
    caption: str
    until_greyed: bool = False
    closes: bool = False


Step = Union[Check, Press]


def _find(pid: int, title: str):
    for dialog in windows.dialogs(pid):
        if title.lower() in (dialog.title or "").lower():
            return dialog
    return None


def _fresh(pid: int, hwnd: int):
    """The dialog read again, so its buttons' enabled state is current."""
    for dialog in windows.dialogs(pid):
        if dialog.hwnd == hwnd:
            return dialog
    return None


def _set(dialog, step: Check) -> dict[str, Any]:
    from . import controls
    # By role: a dialog can carry a panel with the same caption as the
    # option, and the panel is not what gets set.
    found = (controls.find(dialog, step.label, role="checkbox")
             or controls.find(dialog, step.label, role="radiobutton"))
    if not found:
        return {"ok": False, "reason": f"no option labelled {step.label!r}"}
    if found.get("checked") == step.on:
        # Already so; a greyed option in the wanted state needs no click.
        return {"ok": True, "changed": False}
    return controls.set_checked(found["hwnd"], step.on)


def answer(pid: int, title: str, steps: list[Step],
           stop: Optional[threading.Event] = None, appear_within: float = 60.0,
           run_within: float = 300.0, poll: float = 0.1) -> dict[str, Any]:
    """Wait for the dialog titled ``title`` and run ``steps`` on it.

    Returns ``appeared``, ``done`` (the steps that ran), ``closed`` and a
    ``reason`` when something did not happen.
    """
    # COM is per thread, and this runs on a worker thread: without it the
    # accessible reads that find and set an option fail.
    try:
        import comtypes
        comtypes.CoInitialize()
        owned = True
    except Exception:                            # noqa: BLE001 - reads then fail and say so
        owned = False
    try:
        return _answer(pid, title, steps, stop or threading.Event(),
                       appear_within, run_within, poll)
    finally:
        if owned:
            comtypes.CoUninitialize()


def _answer(pid: int, title: str, steps: list[Step], stop: threading.Event,
            appear_within: float, run_within: float, poll: float) -> dict[str, Any]:
    out: dict[str, Any] = {"dialog": title, "appeared": False, "done": [],
                           "closed": False}
    deadline = time.monotonic() + appear_within
    dialog = None
    while not stop.is_set() and time.monotonic() < deadline:
        dialog = _find(pid, title)
        if dialog is not None:
            break
        time.sleep(poll)
    if dialog is None:
        out["reason"] = f"{title!r} did not appear"
        return out
    out["appeared"] = True

    # The window appears before its controls are created: wait until the
    # last button the steps press is there.
    last = next((s.caption for s in reversed(steps) if isinstance(s, Press)), "")
    ready = time.monotonic() + 10.0
    while last and time.monotonic() < ready:
        current = _fresh(pid, dialog.hwnd)
        if current is None or current.find_button(last) is not None:
            break
        time.sleep(poll)

    for step in steps:
        current = _fresh(pid, dialog.hwnd)
        if current is None:
            out["closed"] = True
            out["reason"] = f"{title!r} closed before {step}"
            return out
        if isinstance(step, Check):
            result = _set(current, step)
            if not result.get("ok"):
                out["reason"] = (f"{step.label!r} could not be set "
                                 f"({result.get('reason')}); the dialog was "
                                 f"cancelled")
                cancel = current.find_button("Cancel")
                if cancel is not None:
                    windows.click(cancel)
                    out["closed"] = _gone(pid, dialog.hwnd, poll)
                return out
            out["done"].append(f"{step.label}={'on' if step.on else 'off'}")
            continue

        button = current.find_button(step.caption)
        if button is None or not button.enabled:
            out["reason"] = f"{step.caption!r} was not available"
            return out
        windows.click(button)
        out["done"].append(step.caption)
        if step.until_greyed:
            end = time.monotonic() + run_within
            while time.monotonic() < end:
                time.sleep(poll * 2)
                again = _fresh(pid, dialog.hwnd)
                button = again.find_button(step.caption) if again else None
                if button is None or not button.enabled:
                    break
            else:
                out["reason"] = (f"{step.caption!r} was still running after "
                                 f"{run_within:.0f} s; the dialog was left open")
                return out
        if step.closes:
            out["closed"] = _gone(pid, dialog.hwnd, poll)
            if not out["closed"]:
                out["reason"] = f"{step.caption!r} did not close {title!r}"
            return out
    return out


def _gone(pid: int, hwnd: int, poll: float, within: float = 10.0) -> bool:
    """Has the dialog left the open dialogs? Altium keeps some closed
    dialogs' window handles, so a closed one is one no longer listed."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if _fresh(pid, hwnd) is None:
            return True
        time.sleep(poll)
    return False
