# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Start a console program without opening a console window.

On Windows a console program (powershell, git, ngspice, kicad-cli) started
from a process with no console of its own gets a NEW, visible console
window unless the caller passes CREATE_NO_WINDOW; capturing its output
does not stop the window being made. From a terminal the child shares the
terminal's console, so nothing shows; from a windowless host (pythonw, a
Tk or Qt application, a service) a black window flashes up for as long as
the child runs (issue #142). Elsewhere the flag does not exist and this
passes nothing.
"""

from __future__ import annotations

import subprocess


def no_window() -> dict:
    """Keyword arguments for subprocess.run / Popen and
    asyncio.create_subprocess_exec that keep a console window from opening."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}
