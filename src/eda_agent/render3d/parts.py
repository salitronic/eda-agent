# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The parts on a board, from Altium's STEP export, as triangles.

Altium writes the board as one assembly: the board itself and one child
per part, named by its designator, each part placed where it sits. The
board top is at z = 0. Each solid inside a part carries a colour, and
the models eda-agent builds also name each solid by its role (body,
metal, ceramic, gold, white, lens, pin1), which picks a physical
material; a model from elsewhere is matched by its colour.

The CAD kernel is CadQuery's (OCP), already used to build 3D models.
Triangles are cached beside the STEP by its hash, so an unchanged board
is not converted twice.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..units import MM_PER_MIL

#: Physical materials, linear RGB, by role.
MATERIALS: dict[str, dict[str, Any]] = {
    "metal": {"base": (0.80, 0.80, 0.80), "roughness": 0.28, "metallic": 1.0},
    "gold": {"base": (1.0, 0.77, 0.34), "roughness": 0.22, "metallic": 1.0},
    "copper": {"base": (0.95, 0.64, 0.54), "roughness": 0.3, "metallic": 1.0},
    "body": {"base": (0.018, 0.018, 0.02), "roughness": 0.55, "coat": 0.25},
    "white": {"base": (0.82, 0.82, 0.78), "roughness": 0.45, "coat": 0.4},
    "lens": {"base": (0.97, 0.96, 0.9), "roughness": 0.05, "transmission": 1.0},
    "ceramic": None,      # its own colour, matte
    "pin1": None,         # its own colour, painted
}
ROLE = re.compile(r"^([a-z0-9]+?)(?:_\d+)?$", re.I)


@dataclass
class PartsMesh:
    positions: np.ndarray            # (n, 3) mm, Altium's frame
    normals: np.ndarray              # (n, 3)
    indices: np.ndarray              # (m, 3)
    material_keys: list[str]         # one per triangle group, see groups
    groups: np.ndarray               # (m,) index into material_keys
    materials: dict[str, dict[str, Any]]
    designators: list[str] = field(default_factory=list)


def available() -> bool:
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return True


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def material_for(role: str, colour: Optional[tuple[float, float, float]]) -> dict[str, Any]:
    """A physical material from a solid's role, else from its colour.

    STEP colours are written in sRGB terms by nearly every tool; they
    are made linear here.
    """
    role = (role or "").lower()
    lin = tuple(_srgb_to_linear(c) for c in colour) if colour else None
    if role in MATERIALS and MATERIALS[role] is not None:
        return dict(MATERIALS[role])
    if role in ("ceramic", "pin1") and lin:
        return {"base": lin, "roughness": 0.6 if role == "ceramic" else 0.5}
    if colour is None:
        return {"base": (0.5, 0.5, 0.5), "roughness": 0.5}
    r, g, b = colour
    spread = max(r, g, b) - min(r, g, b)
    # Untinted mid greys are almost always plated metal in vendor models.
    if spread < 0.06 and 0.45 <= max(r, g, b) <= 0.9:
        return dict(MATERIALS["metal"])
    if r > 0.6 and g > 0.4 and b < 0.25 and r > g > b:
        return dict(MATERIALS["gold"])
    if max(r, g, b) < 0.12:
        return dict(MATERIALS["body"])
    return {"base": lin, "roughness": 0.5}


