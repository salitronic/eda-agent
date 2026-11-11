# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What the board rests on: a surface texture and the room around it.

studio: a seamless white sweep. charcoal: the dark grey fibre mat of a
photo bench, lit from above and falling off at the edges. desk: a
lacquered wood worktop. Each surface is a texture made here, with its
relief in the parameter map's alpha, as the board's is.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Backdrop:
    name: str
    #: Linear RGB of the room: overhead, at the horizon, below.
    sky: tuple
    #: Relief of the surface texture at full scale, mm.
    bump: float
    #: Darkening towards the frame's corners, 0 none.
    vignette: float
    #: Plain surface colour (linear) when it has no texture.
    base: tuple = (0.78, 0.78, 0.77)
    roughness: float = 0.9
    coat: float = 0.0


BACKDROPS = {
    "studio": Backdrop("studio", ((0.26, 0.27, 0.29), (0.32, 0.32, 0.32), (0.16, 0.16, 0.16)),
                       0.0, 0.0),
    "charcoal": Backdrop("charcoal", ((0.10, 0.10, 0.11), (0.07, 0.07, 0.07), (0.03, 0.03, 0.03)),
                         0.06, 0.35, roughness=0.92),
    "desk": Backdrop("desk", ((0.30, 0.29, 0.27), (0.24, 0.23, 0.21), (0.10, 0.09, 0.08)),
                     0.01, 0.15, roughness=0.5, coat=0.3),
}


def _soften(a: np.ndarray) -> np.ndarray:
    """A 3x3 binomial blur (Pillow does not filter float images)."""
    p = np.pad(a, 1, mode="wrap")
    rows = p[:-2] + 2 * p[1:-1] + p[2:]
    return (rows[:, :-2] + 2 * rows[:, 1:-1] + rows[:, 2:]) / 16.0


#: How many times finer than drawn a surface's features are laid.
GRAIN_SCALE = {"desk": 1.8}


