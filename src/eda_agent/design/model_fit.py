# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which way round a 3D model sits on its footprint.

A linked STEP model reports its box and its height, which is enough to
centre it and not enough to say that its pin 1 is over pad 1 or that a
module's antenna end is where the footprint has it. That was checked by
opening the 3D view and looking.

A model's terminals are flat faces on its underside. Read from the STEP
file and placed as the body is placed (turned about Z, then moved), each
terminal either lies over a pad or does not. The same is tried for the
four right-angle turns, each centred on the pads, so a model that is
turned or off its place is named with the turn and the move that fit.

The STEP file is read by FreeCAD's ``freecadcmd``; nothing here reaches
an editor.
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..noconsole import no_window

#: A face this much taller than the model is flat for our purpose.
_FLAT = 0.02
#: How far above the model's lowest point a terminal's face may be, in mm.
_UNDERSIDE = 0.06
#: A flat underside face larger than this many times the largest pad is
#: the body or a shield, not a terminal.
_TERMINAL_AREA = 4.0

_FREECAD_SCRIPT = r'''
import json, sys
import FreeCAD  # noqa: F401
import Part

step, out = sys.argv[-2], sys.argv[-1]
shape = Part.Shape()
shape.read(step)
box = shape.BoundBox
tol = max(1e-3, (box.ZMax - box.ZMin) * %(flat)r)
faces = []
for face in shape.Faces:
    fb = face.BoundBox
    if fb.ZMax - fb.ZMin > tol:
        continue
    try:
        normal = face.normalAt(0.5, 0.5)
    except Exception:
        continue
    if normal.z > -0.99:
        continue
    faces.append({"z": (fb.ZMin + fb.ZMax) / 2, "area": face.Area,
                  "box": [fb.XMin, fb.YMin, fb.XMax, fb.YMax]})
json.dump({"box": [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax],
           "solids": len(shape.Solids), "down_faces": faces}, open(out, "w"))
''' % {"flat": _FLAT}


def step_facts(path: str | Path) -> dict[str, Any]:
    """``{box, solids, underside}`` of a STEP model, in its own millimetres.

    ``underside`` is every flat face that looks down and lies at the
    model's lowest point: ``{box: [x0, y0, x1, y1], area}``. Raises
    RuntimeError with the reason when the file cannot be read.
    """
    from .models3d import _freecadcmd

    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"no STEP file at {path}")
    exe = _freecadcmd()
    if not exe:
        raise RuntimeError(
            "reading a STEP model needs FreeCAD (freecadcmd was not found): "
            "install FreeCAD, or check the model in Altium's 3D view")
    with tempfile.TemporaryDirectory(prefix="eda_fit_") as tmp:
        script = Path(tmp) / "read_step.py"
        out = Path(tmp) / "facts.json"
        script.write_text(_FREECAD_SCRIPT, encoding="utf-8")
        proc = subprocess.run([exe, str(script), str(path), str(out)],
                              capture_output=True, text=True, timeout=300,
                              **no_window())
        if not out.is_file():
            raise RuntimeError("freecadcmd read no model from the STEP file: "
                               + (proc.stderr or proc.stdout)[-400:])
        raw = json.loads(out.read_text(encoding="utf-8"))
    return facts_from_faces(raw["box"], raw["down_faces"], raw.get("solids", 0))


def facts_from_faces(box: list[float], down_faces: list[dict[str, Any]],
                     solids: int = 0) -> dict[str, Any]:
    """The model's facts from its box and its flat faces that look down."""
    zmin = box[2]
    underside = [{"box": [round(v, 4) for v in f["box"]], "area": round(f["area"], 4)}
                 for f in down_faces if f["z"] - zmin <= _UNDERSIDE]
    return {"box": [round(v, 4) for v in box], "solids": solids, "underside": underside}


