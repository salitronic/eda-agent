# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The client's requests read from a private copy of standard input.

On Windows a read waiting on the stdin pipe holds up every other call on
that pipe, and loading a compiled module (numpy's, scipy's) queries the
process's standard handles. Read on the shared handle, the first tool to
load one waited for the client's next message. Here the requests come
from a duplicate of the pipe, and the process's standard input is NUL.
"""

from __future__ import annotations

import os
import sys
from typing import BinaryIO


def private_stdin() -> BinaryIO:
    """The client's requests, as a binary file; on Windows a duplicate of
    the stdin pipe, with the process's own standard input pointed at NUL.
    Elsewhere sys.stdin's buffer, as it is."""
    if os.name != "nt":
        return sys.stdin.buffer
    import ctypes
    import msvcrt

    fd = os.dup(sys.stdin.fileno())
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)
    os.close(nul)
    ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))  # STD_INPUT_HANDLE
    sys.stdin = open(os.devnull, encoding="utf-8")
    return os.fdopen(fd, "rb")
