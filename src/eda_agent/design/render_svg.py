# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Render a SchematicCanvas to SVG.

The iteration feedback loop: pure-Python layout produces a canvas, the
renderer dumps it to SVG, you open the SVG in a browser, look at the
result, change the layout, repeat. The Altium emit step only runs once
the SVG looks right.

The renderer is intentionally schematic-recognizable, not pixel-faithful
to Altium. Bodies are plain rectangles, wires are blue, junctions are
filled dots, power ports use simplified glyphs that match the standard
EDA conventions (GND triangle, VCC circle with bar, etc.).

Coordinate system:
- Canvas units are mils, y-up (matches Altium SchDoc).
- SVG units are pixels, y-down. We flip y when emitting.
- Scale is configurable; default 0.1 px/mil keeps a full A4 sheet
  (11500x7600 mils) under 1200x900 px, large enough to read pin names.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape as html_escape
from typing import Optional

from eda_agent.design.canvas import (
    Junction,
    BusEntry,
    BusSegment,
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
    pin_direction_world,
)


@dataclass
class RenderOptions:
    """Knobs for the SVG output."""

    scale: float = 0.10  # px per mil
    margin_px: int = 40
    body_stroke: str = "#000000"
    body_fill: str = "#fffce6"  # pale yellow, the Altium default body fill
    pin_stroke: str = "#000000"
    wire_stroke: str = "#0066cc"  # Altium default wire blue
    bus_stroke: str = "#0033aa"   # Altium bus: a thicker, darker blue
    label_color: str = "#0066cc"
    power_port_stroke: str = "#cc2200"
    junction_fill: str = "#0066cc"
    background: str = "#ffffff"
    font_family: str = "monospace"
    show_pin_numbers: bool = True
    show_pin_names: bool = True


def render_canvas_svg(
    canvas: SchematicCanvas,
    sheet: Optional[str] = None,
    options: Optional[RenderOptions] = None,
) -> str:
    """Render one sheet of the canvas to a standalone SVG string.

    If `sheet` is omitted, renders the first sheet on the canvas.

    Returns the SVG source as a string; the caller writes it to disk.
    Always returns valid SVG, even when the canvas is empty (a blank
    sheet frame), so an empty pipeline run still produces a viewable
    artifact.
    """
    options = options or RenderOptions()
    sheet_obj = _resolve_sheet(canvas, sheet)
    parts: list[str] = []
    parts.append(_svg_header(sheet_obj, options))
    parts.append(_svg_sheet_frame(sheet_obj, options))
    # Order matters for visual stacking: wires under bodies under ports.
    for wire in canvas.wires_on(sheet_obj.name):
        parts.append(_svg_wire(wire, sheet_obj, options))
    for entry in canvas.bus_entries_on(sheet_obj.name):
        parts.append(_svg_bus_entry(entry, sheet_obj, options))
    for bus in canvas.buses_on(sheet_obj.name):
        parts.append(_svg_bus(bus, sheet_obj, options))
    for inst in canvas.instances_on(sheet_obj.name):
        parts.append(_svg_instance(inst, sheet_obj, options))
    for junction in canvas.junctions_on(sheet_obj.name):
        parts.append(_svg_junction(junction, sheet_obj, options))
    for label in canvas.labels_on(sheet_obj.name):
        parts.append(_svg_label(label, sheet_obj, options))
    for port in canvas.power_ports_on(sheet_obj.name):
        parts.append(_svg_power_port(port, sheet_obj, options))
    parts.append("</svg>")
    return "\n".join(parts)


def _resolve_sheet(
    canvas: SchematicCanvas, sheet_name: Optional[str]
) -> Sheet:
    if not canvas.sheets:
        return Sheet(name=sheet_name or "main")
    if sheet_name is None:
        return canvas.sheets[0]
    for s in canvas.sheets:
        if s.name == sheet_name:
            return s
    raise KeyError(f"sheet {sheet_name!r} not on canvas")


# Coordinate conversion: mils -> svg px. Altium y is up; SVG y is down,
# so we flip y around the sheet height. Margin moves the origin off the
# top-left so the sheet frame has whitespace around it.
def _mils_to_svg(
    x_mils: int, y_mils: int, sheet: Sheet, options: RenderOptions
) -> tuple[float, float]:
    sx = options.margin_px + x_mils * options.scale
    sy = options.margin_px + (sheet.height_mils - y_mils) * options.scale
    return (sx, sy)


