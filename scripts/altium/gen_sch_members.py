# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Write SchMemberGate into Generic.pas from eda_agent.sch_members.

    python scripts/altium/gen_sch_members.py          rewrite the block
    python scripts/altium/gen_sch_members.py --check  exit 1 when it differs

The block sits between the BEGIN / END markers below; nothing outside it
is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from eda_agent.sch_members import ROWS  # noqa: E402

GENERIC = ROOT / "scripts" / "altium" / "Generic.pas"
BEGIN = "{ BEGIN GENERATED SchMemberGate: scripts/altium/gen_sch_members.py }"
END = "{ END GENERATED SchMemberGate }"


def _or(terms: list[str], indent: str, nl: str) -> str:
    lines, line = [], ""
    for t in terms:
        piece = t if not line else " Or " + t
        if len(indent) + len(line) + len(piece) > 76 and line:
            lines.append(line)
            line = "Or " + t
        else:
            line += piece
    lines.append(line)
    return (nl + indent).join(lines)


def render(nl: str = "\n") -> str:
    out = [
        BEGIN,
        "{ SchMemberGate - may Obj be asked for PropName? 1 yes, 0 no (its type   }",
        "{ lacks the member, and asking would stop the script), -1 the table does }",
        "{ not govern PropName. Generated from src/eda_agent/sch_members.py.      }",
        "Function SchMemberGate(Obj : ISch_GraphicalObject; PropName : String) : Integer;",
        "Var",
        "    Id : Integer;",
        "Begin",
        "    Result := -1;",
    ]
    for i, row in enumerate(ROWS):
        names = ["(PropName = '%s')" % m for m in row.members]
        types = ["(Id = %s)" % t for t in row.types]
        cond = _or(types, "        ", nl)
        if row.min_version:
            cond = "(%s) And (AltiumVersionCode(0) >= %d)" % (cond, row.min_version)
        out += [
            "    %sIf %s Then" % ("" if i == 0 else "Else ", _or(names, "        ", nl)),
            "    Begin",
            "        Result := 0;",
            "        If Obj = Nil Then Exit;",
            "        Id := Obj.ObjectId;",
            "        If %s Then" % cond,
            "            Result := 1;",
            "    End" + (";" if i == len(ROWS) - 1 else ""),
        ]
    out += ["End;", END]
    return nl.join(out)


def apply(text: str) -> str:
    a, b = text.index(BEGIN), text.index(END) + len(END)
    nl = "\r\n" if "\r\n" in text else "\n"
    return text[:a] + render(nl) + text[b:]


def main(argv: list[str]) -> int:
    text = GENERIC.read_bytes().decode("utf-8")
    new = apply(text)
    if "--check" in argv:
        if new != text:
            print("Generic.pas SchMemberGate is out of date: run scripts/altium/gen_sch_members.py")
            return 1
        return 0
    if new != text:
        GENERIC.write_bytes(new.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
