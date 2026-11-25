# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What a caller's text may make run.

The convert tools run the kicad-cli they are given, and kicad.exe and
python.exe beside it, so only a kicad-cli.exe on a local drive is taken.
ngspice runs ``shell`` from a deck's .control block in batch mode
(ngspice_con -b, Windows, measured 2026-11-23), so every deck that reaches
a simulator has its .control commands checked against an allow-list, in
the deck and in every file it includes.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.convert.altium_kicad import pipeline
from eda_agent.sim import deck_runner, ngspice

B = chr(92)          # a backslash, kept out of string escapes

#: The deck that ran a shell command and wrote files through ngspice -b.
_PROBE = ("probe\nR1 1 0 1k\nV1 1 0 1\n.control\nop\n"
          "shell echo shell-ran > shell_out.txt\nprint v(1) > redirect_out.txt\n"
          ".endc\n.end\n")


# --- kicad_cli ---------------------------------------------------------------

def test_only_a_kicad_cli_on_a_local_drive_is_taken(tmp_path):
    good = tmp_path / "kicad-cli.exe"
    good.write_text("")
    other = tmp_path / "cmd.exe"
    other.write_text("")
    assert pipeline.kicad_cli_refusal(str(good)) == ""
    assert pipeline.find_kicad_cli(str(good)) == os.path.realpath(good)
    assert "kicad-cli.exe" in pipeline.kicad_cli_refusal(str(other))
    assert pipeline.find_kicad_cli(str(other)) == ""
    assert "does not exist" in pipeline.kicad_cli_refusal(str(tmp_path / "x" / "kicad-cli.exe"))
    assert pipeline.kicad_cli_refusal("") == ""


@pytest.mark.parametrize("share", [B * 2 + "host" + B + "s" + B + "kicad-cli.exe",
                                   "//host/s/kicad-cli.exe"])
def test_a_share_is_refused_before_anything_looks_at_it(monkeypatch, share):
    def touched(*a, **k):
        raise AssertionError("the path was resolved")
    monkeypatch.setattr(pipeline.os.path, "realpath", touched)
    monkeypatch.setattr(pipeline.os.path, "isfile", touched)
    assert "network" in pipeline.kicad_cli_refusal(share)


def test_a_mapped_network_drive_is_refused(monkeypatch, tmp_path):
    good = tmp_path / "kicad-cli.exe"
    good.write_text("")
    monkeypatch.setattr(pipeline, "_on_network", lambda p: True)
    assert "network" in pipeline.kicad_cli_refusal(str(good))


def test_a_link_named_kicad_cli_is_judged_by_its_target(tmp_path):
    target = tmp_path / "calc.exe"
    target.write_text("")
    link = tmp_path / "kicad-cli.exe"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("this account cannot make links")
    assert "calc.exe" in pipeline.kicad_cli_refusal(str(link))


def test_the_conversion_stops_at_a_refused_kicad_cli(tmp_path):
    report = pipeline.convert(str(tmp_path / "P.PrjPcb"), str(tmp_path / "out"),
                              kicad_cli=str(tmp_path / "cmd.exe"))
    assert report["ok"] is False and "kicad-cli.exe" in report["reason"]


