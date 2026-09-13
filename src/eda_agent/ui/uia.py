# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""UI Automation: the modern accessible layer, tried before the pixels.

This project read Altium three ways and each has a gap:

  window text      GetWindowText and WM_GETTEXT, which return '' for
                   anything that owns no window handle
  MSAA (oleacc)    ui/controls.py, which handles Altium's VCL dialogs
                   well and Altium's WPF dialogs not at all
  OCR              ui/ocr.py, the last resort, reading pixels

UIA sits between the second and the third and was missing. It matters
most for the case that forced OCR into existence: Altium's newer WPF
dialogs, whose buttons are NOT child windows, so EnumChildWindows finds
nothing, MSAA finds nothing to press, and the only previous answer was a
keystroke chosen from an OCR'd caption. WPF is UIA-native, so those same
buttons are ordinary elements here, with names and an Invoke pattern.

WHAT IT DOES NOT FIX. Delphi's TLabel is a TGraphicControl: it owns no
handle and publishes no accessible object, so its text exists only as
pixels. UIA cannot see it any more than MSAA could, and ui/ocr.py stays
the answer there. The order is UIA, then MSAA, then OCR, because that is
cheapest and most reliable first.

PATTERNS ARE THE REAL GAIN. MSAA offers accDoDefaultAction and little
else, which is why controls.py falls back to moving a real mouse. UIA
exposes what a control can actually do: Invoke to press, Value to set
text, ExpandCollapse to open a node, SelectionItem to choose. Those are
supported operations rather than a simulated click, so they neither move
the pointer nor need the window in front.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

try:                                            # pragma: no cover - platform
    import comtypes.client as _cc

    _cc.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as _UIA

    _AVAILABLE = True
except Exception:                               # pragma: no cover - platform
    _AVAILABLE = False

#: Cached automation object. Creating it is the expensive part and it is
#: stateless, so one per process is right. A fresh one per call showed up
#: as latency on every read.
_AUTOMATION = None


def available() -> bool:
    """Whether UIA can be used on this host."""
    return _AVAILABLE


def _uia():
    global _AUTOMATION
    if not _AVAILABLE:
        return None
    if _AUTOMATION is None:
        try:
            _AUTOMATION = _cc.CreateObject(
                _UIA.CUIAutomation, interface=_UIA.IUIAutomation)
        except Exception:                       # noqa: BLE001
            return None
    return _AUTOMATION


def _element(hwnd: int):
    auto = _uia()
    if auto is None:
        return None
    try:
        return auto.ElementFromHandle(hwnd)
    except Exception:                           # noqa: BLE001
        return None


def _children(element) -> list:
    """Direct children of an element, as a plain list."""
    auto = _uia()
    if auto is None or element is None:
        return []
    try:
        condition = auto.CreateTrueCondition()
        found = element.FindAll(2, condition)    # TreeScope_Children
        return [found.GetElement(i) for i in range(found.Length)]
    except Exception:                           # noqa: BLE001
        return []


def _describe(element) -> dict:
    """Name, type, state and rectangle of one element."""
    def attr(name, default=None):
        try:
            return getattr(element, name)
        except Exception:                       # noqa: BLE001
            return default

    rect = attr("CurrentBoundingRectangle")
    box = None
    if rect is not None:
        try:
            box = [int(rect.left), int(rect.top),
                   int(rect.right - rect.left), int(rect.bottom - rect.top)]
        except Exception:                       # noqa: BLE001
            box = None
    return {
        "name": str(attr("CurrentName", "") or ""),
        "type": str(attr("CurrentLocalizedControlType", "") or ""),
        "automation_id": str(attr("CurrentAutomationId", "") or ""),
        "class": str(attr("CurrentClassName", "") or ""),
        "enabled": bool(attr("CurrentIsEnabled", True)),
        "offscreen": bool(attr("CurrentIsOffscreen", False)),
        "rect": box,
    }


