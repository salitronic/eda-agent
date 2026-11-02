# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_replicate_layout counts each internal and shared net once.

PCB_ReplicateLayout collects the nets of the group's pads into GroupNets,
OutsideNets and InternalNets, each made with Duplicates := dupIgnore. A
TStringList honours Duplicates only when it is Sorted, so the unsorted
lists took one entry per PAD: a net on ten pads of the group was looked at
ten times, and the reply's internal_nets and shared_nets_skipped counted it
ten times. Which nets were copied did not change (membership is IndexOf).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"
_LISTS = ("GroupNets", "OutsideNets", "InternalNets")


def _replicate():
    return load(_PCB)["PCB_ReplicateLayout"]


def _creation(body, name):
    line = re.search(rf"^\s*{name} := TStringList\.Create;.*$", body, re.M)
    assert line, f"{name} is not made where it was"
    return line.group(0).strip()


def test_each_net_list_is_sorted_before_it_drops_duplicates():
    body = _replicate()
    for name in _LISTS:
        line = _creation(body, name)
        assert f"{name}.Sorted := True;" in line
        assert line.index(f"{name}.Sorted := True;") < line.index(f"{name}.Duplicates := dupIgnore;")


def test_the_counts_come_from_those_lists():
    body = _replicate()
    assert "JsonInt('internal_nets', InternalNets.Count)" in body
    assert "If OutsideNets.IndexOf(NetName) < 0 Then" in body
    assert "If IsGroup Then GroupNets.Add(Pad.Net.Name)" in body


def test_a_net_added_once_per_pad_is_kept_once(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    body = _replicate()
    made = "\n".join("  " + _creation(body, name) for name in _LISTS)
    program = "\n".join([
        "program nets;", "{$mode delphi}", "uses Classes, SysUtils;",
        "var GroupNets, OutsideNets, InternalNets: TStringList; I: Integer;",
        "begin",
        made,
        # GND on three pads of the group and one outside, VIN on two inside.
        "  GroupNets.Add('GND'); GroupNets.Add('VIN'); GroupNets.Add('GND');",
        "  GroupNets.Add('VIN'); GroupNets.Add('GND');",
        "  OutsideNets.Add('GND'); OutsideNets.Add('GND');",
        "  for I := 0 to GroupNets.Count - 1 do",
        "    if OutsideNets.IndexOf(GroupNets[I]) < 0 then InternalNets.Add(GroupNets[I]);",
        "  if GroupNets.Count <> 2 then Halt(1);",
        "  if OutsideNets.Count <> 1 then Halt(2);",
        "  if InternalNets.Count <> 1 then Halt(3);",
        "end.",
    ])
    path = tmp_path / "nets.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("nets.exe" if os.name == "nt" else "nets")
    assert subprocess.run([str(exe)], capture_output=True, text=True).returncode == 0
