# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The KiCad libraries already installed on this machine.

Worth having alongside the network registries for reasons none of them
can match: it needs no network, no login and no third party, it cannot
be withdrawn the way EasyEDA's search endpoint was, and its parts are
the ones KiCad itself ships and maintains.

A fetch resolves the symbol's own ``Footprint`` property against the
installed ``.pretty`` libraries, so most hits come back as a whole part
rather than a symbol on its own. That matters on Altium, where a symbol
with no land pattern is half a part and the caller would otherwise have
no way to know one was available. Measured across all 222 libraries
shipped with KiCad 10.0.1: 18003 of 22728 symbols (79 percent) carry
such a reference.

Where the standard libraries genuinely say nothing, so does this. Most
entries carry no MPN and no manufacturer, a generic symbol records no
footprint at all, and each of those comes back blank rather than
guessed. A reference that names a library which is not installed is
reported as exactly that, since "you do not have it" and "there is
none" call for different responses.

Discovery order (first hit wins, all overridable), symbols and
footprints resolved independently because either can be moved alone:
  1. ``EDA_AGENT_KICAD_SYMBOL_DIR`` / ``EDA_AGENT_KICAD_FOOTPRINT_DIR``
  2. ``KICAD_SYMBOL_DIR`` / ``KICAD_FOOTPRINT_DIR`` (KiCad's own)
  3. the standard install locations per platform

Search parsing is a deliberate shallow scan for ``(symbol "NAME"`` at
the start of a line. Fully parsing 222 s-expression files to answer one
query would be slow and buys nothing: the symbol NAME is all a search
needs. A fetch does parse properly, but only the one library named.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from eda_agent.libimport.providers.base import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
)

__all__ = ["KicadLocalProvider"]

#: Top-level symbol definitions only. Nested unit symbols are indented
#: and named ``NAME_0_1`` / ``NAME_1_1``, which are not parts.
_SYMBOL_RE = re.compile(r'^\s{0,2}\(symbol\s+"([^"]+)"', re.M)

def _windows_kicad_roots() -> tuple[str, ...]:
    """Where KiCad's Windows installer puts a tree.

    Its NSIS installer takes ``/currentuser``, which lands under
    LOCALAPPDATA and is invisible to anything that only looks in
    Program Files. Kept as a module-level tuple because
    ``--bare-machine`` blanks it.
    """
    roots = [
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"), "KiCad"),
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "KiCad"),
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs", "KiCad"))
    return tuple(roots)


_WINDOWS_ROOTS = _windows_kicad_roots()


def _version_key(path: Path) -> tuple[int, ...]:
    """Sort 10.0 above 9.0.

    The directory names are dotted version numbers, so sorting them as
    strings puts "9.0" above "10.0" and a machine with both installed
    silently resolves to the older one. Non-numeric entries sort last.
    """
    parts: list[int] = []
    for chunk in path.name.split("."):
        if not chunk.isdigit():
            return (-1,)
        parts.append(int(chunk))
    return tuple(parts) if parts else (-1,)


_POSIX_ROOTS = (
    "/usr/share/kicad",
    "/usr/local/share/kicad",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport",
)


def _library_dir(kind: str, env_vars: tuple[str, ...]) -> Optional[Path]:
    for var in env_vars:
        raw = os.environ.get(var)
        if raw and Path(raw).is_dir():
            return Path(raw)

    # Versioned subdirectories (10.0, 9.0, ...). Rank them across ALL
    # roots at once, not within each one: ranking per-root would let a
    # machine-wide KiCad 9 win over a per-user KiCad 10 purely because
    # Program Files is searched first.
    found: list[tuple[tuple[int, ...], Path]] = []
    for root in _WINDOWS_ROOTS:
        base = Path(root)
        if base.is_dir():
            for version in base.iterdir():
                found.append(
                    (_version_key(version),
                     version / "share" / "kicad" / kind))
    found.sort(key=lambda item: item[0], reverse=True)

    candidates: list[Path] = [path for _, path in found]
    for root in _POSIX_ROOTS:
        candidates.append(Path(root) / kind)

    for path in candidates:
        if path.is_dir():
            return path
    return None


def _symbol_dir() -> Optional[Path]:
    return _library_dir(
        "symbols", ("EDA_AGENT_KICAD_SYMBOL_DIR", "KICAD_SYMBOL_DIR"))


def _footprint_dir() -> Optional[Path]:
    """Where the ``.pretty`` footprint libraries live.

    Searched independently of the symbols rather than derived from them,
    because either can be overridden on its own.
    """
    return _library_dir(
        "footprints",
        ("EDA_AGENT_KICAD_FOOTPRINT_DIR", "KICAD_FOOTPRINT_DIR"))


def _model_dir() -> Optional[Path]:
    """Where the shipped STEP/WRL 3D models live."""
    return _library_dir(
        "3dmodels",
        ("EDA_AGENT_KICAD_3DMODEL_DIR", "KICAD_3DMODEL_DIR"))


def resolve_model_3d(ref: str) -> Optional[Path]:
    """Resolve a footprint's 3D model reference to a file here.

    KiCad writes ``${KICAD10_3DMODEL_DIR}/Lib.3dshapes/Name.step``. The
    variable name carries the major version, so it is stripped rather
    than matched: the point is the path under the model root, and
    hard-coding one version would break on the next release.

    Worth resolving because it completes the part. Altium's linker takes
    STEP and KiCad ships STEP, so a local hit can carry a real 3D body
    rather than a footprint with nothing above the board. Prefers STEP
    over any sibling: Altium cannot load KiCad's WRL.
    """
    text = str(ref or "").strip().replace("\\", "/")
    if not text:
        return None
    root = _model_dir()
    if root is None:
        return None
    # Drop a leading ${...} variable, or an absolute prefix ending at
    # the model root, leaving "Lib.3dshapes/Name.step".
    tail = re.sub(r"^\$\{[^}]*\}/?", "", text)
    if tail == text:
        marker = "3dmodels/"
        idx = text.lower().find(marker)
        tail = text[idx + len(marker):] if idx >= 0 else text
    tail = tail.lstrip("/")
    if not tail:
        return None
    candidate = root / tail
    try:
        if not candidate.resolve().is_relative_to(root.resolve()):
            return None
    except OSError:
        return None
    if candidate.is_file() and candidate.suffix.lower() in (".step", ".stp"):
        return candidate
    # A .wrl reference usually has a .step sibling, which is the one
    # Altium can actually load.
    for suffix in (".step", ".stp"):
        sibling = candidate.with_suffix(suffix)
        if sibling.is_file():
            return sibling
    return None


def _resolve_footprint(ref: str) -> Optional[Path]:
    """Turn a symbol's ``Library:Name`` reference into a real file.

    This is what makes a hit a whole part instead of half of one: 18003
    of the 22728 symbols shipped with KiCad 10.0.1 carry such a
    reference, and without resolving it an Altium import produces a
    symbol with no land pattern.

    Returns None rather than guessing when the reference is blank,
    malformed, or names something not installed. A near-miss footprint
    would be worse than none, since it would look converted.
    """
    if ":" not in (ref or ""):
        return None
    lib, _, name = ref.partition(":")
    root = _footprint_dir()
    if root is None or not lib or not name:
        return None
    path = root / f"{lib}.pretty" / f"{name}.kicad_mod"
    # The reference comes out of a library file, so treat it as input:
    # a crafted "../.." must not escape the footprint root.
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            return None
    except OSError:
        return None
    return path if path.is_file() else None


class KicadLocalProvider:
    """Search the symbol libraries KiCad installed locally."""

    name = "kicad_local"
    description = (
        "The libraries installed with KiCad on this machine. Offline, no "
        "login, no third party. A fetch resolves the symbol's footprint "
        "reference against the installed .pretty libraries, so most hits "
        "are a whole part; a generic symbol that records no footprint "
        "says so. Most entries carry no MPN or datasheet, and those are "
        "reported blank rather than guessed. KiCad format, usable in "
        "Altium via lib_kicad_import.")

    formats = ("kicad_sym",)
    usable_in = ("kicad", "altium")

    def __init__(self) -> None:
        self._cache: Optional[list[tuple[str, str]]] = None
        self._resolved: dict[str, dict[str, Any]] = {}

    def _index(self) -> list[tuple[str, str]]:
        """(library stem, symbol name) for every installed symbol."""
        if self._cache is not None:
            return self._cache
        root = _symbol_dir()
        if root is None:
            raise ProviderUnavailable(
                "no KiCad symbol library directory found; set "
                "EDA_AGENT_KICAD_SYMBOL_DIR if KiCad is installed "
                "somewhere non-standard")
        out: list[tuple[str, str]] = []
        for path in sorted(root.glob("*.kicad_sym")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for name in _SYMBOL_RE.findall(text):
                # Skip the per-unit sub-symbols (NAME_0_1, NAME_1_1).
                if re.search(r"_\d+_\d+$", name):
                    continue
                out.append((path.stem, name))
        self._cache = out
        return out

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        needle = str(query or "").strip().lower()
        if not needle:
            return []
        hits: list[PartHit] = []
        for lib, symbol in self._index():
            if needle not in symbol.lower() and needle not in lib.lower():
                continue
            hits.append(PartHit(
                provider=self.name,
                part_id=f"{lib}:{symbol}",
                mpn=symbol,
                description=f"KiCad library {lib}",
                # Blank on purpose: the standard libraries do not carry
                # a manufacturer, MPN or datasheet for most symbols, and
                # inventing them would be worse than admitting it.
                provenance=f"KiCad standard library {lib}",
                license="CC-BY-SA-4.0 with exception",
                extra={"library": lib, "symbol": symbol},
            ))
            if len(hits) >= max(1, int(limit)):
                break
        return hits

    def _resolve(self, part_id: str) -> dict[str, Any]:
        """Locate a symbol and everything derivable from it.

        Shared by ``fetch`` and ``describe``, and memoised, because
        ``part_fetch`` calls both for the same part: these libraries run
        to several megabytes and the parse is the whole cost of
        answering, so doing it twice would double the call for nothing.

        The memo lives on the instance, and a fresh provider is built per
        ``available_providers()`` call, so it cannot outlive the request
        and go stale against an edited library.
        """
        pid = str(part_id or "").strip()
        cached = self._resolved.get(pid)
        if cached is not None:
            return cached
        if ":" not in pid:
            raise ProviderError(
                f"expected 'library:symbol', got {part_id!r}")
        lib, symbol = pid.split(":", 1)
        root = _symbol_dir()
        if root is None:
            raise ProviderUnavailable("no KiCad symbol library directory")
        path = root / f"{lib}.kicad_sym"
        # The library name is caller input; keep it inside the root.
        if not path.resolve().is_relative_to(root.resolve()):
            raise ProviderError(f"refusing path outside library root: {pid}")
        if not path.is_file():
            raise ProviderError(f"no such library: {lib}")
        # Resolve the symbol's own footprint reference into a real file.
        # A symbol on its own is half a part: converted to Altium it
        # would arrive with no land pattern, and the caller would have
        # no way to know one was available.
        ref, fp_path, datasheet, description = "", None, "", ""
        units = 1
        try:
            from eda_agent.libimport.kicad.reader import read_kicad_symbol

            comp = read_kicad_symbol(
                path.read_text(encoding="utf-8", errors="replace"),
                name=symbol)
            ref = comp.footprint_ref
            datasheet, description = comp.datasheet, comp.description
            units = comp.unit_count
            fp_path = _resolve_footprint(ref)
        except (OSError, ValueError) as exc:
            # Locating the symbol still succeeded; say what did not.
            description = f"(could not read footprint reference: {exc})"
        # The 3D body completes the part. Read from the footprint rather
        # than the symbol, which is where KiCad records it.
        model_3d = ""
        if fp_path is not None:
            try:
                from eda_agent.libimport.kicad.reader import (
                    read_kicad_footprint,
                )

                fp_comp = read_kicad_footprint(
                    fp_path.read_text(encoding="utf-8", errors="replace"))
                resolved = resolve_model_3d(
                    fp_comp.footprint.model_3d_ref)
                model_3d = str(resolved) if resolved else ""
            except (OSError, ValueError):
                model_3d = ""

        found = {"lib": lib, "symbol": symbol, "path": path, "ref": ref,
                 "fp_path": fp_path, "datasheet": datasheet,
                 "description": description, "units": units,
                 "model_3d": model_3d}
        self._resolved[pid] = found
        return found

    def describe(self, part_id: str) -> PartHit:
        """The normalised view, which is what makes sources comparable.

        Populated from the symbol itself rather than from the search
        index, so the datasheet and description are the real ones where
        the library records them. Manufacturer and MPN stay blank: a
        symbol name is not a part number, and treating it as one would
        put a fabricated MPN into a BOM.
        """
        found = self._resolve(part_id)
        return PartHit(
            provider=self.name,
            part_id=str(part_id),
            mpn="",
            description=found["description"],
            datasheet=found["datasheet"],
            package=found["ref"].partition(":")[2],
            provenance=f"KiCad standard library {found['lib']}",
            license="CC-BY-SA-4.0 with exception",
            extra={"library": found["lib"], "symbol": found["symbol"],
                   "footprint_ref": found["ref"]},
        )

    def fetch(self, part_id: str) -> dict[str, Any]:
        found = self._resolve(part_id)
        lib, symbol, path = found["lib"], found["symbol"], found["path"]
        ref, fp_path = found["ref"], found["fp_path"]
        datasheet, description = found["datasheet"], found["description"]

        out: dict[str, Any] = {
            "library": lib,
            "symbol": symbol,
            "path": str(path),
            "symbol_path": str(path),
            "footprint_ref": ref,
            "footprint_path": str(fp_path) if fp_path else "",
            "datasheet": datasheet,
            "description": description,
            "unit_count": found["units"],
            "model_3d_path": found["model_3d"],
            "import_with": "lib_kicad_import",
        }
        if found["units"] > 1:
            # Say it before the import rather than after: converting one
            # unit and stopping leaves most of the part behind, and
            # nothing else in the result would reveal that.
            out["units_note"] = (
                f"This part has {found['units']} units; lib_kicad_import "
                f"builds all of them as one Altium multi-part component "
                f"in a single call. Pass unit=1..{found['units']} only "
                f"if you deliberately want just one sub-part.")
        if fp_path is not None:
            out["note"] = (
                "Whole part: pass symbol_path, footprint_path and "
                "symbol_name to lib_kicad_import for Altium, or use both "
                "files as-is on KiCad. symbol_name is required because "
                "this library holds many symbols.")
        elif ref:
            # A reference that does not resolve is a different problem
            # from no reference at all, and only one of them is worth
            # chasing (an uninstalled library, usually).
            out["note"] = (
                f"Symbol only. It names footprint {ref!r}, which is not "
                f"installed here, so no land pattern was found. Supply "
                f"footprint_path yourself, or convert the symbol alone "
                f"with lib_kicad_import(symbol_path=..., symbol_name=...).")
        else:
            out["note"] = (
                "Symbol only: this entry records no footprint, which is "
                "normal for the generic symbols in the standard "
                "libraries. Pick a land pattern from the manufacturer "
                "datasheet rather than assuming one.")
        return out
