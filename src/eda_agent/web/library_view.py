# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard's Library page: the design's own parts traced to the
libraries they come from, and what is wrong with them.

Three reads of the focused project, made once when asked and kept: the
BOM (comment, library reference, footprint), where each footprint model
resolves (``project.component_models``) and the parts' parameters. Parts
are grouped by what they are (library reference, footprint, comment), so
a row is one line of a BOM with its designators.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Optional

#: Parameter names read as the manufacturer part number, the maker, the
#: datasheet and a supplier part number. Matched without case or spaces;
#: libraries name these differently and none is assumed.
_FIELDS = {
    "mpn": ("manufacturerpartnumber", "manufacturerpn", "mfrpn", "mpn", "partnumber",
            "mfrpartnumber", "manufacturerpartno", "mfgpartnumber"),
    "maker": ("manufacturer", "manufacturername", "mfr", "mfg", "vendor"),
    "datasheet": ("datasheet", "datasheeturl", "datasheetlink", "componentlink1url",
                  "helpurl"),
    "symbol_library": ("libraryname", "sourcelibraryname", "sourcelibrary"),
    "supplier": ("lcsc", "lcscpart", "lcscpartnumber", "jlcpcbpart", "supplierpartnumber1",
                 "supplierpartnumber", "digikeypartnumber", "mouserpartnumber"),
}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _field(params: dict, which: str) -> str:
    keyed = {_key(k): v for k, v in (params or {}).items() if str(v or "").strip()
             and str(v).strip() not in ("*", "-", "~")}
    for name in _FIELDS[which]:
        if name in keyed:
            return str(keyed[name]).strip()
    return ""


def _footprint_source(models: list[dict]) -> dict[str, Any]:
    """The current footprint model's library and where Altium finds it."""
    fps = [m for m in models or [] if str(m.get("type", "")).upper() in ("PCBLIB", "FOOTPRINT")]
    cur = next((m for m in fps if m.get("current")), fps[0] if fps else None)
    if cur is None:
        return {"library": "", "found_at": "", "found": False, "linked": False,
                "integrated": False, "entity": "", "source": ""}
    files = cur.get("datafiles") or []
    found = next((f for f in files if f.get("found_at")), None)
    first = found or (files[0] if files else {})
    lib = first.get("library", "")
    at = (found or {}).get("found_at", "")
    # A footprint inside an integrated library resolves to a path like
    # "SELibrary_CAPACITORS:" + "\PCBLib\0.pcblib": the IntLib's name,
    # then its contents. Two letters or more, so a drive is not taken.
    inside = re.match(r"^([^\\/:]{2,}):[\\/]", at)
    integrated = bool(cur.get("integrated")) or bool(inside)
    if inside:
        source = inside.group(1) + ".IntLib"
    else:
        source = os.path.basename(at or lib)
    return {"library": lib, "found_at": at, "found": bool(found) or integrated,
            "linked": bool(lib) or integrated, "integrated": integrated,
            "entity": first.get("entity") or cur.get("name", ""), "source": source}


