# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Menus and dialogs in every language Altium ships, and every menu item's
command, read from the install rather than written down here.

The fixtures are a miniature install written in the layouts Altium uses
(a forms.fdb record, English ASCII and translated UTF-16 .rcs files), so
none of Altium's own text is copied into the repository. One test reads
the real install when there is one.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agent.ui import altium_locale, altium_menus, dialog_driver, menu


def _record(text: str) -> bytes:
    return bytes([len(text)]) + text.encode("utf-16-le")


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "AD"
    system = root / "System"
    zh = system / "Resources" / "2052"
    zh.mkdir(parents=True)
    forms = b"\x00\x05" + _record("Button_Apply") + b"\x02\x00"
    for english, local in (("Execute Changes", "执行变更"), ("OK", "确定"),
                           ("Cancel", "取消"), ("No", "否"),
                           ("Delete", "删除")):
        forms += _record(f"Caption.'{english}'") + _record(local)
        forms += _record("Hint.''") + b"\x00"
    (zh / "forms.fdb").write_bytes(forms)
    english_rcs = (
        "PL PLPCB:DesignRules Command='PCB:EditRules' Caption='&Rules...' "
        "Description='Design Rules' End\n"
        "PL PLPCB:Tear Command='PCB:TearDropSelectedPads'\n"
        "    Caption='Tear&drops...' Params='Mode = All' Shortcut1='Ctrl+T'\n"
        "End\n"
        "Tree MNPCB_Design Caption='&Design' Popup='D'\n"
        "    Link MNPCB_D10 PLID='PLPCB:DesignRules' End\n"
        "End\n"
        "Tree MNPCBMenu Caption='PCB Menu' TopLevel=True\n"
        "    TreeCopy MNPCB_DesignCopy OriginalID=MNPCB_Design End\n"
        "    Tree MNPCB_Tools Caption='&Tools' Popup='T'\n"
        "        Link MNPCB_T10 PLID='PLPCB:Tear' End\n"
        "        Separator MNPCB_T20 End\n"
        "    End\n"
        "End\n")
    (system / "AdvPcb.rcs").write_text(english_rcs, encoding="latin-1")
    local_rcs = (english_rcs.replace("&Rules...", "规则 (&R)...")
                 .replace("&Design", "设计 (&D)")
                 .replace("&Tools", "工具 (&T)")
                 .replace("Tear&drops...", "泪滴 (&D)..."))
    (zh / "AdvPcb.rcs").write_text(local_rcs, encoding="utf-16")
    return root


def _clear_caches():
    """Forget everything read from an install. A test may have replaced a
    cached function with a stub, which has nothing to clear."""
    for fn in (altium_locale._table, altium_menus._load, altium_menus._items,
               menu._local_names, dialog_driver._localised_roles,
               dialog_driver._localised_irreversible):
        getattr(fn, "cache_clear", lambda: None)()


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = _install(tmp_path)
    monkeypatch.setenv(altium_locale.INSTALL_ENV, str(root))
    monkeypatch.setattr(altium_locale, "_cache_dir",
                        lambda: tmp_path / "cache")
    _clear_caches()
    yield root
    _clear_caches()


def test_dialog_and_menu_translations_come_from_the_install(install):
    assert altium_locale.installed_languages() == {"2052": "zh-CN"}
    assert altium_locale.translations("Execute Changes") == {
        "zh-CN": {"执行变更"}}
    assert altium_locale.translations("&Tools") == {"zh-CN": {"工具 (&T)"}}
    assert altium_locale.plain_caption("工具 (&T)") == "工具"


def test_translated_buttons_get_their_roles(install):
    assert dialog_driver.role_of("执行变更") == "commit"
    assert dialog_driver.role_of("确定") == "advance"
    assert dialog_driver.role_of("取消") == "dismiss"
    assert dialog_driver.role_of("否") == "dismiss"


def test_a_short_translation_must_match_whole(install):
    """"否" (No) inside "是否" is not a button that says No."""
    assert dialog_driver.role_of("是否保存") is None


def test_a_translated_delete_question_is_not_answered(install):
    assert not dialog_driver.is_answerable_confirmation(
        {"title": "确认", "message": ["删除 12 个对象？"]})
    assert dialog_driver.is_answerable_confirmation(
        {"title": "确认", "message": ["继续？"]})


def test_menu_items_carry_their_commands(install):
    items = {"|".join(i.path): i for i in altium_menus.menu_items("PCB")}
    assert items["Design|Rules"].command == "PCB:EditRules"
    tear = items["Tools|Teardrops"]
    assert (tear.command, tear.params, tear.shortcut) == (
        "PCB:TearDropSelectedPads", "Mode = All", "Ctrl+T")
    assert len(items) == 2, "a separator is not a menu item"


def test_a_path_resolves_in_english_or_translated(install):
    assert altium_menus.resolve("Design|Rules...", "PCB").command == \
        "PCB:EditRules"
    assert altium_menus.resolve("设计|规则", "PCB").command == "PCB:EditRules"
    assert altium_menus.resolve("Design|Nope", "PCB") is None
    assert altium_menus.resolve("Design|Rules", "SCH") is None


