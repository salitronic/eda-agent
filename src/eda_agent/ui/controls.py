# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read and SET the controls inside an Altium dialog.

Buttons were the easy half: a dialog is only fully drivable when its
checkboxes, radios, fields and lists can be inspected and changed.

WHAT MAKES THIS POSSIBLE. Altium's VCL controls are opaque to plain
Win32 (a TXPCheckBox is not the stock "Button" class, so BM_GETCHECK
tells you nothing), but they ARE described accessibly. MEASURED on the
Update From Library wizard:

  TXPCheckBox    'Update To Latest Revision'  role=checkbox     value='Checked'
  TXPRadioButton 'Fully replace symbols ...'  role=radiobutton  value='Checked'
  TXPComboBox    'All Components'             role=combobox     value='All Components'
  TdxTreeList    ''                           role=list         value='Grid_Sheets'

So STATE is read from MSAA, which is reliable, and CHANGE is made by
clicking, which is the only thing these controls answer to. Every
setter then READS THE STATE BACK and reports whether it took, because a
click that silently did nothing is the failure mode that matters: it
would leave a caller believing a setting was applied.

WHAT IS DELIBERATELY NOT GUESSED. A control reporting 'unavailable' is
refused rather than clicked; Altium greys options that do not apply to
the current selection, and clicking one is at best a no-op. Grids are
readable and selectable by row text, but their cells are not editable
from here, which is stated rather than papered over.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import POINTER, byref, c_long, wintypes
from typing import Optional

from . import windows as win
from .captions import plain

try:                                            # pragma: no cover - platform
    import comtypes
    import comtypes.client
    from comtypes import COMError
    from comtypes.automation import VARIANT

    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen.Accessibility import IAccessible

    import win32con
    import win32gui
    _AVAILABLE = True
except Exception:                               # pragma: no cover - platform
    _AVAILABLE = False

_OBJID_CLIENT = 0xFFFFFFFC

#: MSAA roles, the ones that matter for driving a dialog.
ROLES = {
    0x2B: "button", 0x2C: "checkbox", 0x2D: "radiobutton",
    0x2E: "combobox", 0x2F: "droplist", 0x2A: "text",
    0x29: "statictext", 0x21: "list", 0x22: "listitem",
    0x23: "outline", 0x24: "outlineitem", 0x18: "table", 0x1D: "cell",
    0x33: "spinbutton", 0x28: "slider", 0x10: "pane", 0x0A: "client",
}

_STATE_UNAVAILABLE = 0x00000001
_STATE_CHECKED = 0x00000010
_STATE_SELECTED = 0x00000002

#: Roles a click toggles or selects. Anything else is not set this way.
SETTABLE = ("checkbox", "radiobutton")


def available() -> bool:
    return _AVAILABLE and win.available()


def _acc(hwnd):
    ptr = POINTER(IAccessible)()
    ctypes.oledll.oleacc.AccessibleObjectFromWindow(
        ctypes.c_void_p(hwnd), ctypes.c_ulong(_OBJID_CLIENT),
        byref(IAccessible._iid_), byref(ptr))
    return ptr.QueryInterface(IAccessible)


#: CHILDID_SELF, built once. It is an immutable constant and every
#: accessible call takes one, so constructing a fresh VARIANT per call
#: was pure overhead. MEASURED: twenty accName calls through _ask took
#: 1.146s against 0.143s raw, and this allocation was the whole
#: difference. On a 162-row grid that is eight seconds of nothing.
_SELF_VARIANT = None


def _self(child: int = 0):
    global _SELF_VARIANT
    if child:
        var = VARIANT()
        var.vt = 3
        var.value = child
        return var
    if _SELF_VARIANT is None:
        var = VARIANT()
        var.vt = 3
        var.value = 0
        _SELF_VARIANT = var
    return _SELF_VARIANT


def _ask(node, attr, child=0):
    try:
        return getattr(node, attr)(_self(child))
    except COMError:
        return None


