# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Simulations a review finding asks for, on the board's real copper.

A finding the closed forms cannot settle carries ``simulate``: the call
that answers it here, whose result file is the evidence the AI gives with
its answer. Two engines, both the user's own installs:

* ngspice, from parasitics measured on the copper: a current loop as the
  resistance and inductance of its routed path, driven by the edge the AI
  gives (the overshoot it rings to, L di/dt); a power pin's decoupling as
  each capacitor's model (the AI's, from its datasheet) behind the
  inductance of its mounting (the track from its pad to the pin over the
  plane, and the vias at its pads), swept for the impedance the pin sees.
* openEMS, from the geometry: the net's tracks and vias on the board's own
  stack, its reference net as sheets where it is a plane or a pour, lumped
  ports at its end pads, meshed from the highest frequency asked. An
  impedance run gives S11, S21 and the line impedance they imply; a
  crosstalk run puts ports at both ends of the victim and the aggressor and
  gives the near and far end coupling.

The parasitic formulas, stated so a result can be judged: a track over its
plane has the inductance per length of a microstrip in air, Z0(air)/c with
Z0 from the IPC-2141 closed form; a via, 5.08 h (ln(4h/d) + 1) nH with h
and d in inches (H. Johnson, High-Speed Digital Design, via inductance);
copper resistance from 1.72e-8 ohm m at 20 C. The plane's own spreading
inductance between pins is left out.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Optional

from .. import geom
from ..model import LayoutBoard

#: Copper resistivity at 20 C, ohm metre.
RHO_CU = 1.72e-8
MIL = 25.4e-6
C0 = 299_792_458.0


def stack_thickness(board: LayoutBoard) -> float:
    """The board's thickness in mils from its stack, or 62 when the stack
    is not read (the commonest board, said in the result)."""
    stack = board.meta.get("stack") or []
    t = sum(l.get("thickness", 0.0) for l in stack)
    return t if t > 0 else 62.0


def via_nh(height_mils: float, drill_mils: float) -> float:
    """A via's inductance, nH (H. Johnson)."""
    h, d = height_mils / 1000.0, max(drill_mils, 1.0) / 1000.0
    return 5.08 * h * (math.log(4 * h / d) + 1)


def track_nh(length_mils: float, width_mils: float, h_mils: float, t_mils: float = 1.4) -> float:
    """A track's inductance over its plane ``h_mils`` below, nH."""
    x = 5.98 * h_mils / (0.8 * width_mils + t_mils)
    if x <= 1.0:
        return 0.0
    z_air = 87.0 / math.sqrt(1.0 + 1.41) * math.log(x)
    return z_air / C0 * length_mils * MIL * 1e9


def track_ohm(length_mils: float, width_mils: float, t_mils: float = 1.4) -> float:
    return RHO_CU * length_mils * MIL / (max(width_mils, 0.1) * MIL * max(t_mils, 0.1) * MIL)


def _h_to_plane(board: LayoutBoard, layer: str) -> float:
    from .impedance import geometry
    g = geometry(board).get(layer)
    if g is None:
        return 4.0
    return g["h"] if g["geometry"] == "microstrip" else g["h"] / 2


def _copper_t(board: LayoutBoard, layer: str) -> float:
    return next((l.copper_mils for l in board.layers if l.name == layer), 1.4) or 1.4


def _folder(board: LayoutBoard, what: str, name: str) -> Path:
    from ...config import get_config
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{Path(board.name).stem}_{what}_{name}")[:80]
    out = Path(get_config().workspace_dir) / "route_sims" / safe
    out.mkdir(parents=True, exist_ok=True)
    return out


# --- ngspice: a current loop --------------------------------------------------------