def _noise(shape: tuple[int, int], cells: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    """Smooth value noise: random cells, resized up bicubically; unit spread."""
    small = rng.standard_normal((max(2, cells[0]), max(2, cells[1]))).astype(np.float32)
    big = np.asarray(Image.fromarray(small, "F").resize((shape[1], shape[0]), Image.BICUBIC))
    return big / (float(big.std()) or 1.0)


def _fbm(shape, cells, rng, octaves=4) -> np.ndarray:
    out = np.zeros(shape, np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        out += amp * _noise(shape, (cells[0] * 2 ** o, cells[1] * 2 ** o), rng)
        total += amp
        amp *= 0.5
    return out / total


def _charcoal(n: int, size_mm: float, rng) -> tuple[np.ndarray, np.ndarray]:
    """A needle-felt mat: short fibres lying every way, matted into clumps
    of uneven density, light and dark fibres mixed, never a repeat."""
    from PIL import ImageDraw

    px = n / size_mm
    canvas = Image.new("L", (n, n), 128)
    draw = ImageDraw.Draw(canvas)
    # Fibres: 0.6 to 3 mm, a few px wide at most, curling a little.
    count = int(n * n / 140)
    xs, ys = rng.uniform(0, n, count), rng.uniform(0, n, count)
    ang = rng.uniform(0, np.pi, count)
    length = rng.lognormal(np.log(1.4), 0.45, count) * px
    shade = np.clip(rng.normal(128, 38, count), 30, 230).astype(int)
    width = np.where(rng.uniform(size=count) < 0.15, 2, 1)
    bend = rng.normal(0, 0.5, count)
    for i in range(count):
        x0, y0, a0, half = xs[i], ys[i], ang[i], length[i] / 2
        mx, my = x0 + np.cos(a0 + bend[i]) * half * 0.2, y0 + np.sin(a0 + bend[i]) * half * 0.2
        draw.line([(x0 - np.cos(a0) * half, y0 - np.sin(a0) * half), (mx, my),
                   (x0 + np.cos(a0 + bend[i]) * half, y0 + np.sin(a0 + bend[i]) * half)],
                  fill=int(shade[i]), width=int(width[i]))
    fibres = (np.asarray(canvas, np.float32) - 128) / 38
    clumps = _fbm((n, n), (max(2, int(size_mm / 9)), max(2, int(size_mm / 9))), rng, 4)
    mottle = _fbm((n, n), (max(2, int(size_mm / 70)), max(2, int(size_mm / 55))), rng, 3)
    pits = np.clip(-_fbm((n, n), (max(2, int(size_mm / 3)),) * 2, rng, 2) - 1.2, 0, None)
    # Under the loose fibres, the dense felt itself: grain at a fibre's
    # width, its density varying with the clumps.
    grain = _soften(rng.standard_normal((n, n)).astype(np.float32))
    grain /= float(grain.std()) or 1.0
    density = 1.0 + 0.45 * clumps
    grey = 60 + 7.5 * grain * density + 5 * fibres + 6 * clumps + 4 * mottle - 9 * pits
    rgb = np.dstack([grey, grey, grey * 1.035])
    height = 128 + 35 * grain * density + 30 * fibres + 30 * clumps - 40 * pits
    return rgb, height


def _desk(n: int, size_mm: float, rng) -> tuple[np.ndarray, np.ndarray]:
    """An office worktop: oak-grain melamine in a grey beige. Its grain
    is many short, thin, dark pore dashes lying along the grain in loose
    rows, over soft lighter and darker drifts stretched the same way;
    the rows sway gently, as an oak board's do."""
    from PIL import ImageDraw

    px = n / size_mm
    cells = lambda along, across: (max(2, int(size_mm / across)), max(2, int(size_mm / along)))
    pores = Image.new("L", (n, n), 0)
    draw = ImageDraw.Draw(pores)
    # Rows of pores across the grain, unevenly spaced.
    y = 0.0
    while y < size_mm:
        y += rng.lognormal(np.log(0.9), 0.5)
        row_dark = rng.uniform(0.3, 1.0)
        x = rng.uniform(-30, 0)
        while x < size_mm:
            x += rng.lognormal(np.log(3.5), 0.9)            # the gap before a dash
            length = rng.lognormal(np.log(6.5), 0.75)
            if rng.uniform() < 0.6 * row_dark + 0.25:
                yy = (y + rng.normal(0, 0.12)) * px
                shade = int(np.clip(rng.normal(190, 45) * row_dark, 40, 255))
                width = int(rng.choice([1, 2, 2, 3]))
                draw.line([(x * px, yy), ((x + length) * px, yy + rng.normal(0, 0.15) * px)],
                          fill=shade, width=width)
            x += length
    dash = _soften(np.asarray(pores, np.float32) / 255.0)
    # Oak's rays: short pale flecks between the pores.
    rays = Image.new("L", (n, n), 0)
    draw = ImageDraw.Draw(rays)
    for _ in range(int(size_mm * size_mm / 10)):
        x, yy = rng.uniform(0, n), rng.uniform(0, n)
        length = rng.lognormal(np.log(2.5), 0.5) * px
        draw.line([(x, yy), (x + length, yy)], fill=int(rng.uniform(80, 255)),
                  width=int(rng.choice([2, 3, 4])))
    ray = _soften(_soften(np.asarray(rays, np.float32) / 255.0))
    # The grain sways: shift each column's rows by a slow wander.
    wander = (_fbm((n, n), cells(300, 80), rng, 3) * 6.0
              + _fbm((n, n), cells(60, 15), rng, 2) * 1.2) * px
    rows = np.clip((np.arange(n)[:, None] + wander).astype(np.int64), 0, n - 1)
    dash = dash[rows, np.arange(n)[None, :]]
    ray = ray[rows, np.arange(n)[None, :]]
    drift = _fbm((n, n), cells(70, 5), rng, 4)
    flecks = _fbm((n, n), cells(6, 1.2), rng, 2)
    fine = _soften(rng.standard_normal((n, n)).astype(np.float32))
    tone = 1.0 + 0.05 * drift + 0.025 * flecks + 0.012 * fine - 0.5 * dash + 0.05 * ray
    base = np.array([194, 176, 154], np.float32)
    rgb = base[None, None, :] * tone[..., None]
    rgb[..., 2] *= 1 - 0.08 * dash                          # pores are a warm grey-brown
    height = 128 + 6 * drift + 4 * fine - 60 * dash
    return rgb, height


@lru_cache(maxsize=4)
def texture(name: str, size_mm: float, n: int = 4096) -> Optional[tuple[int, int, bytes, bytes]]:
    """The surface as a texture (w, h, rgba, params), or None if plain."""
    b = BACKDROPS[name]
    make = {"charcoal": _charcoal, "desk": _desk}.get(name)
    if make is None:
        return None
    rng = np.random.default_rng(7)
    # The grain is drawn at its real size; a desk's pores are finer than
    # the felt's clumps, so it is laid out over a larger span per texel.
    rgb, height = make(n, size_mm * GRAIN_SCALE.get(name, 1.0), rng)
    rgba = np.dstack([np.clip(rgb, 0, 255), np.full((n, n), 255.0)]).astype(np.uint8)
    params = np.zeros((n, n, 4), np.uint8)
    params[..., 0] = round(b.roughness * 255)
    params[..., 2] = round(b.coat * 255)
    params[..., 3] = np.clip(height, 0, 255).astype(np.uint8)
    return n, n, rgba.tobytes(), params.tobytes()


def vignette(img: Image.Image, strength: float) -> Image.Image:
    """Light falling off towards the corners, as under one overhead light."""
    if strength <= 0:
        return img
    w, h = img.size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot((x - w / 2) / (w / 2), (y - h / 2) / (h / 2)) / np.sqrt(2)
    k = 1.0 - strength * np.clip(r, 0, 1) ** 2.2
    a = np.asarray(img).astype(np.float32)
    a[..., :3] *= k[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), img.mode)
