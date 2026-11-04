# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Execute and close the Engineering Change Order an Update opens.

The order a person follows: Execute Changes, wait until Altium greys that
button out (the run has finished), then Close. What the change order did
is read back from the documents afterwards; its grid cannot be read.
"""
from __future__ import annotations

import threading
from typing import Any

from .dialog_answer import Press, answer

TITLE = "Engineering Change Order"
STEPS = [Press("Execute Changes", until_greyed=True), Press("Close", closes=True)]


def drive(pid: int, stop: threading.Event, **timing: Any) -> dict[str, Any]:
    """Wait for the change order, execute it and close it."""
    return answer(pid, TITLE, STEPS, stop, **timing)
