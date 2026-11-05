# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Edit KiCad s-expression files in place, by the text span of each form.

KiCad's schematic, symbol-library, footprint and rule files are
s-expressions. Rewriting a whole file from a parsed tree would reflow every
line of it, so edits here work on spans instead: find a form's start and
end in the text, then cut, replace or insert text there. Everything not
touched keeps its bytes. Values are read with the parser in
``libimport.kicad.sexpr``.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Iterator, Optional

from ..atomicfile import replace_with_retry
from ..libimport.kicad.sexpr import loads

Span = tuple[int, int]


def _skip_string(text: str, i: int) -> int:
    """Index just past the string starting at ``text[i] == '"'``."""
    i += 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated string")


def form_end(text: str, start: int) -> int:
    """Index just past the form that opens at ``text[start] == '('``."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced parentheses")


def forms(text: str, start: int = 0, end: Optional[int] = None,
          comments: bool = False) -> list[Span]:
    """Spans of the forms at the top of ``text[start:end]``.

    ``comments`` skips ``#`` to the end of the line between forms, as the
    rule files allow."""
    end = len(text) if end is None else end
    out = []
    i = start
    while i < end:
        c = text[i]
        if c == "(":
            e = form_end(text, i)
            out.append((i, e))
            i = e
        elif c == '"':
            i = _skip_string(text, i)
        elif comments and c == "#":
            nl = text.find("\n", i)
            i = end if nl < 0 else nl + 1
        else:
            i += 1
    return out


def children(text: str, span: Span) -> list[Span]:
    """Spans of the lists directly inside the form at ``span``."""
    return forms(text, span[0] + 1, span[1] - 1)


def head(text: str, span: Span) -> str:
    """A form's first atom (its tag)."""
    i = span[0] + 1
    while text[i] in " \t\r\n":
        i += 1
    j = i
    while j < span[1] and text[j] not in " \t\r\n()":
        j += 1
    return text[i:j]


def parse(text: str, span: Span) -> list:
    return loads(text[span[0]:span[1]])


def find_children(text: str, span: Span, tag: str) -> list[Span]:
    return [c for c in children(text, span) if head(text, c) == tag]


def line_start(text: str, i: int) -> int:
    """Start of the line holding ``i``, so a cut takes its indentation."""
    j = text.rfind("\n", 0, i)
    return j + 1


def cut(text: str, span: Span) -> str:
    """The text without the form at ``span`` and its line."""
    s = line_start(text, span[0])
    if text[s:span[0]].strip():
        s = span[0]
    e = span[1]
    if text.startswith("\r\n", e):
        e += 2
    elif text.startswith("\n", e):
        e += 1
    return text[:s] + text[e:]


def insert_before_close(text: str, span: Span, block: str, indent: str = "\t") -> str:
    """``block`` added as the last child of the form at ``span``."""
    close = span[1] - 1
    s = line_start(text, close)
    body = "".join(indent + line + "\n" for line in block.rstrip("\n").split("\n"))
    if text[s:close].strip():
        return text[:close] + "\n" + body + text[close:]
    return text[:s] + body + text[s:]


def new_uuid() -> str:
    return str(_uuid.uuid4())


def q(value: Any) -> str:
    """A quoted s-expression string."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_file(path: str, text: str) -> None:
    """Write through a temporary file, checking the result still parses."""
    loads(text if not text.lstrip().startswith("(version") else "(" + text + ")")
    tmp = path + ".eda_agent_tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    replace_with_retry(tmp, path)


def walk(node: Any, tag: str) -> Iterator[list]:
    """Every list under ``node`` (itself included) whose head is ``tag``."""
    if isinstance(node, list):
        if node and node[0] == tag:
            yield node
        for child in node:
            yield from walk(child, tag)


def first(node: list, tag: str) -> Optional[list]:
    for c in node:
        if isinstance(c, list) and c and c[0] == tag:
            return c
    return None


def all_of(node: list, tag: str) -> list:
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]
