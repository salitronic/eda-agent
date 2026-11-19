# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint's 3D bodies and embedded model files come from Altium's own
JSON export of it, never from opening the .PcbLib.

Measured on AD 26.11.1.5: the export of a footprint holds each body's
MODEL.3D.ROTX / ROTY / ROTZ / DZ and, under StepFiles, the embedded STEP
base64-encoded, which decoded to the very bytes that were embedded.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from eda_agent import footprint_export as fe
from eda_agent.tools import library as lib_tools
from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"
_STEP = b"ISO-10303-21;\r\nHEADER;\r\n"


def _export(*models: tuple[str, bytes], bodies=()) -> dict:
    return {"Primitives": [{"TYPE": "Pad"}, *bodies],
            "StepFiles": [{"FILENAME": n, "CHECKSUM": 7, "FILE": base64.b64encode(d).decode()}
                          for n, d in models]}


_BODY = {"TYPE": "ComponentBody", "MODEL.NAME": "SOT.step", "MODEL.MODELTYPE": "1",
         "MODEL.EMBED": "TRUE", "MODEL.3D.ROTX": "10.000", "MODEL.3D.ROTY": "20.000",
         "MODEL.3D.ROTZ": "30.000", "MODEL.3D.DZ": "12mil"}


class _Bridge:
    def __init__(self, workspace, exports, footprints=()):
        self.config = type("C", (), {"workspace_dir": str(workspace)})()
        self.exports, self.footprints, self.sent = exports, list(footprints), []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "library.footprint_export":
            data = self.exports.get(params["footprint_name"])
            if data is None:
                return {"written": False, "problem": "no such footprint"}
            Path(params["out_path"]).write_text("\ufeff" + json.dumps(data), encoding="utf-8")
            return {"written": True}
        if command == "library.get_footprints":
            return {"footprints": [{"name": n} for n in self.footprints]}
        return {}


def test_bodies_are_read_from_the_export():
    got = fe.body_models(_export(bodies=[_BODY, {"TYPE": "ComponentBody", "MODEL.NAME": ""}]))
    assert got[0] == {"model_file": "SOT.step", "model_type": 1, "embedded": True,
                      "rotation_deg": [10.0, 20.0, 30.0], "model_z_offset_mils": 12.0}
    assert got[1]["model_file"] == "" and len(got) == 2


def test_an_extruded_bodys_generated_shape_is_not_a_model_file():
    data = _export(("SOT.step", _STEP))
    data["StepFiles"].append({"FILENAME": "", "CHECKSUM": 1, "FILE": "SVNP"})
    # Altium's base64 is broken into lines.
    data["StepFiles"][0]["FILE"] = data["StepFiles"][0]["FILE"][:8] + "\r\n" \
        + data["StepFiles"][0]["FILE"][8:]
    assert [(m["name"], m["data"]) for m in fe.model_files(data)] == [("SOT.step", _STEP)]


def test_the_export_is_read_and_then_deleted(tmp_path):
    bridge = _Bridge(tmp_path, {"SOT": _export(bodies=[_BODY])})
    data = asyncio.run(fe.read_export(bridge, r"C:\l\L.PcbLib", "SOT"))
    assert fe.body_models(data)[0]["model_file"] == "SOT.step"
    assert list(tmp_path.iterdir()) == []
    assert bridge.sent[0][0] == "library.footprint_export"


def test_a_model_comes_out_exactly(tmp_path):
    bridge = _Bridge(tmp_path / "ws", {"SOT": _export(("SOT.step", _STEP), bodies=[_BODY])})
    (tmp_path / "ws").mkdir()
    got = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out", footprints=["SOT"]))
    assert got["ok"] is True
    assert Path(got["written"][0]["path"]).read_bytes() == _STEP
    assert got["written"][0]["rotation_deg"] == [10.0, 20.0, 30.0]
    assert got["written"][0]["step_header"] is True


