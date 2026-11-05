# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run many items of one tool as bulk requests to Altium.

A tool called with ``items`` runs its own code once per item, all items at
once (tools/bulk.py). Each item's call to the bridge lands here instead of
going straight to Altium. When every item still running is waiting on the
bridge, the waiting requests are sent: those for the same command as ONE
bulk request, which Altium runs inside one edit (ProcessBulk in
StatusForm.pas), and each item gets its own reply back. A tool that sends a
read and then a write makes two bulk requests for any number of items.

Nothing here changes a single call: with no collector active, the bridge
sends as it always has.
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any, Optional

#: The collector of the bulk call this task belongs to, if any.
_COLLECTOR: contextvars.ContextVar = contextvars.ContextVar(
    "eda_agent_bulk_collector", default=None)
#: This task's item number, so replies keep the items' order.
_ITEM: contextvars.ContextVar = contextvars.ContextVar(
    "eda_agent_bulk_item", default=0)

#: Most items in one request. The script engine serves one request at a
#: time, so a very long one holds every other session up.
MAX_ITEMS_PER_REQUEST = 200


@dataclass
class _Pending:
    item: int
    command: str
    params: dict
    timeout: Optional[float]
    future: asyncio.Future


@dataclass
class Collector:
    """Holds the items' bridge requests until all of them are waiting."""

    bridge: Any
    active: int
    keep_going: bool = False
    pending: list = field(default_factory=list)
    requests_sent: int = 0
    _flushing: bool = False

    def current_item(self) -> int:
        return _ITEM.get()

    async def submit(self, command: str, params: dict,
                     timeout: Optional[float]) -> Any:
        future = asyncio.get_running_loop().create_future()
        self.pending.append(_Pending(_ITEM.get(), command, params, timeout,
                                     future))
        self._maybe_flush()
        return await future

    def item_done(self) -> None:
        self.active -= 1
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        if self.pending and not self._flushing \
                and len(self.pending) >= self.active:
            self._flushing = True
            asyncio.get_running_loop().create_task(self._flush())

    async def _flush(self) -> None:
        batch, self.pending = sorted(self.pending, key=lambda p: p.item), []
        try:
            groups: dict = {}
            for entry in batch:
                groups.setdefault(entry.command, []).append(entry)
            for command, entries in groups.items():
                for start in range(0, len(entries), MAX_ITEMS_PER_REQUEST):
                    await self._send(command,
                                     entries[start:start + MAX_ITEMS_PER_REQUEST])
        finally:
            self._flushing = False
            self._maybe_flush()

    async def _send(self, command: str, entries: list) -> None:
        self.requests_sent += 1
        timeout = max((e.timeout or 0) for e in entries) or None
        if len(entries) == 1:
            entry = entries[0]
            try:
                result = await self.bridge.send_command_direct(
                    command, entry.params, timeout)
            except Exception as exc:              # noqa: BLE001 - per item
                _settle(entry.future, exc=exc)
            else:
                _settle(entry.future, result=result)
            return
        try:
            outcomes = await self.bridge.send_bulk_async(
                command, [e.params for e in entries], timeout,
                keep_going=self.keep_going)
        except Exception as exc:                  # noqa: BLE001 - whole request
            for entry in entries:
                _settle(entry.future, exc=exc)
            return
        for entry, (ok, value) in zip(entries, outcomes):
            if ok:
                _settle(entry.future, result=value)
            else:
                _settle(entry.future, exc=value)


def _settle(future: asyncio.Future, result: Any = None,
            exc: Optional[BaseException] = None) -> None:
    if future.done():
        return
    if exc is not None:
        future.set_exception(exc)
    else:
        future.set_result(result)


def current() -> Optional[Collector]:
    """The collector of the bulk call running in this task, or None."""
    return _COLLECTOR.get()


async def run_items(bridge: Any, calls: list,
                    keep_going: bool = False) -> tuple:
    """Run ``calls`` (no-argument coroutine factories), one per item, with
    their bridge requests collected. Returns (each item's result or the
    exception it raised, in order; the number of requests sent)."""
    collector = Collector(bridge=bridge, active=len(calls),
                          keep_going=keep_going)

    async def one(index: int, call):
        _COLLECTOR.set(collector)
        _ITEM.set(index)
        try:
            return await call()
        finally:
            collector.item_done()

    results = await asyncio.gather(
        *(asyncio.create_task(one(i, c)) for i, c in enumerate(calls)),
        return_exceptions=True)
    return list(results), collector.requests_sent
