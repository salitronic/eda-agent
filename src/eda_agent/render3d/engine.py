# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Cameras, lights and the call into the path tracer.

The tracer is the optional eda-agent-render package. Without it a top
or bottom view is still drawn, flat-shaded straight from the board's
side picture; other views need the tracer and say so.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from PIL import Image

from .backdrops import (BACKDROPS, IMAGE_SUFFIXES, Backdrop, image_backdrop,
                        texture as backdrop_texture, vignette)
from .board import BoardScene, _lin

QUALITY = {"draft": (16, 3), "normal": (64, 4), "high": (256, 6)}
#: Named views: (azimuth, elevation) in degrees; azimuth 0 looks along +y.
VIEWS = {
    "top": (0.0, 90.0), "bottom": (0.0, -90.0), "iso": (-35.0, 38.0),
    "iso_back": (145.0, 38.0), "iso_bottom": (-35.0, -38.0),
    "front": (0.0, 12.0), "back": (180.0, 12.0), "left": (-90.0, 12.0), "right": (90.0, 12.0),
}
TRACER_HINT = "pip install eda-agent[render3d]"
STUDIO_GREY = (150, 152, 156)


def tracer():
    """The path tracer module, or None when it is not installed."""
    try:
        import eda_agent_render
    except ImportError:
        return None
    return eda_agent_render


@dataclass
class View:
    azimuth: float
    elevation: float
    zoom: float = 1.0
    #: Orthographic for straight top / bottom views, perspective otherwise.
    ortho: bool = False
    #: Depth of field: 0 all sharp, 1 a macro lens's at a close-up.
    depth_of_field: float = 0.15


def view_for(name: str, azimuth: Optional[float], elevation: Optional[float],
             zoom: float, depth_of_field: float = 0.15) -> View:
    az, el = VIEWS.get(name, VIEWS["iso"])
    if azimuth is not None:
        az = float(azimuth)
    if elevation is not None:
        el = max(-90.0, min(90.0, float(elevation)))
    return View(az, el, max(0.1, float(zoom)), ortho=abs(el) >= 89.9,
                depth_of_field=max(0.0, min(2.0, float(depth_of_field))))


