# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Simulate a SPICE deck in ngspice or LTspice and read the results back.

Which simulator: ngspice if it is installed; LTspice if the caller names it
or it is the only one present. This module installs nothing.

Either simulator returns the same shape: ``.meas`` values, errors and
warnings from its log, and waveforms from an ASCII raw file, which both
write in the SPICE3 layout. Checked here with one RC deck: ngspice-47 and
LTspice 24.0.12 both reported V(out) = 4.96629 V at 5 ms and a 10-90 %
rise of 2.19721 ms (RC ln 9). In batch mode ngspice skips ``.meas`` when
given ``-r``, so the deck gets a ``.control`` block that runs it and
writes the raw file instead. LTspice logs in UTF-8 after a good run and
in UTF-16 without a byte-order mark after a failed one, so logs are
decoded by inspection.

Decks may come from a model, so each is screened first: ``.ferret``
(LTspice's downloader) is refused, and ``.include``/``.lib`` may name a
bare file or one inside the run folder, the workspace, or a folder in
EDA_AGENT_SPICE_MODEL_DIRS. Every run has its own folder and a time limit.
"""
from __future__ import annotations

import math
import os
import re
import secrets
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Optional

from ..noconsole import no_window
from .ngspice import INSTALL_OFFERS, find_ltspice, find_ngspice

SIMULATORS = ("auto", "ngspice", "ltspice")


def pick_simulator(wanted: str = "auto") -> dict:
    """``{"engine", "path", "why"}`` for the simulator to use, or
    ``{"engine": "", "reason", "install_offers"}`` when there is none."""
    wanted = (wanted or "auto").strip().lower()
    if wanted not in SIMULATORS:
        return {"engine": "", "reason": "engine is auto, ngspice or ltspice"}
    found = {"ngspice": find_ngspice(), "ltspice": find_ltspice()}
    if wanted != "auto":
        if found[wanted]:
            return {"engine": wanted, "path": found[wanted], "why": "asked for"}
        label = "LTspice" if wanted == "ltspice" else "ngspice"
        return {"engine": "", "reason": f"{label} is not installed",
                "install_offers": {wanted: INSTALL_OFFERS[wanted]}}
    if found["ngspice"]:
        return {"engine": "ngspice", "path": found["ngspice"],
                "why": "ngspice is installed and comes first"}
    if found["ltspice"]:
        return {"engine": "ltspice", "path": found["ltspice"],
                "why": "LTspice is installed and ngspice is not"}
    return {"engine": "", "reason": "neither ngspice nor LTspice is installed",
            "install_offers": {k: INSTALL_OFFERS[k] for k in ("ngspice", "ltspice")}}


# --- screening the deck ----------------------------------------------------------

_READS_FILE = {".include", ".inc", ".lib"}
_WRITES_FILE = {".savebias", ".loadbias", ".wave", ".backanno"}


def model_folders(workspace: Path) -> list[Path]:
    listed = os.environ.get("EDA_AGENT_SPICE_MODEL_DIRS", "")
    return [workspace] + [Path(p) for p in listed.split(os.pathsep) if p.strip()]


def _words(line: str) -> list[str]:
    """The line's words, quotes taken off; backslashes kept (Windows paths)."""
    try:
        words = shlex.split(line, posix=False)
    except ValueError:
        words = line.split()
    return [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w
            for w in words]


def _leaves_folder(name: str) -> bool:
    """Absolute, on a drive, or climbing with '..'."""
    win = PureWindowsPath(name)
    return Path(name).is_absolute() or win.is_absolute() or bool(win.drive) \
        or ".." in Path(name).parts or ".." in win.parts


def _within(path: Path, folders: list[Path]) -> bool:
    try:
        where = path.resolve()
        return any(where == f.resolve() or f.resolve() in where.parents for f in folders)
    except OSError:
        return False


def unsafe_lines(deck: str, run_folder: Path, folders: list[Path]) -> list[str]:
    """Why the deck may not run here; empty when it may."""
    found = ["the deck contains a NUL byte"] if "\x00" in deck else []
    for number, line in enumerate(deck.splitlines(), 1):
        words = _words(line.strip())
        if not words:
            continue
        directive = words[0].lower()
        if directive == ".ferret":
            found.append(f"line {number}: .ferret downloads files and does not run here")
        elif directive in _READS_FILE and len(words) > 1:
            target = words[1]
            if not _leaves_folder(target):
                continue          # a plain name: the run folder or the simulator's library
            where = Path(target) if Path(target).is_absolute() else run_folder / target
            if not _within(where, [run_folder, *folders]):
                found.append(f"line {number}: {directive} {target} is outside the workspace "
                             "and EDA_AGENT_SPICE_MODEL_DIRS")
        elif directive in _WRITES_FILE:
            if any(_leaves_folder(w) for w in words[1:] if "." in w or "/" in w or "\\" in w):
                found.append(f"line {number}: {directive} writes outside the run folder")
    return found


def _brings_control(deck: str) -> bool:
    return any(line.strip().lower().startswith(".control") for line in deck.splitlines())


def with_ngspice_control(deck: str, raw_name: str) -> str:
    """The deck plus a .control block that runs it and writes an ASCII raw
    file; a deck with its own block is left alone."""
    if _brings_control(deck):
        return deck
    body = deck.rstrip().splitlines()
    if body and body[-1].strip().lower() == ".end":
        body.pop()
    control = [".control", "set filetype=ascii", "run", f"write {raw_name}", ".endc", ".end"]
    return "\n".join(body + control) + "\n"


# --- reading the results -----------------------------------------------------------

def decode_output(path: Path) -> str:
    """A simulator's text file, UTF-16 (marked or not) or UTF-8."""
    blob = path.read_bytes()
    if blob[:2] == b"\xff\xfe":
        return blob[2:].decode("utf-16-le", errors="replace")
    utf16 = len(blob) > 3 and blob[1] == 0 and blob[3] == 0
    return blob.decode("utf-16-le" if utf16 else "utf-8", errors="replace")


_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
#: Ways the two simulators print a measurement, most specific first.
_MEASURE_FORMS = (
    re.compile(rf"([A-Za-z_]\w*)\s*=\s*({_NUMBER})\s+(?:FROM|AT)\b", re.I),   # LTspice
    re.compile(rf"([A-Za-z_]\w*)\s*:\s*.*?=\s*\(?({_NUMBER})"),               # LTspice
    re.compile(rf"([A-Za-z_]\w*)\s*=\s*({_NUMBER})(?:\s|$)"),                 # ngspice
)
_NOT_MEASURES = {"tnom", "temp", "method", "solver", "asciirawfile"}
_FAILURE = re.compile(r"\b(?:error|fatal|singular matrix|time ?step too small|"
                      r"unknown subc|can't find|could not find|interrupted)\b", re.I)


def parse_log(text: str) -> dict:
    """Measurements, errors and warnings from either simulator's log."""
    measures: dict[str, float] = {}
    errors: list[str] = []
    warnings: list[str] = []
    lines = [ln.strip() for ln in text.splitlines()]
    for n, line in enumerate(lines):
        if not line:
            continue
        if line.lower().startswith("warning"):
            warnings.append(line)
        elif _FAILURE.search(line) and "elapsed" not in line.lower():
            # LTspice puts the offending deck line after a line ending in ':'.
            follow = lines[n + 1] if n + 1 < len(lines) and line.endswith(":") else ""
            errors.append(f"{line} {follow}".strip())
        else:
            for form in _MEASURE_FORMS:
                hit = form.match(line)
                if hit:
                    if hit.group(1).lower() not in _NOT_MEASURES:
                        measures[hit.group(1)] = float(hit.group(2))
                    break
    return {"measures": measures, "errors": errors, "warnings": warnings}


@dataclass
class Waveset:
    """One analysis from a raw file: its variables and their samples."""
    title: str
    is_complex: bool
    names: list[tuple[str, str]]                       # (name, kind)
    samples: list[list] = field(default_factory=list)  # one list per variable


def _sections(lines: list[str]):
    """(header dict, variable lines, value tokens) per analysis."""
    at = 0
    while at < len(lines):
        header: dict[str, str] = {}
        while at < len(lines) and not lines[at].startswith("Variables:"):
            if lines[at].startswith("Binary:"):
                raise ValueError("the raw file is binary; ASCII output was not requested")
            key, colon, value = lines[at].partition(":")
            if colon:
                header[key.strip()] = value.strip()
            at += 1
        if at >= len(lines):
            return
        count = int(header.get("No. Variables", "0"))
        variables = lines[at + 1: at + 1 + count]
        at += 1 + count
        if at < len(lines) and lines[at].startswith("Values:"):
            at += 1
        tokens: list[str] = []
        wanted = int(header.get("No. Points", "0")) * (count + 1)
        while at < len(lines) and len(tokens) < wanted and not lines[at].startswith("Title:"):
            tokens += lines[at].split()
            at += 1
        yield header, variables, tokens


def parse_ascii_raw(text: str) -> list[Waveset]:
    """Every analysis in an ASCII SPICE3 raw file."""
    found = []
    for header, variables, tokens in _sections(text.splitlines()):
        names = [(cols[1], cols[2] if len(cols) > 2 else "")
                 for cols in (v.split() for v in variables)]
        complex_values = "complex" in header.get("Flags", "").lower()
        wave = Waveset(header.get("Plotname", ""), complex_values, names,
                       [[] for _ in names])
        width = len(names) + 1                 # each point starts with its index
        for start in range(0, int(header.get("No. Points", "0")) * width, width):
            for slot, token in enumerate(tokens[start + 1:start + width]):
                if complex_values:
                    real, imag = token.split(",")
                    wave.samples[slot].append(complex(float(real), float(imag)))
                else:
                    wave.samples[slot].append(float(token))
        found.append(wave)
    return found


def describe(wave: Waveset, wanted: Optional[list[str]] = None, samples: int = 0) -> dict:
    """Range, start and end of each trace; ``samples`` of the wanted ones too."""
    asked = {w.lower() for w in wanted or []}
    traces: dict[str, dict] = {}
    axis = wave.samples[0] if wave.samples else []
    for (name, kind), values in zip(wave.names[1:], wave.samples[1:]):
        if not values or (asked and name.lower() not in asked):
            continue
        size = [abs(v) for v in values] if wave.is_complex else values
        trace = {"kind": kind, "min": min(size), "max": max(size),
                 "first": size[0], "last": size[-1]}
        if samples and name.lower() in asked:
            stride = max(1, math.ceil(len(values) / samples))
            trace["x"] = [abs(x) if isinstance(x, complex) else x for x in axis[::stride]]
            picked = values[::stride]
            if wave.is_complex:
                trace["mag_db"] = [20 * math.log10(max(abs(v), 1e-300)) for v in picked]
                trace["phase_deg"] = [math.degrees(math.atan2(v.imag, v.real)) for v in picked]
            else:
                trace["y"] = picked
        traces[name] = trace
    return {"plot": wave.title, "x": wave.names[0][0] if wave.names else "",
            "points": len(axis), "waves": traces}


def simulate(deck: str, workspace: Path, engine: str = "auto", timeout_s: float = 120,
             wanted: Optional[list[str]] = None, samples: int = 0) -> dict:
    """Screen a deck, run it in a fresh folder under the workspace, read it back."""
    choice = pick_simulator(engine)
    if not choice.get("engine"):
        return {"ok": False, **choice}
    sim = choice["engine"]
    text = (deck or "").replace("\r\n", "\n").strip()
    if not text:
        return {"ok": False, "reason": "the deck is empty"}
    if text.splitlines()[-1].strip().lower() != ".end":
        text += "\n.end"
    if sim == "ltspice" and _brings_control(text):
        return {"ok": False, "reason": "LTspice does not run a .control block (it is "
                "ngspice's); remove it or use engine='ngspice'"}
    folder = workspace / "sim" / "runs" / f"run_{secrets.token_hex(6)}"
    folder.mkdir(parents=True, exist_ok=True)
    refused = unsafe_lines(text, folder, model_folders(workspace))
    if refused:
        return {"ok": False, "reason": "the deck was not run", "problems": refused}
    if sim == "ngspice":
        (folder / "deck.cir").write_text(with_ngspice_control(text, "deck.raw"),
                                         encoding="utf-8")
        command = [choice["path"], "-b", "deck.cir"]
    else:
        (folder / "deck.net").write_text(text + "\n", encoding="utf-8")
        command = [choice["path"], "-b", "-ascii", str(folder / "deck.net")]
    try:
        done = subprocess.run(command, cwd=folder, capture_output=True, timeout=timeout_s,
                              **no_window())
    except subprocess.TimeoutExpired:
        return {"ok": False, "engine": sim, "reason": f"{sim} was stopped after "
                f"{timeout_s:g} s", "run_folder": str(folder)}
    log_file = folder / "deck.log"
    if sim == "ngspice":
        log = (done.stdout + done.stderr).decode("utf-8", errors="replace")
        log_file.write_text(log, encoding="utf-8")
    else:
        log = decode_output(log_file) if log_file.exists() else ""
    reading = parse_log(log)
    waves: list[Waveset] = []
    for raw in sorted(folder.glob("deck*.raw")):
        try:
            waves += parse_ascii_raw(decode_output(raw))
        except (ValueError, IndexError) as exc:
            reading["errors"].append(f"{raw.name} could not be read: {exc}")
    return {"ok": done.returncode == 0 and not reading["errors"], "engine": sim,
            "engine_path": choice["path"], "why": choice["why"],
            "returncode": done.returncode, **reading,
            "plots": [describe(w, wanted, samples) for w in waves
                      if w.title and w.samples and w.samples[0]],
            "run_folder": str(folder), "log_tail": log.strip().splitlines()[-12:]}
