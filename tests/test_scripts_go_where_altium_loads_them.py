# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Scripts are installed where Altium loads them, and point at the server.

Altium compiles the Altium_API.PrjScr its Global Projects list names. The
installer always wrote %USERPROFILE%\\EDA Agent\\scripts, so a project
registered anywhere else ran old code while every check read the new
files. It also pointed Altium's workspace at <dest>\\..\\workspace, a
folder the server never writes to unless dest was the default.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from eda_agent import cli
from eda_agent.diag import health


def _fake_registry(monkeypatch, installs: dict):
    """installs: {install key: [registered values]}."""
    fake = types.SimpleNamespace(HKEY_CURRENT_USER="HKCU")

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def open_key(root, path):
        if path == r"Software\Altium":
            return Key(path)
        install, _, rest = path.partition("\\")
        if install in installs and rest == cli._GLOBAL_PROJECTS:
            return Key(path)
        raise OSError(path)

    def enum_key(key, index):
        names = list(installs)
        if key.path == r"Software\Altium" and index < len(names):
            return names[index]
        raise OSError

    def enum_value(key, index):
        install = key.path.partition("\\")[0]
        values = installs.get(install, [])
        if index < len(values):
            return (f"Item{index}", values[index], 1)
        raise OSError

    fake.OpenKey, fake.EnumKey, fake.EnumValue = open_key, enum_key, enum_value
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(cli.sys, "platform", "win32")


def test_the_registered_folder_is_the_destination(monkeypatch, tmp_path):
    folder = tmp_path / "MyScripts"
    folder.mkdir()
    (folder / "Altium_API.PrjScr").write_text("")
    _fake_registry(monkeypatch, {
        "Altium Designer {A}": [str(folder / "Altium_API.PrjScr"),
                                r"C:\other\Something.PrjScr"]})
    assert cli.get_default_scripts_dest() == folder
    assert cli.registered_script_projects() == [
        {"install": "Altium Designer {A}",
         "path": str(folder / "Altium_API.PrjScr"), "exists": True}]


def test_nothing_registered_falls_back_to_the_profile(monkeypatch):
    _fake_registry(monkeypatch, {})
    assert cli.get_default_scripts_dest() == cli.get_fallback_scripts_dest()


def test_every_registered_folder_is_installed(monkeypatch, tmp_path):
    a, b = tmp_path / "AD24", tmp_path / "AD26"
    _fake_registry(monkeypatch, {
        "AD24 {1}": [str(a / "Altium_API.PrjScr")],
        "AD26 {2}": [str(b / "Altium_API.PrjScr")],
        "AD26b {3}": [str(b / "Altium_API.PrjScr")]})
    done = []
    monkeypatch.setattr(cli, "_install_scripts_into",
                        lambda dst, force: done.append(dst) or 0)
    assert cli.cmd_install_scripts(force=True) == 0
    assert done == [a, b], "one copy per distinct registered folder"


def test_the_workspace_pointer_is_the_servers_not_beside_dest(
        monkeypatch, tmp_path):
    from eda_agent import config as config_module
    workspace = tmp_path / "server_ws"
    monkeypatch.setattr(
        config_module, "config",
        config_module.config.model_copy(update={"workspace_dir": workspace}))
    written = []
    monkeypatch.setattr(config_module, "write_workspace_pointer",
                        lambda p: written.append(Path(p)))
    _fake_registry(monkeypatch, {})
    assert cli._install_scripts_into(tmp_path / "custom" / "scripts",
                                     force=True) == 0
    assert written == [workspace]


def test_a_registration_pointing_at_nothing_fails_health(monkeypatch,
                                                          tmp_path):
    _fake_registry(monkeypatch, {
        "AD26 {2}": [str(tmp_path / "gone" / "Altium_API.PrjScr")]})
    check = health._check_script_registration()
    assert check.status == health.Status.FAIL
    assert "gone" in check.message


def test_a_fresh_install_is_not_reported_as_different(tmp_path):
    """The install stamps each unit and drops its BOM; that is not a code
    change, and every unit used to read as different right after one."""
    bundled, deployed = tmp_path / "src", tmp_path / "dst"
    bundled.mkdir()
    deployed.mkdir()
    unit = ("Function UnitStamp_Main(Dummy : Integer) : String;\r\nBegin\r\n"
            "    Result := 'dev';\r\nEnd;\r\nProcedure X;\r\nBegin\r\nEnd;\r\n")
    (bundled / "Main.pas").write_bytes(b"\xef\xbb\xbf" + unit.encode())
    (deployed / "Main.pas").write_bytes(unit.encode())
    cli._stamp_deployed_unit(deployed / "Main.pas")
    assert "Result := 'dev'" not in (deployed / "Main.pas").read_text()
    assert health._pas_differences(bundled, deployed) == []
    (deployed / "Main.pas").write_text(
        (deployed / "Main.pas").read_text().replace("Procedure X", "Procedure Y"))
    assert health._pas_differences(bundled, deployed) == ["Main.pas"]


def test_no_registration_is_a_warning(monkeypatch):
    _fake_registry(monkeypatch, {})
    if sys.platform != "win32":
        pytest.skip("the check skips off Windows")
    assert health._check_script_registration().status == health.Status.WARN


def test_scripts_altium_loads_from_the_source_folder_are_left_in_place(
        monkeypatch, tmp_path):
    from eda_agent import config as config_module
    src = tmp_path / "clone" / "scripts" / "altium"
    src.mkdir(parents=True)
    unit = src / "Main.pas"
    unit.write_bytes(b"Unit Main;\r\nEnd.\r\n")
    (src / "Altium_API.PrjScr").write_text("")
    workspace = tmp_path / "server_ws"
    monkeypatch.setattr(
        config_module, "config",
        config_module.config.model_copy(update={"workspace_dir": workspace}))
    written = []
    monkeypatch.setattr(config_module, "write_workspace_pointer",
                        lambda p: written.append(Path(p)))
    monkeypatch.setattr(cli, "get_bundled_scripts_path", lambda: src)

    def no_copy(*a, **k):
        raise AssertionError("copied a file onto itself")

    monkeypatch.setattr(cli.shutil, "copy2", no_copy)
    # The same folder spelled another way still counts as the same.
    same = tmp_path / "clone" / "scripts" / ".." / "scripts" / "altium"
    assert cli._install_scripts_into(same, force=True) == 0
    assert unit.read_bytes() == b"Unit Main;\r\nEnd.\r\n", "not stamped"
    assert written == [workspace]
