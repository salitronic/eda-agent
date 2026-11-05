# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read and change the analysis settings in a project's .simcfg.

The file, MEASURED on what Altium 26.10.1.6 writes:

    <ProjectConfig ...>
      <Settings><Version>1.1</Version><Affect>Project|Document</Affect>
      <Config>OP_Analysis=True|TRAN_StopTime=5u|...</Config>     project
      <Documents>
        <DocumentConfig><Name>sheet.SchDoc</Name><Id>...</Id>
          <Config>...</Config>                                  one sheet
          <Results>...</Results>
      <Results><ResultConfig>...<Config>...</Config>             run history

Only the project Config and each sheet's Config are settings; a Config
inside Results records a past run and is left alone. Settings are edited
as text so everything else in the file, including Altium's namespace
declarations and line endings, is written back untouched.

What is set here is MEASURED to reach a run: a stop time and AC and DC
parameters written to the file were used. The per-analysis on/off flags
are honoured by a run for a sheet, the form the Simulation Dashboard
sends, but only as read at the first run after the project opens:
Altium keeps that selection until the project is reopened, and each run
switches operating point, DC, transient and AC back on in its copy
(MEASURED on AD 26.11). A run of the project's first simulator output
did all four every time, whatever the flags said.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, unescape

DEFAULT_NAME = "project_sim_config.simcfg"

_TAG = re.compile(r"<(/?)([A-Za-z_][\w.-]*)([^>]*?)(/?)>")
_SETTING_PARENTS = ("ProjectConfig", "DocumentConfig")

_SWEEPS = {"dec": "DEC", "oct": "OCT", "lin": "LIN"}

#: The analyses a run can be limited to, by the name a caller gives.
ANALYSES = {"op": "OP_Analysis", "dc": "DC_Analysis",
            "transient": "TRAN_Analysis", "ac": "AC_Analysis"}
_ANALYSIS_ALIASES = {"operating_point": "op", "operating point": "op",
                     "dc_sweep": "dc", "dc sweep": "dc", "tran": "transient",
                     "ac_sweep": "ac", "ac sweep": "ac"}

#: Every on/off flag the dashboard clears before it switches one analysis
#: on (read from the dashboard), so nothing else rides along.
_ALL_FLAGS = ("OP_Analysis", "TRAN_Analysis", "DC_Analysis", "AC_Analysis",
              "TF_Analysis", "PZ_Analysis", "Noise_Analysis", "SP_Analysis",
              "TRAN_FourierAnalysis", "TRAN_StressAnalysis", "TempSweep_Analysis",
              "ParamSweep_Analysis", "MonteCarlo_Analysis")

#: The settings reported for each analysis that is switched on.
_REPORTED = {
    "op": (),
    "dc": ("DC_PrimarySource", "DC_PrimaryStart", "DC_PrimaryStop", "DC_PrimaryStep"),
    "transient": ("TRAN_StartTime", "TRAN_StopTime", "TRAN_StepTime", "TRAN_MaxStepTime"),
    "ac": ("AC_StartFrequency", "AC_StopFrequency", "AC_TestPoints", "AC_SweepType"),
}
_OUTPUT_PREFIX = {"dc": "DC", "transient": "TRAN", "ac": "AC"}


class SimCfgError(ValueError):
    """The file or a requested setting cannot be used."""


@dataclass
class SimCfg:
    text: str
    affect: str = ""
    sheets: list = field(default_factory=list)

    def settings(self) -> list:
        """(owner, settings dict) for the project and each sheet."""
        return [(owner, _decode(raw)) for owner, raw, _, _ in _config_spans(self.text)]


def read_simcfg(path) -> SimCfg:
    text = Path(path).read_bytes().decode("utf-8-sig")
    return parse_simcfg(text)


