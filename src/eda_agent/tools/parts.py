# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Provider-neutral part search and fetch.

Two tools rather than one per source, so adding a provider never grows
the tool surface (see the tool-count concern in issue #10), and so no
single source occupies a privileged name.
"""

from __future__ import annotations

from typing import Any

__all__ = ["register_parts_tools"]



#: Above this many characters the provider's detail is written to the
#: workspace and the reply carries its outline. An EasyEDA part's raw shapes
#: came to 89 KB, which Claude Code set aside in a file under the user's
#: profile and showed the path of.
_DETAIL_INLINE_CHARS = 20_000


def _outline(value: Any, depth: int = 0) -> Any:
    """``value`` with its short scalars kept and everything bulky counted."""
    if isinstance(value, dict):
        if depth >= 2:
            return f"<{len(value)} keys>"
        return {k: _outline(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return f"<{len(value)} entries>"
    if isinstance(value, str) and len(value) > 300:
        return f"<text, {len(value)} characters>"
    return value


def _fit_detail(detail: Any, provider: str, part_id: str) -> dict[str, Any]:
    """Nothing when ``detail`` fits one reply; else its outline in place of
    it, and the whole of it written to the workspace (``detail_path``)."""
    import json

    from ..config import get_config

    text = json.dumps(detail, default=str)
    if len(text) <= _DETAIL_INLINE_CHARS:
        return {}
    folder = get_config().workspace_dir / "parts"
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in f"{provider}_{part_id}")
    path = folder / f"{safe}.json"
    path.write_text(text, encoding="utf-8")
    return {"detail": _outline(detail), "detail_trimmed": len(text),
            "detail_path": str(path),
            "detail_note": ("the provider's full detail is in detail_path; "
                            "summary holds what a part choice needs")}

def register_parts_tools(mcp):
    """Register the provider-neutral part tools with the MCP server."""

    @mcp.tool()
    async def part_search(
        query: str = "",
        limit_per_provider: int = 20,
    ) -> dict[str, Any]:
        """Search EVERY enabled part provider and merge the results.

        All providers count equally; hits are ordered by provider then
        part, which is NOT relevance. A provider that cannot answer says
        why ("unavailable" is not "no such part"). Each hit has a
        ``kind``: "library" gives geometry (symbol or footprint),
        "catalogue" gives identity (MPN, datasheet, stock) and no
        geometry. Remote providers need the internet; local ones still
        answer without it. An empty query lists the providers.

        DATASHEET DISCIPLINE: a hit is a lead. Blank ``provenance`` or
        ``license`` means unknown; audit an imported footprint with
        lib_audit_footprint_vs_datasheet before trusting it.

        Args:
            query: Substring of part name, manufacturer or keywords.
            limit_per_provider: Cap per provider.

        Returns:
            {"ok", "count", "providers": {name: status}, "hits",
            "by_mpn"} (the same part across providers; wildcard part
            numbers are not merged), or {"ok", "providers"} for an empty
            query.
        """
        from eda_agent.libimport.providers import (
            available_providers,
            search_all,
        )

        if not str(query).strip():
            return {
                "ok": True,
                "providers": [
                    {
                        "name": p.name,
                        "description": p.description,
                        # "library" = geometry you can place.
                        # "catalogue" = identity and a datasheet, with
                        # nothing to import.
                        "kind": getattr(p, "kind", "library"),
                        # False = the endpoint was probed live but the
                        # request and response shapes have never been
                        # exercised with a real credential. Stated, not
                        # assumed.
                        "verified_live": bool(
                            getattr(p, "verified_live", True)),
                        "formats": list(getattr(p, "formats", ())),
                        # Which EDA tool can actually consume a fetch.
                        # There is an EasyEDA->Altium converter here but
                        # NO KiCad->Altium path, so a kicad-only source
                        # is a dead end for an Altium user and should
                        # say so before they spend time on it.
                        "usable_in": list(getattr(p, "usable_in", ())),
                    }
                    for p in available_providers()
                ],
                "note": ("No provider is a default; part_search queries "
                         "all of them equally. Select a subset with "
                         "EDA_AGENT_PART_PROVIDERS."),
            }

        result = search_all(query, limit_per_provider)
        result["ok"] = True
        if not result["count"]:
            # Distinguish "everything answered and found nothing" from
            # "nothing could answer", which look identical otherwise.
            reachable = [n for n, s in result["providers"].items()
                         if s.get("ok")]
            result["note"] = (
                f"no matches from {len(reachable)} reachable provider(s)"
                if reachable else
                "NO provider could answer; this is not evidence the part "
                "does not exist")
        return result

    @mcp.tool()
    async def part_fetch(
        part_id: str,
        provider: str = "",
        download_dir: str = "",
    ) -> dict[str, Any]:
        """Fetch one part's detail, by the ``ref`` a search returned.

        The ``ref`` is ``provider:part_id``; pass it through. A bare id
        with no ``provider`` and no known prefix is refused, never
        guessed; ``provider`` wins over a prefix.

        ``download_dir`` also writes the provider's library files there.
        A downloaded KiCad file in a newer format than the local KiCad
        reads comes back with a ``*_warning`` entry.

        Returns the provider's own detail plus a normalised summary
        (mpn, manufacturer, datasheet, provenance, license) where the
        provider supplies one, and ``files`` when a download was asked
        for.
        """
        from eda_agent.libimport.providers import (
            ProviderError,
            ProviderUnavailable,
            available_providers,
            get_provider,
        )

        # Unpack a "provider:part_id" ref. Split on the FIRST colon and
        # only when the prefix names a REGISTERED provider: part ids
        # legitimately contain colons ("Device:R",
        # "Lib.SchLib::Comp"), so an unconditional split would corrupt
        # them. An explicit provider argument wins.
        if not provider:
            prefix, sep, rest = (part_id or "").partition(":")
            if sep and rest:
                known = {p.name for p in available_providers()}
                if prefix in known:
                    provider, part_id = prefix, rest

        if not provider:
            return {
                "ok": False,
                "reason": (
                    f"cannot tell which source {part_id!r} came from. Pass "
                    "the ref from part_search (it looks like "
                    "'provider:part_id'), or name the provider. This is "
                    "not guessed: picking a source would make one of them "
                    "the silent default for every fetch."
                ),
            }

        try:
            source = get_provider(provider)
        except ProviderError as exc:
            return {"ok": False, "reason": str(exc)}

        try:
            detail = source.fetch(part_id)
        except ProviderUnavailable as exc:
            return {"ok": False, "unavailable": str(exc),
                    "provider": source.name}
        except ProviderError as exc:
            return {"ok": False, "reason": str(exc), "provider": source.name}

        summary = None
        describe = getattr(source, "describe", None)
        if callable(describe):
            try:
                summary = describe(part_id).to_dict()
            except Exception:  # noqa: BLE001 - detail already succeeded
                summary = None

        result: dict[str, Any] = {
            "ok": True,
            "provider": source.name,
            "part_id": part_id,
            "summary": summary,
            "detail": detail,
        }
        result.update(_fit_detail(detail, source.name, part_id))

        if download_dir:
            download = getattr(source, "download", None)
            if not callable(download):
                result["files"] = {}
                result["download_note"] = (
                    f"{source.name} offers no downloadable files")
            else:
                try:
                    result["files"] = download(part_id, download_dir)
                except (ProviderError, OSError) as exc:
                    # The detail already succeeded; report the download
                    # failure without discarding what did work.
                    result["files"] = {}
                    result["download_error"] = str(exc)
        return result