def read(hwnd: int) -> dict:
    """Everything known about one control: role, value, checked, enabled.

    ``checked`` is None for controls where the idea does not apply, so a
    caller can tell "not a checkbox" from "a checkbox that is off".
    """
    if not available():
        return {"ok": False, "reason": "accessibility is unavailable here"}
    try:
        node = _acc(hwnd)
    except Exception:                            # pragma: no cover - guard
        return {"ok": False, "reason": "no accessible object for that window"}

    role_id = _ask(node, "accRole")
    state = _ask(node, "accState") or 0
    value = _ask(node, "accValue")
    role = ROLES.get(role_id, role_id)
    checked = None
    if role in ("checkbox", "radiobutton"):
        checked = bool(state & _STATE_CHECKED)
    return {
        "ok": True,
        "role": role,
        "name": _ask(node, "accName") or "",
        "value": value,
        "checked": checked,
        "enabled": not bool(state & _STATE_UNAVAILABLE),
        "selected": bool(state & _STATE_SELECTED),
        "hwnd": hwnd,
    }


def describe_all(window) -> list:
    """Every control of a captured window, with its accessible state.

    Containers are dropped: a dialog has far more panels than controls
    and listing them buries the parts a caller can act on.
    """
    out = []
    for control in window.controls:
        info = read(control.hwnd)
        if not info.get("ok"):
            continue
        if info["role"] in ("pane", "client") and not control.text:
            continue
        info["class"] = control.class_name
        info["label"] = control.text
        out.append(info)
    return out


def find(window, label: str, role: Optional[str] = None):
    """One control by its visible label, ignoring & , case and chrome.

    Matching on the label rather than an index is the same choice made
    for menus, for the same reason: positions move, captions do not.

    WIZARD CHROME IS STRIPPED TOO. Altium spells its wizard buttons
    '&Next >' and '< &Back', so an exact match after removing the
    ampersand still failed find('Next') and a caller walking a wizard
    got "no Next button" from a dialog that plainly has one. MEASURED on
    Update From Library, whose eight buttons include '&Next >',
    '< &Back' and '&Advanced...'.

    Exact is tried across every control BEFORE the relaxed pass, so a
    dialog holding both 'Next' and '&Next >' still resolves the one that
    was asked for rather than whichever came first in the tree.
    """
    def flat(text):
        return plain(text).replace("&", "").strip().lower()

    def bare(text):
        # Wizard arrows and the ellipsis that marks a dialog-opening
        # button. Stripped from the ENDS only: a label like
        # 'Save > Export' keeps its middle intact.
        return flat(text).strip("<>. \t")

    described = describe_all(window)
    wanted = flat(label)
    for info in described:
        if flat(info["label"]) == wanted or flat(info["name"]) == wanted:
            if role is None or info["role"] == role:
                return info

    loose = bare(label)
    if not loose:
        return None
    for info in described:
        if bare(info["label"]) == loose or bare(info["name"]) == loose:
            if role is None or info["role"] == role:
                return info
    return None


def _click(hwnd: int) -> None:
    """Press a VCL control the way Altium's controls accept.

    Posted button messages, addressed to the control's own handle, which
    is what TXPBitBtn was measured to answer. Nothing is synthesised
    globally and the cursor does not move.
    """
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN,
                         win32con.MK_LBUTTON, 0)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, 0)


def set_checked(hwnd: int, want: bool, settle: float = 0.35) -> dict:
    """Set a checkbox, and VERIFY it took.

    Clicks only when the current state differs, so calling this twice is
    not a toggle, and reads the state back afterwards. A click that
    silently fails is the whole risk here: it would leave a caller
    believing an option was applied.
    """
    before = read(hwnd)
    if not before.get("ok"):
        return before
    if before["role"] not in SETTABLE:
        return {"ok": False, "reason": (
            f"{before['name']!r} is a {before['role']}, which is not set "
            f"by checking it")}
    if not before["enabled"]:
        return {"ok": False, "reason": (
            f"{before['name']!r} is disabled, so Altium does not accept "
            f"this setting in the current state. Refusing to click it")}
    if before["role"] == "radiobutton" and not want:
        return {"ok": False, "reason": (
            "a radio button cannot be switched off, only another one in "
            "its group switched on")}

    if before["checked"] == want:
        return {"ok": True, "changed": False, "checked": want,
                "note": "already in that state"}

    _click(hwnd)
    # EVERY ONE OF THESE WAITS ENDS IN A CHECK, so the check is what to
    # poll. settle stays the ceiling; a control that reports its new
    # state at once no longer costs the full pause.
    win.wait_until(lambda: read(hwnd).get("checked") == want, settle)
    after = read(hwnd)
    if after.get("checked") != want:
        return {"ok": False, "changed": False,
                "checked": after.get("checked"),
                "reason": (f"clicked {before['name']!r} but it is still "
                           f"{'checked' if after.get('checked') else 'unchecked'}"
                           f". The control did not accept the press")}
    return {"ok": True, "changed": True, "checked": want,
            "name": before["name"]}


