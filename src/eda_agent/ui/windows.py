# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Thin Win32 window and control access. No Altium knowledge lives here.

Deliberately small and free of any dialog-specific logic, so it can be
exercised against any window on the machine rather than only against
Altium. What a dialog MEANS is decided in ``dialog_report``, and what to
press about it in ``dialog_driver``.

Two rules the callers depend on:

* Nothing here clicks anything unless asked by an explicit call. There
  is no "find and press" convenience, because the whole risk of this
  package is pressing the wrong thing.
* Matching is by window CLASS and caption together. A caption alone is
  not identifying: Altium reuses captions across dialogs, and other
  applications can hold a window with the same title.


WARNING: THIS MODULE CAN SYNTHESISE REAL INPUT, and synthesised input is
not addressed to a window. keybd_event and mouse_event go to whatever is
ACTIVE at the instant they fire, and a click lands on whatever is under
the pointer. Everything else in this package addresses a handle and
cannot go astray; only that path can.

It is here because large parts of Altium have no other route:
execute_menu reports success while invoking nothing, GetMenu returns 0
on a DevExpress bar, and whole dialogs such as Update From Libraries
have no scripting API at all.

Three things contain it, and all three are enforced by
tests/test_foreground_guard.py:

  a check immediately before EVERY event, including between a key press
  and its release, which refocuses the target rather than refusing

  coordinates confined to the target application, because a foreground
  check proves which app is active and says nothing about where the
  pointer is

  a kill switch, EDA_AGENT_UI_AUTOMATION=0, which refuses every
  synthesised event while leaving reads working

See docs/ui-automation.md.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

try:                                            # pragma: no cover - platform
    import win32api
    import win32con
    import win32gui
    import win32process
    _AVAILABLE = True
except ImportError:                             # pragma: no cover - platform
    _AVAILABLE = False


class WindowsUiUnavailable(RuntimeError):
    """pywin32 is not importable, so nothing here can run."""


def available() -> bool:
    """Whether this module can do anything at all on this machine."""
    return _AVAILABLE


def _require():
    if not _AVAILABLE:
        raise WindowsUiUnavailable(
            "pywin32 is not importable, so no dialog can be inspected or "
            "driven. It is a declared dependency on win32, so this usually "
            "means a non-Windows host or a broken install.")


#: Class-name fragments that mark a pressable pushbutton.
#:
#: MEASURED against Altium's Engineering Change Order dialog, not
#: guessed. Its buttons are TXPBitBtn, whose class carries "bitbtn" and
#: NOT "button". A "button"-only test therefore finds ZERO buttons on a
#: real Altium dialog, which is what made the driver abort at its first
#: step every time. Stock Win32 "Button" is kept for MessageBox-style
#: dialogs.
_BUTTON_CLASSES = ("button", "bitbtn", "btn")

#: Fragments that match the above but must never be pressed.
#:
#: Checkboxes and radios answer to BM_CLICK exactly like a pushbutton,
#: so pressing one silently TOGGLES a setting instead of doing nothing.
#: That is not hypothetical: the ECO dialog carries a TXPCheckBox
#: ("Only Show Errors") alongside its four TXPBitBtn buttons.
#:
#: docbar / popuppanel / panelsholder are Altium's document tabs and
#: panel toggles, measured on the main window. They contain "button"
#: and are matched by the old test, so it was simultaneously too narrow
#: for dialogs and too broad for the main window.
_NOT_BUTTON_CLASSES = ("check", "radio", "group", "docbar",
                       "popuppanel", "panelsholder")

#: Win32 button styles. Only meaningful for the stock "Button" class,
#: where a checkbox and a pushbutton share one class name and are told
#: apart by these bits alone.
_BS_TYPEMASK = 0x0000000F
_BS_PRESSABLE = (0x0, 0x1, 0x8, 0xB)   # push, default-push, user, ownerdraw


#: Kill switch. UI automation is ON by default, because for a great deal
#: of Altium there is no other route: see the module docstring and
#: docs/ui-automation.md. Set this to 0/false/no/off to refuse every
#: synthesised event.
UI_AUTOMATION_ENV = "EDA_AGENT_UI_AUTOMATION"
_OFF = ("0", "false", "no", "off", "disable", "disabled")


class AutomationDisabled(RuntimeError):
    """UI automation is switched off by EDA_AGENT_UI_AUTOMATION."""


