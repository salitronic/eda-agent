# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Claude sessions sharing one Altium take turns, one tool call each.

One script loop serves every session. Requests carry a session tag, a
sequence and a mark: the tool call they belong to, or background work.
Altium serves a lone session straight through; with several, the turn
passes after each tool call, never inside one, and background work (the
dashboard's reads) waits while any session has a request. It used to pass
only after 30 s of one session's commands. A client whose request is queued
waits while the queue is visibly alive instead of reporting a dead loop.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from eda_agent.bridge import altium_bridge as ab

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "scripts" / "altium" / "Main.pas"
HARNESS = ROOT / "tests" / "cross_validate_pascal.pas"


def _function(code: str, name: str) -> str:
    start = code.index(f"Function {name}(")
    end = code.index("\nEnd;", start) + 5
    return code[start:end]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", "")).strip()


@pytest.mark.parametrize("name", ["PickSession", "NextTurn"])
def test_the_cross_checked_copy_is_the_shipped_function(name):
    main = _function(MAIN.read_text(encoding="ascii"), name)
    copy = _function(HARNESS.read_text(encoding="ascii"), name)
    assert _norm(main) == _norm(copy)


def test_the_scan_picks_through_pick_session_and_notes_only_contention():
    scan = _function(MAIN.read_text(encoding="ascii"), "ScanForRequestFile")
    assert "NextTag := NextTurn(Keys, Marks, SlotSession, HoldCall," in scan
    # Background requests queue as a key of their own; the call mark is
    # read from the id; the server's end-of-call flag is honoured.
    assert "If LowerCase(Copy(IdPart, 19, 1)) = 'q' Then Tag := Tag + '.bg';" in scan
    assert "Mark := CallMark(" in scan and "'callend_'" in scan
    # FindFiles can upper-case the names: the marks are read case-blind,
    # as the tag is. Measured 2026-11-05: an exact 'k' never matched live,
    # and two sessions' tool calls interleaved command by command.
    mark = _function(MAIN.read_text(encoding="ascii"), "CallMark")
    assert "LowerCase(Copy(IdPart, 19, 1)) = 'k'" in mark
    assert "Result := LowerCase(Copy(IdPart, 19, 5));" in mark
    # queue.json is written only while more than one session is pending,
    # so a session working alone sees no change at all.
    note = scan.index("'queue.json'")
    guard = scan.rindex("If Tags.Count > 1 Then", 0, note)
    assert guard < note
    # Each session's next request is its lowest sequence; old ids are one
    # 'legacy' session.
    assert "If Seq < BestSeq[Pick] Then" in scan
    assert "Tag := 'legacy';" in scan
    # Only names this binding has run before: no CompareStr, no 'Now'.
    assert "CompareStr" not in scan and not re.search(r"\bNow\b", scan)


def test_ids_carry_the_session_and_its_order():
    ids = [ab._short_id() for _ in range(3)]
    shape = re.compile(r"^[0-9a-f]{8}-\d{8}-[0-9a-f]{12}$")   # outside a tool call
    assert all(shape.match(i) for i in ids)
    assert len({i[:8] for i in ids}) == 1 and ids[0][:8] == ab._SESSION_TAG
    seqs = [int(i[9:17]) for i in ids]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    # Pascal's IsValidRequestId: letters, digits, '-', '_', at most 64.
    assert all(len(i) <= 64 and re.fullmatch(r"[0-9A-Za-z_-]+", i) for i in ids)


def test_a_live_queue_is_told_from_a_leftover(tmp_path):
    assert not ab._queue_is_alive(tmp_path)
    q = tmp_path / "queue.json"
    q.write_text("{}")
    assert ab._queue_is_alive(tmp_path)
    old = time.time() - 60
    import os
    os.utime(q, (old, old))
    assert not ab._queue_is_alive(tmp_path)
    # This session's own marker is a leftover; another session's is work.
    own = tmp_path / f"progress_{ab._SESSION_TAG}-00000001-abc.json"
    own.write_text("{}")
    assert not ab._queue_is_alive(tmp_path)
    (tmp_path / "progress_ffff0000-00000007-abc.json").write_text("{}")
    assert ab._queue_is_alive(tmp_path)


@pytest.fixture
def bridge(tmp_path):
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    b = ab.AltiumBridge()
    yield b
    b.detach()


def test_a_queued_request_waits_past_its_window(bridge, tmp_path):
    rid = ab._short_id()
    (tmp_path / f"request_{rid}.json").write_text("{}")
    stop = threading.Event()

    def altium_busy_with_another_session():
        # queue.json refreshed while another session holds the slot, then
        # our request is taken and answered.
        for _ in range(8):
            (tmp_path / "queue.json").write_text('{"serving":"ffff0000"}')
            time.sleep(0.2)
        (tmp_path / f"request_{rid}.json").unlink()
        (tmp_path / f"response_{rid}.json").write_text(
            '{"protocol_version": 2, "id": "%s", "success": true, '
            '"data": {"ok": 1}, "error": null}' % rid)
        stop.set()

    t = threading.Thread(target=altium_busy_with_another_session)
    t.start()
    try:
        resp = bridge._poll_response(rid, timeout=0.5)
    finally:
        t.join()
    assert resp.success and resp.data == {"ok": 1}


def test_a_session_alone_still_times_out_on_a_dead_loop(bridge, tmp_path):
    from eda_agent.bridge.exceptions import AltiumTimeoutError

    rid = ab._short_id()
    (tmp_path / f"request_{rid}.json").write_text("{}")
    start = time.monotonic()
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_response(rid, timeout=0.5)
    assert time.monotonic() - start < 5


def test_ids_carry_their_tool_call_or_background_mark(tmp_path):
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    with ab.tool_call() as call:
        a, b = ab._short_id(), ab._short_id()
        with ab.background():
            q = ab._short_id()
    assert a[18:23] == b[18:23] == "k" + call["id"] and len(a) == len(b) == 30
    assert q[18] == "q"
    with ab.following_front():
        assert ab._short_id()[18] == "q"
    with ab.following_front(background=False):
        assert ab._short_id()[18] in "0123456789abcdef"
    # Pascal's IsValidRequestId: letters, digits, '-', '_', at most 64.
    assert all(re.fullmatch(r"[0-9A-Za-z_-]{1,64}", i) for i in (a, q))
    # The call is over: Altium is told, so the turn passes at once.
    flags = list(tmp_path.glob(f"callend_{ab._SESSION_TAG}-*.flag"))
    assert [f.name for f in flags] == [f"callend_{ab._SESSION_TAG}-{call['id']}.flag"]


def test_a_tool_call_that_sent_nothing_leaves_no_flag(tmp_path):
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    with ab.tool_call():
        pass
    assert not list(tmp_path.glob("callend_*"))


def test_the_mark_reaches_the_thread_the_call_runs_on(tmp_path):
    """A call runs on the bridge's executor thread; the mark set by the tool
    call must reach it, or every command would look like no call at all."""
    import asyncio
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    seen = []

    class Probe(ab.AltiumBridge):
        def is_altium_running(self):
            return True

        def _ensure_keepalive(self):
            pass

        def _execute_command(self, command, params, timeout):
            seen.append(ab._short_id())
            return {}

    async def go():
        with ab.tool_call() as call:
            await Probe().send_command_direct("application.ping", {}, 1.0)
            return call["id"]

    call_id = asyncio.run(go())
    assert seen and seen[0][18:23] == "k" + call_id


def test_a_page_action_follows_the_front_on_the_executor_thread(tmp_path):
    """A dashboard action runs an async tool in its own thread; the tool's
    commands go out on the bridge's executor thread. They must still act on
    the board in front, not put the session's own document back first:
    measured 2026-11-05, they did, Altium switched windows on every command
    and the teardrops landed on another open board."""
    import asyncio
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    seen = []

    class Probe(ab.AltiumBridge):
        def is_altium_running(self):
            return True

        def _ensure_keepalive(self):
            pass

        def _execute_command(self, command, params, timeout):
            seen.append((ab._unpinned(command, params), ab._short_id()[18]))
            return {}

    def page_action():
        with ab.following_front(background=False):
            asyncio.run(Probe().send_command_direct("pcb.teardrops", {}, 1.0))

    t = threading.Thread(target=page_action)
    t.start()
    t.join()
    assert seen and seen[0][0] is True
    assert seen[0][1] not in "qk"          # a page action is not background
    # Outside it, the session's document is put back as before.
    seen.clear()
    asyncio.run(Probe().send_command_direct("pcb.teardrops", {}, 1.0))
    assert seen[0][0] is False