def set_text(hwnd: int, text: str, settle: float = 0.2) -> dict:
    """Put text into an edit field, and read it back.

    WM_SETTEXT rather than synthesised typing: it is addressed to the
    control and cannot land somewhere else if focus moves.
    """
    before = read(hwnd)
    if not before.get("ok"):
        return before
    if not before["enabled"]:
        return {"ok": False, "reason": f"{before['name']!r} is disabled"}
    try:
        win32gui.SendMessage(hwnd, win32con.WM_SETTEXT, 0, str(text))
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "reason": f"WM_SETTEXT failed: {exc}"}
    win.wait_until(lambda: win.read_text(hwnd) == str(text), settle)
    got = win.read_text(hwnd)
    if got != str(text):
        return {"ok": False, "reason": (
            f"set {text!r} but the field reads {got!r}. Some VCL fields "
            f"ignore WM_SETTEXT and only accept typed input")}
    return {"ok": True, "text": got}


def child_count(node) -> int:
    """How many children a node HAS, without materialising any of them.

    One call, and cheap: accChildCount does not touch the children, so
    it costs nothing like the ~116 ms per node that reading a name does.

    That distinction is what lets a truncated read stay honest. A caller
    can be told there are 162 rows and shown 50, instead of being handed
    50 and left to assume that is all of them.
    """
    try:
        return int(node.accChildCount)
    except Exception:                            # noqa: BLE001
        return 0


def _child_objects(node, limit: int = 0, offset: int = 0) -> list:
    """Child IAccessible OBJECTS, which is what a grid actually returns.

    The first version asked for simple child IDs (accName(1), accName(2)
    ...) and got nothing, and the empty result was reported as "this
    tree has no rows". MEASURED on Altium's TdxTreeList: accChildCount
    is 65 and every child arrives as a full object, not an id. The rows
    were always there; the question was wrong.
    """
    try:
        count = node.accChildCount
    except COMError:
        return []
    if not count:
        return []
    # ASK THE OS FOR THE WINDOW WE WANT, not for everything.
    #
    # AccessibleChildren takes a START INDEX and a count, and this
    # passed 0 and the full count regardless of what the caller asked
    # for, then skipped in Python. MEASURED on a 163-row grid: every
    # page cost about 17 seconds whatever the offset, and even a 2-row
    # final page cost 11, because the whole tree was materialised each
    # time. Paging that way is slower than not paging at all.
    skip = int(offset) if offset and offset > 0 else 0
    cap = int(limit) if limit and limit > 0 else 0
    if skip >= count:
        return []
    want = count - skip
    if cap:
        want = min(want, cap)
    arr = (VARIANT * want)()
    got = c_long()
    try:
        ctypes.oledll.oleacc.AccessibleChildren(node, skip, want, arr,
                                                byref(got))
    except Exception:                            # pragma: no cover - guard
        return []
    # QueryInterface PER CHILD IS THE COST, not the enumeration.
    # Measured: 163 children took 3.785s here, and a caller that wanted
    # the first forty paid for all of them. The cap stops at what was
    # asked for.
    # OFFSET SKIPS BEFORE ANY WORK IS DONE. Skipped entries are never
    # QueryInterface'd and their names are never asked for, which is
    # what makes paging cheaper than re-reading: page two costs page
    # two, not pages one and two.
    # The OS already applied the offset and the cap, so this only
    # unwraps what came back.
    out = []
    for i in range(got.value):
        if arr[i].vt != 9 or not arr[i].value:
            continue
        try:
            out.append(arr[i].value.QueryInterface(IAccessible))
        except COMError:
            pass
    return out


