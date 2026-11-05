# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which other eda-agent servers share this workspace.

Several sessions on one machine are normal, and they share one request
directory. A server left running for weeks keeps polling it too, and
nothing used to say so: twelve were once reported alive on one machine,
the oldest three weeks old.

Each server keeps one small record in ``<workspace>/servers/`` while it
runs and reads the others' records when asked. A record whose process is
gone, or whose pid now belongs to a different process, is deleted on the
next read, so a crash leaves nothing behind for long.

ONLY EVER A WARNING. Nothing here refuses to start, and nothing here
stops another process: running several sessions on purpose is allowed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil

from .atomicfile import discard, replace_with_retry

#: Subdirectory of the workspace. The script side finds requests with a
#: non-recursive FindFiles on request_*.json, so nothing here can be
#: taken for one.
SERVERS_DIR = "servers"


def _record_path(workspace: Path, pid: int) -> Path:
    return Path(workspace) / SERVERS_DIR / f"server_{pid}.json"


def _started(create_time: Optional[float]) -> Optional[str]:
    if create_time is None:
        return None
    try:
        return datetime.fromtimestamp(create_time).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _create_time(pid: int) -> Optional[float]:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def register(workspace: Path) -> Optional[Path]:
    """Record this server in the workspace. Best effort: None on failure."""
    pid = os.getpid()
    created = _create_time(pid)
    path = _record_path(workspace, pid)
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"pid": pid, "create_time": created,
                                   "started": _started(created)}),
                       encoding="utf-8")
        replace_with_retry(tmp, path)
        return path
    except OSError:
        discard(tmp)
        return None


def unregister(workspace: Path) -> None:
    """Remove this server's record. A missing one is not an error."""
    try:
        _record_path(workspace, os.getpid()).unlink()
    except OSError:
        pass


def _alive(pid: int, created: Any) -> bool:
    """Whether ``pid`` is still the process that wrote the record.

    A pid can be reused, so the start time has to agree as well. A process
    this user may not inspect counts as alive: dropping its record on a
    guess would hide a real server.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True
    if created is None:
        return True
    try:
        return abs(proc.create_time() - float(created)) < 1.0
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError, TypeError, ValueError):
        return True


def orphaned(pid: int) -> bool:
    """Whether the session that started this server is gone.

    A server is started by its Claude session, directly or through the
    eda-agent launcher, and serves only it. With nothing above it the
    server is left over and polling for nobody. Measured 2026-10-23: all
    22 servers on this machine belonged to 11 live sessions, two each
    (the full and the minimal server), and a warning that called them
    worth closing sent sessions hunting for leftovers that did not exist.
    Anything unreadable counts as not orphaned.
    """
    try:
        parent = psutil.Process(pid).parent()
        if parent is None:
            return True
        if parent.name().lower().startswith("eda-agent"):
            return parent.parent() is None
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return False
    return False


def others(workspace: Path) -> list[dict[str, Any]]:
    """Other live servers recorded in this workspace, oldest pid first.

    Stale records are deleted on the way. A record that cannot be read is
    skipped and kept, because it may be one being written right now.
    """
    folder = Path(workspace) / SERVERS_DIR
    try:
        entries = sorted(folder.glob("server_*.json"))
    except OSError:
        return []
    me = os.getpid()
    found = []
    for entry in entries:
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = record.get("pid") if isinstance(record, dict) else None
        if not isinstance(pid, int) or pid == me:
            continue
        if _alive(pid, record.get("create_time")):
            found.append({"pid": pid, "started": record.get("started"),
                          "orphaned": orphaned(pid)})
        else:
            try:
                entry.unlink()
            except OSError:
                pass
    return sorted(found, key=lambda r: r["pid"])


def duplicate_warning(servers: list[dict[str, Any]]) -> Optional[str]:
    """The warning for ``others()``, or None when there are none."""
    left = [s for s in servers if s.get("orphaned")]
    if not left:
        return None
    listed = ", ".join(f"pid {s['pid']} (started {s.get('started') or 'unknown'})"
                       for s in left)
    return (f"{len(left)} of the {len(servers)} other eda-agent servers on "
            f"this workspace outlived the session that started them: {listed}. "
            f"They poll for nobody and can be ended. The rest belong to "
            f"running sessions, which is normal. Nothing was stopped.")
