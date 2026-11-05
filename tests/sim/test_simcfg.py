# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Analysis settings land in the project and every sheet, and nowhere else.

The file below follows the shape Altium 26.10.1.6 writes: a project
Config, one DocumentConfig per sheet with its own Config, and a Results
history whose Configs record past runs and must not change.
"""

from __future__ import annotations

import pytest

from eda_agent.sim.simcfg import (SimCfgError, analysis_changes, apply, describe,
                                  new_simcfg, only_analyses, parse_simcfg,
                                  read_simcfg, spice_number, write_simcfg)

FILE = (
    '<?xml version="1.0"?>\r\n'
    '<ProjectConfig xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n'
    '  <Settings>\r\n    <Version>1.1</Version>\r\n    <Affect>Project</Affect>\r\n  </Settings>\r\n'
    '  <Config>OP_Analysis=True|TRAN_StopTime=5u|TRAN_StopTime_UserValue=5u|AC_Variable0=dB(v(out)/v(in))</Config>\r\n'
    '  <Documents>\r\n    <DocumentConfig>\r\n      <Name>amp.SchDoc</Name>\r\n      <Id>ABCDEFGH</Id>\r\n'
    '      <Config>OP_Analysis=False|TRAN_StopTime=5u|R=a&amp;b</Config>\r\n      <Results />\r\n'
    '    </DocumentConfig>\r\n  </Documents>\r\n'
    '  <Results>\r\n    <ResultConfig>\r\n      <Name>Transient Analysis</Name>\r\n'
    '      <Config>TRAN_StopTime=5u</Config>\r\n      <Charts />\r\n    </ResultConfig>\r\n  </Results>\r\n'
    '</ProjectConfig>\r\n')


def test_the_project_and_each_sheet_are_settings_and_the_history_is_not():
    cfg = parse_simcfg(FILE)
    assert cfg.affect == "Project" and cfg.sheets == ["amp.SchDoc"]
    owners = [owner for owner, _ in cfg.settings()]
    assert owners == ["project", "amp.SchDoc"]


def test_a_change_reaches_both_and_leaves_history_and_the_rest_alone():
    cfg = parse_simcfg(FILE)
    new = apply(cfg, analysis_changes(transient={"stop": "2u", "step": "5n"}))
    for _owner, settings in new.settings():
        assert settings["TRAN_StopTime"] == "2u"
        assert settings["TRAN_StopTime_UserValue"] == "2u"
        assert settings["TRAN_StepTime"] == "5n"
        assert settings["TRAN_Analysis"] == "True"
    assert "<Config>TRAN_StopTime=5u</Config>" in new.text, "history changed"
    assert new.text.startswith(FILE[:FILE.index("<Config>")]), "header changed"
    assert "\r\n" in new.text and new.text.count("\r\n") == FILE.count("\r\n")
    # Values that were not asked about survive, escaped as Altium wrote them.
    sheet = dict(new.settings())["amp.SchDoc"]
    assert sheet["R"] == "a&b" and "R=a&amp;b" in new.text
    assert dict(new.settings())["project"]["AC_Variable0"] == "dB(v(out)/v(in))"


def test_ac_dc_and_outputs_write_the_measured_keys():
    ch = analysis_changes(ac={"start": 10, "stop": "100meg", "points": 20, "sweep": "oct"},
                          dc={"source": "V1", "start": 0, "stop": 5, "step": 0.5},
                          outputs=["v(out)", "i(v1)"])
    assert ch["AC_StartFrequency"] == "10" and ch["AC_StopFrequency"] == "100meg"
    assert ch["AC_TestPoints"] == "20" and ch["AC_SweepType"] == "OCT"
    assert ch["DC_PrimarySource"] == "V1" and ch["DC_CustomSource0"] == "V1"
    assert ch["DC_PrimaryStep"] == "0.5" and ch["DC_CustomStep0"] == "0.5"
    assert ch["AC_Variable0"] == "v(out)" and ch["DC_Variable1"] == "i(v1)"
    assert ch["AC_VariableEnabled1"] == "True"


@pytest.mark.parametrize("kwargs, words", [
    ({}, "nothing to set"),
    ({"transient": {"stop": "1u"}}, "needs 'step'"),
    ({"transient": {"stop": "1u", "step": 0}}, "greater than zero"),
    ({"transient": {"stop": "1u", "step": "2u"}}, "longer than the run"),
    ({"transient": {"start": "2u", "stop": "1u", "step": "1n"}}, "after its start"),
    ({"ac": {"start": 10, "stop": 1, "points": 5}}, "above its start"),
    ({"ac": {"start": 10, "stop": 100, "points": 0}}, "at least 1"),
    ({"ac": {"start": 10, "stop": 100, "points": 5, "sweep": "log"}}, "dec, oct or lin"),
    ({"dc": {"start": 0, "stop": 1, "step": 0.1}}, "needs a source"),
    ({"dc": {"source": "V1", "start": 0, "stop": 1, "step": -0.1}}, "points away"),
    ({"transient": {"stop": "abc", "step": "1n"}}, "not a SPICE value"),
])
def test_settings_that_cannot_run_are_refused(kwargs, words):
    with pytest.raises(SimCfgError, match=words):
        analysis_changes(**kwargs)


@pytest.mark.parametrize("text, number", [
    ("5u", 5e-6), ("10n", 1e-8), ("1meg", 1e6), ("2.5k", 2500.0), ("1e-6", 1e-6),
    ("3", 3.0), (4.7e-9, 4.7e-9), ("100MEG", 1e8), ("1kHz", 1e3),
])
def test_spice_numbers(text, number):
    assert spice_number(text) == pytest.approx(number)


def test_a_new_file_reads_back_and_takes_settings(tmp_path):
    path = tmp_path / "project_sim_config.simcfg"
    write_simcfg(apply(new_simcfg(), analysis_changes(transient={"stop": 1e-6, "step": 1e-9})), path)
    cfg = read_simcfg(path)
    [(owner, settings)] = cfg.settings()
    assert owner == "project" and settings["TRAN_StopTime"] == "1e-06"


def test_a_file_that_is_not_settings_is_refused():
    with pytest.raises(SimCfgError):
        parse_simcfg("SDF: 1.0\nTotalPoints: 0\n")


def test_only_analyses_switches_the_named_on_and_every_other_off():
    changes = only_analyses(["tran", "AC sweep"])
    assert changes["TRAN_Analysis"] == "True" and changes["AC_Analysis"] == "True"
    assert changes["OP_Analysis"] == "False" and changes["DC_Analysis"] == "False"
    # The rarer analyses go off too, so nothing rides along.
    assert changes["Noise_Analysis"] == "False" and changes["MonteCarlo_Analysis"] == "False"


def test_only_analyses_refuses_an_unknown_name_and_an_empty_list():
    with pytest.raises(SimCfgError):
        only_analyses(["noise"])
    with pytest.raises(SimCfgError):
        only_analyses([])


def test_describe_reads_the_sheet_when_the_setup_is_per_document():
    text = FILE.replace("<Affect>Project</Affect>", "<Affect>Document</Affect>")
    cfg = apply(parse_simcfg(text), only_analyses(["dc"]))
    sheet = describe(cfg, r"C:\proj\amp.SchDoc")
    assert sheet["source"] == "amp.SchDoc" and sheet["enabled"] == ["dc"]
    # A sheet without its own settings reads the project's.
    other = describe(cfg, "other.SchDoc")
    assert other["source"] == "project"


def test_describe_lists_outputs_and_the_run_history():
    cfg = apply(parse_simcfg(FILE), {"AC_Analysis": "True", "AC_StartFrequency": "1k"})
    setup = describe(cfg)
    assert setup["source"] == "project" and "ac" in setup["enabled"]
    assert setup["settings"]["ac"]["outputs"] == ["dB(v(out)/v(in))"]
    assert setup["settings"]["ac"]["AC_StartFrequency"] == "1k"
    assert setup["results"] == [{"name": "Transient Analysis"}]
