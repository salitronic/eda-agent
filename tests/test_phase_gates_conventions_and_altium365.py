# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Stages to production, how far obj_query reaches into Altium's SDK,
simulating a deck in ngspice or LTspice, a company's conventions, and
Altium 365.

All offline. The ngspice and LTspice runs were measured on this machine
(both gave 4.96629 V and 2.1972 ms on one RC deck); here the simulator
choice, the deck screening and both log and raw formats are pinned with
text taken from those runs.
"""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error
from datetime import datetime, timedelta

import pytest

from eda_agent import altium365
from eda_agent.design import company_rules, library_conventions, phase_gates
from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_compare import compare_plans
from eda_agent.sim import deck_runner
from eda_agent.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def tools():
    from eda_agent.tools import register_backend
    registry = ToolRegistry()
    register_backend(registry, "altium")
    return registry


# --- stages to production ---------------------------------------------------------------

def test_every_tool_a_check_names_exists_here(tools):
    named = {t for check in phase_gates.CHECKS.values() for t in check.tools}
    # A member of a family tool counts; the reply names the family tool.
    unknown = sorted(t for t in named
                     if tools.get(t) is None and t not in tools.hidden)
    assert not unknown, f"checks name tools this server does not have: {unknown}"
    assert {c.stage for c in phase_gates.CHECKS.values()} == set(phase_gates.STAGES)


def test_a_product_is_in_the_first_stage_that_does_not_clear(tmp_path):
    ledger = phase_gates.Ledger(tmp_path / "p.jsonl")
    for check in phase_gates.checks_of("feasibility"):
        ledger.add(check.key, phase_gates.MET, source="test", detail="done", revision="A")
    stage, reviews = phase_gates.standing(ledger.answers(), "A")
    assert stage == "prototype" and reviews[0].cleared
    # Answers given for another revision stop counting.
    stage, reviews = phase_gates.standing(ledger.answers(), "B")
    assert stage == "feasibility"
    assert "revision 'A'" in reviews[0].lines[0].ignored_because


def test_answers_age_out_but_facts_about_the_product_do_not(tmp_path):
    ledger = phase_gates.Ledger(tmp_path / "p.jsonl")
    long_ago = datetime.now() - timedelta(days=40)
    ledger.add("feasibility:key-parts-buyable", phase_gates.MET, when=long_ago)
    ledger.add("feasibility:spec-closed", phase_gates.MET, when=long_ago)
    lines = {line.check.key: line for line in
             phase_gates.review("feasibility", ledger.answers()).lines}
    aged = lines["feasibility:key-parts-buyable"]
    assert aged.outcome == phase_gates.OPEN and "30 days" in aged.ignored_because
    assert lines["feasibility:spec-closed"].outcome == phase_gates.MET


def test_a_waiver_needs_a_reason_and_a_torn_line_loses_only_itself(tmp_path):
    ledger = phase_gates.Ledger(tmp_path / "p.jsonl")
    with pytest.raises(ValueError, match="waiver needs a detail"):
        ledger.add("prototype:drc", phase_gates.WAIVED)
    ledger.add("prototype:drc", phase_gates.WAIVED, detail="the fab runs DRC")
    with ledger.path.open("a", encoding="utf-8") as out:
        out.write('{"key": "prototype:erc", "outc')
    assert [a.key for a in ledger.answers()] == ["prototype:drc"]
    with pytest.raises(ValueError, match="no check"):
        ledger.add("prototype:nothing", phase_gates.MET)
    assert phase_gates.ledger_name("../../x") == "x.jsonl"
    assert phase_gates.stage_from("Design Validation") == "dvt"
    with pytest.raises(ValueError):
        phase_gates.stage_from("mass production")


def test_the_latest_answer_wins_whatever_order_the_file_holds(tmp_path):
    ledger = phase_gates.Ledger(tmp_path / "p.jsonl")
    now = datetime.now()
    ledger.add("prototype:drc", phase_gates.MET, when=now)
    ledger.add("prototype:drc", phase_gates.UNMET, when=now - timedelta(hours=1))
    review = phase_gates.review("prototype", ledger.answers())
    assert next(l for l in review.lines if l.check.key == "prototype:drc").outcome == "met"


def test_the_stage_tools_record_and_review(tools, tmp_path, monkeypatch):
    from eda_agent import config
    monkeypatch.setattr(config.get_config(), "workspace_dir", tmp_path, raising=False)
    answer = tools.get("design_phase_answer").fn
    assert asyncio.run(answer("feasibility:cost-target", "waived"))["ok"] is False
    recorded = asyncio.run(answer("feasibility:cost-target", "met",
                                  detail="2.40 USD at 10k", product="lamp"))
    assert recorded["ok"] and "feasibility:cost-target" not in recorded["holding"]
    gate = asyncio.run(tools.get("design_phase_gate").fn("feasibility", product="lamp"))
    row = next(c for c in gate["checks"] if c["key"] == "feasibility:cost-target")
    assert row["outcome"] == "met" and "feasibility:outline-agreed" in gate["for_people"]
    standing = asyncio.run(tools.get("design_phase_standing").fn(product="lamp"))
    assert standing["stage"] == "feasibility" and not standing["in_production"]


# --- how far the property tools reach into the SDK ---------------------------------------

def test_the_chains_and_type_words_are_read_from_the_pascal():
    from eda_agent.diag import sdk_reach
    tested = sdk_reach.names_tested("Generic.pas", "GetSchProperty")
    assert {"Location", "OverrideDisplayString", "UniqueId"} <= tested
    assert sdk_reach.type_words("SCH")["port"] == "ePort"
    assert sdk_reach.type_words("PCB")["track"] == "eTrackObject"


def test_interfaces_are_found_in_the_index_and_inherited_members_count(tmp_path):
    from eda_agent.diag import sdk_reach
    index = {"interfaces": {
        "IPCB_Primitive": {"bases": [], "methods": [["GetState_Layer", "", "Int32"],
                                                    ["SetState_Layer", "", "Void"]]},
        "IPCB_Track": {"bases": ["IPCB_Primitive"], "methods": [
            ["GetState_Width", "", "Int32"], ["GetState_Mystery", "", "Int32"]]},
        "IPCB_Polygon": {"bases": [], "methods": []},
        "ISch_Component": {"bases": [], "methods": []}}}
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    sdk = sdk_reach.SdkIndex(path)
    assert sdk.interface_for("eTrackObject", "IPCB_") == "IPCB_Track"
    assert sdk.interface_for("ePolyObject", "IPCB_") == "IPCB_Polygon"
    assert sdk.interface_for("eSchComponent", "ISch_") == "ISch_Component"
    assert sdk.interface_for("eNothing", "IPCB_") is None
    reads, writes = sdk.properties("IPCB_Track")
    assert reads == {"Layer", "Width", "Mystery"} and writes == {"Layer"}
    row = next(r for r in sdk_reach.measure(sdk) if r.interface == "IPCB_Track")
    assert "Mystery" not in row.reached and {"Width", "Layer"} <= row.reached


# --- simulating a deck ------------------------------------------------------------------------

def test_auto_takes_ngspice_first_then_ltspice(monkeypatch):
    monkeypatch.setattr(deck_runner, "find_ngspice", lambda: "ng.exe")
    monkeypatch.setattr(deck_runner, "find_ltspice", lambda: "lt.exe")
    assert deck_runner.pick_simulator("auto")["engine"] == "ngspice"
    assert deck_runner.pick_simulator("LTspice")["engine"] == "ltspice"
    monkeypatch.setattr(deck_runner, "find_ngspice", lambda: None)
    fallback = deck_runner.pick_simulator("auto")
    assert fallback["engine"] == "ltspice" and "ngspice is not" in fallback["why"]
    assert deck_runner.pick_simulator("ngspice")["install_offers"]
    monkeypatch.setattr(deck_runner, "find_ltspice", lambda: None)
    assert set(deck_runner.pick_simulator("auto")["install_offers"]) == {"ngspice", "ltspice"}
    assert deck_runner.pick_simulator("spectre")["engine"] == ""


def test_a_deck_that_reaches_outside_its_folders_is_refused(tmp_path):
    folders = [tmp_path / "ws"]
    assert deck_runner.unsafe_lines(".include models.lib\n.op", tmp_path, folders) == []
    assert deck_runner.unsafe_lines('.include "my models.lib"\n.op', tmp_path, folders) == []
    refused = deck_runner.unsafe_lines(
        ".include C:/Windows/x.lib\n.ferret http://a/b\n.lib ../../x.lib\n"
        ".wave C:/out.wav 16 44k V(a)\n.include C:\\Windows\\y.lib", tmp_path, folders)
    assert len(refused) == 5


def test_ngspice_gets_a_control_block_unless_the_deck_has_one():
    deck = deck_runner.with_ngspice_control("* t\nR1 a 0 1k\n.op\n.end", "deck.raw")
    assert deck.splitlines()[-6:] == [".control", "set filetype=ascii", "run",
                                      "write deck.raw", ".endc", ".end"]
    own = "* t\n.op\n.control\nrun\n.endc\n.end"
    assert deck_runner.with_ngspice_control(own, "x") == own


def test_both_simulators_logs_and_a_failure_are_read():
    lt = ("LTspice 24.0.12 for Windows\nvend: v(out)=4.96629 at 0.005\n"
          "trise=0.00219722 FROM 0.000105862 TO 0.00230308\n\nTotal elapsed time: 0.120 seconds.")
    ng = ("vend                =  4.96629e+00\n"
          "trise               =  2.19721e-03 targ=  2.30307e-03 trig=  1.05862e-04\n")
    assert deck_runner.parse_log(lt)["measures"] == {"vend": 4.96629, "trise": 0.00219722}
    assert deck_runner.parse_log(ng)["measures"] == {"vend": 4.96629, "trise": 0.00219721}
    failed = "Circuit: * bad\nFatal Error: Unknown subcircuit called in:\n  x1 a b nosuch\n"
    (error,) = deck_runner.parse_log(failed)["errors"]
    assert error.startswith("Fatal Error") and error.endswith("x1 a b nosuch")


def test_a_failed_ltspice_log_in_utf16_without_a_mark_reads(tmp_path):
    log = tmp_path / "deck.log"
    log.write_bytes("LTspice 24.0.12 for Windows\nFatal Error: x\n".encode("utf-16-le"))
    assert deck_runner.decode_output(log).startswith("LTspice 24")


def test_ascii_raw_files_read_real_and_complex_analyses():
    raw = ("Title: * rc\nPlotname: Transient Analysis\nFlags: real forward\n"
           "No. Variables: 2\nNo. Points: 2\nVariables:\n\t0\ttime\ttime\n\t1\tV(out)\tvoltage\n"
           "Values:\n0\t\t0.0\n\t0.0\n1\t\t1e-3\n\t3.16\n"
           "Title: * ac\nPlotname: AC Analysis\nFlags: complex forward log\n"
           "No. Variables: 2\nNo. Points: 1\nVariables:\n\t0\tfrequency\tfrequency\n"
           "\t1\tV(out)\tvoltage\nValues:\n0\t\t1.0e+01,0.0e+00\n\t6.0e-01,-8.0e-01\n")
    transient, ac = deck_runner.parse_ascii_raw(raw)
    assert transient.samples[1] == [0.0, 3.16] and not transient.is_complex
    assert ac.is_complex and abs(ac.samples[1][0]) == pytest.approx(1.0)
    summary = deck_runner.describe(ac, ["V(out)"], samples=5)
    assert summary["waves"]["V(out)"]["mag_db"] == [pytest.approx(0.0)]
    with pytest.raises(ValueError, match="binary"):
        deck_runner.parse_ascii_raw("Title: x\nBinary:\n")


# --- library conventions ---------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("CAPC1005X55N", {"family": "chip", "length": 1.0, "width": 0.5, "height": 0.55,
                      "pins": 2}),
    ("SOT95P280X130-5N", {"family": "two-sided", "pitch": 0.95, "pins": 5}),
    ("QFN50P400X400X100-21N", {"family": "four-sided", "span2": 4.0, "pins": 21}),
])
def test_an_ipc_name_decodes_to_its_package(name, expected):
    decoded = library_conventions.read_ipc_name(name)
    assert {k: decoded[k] for k in expected} == expected


def test_an_ipc_name_is_held_against_the_pads():
    pads = ([{"x_mm": -1.3, "y_mm": 0.95 * (1 - i)} for i in range(3)]
            + [{"x_mm": 1.3, "y_mm": -0.95}, {"x_mm": 1.3, "y_mm": 0.95}])
    assert library_conventions.compare_ipc_name("SOT95P280X130-5N", pads)["matches"]
    wrong = library_conventions.compare_ipc_name("SOT65P210X110-6N", pads)
    assert len(wrong["issues"]) == 2
    assert library_conventions.compare_ipc_name("LED_WS2812B-2020", pads)["ipc_name"] is False


def test_without_a_naming_policy_nothing_is_enforced(tools, monkeypatch, tmp_path):
    monkeypatch.delenv("EDA_AGENT_POLICY_DIR", raising=False)
    unset = asyncio.run(tools.get("lib_check_naming").fn("symbol", ["anything at all"]))
    assert unset["ok"] is False and "no naming policy" in unset["reason"]
    (tmp_path / "naming.json").write_text(json.dumps({
        "component": {"template": "{mfr_short}_{mpn}", "pattern": "[A-Z]{2,8}_[A-Z0-9-]+"},
        "manufacturer_abbreviations": {"Texas Instruments": "TI"}}), encoding="utf-8")
    monkeypatch.setenv("EDA_AGENT_POLICY_DIR", str(tmp_path))
    made = asyncio.run(tools.get("lib_make_name").fn(
        "component", {"mpn": "tps62130rgtr", "manufacturer": "Texas Instruments"}))
    assert made["name"] == "TI_TPS62130RGTR"
    checked = asyncio.run(tools.get("lib_check_naming").fn("component", ["TI_X1", "bad name"]))
    assert [o["name"] for o in checked["off_policy"]] == ["bad name"]
    missing = asyncio.run(tools.get("lib_make_name").fn("component", {"mpn": "X"}))
    assert missing["ok"] is False and "mfr_short" in missing["reason"]


def test_a_pin_table_turns_into_symbol_pins(tools):
    table = "number,name,type\n1,VIN,power\n2,GND,power\n3,EN,input\n4,FB,input\n5,SW,output"
    read = asyncio.run(tools.get("lib_read_pin_table").fn(table, package_pins=5))
    assert read["ok"] and [p["name"] for p in read["right_pins"]] == ["SW"]
    assert [p["name"] for p in read["left_pins"]] == ["VIN", "GND", "EN", "FB"]
    broken = asyncio.run(tools.get("lib_read_pin_table").fn(
        "number,name,type\n1,A,input\n1,B,wobbly", package_pins=3))
    assert len(broken["errors"]) == 3 and "left_pins" not in broken
    warned = asyncio.run(tools.get("lib_read_pin_table").fn(
        "number,name,type\n1,VDD,input\n2,SDA,io\n3,SDA,io"))
    assert len(warned["warnings"]) == 2


# --- company design rules -----------------------------------------------------------------------

def _rules(*checks):
    return {"name": "t", "checks": list(checks)}


def test_company_rules_find_skip_and_carry_notes():
    rules = _rules({"id": "mpn", "check": "params_present", "level": "error",
                    "parameters": ["MPN"]},
                   {"id": "gap", "check": "board_rule_present", "level": "error",
                    "kind": "clearance", "at_least": {"Gap": "8mil"}},
                   {"id": "side", "check": "parts_on_side", "prefixes": ["J"], "side": "Top"},
                   {"id": "say", "check": "note", "text": "keep it short"},
                   {"id": "off", "check": "net_names", "pattern": "x", "off": True})
    data = {"symbols": [{"designator": "R1"}, {"designator": "R2"}],
            "symbol_params": {"R1": {"MPN": "X"}, "R2": {}},
            "board_rules": [{"name": "Silk", "descriptor": "Silk to Silk (Clearance=2mil)",
                             "enabled": True},
                            {"name": "Clearance", "descriptor": "Clearance Constraint (Gap=6mil)",
                             "enabled": True}]}
    result = company_rules.run_rules(rules, data)
    assert [(f["check"], f["object"]) for f in result["findings"]] == [("mpn", "R2"),
                                                                       ("gap", "Clearance")]
    assert result["not_run"][0]["check"] == "side"
    assert result["notes"][0]["text"] == "keep it short"
    assert not result["passed"] and result["tally"]["error"] == 2
    assert company_rules.inputs_of(rules) == {"symbols", "symbol_params", "board_rules",
                                              "placed"}


def test_net_checks_cover_names_lone_pins_and_needed_parts():
    rules = _rules({"id": "n", "check": "net_names", "pattern": "[A-Z0-9_]+"},
                   {"id": "lone", "check": "no_lone_pins", "except": ["NC_.*"]},
                   {"id": "tvs", "check": "net_has_part", "nets": "^USB_", "prefix": "D"})
    data = {"nets": [{"name": "usb_dp", "pins": ["J1.2", "U1.4"]},
                     {"name": "NetR1_2", "pins": ["R1.2"]},
                     {"name": "NC_1", "pins": ["U1.9"]},
                     {"name": "USB_DM", "pins": ["J1.3", "U1.5", "D1.1"]}]}
    found = {(f["check"], f["object"]) for f in company_rules.run_rules(rules, data)["findings"]}
    assert found == {("n", "usb_dp"), ("lone", "NetR1_2")}


def test_a_rules_file_with_mistakes_lists_each_one():
    problems = company_rules.problems_in(_rules(
        {"id": "a", "check": "nope"},
        {"id": "a", "check": "net_names", "pattern": "("},
        {"id": "b", "check": "param_matches", "level": "loud"},
        {"id": "c", "check": "board_rule_present", "kind": "x", "at_least": {"Gap": "8 parsecs"}}))
    # unknown check, id reused, bad pattern, two fields missing, bad level, bad length
    assert len(problems) == 7
    assert company_rules.problems_in([]) == ["a rules file is an object with a 'checks' list"]


def test_an_altium_rules_export_reads_as_records():
    text = "|RULEKIND=Clearance|NAME=Clearance|GAP=10mil|ENABLED=TRUE|\n\n|RULEKIND=Width|NAME=W|"
    first, second = company_rules.read_rules_export(text)
    assert first == {"RULEKIND": "Clearance", "NAME": "Clearance", "GAP": "10mil",
                     "ENABLED": "TRUE"}
    assert second == {"RULEKIND": "Width", "NAME": "W"}


def _plan(parts, nets):
    return DesignPlan.model_validate({
        "spec": "s", "summary": "s", "sheets": [{"name": "main"}],
        "parts": [{"refdes": r, "lib_ref": "R", "value": v} for r, v in parts],
        "nets": [{"name": n, "pins": [{"refdes": p.split(".")[0], "pin": p.split(".")[1]}
                                      for p in pins]} for n, pins in nets]})


def test_two_plans_compare_by_parts_nets_and_pins():
    before = _plan([("R1", "1k"), ("R2", "2k")],
                   [("A", ["R1.1", "R2.1"]), ("B", ["R1.2", "R2.2"])])
    after = _plan([("R1", "10k"), ("R3", "3k")],
                  [("A", ["R1.1", "R3.1"]), ("C", ["R1.2", "R3.2"])])
    result = compare_plans(before, after)
    assert result["new_parts"] == ["R3"] and result["dropped_parts"] == ["R2"]
    assert result["edited_parts"] == [{"refdes": "R1",
                                       "fields": {"value": {"was": "1k", "now": "10k"}}}]
    assert result["new_nets"] == ["C"] and result["dropped_nets"] == ["B"]
    assert result["pins_changing_net"] == [{"pin": "R1.2", "was": "B", "now": "C"}]
    assert compare_plans(before, before)["identical"]


# --- Altium 365 ----------------------------------------------------------------------------------

WORKSPACE_HOST = "workspace.example.com"


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _session(answer, config=None):
    requests = []

    def urlopen(request, timeout=0):
        requests.append(request)
        body = json.loads(request.data) if request.data and request.data.startswith(b"{") else {}
        reply = answer(body)
        if isinstance(reply, Exception):
            raise reply
        return _Reply(json.dumps(reply).encode())
    config = config or altium365.Altium365Config(domain=WORKSPACE_HOST, workspace_token="T")
    return altium365.Altium365Session(config, urlopen=urlopen, pause=lambda _s: None), requests


def test_the_workspace_route_posts_there_with_its_token_and_remembers():
    session, requests = _session(lambda b: {"data": {"desWorkspaceInfos": [{"url": "u/"}]}})
    assert session.list_workspaces() == [{"url": "u/"}]
    assert requests[0].full_url == f"https://{WORKSPACE_HOST}/api/graphql/"
    assert requests[0].headers["Authorization"] == "Bearer T"
    session.list_workspaces()
    assert len(requests) == 1           # answered from memory the second time


def test_busy_answers_are_retried_and_then_given_up_on():
    busy = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
    calls = []

    def answer(body):
        calls.append(1)
        return busy if len(calls) < 3 else {"data": {"desProjectById": {"id": "P1"}}}
    session, _ = _session(answer)
    assert session.get_project("P1") == {"id": "P1"} and len(calls) == 3
    session, requests = _session(lambda b: busy)
    with pytest.raises(altium365.Altium365Error, match="HTTP 503"):
        session.get_project("P2")
    assert len(requests) == 4           # three retries after the first


def test_reading_queries_refuse_changes_and_changes_are_built_safely():
    with pytest.raises(altium365.Altium365Error):
        altium365.refuse_changes("query { a } mutation { b }")
    altium365.refuse_changes('query { a(note: "a mutation inside a string") }')
    text = altium365.parameters_operation("C1", [{"name": "Value", "value": 10}], "why", False)
    assert text.startswith("mutation { desUpdateComponentRevisionParameters(input: {")
    assert '"10"' in text
    with pytest.raises(altium365.Altium365Error):
        altium365.gql_value({"bad key": 1})
    assert altium365.gql_value([True, 1.5, "a\"b"]) == '[true, 1.5, "a\\"b"]'


def test_an_unconfigured_route_and_a_refused_token_are_said_plainly():
    session, _ = _session(lambda b: {}, altium365.Altium365Config())
    with pytest.raises(altium365.Altium365Error, match="not set up"):
        session.list_workspaces()
    refused = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b""))
    session, _ = _session(lambda b: refused)
    with pytest.raises(altium365.Altium365Error, match="did not accept the token"):
        session.get_project("P1")


def test_the_environment_sets_the_route_and_the_allowed_workspaces():
    config = altium365.read_environment({
        "EDA_AGENT_ALTIUM365_DOMAIN": f"https://{WORKSPACE_HOST}/",
        "EDA_AGENT_ALTIUM365_TOKEN": " T ",
        "EDA_AGENT_ALTIUM365_WORKSPACES": "https://one, https://two/"})
    assert config.mode == "workspace" and config.domain == WORKSPACE_HOST
    assert config.workspace_token == "T"
    assert config.allowed_workspaces == ("https://one/", "https://two/")
    assert altium365.read_environment({"EDA_AGENT_NEXAR_APP_ID": "x"}).mode == "nexar"
    assert altium365.read_environment({}).mode == ""


def test_a_change_without_confirm_sends_nothing(tools):
    preview = asyncio.run(tools.get("altium365_release_component").fn(
        {"workspaceUrl": "https://w/", "componentItemName": "CMP-1"}))
    assert preview["sent"] is False and "desReleaseComponent" in preview["would_send"]
    refused = asyncio.run(tools.get("altium365_set_component_parameters").fn(
        "C1", [{"name": "V"}], ""))
    assert refused["ok"] is False


def test_a_workspace_off_the_allow_list_is_refused():
    session, requests = _session(lambda b: {}, altium365.Altium365Config(
        domain="d", workspace_token="t", allowed_workspaces=("https://ok/",)))
    with pytest.raises(altium365.Altium365Error, match="not listed"):
        session.list_library_folders("https://other")
    assert requests == []


def test_a_pkce_challenge_is_the_unpadded_base64url_sha256_of_its_verifier():
    import base64
    import hashlib
    verifier = "a-verifier_of.43~characters-or-more-0123456789"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert altium365.code_challenge(verifier) == expected
    assert "=" not in expected and len(expected) == 43


def test_logout_removes_the_saved_login_and_says_when_there_was_none(tmp_path, monkeypatch,
                                                                    capsys):
    import sys
    from eda_agent import server
    saved = tmp_path / "nexar_login.bin"
    saved.write_bytes(b"sealed")
    monkeypatch.setattr(altium365, "read_environment",
                        lambda env=None: altium365.Altium365Config(saved_login=saved))
    monkeypatch.setattr(sys, "argv", ["eda-agent", "altium365-logout"])
    assert server.main() == 0
    assert not saved.exists() and "Signed out" in capsys.readouterr().out
    assert server.main() == 0
    assert "No saved Nexar login" in capsys.readouterr().out
