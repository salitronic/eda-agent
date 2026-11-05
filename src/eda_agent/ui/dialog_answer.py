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


@dataclass(frozen=True)
class Read:
    """Read the dialog's text by OCR, as screen rows, into ``read``.
    ``show`` names a view filter to tick first (the change order's Only
    Show Errors); ``read_filtered`` says whether it took. A filter that
    does not take is noted and never cancels: it changes only the view."""
    show: str = ""


Step = Union[Check, Press, Read]


@dataclass(frozen=True)
class Pass:
    """A dialog that can come up on the way, and the button that carries
    on, pressed only when the dialog's text (read by OCR) contains
    ``says``. For a title as common as "Confirm", where the text is what
    tells one question from another. What it said goes into ``passed``."""
    caption: str
    says: str = ""


def _letters(text: str) -> str:
    """Letters and digits only, with the pairs OCR confuses made one."""
    import re
    return re.sub(r"[^0-9a-z]", "", text.lower()).translate(str.maketrans("oils", "0115"))


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
           run_within: float = 300.0, poll: float = 0.1,
           on_the_way: Optional[dict[str, Union[str, "Pass"]]] = None) -> dict[str, Any]:
    """Wait for the dialog titled ``title`` and run ``steps`` on it.

    ``on_the_way`` maps the title of a dialog that can come up first to
    the button that carries on to ``title``; each one pressed is listed in
    ``passed``.

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
                       appear_within, run_within, poll, on_the_way or {})
    finally:
        if owned:
            comtypes.CoUninitialize()


def _answer(pid: int, title: str, steps: list[Step], stop: threading.Event,
            appear_within: float, run_within: float, poll: float,
            on_the_way: dict[str, Union[str, Pass]]) -> dict[str, Any]:
    out: dict[str, Any] = {"dialog": title, "appeared": False, "done": [],
                           "closed": False}
    heard: dict[int, str] = {}           # what a text-matched dialog said, by window
    pressed: dict[int, float] = {}       # when each window was last pressed
    deadline = time.monotonic() + appear_within
    dialog = None
    while not stop.is_set() and time.monotonic() < deadline:
        dialog = _find(pid, title)
        if dialog is not None:
            break
        _pass(pid, on_the_way, heard, out, pressed)
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
        if isinstance(step, Read):
            from . import ocr
            if step.show:
                out["read_filtered"] = bool(_set(current, Check(step.show)).get("ok"))
                time.sleep(0.5)                  # the grid repaints
            out["read"] = ocr.read_window_rows(dialog.hwnd)
            out["done"].append("read")
            continue
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
                # A run can raise its own question (Update From PCB
                # Libraries asks which attributes to update when the
                # change order executes).
                _pass(pid, on_the_way, heard, out, pressed)
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


def _pass(pid: int, on_the_way: dict[str, Union[str, Pass]],
          heard: dict[int, str], out: dict[str, Any],
          pressed: dict[int, float]) -> None:
    """Press the button that carries on in any dialog of ``on_the_way``
    that is open, a text-matched one only when its text matches."""
    for first, carry_on in on_the_way.items():
        before = _find(pid, first)
        if before is None:
            continue
        want = carry_on if isinstance(carry_on, Pass) else Pass(carry_on)
        said = ""
        if want.says:
            said = heard.get(before.hwnd)
            if said is None:
                from . import ocr
                said = heard[before.hwnd] = " ".join(ocr.read_window_rows(before.hwnd))
            if _letters(want.says) not in _letters(said):
                continue
        # Once per window: a dialog can take a moment to close, and a
        # second press could land on whatever replaces it.
        if time.monotonic() - pressed.get(before.hwnd, -10.0) < 2.0:
            continue
        button = before.find_button(want.caption)
        if button is not None and button.enabled:
            windows.click(button)
            pressed[before.hwnd] = time.monotonic()
            entry = {"dialog": before.title, "pressed": want.caption}
            if said:
                entry["said"] = said
            out.setdefault("passed", []).append(entry)


def _gone(pid: int, hwnd: int, poll: float, within: float = 10.0) -> bool:
    """Has the dialog left the open dialogs? Altium keeps some closed
    dialogs' window handles, so a closed one is one no longer listed."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if _fresh(pid, hwnd) is None:
            return True
        time.sleep(poll)
    return False