def automation_enabled() -> bool:
    """Whether synthesised input is permitted at all.

    READ AT CALL TIME, not at import, so it can be turned off in a
    running process and takes effect on the very next event rather than
    on the next restart.
    """
    raw = os.environ.get(UI_AUTOMATION_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _OFF


def _gate(what: str) -> None:
    """Refuse everything when the kill switch is set.

    Placed in the primitives rather than only in the tools, so a caller
    reaching past the tool layer is stopped too. Every synthesised event
    in this package passes through here or through require_foreground.
    """
    if not automation_enabled():
        raise AutomationDisabled(
            f"refusing {what}: UI automation is disabled by "
            f"{UI_AUTOMATION_ENV}. Unset it, or set it to 1, to allow "
            f"synthesised keyboard and mouse input again")


class ForegroundLost(RuntimeError):
    """Could not give the target the foreground, so input was not sent."""


#: GetAncestor(GA_ROOT), SW_RESTORE. Activation applies to the top-level
#: window, not to the child control an event is aimed at.
_GA_ROOT = 2
_SW_RESTORE = 9
_SW_SHOW = 5


class ForeignTarget(RuntimeError):
    """Refused: the point or window does not belong to the target app."""


def window_at(x: int, y: int):
    """The window under a screen point, or None."""
    try:
        return int(ctypes.windll.user32.WindowFromPoint(
            wintypes.POINT(int(x), int(y)))) or None
    except Exception:                            # noqa: BLE001
        return None


def require_point_in_app(x: int, y: int, target, what: str = "a click") -> int:
    """Refuse to click a point that is not over the target application.

    THE FOREGROUND CHECK IS NOT ENOUGH ON ITS OWN. It proves the right
    application is active; it says nothing about where the pointer is.
    A coordinate outside Altium's window, or over something floating on
    top of it, is still delivered to whatever is at that point, and a
    click can raise that window and put the NEXT event there too.

    Only coordinate entry points need this. Every other target in this
    package is resolved by name out of dialogs(pid) or frame(pid) and is
    therefore already confined to the process; a caller cannot pass a
    handle in from outside.

    Returns the window under the point, so a caller can report what was
    actually there.
    """
    want = window_pid(target)
    if want is None:
        raise ForeignTarget(f"refusing {what}: the target window is gone")
    under = window_at(x, y)
    if under is None:
        raise ForeignTarget(
            f"refusing {what} at ({x}, {y}): there is no window at that "
            f"point")
    got = window_pid(under)
    if got != want:
        raise ForeignTarget(
            f"refusing {what} at ({x}, {y}): that point is over a window "
            f"belonging to process {got}, not to the target application "
            f"({want}). Coordinates are only accepted over the "
            f"application this tool drives")
    return under


def foreground_pid():
    """Process id owning the foreground window, or None."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        return int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:                            # noqa: BLE001
        return None


def window_pid(hwnd):
    """Process id owning a window, or None."""
    try:
        return int(win32process.GetWindowThreadProcessId(int(hwnd))[1])
    except Exception:                            # noqa: BLE001
        return None


def window_title(hwnd) -> str:
    """A window's caption, or '' when it cannot be read or is gone."""
    try:
        return str(win32gui.GetWindowText(int(hwnd)) or "")
    except Exception:                            # noqa: BLE001
        return ""


def owns_foreground(hwnd) -> bool:
    """Whether hwnd's APPLICATION is the active one.

    By process, not by handle: a dialog, a popup menu and the main frame
    are different windows of the same application and all are legitimate
    targets for input aimed at any of them.
    """
    target = window_pid(hwnd)
    return target is not None and foreground_pid() == target


def _force_foreground(root: int) -> None:
    """Raise a window, working around Windows' foreground lock.

    A BARE SetForegroundWindow IS USUALLY DENIED. Windows only grants it
    to a process that already owns the foreground, or that is otherwise
    entitled, and this one is a background console. It fails SILENTLY:
    the call returns and the window stays where it was. MEASURED here as
    a refusal to send a keystroke to a minimized Altium, and it is why
    bring_to_front used to report success it had never confirmed.

    The supported way through is to attach this thread's input queue to
    the foreground window's thread, which makes the two count as one
    input context for the duration, and to detach immediately after.

    Nothing is synthesised here, so this is not itself an input event
    and needs no guard of its own.
    """
    user32 = ctypes.windll.user32
    current = ctypes.windll.kernel32.GetCurrentThreadId()
    active = user32.GetForegroundWindow()
    other = user32.GetWindowThreadProcessId(active, None) if active else 0

    attached = False
    if other and other != current:
        attached = bool(user32.AttachThreadInput(current, other, True))
    try:
        user32.ShowWindow(root, _SW_SHOW)
        user32.BringWindowToTop(root)
        user32.SetForegroundWindow(root)
    finally:
        if attached:
            user32.AttachThreadInput(current, other, False)


def require_foreground(hwnd, what: str = "input", timeout: float = 1.2) -> bool:
    """Make the target active, then confirm it, IMMEDIATELY BEFORE an event.

    SYNTHESISED INPUT IS NOT ADDRESSED TO A WINDOW. keybd_event and
    mouse_event go to whatever is active at the instant they fire, so a
    keystroke meant for Altium lands in whatever the user switched to
    and a click lands wherever the pointer now is. Every other read in
    this package is addressed to a handle and cannot do this; only the
    synthesis path can.

    MEASURED, and the reason this exists: a keyboard walk of the menu
    bar kept sending Alt and arrow keys after Altium had lost the
    foreground. Nothing was harmed that time, which was luck.

    IT REFOCUSES RATHER THAN REFUSING. Focus moves for ordinary reasons
    between one event and the next, and failing the whole operation
    because the user glanced at another window would make this unusable.
    So the window is restored and raised, and only a target that will
    not come forward raises.

    Called before EVERY event rather than once per operation, including
    between a key press and its release: a key held while focus moves
    releases into another application.

    Returns True when it had to refocus, False when it was already
    active, so a caller can tell a clean run from a recovered one.
    """
    _gate(what)
    target = window_pid(hwnd)
    if target is None:
        raise ForegroundLost(
            f"refusing to send {what}: the target window is gone")
    if foreground_pid() == target:
        return False

    user32 = ctypes.windll.user32
    root = user32.GetAncestor(int(hwnd), _GA_ROOT) or int(hwnd)
    if user32.IsIconic(root):
        user32.ShowWindow(root, _SW_RESTORE)
        wait_until(lambda: not user32.IsIconic(root), timeout)
    _force_foreground(root)
    wait_until(lambda: foreground_pid() == target, timeout)

    if foreground_pid() != target:
        raise ForegroundLost(
            f"refusing to send {what}: the target could not be brought to "
            f"the front and the active window belongs to process "
            f"{foreground_pid()}, not {target}. Synthesised input goes to "
            f"whatever is active, so this would have gone elsewhere")
    return True


@dataclass
class Control:
    """One child control: what it is, what it says, where it sits."""

    hwnd: int
    class_name: str
    text: str
    rect: tuple = field(default=(0, 0, 0, 0))
    enabled: bool = True
    visible: bool = True
    #: Raw GWL_STYLE, used to tell a stock pushbutton from a checkbox.
    style: int = 0

    def is_pressable(self) -> bool:
        """Whether this is a button a plan may legitimately press.

        Class name first, because Altium's dialogs are VCL and their
        class is the only reliable signal. For the stock Win32 "Button"
        class the name says nothing (checkbox, radio, groupbox and
        pushbutton all share it), so the style bits decide.
        """
        if not self.text:
            return False
        name = self.class_name.lower()
        if any(bad in name for bad in _NOT_BUTTON_CLASSES):
            return False
        if not any(good in name for good in _BUTTON_CLASSES):
            return False
        if name == "button":
            return (self.style & _BS_TYPEMASK) in _BS_PRESSABLE
        return True

    def describe(self) -> str:
        state = "" if self.enabled else " (disabled)"
        return f"{self.class_name}:{self.text!r}{state}"


@dataclass
class Window:
    """One top-level window, with its controls read at capture time."""

    hwnd: int
    class_name: str
    title: str
    pid: int
    controls: list = field(default_factory=list)

    def buttons(self) -> list:
        """Controls a plan may legitimately press.

        Altium's dialogs are VCL: the ECO's buttons are TXPBitBtn, and
        stock Win32 "Button" appears only in message boxes. Both are
        accepted; checkboxes, radios and the main window's tab and panel
        chrome are not. See ``Control.is_pressable``.
        """
        return [c for c in self.controls if c.is_pressable()]

    def find_button(self, caption: str):
        """The button whose caption matches, ignoring case and & markers.

        Windows accelerators put an ampersand in the caption (&OK), and
        it is invisible to the user, so callers should not have to know
        about it.
        """
        want = _normalise(caption)
        for control in self.buttons():
            if _normalise(control.text) == want:
                return control
        return None

    def message_text(self) -> list:
        """What the dialog SAYS, as opposed to what it offers.

        Button captions are the actions; everything else with text is
        the message. Separating them matters because "Cannot compare a
        source document against its owner project" is the whole content
        of an error dialog, and burying it in a list that also contains
        "OK" makes it easy to miss.

        Grids and owner-drawn lists still contribute nothing: they hold
        their content internally and expose no window text at all, which
        is why an ECO's pending-change list cannot be read this way.
        """
        pressable = {c.hwnd for c in self.buttons()}
        seen, out = set(), []
        for control in self.controls:
            if control.hwnd in pressable:
                continue
            text = (control.text or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out

    def has_unreadable_content(self) -> bool:
        """True when this dialog is displaying text that cannot be read.

        The distinction that matters is "the dialog says nothing" versus
        "the dialog is saying something I cannot see", because only the
        second one means a human has to look at the screen.

        Two causes, both MEASURED on real Altium dialogs:

        * a grid or owner-drawn list, which holds its rows internally.
          The ECO's pending changes live in a TdxTreeList.
        * text painted straight onto a panel. Delphi's TLabel is a
          TGraphicControl with NO window handle, so it cannot be
          enumerated, and the panel it is painted on answers
          WM_GETTEXTLENGTH with zero. The "No Differences Detected"
          message sits in exactly that state, and MSAA exposes nothing
          for it either.

        The second case is inferred from a container that carries no
        text of its own, which is what an all-painted panel looks like
        from outside the process.
        """
        if any(any(k in c.class_name.lower() for k in _OPAQUE_CLASSES)
               for c in self.controls):
            return True
        pressable = {c.hwnd for c in self.buttons()}
        return any(not (c.text or "").strip() and c.hwnd not in pressable
                   and any(k in c.class_name.lower()
                           for k in _CONTAINER_CLASSES)
                   for c in self.controls)

    def describe(self) -> str:
        return (f"{self.class_name} {self.title!r} "
                f"({len(self.controls)} controls)")


#: Controls that render their content without exposing window text.
#: MEASURED: the ECO's pending changes live in a TdxTreeList, which is
#: why the change list cannot be read before it is executed.
_OPAQUE_CLASSES = ("treelist", "listview", "grid", "syslistview",
                   "virtualtree")

#: Containers a VCL dialog paints its message onto. A panel with no
#: window text of its own is the signature of a TLabel drawn on it.
_CONTAINER_CLASSES = ("panel", "groupbox", "static")


def _normalise(text: str) -> str:
    return re.sub(r"[&\s.]+", "", str(text or "")).lower()


def read_text(hwnd: int) -> str:
    """A control's text, asking the control rather than trusting a cache.

    ``GetWindowText`` does NOT send WM_GETTEXT across a process boundary:
    for a window owned by another process it returns Windows' cached
    caption, which for many VCL controls is empty even though the
    control is displaying text. Altium is another process, so the cheap
    call silently loses exactly the message body that says what a dialog
    is telling you.

    WM_GETTEXT is sent with a timeout because a control belonging to a
    blocked UI thread would otherwise hang the caller, and a dialog that
    has blocked its own thread is a state this code has to survive.
    """
    _require()
    try:
        cached = win32gui.GetWindowText(hwnd)
    except Exception:                            # pragma: no cover - races
        cached = ""
    if cached:
        return cached
    try:
        length = win32gui.SendMessageTimeout(
            hwnd, win32con.WM_GETTEXTLENGTH, 0, 0,
            win32con.SMTO_ABORTIFHUNG, 500)[1]
        if not length:
            return ""
        buffer = win32gui.PyMakeBuffer((length + 1) * 2)
        win32gui.SendMessageTimeout(
            hwnd, win32con.WM_GETTEXT, length + 1, buffer,
            win32con.SMTO_ABORTIFHUNG, 800)
        raw = bytes(buffer)[:length * 2]
        return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    except Exception:                            # pragma: no cover - platform
        return ""


def is_blocked(pid: int) -> bool:
    """Whether a modal is holding this process's main window.

    Windows disables the owner while a modal is up, so a DISABLED main
    frame is the signal that the application cannot be driven and, for
    Altium, that the scripting bridge will not answer until the dialog
    goes away. Far more reliable than inferring it from a silent bridge,
    which looks identical to a crash, a busy compile, or a stopped
    polling loop.
    """
    _require()
    for window in enumerate_windows(pid=pid):
        if _is_main_frame(window):
            try:
                return not win32gui.IsWindowEnabled(window.hwnd)
            except Exception:                    # pragma: no cover - races
                return False
    return False


#: Class of Altium's main document frame. Everything else with a caption
#: is either a dialog or a dockable panel.
_MAIN_FRAME_CLASSES = ("TDocumentForm",)

#: Top-level windows that carry a caption but are not dialogs.
#:
#: TScriptForm is the SCRIPTING SYSTEM's form, which is what this
#: project's own status window is built from. Reporting it as an Altium
#: dialog is not just noise: it is always present, so "a dialog is open"
#: would be permanently true, and its perf log is long enough to bury
#: the real dialog's message underneath it. Altium's own dialogs are
#: TChangeManagementForm, TXPForm and similar, never TScriptForm.
_NOT_A_DIALOG_CLASSES = ("TScriptForm",)


def _is_main_frame(window) -> bool:
    return window.class_name in _MAIN_FRAME_CLASSES


def _is_dialog(window) -> bool:
    return (not _is_main_frame(window)
            and window.class_name not in _NOT_A_DIALOG_CLASSES
            and bool((window.title or "").strip()))


def dialogs(pid: int) -> list:
    """Every dialog-like window on a process, captured with its text.

    Excludes the main frame and anything with no caption. Dockable
    panels are kept out by requiring the window to be OWNED, which is
    what makes a dialog a dialog: panels are children of the frame,
    dialogs are owned top-level windows.
    """
    _require()
    out = []
    for window in enumerate_windows(pid=pid):
        if not _is_dialog(window):
            continue
        full = capture(window.hwnd)
        if full is not None:
            out.append(full)
    return out


def enumerate_windows(pid: Optional[int] = None,
                      visible_only: bool = True) -> list:
    """Every top-level window, optionally limited to one process.

    Controls are NOT read here: enumerating children of every window on
    the machine is slow and mostly wasted. Use ``capture`` for that.
    """
    _require()
    out = []

    def visit(hwnd, _):
        if visible_only and not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:                        # pragma: no cover - races
            return True
        if pid is not None and wpid != pid:
            return True
        try:
            out.append(Window(hwnd=hwnd,
                              class_name=win32gui.GetClassName(hwnd),
                              title=win32gui.GetWindowText(hwnd),
                              pid=wpid))
        except Exception:                        # pragma: no cover - races
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return out


def capture(hwnd: int) -> Optional[Window]:
    """One window with its child controls read, or None if it is gone.

    A window can be destroyed between being found and being inspected,
    which is ordinary rather than exceptional when a wizard is
    advancing, so that case returns None instead of raising.
    """
    _require()
    if not win32gui.IsWindow(hwnd):
        return None
    try:
        window = Window(hwnd=hwnd,
                        class_name=win32gui.GetClassName(hwnd),
                        title=win32gui.GetWindowText(hwnd),
                        pid=win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        return None

    def visit(child, _):
        try:
            try:
                style = win32api.GetWindowLong(child, win32con.GWL_STYLE)
            except Exception:                    # pragma: no cover - races
                style = 0
            window.controls.append(Control(
                hwnd=child,
                class_name=win32gui.GetClassName(child),
                # read_text, not GetWindowText: Altium is another
                # process, and the cheap call returns a cache that is
                # empty for many VCL controls, which is precisely how a
                # dialog's message body goes missing.
                text=read_text(child),
                rect=win32gui.GetWindowRect(child),
                enabled=win32gui.IsWindowEnabled(child),
                visible=win32gui.IsWindowVisible(child),
                style=style))
        except Exception:                        # pragma: no cover - races
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
    except Exception:
        # A window with no children raises rather than returning empty.
        pass
    return window


def wait_for_window(match: Callable, timeout: float,
                    pid: Optional[int] = None,
                    poll: float = 0.25) -> Optional[Window]:
    """Wait for a window satisfying ``match``, captured with controls.

    ``match`` receives a Window WITHOUT controls (the cheap form) and
    returns a bool. The winner is then re-captured with its controls,
    because reading children of every candidate on every poll is the
    slow part.
    """
    _require()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for window in enumerate_windows(pid=pid):
            try:
                hit = match(window)
            except Exception:                    # pragma: no cover - guard
                hit = False
            if hit:
                full = capture(window.hwnd)
                if full is not None:
                    return full
        time.sleep(poll)
    return None


def wait_until(check, timeout: float, poll: float = 0.02) -> bool:
    """Poll ``check`` until it is true, or ``timeout`` elapses.

    A FIXED SLEEP PAYS THE WORST CASE EVERY TIME. The UI path was built
    on them, and they add up: focusing the frame cost 0.8s whether or
    not Altium was already in front, and one menu click cost 0.58s in
    three sleeps. A two-level path therefore spent about two seconds
    waiting before it did anything.

    The timeout stays what the sleep was, so nothing gets less patient.
    What changes is the common case, where the condition is already true
    and this returns in microseconds.

    The poll is deliberately much shorter than the waits it replaces. It
    is a ``GetForegroundWindow`` or an ``IsIconic``, which cost nothing;
    polling slower than the thing being waited for would give back the
    time this exists to save.
    """
    if check():
        return True
    deadline = time.monotonic() + timeout
    interval = poll
    while time.monotonic() < deadline:
        time.sleep(interval)
        if check():
            return True
        # BACK OFF. A flat fast poll is right for the first fraction of a
        # second, when most of these resolve, and wrong for the long tail:
        # a menu that takes six seconds would otherwise be asked a hundred
        # and twenty times, and each ask here can be a window enumeration
        # and an accessible-tree walk. Doubling keeps the fast case fast
        # and stops a slow one burning CPU for the whole wait.
        if interval < 0.25:
            interval = min(0.25, interval * 2)
    return False


def wait_for_close(hwnd: int, timeout: float, poll: float = 0.05) -> bool:
    """Wait for a window to disappear. True if it did.

    Polled at 0.05 rather than 0.25: IsWindow is a cheap call and this
    is on the path of every dialog the driver dismisses, so the interval
    was the floor on how fast a sequence of dialogs could be cleared.
    """
    _require()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not win32gui.IsWindow(hwnd):
            return True
        time.sleep(poll)
    return not win32gui.IsWindow(hwnd)


def click(control: Control) -> None:
    """Press one control, by messaging it rather than moving the mouse.

    Every message here is addressed to the control's OWN handle, so it
    reaches the intended control regardless of what is physically on
    top, what has focus, or where the pointer happens to be, and the
    physical cursor is never touched. A stray mouse move during a real
    click sequence is the classic way UI automation presses the wrong
    thing.

    Two mechanisms, because one does not cover both worlds. BM_CLICK is
    the correct press for the stock Win32 "Button" class used by message
    boxes. Altium's dialogs are VCL, and MEASURED on the Engineering
    Change Order dialog a TXPBitBtn IGNORES BM_CLICK completely: the
    press returned cleanly and the dialog stayed open. It responds to a
    posted button-down/up pair on its own handle, which is what the
    non-stock branch sends.
    """
    _require()
    if not control.enabled:
        raise RuntimeError(
            f"refusing to click {control.describe()}: it is disabled, which "
            f"usually means the dialog is not in the state expected")
    if control.class_name.lower() == "button":
        win32gui.SendMessage(control.hwnd, win32con.BM_CLICK, 0, 0)
        return
    # lParam is the pointer position in CLIENT coordinates, and it used
    # to be 0, which is the top-left CORNER of the control. A VCL
    # button tracks where the press landed and can treat the corner
    # pixel as a miss or as being on the border. The same mistake in
    # select_row put a click at a grid's origin and selected its first
    # row. Aim at the middle instead.
    lparam = 0
    try:
        left, top, right, bottom = win32gui.GetClientRect(control.hwnd)
        x, y = (right - left) // 2, (bottom - top) // 2
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    except Exception:                            # pragma: no cover - guard
        pass
    win32gui.PostMessage(control.hwnd, win32con.WM_LBUTTONDOWN,
                         win32con.MK_LBUTTON, lparam)
    win32gui.PostMessage(control.hwnd, win32con.WM_LBUTTONUP, 0, lparam)


def close_window(hwnd: int) -> None:
    """Ask a window to close, as clicking its X would."""
    _require()
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


#: Keys that answer a dialog with no addressable buttons.
_KEYS = {
    "enter": 0x0D, "escape": 0x1B, "esc": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}

#: Modifier virtual-key codes, held down around a key in a chord.
_MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10}


def press_key(hwnd: int, key: str) -> bool:
    """Answer a dialog by keystroke, for the ones with NO button handles.

    THE CASE THIS EXISTS FOR. Altium's newer dialogs are WPF, hosted in
    an HwndWrapper window. Their buttons are not child windows, so
    EnumChildWindows returns nothing, ``buttons()`` comes back empty and
    ``click`` has no handle to address. MEASURED on the "Unsaved
    Changes" prompt: the captions are readable only by OCR, and
    app_press_dialog_button could do nothing but refuse while the bridge
    stayed blocked for over two minutes.

    A keystroke reaches it, because the dialog has keyboard focus while
    it is modal. Enter takes the default button and Escape cancels,
    which between them cover the confirm prompts that actually wedge
    this bridge.

    Unlike ``click`` this CANNOT be addressed to a specific control, so
    it goes to the foreground window and the caller must have made the
    dialog foreground first. That is the trade: it is less precise, and
    it is the only thing that works when no handle exists.

    Returns whether the key was sent, not whether the dialog obeyed.
    Confirm with ``wait_for_close``.
    """
    _require()
    code = _KEYS.get(key.lower())
    if code is None:
        raise ValueError(f"unsupported key {key!r}; use one of "
                         f"{sorted(_KEYS)}")
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:                            # pragma: no cover - guard
        # Windows refuses foreground changes from a background process
        # in some states. The post below can still land if the dialog
        # already has focus, so this is not fatal.
        pass
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, code, 0)
    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, code, 0)
    return True


