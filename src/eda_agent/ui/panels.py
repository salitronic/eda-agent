# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium's dockable panels, as targets rather than as chrome.

Panels were the hole in this layer. ``ui/windows.py`` knew the words
``docbar``, ``popuppanel`` and ``panelsholder`` only well enough to
EXCLUDE them from button hunts, so Projects, Navigator, PCB, Properties
and Messages were unreachable: nothing opened one, read one, or acted
inside one. A great deal of Altium's state lives there and none of it
was addressable.

WHY A PANEL IS NOT A DIALOG. A dialog is a top-level window with its own
handle, which is what ``windows.dialogs`` enumerates. A docked panel is a
CHILD of the main frame, sharing its handle space and its message queue,
so none of the dialog machinery finds it. Floating it would make it
top-level, but floating changes the user's layout, so this reads panels
where they are.

HOW ONE IS OPENED. Altium has no scripted "show panel" that this project
could find, and ``application.execute_menu`` reports success while
invoking nothing (task #83, recorded in ui/menu.py). So a panel is opened
the way a person opens one: through the View menu, which is a real menu
click and therefore steals focus. That is inherited, not chosen, and it
is why opening is separate from reading here. Reading takes no focus at
all.

WHAT IS READ. Once located, a panel is an ordinary accessible subtree, so
``ui/controls`` works inside it unchanged: rows, cells, trees, combos and
text all behave as they do in a dialog. Nothing new was needed for the
contents, only for finding the container.
"""
from __future__ import annotations

from typing import Optional

from . import controls, menu
from . import windows as win

#: The class that actually carries a panel's NAME, measured on a live
#: AD26 frame rather than guessed.
#:
#: The first version of this looked for holder classes (panelsholder,
#: popuppanel, dockpanel, docksite) and found the wrong windows: the
#: document tab strip and a set of off-screen holders parked at
#: -31985, none of which is a panel. Nothing named Projects or
#: Properties appeared at all.
#:
#: What names a panel is its TPopupPanelButton, the tab down the edge of
#: the frame. Measured, one per panel: Projects, Properties, Components,
#: Messages, Storage Manager.
_PANEL_BUTTON = "tpopuppanelbutton"

#: Where a panel's CONTENT lives once it is on screen. A docked panel
#: holds its grid or tree in one of these, and that is what
#: ui/controls reads. TdxTreeList is what the Projects panel uses, and
#: its grids name themselves (MainGrid, StructureGrid, SubProjectsGrid).
_PANEL_CONTENT = ("tdxtreelist", "tdxdbgrid", "tlistview", "ttreeview",
                  "tpanelframesection")

#: The View submenu that lists panels. Altium groups them under
#: View > Panels, and the group is stable across editors even though the
#: panels offered are not.
_PANELS_MENU = "View|Panels"


def available() -> bool:
    """Whether this layer can run at all. Windows plus pywin32."""
    return win.available() and controls.available()


#: A panel docked with a header on AD 26.11 is a TXPPanel caption and a
#: TViewComponent of the same name holding its content, seen live for
#: Projects and Messages. Documents are TViewComponents too, but no
#: caption carries a document's name, so a caption matching a view is a
#: panel even where no panel button names it.
_PANEL_CAPTION = "txppanel"
_PANEL_VIEW = "tviewcomponent"
#: A floating panel is a top-level window of its own, outside the frame.
_FLOATING_PANEL = "TClientBarControl"


def _frame_controls(pid: int) -> list:
    """Every child of the main frame and of each floating panel window,
    captured once."""
    frame = menu.frame(pid)
    if frame is None:
        return []
    every = []
    captured = win.capture(frame.hwnd)
    if captured is not None:
        every.extend(captured.controls)
    try:
        floating = [w for w in win.enumerate_windows(pid=pid)
                    if w.class_name == _FLOATING_PANEL]
    except Exception:                            # noqa: BLE001 - floating is extra
        floating = []
    for window in floating:
        held = win.capture(window.hwnd)
        if held is not None:
            every.extend(held.controls)
    return every


def _is_panel_title(control, views: set) -> bool:
    kind = control.class_name.lower()
    return _PANEL_BUTTON in kind or (kind == _PANEL_CAPTION
                                     and control.text in views)


def list_open(pid: int) -> list:
    """Panels currently on screen, by title.

    Titles come from the accessible tree rather than the window text: a
    docked panel's caption is drawn by the docking framework, so the
    window itself frequently reports an empty string while MSAA still
    names it.
    """
    every = _frame_controls(pid)
    views = {c.text for c in every
             if c.text and c.class_name.lower() == _PANEL_VIEW}
    seen = {}
    for control in every:
        if not _is_panel_title(control, views):
            continue
        if not control.text or control.text in seen:
            continue
        x, y, width, height = control.rect
        if x < -10000:                           # parked off screen
            continue
        seen[control.text] = {
            "name": control.text,
            "button_hwnd": control.hwnd,
            "rect": [x, y, width, height],
            "content": _content_for(every, control),
        }
    return sorted(seen.values(), key=lambda p: p["name"])


#: What an empty scan does and does not say.
EMPTY_SCAN_NOTE = (
    "no panel tabs were found. Panels are found by the tab each one "
    "carries, and this has been reported empty on AD 25 while the "
    "Properties panel was open, so it does not prove no panel is open")


def describe(panel: dict) -> dict:
    """One panel as app_list_panels reports it.

    Built from the keys list_open returns and read with .get, so a key
    the scan stops returning costs that field and not the whole reply.
    app_list_panels once read a 'holder_class' the scan no longer
    produced and answered every call with a KeyError.
    """
    return {"name": panel.get("name", ""),
            "rect": panel.get("rect"),
            "button_hwnd": panel.get("button_hwnd"),
            "content_count": len(panel.get("content") or [])}


def _content_for(every: list, button) -> list:
    """Content controls sharing a panel button's column.

    A panel's tab sits at the edge of the frame and its content fills
    the area beside it, so they share an x span. Reported as handles
    because that is what ui/controls acts on: rows, cells and trees all
    take an hwnd.
    """
    bx, by, bw, bh = button.rect
    out = []
    for control in every:
        if not any(k in control.class_name.lower() for k in _PANEL_CONTENT):
            continue
        x, y, width, height = control.rect
        if x < -10000 or width < 40 or height < 40:
            continue
        if bx - 40 <= x <= bx + max(bw, 400):
            out.append({"hwnd": control.hwnd, "class": control.class_name,
                        "rect": [x, y, width, height]})
    return out


def find(pid: int, name: str) -> Optional[dict]:
    """One open panel by name, ignoring case."""
    wanted = str(name or "").strip().lower()
    for panel in list_open(pid):
        if panel["name"].strip().lower() == wanted:
            return panel
    return None


def offered(pid: int) -> dict:
    """What View > Panels lists, without opening any of them.

    Separate from ``list_open`` on purpose: one answers "what could I
    open", the other "what is on screen". Confusing the two is how a
    caller concludes a panel does not exist when it is merely closed.
    """
    return menu.list_path(pid, _PANELS_MENU)


def open_panel(pid: int, name: str, settle: float = 1.2) -> dict:
    """Open a panel by name, and VERIFY it appeared.

    Already-open panels are reported as such rather than toggled. The
    View > Panels entries are toggles, so clicking one that is already
    open CLOSES it, and a caller asking for a panel it already had would
    have lost it.

    THE CHECK BEFORE THE CLICK CAN MISS. A panel is found by its tab,
    and the scan has been reported to find no tab while Properties was
    open, so the click closed it. A click that cannot be confirmed is
    therefore made a second time: a toggle pressed twice is back where it
    started, open or closed, so the user's panel is never left closed.
    """
    if not available():
        return {"ok": False, "reason": "UI automation is unavailable here"}

    existing = find(pid, name)
    if existing is not None:
        return {"ok": True, "name": existing["name"], "opened": False,
                "note": "already open", "rect": existing["rect"]}

    path = f"{_PANELS_MENU}|{name}"
    result = menu.click_path(pid, path, settle=settle)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "menu click failed"),
                "offered": result.get("offered", [])}

    win.wait_until(lambda: find(pid, name) is not None, settle * 2)
    panel = find(pid, name)
    if panel is None:
        back = menu.click_path(pid, path, settle=settle)
        restored = bool(back.get("ok"))
        reason = (
            "the menu entry was clicked and no panel by that name could be "
            "seen afterwards. The entry is a toggle and the panel may "
            "already have been open with no tab the scan could read, so ")
        if restored:
            reason += ("it was clicked a second time, which puts a toggle "
                       "back where it started. Whether the panel is open "
                       "could not be read either way.")
        else:
            reason += (f"it was clicked again to put it back, and that click "
                       f"failed ({back.get('reason', 'menu click failed')}). "
                       f"Check View > Panels: the panel may now be closed.")
        return {"ok": False, "name": name, "opened": False,
                "toggled_back": restored, "reason": reason}
    return {"ok": True, "name": panel["name"], "opened": True,
            "rect": panel["rect"]}


def read_panel(pid: int, name: str) -> dict:
    """Everything readable inside one panel.

    READS THE WHOLE PANEL. There is no limit and no paging, because a
    partial read of a panel is not an answer: a caller asking what is in
    Projects wants what is in Projects, and 50 of 162 rows dressed up
    with a flag is a worse version of the same wrong answer.

    It is not fast. MEASURED on a live Projects panel: the FIRST touch
    of an accessible node costs about 116 ms and every touch after about
    14 ms, because Altium materialises its tree lazily, so 162 rows is
    roughly thirty seconds. That is Altium's cost and no caching on this
    side changes it. Slow and complete beats fast and partial.

    Each content control is reported separately and named, because a
    panel routinely holds more than one and they mean different things.
    The Projects panel alone carries MainGrid, StructureGrid and
    SubProjectsGrid, and collapsing those into one list would silently
    mix a document tree with a project list.
    """
    panel = find(pid, name)
    if panel is None:
        return {"ok": False, "reason": (
            f"no panel named {name!r}. app_list_panels shows what is on "
            f"screen, and app_open_panel opens one"),
            "offered": [p["name"] for p in list_open(pid)]}

    grids = []
    total = 0
    for content in panel["content"]:
        try:
            rows = controls.list_rows(content["hwnd"])
        except Exception:                        # noqa: BLE001
            rows = []
        if not rows:
            continue
        node = controls._acc(content["hwnd"])
        label = ""
        if node is not None:
            try:
                label = str(node.accName(controls._self()) or "")
            except Exception:                    # noqa: BLE001
                label = ""
        total += len(rows)
        grids.append({
            "control": label or content["class"],
            "hwnd": content["hwnd"],
            "row_count": len(rows),
            "rows": [r["name"] for r in rows],
        })

    return {"ok": True, "name": panel["name"], "rect": panel["rect"],
            "grids": grids, "row_total": total,
            "note": ("rows come from the panel's own grids; pass a grid "
                     "hwnd to app_scroll_control or app_activate_row to "
                     "act on one" if grids else
                     "the panel is on screen and exposes no rows, which "
                     "is normal for a collapsed tab: open it first")}
