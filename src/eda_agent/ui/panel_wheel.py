# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Mouse-wheel scrolling for the Config tab of the bridge panel in Altium.

Altium's script engine never runs a script form's wheel events, so the
panel cannot scroll itself. This thread does it from outside: while the
cursor is over the panel's scroll box, it installs a low-level mouse hook,
turns each wheel notch into a new scroll position posted to the box, and
swallows the wheel so it does not also reach an Altium editor. When the
cursor leaves, the hook comes off again, so the rest of the desktop never
goes through it.

Several eda-agent servers can run at once; a named mutex lets only one of
them hook at a time, or each notch would scroll several times.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

PANEL_TITLE = "EDA Agent MCP"
SCROLL_CLASS = "TScrollBox"
PIXELS_PER_NOTCH = 40
POLL_SECONDS = 0.1
_MUTEX_NAME = "Local\\EdaAgentPanelWheel"

WH_MOUSE_LL = 14
WM_MOUSEWHEEL = 0x020A
WM_VSCROLL = 0x0115
SB_THUMBPOSITION = 4
SB_VERT = 1
SIF_RANGE, SIF_PAGE, SIF_POS = 0x1, 0x2, 0x4
GA_ROOT = 2
QS_ALLINPUT = 0x04FF
PM_REMOVE = 1
WAIT_OBJECT_0, WAIT_ABANDONED = 0, 0x80

_started = False


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _SCROLLINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("fMask", wintypes.UINT),
                ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
                ("nPage", wintypes.UINT), ("nPos", ctypes.c_int),
                ("nTrackPos", ctypes.c_int)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                               wintypes.WPARAM, wintypes.LPARAM)


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC,
                                         wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.MsgWaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        wintypes.DWORD]
    user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT,
                                    wintypes.UINT]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, kernel32


def _class_name(user32, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def scroll_box_at(user32, x: int, y: int):
    """The panel's scroll box under screen point (x, y), or None."""
    panel = user32.FindWindowW(None, PANEL_TITLE)
    if not panel or not user32.IsWindowVisible(panel):
        return None
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd or user32.GetAncestor(hwnd, GA_ROOT) != panel:
        return None
    while hwnd and hwnd != panel:
        if _class_name(user32, hwnd) == SCROLL_CLASS:
            return hwnd if user32.IsWindowVisible(hwnd) else None
        hwnd = user32.GetParent(hwnd)
    return None


def wheel_position(base: int, delta: int, top: int) -> int:
    """The scroll position after a wheel delta (+ up, - down; 120 a notch),
    kept within 0..top. Fractions from high-resolution wheels count."""
    return max(0, min(top, base - round(delta * PIXELS_PER_NOTCH / 120)))


def _scroll_range(user32, box) -> tuple[int, int]:
    """(position, highest position) of the box's vertical scroll bar."""
    info = _SCROLLINFO(ctypes.sizeof(_SCROLLINFO), SIF_RANGE | SIF_PAGE | SIF_POS)
    if not user32.GetScrollInfo(box, SB_VERT, ctypes.byref(info)):
        return 0, 0
    return info.nPos, max(0, info.nMax - max(int(info.nPage), 1) + 1)


def _run() -> None:
    user32, kernel32 = _api()
    module = kernel32.GetModuleHandleW(None)
    mutex = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    # Posted scrolls land later, so quick notches build on the last target.
    state = {"box": None, "target": 0, "when": 0.0}

    def proc(code, wparam, lparam):
        try:
            box = state["box"]
            if code >= 0 and wparam == WM_MOUSEWHEEL and box:
                info = ctypes.cast(lparam,
                                   ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                if scroll_box_at(user32, info.pt.x, info.pt.y) == box:
                    delta = ctypes.c_short(info.mouseData >> 16).value
                    pos, top = _scroll_range(user32, box)
                    now = time.monotonic()
                    base = state["target"] if now - state["when"] < 0.5 else pos
                    target = wheel_position(base, delta, top)
                    state["target"], state["when"] = target, now
                    user32.PostMessageW(box, WM_VSCROLL,
                                        SB_THUMBPOSITION | (target << 16), 0)
                    return 1
        except Exception:  # a hook that raises stalls every mouse in Windows
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    callback = _HOOKPROC(proc)   # held for the thread's lifetime
    hook = None
    msg = wintypes.MSG()
    point = wintypes.POINT()
    while True:
        try:
            box = None
            if user32.GetCursorPos(ctypes.byref(point)):
                box = scroll_box_at(user32, point.x, point.y)
            if box and not hook and mutex and kernel32.WaitForSingleObject(
                    mutex, 0) in (WAIT_OBJECT_0, WAIT_ABANDONED):
                hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback,
                                                module, 0)
                if not hook:
                    kernel32.ReleaseMutex(mutex)
            elif not box and hook:
                user32.UnhookWindowsHookEx(hook)
                hook = None
                kernel32.ReleaseMutex(mutex)
            state["box"] = box
            # The hook is called while this thread waits for messages.
            user32.MsgWaitForMultipleObjects(0, None, False,
                                             int(POLL_SECONDS * 1000),
                                             QS_ALLINPUT)
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0,
                                      PM_REMOVE):
                pass
        except Exception as e:   # never take the server down
            logger.debug("panel wheel: %s", e)


def start() -> bool:
    """Start the wheel thread once, on Windows only."""
    global _started
    if _started or sys.platform != "win32":
        return False
    _started = True
    threading.Thread(target=_run, name="panel-wheel", daemon=True).start()
    return True
