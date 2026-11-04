# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Execute and close the Engineering Change Order an Update opens.

The order a person follows: Execute Changes, wait until Altium greys that
button out (the run has finished), then Close. Before closing, the grid
is shown with Only Show Errors ticked and read by OCR, so a line that
failed ("Footprint Not Found X") reaches the caller. The grid has no
accessible text, so those rows are recognised from pixels: a hint that
names what failed, with characters such as 0/O and 1/l sometimes wrong.
"""
from __future__ import annotations

import re
import threading
from typing import Any

from .dialog_answer import Press, Read, answer

TITLE = "Engineering Change Order"
STEPS = [Press("Execute Changes", until_greyed=True), Read(show="Only Show Errors"),
         Press("Close", closes=True)]

#: Asked before the change order when some differences cannot go into it
#: ("Continue and create ECO?"): the caller asked for the sync, so it goes
#: on, and the lines that cannot be made show as failed. With nothing to
#: change, Altium says so and only OK closes it.
ON_THE_WAY = {"Comparator Results": "Yes", "No Differences": "OK"}

#: Rows of the dialog that are not change lines: its title (OCR can split
#: it, leaving "Order x"), the column headings, a group heading such as
#: "Add Components(5)" and the buttons.
_NOT_A_LINE = re.compile(
    r"Engineering Change|^Order\b|^Modifications|Affected Object|Validate Changes|"
    r"Execute Changes|Only Show Errors|\(\w{1,3}\)\s*$", re.I)

#: Control characters OCR reads out of the status icons.
_CONTROL = re.compile(r"[\x00-\x1f]")


def drive(pid: int, stop: threading.Event, **timing: Any) -> dict[str, Any]:
    """Wait for the change order, execute it, read its failures, close it."""
    return answer(pid, TITLE, STEPS, stop, on_the_way=ON_THE_WAY, **timing)


def failed_lines(driven: dict) -> list[str]:
    """The change lines left on screen with Only Show Errors ticked: the
    ones that failed. Empty when the filter did not take, because then
    every line shows and the rows cannot say which failed."""
    if not isinstance(driven, dict) or not driven.get("read_filtered"):
        return []
    rows = (" ".join(_CONTROL.sub(" ", row).split()) for row in driven.get("read") or [])
    return [row for row in rows if row and not _NOT_A_LINE.search(row)]


def _squash(text: str) -> str:
    """Letters and digits only, with the pairs OCR confuses made one."""
    text = re.sub(r"[^0-9a-z]", "", text.lower())
    return text.translate(str.maketrans("oils", "0115"))


def failure_for(footprint: str, lines: list[str], designator: str = "") -> str:
    """The failed line that names ``footprint``, matched past OCR's usual
    confusions, else a footprint line that names ``designator`` (OCR can
    cut a long name off); '' when none does."""
    want = _squash(footprint)
    if want:
        for line in lines:
            if want in _squash(line):
                return line
    if designator:
        part = re.compile(r"(?<![0-9a-z])" + re.escape(_squash(designator))
                          + r"(?![0-9])")
        for line in lines:
            if "footprint" in line.lower():
                words = [_squash(w) for w in re.split(r"[\s\[\]()]+", line)]
                if any(part.fullmatch(w) for w in words if w):
                    return line
    return ""