def parse_simcfg(text: str) -> SimCfg:
    if "<ProjectConfig" not in text:
        raise SimCfgError("not a simulation settings file: no <ProjectConfig>")
    affect = re.search(r"<Affect>(.*?)</Affect>", text, re.S)
    sheets = re.findall(r"<DocumentConfig>.*?<Name>(.*?)</Name>", text, re.S)
    if not _config_spans(text):
        raise SimCfgError("the settings file holds no project or sheet Config")
    return SimCfg(text=text, affect=(affect.group(1).strip() if affect else ""),
                  sheets=[unescape(s.strip()) for s in sheets])


def new_simcfg() -> SimCfg:
    """A settings file with an empty project Config, for a project without one."""
    text = ('<?xml version="1.0"?>\r\n'
            '<ProjectConfig xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n'
            '  <Settings>\r\n    <Version>1.1</Version>\r\n'
            '    <Affect>Project</Affect>\r\n  </Settings>\r\n'
            '  <Config>OP_Analysis=True</Config>\r\n'
            '  <Documents />\r\n  <Results />\r\n</ProjectConfig>\r\n')
    return parse_simcfg(text)


def write_simcfg(cfg: SimCfg, path) -> None:
    Path(path).write_bytes(cfg.text.encode("utf-8"))


def apply(cfg: SimCfg, changes: dict) -> SimCfg:
    """The same file with ``changes`` set in the project and every sheet."""
    out, last = [], 0
    for _owner, raw, start, end in _config_spans(cfg.text):
        settings = _decode(raw)
        settings.update(changes)
        out.append(cfg.text[last:start])
        out.append(_encode(settings))
        last = end
    out.append(cfg.text[last:])
    return parse_simcfg("".join(out))


def only_analyses(names) -> dict:
    """Settings keys that switch on the analyses named and every other off.

    names: "op", "dc", "transient" and "ac" (also "operating_point",
    "dc_sweep", "tran", "ac_sweep").
    """
    if isinstance(names, str):
        names = [names]
    chosen = []
    for name in names or []:
        key = str(name).strip().lower()
        key = _ANALYSIS_ALIASES.get(key, key)
        if key not in ANALYSES:
            raise SimCfgError(f"unknown analysis {name!r}; use one of "
                              + ", ".join(ANALYSES))
        if key not in chosen:
            chosen.append(key)
    if not chosen:
        raise SimCfgError("name at least one analysis to run")
    changes = {flag: "False" for flag in _ALL_FLAGS}
    for key in chosen:
        changes[ANALYSES[key]] = "True"
    return changes


def describe(cfg: SimCfg, sheet: str = "") -> dict:
    """The setup a run would use: where it comes from and what is on.

    Altium reads the sheet's own settings when ``affect`` is Document and
    the project's when it is Project. ``sheet`` is the sheet's file name
    or path; empty, or a sheet without its own settings, reports the
    project's.
    """
    configs = cfg.settings()
    owner, settings = configs[0]
    name = Path(sheet).name.lower() if sheet else ""
    if cfg.affect.lower() == "document" and name:
        for o, s in configs:
            if o.lower() == name:
                owner, settings = o, s
                break
    def on(key):
        return settings.get(key, "").strip().lower() == "true"

    enabled = [k for k, flag in ANALYSES.items() if on(flag)]
    others = sorted(f for f in _ALL_FLAGS
                    if f not in ANALYSES.values() and on(f))
    detail = {}
    for key in enabled:
        entry = {k: settings[k] for k in _REPORTED[key] if k in settings}
        prefix = _OUTPUT_PREFIX.get(key)
        if prefix:
            entry["outputs"] = _enabled_outputs(settings, prefix)
        detail[key] = entry
    return {"affect": cfg.affect, "source": owner, "enabled": enabled,
            "other_enabled": others, "settings": detail,
            "sheets": list(cfg.sheets), "results": _results_history(cfg.text)}


def _enabled_outputs(settings: dict, prefix: str) -> list:
    out, i = [], 0
    while f"{prefix}_Variable{i}" in settings:
        expr = settings[f"{prefix}_Variable{i}"].strip()
        if expr and settings.get(f"{prefix}_VariableEnabled{i}", "True").lower() == "true":
            out.append(expr)
        i += 1
    return out


