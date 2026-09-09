# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read DelphiScript source the way the guards need to read it.

Two jobs, both of which were got wrong by hand first:

* Strip comments, so a guard that searches for the defect it forbids
  does not match the comment describing that defect.
* Split into functions, so a check for "X near Y" cannot wander past
  the end of one handler into the next.

BRACES INSIDE STRING LITERALS ARE NOT COMMENTS. Generic.pas builds its
JSON with literals full of ``{`` and ``}``; a stripper that counts them
loses track of comment depth and eats most of the file, which then reads
as "no functions found" at best and a silently empty scan at worst.
"""
from __future__ import annotations

import re

_NL = chr(10)


def strip_comments(text: str) -> str:
    """Pascal source with ``{...}`` and ``//`` comments removed.

    String literals are copied through untouched, braces and all.
    """
    out: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if depth:
            if ch == "}":
                depth -= 1
            elif ch == "{":
                depth += 1
            i += 1
            continue
        if ch == "'":
            # A Pascal string. '' inside one is an escaped quote, and the
            # loop handles it naturally: the closing quote ends the
            # string and the next quote opens another.
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find(_NL, i)
            if j < 0:
                break
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def functions(text: str) -> dict[str, str]:
    """``{name: body}`` for every Function/Procedure in the source."""
    found: dict[str, str] = {}
    for part in re.split(r"(?m)^(?=(?:Function|Procedure)\s+\w+)", text):
        m = re.match(r"(?:Function|Procedure)\s+(\w+)", part)
        if m and len(part) > 40:
            found[m.group(1)] = part
    return found


def load(path, minimum: int = 40) -> dict[str, str]:
    """Comment-free functions from a .pas file, with a floor.

    The floor is the point: a stripper that eats the file, or a splitter
    whose pattern stops matching, otherwise turns every check built on
    it into a vacuous pass.
    """
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    found = functions(text)
    assert len(found) >= minimum, (
        f"{path.name}: only {len(found)} functions parsed, expected at "
        f"least {minimum}; the comment stripper or the splitter has "
        f"stopped working and every check built on this would pass "
        f"over nothing")
    return found