def describe_window(hwnd: int, depth: int = 4, limit: int = 400) -> dict:
    """Every element under a window, flattened, shallowest first.

    The WPF answer to controls.describe_all. Where that walks child
    WINDOW handles and finds none in a WPF dialog, this walks the UIA
    tree, where the same buttons are ordinary elements.

    BREADTH-FIRST, because the element budget is spent in walk order.
    Depth-first let the first deep subtree take all of it: on an
    Engineering Change Order with 47 rows the grid came first in tree
    order, filled the 400-element limit, and the dialog's buttons were
    never reached. The caller saw one button of five and nothing said
    four were missing. Walking level by level reads everything near the
    root before anything deep.

    ``truncated`` is True when the limit cut the walk short, meaning at
    least one more element was found and left out. A tree that ends at
    exactly ``limit`` elements is not truncated. ``depth`` is the
    caller's choice, so stopping there is not reported as truncation.
    """
    root = _element(hwnd)
    if root is None:
        return {"ok": False, "reason": (
            "UI Automation could not attach to that window. It is "
            "available on this host, so the window is likely gone")}

    out: list[dict] = []
    truncated = False
    queue = deque([(root, 0)])
    while queue and not truncated:
        node, level = queue.popleft()
        for child in _children(node):
            info = _describe(child)
            if info["name"] or info["type"]:
                if len(out) >= limit:
                    truncated = True
                    break
                info["depth"] = level
                out.append(info)
            if level + 1 <= depth:
                queue.append((child, level + 1))

    reply = {"ok": True, "hwnd": hwnd, "count": len(out),
             "truncated": truncated, "limit": limit,
             "elements": out,
             "root": _describe(root)}
    if truncated:
        reply["note"] = (
            f"stopped at the {limit}-element limit with more of the tree "
            f"unread. Elements are listed shallowest first, so what is "
            f"missing is the deepest part of the tree. Pass a larger "
            f"limit to read it")
    return reply


def text_of(hwnd: int) -> list:
    """Every readable string under a window, shallowest first.

    Tried BEFORE ocr.read_window_text. When UIA can see the text this is
    exact, where OCR is a hint that confuses 0 with O. When it cannot,
    the caller still has the pixels.
    """
    described = describe_window(hwnd)
    if not described.get("ok"):
        return []
    seen, out = set(), []
    for element in described["elements"]:
        name = element["name"].strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _find(hwnd: int, name: str, control_type: Optional[str] = None):
    """One element by name, ignoring case and accelerators."""
    def flat(text):
        return str(text or "").replace("&", "").strip().lower()

    root = _element(hwnd)
    if root is None:
        return None
    wanted = flat(name)

    stack = [(root, 0)]
    while stack:
        node, level = stack.pop()
        if level > 6:
            continue
        for child in _children(node):
            info = _describe(child)
            if flat(info["name"]) == wanted or flat(info["automation_id"]) == wanted:
                if control_type is None or flat(info["type"]) == flat(control_type):
                    return child, info
            stack.append((child, level + 1))
    return None


def _pattern(element, pattern_id: int, interface):
    try:
        raw = element.GetCurrentPattern(pattern_id)
        if not raw:
            return None
        return raw.QueryInterface(interface)
    except Exception:                           # noqa: BLE001
        return None


#: Pattern ids from UIAutomationClient. Named because a bare 10000 in a
#: call is unreadable.
_PATTERN_INVOKE = 10000
_PATTERN_SELECTION_ITEM = 10010
_PATTERN_VALUE = 10002
_PATTERN_EXPAND_COLLAPSE = 10005
_PATTERN_TOGGLE = 10015