def _svg_header(sheet: Sheet, options: RenderOptions) -> str:
    w = int(sheet.width_mils * options.scale + 2 * options.margin_px)
    h = int(sheet.height_mils * options.scale + 2 * options.margin_px)
    title = html_escape(sheet.title or sheet.name)
    # Embed coordinate-conversion data so a JS frontend can map drag
    # events back to mils. The drag-edit UI uses these to compute new
    # part positions when the user moves a component group.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="{options.font_family}" '
        f'data-scale="{options.scale}" data-margin-px="{options.margin_px}" '
        f'data-sheet-width-mils="{sheet.width_mils}" '
        f'data-sheet-height-mils="{sheet.height_mils}">'
        f"\n<title>{title}</title>"
        f'\n<rect x="0" y="0" width="{w}" height="{h}" '
        f'fill="{options.background}"/>'
    )


def _svg_sheet_frame(sheet: Sheet, options: RenderOptions) -> str:
    x0, y0 = _mils_to_svg(0, sheet.height_mils, sheet, options)
    x1, y1 = _mils_to_svg(sheet.width_mils, 0, sheet, options)
    return (
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" '
        f'height="{y1 - y0:.1f}" fill="none" stroke="#888" '
        f'stroke-width="1"/>'
    )


def _svg_instance(
    inst: SymbolInstance, sheet: Sheet, options: RenderOptions
) -> str:
    out: list[str] = []
    # Wrap the whole component in a <g class="component"> tagged with
    # its refdes + current mil-frame position so a drag-edit frontend
    # can grab it and remap mouse deltas to mil-space updates. The
    # `transform="translate(0,0)"` is the JS's manipulation surface.
    out.append(
        f'<g class="component" data-refdes="{html_escape(inst.refdes)}" '
        f'data-x-mils="{inst.x}" data-y-mils="{inst.y}" '
        f'data-rotation="{inst.rotation}" '
        f'data-flipped="{"1" if inst.flipped else "0"}" '
        f'transform="translate(0,0)">'
    )
    # Body rectangle from the rotated+translated bbox.
    bb = inst.world_bbox()
    x0, y0 = _mils_to_svg(bb.x_min, bb.y_max, sheet, options)
    x1, y1 = _mils_to_svg(bb.x_max, bb.y_min, sheet, options)
    out.append(
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" '
        f'height="{y1 - y0:.1f}" fill="{options.body_fill}" '
        f'stroke="{options.body_stroke}" stroke-width="1"/>'
    )
    # Designator and value. When text_placement chose collision-free
    # anchors, honour them (canvas mils, lower-left of each text line);
    # otherwise fall back to the legacy above-top-left stacking.
    has_value = bool(inst.value)
    if inst.designator_pos is not None:
        dx_, dy_ = _mils_to_svg(
            inst.designator_pos[0], inst.designator_pos[1], sheet, options)
        out.append(
            f'<text x="{dx_:.1f}" y="{dy_:.1f}" font-size="11" '
            f'fill="#222">{html_escape(inst.refdes)}</text>'
        )
        if has_value and inst.value_pos is not None:
            vx_, vy_ = _mils_to_svg(
                inst.value_pos[0], inst.value_pos[1], sheet, options)
            out.append(
                f'<text x="{vx_:.1f}" y="{vy_:.1f}" font-size="10" '
                f'fill="#1565c0">{html_escape(inst.value)}</text>'
            )
    else:
        refdes_y = y0 - (15 if has_value else 4)
        out.append(
            f'<text x="{x0 + 4:.1f}" y="{refdes_y:.1f}" font-size="11" '
            f'fill="#222">{html_escape(inst.refdes)}</text>'
        )
        if has_value:
            out.append(
                f'<text x="{x0 + 4:.1f}" y="{y0 - 4:.1f}" font-size="10" '
                f'fill="#1565c0">{html_escape(inst.value)}</text>'
            )
    # Pins: a line from the body-attach end (pin endpoint - length*dir)
    # to the world endpoint, then the pin number near the endpoint and
    # the pin name near the body-attach end.
    for endpoint in inst.all_pin_endpoints():
        dx, dy = pin_direction_world(endpoint.orientation)
        body_x = endpoint.x - endpoint.length * dx
        body_y = endpoint.y - endpoint.length * dy
        ex, ey = _mils_to_svg(endpoint.x, endpoint.y, sheet, options)
        bx, by = _mils_to_svg(body_x, body_y, sheet, options)
        out.append(
            f'<line x1="{ex:.1f}" y1="{ey:.1f}" x2="{bx:.1f}" '
            f'y2="{by:.1f}" stroke="{options.pin_stroke}" stroke-width="1"/>'
        )
        # Small filled circle at the electrical endpoint so wire connections
        # are visually obvious even before any wire is drawn.
        out.append(
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="1.5" '
            f'fill="{options.pin_stroke}"/>'
        )
        if options.show_pin_numbers:
            # Pin number sits just inside the body, perpendicular to the pin.
            label_x = body_x + 20 * dx
            label_y = body_y + 20 * dy
            lx, ly = _mils_to_svg(label_x, label_y, sheet, options)
            out.append(
                f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="8" '
                f'fill="#666" text-anchor="middle">'
                f'{html_escape(endpoint.pin_id)}</text>'
            )
        if options.show_pin_names and endpoint.electrical_type != "passive":
            # Pin name sits next to the body, on the body side. Skip for
            # passives (R/C/etc) since their pin names are usually "1"/"2".
            pin = inst.symbol.pin_by_id(endpoint.pin_id)
            if pin and pin.name and pin.name not in (pin.designator, ""):
                # INSIDE the body. (dx, dy) points OUT along the pin, from
                # the body edge to the wire end, so the name steps against
                # it. Stepping with it drew every name on its own pin line,
                # on top of the pin number, which is not where Altium puts
                # a pin name and made every IC in a preview look cluttered.
                name_x = body_x - 40 * dx
                name_y = body_y - 40 * dy
                nx, ny = _mils_to_svg(name_x, name_y, sheet, options)
                # Grow away from the edge, into the body.
                anchor = "end" if dx > 0 else "start" if dx < 0 else "middle"
                out.append(
                    f'<text x="{nx:.1f}" y="{ny:.1f}" font-size="9" '
                    f'fill="#444" text-anchor="{anchor}" '
                    f'dominant-baseline="middle">'
                    f'{html_escape(pin.name)}</text>'
                )
    out.append("</g>")  # close component group
    return "\n".join(out)


