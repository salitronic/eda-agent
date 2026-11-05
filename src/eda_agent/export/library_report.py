# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A library report as one HTML page.

Altium's Library Report runs only from its dialog, so the report is built
here from what the bridge reads: each symbol of a schematic library with
its designator, parts, pins, parameters and models, or each footprint of
a PCB library with its pads. The page is self-contained.
"""
from __future__ import annotations

from html import escape
from typing import Any


def _cell(value: Any) -> str:
    return escape("" if value is None else str(value))


def _symbol_rows(entries: list[dict[str, Any]]) -> list[str]:
    rows = []
    for e in entries:
        params = e.get("parameters") or {}
        param_text = "<br>".join(
            f"{_cell(k)} = {_cell(v)}" for k, v in sorted(params.items()))
        models = "<br>".join(
            f"{_cell(m.get('model_type'))}: {_cell(m.get('model_name'))}"
            for m in (e.get("models") or []))
        rows.append(
            "<tr>"
            f"<td>{_cell(e.get('name'))}</td>"
            f"<td>{_cell(e.get('description'))}</td>"
            f"<td>{_cell(e.get('designator'))}</td>"
            f"<td class=n>{_cell(e.get('part_count'))}</td>"
            f"<td class=n>{_cell(e.get('pin_count'))}</td>"
            f"<td>{models}</td>"
            f"<td>{param_text}</td>"
            "</tr>")
    return rows


def _footprint_rows(entries: list[dict[str, Any]]) -> list[str]:
    rows = []
    for e in entries:
        rows.append(
            "<tr>"
            f"<td>{_cell(e.get('name'))}</td>"
            f"<td>{_cell(e.get('description'))}</td>"
            f"<td class=n>{_cell(e.get('pad_count'))}</td>"
            f"<td>{_cell(', '.join(e.get('pad_names') or []))}</td>"
            "</tr>")
    return rows


def render_library_report(library_path: str, kind: str,
                          entries: list[dict[str, Any]],
                          unread: list[str] | None = None) -> str:
    """The report page for one library.

    Args:
        library_path: The library the entries were read from.
        kind: ``"schlib"`` or ``"pcblib"``.
        entries: One dict per symbol (name, description, designator,
            part_count, pin_count, models, parameters) or per footprint
            (name, description, pad_count, pad_names).
        unread: Names that could not be read; listed so the page does
            not pass for complete.
    """
    if kind == "schlib":
        head = ("<tr><th>Symbol</th><th>Description</th><th>Designator</th>"
                "<th>Parts</th><th>Pins</th><th>Models</th>"
                "<th>Parameters</th></tr>")
        rows = _symbol_rows(entries)
        noun = "symbols"
    elif kind == "pcblib":
        head = ("<tr><th>Footprint</th><th>Description</th><th>Pads</th>"
                "<th>Pad names</th></tr>")
        rows = _footprint_rows(entries)
        noun = "footprints"
    else:
        raise ValueError(f"kind is schlib or pcblib, not {kind!r}")
    missing = ""
    if unread:
        missing = ("<p class=warn>Not read: "
                   + escape(", ".join(unread)) + "</p>")
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>{escape(library_path)}</title><style>"
        "body{font:13px sans-serif;margin:16px}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #bbb;padding:4px 6px;vertical-align:top;"
        "text-align:left}th{background:#eee}td.n{text-align:right}"
        ".warn{color:#a00}</style></head><body>"
        f"<h1>{escape(library_path)}</h1>"
        f"<p>{len(entries)} {noun}</p>{missing}"
        f"<table>{head}{''.join(rows)}</table></body></html>")
