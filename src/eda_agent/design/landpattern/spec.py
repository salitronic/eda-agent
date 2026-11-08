# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Package description transcribed from a datasheet: the input to the engine.

A :class:`PackageSpec` holds what a package drawing gives: body, lead span,
terminal length and width, pitch, exposed pad, ball grid, each as
min / nom / max with an optional citation (page, figure, table). The
vendor's recommended land pattern rides along as a second, independent
source (:class:`VendorLandPattern`) so the engine's IPC result can be
checked against it.

Axes follow IPC zero orientation, pin 1 top-left, Y up:

* two-row parts (SOIC, SOT, SON, SOD, chips) have their terminals on the
  LEFT and RIGHT, so ``lead_span_x`` runs across the rows (JEDEC E or HE)
  and ``body_y`` runs along them (JEDEC D);
* chips, MELF and molded parts have pad 1 on the left, so
  ``lead_span_x`` is the overall component length L;
* quad parts use ``lead_span_x`` / ``lead_span_y`` and ``body_x`` /
  ``body_y``.

Datasheets disagree on the letters (Nexperia's SOD123 calls the body
length D, Diodes' calls it E), which is why the fields are named by axis
and not by letter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Any, Optional, Union

__all__ = [
    "Citation",
    "Dim",
    "dim",
    "CustomPad",
    "VendorLandPattern",
    "PackageSpec",
    "SpecError",
    "FAMILIES",
]


class SpecError(ValueError):
    """A package description that cannot produce a land pattern."""


@dataclass(frozen=True)
class Citation:
    """Where a number came from."""

    document: str = ""
    page: str = ""
    ref: str = ""        # figure, table or drawing number
    url: str = ""
    revision: str = ""
    path: str = ""       # the datasheet PDF on disk, for the land-pattern check

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}

    @classmethod
    def from_any(cls, value: Any) -> Optional["Citation"]:
        if value is None or isinstance(value, Citation):
            return value
        if isinstance(value, str):
            return cls(document=value)
        if isinstance(value, dict):
            return cls(**{k: str(v) for k, v in value.items()
                          if k in {f.name for f in fields(cls)}})
        raise SpecError(f"cannot read a citation from {value!r}")

    def short(self) -> str:
        parts = [p for p in (self.document, self.revision, self.ref,
                             f"p. {self.page}" if self.page else "") if p]
        return ", ".join(parts)


@dataclass(frozen=True)
class Dim:
    """A dimension as min / nom / max in mm, with an optional citation."""

    min: float
    nom: float
    max: float
    cite: Optional[Citation] = None

    @property
    def tol(self) -> float:
        return self.max - self.min

    @property
    def pair(self) -> tuple[float, float]:
        return (self.min, self.max)

    @classmethod
    def basic(cls, value: float, cite: Any = None) -> "Dim":
        v = float(value)
        return cls(v, v, v, Citation.from_any(cite))

    @classmethod
    def range(cls, lo: float, hi: float, nom: Optional[float] = None,
              cite: Any = None) -> "Dim":
        lo, hi = float(lo), float(hi)
        n = (lo + hi) / 2.0 if nom is None else float(nom)
        return cls(lo, n, hi, Citation.from_any(cite))

    @classmethod
    def tol_(cls, nom: float, tol: float, cite: Any = None) -> "Dim":
        """``nom`` plus or minus ``tol``."""
        n, t = float(nom), abs(float(tol))
        return cls(round(n - t, 9), n, round(n + t, 9), Citation.from_any(cite))

    def check(self, name: str, *, allow_zero: bool = False) -> None:
        vals = (self.min, self.nom, self.max)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
            raise SpecError(f"{name}: every limit must be a finite number, got "
                            f"min={self.min} nom={self.nom} max={self.max}")
        if not (self.min <= self.nom + 1e-9 and self.nom <= self.max + 1e-9):
            raise SpecError(
                f"{name}: limits out of order, need min <= nom <= max but got "
                f"min={self.min} nom={self.nom} max={self.max}")
        if self.min < 0 or (not allow_zero and self.max <= 0):
            raise SpecError(f"{name}: must be positive (min={self.min}, "
                            f"max={self.max})")

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"min": self.min, "nom": self.nom, "max": self.max}
        if self.cite is not None:
            d["cite"] = self.cite.to_dict()
        return d