def list_rows(hwnd: int, limit: int = 0, offset: int = 0) -> list:
    """Every row of a list, grid or tree, with its name and location.

    Scrollbars are dropped: a grid reports them as children and they
    are not rows.
    """
    if not available():
        return []
    try:
        node = _acc(hwnd)
    except Exception:                            # pragma: no cover - guard
        return []
    # A NAME IS A COM ROUND TRIP PER ROW. Measured on a live Projects
    # panel: 162 rows cost about fifteen seconds, and the caller then
    # threw most of them away. Stopping at the cap turns the cost into
    # what was actually asked for.
    cap = int(limit) if limit and limit > 0 else 0
    # Rows that carry no name are skipped below, so ask for a few more
    # than the cap rather than exactly the cap.
    rows = []
    for child in _child_objects(node, limit=cap, offset=offset):
        name = _ask(child, "accName")
        if not name or not str(name).strip():
            continue
        if "scrollbar" in str(name).lower():
            continue
        rows.append({"name": str(name).strip(), "node": child})
        if cap and len(rows) >= cap:
            break
    return rows


def list_items(hwnd: int) -> list:
    """Just the row labels."""
    return [row["name"] for row in list_rows(hwnd)]


def select_row(hwnd: int, name: str, settle: float = 0.6) -> dict:
    """Select a row by name, and VERIFY the selection took.

    Tried in order: the accessible selection call, then a click on the
    row's own rectangle. DevExpress exposes rows read-only in places,
    so the click is the fallback that actually lands, the same pattern
    the menus needed.
    """
    rows = list_rows(hwnd)
    if not rows:
        return {"ok": False, "reason": "that control exposes no rows"}

    def flat(text):
        return plain(text).strip().lower()

    match = next((r for r in rows if flat(r["name"]) == flat(name)), None)
    if match is None:
        return {"ok": False,
                "reason": f"no row named {name!r}",
                "offered": [r["name"] for r in rows][:60]}

    node = match["node"]
    try:
        node.accSelect(3, _self())   # TAKEFOCUS | TAKESELECTION
        win.wait_until(lambda: _row_selected(node), settle)
    except COMError:
        pass
    if _row_selected(node):
        return {"ok": True, "row": match["name"], "how": "accSelect"}

    try:
        x, y, width, height = node.accLocation(_self())
    except COMError:
        return {"ok": False, "reason": (
            f"{name!r} could not be selected and reports no location to "
            f"click")}
    if width <= 0 or x < -10000:
        return {"ok": False, "reason": (
            f"{name!r} is not on screen; the list may need scrolling, "
            f"which is not implemented")}
    # A real click on the row's own rectangle, and ONLY that. An earlier
    # version also posted a button message to the tree with lParam 0,
    # which is client coordinate (0,0): that lands on the FIRST row and
    # selected the wrong one before the real click ever happened.
    _real_click(hwnd, x + width // 2, y + height // 2)
    win.wait_until(lambda: _row_selected(node), settle)
    if _row_selected(node):
        return {"ok": True, "row": match["name"], "how": "click"}
    return {"ok": False, "row": match["name"],
            "reason": (f"clicked {match['name']!r} but it does not report "
                       f"as selected")}


def _row_selected(node) -> bool:
    state = _ask(node, "accState") or 0
    return bool(state & (_STATE_SELECTED | 0x00000004))   # selected|focused


def _real_click(hwnd: int, x: int, y: int) -> None:
    """Real click, cursor restored. Needed where posted messages are
    ignored, which is the case for DevExpress grid rows.

    REFUSES UNLESS hwnd's APPLICATION OWNS THE FOREGROUND, checked again
    after the pointer has moved. mouse_event is not addressed to a
    window: it lands on whatever is under the cursor in whatever is
    active, so without this a click meant for a grid row can be
    delivered to another application entirely.
    """
    from ctypes import wintypes

    win.require_foreground(hwnd, "a click")
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    origin = (point.x, point.y)
    user32.SetCursorPos(x, y)

    def _arrived() -> bool:
        here = wintypes.POINT()
        user32.GetCursorPos(byref(here))
        return abs(here.x - x) <= 2 and abs(here.y - y) <= 2

    # Windows moves the cursor synchronously, so this is normally already
    # true and the old 0.15 was paid for nothing. The hold between down
    # and up stays a sleep: it is not waiting for anything observable,
    # it is making the press long enough to register as a click.
    win.wait_until(_arrived, 0.15)
    try:
        win.require_foreground(hwnd, "a mouse press")
    except win.ForegroundLost:
        user32.SetCursorPos(*origin)
        raise
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.25)
    user32.SetCursorPos(*origin)


