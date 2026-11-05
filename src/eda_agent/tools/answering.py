# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run a bridge command that opens a dialog, and answer the dialog.

The command blocks the bridge while its dialog is up, so the answer runs
on a thread (ui.dialog_answer) while the call waits, and the bridge is
told not to stop the call for that dialog. The Config tab's Dialogs
switch decides whether this happens: Manual, or menus and dialogs
switched off, leaves the dialog for the user as Altium shows it.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any, Optional

from ..ui.dialog_answer import Step, answer


def driver(title: str, steps: list[Step],
           on_the_way: Optional[dict[str, Any]] = None):
    """``(run, stop)`` that answers the dialog, or None when it is left to
    the user or Altium's process cannot be named. ``on_the_way`` is passed
    to ``dialog_answer.answer``."""
    from ..preferences import dialogs_auto
    if not dialogs_auto():
        return None
    from .uiauto import _altium_pid
    pid, problem = _altium_pid()
    if problem:
        return None
    stop = threading.Event()
    return (lambda: answer(pid, title, steps, stop, on_the_way=on_the_way)), stop


async def send_answering(bridge, command: str, params: dict, title: str,
                         steps: list[Step], timeout: float = 360.0,
                         on_the_way: Optional[dict[str, Any]] = None,
                         ) -> tuple[Any, Optional[dict]]:
    """The command's reply, and what was done to its dialog (None when the
    dialog was left to the user). ``on_the_way``: dialogs that can come up
    before it, by title, with the button that carries on."""
    found = driver(title, steps, on_the_way)
    if found is None:
        return await bridge.send_command_async(command, params), None
    run, stop = found
    pressing = asyncio.get_running_loop().run_in_executor(None, run)
    try:
        with contextlib.ExitStack() as expected:
            for name in [title, *(on_the_way or {})]:
                expected.enter_context(bridge.expecting(name))
            result = await bridge.send_command_async(command, params, timeout=timeout)
    finally:
        stop.set()
    return result, await pressing
