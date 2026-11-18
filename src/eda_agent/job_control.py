# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Cancel, time limit and progress for the job running on this thread.

A job runs on a worker thread that nothing outside can stop, so the engine
asks: ``checkpoint()`` at the places it loops leaves the job when it has
been cancelled, and ``over_time()`` tells it the time limit has passed, so
it can stop where it is and keep what it has. ``note()`` records the
engine's latest line for ``design_job_status``.

Outside a job (a test, the benchmark) every call here does nothing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


class Cancelled(BaseException):
    """Leaves a cancelled job. A BaseException, so the ``except Exception``
    an engine keeps around a step it may skip does not swallow it."""


@dataclass
class Control:
    #: Seconds the job may run before ``over_time`` says so; 0: no limit.
    time_limit_s: float = 0.0
    started: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    progress: str = ""
    progress_at: float = 0.0
    #: Set once ``over_time`` has answered True.
    timed_out: bool = False

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def hard_limit(self) -> float:
        """Past this, a job stopping at its limit is left as cancelled:
        what it does after the limit (the clean-up, the review) may take
        half as long again, no more."""
        return self.time_limit_s * 1.5 if self.time_limit_s else 0.0


_local = threading.local()


def bind(control: Optional[Control]) -> None:
    _local.control = control


def current() -> Optional[Control]:
    return getattr(_local, "control", None)


def checkpoint() -> None:
    """Leave the job (raise Cancelled) once it is cancelled, or once it has
    run half as long again as its time limit."""
    c = current()
    if c is None:
        return
    if c.cancel.is_set():
        raise Cancelled("cancelled")
    hard = c.hard_limit()
    if hard and c.elapsed() > hard:
        c.timed_out = True
        raise Cancelled(f"stopped at {hard:.0f} s, half as long again as its "
                        f"{c.time_limit_s:.0f} s time limit")


def over_time() -> bool:
    """True once the job has run past its time limit."""
    c = current()
    if c is None or not c.time_limit_s:
        return False
    if c.elapsed() > c.time_limit_s:
        c.timed_out = True
        return True
    return False


def note(text: str) -> None:
    """The engine's latest progress line, and a checkpoint."""
    c = current()
    if c is None:
        return
    c.progress = text
    c.progress_at = time.monotonic()
    checkpoint()


class OutOfTime(Exception):
    """A step run ``within`` its own seconds ran past them."""


def within(seconds: float, fn, *args, **kwargs):
    """``fn(*args, **kwargs)`` under its own time limit: past ``seconds``
    the engine stops where it is (over_time), and at half as long again it
    is left, raising OutOfTime. Cancelling the job still leaves the job.
    Inside a job or out of one."""
    parent = current()
    child = Control(time_limit_s=max(float(seconds), 0.001))
    if parent is not None:
        child.cancel = parent.cancel
    bind(child)
    try:
        return fn(*args, **kwargs)
    except Cancelled:
        if child.cancel.is_set():
            raise
        raise OutOfTime(f"ran past its {seconds:.0f} s") from None
    finally:
        bind(parent)
        if parent is not None and child.progress:
            parent.progress, parent.progress_at = child.progress, child.progress_at