def _convert_tools(monkeypatch):
    from eda_agent.tools import convert

    class Bridge:
        async def send_command_async(self, *a, **k):
            raise AssertionError("Altium was asked before the path was checked")

    monkeypatch.setattr(convert, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    convert.register_convert_tools(Mcp())
    return tools


def test_both_convert_tools_refuse_before_touching_altium(monkeypatch, tmp_path):
    tools = _convert_tools(monkeypatch)
    bad = str(tmp_path / "cmd.exe")
    out = asyncio.run(tools["proj_convert_to_kicad"](output_dir=str(tmp_path / "o"),
                                                     kicad_cli=bad))
    assert out["ok"] is False and "kicad-cli.exe" in out["reason"]
    out = asyncio.run(tools["proj_import_from_kicad"](
        kicad_project=str(tmp_path / "p.kicad_pro"), output_dir=str(tmp_path / "o"),
        kicad_cli=bad))
    assert out["ok"] is False and "kicad-cli.exe" in out["reason"]


# --- .control blocks -----------------------------------------------------------

def test_the_probe_deck_is_refused(tmp_path):
    refused = deck_runner.unsafe_lines(_PROBE, tmp_path, [tmp_path])
    assert refused == ["line 6: shell is not a simulation or output command and "
                       "does not run here"]


@pytest.mark.parametrize("command", ["shell calc", "SHELL calc", "source x.cir",
                                     "exec calc", "codemodel x.cm", "cd C:/",
                                     "edit", "load x.raw", "gnuplot g v(1)",
                                     "alias run shell calc", "circbyline .include x",
                                     "$cmd", "op; shell calc"])
def test_a_command_off_the_list_is_refused(tmp_path, command):
    deck = f"t\nR1 1 0 1k\n.control\n{command}\n.endc\n.end"
    assert deck_runner.unsafe_lines(deck, tmp_path, [tmp_path])


@pytest.mark.parametrize("line", ["write C:/x.raw", "write ../x.raw", "wrdata $p v(1)",
                                  "hardcopy " + B + "x.ps", "print v(1) > C:/o.txt",
                                  "echo x >> ../o.txt", "print v(1) > $f"])
def test_files_stay_in_the_run_folder(tmp_path, line):
    deck = f"t\n.control\nrun\n{line}\n.endc\n.end"
    assert deck_runner.unsafe_lines(deck, tmp_path, [tmp_path])


def test_the_decks_built_here_pass(tmp_path):
    own = deck_runner.with_ngspice_control("t\nR1 1 0 1k\nV1 1 0 1\n.op\n.end", "deck.raw")
    loop = (".control\nrun\nmeas tran vpk MAX v(n1)\nmeas tran vmin MIN v(n1)\n"
            "echo \"$&vpk $&vmin\" > peak.txt\n.endc\n.end")
    gain = (".control\nac dec 40 1 1e+08\nlet tv = -v(a)/v(b)\nalter Vlg_i acmag=0\n"
            "alter Ilg_i acmag=1\nac dec 40 1 1e+08\nlet ti = -ac2.i(vlg_i)/ac2.i(vlg_s)\n"
            "let t = (tvv*ti - 1)/(tvv + ti + 2)\nwrdata loopgain.txt t\n.endc\n.end")
    flow = (".control\n* a comment\nset filetype=ascii\nforeach v 1 2\n  if $v > 1\n"
            "    echo big\n  else\n    echo small\n  end\nend\nquit\n.endc\n.end")
    for deck in (own, loop, gain, flow):
        assert deck_runner.unsafe_lines(deck, tmp_path, [tmp_path]) == [], deck
        assert deck_runner.unsafe_control(deck, tmp_path) == [], deck


def test_an_included_file_is_screened_too(tmp_path):
    (tmp_path / "inner.lib").write_text(".model d1 d\n.control\nshell calc\n.endc\n")
    (tmp_path / "outer.lib").write_text(".include inner.lib\n")
    refused = deck_runner.unsafe_lines("t\n.include outer.lib\n.op\n.end", tmp_path, [tmp_path])
    assert refused == ["inner.lib line 3: shell is not a simulation or output command "
                       "and does not run here"]
    # Decks built here around a caller's model file: the file may be anywhere
    # local, but it is screened all the same.
    elsewhere = tmp_path / "vendor"
    elsewhere.mkdir()
    (elsewhere / "m.lib").write_text(".control\nsource x\n.endc\n")
    deck = f"t\n.include {elsewhere / 'm.lib'}\n.op\n.end"
    assert deck_runner.unsafe_control(deck, tmp_path)
    (tmp_path / "loop.lib").write_text(".include loop.lib\n")
    assert deck_runner.unsafe_lines(".include loop.lib\n.op", tmp_path, [tmp_path]) == []


@pytest.mark.parametrize("target", [B * 2 + "host" + B + "s" + B + "m.lib", "//host/s/m.lib",
                                    B + "Windows" + B + "m.lib"])
def test_an_include_from_a_share_or_a_root_is_refused(tmp_path, target):
    assert deck_runner.unsafe_lines(f".include {target}\n.op", tmp_path, [tmp_path])
    if target.startswith((B * 2, "//")):
        assert deck_runner.unsafe_control(f".include {target}\n.op", tmp_path)


def test_no_simulator_starts_for_a_refused_deck(monkeypatch, tmp_path):
    def started(*a, **k):
        raise AssertionError("a simulator was started")
    monkeypatch.setattr(subprocess, "run", started)
    monkeypatch.setattr(deck_runner, "pick_simulator",
                        lambda wanted="auto": {"engine": "ngspice", "path": "ngspice"})
    out = ngspice.run_deck(_PROBE, ngspice="ngspice")
    assert out["ok"] is False and "shell" in out["problems"][0]
    out = deck_runner.simulate(_PROBE, tmp_path)
    assert out["ok"] is False and "shell" in out["problems"][0]


def test_altiums_simulator_gets_no_refused_deck(monkeypatch, tmp_path):
    from eda_agent.tools import sim as sim_tools

    sent, tools = [], {}

    class Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append(command)
            return {}

    monkeypatch.setattr(sim_tools, "get_bridge", lambda: Bridge())
    sim_tools.register_sim_tools(Mcp())
    path = tmp_path / "x.nsx"
    out = asyncio.run(tools["sim_run_deck"](
        deck="t\nR1 1 0 1k\nV1 1 0 1\n.OP\n.control\nshell calc\n.endc\n.END",
        file_path=str(path)))
    assert out["success"] is False and sent == [] and not path.exists()


@pytest.mark.skipif(not shutil.which("ngspice_con") and not ngspice.find_ngspice(),
                    reason="ngspice is not installed")
def test_a_deck_with_its_own_control_block_still_runs(tmp_path):
    deck = ("rc\nV1 in 0 PULSE(0 1 0 1n)\nR1 in out 1k\nC1 out 0 1u\n.tran 10u 5m\n.control\nrun\n"
            "meas tran vend FIND v(out) AT=5m\n.endc\n.end\n")
    out = deck_runner.simulate(deck, tmp_path, engine="ngspice")
    assert out["ok"] is True, out
    assert abs(out["measures"]["vend"] - 0.99326) < 1e-3
    refused = deck_runner.simulate(_PROBE, tmp_path, engine="ngspice")
    assert refused["ok"] is False
    assert not list(Path(tmp_path).rglob("shell_out.txt"))
