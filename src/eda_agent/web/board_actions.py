# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What the dashboard's Board page does to the selection: the tools it
calls, with arguments built from what is selected in Altium and the board
the cockpit holds. No model is involved.

One action runs at a time, on its own thread, and only on the board the
page shows while that board is in front; its result is kept for the page.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("eda_agent.web.board_actions")

#: Actions that change copper: the cockpit reads the whole board after them
#: (its copper stamp is only counted on a large board).
_COPPER = {"route", "unroute", "tidy", "teardrops", "repour"}

#: What each action needs selected: "parts", "parts2" (two or more),
#: "parts3" (three or more), "nets" or nothing.
NEEDS = {
    "align": "parts2", "distribute": "parts3", "rotate": "parts", "flip": "parts",
    "snap": "parts", "route": "nets", "unroute": "nets", "tidy": "nets",
    "teardrops": "", "repour": "", "drc": "",
}


def plan(action: str, args: dict, selected: dict, view: Optional[dict],
         board_file: str) -> list[tuple[str, dict]]:
    """The tool calls one action makes, as (tool name, arguments).
    ValueError when the selection does not fit the action."""
    parts = list(selected.get("parts") or [])
    nets = list(selected.get("nets") or [])
    need = NEEDS.get(action)
    if need is None:
        raise ValueError(f"no action {action!r}")
    least = {"parts": 1, "parts2": 2, "parts3": 3}.get(need, 0)
    if need.startswith("parts") and len(parts) < least:
        raise ValueError(f"{action} needs {least} or more parts selected in Altium")
    if need == "nets" and not nets:
        raise ValueError(f"{action} needs a track, pad or via of a net selected in Altium")
    where = {c["ref"]: c for c in (view or {}).get("components") or []}

    if action == "align":
        mode = str(args.get("mode") or "left")
        if mode not in ("left", "right", "top", "bottom", "center_x", "center_y"):
            raise ValueError("align mode is left, right, top, bottom, center_x or center_y")
        return [("pcb_align_components", {"designators": ",".join(parts), "alignment": mode})]
    if action == "distribute":
        axis = "y" if args.get("axis") == "y" else "x"
        known = [p for p in parts if p in where]
        if len(known) < 3:
            raise ValueError("the selected parts are not on the board shown")
        key = "x" if axis == "x" else "y"
        known.sort(key=lambda p: where[p][key])
        return [("pcb_distribute_components", {
            "designators": ",".join(known), "axis": axis,
            "start": where[known[0]][key], "end": where[known[-1]][key]})]
    if action == "rotate":
        turn = float(args.get("degrees") or 90)
        moves = [{"designator": p, "rotation": (float(where[p]["rot"]) + turn) % 360}
                 for p in parts if p in where]
        if not moves:
            raise ValueError("the selected parts are not on the board shown")
        return [("pcb_move_components", {"moves": moves})]
    if action == "flip":
        return [("pcb_flip_component", {"designator": p}) for p in parts]
    if action == "snap":
        grid = float(args.get("grid") or 25)
        return [("pcb_snap_to_grid", {"designator": p, "grid_size": grid}) for p in parts]
    if action == "route":
        return [("pcb_autoroute", {"expect_file": board_file, "nets": nets})]
    if action == "unroute":
        return [("pcb_unroute", {"expect_file": board_file, "nets": nets})]
    if action == "tidy":
        return [("pcb_tidy_tracks", {"board_path": board_file, "nets": nets})]
    if action == "teardrops":
        return [("pcb_add_teardrops", {})]
    if action == "repour":
        return [("pcb_repour_polygons", {"board_path": board_file})]
    if action == "drc":
        return [("pcb_run_drc", {})]
    raise ValueError(f"no action {action!r}")


def _tool(name: str) -> Callable:
    from ..server import mcp
    reg = getattr(getattr(mcp, "_tool_manager", None), "_tools", None) or {}
    tool = reg.get(name)
    if tool is None or getattr(tool, "fn", None) is None:
        raise RuntimeError(f"tool {name} is not registered")
    return tool.fn


def _failed(reply: Any) -> str:
    if not isinstance(reply, dict):
        return ""
    if reply.get("success") is False or reply.get("ok") is False:
        return str(reply.get("reason") or reply.get("error") or "refused")
    if reply.get("error") and not isinstance(reply.get("error"), bool):
        return str(reply["error"])
    return ""


