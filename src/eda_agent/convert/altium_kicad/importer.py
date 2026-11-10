# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad's own Altium importer, driven through its window.

``kicad-cli`` imports an Altium board but not a schematic, and only the
project importer brings both in linked. So kicad.exe is started on its own,
File > Import Non-KiCad Project > Altium Project is posted to it, the file
and destination dialogs are filled in, and both editors are saved.

Every menu command is read from the menu at run time, never hard-coded,
and posted (PostMessage WM_COMMAND), because the dialogs are modal and a
call that waits for the menu would wait for the dialog. Each step waits for
the window or file it expects instead of sleeping. A message box KiCad
raises on the way is read, answered with its default when it offers only
OK, and reported; any other dialog stops the import with its text.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Callable, Optional

FILE_DIALOG = "Import Altium Project Files"
FOLDER_DIALOG = "KiCad Project Destination"
#: KiCad's own steps of the import: the layer map is accepted as KiCad
#: matched it (the mechanical layers are mapped by kind afterwards), and a
#: progress window is left to finish.
LAYER_MAP_DIALOG = "Edit Mapping of Imported Layers"
PROGRESS = ("Load PCB", "Loading", "Importing")
#: KiCad reopens the last project, so the title is "<project> - KiCad 10.0".
MAIN_TITLE = "KiCad"


class ImportFailed(RuntimeError):
    def __init__(self, stage: str, reason: str, seen: Optional[list] = None):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason, self.seen = stage, reason, seen or []


def kicad_exe(cli_path: str) -> str:
    """kicad.exe beside kicad-cli.exe."""
    return os.path.join(os.path.dirname(cli_path), "kicad.exe")


def _wait(check: Callable[[], Any], timeout: float, poll: float = 0.25) -> Any:
    end = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= end:
            return None
        time.sleep(poll)


def _clean(text: str) -> str:
    return text.split("\t")[0].replace("&", "").strip().lower()


def menu_command(hwnd: int, path: list[str]) -> Optional[int]:
    """The command id at ``path`` in a window's menu bar, matched by text
    without accelerators, case ignored, by prefix."""
    import win32gui

    menu = win32gui.GetMenu(hwnd)
    for depth, want in enumerate(path):
        if not menu:
            return None
        found = None
        texts = [_clean(_menu_text(menu, i)) for i in range(win32gui.GetMenuItemCount(menu))]
        # An exact name first, so "save" is Save and never Save As.
        for i, text in enumerate(texts):
            if text.rstrip(".") == want.lower():
                found = i
                break
        if found is None:
            found = next((i for i, text in enumerate(texts)
                          if text.startswith(want.lower())), None)
        if found is None:
            return None
        if depth == len(path) - 1:
            return win32gui.GetMenuItemID(menu, found)
        menu = win32gui.GetSubMenu(menu, found)
    return None


def _menu_text(menu: int, index: int) -> str:
    """An item's text. KiCad's (wx) menus answer GetMenuString with
    nothing; GetMenuItemInfo with MIIM_STRING has the text."""
    import win32con
    import win32gui
    import win32gui_struct
    try:
        buf, _extras = win32gui_struct.EmptyMENUITEMINFO(win32con.MIIM_STRING | win32con.MIIM_FTYPE)
        win32gui.GetMenuItemInfo(menu, index, True, buf)
        return win32gui_struct.UnpackMENUITEMINFO(buf).text or ""
    except Exception:                            # noqa: BLE001 - a separator
        return ""


def _post_command(hwnd: int, command: int) -> None:
    import win32con
    import win32gui
    win32gui.PostMessage(hwnd, win32con.WM_COMMAND, command, 0)


def _children(hwnd: int, class_name: str) -> list[int]:
    import win32gui
    out = []

    def visit(child, _):
        if win32gui.GetClassName(child) == class_name:
            out.append(child)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
    except Exception:                            # noqa: BLE001 - no children
        pass
    return out


def _set_text(hwnd: int, text: str) -> None:
    import win32con
    import win32gui
    win32gui.SendMessage(hwnd, win32con.WM_SETTEXT, 0, text)


def _press(dialog: int, caption: str) -> bool:
    import win32con
    import win32gui
    want = caption.replace("&", "").lower()
    for button in _children(dialog, "Button"):
        if win32gui.GetWindowText(button).replace("&", "").lower() == want:
            win32gui.PostMessage(button, win32con.BM_CLICK, 0, 0)
            return True
    return False


def _windows(pid: int) -> list:
    from ...ui import windows
    return windows.enumerate_windows(pid=pid)


def _dialog(pid: int, title: str):
    for w in _windows(pid):
        if w.class_name == "#32770" and w.title == title:
            return w
    return None


def _text_of(hwnd: int) -> list[str]:
    from ...ui import windows
    out = []
    for cls in ("Static", "Edit"):
        for child in _children(hwnd, cls):
            t = windows.read_text(child).strip()
            if t:
                out.append(t)
    return out


