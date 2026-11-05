# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Clone an OutJob into another project, keeping its owner's export style.

Altium's scripting can create an empty OutJob and run one, but cannot add
an output to it, so a project without an OutJob could not produce
Gerbers, drill files or a pick-and-place from this server. Every user
exports differently, so nothing here invents outputs: an existing OutJob,
the user's own or one Altium ships, is copied and pointed at the target
project. Approved by George 2026-10-23 as the third exception to the rule
that design files are reached only through the bridge. It writes a NEW
file and never edits an existing one.

What changes in the copy, and nothing else:

* each output's document (``OutputDocumentPathN``) is pointed at the
  target project's document of the same kind, when there is exactly one;
  anything ambiguous is left and reported, never guessed;
* each container writes to ``output_dir`` and is not release managed,
  since a release-managed container writes nothing outside a release
  (measured 2026-10-17);
* the vault identity in ``[OutputJobFile]`` is cleared, so the copy is
  not taken for the released item it came from.

Bytes outside those lines are kept as they were: encoding, byte-order
mark and line endings included.
"""
from __future__ import annotations

import os
import re
from pathlib import PureWindowsPath
from typing import Any, Iterable

_HEADER = re.compile(r"^\[(?P<name>[^\]\r\n]+)\]\s*$")
_KEY = re.compile(r"^(?P<key>[A-Za-z_]+?)(?P<n>\d+)=(?P<value>.*)$")
_DOC_PATH = re.compile(r"^OutputDocumentPath(?P<n>\d+)=(?P<value>.*)$")
_VAULT_KEYS = ("VaultGUID", "ItemGUID", "ItemHRID", "RevisionGUID",
               "RevisionId", "VaultHRID", "AutoItemHRID", "NextRevId",
               "FolderGUID", "LifeCycleDefinitionGUID",
               "RevisionNamingSchemeGUID")
_CONTAINER_SECTIONS = ("publishsettings", "generatedfilessettings")
#: Keys that hold where a container writes. A value naming a file keeps
#: its file name; one naming a folder becomes output_dir.
_WHERE_KEYS = ("OutputFilePath", "RelativeOutputPath")


def _split(data: bytes) -> tuple[bytes, list[str], str]:
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    body = data[len(bom):]
    try:
        text, encoding = body.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = body.decode("cp1252"), "cp1252"
    return bom, text.splitlines(keepends=True), encoding


def _eol(line: str) -> tuple[str, str]:
    stripped = line.rstrip("\r\n")
    return stripped, line[len(stripped):]


def _is_folder(value: str) -> bool:
    return not value or value.endswith(("\\", "/")) or not PureWindowsPath(value).suffix


#: OutJob layer codes of the copper layers: top, mid 1..30, bottom, and
#: internal planes 1..16 (Altium's V7 layer ids).
_V7_TOP, _V7_BOTTOM, _V7_PLANE0 = 16777217, 16842751, 16842752
_PLOT_SET = re.compile(r"(Plot\.Set=SerializeLayerHash\.Version~2,ClassName~TLayerToBoolean)"
                       r"((?:,\d+~\d+)*)")


def copper_layer_codes(layer_ids: Iterable[int]) -> list[int]:
    """OutJob layer codes for a board's copper layers, from their layer ids
    (pcb.get_layer_stackup ``layer_id``: 1 top, 2..31 mid, 32 bottom,
    39..54 internal planes). Ids of other layers are dropped."""
    codes = []
    for lid in layer_ids:
        if lid == 1:
            codes.append(_V7_TOP)
        elif 2 <= lid <= 31:
            codes.append(_V7_TOP + lid - 1)
        elif lid == 32:
            codes.append(_V7_BOTTOM)
        elif 39 <= lid <= 54:
            codes.append(_V7_PLANE0 + lid - 38)
    return codes


def _is_copper_code(code: int) -> bool:
    return _V7_TOP <= code <= _V7_BOTTOM or _V7_PLANE0 < code <= _V7_PLANE0 + 16


def _plot_copper(text: str, codes: list[int]) -> str:
    """``text`` with the copper entries of each layer set it holds replaced
    by ``codes``, in the place of the first copper entry; a set with no
    copper entry is left as it was."""
    def fix(m: re.Match) -> str:
        entries = [e for e in m.group(2).split(",") if e]
        if not any(_is_copper_code(int(e.split("~")[0])) for e in entries):
            return m.group(0)
        kept, placed = [], False
        for e in entries:
            if _is_copper_code(int(e.split("~")[0])):
                if not placed:
                    kept.extend(f"{c}~1" for c in codes)
                    placed = True
                continue
            kept.append(e)
        return m.group(1) + "".join("," + e for e in kept)
    return _PLOT_SET.sub(fix, text)


def clone(data: bytes, project_path: str, documents: Iterable[str],
          output_dir: str, copper_codes: Iterable[int] = ()) -> tuple[bytes, dict[str, Any]]:
    """The OutJob ``data`` pointed at another project.

    Args:
        data: the source .OutJob's bytes.
        project_path: the target .PrjPcb.
        documents: the target project's document paths.
        output_dir: absolute folder every container writes into. One
            inside the project is written relative to it.
        copper_codes: the target board's copper layers as OutJob layer
            codes (copper_layer_codes). Given, every layer set that plots
            copper plots exactly these: a template made for four signal
            layers plotted none of a board's internal planes.

    Returns:
        The new bytes and a report: ``retargeted`` (output number, old,
        new), ``unmapped`` (output number, old, why), ``containers`` and
        ``copper_layer_sets`` (how many layer sets were set to the board).
    """
    codes = list(copper_codes)
    copper_sets = 0
    bom, lines, encoding = _split(data)
    project_dir = os.path.dirname(project_path)
    # RELATIVE TO THE PROJECT when the folder is inside it, as Altium's own
    # "Project Outputs for <project>" is written. Absolute, a project copied
    # elsewhere (the 07d take worked on a copy of 07b) went on writing into
    # the original's folder, and the agent edited the OutJob by hand.
    where = output_dir
    try:
        rel = os.path.relpath(output_dir, project_dir)
        if not rel.startswith("..") and not os.path.isabs(rel):
            where = rel
    except ValueError:                      # another drive
        pass
    by_ext: dict[str, list[str]] = {}
    for doc in documents:
        by_ext.setdefault(PureWindowsPath(doc).suffix.lower(), []).append(doc)

    retargeted, unmapped, containers = [], [], set()
    section = ""
    out = []
    for line in lines:
        text, eol = _eol(line)
        head = _HEADER.match(text)
        if head:
            section = head.group("name").lower()
            out.append(line)
            continue

        if section == "outputjobfile":
            key = text.split("=", 1)[0]
            if key in _VAULT_KEYS and "=" in text:
                out.append(f"{key}={eol}")
                continue

        doc = _DOC_PATH.match(text)
        if doc and section.startswith("outputgroup"):
            old = doc.group("value")
            if old:
                matches = by_ext.get(PureWindowsPath(old).suffix.lower(), [])
                if len(matches) == 1:
                    new = os.path.relpath(matches[0], project_dir)
                    retargeted.append({"output": int(doc.group("n")),
                                       "was": old, "now": new})
                    out.append(f"OutputDocumentPath{doc.group('n')}={new}{eol}")
                    continue
                unmapped.append({"output": int(doc.group("n")), "was": old,
                                 "why": ("the project has no document of that "
                                         "kind" if not matches else
                                         f"the project has {len(matches)} of "
                                         "that kind; pick one in Altium")})
            out.append(line)
            continue

        if codes and section.startswith("outputgroup") and "Plot.Set=" in text:
            fixed = _plot_copper(text, codes)
            if fixed != text:
                copper_sets += 1
                out.append(fixed + eol)
                continue

        keyed = _KEY.match(text)
        if keyed and section in _CONTAINER_SECTIONS:
            key, n, value = keyed.group("key"), keyed.group("n"), keyed.group("value")
            containers.add(int(n))
            if key == "ReleaseManaged":
                out.append(f"ReleaseManaged{n}=0{eol}")
                continue
            if key == "OutputBasePath":
                out.append(f"OutputBasePath{n}={where}{eol}")
                continue
            if key in _WHERE_KEYS:
                if _is_folder(value):
                    new = where.rstrip("\\/") + "\\"
                else:
                    new = str(PureWindowsPath(where) / PureWindowsPath(value).name)
                out.append(f"{key}{n}={new}{eol}")
                continue
        out.append(line)

    report = {"retargeted": retargeted, "unmapped": unmapped,
              "containers": sorted(containers), "copper_layer_sets": copper_sets}
    return bom + "".join(out).encode(encoding), report


def outputs(data: bytes) -> list[dict[str, Any]]:
    """Every output in an OutJob: number, type, name, enabled."""
    _bom, lines, _enc = _split(data)
    found: dict[int, dict[str, Any]] = {}
    for line in lines:
        text, _eol_ = _eol(line)
        keyed = _KEY.match(text)
        if not keyed:
            continue
        key, n = keyed.group("key"), int(keyed.group("n"))
        if key in ("OutputType", "OutputName", "OutputEnabled") \
                and not text.startswith(f"{key}{n}_"):
            found.setdefault(n, {"output": n})[key] = keyed.group("value")
    return [{"output": n, "type": o.get("OutputType", ""),
             "name": o.get("OutputName", ""),
             "enabled": o.get("OutputEnabled") == "1"}
            for n, o in sorted(found.items())]