class Runner:
    """Runs one action at a time and keeps what happened."""

    def __init__(self, call: Optional[Callable[[str, dict], Any]] = None) -> None:
        self._call = call
        self._lock = threading.Lock()
        self.current: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []

    def busy(self) -> bool:
        return self.current.get("state") == "running"

    def call(self, name: str, args: dict) -> Any:
        if self._call is not None:
            return self._call(name, args)
        from ..bridge.altium_bridge import following_front
        fn = _tool(name)
        with following_front(background=False):
            return asyncio.run(fn(**args))

    def command(self, command: str, params: dict) -> Any:
        """One bridge command, on the document in front."""
        if self._call is not None:
            return self._call("cmd:" + command, params)
        from ..bridge.altium_bridge import following_front, get_bridge
        with following_front(background=False):
            return get_bridge().send_command(command, params, timeout=30.0)

    def start(self, action: str, calls: Any,
              done: Optional[Callable[[str], None]] = None,
              board: str = "") -> dict[str, Any]:
        """Run ``calls`` (a list, or a function of the board's selection
        that returns one). With ``board`` the action is for that file:
        it is brought to the front first and the document that was in
        front is put back after, whatever the person is looking at."""
        with self._lock:
            if self.busy():
                return {"ok": False, "reason": f"{self.current.get('action')} is still running"}
            self.current = {"action": action, "state": "running", "started": time.time(),
                            "board": board, "results": []}
            record = self.current
        threading.Thread(target=self._run, args=(record, calls, done, board),
                         name="board-action", daemon=True).start()
        return {"ok": True, "started": action}

    def _run(self, record: dict, calls: Any,
             done: Optional[Callable[[str], None]], board: str = "") -> None:
        # One turn in Altium for the whole action, so another session's
        # command cannot move the focus between its steps.
        turn = contextlib.nullcontext()
        if self._call is None:
            from ..bridge.altium_bridge import tool_call
            turn = tool_call()
        with turn:
            prev, problems = "", []
            try:
                if board:
                    front = self.command("application.get_active_document", {"front": "true"})
                    prev = front.get("file_path", "") if isinstance(front, dict) else ""
                    why = _failed(self.call("pcb_focus_board", {"board_path": board}))
                    if why:
                        raise RuntimeError(f"{board} could not be brought to the front: {why}")
                if callable(calls):
                    selected = {}
                    if board:
                        state = self.command("pcb.cockpit_state", {"copper": "false"})
                        selected = (state or {}).get("selected") or {}
                    calls = calls(selected)
            except (ValueError, RuntimeError) as exc:
                problems.append(str(exc))
                calls = []
            try:
                self._run_calls(record, calls, problems)
            finally:
                if board and prev and not _same(prev, board):
                    try:
                        self.command("application.set_active_document", {"file_path": prev})
                    except Exception:                     # noqa: BLE001
                        logger.debug("could not give the front back", exc_info=True)
        if done is not None:
            try:
                done(record["action"])
            except Exception:                             # noqa: BLE001
                logger.debug("after-action hook failed", exc_info=True)

    def _run_calls(self, record: dict, calls: list[tuple[str, dict]],
                   problems: Optional[list] = None) -> None:
        problems = problems if problems is not None else []
        for name, args in calls:
            try:
                reply = self.call(name, args)
            except Exception as exc:                      # noqa: BLE001
                reply = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            why = _failed(reply)
            if why:
                problems.append(f"{name}: {why}")
            record["results"].append({"tool": name, "reply": reply})
        record["state"] = "failed" if problems else "done"
        record["problems"] = problems
        record["seconds"] = round(time.time() - record["started"], 1)
        self.history = ([dict(record)] + self.history)[:20]

    def snapshot(self) -> dict[str, Any]:
        """The current or last action, its calls summarised for the page."""
        cur = dict(self.current)
        cur["results"] = [_result_line(r) for r in cur.get("results") or []]
        return cur


def _result_line(r: dict[str, Any]) -> dict[str, Any]:
    """One call's outcome for the page; a DRC keeps its verdict, so the page
    never shows a pass without the rules it was under."""
    out = {"tool": r["tool"], "problem": _failed(r["reply"])}
    reply = r.get("reply")
    if isinstance(reply, dict) and reply.get("verdict"):
        out["verdict"] = reply["verdict"]
        out["verdict_reason"] = reply.get("verdict_reason", "")
        out["violation_count"] = reply.get("violation_count")
    return out


_RUNNER: Optional[Runner] = None


def _same(a: str, b: str) -> bool:
    return str(a).replace("/", "\\").lower() == str(b).replace("/", "\\").lower()


def get_runner() -> Runner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = Runner()
    return _RUNNER


def after(action: str) -> Optional[str]:
    """What the cockpit should do after an action: "refresh" for copper."""
    return "refresh" if action in _COPPER else None
