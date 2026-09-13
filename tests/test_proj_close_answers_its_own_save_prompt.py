# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_close(save=False) answers Altium's save prompt, for its own project only.

MEASURED 2026-09-13 on AD 26.10.1.6, on a scratch project: after an edit
the editor tab showed the sheet as modified while IServerDocument.Modified
read False, and CloseObject raised "Confirm Save Locally for (2) Modified
Documents" even with save=False. The handler cannot answer it, because the
prompt blocks the loop the handler runs in.

Answered through UI Automation in the same session: "Save None" retitled
the dialog to "Confirm Not Saving", "OK" closed it, the project closed, and
the file on disk was byte-identical afterwards. This guards the tool doing
that itself, and above all the refusals: it must press nothing when the
prompt would discard a document outside the project, and must never press
OK before the retitle shows the decisions changed, because OK on the
original decisions SAVES.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.bridge.exceptions import AltiumTimeoutError
from eda_agent.tools import project as project_tools
from eda_agent.ui import uia, windows

PROJECT = r"C:\scratch\CloseTest.PrjPcb"
SHEET = r"C:\scratch\CloseSheet.SchDoc"
FOREIGN = r"C:\client\Board.SchDoc"


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Dialog:
    def __init__(self, hwnd, title):
        self.hwnd = hwnd
        self.title = title


class _Altium:
    """A bridge and a screen that behave as measured."""

    def __init__(self, listed=(PROJECT, SHEET), members=(SHEET,),
                 retitles=True, pid=77, second_prompt=False,
                 close_reply=None):
        self.close_reply = close_reply
        self.listed = list(listed)
        self.members = list(members)
        self.retitles = retitles
        self.pid = pid
        self.second_prompt = second_prompt
        self.dialogs = {5: "Confirm Save Locally for (2) Modified Documents"}
        self.presses: list[tuple[int, str]] = []
        self.project_open = True

    # --- bridge -----------------------------------------------------------
    def get_altium_status(self):
        if self.pid is None:
            return {"running": True, "pid": None, "ambiguous": True,
                    "reason": "2 Altium processes are running"}
        return {"running": True, "pid": self.pid}

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "project.get_documents":
            return [{"file_name": p.rsplit("\\", 1)[-1], "file_path": p}
                    for p in self.members]
        if command == "project.close":
            if self.close_reply is not None:
                return dict(self.close_reply)
            raise AltiumTimeoutError(
                "Altium is showing a modal dialog 3s into this call",
                details={"dialogs": {"blocked": True, "dialogs": [
                    {"title": self.dialogs[5], "hwnd": 5}]}})
        if command == "project.get_open_projects":
            projects = [{"project_path": PROJECT}] if self.project_open else []
            return {"projects": projects}
        raise AssertionError(f"unexpected command {command}")

    # --- screen -----------------------------------------------------------
    def describe_window(self, hwnd, depth=4, limit=400):
        return {"ok": True, "elements": [{"name": "Save None"},
                                         {"name": "OK"}]
                + [{"name": p} for p in self.listed]}

    def invoke(self, hwnd, name):
        self.presses.append((hwnd, name))
        if name == "Save None" and self.retitles:
            self.dialogs[hwnd] = "Confirm Not Saving (2) Modified Documents"
        if name == "OK":
            del self.dialogs[hwnd]
            if self.second_prompt and hwnd == 5:
                self.dialogs[6] = "Confirm Save Locally for (1) Modified Documents"
                self.listed = [SHEET]
            else:
                self.project_open = False
        return {"ok": True, "element": name, "how": "invoke"}

    def window_title(self, hwnd):
        return self.dialogs.get(hwnd, "")

    def list_dialogs(self, pid):
        return [_Dialog(h, t) for h, t in self.dialogs.items()]


@pytest.fixture()
def altium(monkeypatch):
    def install(**kwargs):
        fake = _Altium(**kwargs)
        monkeypatch.setattr(project_tools, "get_bridge", lambda: fake)
        monkeypatch.setattr(windows, "available", lambda: True)
        monkeypatch.setattr(windows, "automation_enabled", lambda: True)
        monkeypatch.setattr(windows, "window_title", fake.window_title)
        monkeypatch.setattr(windows, "dialogs", fake.list_dialogs)
        monkeypatch.setattr(windows, "wait_for_close",
                            lambda hwnd, timeout=0: hwnd not in fake.dialogs)

        def wait_until(check, timeout, *a, **k):
            return any(check() for _ in range(3))

        monkeypatch.setattr(windows, "wait_until", wait_until)
        monkeypatch.setattr(uia, "available", lambda: True)
        monkeypatch.setattr(uia, "describe_window", fake.describe_window)
        monkeypatch.setattr(uia, "invoke", fake.invoke)
        mcp = _Mcp()
        project_tools.register_project_tools(mcp)
        return fake, mcp.tools["proj_close"]
    return install