def window_text_dump(window: Window, limit: int = 60) -> list:
    """Readable text from a window's controls, for the record.

    Grids and owner-drawn lists return nothing here, which is exactly
    why an ECO's change list cannot be enumerated before it runs. What
    comes back is labels, static text and button captions, and that is
    stated rather than presented as the dialog's full contents.
    """
    seen = []
    for control in window.controls:
        text = (control.text or "").strip()
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def send_keys(sequence: str, hold: float = 0.03, target=None) -> dict:
    """Send a key sequence to whatever has focus.

    THE LAST GAP IN "ANY INPUT". Everything else here addresses a
    control: a checkbox, a field, a row, a cell. Some things in Altium
    take neither, they take a keystroke: a canvas shortcut, a grid that
    commits on Enter, a dialog answered with Escape, a chord like
    Ctrl+Shift+D.

    Sequence is space separated, and each item is either a key name or a
    chord joined by '+':

        "escape"                 one key
        "ctrl+s"                 a chord
        "tab tab enter"          three keys in order

    SENT TO THE FOCUSED WINDOW, not to a handle. That is the point of
    it, and also the risk: whatever is focused receives this, so a
    caller should establish focus first and check afterwards. Nothing
    here can verify the effect, because a keystroke has no read-back;
    the reply says what was sent, never that it worked.
    """
    _require()
    _gate("synthesised input")
    items = [chunk for chunk in str(sequence or "").split() if chunk]
    if not items:
        return {"ok": False, "reason": "no keys given"}

    user32 = ctypes.windll.user32
    sent = []
    for item in items:
        parts = [p.strip().lower() for p in item.split("+") if p.strip()]
        if not parts:
            continue
        *mods, key = parts
        codes = []
        for mod in mods:
            if mod not in _MODIFIERS:
                return {"ok": False, "reason": (
                    f"{mod!r} is not a modifier. Known: "
                    f"{', '.join(sorted(set(_MODIFIERS)))}")}
            codes.append(_MODIFIERS[mod])
        if key not in _KEYS:
            return {"ok": False, "reason": (
                f"{key!r} is not a known key. Known: "
                f"{', '.join(sorted(_KEYS))}"), "sent": sent}

        for code in codes:
            if target is not None:
                require_foreground(target, f"the chord for {item!r}")
            user32.keybd_event(code, 0, 0, 0)
        if target is not None:
            require_foreground(target, f"the key {key!r}")
        user32.keybd_event(_KEYS[key], 0, 0, 0)
        time.sleep(hold)
        # Re-checked before the release: a key held down while focus
        # moves releases into whatever became active.
        if target is not None:
            require_foreground(target, f"releasing {key!r}")
        user32.keybd_event(_KEYS[key], 0, 2, 0)
        for code in reversed(codes):
            user32.keybd_event(code, 0, 2, 0)
        sent.append(item)

    return {"ok": True, "sent": sent, "note": (
        "sent to whatever had focus. A keystroke has no read-back, so "
        "this reports what was sent and not what it did")}


