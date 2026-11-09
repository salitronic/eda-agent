# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A SPICE model file checked before it is attached to a part.

Three things went wrong silently before: a model name that is not declared
in the file (the simulation fails much later, naming the netlist), a
subcircuit with more nodes than the part has pins (a node left floating),
and a file written from datasheet reasoning instead of the vendor's own
model. A vendor's file names who published it in its header comments.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

#: Element letter and the .MODEL types it takes.
MODEL_TYPES = {"D": ("D",), "Q": ("NPN", "PNP"), "M": ("NMOS", "PMOS", "VDMOS"),
               "J": ("NJF", "PJF"), "Z": ("NMF", "PMF")}

_DECL = re.compile(r"^\s*\.(subckt|model)\s+(\S+)\s*(.*)$", re.I)
_ENDS = re.compile(r"^\s*\.ends\b", re.I)
#: Words a publisher's header carries: an owner, a company, a link.
_PUBLISHER = re.compile(
    r"copyright|\(c\)|\u00a9|https?://|www\.|\binc\b|\bcorp|\bltd\b|\bgmbh\b|"
    r"\bllc\b|semiconductor|instruments|technolog|electronics|microchip|"
    r"nexperia|infineon|onsemi|vishay|rohm|toshiba|diodes inc|littelfuse|"
    r"renesas|analog devices|linear technology|maxim|stmicro|texas|nxp|"
    r"wolfspeed|bourns|murata|tdk|panasonic|allegro|monolithic power|"
    r"richtek|silicon labs|alpha ?& ?omega|taiwan semi|mccsemi|epc\b",
    re.I)


def _logical_lines(text: str) -> list[str]:
    """Lines with ``+`` continuations joined on."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("+") and out:
            out[-1] += " " + line.lstrip()[1:]
        else:
            out.append(line)
    return out


def _comment(line: str) -> Optional[str]:
    s = line.strip()
    if s.startswith(("*", ";", "$", "//")):
        return s.lstrip("*;$/ ").strip()
    return None


def check_model_file(path: str, model_name: str, primitive: str = "X",
                     pin_count: Optional[int] = None) -> dict[str, Any]:
    """``{ok, kind, nodes, publisher, reason?, note?}`` for ``model_name``
    in ``path`` attached as element ``primitive``."""
    f = Path(path)
    if not f.is_file():
        return {"ok": False, "reason": f"no file at {path}"}
    text = f.read_text(encoding="utf-8", errors="replace")
    lines = _logical_lines(text)
    want = model_name.strip().lower()
    prim = (primitive or "X").strip().upper()[:1]
    found = None
    declared: list[str] = []
    for i, line in enumerate(lines):
        m = _DECL.match(line)
        if not m:
            continue
        kind, name, rest = m.group(1).lower(), m.group(2), m.group(3)
        declared.append(f".{kind.upper()} {name}")
        if name.lower() == want:
            found = (i, kind, rest)
            break
    if found is None:
        return {"ok": False, "reason": (
            f"{f.name} declares no .SUBCKT or .MODEL named {model_name!r}"
            + (f"; it declares {', '.join(declared[:12])}" if declared
               else "; it declares no model at all"))}
    at, kind, rest = found
    header = [c for c in (_comment(ln) for ln in lines[:at]) if c]
    near = [c for c in (_comment(ln) for ln in lines[at:at + 40]) if c]
    publisher = next((c for c in header + near if _PUBLISHER.search(c)), "")
    out: dict[str, Any] = {"ok": True, "kind": kind, "file": str(f),
                           "publisher": publisher[:160]}
    if not publisher:
        return {**out, "ok": False, "reason": (
            f"{f.name} names no publisher in its comments: a vendor model "
            "carries the manufacturer's header (company, copyright or web "
            "address). Attach the file as the vendor published it; never "
            "one written from the datasheet")}
    if kind == "subckt":
        if prim != "X":
            return {**out, "ok": False, "reason": (
                f"{model_name} is a .SUBCKT, which is attached with "
                f"primitive 'X', not {prim!r}")}
        nodes = [n for n in rest.split()
                 if "=" not in n and n.lower() != "params:"]
        params_at = next((i for i, n in enumerate(rest.split())
                          if n.lower() == "params:"), None)
        if params_at is not None:
            nodes = [n for n in rest.split()[:params_at] if "=" not in n]
        out["nodes"] = nodes
        if not any(_ENDS.match(ln) for ln in lines[at + 1:]):
            return {**out, "ok": False, "reason": (
                f".SUBCKT {model_name} has no .ENDS after it: the file is cut short")}
        if pin_count is not None:
            out["pins"] = pin_count
            if len(nodes) > pin_count:
                return {**out, "ok": False, "reason": (
                    f".SUBCKT {model_name} has {len(nodes)} nodes "
                    f"({' '.join(nodes)}) and the part {pin_count} pins: "
                    "a node would be left unconnected. Use the part whose "
                    "pins the model has, or the vendor's model for this one")}
            if len(nodes) < pin_count:
                out["note"] = (f"{pin_count - len(nodes)} pin(s) of the part "
                               "have no node in the model; map them as not "
                               "connected")
    else:
        mtype = (rest.replace("(", " ").split() or [""])[0].upper()
        out["model_type"] = mtype
        allowed = MODEL_TYPES.get(prim)
        if prim == "X":
            return {**out, "ok": False, "reason": (
                f"{model_name} is a .MODEL {mtype}, attached with its element "
                f"letter (D, Q, M or J), not 'X'")}
        if allowed and mtype not in allowed:
            return {**out, "ok": False, "reason": (
                f".MODEL {model_name} is of type {mtype}; primitive {prim!r} "
                f"takes {', '.join(allowed)}")}
    return out