def _results_history(text: str) -> list:
    """Each recorded run: its name, type, date and whether it is locked."""
    out = []
    for block in re.findall(r"<ResultConfig>(.*?)</ResultConfig>", text, re.S):
        row = {}
        for tag, key in (("Name", "name"), ("Type", "type"), ("Date", "date"),
                         ("IsLocked", "locked")):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
            if m:
                row[key] = unescape(m.group(1).strip())
        if "locked" in row:
            row["locked"] = row["locked"].lower() == "true"
        out.append(row)
    return out


def analysis_changes(*, transient=None, ac=None, dc=None, outputs=None) -> dict:
    """Settings keys for the analyses asked for, validated.

    transient: {"stop", "step", "start"=0, "max_step"=step}
    ac:        {"start", "stop", "points", "sweep"="dec"}
    dc:        {"source", "start", "stop", "step"}
    outputs:   expressions to plot for each analysis set, e.g. "v(out)"

    Times, frequencies and levels are SPICE values: a number in SI units,
    or a string with a SPICE suffix such as "5u" or "1meg".
    """
    changes: dict = {}
    exprs = [str(o).strip() for o in (outputs or []) if str(o).strip()]
    if transient is not None:
        stop = _value(transient, "stop", "transient")
        step = _value(transient, "step", "transient")
        start = _value(transient, "start", "transient", default=0.0)
        max_step = _value(transient, "max_step", "transient", default=None)
        _positive(step, "transient step")
        if stop <= start:
            raise SimCfgError("transient stop must be after its start")
        if step > stop - start:
            raise SimCfgError("transient step is longer than the run")
        if max_step is not None:
            _positive(max_step, "transient max_step")
        changes.update(_pair("TRAN_StartTime", _given(transient, "start", start)))
        changes.update(_pair("TRAN_StopTime", _given(transient, "stop", stop)))
        changes.update(_pair("TRAN_StepTime", _given(transient, "step", step)))
        changes.update(_pair("TRAN_MaxStepTime",
                             _given(transient, "max_step", max_step) if max_step is not None
                             else _given(transient, "step", step)))
        changes["TRAN_Analysis"] = "True"
        changes.update(_outputs("TRAN", exprs))
    if ac is not None:
        start = _value(ac, "start", "ac")
        stop = _value(ac, "stop", "ac")
        _positive(start, "AC start frequency")
        if stop <= start:
            raise SimCfgError("AC stop frequency must be above its start")
        points = ac.get("points")
        if not isinstance(points, int) or isinstance(points, bool) or points < 1:
            raise SimCfgError("AC points must be a whole number of at least 1")
        sweep = _SWEEPS.get(str(ac.get("sweep", "dec")).strip().lower())
        if sweep is None:
            raise SimCfgError("AC sweep must be dec, oct or lin")
        changes.update(_pair("AC_StartFrequency", _given(ac, "start", start)))
        changes.update(_pair("AC_StopFrequency", _given(ac, "stop", stop)))
        changes.update(_pair("AC_TestPoints", points))
        changes["AC_SweepType"] = sweep
        changes["AC_Analysis"] = "True"
        changes.update(_outputs("AC", exprs))
    if dc is not None:
        source = str(dc.get("source", "")).strip()
        if not source:
            raise SimCfgError("a DC sweep needs a source, e.g. V1 or Temp")
        start = _value(dc, "start", "dc")
        stop = _value(dc, "stop", "dc")
        step = _value(dc, "step", "dc")
        _positive(abs(step), "DC step")
        if (stop - start) * step < 0:
            raise SimCfgError("the DC step points away from the stop value")
        start, stop, step = (_given(dc, "start", start), _given(dc, "stop", stop),
                             _given(dc, "step", step))
        changes["DC_PrimarySource"] = source
        changes.update(_pair("DC_PrimaryStart", start))
        changes.update(_pair("DC_PrimaryStop", stop))
        changes.update(_pair("DC_PrimaryStep", step))
        # The dashboard keeps the same sweep as its first custom row.
        changes["DC_CustomEnabled0"] = "True"
        changes["DC_CustomSource0"] = source
        changes.update(_pair("DC_CustomStart0", start))
        changes.update(_pair("DC_CustomStop0", stop))
        changes.update(_pair("DC_CustomStep0", step))
        changes["DC_Analysis"] = "True"
        changes.update(_outputs("DC", exprs))
    if not changes:
        raise SimCfgError("nothing to set: give transient, ac or dc")
    return changes