def type_text(text: str, target=None) -> dict:
    """Type literal text into the focused control, character by character.

    Uses WM_CHAR-style unit input rather than a key table, so it carries
    punctuation and case without needing a virtual-key code for every
    character. For a field with a handle, controls.set_text is better:
    it addresses the control and reads the value back. This is for the
    places that have no handle to address.
    """
    _require()
    _gate("synthesised input")
    if not str(text or ""):
        return {"ok": False, "reason": "no text given"}

    user32 = ctypes.windll.user32
    for char in str(text):
        code = ord(char)
        # KEYEVENTF_UNICODE, with the character in wScan.
        if target is not None:
            require_foreground(target, "a typed character")
        user32.keybd_event(0, code, 0x0004, 0)
        user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
    return {"ok": True, "typed": len(str(text)), "note": (
        "typed into whatever had focus, with no read-back. Prefer "
        "app_set_dialog_control for a field that has a handle")}


#: Mouse event flags. Absolute coordinates are normalised to 0..65535
#: across the virtual desktop, which is the only form that works on a
#: multi-monitor setup.
_ME_MOVE, _ME_ABSOLUTE = 0x0001, 0x8000
_ME_LDOWN, _ME_LUP = 0x0002, 0x0004
_SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 78, 79


def _to_absolute(x: int, y: int):
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN) or 1
    height = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN) or 1
    return int(x * 65535 / width), int(y * 65535 / height)