def camera(scene: BoardScene, view: View, width: int, height: int) -> dict[str, Any]:
    w, h = scene.size_mm
    t = scene.thickness_mm
    centre = (0.0, 0.0, t / 2)
    az, el = math.radians(view.azimuth), math.radians(view.elevation)
    # From the target towards the camera.
    d = (-math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
    radius = 0.5 * math.sqrt(w * w + h * h + t * t)
    aspect = width / height
    if view.ortho:
        # Fit the board's footprint, whichever way the frame is longer.
        span = max(h, w / aspect) * 1.06 / view.zoom
        up = (0.0, 1.0, 0.0)
        dist = radius * 4 + 50
        pos = tuple(c + dist * k for c, k in zip(centre, d))
        return {"position": pos, "target": centre, "up": up, "ortho_height": span}
    fov = 30.0
    half = math.radians(fov / 2)
    fit = radius / math.sin(half) if aspect >= 1 else radius / math.sin(math.atan(math.tan(half) * aspect))
    # The sphere around the board over-fits; a board is flat.
    dist = fit * 0.64 / view.zoom
    pos = tuple(c + dist * k for c, k in zip(centre, d))
    up = (0.0, 0.0, 1.0) if abs(view.elevation) < 89 else (0.0, 1.0, 0.0)
    # A real lens: the blur grows as the camera comes closer to the board,
    # as a macro shot's does.
    aperture = 0.0
    if view.depth_of_field > 0:
        aperture = view.depth_of_field * 0.004 * radius * min(4.0, view.zoom) ** 1.5
    return {"position": pos, "target": centre, "up": up, "fov_deg": fov,
            "aperture": aperture, "focus": dist}


#: A product-photo set, relative to the camera: (right, towards the
#: camera, up), radiance, half-angle in degrees. A big key softbox high
#: on the left, a broad overhead scrim whose reflection the gloss of the
#: mask shows, a fill on the right and a rim light from behind.
STUDIO = [
    ((-0.55, 0.35, 1.0), (1.9, 1.88, 1.82), 13.0),
    ((0.0, 0.1, 1.0), (0.55, 0.55, 0.56), 32.0),
    ((0.75, 0.45, 0.45), (0.32, 0.33, 0.35), 22.0),
    ((0.7, -1.0, 1.0), (0.4, 0.4, 0.42), 9.0),
]


def lights(view: View) -> list:
    """The studio lights turned with the camera, on the side seen."""
    s = 1.0 if view.elevation >= 0 else -1.0
    az = math.radians(view.azimuth)
    # The camera's right and the direction from the board to the camera,
    # flat on the floor.
    right = (math.cos(az), -math.sin(az))
    back = (-math.sin(az), -math.cos(az))
    out = []
    for (r, b, u), radiance, angle in STUDIO:
        x = r * right[0] + b * back[0]
        y = r * right[1] + b * back[1]
        out.append(((x, y, u * s), radiance, angle))
    return out


def _floor(scene: BoardScene, materials: list, textures: list, mesh_arrays: dict,
           backdrop: Backdrop) -> dict:
    """The surface the board rests on, for its contact shadow."""
    w, h = scene.size_mm
    k = 4.0 * max(w, h)
    # Under whatever is lowest: a board with parts underneath rests on them.
    z = float(mesh_arrays["positions"][:, 2].min()) - 0.002
    pos = np.array([(-k, -k, z), (k, -k, z), (k, k, z), (-k, k, z)], "<f4")
    base = len(mesh_arrays["positions"])
    mat = {"base": backdrop.base, "roughness": backdrop.roughness, "coat": backdrop.coat}
    tex = backdrop_texture(backdrop, round(2 * k))
    if tex is not None:
        textures.append(tex)
        mat.update(texture=len(textures) - 1, bump=backdrop.bump)
    materials.append(mat)
    m = len(materials) - 1
    return {
        "positions": np.vstack([mesh_arrays["positions"], pos]),
        "normals": np.vstack([mesh_arrays["normals"], np.tile([[0, 0, 1]], (4, 1)).astype("<f4")]),
        "uvs": np.vstack([mesh_arrays["uvs"], np.array([(0, 0), (1, 0), (1, 1), (0, 1)], "<f4")]),
        "indices": np.vstack([mesh_arrays["indices"],
                              np.array([[base, base + 1, base + 2], [base, base + 2, base + 3]], "<u4")]),
        "material_ids": np.concatenate([mesh_arrays["material_ids"], np.array([m, m], "<u4")]),
    }


def _turned_over(arrays: dict, thick: float) -> dict:
    """The scene turned half a turn about the x axis, the board's bottom
    face up: y and z reverse, so nothing is mirrored."""
    out = dict(arrays)
    pos = arrays["positions"].copy()
    pos[:, 1] = -pos[:, 1]
    pos[:, 2] = thick - pos[:, 2]
    nrm = arrays["normals"].copy()
    nrm[:, 1:] = -nrm[:, 1:]
    out["positions"], out["normals"] = pos.astype("<f4"), nrm.astype("<f4")
    return out


def _join(a: dict, b: dict) -> dict:
    base = len(a["positions"])
    return {
        "positions": np.vstack([a["positions"], b["positions"]]),
        "normals": np.vstack([a["normals"], b["normals"]]),
        "uvs": np.vstack([a["uvs"], b["uvs"]]),
        "indices": np.vstack([a["indices"], b["indices"] + base]).astype("<u4"),
        "material_ids": np.concatenate([a["material_ids"], b["material_ids"]]),
    }


def background_settings(background: str, backdrop_width_mm: float = 300.0
                        ) -> tuple[dict[str, Any], Optional[Backdrop]]:
    """Tracer settings for a background, and the surface to lay, if any:
    a named backdrop, a photo's path, transparent or a colour."""
    raw = (background or "studio").strip().strip('"')
    if raw.lower().endswith(IMAGE_SUFFIXES):
        if not os.path.isfile(raw):
            raise ValueError(f"no backdrop image at {raw}")
        if backdrop_width_mm <= 0:
            raise ValueError("backdrop_width_mm, the width the photo shows, must be above 0")
        floor = image_backdrop(raw, backdrop_width_mm)
        zen, hor, gnd = floor.sky
        return {"background": 0, "sky_zenith": zen, "sky_horizon": hor, "sky_ground": gnd}, floor
    b = raw.lower()
    if b == "transparent":
        return {"background": 1}, None
    if b in BACKDROPS:
        zen, hor, gnd = BACKDROPS[b].sky
        return {"background": 0, "sky_zenith": zen, "sky_horizon": hor,
                "sky_ground": gnd}, BACKDROPS[b]
    if b.startswith("#") and len(b) == 7:
        rgb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
        return {"background": 2, "background_color": _lin(rgb)}, None
    raise ValueError(f"background is {', '.join(BACKDROPS)}, transparent, a colour like "
                     "#202428, or the path of a photo of a surface")


def render(scene: BoardScene, view: View, width: int, height: int, quality: str,
           background: str, seed: int = 1, denoise: bool = True,
           backdrop_width_mm: float = 300.0) -> tuple[Image.Image, dict[str, Any]]:
    """The picture, and how it was made."""
    if quality not in QUALITY:
        raise ValueError(f"quality is one of {', '.join(QUALITY)}")
    bg, floor = background_settings(background, backdrop_width_mm)
    if view.ortho and bg.get("background") == 0 and floor is not None and floor.name == "studio":
        # Straight down or up there is no floor or horizon to show.
        bg = {"background": 2, "background_color": _lin(STUDIO_GREY)}
    core = tracer()
    if core is None:
        if not view.ortho:
            raise RuntimeError(
                f"an angled view needs the path tracer: {TRACER_HINT}. "
                "Top and bottom views work without it.")
        return _flat(scene, view, width, height, background), {
            "renderer": "flat", "note": f"flat shading; {TRACER_HINT} for a raytraced picture"}
    spp, bounces = QUALITY[quality]
    arrays = scene.mesh.arrays()
    materials = [dict(m) for m in scene.materials]
    textures = list(scene.textures)
    if scene.parts is not None and len(scene.parts.indices):
        from .parts import place
        extra, extra_mats = place(scene.parts, scene.centre_mils, scene.thickness_mm,
                                  len(materials))
        arrays = _join(arrays, extra)
        materials += extra_mats
    if floor is not None and view.elevation < 0 and not view.ortho:
        # The other side is photographed as people do: the board turned
        # over (half a turn about x, not a mirror) and laid on the surface.
        arrays, view = _turned_over(arrays, scene.thickness_mm), View(
            180.0 - view.azimuth, -view.elevation, view.zoom, view.ortho, view.depth_of_field)
    if floor is not None and view.elevation > 0 and not (view.ortho and floor.name == "studio"):
        arrays = _floor(scene, materials, textures, arrays, floor)
    settings = {
        "width": int(width), "height": int(height), "spp": spp, "max_bounces": bounces,
        "exposure": 1.0, "seed": int(seed), "lights": lights(view), "denoise": bool(denoise),
        **bg,
    }
    img, w, h = core.render(
        arrays["positions"].tobytes(), arrays["indices"].tobytes(),
        arrays["material_ids"].tobytes(), materials, textures,
        camera(scene, view, width, height), settings,
        normals=arrays["normals"].tobytes(), uvs=arrays["uvs"].tobytes())
    picture = Image.frombytes("RGBA", (w, h), img)
    if floor is not None:
        picture = vignette(picture, floor.vignette)
    return picture, {
        "renderer": f"path tracer {getattr(core, '__version__', '?')}",
        "samples_per_pixel": spp, "bounces": bounces, "denoised": bool(denoise),
        "triangles": int(len(arrays["indices"]))}


def _flat(scene: BoardScene, view: View, width: int, height: int, background: str) -> Image.Image:
    """A top or bottom view straight from the side picture, lit flat."""
    side = 0 if view.elevation > 0 else 1
    tw, th, rgba, _ = scene.textures[side]
    pic = np.frombuffer(rgba, np.uint8).reshape(th, tw, 4)[::-1]
    if side == 1:
        pic = pic[:, ::-1]
    img = Image.fromarray(np.ascontiguousarray(pic), "RGBA")
    scale = min(width / tw, height / th) * view.zoom
    img = img.resize((max(1, int(tw * scale)), max(1, int(th * scale))), Image.LANCZOS)
    b = (background or "studio").lower()
    fill = (0, 0, 0, 0) if b == "transparent" else (
        tuple(int(b[i:i + 2], 16) for i in (1, 3, 5)) + (255,) if b.startswith("#") else STUDIO_GREY + (255,))
    canvas = Image.new("RGBA", (width, height), fill)
    if img.width > width or img.height > height:
        # Zoomed in: the middle of the board.
        left, top = max(0, (img.width - width) // 2), max(0, (img.height - height) // 2)
        img = img.crop((left, top, left + min(width, img.width), top + min(height, img.height)))
    canvas.alpha_composite(img, ((width - img.width) // 2, (height - img.height) // 2))
    return canvas
