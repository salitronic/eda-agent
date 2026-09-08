# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Floats must survive a comma-decimal locale (issue #21).

On a machine whose regional settings use ',' as the decimal separator,
which is most of Europe, a float emitted by a bare FloatToStr comes out as
'1,5'. That is not valid JSON, and parsing it back is what took the whole
polling loop down: an RTL conversion error surfaces as a modal before the
surrounding Except can run, so the script unwinds and the bridge stops
answering. The symptom looks like Altium hanging, not like a bad number.

The reporter measured the emit half (10 bypassing call sites, five in
PCB.pas and five in PCBGeneric.pas) and was explicit that the parse half
was inferred rather than observed. Both are addressed here.

WHY THE HARNESS COULD NOT HAVE CAUGHT THIS. cross_validate_pascal.pas
carried a StrToFloatDefCustom that shared only the NAME with the real
StrToFloatDef and had none of its guards, and it pinned DecimalSeparator
to '.', which is the single setting under which the bug cannot appear. The
real functions are now lifted verbatim and driven with the separator set
to ',', so the Turkish configuration is reproduced on any machine.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_cross_validate import (          # noqa: F401  (fixture)
    fpc_executable,
    read_outputs,
    write_inputs,
)

SCRIPTS = Path(__file__).parent.parent / "scripts" / "altium"
UTILS = SCRIPTS / "Utils.pas"
CROSS_VALIDATOR = Path(__file__).parent / "cross_validate_pascal.pas"


def _function_source(text: str, name: str) -> str:
    start = text.index("Function " + name)
    terminator = "\nEnd;"
    return text[start:text.index(terminator, start) + len(terminator)]


# ---------------------------------------------------------------------------
# Emit side: every float leaves through the wrapper.
# ---------------------------------------------------------------------------

#: Every RTL conversion that reads or writes the global DecimalSeparator.
#: The guard used to name only FloatToStr, the one the reporter happened to
#: measure. The hazard is the FAMILY: FloatToStrF and FormatFloat emit
#: through the same global, and a bare StrToFloat RAISES, which the script
#: engine surfaces as a modal before any Except can run, so it stops the
#: polling loop rather than returning a wrong number. None of the four has
#: a live call site today outside the two wrappers, so this is preventive:
#: it exists to stop the next one being added, which is how the first
#: arrived.
_LOCALE_SENSITIVE = ("FloatToStr(", "FloatToStrF(", "FormatFloat(",
                     "StrToFloat(")

#: The wrappers' own bodies, the only places allowed to call the raw RTL:
#: FloatToJsonStr converts and then swaps the separator character in the
#: result, and StrToFloatDef reshapes the string into the form this locale
#: parses once IsFloatStr has pre-validated it.
_WRAPPER_BODIES = ("Result := FloatToStr(Value)", "Result := StrToFloat(Work)")


