# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium's IPC footprint batch templates: where they are, the package
families they cover, and the columns and legend of each.

Altium installs one blank .xls per package family under
``%ProgramData%\\Altium\\<install>\\Extensions\\IPC Footprint Generator\\
Templates``. A template's Data sheet holds the family's code in A1 (what a
batch table's own A1 must say, e.g. "QFP" in the PQFP file) and the column
names in row 2; its Legend sheets say which columns are required and what
each measures.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_SUBDIR = Path("Extensions") / "IPC Footprint Generator" / "Templates"


def template_folders(root: Optional[Path] = None) -> list[Path]:
    """Every installed template folder, the most recently changed first."""
    base = root or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Altium"
    if not base.is_dir():
        return []
    found = [p / _SUBDIR for p in base.iterdir()
             if p.is_dir() and (p / _SUBDIR).is_dir()]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _cells(sheet, row: int) -> list[str]:
    return [str(c.value).strip() for c in sheet.row(row)] if sheet.nrows > row else []


@lru_cache(maxsize=128)
def _read(path: str, stamp: float) -> dict[str, Any]:
    import xlrd
    book = xlrd.open_workbook(path)
    names = book.sheet_names()
    data = book.sheet_by_name("Data") if "Data" in names else book.sheet_by_index(0)
    family = _cells(data, 0)[0] if data.nrows else ""
    columns = [c for c in _cells(data, 1) if c]
    legend = []
    for name in names:
        if not name.lower().startswith("legend"):
            continue
        sheet = book.sheet_by_name(name)
        head = [h.lower() for h in _cells(sheet, 0)]
        col = {key: next((i for i, h in enumerate(head) if h.startswith(key)), None)
               for key in ("name", "value required", "format", "description")}
        if col["name"] is None:
            continue
        for r in range(1, sheet.nrows):
            row = _cells(sheet, r)
            pick = lambda k: row[col[k]] if col[k] is not None and col[k] < len(row) else ""  # noqa: E731
            if not pick("name"):
                continue
            legend.append({"name": pick("name"), "required": pick("value required"),
                           "format": pick("format"), "description": pick("description"),
                           "sheet": name})
    return {"family": family, "file": path, "columns": columns, "legend": legend}


def read_template(path: Path) -> dict[str, Any]:
    """A template's family code, column names and legend rows."""
    return dict(_read(str(path), path.stat().st_mtime))


def families(folder: Path) -> list[dict[str, Any]]:
    """Each template in a folder: its family code, file and column count."""
    out = []
    for path in sorted(folder.glob("*.xls")):
        try:
            t = read_template(path)
        except Exception as exc:                          # noqa: BLE001
            out.append({"family": "", "file": str(path), "error": str(exc)})
            continue
        out.append({"family": t["family"], "file": str(path),
                    "columns": len(t["columns"])})
    return out


def find_template(folder: Path, name: str) -> Optional[dict[str, Any]]:
    """The template whose family code is ``name`` (case aside), else the
    one whose file name starts with it."""
    want = name.strip().lower()
    rows = [r for r in families(folder) if not r.get("error")]
    hit = next((r for r in rows if r["family"].lower() == want), None) \
        or next((r for r in rows if Path(r["file"]).stem.lower().startswith(want)), None)
    return read_template(Path(hit["file"])) if hit else None


def required_columns(template: dict[str, Any]) -> list[str]:
    """Package columns the legend marks required; "Dmin, Dmax" names two.

    Only the package legend counts: the footprint legend's columns are
    the pad geometry the generator works out, given only to override it.
    """
    out: list[str] = []
    for row in template["legend"]:
        if "footprint" in row["sheet"].lower():
            continue
        if row["required"].strip().lower().startswith("yes"):
            for part in re.split(r"[,/]", row["name"]):
                part = part.strip()
                if part in template["columns"] and part not in out:
                    out.append(part)
    return out


def table_problems(rows: list[list[str]], template: Optional[dict[str, Any]],
                   known_families: set[str]) -> tuple[list[str], int]:
    """What is wrong with a batch table read as rows of cells, and how
    many footprints it holds. ``template`` is the family's, when found."""
    if len(rows) < 3 or not rows[0] or not rows[0][0].strip():
        return ["the table needs the family in A1, the column names in row 2 "
                "and one footprint per row after that"], 0
    family = rows[0][0].strip()
    if known_families and family.lower() not in {f.lower() for f in known_families}:
        return [f"A1 says {family!r}; the installed templates' families are "
                + ", ".join(sorted(known_families))], 0
    header = [h.strip() for h in rows[1]]
    named = [h for h in header if h]
    data = [r for r in rows[2:] if any(c.strip() for c in r)]
    problems = []
    if not data:
        problems.append("the table has no footprint rows")
    if any(len(r) > len(header) and any(c.strip() for c in r[len(header):]) for r in data):
        problems.append("a row has values beyond the named columns")
    if template:
        unknown = [h for h in named if h not in template["columns"]]
        if unknown:
            problems.append(f"{family} has no columns {unknown}")
        for col in required_columns(template):
            if col not in named:
                problems.append(f"required column {col} is missing")
            else:
                i = header.index(col)
                empty = [n + 3 for n, r in enumerate(data) if i >= len(r) or not r[i].strip()]
                if empty:
                    problems.append(f"required column {col} is empty on row(s) {empty}")
    if "DensityLevel" in header:
        i = header.index("DensityLevel")
        bad = [n + 3 for n, r in enumerate(data)
               if i < len(r) and r[i].strip() and r[i].strip().upper() not in {"L", "N", "M"}]
        if bad:
            problems.append(f"DensityLevel is L, N or M (row(s) {bad})")
    return problems, len(data)
