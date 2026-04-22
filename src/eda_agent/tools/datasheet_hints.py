# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Datasheet-discipline guidance injected into component-touching responses.

The agent's job in any design task is to ground conclusions in the
actual manufacturer datasheet, not in library metadata. This module
is the single source of truth for that rule.

Every tool that surfaces component information (BOM, component list,
design diff, library search result, simulation readiness, design
review) calls ``tag_response(response, parts)`` before returning.
The caller gets two extra fields:

  - ``_datasheet_guidance``  — rules + action_required text + structured
                               per-part search hints
  - ``_datasheet_parts``     — the unique (manufacturer, part_number,
                               designators) list the LLM should fetch
                               datasheets for

The rules make it explicit that when a datasheet isn't already on
hand, the LLM MUST use ``WebSearch`` / ``WebFetch`` to locate and
download the PDF from the manufacturer before drawing conclusions.
"""

from __future__ import annotations

from typing import Any


DATASHEET_RULES: list[str] = [
    "The manufacturer datasheet is the only authoritative source "
    "for pin function, voltage rating, current limit, timing, and "
    "any electrical spec. Library symbol metadata (Description, "
    "Comment, Value fields, footprint assignments) can be wrong or "
    "outdated and must not be trusted for any conclusion.",
    "Before proposing any fix, pin change, part substitution, or "
    "rule/clearance decision on a specific part: open and read the "
    "actual datasheet.",
    "If the datasheet is not already available locally or in the "
    "conversation, you MUST search for it with WebSearch "
    "('<manufacturer> <part_number> datasheet filetype:pdf') and "
    "fetch it with WebFetch. Do not skip this step. Do not answer "
    "'I'll assume ...' about an electrical spec — find the sheet.",
    "When citing a claim derived from a datasheet, include the page "
    "or section you relied on. If you cannot cite it, you have not "
    "actually verified — say so and go read the datasheet.",
    "Never fabricate, guess, or LLM-generate datasheet-derived "
    "values (pin functions, Vmax, Vmin, absolute maximums, sim "
    "models). If a datasheet is genuinely unavailable after a real "
    "search (proprietary, obsolete), flag that to the user and stop "
    "— do not substitute a plausible guess.",
]


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def extract_unique_parts(
    components: Any = None,
    bom: Any = None,
) -> list[dict[str, str]]:
    """Pull unique (manufacturer, part_number, designators) triples from
    either a components-list response or a BOM-shaped response.

    BOM data takes priority (it carries manufacturer part numbers).
    Falls back to the component-list's Comment/Value field when no BOM
    is present. Dedup is case-insensitive on (manufacturer, part_number).
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []

    def _push(mfr: str, part: str, desig: str) -> None:
        if not part:
            return
        key = (mfr.lower(), part.lower())
        if key in seen:
            return
        seen.add(key)
        out.append({
            "manufacturer": mfr,
            "part_number": part,
            "designators": desig,
        })

    if isinstance(bom, dict):
        rows = bom.get("bom") or bom.get("items") or bom.get("rows") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mfr = _normalize(
                    row.get("Manufacturer") or row.get("manufacturer")
                )
                part = _normalize(
                    row.get("ManufacturerPartNumber")
                    or row.get("manufacturer_part_number")
                    or row.get("PartNumber")
                    or row.get("part_number")
                    or row.get("Comment")
                    or row.get("comment")
                )
                desig = _normalize(
                    row.get("Designator")
                    or row.get("designator")
                    or row.get("Designators")
                )
                _push(mfr, part, desig)

    # Components are a fallback source — BOM manufacturer part numbers
    # are authoritative when present. Only walk the component list when
    # BOM produced nothing, to avoid merging the same parts under
    # inconsistent names (e.g. "STM32F411RE" in the BOM vs a shortened
    # "STM32F411" in the symbol Comment field).
    if not out and isinstance(components, dict):
        rows = components.get("components") or components.get("items") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mfr = _normalize(
                    row.get("Manufacturer")
                    or row.get("manufacturer")
                    or row.get("ManufacturerName")
                )
                part = _normalize(
                    row.get("ManufacturerPartNumber")
                    or row.get("manufacturer_part_number")
                    or row.get("PartNumber")
                    or row.get("part_number")
                    or row.get("Comment")
                    or row.get("comment")
                    or row.get("value")
                )
                desig = _normalize(
                    row.get("designator")
                    or row.get("Designator")
                )
                _push(mfr, part, desig)

    return out


def _search_hint(part: dict[str, str]) -> dict[str, str]:
    mfr = part.get("manufacturer", "")
    pn = part.get("part_number", "")
    query_part = f"{mfr} {pn}".strip() if mfr else pn
    return {
        "manufacturer": mfr,
        "part_number": pn,
        "designators": part.get("designators", ""),
        "datasheet_query": f"{query_part} datasheet filetype:pdf",
        "vendor_product_query": f"{query_part} site:{mfr.lower()}.com" if mfr else "",
    }


def build_guidance_block(
    parts: list[dict[str, str]] | None = None,
    context: str = "",
) -> dict[str, Any]:
    """Build the ``_datasheet_guidance`` dict that gets attached to
    every component-returning response.

    ``context`` is an optional short string describing where this was
    tagged from (e.g. "bom", "components", "design_diff"). It lands
    in the guidance so the LLM can see which flow triggered the
    reminder, but it's purely informational.
    """
    parts = parts or []
    return {
        "datasheet_rules": DATASHEET_RULES,
        "action_required": (
            "For every manufacturer part number in _datasheet_parts "
            "that you don't already have a datasheet for: use "
            "WebSearch with the provided datasheet_query, then "
            "WebFetch the PDF, and ground any decision about that "
            "part in what the datasheet actually says. Do not guess, "
            "do not assume, do not rely on the symbol's Comment or "
            "Description field. If the datasheet is genuinely not "
            "available after a real search, flag that explicitly and "
            "ask the user."
        ),
        "unique_part_count": len(parts),
        "search_hints": [_search_hint(p) for p in parts],
        "reminder": (
            "The symbol, footprint, Comment, and parameter fields can "
            "be wrong. The manufacturer datasheet is ground truth. "
            "Fetch it with WebFetch if you don't already have it."
        ),
        "context": context,
    }


def tag_response(
    response: Any,
    *,
    components: Any = None,
    bom: Any = None,
    explicit_parts: list[dict[str, str]] | None = None,
    context: str = "",
) -> Any:
    """Attach ``_datasheet_guidance`` + ``_datasheet_parts`` to a response.

    Call signatures:
      - Most call-sites pass ``components=response`` or ``bom=response``
        and let the helper extract parts.
      - For responses that already carry a curated part list (e.g.,
        readiness), pass ``explicit_parts`` directly.

    No-ops gracefully if ``response`` isn't a dict — the caller's
    result is returned unchanged so this is safe to wrap every
    send_command_async return.
    """
    if not isinstance(response, dict):
        return response
    if explicit_parts is not None:
        parts = explicit_parts
    else:
        parts = extract_unique_parts(components=components, bom=bom)
    response["_datasheet_parts"] = parts
    response["_datasheet_guidance"] = build_guidance_block(parts, context)
    return response
