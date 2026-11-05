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
backslashes, and nothing else changes.
"""
from __future__ import annotations

import ntpath
import os
import re
from typing import Any

_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})")

# A FileName field of a pipe-separated process parameter string.
_FILENAME = re.compile(r"(?i)(^|\|)(FileName=)([^|]*)")

# Keys whose value is a process parameter string.
_PROCESS_KEYS = {"parameters", "params"}


def backslashed(value: Any) -> Any:
    """``value`` with backslashes when it is an absolute Windows path."""
    if isinstance(value, str) and _ABSOLUTE.match(value):
        return value.replace("/", "\\")
    return value


def _process_string(text: str) -> str:
    return _FILENAME.sub(
        lambda m: m.group(1) + m.group(2) + backslashed(m.group(3)), text)


def one_spelling(params: Any) -> Any:
    """A copy of ``params`` with every document path backslashed, at any
    depth: a string under a key ending in "path", the strings of a list under
    a key ending in "paths", and the FileName of a process string."""
    if isinstance(params, dict):
        out: dict = {}
        for key, value in params.items():
            low = key.lower() if isinstance(key, str) else ""
            if low.endswith("path") and isinstance(value, str):
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