def _close(tool, **kwargs):
    return asyncio.run(tool(**kwargs))


def test_the_measured_sequence_closes_the_project(altium):
    fake, close = altium()
    out = _close(close, project_path=PROJECT, save=False)
    assert [name for _, name in fake.presses] == ["Save None", "OK"]
    assert out["success"] is True and out["closed"] is True
    assert out["prompts_answered"] == 1
    assert out["discarded"] == sorted([PROJECT, SHEET])


def test_a_prompt_listing_another_project_is_not_answered(altium):
    """The reporter saw 49 documents listed when closing a 10-document
    project. Answering that would discard work nobody named."""
    fake, close = altium(listed=(PROJECT, SHEET, FOREIGN))
    out = _close(close, project_path=PROJECT, save=False)
    assert fake.presses == []
    assert out["success"] is False and out["closed"] is False
    assert FOREIGN in out["reason"]


def test_ok_is_never_pressed_before_the_decisions_change(altium):
    """OK on the original decisions saves every listed document."""
    fake, close = altium(retitles=False)
    out = _close(close, project_path=PROJECT, save=False)
    assert [name for _, name in fake.presses] == ["Save None"]
    assert out["closed"] is False
    assert "would save" in out["reason"]


def test_a_second_prompt_from_the_retried_close_is_answered_too(altium):
    fake, close = altium(second_prompt=True)
    out = _close(close, project_path=PROJECT, save=False)
    assert [name for _, name in fake.presses] == [
        "Save None", "OK", "Save None", "OK"]
    assert out["prompts_answered"] == 2
    assert out["closed"] is True


def test_saving_is_never_answered_for_the_caller(altium):
    fake, close = altium()
    with pytest.raises(AltiumTimeoutError):
        _close(close, project_path=PROJECT, save=True)
    assert fake.presses == []


def test_without_a_project_path_nothing_is_pressed(altium):
    fake, close = altium()
    out = _close(close, save=False)
    assert fake.presses == []
    assert out["closed"] is False
    assert "Nothing was pressed" in out["reason"]


def test_member_names_without_paths_mean_nothing_is_pressed(altium):
    """A deployed script older than the path fix reports bare file names,
    and a name cannot tell two projects' documents apart.

    The prompt here lists only the project file, which is always a member,
    so accepting the bare name WOULD lead to a press. The first version
    listed the sheet as well, and the foreign-document refusal produced
    the same empty press list: a mutation accepting bare names survived.
    """
    fake, close = altium(listed=(PROJECT,), members=("CloseSheet.SchDoc",))
    out = _close(close, project_path=PROJECT, save=False)
    assert fake.presses == []
    assert out["closed"] is False
    assert "file names rather than paths" in out["reason"]


def test_an_ambiguous_altium_means_nothing_is_pressed(altium):
    fake, close = altium(pid=None)
    out = _close(close, project_path=PROJECT, save=False)
    assert fake.presses == []
    assert out["closed"] is False
    assert "2 Altium processes" in out["reason"], (
        "nothing was pressed, but not because the process was ambiguous")


def test_disabled_ui_automation_means_nothing_is_pressed(altium, monkeypatch):
    fake, close = altium()
    monkeypatch.setattr(windows, "automation_enabled", lambda: False)
    out = _close(close, project_path=PROJECT, save=False)
    assert fake.presses == []
    assert "disabled" in out["reason"]


def test_a_retried_close_does_not_list_a_path_twice(altium):
    """MEASURED: a two-attempt close came back with the project file
    listed twice, once per attempt."""
    reply = {"success": True, "closed": True, "project_path": PROJECT,
             "saved": False, "attempts": 2,
             "discarded": [PROJECT, PROJECT.lower(), SHEET]}
    fake, close = altium(close_reply=reply)
    out = _close(close, project_path=PROJECT, save=False)
    assert out["discarded"] == [PROJECT, SHEET]
    assert out["attempts"] == 2
    assert fake.presses == []
