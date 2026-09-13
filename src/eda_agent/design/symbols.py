# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Symbol geometry models cached from SchLib.

A SymbolModel captures everything the layout / router / renderer need to
reason about a placed component without asking Altium for pin positions
during every iteration. Pins are stored in the symbol-local frame; the
canvas applies the placed instance's (x, y, rotation) to produce world
coordinates.

Caching: each SchLib is extracted once (via `lib_get_component_details`)
and cached on disk keyed by (lib_path, lib_ref, lib_mtime). Subsequent
runs re-use the cache when the .SchLib mtime is unchanged. To force a
re-extract, delete the .symbol_cache/ directory or call
`SymbolCache.invalidate(lib_path)`.

Coordinate convention (matches Altium):
- All distances in mils.
- Pin (x, y) is the OUTSIDE/electrical endpoint where wires connect.
- Pin orientation: 0=right, 1=up, 2=left, 3=down (TRotationBy90 / 90 deg).
- Length is the visible pin stub length in mils; the body-attach end is
  at (x - length*dx, y - length*dy) where (dx, dy) is the direction the
  pin points outward.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from eda_agent.atomicfile import discard, replace_with_retry

logger = logging.getLogger("eda_agent.design.symbols")


@dataclass(frozen=True)
class SymbolPin:
    """One pin in symbol-local coordinates."""

    designator: str  # pin number, e.g. "1", "8"
    name: str  # pin name, e.g. "VCC", "GND", "OUT"
    x: int  # symbol-local mils, OUTSIDE endpoint
    y: int
    orientation: int  # 0=right, 1=up, 2=left, 3=down
    length: int  # mils
    electrical_type: str  # input/output/passive/power/io/open_collector/...
    hidden: bool = False


@dataclass(frozen=True)
class SymbolBBox:
    """Axis-aligned bbox in symbol-local mils.

    Derived from pin positions plus a small padding so the bbox encloses
    the visible body. NOT the same as the symbol's rectangle primitive
    (which we'd need a separate Pascal call to read); for layout-collision
    purposes the pin-derived bbox is conservative enough.
    """

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    @property
    def width(self) -> int:
        return self.x_max - self.x_min

    @property
    def height(self) -> int:
        return self.y_max - self.y_min


@dataclass(frozen=True)
class SymbolModel:
    """One library component, extracted once and cached.

    Keyed by (lib_path, lib_ref). Equality is by content so two equal
    SymbolModels from different cache lookups deduplicate cleanly.
    """

    lib_path: str
    lib_ref: str
    pins: tuple[SymbolPin, ...]
    body_bbox: SymbolBBox
    designator_prefix: str = "U"
    description: str = ""

    def pin_by_id(self, pin_id: str) -> Optional[SymbolPin]:
        """Look up a pin by its designator or name (designator first).

        DesignPlan PinRefs may reference either; the executor used to call a
        Pascal helper that did the same flexible lookup. We replicate that
        here so plan authors can keep using either form.
        """
        for pin in self.pins:
            if pin.designator == pin_id:
                return pin
        for pin in self.pins:
            if pin.name == pin_id:
                return pin
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lib_path": self.lib_path,
            "lib_ref": self.lib_ref,
            "pins": [asdict(p) for p in self.pins],
            "body_bbox": asdict(self.body_bbox),
            "designator_prefix": self.designator_prefix,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SymbolModel":
        return cls(
            lib_path=d["lib_path"],
            lib_ref=d["lib_ref"],
            pins=tuple(SymbolPin(**p) for p in d["pins"]),
            body_bbox=SymbolBBox(**d["body_bbox"]),
            designator_prefix=d.get("designator_prefix", "U"),
            description=d.get("description", ""),
        )


# Pin orientation -> outward unit vector. Matches Altium's TRotationBy90
# (0=right=+X, 1=up=+Y, 2=left=-X, 3=down=-Y).
_PIN_DIRECTION: dict[int, tuple[int, int]] = {
    0: (1, 0),
    1: (0, 1),
    2: (-1, 0),
    3: (0, -1),
}