def _turned(x: float, y: float, degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def _placed_box(box: list[float], degrees: float, dx: float, dy: float) -> list[float]:
    corners = [_turned(x, y, degrees) for x in (box[0], box[2]) for y in (box[1], box[3])]
    xs = [c[0] + dx for c in corners]
    ys = [c[1] + dy for c in corners]
    return [min(xs), min(ys), max(xs), max(ys)]


def pad_box(pad: dict[str, Any]) -> list[float]:
    """A pad's box in the footprint's millimetres, its rotation applied."""
    x, y = float(pad["x"]), float(pad["y"])
    w, h = float(pad["x_size"]), float(pad["y_size"])
    half = _placed_box([-w / 2, -h / 2, w / 2, h / 2], float(pad.get("rotation", 0) or 0), 0, 0)
    return [x + half[0], y + half[1], x + half[2], y + half[3]]


def _centre(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _span_centre(boxes: list[list[float]]) -> tuple[float, float]:
    return ((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2,
            (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2)


def _match(pads: list[dict[str, Any]], pad_boxes: list[list[float]],
           terminals: list[list[float]], tolerance: float) -> tuple[list[str], list[int]]:
    """Pads with no terminal over them, and terminals over no pad."""
    centres = [_centre(t) for t in terminals]
    used: set[int] = set()
    bare: list[str] = []
    for pad, b in zip(pads, pad_boxes):
        hits = [i for i, (cx, cy) in enumerate(centres)
                if b[0] - tolerance <= cx <= b[2] + tolerance
                and b[1] - tolerance <= cy <= b[3] + tolerance]
        if hits:
            used.update(hits)
        else:
            bare.append(str(pad.get("designator", "")))
    return bare, [i for i in range(len(terminals)) if i not in used]


def check_fit(facts: dict[str, Any], pads: list[dict[str, Any]], *,
              rotation: float = 0.0, offset_x: float = 0.0, offset_y: float = 0.0,
              tolerance: float = 0.1,
              courtyard: Optional[list[float]] = None) -> dict[str, Any]:
    """Whether the model's terminals lie over the footprint's pads as placed.

    ``pads``: ``{designator, x, y, x_size, y_size, rotation?}`` in mm, in
    the footprint's coordinates. The model is turned by ``rotation``
    degrees about Z and then moved by the offsets, as its 3D body is.

    ``courtyard``: ``[x0, y0, x1, y1]`` in mm, the footprint's courtyard
    or body outline. A body that reaches past the pads on one side only (a
    module's antenna end, a connector's shell) is what tells two turns
    apart when the pads alone read the same: turned the wrong way it
    leaves the courtyard.

    Returns ``{ok, pads, terminals, pads_without_terminal,
    terminals_off_pads, body_box_mm, body_past_pads_mm, outside_courtyard_mm?,
    placed_terminals, other_faces, rotations, reason?}``.
    ``body_past_pads_mm`` is how far the body reaches past the pads on each
    side. ``rotations`` gives, for each right-angle turn centred on the
    pads, how many pads have a terminal over them and the offsets that
    centre it; when the placement asked about is not the one that fits,
    ``reason`` names the one that does.
    """
    pads = [p for p in pads if p]
    if not pads:
        return {"ok": False, "reason": "give the footprint's pads to check the model against"}
    pad_boxes = [pad_box(p) for p in pads]
    largest = max((b[2] - b[0]) * (b[3] - b[1]) for b in pad_boxes)
    small = [f for f in facts["underside"] if f["area"] <= largest * _TERMINAL_AREA]
    other = [f for f in facts["underside"] if f["area"] > largest * _TERMINAL_AREA]
    if not small:
        return {"ok": False, "pads": len(pads), "terminals": 0, "reason": (
            "the model has no flat terminal faces on its underside (a body "
            "drawn as one block): its turn cannot be read from its "
            "terminals. Compare the picture of the model with the footprint")}

    def placed(degrees: float, dx: float, dy: float) -> list[list[float]]:
        return [_placed_box(f["box"], degrees, dx, dy) for f in small]

    here = placed(rotation, offset_x, offset_y)
    bare, loose = _match(pads, pad_boxes, here, tolerance)
    pcx, pcy = _span_centre(pad_boxes)
    turns = []
    for extra in (0, 90, 180, 270):
        degrees = (rotation + extra) % 360
        free = placed(degrees, 0.0, 0.0)
        tcx, tcy = _span_centre(free)
        dx, dy = pcx - tcx, pcy - tcy
        b, l = _match(pads, pad_boxes, placed(degrees, dx, dy), tolerance)
        turns.append({"rotation": degrees, "offset_x": round(dx, 4), "offset_y": round(dy, 4),
                      "pads_with_terminal": len(pads) - len(b), "terminals_off_pads": len(l)})
    out: dict[str, Any] = {
        "ok": not bare and not loose,
        "pads": len(pads), "terminals": len(small),
        "pads_without_terminal": bare[:40], "terminals_off_pads": len(loose),
        "body_box_mm": [round(v, 4) for v in _placed_box(
            [facts["box"][0], facts["box"][1], facts["box"][3], facts["box"][4]],
            rotation, offset_x, offset_y)],
        "height": round(facts["box"][5] - facts["box"][2], 4),
        "placed_terminals": [[round(v, 4) for v in t] for t in here],
        "other_faces": [[round(v, 4) for v in _placed_box(f["box"], rotation, offset_x, offset_y)]
                        for f in other],
        "rotations": turns,
    }
    body = out["body_box_mm"]
    span = [min(b[0] for b in pad_boxes), min(b[1] for b in pad_boxes),
            max(b[2] for b in pad_boxes), max(b[3] for b in pad_boxes)]
    out["body_past_pads_mm"] = {"left": round(span[0] - body[0], 4), "bottom": round(span[1] - body[1], 4),
                             "right": round(body[2] - span[2], 4), "top": round(body[3] - span[3], 4)}
    outside: dict[str, float] = {}
    if courtyard is not None:
        if len(courtyard) != 4 or courtyard[0] >= courtyard[2] or courtyard[1] >= courtyard[3]:
            return {"ok": False, "reason": "courtyard is [x0, y0, x1, y1] in mm, x0 < x1 and y0 < y1"}
        for side, over in (("left", courtyard[0] - body[0]), ("bottom", courtyard[1] - body[1]),
                           ("right", body[2] - courtyard[2]), ("top", body[3] - courtyard[3])):
            if over > tolerance:
                outside[side] = round(over, 4)
        out["courtyard"] = [round(float(v), 4) for v in courtyard]
        if outside:
            out["outside_courtyard_mm"] = outside
    full = [t for t in turns if t["pads_with_terminal"] == len(pads) and not t["terminals_off_pads"]]
    if out["ok"] and outside:
        out["ok"] = False
        out["reason"] = (
            "every pad has a terminal over it, but the body leaves the courtyard: "
            + ", ".join(f"{v:g} mm at the {k}" for k, v in outside.items())
            + ". A model turned the wrong way round does this when its body reaches "
              "past the pads on one side: see rotations for the turns the pads allow")
    elif out["ok"]:
        same = [t["rotation"] for t in full if t["rotation"] != rotation % 360]
        if same:
            out["note"] = (
                "the pads read the same turned to " + ", ".join(f"{r:g}" for r in same)
                + " degrees: the terminals cannot tell those turns apart. "
                + ("The body stays inside the courtyard as placed."
                   if courtyard is not None else
                   "Give courtyard to check the body's own outline, and look at "
                   "pin 1 and any feature that has no pad (an antenna, a tab) on "
                   "the picture"))
    elif full:
        best = full[0]
        out["reason"] = (
            f"as placed, {len(pads) - len(bare)} of {len(pads)} pads have a terminal "
            f"over them. Turned to {best['rotation']:g} degrees with offsets "
            f"({best['offset_x']:g}, {best['offset_y']:g}) mm every pad has one")
    else:
        best = max(turns, key=lambda t: (t["pads_with_terminal"], -t["terminals_off_pads"]))
        out["reason"] = (
            f"as placed, {len(pads) - len(bare)} of {len(pads)} pads have a terminal "
            f"over them, and no right-angle turn puts one over every pad (the best, "
            f"{best['rotation']:g} degrees, covers {best['pads_with_terminal']}). The "
            "model may be of another package, mirrored, or drawn with terminals "
            "this check does not read")
    return out


def render_fit(result: dict[str, Any], pads: list[dict[str, Any]], png: str | Path,
               *, title: str = "") -> str:
    """The pads, the model's terminals and its body outline from above.

    Pads are outlined, terminals filled, pad 1 marked. Raises ImportError
    when matplotlib is absent."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    body = result.get("body_box_mm")
    if body:
        ax.add_patch(Rectangle((body[0], body[1]), body[2] - body[0], body[3] - body[1],
                               fill=False, edgecolor="#555555", linewidth=1.2, linestyle="--",
                               label="model body"))
    yard = result.get("courtyard")
    if yard:
        ax.add_patch(Rectangle((yard[0], yard[1]), yard[2] - yard[0], yard[3] - yard[1],
                               fill=False, edgecolor="#208040", linewidth=1.0,
                               label="courtyard"))
    for face in result.get("other_faces", []):
        ax.add_patch(Rectangle((face[0], face[1]), face[2] - face[0], face[3] - face[1],
                               fill=False, edgecolor="#999999", linewidth=0.6))
    first = True
    for t in result.get("placed_terminals", []):
        ax.add_patch(Rectangle((t[0], t[1]), t[2] - t[0], t[3] - t[1], facecolor="#f0a030",
                               edgecolor="none", alpha=0.75,
                               label="model terminal" if first else None))
        first = False
    bare = set(result.get("pads_without_terminal", []))
    first = True
    for pad in pads:
        b = pad_box(pad)
        name = str(pad.get("designator", ""))
        colour = "#d02020" if name in bare else "#1060c0"
        ax.add_patch(Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1], fill=False,
                               edgecolor=colour, linewidth=1.0,
                               label="pad" if first else None))
        first = False
        if name == "1":
            cx, cy = _centre(b)
            ax.plot([cx], [cy], marker="o", color="#d02020", markersize=5)
            ax.annotate("pad 1", (cx, cy), textcoords="offset points", xytext=(6, 6),
                        fontsize=8, color="#d02020")
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.margins(0.08)
    ax.grid(True, linewidth=0.3, alpha=0.5)
    ax.set_xlabel("mm, footprint coordinates, seen from above")
    verdict = "fits" if result.get("ok") else "DOES NOT FIT"
    ax.set_title(f"{title + ': ' if title else ''}{verdict} "
                 f"({result.get('pads', 0) - len(bare)} of {result.get('pads', 0)} pads under a terminal)",
                 fontsize=10)
    ax.legend(loc="upper right", fontsize=7)
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, bbox_inches="tight")
    plt.close(fig)
    return str(png)


def fit_report(step_path: str, pads: list[dict[str, Any]], *, rotation: float = 0.0,
               offset_x: float = 0.0, offset_y: float = 0.0, tolerance: float = 0.1,
               courtyard: Optional[list[float]] = None,
               png_path: Optional[str] = None) -> dict[str, Any]:
    """step_facts, check_fit and the picture in one reply."""
    try:
        facts = step_facts(step_path)
    except RuntimeError as e:
        return {"ok": False, "reason": str(e)}
    out = check_fit(facts, pads, rotation=rotation, offset_x=offset_x, offset_y=offset_y,
                    tolerance=tolerance, courtyard=courtyard)
    out["model_box_mm"] = facts["box"]
    if png_path and "placed_terminals" in out:
        try:
            out["png"] = render_fit(out, pads, png_path, title=Path(step_path).stem)
        except ImportError:
            out["png_error"] = "matplotlib is not installed (pip install eda-agent[render])"
        except Exception as e:  # a failed picture must not hide the check
            out["png_error"] = f"{type(e).__name__}: {e}"
    out.pop("placed_terminals", None)
    out.pop("other_faces", None)
    return out
