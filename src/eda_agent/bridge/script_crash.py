# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Recover from a handler that crashed the bridge script, without a person.

A DelphiScript runtime error in a handler puts up an "Error" box with only
OK. Pressing OK does not end the script: Altium opens the unit at the
failing line and holds the script there, so the loop answers nothing, and
a new loop refuses to start with "Another script executing now". Until
now a person had to press OK, press Ctrl+F3 and start StartMCPServer again.

MEASURED 2026-10-30 (PCB_PlaceArc, a stray comma in its reply): the box
was captioned "Error" with only OK and no readable message, and after OK
the main window's title named the script project, "Altium_API.PrjScr -
Altium Designer", in place of the focused design document.

This does what the person did: a screenshot of the box, OK, a screenshot
of the halted line, then ``eda-agent relaunch --restart``, which stops the
halted script (EditScript:Stop) and starts the loop again while other
sessions' calls wait. The screenshots and a record of the crash go to
``logs/crashes`` in the workspace.

A halt is concluded only when BOTH hold after OK: no reply came, and the
title names the script project. An ordinary Altium error box that the
handler survives is answered with OK and the call goes on waiting.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from ..server_log import LOG_DIR

#: The bridge's script project. Its name in the title bar means Altium
#: has opened one of its units at a failing line.
SCRIPT_PROJECT_STEM = "Altium_API"

#: Where crash screenshots and records go, under the workspace.
CRASH_DIR = "crashes"

#: How long a reply may still come after OK before the script is taken
#: as halted. A handler that survived an error box answers within this.
REPLY_AFTER_OK_S = 5.0

#: Ceiling on the restart itself; it measured about 20 s.
RESTART_TIMEOUT_S = 240.0


def is_script_error_dialog(dialog: dict) -> bool:
    """An error box offering nothing but OK, the shape a script error has."""
    captions = {(b.get("caption") or "").replace("&", "").strip().lower()
                for b in dialog.get("buttons") or []}
    return dialog.get("kind") == "error" and captions == {"ok"}


def script_error_dialog(probe: Optional[dict]) -> Optional[dict]:
    """The script error box in a dialog report, or None."""
    if not probe or not probe.get("blocked"):
        return None
    for dialog in probe.get("dialogs") or []:
        if is_script_error_dialog(dialog):
            return dialog
    return None


def title_shows_halt(title: str) -> bool:
    """Whether a main-window title names the bridge's script project."""
    return SCRIPT_PROJECT_STEM.lower() in (title or "").lower()


def crash_folder(workspace_dir) -> Path:
    folder = Path(workspace_dir) / LOG_DIR / CRASH_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _capture(hwnd: int, path: Path) -> str:
    """The PNG's path, or "" when the capture failed. Never raises."""
    try:
        from ..ui import ocr

        if ocr.available() and ocr.capture_png(hwnd, str(path)):
            return str(path)
    except Exception:                                  # noqa: BLE001
        pass
    return ""


def _press_ok(hwnd: int) -> str:
    """Press OK on the box. "" when it closed after OK, else why not.

    A box already gone is NOT "" (2026-10-30): it was recorded as pressed
    while the box, or the one in front of it, stayed on screen until a
    person closed it.
    """
    try:
        from ..ui import windows

        window = windows.capture(hwnd)
        if window is None:
            return "the box was gone before OK could be pressed"
        button = window.find_button("OK")
        if button is None:
            return "the box has no OK button"
        windows.click(button)
        if windows.wait_for_close(hwnd, 3.0):
            return ""
        return "the box stayed open after OK"
    except Exception as exc:                           # noqa: BLE001
        return f"pressing OK failed: {exc}"


def clear_error_boxes(pid: int, folder: Optional[Path] = None,
                      base: str = "") -> dict:
    """Press OK on every script error box Altium has up, and say what else
    blocks it.

    MEASURED 2026-10-30: a recovery that had pressed OK on the crash box
    then sat 80 s in its restart behind a box on screen, until a person
    closed it. A restart cannot stop or start the script past a modal, so
    it clears them as they come rather than waiting out its timeouts.

    Returns ``cleared`` (one entry per box: title, what it said, the
    screenshot and how the press went) and ``blocking`` (the summary of
    what still blocks Altium, "" when nothing does).
    """
    out: dict = {"cleared": [], "blocking": ""}
    try:
        from ..ui import dialog_report

        report = dialog_report.report(pid)
    except Exception:                                  # noqa: BLE001
        return out
    for dialog in report.get("dialogs") or []:
        if not is_script_error_dialog(dialog):
            continue
        hwnd = int(dialog.get("hwnd") or 0)
        shot = ""
        if folder is not None:
            shot = _capture(hwnd, folder / f"{base}_box{len(out['cleared']) + 1}.png")
        out["cleared"].append({
            "title": dialog.get("title"),
            "said": dialog.get("message") or dialog.get("ocr_text") or [],
            "screenshot": shot,
            "pressed": _press_ok(hwnd) or "pressed",
        })
    try:
        after = dialog_report.report(pid)
        if after.get("blocked"):
            out["blocking"] = after.get("summary") or "a dialog is open"
    except Exception:                                  # noqa: BLE001
        pass
    return out


def _main_frame(pid: int):
    try:
        from ..ui import menu

        return menu.frame(pid)
    except Exception:                                  # noqa: BLE001
        return None


