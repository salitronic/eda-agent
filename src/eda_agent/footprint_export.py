# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint as Altium exports it: its 3D bodies and embedded model files.

``library.footprint_export`` has Altium write one footprint of a .PcbLib
as its own JSON export, unsaved edits included. The export carries what
no script getter returns: each 3D body's model placement (its 3D
rotations and Z offset) and the original file of every embedded model the
footprint uses, as Altium stored it (measured on AD 26.11.1.5: the STEP
came out byte for byte the file that was embedded, where the model's own
STEP export is Altium's re-encoding). The export is read here and deleted;
the library is never opened.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from .units import MM_PER_MIL


class ExportFailed(ValueError):
    """Altium did not export the footprint; the message says why."""


def _mils(text: str) -> Optional[float]:
    m = re.fullmatch(r"\s*(-?[\d.]+(?:E[-+]?\d+)?)\s*(mil|mm)?\s*", text or "", re.I)
    if not m:
        return None
    value = float(m.group(1))
    return value / MM_PER_MIL if (m.group(2) or "").lower() == "mm" else value


def _num(text: Any) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


async def read_export(bridge, library_path: str, footprint: str) -> dict[str, Any]:
    """Altium's JSON export of one footprint, read and deleted."""
    out = Path(bridge.config.workspace_dir) / f"footprint_export_{uuid.uuid4().hex}.json"
    try:
        reply = await bridge.send_command_async(
            "library.footprint_export",
            {"library_path": str(library_path), "footprint_name": footprint,
             "out_path": str(out)}, timeout=120.0) or {}
        if not reply.get("written"):
            raise ExportFailed(reply.get("problem") or "Altium wrote no export")
        try:
            data = json.loads(out.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ExportFailed(f"the export could not be read: {exc}") from exc
    finally:
        out.unlink(missing_ok=True)
    if not isinstance(data, dict) or not isinstance(data.get("Primitives"), list):
        raise ExportFailed("the export has no primitives")
    return data


def body_models(export: dict[str, Any]) -> list[dict[str, Any]]:
    """Each 3D body of the footprint, in the export's order: its model file,
    model type, 3D rotations in degrees and Z offset in mils."""
    out = []
    for prim in export.get("Primitives") or []:
        if not isinstance(prim, dict) or prim.get("TYPE") != "ComponentBody":
            continue
        out.append({
            "model_file": prim.get("MODEL.NAME", ""),
            "model_type": int(_num(prim.get("MODEL.MODELTYPE"))),
            "embedded": str(prim.get("MODEL.EMBED", "")).upper() == "TRUE",
            "rotation_deg": [_num(prim.get("MODEL.3D.ROTX")), _num(prim.get("MODEL.3D.ROTY")),
                             _num(prim.get("MODEL.3D.ROTZ"))],
            "model_z_offset_mils": _mils(str(prim.get("MODEL.3D.DZ", ""))),
        })
    return out


def model_files(export: dict[str, Any]) -> list[dict[str, Any]]:
    """The embedded model files the export carries: name, Altium's checksum
    and the bytes (None when they could not be decoded). An entry with no
    file name is the shape Altium generates for an extruded body, not a
    model file, and is left out (measured on AD 26.11.1.5)."""
    out = []
    for entry in export.get("StepFiles") or []:
        if not isinstance(entry, dict) or not str(entry.get("FILENAME") or "").strip():
            continue
        text = re.sub(r"\s+", "", str(entry.get("FILE") or ""))
        try:
            data: Optional[bytes] = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            data = None
        out.append({"name": str(entry.get("FILENAME") or ""),
                    "checksum": entry.get("CHECKSUM"), "data": data})
    return out


def _file_name(name: str) -> str:
    """A model's file name, lower case: the export names a model embedded
    from a file by that file's full path in one place and by its file name
    in another."""
    return re.split(r"[\\/]", str(name or ""))[-1].strip().lower()


def _safe_name(name: str, index: int) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).name).strip(" .")
    return stem or f"model_{index}.step"


async def extract_models(bridge, library_path: str, out_dir: str | Path, *,
                         footprints: list[str], names: Optional[list[str]] = None,
                         overwrite: bool = False,
                         per_footprint_directory: bool = False) -> dict[str, Any]:
    """Write the original files of the embedded models the footprints use.

    ``names`` limits it to those model file names (case-insensitive; a
    full path counts by its file name). An
    existing file is kept unless ``overwrite``; ``per_footprint_directory``
    puts each footprint's models in a folder of its own, so two footprints'
    models of the same name do not meet.
    """
    out = Path(out_dir)
    wanted = {_file_name(n) for n in names} if names else None
    written: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    found: set[str] = set()
    done: set[tuple[str, str]] = set()
    for footprint in footprints:
        try:
            export = await read_export(bridge, library_path, footprint)
        except ExportFailed as exc:
            refused.append({"footprint": footprint, "reason": str(exc)})
            continue
        bodies = {_file_name(b["model_file"]): b for b in body_models(export)}
        for i, model in enumerate(model_files(export)):
            name = model["name"]
            file_name = _file_name(name)
            if wanted is not None and file_name not in wanted:
                continue
            found.add(file_name)
            info = {"name": name, "footprint": footprint, "checksum": model["checksum"],
                    "rotation_deg": (bodies.get(file_name) or {}).get("rotation_deg")}
            data = model["data"]
            if not data:
                refused.append({**info, "reason": "the export's copy of the file "
                                "could not be decoded"})
                continue
            folder = out / _safe_name(footprint, i) if per_footprint_directory else out
            target = folder / _safe_name(name, i)
            key = (str(folder).lower(), target.name.lower())
            if key in done:
                continue
            if target.exists() and not overwrite:
                refused.append({**info, "reason": f"{target} exists; pass overwrite"})
                continue
            folder.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            done.add(key)
            is_step = name.lower().endswith((".step", ".stp"))
            written.append({**info, "path": str(target), "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "step_header": data.startswith(b"ISO-10303-21") if is_step else None})
    missing = sorted(wanted - found) if wanted is not None else []
    return {"ok": bool(written) and not refused and not missing,
            "library": str(library_path), "written": written, "refused": refused,
            "not_found": missing}


__all__ = ["ExportFailed", "read_export", "body_models", "model_files", "extract_models"]
