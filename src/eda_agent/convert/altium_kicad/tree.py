# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad s-expressions as trees that keep a bare atom apart from a string.

``libimport.kicad.sexpr`` reads values and forgets which tokens were quoted,
which is right for reading a library and wrong for rewriting a schematic:
KiCad wants ``(hide yes)`` bare and ``(property "Value" "1k")`` quoted. A
bare atom here is a ``Sym``; a quoted one is a plain ``str``. ``dump``
writes KiCad's own tab-indented layout, and ``blocks`` finds a form's text
span for edits that must leave the rest of a file's bytes alone.
"""

from __future__ import annotations

import re
from typing import Iterator, Optional


class Sym(str):
    """A bare (unquoted) atom."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def _unescape(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse(text: str) -> list:
    """The document's top-level form as nested lists."""
    stack: list[list] = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"cannot read s-expression at {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append(_unescape(m.group(3)))
        else:
            stack[-1].append(Sym(m.group(4)))
    if len(stack) != 1 or not stack[0]:
        raise ValueError("unbalanced s-expression")
    return stack[0][0]


def _atom(value) -> str:
    if isinstance(value, Sym):
        return str(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return num(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def num(value: float) -> str:
    """A number as KiCad writes it: no exponent, no trailing zeros."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def dump(node, indent: int = 0) -> str:
    """KiCad's layout: a form of atoms on one line, nested forms indented."""
    if not isinstance(node, list):
        return _atom(node)
    if all(not isinstance(c, list) for c in node):
        return "(" + " ".join(_atom(c) for c in node) + ")"
    head = []
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        head.append(_atom(node[i]))
        i += 1
    out = "(" + " ".join(head)
    for child in node[i:]:
        out += "\n" + "\t" * (indent + 1) + dump(child, indent + 1)
    return out + "\n" + "\t" * indent + ")"


def find(node: list, tag: str) -> list:
    """The direct children of ``node`` whose head is ``tag``."""
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def first(node: list, tag: str) -> Optional[list]:
    found = find(node, tag)
    return found[0] if found else None


def prop(symbol: list, name: str) -> Optional[list]:
    """A symbol's ``(property "name" ...)``."""
    for p in find(symbol, "property"):
        if len(p) > 1 and p[1] == name:
            return p
    return None


def read(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())


def write(path: str, node: list) -> None:
    from ...atomicfile import replace_with_retry
    tmp = path + ".eda-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dump(node) + "\n")
    replace_with_retry(tmp, path)


def blocks(text: str, head: str) -> Iterator[tuple[int, int]]:
    """``(start, end)`` of every ``(head ...`` form in ``text``, brackets
    matched outside strings."""
    i = 0
    while True:
        i = text.find("(" + head, i)
        if i < 0:
            return
        after = i + 1 + len(head)
        if after < len(text) and text[after] not in " \n\t\r)":
            i += 1
            continue
        depth = 0
        j = i
        quoted = False
        while j < len(text):
            c = text[j]
            if c == "\\" and quoted:
                j += 2
                continue
            if c == '"':
                quoted = not quoted
            elif not quoted:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        yield i, j + 1
        i = j + 1


def strip_blocks(text: str, head: str, keep=lambda block: False) -> tuple[str, int]:
    """``text`` without its ``(head ...)`` forms (those ``keep`` refuses),
    each with the indentation before it; and how many went."""
    out = []
    last = 0
    gone = 0
    for a, b in list(blocks(text, head)):
        if keep(text[a:b]):
            continue
        k = a
        while k > last and text[k - 1] in " \t":
            k -= 1
        if k > last and text[k - 1] == "\n":
            k -= 1
        out.append(text[last:k])
        last = b
        gone += 1
    out.append(text[last:])
    return "".join(out), gone


def replace_in_blocks(text: str, head: str, edit) -> tuple[str, int]:
    """``text`` with each ``(head ...)`` form passed through ``edit``
    (block text in, block text out); and how many changed."""
    out = []
    last = 0
    changed = 0
    for a, b in list(blocks(text, head)):
        new = edit(text[a:b])
        if new != text[a:b]:
            changed += 1
        out.append(text[last:a])
        out.append(new)
        last = b
    out.append(text[last:])
    return "".join(out), changed