def _answer_message_boxes(pid: int, expected: set[str], seen: list) -> None:
    """Answer an OK-only message box and record it; refuse anything else."""
    import win32gui
    for w in _windows(pid):
        if w.class_name != "#32770" or w.title in expected:
            continue
        if w.title.startswith(PROGRESS):
            continue
        if w.title == LAYER_MAP_DIALOG:
            if sum(1 for r in seen if r.get("title") == LAYER_MAP_DIALOG) >= 2:
                raise ImportFailed("layers", "KiCad's automatic layer match left "
                                             "layers it will not import without "
                                             "them", seen)
            _press(w.hwnd, "Auto-Match Layers")
            time.sleep(0.5)
            if _press(w.hwnd, "OK"):
                seen.append({"title": w.title, "answered": "Auto-Match Layers, OK"})
                continue
        buttons = [win32gui.GetWindowText(b).replace("&", "")
                   for b in _children(w.hwnd, "Button")]
        record = {"title": w.title, "text": _text_of(w.hwnd), "buttons": buttons}
        # A progress window: elapsed time and only Cancel. Left to finish.
        if [b.lower() for b in buttons] == ["cancel"] and any(
                t.startswith("Elapsed time") for t in record["text"]):
            continue
        if [b.lower() for b in buttons] in (["ok"], ["close"]):
            _press(w.hwnd, buttons[0])
            record["answered"] = buttons[0]
            seen.append(record)
            continue
        raise ImportFailed("dialog", f"KiCad asked something the import does not "
                                     f"answer: {w.title!r}", seen + [record])


def import_project(cli_path: str, prjpcb: str, out_dir: str,
                   timeout: float = 240.0) -> dict[str, Any]:
    """Import ``prjpcb`` into the empty folder ``out_dir`` and save it.

    Returns the files written and the message boxes answered on the way.
    KiCad is closed afterwards whatever happened.
    """
    import win32con
    import win32gui

    exe = kicad_exe(cli_path)
    if not os.path.isfile(exe):
        raise ImportFailed("launch", f"kicad.exe not found beside {cli_path}")
    os.makedirs(out_dir, exist_ok=True)
    if os.listdir(out_dir):
        raise ImportFailed("destination", f"{out_dir} is not empty")
    seen: list = []
    from ...noconsole import no_window
    proc = subprocess.Popen([exe], **no_window())
    try:
        main = _wait(lambda: next((w for w in _windows(proc.pid)
                                   if MAIN_TITLE in w.title and w.class_name != "#32770"
                                   and win32gui.GetMenu(w.hwnd)), None), 60)
        if main is None:
            raise ImportFailed("launch", "KiCad's main window did not appear")
        command = _wait(lambda: menu_command(main.hwnd, ["file", "import non-kicad project",
                                                         "altium project"]), 15)
        if not command:
            raise ImportFailed("menu", "File > Import Non-KiCad Project > Altium Project "
                                       "is not in KiCad's menu")
        _post_command(main.hwnd, command)

        dlg = _wait(lambda: _dialog(proc.pid, FILE_DIALOG), 30)
        if dlg is None:
            raise ImportFailed("file dialog", f"{FILE_DIALOG!r} did not open")
        edits = _children(dlg.hwnd, "Edit")
        if not edits:
            raise ImportFailed("file dialog", "no file name box")
        _set_text(edits[0], os.path.abspath(prjpcb))
        if not _press(dlg.hwnd, "Open"):
            raise ImportFailed("file dialog", "no Open button")

        folder = _wait(lambda: _dialog(proc.pid, FOLDER_DIALOG), 30)
        if folder is None:
            raise ImportFailed("folder dialog", f"{FOLDER_DIALOG!r} did not open")
        edits = _children(folder.hwnd, "Edit")
        if not edits:
            raise ImportFailed("folder dialog", "no folder box")
        # The folder box (control 1152) reads what is set in it; the
        # address combo does not take WM_SETTEXT as typing, and the dialog
        # then answers with the folder it was showing. Both are set.
        for edit in edits:
            _set_text(edit, os.path.abspath(out_dir))
        _press(folder.hwnd, "Select Folder")
        # The first press can only navigate into the folder.
        if _wait(lambda: not win32gui.IsWindow(folder.hwnd), 2) is None:
            _press(folder.hwnd, "Select Folder")
            if _wait(lambda: not win32gui.IsWindow(folder.hwnd), 10) is None:
                raise ImportFailed("folder dialog", "the destination was not taken")

        def editors():
            _answer_message_boxes(proc.pid, {FILE_DIALOG, FOLDER_DIALOG}, seen)
            found = {}
            for w in _windows(proc.pid):
                if w.title.endswith("Schematic Editor"):
                    found["sch"] = w
                elif w.title.endswith("PCB Editor"):
                    found["pcb"] = w
            return found if len(found) == 2 else None

        found = _wait(editors, timeout, poll=1.0)
        if not found:
            raise ImportFailed("import", "the schematic and PCB editors did not both open",
                               seen)

        name = os.path.splitext(os.path.basename(prjpcb))[0]
        targets = {"sch": os.path.join(out_dir, name + ".kicad_sch"),
                   "pcb": os.path.join(out_dir, name + ".kicad_pcb")}
        written = {}
        for key in ("sch", "pcb"):
            w = found[key]
            before = os.path.getmtime(targets[key]) if os.path.exists(targets[key]) else 0
            save = menu_command(w.hwnd, ["file", "save"])
            if not save:
                raise ImportFailed("save", f"no File > Save in the {w.title!r} window", seen)
            _post_command(w.hwnd, save)
            ok = _wait(lambda: os.path.exists(targets[key])
                       and os.path.getmtime(targets[key]) > before, 60)
            _answer_message_boxes(proc.pid, {FILE_DIALOG, FOLDER_DIALOG}, seen)
            if not ok:
                raise ImportFailed("save", f"{os.path.basename(targets[key])} was not written",
                                   seen)
            written[key] = targets[key]
        return {"files": sorted(os.listdir(out_dir)), "written": written,
                "message_boxes": seen}
    finally:
        for w in _windows(proc.pid):
            try:
                win32gui.PostMessage(w.hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:                    # noqa: BLE001 - already gone
                pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
