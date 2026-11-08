# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Datasheet and package tools that need no editor, on every backend.

The client reads the datasheet; these make a misread number hard to miss
before a footprint is drawn from it:

* ``datasheet_fetch`` downloads the PDF once into the workspace and hands
  back its path and page count;
* ``datasheet_find_pages`` lists the pages that mention given words, by
  default where a land pattern is published;
* ``footprint_check_package`` runs the physical sanity checks on a package
  description and cross-checks its IPC land pattern against the vendor's
  recommended one from the same datasheet;
* ``footprint_render_package`` draws the land pattern the engine would
  make, with the package's terminals over it, for a side-by-side look at
  the datasheet drawing.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

from ..config import get_config

__all__ = ["register_footprint_tools"]


def _metrics_lines(model) -> list[str]:
    from ..design.landpattern.compare import pattern_metrics

    met = pattern_metrics(model)

    def mm(v) -> str:
        return f"{v:g}" if isinstance(v, (int, float)) else str(v)

    lines = []
    bits = []
    if met.get("pitch") is not None:
        bits.append(f"pitch {mm(met['pitch'])}")
    if met.get("span") is not None:
        bits.append(f"span {mm(met['span'])} c-c")
    if met.get("span_y") is not None:
        bits.append(f"span_y {mm(met['span_y'])} c-c")
    if met.get("pad_length") is not None:
        bits.append(f"land {mm(met['pad_length'])} x {mm(met['pad_width'])}")
    if met.get("land_diameter") is not None:
        bits.append(f"land {mm(met['land_diameter'])} dia")
    if met.get("ep_x") is not None:
        bits.append(f"exposed pad {mm(met['ep_x'])} x {mm(met['ep_y'])}")
    if met.get("tab_length") is not None:
        bits.append(f"tab {mm(met['tab_length'])} x {mm(met['tab_width'])}")
    if bits:
        lines.append("IPC land pattern, mm: " + ", ".join(bits))
    return lines


def _split_dims(package: Any) -> tuple[Any, Any, Any]:
    """The package without ``drawing_dims`` and ``drawing_numbers``, and
    those: they check the built pads and are not PackageSpec fields."""
    import json

    from ..design.landpattern.drawing_dims import pop_drawing

    if isinstance(package, str):
        try:
            package = json.loads(package)
        except ValueError:
            return package, None, None
    return pop_drawing(package)
    if isinstance(package, dict) and "drawing_dims" in package:
        package = dict(package)
        return package, package.pop("drawing_dims")
    return package, None


def _cited(page: str) -> set[int]:
    from ..design.landpattern.land_source import cited_pages

    return cited_pages(page)