def _natural(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


def build(bom: list[dict], models: dict[str, list[dict]],
          params: dict[str, dict]) -> dict[str, Any]:
    """Rows (one per kind of part), libraries and the findings over them."""
    groups: dict[tuple, dict] = {}
    for c in bom:
        des = c.get("designator")
        if not des:
            continue
        key = (c.get("lib_ref", ""), c.get("footprint", ""), c.get("comment", ""))
        g = groups.get(key)
        if g is None:
            src = _footprint_source(models.get(des) or [])
            p = params.get(des) or {}
            g = groups[key] = {
                "lib_ref": key[0], "footprint": key[1], "comment": key[2],
                "designators": [], "library": src["library"],
                "library_file": src["found_at"], "found": src["found"],
                "source": src["source"], "entity": src["entity"],
                "linked": src["linked"], "integrated": src["integrated"],
                "mpn": _field(p, "mpn"), "maker": _field(p, "maker"),
                "datasheet": _field(p, "datasheet"), "supplier": _field(p, "supplier"),
                "symbol_library": _field(p, "symbol_library"),
                "issues": []}
        g["designators"].append(des)
    rows = list(groups.values())
    for r in rows:
        r["designators"].sort(key=_natural)
        r["qty"] = len(r["designators"])
        if not r["footprint"]:
            r["issues"].append(("error", "no footprint"))
        elif not r["linked"]:
            r["issues"].append(("error", "the footprint model names no library"))
        elif not r["found"]:
            r["issues"].append(("error", f"Altium does not find {r['entity'] or r['footprint']} "
                                         f"in {r['library']}"))
        if not r["mpn"]:
            r["issues"].append(("warning", "no manufacturer part number"))
        if not r["datasheet"]:
            r["issues"].append(("warning", "no datasheet link"))
    by_ref: dict[str, set] = {}
    by_what: dict[tuple, set] = {}
    for r in rows:
        if r["lib_ref"]:
            by_ref.setdefault(r["lib_ref"], set()).add(r["footprint"])
        if r["comment"] and r["footprint"]:
            by_what.setdefault((r["comment"].lower(), r["footprint"]), set()).add(r["lib_ref"])
    for r in rows:
        fps = by_ref.get(r["lib_ref"]) or set()
        if len(fps) > 1:
            r["issues"].append(("warning", f"{r['lib_ref']} is used with {len(fps)} footprints"))
        refs = by_what.get((r["comment"].lower(), r["footprint"])) or set()
        if len(refs) > 1:
            r["issues"].append(("info", f"{r['comment']} {r['footprint']} comes from "
                                        f"{len(refs)} library parts; could be one"))
        r["issues"] = [{"severity": s, "text": t} for s, t in r["issues"]]
        r["worst"] = min((("error", "warning", "info").index(i["severity"])
                          for i in r["issues"]), default=3)
    rows.sort(key=lambda r: (r["worst"], _natural(r["designators"][0])))
    libs: dict[str, dict] = {}
    for r in rows:
        r["source"] = r["source"] or "(no library)"
        name = r["source"]
        lib = libs.setdefault(name.lower(), {"library": name, "name": name, "paths": [],
                                             "kinds": 0, "parts": 0, "missing": 0})
        if r["library_file"] and r["library_file"] not in lib["paths"]:
            lib["paths"].append(r["library_file"])
        lib["kinds"] += 1
        lib["parts"] += r["qty"]
        lib["missing"] += 0 if r["found"] else r["qty"]
    count = {s: sum(1 for r in rows for i in r["issues"] if i["severity"] == s)
             for s in ("error", "warning", "info")}
    return {"rows": rows, "libraries": sorted(libs.values(), key=lambda l: -l["parts"]),
            "counts": count, "parts": sum(r["qty"] for r in rows), "kinds": len(rows),
            "with_mpn": sum(r["qty"] for r in rows if r["mpn"]),
            "with_datasheet": sum(r["qty"] for r in rows if r["datasheet"])}


class LibraryView:
    """Reads the project once on request, in the background, and keeps it."""

    def __init__(self, send: Optional[Callable[[str, dict, float], Any]] = None) -> None:
        self._send = send
        self.state: dict[str, Any] = {"state": "empty"}
        self._lock = threading.Lock()

    def send(self, command: str, params: dict, timeout: float) -> Any:
        if self._send is not None:
            return self._send(command, params, timeout)
        from ..bridge.altium_bridge import following_front, get_bridge
        with following_front():
            return get_bridge().send_command(command, params, timeout=timeout)

    def read(self, board: str = "") -> dict[str, Any]:
        with self._lock:
            if self.state.get("state") == "reading":
                return {"ok": False, "reason": "already reading"}
            self.state = {"state": "reading", "step": "the BOM", "board": board,
                          "started": time.time()}
        threading.Thread(target=self._read, args=(board,), name="library-view",
                         daemon=True).start()
        return {"ok": True}

    def _read(self, board: str) -> None:
        st = self.state
        try:
            bom = self.send("project.get_bom", {"limit": "100000"}, 180.0) or {}
            comps = [c for c in bom.get("components") or [] if isinstance(c, dict)]
            if not comps:
                raise RuntimeError(bom.get("error") or "the project has no parts; is a "
                                   "project focused in Altium?")
            st["step"] = "where each footprint comes from"
            mod = self.send("project.component_models", {"limit": "100000"}, 300.0) or {}
            models = {p.get("designator"): p.get("models") or []
                      for p in mod.get("parts") or [] if isinstance(p, dict)}
            st["step"] = "the parts' parameters"
            params: dict[str, dict] = {}
            names = [c["designator"] for c in comps if c.get("designator")]
            for i in range(0, len(names), 200):
                r = self.send("project.get_component_info_batch", {
                    "designators": "~~".join(names[i:i + 200]), "with_pin_nets": "false",
                    "with_parameters": "true"}, 120.0) or {}
                for c in r.get("components") or []:
                    if isinstance(c, dict) and c.get("designator"):
                        params[c["designator"]] = c.get("parameters") or {}
            out = build(comps, models, params)
            out.update(state="ready", board=board, read_at=time.time(),
                       seconds=round(time.time() - st["started"], 1),
                       models_read=bool(models))
            self.state = out
        except Exception as e:                            # noqa: BLE001
            self.state = {"state": "failed", "board": board, "error": f"{e}"}

    def snapshot(self) -> dict[str, Any]:
        return self.state


_VIEW: Optional[LibraryView] = None


def get_library_view() -> LibraryView:
    global _VIEW
    if _VIEW is None:
        _VIEW = LibraryView()
    return _VIEW