def invoke(hwnd: int, name: str) -> dict:
    """Press an element by name, through its Invoke pattern.

    NO POINTER MOVES AND NO FOCUS IS TAKEN. That is the difference from
    every click in this layer: Invoke is a supported operation the
    control implements, not a simulated gesture, so it works on a window
    that is not in front and cannot land on whatever happens to be under
    the cursor.
    """
    if not _AVAILABLE:
        return {"ok": False, "reason": "UI Automation is unavailable here"}
    hit = _find(hwnd, name)
    if hit is None:
        return {"ok": False, "reason": f"no element named {name!r}",
                "offered": text_of(hwnd)[:60]}
    element, info = hit
    if not info["enabled"]:
        return {"ok": False, "reason": f"{info['name']!r} is disabled"}

    pattern = _pattern(element, _PATTERN_INVOKE, _UIA.IUIAutomationInvokePattern)
    if pattern is None:
        # Some elements are selectable rather than invokable, which is
        # how WPF models a list item and a tab.
        pattern = _pattern(element, _PATTERN_SELECTION_ITEM,
                           _UIA.IUIAutomationSelectionItemPattern)
        if pattern is None:
            return {"ok": False, "element": info["name"], "reason": (
                "that element supports neither Invoke nor SelectionItem, "
                "so UIA offers no way to press it. A real click may still "
                "work: its rectangle is in the reply"), "rect": info["rect"]}
        try:
            pattern.Select()
            return {"ok": True, "element": info["name"], "how": "select"}
        except Exception as exc:                # noqa: BLE001
            return {"ok": False, "element": info["name"],
                    "reason": f"Select failed: {exc}"}
    try:
        pattern.Invoke()
        return {"ok": True, "element": info["name"], "how": "invoke"}
    except Exception as exc:                    # noqa: BLE001
        return {"ok": False, "element": info["name"],
                "reason": f"Invoke failed: {exc}"}


def set_value(hwnd: int, name: str, text: str) -> dict:
    """Set an element's value through the Value pattern, and read it back."""
    if not _AVAILABLE:
        return {"ok": False, "reason": "UI Automation is unavailable here"}
    hit = _find(hwnd, name)
    if hit is None:
        return {"ok": False, "reason": f"no element named {name!r}",
                "offered": text_of(hwnd)[:60]}
    element, info = hit

    pattern = _pattern(element, _PATTERN_VALUE,
                       _UIA.IUIAutomationValuePattern)
    if pattern is None:
        return {"ok": False, "element": info["name"], "reason": (
            "that element has no Value pattern, so it does not accept a "
            "value this way")}
    try:
        if pattern.CurrentIsReadOnly:
            return {"ok": False, "element": info["name"],
                    "reason": "that element reports itself read-only"}
        pattern.SetValue(str(text))
    except Exception as exc:                    # noqa: BLE001
        return {"ok": False, "element": info["name"],
                "reason": f"SetValue failed: {exc}"}

    try:
        got = str(pattern.CurrentValue)
    except Exception:                           # noqa: BLE001
        got = None
    if got is not None and got != str(text):
        return {"ok": False, "element": info["name"], "value": got,
                "reason": f"set {text!r} and it reads {got!r}"}
    return {"ok": True, "element": info["name"], "value": got}


def expand(hwnd: int, name: str, want: bool = True) -> dict:
    """Open or close a node through the ExpandCollapse pattern."""
    if not _AVAILABLE:
        return {"ok": False, "reason": "UI Automation is unavailable here"}
    hit = _find(hwnd, name)
    if hit is None:
        return {"ok": False, "reason": f"no element named {name!r}"}
    element, info = hit

    pattern = _pattern(element, _PATTERN_EXPAND_COLLAPSE,
                       _UIA.IUIAutomationExpandCollapsePattern)
    if pattern is None:
        return {"ok": False, "element": info["name"], "reason": (
            "that element has no ExpandCollapse pattern, so it has "
            "nothing to open")}
    try:
        #: 0 collapsed, 1 expanded, 2 partially, 3 leaf
        state = int(pattern.CurrentExpandCollapseState)
        if state == 3:
            return {"ok": False, "element": info["name"],
                    "reason": "that node is a leaf and has no children"}
        if (state == 1) == want:
            return {"ok": True, "element": info["name"], "expanded": want,
                    "changed": False, "note": "already in that state"}
        if want:
            pattern.Expand()
        else:
            pattern.Collapse()
        now = int(pattern.CurrentExpandCollapseState) == 1
        return {"ok": now == want, "element": info["name"], "expanded": now,
                "changed": True}
    except Exception as exc:                    # noqa: BLE001
        return {"ok": False, "element": info["name"],
                "reason": f"ExpandCollapse failed: {exc}"}
