# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Find the simulators on this machine, run ngspice, measure loop gain.

Nothing here installs anything. A missing simulator comes back as not
available with an install offer (command and source) for the caller to put
to the user; installing is the user's decision, made in the conversation.

Loop gain is measured by Middlebrook's double injection, which needs no
assumption about the impedances at the break: a voltage injection gives
Tv = -V(a)/V(b), a current injection gives Ti = -I(a)/I(b), and

    T = (Tv*Ti - 1) / (Tv + Ti + 2)

Checked in ngspice-47 on a VCVS of gain 10 behind 1k into a 9k/1k divider:
Tv = 10.1, Ti = 110, T = 9.0909, the analytic 10 * 10k / 11k.
"""

from __future__ import annotations

import cmath
import glob
import importlib.util
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..noconsole import no_window

#: Batch-mode executables, the console build first (it never opens a window).
NGSPICE_NAMES = ("ngspice_con", "ngspice")

INSTALL_OFFERS = {
    "ngspice": {
        "source": "https://ngspice.sourceforge.io/download.html",
        "scoop": "scoop install ngspice",
        "note": "KiCad ships ngspice only as a DLL for its own GUI; the "
                "batch simulator is a separate install.",
    },
    "ltspice": {
        "source": "https://www.analog.com/en/resources/design-tools-and-calculators/"
                  "ltspice-simulator.html",
        "note": "free from Analog Devices; ngspice stays the first choice when "
                "both are installed",
    },
    "openems": {
        "source": "https://github.com/thliebig/openEMS-Project/releases",
        "note": "Windows builds are a zip from the release page; the Python "
                "bindings (openEMS, CSXCAD) come with it.",
    },
}


def find_ngspice() -> Optional[str]:
    for name in NGSPICE_NAMES:
        path = shutil.which(name)
        if path:
            return path
    shims = Path.home() / "scoop" / "shims"
    for name in NGSPICE_NAMES:
        exe = shims / f"{name}.exe"
        if exe.exists():
            return str(exe)
    return None


def find_ltspice() -> Optional[str]:
    """LTspice 24 (Analog Devices) or XVII (Linear), per-user or system
    install, or the file EDA_AGENT_LTSPICE_EXE names."""
    import os
    named = os.environ.get("EDA_AGENT_LTSPICE_EXE", "")
    if named and Path(named).is_file():
        return named
    for name in ("LTspice", "XVIIx64"):
        found = shutil.which(name)
        if found:
            return found
    local = os.environ.get("LOCALAPPDATA", "")
    places = [Path(local) / "Programs" / "ADI" / "LTspice" / "LTspice.exe"] if local else []
    places += [Path(r"C:\Program Files\ADI\LTspice\LTspice.exe"),
               Path(r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe")]
    return next((str(p) for p in places if p.is_file()), None)


def ngspice_version(path: str) -> str:
    try:
        out = subprocess.run([path, "-v"], capture_output=True, text=True,
                             timeout=20, **no_window()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"ngspice-(\d+(?:\.\d+)?)", out)
    return m.group(1) if m else ""


def simulator_backends() -> dict:
    """Which simulators are here, with versions, and offers for the rest."""
    ng = find_ngspice()
    lt = find_ltspice()
    kicad_dll = sorted(glob.glob(r"C:/Program Files/KiCad/*/bin/ngspice.dll"))
    ems_exe = shutil.which("openEMS")
    ems_py = all(importlib.util.find_spec(m) is not None
                 for m in ("openEMS", "CSXCAD"))
    report = {
        "ngspice": {"available": bool(ng), "path": ng or "",
                    "version": ngspice_version(ng) if ng else "",
                    "kicad_library_only": kicad_dll if not ng else []},
        # Runs a deck when asked for, or when ngspice is not installed
        # (design_simulate_deck); ngspice comes first when both are here.
        "ltspice": {"available": bool(lt), "path": lt or ""},
        "openems": {"available": bool(ems_exe or ems_py),
                    "path": ems_exe or "", "python_bindings": ems_py},
    }
    offers = {}
    for name, entry in report.items():
        if not entry["available"]:
            offer = dict(INSTALL_OFFERS[name])
            if name == "ngspice" and not shutil.which("scoop"):
                offer.pop("scoop", None)
            offers[name] = offer
    if offers:
        report["install_offers"] = offers
        report["install_policy"] = (
            "nothing is installed without the user's approval in the "
            "conversation; a missing simulator never fails a design")
    return report


def run_deck(deck: str, outputs: tuple[str, ...] = (), timeout: float = 120,
             ngspice: Optional[str] = None) -> dict:
    """Run a deck in batch mode in a scratch folder; return the text of the
    files its .control block writes (``outputs``) with the log."""
    exe = ngspice or find_ngspice()
    if not exe:
        return {"ok": False, "reason": "ngspice is not installed",
                "install_offer": INSTALL_OFFERS["ngspice"]}
    with tempfile.TemporaryDirectory(prefix="eda_ngspice_") as tmp:
        (Path(tmp) / "deck.cir").write_text(deck, encoding="utf-8")
        try:
            proc = subprocess.run([exe, "-b", "deck.cir"], cwd=tmp,
                                  capture_output=True, text=True,
                                  timeout=timeout, **no_window())
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": f"ngspice ran past {timeout:g} s"}
        files = {}
        for name in outputs:
            f = Path(tmp) / name
            if f.exists():
                files[name] = f.read_text(encoding="utf-8", errors="replace")
        log = proc.stdout + proc.stderr
        errors = [ln.strip() for ln in log.splitlines()
                  if re.match(r"\s*(error|Error|ERROR)\b", ln)]
        ok = proc.returncode == 0 and all(n in files for n in outputs)
        return {"ok": ok, "returncode": proc.returncode, "files": files,
                "errors": errors, "log_tail": log.strip().splitlines()[-15:]}


def parse_wrdata_complex(text: str) -> tuple[list[float], list[complex]]:
    """``wrdata`` of one complex vector: frequency, real, imaginary."""
    freqs, values = [], []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) >= 3:
            freqs.append(float(cols[0]))
            values.append(complex(float(cols[1]), float(cols[2])))
    return freqs, values


def _unwrap(degrees: list[float]) -> list[float]:
    out, offset = [], 0.0
    for i, d in enumerate(degrees):
        if i:
            step = d + offset - out[-1]
            if step > 180:
                offset -= 360
            elif step < -180:
                offset += 360
        out.append(d + offset)
    return out


def _cross(freqs, ys, level):
    """First frequency where ys falls through ``level``, log-interpolated."""
    for i in range(1, len(ys)):
        if ys[i - 1] >= level > ys[i]:
            t = (ys[i - 1] - level) / (ys[i - 1] - ys[i])
            lf = math.log10(freqs[i - 1]) + t * (
                math.log10(freqs[i]) - math.log10(freqs[i - 1]))
            return 10 ** lf, t, i
    return None


def stability_margins(freqs: list[float], gain: list[complex]) -> dict:
    """Crossover, phase margin and gain margin of a loop gain T(f), with T
    positive at low frequency for negative feedback."""
    mag_db = [20 * math.log10(max(abs(g), 1e-30)) for g in gain]
    phase = _unwrap([math.degrees(cmath.phase(g)) for g in gain])
    out = {"dc_gain_db": round(mag_db[0], 2) if mag_db else None}
    c = _cross(freqs, mag_db, 0.0)
    if c:
        fc, t, i = c
        ph = phase[i - 1] + t * (phase[i] - phase[i - 1])
        out.update(crossover_hz=fc, phase_margin_deg=round(180 + ph, 1))
    else:
        out["crossover_hz"] = None
        out["note"] = ("no 0 dB crossing in the sweep" if mag_db and mag_db[0] > 0
                       else "the loop gain is below 1 at the lowest frequency")
    g = _cross(freqs, phase, -180.0)
    if g:
        f180, t, i = g
        m = mag_db[i - 1] + t * (mag_db[i] - mag_db[i - 1])
        out.update(phase_180_hz=f180, gain_margin_db=round(-m, 1))
    return out


def loop_gain(netlist: str, driver_node: str, load_node: str,
              f_start: float = 1.0, f_stop: float = 100e6,
              points_per_decade: int = 40, timeout: float = 120,
              ngspice: Optional[str] = None) -> dict:
    """Loop gain at a break between ``driver_node`` (the output that drives
    the loop) and ``load_node`` (everything it drives). The netlist must
    already have the driver's pin on ``driver_node`` and the rest of the
    net on ``load_node``; the injection joins them."""
    inject = (f"Vlg_i {driver_node} lg_m DC 0 AC 1\n"
              f"Vlg_s lg_m {load_node} DC 0\n"
              f"Ilg_i 0 lg_m DC 0 AC 0\n")
    sweep = f"ac dec {points_per_decade} {f_start:g} {f_stop:g}"
    control = (".control\n"
               f"{sweep}\n"
               f"let tv = -v({driver_node})/v({load_node})\n"
               "alter Vlg_i acmag=0\n"
               "alter Ilg_i acmag=1\n"
               f"{sweep}\n"
               "let ti = -ac2.i(vlg_i)/ac2.i(vlg_s)\n"
               "let tvv = ac1.tv\n"
               "let t = (tvv*ti - 1)/(tvv + ti + 2)\n"
               "wrdata loopgain.txt t\n"
               ".endc\n")
    body = netlist.rstrip()
    if body.lower().endswith(".end"):
        body = body[:-4].rstrip()
    deck = f"{body}\n* loop-gain injection\n{inject}{control}.end\n"
    run = run_deck(deck, outputs=("loopgain.txt",), timeout=timeout,
                   ngspice=ngspice)
    if not run.get("ok"):
        return {"ok": False, **{k: v for k, v in run.items() if k != "files"}}
    freqs, gain = parse_wrdata_complex(run["files"]["loopgain.txt"])
    return {"ok": True, "method": "Middlebrook double injection",
            "points": len(freqs), **stability_margins(freqs, gain),
            "freqs": freqs, "gain": gain}
