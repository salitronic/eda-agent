# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The labelled pictures of the bridge's window on the help page.

The window is shown a section at a time, each a strip cut from a
screenshot of it, enlarged, with its controls named in labels joined to
them by leader lines. A label is (title, what it does, the control's x and
y on the strip in pixels, where the label stands: above, below, left or
right). Labels in one row keep the order of their controls and move apart
only as far as they need to clear one another.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Optional

HERE = Path(__file__).parent / "dashboard_static" / "help"

#: How much a strip is enlarged.
ZOOM = 1.6
#: Room either side of a strip, a column of side labels, and the gap
#: between a label and the strip, in pixels of the figure.
MARGIN = 90
COLUMN = 230
GAP = 26
#: A label's line height, characters on a line (above or below a strip,
#: and beside it), its width above or below, and the room between labels.
LINE = 15
CHARS_ROW = 24
CHARS_SIDE = 34
WIDTH_ROW = 160
SPACE = 10

Label = tuple  # (title, text, x, y, where)

FIGURES: dict[str, dict] = {
    "header": {
        "picture": "window-header.png",
        "alt": "The top of the window: its state, the update badge, the scripts' version and Help.",
        "labels": [
            ("Status", "Green: idle. Amber: running a command. Grey: paused.",
             32, 3, "above"),
            ("Version", "The eda-agent scripts loaded in Altium.", 292, 3, "above"),
            ("Update", "Shown when a newer eda-agent is out. Ask your AI "
             "assistant to update it; it says what is new first.", 236, 25, "below"),
            ("Help", "Opens this page.", 350, 25, "below"),
        ],
    },
    "counters": {
        "picture": "window-counters.png",
        "alt": "The four counters under the window's top.",
        "labels": [
            ("Uptime", "How long the bridge has run in this session.", 49, 10, "above"),
            ("Requests", "Commands handled, keep-alive pings included.", 140, 64,
             "below"),
            ("Busy time", "Time Altium spent on commands; the rest is yours.", 232, 10, "above"),
            ("Auto-off in", "Time before the bridge stops when no command comes. "
             "Double-click: always on.", 326, 64, "below"),
        ],
    },
    "buttons": {
        "picture": "window-buttons.png",
        "alt": "The window's six buttons.",
        "labels": [
            ("Dashboard", "Opens the web dashboard, when it runs.", 66, 4, "above"),
            ("Pause", "Holds the AI's commands until you resume.", 189, 4, "above"),
            ("Renew", "Starts the auto-off countdown again.", 312, 4, "above"),
            ("Clear log", "Empties the log. Nothing else changes.", 66, 56, "below"),
            ("Saving", "On: saves when a command asks. Off: every save "
             "refused; you save.", 189, 56, "below"),
            ("Detach", "Disconnects the AI and closes this window. Nothing is "
             "saved.", 312, 56, "below"),
        ],
    },
    "filter": {
        "picture": "window-filter.png",
        "alt": "The log's filter row.",
        "labels": [
            ("Filter", "Shows only new log lines with this text.", 92, 3, "above"),
            ("Hide pings", "Leaves out the AI client's keep-alive pings.", 207, 25,
             "below"),
            ("Slow only", "Only commands of 100 ms or more, and errors.", 277, 3,
             "above"),
            ("On top", "Keeps this window in front of Altium's.", 339, 25, "below"),
        ],
    },
    "tabs": {
        "picture": "window-tabs.png",
        "alt": "The tab bar.",
        "labels": [
            ("Tabs", "Log, Perf and Config, each shown below.", 97, 2, "above"),
            ("Order", "The log's newest line is at the top.", 290, 24, "below"),
            ("Sponsor", "Sponsor eda-agent on GitHub.", 328, 2, "above"),
            ("Copy", "Copies the log or the timings, for a bug report.", 358, 23,
             "below"),
        ],
    },
    "log": {
        "picture": "window-log.png",
        "alt": "Lines of the Log tab.",
        "labels": [
            ("Time", "How long the command took, in ms.", 32, 4, "above"),
            ("Request", "The id of the request, to find it in a report.", 92, 4,
             "above"),
            ("Command", "What the AI asked Altium to do.", 222, 4, "above"),
            ("Newest first", "Each command adds a line at the top.", 152, 40, "below"),
        ],
    },
    "perf": {
        "picture": "window-perf.png",
        "alt": "The Perf tab's table.",
        "labels": [
            ("Command", "Each command run this session.", 32, 2, "above"),
            ("N", "How many times it ran.", 214, 2, "above"),
            ("avg, max", "Its average and longest time, in ms.", 292, 2, "above"),
        ],
    },
    "config": {
        "picture": "window-config.png",
        "alt": "The top of the Config tab: the Permissions group's first settings.",
        "labels": [
            ("Group", "Settings of one kind.", 10, 14, "left"),
            ("Setting", "Hover over it for a short note.", 10, 40, "left"),
            ("What it does", "The lit choice, in a line.", 10, 64, "left"),
            ("Lit choice", "The one in force. Amber: it holds the AI back or "
             "skips a check.", 211, 29, "above"),
            ("Other choice", "Click to change; it takes effect on the next "
             "command.", 307, 29, "above"),
        ],
    },
    "footer": {
        "picture": "window-footer.png",
        "alt": "The bottom of the window.",
        "labels": [
            ("Standalone dashboard", "The command that runs the dashboard without "
             "an AI client.", 142, 2, "above"),
            ("Copy command", "Copies that command.", 358, 23, "below"),
        ],
    },
}