def _default_action(node) -> bool:
    """Invoke a node's own default action. False if it declines.

    Tried FIRST everywhere below, because it needs no coordinates and no
    focus. Altium's DevExpress MENU bars refuse it (see ui/menu.py, which
    is why menus move a real mouse), but that finding is about the bars.
    Ordinary list, tree and grid nodes were measured to accept it, so the
    expensive path is the fallback rather than the rule.
    """
    try:
        node.accDoDefaultAction(_self())
        return True
    except COMError:
        return False


def _node_rect(node):
    """Screen rectangle of an accessible node, or None if it has none."""
    try:
        left, top, width, height = node.accLocation(_self())
    except COMError:
        return None
    if width <= 0 or height <= 0:
        return None
    return int(left), int(top), int(width), int(height)


def _click_node(node, hwnd: int) -> bool:
    """Real click at a node's centre. The last resort, and it moves the
    pointer, so everything above tries not to need it. ``hwnd`` is the
    window the node belongs to, which must own the foreground."""
    rect = _node_rect(node)
    if rect is None:
        return False
    left, top, width, height = rect
    _real_click(hwnd, left + width // 2, top + height // 2)
    return True


def _state_has(node, bit: int) -> bool:
    try:
        return bool(int(node.accState(_self())) & bit)
    except (COMError, TypeError, ValueError):
        return False


#: MSAA state bits used below. Named rather than inlined because a bare
#: 0x200000 in a condition is unreadable and easy to get wrong.
_STATE_EXPANDED = 0x00000200
_STATE_COLLAPSED = 0x00000400
_STATE_SELECTED = 0x00000002
_STATE_FOCUSED = 0x00000004


def list_choices(hwnd: int) -> list:
    """The options of a combo box or drop list, in order.

    A combo reports its options as children, the same shape a grid uses
    for rows, so this is list_rows' logic against a different role. The
    reader already surfaced the CURRENT value; without this a caller
    could see what a combo said and not what it could say.
    """
    node = _acc(hwnd)
    if node is None:
        return []
    out = []
    for child in _child_objects(node):
        try:
            name = child.accName(_self())
        except COMError:
            name = None
        if name:
            out.append({"name": str(name), "node": child})
    return out


def select_item(hwnd: int, name: str, settle: float = 0.6) -> dict:
    """Choose an option in a combo box or drop list, and verify it took.

    THE READER COULD SEE THESE AND NOTHING COULD SET THEM. combobox and
    droplist have been in ROLES and in read() from the start, so a caller
    could list a dialog, see a combo and its current value, and have no
    way to change it. That is the same shape as a property that is
    readable and unwritable, which this project keeps finding and fixing.

    Three routes, cheapest first: the option's own default action, then
    an accessible selection, then a real click on its rectangle. The
    value is read back afterwards, so a route that runs and changes
    nothing is reported rather than believed.
    """
    def flat(text):
        return plain(text).replace("&", "").strip().lower()

    choices = list_choices(hwnd)
    if not choices:
        return {"ok": False, "reason": (
            "that control exposes no options. A DevExpress combo often "
            "populates only once it is dropped down; try pressing it "
            "first with app_set_dialog_control")}

    match = next((c for c in choices if flat(c["name"]) == flat(name)), None)
    if match is None:
        return {"ok": False,
                "reason": f"no option named {name!r}",
                "offered": [c["name"] for c in choices][:60]}

    before = read(hwnd).get("value")
    node = match["node"]

    for how in ("default_action", "accSelect", "click"):
        if how == "default_action":
            acted = _default_action(node)
        elif how == "accSelect":
            try:
                node.accSelect(3, _self())       # TAKEFOCUS | TAKESELECTION
                acted = True
            except COMError:
                acted = False
        else:
            acted = _click_node(node, hwnd)
        if not acted:
            continue

        win.wait_until(
            lambda: flat(read(hwnd).get("value")) == flat(match["name"]), settle)
        now = read(hwnd).get("value")
        if flat(now) == flat(match["name"]):
            return {"ok": True, "value": now, "how": how, "was": before}

    return {"ok": False, "value": read(hwnd).get("value"), "was": before,
            "reason": (f"tried the default action, accSelect and a click on "
                       f"{match['name']!r}; the control still reads "
                       f"{read(hwnd).get('value')!r}")}


def expand(hwnd: int, name: str, want: bool = True,
           settle: float = 0.6) -> dict:
    """Open or close a tree node, and verify the state changed.

    outline and outlineitem have been in ROLES since the beginning and
    nothing could open one, so anything nested was invisible until a
    human expanded it by hand. A tree that cannot be expanded is a tree
    whose contents do not exist as far as a caller is concerned.
    """
    def flat(text):
        return plain(text).strip().lower()

    rows = list_rows(hwnd)
    if not rows:
        return {"ok": False, "reason": "that control exposes no nodes"}

    match = next((r for r in rows if flat(r["name"]) == flat(name)), None)
    if match is None:
        return {"ok": False, "reason": f"no node named {name!r}",
                "offered": [r["name"] for r in rows][:60]}

    node = match["node"]

    def expanded() -> bool:
        return _state_has(node, _STATE_EXPANDED)

    def collapsed() -> bool:
        return _state_has(node, _STATE_COLLAPSED)

    if not expanded() and not collapsed():
        return {"ok": False, "node": match["name"], "reason": (
            "that node reports neither expanded nor collapsed, so it has "
            "no children to show and there is nothing to open")}

    if expanded() == want:
        return {"ok": True, "node": match["name"], "expanded": want,
                "changed": False, "note": "already in that state"}

    for how in ("default_action", "click"):
        acted = _default_action(node) if how == "default_action"             else _click_node(node, hwnd)
        if not acted:
            continue
        win.wait_until(lambda: expanded() == want, settle)
        if expanded() == want:
            return {"ok": True, "node": match["name"], "expanded": want,
                    "changed": True, "how": how}

    return {"ok": False, "node": match["name"], "expanded": expanded(),
            "reason": f"tried to {'expand' if want else 'collapse'} it and "
                      f"the node did not change state"}


def list_cells(hwnd: int, row_name: str) -> dict:
    """The cells of one grid row, by column position.

    A grid row's children are its cells, so this is one level below
    list_rows. Columns are reported by index as well as by name because
    Altium's rule editors leave many cells unnamed, and an unnamed cell
    is still addressable by where it sits.
    """
    def flat(text):
        return plain(text).strip().lower()

    rows = list_rows(hwnd)
    match = next((r for r in rows if flat(r["name"]) == flat(row_name)), None)
    if match is None:
        return {"ok": False, "reason": f"no row named {row_name!r}",
                "offered": [r["name"] for r in rows][:60]}

    cells = []
    for index, child in enumerate(_child_objects(match["node"])):
        try:
            name = child.accName(_self())
        except COMError:
            name = None
        try:
            value = child.accValue(_self())
        except COMError:
            value = None
        cells.append({"index": index, "name": str(name or ""),
                      "value": None if value is None else str(value)})
    return {"ok": True, "row": match["name"], "cells": cells}


def set_cell(hwnd: int, row_name: str, column: str, text: str,
             settle: float = 0.6) -> dict:
    """Type into one grid cell, and read it back.

    ALTIUM'S RULE AND PROPERTY EDITORS TAKE THEIR INPUT THIS WAY, and
    until now a caller could select a row and change nothing in it.

    A grid cell is not an edit control until it is being edited, so the
    cell is activated first and the text then goes to whatever edit
    control the grid put in its place. That is why this reads the value
    back off the CELL rather than off the editor: the editor disappears.

    column accepts a name or a numeric index, because these grids leave
    many cells unnamed and an index is then the only handle there is.
    """
    listing = list_cells(hwnd, row_name)
    if not listing.get("ok"):
        return listing

    cells = listing["cells"]
    target = None
    if str(column).strip().isdigit():
        index = int(str(column).strip())
        target = next((c for c in cells if c["index"] == index), None)
        if target is None:
            return {"ok": False, "reason": (
                f"column {index} is out of range; the row has "
                f"{len(cells)} cells")}
    else:
        wanted = str(column).strip().lower()
        target = next((c for c in cells
                       if c["name"].strip().lower() == wanted), None)
        if target is None:
            return {"ok": False, "reason": f"no column named {column!r}",
                    "offered": [c["name"] or f"#{c['index']}" for c in cells]}

    rows = list_rows(hwnd)
    row = next((r for r in rows
                if str(r["name"]).strip().lower()
                == str(listing["row"]).strip().lower()), None)
    if row is None:
        return {"ok": False, "reason": "the row disappeared while reading it"}

    kids = _child_objects(row["node"])
    if target["index"] >= len(kids):
        return {"ok": False, "reason": "the row's cells changed while reading"}
    cell = kids[target["index"]]

    # Put the cell into edit mode. A grid that answers the default action
    # opens its editor without the pointer moving; otherwise click it.
    if not _default_action(cell):
        if not _click_node(cell, hwnd):
            return {"ok": False, "reason": (
                "that cell has no screen rectangle and refused its default "
                "action, so there is no way to start editing it")}

    focused = win32gui.GetFocus()
    if not focused:
        return {"ok": False, "reason": (
            "the cell was activated but no edit control took focus, so the "
            "grid is not accepting typed input there")}

    try:
        win32gui.SendMessage(focused, win32con.WM_SETTEXT, 0, str(text))
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "reason": f"WM_SETTEXT failed: {exc}"}

    # Commit. A grid editor keeps the old value until the edit is closed,
    # so reading back before this reports whatever was there before.
    _tap_enter(hwnd)

    def reads_back() -> bool:
        again = list_cells(hwnd, listing["row"])
        if not again.get("ok"):
            return False
        for c in again["cells"]:
            if c["index"] == target["index"]:
                return str(c["value"] or "") == str(text)
        return False

    win.wait_until(reads_back, settle)
    again = list_cells(hwnd, listing["row"])
    got = next((c["value"] for c in again.get("cells", [])
                if c["index"] == target["index"]), None)
    if str(got or "") != str(text):
        return {"ok": False, "value": got, "reason": (
            f"set {text!r} but the cell reads {got!r}. Some grid columns "
            f"are read-only, and some validate on commit and revert")}
    return {"ok": True, "row": listing["row"],
            "column": target["name"] or target["index"], "value": got}