#: A Place menu built as Altium builds the schematic one: copied onto the
#: menu bar with an id suffix, then changed by an Insertion and a Deletion
#: that name the suffixed ids.
_SCH_RCS = (
    "PL PLSch:Wire Command='Sch:PlaceWire' Caption='&Wire' End\n"
    "PL PLSch:Arc Command='Sch:PlaceArc' Caption='&Arc' End\n"
    "PL PLSch:Note Command='Sch:PlaceNote' Caption='&Note' End\n"
    "PL PLSch:Comment Command='Sch:CreateCommentThread' Caption='Comment...'\n"
    "    Shortcut1='Ctrl+Alt+C' Params='Source=PlaceMenu' End\n"
    "Tree MNSchematic_Place Caption='&Place'\n"
    "    Link MNSchematic_Place10 PLID='PLSch:Wire' End\n"
    "    Tree MNSchematic_PlaceTools Caption='&Drawing Tools'\n"
    "        Link MNSchematic_PlaceTools1 PLID='PLSch:Arc' End\n"
    "    End\n"
    "    Link MNSchematic_Place30 PLID='PLSch:Note' End\n"
    "End\n"
    "Tree MNSchematicMenu Caption='Schematic Menu' TopLevel=True\n"
    "    TreeCopy MNSchematic_Place2 OriginalID=MNSchematic_Place IDSuffix='_Menu' End\n"
    "End\n"
    "Insertion MNSchematic_Place_Ins1 TargetID='MNSchematic_Place_Menu'\n"
    "    RefID0='MNSchematic_PlaceTools_Menu' InsertType='After'\n"
    "    Link MNSchematic_Place190 PLID='PLSch:Comment' End\n"
    "End\n"
    "Insertion MNSchematic_Place_Ins2 TargetID='MNSchematic_Place_EsdMenu'\n"
    "    RefID0='MNSchematic_PlaceTools_EsdMenu' InsertType='After'\n"
    "    Link MNSchematic_Place200 PLID='PLSch:Wire' End\n"
    "End\n"
    "Deletion MNSchematic_Del1 TargetID='MNSchematic_Place_Menu'\n"
    "    DeleteID='MNSchematic_Place30_Menu' End\n")


def test_insertions_and_deletions_shape_the_menu(install):
    """Place > Comment reaches the schematic menu only through an
    Insertion. Read as a one-line leaf, its Link was dropped and the item
    was missing from every search."""
    (install / "System" / "AdvSch.rcs").write_text(_SCH_RCS, encoding="latin-1")
    paths = ["|".join(i.path) for i in altium_menus.menu_items("SCH")]
    assert paths == ["Place|Wire", "Place|Drawing Tools|Arc", "Place|Comment"]
    hit = altium_menus.resolve("Place|Comment...", "SCH")
    assert (hit.command, hit.params, hit.shortcut) == (
        "Sch:CreateCommentThread", "Source=PlaceMenu", "Ctrl+Alt+C")
    assert [h["path"] for h in altium_menus.search("comment", "SCH")] == \
        ["Place|Comment"]


def test_a_misspelt_path_gets_the_nearest(install):
    assert [h["path"] for h in altium_menus.nearest("Desgn|Rules", "PCB")] \
        == ["Design|Rules"]


def test_search_finds_by_any_language_or_command(install):
    assert [h["path"] for h in altium_menus.search("teardrop")] == \
        ["Tools|Teardrops"]
    assert [h["path"] for h in altium_menus.search("泪滴", "PCB")] == \
        ["Tools|Teardrops"]
    assert [h["path"] for h in altium_menus.search("editrules")] == \
        ["Design|Rules"]


def test_a_translated_menu_bar_is_recognised(install, monkeypatch):
    assert altium_locale.language_of(["设计 (&D)", "工具 (&T)"]) == "zh-CN"
    assert altium_locale.language_of(["File", "Edit"]) == "en"
    monkeypatch.setattr(menu, "_UI_LANGUAGE", "zh-CN")
    assert menu._same("工具 (&T)", "Tools")
    monkeypatch.setattr(menu, "_UI_LANGUAGE", "en")
    assert not menu._same("工具 (&T)", "Tools")


def test_a_later_session_reads_the_cache_until_altium_changes(
        install, tmp_path, monkeypatch):
    altium_menus.menu_items("PCB")
    altium_locale.translations("&Tools")
    cached = sorted(p.name.split("-")[0] for p in
                    (tmp_path / "cache").glob("*.json"))
    assert cached == ["locale", altium_menus._CACHE_FORMAT]

    # A new session: nothing in memory, and the files cannot be parsed.
    altium_menus._items.cache_clear()
    altium_locale._table.cache_clear()
    monkeypatch.setattr(altium_menus, "_load", lambda install: 1 / 0)
    monkeypatch.setattr(altium_locale, "_parse_forms", lambda data: 1 / 0)
    assert altium_menus.resolve("Design|Rules", "PCB").command == \
        "PCB:EditRules"
    assert altium_locale.translations("&Tools") == {"zh-CN": {"工具 (&T)"}}

    # An Altium update changes the files, so the old cache is not used.
    rcs = install / "System" / "AdvPcb.rcs"
    rcs.write_text(rcs.read_text(encoding="latin-1") + "\n",
                   encoding="latin-1")
    altium_menus._items.cache_clear()
    with pytest.raises(ZeroDivisionError):
        altium_menus.menu_items("PCB")


def test_windows_message_box_captions_are_read():
    if os.name != "nt":
        pytest.skip("Windows only")
    found = altium_locale.windows_captions()
    assert {"ok", "cancel", "yes", "no"} <= set(found)


def test_the_real_install_when_there_is_one(monkeypatch):
    monkeypatch.delenv(altium_locale.INSTALL_ENV, raising=False)
    install = altium_locale.install_dir()
    if install is None or not (install / "System").is_dir():
        pytest.skip("no Altium install on this host")
    items = altium_menus.menu_items("PCB", install)
    assert len(items) > 100
    assert all(i.command for i in items)
