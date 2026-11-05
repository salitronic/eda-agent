# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A write that raised is reported, not passed over in silence.

Most property writes in the bridge sit in a ``Try`` so one refused value
does not abort the rest of an edit. With an empty handler the reply still
said success and nothing told the caller the value was never set. A
handler around a write now either calls ``NoteWriteFailed``, which puts
``writes_failed`` and the places on the reply at the one dispatch point,
or says in a comment why nothing is lost (the value is read back, a flag
reports it, or the write only touches the display).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.pascal_source import load

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: A member assigned (X.Y := ...) or a mutating call: what makes a Try body
#: a write. Iterator setup (AddFilter_*) is not.
_MEMBER_WRITE = re.compile(r"\b[\w\]\)]+(\.[\w\[\]\(\)']+)+\s*:=")
_MUTATOR = re.compile(
    r"\.(SetState_\w+|Add(?!Filter)\w*Object\w*|Remove\w*Object\w*|AddSchObject|"
    r"RemoveSchObject|MoveByXY|MoveToXY|RotateBy\w*|Rebuild\w*|SetFileName|"
    r"DoFileSave|ClearAllVertices|InsertVertex|RemoveVertex|"
    r"RegisterSchObjectInContainer|SetModified)\s*\(", re.I)

#: The status window's own controls. A write to one paints the bridge's
#: window and touches no design, so a failed one has nothing to report.
_STATUS_CONTROLS = re.compile(
    r"^\s*(?:Try\s+)?(?:If\b.*?\bThen\s+)?(lbl_|btn_|pnl_|ico_|chk_|tab_|mmo_|opt_|"
    r"tmr_Spinner|StatusForm\.|Application\.HintHidePause|Clipboard\.|"
    r"Ctl\.|Pnl\.)", re.I)


def _is_write(body: str) -> bool:
    return bool(_MEMBER_WRITE.search(body) or _MUTATOR.search(body))


def _only_status_controls(body: str) -> bool:
    body = re.sub(r"\{[^}]*\}", " ", body)
    writes = [s for s in re.split(r";|\bElse\b|\bBegin\b|\bEnd\b|\bExcept\b", body)
              if ":=" in s or _MUTATOR.search(s)]
    return bool(writes) and all(
        _STATUS_CONTROLS.search(s) or not _MEMBER_WRITE.search(s) and not _MUTATOR.search(s)
        or re.match(r"^\s*\w+\s*:=", s)          # a local, not a member
        for s in writes)