def pin_direction(orientation: int) -> tuple[int, int]:
    """Outward (dx, dy) unit vector for a pin at the given orientation."""
    return _PIN_DIRECTION[orientation % 4]


def _bbox_from_pins(pins: tuple[SymbolPin, ...]) -> SymbolBBox:
    """Compute a tight symbol bbox from pin endpoints.

    Altium API asymmetry, learned empirically:

    - For a pin on a SchLib component (what ``lib_get_component_details``
      returns and what this function consumes), ``Pin.Location`` is the
      BODY-ATTACH end -- the inside of the symbol where the pin meets
      the body rectangle. The pin extends from Location OUTWARD by
      ``length`` to the electrical end (where wires snap).
    - For a placed pin on a SchDoc (what world-coord routing consumes),
      ``Pin.Location`` is the ELECTRICAL end -- the wire-connection
      point in world coordinates.

    So for the body bounding box (used by overlap detection and the
    placement engine), we want the body-attach ends -- and those are
    already what ``pin.x`` / ``pin.y`` carry on a SymbolModel. No
    offset needed; previous +/- length * direction formulas were both
    wrong.
    """
    if not pins:
        return SymbolBBox(x_min=-100, y_min=-100, x_max=100, y_max=100)
    body_xs: list[int] = []
    body_ys: list[int] = []
    for pin in pins:
        body_xs.append(pin.x)
        body_ys.append(pin.y)
    # Pad the body by a tiny amount so a body that touches a pin (1-pin
    # special parts) still has non-zero area.
    pad = 50
    return SymbolBBox(
        x_min=min(body_xs) - pad,
        y_min=min(body_ys) - pad,
        x_max=max(body_xs) + pad,
        y_max=max(body_ys) + pad,
    )


def _designator_prefix_from_refdes_or_default(lib_ref: str) -> str:
    """Best-effort guess at a designator prefix from the symbol name.

    Only used as a last-resort fallback; the authoritative prefix is
    the symbol's own ``designator`` field (e.g. ``"U?"`` strips to
    ``"U"``) which the caller should prefer. We only sniff the lib_ref
    when its prefix follows a well-known passive convention like
    ``RES ``, ``CAP ``, ``LED ``, ``DIODE ``, ``IND ``. IC names do
    NOT follow a predictable lib_ref prefix so we default to ``U``;
    the caller's own designator field is the only reliable source for
    those.
    """
    name = lib_ref.upper().strip()
    if name.startswith("RES "):
        return "R"
    if name.startswith("CAP "):
        return "C"
    if name.startswith("LED "):
        return "D"
    if name.startswith("DIODE "):
        return "D"
    if name.startswith("IND ") or name.startswith("INDUCTOR"):
        return "L"
    return "U"


def parse_symbol_from_details(
    details: dict[str, Any], lib_path: str
) -> SymbolModel:
    """Parse a SymbolModel from a `lib_get_component_details` response.

    Pascal returns: {name, library_path, designator{}, comment{},
    description, alias_name, part_count, pin_count, pins[], parameters{},
    parameter_styles[]}.

    We only need name, description, pins. designator_prefix is heuristic
    when not available; the placed-instance refdes overrides it.
    """
    pins_raw = details.get("pins", []) or []
    pins: list[SymbolPin] = []
    for p in pins_raw:
        # `length` was added in script v2026.05.15.8; older Pascal builds
        # don't include it. Fall back to 200 (Altium default) so the
        # parser is forward-compatible during the transition.
        pin_length = int(p.get("length", 200))
        pins.append(SymbolPin(
            designator=str(p.get("designator", "")),
            name=str(p.get("name", "")),
            x=int(p.get("x", 0)),
            y=int(p.get("y", 0)),
            orientation=int(p.get("orientation", 0)) % 4,
            length=pin_length,
            electrical_type=str(p.get("electrical_type", "passive")),
            hidden=bool(p.get("hidden", False)),
        ))
    pins_tuple = tuple(pins)
    bbox = _bbox_from_pins(pins_tuple)
    lib_ref = str(details.get("name", ""))
    return SymbolModel(
        lib_path=lib_path,
        lib_ref=lib_ref,
        pins=pins_tuple,
        body_bbox=bbox,
        designator_prefix=_designator_prefix_from_refdes_or_default(lib_ref),
        description=str(details.get("description", "")),
    )


