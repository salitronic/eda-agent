# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The component names and descriptions in a .SchLib, for the library search.

An Altium ``.SchLib`` is an OLE compound document with one storage per
component (``<CompName>/Data``), whose first record is an ASCII
``|KEY=VALUE|`` header (``LibReference``, ``ComponentDescription``). Only
that header is read; the rest of each stream is not parsed, and the file is
never written.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

_NON_COMPONENT_STREAMS = {"Storage", "FileHeader"}


def read_schlib_components(path: str | Path) -> list[dict[str, Any]]:
    """Return each library component's header ``{name, lib_reference,
    description}``.

    Enumerates the per-component storages and parses the first (ASCII)
    record of each ``Data`` stream. Raises ValueError if the file is not a
    valid SchLib OLE container.
    """
    import olefile

    path = str(path)
    if not olefile.isOleFile(path):
        raise ValueError(f"not an OLE compound file (not a .SchLib?): {path}")
    ole = olefile.OleFileIO(path)
    try:
        comp_storages = sorted(
            entry[0] for entry in ole.listdir()
            if len(entry) == 2 and entry[1] == "Data"
            and entry[0] not in _NON_COMPONENT_STREAMS
        )
        out: list[dict[str, Any]] = []
        for name in comp_storages:
            data = ole.openstream([name, "Data"]).read()
            hdr = _parse_first_record(data)
            out.append({
                "name": name,
                "lib_reference": hdr.get("LibReference", ""),
                "description": hdr.get("ComponentDescription", ""),
            })
        return out
    finally:
        ole.close()


def _parse_first_record(data: bytes) -> dict[str, str]:
    if len(data) < 4:
        return {}
    length = struct.unpack("<I", data[:4])[0]
    payload = data[4:4 + length].rstrip(b"\x00")
    text = payload.decode("latin-1", "replace")
    fields: dict[str, str] = {}
    for part in text.split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields
