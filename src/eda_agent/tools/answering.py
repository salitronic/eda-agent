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
import threading
from typing import Any, Optional

from ..ui.dialog_answer import Step, answer


def driver(title: str, steps: list[Step]):
    """``(run, stop)`` that answers the dialog, or None when it is left to
    the user or Altium's process cannot be named."""
    from ..preferences import dialogs_auto
    if not dialogs_auto():
        return None
    from .uiauto import _altium_pid
    pid, problem = _altium_pid()
    if problem:
        return None
    stop = threading.Event()
    return (lambda: answer(pid, title, steps, stop)), stop


async def send_answering(bridge, command: str, params: dict, title: str,
                         steps: list[Step], timeout: float = 360.0,
                         ) -> tuple[Any, Optional[dict]]:
    """The command's reply, and what was done to its dialog (None when the
    dialog was left to the user)."""
    found = driver(title, steps)
    if found is None:
        return await bridge.send_command_async(command, params), None
    run, stop = found
    pressing = asyncio.get_running_loop().run_in_executor(None, run)
    try:
        with bridge.expecting(title):
            result = await bridge.send_command_async(command, params, timeout=timeout)
    finally:
        stop.set()
    return result, await pressing
