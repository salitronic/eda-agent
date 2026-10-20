# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The original 3D model files embedded in a .PcbLib, byte for byte.

READ ONLY, and the one approved exception to driving libraries through
Altium (George, 2026-10-22): no Altium API saves an embedded model
(IPCB_Model has no save, and PCB:ExportSTEP3D in a PcbLib writes
nothing), and a board STEP export is Altium's re-encoding, not the
vendor's file. The library is read into memory and never opened for
writing. The one other thing parsed is each 3D body's model placement
(``saved_body_models``): a STEP model's Z offset and 3D rotations have
no script getter, and ``lib_set_3d_body`` needs them.

A PcbLib keeps its embedded models in ``Library/Models``: ``Data`` is
one record per model (a 4-byte length, then ``KEY=VALUE|`` text naming
the file, its ID, its rotation and Altium's CHECKSUM), and ``<n>`` is
the n-th model's file, zlib-compressed.

PROOF OF ORIGINAL: Altium stores a checksum of each model it embeds. A
file is written only when the checksum of the extracted bytes equals
it, so what comes out is what Altium took in. The checksum weighs each
byte by its position (1, 1, 2, 3, ...) and wraps at 32 bits; Altium
stores the same bits signed. Measured equal on every model of a real
library (four STEP models, two of them with negative stored values).

It reads the SAVED file: a model added in Altium and not yet saved is
not there.
"""
from __future__ import annotations

import hashlib
import io
import re
import struct
import zlib
from pathlib import Path
from typing import Any, Optional

from ..units import MM_PER_MIL

_DATA = "Library/Models/Data"


def model_checksum(data: bytes) -> int:
    """Altium's checksum of a model file, as the signed 32-bit value it
    stores in the model record."""
    total = 0
    for i, byte in enumerate(data):
        total = (total + byte * (i if i else 1)) & 0xFFFFFFFF
    return total - (1 << 32) if total & 0x80000000 else total


def _open(path: Path):
    import olefile

    raw = path.read_bytes()                  # never a writable handle
    if not olefile.isOleFile(io.BytesIO(raw)):
        raise ValueError(f"not an Altium library file: {path}")
    return olefile.OleFileIO(io.BytesIO(raw))


def _records(ole) -> list[dict[str, str]]:
    if not ole.exists(_DATA):
        return []
    data = ole.openstream(_DATA).read()
    out, at = [], 0
    while at + 4 <= len(data):
        size = struct.unpack("<I", data[at:at + 4])[0]
        text = data[at + 4:at + 4 + size].rstrip(b"\0").decode("latin-1")
        at += 4 + size
        out.append({k.strip().upper(): v.strip() for k, v in
                    (part.split("=", 1) for part in text.split("|") if "=" in part)})
    return out


def _summary(index: int, rec: dict[str, str], ole) -> dict[str, Any]:
    def num(key: str) -> Optional[float]:
        try:
            return float(rec[key])
        except (KeyError, ValueError):
            return None
    stream = f"Library/Models/{index}"
    return {"index": index, "name": rec.get("NAME", ""), "id": rec.get("ID", ""),
            "embedded": rec.get("EMBED", "").upper() == "TRUE",
            "has_data": ole.exists(stream),
            "checksum": int(rec["CHECKSUM"]) if re.fullmatch(r"-?\d+", rec.get("CHECKSUM", "")) else None,
            "rotation_deg": [num("ROTX"), num("ROTY"), num("ROTZ")]}


def list_embedded_models(library_path: str | Path) -> list[dict[str, Any]]:
    """Every model record in the library, with whether its file is there."""
    ole = _open(Path(library_path))
    try:
        return [_summary(i, rec, ole) for i, rec in enumerate(_records(ole))]
    finally:
        ole.close()


def _safe_name(name: str, index: int) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).name).strip(" .")
    return stem or f"model_{index}.step"


def extract_embedded_models(library_path: str | Path, out_dir: str | Path, *,
                            names: Optional[list[str]] = None,
                            overwrite: bool = False) -> dict[str, Any]:
    """Write the original files of the library's embedded models.

    ``names`` limits it to those model file names (case-insensitive);
    every embedded model otherwise. A file is written only when its
    checksum matches Altium's record; an existing file is kept unless
    ``overwrite``.
    """
    path = Path(library_path)
    out = Path(out_dir)
    wanted = {n.lower() for n in names} if names else None
    ole = _open(path)
    written, refused = [], []
    try:
        records = _records(ole)
        found = set()
        for i, rec in enumerate(records):
            info = _summary(i, rec, ole)
            if wanted is not None and info["name"].lower() not in wanted:
                continue
            found.add(info["name"].lower())
            if not info["has_data"]:
                refused.append({**info, "reason": "no embedded file for this "
                                "record (a linked model, not an embedded one)"})
                continue
            try:
                data = zlib.decompress(ole.openstream(f"Library/Models/{i}").read())
            except zlib.error as exc:
                refused.append({**info, "reason": f"cannot decompress: {exc}"})
                continue
            got = model_checksum(data)
            if info["checksum"] is None or got != info["checksum"]:
                refused.append({**info, "reason": f"checksum {got} does not match "
                                f"Altium's {info['checksum']}; not written"})
                continue
            target = out / _safe_name(info["name"], i)
            if target.exists() and not overwrite:
                refused.append({**info, "reason": f"{target} exists; pass overwrite"})
                continue
            out.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            is_step = info["name"].lower().endswith((".step", ".stp"))
            written.append({**info, "path": str(target), "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "checksum_verified": True,
                            "step_header": data.startswith(b"ISO-10303-21") if is_step else None})
    finally:
        ole.close()
    missing = sorted(wanted - found) if wanted is not None else []
    return {"ok": bool(written) and not refused and not missing,
            "library": str(path), "written": written, "refused": refused,
            "not_in_saved_file": missing,
            "note": "read from the saved file; a model added in Altium and "
                    "not saved yet is not in it"}


#: The text of a body record: the model's placement, as Altium saved it.
_BODY_TEXT = re.compile(rb"\|[^\x00]*?MODEL\.3D\.DZ=[^\x00]*")


def _footprint_name(data: bytes) -> str:
    """The name a footprint's ``Data`` stream opens with (a 4-byte length,
    a 1-byte string length, the name). It stays whole when the storage
    name is cut to OLE's 31 characters."""
    if len(data) < 5:
        return ""
    return data[5:5 + data[4]].decode("latin-1")