def _svg_wire(
    wire: WireSegment, sheet: Sheet, options: RenderOptions
) -> str:
    x1, y1 = _mils_to_svg(wire.x1, wire.y1, sheet, options)
    x2, y2 = _mils_to_svg(wire.x2, wire.y2, sheet, options)
    title = html_escape(wire.net) if wire.net else ""
    title_el = f"<title>{title}</title>" if title else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{options.wire_stroke}" stroke-width="2">{title_el}</line>'
    )


def _svg_bus(bus: BusSegment, sheet: Sheet, options: RenderOptions) -> str:
    x1, y1 = _mils_to_svg(bus.x1, bus.y1, sheet, options)
    x2, y2 = _mils_to_svg(bus.x2, bus.y2, sheet, options)
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{options.bus_stroke}" stroke-width="4"/>'
    )


def _svg_bus_entry(
    entry: BusEntry, sheet: Sheet, options: RenderOptions
) -> str:
    x1, y1 = _mils_to_svg(entry.x1, entry.y1, sheet, options)
    x2, y2 = _mils_to_svg(entry.x2, entry.y2, sheet, options)
    title = html_escape(entry.net) if entry.net else ""
    title_el = f"<title>{title}</title>" if title else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{options.bus_stroke}" stroke-width="2">{title_el}</line>'
    )


def _svg_junction(
    junction: Junction, sheet: Sheet, options: RenderOptions
) -> str:
    cx, cy = _mils_to_svg(junction.x, junction.y, sheet, options)
    return (
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" '
        f'fill="{options.junction_fill}"/>'
    )


