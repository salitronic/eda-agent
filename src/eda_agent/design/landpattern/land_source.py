# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Refuse a footprint cited to a page the datasheet's land pattern is not on.

The VL53L5CX was built three times from the wrong drawing: twice from the
package's own pads (bottom view, p. 27) and once from a misread, with the
agent saying "ST gives no land pattern" after reading pp. 6-7 and 26-29.
ST's recommended solder pattern is Fig. 28 on p. 33. With the datasheet's
path in the citation, the pages that publish a land pattern are listed and
a source page that is none of them is refused, unless the citation says
the datasheet has none for this package (a datasheet covering several
packages often gives a pattern for some and not others).
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from ..datasheet_search import land_pattern_pages

#: The words in source.ref that say the pages found are not this package's.
NO_PATTERN = "no land pattern"


def cited_pages(page: str) -> set[int]:
    """1-based pages a citation names: "33", "26-27", "p. 33, 58"."""
    out: set[int] = set()
    for m in re.finditer(r"(\d+)\s*(?:-|\u2013|to)\s*(\d+)|(\d+)", page or ""):
        lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (
            int(m.group(3)), int(m.group(3)))
        if lo <= hi and hi - lo < 50:
            out.update(range(lo, hi + 1))
    return out


def check_land_source(source: Any) -> Optional[dict[str, Any]]:
    """``None`` when there is nothing to check (no path), else ``{ok, ...}``;
    ``ok`` false is a refusal with the pages and the lines that show why."""
    path = str(getattr(source, "path", "") or "")
    if not path:
        return None
    if not os.path.isfile(path):
        return {"ok": False, "reason": f"source.path names no file: {path}"}
    found = land_pattern_pages(path)
    if not found.get("ok"):
        return {"ok": True, "checked": False, "note": found.get("reason")}
    pages = found.get("pages") or []
    if not pages:
        return {"ok": True, "checked": True, "land_pattern_pages": []}
    cited = cited_pages(str(getattr(source, "page", "") or ""))
    numbers = [p["page"] for p in pages]
    if cited & set(numbers):
        return {"ok": True, "checked": True, "land_pattern_pages": numbers}
    if NO_PATTERN in str(getattr(source, "ref", "") or "").lower():
        return {"ok": True, "checked": True, "land_pattern_pages": numbers,
                "note": "source.ref says the datasheet has no land pattern "
                        "for this package"}
    shown = "; ".join(f"p. {p['page']}: {' / '.join(p['lines'])}" for p in pages[:6])
    return {"ok": False, "land_pattern_pages": numbers, "reason": (
        f"the datasheet publishes a land pattern on p. {', '.join(map(str, numbers))}"
        f" and source.page is {getattr(source, 'page', '') or 'empty'}: build the "
        f"lands from that drawing and cite its page, or, if none of these is "
        f"this package's, say \"{NO_PATTERN} for this package\" in source.ref. "
        f"Found: {shown}")}
