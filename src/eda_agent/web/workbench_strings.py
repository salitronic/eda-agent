# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every piece of text the workbench page shows, read from the page itself:
the T("...") calls in its script and the fixed text and title, placeholder
and aria-label attributes in its HTML. The language tables must cover
them all (tests/test_workbench_language.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent / "dashboard_static" / "workbench.html"

#: Text in the page that is not words to translate: keys, units, symbols.
NOT_WORDS = {"+", "?", "Fit", "mil", "mm", "Ctrl K", "Ctrl Enter", "Shift N", "Esc", "Space",
             "Enter", "1", "7", "/", "N", "S", "F", "L", "−", "&minus;"}


def strings(page: str | None = None) -> list[str]:
    text = page if page is not None else PAGE.read_text(encoding="utf-8")
    out: set[str] = set()
    script = text[text.index("<script>"):]
    for m in re.finditer(r'\bT\("((?:[^"\\]|\\.)*)"', script):
        out.add(json.loads(f'"{m.group(1)}"'))
    # The T calls built from a table: {place: "Place", ...}[x].
    for m in re.finditer(r'T\(\{([^{}]*)\}\[', script):
        for v in re.findall(r':\s*"((?:[^"\\]|\\.)*)"', m.group(1)):
            out.add(json.loads(f'"{v}"'))
    # Labels translated where they are used: T(label) over a list of them.
    for m in re.finditer(r'\[\s*"[a-z_]+",\s*\{[^{}]*\},\s*\d,\s*"((?:[^"\\]|\\.)*)"\]', script):
        out.add(json.loads(f'"{m.group(1)}"'))
    for m in re.finditer(r'T\((?:s === "error" \? |cur\.side \? |p\.side === "bottom" \? )?"([^"]+)"', script):
        out.add(m.group(1))
    for m in re.finditer(r'T\([a-z.]+ \? "([^"]+)" : "([^"]+)"\)', script):
        out.update(m.groups())
    html = text[text.index("<body>"):text.index("<script>")]
    for m in re.finditer(r">([^<>]+)<", html):
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        if t and re.search(r"[A-Za-z]{2}", t):
            out.add(t)
    for m in re.finditer(r'(?:title|placeholder|aria-label)="([^"]+)"', html):
        out.add(m.group(1))
    return sorted(s for s in out if s not in NOT_WORDS and re.search(r"[A-Za-z]", s))


if __name__ == "__main__":
    for s in strings():
        print(s)