DimLike = Union[Dim, float, int, tuple, list, dict, None]


def dim(value: DimLike, cite: Any = None) -> Optional[Dim]:
    """Read a dimension from the forms a caller is likely to have.

    * ``0.95`` -> basic (min = nom = max);
    * ``(lo, hi)`` -> nominal is the midpoint;
    * ``(lo, nom, hi)``;
    * ``{"min":, "nom":, "max":}``, or ``{"nom":, "tol":}`` for nom +/- tol,
      or ``{"nom":, "plus":, "minus":}``, each with an optional ``cite``.
    """
    if value is None:
        return None
    if isinstance(value, Dim):
        if cite is not None and value.cite is None:
            return Dim(value.min, value.nom, value.max, Citation.from_any(cite))
        return value
    c = Citation.from_any(cite)
    if isinstance(value, bool):
        raise SpecError(f"a dimension cannot be a boolean ({value!r})")
    if isinstance(value, (int, float)):
        return Dim.basic(value, c)
    if isinstance(value, (tuple, list)):
        if len(value) == 2:
            return Dim.range(value[0], value[1], cite=c)
        if len(value) == 3:
            return Dim(float(value[0]), float(value[1]), float(value[2]), c)
        raise SpecError(f"a dimension list needs 2 or 3 numbers, got {value!r}")
    if isinstance(value, dict):
        c = Citation.from_any(value.get("cite")) or c
        nom = value.get("nom")
        if "tol" in value:
            if nom is None:
                raise SpecError(f"'tol' needs 'nom': {value!r}")
            return Dim.tol_(nom, value["tol"], c)
        if "plus" in value or "minus" in value:
            if nom is None:
                raise SpecError(f"'plus'/'minus' need 'nom': {value!r}")
            n = float(nom)
            return Dim(round(n - abs(float(value.get("minus", 0.0))), 9), n,
                       round(n + abs(float(value.get("plus", 0.0))), 9), c)
        lo, hi = value.get("min"), value.get("max")
        if lo is None and hi is None:
            if nom is None:
                raise SpecError(f"dimension has no numbers: {value!r}")
            return Dim.basic(nom, c)
        if lo is None or hi is None:
            if nom is None:
                raise SpecError(
                    f"dimension needs both min and max, or a nom: {value!r}")
            lo = nom if lo is None else lo
            hi = nom if hi is None else hi
        return Dim.range(lo, hi, nom, c)
    raise SpecError(f"cannot read a dimension from {value!r}")


@dataclass(frozen=True)
class CustomPad:
    """One pad given explicitly: custom footprints, mounting holes, vendor
    land patterns. Centre and size in mm, Y up, rotation in degrees CCW."""

    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str = "roundrect"
    rotation: float = 0.0
    hole: float = 0.0
    hole_length: float = 0.0
    plated: bool = True
    role: str = "signal"
    #: A land that is not a rectangle (a cut corner, a step): its copper
    #: outline, [x, y] points in mm. The pad (width x height) stays the
    #: part of the land that carries the number and paste.
    outline: tuple = ()

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_any(cls, value: Any) -> "CustomPad":
        if isinstance(value, CustomPad):
            return value
        if isinstance(value, dict):
            d = dict(value)
            for alias, key in (("designator", "number"), ("name", "number"),
                               ("w", "width"), ("h", "height"),
                               ("x_size", "width"), ("y_size", "height")):
                if key not in d and alias in d:
                    d[key] = d.pop(alias)
            missing = [k for k in ("number", "x", "y", "width", "height") if k not in d]
            if missing:
                raise SpecError(
                    f"a custom pad needs number, x, y, width and height (mm); "
                    f"{', '.join(missing)} missing in {value!r}")
            d["number"] = str(d["number"])
            if d.get("outline"):
                try:
                    d["outline"] = tuple((float(x), float(y)) for x, y in d["outline"])
                except (TypeError, ValueError):
                    raise SpecError(f"a pad outline is a list of [x, y] points "
                                    f"in mm; got {d['outline']!r}") from None
                if len(d["outline"]) < 3:
                    raise SpecError("a pad outline needs at least 3 points")
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in d.items() if k in known})
        raise SpecError(f"cannot read a pad from {value!r}")


