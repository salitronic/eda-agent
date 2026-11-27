# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The batch tools carry a ';' in a filter, proj_get_unconnected_pins
takes the project it reads, and sch_save_copy_as sends what it was given."""

from __future__ import annotations

import asyncio

from eda_agent.tools import generic

class _Bridge:
    """Answers each command with its own reply; records what was sent."""

    def __init__(self, replies):
        self.sent = []
        self.replies = replies

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        reply = self.replies.get((command, params.get("object_type")),
                                 self.replies.get(command, {"success": True}))
        return reply


def _run(module, register, monkeypatch, tool, replies, **kwargs):
    bridge = _Bridge(replies)
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(_Mcp())
    return asyncio.run(tools[tool](**kwargs)), bridge.sent


def _gen(monkeypatch, tool, **kwargs):
    return _run(generic, "register_generic_tools", monkeypatch, tool,
                {"generic.batch_delete": {"success": True},
                 "generic.batch_modify": {"success": True}}, **kwargs)


def test_a_batch_delete_filter_may_hold_a_semicolon(monkeypatch):
    """A signal harness's Vertices read x1,y1;x2,y2."""
    _, sent = _gen(monkeypatch, "obj_batch_delete", operations=[
        {"object_type": "eSignalHarness", "filter": "Vertices=1,1;2,2"}])
    assert sent and sent[0][1]["operations"].endswith(";filter=Vertices=1,1;2,2")


def test_a_batch_delete_still_refuses_the_op_separator(monkeypatch):
    out, sent = _gen(monkeypatch, "obj_batch_delete", operations=[
        {"object_type": "eWire", "filter": "Name=a~~scope=project;object_type=eWire;filter="}])
    assert sent == [] and out["ok"] is False and "'~~'" in out["reason"]


def test_a_batch_delete_still_refuses_a_semicolon_in_the_type(monkeypatch):
    out, sent = _gen(monkeypatch, "obj_batch_delete", operations=[
        {"object_type": "eWire;filter=", "filter": "Name=a"}])
    assert sent == [] and out["ok"] is False


def test_a_batch_modify_filter_may_hold_a_semicolon_but_not_a_set(monkeypatch):
    _, sent = _gen(monkeypatch, "obj_batch_modify", operations=[
        {"object_type": "eSignalHarness", "filter": "Vertices=1,1;2,2", "set": "LineWidth=1"}])
    assert sent and "filter=Vertices=1,1;2,2;set=LineWidth=1" in sent[0][1]["operations"]
    out, sent = _gen(monkeypatch, "obj_batch_modify", operations=[
        {"object_type": "eLabel", "filter": "Text=a;SET=b", "set": "Text=c"}])
    assert sent == [] and out["ok"] is False


def test_unconnected_pins_reads_the_project_it_is_given(monkeypatch):
    prj = r"C:\d\R.PrjPcb"
    _, sent = _gen(monkeypatch, "proj_get_unconnected_pins", project_path=prj)
    assert sent == [("generic.get_unconnected_pins", {"project_path": prj})]
    _, sent = _gen(monkeypatch, "proj_get_unconnected_pins")
    assert sent == [("generic.get_unconnected_pins", {})]


def test_save_copy_as_sends_only_what_was_given(monkeypatch):
    _, sent = _run(generic, "register_generic_tools", monkeypatch, "sch_save_copy_as",
                   {"generic.sch_save_copy_as": {"written": True}},
                   target_path=r"C:\d\Copy.SchDoc")
    assert sent == [("generic.sch_save_copy_as", {"target_path": r"C:\d\Copy.SchDoc"})]
    _, sent = _run(generic, "register_generic_tools", monkeypatch, "sch_save_copy_as",
                   {"generic.sch_save_copy_as": {"written": True}},
                   target_path=r"C:\d\Copy.SchDoc", source_path=r"C:\d\A.SchDoc",
                   overwrite=True)
    assert sent[0][1] == {"target_path": r"C:\d\Copy.SchDoc",
                          "source_path": r"C:\d\A.SchDoc", "overwrite": "true"}


def test_save_copy_as_adds_the_copy_to_a_project_only_once_written(monkeypatch):
    prj = r"C:\d\R.PrjPcb"
    out, sent = _run(generic, "register_generic_tools", monkeypatch, "sch_save_copy_as",
                     {"generic.sch_save_copy_as": {"written": True},
                      "project.add_document": {"success": True}},
                     target_path=r"C:\d\Copy.SchDoc", add_to_project=prj)
    assert sent[1] == ("project.add_document",
                       {"document_path": r"C:\d\Copy.SchDoc", "project_path": prj})
    assert out["added_to_project"] == {"success": True}
    out, sent = _run(generic, "register_generic_tools", monkeypatch, "sch_save_copy_as",
                     {"generic.sch_save_copy_as": {"written": False}},
                     target_path=r"C:\d\Copy.SchDoc", add_to_project=prj)
    assert [c for c, _ in sent] == ["generic.sch_save_copy_as"]


def test_save_copy_as_takes_a_schematic_path_only(monkeypatch):
    out, sent = _run(generic, "register_generic_tools", monkeypatch, "sch_save_copy_as",
                     {}, target_path=r"C:\d\Copy.PcbDoc")
    assert sent == [] and out["ok"] is False