def drag(x1: int, y1: int, x2: int, y2: int, steps: int = 12, target=None,
         hold: float = 0.05) -> dict:
    """Press at one point, move, release at another.

    THE CANVAS TAKES NEITHER A CONTROL NOR A MENU. Placing, moving,
    rubber-band selecting and routing are all pointer gestures, and
    nothing here could make one: every existing action addresses a named
    control, and the canvas has no named controls at all.

    Moved in steps rather than jumped, because a jump from press to
    release reads as a click at the destination in most editors: the
    intermediate motion is what makes it a drag.

    NO READ-BACK EXISTS FOR THIS. The reply says what gesture was made,
    never what it did. Check the result with the bridge, which can see
    the document, rather than believing this.
    """
    _require()
    _gate("synthesised input")
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    origin = (point.x, point.y)

    ax, ay = _to_absolute(int(x1), int(y1))
    if target is not None:
        require_foreground(target, "a drag move")
        # BOTH ENDS are checked. Windows captures the mouse to whatever
        # received the button-down, so the points in between cannot be
        # delivered elsewhere, but the release can be.
        require_point_in_app(int(x1), int(y1), target, "a drag start")
        require_point_in_app(int(x2), int(y2), target, "a drag end")
    user32.mouse_event(_ME_MOVE | _ME_ABSOLUTE, ax, ay, 0, 0)
    time.sleep(hold)
    if target is not None:
        require_foreground(target, "a drag press")
    user32.mouse_event(_ME_LDOWN, 0, 0, 0, 0)
    time.sleep(hold)

    steps = max(2, int(steps))
    for i in range(1, steps + 1):
        ix = int(x1 + (x2 - x1) * i / steps)
        iy = int(y1 + (y2 - y1) * i / steps)
        mx, my = _to_absolute(ix, iy)
        # Every step of the path, not just the press: a drag that
        # wanders while focus changes is drawing in another window.
        if target is not None:
            require_foreground(target, "a drag move")
        user32.mouse_event(_ME_MOVE | _ME_ABSOLUTE, mx, my, 0, 0)
        time.sleep(hold / 2)

    # A drag holds the button down across many events, so losing focus
    # mid-gesture would drop it somewhere else entirely. Released only
    # while the target is still active.
    if target is not None:
        require_foreground(target, "a drag release")
    user32.mouse_event(_ME_LUP, 0, 0, 0, 0)
    user32.SetCursorPos(*origin)
    return {"ok": True, "from": [int(x1), int(y1)], "to": [int(x2), int(y2)],
            "steps": steps, "note": (
                "a pointer gesture has no read-back. Confirm the effect "
                "with a bridge read, which can see the document")}


