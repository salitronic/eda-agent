# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A project BOM as one variant builds it.

Takes the base BOM (one entry per placed part, as ``proj_get_bom`` reads
it) and one variant from ``proj_list_variants``, and applies the
variant's entries: a not-fitted part leaves the BOM, an alternate part
takes the alternate's library reference and footprint, and varied
parameter values replace the base ones.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

_KINDS = {"fitted": "fitted", "not fitted": "not_fitted",
          "alternate": "alternate"}


def find_variant(variants: list[dict[str, Any]], wanted: str) -> Optional[dict[str, Any]]:
    """The variant whose name or description is ``wanted``, ignoring case."""
    key = wanted.strip().upper()
    for v in variants:
        if key in (str(v.get("name", "")).upper(),
                   str(v.get("description", "")).upper()):
            return v
    return None


def apply_variant(components: list[dict[str, Any]],
                  variant: dict[str, Any]) -> dict[str, Any]:
    """The BOM ``components`` as ``variant`` builds them.

    Returns ``components`` (the parts that are built, each with
    ``variation``: none, fitted or alternate), ``not_fitted`` (their
    designators), ``alternates``, ``varied`` (parts whose parameter values
    differ from the base) and ``entries_not_in_bom`` (variant entries for
    designators the BOM does not list).
    """
    entries: dict[str, dict[str, Any]] = {}
    for e in variant.get("variations") or []:
        d = str(e.get("designator", "")).strip().upper()
        if d:
            entries[d] = e

    built: list[dict[str, Any]] = []
    not_fitted: list[str] = []
    alternates: list[str] = []
    varied: list[str] = []
    seen: set[str] = set()
    for comp in components:
        desig = str(comp.get("designator", ""))
        entry = entries.get(desig.strip().upper())
        seen.add(desig.strip().upper())
        out = copy.deepcopy(comp)
        if entry is None:
            out["variation"] = "none"
            built.append(out)
            continue
        kind = _KINDS.get(str(entry.get("kind", "")).strip().lower(), "unknown")
        if kind == "not_fitted":
            not_fitted.append(desig)
            continue
        out["variation"] = kind
        if kind == "alternate":
            link = entry.get("alternate_link") or {}
            out["alternate"] = {"part": entry.get("alternate_part", ""), **link}
            if link.get("symbol_reference"):
                out["lib_ref"] = link["symbol_reference"]
            if link.get("footprint") and not link.get("same_footprint"):
                out["footprint"] = link["footprint"]
            alternates.append(desig)
        params = entry.get("parameters") or []
        if params:
            values = {str(p.get("name", "")): str(p.get("value", ""))
                      for p in params if p.get("name")}
            out["varied_parameters"] = values
            for name, value in values.items():
                if name.lower() == "comment":
                    out["comment"] = value
            varied.append(desig)
        built.append(out)

    missing = sorted(str(e.get("designator", "")) for d, e in entries.items()
                     if d not in seen)
    return {"variant": variant.get("name", ""),
            "components": built, "count": len(built),
            "not_fitted": not_fitted, "alternates": alternates,
            "varied": varied, "entries_not_in_bom": missing}