def _config_spans(text: str) -> list:
    """(owner, raw text, start, end) of each settings Config, in order.

    The owner is "project" or the sheet's name. A Config nested inside a
    Results block is history and is not returned.
    """
    spans, stack = [], []
    sheet = None
    for m in _TAG.finditer(text):
        closing, name, _attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if name.startswith("?"):
            continue
        if closing:
            while stack and stack.pop() != name:
                pass
            continue
        if selfclose:
            continue
        if name == "Name" and stack and stack[-1] == "DocumentConfig":
            close = text.find("</Name>", m.end())
            sheet = unescape(text[m.end():close].strip())
        if name == "Config" and stack and stack[-1] in _SETTING_PARENTS \
                and "Results" not in stack:
            close = text.find("</Config>", m.end())
            if close < 0:
                raise SimCfgError("a Config element is not closed")
            owner = "project" if stack[-1] == "ProjectConfig" else (sheet or "")
            spans.append((owner, text[m.end():close], m.end(), close))
        stack.append(name)
    return spans


def _decode(raw: str) -> dict:
    out = {}
    for item in unescape(raw).split("|"):
        if not item:
            continue
        key, _, value = item.partition("=")
        out[key] = value
    return out


def _encode(settings: dict) -> str:
    return escape("|".join(f"{k}={v}" for k, v in settings.items()))


def _spice(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        raise SimCfgError("a SPICE value cannot be True or False")
    if isinstance(value, int):
        return str(value)
    return format(float(value), ".6g")


def _pair(key: str, value) -> dict:
    text = _spice(value)
    return {key: text, f"{key}_UserValue": text}


_SUFFIX = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
           "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
_NUMBER = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*(meg|[fpnumkgt])?[a-z]*\s*$",
                     re.I)


def spice_number(value) -> float:
    """A SPICE value as a float: 5u is 5e-6, 1meg is 1e6, 10 is 10."""
    if isinstance(value, bool):
        raise SimCfgError("a SPICE value cannot be True or False")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        m = _NUMBER.match(str(value))
        if not m:
            raise SimCfgError(f"{value!r} is not a SPICE value")
        number = float(m.group(1)) * _SUFFIX.get((m.group(2) or "").lower(), 1.0)
    if not math.isfinite(number):
        raise SimCfgError(f"{value!r} is not a finite value")
    return number


def _value(spec: dict, key: str, what: str, default=...):
    if not isinstance(spec, dict):
        raise SimCfgError(f"{what} must be a dict of settings")
    if key not in spec or spec[key] in (None, ""):
        if default is ...:
            raise SimCfgError(f"{what} needs {key!r}")
        return default
    return spice_number(spec[key])


def _given(spec: dict, key: str, number):
    """What the caller wrote, when it was a SPICE string, else the number."""
    raw = spec.get(key)
    return raw.strip() if isinstance(raw, str) and raw.strip() else number


def _positive(value: float, what: str) -> None:
    if not value > 0:
        raise SimCfgError(f"{what} must be greater than zero")


def _outputs(prefix: str, exprs: list) -> dict:
    out = {}
    for i, expr in enumerate(exprs):
        out[f"{prefix}_VariableEnabled{i}"] = "True"
        out[f"{prefix}_Variable{i}"] = expr
    return out