def _tap_enter(hwnd: int) -> None:
    """Commit a grid edit. Sent to the focused window, not globally.

    Guarded before the press AND before the release: "the focused
    window" is whatever is focused at the instant each event fires, not
    the one that was focused when the edit started.
    """
    user32 = ctypes.windll.user32
    win.require_foreground(hwnd, "Enter")
    user32.keybd_event(0x0D, 0, 0, 0)
    time.sleep(0.03)
    win.require_foreground(hwnd, "an Enter release")
    user32.keybd_event(0x0D, 0, 2, 0)


#: MSAA roles for a tab strip and one of its pages.
_ROLE_PAGETAB = 0x25
_ROLE_PAGETABLIST = 0x3C


def list_tabs(window) -> list:
    """Tab pages of a dialog, in order.

    PROPERTY DIALOGS HIDE MOST OF THEMSELVES BEHIND TABS, and nothing
    could switch one, so everything on a page other than the front one
    was unreadable and unsettable. describe_all only ever saw the
    visible page and reported it as the whole dialog, which reads as a
    dialog that simply lacks the control you wanted.
    """
    out = []
    for control in getattr(window, "controls", []):
        node = _acc(control.hwnd)
        if node is None:
            continue
        for child in _child_objects(node):
            try:
                role = int(child.accRole(_self()))
            except COMError:
                continue
            if role != _ROLE_PAGETAB:
                continue
            try:
                name = child.accName(_self())
            except COMError:
                name = None
            if name:
                out.append({"name": str(name), "node": child,
                            "selected": _state_has(child, _STATE_SELECTED)})
    return out