def _lines(text: str, chars: int) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > chars:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return out + ([cur] if cur else [])


def _spread(want: list[float], size: list[float], low: float, high: float) -> list[float]:
    """Starts for boxes of ``size`` that want to start at ``want`` (in
    order), none overlapping, each block of touching boxes placed where
    its boxes are on average nearest what they want, all within
    [low, high]."""
    blocks: list[list] = []                       # [first, last, start]
    for k in range(len(want)):
        blocks.append([k, k, want[k]])
        while len(blocks) > 1:
            a, b = blocks[-2], blocks[-1]
            if a[2] + sum(size[a[0]:a[1] + 1]) <= b[2]:
                break
            first, last = a[0], b[1]
            offsets = [sum(size[first:i]) for i in range(first, last + 1)]
            start = sum(want[i] - o for i, o in zip(range(first, last + 1), offsets))
            blocks[-2:] = [[first, last, start / (last - first + 1)]]
    out = [0.0] * len(want)
    floor = low
    for first, last, start in blocks:
        total = sum(size[first:last + 1])
        x = min(max(start, floor), high - total)
        for i in range(first, last + 1):
            out[i] = x
            x += size[i]
        floor = x
    return out


def _text(x: float, y: float, title: str, lines: list[str], anchor: str) -> list[str]:
    out = [f'<text class="title" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">'
           f'{html.escape(title)}</text>']
    for k, line in enumerate(lines):
        out.append(f'<text class="note" x="{x:.1f}" y="{y + LINE * (k + 1):.1f}" '
                   f'text-anchor="{anchor}">{html.escape(line)}</text>')
    return out


def svg(name: str) -> Optional[str]:
    """The labelled strip ``name`` as an inline SVG, its picture inlined."""
    fig = FIGURES.get(name)
    if fig is None:
        return None
    path = HERE / fig["picture"]
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    # A PNG's width and height are the first fields of its header chunk.
    w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    W, H = w * ZOOM, h * ZOOM
    labels = fig["labels"]
    sides = any(l[4] in ("left", "right") for l in labels)
    side = COLUMN + GAP if sides else MARGIN
    width = side * 2 + W
    rows = {}
    for where in ("above", "below"):
        mine = sorted((l for l in labels if l[4] == where), key=lambda l: l[2])
        rows[where] = (mine, [_lines(l[1], CHARS_ROW) for l in mine])
    above_h = max([LINE * (1 + len(x)) for x in rows["above"][1]], default=0)
    below_h = max([LINE * (1 + len(x)) for x in rows["below"][1]], default=0)
    top = above_h + GAP if above_h else 6
    # Side columns may run past the strip.
    columns = {}
    for where in ("left", "right"):
        mine = sorted((l for l in labels if l[4] == where), key=lambda l: l[3])
        lines = [_lines(l[1], CHARS_SIDE) for l in mine]
        size = [LINE * (1 + len(x)) + SPACE for x in lines]
        want = [top + l[3] * ZOOM - LINE * 0.7 for l in mine]
        columns[where] = (mine, lines, _spread(want, size, 0.0, 1e9))
    bottom = top + H + (GAP + below_h if below_h else 6)
    for mine, lines, starts in columns.values():
        if mine:
            bottom = max(bottom, starts[-1] + LINE * (1 + len(lines[-1])) + 6)
    data = base64.b64encode(raw).decode("ascii")
    parts = [f'<svg class="figure" viewBox="0 0 {width:.0f} {bottom:.0f}" '
             f'style="max-width: {width:.0f}px" role="img" '
             f'aria-label="{html.escape(fig["alt"])}">',
             f'<image x="{side:.1f}" y="{top:.1f}" width="{W:.1f}" height="{H:.1f}" '
             f'href="data:image/png;base64,{data}"/>']

    def leader(x1, y1, x2, y2):
        parts.append(f'<line class="leader" x1="{x1:.1f}" y1="{y1:.1f}" '
                     f'x2="{x2:.1f}" y2="{y2:.1f}"/>')
        parts.append(f'<circle class="dot" cx="{x2:.1f}" cy="{y2:.1f}" r="3.5"/>')

    for where, (mine, lines) in rows.items():
        if not mine:
            continue
        size = [WIDTH_ROW + SPACE] * len(mine)
        want = [side + l[2] * ZOOM - WIDTH_ROW / 2 for l in mine]
        starts = _spread(want, size, 0.0, width + SPACE)
        for lab, ls, x in zip(mine, lines, starts):
            cx = x + WIDTH_ROW / 2
            px, py = side + lab[2] * ZOOM, top + lab[3] * ZOOM
            if where == "above":
                y = top - GAP - LINE * len(ls) + 2
                leader(cx, top - GAP + 6, px, py)
            else:
                y = top + H + GAP + 10
                leader(cx, y - 13, px, py)
            parts += _text(cx, y, lab[0], ls, "middle")
    for where, (mine, lines, starts) in columns.items():
        left = where == "left"
        for lab, ls, y0 in zip(mine, lines, starts):
            x = side - GAP if left else side + W + GAP
            ly = y0 + LINE * 0.7
            leader(x + (4 if left else -4), ly - 4, side + lab[2] * ZOOM, top + lab[3] * ZOOM)
            parts += _text(x, ly, lab[0], ls, "end" if left else "start")
    parts.append("</svg>")
    return "".join(parts)


__all__ = ["FIGURES", "svg"]
