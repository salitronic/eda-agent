# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic / PCB rendering tools.

Independent of any third-party Altium parser. Geometry is pulled from
the running Altium session via the existing DelphiScript bridge, then
rendered to SVG in pure Python. Output is interactive-ready (``data-*``
attributes on every group) so a dashboard or LLM client can hook
hover / click / cross-probe events without re-parsing.

Shipped surface:
  - ``sch_render_svg`` : active SchDoc -> SVG file.
  - ``pcb_render_svg`` : per-layer PCB rendering.
  - ``design_visual_review``: render + rasterize to PNG + a layout
    critique rubric (the visual self-check loop).

Renders are GEOMETRY ONLY -- a visual artifact for layout/placement
review or when an image is genuinely needed. They are NEVER the basis
for judging connectivity or part values; that comes from the netlist
(see each tool's docstring and the design discipline).

Still deferred: full symbol-internal fidelity (the schematic renderer
draws labelled bounding-box bodies with pin stubs, not the symbol art).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..bridge import get_bridge
from ..config import get_config
from ..render import (
    render_sch_svg, SchRenderOptions,
    render_pcb_svg, PcbRenderOptions,
    rasterize_svg, size_svg_for_raster, visual_review_guidance,
)
from ..render.bom_html import render_bom_html


def _renders_dir() -> Path:
    """Transient render-output directory, kept OUT of the workspace root so
    renders don't pile up in (any) user's folder. Created on demand."""
    d = get_config().workspace_dir / "renders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_render_tools(mcp):
    """Register rendering tools with the MCP server."""

    @mcp.tool()
    async def sch_render_svg(
        output_path: Optional[str] = None,
        margin_mils: int = 200,
        flip_y: bool = True,
    ) -> dict[str, Any]:
        """Render the active SchDoc to an SVG file.

        For a picture of layout and readability. Never judge
        connectivity, values or pin functions from it: use
        ``proj_get_nets``, ``proj_get_connectivity_many`` or
        ``design_review_snapshot``.

        Draws components as labelled boxes with pin stubs (not the symbol
        art), wires, junctions, labels, ports and power ports, each group
        carrying ``data-designator``, ``data-net`` and ``data-pin``.

        Args:
            output_path: SVG path; default the workspace renders folder.
            margin_mils: Padding around the drawing (default 200).
            flip_y: Flip Y to Altium's bottom-left origin (default True).

        Returns:
            ``ok``, ``svg_path``, ``doc``, ``counts``, ``bytes``.
        """
        bridge = get_bridge()
        geometry = await bridge.send_command_async(
            "generic.get_sch_geometry", {}, timeout=60.0,
        )
        if not isinstance(geometry, dict):
            return {"ok": False, "reason": "no geometry returned"}
        opts = SchRenderOptions(margin=margin_mils, flip_y=flip_y)
        svg = render_sch_svg(geometry, opts)

        doc = geometry.get("doc") or "schematic"
        # Strip directory components if Altium handed back a full path;
        # we write under the workspace by default.
        doc_name = Path(doc).name or "schematic"
        if not doc_name.lower().endswith(".svg"):
            doc_name = (
                Path(doc_name).with_suffix(".svg").name
                if doc_name else "schematic.svg"
            )

        if output_path:
            target = Path(output_path)
        else:
            target = _renders_dir() / doc_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg, encoding="utf-8")

        return {
            "ok": True,
            "svg_path": str(target),
            "doc": geometry.get("doc"),
            "counts": geometry.get("counts") or {},
            "bytes": len(svg),
        }

    @mcp.tool()
    async def pcb_render_svg(
        output_path: Optional[str] = None,
        layers: Optional[list[str]] = None,
        margin_mils: int = 250,
        flip_y: bool = True,
        show_drills: bool = True,
        show_texts: bool = True,
        show_designators: bool = True,
        interactive_legend: bool = True,
        background: str = "#1f2937",
    ) -> dict[str, Any]:
        """Render the active PcbDoc to an SVG file.

        For a picture of placement, silkscreen or spacing. Never judge
        connectivity, nets or clearance from it: use ``pcb_get_nets``,
        ``pcb_get_unrouted_nets``, ``pcb_run_drc`` or
        ``proj_compare_sch_pcb``.

        Draws the outline, tracks, arcs, pads, vias and overlay texts;
        not regions, pours or component-body outlines. Each primitive
        group carries ``data-net``, ``data-layer`` and ``data-shape``.

        Args:
            output_path: SVG path; default the workspace renders folder.
            layers: Only these layer names (e.g. ``["TopLayer",
                "TopOverlay"]``); default all. Vias and the outline are
                always drawn.
            margin_mils: Padding around the board.
            flip_y: Flip Y to Altium's bottom-left origin (default True).
            show_drills: Draw pad and via holes.
            show_texts: Draw overlay texts.
            show_designators: Draw designators.
            interactive_legend: Add a clickable per-layer legend.
            background: Background fill; ``"none"`` for transparent.

        Returns:
            ``ok``, ``svg_path``, ``counts``, ``bbox`` (mils), ``bytes``.
        """
        bridge = get_bridge()
        geometry = await bridge.send_command_async(
            "generic.get_pcb_geometry", {}, timeout=120.0,
        )
        if not isinstance(geometry, dict):
            return {"ok": False, "reason": "no geometry returned"}

        opts = PcbRenderOptions(
            margin_mils=margin_mils, flip_y=flip_y, layers=layers,
            background=background, show_drills=show_drills,
            show_texts=show_texts, show_designators=show_designators,
            interactive_legend=interactive_legend,
        )
        svg = render_pcb_svg(geometry, opts)

        if output_path:
            target = Path(output_path)
        else:
            target = _renders_dir() / "pcb.svg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg, encoding="utf-8")

        return {
            "ok": True,
            "svg_path": str(target),
            "counts": geometry.get("counts") or {},
            "bbox": geometry.get("bbox") or {},
            "bytes": len(svg),
        }

    @mcp.tool()
    async def design_visual_review(
        target: str = "auto",
        output_path: Optional[str] = None,
        rasterize: bool = True,
        width: int = 1600,
        margin_mils: Optional[int] = None,
        layers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Render the active sheet or board to a PNG you can Read, with a
        critique ``rubric`` naming the audit tool that measures each item.

        SCOPE: layout and placement only (readability, spacing, overlaps,
        silkscreen). Never judge connectivity, values or wiring from the
        image: use ``design_review_snapshot`` and the netlist tools. Loop:
        render, Read ``png_path``, critique, run the named audits, fix,
        render again.

        Args:
            target: "auto" (the active document), "schematic" or "pcb".
            output_path: SVG path (PNG beside it); default the workspace
                renders folder.
            rasterize: Also make the PNG (default True; ``rasterize_note``
                if no browser).
            width: PNG width in pixels.
            margin_mils: Render margin.
            layers: PCB only: these layers.

        Returns:
            ``ok``, ``target``, ``doc``, ``svg_path``, ``png_path``,
            ``counts``, ``bbox``, ``rubric``, ``loop_protocol``,
            ``next_step``.
        """
        bridge = get_bridge()
        t = (target or "auto").strip().lower()
        if t in ("auto", ""):
            info = await bridge.send_command_async(
                "application.get_active_document")
            kind = ""
            if isinstance(info, dict):
                kind = str(info.get("document_kind")
                           or info.get("file_name") or "").lower()
            # Libraries FIRST. DM_DocumentKind spells them 'PCBLIB' and
            # 'SCHLIB', so a plain substring test reads "pcb" out of
            # "pcblib" and sends a PcbLib down the PcbDoc geometry path,
            # where the user gets an obscure geometry error instead of
            # being told the active document is a library. This is not a
            # rare state: every library-authoring call leaves a SchLib or
            # PcbLib active, which is exactly when someone renders to
            # check their work.
            if "lib" in kind:
                return {
                    "ok": False,
                    "reason": f"the active document is a LIBRARY "
                    f"({kind or 'unknown'}), not a schematic or board. "
                    f"Open the .SchDoc / .PcbDoc you want to review, or "
                    f"pass target='schematic'/'pcb' explicitly to try "
                    f"anyway.",
                    "active_document": info,
                }
            if "pcb" in kind:
                t = "pcb"
            elif "sch" in kind:
                t = "schematic"
            else:
                return {
                    "ok": False,
                    "reason": "could not detect the active document kind; "
                    "pass target='schematic' or target='pcb'",
                    "active_document": info,
                }
        if t in ("sch", "schdoc"):
            t = "schematic"
        if t in ("pcbdoc", "board"):
            t = "pcb"
        if t not in ("schematic", "pcb"):
            return {"ok": False,
                    "reason": f"target must be schematic or pcb (got {target!r})"}

        if t == "schematic":
            geometry = await bridge.send_command_async(
                "generic.get_sch_geometry", {}, timeout=60.0)
            if not isinstance(geometry, dict):
                return {"ok": False, "reason": "no schematic geometry returned"}
            opts = SchRenderOptions(
                margin=margin_mils if margin_mils is not None else 200)
            svg = render_sch_svg(geometry, opts)
            doc = geometry.get("doc") or "schematic"
            default_name = (Path(str(doc)).with_suffix(".review.svg").name
                            or "schematic.review.svg")
        else:
            geometry = await bridge.send_command_async(
                "generic.get_pcb_geometry", {}, timeout=120.0)
            if not isinstance(geometry, dict):
                return {"ok": False, "reason": "no PCB geometry returned"}
            # No interactive legend: its foreignObject HTML is not
            # well-formed XML, which breaks standalone-SVG rasterization.
            # A static critique image does not need the checkboxes anyway.
            opts = PcbRenderOptions(
                margin_mils=margin_mils if margin_mils is not None else 250,
                layers=layers, interactive_legend=False)
            svg = render_pcb_svg(geometry, opts)
            doc = geometry.get("doc") or "pcb"
            default_name = "pcb.review.svg"

        if output_path:
            svg_target = Path(output_path)
            if svg_target.suffix.lower() != ".svg":
                svg_target = svg_target.with_suffix(".svg")
        else:
            svg_target = _renders_dir() / default_name
        svg_target.parent.mkdir(parents=True, exist_ok=True)
        svg_target.write_text(svg, encoding="utf-8")

        result: dict[str, Any] = {
            "ok": True,
            "target": t,
            "doc": geometry.get("doc"),
            "svg_path": str(svg_target),
            "png_path": None,
            "counts": geometry.get("counts") or {},
            "bbox": geometry.get("bbox") or {},
        }

        if rasterize:
            # Give the responsive SVG explicit pixel dims, write a sized
            # copy, and screenshot it. Best-effort: a failure leaves the
            # SVG as the fallback artifact.
            sized, w_px, h_px = size_svg_for_raster(svg, target_width=int(width))
            raster_svg = svg_target.with_suffix(".raster.svg")
            raster_svg.write_text(sized, encoding="utf-8")
            png_target = str(svg_target.with_suffix(".png"))
            rr = rasterize_svg(str(raster_svg), png_target, width=w_px, height=h_px)
            # The sized copy is pure scratch for the screenshot -- never leave
            # it cluttering the user's folder.
            try:
                raster_svg.unlink()
            except OSError:
                pass
            if rr.get("ok"):
                result["png_path"] = rr["png_path"]
            else:
                result["rasterize_note"] = rr.get("reason")

        result.update(visual_review_guidance(t))
        viewable = result["png_path"] or result["svg_path"]
        result["next_step"] = (
            f"Read {viewable} and critique it against `rubric`, then work "
            "through `loop_protocol`."
        )
        return result

    @mcp.tool()
    async def proj_export_bom_html(
        output_path: Optional[str] = None,
        title: str = "Bill of Materials",
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Export the project BOM as a self-contained interactive HTML file.

        Standalone HTML page, no external CSS / JS / Altium runtime
        dependency. Open it in any browser; sortable columns, free-text
        filter, toggle between grouped (one row per value+footprint) and
        per-component layouts. A self-contained one-shot static export.

        Useful for emailing the BOM to a manufacturer, archiving with a
        board release, or sharing with a non-Altium reviewer.

        Args:
            output_path: Where to write the HTML. Defaults to
                ``<workspace>/bom.html``.
            title: Heading + ``<title>`` for the page.
            limit: Max components to include (default 5000; the BOM call
                supports up to ~10000 cleanly).

        Returns:
            Dict with ``ok``, ``html_path``, ``components`` (count
            written), ``bytes``.
        """
        bridge = get_bridge()
        bom = await bridge.send_command_async(
            "project.get_bom",
            {"limit": str(limit)},
        )
        if not isinstance(bom, dict):
            return {"ok": False, "reason": "no BOM returned"}

        # Resolve project name for the sub-heading -- best-effort, the
        # focused-project lookup isn't critical for the export to work.
        project_name = ""
        try:
            focused = await bridge.send_command_async(
                "project.get_focused", {})
            if isinstance(focused, dict):
                project_name = (focused.get("project_name")
                                or focused.get("name") or "")
        except Exception:  # the name only labels the export; it runs without it
            pass

        html_str = render_bom_html(bom, title=title, project=project_name)

        if output_path:
            target = Path(output_path)
        else:
            target = get_config().workspace_dir / "bom.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_str, encoding="utf-8")

        return {
            "ok": True,
            "html_path": str(target),
            "components": len(bom.get("components") or []),
            "bytes": len(html_str),
        }