def test_an_existing_file_is_kept_unless_asked(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "SOT.step").write_bytes(b"mine")
    bridge = _Bridge(tmp_path, {"SOT": _export(("SOT.step", _STEP))})
    got = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out", footprints=["SOT"]))
    assert got["ok"] is False and (tmp_path / "out" / "SOT.step").read_bytes() == b"mine"
    again = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out",
                                          footprints=["SOT"], overwrite=True))
    assert again["ok"] is True and (tmp_path / "out" / "SOT.step").read_bytes() == _STEP


def test_a_name_cannot_write_outside_the_folder(tmp_path):
    bridge = _Bridge(tmp_path, {"SOT": _export(("..\\..\\evil.step", _STEP))})
    got = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out", footprints=["SOT"]))
    assert Path(got["written"][0]["path"]).parent == tmp_path / "out"


def test_a_failed_export_and_a_missing_name_are_said_so(tmp_path):
    bridge = _Bridge(tmp_path, {"SOT": _export(("SOT.step", _STEP))})
    got = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out",
                                        footprints=["SOT", "GONE"], names=["SOT.step", "x.step"]))
    assert got["ok"] is False
    assert got["refused"] == [{"footprint": "GONE", "reason": "no such footprint"}]
    assert got["not_found"] == ["x.step"]


def test_the_tool_exports_every_footprint_altium_lists(monkeypatch, tmp_path):
    libfile = tmp_path / "Lib.PcbLib"
    libfile.write_bytes(b"stand-in")
    bridge = _Bridge(tmp_path, {"A": _export(("a.step", _STEP)), "B": _export(("b.step", _STEP))},
                     footprints=["A", "B"])
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: bridge)
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg
    lib_tools.register_library_tools(Mcp())
    got = asyncio.run(tools["lib_extract_embedded_models"](library_path=str(libfile)))
    assert [w["name"] for w in got["written"]] == ["a.step", "b.step"]
    assert Path(got["written"][0]["path"]).parent == tmp_path / "models" / "Lib"
    assert [c for c, _ in bridge.sent] == ["library.get_footprints", "library.footprint_export",
                                           "library.footprint_export"]


def test_nothing_here_opens_an_altium_file():
    src = Path(fe.__file__).read_text(encoding="utf-8")
    for call in ("olefile", "zlib", "struct", "library_path).read", "open("):
        assert call not in src, call


def test_altium_writes_the_export_and_the_current_footprint_comes_back():
    body = functions(strip_comments(_PAS.read_text(encoding="utf-8")))["Lib_FootprintExport"]
    made = body.index("PcbLib.CurrentComponent := Footprint;")
    export = body.index("BoardEx.SaveToJSONFile(OutPath);", made)
    back = body.index("PcbLib.CurrentComponent := Before;", export)
    assert made < export < back
    assert "'out_path is the library itself'" in body
    text = _PAS.read_text(encoding="utf-8")
    assert "'footprint_export':     Result := Lib_FootprintExport(Params, RequestId);" in text


def test_a_model_embedded_from_a_file_path_is_found_by_its_file_name(tmp_path):
    # The export names the model by the path it was embedded from, the
    # body by its file name.
    bridge = _Bridge(tmp_path / "ws", {"SOT": _export(
        (r"D:\models\vendor\SOT.STEP", _STEP), bodies=[_BODY])})
    (tmp_path / "ws").mkdir()
    got = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out",
                                        footprints=["SOT"], names=["sot.step"]))
    assert got["ok"] is True and got["not_found"] == []
    assert Path(got["written"][0]["path"]).name == "SOT.STEP"
    assert got["written"][0]["rotation_deg"] == [10.0, 20.0, 30.0]
    # Asked by the full path, it is still the same file.
    got = asyncio.run(fe.extract_models(bridge, "L.PcbLib", tmp_path / "out2",
                                        footprints=["SOT"],
                                        names=["D:/elsewhere/SOT.step"]))
    assert got["ok"] is True