def loop_deck(board: LayoutBoard, brief, name: str, drive: dict) -> dict[str, Any]:
    """The loop ``name`` of the brief as R and L from its routed copper,
    driven by ``drive`` {i (A), rise_ns, source}: the deck and what went in."""
    from . import review
    lp = next((x for x in brief.loops if x.name == name), None)
    if lp is None:
        return {"error": f"no loop {name!r} in the brief"}
    nets = {p.net for p in board.pads if p.key in lp.members and p.net}
    got = [m for m in review.loop_measures(board, board, brief, nets) if m["name"] == name]
    if not got:
        return {"error": f"loop {name} could not be measured on the copper"}
    m = got[0]
    widths = sorted(t.width for t in board.tracks if t.net in nets and not t.comp)
    w = widths[len(widths) // 2] if widths else 10.0
    layer = next((t.layer for t in board.tracks if t.net in nets), board.copper_layers()[0])
    length = m.get("length") or (4 * math.sqrt(m.get("area", 0.0)))
    L = m.get("inductance_nh_est") or 0.0
    R = track_ohm(length, w, _copper_t(board, layer))
    i, rise = float(drive["i"]), float(drive["rise_ns"])
    deck = "\n".join([
        f"* loop {name}: {m.get('area', 0):.0f} sq mil, {length:.0f} mil of copper",
        f"I1 0 n1 PULSE(0 {i:g} 0 {rise:g}n {rise:g}n {20 * rise:g}n {60 * rise:g}n)",
        f"R1 n1 n2 {R:.6g}",
        f"L1 n2 0 {L:.6g}n",
        f".tran {rise / 100:g}n {40 * rise:g}n",
        ".control", "run", "meas tran vpk MAX v(n1)", "meas tran vmin MIN v(n1)",
        "echo \"$&vpk $&vmin\" > peak.txt", ".endc", ".end", ""])
    return {"deck": deck, "inputs": {"area_sq_mils": m.get("area"), "length_mils": length,
                                     "inductance_nh": L, "resistance_ohm": round(R, 6),
                                     "width_mils": w, "drive": drive},
            "outputs": ("peak.txt",)}


# --- ngspice: decoupling at a power pin -----------------------------------------------

def pdn_deck(board: LayoutBoard, brief, name: str, models: dict, band: dict) -> dict[str, Any]:
    """The pdn rule ``name``: each rail at its IC as the capacitors that
    serve it (``models``: ref -> {c_uf, esr_ohm, esl_nh, source}) behind
    their mounting inductance, swept from ``band`` f_start to f_stop (Hz)."""
    from .integrity import copper_distance, decaps_of
    r = next((x for x in brief.pdn if x.name == name), None)
    if r is None:
        return {"error": f"no pdn rule {name!r} in the brief"}
    caps = decaps_of(board, r)
    missing = [c for c in caps if c not in models]
    if missing:
        return {"error": f"give each capacitor's model (c_uf, esr_ohm, esl_nh, source from its "
                         f"datasheet): {missing}", "decaps": sorted(caps)}
    thick = stack_thickness(board)
    pins = [p for p in board.pads if p.comp == r.part and p.net in r.rails]
    lines = [f"* pdn {name}: {r.part}"]
    probes, inputs = [], {}
    for k, rail in enumerate(sorted({p.net for p in pins})):
        node = f"r{k}"
        lines.append(f"I{k} 0 {node} AC 1")
        probes.append((rail, node))
        rp = [p for p in pins if p.net == rail]
        for c, ps in caps.items():
            pad = next((p for p in ps if p.net == rail), None)
            ret = next((p for p in ps if p.net != rail), None)
            if pad is None:
                continue
            near = min(rp, key=lambda p: math.hypot(p.x - pad.x, p.y - pad.y))
            along = copper_distance(board, rail, near, pad)
            dist = along if along is not None else math.hypot(near.x - pad.x, near.y - pad.y)
            layer = pad.copper[0].layer if pad.copper else board.copper_layers()[0]
            w = min((t.width for t in board.tracks if t.net == rail and t.layer == layer
                     and not t.comp), default=max((c.w for c in pad.copper), default=10.0))
            lt = track_nh(dist, w, _h_to_plane(board, layer), _copper_t(board, layer))
            reach = r.via_reach or 50.0
            nv = 0
            if ret is not None:
                nv = sum(1 for v in board.vias if v.net == ret.net
                         and math.hypot(v.x - ret.x, v.y - ret.y) <= reach)
            drill = min((v.hole for v in board.vias if v.hole > 0), default=10.0)
            lv = via_nh(thick, drill) / max(nv, 1)
            mdl = models[c]
            esl = float(mdl.get("esl_nh", 0.0))
            ltot = lt + lv + esl
            cf = float(mdl["c_uf"]) * 1e-6
            esr = max(float(mdl.get("esr_ohm", 0.001)), 1e-6)
            lines += [f"R{c}_{k} {node} {c}_{k}a {esr:.6g}", f"L{c}_{k} {c}_{k}a {c}_{k}b {ltot:.6g}n",
                      f"C{c}_{k} {c}_{k}b 0 {cf:.6g}"]
            inputs[f"{c}:{rail}"] = {"mount_track_mils": round(dist, 1),
                                     "along_copper": along is not None,
                                     "track_nh": round(lt, 3), "return_vias": nv,
                                     "via_nh": round(lv, 3), "esl_nh": esl,
                                     "total_nh": round(ltot, 3), "c_uf": float(mdl["c_uf"]),
                                     "esr_ohm": esr}
    if not probes:
        return {"error": f"{r.part} has no pin on {list(r.rails)}"}
    f1, f2 = float(band.get("f_start", 1e4)), float(band.get("f_stop", 1e9))
    lines += [f".ac dec 40 {f1:g} {f2:g}", ".control", "run"]
    for k, (rail, node) in enumerate(probes):
        lines.append(f"wrdata z{k}.txt v({node})")
    lines += [".endc", ".end", ""]
    return {"deck": "\n".join(lines), "inputs": inputs, "rails": [p[0] for p in probes],
            "outputs": tuple(f"z{k}.txt" for k in range(len(probes)))}


def run_ngspice(prep: dict, board: LayoutBoard, what: str, name: str,
                target: Optional[float] = None) -> dict[str, Any]:
    """Run a deck from loop_deck or pdn_deck and say what it gave."""
    from ...sim.ngspice import parse_wrdata_complex, run_deck
    if "error" in prep:
        return prep
    folder = _folder(board, what, name)
    (folder / "deck.cir").write_text(prep["deck"], encoding="utf-8")
    run = run_deck(prep["deck"], outputs=prep["outputs"])
    res: dict[str, Any] = {"engine": "ngspice", "deck": str(folder / "deck.cir"),
                           "inputs": prep["inputs"]}
    if not run.get("ok"):
        res.update(ok=False, reason=run.get("reason") or "ngspice failed",
                   errors=run.get("errors"), log_tail=run.get("log_tail"),
                   **({"install_offer": run["install_offer"]} if "install_offer" in run else {}))
        return res
    if what == "loop":
        vals = run["files"]["peak.txt"].split()
        res.update(ok=True, peak_v=float(vals[0]), min_v=float(vals[1]))
    else:
        rails = {}
        for rail, fn in zip(prep["rails"], prep["outputs"]):
            f, z = parse_wrdata_complex(run["files"][fn])
            mag = [abs(x) for x in z]
            k = max(range(len(mag)), key=mag.__getitem__)
            rails[rail] = {"max_ohm": round(mag[k], 5), "at_hz": f[k],
                           "min_ohm": round(min(mag), 5)}
            if target is not None:
                over = [fi for fi, m in zip(f, mag) if m > target]
                rails[rail]["over_target"] = ({"from_hz": over[0], "to_hz": over[-1]}
                                              if over else None)
        res.update(ok=True, rails=rails, target_ohm=target)
    (folder / "result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    res["result_file"] = str(folder / "result.json")
    return res


# --- openEMS: impedance and crosstalk ---------------------------------------------------

def _z_of(board: LayoutBoard) -> dict[str, float]:
    """Copper layer -> its height above the board's bottom, mils, from the
    stack (evenly spread when the stack is not read)."""
    stack = board.meta.get("stack") or []
    names = board.copper_layers()
    copper = [l for l in stack if l.get("kind") == "copper"]
    if len(copper) == len(names) and stack:
        z, out = stack_thickness(board), {}
        k = 0
        for l in stack:
            if l.get("kind") == "copper":
                out[names[k]] = z
                k += 1
            z -= l.get("thickness", 0.0)
        return out
    t = stack_thickness(board)
    return {n: t * (1 - i / max(len(names) - 1, 1)) for i, n in enumerate(names)}


def _ends(board: LayoutBoard, net: str, prefer: tuple = ()) -> list:
    pads = [p for p in board.pads if p.net == net and p.comp]
    named = [p for p in pads if p.key in prefer]
    if len(named) >= 2:
        return named[:2]
    best = None
    for i, a in enumerate(pads):
        for b in pads[i + 1:]:
            d = math.hypot(a.x - b.x, a.y - b.y)
            if best is None or d > best[0]:
                best = (d, a, b)
    return [best[1], best[2]] if best else []


def openems_model(board: LayoutBoard, brief, what: str, net: str,
                  other: str = "", f_max: float = 10e9) -> dict[str, Any]:
    """The geometry of ``net`` (and ``other``, the aggressor, for
    crosstalk) on the board's stack, as the script openEMS runs."""
    nets = [net] + ([other] if other else [])
    tracks = [t for t in board.tracks if t.net in nets and not t.comp and not t.keepout]
    if not any(t.net == net for t in tracks):
        return {"error": f"{net} has no routed track to simulate"}
    if other and not any(t.net == other for t in tracks):
        return {"error": f"{other} has no routed track to simulate"}
    e = brief.of.get(net)
    ref = (e.reference if e is not None and e.reference else "GND")
    zs = _z_of(board)
    stack = board.meta.get("stack") or []
    xs = [x for t in tracks for x in (t.x1, t.x2)]
    ys = [y for t in tracks for y in (t.y1, t.y2)]
    margin = 10 * max(max(t.width for t in tracks), stack_thickness(board) / 4)
    box = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    sheets = []
    for l in board.layers:
        if l.name in zs and l.kind == "plane" and (l.plane_net == ref or not l.plane_net):
            sheets.append(l.name)
    for rg in board.regions:
        if rg.net == ref and rg.kind in ("pour", "copper") and rg.layer in zs \
                and rg.layer not in sheets:
            sheets.append(rg.layer)
    if not sheets:
        return {"error": f"no {ref} plane or pour to return on; give the entry a reference"}
    polys = []
    for t in tracks:
        polys.append({"net": t.net, "z": zs.get(t.layer, 0.0),
                      "pts": [list(map(float, p)) for p in geom.stroke_outline(
                          t.x1, t.y1, t.x2, t.y2, t.width)]})
    vias = [{"net": v.net, "x": v.x, "y": v.y, "d": v.hole or v.diameter / 2,
             "z1": zs.get(v.low_layer, 0.0), "z2": zs.get(v.high_layer, stack_thickness(board))}
            for v in board.vias if v.net in nets]
    dielectrics, z = [], stack_thickness(board)
    for l in stack:
        th = l.get("thickness", 0.0)
        if l.get("kind") == "dielectric" and th > 0:
            dielectrics.append({"z1": z - th, "z2": z, "er": l.get("er", 4.3) or 4.3})
        z -= th
    if not dielectrics:
        dielectrics = [{"z1": 0.0, "z2": stack_thickness(board), "er": 4.3}]
    ports = []
    ends = {}
    for n in nets:
        prefer = (e.driver,) if (n == net and e is not None and e.driver) else ()
        ends[n] = _ends(board, n, prefer)
    if any(len(v) < 2 for v in ends.values()):
        return {"error": "a net to simulate needs two pads for its ports"}
    if other:
        # The aggressor's near end is the one nearer the victim's near end.
        a0 = ends[net][0]
        ends[other].sort(key=lambda p: math.hypot(p.x - a0.x, p.y - a0.y))
    for n in nets:
        for p in ends[n]:
            pz = zs.get(p.copper[0].layer if p.copper else board.copper_layers()[0], 0.0)
            gz = min((zs[s] for s in sheets), key=lambda s: abs(s - pz))
            ports.append({"net": n, "pad": p.key, "x": p.x, "y": p.y, "z1": gz, "z2": pz})
    return {"what": what, "net": net, "other": other, "reference": ref, "box": box,
            "sheets": [zs[s] for s in sheets], "polygons": polys, "vias": vias,
            "dielectrics": dielectrics, "ports": ports, "f_max": f_max,
            "board_thickness": stack_thickness(board)}


SCRIPT = r'''# openEMS model of {net} on {board}, written by eda-agent: run with the
# Python that has openEMS and CSXCAD. Units: mils. Writes result.json here.
import json, os, sys
import numpy as np
from CSXCAD import ContinuousStructure
from openEMS import openEMS

HERE = os.path.dirname(os.path.abspath(__file__))
M = json.load(open(os.path.join(HERE, "model.json")))
f_max = M["f_max"]
FDTD = openEMS(NrTS=60000, EndCriteria=1e-4)
FDTD.SetGaussExcite(f_max / 2, f_max / 2)
FDTD.SetBoundaryCond(["MUR"] * 6)
CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(25.4e-6)
x0, y0, x1, y1 = M["box"]
top = M["board_thickness"]
for k, d in enumerate(M["dielectrics"]):
    sub = CSX.AddMaterial(f"diel{{k}}", epsilon=d["er"])
    sub.AddBox([x0, y0, d["z1"]], [x1, y1, d["z2"]], priority=0)
gnd = CSX.AddMetal("reference")
for z in M["sheets"]:
    gnd.AddBox([x0, y0, z], [x1, y1, z], priority=10)
metals = {{}}
for p in M["polygons"]:
    m = metals.setdefault(p["net"], CSX.AddMetal(p["net"]))
    pts = np.array(p["pts"]).T
    m.AddPolygon(pts, "z", p["z"], priority=10)
for v in M["vias"]:
    m = metals.setdefault(v["net"], CSX.AddMetal(v["net"]))
    m.AddCylinder([v["x"], v["y"], v["z1"]], [v["x"], v["y"], v["z2"]], v["d"] / 2, priority=10)
# Ports: the victim's near and far end, then the aggressor's; the first of
# the aggressor (crosstalk) or of the net (impedance) is driven.
drive = 2 if M["other"] else 0
ports = []
for k, p in enumerate(M["ports"]):
    excite = 1.0 if k == drive else 0.0
    lo, hi = sorted((p["z1"], p["z2"]))
    ports.append(FDTD.AddLumpedPort(k + 1, 50, [p["x"] - 2, p["y"] - 2, lo],
                                    [p["x"] + 2, p["y"] + 2, hi], "z", excite, priority=5))
# Mesh: a twentieth of the shortest wavelength in the board, finer at copper.
er = max(d["er"] for d in M["dielectrics"])
res = 3e8 / f_max / np.sqrt(er) / 25.4e-6 / 20
xs = [x0, x1] + [q[0] for p in M["polygons"] for q in p["pts"]]
ys = [y0, y1] + [q[1] for p in M["polygons"] for q in p["pts"]]
zs = [0.0, top, top + 5 * top, -5 * top] + M["sheets"] + [p["z"] for p in M["polygons"]]
zs += [d["z1"] for d in M["dielectrics"]] + [d["z2"] for d in M["dielectrics"]]
mesh.AddLine("x", sorted(set(xs)))
mesh.AddLine("y", sorted(set(ys)))
mesh.AddLine("z", sorted(set(zs)))
mesh.SmoothMeshLines("all", res, 1.4)
sim = os.path.join(HERE, "run")
FDTD.Run(sim, cleanup=True)
f = np.linspace(f_max / 100, f_max, 201)
for p in ports:
    p.CalcPort(sim, f)
a = ports[drive].uf_inc
S = [p.uf_ref / a for p in ports]
out = {{"f_hz": f.tolist(), "what": M["what"], "net": M["net"], "other": M["other"]}}
if M["what"] == "impedance":
    s11, s21 = S[0], S[1]
    num = (1 + s11) ** 2 - s21 ** 2
    den = (1 - s11) ** 2 - s21 ** 2
    z0 = 50 * np.sqrt(num / den)
    out.update(s11_db=(20 * np.log10(np.abs(s11) + 1e-15)).tolist(),
               s21_db=(20 * np.log10(np.abs(s21) + 1e-15)).tolist(),
               z0_ohm=np.abs(z0).tolist(), z0_median_ohm=float(np.median(np.abs(z0))))
else:
    next_ = S[0]
    fext = S[1]
    out.update(next_db=(20 * np.log10(np.abs(next_) + 1e-15)).tolist(),
               fext_db=(20 * np.log10(np.abs(fext) + 1e-15)).tolist(),
               next_max_db=float(np.max(20 * np.log10(np.abs(next_) + 1e-15))),
               fext_max_db=float(np.max(20 * np.log10(np.abs(fext) + 1e-15))))
json.dump(out, open(os.path.join(HERE, "result.json"), "w"), indent=1)
print("written", os.path.join(HERE, "result.json"))
'''


def openems_available() -> Optional[str]:
    """A Python that imports openEMS and CSXCAD: this one, or the one
    EDA_AGENT_OPENEMS_PYTHON names."""
    import importlib.util
    import os
    named = os.environ.get("EDA_AGENT_OPENEMS_PYTHON", "")
    if named and Path(named).is_file():
        return named
    if all(importlib.util.find_spec(m) is not None for m in ("openEMS", "CSXCAD")):
        return sys.executable
    return None


def run_openems(board: LayoutBoard, model: dict, timeout: float = 3600.0) -> dict[str, Any]:
    """Write the model and its script; run it when openEMS is here."""
    import subprocess

    from ...noconsole import no_window
    from ...sim.ngspice import INSTALL_OFFERS
    if "error" in model:
        return model
    name = model["net"] + (f"_{model['other']}" if model["other"] else "")
    folder = _folder(board, model["what"], name)
    (folder / "model.json").write_text(json.dumps(model, indent=1), encoding="utf-8")
    script = folder / "run_openems.py"
    script.write_text(SCRIPT.format(net=model["net"], board=board.name), encoding="utf-8")
    res: dict[str, Any] = {"engine": "openems", "script": str(script),
                           "model": str(folder / "model.json"),
                           "ports": [p["pad"] for p in model["ports"]],
                           "reference": model["reference"]}
    py = openems_available()
    if py is None:
        res.update(ok=False, reason="openEMS is not installed here; the script is written and "
                   "runs where it is", install_offer=INSTALL_OFFERS["openems"])
        return res
    try:
        proc = subprocess.run([py, str(script)], cwd=str(folder), capture_output=True,
                              text=True, timeout=timeout, **no_window())
    except subprocess.TimeoutExpired:
        res.update(ok=False, reason=f"openEMS ran past {timeout:g} s")
        return res
    out = folder / "result.json"
    if proc.returncode != 0 or not out.exists():
        res.update(ok=False, reason="openEMS failed",
                   log_tail=(proc.stdout + proc.stderr).strip().splitlines()[-15:])
        return res
    data = json.loads(out.read_text(encoding="utf-8"))
    res.update(ok=True, result_file=str(out),
               **{k: v for k, v in data.items() if k.endswith(("_max_db", "_median_ohm"))})
    return res


__all__ = ["loop_deck", "pdn_deck", "run_ngspice", "openems_model", "run_openems",
           "via_nh", "track_nh", "track_ohm", "openems_available"]
