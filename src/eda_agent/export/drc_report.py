# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What Altium's batch DRC report says, read from the HTML it writes.

The bridge's DRC returns the violations left on the board; the report
adds what they cannot: Altium's own totals (warnings, rule violations,
waived violations, kept apart), every rule it checked with its count (a
rule that is not listed was not checked, which is not the same as passing),
and whether it stopped early at its violation limit.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Optional

_SUMMARY = (("Warnings:", "warnings"), ("Rule Violations:", "rule_violations"),
            ("Waived Violations:", "waived_violations"))


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def read_drc_report(path: str) -> Optional[dict[str, Any]]:
    """The report's totals, rules and early stop; None when it is not one.

    ``rules`` is None when the rule table could not be read whole, so an
    unread table never passes for an empty one.
    """
    report = Path(path)
    if report.suffix.lower() not in (".html", ".htm") or not report.is_file():
        return None
    try:
        page = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    counts: dict[str, int] = {}
    for label, key in _SUMMARY:
        m = re.search(r'class="DRC_summary_header_col1">\s*' + re.escape(label)
                      + r'\s*</td>.*?class="DRC_summary_header_col3"[^>]*>\s*(\d+)\s*<',
                      page, re.IGNORECASE | re.DOTALL)
        if m:
            counts[key] = int(m.group(1))
    if "rule_violations" not in counts:
        return None
    out: dict[str, Any] = {"counts": counts, "rules": None}
    table = re.search(r'<th[^>]*class="column1"[^>]*>\s*Rule Violations\s*</th>(.*?)</table>',
                      page, re.IGNORECASE | re.DOTALL)
    if table:
        body = table.group(1)
        pairs = re.findall(r'<td[^>]*class="column1"[^>]*>(.*?)</td>\s*'
                           r'<td[^>]*class="column2"[^>]*>(.*?)</td>',
                           body, re.IGNORECASE | re.DOTALL)
        rows, total = [], None
        for label_html, count_html in pairs:
            label, count = _text(label_html), _text(count_html)
            if not count.isdigit():
                rows = None
                break
            if label.lower() == "total":
                total = int(count)
            else:
                rows.append({"rule": label, "violations": int(count)})
        # Every row read, or none trusted: one more <tr> than rows is the
        # Total row.
        if rows is not None and total is not None and \
                len(re.findall(r"<tr\b", body, re.IGNORECASE)) == len(rows) + 1:
            out["rules"] = rows
            out["rules_total"] = total
            out["rules_add_up"] = sum(r["violations"] for r in rows) == counts["rule_violations"]
    stop = re.search(r"More than\s+(\d+)\s+violations detected,\s*DRC was stopped",
                     page, re.IGNORECASE)
    out["stopped_at_limit"] = stop is not None
    if stop:
        out["violation_limit"] = int(stop.group(1))
    return out
