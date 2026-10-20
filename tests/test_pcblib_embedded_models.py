# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Embedded 3D models come out of a PcbLib byte for byte, read only.

The proof that a file is the original is Altium's own checksum, stored
in the model record when it embedded the file. Measured on a real
library (four STEP models, two stored as negative numbers): the
checksum here equals Altium's on every one. That library is a user's
own and is not shipped, so that test runs only where it exists; the
rest use a stand-in library built in memory.
"""
from __future__ import annotations

import asyncio
import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from eda_agent.fileio import pcblib_models as pm
from eda_agent.tools import library as lib_tools

_REAL = Path(r"C:/Users/grgsa/Documents/AltiumLL/SamacSys.PcbLib")


def test_the_checksum_weighs_each_byte_by_position_and_is_stored_signed():
    assert pm.model_checksum(b"") == 0
    assert pm.model_checksum(b"\x01\x02\x03") == 1 * 1 + 2 * 1 + 3 * 2
    # 0xFF at every position until the sum passes 2^31: stored negative.
    big = pm.model_checksum(b"\xff" * 5000)
    exact = (255 + sum(255 * i for i in range(1, 5000))) & 0xFFFFFFFF
    assert big == exact - (1 << 32) if exact & 0x80000000 else big == exact


@pytest.mark.skipif(not _REAL.is_file(), reason="needs the measured library")
def test_on_a_real_library_every_checksum_equals_altiums(tmp_path):
    before = hashlib.sha256(_REAL.read_bytes()).hexdigest()
    got = pm.extract_embedded_models(_REAL, tmp_path)
    assert got["ok"] and len(got["written"]) == 4 and not got["refused"]
    assert all(w["checksum_verified"] and w["step_header"] for w in got["written"])
    assert {w["checksum"] < 0 for w in got["written"]} == {True, False}
    assert hashlib.sha256(_REAL.read_bytes()).hexdigest() == before, "the library changed"


class _Ole:
    def __init__(self, streams):
        self.streams = streams

    def exists(self, name):
        return name in self.streams

    def openstream(self, name):
        import io
        return io.BytesIO(self.streams[name])

    def close(self):
        pass


def _record(**props) -> bytes:
    text = "|" + "|".join(f"{k}={v}" for k, v in props.items()) + "\0"
    return struct.pack("<I", len(text)) + text.encode("latin-1")


def _library(monkeypatch, models, records=None):
    """models: [(name, bytes or None)]; records override the checksum."""
    data, streams = b"", {}
    for i, (name, payload) in enumerate(models):
        checksum = (records or {}).get(name, pm.model_checksum(payload or b""))
        data += _record(EMBED="TRUE", ID=f"{{{i}}}", ROTX="90.000", ROTY="0.000",
                        ROTZ="0.000", CHECKSUM=checksum, NAME=name)
        if payload is not None:
            streams[f"Library/Models/{i}"] = zlib.compress(payload)
    streams["Library/Models/Data"] = data
    monkeypatch.setattr(pm, "_open", lambda path: _Ole(streams))


_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"


def test_a_model_comes_out_exactly(monkeypatch, tmp_path):
    _library(monkeypatch, [("part.step", _STEP)])
    got = pm.extract_embedded_models("x.PcbLib", tmp_path)
    assert got["ok"]
    assert (tmp_path / "part.step").read_bytes() == _STEP
    assert got["written"][0]["rotation_deg"] == [90.0, 0.0, 0.0]


def test_a_checksum_that_does_not_match_is_not_written(monkeypatch, tmp_path):
    _library(monkeypatch, [("part.step", _STEP)], records={"part.step": 12345})
    got = pm.extract_embedded_models("x.PcbLib", tmp_path)
    assert not got["ok"] and "does not match" in got["refused"][0]["reason"]
    assert not (tmp_path / "part.step").exists()


def test_a_linked_model_and_a_missing_name_are_said_so(monkeypatch, tmp_path):
    _library(monkeypatch, [("linked.step", None), ("part.step", _STEP)])
    got = pm.extract_embedded_models("x.PcbLib", tmp_path,
                                     names=["LINKED.step", "absent.stp"])
    assert "linked model" in got["refused"][0]["reason"]
    assert got["not_in_saved_file"] == ["absent.stp"] and not got["written"]


def test_an_existing_file_is_kept_unless_asked(monkeypatch, tmp_path):
    _library(monkeypatch, [("part.step", _STEP)])
    (tmp_path / "part.step").write_bytes(b"mine")
    got = pm.extract_embedded_models("x.PcbLib", tmp_path)
    assert "exists" in got["refused"][0]["reason"]
    assert (tmp_path / "part.step").read_bytes() == b"mine"
    assert pm.extract_embedded_models("x.PcbLib", tmp_path, overwrite=True)["ok"]


def test_a_name_cannot_write_outside_the_folder(monkeypatch, tmp_path):
    _library(monkeypatch, [("..\\..\\evil.step", _STEP)])
    got = pm.extract_embedded_models("x.PcbLib", tmp_path / "out")
    assert Path(got["written"][0]["path"]).parent == tmp_path / "out"


def test_the_module_never_opens_the_library_for_writing():
    src = Path(pm.__file__).read_text(encoding="utf-8")
    assert "path.read_bytes()" in src
    for mode in ('"wb"', "'wb'", '"r+b"', '"ab"', "write_stream"):
        assert mode not in src


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def reg(fn):
            self.tools[fn.__name__] = fn
            return fn
        return reg


class _Bridge:
    def __init__(self, reply, workspace):
        self.reply, self.sent = reply, []
        self.config = type("C", (), {"workspace_dir": workspace})()

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, params))
        return self.reply


def test_the_tool_extracts_what_altium_says_the_footprint_uses(monkeypatch, tmp_path):
    _library(monkeypatch, [("other.step", _STEP), ("part.step", _STEP + b"x")])
    libfile = tmp_path / "Lib.PcbLib"
    libfile.write_bytes(b"stand-in")
    bridge = _Bridge({"body_models": [
        {"has_model": True, "model_file": "C:\\models\\part.step"},
        {"has_model": False, "model_file": ""}]}, tmp_path)
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    lib_tools.register_library_tools(mcp)
    got = asyncio.run(mcp.tools["lib_extract_embedded_models"](
        library_path=str(libfile), footprint_name="SOIC8"))
    assert [w["name"] for w in got["written"]] == ["part.step"]
    assert Path(got["written"][0]["path"]).parent == tmp_path / "models" / "Lib"
    assert bridge.sent[0][0] == "library.get_footprint_primitives"


class _Storages(_Ole):
    def listdir(self):
        return [name.split("/") for name in self.streams]

    def openstream(self, name):
        import io
        return io.BytesIO(self.streams["/".join(name) if isinstance(name, list) else name])


def _footprint(name: str, *bodies: dict) -> bytes:
    # A Data stream opens with a 4-byte length, a 1-byte name length and
    # the name; each body is binary, then its text record.
    head = struct.pack("<I", len(name) + 1) + bytes([len(name)]) + name.encode("latin-1")
    out = head + b"\x01<\x00\x00\x00"
    for body in bodies:
        text = "|V7_LAYER=MECHANICAL13|" + "|".join(f"{k}={v}" for k, v in body.items())
        out += b"\x0c\x10\x00\x00\x00" + b"\xff" * 16 + struct.pack("<I", len(text) + 1) \
            + text.encode("latin-1") + b"\x00"
    return out


def test_saved_bodies_are_read_per_footprint_by_their_whole_name(monkeypatch):
    # The values measured live on AD 26.11 after SetState(10, 20, 30, 12 mil).
    sot = {"MODELID": "{1}", "MODEL.NAME": "SOT.step", "MODEL.3D.ROTX": "10.000",
           "MODEL.3D.ROTY": "20.000", "MODEL.3D.ROTZ": "30.000",
           "MODEL.3D.DZ": "12mil", "MODEL.MODELTYPE": "1"}
    extruded = {"MODELID": "{2}", "MODEL.NAME": "", "MODEL.3D.ROTX": "0.000",
                "MODEL.3D.ROTY": "0.000", "MODEL.3D.ROTZ": "0.000",
                "MODEL.3D.DZ": "0.65mm", "MODEL.MODELTYPE": "0"}
    longname = "16-VFQFN Exposed Pad(TXB0106RGYR)"
    streams = {"SOT-23-3/Data": _footprint("SOT-23-3", sot, extruded),
               longname[:31] + "/Data": _footprint(longname, sot),
               "Library/Models/Data": b""}
    monkeypatch.setattr(pm, "_open", lambda path: _Storages(streams))

    got = pm.saved_body_models("x.PcbLib", "SOT-23-3")
    assert [b["model_file"] for b in got] == ["SOT.step", ""]
    assert got[0]["rotation_deg"] == [10.0, 20.0, 30.0]
    assert got[0]["model_z_offset_mils"] == 12.0 and got[0]["model_type"] == 1
    assert got[1]["model_z_offset_mils"] == pytest.approx(25.5906, abs=1e-4)
    assert len(pm.saved_body_models("x.PcbLib", longname)) == 1
    assert pm.saved_body_models("x.PcbLib", longname[:31]) is None
    assert pm.saved_body_models("x.PcbLib", "SOT-23") is None