def test_no_bare_locale_conversion_outside_the_wrappers():
    """The measured half of the report, widened to the whole family.

    A bare FloatToStr respects the global separator and emits '1,5' on a
    comma locale, which is invalid JSON and is the value that later kills
    the parse. Confirmed live on a tr-TR machine: the handler that was
    fixed is the one whose CALL SITE moved to FloatToJsonStr, and its
    accumulator re-reads its own output through StrToFloatDef, so both
    directions run inside a single call. That is why the emit and parse
    sides are guarded together rather than one at a time.
    """
    offenders = []
    for path in SCRIPTS.glob("*.pas"):
        if path.name == "Altium_MCP.pas":
            # The legacy pre-split monolith. It is NOT in Altium_API.PrjScr
            # and does not ship; the twelve live modules are the deployed
            # code, which is how the other Pascal linters scope themselves.
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip block comments so prose about FloatToStr is not a call.
        text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
        for i, line in enumerate(text.splitlines(), 1):
            for name in _LOCALE_SENSITIVE:
                if name not in line:
                    continue
                # The wrapper names contain the bare names as substrings.
                if name == "FloatToStr(" and "FloatToJsonStr(" in line:
                    continue
                if name == "StrToFloat(" and "StrToFloatDef(" in line:
                    continue
                if any(body in line for body in _WRAPPER_BODIES):
                    continue
                offenders.append(f"{path.name}:{i}  {line.strip()}")
    assert not offenders, (
        "these read or write the global decimal separator directly; route "
        "them through FloatToJsonStr / StrToFloatDef:\n  "
        + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# Neither wrapper may mutate the shared global.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["FloatToJsonStr", "StrToFloatDef"])
def test_the_wrappers_do_not_mutate_the_global_separator(name):
    """Both used to set DecimalSeparator and restore it afterwards.

    That works, but it leaves a window in which a global the whole
    application shares has been changed underneath it, and the window
    widens every time another call site adopts the wrapper. The reporter
    raised exactly this: converting the bypassing sites is right, and on
    its own it makes the window bigger. Converting first and swapping the
    separator character afterwards has no window at all.
    """
    source = _function_source(
        UTILS.read_text(encoding="utf-8", errors="replace"), name)
    assert "DecimalSeparator :=" not in source, (
        f"{name} assigns to the global DecimalSeparator; it should read it "
        f"and swap the character in the string instead")
    assert "DecimalSeparator" in source, (
        f"{name} must still READ the separator to know what to swap")


def test_the_parse_side_pre_validates_rather_than_relying_on_except():
    """Try/Except cannot contain this, which is the whole point.

    The script engine surfaces an RTL conversion error as a modal before
    the handler runs, so the default is never returned and the polling
    loop ends. StrToIntDef has always pre-checked for this reason.
    """
    source = _function_source(
        UTILS.read_text(encoding="utf-8", errors="replace"), "StrToFloatDef")
    # COMMENTS ARE NOT CODE. Without stripping them the sentence
    # "IsFloatStr above guarantees S is in dot form" satisfied this guard
    # with the pre-check deleted. Caught by mutating it out, not by
    # reading the test.
    code = re.sub(r"\{[^}]*\}", " ", source, flags=re.S)
    assert "IsFloatStr" in code, (
        "StrToFloatDef no longer calls IsFloatStr, so a non-numeric value "
        "reaches StrToFloat and raises, which ends the polling loop")
    guard = code.index("IsFloatStr")
    convert = code.index("StrToFloat(")
    assert guard < convert, (
        "the IsFloatStr check must come BEFORE the conversion, or the "
        "conversion still raises")


def test_is_float_str_rejects_the_locale_comma():
    """It validates dot form, because the caller supplies dot form."""
    source = _function_source(
        UTILS.read_text(encoding="utf-8", errors="replace"), "IsFloatStr")
    # Only '.' is accepted as a separator; ',' falls through to the Exit.
    assert "Ch = '.'" in source
    assert "Ch = ','" not in source


# ---------------------------------------------------------------------------
# The copies in the harness must not drift.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name", ["IsFloatStr", "FloatToJsonStr", "StrToFloatDef"])
def test_the_cross_validated_copy_matches_the_real_source(name):
    original = _function_source(
        UTILS.read_text(encoding="utf-8", errors="replace"), name)
    copy = _function_source(
        CROSS_VALIDATOR.read_text(encoding="utf-8", errors="replace"), name)

    def flat(text):
        return re.sub(r"\s+", " ", text).strip()

    assert flat(original) == flat(copy), (
        f"{name} in cross_validate_pascal.pas has drifted from Utils.pas, "
        f"so the locale check is exercising a stale copy")


# ---------------------------------------------------------------------------
# The reporter's machine, reproduced.
# ---------------------------------------------------------------------------

def test_floats_round_trip_under_a_comma_locale(fpc_executable, tmp_path):
    """Real Pascal, real comma separator, both directions.

    FloatUnderComma emits with DecimalSeparator set to ',' and must still
    produce dot form. ParseUnderComma takes dot form on a comma locale and
    must return the value rather than a modal.
    """
    emit_cases = ["1.5", "0.0", "-2.25", "90.0", "0.125", "1000.5"]
    parse_cases = ["1.5", "-2.25", "0.0", "90.0"]

    cases = [("FloatUnderComma", [v]) for v in emit_cases]
    cases += [("ParseUnderComma", [v]) for v in parse_cases]

    input_file = tmp_path / "loc_in.txt"
    output_file = tmp_path / "loc_out.txt"
    write_inputs(cases, str(input_file))
    result = subprocess.run(
        [fpc_executable, str(input_file), str(output_file)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"{result.stdout} {result.stderr}"

    got = read_outputs(str(output_file))
    assert len(got) == len(cases)

    problems = []
    for (fn, args), out in zip(cases, got):
        if "," in out:
            problems.append(
                f"  {fn}({args[0]}) returned {out!r}, which carries a comma "
                f"and is not valid JSON")
        # Both directions must round-trip to the same number.
        if float(out) != float(args[0]):
            problems.append(
                f"  {fn}({args[0]}) returned {out!r}, a different value")
    assert not problems, "comma locale breaks floats:\n" + "\n".join(problems)


def test_a_junk_string_returns_the_default_instead_of_raising(
        fpc_executable, tmp_path):
    """The crash path: a value that is not a number at all.

    Pre-validation means StrToFloat is never reached, so no conversion
    error is raised and no modal appears. -999 is the sentinel default
    the dispatch passes.
    """
    junk = ["abc", "1,5", "", "1.2.3", "--4", "1e", "0x10"]
    cases = [("ParseUnderComma", [v]) for v in junk]

    input_file = tmp_path / "junk_in.txt"
    output_file = tmp_path / "junk_out.txt"
    write_inputs(cases, str(input_file))
    result = subprocess.run(
        [fpc_executable, str(input_file), str(output_file)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"the Pascal aborted on junk input, which is the reported crash: "
        f"{result.stdout} {result.stderr}")

    got = read_outputs(str(output_file))
    for value, out in zip(junk, got):
        assert float(out) == -999, (
            f"StrToFloatDef({value!r}) returned {out!r}, not the default. "
            f"'1,5' in particular must be REJECTED: the caller supplies dot "
            f"form, and accepting a comma would mask a bad emit site")