def _mils(text: str) -> Optional[float]:
    m = re.fullmatch(r"\s*(-?[\d.]+(?:E[-+]?\d+)?)\s*(mil|mm)?\s*", text or "", re.I)
    if not m:
        return None
    value = float(m.group(1))
    return value / MM_PER_MIL if (m.group(2) or "").lower() == "mm" else value


def saved_body_models(library_path: str | Path,
                      footprint_name: str) -> Optional[list[dict[str, Any]]]:
    """Each 3D body of one footprint as the SAVED file has it, in file order.

    For a STEP body the Properties panel's Standoff Height is the model's
    ``MODEL.3D.DZ``, and no script getter reaches it or the model's 3D
    rotations (``IPCB_Model.Rotation`` is the 2D one), so this is where
    they are read. None when the footprint is not in the saved file: new,
    renamed, or not saved yet.
    """
    ole = _open(Path(library_path))
    try:
        for entry in ole.listdir():
            if len(entry) != 2 or entry[1] != "Data":
                continue
            data = ole.openstream(entry).read()
            if _footprint_name(data) != footprint_name:
                continue
            bodies = []
            for m in _BODY_TEXT.finditer(data):
                rec = {k.strip().upper(): v.strip() for k, v in
                       (part.split("=", 1) for part in
                        m.group(0).decode("latin-1").split("|") if "=" in part)}

                def num(key: str) -> float:
                    try:
                        return float(rec.get(key, "0"))
                    except ValueError:
                        return 0.0
                bodies.append({
                    "model_file": rec.get("MODEL.NAME", ""),
                    "model_type": int(num("MODEL.MODELTYPE")),
                    "rotation_deg": [num("MODEL.3D.ROTX"), num("MODEL.3D.ROTY"),
                                     num("MODEL.3D.ROTZ")],
                    "model_z_offset_mils": _mils(rec.get("MODEL.3D.DZ", "")),
                })
            return bodies
        return None
    finally:
        ole.close()


__all__ = ["model_checksum", "list_embedded_models", "extract_embedded_models",
           "saved_body_models"]