class SymbolCache:
    """Disk-backed cache of SymbolModels, invalidated by SchLib mtime.

    Layout: one JSON file per SchLib, named after the lib stem. Each file
    holds {"lib_mtime": float, "components": {lib_ref: SymbolModel}}. We
    write per-SchLib (not per-component) so a single SchLib edit
    invalidates the whole lib in one shot.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # In-memory mirror so repeated lookups within one run don't re-read disk.
        self._memory: dict[str, dict[str, Any]] = {}

    def _lib_cache_path(self, lib_path: str) -> Path:
        stem = Path(lib_path).stem
        return self.cache_dir / f"{stem}.json"

    def _load_lib(self, lib_path: str) -> Optional[dict[str, Any]]:
        if lib_path in self._memory:
            return self._memory[lib_path]
        cache_file = self._lib_cache_path(lib_path)
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("symbol cache read failed for %s: %s", cache_file, exc)
            return None
        self._memory[lib_path] = data
        return data

    def is_fresh(self, lib_path: str) -> bool:
        """True iff cached lib_mtime matches the SchLib's current mtime."""
        data = self._load_lib(lib_path)
        if data is None:
            return False
        try:
            disk_mtime = Path(lib_path).stat().st_mtime
        except OSError:
            return False
        cached_mtime = data.get("lib_mtime")
        return cached_mtime is not None and abs(cached_mtime - disk_mtime) < 0.001

    def get(self, lib_path: str, lib_ref: str) -> Optional[SymbolModel]:
        if not self.is_fresh(lib_path):
            return None
        data = self._load_lib(lib_path)
        if data is None:
            return None
        comps = data.get("components", {})
        raw = comps.get(lib_ref)
        if raw is None:
            return None
        try:
            return SymbolModel.from_dict(raw)
        except (KeyError, TypeError) as exc:
            logger.warning(
                "symbol cache corrupt entry %s/%s: %s", lib_path, lib_ref, exc
            )
            return None

    def put(self, model: SymbolModel,
            source_mtime: Optional[float] = None) -> None:
        """Insert a model and persist the lib's cache file atomically.

        ``source_mtime`` is the library's mtime as observed BEFORE the
        model was read out of it. Stamping the mtime found at write time
        instead certifies the entry against a file the model may not
        have come from: the library is reopened in the editor to answer
        the read, and anything that touches it in that window (a
        deferred save landing, an edit, another tool) moves the mtime.
        The entry then reads as current for a symbol whose geometry is
        one version behind, which is worse than a cache miss because
        nothing reports it.

        Falls back to the mtime at write time when the caller does not
        supply one, which is the previous behaviour and is right for a
        model built from something other than a live read.
        """
        try:
            disk_mtime = Path(model.lib_path).stat().st_mtime
        except OSError as exc:
            logger.warning(
                "cannot stat %s, skipping cache write: %s", model.lib_path, exc
            )
            return
        if source_mtime is not None:
            if abs(source_mtime - disk_mtime) >= 0.001:
                # The library moved under the read. Keep the model (the
                # caller asked for it and will use it) but do not claim
                # it is current, so the next run re-reads.
                logger.info(
                    "%s changed while %s was read; not caching it",
                    model.lib_path, model.lib_ref,
                )
                return
            disk_mtime = source_mtime
        data = self._load_lib(model.lib_path) or {
            "lib_mtime": disk_mtime,
            "components": {},
        }
        data["lib_mtime"] = disk_mtime
        data["components"][model.lib_ref] = model.to_dict()
        self._memory[model.lib_path] = data
        cache_file = self._lib_cache_path(model.lib_path)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # On Windows a scanner (Defender, a sync client, an indexer) can hold
        # the freshly-written target open for a few ms, and os.replace then
        # raises PermissionError. Retried in replace_with_retry; if it still
        # will not go through, drop the cache entry rather than propagating.
        # This cache is an optimisation, and losing a whole extraction run
        # because a temp-file rename lost a race is never the right trade.
        # put() rewrites this file once per symbol, so a 38-symbol run makes
        # 38 attempts at the same target and any per-attempt failure rate
        # compounds into near-certainty.
        try:
            replace_with_retry(tmp, cache_file)
        except PermissionError:
            logger.warning(
                "symbol cache write for %s kept losing the rename race; "
                "continuing without caching it", cache_file,
            )
            discard(tmp)

    def invalidate(self, lib_path: str) -> None:
        self._memory.pop(lib_path, None)
        cache_file = self._lib_cache_path(lib_path)
        if cache_file.exists():
            cache_file.unlink()


