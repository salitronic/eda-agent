# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard's cockpit: when it reads the board again and which parts,
what it lists, and that it follows the board in front without ever
taking the focus."""

from __future__ import annotations

import re
from pathlib import Path

from eda_agent.bridge import altium_bridge as ab
from eda_agent.web import cockpit as ck

ALL = {"board", "pads", "components", "copper", "rules", "classes"}


def _state(file="C:\\b\\A.PcbDoc", parts="p1", copper="c1", board=True):
    return {"file": file, "kind": "PCB", "board": board, "parts_stamp": parts,
            "copper_stamp": copper, "copper": 100,
            "selected": {"parts": [], "nets": [], "count": 0}}


def test_what_is_read_again():
    a = _state()
    assert ck.stale_sections(None, a) == ALL
    assert ck.stale_sections(a, _state()) is None
    # Moved parts read their pads and themselves; copper is kept.
    assert ck.stale_sections(a, _state(parts="p2")) == {"pads", "components", "board",
                                                        "rules", "classes"}
    assert ck.stale_sections(a, _state(copper="c2")) == {"copper", "board", "rules", "classes"}
    assert ck.stale_sections(a, _state(file="C:\\b\\B.PcbDoc")) == ALL
    assert ck.stale_sections(a, _state(board=False)) is None


def test_a_counted_poll_keeps_the_walked_stamp_while_the_count_holds():
    walked = "2095:1:2:3"
    state, keep, walk = ck.merge_copper(_state(copper="n:2095"), walked)
    assert state["copper_stamp"] == walked and keep == walked and not walk
    # Copper added or deleted: the stamp differs, and the next poll walks.
    state, keep, walk = ck.merge_copper(_state(copper="n:2096"), walked)
    assert state["copper_stamp"] == "n:2096" and keep == walked and walk
    state, keep, walk = ck.merge_copper(_state(copper="2096:4:5:6"), walked)
    assert keep == "2096:4:5:6" and not walk


def test_unchanged_sections_come_from_the_cache():
    sent = []
    cache = ck.SectionCache()
    send = lambda c, p: sent.append(p["section"]) or {"section": p["section"]}  # noqa: E731
    first = cache.sender(send, ALL)
    for sec in ("board", "copper", "pads"):
        first("pcb.get_layout_model", {"section": sec})
    sent.clear()
    again = cache.sender(send, {"pads"})
    for sec in ("board", "copper", "pads"):
        again("pcb.get_layout_model", {"section": sec})
    assert sent == ["pads"]


def test_findings_carry_where_and_what():
    result = {"checks": {
        "placement_audit": {"overlaps": [{"a": "R2", "b": "U1", "area_sq_mils": 226.8,
                                          "at": [4856.0, 4337.36]}]},
        "drc": {"violations": [{"rule": "clearance", "a": "pad:R2#1", "b": "track:GND#9",
                                "gap": 3.0, "required": 6.0, "at": [1.0, 2.0]}]},
        "pad_entry_audit": {"findings": [{"pad": "LED3.3", "net": "N1", "x": 5.0, "y": 6.0,
                                          "kind": "off_axis", "severity": "high",
                                          "detail": "enters at an odd angle"}]},
        "loop_audit": {"worst_by_kind": {"decoupling": {"parts": ["C23", "U4"],
                                                        "area_sq_mils": 47062.7}}},
    }}
    rows = ck.findings_of(result)
    by = {r["check"]: r for r in rows}
    assert by["placement"]["at"] == [4856.0, 4337.36] and by["placement"]["refs"] == ["R2", "U1"]
    assert by["drc"]["severity"] == "error" and "needs 6.00" in by["drc"]["text"]
    assert by["pad_entry"]["at"] == [5.0, 6.0] and by["pad_entry"]["refs"] == ["LED3", "N1"]
    # Loops have no point of their own: the page finds them by their parts.
    assert by["loops"]["at"] is None and by["loops"]["severity"] == "info"


class _Altium:
    """pcb.cockpit_state answers from a script of states."""

    def __init__(self, states):
        self.states = list(states)
        self.sent = []

    def __call__(self, command, params, timeout):
        self.sent.append((command, dict(params)))
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def _cockpit(states, monkeypatch):
    altium = _Altium(states)
    c = ck.Cockpit(send=altium)
    reads = []
    monkeypatch.setattr(c, "refresh", lambda memo, state, force=False: (
        reads.append((state["parts_stamp"], force)),
        setattr(memo, "read_state", state)))
    return c, altium, reads


def test_a_change_is_read_once_it_holds_still(monkeypatch):
    c, _, reads = _cockpit([_state(parts="p1"), _state(parts="p1"), _state(parts="p2"),
                            _state(parts="p3"), _state(parts="p3"), _state(parts="p3")],
                           monkeypatch)
    c.step()                      # new board: wait one poll
    assert reads == []
    c.step()                      # held: read
    assert reads == [("p1", False)]
    c.step()                      # p2, still moving
    c.step()                      # p3
    assert reads == [("p1", False)]
    c.step()                      # p3 held: read once
    c.step()
    assert reads == [("p1", False), ("p3", False)]


def test_a_pinned_board_is_read_only_when_it_is_in_front(monkeypatch):
    other = _state(file="C:\\other\\Client.PcbDoc")
    c, _, reads = _cockpit([other, other, other], monkeypatch)
    c.pin("C:\\b\\A.PcbDoc")
    for _ in range(3):
        c.step()
    assert reads == [] and c.activity == "not in front"


def test_the_page_stays_on_its_board_when_another_comes_to_the_front(monkeypatch):
    """The first board seen is the one shown; going to another board or to a
    schematic in Altium no longer switches the page."""
    a, other = _state(), _state(file="C:/other/B.PcbDoc", parts="x")
    sch = {"file": "C:/b/Top.SchDoc", "kind": "SCH", "board": False}
    c, _, reads = _cockpit([a, a, other, other, sch, sch], monkeypatch)
    c.step()
    c.step()
    assert c.pinned == a["file"] and reads == [("p1", False)]
    for _ in range(4):
        c.step()
    assert c.memo.file == a["file"] and reads == [("p1", False)]
    assert c.activity == "not in front" and c.front == sch["file"]
    assert c.snapshot()["in_front"] is False and c.snapshot()["shown"] == a["file"]


def test_loading_a_board_brings_it_forward_reads_it_and_gives_the_front_back(monkeypatch):
    b = "C:/other/B.PcbDoc"
    sent = []

    def altium(command, params, timeout):
        sent.append((command, dict(params)))
        if command == "application.get_active_document":
            return {"file_path": "C:/b/Top.SchDoc"}
        if command == "pcb.cockpit_state":
            return _state(file=b, parts="q")
        return {}

    c = ck.Cockpit(send=altium)
    order = []
    monkeypatch.setattr(c, "refresh", lambda memo, state, force=False, after_read=None: (
        order.append(("read", memo.file, force)), after_read and after_read()))
    monkeypatch.setattr(c, "touch", lambda: None)
    import threading
    finished = threading.Event()
    assert c.load(b, done=finished.set)["ok"]
    assert finished.wait(2)
    names = [cmd for cmd, _ in sent]
    assert names == ["application.get_active_document", "pcb.focus_board",
                     "pcb.cockpit_state", "application.set_active_document"]
    assert sent[1][1] == {"board_path": b}
    assert sent[3][1] == {"file_path": "C:/b/Top.SchDoc"}     # the front, given back
    assert order == [("read", b, True)] and c.pinned == b and c.memo.file == b
    assert c.loading == "" and c.error == ""


def test_the_copper_is_walked_only_now_and_then(monkeypatch):
    c, altium, _ = _cockpit([_state(copper="1:1:1:1")] + [_state(copper="n:1")] * 9,
                            monkeypatch)
    for _ in range(10):
        c.step()
    walks = [p.get("copper") for _, p in altium.sent]
    assert walks[0] == "true" and walks.count("true") <= 1 + 10 // ck.COPPER_EVERY


def test_a_big_board_is_counted_not_walked(monkeypatch):
    big = dict(_state(copper="n:20000"), copper=20000)
    first = dict(_state(copper="20000:1:2:3"), copper=20000)
    c, altium, _ = _cockpit([first] + [big] * 12, monkeypatch)
    for _ in range(12):
        c.step()
    assert [p.get("copper") for _, p in altium.sent][1:].count("true") == 0


def test_the_cockpit_neither_restores_nor_adopts_a_document():
    assert ab._unpinned("pcb.cockpit_state", {})
    with ab.following_front():
        assert ab._unpinned("pcb.view", {"action": "set"})
    assert not ab._unpinned("pcb.view", {"action": "set"})


_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _handler() -> str:
    text = _PCB.read_text(encoding="utf-8", errors="replace")
    start = text.index("Function PCB_CockpitState(")
    return text[start:text.index("\nEnd;", start)]


def test_the_handler_names_pcbserver_only_with_a_board_in_front():
    # Naming PCBServer with no PCB ever opened halts the script (see
    # GetPCBBoardAnywhere): the kind is checked first.
    code = re.sub(r"\{[^}]*\}", "", _handler())
    assert code.index("Kind = 'PCB'") < code.index("GetPCBBoardAnywhere(0)")


def test_the_handler_walks_the_copper_only_when_asked():
    code = re.sub(r"\{[^}]*\}", "", _handler())
    asked = code.index("ExtractJsonValue(Params, 'copper')")
    assert asked < code.index("MkSet(eTrackObject, eArcObject, eViaObject)")
    assert "GetPrimitiveCounter" in code


def test_a_cockpit_poll_leaves_no_routine_lines_in_the_trace(tmp_path):
    ab._CALL.command = "pcb.cockpit_state"
    try:
        for line in ("SEND cmd=pcb.cockpit_state id=1", "POLL_START id=1",
                     "POLL_SEEN id=1", "POLL_MATCH id=1"):
            ab._trace_log(tmp_path, line)
        ab._trace_log(tmp_path, "TIMEOUT id=1 after 15s")
    finally:
        ab._CALL.command = ""
    ab._trace_log(tmp_path, "SEND cmd=pcb.get_nets id=2")
    lines = (tmp_path / "bridge_trace.log").read_text().splitlines()
    assert [l.split(" ", 1)[1] for l in lines] == ["TIMEOUT id=1 after 15s",
                                                    "SEND cmd=pcb.get_nets id=2"]


def test_altium_logs_a_cockpit_poll_only_when_it_failed():
    form = (Path(__file__).resolve().parents[1] / "scripts" / "altium" /
            "StatusForm.pas").read_text(encoding="utf-8", errors="replace")
    code = re.sub(r"\{[^}]*\}", "", form)
    at = code.index("AppendLog(FormatLogStamp(0) + ',' + IntToStr(DurationMs)")
    guard = code[max(0, at - 200):at]
    assert "Command <> 'pcb.cockpit_state'" in guard and "ResultTag <> 'OK'" in guard


def test_the_status_window_lists_a_cockpit_poll_only_when_it_failed():
    form = (Path(__file__).resolve().parents[1] / "scripts" / "altium" /
            "StatusForm.pas").read_text(encoding="utf-8", errors="replace")
    code = re.sub(r"\{[^}]*\}", "", form)
    at = code.index("AppendLogLine(Command, DurationMs, DashIsError")
    guard = code[max(0, at - 120):at]
    assert "Command <> 'pcb.cockpit_state'" in guard and "DashIsError" in guard