def _svg_label(
    label: NetLabel, sheet: Sheet, options: RenderOptions
) -> str:
    tx, ty = _mils_to_svg(label.x, label.y, sheet, options)
    # Anchor by orientation so the label reads outward from the pin.
    # 0=right -> label extends right -> text-anchor start
    # 2=left -> label extends left -> text-anchor end
    # 1/3 vertical -> middle, with dy offset for legibility.
    anchor = {
        0: "start",
        1: "middle",
        2: "end",
        3: "middle",
    }.get(label.orientation % 4, "start")
    # Justification 2 (bottom-right, Altium numbering) = text ENDS at
    # the anchor; mirrors what the emitter writes so the offline render
    # shows the same left-of-pin label the live sheet will have.
    if getattr(label, "justification", 0) == 2:
        anchor = "end"
    dy_offset = -4 if label.orientation == 3 else (12 if label.orientation == 1 else 4)
    return (
        f'<text x="{tx:.1f}" y="{ty + dy_offset:.1f}" '
        f'font-size="10" fill="{options.label_color}" '
        f'text-anchor="{anchor}">{html_escape(label.text)}</text>'
    )


def _svg_power_port(
    port: PowerPort, sheet: Sheet, options: RenderOptions
) -> str:
    # Wrap the port in a draggable group keyed by its net text so the
    # editor frontend can move it; the server stores the position as
    # a port_hint and the pipeline pins the centroid there next run.
    body = _svg_power_port_body(port, sheet, options)
    return (
        f'<g class="power-port" data-net="{html_escape(port.text)}" '
        f'data-x-mils="{port.x}" data-y-mils="{port.y}" '
        f'transform="translate(0,0)">'
        f'{body}</g>'
    )


def _svg_power_port_body(
    port: PowerPort, sheet: Sheet, options: RenderOptions,
) -> str:
    """Render a simplified power-port glyph + label.

    The glyph hangs off the wire-connection point and points outward.
    GND-style ports point downward; VCC-style ports point upward. This
    is the visual convention every EDA tool uses and matches Altium's
    default symbol set well enough for an iteration preview.
    """
    cx, cy = _mils_to_svg(port.x, port.y, sheet, options)
    color = options.power_port_stroke
    style = port.style.lower()
    glyph: str
    label_offset_px: int
    is_ground = "gnd" in style or "earth" in style
    if is_ground:
        # GND-family: 3 stepped bars below the connection point.
        glyph = (
            f'<line x1="{cx - 8:.1f}" y1="{cy + 6:.1f}" '
            f'x2="{cx + 8:.1f}" y2="{cy + 6:.1f}" '
            f'stroke="{color}" stroke-width="2"/>'
            f'<line x1="{cx - 5:.1f}" y1="{cy + 10:.1f}" '
            f'x2="{cx + 5:.1f}" y2="{cy + 10:.1f}" '
            f'stroke="{color}" stroke-width="2"/>'
            f'<line x1="{cx - 2:.1f}" y1="{cy + 14:.1f}" '
            f'x2="{cx + 2:.1f}" y2="{cy + 14:.1f}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        label_offset_px = 28
    elif style == "circle":
        # VCC-circle: a small circle hanging above the wire connection.
        glyph = (
            f'<circle cx="{cx:.1f}" cy="{cy - 8:.1f}" r="4" '
            f'fill="none" stroke="{color}" stroke-width="1.5"/>'
        )
        label_offset_px = -18
    elif style == "arrow":
        glyph = (
            f'<polygon points="{cx - 5:.1f},{cy} {cx + 5:.1f},{cy} '
            f'{cx:.1f},{cy - 10:.1f}" fill="none" '
            f'stroke="{color}" stroke-width="1.5"/>'
        )
        label_offset_px = -20
    elif style == "bar":
        glyph = (
            f'<line x1="{cx - 8:.1f}" y1="{cy - 8:.1f}" '
            f'x2="{cx + 8:.1f}" y2="{cy - 8:.1f}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        label_offset_px = -16
    else:  # wave or unknown -> simple ~ glyph
        glyph = (
            f'<path d="M {cx - 8:.1f},{cy - 8:.1f} q 4,-6 8,0 t 8,0" '
            f'fill="none" stroke="{color}" stroke-width="1.5"/>'
        )
        label_offset_px = -20
    label_y = cy + label_offset_px
    label_anchor = "middle"
    return (
        glyph
        + f'<text x="{cx:.1f}" y="{label_y:.1f}" font-size="10" '
        f'fill="{color}" text-anchor="{label_anchor}">'
        f"{html_escape(port.text)}</text>"
    )
