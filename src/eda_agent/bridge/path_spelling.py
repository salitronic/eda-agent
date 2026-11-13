# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every document path sent to Altium in the one spelling it opens files by.

Altium keys an open document by the text of the path it was opened with,
so C:/x/y.SchDoc and C:\\x\\y.SchDoc open as two documents. CanonicalPathValue
in Main.pas turns the slashes round in a value under a key ending in "path".
It cannot see a path inside a process string such as
"ObjectKind=Document|FileName=C:/x/y.SchLib", nor the items of a list, and a
library opened that way became a second copy that read as modified and asked
to be saved on every reload. The same rule is applied here before a request
is written: an absolute drive or UNC path has its forward slashes made
backslashes.

A short 8.3 spelling of a path (a component such as LONGNA~1, as Windows'
own temporary folder is often reported) is a third spelling of the same file.
A document of a project opened under its long path and then named by its
short one loads a second time beside the project's copy, and the compiler
reports two documents of one name. So the short components of an absolute
path are expanded to their long names too. Nothing else changes.
"""
from __future__ import annotations

import ntpath
import os
import re
import sys
from typing import Any

_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})")

# A FileName field of a pipe-separated process parameter string.
_FILENAME = re.compile(r"(?i)(^|\|)(FileName=)([^|]*)")

# Keys whose value is a process parameter string.
_PROCESS_KEYS = {"parameters", "params"}


def _long_name(path: str) -> str:
    """The long spelling Windows gives an existing ``path``, else ``path``."""
    if sys.platform != "win32":
        return path
    import ctypes

    size = 1024
    for _ in range(3):
        buf = ctypes.create_unicode_buffer(size)
        got = ctypes.windll.kernel32.GetLongPathNameW(path, buf, size)
        if got == 0:
            return path
        if got < size:
            return buf.value
        size = got + 1
    return path


def long_spelling(path: str) -> str:
    """``path`` with every 8.3 short component expanded to its long name.

    Only a path holding a "~" is looked at. A path that does not
    exist yet (a file about to be created) keeps its missing tail and has
    the part that does exist expanded."""
    if "~" not in path:
        return path
    head, tail = path, ""
    while True:
        full = _long_name(head)
        if full != head:
            return full + tail
        parent, name = ntpath.split(head)
        if parent == head:
            return path
        tail = "\\" + name + tail
        head = parent


def backslashed(value: Any) -> Any:
    """``value`` with backslashes and long names when it is an absolute
    Windows path."""
    if isinstance(value, str) and _ABSOLUTE.match(value):
        return long_spelling(value.replace("/", "\\"))
    return value


def _process_string(text: str) -> str:
    return _FILENAME.sub(
        lambda m: m.group(1) + m.group(2) + backslashed(m.group(3)), text)


def _path_key(low: str) -> bool:
    """A key whose string value is a path: the keys CanonicalPathValue in
    Main.pas turns the slashes round in."""
    return (low.endswith(("path", "library", "_file"))
            or low == "destination")


def one_spelling(params: Any) -> Any:
    """A copy of ``params`` with every document path in one spelling, at any
    depth: a string under a key ending in "path", "library" or "_file" (or
    named "destination"), the strings of a list under a key ending in
    "paths", and the FileName of a process string."""
    if isinstance(params, dict):
        out: dict = {}
        for key, value in params.items():
            low = key.lower() if isinstance(key, str) else ""
            if _path_key(low) and isinstance(value, str):
                out[key] = backslashed(value)
            elif low.endswith("paths") and isinstance(value, list):
                out[key] = [backslashed(v) for v in value]
            elif low in _PROCESS_KEYS and isinstance(value, str):
                out[key] = _process_string(value)
            else:
                out[key] = one_spelling(value)
        return out
    if isinstance(params, list):
        return [one_spelling(v) for v in params]
    return params


def same_file(a: str, b: str) -> bool:
    """True when two paths name one file, even through two drive letters
    mapped to one share; by their normalised text when either is missing."""
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return (ntpath.normcase(ntpath.normpath(a))
                == ntpath.normcase(ntpath.normpath(b)))
