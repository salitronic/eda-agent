# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What each tool call costs the AI in tokens, and how long it takes.

Every call a client makes is measured as the server answers it: the
arguments the AI wrote, the reply it reads back (the structured copy
when there is one, since that is what Claude Code reads, else the text),
and each image by its size. The counts are estimates (about four
characters a token; an image about width x height / 750 after the
client's downscale to 1568 px on the long side), good for telling which
tools are heavy, not for billing.

One line per call goes to ``usage/<date>.jsonl`` in the workspace folder,
shared by every server of every session. Nothing is counted until the
user turns "Token count" on, on the Config tab of the Altium panel;
EDA_AGENT_USAGE_OFF=1 keeps it off whatever the panel says.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

OFF_ENV = "EDA_AGENT_USAGE_OFF"
CHARS_PER_TOKEN = 4.0
IMAGE_LONG_EDGE = 1568
_lock = threading.Lock()


def enabled() -> bool:
    if os.environ.get(OFF_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    from ..preferences import usage_on
    return usage_on()


def folder() -> Path:
    from ..config import get_config
    return Path(get_config().workspace_dir) / "usage"


def text_tokens(chars: int) -> int:
    return int(round(chars / CHARS_PER_TOKEN))


def image_size(data: str) -> Optional[tuple[int, int]]:
    """(width, height) of a base64 PNG or JPEG, read from its header."""
    try:
        head = base64.b64decode(data[:4096] + "=" * (-len(data[:4096]) % 4))
    except (ValueError, TypeError):
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return struct.unpack(">II", head[16:24])
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                return None
            marker, length = head[i + 1], struct.unpack(">H", head[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            i += 2 + length
    return None


def image_tokens(width: int, height: int) -> int:
    scale = min(1.0, IMAGE_LONG_EDGE / max(width, height, 1))
    return int(round(width * scale * height * scale / 750))


def _blocks(result: Any) -> tuple[list, Any]:
    if isinstance(result, tuple) and len(result) == 2:
        return list(result[0] or []), result[1]
    if isinstance(result, list):
        return result, None
    return [], None


def measure(arguments: Any, result: Any) -> dict[str, int]:
    """Estimated tokens the AI wrote (``in``) and reads back (``out``,
    with ``images`` the part of it that is pictures)."""
    try:
        wrote = len(json.dumps(arguments or {}, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        wrote = len(str(arguments))
    blocks, structured = _blocks(result)
    text, pictures = 0, 0
    for block in blocks:
        kind = getattr(block, "type", None)
        if kind == "image":
            size = image_size(getattr(block, "data", "") or "")
            pictures += image_tokens(*size) if size else 1600
        elif kind == "text" and structured is None:
            text += len(getattr(block, "text", "") or "")
    if structured is not None:
        try:
            text = len(json.dumps(structured, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            text = len(str(structured))
    return {"in": text_tokens(wrote), "out": text_tokens(text) + pictures,
            "images": pictures}


def record(tool: str, arguments: Any, result: Any, seconds: float,
           session: str = "", member: str = "", ok: bool = True) -> Optional[dict]:
    """Measure one call and append it to today's file. Never raises."""
    if not enabled():
        return None
    try:
        row = {"t": round(time.time(), 3), "tool": tool, "session": session,
               "ms": int(seconds * 1000), "ok": ok, **measure(arguments, result)}
        if member:
            row["member"] = member
        target = folder()
        target.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, separators=(",", ":")) + "\n"
        with _lock, open(target / f"{date.today().isoformat()}.jsonl", "a",
                         encoding="utf-8") as handle:
            handle.write(line)
        return row
    except Exception:  # noqa: BLE001 - a count never breaks a tool
        return None


def _rows(days: int, where: Optional[Path] = None) -> Iterable[dict]:
    base = where or folder()
    for back in range(max(days, 1)):
        day = (date.today() - timedelta(days=back)).isoformat()
        path = base / f"{day}.jsonl"
        if not path.is_file():
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def summary(days: int = 7, where: Optional[Path] = None, top: int = 0) -> dict[str, Any]:
    """Calls, tokens and time per tool, per day and per session over the
    last ``days`` days, heaviest tool first."""
    tools: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0,
                                                  "images": 0, "ms": 0, "failed": 0})
    per_day: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    per_session: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    total = {"calls": 0, "in": 0, "out": 0, "images": 0, "ms": 0}
    for row in _rows(days, where):
        name = row.get("tool", "?") + (f" > {row['member']}" if row.get("member") else "")
        t = tools[name]
        t["calls"] += 1
        t["failed"] += 0 if row.get("ok", True) else 1
        for k in ("in", "out", "images", "ms"):
            t[k] += int(row.get(k) or 0)
            total[k] += int(row.get(k) or 0)
        total["calls"] += 1
        tokens = int(row.get("in") or 0) + int(row.get("out") or 0)
        day = datetime.fromtimestamp(float(row.get("t") or 0)).date().isoformat()
        per_day[day]["calls"] += 1
        per_day[day]["tokens"] += tokens
        s = str(row.get("session") or "?")
        per_session[s]["calls"] += 1
        per_session[s]["tokens"] += tokens
    rows = []
    for name, t in tools.items():
        rows.append({"tool": name, **t, "tokens": t["in"] + t["out"],
                     "avg_tokens": round((t["in"] + t["out"]) / t["calls"]),
                     "avg_ms": round(t["ms"] / t["calls"])})
    rows.sort(key=lambda r: (-r["tokens"], r["tool"]))
    return {"days": days, "enabled": enabled(),
            "total": {**total, "tokens": total["in"] + total["out"]},
            "tools": rows[:top] if top else rows,
            "per_day": dict(sorted(per_day.items())),
            "per_session": dict(sorted(per_session.items(),
                                       key=lambda kv: -kv[1]["tokens"])),
            "estimate": "about 4 characters a token; images by size"}