@dataclass(frozen=True)
class VendorLandPattern:
    """The datasheet's recommended land pattern, as an independent source.

    Either explicit ``pads`` or the parametric description of a regular
    pattern: ``pad_length`` (along the span), ``pad_width`` (across it),
    ``span`` (pad centre to pad centre across the rows; ``span_y`` for the
    other axis of a quad), ``pitch``, plus an optional exposed pad.
    """

    cite: Optional[Citation] = None
    pads: tuple[CustomPad, ...] = ()
    pad_length: Optional[float] = None
    pad_width: Optional[float] = None
    span: Optional[float] = None
    span_y: Optional[float] = None
    pitch: Optional[float] = None
    ep_x: Optional[float] = None
    ep_y: Optional[float] = None
    tab_length: Optional[float] = None
    tab_width: Optional[float] = None
    land_diameter: Optional[float] = None
    paste_coverage: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is None or v == () or v == "":
                continue
            if f.name == "cite":
                d["cite"] = v.to_dict()
            elif f.name == "pads":
                d["pads"] = [p.to_dict() for p in v]
            else:
                d[f.name] = v
        return d

    @classmethod
    def from_any(cls, value: Any) -> Optional["VendorLandPattern"]:
        if value is None or isinstance(value, VendorLandPattern):
            return value
        if not isinstance(value, dict):
            raise SpecError(f"cannot read a vendor land pattern from {value!r}")
        d = dict(value)
        for alias in ("land", "land_d", "diameter", "pad_diameter"):
            if alias in d and "land_diameter" not in d:
                d["land_diameter"] = d.pop(alias)
        d["cite"] = Citation.from_any(d.get("cite"))
        d["pads"] = tuple(CustomPad.from_any(p) for p in d.get("pads") or ())
        known = {f.name for f in fields(cls)}
        unknown = sorted(k for k in d if k not in known)
        if unknown:
            raise SpecError(
                f"the vendor land pattern has no field {', '.join(unknown)}; "
                f"it takes {', '.join(sorted(known))}")
        return cls(**d)


#: Families and what each needs. The values are the fields a builder
#: cannot do without; everything else has a stated default.
FAMILIES: dict[str, tuple[str, ...]] = {
    "chip": ("lead_span_x", "lead_length", "lead_width"),
    "melf": ("lead_span_x", "lead_length", "lead_width"),
    "molded": ("lead_span_x", "lead_length", "lead_width"),
    "sod": ("lead_span_x", "lead_length", "lead_width", "body_x"),
    "sot23": ("pins", "pitch", "lead_span_x", "lead_length", "lead_width",
              "body_x", "body_y"),
    "sot89": ("pitch", "lead_span_x", "lead_length", "lead_width",
              "tab_width", "tab_length", "body_x", "body_y"),
    "sot223": ("pitch", "lead_span_x", "lead_length", "lead_width",
               "tab_width", "body_x", "body_y"),
    "dpak": ("pins", "pitch", "lead_span_x", "lead_length", "lead_width",
             "tab_width", "tab_length", "body_x", "body_y"),
    "soic": ("pins", "pitch", "lead_span_x", "lead_length", "lead_width",
             "body_x", "body_y"),
    "son": ("pins", "pitch", "body_x", "body_y", "lead_length", "lead_width"),
    "qfn": ("pins", "pitch", "body_x", "body_y", "lead_length", "lead_width"),
    "qfp": ("pins", "pitch", "lead_span_x", "lead_length", "lead_width",
            "body_x", "body_y"),
    "plcc": ("pins", "pitch", "lead_span_x", "lead_length", "lead_width",
             "body_x", "body_y"),
    "bga": ("rows", "cols", "pitch", "ball_diameter", "body_x", "body_y"),
    "lga": ("rows", "cols", "pitch", "lead_width", "body_x", "body_y"),
    "crystal2": ("body_x", "body_y", "lead_length", "lead_width"),
    "crystal4": ("body_x", "body_y", "pitch", "lead_length", "lead_width"),
    "dip": ("pins", "pitch", "row_pitch", "body_x", "body_y"),
    "header": ("pins", "pitch"),
    "radial": ("pitch", "body_x"),
    "axial": ("pitch", "body_x", "body_y"),
    "to220": ("pins", "pitch", "body_x", "body_y"),
    "to92": ("pins", "pitch", "body_x"),
    "connector": ("pins", "pitch"),
    "custom": ("custom_pads",),
}

