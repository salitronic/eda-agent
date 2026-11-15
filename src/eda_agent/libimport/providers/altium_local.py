# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Altium libraries already on this machine.

The one source that answers a question none of the others can: do I
ALREADY have this part? Every other provider proposes something to
import, and importing a part you own produces a duplicate symbol with a
slightly different name, which is how a library rots.

No network, no login, no rate limit, and no third party. Altium reads
each ``.SchLib`` it finds (``library.get_components``, Altium's own
library reader, which needs no library open), so it needs Altium and the
polling loop running; the files are never opened here.

There is nothing to download. A hit here is already in Altium's own
format, so ``fetch`` returns the library path and the component name
rather than files: the caller places it with the existing library
tools instead of converting anything.

Roots come from ``EDA_AGENT_ALTIUM_LIBRARIES`` (a path-separator list).
Without it the usual install locations are searched. Nothing is
searched recursively beyond those roots, because a scan of an entire
drive is not a thing a search tool should do behind the user's back.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Optional

from eda_agent.libimport.providers.base import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
)

__all__ = ["AltiumLocalProvider"]

#: Where Altium keeps libraries by default. Checked in order; every one
#: that exists contributes, because a machine often has both a shared
#: and a per-user library.
_DEFAULT_ROOTS = (
    r"%PUBLIC%\Documents\Altium\Library",
    r"%USERPROFILE%\Documents\Altium\Library",
    r"%USERPROFILE%\Documents\Altium\Projects",
)


def _roots() -> list[Path]:
    raw = os.environ.get("EDA_AGENT_ALTIUM_LIBRARIES", "").strip()
    if raw:
        return [Path(os.path.expandvars(p)).expanduser()
                for p in raw.split(os.pathsep) if p.strip()]
    return [Path(os.path.expandvars(r)).expanduser() for r in _DEFAULT_ROOTS]


def _schlibs() -> list[Path]:
    """Every readable .SchLib under the configured roots."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in _roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.SchLib")):
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                found.append(path)
    return found


class AltiumLocalProvider:
    """Search the Altium schematic libraries installed on this machine."""

    name = "altium_local"
    description = (
        "The .SchLib libraries already on this machine. Answers whether "
        "you ALREADY own a part, which no other source can. No network "
        "and no login; Altium reads the libraries, so it needs Altium "
        "running. Nothing to download: a hit is already an Altium "
        "symbol. Set EDA_AGENT_ALTIUM_LIBRARIES to point it elsewhere.")

    #: Nothing is fetched, so no convertible format is offered. This is
    #: not an omission: the part is already in the target format.
    formats: tuple = ()
    usable_in = ("altium",)

    #: Backends this source is ALREADY native to, so a claim of
    #: usability needs no converter. Declared positively rather than
    #: inferred from an empty ``formats``, otherwise omitting formats
    #: would be a way to dodge the converter check rather than a
    #: statement about the parts.
    native_to = ("altium",)

    def _components(self) -> Iterable[tuple[Path, dict[str, Any]]]:
        libs = _schlibs()
        if not libs:
            raise ProviderUnavailable(
                "no Altium .SchLib libraries found. Looked in "
                + ", ".join(str(r) for r in _roots())
                + ". Set EDA_AGENT_ALTIUM_LIBRARIES to a path-separated "
                  "list of library folders.")
        from eda_agent.bridge import get_bridge
        bridge = get_bridge()
        if not bridge.is_altium_running():
            raise ProviderUnavailable(
                "Altium is not running; this source has Altium read the "
                "libraries it finds")
        for lib in libs:
            try:
                reply = bridge.send_command(
                    "library.get_components", {"library_path": str(lib)},
                    timeout=60.0) or {}
            except Exception:                         # noqa: BLE001
                # One unreadable library must not hide the rest. A
                # corrupt or in-use file is common on a shared drive.
                continue
            for comp in reply.get("components") or []:
                if isinstance(comp, dict):
                    yield lib, comp

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        needle = (query or "").strip().lower()
        hits: list[PartHit] = []
        for lib, comp in self._components():
            name = str(comp.get("name") or comp.get("lib_reference") or "")
            if not name:
                continue
            description = str(comp.get("description") or "")
            if needle and needle not in name.lower() \
                    and needle not in description.lower():
                continue
            hits.append(PartHit(
                provider=self.name,
                # Qualified by library so two libraries may hold the
                # same symbol name without colliding.
                part_id=f"{lib.name}::{name}",
                mpn="",
                manufacturer="",
                description=description,
                provenance=f"already installed: {lib}",
                extra={"library_path": str(lib), "component": name},
            ))
            if len(hits) >= limit:
                break
        return hits

    def fetch(self, part_id: str) -> dict[str, Any]:
        lib_name, _, comp_name = (part_id or "").partition("::")
        if not lib_name or not comp_name:
            raise ProviderError(
                f"part_id must be '<library.SchLib>::<component>', got "
                f"{part_id!r}")
        for lib, comp in self._components():
            name = str(comp.get("name") or comp.get("lib_reference") or "")
            if lib.name.lower() == lib_name.lower() and name == comp_name:
                return {
                    "provider": self.name,
                    "part_id": part_id,
                    "component": name,
                    "library_path": str(lib),
                    "description": str(comp.get("description") or ""),
                    "lib_reference": str(comp.get("lib_reference") or name),
                    "files": {},
                    "note": (
                        "already an Altium symbol, nothing downloaded. "
                        "Place it with lib_ tools against this library "
                        "rather than importing a second copy."),
                }
        raise ProviderError(f"no component {comp_name!r} in {lib_name!r}")