def restart_loop() -> dict:
    """Stop the halted script and start the loop: ``relaunch --restart``.

    Run as its own process, so the stop file, the maintenance marker that
    keeps other sessions' calls waiting and the wait for the new loop all
    work exactly as they do from a terminal.
    """
    command = [sys.executable, "-m", "eda_agent.server", "relaunch",
               "--restart"]
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=RESTART_TIMEOUT_S,
                              creationflags=getattr(subprocess,
                                                    "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"restarted": False,
                "output": f"the restart took over {RESTART_TIMEOUT_S:.0f}s"}
    except OSError as exc:
        return {"restarted": False, "output": f"could not run it: {exc}"}
    # The command prints its outcome; its logging goes to stderr, which is
    # only worth reading when it failed.
    text = done.stdout if done.returncode == 0 else done.stdout + "\n" + done.stderr
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and " INFO " not in line and "altium_bridge.py" not in line]
    return {"restarted": done.returncode == 0, "output": " ".join(lines[-4:])}


def recover(workspace_dir, pid: int, command: str, request_id: str,
            dialog: Optional[dict], reply_came: Callable[[float], bool],
            force: bool = False,
            on_restarted: Optional[Callable[[], None]] = None) -> dict:
    """Clear a crashed handler and restart the loop.

    With ``on_restarted`` the restart runs in the background and this
    returns as soon as the halt is confirmed; ``on_restarted`` is called
    when the restart is over, however it went. Without it, it returns
    after the restart.

    ``dialog`` is the error box from the dialog report, or None when the
    script halted without one. ``reply_came(seconds)`` waits that long
    for the handler's reply and says whether it came. ``force`` restarts
    even when the title does not show the halt (app_recover_script, asked
    for by someone who has seen it).

    Returns ``halted`` (False: no crash, the call should keep waiting),
    ``screenshots``, ``dialog``, ``title``, ``restart`` and ``record`` (the
    JSON file written for a crash).
    """
    folder = crash_folder(workspace_dir)
    base = f"{time.strftime('%Y%m%d-%H%M%S')}_{command}_{request_id[:8]}"
    shots: list = []
    pressed = ""
    if dialog is not None:
        shot = _capture(int(dialog.get("hwnd") or 0),
                        folder / f"{base}_dialog.png")
        if shot:
            shots.append(shot)
        pressed = _press_ok(int(dialog.get("hwnd") or 0))
        # A box that stayed, or another behind it, is pressed too.
        again = clear_error_boxes(pid, folder, base)
        if reply_came(REPLY_AFTER_OK_S):
            return {"halted": False, "screenshots": shots, "dialog": dialog,
                    "pressed_ok": pressed or "pressed",
                    "more_boxes": again["cleared"]}
    frame = _main_frame(pid)
    title = frame.title if frame is not None else ""
    if not force and not title_shows_halt(title):
        return {"halted": False, "screenshots": shots, "dialog": dialog,
                "title": title}
    if frame is not None:
        # A moment for the editor to scroll to the failing line.
        time.sleep(1.0)
        shot = _capture(frame.hwnd, folder / f"{base}_altium.png")
        if shot:
            shots.append(shot)
    record = folder / f"{base}.json"
    outcome = {
        "halted": True,
        "command": command,
        "request_id": request_id,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dialog": dialog,
        "pressed_ok": pressed or ("pressed" if dialog else "no box"),
        "more_boxes": again["cleared"] if dialog is not None else [],
        "title": title,
        "screenshots": shots,
        "record": str(record),
    }

    def run_restart() -> dict:
        started = time.monotonic()
        restart = restart_loop()
        restart["seconds"] = round(time.monotonic() - started, 1)
        outcome["restart"] = restart
        _write_record(record, outcome)
        return restart

    if on_restarted is None:
        run_restart()
        return outcome
    # THE CALLER IS TOLD NOW, NOT AFTER THE RESTART. The restart measured
    # 145 s (2026-10-30), past the client's 120 s, so the crash reached
    # nobody: the call went to the background and the agent saw nothing.
    # Other calls wait for the new loop through the maintenance marker,
    # which on_restarted removes.
    outcome["restart"] = {"in_background": True}
    _write_record(record, outcome)

    def background() -> None:
        try:
            run_restart()
        finally:
            on_restarted()

    import threading
    threading.Thread(target=background, name="eda-agent-restart",
                     daemon=True).start()
    return outcome


def _write_record(record: Path, outcome: dict) -> None:
    try:
        record.write_text(json.dumps(outcome, indent=2, default=str),
                          encoding="utf-8")
    except OSError:
        outcome["record"] = ""


def crash_message(outcome: dict) -> str:
    """What the caller is told: it crashed, what was done, what to check."""
    dialog = outcome.get("dialog") or {}
    said = (dialog.get("message") or dialog.get("ocr_text") or [])
    box = (f"Altium showed {dialog.get('title', 'an error')!r}"
           + (f" saying {said[0]!r}" if said else "") + ". ") if dialog else ""
    restart = outcome.get("restart") or {}
    if restart.get("in_background"):
        after = ("The loop is being restarted now; the next call waits for "
                 "it and then works. ")
    elif restart.get("restarted"):
        after = ("The script was stopped and the loop restarted, so the next "
                 "call works. ")
    else:
        after = ("The script was stopped but the loop did NOT come back ("
                 f"{restart.get('output') or 'no output'}): run `eda-agent "
                 "relaunch --restart`. ")
    shots = outcome.get("screenshots") or []
    return (
        f"THE ALTIUM SCRIPT CRASHED while running {outcome.get('command')}. "
        + box
        + "The handler stopped part way, so part of its change may already "
        "be applied: read the design back before repeating it. "
        + after
        + (f"Screenshots: {', '.join(shots)}. " if shots else "")
        + (f"Record: {outcome.get('record')}. " if outcome.get("record") else "")
        + "This is a bug in eda-agent's Pascal for that command: tell the "
        "user, with the screenshot."
    )


def stale_progress(workspace_dir, request_id: str) -> None:
    """Remove the progress file the halted handler left behind."""
    try:
        os.unlink(Path(workspace_dir) / f"progress_{request_id}.json")
    except OSError:
        pass
