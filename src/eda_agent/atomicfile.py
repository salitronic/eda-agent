# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Finish an atomic write when Windows will not let go of the target.

Every durable write in this codebase stages to a temp file and renames
over the destination, so a reader never sees a half-written file. On
Windows that last step is the fragile one: a scanner (Defender, a sync
client, an indexer) can hold the freshly-written TARGET open for a few
milliseconds, and ``os.replace`` then fails with ``PermissionError``
(WinError 5, or WinError 32 for a sharing violation, which Python maps
to the same class).

MEASURED, not theorised: two failures in eight tight renames on this
machine. That rate is survivable for a write that happens once, and
fatal for one in a loop. The symbol cache rewrites its library file once
per symbol, so a 38-symbol extraction was near-certain to hit it, and
the exception killed the whole schematic run that needed it.

WHAT THIS DOES NOT DECIDE. The helper retries and then raises. Whether a
failed write should be swallowed is the CALLER's judgement and it is not
the same everywhere:

* A cache entry (symbols, HTTP responses) is an optimisation. Skip it.
* A heartbeat or a fault record is best-effort. Skip it.
* A bridge REQUEST file is the command itself. Raising is right, because
  skipping it silently leaves the caller waiting out a full timeout with
  nothing to explain the silence.
* A checkpoint blob or manifest is a backup. Raising is right, because a
  checkpoint that cannot restore is worse than one that never existed.

So callers that want to degrade keep their own ``except`` around this,
and callers that want to fail loudly simply do not.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("eda_agent.atomicfile")

#: Sleep before each retry, in seconds. Rising, and starting well above
#: the few-millisecond window that was measured, so the common case costs
#: one short sleep. The total is a little over a second, which is the
#: point: long enough to outlast a scanner, short enough that a caller
#: stuck behind a genuinely locked file finds out quickly.
_BACKOFF = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)

_PathLike = Union[str, "os.PathLike[str]"]


def replace_with_retry(tmp: _PathLike, target: _PathLike) -> None:
    """``os.replace(tmp, target)``, retried while Windows holds the target.

    Raises the last ``PermissionError`` if every attempt loses the race,
    so the caller decides what that means.
    """
    last: PermissionError
    for delay in _BACKOFF:
        try:
            os.replace(tmp, target)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(delay)
    # One final attempt after the longest sleep, so the last entry in
    # _BACKOFF is a wait rather than a wasted one.
    try:
        os.replace(tmp, target)
        return
    except PermissionError as exc:
        last = exc
    logger.warning(
        "could not rename %s onto %s after %d attempts: %s",
        tmp, target, len(_BACKOFF) + 1, last,
    )
    raise last


def discard(tmp: _PathLike) -> None:
    """Remove a staged temp file, ignoring any failure.

    For the caller that decided to give up: without this the abandoned
    ``.tmp`` stays next to the real file, where the next run's glob or a
    directory listing can pick it up.
    """
    try:
        Path(tmp).unlink()
    except OSError:
        pass


def take_file_text(path: _PathLike) -> Optional[str]:
    """Read and remove a file that several processes watch, in one claim.

    The file is first renamed to a name only this process uses. A rename
    of a file that has already gone fails, so when several processes see
    the same file at once exactly one of them gets its text and the rest
    get None. Reading first and deleting after let every watcher read it
    in the gap. Returns None when the file is missing or already taken.

    A scanner or sync client can hold the freshly written file for a
    moment, which refuses the rename (or the read) with PermissionError
    although nobody has taken it, so both are retried for up to a second.
    """
    path = Path(path)
    mine = path.with_name(f"{path.name}.{os.getpid()}.taken")
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.rename(path, mine)
            break
        except FileNotFoundError:
            return None
        except OSError:
            if time.monotonic() > deadline or not path.exists():
                return None
            time.sleep(0.02)
    try:
        while True:
            try:
                return mine.read_text(encoding="utf-8", errors="replace")
            except OSError:
                if time.monotonic() > deadline + 1.0:
                    return None
                time.sleep(0.02)
    finally:
        discard(mine)