class SymbolExtractor:
    """Pull SymbolModels from Altium via the bridge, route through cache.

    Single entrypoint: `extract_many([(lib_path, lib_ref), ...]) -> dict`.
    The dict is keyed by (lib_path, lib_ref) so the caller can look up a
    Part's symbol by the same key the plan carries.

    Failure mode: if a lib_get_component_details call errors (lib doesn't
    load, component name doesn't resolve), the failed (lib_path, lib_ref)
    is absent from the returned dict. Callers should check membership and
    treat absence as a hard plan-level error before continuing.
    """

    def __init__(self, bridge: Any, cache: SymbolCache) -> None:
        self.bridge = bridge
        self.cache = cache

    def extract_one(
        self, lib_path: str, lib_ref: str
    ) -> Optional[SymbolModel]:
        cached = self.cache.get(lib_path, lib_ref)
        if cached is not None:
            return cached
        # Cache miss: ask Altium. lib_get_component_details requires the
        # target SchLib to be loaded in the editor. The handler reopens
        # the lib via WorkspaceManager:OpenObject if it isn't already
        # focused, so we don't need a separate load step here.
        # Observed BEFORE the read, so put() can tell whether the library
        # stayed still while Altium answered.
        try:
            source_mtime = Path(lib_path).stat().st_mtime
        except OSError:
            source_mtime = None
        try:
            response = self.bridge.send_command(
                "library.get_component_details",
                {"component_name": lib_ref, "library_path": lib_path},
            )
        except Exception as exc:
            logger.warning(
                "lib_get_component_details failed for %s/%s: %s",
                lib_path, lib_ref, exc,
            )
            return None
        if not isinstance(response, dict):
            logger.warning(
                "lib_get_component_details unexpected response shape for %s/%s: %r",
                lib_path, lib_ref, response,
            )
            return None
        model = parse_symbol_from_details(response, lib_path)
        self.cache.put(model, source_mtime)
        return model

    def extract_many(
        self, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], SymbolModel]:
        out: dict[tuple[str, str], SymbolModel] = {}
        # Group by lib_path so we hit each SchLib once (lib_get_component_details
        # reopens the lib per call; grouping cuts the cost when several
        # parts share a lib).
        by_lib: dict[str, list[str]] = {}
        for lib_path, lib_ref in refs:
            by_lib.setdefault(lib_path, []).append(lib_ref)
        for lib_path, lib_refs in by_lib.items():
            for lib_ref in lib_refs:
                model = self.extract_one(lib_path, lib_ref)
                if model is not None:
                    out[(lib_path, lib_ref)] = model
        return out
