# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SVG -> PNG rasterization via headless Edge/Chromium.

Our renderers emit SVG, but a vision critique needs a raster the model
can actually look at. Windows ships Edge (Chromium) which can screenshot
a file headlessly -- the same method used to verify the PCB render's
colours. This module locates the browser, builds the screenshot command
(pure, unit-testable), and runs it with a graceful fallback: if no
browser is found or the shot fails, the caller still has the SVG.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..noconsole import no_window

_VIEWBOX_RE = re.compile(
    r'viewBox="\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*"'
)


def size_svg_for_raster(
    svg_text: str,
    target_width: int = 1600,
    max_height: int = 6000,
    min_dim: int = 200,
) -> tuple[str, int, int]:
    """Give a viewBox-only SVG explicit pixel dimensions for screenshotting.

    Our renderers emit a responsive ``<svg viewBox=...>`` with no
    intrinsic size, so a headless browser would draw it at the 300x150
    CSS default. This derives width/height from the viewBox aspect (so
    the raster fills the window) and injects them into the root tag.
    Returns ``(sized_svg, width_px, height_px)``.
    """
    m = _VIEWBOX_RE.search(svg_text)
    if m:
        vb_w = abs(float(m.group(3)))
        vb_h = abs(float(m.group(4)))
    else:
        vb_w = vb_h = 0.0
    width = int(max(min_dim, target_width))
    if vb_w > 0 and vb_h > 0:
        height = int(max(min_dim, min(max_height, round(target_width * vb_h / vb_w))))
    else:
        height = width
    # Inject width/height into the first <svg> tag only if not already present.
    sized = re.sub(
        r"<svg\b(?![^>]*\bwidth=)",
        f'<svg width="{width}" height="{height}"',
        svg_text,
        count=1,
    )
    return sized, width, height

# Common Windows install locations, then PATH lookups. Order matters:
# prefer Edge (always present on Win11), then Chrome/Chromium.
_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_PATH_NAMES = ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser")


def find_browser(explicit: Optional[str] = None) -> Optional[str]:
    """Return a usable Chromium-family executable path, or None."""
    if explicit and Path(explicit).exists():
        return explicit
    for cand in _EDGE_CANDIDATES:
        if Path(cand).exists():
            return cand
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def build_screenshot_cmd(
    browser: str,
    svg_path: str,
    png_path: str,
    width: int,
    height: int,
) -> list[str]:
    """Build the headless-screenshot argument list (pure / testable)."""
    uri = Path(svg_path).resolve().as_uri()
    return [
        browser,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={int(width)},{int(height)}",
        f"--screenshot={png_path}",
        uri,
    ]


def rasterize_svg(
    svg_path: str,
    png_path: Optional[str] = None,
    width: int = 1600,
    height: Optional[int] = None,
    browser_path: Optional[str] = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Rasterize an SVG file to PNG. Best-effort; never raises.

    Returns ``{"ok": True, "png_path": ...}`` on success, otherwise
    ``{"ok": False, "reason": ..., "png_path": None}`` so the caller can
    fall back to the SVG.
    """
    svg = Path(svg_path)
    if not svg.exists():
        return {"ok": False, "reason": f"svg not found: {svg_path}", "png_path": None}
    if png_path is None:
        png_path = str(svg.with_suffix(".png"))
    if height is None:
        height = width

    browser = find_browser(browser_path)
    if not browser:
        return {
            "ok": False,
            "reason": "no Edge/Chromium browser found for rasterization; "
            "open the SVG instead",
            "png_path": None,
        }

    cmd = build_screenshot_cmd(browser, str(svg.resolve()), png_path,
                               int(width), int(height))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              **no_window())
    except Exception as exc:  # subprocess timeout / OS error
        return {"ok": False, "reason": f"rasterize failed: {exc}", "png_path": None}

    if Path(png_path).exists():
        return {"ok": True, "png_path": png_path}
    stderr = proc.stderr or b""
    return {
        "ok": False,
        "reason": "browser produced no PNG",
        "png_path": None,
        "stderr": stderr[:400].decode("utf-8", "replace"),
    }
