# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""UI Automation answers from an executor thread as it does on the loop.

app_press_dialog_button presses from run_in_executor. The UIA layer kept
one automation object for the process and never initialised COM on the
worker, so every lookup there found nothing: a WPF "Unsaved Changes" came
back as "no element named 'Save'" with nothing offered, while
app_invoke_element, on the loop thread, pressed it seconds later.
"""

from __future__ import annotations

import concurrent.futures
import sys

import pytest

from eda_agent.ui import uia

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not uia.available(),
    reason="UI Automation is Windows only")


def _taskbar() -> int:
    import ctypes
    hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        pytest.skip("no taskbar window on this desktop")
    return hwnd


def test_a_worker_thread_sees_what_the_main_thread_sees():
    hwnd = _taskbar()
    # The worker goes FIRST: the old code failed there whichever thread
    # had made the shared object.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        worker = pool.submit(uia.text_of, hwnd).result()
    main = uia.text_of(hwnd)
    assert main, "the taskbar shows no UIA text even on the main thread"
    assert worker == main
