# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The activity log is read once per line as it grows. Reading a text
file with tell() inside a for loop raises; the error left the offset
behind and the same line was taken in again every poll, hundreds of
times."""
from __future__ import annotations


from eda_agent.web.dashboard import ActivityTailer


def _line(i: int) -> str:
    return (f"2026-11-05 14:55:{i:02d}.118,110,pcb.get_layout_model,OK,1344,"
            '{"success":true}\n')


def test_new_lines_are_read_once_each(tmp_path):
    log = tmp_path / "activity.log"
    log.write_text(_line(1), encoding="utf-8")
    tailer = ActivityTailer(log)
    offset = tailer.read_new(0)
    with open(log, "a", encoding="utf-8", newline="") as f:
        for i in range(2, 6):
            f.write(_line(i))
        f.write(_line(6)[:30])                       # a partial line
    # Polled again and again, as the reader's loop does: each line once.
    for _ in range(5):
        offset = tailer.read_new(offset)
    stamps = [e.timestamp for e in tailer.entries]
    assert stamps == [f"2026-11-05 14:55:{i:02d}.118" for i in range(1, 6)]
    # The partial line is taken once it is finished.
    with open(log, "a", encoding="utf-8", newline="") as f:
        f.write(_line(6)[30:])
    for _ in range(3):
        offset = tailer.read_new(offset)
    assert len(tailer.entries) == 6
    assert tailer.entries[-1].timestamp == "2026-11-05 14:55:06.118"
    assert offset == log.stat().st_size


def test_a_log_that_shrank_is_read_again_from_the_start(tmp_path):
    log = tmp_path / "activity.log"
    log.write_text(_line(1) + _line(2), encoding="utf-8")
    tailer = ActivityTailer(log)
    offset = tailer.read_new(0)
    log.write_text(_line(3), encoding="utf-8")
    tailer.read_new(offset)
    assert [e.timestamp[-6:] for e in tailer.entries] == ["01.118", "02.118", "03.118"]
