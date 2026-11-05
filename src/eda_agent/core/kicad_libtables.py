# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad's library tables: which nickname means which library file.

A project's ``sym-lib-table`` and ``fp-lib-table`` sit beside its
``.kicad_pro``; the user's global ones in KiCad's settings folder. A row
of type "Table" names another table to read in (KiCad 10's global tables
point at the bundled one this way). URIs use ``${VAR}`` paths, expanded
from the environment, the KiCad install and ``${KIPRJMOD}``.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from ..libimport.kicad.sexpr import loads
from .kicad_sexpr_edit import (cut, find_children, forms, insert_before_close, parse, q,
                               read_file, write_file)

KINDS = {"symbol": "sym-lib-table", "footprint": "fp-lib-table"}


def settings_dir(version: str = "10.0") -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "kicad", version)


def install_root(cli_path: Optional[str]) -> str:
    """KiCad's install folder from the kicad-cli path (``<root>/bin``)."""
    return os.path.dirname(os.path.dirname(cli_path)) if cli_path else ""


def env_vars(project_dir: str = "", cli_path: Optional[str] = None) -> dict[str, str]:
    share = os.path.join(install_root(cli_path), "share", "kicad") if cli_path else ""
    out: dict[str, str] = {}
    if share:
        for ver in ("", "6", "7", "8", "9", "10"):
            out[f"KICAD{ver}_SYMBOL_DIR"] = os.path.join(share, "symbols")
            out[f"KICAD{ver}_FOOTPRINT_DIR"] = os.path.join(share, "footprints")
            out[f"KICAD{ver}_3DMODEL_DIR"] = os.path.join(share, "3dmodels")
            out[f"KICAD{ver}_TEMPLATE_DIR"] = os.path.join(share, "template")
    out.update({k: v for k, v in os.environ.items() if k.startswith("KICAD")})
    if project_dir:
        out["KIPRJMOD"] = project_dir
    return out


def expand(uri: str, variables: dict[str, str]) -> str:
    def sub(m):
        return variables.get(m.group(1), m.group(0))
    return os.path.normpath(re.sub(r"\$\{([^}]+)\}", sub, uri))


def table_path(kind: str, scope: str, project_dir: str = "") -> str:
    name = KINDS[kind]
    if scope == "project":
        if not project_dir:
            raise ValueError("no project folder to hold a project library table")
        return os.path.join(project_dir, name)
    return os.path.join(settings_dir(), name)


def read_table(path: str, variables: dict[str, str], scope: str,
               depth: int = 0) -> list[dict[str, str]]:
    if not os.path.isfile(path) or depth > 3:
        return []
    tree = loads(read_file(path))
    rows = []
    for lib in tree[1:]:
        if not (isinstance(lib, list) and lib and lib[0] == "lib"):
            continue
        row = {k[0]: k[1] for k in lib[1:] if isinstance(k, list) and len(k) > 1}
        uri = expand(row.get("uri", ""), variables)
        if row.get("type") == "Table":
            rows += read_table(uri, variables, scope, depth + 1)
            continue
        rows.append({"nickname": row.get("name", ""), "uri": row.get("uri", ""), "path": uri,
                     "type": row.get("type", ""), "description": row.get("descr", ""),
                     "scope": scope, "disabled": any(isinstance(k, list) and k[0] == "disabled"
                                                     for k in lib[1:])})
    return rows


def libraries(kind: str, project_dir: str = "", cli_path: Optional[str] = None
              ) -> list[dict[str, str]]:
    """Every library of a kind the project sees; project rows first, as
    KiCad resolves a nickname found in both from the project."""
    variables = env_vars(project_dir, cli_path)
    rows = []
    if project_dir:
        rows += read_table(table_path(kind, "project", project_dir), variables, "project")
    rows += read_table(table_path(kind, "global"), variables, "global")
    return rows


def resolve(kind: str, nickname: str, project_dir: str = "",
            cli_path: Optional[str] = None) -> Optional[str]:
    for row in libraries(kind, project_dir, cli_path):
        if row["nickname"] == nickname and not row["disabled"]:
            return row["path"]
    return None


def add_row(kind: str, scope: str, nickname: str, uri: str, project_dir: str = "",
            description: str = "") -> str:
    """Add a library row to a table (made if missing). Returns the table path."""
    path = table_path(kind, scope, project_dir)
    root = "sym_lib_table" if kind == "symbol" else "fp_lib_table"
    if not os.path.isfile(path):
        write_file(path, f"({root}\n\t(version 7)\n)\n")
    text = read_file(path)
    span = forms(text)[0]
    for lib in find_children(text, span, "lib"):
        node = parse(text, lib)
        name = next((k[1] for k in node[1:] if isinstance(k, list) and k[0] == "name"), "")
        if name == nickname:
            raise ValueError(f"{nickname} is already in {path}")
    lib_type = "KiCad"
    row = (f"(lib (name {q(nickname)}) (type {q(lib_type)}) (uri {q(uri)}) "
           f"(options \"\") (descr {q(description)}))")
    write_file(path, insert_before_close(text, span, row))
    return path


def remove_row(kind: str, scope: str, nickname: str, project_dir: str = "") -> str:
    path = table_path(kind, scope, project_dir)
    if not os.path.isfile(path):
        raise ValueError(f"no library table at {path}")
    text = read_file(path)
    span = forms(text)[0]
    for lib in find_children(text, span, "lib"):
        node = parse(text, lib)
        name = next((k[1] for k in node[1:] if isinstance(k, list) and k[0] == "name"), "")
        if name == nickname:
            write_file(path, cut(text, lib))
            return path
    raise ValueError(f"{nickname} is not in {path}")


__all__ = ["libraries", "resolve", "add_row", "remove_row", "env_vars", "expand",
           "table_path", "settings_dir"]