def click_at(x: int, y: int, double: bool = False, target=None) -> dict:
    """A plain left click at a screen point, for the canvas.

    Everything else clicks a control it found by name. This clicks where
    it is told, which is the only way to reach a point on a drawing.
    """
    _require()
    _gate("synthesised input")
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    origin = (point.x, point.y)

    ax, ay = _to_absolute(int(x), int(y))
    if target is not None:
        require_foreground(target, "a pointer move")
        require_point_in_app(int(x), int(y), target, "a click")
    user32.mouse_event(_ME_MOVE | _ME_ABSOLUTE, ax, ay, 0, 0)
    time.sleep(0.05)
    for _ in range(2 if double else 1):
        if target is not None:
            require_foreground(target, "a click")
        user32.mouse_event(_ME_LDOWN, 0, 0, 0, 0)
        time.sleep(0.04)
        if target is not None:
            require_foreground(target, "a click release")
        user32.mouse_event(_ME_LUP, 0, 0, 0, 0)
        time.sleep(0.04)
    user32.SetCursorPos(*origin)
    return {"ok": True, "at": [int(x), int(y)], "double": bool(double),
            "note": "no read-back; confirm with a bridge read"}


def wait_for_dialog(pid: int, title: str = "", timeout: float = 30.0) -> dict:
    """Block until a dialog appears, and report which one.

    A COMMAND THAT RAISES A DIALOG RETURNS BEFORE THE DIALOG EXISTS, so
    a caller that acted and then looked found nothing and concluded no
    dialog was coming. The only alternative was a fixed sleep long
    enough to be safe, which is the pattern this layer has spent the day
    removing.
    """
    _require()
    found = {}

    def appeared() -> bool:
        for dialog in dialogs(pid):
            if not title or title.lower() in (dialog.title or "").lower():
                found["dialog"] = dialog
                return True
        return False

    if not wait_until(appeared, timeout):
        which = f" matching {title!r}" if title else ""
        return {"ok": False, "waited": timeout,
                "reason": f"no dialog{which} appeared within {timeout:g}s"}
    dialog = found["dialog"]
    return {"ok": True, "title": dialog.title, "class": dialog.class_name,
            "hwnd": dialog.hwnd,
            "buttons": [b.text for b in dialog.buttons()]}