def cache_path(step: Path, deflection: float = 0.01) -> Path:
    """Where the triangles of this STEP are kept, by its content."""
    h = hashlib.sha1()
    with open(step, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    h.update(f"{deflection}:v2".encode())
    return step.with_name(step.stem + f".{h.hexdigest()[:16]}.mesh.npz")


def load(step_path: str, deflection_mm: float = 0.01, skip: tuple[str, ...] = ("Board",),
         cache: bool = True) -> PartsMesh:
    """Every part in the STEP as triangles, in Altium's frame (mm)."""
    step = Path(step_path)
    cpath = cache_path(step, deflection_mm) if cache else None
    if cpath and cpath.exists():
        z = np.load(cpath, allow_pickle=False)
        keys = [str(k) for k in z["keys"]]
        mats = {k: _material_from_row(row) for k, row in zip(keys, z["mats"])}
        return PartsMesh(z["positions"], z["normals"], z["indices"], keys, z["groups"], mats,
                         [str(d) for d in z["designators"]])
    mesh = _convert(step, deflection_mm, skip)
    if cpath:
        rows = np.array([_material_row(mesh.materials[k]) for k in mesh.material_keys], "<f4")
        np.savez_compressed(cpath, positions=mesh.positions, normals=mesh.normals,
                            indices=mesh.indices, groups=mesh.groups,
                            keys=np.array(mesh.material_keys), mats=rows,
                            designators=np.array(mesh.designators))
    return mesh


def _material_row(m: dict[str, Any]) -> list[float]:
    return [*m.get("base", (0.5, 0.5, 0.5)), m.get("roughness", 0.5), m.get("metallic", 0.0),
            m.get("coat", 0.0), m.get("transmission", 0.0)]


def _material_from_row(row) -> dict[str, Any]:
    return {"base": tuple(float(v) for v in row[:3]), "roughness": float(row[3]),
            "metallic": float(row[4]), "coat": float(row[5]), "transmission": float(row[6])}


def _convert(step: Path, deflection: float, skip: tuple[str, ...]) -> PartsMesh:
    from OCP.BRep import BRep_Tool
    from OCP.BRepLib import BRepLib_ToolTriangulatedShape
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Quantity import Quantity_ColorRGBA
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.XCAFDoc import XCAFDoc_ColorTool, XCAFDoc_ColorType, XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("render"))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    if reader.ReadFile(str(step)) != IFSelect_RetDone:
        raise ValueError(f"the STEP file could not be read: {step}")
    reader.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ctool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def name(lab) -> str:
        n = TDataStd_Name()
        return n.Get().ToExtString() if lab.FindAttribute(TDataStd_Name.GetID_s(), n) else ""

    def colour(lab=None, shape=None):
        c = Quantity_ColorRGBA()
        for t in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
            if lab is not None and XCAFDoc_ColorTool.GetColor_s(lab, t, c):
                break
            if shape is not None and ctool.GetColor(shape, t, c):
                break
        else:
            return None
        rgb = c.GetRGB()
        return (rgb.Red(), rgb.Green(), rgb.Blue())

    pos: list[np.ndarray] = []
    nrm: list[np.ndarray] = []
    idx: list[np.ndarray] = []
    grp: list[np.ndarray] = []
    keys: list[str] = []
    mats: dict[str, dict[str, Any]] = {}
    count = [0]
    designators: list[str] = []

    def key_for(role: str, col) -> str:
        m = material_for(role, col)
        k = f"{role or '?'}:{','.join(f'{v:.3f}' for v in m['base'])}:{m.get('roughness')}:" \
            f"{m.get('metallic', 0)}:{m.get('coat', 0)}"
        if k not in mats:
            mats[k] = m
            keys.append(k)
        return k

    # One model is meshed once, however many parts use it; each copy is
    # placed by its matrix.
    meshed: dict[tuple, list] = {}

    def faces_of(lab, role: str, col) -> list:
        entry = TCollection_AsciiString()
        TDF_Tool.Entry_s(lab, entry)
        cache_key = (entry.ToCString(), role, col)
        if cache_key in meshed:
            return meshed[cache_key]
        shape = st.GetShape_s(lab)
        BRepMesh_IncrementalMesh(shape, deflection, False, 0.35, True)
        out = []
        ex = TopExp_Explorer(shape, TopAbs_FACE)
        while ex.More():
            face = TopoDS.Face_s(ex.Current())
            ex.Next()
            floc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, floc)
            if tri is None or tri.NbTriangles() == 0:
                continue
            if not tri.HasNormals():
                BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
            trsf = floc.Transformation()
            n = tri.NbNodes()
            p = np.empty((n, 3), np.float64)
            q = np.empty((n, 3), np.float64)
            for i in range(1, n + 1):
                pt = tri.Node(i).Transformed(trsf)
                p[i - 1] = (pt.X(), pt.Y(), pt.Z())
                d = tri.Normal(i).Transformed(trsf)
                q[i - 1] = (d.X(), d.Y(), d.Z())
            m = tri.NbTriangles()
            t = np.empty((m, 3), np.int64)
            for i in range(1, m + 1):
                a, b, c = tri.Triangle(i).Get()
                t[i - 1] = (a - 1, b - 1, c - 1)
            if face.Orientation() == TopAbs_REVERSED:
                t = t[:, ::-1]
                q = -q
            k = keys.index(key_for(role, colour(shape=face) or col))
            out.append((p, q, t, k))
        meshed[cache_key] = out
        return out

    def add_shape(lab, loc: TopLoc_Location, role: str, col) -> None:
        trsf = loc.Transformation()
        rot = np.array([[trsf.Value(r, c) for c in (1, 2, 3)] for r in (1, 2, 3)])
        move = np.array([trsf.Value(r, 4) for r in (1, 2, 3)])
        for p, q, t, k in faces_of(lab, role, col):
            pos.append(p @ rot.T + move)
            nrm.append(q @ rot.T)
            idx.append(t + count[0])
            grp.append(np.full(len(t), k, np.int64))
            count[0] += len(p)

    def walk(lab, loc: TopLoc_Location, role: str, col) -> None:
        comps = TDF_LabelSequence()
        st.GetComponents_s(lab, comps)
        own = colour(lab=lab) or col
        m = ROLE.match(name(lab) or "")
        own_role = m.group(1).lower() if m and m.group(1).lower() in MATERIALS else role
        if comps.Length() == 0:
            add_shape(lab, loc, own_role, own)
            return
        for i in range(1, comps.Length() + 1):
            ref = comps.Value(i)
            target = TDF_Label()
            st.GetReferredShape_s(ref, target)
            walk(target, loc.Multiplied(st.GetLocation_s(ref)), own_role, colour(lab=ref) or own)

    roots = TDF_LabelSequence()
    st.GetFreeShapes(roots)
    for r in range(1, roots.Length() + 1):
        top = TDF_LabelSequence()
        st.GetComponents_s(roots.Value(r), top)
        if top.Length() == 0:
            walk(roots.Value(r), TopLoc_Location(), "", None)
            continue
        for i in range(1, top.Length() + 1):
            ref = top.Value(i)
            target = TDF_Label()
            st.GetReferredShape_s(ref, target)
            part = name(target)
            if part in skip:
                continue
            designators.append(part)
            walk(target, st.GetLocation_s(ref), "", None)

    if not pos:
        empty = np.zeros((0, 3))
        return PartsMesh(empty, empty, np.zeros((0, 3), np.int64), [], np.zeros(0, np.int64), {},
                         designators)
    positions = np.vstack(pos).astype("<f4")
    normals = np.vstack(nrm)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = (normals / np.where(lengths > 0, lengths, 1)).astype("<f4")
    return PartsMesh(positions, normals, np.vstack(idx).astype("<u4"), keys,
                     np.concatenate(grp), mats, designators)


def triangle_count(mesh: PartsMesh) -> int:
    return int(len(mesh.indices))



def place(mesh: PartsMesh, centre_mils: tuple[float, float], thickness_mm: float,
          first_material: int) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    """The parts in the board scene's frame: Altium's mm moved so the
    board's centre is the origin and its bottom face is z = 0."""
    mm = MM_PER_MIL
    shift = np.array([centre_mils[0] * mm, centre_mils[1] * mm, -thickness_mm], "<f4")
    arrays = {
        "positions": (mesh.positions - shift).astype("<f4"),
        "normals": mesh.normals.astype("<f4"),
        "uvs": np.zeros((len(mesh.positions), 2), "<f4"),
        "indices": mesh.indices.astype("<u4"),
        "material_ids": (mesh.groups + first_material).astype("<u4"),
    }
    return arrays, [dict(mesh.materials[k]) for k in mesh.material_keys]
