# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_update_footprints_from_library refreshes only from a real library.

LoadFromLibrary does not fail on a library that lacks the pattern.
Measured: pointed at a .PcbLib that does not exist, it loaded the
footprint from elsewhere and the part "refreshed" to exactly what it was,
reported as a success. So the library is checked to exist and hold the
pattern before anything on the board changes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _tool(monkeypatch, tmp_path, held):
    lib = tmp_path / "Parts.PcbLib"
    lib.write_text("library")
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, dict(params or {})))
            if command == "library.get_footprints":
                return {"footprints": [{"name": n} for n in held]}
            if command == "pcb.update_footprints_from_library":
                names = params["designators"].split("|")
                return {"board": "B.PcbDoc", "items": [
                    {"designator": d, "ok": True, "pattern": "P_" + d,
                     "library": params["library_path"] or str(lib)}
                    for d in names]}
            return {"focused": True}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    pcb_tools.register_pcb_tools(_Mcp())
    return tools["pcb_update_footprints_from_library"], sent, str(lib)


def _real_runs(sent):
    return [p["designators"] for c, p in sent
            if c == "pcb.update_footprints_from_library"
            and p["dry_run"] == "false"]


def test_a_pattern_the_library_lacks_is_never_swapped(monkeypatch, tmp_path):
    tool, sent, lib = _tool(monkeypatch, tmp_path, held=["P_C1"])
    out = asyncio.run(tool(["C1", "C2"]))
    assert _real_runs(sent) == ["C1"]
    refused = [i for i in out["items"] if not i["ok"]]
    assert [i["designator"] for i in refused] == ["C2"]
    assert "not in" in refused[0]["reason"]


def test_a_library_that_is_not_on_disk_is_refused(monkeypatch, tmp_path):
    tool, sent, lib = _tool(monkeypatch, tmp_path, held=["P_C1"])
    out = asyncio.run(tool(["C1"], library_path=str(tmp_path / "Nope.PcbLib")))
    assert _real_runs(sent) == []
    assert out["items"][0]["ok"] is False


def test_the_board_is_brought_back_after_reading_libraries(monkeypatch, tmp_path):
    tool, sent, lib = _tool(monkeypatch, tmp_path, held=["P_C1"])
    asyncio.run(tool(["C1"]))
    reads = [i for i, (c, _) in enumerate(sent) if c == "library.get_footprints"]
    focus = [i for i, (c, _) in enumerate(sent) if c == "pcb.focus_board"]
    run = [i for i, (c, p) in enumerate(sent)
           if c == "pcb.update_footprints_from_library" and p["dry_run"] == "false"]
    assert reads and focus and focus[-1] > reads[-1] and run[0] > focus[-1]


def test_a_dry_run_changes_nothing(monkeypatch, tmp_path):
    tool, sent, lib = _tool(monkeypatch, tmp_path, held=["P_C1"])
    out = asyncio.run(tool(["C1"], dry_run=True))
    assert _real_runs(sent) == [] and out["would_update"] == 1


def test_the_old_part_goes_only_after_the_new_one_passed():
    body = load(_ALTIUM / "PCB.pas")["PCB_UpdateFootprintsFromLibrary"]
    checks = body.index("Reason := 'the new footprint lacks pads that carry nets: '")
    removed_old = body.index("Board.RemovePCBObject(OldC);")
    removed_new = body.rindex("Board.RemovePCBObject(NewC);")
    assert checks < removed_new < removed_old
    # The old part goes only once a loaded copy matched the named library.
    assert body.index("If NewSig = LibSig Then") < body.rindex("If Loaded Then") < removed_old
    # The schematic link is carried over, the pair multi-channel matches on.
    for field in ("SourceUniqueId", "SourceHierarchicalPath", "SourceDesignator"):
        assert f"NewC.{field} := " in body, field
    # Side, then turn, then origin, as every placement here.
    assert (body.index("NewC.Layer := OldLayer;")
            < body.index("NewC.Rotation := OldRot;")
            < body.index("NewC.x := OldX;"))


def test_the_copy_must_match_the_named_library():
    """Altium's loaders find a footprint by name. Measured: with two
    libraries holding SOIC8_T, a load naming the second gave the first's
    copy, and a stale copy kept a part's old pin-1 dot. A copy is kept
    only when its signature matches the footprint read from the named
    library; a copy that does not is taken off the board again."""
    funcs = load(_ALTIUM / "PCB.pas")
    body = funcs["PCB_UpdateFootprintsFromLibrary"]
    read = body.index("LibSig := LibraryFootprintSignature(LibPath, Pattern);")
    back = body.index("Board := ResolvePCBBoard(BoardPath);")
    compare = body.index("NewSig := FootprintSignature(NewC);")
    assert read < back < compare
    miss = body[compare:body.index("If Not Loaded Then", compare)]
    assert "Board.RemovePCBObject(NewC);" in miss
    lib = funcs["LibraryFootprintSignature"]
    # Read from the library that is in front and is the one named.
    assert "If UpperCase(Front) <> UpperCase(LibPath) Then Exit;" in lib
    sig = funcs["FootprintSignature"]
    for part in ("Arc.Radius", "Arc.LineWidth", "Track.Width", "Seen.IndexOf(Addr) < 0"):
        assert part in sig, part


def test_the_first_loader_names_the_library_file():
    """Measured: LoadFromLibrary with FootPrint and FileName loaded the
    named library's copy where SourceComponentLibrary and the part's own
    loader both took the other library's footprint of the same name."""
    body = load(_ALTIUM / "PCB.pas")["PCB_UpdateFootprintsFromLibrary"]
    first = body.index("If Attempt = 1 Then")
    assert body.index(
        "NewC.LoadFromLibrary('FootPrint=' + Pattern + '|FileName=' + LibPath);") \
        < body.index("If Attempt = 2 Then") and first < body.index("If Attempt = 2 Then")
