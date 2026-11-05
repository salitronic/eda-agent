# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""PCB SVG renderer.

Takes the geometry payload produced by the Pascal ``Gen_GetPcbGeometry``
handler (board outline + tracks + arcs + pads + vias + texts, in mils,
each tagged with its Altium layer name) and emits a per-layer SVG with
the layers grouped, z-ordered for review, and CSS-toggleable.

Interactive-ready: every track / arc / pad / via group carries
``data-net``, ``data-layer``, and ``data-shape`` so a downstream
dashboard / LLM tool can hook cross-probe and net-highlight without
having to re-parse the board.
"""

from __future__ import annotations

import html
import logging
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable


def _tcolor_to_hex(value: Any) -> str | None:
    """Convert an Altium TColor integer (BGR-packed, ``$00BBGGRR``) to an
    SVG ``#RRGGBB`` string. Returns None for non-integer / missing input so
    callers can fall back to the default palette.
    """
    try:
        v = int(value) & 0xFFFFFF
    except (TypeError, ValueError):
        return None
    r = v & 0xFF
    g = (v >> 8) & 0xFF
    b = (v >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


# Render z-order, bottom-up (= painter's algorithm: first drawn = deepest).
# When viewing the board from the bottom (view_side='bottom') this list is
# reversed so the bottom layers become visually dominant.
#
# Key principle: AUXILIARY layers (mask / paste / assembly / mech) go
# UNDER copper, never over it. The auxiliary layers carry filled regions
# that would obscure copper if drawn on top. Real Altium renders mask
# semi-transparent OVER copper, but the geometry we receive is the
# mask OPENINGS (i.e., where mask is removed), so colouring them on top
# is the inverse of the truth and just hides the copper.
_LAYER_ORDER_TOPVIEW = [
    # Deepest: bottom-side stuff, faded by the view-side multiplier.
    "BottomAssembly",
    "BottomOverlay",
    "BottomPaste",
    "BottomSolder",
    "BottomLayer",
    # Internal copper / planes (also faded).
    "InternalPlane1", "InternalPlane2", "InternalPlane3", "InternalPlane4",
    "InternalPlane5", "InternalPlane6", "InternalPlane7", "InternalPlane8",
    "InternalPlane9", "InternalPlane10", "InternalPlane11", "InternalPlane12",
    "InternalPlane13", "InternalPlane14", "InternalPlane15", "InternalPlane16",
    "MidLayer1", "MidLayer2", "MidLayer3", "MidLayer4", "MidLayer5",
    "MidLayer6", "MidLayer7", "MidLayer8", "MidLayer9", "MidLayer10",
    "MidLayer11", "MidLayer12", "MidLayer13", "MidLayer14", "MidLayer15",
    "MidLayer16", "MidLayer17", "MidLayer18", "MidLayer19", "MidLayer20",
    "MidLayer21", "MidLayer22", "MidLayer23", "MidLayer24", "MidLayer25",
    "MidLayer26", "MidLayer27", "MidLayer28", "MidLayer29", "MidLayer30",
    # Mechanical drawing layers UNDER copper (subtle background reference).
    "Mechanical1", "Mechanical2", "Mechanical3", "Mechanical4",
    "Mechanical5", "Mechanical6", "Mechanical7", "Mechanical8",
    "Mechanical9", "Mechanical10", "Mechanical11", "Mechanical12",
    "Mechanical13", "Mechanical14", "Mechanical15", "Mechanical16",
    "KeepOutLayer",
    # Top-side auxiliary layers UNDER copper (faint references).
    "TopAssembly",
    "TopSolder",
    "TopPaste",
    # Multi-layer pads sit just below top copper so their copper shows
    # against the colour of pads belonging to TopLayer-only nets.
    "MultiLayer",
    # SOLID top copper -- the star of the show.
    "TopLayer",
    # Silkscreen above copper (subtle so copper colour reads).
    "TopOverlay",
]

# Altium's "Maximize PCB Editor" theme native palette. Bright primary
# colours, the same scheme the user sees in Altium itself so the dashboard
# render matches the bench view. Overridable via PcbRenderOptions.layer_colors.
_DEFAULT_COLORS: dict[str, str] = {
    "TopLayer":         "#FF0000",  # red
    "BottomLayer":      "#0000FF",  # blue
    "TopOverlay":       "#FFFF00",  # yellow silkscreen
    "BottomOverlay":    "#FFFF00",
    "TopPaste":         "#A0A0A0",  # neutral grey stencil
    "BottomPaste":      "#606060",
    "TopSolder":        "#A020F0",  # violet mask
    "BottomSolder":     "#8000B0",
    "KeepOutLayer":     "#FF00FF",  # magenta routing keepout
    "MultiLayer":       "#A0A0A0",  # through-hole pad copper
    "DrillGuide":       "#3030FF",
    "DrillDrawing":     "#000000",
    "TopAssembly":      "#00C000",  # green assembly drawing
    "BottomAssembly":   "#A00000",
    # Internal copper layers: brown / brass family so they stay distinct
    # from top (red) and bottom (blue) when faded.
    "MidLayer1":        "#FF8000",
    "MidLayer2":        "#FFA050",
    "MidLayer3":        "#A0A000",
    "MidLayer4":        "#A0FF00",
    "InternalPlane1":   "#FF80A0",
    "InternalPlane2":   "#A080FF",
    "InternalPlane3":   "#80A0FF",
    "InternalPlane4":   "#80FFA0",
    # Mechanical layers: cyan / teal family.
    "Mechanical1":      "#00FFFF",
    "Mechanical2":      "#00C0C0",
    "Mechanical3":      "#008080",
    "Mechanical13":     "#80FFFF",
    "Mechanical15":     "#60E0E0",
    # Renderer-internal pseudo-layers.
    "Outline":          "#FFFFFF",
    "ViaPlated":        "#A0A0A0",
    "Drill":            "#000000",
}

# Subset of layers that get rendered at reduced opacity when they're "the
# other side" of the board the user is looking at. Bottom-side layers
# fade when viewing top, and vice-versa via the view_side reverse.
_BOTTOM_SIDE_LAYERS = {
    "BottomLayer", "BottomPaste", "BottomSolder", "BottomOverlay",
    "BottomAssembly",
}
_TOP_SIDE_LAYERS = {
    "TopLayer", "TopPaste", "TopSolder", "TopOverlay", "TopAssembly",
}
# Internal copper layers always fade -- they live inside the board.
_INNER_LAYERS_PREFIX = ("InternalPlane", "MidLayer")

# Per-layer base opacity. Altium's default 2D view draws layers OPAQUE in
# stack order (the upper layer wins per-pixel) -- so copper, silk, mech and
# keepout all render at full opacity here. The only see-through layers are
# the mask / paste / assembly overlays: they're meant to read AS overlays
# on top of copper (mask openings, paste apertures, courtyards), so we give
# them a moderate alpha. Anything not listed defaults to 1.0 (opaque),
# matching Altium. Tune via PcbRenderOptions if a design needs different
# emphasis.
_LAYER_BASE_OPACITY: dict[str, float] = {
    "TopSolder":         0.45,
    "BottomSolder":      0.45,
    "TopPaste":          0.45,
    "BottomPaste":       0.45,
    "TopAssembly":       0.55,
    "BottomAssembly":    0.55,
}


@dataclass
class PcbRenderOptions:
    """Tuning knobs for the PCB SVG output."""
    margin_mils: int = 250
    flip_y: bool = True
    layers: list[str] | None = None        # None = render all known layers
    background: str = "#0a0a0a"            # near-black to match Altium dark scheme
    outline_stroke_mils: float = 8.0
    layer_colors: dict[str, str] = field(default_factory=dict)
    fade_others_opacity: float = 0.35      # unknown / mech layers when faded
    show_drills: bool = True
    show_texts: bool = True
    show_designators: bool = True          # render per-component designator labels
    show_hidden_text: bool = False         # honour Text.IsHidden / Comp.NameOn
    pad_label_min_mils: float = 30.0       # below this size pad numbers are hidden
    interactive_legend: bool = True        # embed a click-to-toggle layer legend
    # 'top' (default) draws bottom-side faded behind, top-side bright on top.
    # 'bottom' reverses: top-side fades behind, bottom-side bright on top
    # AND the X coordinate gets mirrored so the rendered board matches the
    # physical board when flipped on the bench.
    view_side: str = "top"
    # Opacity for "the other side" so the operator can still see them but
    # they don't compete with the active side. 0.0 = invisible. Altium's
    # own top view shows the far side faintly through the board, so keep
    # this fairly high for a faithful look.
    other_side_opacity: float = 0.45
    # Opacity for internal copper layers (always reduced -- they're inside
    # the board, not visible from either face).
    inner_layer_opacity: float = 0.50
    # Set of layer names Altium currently DISPLAYS (from the geometry
    # payload's "layers" map). Layers not in this set are still rendered
    # but start hidden (display:none) so the default view matches Altium's
    # displayed-layer set while the legend can still toggle them on. None
    # = no visibility info available (render everything visible).
    altium_visible: set[str] | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_pcb_svg(geometry: dict[str, Any],
                   options: PcbRenderOptions | None = None) -> str:
    """Render the PCB geometry payload to a self-contained SVG string."""
    opt = options or PcbRenderOptions()

    # Pull Altium's actual per-layer colours + visibility out of the
    # geometry payload (emitted by Gen_GetPcbGeometry) so the render
    # reproduces exactly what the user sees on the bench. Real colours win
    # over the built-in default palette; an explicit caller-supplied
    # layer_colors override still wins over Altium (so callers can force a
    # colour). Visibility seeds opt.altium_visible so hidden layers start
    # collapsed but stay legend-toggleable.
    layers_meta = geometry.get("layers")
    if isinstance(layers_meta, dict) and layers_meta:
        altium_colors: dict[str, str] = {}
        visible: set[str] = set()
        for name, meta in layers_meta.items():
            if not isinstance(meta, dict):
                continue
            hexc = _tcolor_to_hex(meta.get("color"))
            if hexc:
                altium_colors[name] = hexc
            if meta.get("visible"):
                visible.add(name)
        opt = replace(
            opt,
            layer_colors={**altium_colors, **opt.layer_colors},
            # Non-empty set => use Altium's displayed-layer set. Empty =>
            # treat as "no info" (None) so we don't hide every layer.
            altium_visible=(visible or None),
        )

    pieces: list[str] = []
    pieces.extend(_compute_viewbox_and_root(geometry, opt))
    pieces.append(_embedded_style(opt))
    pieces.append(_render_background(geometry, opt))

    # Bottom-view: mirror X around the board's centre so the rendered
    # board matches the physical board when flipped on the bench. The
    # viewBox stays the same; we wrap the rest of the SVG in a scaleX(-1)
    # group translated by the X span.
    bottom_view = (opt.view_side or "top").lower() == "bottom"
    if bottom_view:
        bbox = geometry.get("bbox") or {}
        # Mirror around the board centre cx=(x1+x2)/2: translate(2*cx)
        # scale(-1,1) = translate(x1+x2) scale(-1,1). The bbox uses x1/x2
        # (same keys as the viewBox); reading x_min/x_max here was the bug
        # that flung the flipped board off-screen.
        x1 = float(bbox.get("x1", 0) or 0)
        x2 = float(bbox.get("x2", 0) or 0)
        pieces.append(
            f'<g transform="translate({x1 + x2} 0) scale(-1 1)">'
        )

    # Bucket geometry by layer so we can z-order.
    by_layer = _bucket_by_layer(geometry, opt)

    layer_list = list(_LAYER_ORDER_TOPVIEW)
    if bottom_view:
        layer_list = list(reversed(layer_list))
    # Append any unknown layers we saw (so we still draw them, but at the
    # very top -- they're usually mech / overlay extras).
    for lay in by_layer.keys():
        if lay not in layer_list and lay != "_outline":
            layer_list.append(lay)

    requested = set(opt.layers) if opt.layers else None
    for lay in layer_list:
        if requested is not None and lay not in requested:
            continue
        bucket = by_layer.get(lay)
        if not bucket:
            continue
        pieces.append(_render_layer(lay, bucket, opt))

    # Board outline drawn on top of layers, beneath designators / drills.
    pieces.append(_render_outline(geometry.get("outline") or [], opt))

    # Per-component designator labels (virtual "Designators" pseudo-layer).
    if opt.show_designators:
        pieces.append(_render_designators(geometry.get("components") or [], opt))

    # Drills go last (white holes punch through everything).
    if opt.show_drills:
        pieces.append(_render_drills(geometry, opt))

    if bottom_view:
        pieces.append("</g>")  # close X-mirror wrapper
    if opt.flip_y:
        pieces.append("</g>")  # close outer Y-flip wrapper

    # Interactive legend goes OUTSIDE the Y-flip wrapper so its HTML
    # checkboxes render upright. Modern browsers handle this fine;
    # static viewers ignore foreignObject + script and the SVG still
    # shows every layer.
    if opt.interactive_legend:
        pieces.append(_render_layer_legend(geometry, opt))

    pieces.append("</svg>")
    return "".join(p for p in pieces if p)


# ---------------------------------------------------------------------------
# viewBox + root
# ---------------------------------------------------------------------------


def _compute_viewbox_and_root(geometry: dict[str, Any],
                              opt: PcbRenderOptions) -> list[str]:
    bbox = geometry.get("bbox") or {}
    x1 = float(bbox.get("x1", 0))
    y1 = float(bbox.get("y1", 0))
    x2 = float(bbox.get("x2", 1000))
    y2 = float(bbox.get("y2", 1000))
    if x1 == x2 or y1 == y2:
        x1, y1, x2, y2 = -500, -500, 500, 500
    x1 -= opt.margin_mils
    y1 -= opt.margin_mils
    x2 += opt.margin_mils
    y2 += opt.margin_mils
    w = x2 - x1
    h = y2 - y1
    if opt.flip_y:
        view = f"{x1} {-y2} {w} {h}"
    else:
        view = f"{x1} {y1} {w} {h}"
    pieces: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]
    if opt.flip_y:
        pieces.append('<g transform="scale(1,-1)">')
    return pieces


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


_color_fallbacks_logged: set[str] = set()


def _color_for(layer: str, opt: PcbRenderOptions) -> str:
    color = opt.layer_colors.get(layer)
    if color:
        return color
    # Altium's real layer colors should always arrive in layer_colors; a
    # fallback means the geometry payload lacked this layer. Log once per
    # layer so a silently-miscolored render is traceable.
    if layer not in _color_fallbacks_logged:
        _color_fallbacks_logged.add(layer)
        logging.getLogger("eda_agent.render").debug(
            "no Altium color for layer %r; using default palette", layer)
    return _DEFAULT_COLORS.get(layer, "#7f8c8d")


def _embedded_style(opt: PcbRenderOptions) -> str:
    rules = [
        # Normal alpha compositing -- Altium's default 2D view draws layers
        # opaque in stack order (no additive/screen blend). Screen blend was
        # washing solid red copper out to pink.
        f"  .layer-other {{ opacity: {opt.fade_others_opacity}; }}",
        "  .outline { fill: none; }",
        "  .drill { fill: #000; }",
        "  .pad-num { font-family: 'JetBrains Mono', monospace; "
        "fill: rgba(255,255,255,0.85); pointer-events: none; }",
        "  .pcb-text { font-family: 'JetBrains Mono', monospace; "
        "pointer-events: none; }",
        "  .track:hover, .arc:hover, .pad:hover, .via:hover { "
        "filter: brightness(1.6); cursor: pointer; }",
    ]
    return "<style>" + "\n".join(rules) + "</style>"


def _render_background(geometry: dict[str, Any],
                       opt: PcbRenderOptions) -> str:
    bbox = geometry.get("bbox") or {}
    x1 = float(bbox.get("x1", 0)) - opt.margin_mils
    y1 = float(bbox.get("y1", 0)) - opt.margin_mils
    x2 = float(bbox.get("x2", 0)) + opt.margin_mils
    y2 = float(bbox.get("y2", 0)) + opt.margin_mils
    return (
        f'<rect class="bg" x="{x1}" y="{y1}" '
        f'width="{x2 - x1}" height="{y2 - y1}" '
        f'fill="{opt.background}"/>'
    )


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


def _bucket_by_layer(geometry: dict[str, Any],
                     opt: PcbRenderOptions
                     ) -> dict[str, dict[str, list[dict[str, Any]]]]:
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def _push(layer: str, kind: str, item: dict[str, Any]) -> None:
        out.setdefault(layer, {}).setdefault(kind, []).append(item)

    # Regions go in first so they paint BENEATH tracks/arcs on the same
    # layer -- that's what gives the rendered board the proper "copper
    # fill" look instead of bare traces over background.
    for r in geometry.get("regions") or []:
        _push(r.get("layer", "Unknown"), "regions", r)
    for t in geometry.get("tracks") or []:
        _push(t.get("layer", "Unknown"), "tracks", t)
    for a in geometry.get("arcs") or []:
        _push(a.get("layer", "Unknown"), "arcs", a)
    for p in geometry.get("pads") or []:
        _push(p.get("layer", "Unknown"), "pads", p)
    if opt.show_texts:
        for tx in geometry.get("texts") or []:
            _push(tx.get("layer", "Unknown"), "texts", tx)
    # Vias span layers -- bucket them under "MultiLayer" so they always render.
    for v in geometry.get("vias") or []:
        _push("MultiLayer", "vias", v)
    return out


# ---------------------------------------------------------------------------
# Per-layer rendering
# ---------------------------------------------------------------------------


def _layer_opacity(layer: str, opt: PcbRenderOptions) -> float:
    """Per-layer opacity. Combines three factors multiplicatively:
        1. Base opacity for auxiliary layers (mask / paste / mech /
           assembly / silk) so they stay subtle vs. copper.
        2. View-side fade: the side the user is looking AT renders at
           full side-opacity, the other side at other_side_opacity.
        3. Internal copper always fades.
    """
    base = _LAYER_BASE_OPACITY.get(layer, 1.0)
    if layer.startswith(_INNER_LAYERS_PREFIX):
        return base * opt.inner_layer_opacity
    side = (opt.view_side or "top").lower()
    if side == "bottom":
        if layer in _TOP_SIDE_LAYERS:
            return base * opt.other_side_opacity
        if layer in _BOTTOM_SIDE_LAYERS:
            return base
    else:  # top view (default)
        if layer in _BOTTOM_SIDE_LAYERS:
            return base * opt.other_side_opacity
        if layer in _TOP_SIDE_LAYERS:
            return base
    return base


def _render_layer(layer: str, bucket: dict[str, list[dict[str, Any]]],
                  opt: PcbRenderOptions) -> str:
    color = _color_for(layer, opt)
    is_known = (layer in _LAYER_ORDER_TOPVIEW or layer == "MultiLayer")
    klass = "layer" if is_known else "layer layer-other"
    opacity = _layer_opacity(layer, opt)
    op_attr = f' opacity="{opacity:.2f}"' if opacity < 1.0 else ""
    # Start hidden if Altium isn't currently displaying this layer, so the
    # default view matches the bench. The legend checkbox can toggle it
    # back on (it flips this same group's display).
    hidden = (opt.altium_visible is not None
              and layer in _LAYER_ORDER_TOPVIEW
              and layer not in opt.altium_visible)
    style_attr = ' style="display:none"' if hidden else ""
    out: list[str] = [
        f'<g class="{klass}" data-layer="{_xml(layer)}"{op_attr}{style_attr}>'
    ]

    # Regions go first so tracks / pads / arcs paint over them.
    for r in bucket.get("regions", []):
        pts = " ".join(f"{p[0]},{p[1]}" for p in (r.get("pts") or []))
        if not pts:
            continue
        net = r.get("net", "")
        if layer == "MultiLayer" and not net:
            # Not copper: the board's own region (a shaped board carries
            # one the size of the outline). Filled, it was drawn over every
            # layer and a routed board rendered as vias on a grey disc.
            out.append(
                f'<polygon class="region region-outline" data-net="" '
                f'points="{pts}" fill="none" stroke="{color}" stroke-width="4"/>'
            )
            continue
        out.append(
            f'<polygon class="region" data-net="{_xml(net)}" '
            f'points="{pts}" fill="{color}" stroke="none"/>'
        )

    for t in bucket.get("tracks", []):
        net = t.get("net", "")
        out.append(
            f'<line class="track" data-net="{_xml(net)}" '
            f'x1="{t.get("x1", 0)}" y1="{t.get("y1", 0)}" '
            f'x2="{t.get("x2", 0)}" y2="{t.get("y2", 0)}" '
            f'stroke="{color}" stroke-width="{t.get("width", 6)}" '
            f'stroke-linecap="round"/>'
        )

    for a in bucket.get("arcs", []):
        net = a.get("net", "")
        path = _arc_path(a)
        if not path:
            continue
        out.append(
            f'<path class="arc" data-net="{_xml(net)}" '
            f'd="{path}" stroke="{color}" fill="none" '
            f'stroke-width="{a.get("width", 6)}" stroke-linecap="round"/>'
        )

    for p in bucket.get("pads", []):
        out.append(_render_pad(p, color, opt))

    for v in bucket.get("vias", []):
        out.append(_render_via(v, opt))

    for tx in bucket.get("texts", []):
        out.append(_render_text(tx, color, opt))

    out.append("</g>")
    return "".join(out)


def _render_pad(p: dict[str, Any], color: str, opt: PcbRenderOptions) -> str:
    x = float(p.get("x", 0))
    y = float(p.get("y", 0))
    xs = float(p.get("x_size", 0))
    ys = float(p.get("y_size", 0))
    rot = float(p.get("rotation", 0) or 0)
    shape = (p.get("shape") or "Round").lower()
    name = p.get("name", "") or ""
    net = p.get("net", "") or ""
    transform = f' transform="rotate({rot} {x} {y})"' if rot else ""

    if shape.startswith("round") and "rect" not in shape:
        # Round pad (XSize is the diameter).
        body = (
            f'<ellipse cx="{x}" cy="{y}" '
            f'rx="{xs / 2}" ry="{ys / 2}" fill="{color}"/>'
        )
    elif shape.startswith("oct"):
        # Regular octagon inscribed in the X/YSize rectangle.
        body = _octagon_path(x, y, xs, ys, color)
    elif shape.startswith("roundedrect"):
        rxy = min(xs, ys) * 0.2
        body = (
            f'<rect x="{x - xs / 2}" y="{y - ys / 2}" '
            f'width="{xs}" height="{ys}" rx="{rxy}" ry="{rxy}" '
            f'fill="{color}"/>'
        )
    else:  # Rectangular
        body = (
            f'<rect x="{x - xs / 2}" y="{y - ys / 2}" '
            f'width="{xs}" height="{ys}" fill="{color}"/>'
        )

    comp = p.get("comp", "") or ""
    # data-designator on the pad <g> lets the dashboard's existing
    # click handler open the owning component's drawer on a PCB click.
    # Free pads (fiducials, mounting holes) have comp="" and stay
    # net-only -- they shouldn't open a drawer because no component
    # owns them.
    des_attr = f' data-designator="{_xml(comp)}"' if comp else ""
    out = (
        f'<g class="pad" data-net="{_xml(net)}" data-shape="{_xml(shape)}" '
        f'data-name="{_xml(name)}"{des_attr}{transform}>'
        + body
    )
    # Pad number label (only if pad is big enough to read).
    if name and min(xs, ys) >= opt.pad_label_min_mils:
        font = max(8.0, min(xs, ys) * 0.35)
        out += _flippable_text(
            x, y,
            f'class="pad-num" font-size="{font}" '
            f'text-anchor="middle" dominant-baseline="middle"',
            name, opt,
        )
    out += "</g>"
    return out


def _render_via(v: dict[str, Any], opt: PcbRenderOptions) -> str:
    x = float(v.get("x", 0))
    y = float(v.get("y", 0))
    size = float(v.get("size", 0))
    net = v.get("net", "") or ""
    return (
        f'<g class="via" data-net="{_xml(net)}">'
        f'<circle cx="{x}" cy="{y}" r="{size / 2}" '
        f'fill="{_color_for("ViaPlated", opt)}"/></g>'
    )


def _render_text(tx: dict[str, Any], color: str, opt: PcbRenderOptions) -> str:
    text = tx.get("text", "") or ""
    if not text:
        return ""
    # Skip text that's hidden in the source design (IsHidden=True on
    # IPCB_Text). The Pascal emitter sends "hidden": true on those.
    if not opt.show_hidden_text and tx.get("hidden"):
        return ""
    x = float(tx.get("x", 0))
    y = float(tx.get("y", 0))
    size = float(tx.get("size", 60) or 60)
    rot = float(tx.get("rotation", 0) or 0)
    attrs = (
        f'class="pcb-text" font-size="{size}" '
        f'text-anchor="start" dominant-baseline="auto" '
        f'fill="{color}"'
    )
    if opt.flip_y:
        # Counter-flip Y plus optional in-plane rotation in Altium-native
        # space (where +ccw is up). The outer scale(1,-1) inverts visual
        # rotation, so emit -rot for the local transform.
        return (
            f'<g transform="translate({x},{y}) scale(1,-1) rotate({-rot})">'
            f'<text {attrs} x="0" y="0">{_xml(text)}</text></g>'
        )
    return (
        f'<text {attrs} x="{x}" y="{y}" '
        f'transform="rotate({rot} {x} {y})">{_xml(text)}</text>'
    )


def _outline_arc(seg: dict[str, Any], start: tuple[float, float],
                 end: tuple[float, float]) -> list[str]:
    """SVG arc commands for one outline arc segment, from start to end.

    Altium's arc runs counter-clockwise from angle1 to angle2 and does
    not say which end the segment starts on, so the direction comes from
    the end nearer ``start``: angle1 means counter-clockwise (sweep-flag
    1, which is increasing angle in the path's own coordinates, flipped
    or not), angle2 means clockwise. A full circle has the same start
    and end, which SVG draws as nothing, so it goes in two halves.
    """
    r = float(seg.get("radius", 0) or 0)
    a1 = float(seg.get("angle1", 0) or 0)
    a2 = float(seg.get("angle2", 0) or 0)
    span = a2 - a1
    full = abs(span) >= 359.999
    sweep_deg = 360.0 if full else span % 360.0
    sweep = 1
    if "cx" in seg and "cy" in seg:
        cx, cy = float(seg["cx"]), float(seg["cy"])
        p1 = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
        p2 = (cx + r * math.cos(math.radians(a2)), cy + r * math.sin(math.radians(a2)))
        if not full and math.dist(start, p2) < math.dist(start, p1):
            sweep = 0
        if full or math.dist(start, end) < 1e-6:
            far = (2 * cx - start[0], 2 * cy - start[1])
            return [f"A {r} {r} 0 0 {sweep} {far[0]} {far[1]}",
                    f"A {r} {r} 0 0 {sweep} {end[0]} {end[1]}"]
    large = 1 if sweep_deg > 180.0 else 0
    return [f"A {r} {r} 0 {large} {sweep} {end[0]} {end[1]}"]


def _render_outline(outline: Iterable[dict[str, Any]],
                    opt: PcbRenderOptions) -> str:
    """Render the board outline as a path -- arc segments emit real SVG
    arc commands so curved board shapes (rounded corners, mounting
    cutouts) draw correctly instead of being flattened to chord lines.

    Segment i runs FROM its vertex to the next segment's vertex, the
    last one back to the first (layout.read_altium reads it the same
    way). Read the other way round, the first segment's arc was never
    drawn and the closing Z joined the last vertex to the first with a
    straight line: one quadrant of a round board came out as a diagonal.
    """
    segs = list(outline)
    if not segs:
        return ""
    color = _color_for("Outline", opt)

    pts = [(float(s.get("x", 0)), float(s.get("y", 0))) for s in segs]
    parts: list[str] = [f"M {pts[0][0]} {pts[0][1]}"]
    for i, seg in enumerate(segs):
        sx, sy = pts[i]
        ex, ey = pts[(i + 1) % len(segs)]
        if seg.get("kind") == "arc":
            parts.extend(_outline_arc(seg, (sx, sy), (ex, ey)))
        elif i + 1 < len(segs):
            parts.append(f"L {ex} {ey}")
    parts.append("Z")
    d = " ".join(parts)
    return (
        f'<g class="layer outline" data-layer="Outline">'
        f'<path class="outline" d="{d}" '
        f'stroke="{color}" stroke-width="{opt.outline_stroke_mils}" '
        f'fill="none" stroke-linejoin="round"/></g>'
    )


def _render_designators(components: Iterable[dict[str, Any]],
                        opt: PcbRenderOptions) -> str:
    """Render per-component designator labels on a virtual 'Designators'
    pseudo-layer. The actual silkscreen art lives in each footprint's
    overlay primitives (already rendered); this adds the identifying
    text so a reviewer can locate every part by name without zooming
    into the overlay.
    """
    out: list[str] = ['<g class="layer designators" data-layer="Designators">']
    color = _color_for("TopOverlay", opt)
    for c in components:
        des = c.get("des", "") or ""
        if not des:
            continue
        # Respect Component.NameOn -- if the designator is hidden in the
        # source design, don't paint it here either. The Pascal emitter
        # sends "name_on": false on components where NameOn = False.
        # Default to True when the field is missing so an old geometry
        # payload still shows designators.
        name_on = c.get("name_on")
        if name_on is False and not opt.show_hidden_text:
            continue
        x = float(c.get("x", 0))
        y = float(c.get("y", 0))
        rot = float(c.get("rotation", 0) or 0)
        # Small text floating above the component origin. Size scales
        # mildly so a dense top view stays readable; v1.1 of v1.1 could
        # tune by component bbox.
        attrs = (
            f'class="designator" font-size="40" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'fill="{color}" font-family="JetBrains Mono, monospace" '
            f'font-weight="700" pointer-events="none"'
        )
        # Keep the label readable: fold the component rotation into
        # (-90, 90] so a part placed at 180 deg doesn't render its
        # designator upside-down. Altium keeps designators upright too.
        rr = ((rot + 90.0) % 180.0) - 90.0
        bottom = (opt.view_side or "top").lower() == "bottom"
        if opt.flip_y:
            # scale(1,-1) cancels the outer Y-flip; in bottom view
            # scale(-1,-1) ALSO cancels the X-mirror wrapper so labels read
            # forwards instead of mirrored/upside-down.
            sx = -1 if bottom else 1
            disp_rot = rr if bottom else -rr
            out.append(
                f'<g data-designator="{_xml(des)}" '
                f'transform="translate({x},{y}) scale({sx},-1) '
                f'rotate({disp_rot})">'
                f'<text {attrs} x="0" y="0">{_xml(des)}</text></g>'
            )
        else:
            out.append(
                f'<g data-designator="{_xml(des)}">'
                f'<text {attrs} x="{x}" y="{y}" '
                f'transform="rotate({rr} {x} {y})">{_xml(des)}</text></g>'
            )
    out.append("</g>")
    return "".join(out)


def _render_drills(geometry: dict[str, Any], opt: PcbRenderOptions) -> str:
    """Punch black holes through pads + vias. Pads honour their
    HoleType (Round / Square / Slot); vias are always round.
    """
    out: list[str] = ['<g class="drills">']
    for p in geometry.get("pads") or []:
        h = float(p.get("hole_size", 0) or 0)
        if h <= 0:
            continue
        x = float(p.get("x", 0))
        y = float(p.get("y", 0))
        htype = (p.get("hole_type") or "Round").lower()
        if htype == "square":
            side = h
            out.append(
                f'<rect class="drill" x="{x - side / 2}" y="{y - side / 2}" '
                f'width="{side}" height="{side}"/>'
            )
        elif htype == "slot":
            # Slot is rounded-end rectangle: length = hole_size,
            # width = hole_width, rotated by hole_rotation about (x,y).
            length = h
            width = float(p.get("hole_width", h) or h)
            if width <= 0:
                width = h
            rot = float(p.get("hole_rotation", 0) or 0)
            rx = width / 2.0
            transform = f' transform="rotate({rot} {x} {y})"' if rot else ""
            out.append(
                f'<rect class="drill" x="{x - length / 2}" y="{y - width / 2}" '
                f'width="{length}" height="{width}" '
                f'rx="{rx}" ry="{rx}"{transform}/>'
            )
        else:
            out.append(
                f'<circle class="drill" cx="{x}" cy="{y}" r="{h / 2}"/>'
            )
    for v in geometry.get("vias") or []:
        h = float(v.get("hole_size", 0) or 0)
        if h <= 0:
            continue
        out.append(
            f'<circle class="drill" '
            f'cx="{v.get("x", 0)}" cy="{v.get("y", 0)}" r="{h / 2}"/>'
        )
    out.append("</g>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _arc_path(a: dict[str, Any]) -> str:
    cx = float(a.get("cx", 0))
    cy = float(a.get("cy", 0))
    r = float(a.get("r", 0) or 0)
    if r <= 0:
        return ""
    sa = float(a.get("start", 0) or 0)
    ea = float(a.get("end", 0) or 0)
    if ea < sa:
        ea += 360.0
    # Clamp oversized sweeps (start=10, end=400 -> 390 degrees): anything
    # past a full turn IS a full circle, not a spiral the renderer can draw.
    if ea - sa > 360.0:
        ea = sa + 360.0
    sweep = ea - sa
    full = abs(sweep - 360.0) < 0.01 or sweep <= 0.01
    sa_r = math.radians(sa)
    ea_r = math.radians(ea)
    sx = cx + r * math.cos(sa_r)
    sy = cy + r * math.sin(sa_r)
    ex = cx + r * math.cos(ea_r)
    ey = cy + r * math.sin(ea_r)
    if full:
        # Full circle as two halves so SVG renders it.
        return (
            f"M {cx + r} {cy} "
            f"A {r} {r} 0 1 1 {cx - r} {cy} "
            f"A {r} {r} 0 1 1 {cx + r} {cy}"
        )
    large = 1 if sweep > 180.0 else 0
    return f"M {sx} {sy} A {r} {r} 0 {large} 1 {ex} {ey}"


def _octagon_path(cx: float, cy: float, xs: float, ys: float,
                  color: str) -> str:
    # Octagon inscribed in the xs x ys rectangle; corners cut at 30% of
    # the half-size for a recognisable shape.
    hx = xs / 2.0
    hy = ys / 2.0
    cx_o = min(hx, hy) * 0.41
    pts = [
        (cx - hx + cx_o, cy - hy),
        (cx + hx - cx_o, cy - hy),
        (cx + hx,        cy - hy + cx_o),
        (cx + hx,        cy + hy - cx_o),
        (cx + hx - cx_o, cy + hy),
        (cx - hx + cx_o, cy + hy),
        (cx - hx,        cy + hy - cx_o),
        (cx - hx,        cy - hy + cx_o),
    ]
    pts_str = " ".join(f"{p[0]},{p[1]}" for p in pts)
    return f'<polygon points="{pts_str}" fill="{color}"/>'


def _flippable_text(x: float, y: float, attrs: str, text: Any,
                    opt: PcbRenderOptions) -> str:
    safe = _xml(text)
    if opt.flip_y:
        return (
            f'<g transform="translate({x},{y}) scale(1,-1)">'
            f'<text {attrs} x="0" y="0">{safe}</text></g>'
        )
    return f'<text {attrs} x="{x}" y="{y}">{safe}</text>'


def _render_layer_legend(geometry: dict[str, Any],
                         opt: PcbRenderOptions) -> str:
    """Embed an interactive layer-toggle legend.

    Built as a foreignObject containing HTML checkboxes (one per layer
    that actually appears in the geometry) + an inline <script> that
    wires up click->toggle on the matching ``<g data-layer="…">`` groups.
    In a static SVG viewer the foreignObject + script are ignored and
    every layer still renders -- the legend is a browser-only
    enhancement, not a load-bearing feature.
    """
    # Build the set of layers actually present so the legend doesn't
    # advertise empty toggles.
    layers: list[str] = []
    seen: set[str] = set()
    for kind in ("regions", "tracks", "arcs", "pads", "texts"):
        for o in geometry.get(kind) or []:
            lay = o.get("layer")
            if isinstance(lay, str) and lay and lay not in seen:
                seen.add(lay)
                layers.append(lay)
    # Always-present pseudo-layers we draw separately:
    for extra in ("MultiLayer", "Outline", "Designators"):
        if extra not in seen:
            layers.append(extra)
            seen.add(extra)
    # Order by the canonical layer z-order so the legend reads sensibly.
    ordered: list[str] = []
    for lay in (_LAYER_ORDER_TOPVIEW + ["MultiLayer", "Outline", "Designators"]):
        if lay in seen:
            ordered.append(lay)
    for lay in layers:
        if lay not in ordered:
            ordered.append(lay)

    bbox = geometry.get("bbox") or {}
    x1 = float(bbox.get("x1", 0)) - opt.margin_mils
    y2 = float(bbox.get("y2", 0)) + opt.margin_mils
    width = 2600
    height = max(160, 60 + len(ordered) * 80)

    def _checked(lay: str) -> str:
        # Mirror Altium's displayed-layer set: layers Altium hides start
        # unchecked (and their group starts display:none), so the legend
        # and the canvas agree on first paint. Pseudo-layers (Outline /
        # Designators / MultiLayer) and unknown layers default checked.
        if (opt.altium_visible is not None
                and lay in _LAYER_ORDER_TOPVIEW
                and lay not in opt.altium_visible):
            return ""
        return " checked"

    rows = "".join(
        f'<label style="display:block; padding:2px 4px; cursor:pointer; '
        f'color:#fff; font:600 22px monospace;">'
        f'<input type="checkbox" class="layer-cb" data-layer="{_xml(lay)}"'
        f'{_checked(lay)} style="margin-right:6px;">'
        f'<span style="display:inline-block; width:14px; height:14px; '
        f'background:{_color_for(lay, opt)}; '
        f'vertical-align:-2px; margin-right:6px;"></span>'
        f'{_xml(lay)}</label>'
        for lay in ordered
    )

    # foreignObject in the flipped frame would render upside-down, so
    # we put it OUTSIDE the wrapper at "natural" SVG coords. With Y
    # flipped, y2 is the visual top of the board; we offset upward.
    if opt.flip_y:
        fo_x = x1
        fo_y = -y2
    else:
        fo_x = x1
        fo_y = y2 - height

    return (
        f'<foreignObject class="legend-host" '
        f'x="{fo_x}" y="{fo_y}" width="{width}" height="{height}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" '
        f'style="background:rgba(0,0,0,0.7); border-radius:6px; '
        f'padding:8px 10px; box-shadow:0 4px 12px rgba(0,0,0,0.4);">'
        f'<div style="color:#fff; font:700 22px monospace; '
        f'margin-bottom:4px; opacity:0.85;">LAYERS</div>'
        f'{rows}'
        f'</div></foreignObject>'
        f'<script><![CDATA['
        f'document.querySelectorAll(".layer-cb").forEach(function(cb){{'
        f'cb.addEventListener("change",function(){{'
        f'var name=cb.getAttribute("data-layer");'
        f'document.querySelectorAll(\'g[data-layer="\'+name+\'"]\').forEach('
        f'function(g){{g.style.display=cb.checked?"":"none";}});'
        f'}});'
        f'}});'
        f']]></script>'
    )


def _xml(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)