def _silent_write_handlers(path: Path) -> list[str]:
    """Empty handlers (no statement, no comment) whose Try body writes."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = []
    one = re.compile(r"\bTry\b(?P<body>.*?)\bExcept\s+End\b", re.I)
    for i, line in enumerate(lines):
        for m in one.finditer(line):
            if _is_write(m.group("body")):
                found.append((i, m.group("body")))
    for k, line in enumerate(lines):
        s = line.strip()
        if re.fullmatch(r"Except\s+End\s*;?", s, re.I):
            closing = k
        elif re.fullmatch(r"Except", s, re.I):
            j = k + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or not re.fullmatch(r"End\s*;?", lines[j].strip(), re.I):
                continue
            closing = k
        else:
            continue
        indent = len(line) - len(line.lstrip())
        for t in range(closing - 1, max(-1, closing - 80), -1):
            lt = lines[t]
            if lt.strip().lower() == "try" and len(lt) - len(lt.lstrip()) == indent:
                body = " ".join(x.strip() for x in lines[t + 1:closing])
                if _is_write(body):
                    found.append((t, body))
                break
    out = []
    for i, body in found:
        if path.name == "StatusForm.pas" and _only_status_controls(body):
            continue
        out.append(f"{path.name}:{i + 1}: {body[:90]}")
    return out


def test_no_write_is_wrapped_in_a_silent_handler():
    sources = [p for p in sorted(_SCRIPTS.glob("*.pas")) if p.name != "Altium_MCP.pas"]
    assert len(sources) >= 9, "the bridge sources were not found"
    offenders = [o for p in sources for o in _silent_write_handlers(p)]
    assert not offenders, (
        "these writes sit in a Try whose Except is empty, so a write that "
        "raises still answers success. Call NoteWriteFailed('what') in the "
        "handler, or say in a { comment } there why nothing is lost (read "
        "back below, a flag reports it, display only):\n" + "\n".join(offenders))


def test_the_guard_still_sees_a_silent_write():
    """Mutated: the pattern the sweep removed must still be caught."""
    probe = _SCRIPTS / "_probe_silent_write.pas"
    probe.write_text("Procedure P;\nBegin\n    Try Obj.Text := 'x'; Except End;\nEnd;\n",
                     encoding="utf-8")
    try:
        assert _silent_write_handlers(probe), "the guard no longer sees a silent write"
        probe.write_text("Procedure P;\nBegin\n    Try Obj.Text := 'x'; "
                         "Except NoteWriteFailed('Text'); End;\nEnd;\n", encoding="utf-8")
        assert not _silent_write_handlers(probe)
        probe.write_text("Procedure P;\nBegin\n    Try Obj.Text := 'x'; "
                         "Except { read back below } End;\nEnd;\n", encoding="utf-8")
        assert not _silent_write_handlers(probe)
    finally:
        probe.unlink()


def test_the_dispatcher_resets_and_reports_per_request():
    status = load(_SCRIPTS / "StatusForm.pas")
    reset = status["ResetItemState"]
    assert "WritesFailed := 0;" in reset and "WritesFailedAt := '';" in reset
    body = status["ProcessOne"]
    assert (body.index("ResetItemState(0);") < body.index("Case Category Of")
            < body.index("Result := WriteFailures(Result);"))


def test_the_note_counts_every_failure_and_names_the_first_eight():
    body = load(_SCRIPTS / "Main.pas")["NoteWriteFailed"]
    assert "Inc(WritesFailed);" in body
    assert "If WritesFailed <= 8 Then" in body


def _failures(response: str, count: int, places: list[str]) -> str:
    """The Pascal WriteFailures, line for line, to check the JSON it makes."""
    if count == 0:
        return response
    marker = '"success":true,"data":{'
    p = response.find(marker)
    if p < 0:
        return response
    p += len(marker)
    at = ",".join('"' + w.replace("\\", "/").replace('"', "'") + '"' for w in places)
    added = (f'"writes_failed":{count},"writes_failed_at":[{at}],'
             f'"writes_warning":"{count} write(s) in this call raised and did not '
             f'take effect, although the call went on: read the objects back '
             f'before relying on them."')
    if response[p] != "}":
        added += ","
    return response[:p] + added + response[p:]


def test_the_injected_fields_keep_the_reply_valid_json():
    full = '{"protocol_version":1,"id":"x","success":true,"data":{"matched":3},"error":null}'
    empty = '{"protocol_version":1,"id":"x","success":true,"data":{},"error":null}'
    failed = '{"protocol_version":1,"id":"x","success":false,"data":null,"error":{}}'
    for reply in (full, empty):
        out = json.loads(_failures(reply, 2, ['Rectangle.Color', 'a "quoted" \\ name']))
        assert out["data"]["writes_failed"] == 2
        assert out["data"]["writes_failed_at"][0] == "Rectangle.Color"
    assert json.loads(_failures(full, 2, ["x"]))["data"]["matched"] == 3
    assert _failures(failed, 2, ["x"]) == failed


def test_the_python_mirror_matches_the_pascal():
    main = load(_SCRIPTS / "Main.pas")
    body = main["WriteFailures"]
    for piece in ("'\"writes_failed\":'", "',\"writes_failed_at\":['",
                  "' write(s) in this '", "'read the objects back before relying on them.\"'",
                  "If Copy(Response, P, 1) <> Chr(125) Then Added := Added + ','"):
        assert piece in body, piece
    note = main["NoteWriteFailed"]
    for piece in ("'\\', '/'", "'\"', ''''"):
        assert piece in note, piece
