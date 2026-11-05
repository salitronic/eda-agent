# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Variant parameter values, renaming and copying a variant, and the BOM
one variant builds.

What each tool sends, what it refuses before Altium sees it, how a
variant's entries change the BOM, and the Pascal shapes that read every
write back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.export.variant_bom import apply_variant, find_variant
from eda_agent.tools import project

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(name: str) -> str:
    text = (ALTIUM / "Project.pas").read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;", start)]


class _Bridge:
    def __init__(self, replies=None):
        self.sent = []
        self.replies = replies or {}

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return self.replies.get(command, {"success": True})


def _tools(monkeypatch, replies=None):
    bridge = _Bridge(replies)
    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    project.register_project_tools(_Mcp())
    return captured, bridge


def test_parameter_values_go_as_set_and_clear_lists(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["proj_set_parameter_variation"](
        variant="Lite", designator=" R5 ",
        parameters={"Value": "4k7", "Comment": "4k7 1%", "Tolerance": None}))
    assert bridge.sent == [("project.set_parameter_variation", {
        "variant": "Lite", "designator": "R5",
        "set": "Value=4k7~~Comment=4k7 1%", "clear": "Tolerance"})]


def test_parameter_values_refused_before_altium(monkeypatch):
    for kwargs in ({"variant": "", "designator": "R5", "parameters": {"Value": "1"}},
                   {"variant": "Lite", "designator": "R5", "parameters": {}},
                   {"variant": "Lite", "designator": "R5", "parameters": {"A=B": "1"}},
                   {"variant": "Lite", "designator": "R5", "parameters": {" ": "1"}},
                   {"variant": "Lite", "designator": "R5", "parameters": {"Value": "a~~b"}}):
        tools, bridge = _tools(monkeypatch)
        result = asyncio.run(tools["proj_set_parameter_variation"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


def test_rename_and_copy_send_what_was_asked(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["proj_rename_variant"](variant="Lite", new_name="Basic"))
    asyncio.run(tools["proj_copy_variant"](source="Lite", name="Lite B"))
    assert bridge.sent == [
        ("project.rename_variant", {"variant": "Lite", "new_name": "Basic"}),
        ("project.copy_variant", {"source": "Lite", "name": "Lite B"})]
    for tool, kwargs in (("proj_rename_variant", {"variant": "Lite", "new_name": " "}),
                         ("proj_rename_variant", {"variant": " ", "new_name": "B"}),
                         ("proj_copy_variant", {"source": "Lite", "name": " "})):
        tools, bridge = _tools(monkeypatch)
        result = asyncio.run(tools[tool](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


_BOM = [
    {"designator": "R1", "comment": "10k", "footprint": "0402", "lib_ref": "RES"},
    {"designator": "R5", "comment": "10k", "footprint": "0402", "lib_ref": "RES"},
    {"designator": "U1", "comment": "LDO A", "footprint": "SOT23-5", "lib_ref": "LDO_A"},
    {"designator": "C9", "comment": "10u", "footprint": "0805", "lib_ref": "CAP"},
]
_LITE = {"name": "Lite", "description": "Low cost", "variations": [
    {"designator": "c9", "kind": "Not Fitted", "alternate_part": "", "parameters": []},
    {"designator": "R5", "kind": "Fitted", "alternate_part": "",
     "parameters": [{"name": "Comment", "value": "4k7"}, {"name": "Value", "value": "4k7"}]},
    {"designator": "U1", "kind": "Alternate", "alternate_part": "LDO_B",
     "alternate_link": {"symbol_reference": "LDO_B", "footprint": "SOT23-5",
                        "same_footprint": True, "library": "Power.SchLib"},
     "parameters": []},
    {"designator": "R99", "kind": "Not Fitted", "alternate_part": "", "parameters": []},
]}


def test_a_variant_bom_drops_not_fitted_and_applies_values_and_alternates():
    out = apply_variant(_BOM, _LITE)
    by = {c["designator"]: c for c in out["components"]}
    assert set(by) == {"R1", "R5", "U1"} and out["count"] == 3
    assert out["not_fitted"] == ["C9"]
    assert by["R1"]["variation"] == "none" and by["R1"]["comment"] == "10k"
    assert by["R5"]["comment"] == "4k7"
    assert by["R5"]["varied_parameters"] == {"Comment": "4k7", "Value": "4k7"}
    assert by["U1"]["lib_ref"] == "LDO_B" and by["U1"]["footprint"] == "SOT23-5"
    assert by["U1"]["alternate"]["part"] == "LDO_B"
    assert out["alternates"] == ["U1"] and out["varied"] == ["R5"]
    assert out["entries_not_in_bom"] == ["R99"]
    assert _BOM[1]["comment"] == "10k", "the base BOM must not change"


def test_an_alternate_with_its_own_footprint_takes_it():
    v = {"name": "X", "variations": [{"designator": "U1", "kind": "Alternate",
         "alternate_part": "LDO_C", "alternate_link": {
             "symbol_reference": "LDO_C", "footprint": "SOT223",
             "same_footprint": False}}]}
    (u1,) = [c for c in apply_variant(_BOM, v)["components"] if c["designator"] == "U1"]
    assert u1["footprint"] == "SOT223"


def test_variants_are_found_by_name_or_description():
    assert find_variant([_LITE], "lite") is _LITE
    assert find_variant([_LITE], "LOW COST") is _LITE
    assert find_variant([_LITE], "Full") is None


def test_the_bom_tool_applies_the_named_variant(monkeypatch):
    replies = {"project.get_bom": {"components": _BOM, "count": 4},
               "project.get_variants": {"variants": [_LITE], "count": 1}}
    tools, bridge = _tools(monkeypatch, replies)
    out = asyncio.run(tools["proj_get_bom"](variant="Lite"))
    assert [c for c, _ in bridge.sent] == ["project.get_bom", "project.get_variants"]
    assert out["not_fitted"] == ["C9"] and out["count"] == 3
    out = asyncio.run(tools["proj_get_bom"](variant="Full"))
    assert out["ok"] is False and out["variants"] == ["Lite"]
    tools, bridge = _tools(monkeypatch, replies)
    out = asyncio.run(tools["proj_get_bom"]())
    assert [c for c, _ in bridge.sent] == ["project.get_bom"] and out["count"] == 4


def test_parameter_values_are_read_back_through_a_fresh_lookup():
    body = _body("Proj_SetParameterVariation")
    write = body.index("PV.DM_SetVariedValue(PValue);")
    reread = body.index("CompVar := FindVariationFor(Variant, Comp);", write)
    assert reread > write
    assert "DM_RemoveParameterVariation(K)" in body
    # A not-fitted part is refused before anything is written.
    assert body.index("'NOT_FITTED'") < body.index("DM_AddComponentVariation")


def test_rename_refuses_a_taken_name_before_writing():
    body = _body("Proj_RenameVariant")
    assert body.index("VariantNameTaken(Project, NewName, Variant)") \
        < body.index("Variant.DM_SetDescription(NewName)")


def test_variants_are_found_and_named_by_the_description_altium_saves():
    # After a reopen every variant's DM_Name reads 'Variant'; only the
    # description tells them apart.
    taken = _body("VariantNameTaken")
    assert "UpperCase(V.DM_Description) = UpperCase(Name)" in taken
    assert "Lbl := V.DM_Description;" in _body("VariantLabel")
    active = _body("Proj_SetActiveVariant")
    assert "FindVariantNamed(Project, VariantName)" in active
    assert active.index("Project.DM_SetCurrentProjectVariant(Variant)") \
        < active.index("RunProcess('WorkspaceManager:VariantManagement')")
    assert "Current.DM_UniqueId = V.DM_UniqueId" in _body("CurrentVariantIs")
    delete = _body("Proj_DeleteVariant")
    assert "Variant.DM_Name = VariantName" not in delete
    assert "FindVariantNamed(Project, VariantName)" in delete
    listing = _body("Proj_GetVariants")
    assert "EscapeJsonString(VariantLabel(Variant))" in listing
    board = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    assert "UpperCase(V0.DM_Description) = UpperCase(VariantName)" in board


def test_a_copy_is_compared_with_its_source_entry_by_entry():
    body = _body("Proj_CopyVariant")
    copy_at = body.index("D.DM_SetVariationKind(S.DM_VariationKind);")
    check = body.index("Dst.DM_FindComponentVariationByUniqueId(S.DM_UniqueId)")
    assert check > copy_at
    assert "CopyAltLink(S, D)" in body and "Dst.DM_AddParameter(" in body


def test_the_new_commands_are_routed():
    text = (ALTIUM / "Project.pas").read_text(encoding="utf-8-sig")
    for cmd, fn in (("set_parameter_variation", "Proj_SetParameterVariation"),
                    ("rename_variant", "Proj_RenameVariant"),
                    ("copy_variant", "Proj_CopyVariant")):
        assert f"'{cmd}':" in text and f"Result := {fn}(Params, RequestId);" in text


def test_an_alternate_carries_its_library_link(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["proj_set_component_variation"](
        variant="Lite", designator="Q6", kind="alternate", alternate_part="2N3906",
        alternate_library="Miscellaneous Devices.IntLib"))
    assert bridge.sent == [("project.set_component_variation", {
        "variant": "Lite", "designator": "Q6", "kind": "alternate",
        "alternate_part": "2N3906",
        "alternate_library": "Miscellaneous Devices.IntLib"})]
    tools, bridge = _tools(monkeypatch)
    result = asyncio.run(tools["proj_set_component_variation"](
        variant="Lite", designator="Q6", kind="fitted",
        alternate_library="Miscellaneous Devices.IntLib"))
    assert bridge.sent == [] and result["ok"] is False
    # Without the link Altium loads the entry back as fitted.
    body = _body("Proj_SetComponentVariation")
    link = body.index("Link.DM_SetLibraryIdentifier(AltLib);")
    assert body.index("Link.DM_SetLibIdentifierKind(2);") < link
    assert body.index("Variant.DM_FindComponentVariationByUniqueId", link) > link