def select_tab(window, name: str, settle: float = 0.6) -> dict:
    """Switch to a tab page by name, and verify it is the selected one."""
    def flat(text):
        return plain(text).replace("&", "").strip().lower()

    tabs = list_tabs(window)
    if not tabs:
        return {"ok": False, "reason": "that dialog exposes no tab pages"}

    match = next((t for t in tabs if flat(t["name"]) == flat(name)), None)
    if match is None:
        return {"ok": False, "reason": f"no tab named {name!r}",
                "offered": [t["name"] for t in tabs]}
    if match["selected"]:
        return {"ok": True, "tab": match["name"], "changed": False,
                "note": "already the front page"}

    node = match["node"]
    for how in ("default_action", "accSelect", "click"):
        if how == "default_action":
            acted = _default_action(node)
        elif how == "accSelect":
            try:
                node.accSelect(3, _self())
                acted = True
            except COMError:
                acted = False
        else:
            acted = _click_node(node, window.hwnd)
        if not acted:
            continue
        win.wait_until(lambda: _state_has(node, _STATE_SELECTED), settle)
        if _state_has(node, _STATE_SELECTED):
            return {"ok": True, "tab": match["name"], "changed": True,
                    "how": how}

    return {"ok": False, "tab": match["name"],
            "reason": "clicked the tab and it does not report as selected"}


