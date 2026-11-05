# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Invoke ANY Altium menu command, by path, when nothing scriptable will.

THIS IS THE ONE PLACE THAT SYNTHESISES REAL INPUT. Everywhere else in
this package addresses messages to a specific window handle, so nothing
depends on focus, z-order or where the pointer is. That property cannot
be kept here, and every step below exists because the safer one was
tried against a live Altium and did nothing:

  application.execute_menu     reports success and invokes nothing, for
                               MAPPED paths too. RunProcess silently
                               ignores ids it does not know and cannot
                               report failure (task #83)
  GetMenu on the main frame    returns 0. Altium draws menus with
                               DevExpress bars: no menu ids, no
                               WM_COMMAND route
  accDoDefaultAction           returns cleanly and raises no popup, on
                               bar items AND on submenu items. The bars
                               expose MSAA READ-ONLY: names and
                               rectangles yes, actions no
  posted WM_LBUTTONDOWN/UP     ignored by the bar, unlike Altium's
                               TXPBitBtn which accepts it
  a REAL click                 WORKS, at every level. The popup arrives
                               as a TxpBarSubMenuControl whose items ARE
                               exposed accessibly, so each level is
                               found BY NAME and clicked at its own
                               rectangle

Names, never positions: entries move with context and separators are
not exposed at all, so an index would select the wrong thing the first
time a menu changed.

BECAUSE IT STEALS FOCUS, IT IS NOT A DEFAULT ANYWHERE. A caller asks for
it explicitly, and should not while the user is typing.

EVERY EVENT HERE IS GUARDED. A click or keystroke is delivered to
whatever is active when it fires, so windows.require_foreground runs
immediately before each one and coordinates are confined to Altium's own
windows. Turn the whole thing off with EDA_AGENT_UI_AUTOMATION=0. The
risks and the reasons are in docs/ui-automation.md.

ONE THING IT CANNOT DO SAFELY. Altium's bar carries entries that are
COMMANDS rather than menus, and nothing tells them apart: measured
across all 17, identical MSAA state including HASPOPUP, identical
accDefaultAction, identical UIA control type. Listing such an entry
clicks it, and clicking it runs it.
"""

from __future__ import annotations

import ctypes
import functools
import time
from ctypes import POINTER, byref, c_long, wintypes

from . import altium_locale
from . import windows as win
from .captions import plain

try:                                            # pragma: no cover - platform
    import comtypes
    import comtypes.client
    from comtypes import COMError
    from comtypes.automation import VARIANT

    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen.Accessibility import IAccessible

    import win32gui
    _AVAILABLE = True
except Exception:                               # pragma: no cover - platform
    _AVAILABLE = False

_OBJID_CLIENT = 0xFFFFFFFC
_VK_ESC = 0x1B
_KEYUP = 2
_SW_RESTORE = 9
_MOUSE_DOWN, _MOUSE_UP = 0x0002, 0x0004
_RIGHT_DOWN, _RIGHT_UP = 0x0008, 0x0010
_FRAME_CLASS = "TDocumentForm"


class MenuBarUnavailable(RuntimeError):
    """The menu bar cannot be read in the editor's current state."""


def available() -> bool:
    """Whether menus can be driven at all on this host."""
    return _AVAILABLE and win.available()


def _u():
    return ctypes.windll.user32


def _acc(hwnd):
    ptr = POINTER(IAccessible)()
    ctypes.oledll.oleacc.AccessibleObjectFromWindow(
        ctypes.c_void_p(hwnd), ctypes.c_ulong(_OBJID_CLIENT),
        byref(IAccessible._iid_), byref(ptr))
    return ptr.QueryInterface(IAccessible)


def _self(child: int = 0):
    var = VARIANT()
    var.vt = 3
    var.value = child
    return var


def _children(node) -> list:
    try:
        count = node.accChildCount
    except COMError:
        return []
    if not count:
        return []
    arr = (VARIANT * count)()
    got = c_long()
    try:
        ctypes.oledll.oleacc.AccessibleChildren(node, 0, count, arr,
                                                byref(got))
    except Exception:                            # pragma: no cover - guard
        return []
    out = []
    for i in range(got.value):
        if arr[i].vt == 9 and arr[i].value:
            try:
                out.append(arr[i].value.QueryInterface(IAccessible))
            except COMError:
                pass
    return out


def _name(node) -> str:
    try:
        return (node.accName(_self()) or "").strip()
    except COMError:
        return ""


def _same(a: str, b: str) -> bool:
    """Compare captions ignoring the accelerator marker and an ellipsis.

    A caller should not have to know that Altium writes "&Rules..." when
    the menu shows "Rules".
    """
    def flat(text):
        return (plain(text).replace("&", "").strip()
                .rstrip(".").strip().lower())
    if flat(a) == flat(b):
        return True
    # On a translated Altium the caller still names the English caption,
    # and the bar shows Altium's own translation of it.
    if _UI_LANGUAGE in (None, "en"):
        return False
    return altium_locale.plain_caption(a) in _local_names(_UI_LANGUAGE, b)


#: The language Altium's menu bar was last read in; set by bar_items.
_UI_LANGUAGE: str | None = None


@functools.lru_cache(maxsize=512)
def _local_names(language: str, english: str) -> frozenset:
    found = altium_locale.translations(english).get(language, set())
    return frozenset(altium_locale.plain_caption(t) for t in found)


def ui_language() -> str | None:
    """The language Altium's menus were last seen in, or None if unread."""
    return _UI_LANGUAGE


def _name_at(x: int, y: int) -> str:
    """The accessible name of whatever is under screen point (x, y), or ''.

    '' also means it could not be read, so a caller may only refuse on a
    name that is present and different, never on an empty one.
    """
    try:
        ptr = POINTER(IAccessible)()
        child = VARIANT()
        ctypes.oledll.oleacc.AccessibleObjectFromPoint(
            wintypes.POINT(int(x), int(y)), byref(ptr), byref(child))
        return (ptr.accName(child) or "").strip()
    except Exception:                            # pragma: no cover - guard
        return ""


def _visible_rect(node):
    try:
        rect = node.accLocation(_self())
    except COMError:
        return None
    x, _y, width, height = rect
    if width <= 0 or height <= 0 or x < -10000:
        return None
    return rect


#: How often the menu polls for the UI to catch up. It replaced fixed
#: sleeps of 0.2 and 0.25, which were the granularity of every wait in
#: the walk and therefore the floor on how fast a menu click could be.
#: Each poll is an accessible-tree read, so this trades a little CPU for
#: latency that the caller feels directly.
_POLL = 0.05


def _tap(pid: int, vk: int) -> None:
    """One keystroke, ONLY while Altium owns the foreground.

    Checked before the press and again before the release: a key held
    down while focus moves releases into another application.
    """
    target = frame(pid)
    hwnd = target.hwnd if target is not None else 0
    win.require_foreground(hwnd, "a keystroke")
    _u().keybd_event(vk, 0, 0, 0)
    time.sleep(0.08)
    win.require_foreground(hwnd, "a key release")
    _u().keybd_event(vk, 0, _KEYUP, 0)


def _click(pid: int, x: int, y: int, after: float = 0.3, expect: str = "",
           until=None, wait: float = 1.0) -> str:
    """A real click, with the pointer returned to where the user left it.

    Returns '' when the click went in, else why it did not. With
    ``expect``, the item under the pointer must carry that name before
    the press; a different name refuses.

    THE POINTER STAYS PUT UNTIL THE CLICK HAS TAKEN. The release is
    queued input, delivered where the pointer is when Windows gets to
    it. Put back at once, the release could land on whatever item lay
    under the user's pointer, and a menu path ran a neighbouring command
    while reporting the one asked for. The pointer now waits on
    ``until`` (the menu opening, or closing) or, without one, a short
    floor, and only then goes back.

    THE THREE SLEEPS HERE WERE 0.58s OF EVERY CLICK, and a menu path pays
    one per level. Two of them are now waits on the thing they were
    covering for:

    * before the press, that the pointer has actually arrived. Windows
      moves the cursor synchronously, so this is normally already true.
    * after the release, that the UI has something to show for it. What
      "something" means depends on the caller, so the caller passes it;
      with no condition this keeps the old fixed pause.

    The hold between down and up stays a real sleep. It is not waiting
    for anything observable, it is making the press long enough for the
    target to register as a click rather than a twitch.
    """
    target = frame(pid)
    hwnd = target.hwnd if target is not None else 0
    win.require_foreground(hwnd, "a click")

    user32 = _u()
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    origin = (point.x, point.y)
    user32.SetCursorPos(x, y)

    def _arrived() -> bool:
        here = wintypes.POINT()
        user32.GetCursorPos(byref(here))
        return abs(here.x - x) <= 2 and abs(here.y - y) <= 2

    win.wait_until(_arrived, 0.2)
    if expect:
        under = _name_at(x, y)
        if under and not _same(under, expect):
            user32.SetCursorPos(*origin)
            return f"the pointer is over {under!r}, not {expect!r}"
    # Re-checked after the pointer move: moving the cursor over another
    # application can raise it, and the press below would then land
    # there. The cursor is restored in the finally so a refusal here
    # does not leave the pointer parked on the menu bar.
    try:
        win.require_foreground(hwnd, "a mouse press")
        user32.mouse_event(_MOUSE_DOWN, 0, 0, 0, 0)
        time.sleep(0.08)
        user32.mouse_event(_MOUSE_UP, 0, 0, 0, 0)
    except win.ForegroundLost:
        user32.SetCursorPos(*origin)
        raise
    if until is not None:
        win.wait_until(until, wait)
    else:
        time.sleep(max(after, 0.15))
    user32.SetCursorPos(*origin)
    return ""


def _click_node(pid: int, node, after: float = 0.3) -> bool:
    rect = _visible_rect(node)
    if rect is None:
        return False
    x, y, width, height = rect
    _click(pid, x + width // 2, y + height // 2, after=after)
    return True


def _press(pid: int, node, wanted: str, until) -> str:
    """Click a menu entry checked to be ``wanted``: '' or why not.

    The rectangle is read twice and must agree, so an entry still being
    laid out is not clicked at a position it is leaving."""
    rect = _visible_rect(node)
    if rect is None or not win.wait_until(lambda: _visible_rect(node) == rect, 0.3):
        return f"{wanted!r} has no settled clickable rectangle"
    x, y, width, height = rect
    return _click(pid, x + width // 2, y + height // 2, expect=wanted,
                  until=until, wait=1.0)


def frame(pid: int):
    """Altium's main document frame, or None.

    A HIDDEN frame is shown again and returned. Altium's main window has
    been found hidden (not minimized) while the bridge kept answering;
    the bridge re-shows it after each reply, but a menu walk does not go
    through the bridge, and app_update_from_libraries then failed with
    "no Altium main window" on an Altium that was running.
    """
    for window in win.enumerate_windows(pid=pid):
        if window.class_name == _FRAME_CLASS:
            return window
    for window in win.enumerate_windows(pid=pid, visible_only=False):
        if window.class_name == _FRAME_CLASS:
            win.show_without_focus(window.hwnd)
            win.wait_until(lambda: bool(win32gui.IsWindowVisible(window.hwnd)), 1.0)
            return window
    return None


def _submenus(pid: int) -> list:
    return [x for x in win.enumerate_windows(pid=pid, visible_only=False)
            if "submenu" in x.class_name.lower()
            and win32gui.IsWindowVisible(x.hwnd)]


def close_open_menu(pid: int) -> None:
    """Escape any menu left dropped, so the next run starts clean."""
    for _ in range(3):
        if not _submenus(pid):
            return
        _tap(pid, _VK_ESC)
        # The popup going away is the condition. Escape usually closes it
        # in well under the 0.4s this used to spend on every attempt, and
        # this runs before every menu click.
        win.wait_until(lambda: not _submenus(pid), 0.4)


def bar_items(pid: int, timeout: float = 6.0) -> dict:
    """Top-level menu names currently ON SCREEN, mapped to their node.

    Altium's menu bar is per editor context and only the active one is
    laid out: with a board focused the schematic bar still exists but
    every item reports a rectangle far off screen. Filtering on a
    visible rectangle is what picks the live bar.

    POLLED, not read once. The bar takes a moment to lay out after the
    window is restored or the focused document changes, and a single
    read during that window returns nothing, which is indistinguishable
    from an editor that has no menus. Measured: empty immediately after
    a restore, populated a moment later.
    """
    deadline = time.monotonic() + timeout
    interval = _POLL
    while True:
        found = _bar_items_once(pid)
        if found:
            global _UI_LANGUAGE
            _UI_LANGUAGE = altium_locale.language_of(found.keys())
            return found
        if time.monotonic() >= deadline:
            # Empty after polling is not "this editor has no menus".
            # MEASURED, straight after a restore: the bar windows exist
            # with sensible rectangles and are still IsWindowVisible
            # false, and Altium had recreated some of them under new
            # handles. Activating fixed it.
            #
            # NOT always, though, and the refusal below is worded for
            # what was actually seen rather than as a rule. A frame that
            # has been activated once reads its bars fine after a later
            # restore with the focus left elsewhere, so activation is
            # not a precondition of reading in general. It is the fix
            # for THIS case, where the read already came back empty.
            target = frame(pid)
            if target is not None and _u().GetForegroundWindow() != target.hwnd:
                raise MenuBarUnavailable(
                    "the menu bar is not laid out because Altium is not "
                    "the active window. Altium builds its bars on "
                    "activation, so the window has to be brought to the "
                    "front first, which steals focus")
            # Foreground and still nothing. Whatever the cause, a
            # running Altium HAS menus, so reporting an empty bar would
            # be a false clean of the kind this project keeps finding.
            raise MenuBarUnavailable(
                "no menu bar could be read even though Altium is the "
                "active window. Its bars exist but report no visible "
                "items, which usually clears after the editor finishes "
                "rebuilding them")
        time.sleep(interval)
        if interval < 0.25:
            interval = min(0.25, interval * 2)


def _bar_items_once(pid: int) -> dict:
    target = frame(pid)
    if target is None:
        return {}
    if _u().IsIconic(target.hwnd):
        # A minimized frame parks every child at -32000, so the visible
        # rectangle test below rejects everything and this returns
        # nothing. Returning an empty menu bar for a running editor is a
        # lie, and it is the kind that reads as "this editor has no
        # menus" rather than "look again once it is on screen".
        raise MenuBarUnavailable(
            "the Altium window is minimized, so its menu bar is not laid "
            "out and no item can be located or clicked")
    bars: list = []

    def visit(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:                        # pragma: no cover - races
            return True
        if "clientbarcontrol" in cls.lower() and win32gui.IsWindowVisible(hwnd):
            bars.append(hwnd)
        return True

    win32gui.EnumChildWindows(target.hwnd, visit, None)

    # Only the MENU bar, not every bar on screen. Altium's toolbars are
    # the same control class and expose their buttons the same way, so
    # scraping them all returned fifty-odd names including Copy, Cut and
    # Arc (Edge), and took fifteen seconds. The menu bar is identified
    # by carrying both File and Help, which no toolbar does.
    for hwnd in bars:
        try:
            node = _acc(hwnd)
        except Exception:                        # pragma: no cover - guard
            continue
        items = {}
        for item in _children(node):
            caption = _name(item)
            if caption and _visible_rect(item) is not None:
                items.setdefault(caption.replace("&", ""), item)
        lowered = {k.lower() for k in items}
        if "file" in lowered and "help" in lowered:
            return items
    return {}


def _open_items(pid: int, timeout: float = 6.0) -> list:
    """Items of whatever popups are open, WAITING for them to populate.

    A menu window exists before its entries do. A fixed sleep read it
    too early and reported an empty menu, which is indistinguishable
    from a menu that genuinely lacks the item: measured, the same path
    failed once and worked on the retry. Polling removes the race and
    is faster than a sleep long enough to be safe.
    """
    deadline = time.monotonic() + timeout
    interval = _POLL
    while True:
        out = []
        for popup in _submenus(pid):
            try:
                out.extend(_children(_acc(popup.hwnd)))
            except Exception:                    # pragma: no cover - guard
                pass
        if out or time.monotonic() >= deadline:
            return out
        # Same backoff as win.wait_until, and for the same reason: each
        # turn of this loop enumerates windows and walks an accessible
        # tree, so a flat fast poll is only affordable while the answer
        # is likely to arrive imminently.
        time.sleep(interval)
        if interval < 0.25:
            interval = min(0.25, interval * 2)


def _flat_title(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def click_path(pid: int, path: str, settle: float = 1.2, expect_dialog: str = "",
               expect_timeout: float = 10.0) -> dict:
    """Invoke a menu command by path, e.g. ``Design|Rules...``.

    Each level is matched by NAME against what is actually on screen and
    clicked at its own rectangle. Nested submenus work by repetition:
    every level after the first is looked for among whatever popups are
    open at that moment.

    Args:
        pid: the Altium process.
        path: pipe-separated, top level first. Accelerator markers and a
            trailing ellipsis are ignored, so "Design|Rules" and
            "&Design|Rules..." both work.
        settle: pause after each click for the next level to be built.
        expect_dialog: part of the title of the dialog the command opens.
            The reply is ok only when such a dialog appears within
            ``expect_timeout``; another dialog, or none, is a failure
            that names what did open.

    Returns:
        Dict with ``ok`` and ``opened``, the titles of dialogs that
        appeared; on failure ``reason`` and ``offered``, which lists
        what that level actually contained. That list is what turns a
        wrong caption into a one-line correction.
    """
    if not available():
        return {"ok": False, "reason": (
            "menu driving needs pywin32 and comtypes on Windows")}

    # COM is per THREAD. comtypes initialises the thread that imports
    # it, and callers run this in an executor because it sleeps between
    # clicks. Without this every accessible call on the worker fails and
    # a real menu reads as EMPTY rather than as broken, which is exactly
    # how it first presented.
    try:
        comtypes.CoInitialize()
        owned = True
    except Exception:                            # pragma: no cover - guard
        owned = False
    try:
        before = {d.hwnd for d in win.dialogs(pid)}
        out = _click_path_read_twice(pid, path, settle)
        if not out.get("ok"):
            return out

        def fresh() -> list:
            return [d.title for d in win.dialogs(pid) if d.hwnd not in before]

        if expect_dialog:
            want = _flat_title(expect_dialog)
            win.wait_until(lambda: any(want in _flat_title(t) for t in fresh()),
                           expect_timeout)
        out["opened"] = fresh()
        if expect_dialog and not any(_flat_title(expect_dialog) in _flat_title(t)
                                     for t in out["opened"]):
            out.update(ok=False, opened_instead=out["opened"], reason=(
                f"clicked {out['path']!r}, but {out['opened'] or 'no dialog'} "
                f"opened instead of {expect_dialog!r}"))
        return out
    finally:
        if owned:
            try:
                comtypes.CoUninitialize()
            except Exception:                    # pragma: no cover - guard
                pass


def bring_to_front(pid: int) -> bool:
    """Restore and activate Altium so its menu bar can be used.

    A MINIMIZED FRAME HAS NO BARS AT ALL: measured, 35 bar windows
    before minimizing and 0 while minimized. Restoring brings them
    back, and a click additionally needs the activation, because
    Windows delivers a synthesised click to whatever is foreground
    rather than to the window it was aimed at.

    Restoring alone is enough to READ, on a frame that has been
    activated at least once. Measured with another application
    deliberately left in front: shown with SW_SHOWNOACTIVATE, the bar
    returned all 17 menus with Altium still in the background, and the
    same 35 windows and 14 visible ones as before. Some of them come
    back under NEW HANDLES, so nothing here may cache a bar handle
    across a minimize.

    The never-activated frame is a different case and is NOT covered by
    that measurement: see _bar_items, which raises rather than reporting
    an editor with no menus.

    Both steps are done here regardless. Splitting them would offer a
    read-only variant whose only gain is not stealing focus, and every
    caller of this either clicks now or is about to.

    Returns False when there is no main window to activate.
    """
    target = frame(pid)
    if target is None:
        return False

    user32 = _u()
    if user32.IsIconic(target.hwnd):
        # A minimized frame parks every child at -32000, where nothing
        # can be located or clicked. Left restored afterwards: putting
        # it back would race whatever dialog the command opens.
        #
        # Waited for rather than slept through. The restore animation is
        # what the old 1.2s was covering, and IsIconic going false is the
        # thing that actually matters.
        user32.ShowWindow(target.hwnd, _SW_RESTORE)
        win.wait_until(lambda: not user32.IsIconic(target.hwnd), 1.2)

    close_open_menu(pid)
    user32.SetForegroundWindow(target.hwnd)
    # THE COMMON CASE IS THAT ALTIUM IS ALREADY IN FRONT, and this used
    # to cost 0.8s regardless. The same 0.8s is still allowed for the
    # case where it is not.
    win.wait_until(lambda: user32.GetForegroundWindow() == target.hwnd, 0.8)
    return True


def _click_path_read_twice(pid: int, path, settle: float) -> dict:
    """``_click_path``, walked once more when a level's items read wrong.

    MEASURED on AD 26.10.1.6: the first read of Tools came back "a menu
    opened but exposed no items" one time in six. Reported on AD 25:
    asked for Tools, the items read were the Design menu's about one time
    in four, and the same call worked straight after. Nothing here can
    tell which menu a popup belongs to, so "not found" is retried too.

    Both failures happen before the last entry is pressed, so no command
    has run and the walk can be made again. Once only, and when both
    reads fail the reply carries both.
    """
    first = _click_path(pid, path, settle)
    if first.get("ok") or not first.pop("_read_failed", False):
        return first
    close_open_menu(pid)
    second = _click_path(pid, path, settle)
    second.pop("_read_failed", None)
    second["retried"] = True
    second["first_read"] = {"reason": first.get("reason"),
                            "offered": first.get("offered", [])}
    if not second.get("ok"):
        second["reason"] = (f"{second.get('reason')}. Read twice; the first "
                            f"read said: {first.get('reason')}")
    return second


def _click_path(pid: int, path, settle: float) -> dict:
    levels = _levels(path)
    if not levels:
        return {"ok": False, "reason": "menu_path is empty"}

    if not bring_to_front(pid):
        return {"ok": False, "reason": "no Altium main window"}

    user32 = _u()
    try:
        top = bar_items(pid)
    except MenuBarUnavailable as exc:
        return {"ok": False, "reason": str(exc)}
    levels = _resolve_top(top, levels, path)
    match = next((node for caption, node in top.items()
                  if _same(caption, levels[0])), None)
    if match is None:
        return {"ok": False,
                "reason": (f"{levels[0]!r} is not on the menu bar. Altium's "
                           f"bar is per editor, so the matching document "
                           f"kind has to be focused first"),
                "offered": sorted(top)}

    def _opened(before: set):
        return lambda: any(w.hwnd not in before and _items_of(w.hwnd)
                           for w in _submenus(pid))

    def _closed():
        return not _submenus(pid)

    before = {w.hwnd for w in _submenus(pid)}
    why = _press(pid, match, levels[0],
                 _opened(before) if len(levels) > 1 else _closed)
    if why:
        close_open_menu(pid)
        return {"ok": False, "reason": why}

    for depth, wanted in enumerate(levels[1:], start=1):
        # Only the popup this level opened is searched. Every open popup
        # used to be read together, the parent's entries included, and
        # the first name match was taken.
        popup = _newest_popup(pid, before)
        entries = _items_of(popup) if popup else []
        if not entries:
            close_open_menu(pid)
            return {"ok": False, "reason": (
                f"a menu opened but exposed no items, so {wanted!r} could "
                f"not be looked for. The accessible layer returned "
                f"nothing, which usually means COM is not initialised on "
                f"this thread"), "offered": [], "_read_failed": True}

        hits = [e for e in entries if _same(_name(e), wanted)]
        if not hits:
            offered = [_name(e) for e in entries]
            close_open_menu(pid)
            return {"ok": False,
                    "reason": f"{wanted!r} is not in {levels[depth - 1]!r}",
                    "offered": [o for o in offered if o],
                    "_read_failed": True}
        if len({_visible_rect(h) for h in hits}) > 1:
            close_open_menu(pid)
            return {"ok": False, "reason": (
                f"{levels[depth - 1]!r} has {len(hits)} entries named "
                f"{wanted!r}, so which one to run is a guess"),
                "offered": [_name(e) for e in entries if _name(e)]}
        final = depth == len(levels) - 1
        before = {w.hwnd for w in _submenus(pid)}
        why = _press(pid, hits[0], wanted, _closed if final else _opened(before))
        if why:
            close_open_menu(pid)
            return {"ok": False, "reason": why}

    # A command runs when its entry is clicked and the menu goes away. A
    # menu still open means the click opened a submenu, or did not land,
    # and in neither case did a command run.
    win.wait_until(_closed, settle)
    if not _closed():
        close_open_menu(pid)
        return {"ok": False, "reason": (
            f"the menu stayed open after {levels[-1]!r} was clicked, so no "
            f"command ran. A path must end at a command, not a submenu")}
    return {"ok": True, "path": "|".join(levels)}


def open_item(pid: int, menu_key: str, item_name: str) -> dict:
    """Back-compat shim: two-level path with the top level named by key.

    Superseded by ``click_path``, which takes the whole path by name and
    needs no accelerator letter.
    """
    top = {"t": "Tools", "d": "Design", "p": "Place", "r": "Reports"}
    return click_path(pid, f"{top.get(menu_key.lower(), menu_key)}|"
                           f"{item_name}")


def list_path(pid: int, path: str = "", settle: float = 1.2) -> dict:
    """List what a menu level contains, without invoking a command.

    ``list_path(pid)`` gives the menu bar. ``list_path(pid, "Tools")``
    gives the Tools menu, ``list_path(pid, "Tools|Annotation")`` the
    Annotation submenu. This exists because the only way to discover a
    submenu used to be to click a deliberately wrong leaf and read the
    ``offered`` list off the failure, which is not something a caller
    should have to invent.

    CAVEAT, and it is a real one: opening a submenu means clicking the
    entries that lead to it, so every level NAMED here must be a
    submenu. Naming a command runs it. The menu bar and one level down
    are always safe to ask for, and from there the returned names tell
    you what is a container before you descend.
    """
    if not available():
        return {"ok": False, "reason": (
            "menu driving needs pywin32 and comtypes on Windows")}

    try:
        comtypes.CoInitialize()
        owned = True
    except Exception:                            # pragma: no cover - guard
        owned = False
    try:
        return _list_path(pid, path, settle)
    finally:
        if owned:
            try:
                comtypes.CoUninitialize()
            except Exception:                    # pragma: no cover - guard
                pass


def _levels(path) -> list:
    """Path levels. Accepts a LIST so a caption may contain the separator.

    Altium has a bar item spelled 'Symbols | Footprints | 3D Models'.
    Split on '|' that became three levels, none of which exists, so the
    item was unaddressable by name and the error blamed the user for a
    menu called 'Symbols'. A list states the levels outright and is the
    only unambiguous form when a caption contains a pipe.

    Strings still split, because every other caller passes one and
    'Tools|Annotation' has to keep working. _resolve_top recovers the
    string case where it can.
    """
    if isinstance(path, (list, tuple)):
        return [str(p).strip() for p in path if str(p).strip()]
    return [p.strip() for p in str(path or "").split("|") if p.strip()]


def _resolve_top(top: dict, levels: list, raw) -> list:
    """Levels, with a split-up bar caption put back together.

    Only rewrites when the split FAILED and the whole unsplit string is
    a real bar item, so an ordinary 'Tools|Annotation' is never touched.
    A pipe inside a DEEPER caption cannot be recovered this way and
    needs the list form.
    """
    if not levels or isinstance(raw, (list, tuple)):
        return levels
    if any(_same(caption, levels[0]) for caption in top):
        return levels
    whole = str(raw or "").strip()
    if whole and any(_same(caption, whole) for caption in top):
        return [whole]
    return levels


def _list_path(pid: int, path, settle: float) -> dict:
    levels = _levels(path)

    if not bring_to_front(pid):
        return {"ok": False, "reason": "no Altium main window"}

    try:
        top = bar_items(pid)
    except MenuBarUnavailable as exc:
        return {"ok": False, "reason": str(exc)}

    if not levels:
        return {"ok": True, "path": "", "items": sorted(top), "note": (
            "the menu bar is per editor: focus a schematic or a board "
            "to see that editor's menus")}

    levels = _resolve_top(top, levels, path)
    match = next((node for caption, node in top.items()
                  if _same(caption, levels[0])), None)
    if match is None:
        return {"ok": False,
                "reason": f"{levels[0]!r} is not on the menu bar",
                "offered": sorted(top)}

    try:
        before = {w.hwnd for w in _submenus(pid)}
        if not _click_node(pid, match, after=0.0):
            return {"ok": False,
                    "reason": f"{levels[0]!r} has no clickable rectangle"}
        win.wait_until(lambda: bool({w.hwnd for w in _submenus(pid)} - before),
                       settle)
        opened = _newest_popup(pid, before)

        # Opening a submenu leaves the PARENT popup on screen too, and
        # the accessible layer reports every open popup together. So
        # each level below is clicked with the current popup handles
        # recorded first, and only the popup that appeared is read back.
        # Reading them merged returned Tools' own entries alongside
        # Annotation's, which reads as one flat menu that does not exist.

        for depth, wanted in enumerate(levels[1:], start=1):
            entries = _items_of(opened) if opened else _open_items(pid)
            hit = next((e for e in entries if _same(_name(e), wanted)), None)
            if hit is None:
                return {"ok": False,
                        "reason": (f"{wanted!r} is not in "
                                   f"{levels[depth - 1]!r}"),
                        "offered": [n for n in (_name(e) for e in entries)
                                    if n]}
            before = {w.hwnd for w in _submenus(pid)}
            if not _click_node(pid, hit, after=0.0):
                return {"ok": False,
                        "reason": f"{wanted!r} has no clickable rectangle"}
            win.wait_until(
                lambda: bool({w.hwnd for w in _submenus(pid)} - before), settle)
            opened = _newest_popup(pid, before) or opened

        entries = _items_of(opened) if opened else _open_items(pid)
        items = [n for n in (_name(e) for e in entries) if n]

        # A popup can be on screen and still expose no NAMED children,
        # in which case reading only the newest one yields nothing. The
        # merged read across every open popup is less precise, but a
        # slightly over-broad list beats an empty one.
        if not items:
            items = [n for n in (_name(e) for e in _open_items(pid)) if n]

        # EMPTY IS NOT A SUCCESS. This used to return ok true with
        # items [], so a caller asking what a menu contains was told
        # the call worked and handed nothing, with no way to tell that
        # apart from a genuinely empty menu. MEASURED: "File" with
        # may_steal_focus on returned ok true and no items on three
        # consecutive calls with a PcbLib focused.
        if not items:
            return {"ok": False, "path": "|".join(levels), "items": [],
                    "reason": (
                        f"{'|'.join(levels)!r} opened but exposed no readable "
                        f"entries. Altium builds its menus on activation and "
                        f"the bar is per editor, so this usually means the "
                        f"menu belongs to a different editor context than the "
                        f"focused document, or the popup had not rendered "
                        f"yet. Focus the matching document and retry.")}

        return {"ok": True, "path": "|".join(levels), "items": items}
    finally:
        # Always put the menu away. A popup left open swallows the next
        # click, so a discovery call must not change what the editor
        # does next.
        close_open_menu(pid)


def _items_of(hwnd: int) -> list:
    """Accessible children of ONE popup window."""
    try:
        return _children(_acc(hwnd))
    except Exception:                            # pragma: no cover - guard
        return []


def _newest_popup(pid: int, before: set, timeout: float = 6.0):
    """The popup handle that appeared since ``before`` was taken.

    Returns None when nothing new opened, which is how a caller learns
    that the entry it clicked was a command rather than a submenu.
    """
    deadline = time.monotonic() + timeout
    interval = _POLL
    while True:
        fresh = [w.hwnd for w in _submenus(pid) if w.hwnd not in before]
        # A popup window exists before its items do, so an empty one is
        # not yet the answer.
        for hwnd in fresh:
            if _items_of(hwnd):
                return hwnd
        if time.monotonic() >= deadline:
            return fresh[0] if fresh else None
        time.sleep(interval)
        if interval < 0.25:
            interval = min(0.25, interval * 2)


def _right_click(pid: int, x: int, y: int) -> None:
    """A real right click, pointer returned to where the user left it.

    The same shape as the left click above and for the same reason: the
    DevExpress bars answer a real click and nothing else, and a context
    menu is drawn by the same framework.
    """
    target = frame(pid)
    hwnd = target.hwnd if target is not None else 0
    win.require_foreground(hwnd, "a right click")
    # A right click opens a context menu wherever it lands, so the point
    # has to be over Altium and not merely aimed while Altium is active.
    win.require_point_in_app(int(x), int(y), hwnd, "a right click")
    user32 = _u()
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    origin = (point.x, point.y)
    user32.SetCursorPos(x, y)

    def _arrived() -> bool:
        here = wintypes.POINT()
        user32.GetCursorPos(byref(here))
        return abs(here.x - x) <= 2 and abs(here.y - y) <= 2

    win.wait_until(_arrived, 0.2)
    try:
        win.require_foreground(hwnd, "a right click")
    except win.ForegroundLost:
        user32.SetCursorPos(*origin)
        raise
    user32.mouse_event(_RIGHT_DOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    win.require_foreground(hwnd, "a right release")
    user32.mouse_event(_RIGHT_UP, 0, 0, 0, 0)
    user32.SetCursorPos(*origin)


def context_menu(pid: int, x: int, y: int, settle: float = 1.2) -> dict:
    """Right click at a point and report what the context menu offers.

    CONTEXT MENUS WERE ENTIRELY UNREACHABLE, and in Altium they carry a
    great deal that no top menu duplicates: what you can do to a
    component, a net, a polygon, a row in a panel. Automating the menu
    bar while ignoring these leaves most of the editor unavailable.

    Nothing is invoked here. A right click opens the menu and this reads
    it, so a caller can see what is on offer before choosing. Use
    ``context_click`` to pick one.

    The menu is CLOSED again afterwards. Leaving a popup dropped blocks
    the next operation and looks to a user like the editor has hung.
    """
    if not bring_to_front(pid):
        return {"ok": False, "reason": "no Altium main window"}

    close_open_menu(pid)
    _right_click(pid, int(x), int(y))
    entries = _open_items(pid, timeout=settle * 2)
    items = [n for n in (_name(e) for e in entries) if n]
    close_open_menu(pid)

    if not items:
        return {"ok": False, "at": [int(x), int(y)], "reason": (
            "nothing opened, or it opened and exposed no items. Not "
            "everything in Altium has a context menu, and a right click "
            "on empty canvas often has none")}
    return {"ok": True, "at": [int(x), int(y)], "count": len(items),
            "items": items}


def context_click(pid: int, x: int, y: int, item: str,
                  settle: float = 1.2) -> dict:
    """Right click at a point and choose one entry by name.

    Nested entries work like the menu bar: pass the path with '|' and
    each level is matched by name against what is on screen. Pass a LIST
    instead when an entry's own caption contains a pipe.
    """
    levels = _levels(item)
    if not levels:
        return {"ok": False, "reason": "no item named"}
    if not bring_to_front(pid):
        return {"ok": False, "reason": "no Altium main window"}

    close_open_menu(pid)
    _right_click(pid, int(x), int(y))

    for depth, wanted in enumerate(levels):
        entries = _open_items(pid, timeout=settle * 2)
        if not entries:
            close_open_menu(pid)
            return {"ok": False, "reason": (
                "no context menu opened at that point"), "offered": []}
        hit = next((e for e in entries if _same(_name(e), wanted)), None)
        if hit is None:
            offered = [n for n in (_name(e) for e in entries) if n]
            close_open_menu(pid)
            return {"ok": False,
                    "reason": (f"{wanted!r} is not in the context menu"
                               if depth == 0 else
                               f"{wanted!r} is not in {levels[depth - 1]!r}"),
                    "offered": offered}
        if not _click_node(pid, hit, after=0.0):
            close_open_menu(pid)
            return {"ok": False,
                    "reason": f"{wanted!r} has no clickable rectangle"}
        win.wait_until(
            lambda: not _submenus(pid) or bool(_open_items(pid, 0.0)), settle)

    return {"ok": True, "at": [int(x), int(y)], "item": "|".join(levels)}


def toolbars(pid: int) -> dict:
    """Toolbar buttons on the main frame, by name.

    MEASURED AT 23.9 SECONDS before this was rewritten. The first
    version walked the accessible tree to depth four, one COM round trip
    per node, across a frame with 126 direct children and far more below
    them. It returned four buttons for twenty four seconds of work,
    which is worse than not having it.

    Window enumeration answers the same question in half a second: the
    frame's children are already captured, and a toolbar button is
    identifiable by class. The accessible tree is only consulted for the
    handful that carry no window text.
    """
    frame_win = frame(pid)
    if frame_win is None:
        return {"ok": False, "reason": "no Altium main window"}

    captured = win.capture(frame_win.hwnd)
    if captured is None:
        return {"ok": False, "reason": "could not read the main window"}

    #: Measured on AD26: the toolbars themselves are TClientBarControl
    #: and their buttons are TXPButtonEx / TPopupPanelButton. Anything
    #: parked off screen is a hidden bar rather than a usable one.
    bars, buttons = [], []
    for control in captured.controls:
        name = control.class_name.lower()
        x, y, width, height = control.rect
        if x < -10000 or not control.text:
            continue
        if "clientbarcontrol" in name:
            bars.append({"name": control.text, "rect": [x, y, width, height]})
        elif "buttonex" in name or "guidocbarbutton" in name:
            buttons.append({"name": control.text, "hwnd": control.hwnd,
                            "rect": [x, y, width, height]})

    seen, unique = set(), []
    for item in buttons:
        if item["name"] in seen:
            continue
        seen.add(item["name"])
        unique.append(item)

    return {"ok": True, "count": len(unique), "buttons": unique,
            "bars": bars,
            "note": ("bars are the toolbars themselves; buttons are the "
                     "ones exposing a caption. A button with only an icon "
                     "has no text to match and is reachable by its "
                     "rectangle")}


def click_toolbar(pid: int, name: str, settle: float = 0.8) -> dict:
    """Press a toolbar button by name."""
    frame_win = frame(pid)
    if frame_win is None:
        return {"ok": False, "reason": "no Altium main window"}
    if not bring_to_front(pid):
        return {"ok": False, "reason": "could not focus Altium"}

    listing = toolbars(pid)
    if not listing.get("ok"):
        return listing

    def flat(text):
        return plain(text).replace("&", "").strip().lower()

    hit = next((b for b in listing["buttons"]
                if flat(b["name"]) == flat(name)), None)
    if hit is None:
        return {"ok": False, "reason": f"no toolbar button named {name!r}",
                "offered": [b["name"] for b in listing["buttons"]][:80]}

    left, top, width, height = hit["rect"]
    _click(pid, left + width // 2, top + height // 2, after=0.0)
    win.wait_until(lambda: bool(_submenus(pid)), settle)
    return {"ok": True, "button": hit["name"],
            "opened_menu": bool(_submenus(pid))}


#: The toolbar that carries the view-configuration combo, by its
#: accessible name, and the combo's item name on that bar. Measured on
#: AD26: the combo is a bar item with a plain Edit window over it that
#: holds the current configuration. Its default action reports success
#: and opens nothing, as Altium's menu bars do, so it is opened with a
#: real click on its arrow. It drops a TdxBarDropDownListBox, which has
#: no accessible children but answers the standard listbox messages.
_VIEW_CONFIG_ITEM = "Change View Configuration"
_DROP_LIST_CLASS = "TdxBarDropDownListBox"
_LB_GETTEXT, _LB_GETTEXTLEN, _LB_GETCOUNT = 0x0189, 0x018A, 0x018B
_LB_GETITEMRECT = 0x0198


def _view_config_combo(pid: int, bar_name: str):
    """(combo node, edit hwnd) on one toolbar, or None.

    The bar is matched by its accessible name, not its window text:
    window text was measured empty on the same bar a minute after it
    read "PCB Standard", and its handle changes as editors switch.
    """
    from . import controls as ctl

    frame_win = frame(pid)
    if frame_win is None:
        return None
    captured = win.capture(frame_win.hwnd)
    if captured is None:
        return None
    for control in captured.controls:
        if "clientbarcontrol" not in control.class_name.lower():
            continue
        if control.rect[0] < -10000:
            continue
        if ctl.read(control.hwnd).get("name") != bar_name:
            continue
        node = next((c["node"] for c in ctl.list_choices(control.hwnd)
                     if _same(c["name"], _VIEW_CONFIG_ITEM)), None)
        if node is None:
            continue
        edit = next((c.hwnd for c in captured.controls
                     if c.class_name == "Edit"
                     and win32gui.GetParent(c.hwnd) == control.hwnd), None)
        return node, edit
    return None


def view_configuration(pid: int, bar_name: str = "PCB Standard") -> str | None:
    """The configuration the toolbar combo shows, or None if it is not there."""
    found = _view_config_combo(pid, bar_name)
    if found is None or found[1] is None:
        return None
    return win.read_text(found[1]) or None


def _drop_list(pid: int):
    return next((w.hwnd for w in win.enumerate_windows(pid=pid)
                 if w.class_name == _DROP_LIST_CLASS), None)


def _list_box_items(hwnd: int) -> list:
    user32 = _u()
    out = []
    for index in range(max(user32.SendMessageW(hwnd, _LB_GETCOUNT, 0, 0), 0)):
        length = user32.SendMessageW(hwnd, _LB_GETTEXTLEN, index, 0)
        buffer = ctypes.create_unicode_buffer(max(length, 0) + 2)
        user32.SendMessageW(hwnd, _LB_GETTEXT, index, buffer)
        out.append(buffer.value)
    return out


def set_view_configuration(pid: int, name: str = "",
                           bar_name: str = "PCB Standard",
                           settle: float = 2.0) -> dict:
    """Pick a view configuration in the toolbar combo, and read it back.

    There is no API route: PCB:ChangeViewConfiguration ignores every
    parameter name tried, and the only setter is internal to the view.
    With no ``name`` this only reads the combo. A name the list does not
    hold is refused with the names it does.
    """
    from . import controls as ctl

    found = _view_config_combo(pid, bar_name)
    if found is None:
        return {"ok": False, "reason": (
            f"no {bar_name!r} toolbar with a view configuration combo is "
            "showing. It shows only while that editor is the active one")}
    node, edit = found
    was = win.read_text(edit) if edit else None
    if not name:
        return {"ok": True, "value": was}
    if was and _same(was, name):
        return {"ok": True, "value": was, "changed": False}

    rect = ctl._node_rect(node)
    if rect is None:
        return {"ok": False, "reason": "the combo has no screen rectangle"}
    if not bring_to_front(pid):
        return {"ok": False, "reason": "could not focus Altium"}
    left, top, width, height = rect
    refused = _click(pid, left + width - 8, top + height // 2,
                     until=lambda: _drop_list(pid) is not None, wait=settle)
    drop = _drop_list(pid)
    if refused or drop is None:
        return {"ok": False, "value": was,
                "reason": refused or "the combo did not drop its list"}

    offered = _list_box_items(drop)
    index = next((i for i, text in enumerate(offered) if _same(text, name)),
                 None)
    if index is None:
        _tap(pid, _VK_ESC)
        win.wait_until(lambda: _drop_list(pid) is None, 0.6)
        return {"ok": False, "value": was, "offered": offered,
                "reason": f"no view configuration named {name!r}"}

    item = wintypes.RECT()
    _u().SendMessageW(drop, _LB_GETITEMRECT, index, byref(item))
    corner = wintypes.POINT(item.left, item.top)
    _u().ClientToScreen(drop, byref(corner))
    _click(pid, corner.x + (item.right - item.left) // 2,
           corner.y + (item.bottom - item.top) // 2,
           until=lambda: _drop_list(pid) is None, wait=settle)
    win.wait_until(
        lambda: _same(view_configuration(pid, bar_name) or "", name), settle)
    now = view_configuration(pid, bar_name)
    if now is None or not _same(now, name):
        return {"ok": False, "value": now, "was": was, "offered": offered,
                "reason": f"chose {name!r} and the combo still reads {now!r}"}
    return {"ok": True, "value": now, "was": was, "changed": True}