_DIM_FIELDS = (
    "pitch", "body_x", "body_y", "height", "standoff", "lead_span_x",
    "lead_span_y", "lead_length", "lead_width", "ep_x", "ep_y", "tab_width",
    "tab_length", "ball_diameter", "lead_diameter", "lead_thickness",
    "row_pitch", "pitch_y",
)


@dataclass
class PackageSpec:
    """A package as its datasheet draws it. See the module docstring for
    the axis convention; every length is mm.

    Lead terms follow IPC-7351: ``lead_span_x`` is L (overall, tip to
    tip), ``lead_length`` is T (the terminal's contact length along the
    span: gull-wing foot, chip end band, MELF cap, no-lead terminal),
    ``lead_width`` is W (the terminal width across the span).
    """

    family: str
    pins: int = 0
    pitch: Optional[Dim] = None
    pitch_y: Optional[Dim] = None
    body_x: Optional[Dim] = None
    body_y: Optional[Dim] = None
    height: Optional[Dim] = None
    standoff: Optional[Dim] = None
    lead_span_x: Optional[Dim] = None
    lead_span_y: Optional[Dim] = None
    lead_length: Optional[Dim] = None
    lead_width: Optional[Dim] = None
    lead_diameter: Optional[Dim] = None
    lead_thickness: Optional[Dim] = None
    ep_x: Optional[Dim] = None
    ep_y: Optional[Dim] = None
    ep_offset: tuple[float, float] = (0.0, 0.0)
    ep_number: Optional[str] = None
    tab_width: Optional[Dim] = None
    tab_length: Optional[Dim] = None
    tab_number: Optional[str] = None
    lead_numbers: Optional[tuple[str, ...]] = None
    pins_per_side: Optional[tuple[int, ...]] = None
    pin1: str = "default"
    rows: int = 0
    cols: int = 0
    ball_diameter: Optional[Dim] = None
    ball_type: str = "collapsing"
    depopulated: tuple[str, ...] = ()
    row_pitch: Optional[Dim] = None
    custom_pads: tuple[CustomPad, ...] = ()
    mounting_pads: tuple[CustomPad, ...] = ()
    polarized: Optional[bool] = None
    lead_form: Optional[str] = None
    density: str = "N"
    name: str = ""
    part_number: str = ""
    description: str = ""
    source: Optional[Citation] = None
    vendor: Optional[VendorLandPattern] = None
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.family = str(self.family or "").strip().lower()
        for name in _DIM_FIELDS:
            setattr(self, name, dim(getattr(self, name)))
        self.custom_pads = tuple(CustomPad.from_any(p) for p in self.custom_pads)
        self.mounting_pads = tuple(CustomPad.from_any(p)
                                   for p in self.mounting_pads)
        self.depopulated = tuple(str(s).strip().upper() for s in self.depopulated)
        if self.lead_numbers is not None:
            self.lead_numbers = tuple(str(s) for s in self.lead_numbers)
        if self.pins_per_side is not None:
            self.pins_per_side = tuple(int(n) for n in self.pins_per_side)
        self.ep_offset = (float(self.ep_offset[0]), float(self.ep_offset[1]))
        self.source = Citation.from_any(self.source)
        self.vendor = VendorLandPattern.from_any(self.vendor)
        self.pins = int(self.pins or 0)
        self.rows = int(self.rows or 0)
        self.cols = int(self.cols or 0)

    # -- validation -------------------------------------------------------

    def validate(self) -> list[str]:
        """Raise :class:`SpecError` on anything the engine cannot use.

        Returns warnings (plausible but suspicious input) as strings.
        """
        if self.family not in FAMILIES:
            raise SpecError(
                f"unknown family {self.family!r}; known: "
                f"{', '.join(sorted(FAMILIES))}")
        missing = [f for f in FAMILIES[self.family] if _is_missing(getattr(self, f))]
        if missing:
            raise SpecError(
                f"family {self.family!r} needs {', '.join(missing)} "
                f"(see PackageSpec docs for the axis convention)")
        for name in _DIM_FIELDS:
            d = getattr(self, name)
            if d is not None:
                d.check(name, allow_zero=name in ("standoff",))
        warnings: list[str] = []
        if self.pitch is not None and self.lead_width is not None:
            if self.lead_width.max >= self.pitch.nom and self.family not in (
                    "chip", "melf", "molded", "crystal2", "radial", "axial"):
                raise SpecError(
                    f"lead_width max {self.lead_width.max} is not below the "
                    f"pitch {self.pitch.nom}: adjacent terminals would touch")
        if self.lead_span_x is not None and self.lead_length is not None:
            if 2 * self.lead_length.nom >= self.lead_span_x.nom:
                raise SpecError(
                    f"two terminals of nominal length {self.lead_length.nom} do "
                    f"not fit in lead_span_x {self.lead_span_x.nom}; lead_length "
                    "is one terminal (T), lead_span_x the whole span (L)")
        if (self.body_x is not None and self.lead_span_x is not None
                and self.family in ("soic", "sot23", "qfp", "sod", "sot223",
                                    "sot89", "plcc")
                and self.body_x.min > self.lead_span_x.max):
            raise SpecError(
                f"body_x min {self.body_x.min} is wider than lead_span_x max "
                f"{self.lead_span_x.max}; for two-row parts body_x is the body "
                "between the rows (JEDEC E1) and lead_span_x the tip-to-tip "
                "span (JEDEC E)")
        if self.ep_x is not None and self.body_x is not None:
            if self.ep_x.max > self.body_x.max or (
                    self.ep_y is not None and self.body_y is not None
                    and self.ep_y.max > self.body_y.max):
                raise SpecError("exposed pad is larger than the body")
        if (self.ep_x is None) != (self.ep_y is None):
            raise SpecError("give both ep_x and ep_y for an exposed pad")
        if self.family in ("soic", "son") and self.pins % 2:
            raise SpecError(f"{self.family} needs an even pin count, got {self.pins}")
        if self.family == "sot23" and self.pins not in (3, 5, 6):
            raise SpecError(f"sot23 takes 3, 5 or 6 pins, got {self.pins}")
        if self.family in ("qfn", "qfp", "plcc"):
            if self.pins_per_side is not None:
                if len(self.pins_per_side) != 4 or sum(self.pins_per_side) != self.pins:
                    raise SpecError(
                        f"pins_per_side {self.pins_per_side} must be four counts "
                        f"(left, bottom, right, top) adding up to {self.pins}")
            elif self.pins % 4:
                raise SpecError(
                    f"{self.family} with {self.pins} pins is not divisible by 4; "
                    "give pins_per_side (left, bottom, right, top)")
        if self.family in ("bga", "lga") and (self.rows < 1 or self.cols < 1):
            raise SpecError("bga/lga need rows >= 1 and cols >= 1")
        if self.family == "dip" and self.pins % 2:
            raise SpecError(f"dip needs an even pin count, got {self.pins}")
        if self.ball_type not in ("collapsing", "non_collapsing"):
            raise SpecError("ball_type must be 'collapsing' or 'non_collapsing'")
        for d in (self.lead_length, self.lead_width, self.lead_span_x):
            if d is not None and d.tol == 0:
                warnings.append(
                    "a terminal dimension has no tolerance; IPC-7351 sizes lands "
                    "from min/max, so a datasheet range gives a better pattern")
                break
        if self.source is None or not self.source.document:
            warnings.append("no source datasheet given; the description cannot "
                            "name where the dimensions came from")
        return warnings

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is None or v == () or v == {} or v == "":
                continue
            if isinstance(v, Dim):
                d[f.name] = v.to_dict()
            elif isinstance(v, (Citation, VendorLandPattern)):
                d[f.name] = v.to_dict()
            elif f.name in ("custom_pads", "mounting_pads"):
                d[f.name] = [p.to_dict() for p in v]
            elif isinstance(v, tuple):
                d[f.name] = list(v)
            else:
                d[f.name] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PackageSpec":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise SpecError(f"unknown PackageSpec fields: {', '.join(sorted(unknown))}")
        kw = dict(d)
        for k in ("ep_offset", "depopulated", "lead_numbers", "pins_per_side",
                  "custom_pads", "mounting_pads"):
            if k in kw and isinstance(kw[k], list):
                kw[k] = tuple(kw[k])
        return cls(**kw)


def _is_missing(v: Any) -> bool:
    return v is None or v == 0 or v == () or v == ""