def scroll(hwnd: int, lines: int = 3, settle: float = 0.2) -> dict:
    """Scroll a list, grid or tree by wheel notches.

    A LONG LIST HIDES ITS OWN CONTENTS. list_rows returns what the
    control exposes, and for a virtualised DevExpress grid that is
    roughly what is on screen, so an item further down did not exist as
    far as a caller was concerned. Scrolling is how it comes into view.

    Positive scrolls down, negative up. The row count is reported before
    and after so a caller can tell movement from the end of the list.
    """
    node = _acc(hwnd)
    if node is None:
        return {"ok": False, "reason": "that control exposes no tree"}

    rect = _node_rect(node)
    if rect is None:
        return {"ok": False, "reason": "that control has no screen rectangle"}

    before = [r["name"] for r in list_rows(hwnd)]
    left, top, width, height = rect
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    origin = (point.x, point.y)
    win.require_foreground(hwnd, "a scroll")
    user32.SetCursorPos(left + width // 2, top + height // 2)
    # Wheel notches are 120 units, negative for down. Re-checked after
    # the move, like every other synthesised event here.
    try:
        win.require_foreground(hwnd, "a scroll")
    except win.ForegroundLost:
        user32.SetCursorPos(*origin)
        raise
    user32.mouse_event(0x0800, 0, 0, -120 * int(lines), 0)
    user32.SetCursorPos(*origin)

    win.wait_until(lambda: [r["name"] for r in list_rows(hwnd)] != before,
                   settle)
    after = [r["name"] for r in list_rows(hwnd)]
    return {"ok": True, "moved": after != before,
            "rows_before": len(before), "rows_after": len(after),
            "rows": after[:60],
            "note": ("moved is false at the end of a list, which is a "
                     "real answer rather than a failure")}


def activate_row(hwnd: int, name: str, double: bool = False,
                 settle: float = 0.6) -> dict:
    """Invoke a row, rather than merely selecting it.

    select_row highlights; this ACTS. In a panel that is the difference
    between pointing at a document and opening it, and there was no way
    to do the second.
    """
    def flat(text):
        return plain(text).strip().lower()

    rows = list_rows(hwnd)
    match = next((r for r in rows if flat(r["name"]) == flat(name)), None)
    if match is None:
        return {"ok": False, "reason": f"no row named {name!r}",
                "offered": [r["name"] for r in rows][:60]}

    node = match["node"]
    if not double and _default_action(node):
        return {"ok": True, "row": match["name"], "how": "default_action"}

    rect = _node_rect(node)
    if rect is None:
        return {"ok": False, "row": match["name"],
                "reason": "that row has no screen rectangle"}
    left, top, width, height = rect
    x, y = left + width // 2, top + height // 2
    _real_click(hwnd, x, y)
    if double:
        _real_click(hwnd, x, y)
    return {"ok": True, "row": match["name"],
            "how": "double click" if double else "click",
            "note": ("a click has no read-back here; the effect is "
                     "whatever the panel does with it")}