def register_footprint_tools(mcp):
    """Register the datasheet and package tools."""

    @mcp.tool()
    async def datasheet_fetch(url: str, part_number: str = "") -> dict[str, Any]:
        """Download a datasheet PDF into the workspace and return its path.

        Read the ``path`` yourself; nothing here parses the PDF. Files are
        cached under ``<workspace>/datasheets`` with a sidecar (URL, part
        number, SHA-256), so a repeat call is a ``cache: "hit"``; a copy
        whose hash no longer matches is fetched again. Refused, storing
        nothing: a non-http(s) URL, over 50 MB, no answer in 60 s, or a
        reply that is not a PDF (a login page); download those in a
        browser instead.

        Args:
            url: the datasheet's http or https URL, ideally the
                manufacturer's own.
            part_number: recorded in the sidecar, for the citation.

        Returns:
            ``{ok, path, sidecar, cache, url, final_url, sha256, size,
            pages, pages_approximate, pages_method, fetched_at}``. ``pages``
            is counted from the page objects in the file without a PDF
            parser and can be None or off for a compressed file.
        """
        from ..design.datasheet_fetch import fetch_datasheet

        return await asyncio.to_thread(fetch_datasheet, url, part_number)

    @mcp.tool()
    async def datasheet_find_pages(path: str,
                                   terms: list[str] | None = None) -> dict[str, Any]:
        """List the pages of a datasheet PDF that mention given words.

        With no ``terms`` it looks for where a manufacturer publishes its
        land pattern (land pattern, footprint, PCB layout, solder pad,
        stencil, ...). That is often far from the package outline, so call
        it before deciding a datasheet gives none, then read those pages.
        A scanned page has no text and is not found.

        Args:
            path: the PDF on disk (from datasheet_fetch, or a local file).
            terms: words or phrases to find, case ignored.

        Returns:
            ``{ok, path, pages_searched, terms, pages: [{page, lines}]}``,
            pages 1-based with up to three matching lines each.
        """
        from ..design.datasheet_search import LAND_PATTERN_TERMS, find_pages

        return await asyncio.to_thread(find_pages, path, terms or LAND_PATTERN_TERMS)

    @mcp.tool()
    async def datasheet_view_page(path: str, page: int,
                                  region: list[float] | None = None,
                                  dpi: int = 400) -> dict[str, Any]:
        """Render part of a datasheet page as a zoomed image, to read a
        drawing's small numbers.

        A land-pattern figure read as a whole page is too small: a 0.75
        among 0.5 spacings or a stepped land corner is lost. Read the image
        it writes. Numbers printed as text in the region are also listed.

        Args:
            path: the PDF on disk.
            page: 1-based.
            region: [left, top, right, bottom] as fractions of the page
                from its top left, e.g. [0.3, 0.2, 0.75, 0.6]; empty is the
                whole page.
            dpi: 72 to 800 (default 400).

        Returns:
            ``{ok, png, width, height, region, dpi, numbers: [{value, at}]}``,
            ``at`` the number's place as fractions of the image.
        """
        from ..design.datasheet_render import render_region

        out = get_config().workspace_dir / "renders" /             f"{Path(path).stem}_p{int(page)}.png"
        return await asyncio.to_thread(render_region, path, page, region, dpi, str(out))

    @mcp.tool()
    async def footprint_compare_drawing(package: Any, region: list[float],
                                        view: str = "top",
                                        dpi: int = 400,
                                        names_page: int = 0,
                                        names_region: Optional[list[float]] = None,
                                        names_view: str = "") -> dict[str, Any]:
        """Set the land-pattern figure beside the lands a package builds,
        to compare them by eye before the footprint is drawn.

        The figure is source.page of source.path, zoomed to ``region``;
        beside it the lands are drawn alike, each numbered, the gap between
        every pair of neighbouring rows and columns written in red, land
        sizes listed. Read the image and compare every land, gap, size and
        shape with the figure; correct the package for any difference.
        lib_create_footprint_from_package takes custom_pads only with the
        ``compared`` id this returns for those same lands. The package body
        is drawn dashed over the lands.

        Lands named like balls (A1, H8) need their names checked against a
        figure that prints them: when the land figure does not, give the
        ball map (pinout) as names_page and names_region; it is set beside
        the lands drawn as the map is (names_view). Names the map prints as
        text are matched to the lands by position, and a mismatch withholds
        the id.

        Args:
            package: the PackageSpec as for lib_create_footprint_from_package.
            region: the figure on its page, [left, top, right, bottom] as
                fractions of the page from its top left.
            view: "top", or "bottom" when the figure is drawn from below
                (the lands are mirrored to match it).
            dpi: 72 to 800 (default 400).
            names_page: the ball map's page in the same PDF.
            names_region: the ball map on that page, as ``region``.
            names_view: "top" or "bottom", the side the ball map is drawn
                from (default ``view``).

        Returns:
            ``{ok, png, compared, printed_numbers, not_built, ball_names}``:
            ``not_built`` lists numbers printed as text in the figure that no
            land size, gap or span of the built lands shows; ``ball_names``
            ``{checked, mismatched}`` for names the figures print as text.
        """
        from ..design.datasheet_render import render_region
        from ..design.landpattern.builder import build_footprint
        from ..design.landpattern.check import read_package
        from ..design.landpattern.compare_figure import (
            axis_names, built_values, check_ball_names, compared_id, draw_lands,
            grid_named, land_outlines, names_in_figure, side_by_side)
        from ..design.landpattern.spec import SpecError

        names_view = names_view or view
        if view not in ("top", "bottom") or names_view not in ("top", "bottom"):
            return {"ok": False, "reason": "view and names_view are top or bottom"}
        package, _, _ = _split_dims(package)
        if isinstance(package, dict):
            package = {k: v for k, v in package.items() if k != "compared"}
        try:
            spec = read_package(package)
            built = build_footprint(spec)
            pads = list(built.pads)
            outlines = land_outlines(built)
            b3 = getattr(built, "body", None)
            body = ((b3.x0, b3.y0, b3.x1, b3.y1)
                    if b3 is not None and b3.x1 > b3.x0 and b3.y1 > b3.y0 else None)
        except SpecError as exc:
            return {"ok": False, "reason": str(exc)}
        src = spec.source
        path = str(getattr(src, "path", "") or "")
        pages = sorted(_cited(str(getattr(src, "page", "") or "")))
        if not path or not pages:
            return {"ok": False, "reason": "source needs path (the PDF) and page "
                                           "(the land-pattern figure's page)"}
        out = get_config().workspace_dir / "renders"
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(getattr(spec, "name", "") or
                                                       Path(path).stem)).strip("._")
        fig = await asyncio.to_thread(render_region, path, pages[0], region, dpi,
                                      str(out / f"{name}_figure.png"))
        cannot = ("the comparison could not run, so this footprint cannot be "
                  "checked: stop and report why; do not build its lands another way")
        if not fig.get("ok"):
            return {**fig, "next": cannot}
        names_fig: dict[str, Any] = {}
        if names_page:
            names_fig = await asyncio.to_thread(
                render_region, path, int(names_page), names_region, dpi,
                str(out / f"{name}_ball_map.png"))
            if not names_fig.get("ok"):
                return {**names_fig, "next": cannot}

        def compose() -> str:
            from PIL import Image

            figure = Image.open(fig["png"]).convert("RGB")
            lands = draw_lands(pads, max(figure.height, 600), view,
                               f"built: {name}", width_px=max(figure.width, 600),
                               outlines=outlines, body=body)
            image = side_by_side(figure, lands)
            if names_fig:
                ball_map = Image.open(names_fig["png"]).convert("RGB")
                named = draw_lands(pads, max(ball_map.height, 600), names_view,
                                   f"names: {name}", width_px=max(ball_map.width, 600),
                                   outlines=outlines, body=body)
                image = side_by_side(image, side_by_side(ball_map, named))
            target = out / f"{name}_vs_drawing.png"
            image.save(target)
            return str(target)

        png = await asyncio.to_thread(compose)
        from ..design.landpattern import compare_figure

        values = built_values(pads)
        printed = sorted({n["value"] for n in fig.get("numbers", [])},
                         key=float)
        not_built = [v for v in printed
                     if not any(abs(float(v) - b) <= 0.01 for b in values)]
        result: dict[str, Any] = {
            "ok": True, "png": png, "page": pages[0],
            "printed_numbers": printed, "not_built": not_built,
            "note": "read the image: compare every land, gap, size and shape "
                    "with the figure, and correct the package before drawing"}
        if not printed:
            result["printed_numbers_note"] = fig.get("note")
        if grid_named(pads):
            in_figure = names_in_figure(pads, fig.get("names", []))
            if names_fig:
                marks = names_fig.get("names", [])
                if names_in_figure(pads, marks) < len(pads) // 2:
                    marks = axis_names(names_fig.get("labels", []), pads) or marks
                names = check_ball_names(pads, marks, names_view)
            elif in_figure >= min(4, len(pads)):
                names = check_ball_names(pads, fig.get("names", []), view)
            else:
                return {**result, "ok": False, "reason": (
                    "these lands carry ball names and the land figure prints "
                    "none, so the names are unchecked"), "next": (
                    "call again with names_page and names_region (the ball map "
                    "or pinout figure) and names_view")}
            result["ball_names"] = names
            if names.get("mismatched"):
                return {**result, "ok": False, "reason": (
                    "ball names differ from the map: " + ", ".join(
                        f"{m['built']} where the map has {m['map']}"
                        for m in names["mismatched"][:8])),
                    "next": "correct the names in the package and compare again"}
            if not names.get("checked"):
                result["ball_names_note"] = (
                    "the ball map prints no names as text: compare every land's "
                    "name with it by eye")
        compare_figure.ISSUED[compared_id(pads, view, outlines)] = len(pads)
        result["compared"] = compared_id(pads, view, outlines)
        return result

    @mcp.tool()
    async def footprint_check_package(package: Any) -> dict[str, Any]:
        """Check a package description for numbers that cannot be right.

        Run this on every package transcribed from a datasheet, before
        drawing a footprint from it. Offline.

        Sanity checks (each finding names the dimensions, their values and
        citations, and why the combination is impossible or unusual):
        limits in order; lead width below the pitch; each row of pins fits
        the body it comes out of; lead span larger than the body for
        leaded parts; the foot shorter than the lead's reach and two
        terminals fitting the span; the exposed pad inside the body and
        clear of the terminals; ball diameter below the pitch; values 25.4
        times off their neighbours (mils typed as mm); pin counts the
        family can have; the vendor land pattern consistent with itself.
        ``error`` is impossible, ``warning`` unusual.

        Cross-check, when the package carries the datasheet's recommended
        land pattern as ``vendor``: the IPC pattern computed from the
        package against it, per quantity (pitch, span, land length and
        width, tab, exposed pad, grid land), with both numbers, both
        citations and the difference. Beyond the tolerance measured
        between real vendor patterns and IPC, or a factor of two apart,
        the pair is reported as a likely transcription error in one of the
        two sources.

        Args:
            package: the PackageSpec as an object or JSON text, the same
                schema lib_create_footprint_from_package takes.

        Returns:
            ``{ok, errors, warnings, findings, cross_check, examined,
            notes}``. ``ok`` is true when no sanity finding is an error and
            the cross-check (if it ran) agreed.
        """
        from ..design.landpattern.check import check_package, read_package
        from ..design.landpattern.compare import cross_check
        from ..design.landpattern.spec import SpecError

        package, raw_dims, raw_numbers = _split_dims(package)
        report = check_package(package)
        out: dict[str, Any] = dict(report)
        cross: dict[str, Any]
        if report["errors"]:
            cross = {"ran": False, "ok": True, "checks": [],
                     "reason": "the package has sanity errors; the IPC pattern "
                               "from it would compare a misread number"}
        else:
            try:
                spec = read_package(package)
                cross = cross_check(spec)
            except SpecError as exc:
                cross = {"ran": False, "ok": False, "checks": [],
                         "reason": f"the engine refused the package: {exc}"}
                out["ok"] = False
        out["cross_check"] = cross
        if cross.get("ran") and not cross.get("ok"):
            out["ok"] = False
        if not report["errors"]:
            from ..design.landpattern.land_source import check_land_source
            try:
                land = await asyncio.to_thread(
                    check_land_source, read_package(package).source)
            except SpecError:
                land = None
            if land is not None:
                out["land_source"] = land
                if not land["ok"]:
                    out["ok"] = False
        if (raw_dims or raw_numbers) and not report["errors"]:
            from ..design.landpattern.builder import build_footprint
            from ..design.landpattern.drawing_dims import (
                check_dims, read_dims, read_numbers, unplaced_numbers)
            try:
                dims = read_dims(raw_dims)
                rows = check_dims(dims, build_footprint(read_package(package)).pads)
                left = unplaced_numbers(read_numbers(raw_numbers), dims)
            except SpecError as exc:
                rows, left = [{"ok": False, "why": str(exc)}], []
            out["drawing_dims"] = rows
            if left:
                out["unplaced_numbers"] = left
            if left or any(not r["ok"] for r in rows):
                out["ok"] = False
        bad = [c for c in cross.get("checks", []) if not c.get("ok")]
        out["summary"] = (
            f"{report['errors']} error(s), {report['warnings']} warning(s) in "
            f"the package; cross-check "
            + ("not run" if not cross.get("ran") else
               "agrees" if not bad else
               f"disagrees on {', '.join(c['quantity'] for c in bad)}"))
        return out

    @mcp.tool()
    async def footprint_render_package(
        package: Any,
        density: str = "N",
        path: str = "",
        png: bool = True,
    ) -> dict[str, Any]:
        """Draw the land pattern a package gives, to compare with its datasheet.

        Offline. Renders the footprint the engine would build (lands with
        their numbers, paste windows, silkscreen with the pin 1 dot,
        assembly outline, courtyard, origin and a 1 mm scale bar) and draws
        the package's terminals over the lands as dashed green outlines,
        so a land that misses its terminal shows. A caption gives the
        pattern's pitch, span and land sizes. Read the returned image next
        to the datasheet's package drawing and recommended land pattern.

        Args:
            package: the PackageSpec as an object or JSON text.
            density: L, N or M (least, nominal, most).
            path: where to write the SVG; default
                ``<workspace>/renders/<name>_<density>.svg``.
            png: also write a PNG beside it through the headless browser
                the board renders use, when one is installed.

        Returns:
            ``{ok, svg_path, png_path, name, density, metrics, warnings}``,
            with ``rasterize_note`` when no PNG could be made.
        """
        from ..design.landpattern.audit import terminal_outlines
        from ..design.landpattern.check import read_package
        from ..design.landpattern.compare import pattern_metrics
        from ..design.landpattern.emit import model_from_package
        from ..design.landpattern.spec import SpecError
        from ..design.landpattern.svg import render_svg

        package, _, _ = _split_dims(package)
        try:
            spec = read_package(package)
            model = model_from_package(spec, density=density or "N")
        except SpecError as exc:
            return {"ok": False, "reason": str(exc)}
        terms = terminal_outlines(spec, model)
        notes = _metrics_lines(model)
        if terms:
            notes.append("dashed green: the package's terminals, nominal size")
        svg = render_svg(model, notes=notes, terminals=terms)
        if path:
            target = Path(path)
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
        else:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model.name).strip("._") \
                or "footprint"
            target = get_config().workspace_dir / "renders" / \
                f"{safe}_{model.density}.svg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(svg, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "reason": f"could not write {target}: {exc}"}
        out: dict[str, Any] = {
            "ok": True, "svg_path": str(target), "png_path": None,
            "name": model.name, "density": model.density,
            "metrics": pattern_metrics(model), "terminals_drawn": len(terms),
            "warnings": list(model.warnings),
            "legend": {"copper": "lands", "dark": "paste windows",
                       "black": "silkscreen", "blue dashed": "assembly",
                       "magenta": "courtyard",
                       "green dashed": "package terminals (nominal)"},
        }
        if png:
            from ..render import rasterize_svg, size_svg_for_raster

            sized, w_px, h_px = size_svg_for_raster(svg, target_width=1400)
            raster = target.with_suffix(".raster.svg")
            try:
                raster.write_text(sized, encoding="utf-8")
                rr = await asyncio.to_thread(
                    rasterize_svg, str(raster), str(target.with_suffix(".png")),
                    w_px, h_px)
            finally:
                try:
                    raster.unlink()
                except OSError:
                    pass
            if rr.get("ok"):
                out["png_path"] = rr["png_path"]
            else:
                out["rasterize_note"] = rr.get("reason")
        out["next_step"] = (f"Read {out['png_path'] or out['svg_path']} beside "
                            "the datasheet's package drawing")
        return out
